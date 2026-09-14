# -*- coding: utf-8 -*-
"""governance — 治理状态机（E3'）：过滤 → 状态迁移 → 审核队列 → 打分。

对齐 DSP 治理层与审查结论：
- 状态机：draft → project → candidate → promoted(global) / archived / rejected
- 软标记零删除：rejected/archived 只改 status 字段，不删文件（Mem0 冲突检测同款）
- vault status 兼容：导出映射 promoted→active（G8，避免体检告警）
- 打分衔接 distill `_lifecycle`（STALE_DAYS=30）：时间衰减直接复用 stale 语义
- 过滤：kind 白名单（decision/result/error/review/artifact/lesson/preference）

数据无关：所有路径由调用方注入；frontmatter 修改经 engine.write_atomic（幂等+审计）。

包结构（2026-09-14 架构债整改：按职责耦合拆分，外部 API 零变化）：
- _state：状态机与内容处置（filter_note / transition / redact / review_queue /
  vault_status / STATES / VALID_TRANSITIONS / KIND_* / REDACTED*）
- _score：打分策略（score_note / _half_life / stale_days / TIER_POLICY / SCORE_W /
  STALE_DAYS / ARCHIVE_THRESHOLD / SUPERSEDED_PENALTY）
- _scan：候选扫描（scan_stale_notes + 时间图谱信号合并）
- _health：治理健康度只读快照（governance_health）
"""
from __future__ import annotations

from ._health import governance_health
from ._scan import scan_stale_notes
from ._score import (ARCHIVE_THRESHOLD, DEFAULT_HALF_LIFE, SCORE_W, STALE_DAYS,
                     SUPERSEDED_PENALTY, TIER_POLICY, _half_life, score_note,
                     stale_days)
from ._state import (KIND_BLACKLIST_HINTS, KIND_DEFAULT, KIND_WHITELIST,
                     REDACTED, REDACTED_PLACEHOLDER, STATES, VALID_TRANSITIONS,
                     VAULT_STATUS_MAP, _FM_RE, _read_fm_value, _upsert_status,
                     filter_note, redact, review_queue, transition, vault_status)

__all__ = [
    "ARCHIVE_THRESHOLD", "DEFAULT_HALF_LIFE", "SCORE_W", "STALE_DAYS",
    "SUPERSEDED_PENALTY", "TIER_POLICY", "_half_life", "score_note",
    "stale_days",
    "KIND_BLACKLIST_HINTS", "KIND_DEFAULT", "KIND_WHITELIST", "REDACTED",
    "REDACTED_PLACEHOLDER", "STATES", "VALID_TRANSITIONS", "VAULT_STATUS_MAP",
    "_FM_RE", "_read_fm_value", "_upsert_status", "filter_note", "redact",
    "review_queue", "transition", "vault_status",
    "scan_stale_notes", "governance_health",
]
