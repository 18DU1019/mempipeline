# -*- coding: utf-8 -*-
"""governance.py — 治理状态机（E3'）：过滤 → 状态迁移 → 审核队列 → 打分。

对齐 DSP 治理层与审查结论：
- 状态机：draft → project → candidate → promoted(global) / archived / rejected
- 软标记零删除：rejected/archived 只改 status 字段，不删文件（Mem0 冲突检测同款）
- vault status 兼容：导出映射 promoted→active（G8，避免体检告警）
- 打分衔接 distill `_lifecycle`（STALE_DAYS=30）：时间衰减直接复用 stale 语义
- 过滤：kind 白名单（decision/result/error/review/artifact/lesson/preference）

数据无关：所有路径由调用方注入；frontmatter 修改经 engine.write_atomic（幂等+审计）。
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .engine import write_atomic
from .audit import AuditBackend
from .recall import scan_tier_dirs

# --- 状态机定义 ---
STATES = {"draft", "active", "project", "candidate", "promoted", "archived", "rejected"}
# vault 兼容映射：资产化导出时 promoted → active（vault 白名单为 active/draft/archived）
VAULT_STATUS_MAP = {"promoted": "active", "draft": "draft", "archived": "archived"}
VALID_TRANSITIONS = {
    "draft": {"project", "rejected"},          # 投稿过过滤 → 项目域 / 拒绝
    "active": {"project", "candidate", "rejected"}, # Note 默认写入态，可入治理链
    "project": {"candidate", "archived", "rejected"},  # AI 判别标记候选 / 归档 / 拒绝
    "candidate": {"promoted", "rejected", "project"},  # 审核晋升全局 / 拒绝 / 退回项目
    "promoted": {"archived", "rejected"},      # 低分衰减归档 / 人工下架
    "archived": {"promoted"},                  # 归档可复活（零删除的逆向）
    "rejected": set(),                         # 终态（软标记，不删除）
}

# --- 过滤规则（DSP 白名单/黑名单）---
KIND_WHITELIST = {"decision", "result", "error", "review", "artifact",
                  "lesson", "preference"}
KIND_BLACKLIST_HINTS = ("闲聊", "草稿", "调试", "临时", "测试片段", "随手记")
KIND_DEFAULT = "result"

# --- 打分（可解释线性分，衔接 distill STALE_DAYS）---
STALE_DAYS = 30          # 与 distill_memory._lifecycle 一致
SCORE_W = {"reuse": 0.4, "decay": 0.3, "feedback": 0.2, "hit": 0.1}
ARCHIVE_THRESHOLD = 0.2  # score 低于阈值且长期未命中 → 建议归档


def filter_note(fm: dict) -> tuple[bool, str]:
    """过滤规则：kind 白名单 + 黑名单特征。返回 (通过?, 原因)。

    fm 须含 kind（缺省 KIND_DEFAULT=result）；title/summary 命中黑名单特征即拒。
    """
    kind = (fm.get("kind") or KIND_DEFAULT).strip().lower()
    if kind not in KIND_WHITELIST:
        return False, f"kind 不在白名单：{kind}"
    blob = " ".join(str(fm.get(k, "")) for k in ("title", "summary", "body"))
    for hint in KIND_BLACKLIST_HINTS:
        if hint in blob:
            return False, f"命中黑名单特征：{hint}"
    return True, "ok"


def score_note(importance: float = 0.6, days_since_active: int = 0,
               hits: int = 0, feedback: float = 0.0) -> float:
    """可解释线性分：0.4×复用(importance 归一) + 0.3×时间衰减 + 0.2×反馈 + 0.1×命中。

    - 时间衰减 exp(-days/180)：days 用 stale 判定（>STALE_DAYS=30 天未活跃即显著衰减）
    - 输出 0~1，可配置（SCORE_W），语义与 distill _lifecycle 衔接
    """
    reuse = min(1.0, importance / 0.9)
    decay = math.exp(-max(0, days_since_active) / 180.0)
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


# --- frontmatter status 修改（复用 crossref._upsert 模式，放本模块自持）---
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---", re.S)


def _upsert_status(text: str, status: str) -> str:
    """在 frontmatter 内插入/替换 status 行（DQ 标量）。无 frontmatter 不改动。"""
    from .protocol import _fmt_scalar
    m = _FM_RE.match(text)
    if not m:
        return text
    fm = m.group(1)
    line = f"status: {_fmt_scalar(status)}"
    if re.search(r"(?m)^\s*status:", fm):
        fm = re.sub(r"(?m)^\s*status:.*$", line, fm)
    else:
        parts = fm.splitlines()
        insert_at = next((i for i, l in enumerate(parts)
                          if re.match(r"^\s*updated:", l)), len(parts))
        parts.insert(insert_at, line)
        fm = "\n".join(parts)
    return f"---\n{fm}\n---" + text[m.end():]


def transition(note_path: Path, to_state: str, audit: AuditBackend,
               source: str = "governance",
               now_iso: str | None = None) -> tuple[str, str]:
    """状态迁移：校验合法 → 改 frontmatter status → write_atomic 写回（幂等+审计）。

    返回 (status, from_state)。rejected/archived 为软标记：文件保留，零删除。
    """
    if to_state not in STATES:
        return "invalid_target", ""
    try:
        text = note_path.read_text(encoding="utf-8")
    except Exception as exc:
        return f"read_error:{exc}", ""
    m = _FM_RE.match(text)
    cur = "draft"
    if m:
        mm = re.search(r"(?m)^\s*status:\s*(.+)$", m.group(1))
        if mm:
            val = mm.group(1).strip().strip('"\'')
            if val in STATES:
                cur = val
    if to_state not in VALID_TRANSITIONS.get(cur, set()):
        return "invalid_transition", cur
    new_text = _upsert_status(text, to_state)
    if new_text == text:  # 无 frontmatter 或 status 已一致 → 幂等跳过
        return "skipped", cur
    st, _ = write_atomic(note_path, new_text, audit, source=source,
                         skip_if_same=False)
    if st in ("wrote", "skipped"):
        # P1 自动化审计：转移轨迹由系统自动登记，reason 自动生成，免人工填写
        audit.trace(note_path, cur, to_state,
                    reason=f"auto:transition {cur}→{to_state} via {source}",
                    source=source)
    return st, cur


def review_queue(mem_root: Path, tiers: Iterable[str] | None = None,
                 projects: Iterable[str] | None = None) -> list[Path]:
    """扫描 candidate 状态笔记，返回待人工审核清单（升序）。"""
    from .protocol import TIER_DIR
    if tiers is None:
        tiers = TIER_DIR.values()
    out: list[Path] = []
    for tier in tiers:
        for d in scan_tier_dirs(mem_root, tier, projects):
            for md in d.glob("*.md"):
                try:
                    txt = md.read_text(encoding="utf-8")
                except Exception:
                    continue
                m = _FM_RE.match(txt)
                if not m:
                    continue
                mm = re.search(r"(?m)^\s*status:\s*(.+)$", m.group(1))
                if mm and mm.group(1).strip().strip('"\'') == "candidate":
                    out.append(md)
    return sorted(out)


def vault_status(status: str) -> str:
    """资产化导出映射：promoted→active（vault 白名单兼容，G8）。"""
    return VAULT_STATUS_MAP.get(status, status)

def _read_fm_value(fm: str, key: str) -> str | None:
    """从 frontmatter 文本读某字段的标量值（剥外层引号），缺省 None。"""
    import re as _re
    mm = _re.search(r"(?m)^\s*" + key + r":\s*(.+?)\s*$", fm)
    if not mm:
        return None
    return mm.group(1).strip().strip(chr(34) + chr(39))

def scan_stale_notes(mem_root: Path, tiers: Iterable[str] | None = None,
                     projects: Iterable[str] | None = None,
                     threshold: float = ARCHIVE_THRESHOLD) -> list[dict]:
    """P1 质量扫描：自动标记旧/冷/冗余候选，只出清单、不自动改状态。

    遍历各 tier 笔记，按 stale_days 与 score_note 算（旧度、评分），把 score
    低于 threshold 且处于活跃态（非 archived/rejected）的笔记列为"建议归档"
    候选。返回结构化清单；**不执行任何 transition**——人工只在最终裁决点
    （晋升/归档）出现一次，其余全部由系统兜底。

    返回字段：{path, status, stale_days, score, suggestion}。
    """
    from .protocol import TIER_DIR
    from .recall import scan_tier_dirs
    if tiers is None:
        tiers = TIER_DIR.values()
    out: list[dict] = []
    for tier in tiers:
        for d in scan_tier_dirs(mem_root, tier, projects):
            for md in sorted(d.glob("*.md")):
                try:
                    txt = md.read_text(encoding="utf-8")
                except Exception:
                    continue
                m = _FM_RE.match(txt)
                if not m:
                    continue
                fm = m.group(1)
                status = _read_fm_value(fm, "status") or "active"
                if status in ("archived", "rejected"):
                    continue  # 终态/归档态不重复提醒
                updated = _read_fm_value(fm, "updated") or None
                days = stale_days(updated)
                imp = _roz_importance(fm)
                score = score_note(importance=imp, days_since_active=days)
                if score < threshold:
                    out.append({
                        "path": str(md),
                        "status": status,
                        "stale_days": days,
                        "score": round(score, 3),
                        "suggestion": "archive",
                    })
    return out


def _roz_importance(fm: str) -> float:
    """读 frontmatter 的 importance，缺省/异常回退 0.6。"""
    v = _read_fm_value(fm, "importance")
    if v is None:
        return 0.6
    try:
        return float(v)
    except ValueError:
        return 0.6
