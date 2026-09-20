# -*- coding: utf-8 -*-
"""test_timegrap.py — P2 时间维度图谱验收（零依赖，自带临时 fixtures）。

覆盖：
1. subject_key 聚簇：同题标题带标点/空白差异归同键；不同主题分离；摘要不拆簇
2. 时间序回放：ToloLine.recall 按时间升序还原完整脉络 + 跨天跨度
3. 演化信号：首稿/断更复活/同天多稿
4. 结论漂移 + 重复：summary 相似度阈值自动判别（阈值可配）
5. 缺时间字段：updated/created 缺失回落 mtime 且打 time_src=mtime 标记（不丢笔记）
6. 项目隔离：projects 限定作用域
--- P3-1 新增（2026-09-11，主题键改走项目 token）---
7. project_topic_token 提取优先级（project_id → 文件名锚定 → 标题 hex）+ WorkBuddy 内容哈希排除
8. 层感知：跨层对（长期 vs 中期）只出时序信号，不输出 drift/duplicate
9. 层感知回归：同层对仍正常输出 relation（防过度屏蔽）
10. 不可达信号如实标注：horizon < gap_days 时标 revived 不可达 + 原因
"""
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mempipeline.timegrap import (
    subject_key, summary_similarity, build_timeline, TimelineGraph,
    GAP_DAYS, DRIFT_THRESHOLD, DUP_THRESHOLD,
)


def _iso(days_ago: int) -> str:
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _write(md: Path, title: str, summary: str, *, days_ago: int | None = None,
           extra_fm: str = "") -> None:
    """写字面 frontmatter。days_ago=None 时不写 updated（测 mtime 回退）。"""
    fm = "---\n"
    fm += f'title: "{title}"\n'
    fm += f'summary: "{summary}"\n'
    fm += 'memory_tier: "long"\n'
    fm += "importance: 0.9\n"
    fm += "status: \"active\"\n"
    if extra_fm:
        fm += extra_fm
    if days_ago is not None:
        fm += f'updated: "{_iso(days_ago)}"\n'
    fm += "---\n\n正文。\n"
    md.write_text(fm, encoding="utf-8")


def main() -> bool:
    ok = True

    def check(cond: bool, msg: str):
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {msg}")
        if not cond:
            ok = False

    # ---- 1. subject_key 聚簇 ----
    print("== subject_key 聚簇 ==")
    k1 = subject_key("韩餐动线")
    k2 = subject_key("韩餐动线。  ")
    check(k1 == k2, "同题标题带标点/空白差异归同键")
    k3 = subject_key("仓位规则")
    check(k3 != k1, "不同主题键分离")
    check(subject_key("", "") == "__empty__", "空输入回落哨兵")
    check(subject_key("韩餐动线", "出餐口到餐桌要短") == k1,
          "摘要不参与主题键（避免漂移投稿被拆簇）")

    # ---- 2-4. 时间脉络 + 演化信号 + 漂移/重复 ----
    print("== 时间脉络与演化信号 ==")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        mem_root = tmp / "mem"
        (mem_root / "01-长期记忆").mkdir(parents=True)
        out_a = mem_root / "01-长期记忆" / "韩餐动线-a.md"
        out_b = mem_root / "01-长期记忆" / "韩餐动线-b.md"
        out_c = mem_root / "01-长期记忆" / "韩餐动线-c.md"
        # 稿1：首稿（180 天前）
        _write(out_a, "韩餐动线", "出餐口到餐桌要短", days_ago=180)
        # 稿2：断更 180 天后复活（同摘要）
        _write(out_b, "韩餐动线", "出餐口到餐桌要短", days_ago=0)
        # 稿3：同天多稿，结论漂移（摘要大变）
        _write(out_c, "韩餐动线", "取消堂食只做外带", days_ago=0)

        timelines = build_timeline(mem_root)
        sk = subject_key("韩餐动线")
        tl = timelines.get(sk)
        check(tl is not None, "同一主题聚簇为一条时间脉络")
        if tl is not None:
            recall_paths = tl.recall()
            check(len(recall_paths) == 3, f"脉络含 3 条投稿（实得 {len(recall_paths)}）")
            check(str(out_a) in recall_paths and str(out_b) in recall_paths
                  and str(out_c) in recall_paths, "时间回放包含全部投稿")
            check(tl.spans_days() >= 179, f"脉络跨度 ~180 天（{tl.spans_days()}）")
            flags = [s.get("flag") for s in tl.signals]
            check(flags[0] == "first", f"首稿信号 first（{flags}）")
            check("revived" in flags, f"断更后再次投稿 → 复活信号（{flags}）")
            check("same_day" in flags, f"同天多稿 → same_day（{flags}）")
            rels = [s.get("relation") for s in tl.signals]
            check("drift" in rels, f"结论漂移被自动识别（{rels}）")
            check("duplicate" in rels, f"同摘要重复识别（{rels}）")
            # 稿3 的 relation 应为 drift（相对稿2）
            check(tl.signals[2].get("relation") == "drift",
                  f"漂移精确落在漂移稿（{rels}）")
            # 每条信号含阈值（可配置可追溯）
            check(all(s.get("drift_threshold") == DRIFT_THRESHOLD for s in tl.signals[1:]),
                  "漂移阈值显式写进信号（非 magic number）")
            # B4 时序边 valid-from/to：编码「内容何时生效 / 何时被取代」
            vw = tl.valid_windows()
            check(len(vw) == 3, f"valid_windows 3 条（实得 {len(vw)}）")
            check(vw[2]["valid_to"] is None, "末稿 valid_to=None（仍现行）")
            check(vw[0]["valid_from"] and vw[0]["valid_to"]
                  and vw[0]["valid_from"] < vw[0]["valid_to"],
                  "首稿生效窗口 from<to（被稿2取代）")

    # ---- 5. 缺时间字段回落 mtime ----
    print("== 缺时间字段回落 mtime ==")
    with tempfile.TemporaryDirectory() as td2:
        tmp2 = Path(td2)
        mem2 = tmp2 / "mem"
        (mem2 / "01-长期记忆").mkdir(parents=True)
        out_no = mem2 / "01-长期记忆" / "无时间戳-mm.md"
        _write(out_no, "无时间戳", "只有正文", days_ago=None)  # 不写 updated/created
        g = TimelineGraph(mem2)
        tls = g.scan()
        sk_nt = subject_key("无时间戳")
        tl_nt = tls.get(sk_nt)
        check(tl_nt is not None, "缺时间字段笔记仍进入时间谱（覆盖完备）")
        if tl_nt is not None:
            check(tl_nt.nodes[0].time_src == "mtime",
                  f"回落 mtime 并打 time_src=mtime（{tl_nt.nodes[0].time_src}）")
            check(tl_nt.nodes[0].when is not None, "mtime 时间有效")

    # ---- 6. 项目隔离 ----
    print("== 项目隔离 ==")
    with tempfile.TemporaryDirectory() as td3:
        tmp3 = Path(td3)
        mem3 = tmp3 / "mem"
        (mem3 / "projects" / "dora" / "01-长期记忆").mkdir(parents=True)
        (mem3 / "projects" / "onequant" / "01-长期记忆").mkdir(parents=True)
        _write(mem3 / "projects" / "dora" / "01-长期记忆" / "动线-d.md",
               "动线", "dora 项目", days_ago=0)
        _write(mem3 / "projects" / "onequant" / "01-长期记忆" / "动线-o.md",
               "动线", "onequant 项目", days_ago=0)
        tls_dora = build_timeline(mem3, projects=["dora"])
        sk_d = subject_key("动线")
        check(sk_d in tls_dora, "dora 项目脉络可建")
        sk_o = subject_key("动线")
        tl_dora = tls_dora.get(sk_o)
        check(tl_dora is not None and len(tl_dora.nodes) == 1
              and str(mem3 / "projects" / "onequant" / "01-长期记忆" / "动线-o.md")
              not in [n.path for n in tl_dora.nodes],
              "项目隔离：onequant 不进入 dora 时间谱")

    # ---- 7. P3-1 项目 token 主题键 ----
    print("== P3-1 项目 token 主题键 ==")
    from mempipeline.timegrap import project_topic_token
    check(project_topic_token(title="项目约束（6165c7）") == "6165c7",
          "标题（hex）提取 token")
    # 用相对路径构造，避免仓库隐私门禁（.githooks/pre-commit）的硬编码盘符拦截
    check(project_topic_token(
        path=str(Path("mem") / "01-长期记忆" / "项目约束-6165c7.md")) == "6165c7",
        "文件名锚定模式提取 token")
    check(project_topic_token(
        path=str(Path("mem") / "项目会话-9条对话协议与沟通风格硬约定-a561ad6f.md")) == "",
        "WorkBuddy 内容哈希尾部 8hex 不被当 token（锚定模式排除）")
    check(project_topic_token(project_id="pid-1", title="项目约束（6165c7）") == "pid-1",
          "project_id 优先级高于标题/文件名")
    check(subject_key("项目约束（6165c7）") == subject_key("项目会话（6165c7）"),
          "同 hex 的长期约束与中期快照归同一主题键")
    check(subject_key("项目约束（6165c7）").startswith("topic:"),
          "token 路径带 topic: 前缀（与指纹回落可区分）")
    check(subject_key("项目约束（6165c7）") != subject_key("项目约束（29e9f3）"),
          "不同项目 token 分离（无假合并）")

    # ---- 8. P3-1 跨层对不出内容关系信号 ----
    print("== P3-1 层感知：跨层对只出时序信号 ==")
    with tempfile.TemporaryDirectory() as td4:
        mem4 = Path(td4) / "mem"
        (mem4 / "01-长期记忆").mkdir(parents=True)
        (mem4 / "02-中期记忆").mkdir(parents=True)
        _write(mem4 / "01-长期记忆" / "项目约束-6165c7.md",
               "项目约束（6165c7）", "约束条目", days_ago=15)
        _write(mem4 / "02-中期记忆" / "项目会话-6165c7.md",
               "项目会话（6165c7）", "会话流水", days_ago=0)
        tls4 = build_timeline(mem4)
        sk4 = subject_key("项目约束（6165c7）")
        tl4 = tls4.get(sk4)
        check(tl4 is not None and len(tl4.nodes) == 2,
              "同 token 的长期约束 + 中期快照聚为一簇")
        if tl4 is not None:
            flags4 = [s.get("flag") for s in tl4.signals]
            check(flags4 == ["first", "continued"], f"时序信号照常产出（{flags4}）")
            check(all("relation" not in s for s in tl4.signals[1:]),
                  "跨层对不输出 drift/duplicate")
            check(tl4.signals[1].get("relation_scope") == "cross-tier",
                  "跨层对标注 relation_scope=cross-tier")
            check(tl4.signals[0]["valid_to"] is None,
                  "跨层对首稿 valid_to=None（不产生 D3 superseded）")
            check([n.tier for n in tl4.nodes] == ["01-长期记忆", "02-中期记忆"],
                  "节点带层标记（跨层判定的依据）")

    # ---- 9. P3-1 同层对仍产出内容关系（防过度屏蔽）----
    print("== P3-1 层感知：同层对不受影响 ==")
    with tempfile.TemporaryDirectory() as td5:
        mem5 = Path(td5) / "mem"
        (mem5 / "01-长期记忆").mkdir(parents=True)
        _write(mem5 / "01-长期记忆" / "甲.md",
               "项目约束（6165c7）", "出餐口到餐桌要短", days_ago=10)
        _write(mem5 / "01-长期记忆" / "乙.md",
               "项目约束（6165c7）", "出餐口到餐桌要短", days_ago=0)
        tls5 = build_timeline(mem5)
        tl5 = tls5.get(subject_key("项目约束（6165c7）"))
        check(tl5 is not None and len(tl5.nodes) == 2, "同 token 同层两稿聚为一簇")
        if tl5 is not None:
            check(tl5.signals[1].get("relation") == "duplicate",
                  f"同层对仍输出 relation（{tl5.signals[1].get('relation')}）")
            check("relation_scope" not in tl5.signals[1],
                  "同层对不标 cross-tier（未过度屏蔽）")

    # ---- 10. P3-1 不可达信号如实标注 ----
    print("== P3-1 不可达信号如实标注 ==")
    with tempfile.TemporaryDirectory() as td6:
        mem6 = Path(td6) / "mem"
        (mem6 / "01-长期记忆").mkdir(parents=True)
        (mem6 / "02-中期记忆").mkdir(parents=True)
        _write(mem6 / "01-长期记忆" / "项目约束-6165c7.md",
               "项目约束（6165c7）", "s", days_ago=15)
        _write(mem6 / "02-中期记忆" / "项目会话-6165c7.md",
               "项目会话（6165c7）", "s", days_ago=0)
        tls6 = build_timeline(mem6)
        tl6 = tls6.get(subject_key("项目约束（6165c7）"))
        if tl6 is not None:
            check(tl6.horizon_days == 15, f"horizon_days 如实计算（{tl6.horizon_days}）")
            check(any("revived" in u for u in tl6.unreachable_signals),
                  "跨度 15 天 < gap_days 90 → 标注 revived 不可达")
            check("数学上不可能触发" in "".join(tl6.unreachable_signals),
                  "标注含原因（非静默返回 0）")

    # ---- 11. P3.12 §7.1 redacted 退出活跃信号源 / 保留谱系历史 ----
    print("== P3.12 redacted 退出活跃信号源 ==")
    with tempfile.TemporaryDirectory() as td7:
        mem7 = Path(td7) / "mem"
        (mem7 / "01-长期记忆").mkdir(parents=True)
        _write(mem7 / "01-长期记忆" / "主体-旧.md",
               "项目约束（6165c7）", "旧结论", days_ago=10)
        # redacted 稿用字面 frontmatter（status 覆盖为 redacted）
        red_path = mem7 / "01-长期记忆" / "主体-新.md"
        red_text = (f'---\ntitle: "项目约束（6165c7）"\n'
                    f'summary: "新结论"\nmemory_tier: "long"\n'
                    f'importance: 0.9\nstatus: "redacted"\n'
                    f'updated: "{_iso(0)}"\n---\n\n正文。\n')
        red_path.write_text(red_text, encoding="utf-8")
        tl7 = build_timeline(mem7).get(subject_key("项目约束（6165c7）"))
        check(tl7 is not None and len(tl7.nodes) == 2,
              "redacted 稿仍在脉络节点（谱系历史保留）")
        check(str(red_path) in tl7.recall() if tl7 else False,
              "redacted 稿仍在历史脉络回放中占位")
        if tl7 is not None:
            check(str(red_path) not in {s.get("path") for s in tl7.signals},
                  "redacted 稿不派生任何信号（退出活跃信号源）")
            check(str(red_path) not in [w.get("path") for w in tl7.valid_windows()],
                  "redacted 稿不进 valid_windows（不作 superseded/drift 候选）")

    # ---- (bonus) summary_similarity 阈值边界 ----
    print("== 相似度阈值 ==")
    check(summary_similarity("出餐口到餐桌要短", "出餐口到餐桌要短") >= DUP_THRESHOLD,
          "相同摘要 ≥ 重复阈值")
    check(summary_similarity("取消堂食只做外带", "出餐口到餐桌要短") < DRIFT_THRESHOLD,
          "大改摘要 < 漂移阈值")
    check(GAP_DAYS == 90, "断更阈值默认 90 天（显式常量）")
    # ---- (bonus) 收敛源锁定：bigram/Jaccard 共用 recall 实现（检验报告第五-2）----
    from mempipeline.recall import _bigrams, _jaccard
    check(_jaccard(_bigrams("出餐口到餐桌要短"), _bigrams("出餐口到餐桌要短")) == 1.0
          and _jaccard(_bigrams("abc"), _bigrams("def")) == 0.0,
          "recall._bigrams/_jaccard 为 timegrap/crossref 共享源")

    print("\nTIMEGRAP (P2):", "ALL PASS" if ok else "SOME FAILED")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)