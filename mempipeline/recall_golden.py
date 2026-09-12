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
#
# 2026-09-11 重建说明：旧 GOLDEN 三条（风险 仓位 / 仓位调度 / positioning 调度）指向
# 「量化域」概念，但本库是记忆镜像（项目管理域），库内不存在任何「仓位规则/仓位调度」
# 主题笔记，导致 check() 恒定 0 命中（实测命中率 0.0）。根因是 golden 建错域，而非
# 匹配逻辑缺陷（expect in stem 本身正确）。
#
# 2026-09-12 扩充（P3-① 全层级回归基线）：GOLDEN 从 4 条扩为覆盖「长期记忆 + 中期记忆」
# 两层的回归基线。锚点均取自库内真实存在的笔记（01-长期记忆/02-中期记忆），每条已实测
# recall(k=5) 命中（本批命中率 1.000，整体红线 ≥ 2/3）。全层级覆盖使回归不再只验规则层，
# 结论/会话/交付偏好类的中期笔记退化也能被门禁抓到。
GOLDEN: dict[str, str] = {
    # ---- 长期记忆（01-长期记忆：规则 / 精约束）----
    "知识库 git 提交 精确化": "知识库git提交交集判据",   # 泛查询命中规则类
    "时间图谱 主题键 去偏": "时间图谱主题键去偏",       # 强查询命中且不混入干扰
    "mempipeline created 闲置 封条": "mempipelinecreated字段裁决",  # 跨关键词召回
    "红利 低波 定投 方案": "永久组合定投方案",          # 泛查询命中结论类
    # ---- 中期记忆（02-中期记忆：结论 / 会话 / 交付偏好）----
    "最优配置 扫描寻优 组合": "S34扫描寻优永久组合",
    "交付偏好 微信 长图": "结果交付偏好微信可读长图",
    "RAGFlow 索引 治理 判重": "RAGFlow索引治理工具目录污染",
    "知识库 断链 误报 终局处置": "知识库断链终局处置",
    "沪深300 替换 回测 占优": "S33沪深300替换版回测",
    "OneQuant 前端 统一 换皮 设计系统": "OneQuant前端统一换皮中枢设计系统",
}

# 退化红线：命中率低于该值即上报 Check 失败，提示应 Act。
MIN_HIT_RATE = 2 / 3


def check(mem_root: Path, tiers: Iterable[str] | None = None,
          synonyms: dict[str, list[str]] | None = None,
          min_hit: float = MIN_HIT_RATE,
          golden: dict[str, str] | None = None) -> dict:
    """Check 相：对 golden 集逐条召回并按期望子串判定命中。

    只读不写。返回结构化信号：逐条结果、整体命中率、是否跌破红线，以及
    「建议 Act 面」提示。Act 本身留给人类，不做任何自动参数修改。

    golden：待检验的 query->expect 映射；缺省用模块级 GOLDEN 常量。
    测试需注入自有沙盒 golden 集（避免测试依赖全局常量导致 GOLDEN 改后测试崩）。
    """
    golden = golden if golden is not None else GOLDEN
    recaller = MemoryRecall(mem_root, tiers=tiers, synonyms=synonyms or {})
    results: list[dict] = []
    for query, expect in golden.items():
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