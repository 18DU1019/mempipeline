# -*- coding: utf-8 -*-
"""governance._score — 打分策略（可解释线性分 + 三因子 tier 分档 + stale 天数）。

自 governance.py 拆分（2026-09-14 架构债整改），行为零变化。
衔接 distill `_lifecycle`（STALE_DAYS=30）：时间衰减直接复用 stale 语义。
"""
from __future__ import annotations

import math
from datetime import datetime

# --- 打分（可解释线性分，衔接 distill STALE_DAYS）---
STALE_DAYS = 30          # 与 distill_memory._lifecycle 一致
SCORE_W = {"reuse": 0.4, "decay": 0.3, "feedback": 0.2, "hit": 0.1}
ARCHIVE_THRESHOLD = 0.2  # score 低于阈值且长期未命中 → 建议归档

# --- 三因子治理分档（valid_to 强失效 > tier 基础档 > importance 连续微调）---
# 优先级：valid_to（被同主题新稿取代，强失效）> tier（基础基座）> importance（连续微调）。
# - half_life：时间衰减的自适应半衰期基座（天）。long 对齐"长期永久"近永久基座(≈3年)，
#   medium 沿用现值 180 天。实际半衰期 = half_life × (0.5 + importance)。
# - threshold：该 tier 的建议归档线。long 更晚归档(0.1)，medium 沿用现值(0.2)。
# - stale_binary：二进制 stale 标记开关。long 禁用（软降位不标 stale/archived）；
#   medium 保留 stale:true/false。仅作配置声明，stale 落标由 distill 侧按此消费。
TIER_POLICY = {
    "long": {"half_life": 1095, "threshold": 0.1, "stale_binary": False},
    "medium": {"half_life": 180, "threshold": 0.2, "stale_binary": True},
}
DEFAULT_HALF_LIFE = 180  # 缺省半衰期基座：无 tier 上下文时回落旧全局值
SUPERSEDED_PENALTY = 0.4  # valid_to 触发后的治理降权：score_final = score × 0.4


def _half_life(importance: float, tier: str | None = None,
               half_life: float | None = None) -> float:
    """自适应半衰期（天）：HL = HL_tier × (0.5 + importance)。

    - tier 取 TIER_POLICY 基础半衰期基座（long≈3年，medium=180 天）；
      importance 连续微调：imp0.9→×1.4、imp0.6→×1.1、imp0.3→×0.8。
    - 显式 half_life 覆盖一切（测试注入用）；无 tier 上下文回落全局 180。
    """
    if half_life is not None:
        return float(half_life)
    base = DEFAULT_HALF_LIFE
    if tier and tier in TIER_POLICY:
        base = TIER_POLICY[tier]["half_life"]
    return base * (0.5 + float(importance))


def score_note(importance: float = 0.6, days_since_active: int = 0,
               hits: int = 0, feedback: float = 0.0,
               tier: str | None = None, half_life: float | None = None) -> float:
    """可解释线性分：0.4×复用(importance 归一) + 0.3×时间衰减 + 0.2×反馈 + 0.1×命中。

    - 时间衰减 exp(-days/HL)：半衰期可经 tier→importance 自适应（三因子），也支持
      显式 half_life 注入；缺省回落全局 180（存量行为不变，测试不回退）
    - 输出 0~1，可配置（SCORE_W），语义与 distill _lifecycle 衔接
    """
    reuse = min(1.0, importance / 0.9)
    hl = _half_life(importance, tier=tier, half_life=half_life)
    decay = math.exp(-max(0, days_since_active) / hl)
    fb = max(0.0, min(1.0, feedback))
    hit = min(1.0, hits / 10.0)
    return (SCORE_W["reuse"] * reuse + SCORE_W["decay"] * decay
            + SCORE_W["feedback"] * fb + SCORE_W["hit"] * hit)


def stale_days(updated: str | None, now: datetime | None = None) -> int:
    """从 updated 字段算距今天数（衔接 distill _lifecycle）。日期异常返回 0（不判 stale）。"""
    if not updated:
        return 0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d"):
        try:
            d = datetime.strptime(updated, fmt)
            if d.tzinfo:
                d = d.replace(tzinfo=None)
            now = now or datetime.now()
            return max(0, (now - d).days)
        except ValueError:
            continue
    return 0
