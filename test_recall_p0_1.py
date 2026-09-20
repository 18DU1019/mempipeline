# -*- coding: utf-8 -*-
"""test_recall_p0_1.py — P0-1 真倒排（docgram 关联表 + SQL 候选预筛）行为快照。

规格来源：检验报告《mempipeline架构优化与重构方案_2026-09-21》第六节 P0-1：
「gram 表加 doc 列/建关联表，SQL 先筛候选；守住『与全扫同构』红线（打分不变，
仅候选集预筛）」。

覆盖（自运行 + pytest 双轨）：
1. 双路径一致性：新 recall（SQL 候选预筛）与旧全表扫描参考实现，
   多组查询的 (path, score) 列表逐项精确相等（打分不变红线）。
2. 候选集等价：SQL 预筛候选路径集 == 旧「any(g in grams)」过滤后的文档集；
   且未命中文档不在候选集（候选集 < 全表，验证真倒排筛减）。
3. schema 回填：旧库（doc 有数据、无 docgram）实例化后自动回填 docgram，
   recall 结果与全新 build 索引一致。
4. 维护同步：增量 build 新增文档写入 docgram；红act 剔除同步删 docgram。
5. 空索引安全：未 build 时空 docgram → recall 返回 []。
"""
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mempipeline.recall import TFIDFIndex, _score_tfidf, _tokens


def _legacy_recall(idx, query: str, k: int = 8):
    """旧版 recall（P0-1 提交前的全表扫描实现），作为双路径一致性参考。

    与 recall.py 的旧实现逐行同构：只扫 doc 表、Python 层过滤命中 ngram，
    与查询共用同一 gram 表 idf 与 _score_tfidf。
    """
    qterms = idx._query_terms(query)
    if not qterms:
        return []
    qgrams = _tokens(" ".join(qterms))
    if not qgrams:
        return []
    qfreq = Counter(qgrams)
    qmax = max(qfreq.values()) or 1.0
    idf: dict[str, float] = {}
    for g in set(qfreq):
        rows = idx._conn.execute(
            "SELECT df FROM gram WHERE gram=?", (g,)).fetchall()
        if rows:
            idf[g] = rows[0][0]
    scored: list[tuple[str, float]] = []
    for path, norms, ngrams_str in idx._conn.execute(
            "SELECT path, norm, ngrams FROM doc"):
        grams: dict[str, float] = {}
        for pair in ngrams_str.split():
            if ":" in pair:
                g, c = pair.split(":", 1)
                grams[g] = float(c)
        if not any(g in grams for g in qfreq):
            continue
        s = _score_tfidf(qfreq, qmax, idf, Counter(grams), len(norms))
        if s > 0:
            scored.append((path, s))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]


def _legacy_candidate_paths(idx, qfreq: Counter[str]) -> set[str]:
    """旧过滤条件的文档集合：doc 表任一文档含任一查询 ngram。"""
    out: set[str] = set()
    for path, _norm, ngrams_str in idx._conn.execute(
            "SELECT path, norm, ngrams FROM doc"):
        grams = set()
        for pair in ngrams_str.split():
            if ":" in pair:
                grams.add(pair.split(":", 1)[0])
        if any(g in grams for g in qfreq):
            out.add(path)
    return out


def _make_mirror(root: Path) -> Path:
    """构造 3 篇镜像：a 与 b 部分同词，c 完全无关（保证候选集 < 全表）。"""
    mem_root = root / "mem"
    (mem_root / "01-长期记忆").mkdir(parents=True)
    (mem_root / "02-中期记忆").mkdir(parents=True)
    (mem_root / "02-中期记忆" / "a-仓位调度.md").write_text(
        "---\ntype: note\ntitle: 仓位调度\nsummary: 量化仓位\n---\n\n"
        "量化仓位调度规则：按信号强度分配仓位。\n", encoding="utf-8")
    (mem_root / "02-中期记忆" / "b-市场风险.md").write_text(
        "---\ntype: note\ntitle: 市场风险\nsummary: 风险\n---\n\n"
        "市场风险提示：控制风险敞口。\n", encoding="utf-8")
    (mem_root / "02-中期记忆" / "c-咖啡记录.md").write_text(
        "---\ntype: note\ntitle: 咖啡\nsummary: 烘焙\n---\n\n"
        "浅烘豆子咖啡烘焙度记录。\n", encoding="utf-8")
    return mem_root


def _build_candidate_set(idx, query: str) -> set[str]:
    """新路径 SQL 预筛候选集（白盒：按 docgram 查）。"""
    qterms = idx._query_terms(query)
    qgrams = _tokens(" ".join(qterms))
    qfreq = Counter(qgrams)
    cand: set[str] = set()
    for g in set(qfreq):
        for (path,) in idx._conn.execute(
                "SELECT path FROM docgram WHERE gram=?", (g,)):
            cand.add(path)
    return cand


def test_dual_path_consistency():
    # 1. 打分不变红线：新 recall == 旧全扫参考，多组查询精确相等
    with tempfile.TemporaryDirectory() as td:
        mem_root = _make_mirror(Path(td))
        idx = TFIDFIndex(Path(td) / "idx.db")
        idx.build(mem_root)
        for q in ("仓位 调度", "市场 风险", "风险", "咖啡", "仓位",
                  "不存在的词", "量化 仓位 调度"):
            new = idx.recall(q, k=5)
            old = _legacy_recall(idx, q, k=5)
            assert new == old, (
                f"双路径打分不一致 query={q!r}\n  new={new}\n  old={old}")
            assert isinstance(new, list) and all(
                isinstance(p, str) and isinstance(s, float) for p, s in new)
        idx.close()


def test_candidate_set_equiv():
    # 2. 候选集 = 旧过滤集；且未命中文档（c）不在候选集
    with tempfile.TemporaryDirectory() as td:
        mem_root = _make_mirror(Path(td))
        idx = TFIDFIndex(Path(td) / "idx.db")
        idx.build(mem_root)
        total = idx._conn.execute("SELECT COUNT(*) FROM doc").fetchone()[0]
        assert total == 3, f"全表应为 3 篇，实得 {total}"
        for q in ("仓位 调度", "市场 风险", "风险", "咖啡"):
            qterms = idx._query_terms(q)
            qfreq = Counter(_tokens(" ".join(qterms)))
            cand = _build_candidate_set(idx, q)
            expect = _legacy_candidate_paths(idx, qfreq)
            assert cand == expect, (
                f"候选集不等 query={q!r}\n  cand={sorted(cand)}\n"
                f"  expect={sorted(expect)}")
            assert len(cand) < total, (
                f"候选集应小于全表（真倒排筛减）query={q!r} "
                f"cand={len(cand)} total={total}")
        idx.close()


def test_schema_backfill():
    # 3. 旧库（doc 有数据、无 docgram）实例化自动回填，recall 与全新 build 一致
    with tempfile.TemporaryDirectory() as td:
        mem_root = _make_mirror(Path(td))
        # 手工构造旧库：doc + gram 两表，无 docgram，doc 插入与 build 同构的数据
        old = Path(td) / "old.db"
        idx_new = TFIDFIndex(Path(td) / "fresh.db")
        idx_new.build(mem_root)
        _c = sqlite3.connect(str(old))
        _c.execute("CREATE TABLE doc (path TEXT PRIMARY KEY,"
                   " ckey TEXT, norm TEXT NOT NULL, ngrams TEXT NOT NULL)")
        _c.execute("CREATE TABLE gram (gram TEXT NOT NULL, df REAL NOT NULL)")
        _c.execute("CREATE INDEX IF NOT EXISTS idx_gram ON gram(gram)")
        for path, ckey, norm, ngrams in idx_new._conn.execute(
                "SELECT path, ckey, norm, ngrams FROM doc"):
            _c.execute(
                "INSERT INTO doc (path, ckey, norm, ngrams) VALUES (?,?,?,?)",
                (path, ckey, norm, ngrams))
        for gram, df in idx_new._conn.execute("SELECT gram, df FROM gram"):
            _c.execute("INSERT INTO gram (gram, df) VALUES (?,?)", (gram, df))
        _c.commit()
        _c.close()
        idx_new.close()

        idx_old = TFIDFIndex(old)  # 触发 docgram 建表 + 自动回填
        dg_n = idx_old._conn.execute(
            "SELECT COUNT(*) FROM docgram").fetchone()[0]
        assert dg_n > 0, f"旧库 docgram 应被回填，实得 {dg_n}"
        # 与全新 build 索引的结果一致性（同一数据源）
        fresh = TFIDFIndex(Path(td) / "fresh.db")
        for q in ("仓位 调度", "市场 风险", "咖啡"):
            assert idx_old.recall(q, k=5) == fresh.recall(q, k=5), (
                f"回填索引 recall 与全新索引不一致 query={q!r}")
        fresh.close()
        idx_old.close()


def test_build_maintenance():
    # 4. 增量 build 同步写 docgram；红act 剔除同步删 docgram
    with tempfile.TemporaryDirectory() as td:
        mem_root = _make_mirror(Path(td))
        idx = TFIDFIndex(Path(td) / "idx.db")
        idx.build(mem_root)
        assert idx._conn.execute(
            "SELECT COUNT(*) FROM docgram").fetchone()[0] > 0
        # 增量：新增 1 篇
        (mem_root / "02-中期记忆" / "d-定投.md").write_text(
            "---\ntype: note\ntitle: 定投\nsummary: 纪律\n---\n\n"
            "每月固定日定投纪律。\n", encoding="utf-8")
        added = idx.build(mem_root)
        assert added == 1, f"增量 build 应新增 1 篇，实得 {added}"
        assert idx._conn.execute(
            "SELECT COUNT(*) FROM docgram WHERE path LIKE '%d-定投.md'"
        ).fetchone()[0] > 0, "新增文档应写入 docgram"
        # 红act：c 篇加 status: redacted 后重新 build → docgram 剔除
        c_path = mem_root / "02-中期记忆" / "c-咖啡记录.md"
        c_path.write_text(
            "---\ntype: note\ntitle: 咖啡\nstatus: redacted\n---\n\n"
            "浅烘豆子咖啡烘焙度记录。\n", encoding="utf-8")
        idx.build(mem_root)
        assert idx._conn.execute(
            "SELECT COUNT(*) FROM docgram WHERE path=?", (str(c_path),)
        ).fetchone()[0] == 0, "红act 剔除后 docgram 应同步删除"
        assert idx.recall("咖啡") == [], "红act 文档不应再被召回"
        idx.close()


def test_empty_index_safe():
    # 5. 未 build 时空 docgram → recall 返回 []（调用方自行回退全扫）
    with tempfile.TemporaryDirectory() as td:
        idx = TFIDFIndex(Path(td) / "idx.db")
        assert idx.recall("任意 查询") == [], "空索引 recall 应返回 []"
        assert idx.recall("") == [] and idx.recall("   ") == []
        idx.close()


def main() -> bool:
    ok = True

    def check(cond: bool, msg: str):
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {msg}")
        if not cond:
            ok = False

    print("== P0-1 真倒排（docgram + SQL 候选预筛）==")
    cases = [
        ("1. 双路径打分一致（新 recall == 旧全扫参考）", test_dual_path_consistency),
        ("2. 候选集 = 旧过滤集且小于全表", test_candidate_set_equiv),
        ("3. 旧库 schema 自动回填 docgram", test_schema_backfill),
        ("4. build 增量/红act 剔除同步维护 docgram", test_build_maintenance),
        ("5. 空索引安全返回 []", test_empty_index_safe),
    ]
    for msg, fn in cases:
        try:
            fn()
            check(True, msg)
        except AssertionError as e:
            check(False, f"{msg}（{e}）")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
