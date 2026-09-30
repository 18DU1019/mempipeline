# -*- coding: utf-8 -*-
"""test_bench_precision.py — bench_recall_precision 逻辑验证（合成小语料，全离线）。

覆盖（自运行 + pytest 双轨，风格对齐 test_recall_p0_1.py）：
1. 命中判定：词面查询 top1 命中（first_hit_rank=1）；期望路径不含命中篇时
   top5 命中但 top1 不命中；完全不相关时双 False。
2. 工件结构：run_bench 产物含 run_id / 语料指纹（笔记数+mtime 聚合哈希）/
   逐 query 明细 / top1、top5 汇总，且命中率数值与明细一致。
3. 汇总计算：summarize 的 rate 数学正确、by_category 分布与明细一致。
4. 标注集校验：load_eval 对缺字段 / 非法 category / 空 queries 抛 ValueError。
5. 语料指纹敏感性：新增一篇笔记后指纹变化；同语料两次计算结果一致。
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from bench_recall_precision import (corpus_fingerprint, evaluate_query,
                                    load_eval, run_bench, summarize)


def _make_corpus(root: Path) -> Path:
    """合成 4 篇镜像：两篇 01 层、两篇 02 层，主题互不重叠。"""
    mem_root = root / "mem"
    (mem_root / "01-长期记忆").mkdir(parents=True)
    (mem_root / "02-中期记忆").mkdir(parents=True)
    (mem_root / "01-长期记忆" / "仓位调度.md").write_text(
        "---\ntype: note\ntitle: 仓位调度\nsummary: 量化仓位\n---\n\n"
        "量化仓位调度规则：按信号强度分配仓位。\n", encoding="utf-8")
    (mem_root / "01-长期记忆" / "市场风险.md").write_text(
        "---\ntype: note\ntitle: 市场风险\nsummary: 风险\n---\n\n"
        "市场风险提示：控制风险敞口。\n", encoding="utf-8")
    (mem_root / "02-中期记忆" / "咖啡烘焙.md").write_text(
        "---\ntype: note\ntitle: 咖啡\nsummary: 烘焙\n---\n\n"
        "浅烘豆子咖啡烘焙度记录。\n", encoding="utf-8")
    (mem_root / "02-中期记忆" / "定投纪律.md").write_text(
        "---\ntype: note\ntitle: 定投\nsummary: 纪律\n---\n\n"
        "每月固定日定投纪律，仓位按金字塔加仓。\n", encoding="utf-8")
    return mem_root


def _eval_doc() -> dict:
    return {
        "name": "eval_synth_v0", "version": 0,
        "queries": [
            {"id": "s1", "category": "literal", "query": "仓位 调度",
             "expected_paths": ["01-长期记忆/仓位调度.md"]},
            {"id": "s2", "category": "cross_dir", "query": "仓位",
             "expected_paths": ["01-长期记忆/仓位调度.md",
                                "02-中期记忆/定投纪律.md"]},
            {"id": "s3", "category": "paraphrase", "query": "量子隧穿效应",
             "expected_paths": ["01-长期记忆/市场风险.md"]},
        ],
    }


def test_hit_evaluation():
    # 1. 命中判定三分支：top1 命中 / top5 命中非首位 / 全 miss
    with tempfile.TemporaryDirectory() as td:
        mem_root = _make_corpus(Path(td))
        rec1 = evaluate_query(
            "s1", "仓位 调度", "literal", ["01-长期记忆/仓位调度.md"],
            [(str(mem_root / "01-长期记忆" / "仓位调度.md"), 0.5),
             (str(mem_root / "02-中期记忆" / "定投纪律.md"), 0.2)],
            mem_root)
        assert rec1["top1_hit"] and rec1["top5_hit"]
        assert rec1["first_hit_rank"] == 1
        assert rec1["hits"][0]["path"] == "01-长期记忆/仓位调度.md"
        rec2 = evaluate_query(
            "s2", "仓位", "cross_dir", ["02-中期记忆/定投纪律.md"],
            [(str(mem_root / "01-长期记忆" / "仓位调度.md"), 0.5),
             (str(mem_root / "02-中期记忆" / "定投纪律.md"), 0.2)],
            mem_root)
        assert not rec2["top1_hit"] and rec2["top5_hit"]
        assert rec2["first_hit_rank"] == 2
        rec3 = evaluate_query(
            "s3", "无关查询", "paraphrase", ["01-长期记忆/市场风险.md"],
            [(str(mem_root / "02-中期记忆" / "咖啡烘焙.md"), 0.1)],
            mem_root)
        assert not rec3["top1_hit"] and not rec3["top5_hit"]
        assert rec3["first_hit_rank"] is None


def test_artifact_structure():
    # 2. run_bench 工件：必备键齐全 + 命中率与明细一致 + 词面查询真实命中
    with tempfile.TemporaryDirectory() as td:
        mem_root = _make_corpus(Path(td))
        art = run_bench(mem_root, _eval_doc(), k=5)
        for key in ("run_id", "engine", "mem_root", "k", "corpus_fingerprint",
                    "eval_set", "queries", "summary"):
            assert key in art, f"工件缺键 {key}"
        assert len(art["run_id"]) == 16 and art["run_id"].endswith("Z")
        fp = art["corpus_fingerprint"]
        assert fp["note_count"] == 4, f"笔记数应 4，实得 {fp['note_count']}"
        assert len(fp["sha256_mtime_agg"]) == 64
        assert art["eval_set"]["name"] == "eval_synth_v0"
        assert len(art["queries"]) == 3
        s = art["summary"]
        assert s["n_queries"] == 3
        # 明细与汇总自洽
        assert s["top1_hits"] == sum(1 for r in art["queries"]
                                     if r["top1_hit"])
        assert s["top5_hits"] == sum(1 for r in art["queries"]
                                     if r["top5_hit"])
        assert abs(s["top1_rate"] - round(s["top1_hits"] / 3, 4)) < 1e-9
        # 词面查询在真实 lexical 链路上应 top1 命中（s1）
        assert art["queries"][0]["top1_hit"], (
            f"词面查询未命中: {art['queries'][0]['hits']}")
        assert s["top1_hits"] >= 1, "合成语料基线 top1 应至少 1 命中"


def test_summarize_math():
    # 3. summarize：rate 数学 + by_category 分布
    recs = [
        {"category": "literal", "top1_hit": True, "top5_hit": True},
        {"category": "literal", "top1_hit": False, "top5_hit": True},
        {"category": "paraphrase", "top1_hit": False, "top5_hit": False},
        {"category": "cross_dir", "top1_hit": True, "top5_hit": True},
    ]
    s = summarize(recs)
    assert s["n_queries"] == 4
    assert s["top1_hits"] == 2 and s["top1_rate"] == 0.5
    assert s["top5_hits"] == 3 and abs(s["top5_rate"] - 0.75) < 1e-9
    assert s["by_category"]["literal"] == {
        "n": 2, "top1_hits": 1, "top5_hits": 2}
    assert s["by_category"]["paraphrase"]["top5_hits"] == 0
    empty = summarize([])
    assert empty["n_queries"] == 0 and empty["top1_rate"] == 0.0


def test_load_eval_validation():
    # 4. load_eval 非法结构必须抛 ValueError
    base = {"name": "x", "queries": [
        {"id": "a", "category": "literal", "query": "q",
         "expected_paths": ["p"]}]}
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "eval.json"

        def _write(doc):
            p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            return load_eval(p)

        _write(base)  # 合法结构不抛
        for bad, hint in [
            ({"name": "x"}, "缺 queries"),
            ({"queries": []}, "空 queries"),
            ({"queries": [{"id": "a", "category": "literal", "query": "q"}]},
             "缺 expected_paths"),
            ({"queries": [{"id": "a", "category": "nonsense", "query": "q",
                           "expected_paths": ["p"]}]}, "非法 category"),
            ({"queries": [dict(base["queries"][0]),
                          dict(base["queries"][0])]}, "id 重复"),
        ]:
            try:
                _write(bad)
            except ValueError:
                pass
            else:
                raise AssertionError(f"非法标注集未抛 ValueError: {hint}")


def test_fingerprint_sensitivity():
    # 5. 指纹：同语料两次一致；新增笔记后变化
    with tempfile.TemporaryDirectory() as td:
        mem_root = _make_corpus(Path(td))
        fp1 = corpus_fingerprint(mem_root)
        fp2 = corpus_fingerprint(mem_root)
        assert fp1 == fp2, "同语料指纹应可复现"
        (mem_root / "02-中期记忆" / "新笔记.md").write_text(
            "---\ntype: note\ntitle: 新\n---\n\n新增语料。\n",
            encoding="utf-8")
        fp3 = corpus_fingerprint(mem_root)
        assert fp3["note_count"] == 5
        assert fp3["sha256_mtime_agg"] != fp1["sha256_mtime_agg"], \
            "语料变化后聚合哈希应改变"


def main() -> bool:
    ok = True

    def check(cond: bool, msg: str):
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {msg}")
        if not cond:
            ok = False

    print("== bench_recall_precision 逻辑验证（合成语料）==")
    cases = [
        ("1. 命中判定三分支（top1/top5/miss）", test_hit_evaluation),
        ("2. 工件结构与明细-汇总自洽", test_artifact_structure),
        ("3. summarize 数学与分类分布", test_summarize_math),
        ("4. load_eval 非法结构抛 ValueError", test_load_eval_validation),
        ("5. 语料指纹可复现且对增删敏感", test_fingerprint_sensitivity),
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
