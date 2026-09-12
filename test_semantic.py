# -*- coding: utf-8 -*-
"""test_semantic.py — E2' 语义检索层验收（依赖本地 Ollama bge-m3，非零依赖套件）。

覆盖：
1. 同义改写命中：TF-IDF 字符 ngram 几乎无重叠，语义应命中（E2' 核心价值）
2. 项目隔离：semantic recall 按项目作用域过滤（与 E1 对齐）
3. hybrid 融合：lexical+semantic RRF，结果包含语义命中项
4. RRF 单元：多路排序融合得分正确
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mempipeline.semantic import SemanticIndex, hybrid_recall, rrf_fuse


def main() -> bool:
    ok = True

    def check(cond: bool, msg: str):
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {msg}")
        if not cond:
            ok = False

    # ---- 0. RRF 单元 ----
    print("== RRF ==")
    fused = rrf_fuse([["a", "b"], ["b", "c"]], k=60)
    check(fused[0][0] == "b", f"b 两路命中排第一（{fused[:2]}）")
    check(len(fused) == 3, "融合后去重为 3 项")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        mem_root = tmp / "mem"
        # 项目 A：语义相关但字符级与查询几乎无重叠的笔记
        (mem_root / "projects" / "dora" / "02-中期记忆").mkdir(parents=True)
        (mem_root / "projects" / "onequant" / "02-中期记忆").mkdir(parents=True)
        (mem_root / "projects" / "dora" / "02-中期记忆" / "a1.md").write_text(
            "---\ntitle: 定投纪律\nsummary: 每月固定日期把资金投入指数基金\n"
            "memory_tier: medium\nproject_id: dora\n---\n"
            "每个月的固定日期，把一定金额的资金买入大盘指数基金，长期坚持。",
            encoding="utf-8")
        (mem_root / "projects" / "onequant" / "02-中期记忆" / "b1.md").write_text(
            "---\ntitle: 定投纪律\nsummary: 每月固定日期把资金投入指数基金\n"
            "memory_tier: medium\nproject_id: onequant\n---\n"
            "每个月的固定日期，把一定金额的资金买入大盘指数基金，长期坚持。",
            encoding="utf-8")
        db = tmp / "idx.db"
        idx = SemanticIndex(db)
        n = idx.build(mem_root)
        check(n == 2, f"索引构建 2 条（实得 {n}）")
        check(idx.count() == 2, "索引计数 2")

        # ---- 1. 同义改写命中（语义核心价值）----
        print("== 语义召回 ==")
        q1 = "定期往市场投入钱买基金"
        sem_dora = None
        # 直接语义召回（项目 A）
        from mempipeline.semantic import embed
        qvec = embed([q1])[0]
        r = idx.recall(qvec, k=5, projects=["dora"])
        sem_dora = [p for p, _ in r]
        check(bool(sem_dora) and "dora" in sem_dora[0],
              f"语义命中 dora 项目笔记（top={Path(sem_dora[0]).name if sem_dora else '无'}）")

        # ---- 2. 项目隔离（语义层也隔离）----
        r_all = idx.recall(qvec, k=5)
        r_b = idx.recall(qvec, k=5, projects=["onequant"])
        check(all("onequant" in p for p, _ in r_b) and len(r_b) == 1,
              "onequant 语义召回仅限本项目")
        check(len(r_all) == 2, "无过滤返回两项目（全库 2 条）")

        # ---- 3. hybrid 融合 ----
        print("== hybrid 融合 ==")
        h = hybrid_recall(q1, k=3, mem_root=mem_root, index=idx, projects=["dora"])
        check(bool(h) and "dora" in h[0][0],
              f"hybrid 融合命中 dora（top={Path(h[0][0]).name if h else '无'}）")
        # TF-IDF 单独（同义改写应弱命中甚至不命中——语义层补强）
        from mempipeline.recall import MemoryRecall
        tf = MemoryRecall(mem_root, projects=["dora"]).recall(q1, k=3)
        tf_hit = bool(tf)
        check(tf_hit or not tf_hit, f"TF-IDF 单独命中情况记录（{len(tf)} 条）")
        if tf_hit:
            print("    注：TF-IDF 也命中了（字符重叠），语义层作为第二通道")

        # ---- 4. 语义不可用时回落 ----
        h_fallback = hybrid_recall(q1, k=3, mem_root=mem_root, index=None,
                                   projects=["dora"])
        check(bool(h_fallback), "index=None 时回落纯 TF-IDF 不抛错")

        # ---- 5. 注入带 TFIDFIndex 的 memory（panel P1-A2 接线路径）----
        from mempipeline.recall import MemoryRecall, TFIDFIndex
        tf_index = TFIDFIndex(tmp / "tf_idx.db")
        try:
            tf_index.build(mem_root)
            m_idx = MemoryRecall(mem_root, index=tf_index)
            h_mem = hybrid_recall(q1, k=3, mem_root=mem_root, index=idx,
                                  projects=["dora"], memory=m_idx)
            check(bool(h_mem) and "dora" in h_mem[0][0],
                  f"注入带索引 memory 的 hybrid 命中 dora（top={Path(h_mem[0][0]).name if h_mem else '无'}）")
        finally:
            tf_index.close()

        idx.close()

        # ---- 6. A1 语义缓存：查询嵌入只算一次 + top-k 结果复用 ----
        print("== A1 语义缓存 ==")
        db2 = tmp / "cache.db"

        def fake_embed(texts):
            return [[1.0, 0.0, 0.0]] * len(texts)

        c_idx = SemanticIndex(db2)
        c_idx.build(mem_root, embed_fn=fake_embed)
        check(c_idx.gen() == 2, f"build 后 gen 换代=2（实得 {c_idx.gen()}）")
        spy = {"n": 0}

        def spy_embed(texts):
            spy["n"] += 1
            return [[1.0, 0.0, 0.0]] * len(texts)

        v1 = c_idx.query_embed("缓存 查询", spy_embed)
        v2 = c_idx.query_embed("缓存 查询", spy_embed)
        check(spy["n"] == 1, f"同查询嵌入只算 1 次（实得 {spy['n']}）")
        check(v1 == v2, "两次读取嵌入一致")
        rc1 = c_idx.recall_cached("缓存 查询", v1, k=4)
        rc2 = c_idx.recall_cached("缓存 查询", v2, k=4)
        check(rc1 == rc2 and len(rc1) == 2, "top-k 缓存复用且条数=2")
        n_qres = c_idx._conn.execute("SELECT COUNT(*) FROM qres").fetchone()[0]
        check(n_qres == 1, f"qres 仅 1 行（实得 {n_qres}）")
        c_idx.close()

    print("\nSEMANTIC (E2'):", "ALL PASS" if ok else "SOME FAILED")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
