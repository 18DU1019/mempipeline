# -*- coding: utf-8 -*-
"""test_timegrap.py — P2 时间维度图谱验收（零依赖，自带临时 fixtures）。

覆盖：
1. subject_key 聚簇：同题标题带标点/空白差异归同键；不同主题分离；摘要不拆簇
2. 时间序回放：ToloLine.recall 按时间升序还原完整脉络 + 跨天跨度
3. 演化信号：首稿/断更复活/同天多稿
4. 结论漂移 + 重复：summary 相似度阈值自动判别（阈值可配）
5. 缺时间字段：updated/created 缺失回落 mtime 且打 time_src=mtime 标记（不丢笔记）
6. 项目隔离：projects 限定作用域
"""
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mempipeline.timegrap import (  # noqa: E402
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

    # ---- (bonus) summary_similarity 阈值边界 ----
    print("== 相似度阈值 ==")
    check(summary_similarity("出餐口到餐桌要短", "出餐口到餐桌要短") >= DUP_THRESHOLD,
          "相同摘要 ≥ 重复阈值")
    check(summary_similarity("取消堂食只做外带", "出餐口到餐桌要短") < DRIFT_THRESHOLD,
          "大改摘要 < 漂移阈值")
    check(GAP_DAYS == 90, "断更阈值默认 90 天（显式常量）")

    print("\nTIMEGRAP (P2):", "ALL PASS" if ok else "SOME FAILED")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)