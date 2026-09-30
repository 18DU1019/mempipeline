# -*- coding: utf-8 -*-
"""bench_recall_precision.py — 召回准确率基线测量件（report-only，不设门禁）。

背景：test_recall_p0_1.py 只守新旧实现一致性（dual-path equivalence），全仓没有
「给定查询应命中哪篇」的准确率度量。本件补基线：内置人工标注集
eval_precision_v1.json（18 条，覆盖词面命中 / 同义改写 / 跨目录三类），对真实
vault 记忆镜像（mem_root）离线跑 lexical recall（MemoryRecall 全扫 +
DEFAULT_SYNONYMS，与 panel/semantic 生产口径一致，零 embedding 依赖），量化
top-1 / top-5 命中率，产出可回放 JSON 工件，作为未来改进的参照起点。

纪律边界：
- P3.16 引擎冻结：只调用 mempipeline.recall 公开 API，零修改包内文件；
- report-only：无阈值门禁，仅一条管道健全性断言 top1_hits > 0（防链路整体断裂，
  命中率为 0 说明语料/索引/打分某一环坏了，属测量件自身失效而非策略退步）；
- fail loud：mem_root 不可读或标注集非法即报错退出（码 2），不静默出空基线。

用法：
    python bench_recall_precision.py [--mem-root PATH] [--eval PATH]
                                     [--out-dir PATH] [--k 5]
工件：bench_results/recall_precision_<run_id>.json（run_id=UTC 时间戳）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mempipeline.recall import (DEFAULT_SYNONYMS, MemoryRecall,  # noqa: E402
                                scan_tier_dirs)
from mempipeline.protocol import TIER_DIR  # noqa: E402

DEFAULT_EVAL = ROOT / "eval_precision_v1.json"
DEFAULT_OUT_DIR = ROOT / "bench_results"
CATEGORIES = ("literal", "paraphrase", "cross_dir")
ENGINE_TAG = ("mempipeline.recall.MemoryRecall(lexical full-scan,"
              " synonyms=DEFAULT_SYNONYMS)")


def load_eval(path: Path) -> dict:
    """读标注集并做结构校验；非法即抛 ValueError（调用方负责 fail loud）。"""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    queries = doc.get("queries")
    if not isinstance(queries, list) or not queries:
        raise ValueError(f"标注集 {path} 缺 queries 或为空")
    seen: set[str] = set()
    for i, q in enumerate(queries):
        for field in ("id", "query", "expected_paths"):
            if not q.get(field):
                raise ValueError(f"queries[{i}] 缺字段 {field}")
        if q["id"] in seen:
            raise ValueError(f"queries[{i}] id 重复: {q['id']}")
        seen.add(q["id"])
        if q.get("category") not in CATEGORIES:
            raise ValueError(
                f"queries[{i}] category 非法: {q.get('category')!r}"
                f"（允许 {CATEGORIES}）")
        if not all(isinstance(p, str) for p in q["expected_paths"]):
            raise ValueError(f"queries[{i}] expected_paths 必须全为字符串")
    return doc


def corpus_fingerprint(mem_root: Path) -> dict:
    """语料指纹：笔记数 + 逐篇 (相对路径|mtime_ns|size) 聚合 SHA-256。

    扫描口径与 MemoryRecall 候选集一致（scan_tier_dirs × tier 目录 × *.md 一层），
    保证工件里「语料变了没有」可被复现比对。
    """
    entries: list[str] = []
    for tier in TIER_DIR.values():
        for d in scan_tier_dirs(mem_root, tier):
            for md in sorted(d.glob("*.md")):
                st = md.stat()
                rel = md.relative_to(mem_root).as_posix()
                entries.append(f"{rel}|{st.st_mtime_ns}|{st.st_size}")
    agg = hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()
    return {"note_count": len(entries), "sha256_mtime_agg": agg}


def _rel(path_str: str, mem_root: Path) -> str:
    """recall 返回的绝对路径 → 相对 mem_root 的 POSIX 路径（比对键）。"""
    try:
        return Path(path_str).relative_to(mem_root).as_posix()
    except ValueError:
        return Path(path_str).name


def evaluate_query(qid: str, query: str, category: str,
                   expected_paths: list[str],
                   hits: list[tuple[str, float]],
                   mem_root: Path) -> dict:
    """单查询命中明细：top1/top5 命中判定 + 首个命中排名。"""
    expected = {p.strip() for p in expected_paths}
    hits_detail = [{"rank": i + 1, "path": _rel(p, mem_root),
                    "score": round(s, 6)}
                   for i, (p, s) in enumerate(hits)]
    hit_ranks = [h["rank"] for h in hits_detail if h["path"] in expected]
    return {
        "id": qid,
        "query": query,
        "category": category,
        "expected_paths": sorted(expected),
        "hits": hits_detail,
        "top1_hit": bool(hit_ranks) and hit_ranks[0] == 1,
        "top5_hit": bool(hit_ranks),
        "first_hit_rank": hit_ranks[0] if hit_ranks else None,
    }


def summarize(records: list[dict]) -> dict:
    """逐查询明细 → top1/top5 汇总命中率（含分类分布）。"""
    n = len(records)
    top1 = sum(1 for r in records if r["top1_hit"])
    top5 = sum(1 for r in records if r["top5_hit"])
    by_cat: dict[str, dict] = {}
    for cat in CATEGORIES:
        sub = [r for r in records if r["category"] == cat]
        if not sub:
            continue
        by_cat[cat] = {
            "n": len(sub),
            "top1_hits": sum(1 for r in sub if r["top1_hit"]),
            "top5_hits": sum(1 for r in sub if r["top5_hit"]),
        }
    return {
        "n_queries": n,
        "top1_hits": top1,
        "top1_rate": round(top1 / n, 4) if n else 0.0,
        "top5_hits": top5,
        "top5_rate": round(top5 / n, 4) if n else 0.0,
        "by_category": by_cat,
    }


def run_bench(mem_root: Path, eval_doc: dict, k: int = 5) -> dict:
    """对 mem_root 离线跑 lexical recall，产出完整工件 dict（不落盘）。"""
    recall = MemoryRecall(mem_root, synonyms=DEFAULT_SYNONYMS)
    records = []
    for q in eval_doc["queries"]:
        hits = recall.recall(q["query"], k=k)
        records.append(evaluate_query(q["id"], q["query"], q["category"],
                                      q["expected_paths"], hits, mem_root))
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return {
        "run_id": run_id,
        "engine": ENGINE_TAG,
        "mem_root": str(mem_root),
        "k": k,
        "corpus_fingerprint": corpus_fingerprint(mem_root),
        "eval_set": {
            "name": eval_doc.get("name", "unknown"),
            "version": eval_doc.get("version"),
            "n_queries": len(records),
        },
        "queries": records,
        "summary": summarize(records),
    }


def _resolve_mem_root(cli_val: Path | None) -> Path | None:
    """mem_root 解析序：CLI --mem-root > 本地 config.MEM_ROOT（gitignored）。

    提交代码零硬编码私有路径（隐私驻点纪律）；真实 vault 路径由本地未入库的
    config.py 提供。两者皆缺 → None，由调用方 fail loud。
    """
    if cli_val is not None:
        return cli_val
    try:
        import config  # noqa: PLC0415  本地 gitignored 运行时配置
    except Exception:
        return None
    return getattr(config, "MEM_ROOT", None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="召回准确率基线（report-only）")
    ap.add_argument("--mem-root", type=Path, default=None,
                    help="记忆镜像根；缺省回落本地 config.MEM_ROOT")
    ap.add_argument("--eval", type=Path, default=DEFAULT_EVAL)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args(argv)

    args.mem_root = _resolve_mem_root(args.mem_root)
    if args.mem_root is None:
        print("[FAIL] 未指定 --mem-root 且本地无 config.MEM_ROOT，"
              "无法定位记忆镜像根（fail loud，不静默出空基线）",
              file=sys.stderr)
        return 2

    if not args.mem_root.is_dir():
        print(f"[FAIL] mem_root 不可读（目录不存在）: {args.mem_root}",
              file=sys.stderr)
        return 2
    if not any((args.mem_root / t).is_dir() for t in TIER_DIR.values()):
        print(f"[FAIL] mem_root 下无任何 tier 目录 {list(TIER_DIR.values())}"
              f"，语料口径不符: {args.mem_root}", file=sys.stderr)
        return 2
    if not args.eval.is_file():
        print(f"[FAIL] 标注集不存在: {args.eval}", file=sys.stderr)
        return 2
    try:
        eval_doc = load_eval(args.eval)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"[FAIL] 标注集非法: {e}", file=sys.stderr)
        return 2

    artifact = run_bench(args.mem_root, eval_doc, k=args.k)
    s = artifact["summary"]
    fp = artifact["corpus_fingerprint"]
    print(f"run_id={artifact['run_id']}  mem_root={artifact['mem_root']}")
    print(f"语料指纹: 笔记数={fp['note_count']}  "
          f"mtime聚合哈希={fp['sha256_mtime_agg'][:16]}…")
    print(f"标注集: {artifact['eval_set']['name']} v{artifact['eval_set']['version']}"
          f"  {s['n_queries']} 条")
    for r in artifact["queries"]:
        mark = "T1" if r["top1_hit"] else ("T5" if r["top5_hit"] else "MISS")
        print(f"  [{mark:>4}] {r['id']} ({r['category']}) q={r['query']!r}")
    print(f"top1 = {s['top1_hits']}/{s['n_queries']} = {s['top1_rate']:.1%}")
    print(f"top5 = {s['top5_hits']}/{s['n_queries']} = {s['top5_rate']:.1%}")
    for cat, c in s["by_category"].items():
        print(f"  {cat}: top1 {c['top1_hits']}/{c['n']}"
              f"  top5 {c['top5_hits']}/{c['n']}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / f"recall_precision_{artifact['run_id']}.json"
    out.write_text(json.dumps(artifact, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"工件: {out}")

    # 唯一健全性断言（非门禁）：top1 命中数为 0 = 召回链路整体断裂。
    if s["top1_hits"] == 0:
        print("[FAIL] 管道健全性断言失败：top1_hits == 0，"
              "召回链路疑似整体断裂（语料/打分/标注口径需排查）",
              file=sys.stderr)
        return 1
    print("[OK] 基线测量完成（report-only，无阈值门禁）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
