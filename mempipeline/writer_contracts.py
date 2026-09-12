# -*- coding: utf-8 -*-
"""writer_contracts.py — E2 写者约束数据化（写者可转移的约束基底）。

把「每个写者能写什么、是否须产 project_id、可否被治理」从生产者硬编码里抽成
**纯数据**注册表 + 只读校验 `check_contract`。写者可转移 = 约束在数据层声明，
换/加写者只改这张表，不碰 engine/governance。

设计约束（对齐 mempipeline 薄 / 可逆 / 信号层惯例）：
- 纯数据 + 只读：check_contract 不写库、不自动执行，人/周检触发才跑。
- 与 project_id 权威键解耦：契约里 `project_scope` 描述族级约束，呼应 timegrap
  的「project_id 权威、文件名正则仅兜底」口径。
- 诚实边界：writer_id 是溯源标签（自声明），非防伪证明；审计线靠 git + audit_core。

`governable`：该写者笔记的治理候选（supersede/archive 等）是否允许产生建议。
distill_memory（TRAE 单方镜像，人工策展）可治理；staging_ingest（WorkBuddy 源
投稿，外部内容，尊重不覆灭他人稿红线）默认不可治理。
"""
from __future__ import annotations

import re
from typing import Any

# ---- 写者契约注册表（单一事实源：约束在此声明，各写者引用而非重实现） ----
WRITER_CONTRACTS: dict[str, dict[str, Any]] = {
    # distill_memory：TRAE 记忆单向蒸馏镜像，人工策展，**可**被治理建议覆盖
    "distill_memory": {
        "allowed_tiers": {"long", "medium"},
        "project_scope": "family",   # 项目约束/会话族须带 project_id；用户画像族禁带
        "governable": True,
    },
    # staging_ingest：WorkBuddy 源投稿，外部内容，默认**不可**被治理建议覆盖
    "staging_ingest": {
        "allowed_tiers": {"medium"},
        "project_scope": "optional",  # project_id 源带则透传，不带不强行
        "governable": False,
    },
    # robot-vision：具身机器人观测投稿。感知稿天然短时效 → 只写中期；语义归属 → 强制
    # 项目族 project_id；可治理 → 允许被 supersede/archive 建议覆盖（观测可证伪）。
    # 跨写者冲突裁决规则见 ROADMAP P3.6 后续段（关键：观测数≠独立证据数，需 source_modal
    # 区分模态/时间窗；信任=min(写者基线,观测置信)——此条目静态基线，动态聚合在 runbook）。
    "robot-vision": {
        "allowed_tiers": {"medium"},      # 观测稿短时效，不染指长期层
        "project_scope": "family",        # 必须带 project_id（语义归属，独立证据聚合不自生效）
        "governable": True,               # 可被建议覆盖，最终执行仍在人类
    },
}

# 兜底契约：allowed_tiers 用 frozenset 不可变，杜绝读侧误改污染（dict(_DEFAULT) 浅拷贝
# 只隔离外层 dict，set 值若不冻结则仍是共享可变引用；契约表只读，冻结即正确形态）。
_DEFAULT = {"allowed_tiers": frozenset(), "project_scope": "optional", "governable": False}

# frontmatter 块与字段读取（与 governance._FM_RE / timegrap 同构）
_FM_RE = re.compile(r"(?m)^---\s*\n(.*?)\n---\s*\n?", re.S)


def _fm_blocks(text: str) -> list[str]:
    return _FM_RE.findall(text)


def _field_value(fm: str, key: str) -> str:
    m = re.search(rf"(?m)^{key}:\s*(.+?)\s*$", fm)
    return m.group(1).strip().strip("\"'") if m else ""


def writer_of(text: str) -> str | None:
    """从一条笔记的 frontmatter 读 writer_id；无则 None。"""
    fms = _fm_blocks(text)
    if not fms:
        return None
    v = _field_value(fms[0], "writer_id")
    return v or None


def _family(stem: str) -> str:
    if stem.startswith("项目约束"):
        return "约束"
    if stem.startswith("项目会话"):
        return "会话"
    if stem.startswith("用户画像"):
        return "画像"
    return "其他"


def check_contract(text: str, *, stem: str | None = None) -> list[str]:
    """对一条笔记文本做写者契约校验，返回违规清单（空=合规）。只读不写。

    覆盖：allowed_tiers、project_scope(族级 project_id 是否要求/禁带)、以及
    writer_id 是否缺失（识别维度本就是契约的一部分）。缺认的写者按 _DEFAULT
    兜底（不报"未注册"违规，避免武装僵化；由人决定是否登记新写者）。
    """
    violations: list[str] = []
    fms = _fm_blocks(text)
    if not fms:
        return ["frontmatter 缺失"]
    fm = fms[0]
    writer = writer_of(text)
    if not writer:
        violations.append("writer_id 缺失")
    contract = WRITER_CONTRACTS.get(writer or "", dict(_DEFAULT))

    tier = _field_value(fm, "memory_tier")
    if tier and contract["allowed_tiers"]:
        if tier not in contract["allowed_tiers"]:
            violations.append(f"memory_tier '{tier}' 不在写者 {writer} 允许层 {sorted(contract['allowed_tiers'])}")

    pid = _field_value(fm, "project_id")
    name = stem or ""
    fam = _family(name) if name else "其他"
    scope = contract["project_scope"]
    if scope == "family":
        if fam in ("约束", "会话") and not pid:
            violations.append(f"项目{fam}族缺 project_id（权威主题键）")
        if fam == "画像" and pid:
            violations.append("用户画像族不应带 project_id（全局作用域）")
    # scope == "optional"：不约束，如实反映

    return violations


def non_governable_writers() -> set[str]:
    """返回 governable=False 的写者集合（供治理候选过滤注入）。"""
    return {w for w, c in WRITER_CONTRACTS.items() if not c.get("governable", True)}