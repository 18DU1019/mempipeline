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
from mempipeline.recall import MemoryRecall, TFIDFIndex
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


def test_crash_recover_sidecar() -> None:
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
    if not ok:
        raise AssertionError("test_crash_recover_sidecar: 子断言失败")

def test_tfidf_recall() -> None:
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
    if not ok:
        raise AssertionError("test_tfidf_recall: 子断言失败")


def test_recall_golden() -> None:
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
    if not ok:
        raise AssertionError("test_recall_golden: 子断言失败")


def test_project_isolation() -> None:
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
        ingest(staging, mem_root, None, audit, require_project=False)
        check(any("旧式" in f.name for f in (mem_root / "02-中期记忆").glob("*.md")),
              "无 project_id 回落 legacy 顶层目录")
    print("\nPROJECT ISOLATION (E1):", "ALL PASS" if ok else "SOME FAILED")
    if not ok:
        raise AssertionError("test_project_isolation: 子断言失败")


def test_tfidf_index() -> None:
    """TFIDFIndex（SQLite 倒排）与 MemoryRecall 结果一致性 + 增量 build + 空索引安全。

    P1-A2：验证倒排快路径的 Top-K 结果与全扫基准一致，且不触碰镜像（纯读索引）。
    """
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
        (mem_root / "02-中期记忆" / "项目会话-仓位调度-1111.md").write_text(
            "---\ntype: note\ntitle: 仓位调度\nsummary: 量化仓位\n"
            "memory_tier: medium\n---\n\n量化仓位调度规则：按信号强度分配仓位。\n",
            encoding="utf-8")
        (mem_root / "02-中期记忆" / "项目会话-风险提示-2222.md").write_text(
            "---\ntype: note\ntitle: 市场风险\nsummary: 风险\n"
            "memory_tier: medium\n---\n\n市场风险提示：控制风险敞口。\n",
            encoding="utf-8")

        idx = TFIDFIndex(tmp / "idx.db")
        # 未 build 时空索引：recall 应安全返回 []（不抛错），调用方自行回退
        check(idx.recall("仓位 调度") == [], "未 build 时空索引安全返回 []")

        built = idx.build(mem_root)
        check(built == 2, f"首次 build 索引 2 篇 (实得 {built})")

        # 一致性：倒排 vs 全扫基准，同查询 top-1 应指向同一篇
        base = MemoryRecall(mem_root)
        b1 = base.recall("仓位 调度", k=5)
        i1 = idx.recall("仓位 调度", k=5)
        check(bool(b1) and bool(i1), "两路均能召回")
        if b1 and i1:
            check(Path(b1[0][0]).name == Path(i1[0][0]).name,
                  f"top-1 一致 (全扫={Path(b1[0][0]).name}, 倒排={Path(i1[0][0]).name})")

        # 增量 build：新增 1 篇后再次 build，返回新增数（幂等）
        (mem_root / "02-中期记忆" / "项目会话-定投-3333.md").write_text(
            "---\ntype: note\ntitle: 定投\nsummary: 定投纪律\n"
            "memory_tier: medium\n---\n\n每月固定日定投纪律。\n", encoding="utf-8")
        added = idx.build(mem_root)
        check(added == 1, f"增量 build 返回 1 (实得 {added})")
        check(idx.count() == 3, f"索引计数口径=3 (实得 {idx.count()})")
        # 空查询安全
        check(idx.recall("") == [] and idx.recall("   ") == [], "空查询返回 []")
        # A3 ASI06：同内容跨路径重复入库 -> 内容键去重（防重复/污染）
        build = (mem_root / "02-中期记忆")
        dup_src = ("---\ntype: note\ntitle: 仓位调度\nsummary: 量化仓位\n"
                   "memory_tier: medium\n---\n\n量化仓位调度规则：按信号强度分配仓位。\n")
        (build / "重复同内容-7777.md").write_text(dup_src, encoding="utf-8")
        added_dup = idx.build(mem_root)
        check(added_dup == 0, f"A3 同内容不重复入库 (added={added_dup})")
        check(idx.count() == 3, f"A3 内容去重后计数仍 3 (实得 {idx.count()})")
        # schema 迁移（A3 ASI06 演进）：旧版 DB 的 doc 表缺 ckey 列，
        # CREATE TABLE IF NOT EXISTS 不补列 → TFIDFIndex 应丢弃重建，而不是建崩。
        import sqlite3
        mig = tmp / "old.db"
        _c = sqlite3.connect(str(mig))
        _c.execute("CREATE TABLE doc (path TEXT PRIMARY KEY,"
                   " norm TEXT NOT NULL, ngrams TEXT NOT NULL)")
        _c.execute("CREATE TABLE gram (gram TEXT NOT NULL, df REAL NOT NULL)")
        _c.commit(); _c.close()
        mig_idx = TFIDFIndex(mig)
        mcols = [r[1] for r in mig_idx._conn.execute("PRAGMA table_info(doc)")]
        check("ckey" in mcols, f"旧 schema 迁移自动补 ckey 列 (实得 {mcols})")
        mig_idx.close()
        idx.close()
    print("\nTFIDF INDEX (P1-A2):", "ALL PASS" if ok else "SOME FAILED")
    if not ok:
        raise AssertionError("test_tfidf_index: 子断言失败")


def test_tfidf_vacuum():
    """二期 F 项（2026-09-17）验收：物理删除镜像文件后 build 清除幽灵路径。

    验收口径（ARCHITECTURE.md §8.1 F 行）：索引后删文件再 build ->
    快路径 0 幽灵命中 + 清除清单（last_vacuum）正确；范围外路径
    （mem_root 变更场景）保守不动防误清。
    """
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
        (mem_root / "02-中期记忆" / "会话-可保留-1111.md").write_text(
            "---\ntype: note\ntitle: 可保留\nsummary: 保留稿\n"
            "memory_tier: medium\n---\n\n可保留稿：再平衡纪律要点。\n",
            encoding="utf-8")
        ghost = mem_root / "02-中期记忆" / "会话-待删除-2222.md"
        ghost.write_text(
            "---\ntype: note\ntitle: 待删除\nsummary: 幽灵稿\n"
            "memory_tier: medium\n---\n\n待删除稿：期权卖方风控要点。\n",
            encoding="utf-8")
        idx = TFIDFIndex(tmp / "vac.db")
        built = idx.build(mem_root)
        check(built == 2, f"首次 build 索引 2 篇 (实得 {built})")
        check(idx.last_vacuum == [], "正常 build 无清除")

        # 场景 1：物理删除一篇 -> 再 build -> 幽灵路径被清除且快路径不再命中
        ghost.unlink()
        added = idx.build(mem_root)
        check(added == 0, f"删除后 build 无新增 (实得 {added})")
        check(idx.count() == 1, f"幽灵路径已出倒排 (count={idx.count()})")
        check(len(idx.last_vacuum) == 1 and Path(idx.last_vacuum[0]).name == ghost.name,
              f"清除清单正确 (last_vacuum={idx.last_vacuum})")
        hits = idx.recall("期权 风控", k=5)
        check(hits == [], f"快路径 0 幽灵命中 (hits={hits})")
        keep = idx.recall("再平衡", k=5)
        check(len(keep) == 1, "幸存文档召回不受影响")

        # 场景 2：mem_root 变更（known 全部越界）-> 保守不清，防整体误清
        other = tmp / "other_root"
        (other / "01-长期记忆").mkdir(parents=True)
        n = idx.build(other)
        check(idx.count() == 1, f"越界路径保守保留 (count={idx.count()})")
        check(idx.last_vacuum == [], "越界场景清除清单为空")
        check(n == 0, "越界场景无新增")
        idx.close()
    print("\nTFIDF VACUUM (F-2026-09-17):", "ALL PASS" if ok else "SOME FAILED")
    if not ok:
        raise AssertionError("test_tfidf_vacuum: 子断言失败")


def test_g_error_visibility():
    """二期 G 项（2026-09-17）验收：数据读路径静默丢失显形（计数上报）。

    坏编码文件进镜像后：召回/索引的正常候选不受影响（行为不变），
    但 read_errors / last_read_errors 计数 > 0（可见性达成）。
    """
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
        (mem_root / "02-中期记忆" / "会话-正常-1111.md").write_text(
            "---\ntype: note\ntitle: 正常\nsummary: 正常稿\n"
            "memory_tier: medium\n---\n\n正常稿：定投纪律要点。\n", encoding="utf-8")
        # 坏编码文件（非法 utf-8 字节序列）
        (mem_root / "02-中期记忆" / "会话-坏编码-2222.md").write_bytes(
            b"---\ntitle: bad\n---\n\n\xff\xfe\xff invalid bytes\n")

        base = MemoryRecall(mem_root)
        check(base.read_errors == 0, "初始 read_errors=0")
        hits = base.recall("定投 纪律", k=5)
        check(len(hits) == 1 and Path(hits[0][0]).name.startswith("会话-正常"),
              "坏编码条目缺席、正常候选不受影响（行为不变）")
        check(base.read_errors == 1, f"全扫读失败计数=1 (实得 {base.read_errors})")
        hits2 = base.recall("定投 纪律", k=5)
        check(base.read_errors == 2, f"累计语义：第二次 recall 后=2 (实得 {base.read_errors})")

        idx = TFIDFIndex(tmp / "gvis.db")
        built = idx.build(mem_root)
        check(built == 1, f"索引只收正常稿 (built={built})")
        check(idx.last_read_errors == 1, f"倒排 build 读失败计数=1 (实得 {idx.last_read_errors})")
        added = idx.build(mem_root)
        check(idx.last_read_errors == 1, f"每轮重置：增量 build 仍=1 (实得 {idx.last_read_errors})")
        idx.close()
    print("\nG ERROR VISIBILITY (2026-09-17):", "ALL PASS" if ok else "SOME FAILED")
    if not ok:
        raise AssertionError("test_g_error_visibility: 子断言失败")


def test_inject_rules():
    """二期 A 项（2026-09-17）验收：读端规则层 + 注入等价物（正本常数忠实性）。

    评分正本 = 公司机脚本版字面常数（W_R/LAMBDA/W_I/W_S=0.4/0.05/0.4/0.2、
    LAYER_IMP、RULE_REL_BOOST=2.5、ABSTAIN=W_S*0.5、recency 封顶 0.2、
    activity_date 链 last_active>created>updated）。本测试按正本公式手工
    复算期望值逐条断言；真·跨机一致性（top3 对齐公司机脚本）在笔记本部署
    实测时以判别力查询集校准（§8 A 行验收口径）。
    """
    ok = True

    def check(cond: bool, msg: str):
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {msg}")
        if not cond:
            ok = False

    import math as _math
    from datetime import datetime as _dt, timedelta as _td

    from mempipeline.inject import (ABSTAIN_REL, LAYER_IMP, RECENCY_CAP,
                                    RULE_LAYER, RULE_REL_BOOST, RULE_REL_GATE,
                                    W_I, W_R, W_S, _build_idf_mix,
                                    _days_since, _layer_of, activity_date,
                                    collect_rules, inject, score_mixed)

    # 正本常数字面复核（防手滑改参）
    check((W_R, W_S, W_I) == (0.4, 0.2, 0.4) and RULE_REL_BOOST == 2.5,
          "正本字面常数 W_R/W_S/W_I/RULE_REL_BOOST")
    check(ABSTAIN_REL == W_S * 0.5 and RULE_REL_GATE == W_S * 0.5
          and RECENCY_CAP == 0.2, "弃权线/闸门/封顶与正本同源")
    check(LAYER_IMP == {"01-长期记忆": 0.9, "02-中期记忆": 0.6, "01-规则": 0.9},
          "LAYER_IMP 三层表")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        mem_root = tmp / "mem"
        rule_root = tmp / "rules"
        (mem_root / "01-长期记忆").mkdir(parents=True)
        (mem_root / "02-中期记忆").mkdir(parents=True)
        rule_root.mkdir(parents=True)

        today = _dt.now().strftime("%Y-%m-%d")
        yest = (_dt.now() - _td(days=1)).strftime("%Y-%m-%d")
        # 规则条目：与 query "仓位 管理" 高重叠（ngram 命中充足）触发 boost
        (rule_root / "规则-仓位纪律.md").write_text(
            "---\ntype: rule\ntitle: 仓位纪律\nsummary: 仓位管理纪律\n"
            "importance: 0.9\n---\n\n仓位管理：加仓纪律与减仓纪律。\n", encoding="utf-8")
        # 中期条目：last_active=今天 → recency 满 0.4 但须封顶 0.2
        (mem_root / "02-中期记忆" / "会话-定投-1111.md").write_text(
            f"---\ntype: note\ntitle: 定投纪律\nsummary: 定投与再平衡\n"
            f"memory_tier: medium\nlast_active: {today}\n---\n\n定投纪律：再平衡频率。\n",
            encoding="utf-8")
        # 长期锚点：无 last_active，created=昨天（activity_date 链取 created）
        (mem_root / "01-长期记忆" / "锚-资产配置-2222.md").write_text(
            f"---\ntype: note\ntitle: 资产配置锚\nsummary: 资产配置与仓位\n"
            f"memory_tier: long\ncreated: {yest}\n---\n\n资产配置锚：仓位上限。\n",
            encoding="utf-8")
        # 坏编码文件（读失败计数联动）
        (mem_root / "01-长期记忆" / "会话-坏编码-3333.md").write_bytes(
            b"---\ntitle: bad\n---\n\n\xff\xfe\xff invalid\n")

        rules = collect_rules(rule_root)
        check(len(rules) == 1 and "仓位管理" in rules[0][2], "collect_rules 平铺收集+文本拼接")
        check(rules[0][1].get("title") == "仓位纪律", "规则 frontmatter 解析")

        # 规则条目打分：rec 恒 0、imp=0.4*0.9、boost 闸门生效
        # （layer 显式传：rule_root 目录名数据无关化，inject 主流程对规则集同样显式传）
        fm_r = rules[0][1]
        entries_texts = [t for _p, _fm, t in rules]
        idf1 = _build_idf_mix(entries_texts)
        s_rule = score_mixed(rules[0][0], fm_r, rules[0][2], "仓位 管理", idf1,
                             layer=RULE_LAYER)
        check(s_rule["rec"] == 0.0, f"规则层 Recency 恒 0 (实得 {s_rule['rec']})")
        check(abs(s_rule["imp"] - W_I * 0.9) < 1e-9, f"imp=W_I*0.9 (实得 {s_rule['imp']})")
        check(s_rule["lex"] >= 0.5 and abs(s_rule["rel"] - W_S * s_rule["lex"] * RULE_REL_BOOST) < 1e-9,
              f"boost 生效 rel=W_S*lex*2.5 (lex={s_rule['lex']:.3f} rel={s_rule['rel']:.3f})")

        # 无关规则：rel=0 不 boost（闸门语义）
        (rule_root / "规则-无关主题.md").write_text(
            "---\ntitle: 无关主题\nsummary: 烹饪火候\n---\n\n烹饪火候与食材。\n", encoding="utf-8")
        rules2 = collect_rules(rule_root)
        idf2 = _build_idf_mix([t for _p, _fm, t in rules2])
        s_off = score_mixed(rules2[1][0], rules2[1][1], rules2[1][2], "仓位 管理", idf2,
                            layer=RULE_LAYER)
        check(s_off["lex"] == 0.0 and s_off["rel"] == 0.0, "无关规则零重叠不 boost")

        # recency 封顶：中期 last_active=今天，raw=0.4 但 cap=0.2
        r = inject("定投 纪律", mem_root, rule_root)
        mid = next((s for s in r["top"] if s["layer"] == "02-中期记忆"), None)
        check(mid is not None and mid["rec"] == RECENCY_CAP,
              f"中期 recency 封顶 0.2 (实得 {mid['rec'] if mid else None})")
        check(mid is not None and abs(mid["total"] - (RECENCY_CAP + W_I * 0.6 + mid["rel"])) < 1e-9,
              "total=rec+imp+rel 正本公式")

        # 锚点归类 + activity_date 链（created 优先于 updated/缺失）
        check(any(s["layer"] == "01-长期记忆" for s in r["anchor"]), "长期锚点归类")
        anchor_fm = {"created": yest, "updated": today}
        check(activity_date(anchor_fm) == yest and _days_since(yest) == 1,
              "activity_date 链 last_active>created>updated + days 计算")
        check(_layer_of(Path(r"x/02-中期记忆/a.md")) == "02-中期记忆"
              and _layer_of(Path(r"x/other/a.md")) == "01-长期记忆", "_layer_of 判层")

        # warmup 升序 + abstain + 读失败计数
        check(len(r["warmup"]) >= 2 and r["warmup"][0]["days"] <= r["warmup"][-1]["days"],
              "warmup 按天数升序")
        check(r["read_errors"] == 1, f"读失败计数联动 (实得 {r['read_errors']})")
        r_abstain = inject("量子力学 测不准", mem_root, rule_root)
        check(r_abstain["abstain"] is True, "无关 query 触发弃权线")
        check(r["abstain"] is False, "相关 query 不弃权")
    print("\nA INJECT RULES (2026-09-17):", "ALL PASS" if ok else "SOME FAILED")
    if not ok:
        raise AssertionError("test_inject_rules: 子断言失败")


def test_default_synonyms():
    """AGI-②（2026-09-18）：默认同义表——映射展开、整词边界、快路径端到端。

    收录依据：317 篇定向 df（head=语料高频形式：commit 73/frontmatter 36/audit 35/
    闭环 36/记忆入库 30…；alts 多为 df=0 纯查询词）+ 30 条真实查询日志。
    排除组（不合并）：快照↔备份 / IRR↔年化 / ETF↔指数基金（语义冲突）。
    「元数据」alt 已删（2026-09-18 GOLDEN 回归实证）：中文通用词强映射到特指 YAML
    头引发词法漂移（"9月5日…元数据 治理" 锚点被 frontmatter 主题笔记挤出 Top-5，
    hit_rate 1.0→0.9167），用户拍板删 alt 保门禁 1.0。
    已知边界：整词语义，中文连写（如「提交的精确判据」）不触发归一。
    """
    import tempfile
    from pathlib import Path
    from mempipeline.recall import (DEFAULT_SYNONYMS, MemoryRecall, TFIDFIndex,
                                    map_terms, syn_norm_map)
    from mempipeline.protocol import TIER_DIR
    norm = syn_norm_map(DEFAULT_SYNONYMS)
    assert len(DEFAULT_SYNONYMS) == 8, f"默认表应为 8 组 {sorted(DEFAULT_SYNONYMS)}"
    assert norm["commit"] == "commit" and norm["提交"] == "commit", "head 自映射+alts 归一"
    # 查询侧整词归一：空格分词的查询触发（真实日志习惯："沙箱 拦截"）
    assert map_terms("知识库 git 提交 判据", norm) == "知识库 git commit 判据"
    # 「元数据」不归一（alt 已删，见 docstring）：中文通用词保留原词形
    assert map_terms("元数据 治理", norm) == "元数据 治理"
    # 已知边界：中文连写不触发（整词语义，与 _norm_doc 口径一致）
    assert map_terms("git 提交的精确判据", norm) == "git 提交的精确判据"
    assert map_terms("随便查询", norm) == "随便查询"
    # 端到端（快路径保留）：文档侧 alts 独立成词时归入主词空间，英文查询命中中文文档
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        mem = tmp / "mem"
        (mem / TIER_DIR["long"]).mkdir(parents=True)
        (mem / TIER_DIR["long"] / "n1.md").write_text(
            "---\ntype: note\ntitle: 判据\nsummary: s\n---\n\ngit 提交 判据：工作树干净。\n",
            encoding="utf-8")
        (mem / TIER_DIR["long"] / "n2.md").write_text(
            "---\ntype: note\ntitle: 无关\nsummary: s\n---\n\n钢琴指法练习记录。\n",
            encoding="utf-8")
        idx = TFIDFIndex(tmp / "idx.sqlite", synonyms=DEFAULT_SYNONYMS)
        assert idx.build(mem) == 2, "两篇文档应全部索引"
        r = MemoryRecall(mem, index=idx).recall("git commit", k=5)
        # 字符 ngram 下短 bigram 偶然重叠难免（如 title 的 it），契约是排序正确而非硬过滤
        assert r and "n1" in r[0][0], f"commit 查询应 top1 命中提交文档 {[(Path(p).name, s) for p, s in r]}"
        # 反向：中文 alt 查询命中含英文 head 的文档
        (mem / TIER_DIR["long"] / "n3.md").write_text(
            "---\ntype: note\ntitle: 流程\nsummary: s\n---\n\npre-commit 钩子先于 commit 运行。\n",
            encoding="utf-8")
        assert idx.build(mem) == 1, "增量 build 只收新文档"
        r2 = MemoryRecall(mem, index=idx).recall("提交 记录", k=5)
        assert any("n3" in p for p, _ in r2), f"提交 查询应命中 commit 文档 {[p for p, _ in r2]}"
        # 快路径同表放行（2026-09-18）：同表注入 MemoryRecall(index, synonyms) 与
        # 索引口径对等（双侧归一同构），recall() 放行快路径——行为级观测：删除
        # 镜像文件后仍能召回 = 只扫索引未读盘；异表注入则回落全扫（读不到已删文件）。
        (mem / TIER_DIR["long"] / "n3.md").unlink()
        r3 = MemoryRecall(mem, index=idx, synonyms=DEFAULT_SYNONYMS).recall("提交 记录", k=5)
        assert any("n3" in p for p, _ in r3), f"同表注入应走快路径命中已删镜像 {[p for p, _ in r3]}"
        other = TFIDFIndex(tmp / "idx2.sqlite", synonyms={})
        other.build(mem)
        r4 = MemoryRecall(mem, index=other, synonyms=DEFAULT_SYNONYMS).recall("提交 记录", k=5)
        assert not any("n3" in p for p, _ in r4), "异表注入应回落全扫（镜像已删不可命中）"
        other.close()
        idx.close()


if __name__ == "__main__":
    _ok = main()
    for _fn in (test_crash_recover_sidecar, test_tfidf_recall, test_recall_golden,
                test_project_isolation, test_tfidf_index, test_tfidf_vacuum,
                test_g_error_visibility, test_inject_rules, test_default_synonyms):
        try:
            _fn()
        except AssertionError as _e:
            print(f"  [ERR] {_e}")
            _ok = False
    sys.exit(0 if _ok else 1)
