# -*- coding: utf-8 -*-
"""recall_golden.py — PDCA · Check 信号层（默认关闭，Act 留人）。

把「召回质量是否退化」编码成 golden set 回归，作为 Plan-Do-Check-Act 的
Check 相：只读既有数据（走 MemoryRecall），产出结构化信号；绝不擅自改参数，
Act（改 synonyms / 停用字 / 阈值）由人类显式触发，写回仍走 write_atomic + 审计。

设计约束（对齐 mempipeline 薄/可逆/重引擎不入）：
- 零副作用：check() 只读不写，不新增持久化索引，是信号层不是引擎。
- 复用保证：判定口径与 test_tfidf_recall 一致（取 query 递归命中即认），不造第二套度量。
- 默认关闭：不自动在任何入口被激活，仅在显式调用时运行，随时可逆移除。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .recall import MemoryRecall

# golden set：query -> 期望命中的笔记「文件名中应出现的子串」。由人维护（Plan 相产物）。
GOLDEN: dict[str, str] = {
    "风险 仓位": "仓位规则",        # 泛查询命中规则类
    "仓位调度 分配": "仓位调度",     # 强查询命中且不混入干扰
    "positioning 调度": "仓位调度",  # 校验同义归一不回退
}

# 退化红线：命中率低于该值即上报 Check 失败，提示应 Act。
MIN_HIT_RATE = 2 / 3


def check(mem_root: Path, tiers: Iterable[str] | None = None,
          synonyms: dict[str, list[str]] | None = None,
          min_hit: float = MIN_HIT_RATE) -> dict:
    """Check 相：对 golden 集逐条召回并按期望子串判定命中。

    只读不写。返回结构化信号：逐条结果、整体命中率、是否跌破红线，以及
    「建议 Act 面」提示。Act 本身留给人类，不做任何自动参数修改。
    """
    recaller = MemoryRecall(mem_root, tiers=tiers, synonyms=synonyms or {})
    results: list[dict] = []
    for query, expect in GOLDEN.items():
        hits = recaller.recall(query, k=5)
        hit_names = [Path(p).stem for p, _ in hits]
        hit = any(expect in n for n in hit_names)
        results.append({
            "query": query,
            "expect": expect,
            "hit": hit,
            "phase": "hit" if hit else "miss",
            "top_hits": hit_names,
        })
    misses = [r for r in results if not r["hit"]]
    hit_rate = (len(results) - len(misses)) / len(results) if results else 0.0
    return {
        "phase": "check",
        "hit_rate": hit_rate,
        "min_hit": min_hit,
        "passed": hit_rate >= min_hit,
        "results": results,
        "suggested_act": _suggest(len(misses)),
    }


def _suggest(miss: int) -> str:
    if miss == 0:
        return "no_action"
    return "check synonyms & stopchars; rerun check after edit"