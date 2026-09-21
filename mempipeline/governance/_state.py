# -*- coding: utf-8 -*-
"""governance._state — 状态机与内容处置（过滤 / 迁移 / 红act / 审核队列 / vault 映射）。

自 governance.py 拆分（2026-09-14 架构债整改：职责耦合非行数），行为零变化。
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

from ..engine import write_atomic
from ..audit import AuditBackend
from ..recall import scan_tier_dirs

# --- 状态机定义 ---
# redacted（P3.12）：紧急撤离的**软终态**标记 —— 从活跃态人工单向迁入，迁出为空、
# 系统永不自动触发。只脱离「检索/导出」活跃面，不删除文件（零删除）；审计保留
# 轨迹（谁在何时脱敏了什么），不覆盖不删历史。
REDACTED = "redacted"
REDACTED_PLACEHOLDER = "【已脱敏 redacted】\n"
STATES = {"draft", "active", "project", "candidate", "promoted", "archived",
          "rejected", REDACTED}
# vault 兼容映射：资产化导出时 promoted → active（vault 白名单为 active/draft/archived）
VAULT_STATUS_MAP = {"promoted": "active", "draft": "draft", "archived": "archived"}
VALID_TRANSITIONS = {
    "draft": {"project", "rejected"},          # 投稿过过滤 → 项目域 / 拒绝
    "active": {"project", "candidate", "rejected", REDACTED},  # Note 默认写入态，可入治理链
    "project": {"candidate", "archived", "rejected", REDACTED},  # AI 判别标记候选 / 归档 / 拒绝
    "candidate": {"promoted", "rejected", "project", REDACTED},  # 审核晋升全局 / 拒绝 / 退回项目
    "promoted": {"archived", "rejected", REDACTED},  # 低分衰减归档 / 人工下架
    "archived": {"promoted"},                  # 归档可复活（零删除的逆向）
    "rejected": set(),                         # 终态（软标记，不删除）
    REDACTED: set(),                           # 软终态：只入不迁出（不可复活）
}

# --- 过滤规则（DSP 白名单/黑名单）---
KIND_WHITELIST = {"decision", "result", "error", "review", "artifact",
                  "lesson", "preference"}
KIND_BLACKLIST_HINTS = ("闲聊", "草稿", "调试", "临时", "测试片段", "随手记")
KIND_DEFAULT = "result"


# --- frontmatter status 修改（二期：统一走 protocol.upsert_fm_value，同模式收敛）---
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---", re.S)


def _upsert_status(text: str, status: str) -> str:
    """在 frontmatter 内插入/替换 status 行（DQ 标量）。无 frontmatter 不改动。

    收敛自 protocol.upsert_fm_value（锚点 = updated 前插入；替换/无 FM 语义
    与原实现逐字一致，检验报告第六节"相邻债务"二期）。
    """
    from ..protocol import upsert_fm_value
    return upsert_fm_value(text, "status", status, insert_before=("updated",))


def _read_fm_value(fm: str, key: str) -> str | None:
    """从 frontmatter 块文本读某字段标量值（P3：统一走 protocol.parse_frontmatter）。

    缺键返回 None（fm.get 缺键语义）；空值行返回 ""（与旧实现 None 的差异仅
    影响 `is None` 直判，各调用点均以 or/falsy 兜底，结果不变）。
    """
    from ..protocol import parse_frontmatter
    return parse_frontmatter(f"---\n{fm}\n---").get(key)


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


def transition(note_path: Path, to_state: str, audit: AuditBackend,
               source: str = "governance",
               now_iso: str | None = None) -> tuple[str, str]:
    """状态迁移：校验合法 → 改 frontmatter status → write_atomic 写回（幂等+审计）。

    返回 (status, from_state)。rejected/archived 为软标记：文件保留，零删除。

    ⚠️ 调用方式现状（2026-09-14 审查实测，人工裁决留档）
    ---------------------------------------------------------------
    本函数目前**一律由人工/agent 显式指定 to_state 调用**，系统中没有任何
    自动产出 candidate 的路径。实测：镜像 235 篇 status 全为 active，
    candidate 队列恒 0，非活跃占比 0.0%（周报 ③ 节连续标记此异常）。

    这是**有意保留的现状**，非接线遗漏。理由：审查未发现"该流转却被卡住"
    的实例——235 篇中没有一篇因缺少流转而失活。在根因不明时启用自动评分，
    只会凭空生产 candidate 增加人工审核负担，属负收益。

    若未来决定启用自动评分，须先满足下面三个前提，且**按顺序**执行：
      1. 先确认候选来源：启用后 candidate 会从哪里产生？只能是
         `scan_stale_notes()` 的候选清单（已有打分）或新增写者侧钩子。
         前者是纯时间衰减，后者需改 writer_contracts。
      2. 先确认审核容量：candidate 是给人看的队列，若无人定期处理，
         队列只增不减，与"零流量"相比是更糟的状态（堆积而非闲置）。
      3. 先小样本试运行：对 scan_stale_notes 命中且 score < 阈值 的少数几篇
         （建议 <= 5 篇）手工调 transition(..., "candidate") 观察一周，
         确认人能消化再考虑脚本化。

    启用方式（当前未启用，勿直接抄）：在 weekly_health.py 的 stale_scan 之后
    加一段对候选清单的批量 transition，并把 source 设为 "weekly" 以在审计中
    可区分自动化来源。注意 transition 会对每篇调 write_atomic，批量执行有
    写放大，且需 oq_lock 同款单写者保护——**这是它未被默认启用的第二个原因**。
    """
    if to_state not in STATES:
        return "invalid_target", ""
    try:
        text = note_path.read_text(encoding="utf-8")
    except Exception as exc:
        return f"read_error:{exc}", ""
    from ..protocol import parse_frontmatter
    cur = parse_frontmatter(text).get("status", "draft")
    if cur not in STATES:
        cur = "draft"
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


def _stamp(now_iso: str | None) -> str:
    """时间戳文件名片段（归档备份用）。"""
    s = (now_iso or datetime.now().strftime("%Y%m%d-%H%M%S"))
    return s.replace(" ", "").replace(":", "").replace("-", "")


def _blot_body(text: str, placeholder: str = REDACTED_PLACEHOLDER) -> str:
    """保留 frontmatter、把正文替换为占位符（硬脱敏内容处置）。"""
    m = _FM_RE.match(text)
    if not m:
        return placeholder
    return f"{m.group(0)}\n\n{placeholder}"


def redact(note_path: Path, audit: AuditBackend, *, wipe: bool = False,
           archive_dir: Path | None = None, source: str = "governance",
           now_iso: str | None = None,
           placeholder: str = REDACTED_PLACEHOLDER) -> tuple[str, str]:
    """红act（P3.12）：三段式顺序，默认软脱敏。

    1. **检索切断（先于标记）**：recall 全扫跳过 `status: redacted`、TFIDF `build()`
       去掉 redacted 文档 —— 已编码在 recall.py 恒开，先于本动作存在，杜绝泄漏窗口。
    2. **迁移标记**：`transition(note_path, REDACTED, ...)` 走幂等原子写 + 审计轨迹
       （可回查谁在何时脱敏了哪条）。
    3. **内容处置（可选、默认关）**：仅 `wipe=True` 且人工显式触发时执行硬脱敏 ——
       先备份原稿到 git 外 `archive_dir`（防丢失底线），再抹正文为占位符并重写，
       审计记新 hash（可证明正文已变更）。`transcribe` 需显式 `archive_dir`。

    返回 (transition_status, from_state)。
    """
    st, frm = transition(note_path, REDACTED, audit, source=source, now_iso=now_iso)
    if st not in ("wrote", "skipped"):
        return st, frm  # 软标记失败（含已终态 → invalid_transition）即中止，不触碰内容
    if not wipe:
        return st, frm  # 软脱敏：只标记 + 检索/导出切断，正文保留（忠于审计取证）
    if archive_dir is None:
        raise ValueError("硬脱敏需显式 archive_dir（git 外归档区）")
    try:
        txt = note_path.read_text(encoding="utf-8")
    except Exception as exc:
        return f"read_error:{exc}", frm
    archive_dir.mkdir(parents=True, exist_ok=True)
    bak = archive_dir / f"{note_path.stem}-{_stamp(now_iso)}.md"
    try:
        bak.write_text(txt, encoding="utf-8")
    except OSError as exc:
        return f"backup_error:{exc}", frm
    new_text = _blot_body(txt, placeholder)
    st2, _ = write_atomic(note_path, new_text, audit, source=source + ":redact-wipe")
    return st2, frm


def review_queue(mem_root: Path, tiers: Iterable[str] | None = None,
                 projects: Iterable[str] | None = None) -> list[Path]:
    """扫描 candidate 状态笔记，返回待人工审核清单（升序）。

    P1-5：status 判读收编走 protocol.parse_frontmatter（P5 只收了 transition，
    本处 strip('"\'') 不反转义分叉为漏网点；candidate 值无特殊字符行为等价，
    实现路径归一）。
    """
    from ..protocol import TIER_DIR, parse_frontmatter
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
                if parse_frontmatter(txt).get("status") != "candidate":
                    continue
                out.append(md)
    return sorted(out)


def vault_status(status: str) -> str:
    """资产化导出映射：promoted→active（vault 白名单兼容，G8）。"""
    return VAULT_STATUS_MAP.get(status, status)
