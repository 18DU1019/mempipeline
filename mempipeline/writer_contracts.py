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

`baseline_trust`：该写者信任档的**静态基线**（trusted / unknown / untrusted，
对齐 protocol.TRUST_* 三档），是写侧信任分层（P0 方案 B）的登记层落点。
信任模型 `信任 = min(基线, 观测置信)` 中的基线在此声明；观测置信来自
`confidence_*` 字段，动态聚合在读取/仲裁侧（本模块只存静态基线，不判观测）。
诚实边界：基线是出自身声明的静态档，非观测证据、非防伪证明。
⚠ 命名空间错位：本表键是 writer_id（如 distill_memory），而信任归一
`normalize_trust` 的名单匹配的是 source_agent（如 workbuddy，非同一值）。
消费端联调时须明确「writer_id ↔ trust 匹配名」的映射，勿直接拿 writer_id
当 source_agent 名单喂 normalize_trust。
"""
from __future__ import annotations

import re
from typing import Any

from .protocol import parse_frontmatter

# ---- 写者契约注册表（单一事实源：约束在此声明，各写者引用而非重实现） ----
WRITER_CONTRACTS: dict[str, dict[str, Any]] = {
    # distill_memory：TRAE 记忆单向蒸馏镜像，人工策展，**可**被治理建议覆盖
    "distill_memory": {
        "allowed_tiers": {"long", "medium"},
        "project_scope": "family",   # 项目约束/会话族须带 project_id；用户画像族禁带
        "governable": True,
        "baseline_trust": "trusted",   # 单写者蒸馏主链，唯一默认高信任基线
    },
    # staging_ingest：WorkBuddy 源投稿，外部内容，默认**不可**被治理建议覆盖
    "staging_ingest": {
        "allowed_tiers": {"medium"},
        "project_scope": "optional",  # project_id 源带则透传，不带不强行
        "governable": False,
        "baseline_trust": "untrusted",   # 外部投稿未信任来源，观测建立后再升档
    },
    # robot-vision：具身机器人观测投稿。感知稿天然短时效 → 只写中期；语义归属 → 强制
    # 项目族 project_id；可治理 → 允许被 supersede/archive 建议覆盖（观测可证伪）。
    # 跨写者冲突裁决规则见 ROADMAP P3.6 后续段（关键：观测数≠独立证据数，需 source_modal
    # 区分模态/时间窗；信任=min(写者基线,观测置信)——此条目静态基线，动态聚合在 runbook）。
    "robot-vision": {
        "allowed_tiers": {"medium"},      # 观测稿短时效，不染指长期层
        "project_scope": "family",        # 必须带 project_id（语义归属，独立证据聚合不自生效）
        "governable": True,               # 可被建议覆盖，最终执行仍在人类
        "baseline_trust": "unknown",      # 观测置信未建立前保守回落 unknown，动态聚合在读取侧
    },
}

# 兜底契约：allowed_tiers 用 frozenset 不可变，杜绝读侧误改污染（dict(_DEFAULT) 浅拷贝
# 只隔离外层 dict，set 值若不冻结则仍是共享可变引用；契约表只读，冻结即正确形态）。
# baseline_trust 兜底为 "unknown"：未登记写者保守回落未知档，不误判为 trusted/untrusted。
_DEFAULT = {"allowed_tiers": frozenset(), "project_scope": "optional", "governable": False,
            "baseline_trust": "unknown"}

# frontmatter 块与字段读取（与 governance._FM_RE / timegrap 同构）
_FM_RE = re.compile(r"(?m)^---\s*\n(.*?)\n---\s*\n?", re.S)


def _fm_blocks(text: str) -> list[str]:
    return _FM_RE.findall(text)


def _field_value(fm: str, key: str) -> str:
    """从 frontmatter 块文本读某字段值（P1：统一走 protocol.parse_frontmatter）。

    与旧实现的行为差异（方案第六节接受并修正）：
    - 旧实现只 strip 引号不反转义 DQ 序列；新实现经 unquote 反转义，
      与写侧 _fmt_scalar 闭环（实际读取字段 writer_id/memory_tier/project_id 不含转义，影响面≈0）；
    - 空值/缺键均返回 ""（旧实现同）；
    - 行锚定防跨行吞值（旧实现靠 [ \t]*，现由解析器统一保证）。
    """
    return parse_frontmatter(f"---\n{fm}\n---").get(key, "")


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


def baseline_trust_of(writer_id: str) -> str:
    """读某写者的静态信任基线（trusted / unknown / untrusted，对齐 protocol.TRUST_*）。

    返回登记表声明的 baseline_trust；未登记写者回落 _DEFAULT 的 "unknown"。
    只读纯查询，服务于写侧信任分层（P0）的登记层取值。注意：返回值是静态基线，
    不含观测置信的动态聚合——完整 `信任 = min(基线, 观测置信)` 在读取/仲裁侧实现。
    """
    c = WRITER_CONTRACTS.get(writer_id, _DEFAULT)
    return c.get("baseline_trust", "unknown")