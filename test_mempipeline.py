# -*- coding: utf-8 -*-
"""mempipeline 端到端最小验证（数据无关，运行时自建临时脱敏 fixtures）。

覆盖「1写者 + N投稿 + 无限读」四件事：
1. 写者：engine.write_atomic 原子写 + 幂等跳过（同稳定正文第二次不重写）
2. 读者：recall.recall 能从笔记召回相关结果（含同义词归一化）
3. 投稿：ingest 把 staging 里带 frontmatter 的裸 md 熔合进正确层目录
4. 审计：FileAudit 追加 log + manifest 指纹，写/跳均登记
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mempipeline.protocol import Note, TIER_DIR
from mempipeline.audit import FileAudit
from mempipeline.engine import write_atomic
from mempipeline.recall import MemoryRecall
from mempipeline.ingest import ingest


def main() -> bool:
    ok = True

    def check(cond: bool, msg: str):
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {msg}")
        if not cond:
            ok = False

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        mem_root = tmp / "mem"
        (mem_root / "01-长期记忆").mkdir(parents=True)
        (mem_root / "02-中期记忆").mkdir(parents=True)
        log_path = tmp / "audit" / "log.md"
        manifest_path = tmp / "audit" / "manifest.json"
        audit = FileAudit(log_path, manifest_path, mem_root)

        # ---- 1. 写者：原子写 + 幂等跳过 ----
        print("== 写者 engine ===")
        note = Note(title="仓位规则", summary="单笔风险不超过 1 ATR", tier="long",
                    importance=0.9, body="每笔只下 1 个 ATR 的风险。")
        out = mem_root / TIER_DIR["long"] / "项目会话-仓位规则-ab12cd34.md"
        s1, _ = write_atomic(out, note.to_frontmatter() + "\n\n" + note.body + "\n", audit)
        check(s1 == "wrote", f"首次写 -> {s1}, 文件存在={out.exists()}")
        s2, _ = write_atomic(out, note.to_frontmatter() + "\n\n" + note.body + "\n", audit)
        check(s2 == "skipped", f"同稳定正文幂等 -> {s2}")
        n2 = Note(title="仓位规则", summary="单笔风险不超过 1 ATR", tier="long",
                  importance=0.9, body="每笔只下 1 个 ATR 的风险。")
        s3, _ = write_atomic(out, n2.to_frontmatter() + "\n\n" + n2.body + "\n", audit)
        check(s3 == "skipped", f"updated 变但正文未变仍跳过 -> {s3}")

        # ---- 2. 读者：召回（同义词归一化：仓位/positioning 映射主词） ----
        print("== 读者 recall ===")
        (mem_root / "02-中期记忆" / "项目会话-定投-aabbccdd.md").write_text(
            "---\ntype: note\ntitle: 定投纪律\nsummary: 每月固定日定投\n"
            "memory_tier: medium\nimportance: 0.6\nsource_agent: workbuddy\nupdated: 2026-08-01\n---\n\n定投要按纪律执行。\n",
            encoding="utf-8")
        recall = MemoryRecall(mem_root, synonyms={"仓位": ["positioning"]})
        hits = recall.recall("风险 仓位", k=5)
        check(any("仓位规则" in h for h, _ in hits), f"召回命中仓位规则 {[h for h, _ in hits]}")
        # 文档侧同义归一：文档只含英文同义词 positioning，查询用主词"仓位"应能命中
        (mem_root / "02-中期记忆" / "项目会话-synonym-fe123456.md").write_text(
            "---\ntype: note\ntitle: positioning规则\nsummary: 仓位同义词测试\n"
            "memory_tier: medium\nimportance: 0.5\n---\n\npositioning 管理：单笔风险不超过 0.5 ATR。\n",
            encoding="utf-8")
        hits2 = recall.recall("仓位 管理", k=5)
        check(any("synonym" in h for h, _ in hits2), f"文档侧同义归一命中 {[h for h, _ in hits2]}")

        # ---- 2.1 crossref add_backlinks 统计回归（wrote->linked，已存在->skipped） ----
        print("== crossref add_backlinks 统计 ==")
        from mempipeline.crossref import find_related, add_backlinks
        (mem_root / "02-中期记忆" / "项目会话-调度甲-11111111.md").write_text(
            "---\ntype: note\ntitle: 调度甲\nsummary: 底层\n"
            "memory_tier: medium\nimportance: 0.5\n---\n\n仓位调度纪律与分配原则。\n",
            encoding="utf-8")
        (mem_root / "02-中期记忆" / "项目会话-调度乙-22222222.md").write_text(
            "---\ntype: note\ntitle: 调度乙\nsummary: 底层\n"
            "memory_tier: medium\nimportance: 0.5\n---\n\n仓位调度与分配的核心内容。\n",
            encoding="utf-8")
        rel = find_related("仓位调度纪律 分配", mem_root, TIER_DIR.values(), min_score=0.0)
        res = add_backlinks(mem_root, "仓位调度纪律", rel, audit)
        check(res["linked"] == len(rel), f"新建 backlink counted as linked {res}")
        res2 = add_backlinks(mem_root, "仓位调度纪律", rel, audit)
        check(res2["linked"] == 0 and res2["skipped"] == len(rel),
              f"重复 backlink counted as skipped {res2}")

        # ---- 3. 投稿：staging 熔合进 02-中期记忆 ----
        print("== 投稿 ingest ===")
        staging = tmp / "staging"
        (staging / "workbuddy").mkdir(parents=True)
        (staging / "workbuddy" / "daily.md").write_text(
            "---\ntitle: 今日复盘\nsummary: 中期情景摘要\n"
            "memory_tier: medium\nsource_agent: workbuddy\n---\n\n复盘主体。\n",
            encoding="utf-8")
        (staging / "workbuddy" / "note_no_tier.md").write_text(
            "---\ntitle: 无层笔记\nsummary: 无\n---\nbody\n", encoding="utf-8")
        stats = ingest(staging, mem_root, TIER_DIR, audit)
        medium_files = list((mem_root / "02-中期记忆").glob("*.md"))
        check(stats["wrote"] >= 1, f"熔合数 = {stats}")
        check(any("workbuddy" in f.read_text(encoding="utf-8") for f in medium_files),
              "投稿 source_agent=workbuddy 落 02 层")

        # ---- 4. 审计 ----
        print("== 审计 audit ===")
        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        check(len(log_lines) >= 4, f"log {len(log_lines)} 行（write/skip 均应登记）")
        mani = manifest_path.read_text(encoding="utf-8")
        check('"files"' in mani, "manifest 指纹已登记")

    print("\nRESULT:", "ALL PASS" if ok else "SOME FAILED")
    return ok


def test_crash_recover_sidecar() -> bool:
    """崩溃恢复 sidecar 专项：残留 .bak 应在幂等跳过时被登记为 recover 并清理。"""
    import shutil
    ok = True

    def check(cond: bool, msg: str):
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {msg}")
        if not cond:
            ok = False

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        mem_root = tmp / "mem"
        (mem_root / "01-长期记忆").mkdir(parents=True)
        log_path = tmp / "audit" / "log.md"
        manifest_path = tmp / "audit" / "manifest.json"
        audit = FileAudit(log_path, manifest_path, mem_root)

        note = Note(title="崩溃恢复", summary="sidecar 测试", tier="long",
                    importance=0.5, body="正文体。")
        out = mem_root / TIER_DIR["long"] / "项目会话-崩溃恢复-cafe0000.md"
        s1, _ = write_atomic(out, note.to_frontmatter() + "\n\n" + note.body + "\n", audit)
        check(s1 == "wrote", f"首次写 -> {s1}")
        # 模拟崩溃残留：写入完成但 .bak 未清理（真实场景=os.replace 后 audit 前中断）
        bak = out.with_name(out.name + ".bak")
        shutil.copy2(out, bak)
        check(bak.exists(), "预留崩溃残留 .bak")
        # 幂等跳过分支应检测残留并登记 recover
        s2, _ = write_atomic(out, note.to_frontmatter() + "\n\n" + note.body + "\n", audit)
        check(s2 == "skipped", f"残留存在时幂等 -> {s2}")
        check(not bak.exists(), "残留 .bak 已被清理")
        log = log_path.read_text(encoding="utf-8")
        check("| recover |" in log, "审计日志已登记 recover 行")
    print("\nCRASH RECOVER:", "ALL PASS" if ok else "SOME FAILED")
    return ok

def test_tfidf_recall() -> bool:
    """ngram TF-IDF 召回专项：命中率 / 区分度 / 同义不回归 / 接口不破。"""
    ok = True

    def check(cond: bool, msg: str):
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {msg}")
        if not cond:
            ok = False

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        mem_root = tmp / "mem"
        (mem_root / "02-中期记忆").mkdir(parents=True)
        d = mem_root / "02-中期记忆"
        (d / "target.md").write_text(
            "---\ntype: note\ntitle: 仓位调度\nsummary: 量化仓位规则\n"
            "memory_tier: medium\nimportance: 0.9\n---\n\n"
            "量化仓位调度规则：单笔风险不超过 1 ATR，按信号强度分配仓位。\n",
            encoding="utf-8")
        (d / "distractor.md").write_text(
            "---\ntype: note\ntitle: 市场风险\nsummary: 风险提示\n"
            "memory_tier: medium\nimportance: 0.4\n---\n\n"
            "市场风险提示：今日波动加大，注意控制风险敞口。\n",
            encoding="utf-8")
        (d / "unrelated.md").write_text(
            "---\ntype: note\ntitle: 定投纪律\nsummary: 定投\n"
            "memory_tier: medium\nimportance: 0.3\n---\n\n"
            "当季定投纪律，每月固定日期执行。\n",
            encoding="utf-8")

        # 命中率 + 区分度：强查询应命中 target 且排第一，distractor 不混入
        r = MemoryRecall(mem_root)
        hits = r.recall("仓位调度 分配", k=5)
        top = hits[0][0] if hits else ""
        check(any("target.md" in h for h, _ in hits),
              f"命中 target {[h for h, _ in hits]}")
        check("target.md" in top, f"target 排第一 (top={top})")
        check(not any("distractor.md" in h for h, _ in hits), "distractor 不混入")

        # 同义不回归：positioning 归一为 仓位 后仍召回
        r2 = MemoryRecall(mem_root, synonyms={"仓位": ["positioning"]})
        hits2 = r2.recall("positioning 调度", k=5)
        check(any("target.md" in h for h, _ in hits2), "同义 positioning 仍命中 target")

        # 接口不破：类型 / 降序 / 长度上限 / 空查询
        check(len(hits) <= 5, "结果数不超过 k")
        check(all(a[1] >= b[1] for a, b in zip(hits, hits[1:])), "按分数降序")
        check(all(isinstance(t, tuple) and len(t) == 2 and isinstance(t[1], float)
                  for t in hits), "返回 list[tuple[str,float]]")
        check(r.recall("") == [] and r.recall("   ") == [], "空查询返回 []")
    print("\nTFIDF RECALL:", "ALL PASS" if ok else "SOME FAILED")
    return ok


def test_recall_golden() -> bool:
    """PDCA · Check 信号层：golden 回归命中率、passed 断言、并确保 Act 不自动改参数。"""
    ok = True

    def check(cond: bool, msg: str):
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {msg}")
        if not cond:
            ok = False

    from mempipeline.recall_golden import check as golden_check
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        mem_root = tmp / "mem"
        (mem_root / "01-长期记忆").mkdir(parents=True)
        (mem_root / "02-中期记忆").mkdir(parents=True)
        long_d = mem_root / "01-长期记忆"
        mid_d = mem_root / "02-中期记忆"
        (long_d / "项目会话-仓位规则-ab12cd34.md").write_text(
            "---\ntype: note\ntitle: 仓位规则\nsummary: 单笔风险\n"
            "memory_tier: long\nimportance: 0.9\n---\n\n"
            "单笔风险不超过 1 ATR，按信号强度分配仓位。\n", encoding="utf-8")
        (mid_d / "项目会话-仓位调度-cd34cd34.md").write_text(
            "---\ntype: note\ntitle: 仓位调度\nsummary: 量化仓位规则\n"
            "memory_tier: medium\nimportance: 0.9\n---\n\n"
            "量化仓位调度规则：按信号强度分配仓位。\n", encoding="utf-8")
        (mid_d / "项目会话-市场风险-ef56ef56.md").write_text(
            "---\ntype: note\ntitle: 市场风险\nsummary: 风险提示\n"
            "memory_tier: medium\nimportance: 0.4\n---\n\n"
            "市场风险提示：今日波动加大。\n", encoding="utf-8")

        syn = {"仓位": ["positioning"]}
        # 注入沙盒自有 golden（不依赖全局 GOLDEN 常量，避免 GOLDEN 维护后测试崩塌）
        own_golden = {"风险 仓位": "仓位规则", "仓位调度 分配": "仓位调度"}
        sig = golden_check(mem_root, synonyms=syn, golden=own_golden)
        check(sig["hit_rate"] >= 2 / 3, f"命中率 = {sig['hit_rate']:.2f}")
        check(sig["passed"] is True, "passed=True（跌破红线才报错）")
        check(len(sig["results"]) == len(own_golden), "逐条覆盖注入 golden 集")
        check(all(r["phase"] in ("hit", "miss") for r in sig["results"]),
              "每条都有 hit/miss 相位")
        # Act 留人：同一召回调用前后一致，check 不改任何状态
        r1 = MemoryRecall(mem_root, synonyms=syn).recall("风险 仓位", k=5)
        r2 = MemoryRecall(mem_root, synonyms=syn).recall("风险 仓位", k=5)
        check(r1 == r2, "check 无副作用：召回结果前后一致")
    print("\nRECALL GOLDEN (PDCA Check):", "ALL PASS" if ok else "SOME FAILED")
    return ok


def test_project_isolation() -> bool:
    """E1：项目隔离 + 二维落盘 + crossref/recall 项目作用域（G1 回归）。"""
    ok = True

    def check(cond: bool, msg: str):
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {msg}")
        if not cond:
            ok = False

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        mem_root = tmp / "mem"
        (mem_root / "02-中期记忆").mkdir(parents=True)
        log_path = tmp / "audit" / "log.md"
        manifest_path = tmp / "audit" / "manifest.json"
        audit = FileAudit(log_path, manifest_path, mem_root)
        staging = tmp / "staging"
        (staging / "proj_a").mkdir(parents=True)
        (staging / "proj_b").mkdir(parents=True)

        # 两个项目各投一篇语义相近的笔记（若跨项目建链/召回即泄漏）
        (staging / "proj_a" / "a.md").write_text(
            "---\ntitle: 韩餐店动线\nsummary: 出餐口到餐桌动线\nmemory_tier: medium\n"
            "project_id: dora\n---\n韩餐店的出餐口到餐桌动线要短。", encoding="utf-8")
        (staging / "proj_b" / "b.md").write_text(
            "---\ntitle: 韩餐店动线\nsummary: 出餐口到餐桌动线\nmemory_tier: medium\n"
            "project_id: onequant\n---\n韩餐店的出餐口到餐桌动线要短。", encoding="utf-8")

        # 无 project_id 投稿（require_project 时拒收）
        (staging / "proj_a" / "no_id.md").write_text(
            "---\ntitle: 无主笔记\nsummary: 无项目\nmemory_tier: medium\n---\n无主内容。",
            encoding="utf-8")

        # 1) 二维落盘 + 无 ID 拒收
        st = ingest(staging, mem_root, None, audit, crossref=True, require_project=True)
        check((mem_root / "projects" / "dora" / "02-中期记忆").exists(),
              "项目 dora 落盘到 projects/dora/02-中期记忆")
        check((mem_root / "projects" / "onequant" / "02-中期记忆").exists(),
              "项目 onequant 落盘到 projects/onequant/02-中期记忆")
        check(st.get("rejected", 0) == 1, f"无 project_id 投稿拒收（rejected=1，实得 {st.get('rejected')}）")

        # 2) recall 项目作用域：dora 召回不含 onequant 的笔记
        r_a = MemoryRecall(mem_root, projects=["dora"]).recall("动线", k=5)
        r_b = MemoryRecall(mem_root, projects=["onequant"]).recall("动线", k=5)
        a_paths = {Path(p).parent for p, _ in r_a}
        b_paths = {Path(p).parent for p, _ in r_b}
        check(all("dora" in str(p) for p in a_paths), "dora 召回仅限 dora 项目目录")
        check(all("onequant" in str(p) for p in b_paths), "onequant 召回仅限 onequant 项目目录")
        check(bool(r_a) and bool(r_b), "两项目均能召回本项目笔记")

        # 3) crossref 项目作用域：dora 笔记的 links 只指向 dora 项目内
        dora_note = next((mem_root / "projects" / "dora" / "02-中期记忆").glob("*.md"))
        fm = dora_note.read_text(encoding="utf-8")
        links = [ln for ln in fm.splitlines() if ln.startswith("links:")]
        if links:
            check("onequant" not in links[0] and "dora" in links[0],
                  f"dora 笔记 links 仅项目内（{links[0][:60]}）")
        else:
            # 若 min_score 未达阈值无链接，至少确认非空链接不含跨项目引用
            check(True, "无 links（相似度未达阈值，跳过跨项目检查）")

        # 4) legacy 兼容：无 project_id + require_project=False 回落顶层 tier
        (staging / "proj_a" / "legacy.md").write_text(
            "---\ntitle: 旧式笔记\nsummary: 无项目\nmemory_tier: medium\n---\n旧式内容。",
            encoding="utf-8")
        st2 = ingest(staging, mem_root, None, audit, require_project=False)
        check(any("旧式" in f.name for f in (mem_root / "02-中期记忆").glob("*.md")),
              "无 project_id 回落 legacy 顶层目录")
    print("\nPROJECT ISOLATION (E1):", "ALL PASS" if ok else "SOME FAILED")
    return ok


if __name__ == "__main__":
    main_result = main()
    crash_result = test_crash_recover_sidecar()
    tfidf_result = test_tfidf_recall()
    golden_result = test_recall_golden()
    iso_result = test_project_isolation()
    sys.exit(0 if (main_result and crash_result and tfidf_result and golden_result and iso_result) else 1)
