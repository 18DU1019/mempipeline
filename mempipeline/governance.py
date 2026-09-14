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
    """从 frontmatter 文本读某字段的标量值（剥引号+反转义 DQ），缺省 None。"""
    from .protocol import unquote
    mm = re.search(r"(?m)^\s*" + key + r":\s*(.+?)\s*$", fm)
    if not mm:
        return None
    return unquote(mm.group(1).strip())


def _path_writer(path: str | None) -> str | None:
    """读某路径笔记 frontmatter 的 writer_id（读不到返回 None，不抛）。"""
    if not path:
        return None
    try:
        txt = Path(path).read_text(encoding="utf-8")
    except Exception:
        return None
    return _read_fm_value(txt.split("---", 2)[1], "writer_id") if txt.startswith("---") else None


def scan_stale_notes(mem_root: Path, tiers: Iterable[str] | None = None,
                     projects: Iterable[str] | None = None,
                     threshold: float | None = None,
                     timeline: dict | None = None,
                     non_governable_writers: set[str] | None = None,
                     superseded: set[str] | None = None) -> list[dict]:
    """P1 质量扫描：自动标记旧/冷/冗余候选，只出清单、不自动改状态。

    遍历各 tier 笔记，按 stale_days 与 score_note 算（旧度、评分），把 score
    低于该 tier 归档线（TIER_POLICY[threshold]）且处于活跃态（非 archived/
    rejected）的笔记列为"建议归档"候选。返回结构化清单；**不执行任何
    transition**——人工只在最终裁决点（晋升/归档）出现一次，其余由系统兜底。

    三因子分档：thr 默认按 `tier` 取 TIER_POLICY（long=0.1 / medium=0.2），
    显式 `threshold` 可整体覆盖（存量调用语义不变）；score 半衰期经
    tier→importance 自适应（见 score_note）。

    P3-③（时间图谱信号使入治理）：可选 `timeline`（`timegrap.build_timeline`
    输出 {subject: TopicTimeline}）注入后，额外把时间图谱的**结论漂移(drift)**
    与**断更后复活(revived)** 信号并入同一候选清单——仍不自动改状态。信号
    复用 timegrap（经 P3-1 实测），不新增第二套度量。

    `superseded`（可选，P2-B 单点数据源 `recall_golden.stale_map` 产出的
    path 集）：命中「已被同主题后续稿取代」的旧稿时，对其做强失效降权——
    score_final = score × SUPERSEDED_PENALTY(0.4)，且**即使分未跌破归档线也
    单列高优先级候选**（suggestion=supersede）。只出候选、不改 status、不落
    archived/rejected，真正落位仅当人工经 transition 显式触发，零删除。

    返回字段（归档候选）：{path, status, stale_days, score, suggestion}；
    信号候选额外含 {subject, signal, signal_detail}；superseded 候选带
    signal=superseded。
    """
    from .protocol import TIER_DIR
    from .recall import scan_tier_dirs
    tier_key = {v: k for k, v in TIER_DIR.items()}
    if tiers is None:
        tiers = TIER_DIR.values()
    superseded = superseded or set()
    out: list[dict] = []
    for tier in tiers:
        key = tier_key.get(tier) or "medium"
        if threshold is None:
            thr = TIER_POLICY[key]["threshold"]
        else:
            thr = threshold
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
                score = score_note(importance=imp, days_since_active=days, tier=key)
                if str(md) in superseded:
                    # P2-B：valid_to 强失效轨道——降权且高优先级单列，不改状态
                    out.append({
                        "path": str(md),
                        "status": status,
                        "stale_days": days,
                        "score": round(score * SUPERSEDED_PENALTY, 3),
                        "suggestion": "supersede",
                        "signal": "superseded",
                        "signal_detail": "过时旧稿：valid_to 触发（被同主题新稿取代）",
                    })
                    continue
                if score < thr:
                    out.append({
                        "path": str(md),
                        "status": status,
                        "stale_days": days,
                        "score": round(score, 3),
                        "suggestion": "archive",
                    })
    if timeline:
        out = _merge_signal_candidates(out,
                                       _timeline_signal_candidates(timeline))
    if non_governable_writers:
        # E2：剔除不可治理写者的候选来源（staging_ingest 等外部稿，尊重不覆灭他人稿）
        out = [c for c in out
               if _path_writer(c.get("path")) not in non_governable_writers]
    return out


def _timeline_signal_candidates(timeline: dict) -> list[dict]:
    """把 timegrap 时间图谱的断更/结论漂移/过时旧稿信号转成候选条目（只读，不改状态）。

    - relation == "drift"：相邻两稿摘要相似度低于阈值 → 结论漂移 → re-review
    - flag == "revived"：断更 gap 后再次投稿 → 复活 → review（结论需再校验）
    - valid_to 非空（D3）：该稿已被同主题后续稿正式取代 → 软失效提示 superseded
      （只出候选、不改写文件，对齐 Mem0 ADD-only / Claude 审计可回滚的"零删除"取态）
    """
    out: list[dict] = []
    emitted: set[str] = set()
    for subject, tl in (timeline or {}).items():
        for sig in getattr(tl, "signals", []) or []:
            path = sig.get("path")
            if not path:
                continue
            node = next((n for n in tl.nodes if n.path == path), None)
            imp = node.importance if node else 0.6
            days = 0
            if node and node.when is not None:
                days = max(0, (datetime.now() - node.when).days)
            # 同一节点可同时带时序信号(flag)与内容关系(relation)。时序的断更复活
            # 是更稀有的治理事件，优先归为此类；若同时结论漂移，把 drift 作为补充细节。
            if sig.get("flag") == "revived":
                detail = f"断更后复活：距上一稿 gap {sig.get('gap_days', '?')} 天"
                if sig.get("relation") == "drift":
                    detail += (f"；且结论漂移（相似度 {sig.get('sim_prev', '?')} "
                               f"< 阈值 {sig.get('drift_threshold', '?')}）")
                out.append({
                    "path": path, "subject": subject,
                    "status": node.status if node else "active",
                    "stale_days": days,
                    "score": round(score_note(importance=imp, days_since_active=days), 3),
                    "suggestion": "review",
                    "signal": "revived",
                    "signal_detail": detail,
                })
                emitted.add(path)
            elif sig.get("relation") == "drift":
                out.append({
                    "path": path, "subject": subject,
                    "status": node.status if node else "active",
                    "stale_days": days,
                    "score": round(score_note(importance=imp, days_since_active=days), 3),
                    "suggestion": "re-review",
                    "signal": "conclusion_drift",
                    "signal_detail": (f"结论漂移：与上一稿摘要相似度 "
                                      f"{sig.get('sim_prev', '?')} < 阈值 "
                                      f"{sig.get('drift_threshold', '?')}"),
                })
                emitted.add(path)
        # D3：过时旧稿（valid_from/valid_to 编码，valid_to 非空 = 已被同主题后续稿取代）
        for w in tl.valid_windows():
            if w.get("valid_to") is None or w.get("path") in emitted:
                continue
            path = w["path"]
            node = next((n for n in tl.nodes if n.path == path), None)
            imp = node.importance if node else 0.6
            days = 0
            vt = w.get("valid_to")
            if vt:
                try:
                    days = max(0, (datetime.now() - datetime.fromisoformat(vt)).days)
                except (ValueError, TypeError):
                    days = 0
            out.append({
                "path": path, "subject": subject,
                "status": node.status if node else "active",
                "stale_days": days,
                "score": round(score_note(importance=imp, days_since_active=days), 3),
                "suggestion": "supersede",
                "signal": "superseded",
                "signal_detail": f"过时旧稿：已被同主题后续稿取代（valid_to={vt}）",
            })
            emitted.add(path)
    return out


def _merge_signal_candidates(base: list[dict], signals: list[dict]) -> list[dict]:
    """把时间图谱信号候选并入基础候选清单：按路径去重，已上浮的不重复推送。"""
    known = {c["path"] for c in base}
    return base + [s for s in signals if s["path"] not in known]


def _roz_importance(fm: str) -> float:
    """读 frontmatter 的 importance，缺省/异常回退 0.6。"""
    v = _read_fm_value(fm, "importance")
    if v is None:
        return 0.6
    try:
        return float(v)
    except ValueError:
        return 0.6


def governance_health(mem_root: Path, tiers: Iterable[str] | None = None,
                      projects: Iterable[str] | None = None) -> dict:
    """治理健康度只读快照（P1 诊断，无副作用）。

    汇总：各 status 分布、candidate 待审数、candidate 最长停留天数、各状态
    stale 天数分位。只计数不改状态，供周检报告渲染"治理活跃度"段。

    返回：{"by_status": {...}, "candidate_pending": n, "candidate_oldest_days": int,
          "non_active_ratio": float(0~1)}。
    """
    from .protocol import TIER_DIR
    from .recall import scan_tier_dirs
    if tiers is None:
        tiers = TIER_DIR.values()
    by_status: dict[str, int] = {}
    cand_days: list[int] = []
    total = 0
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
                fm = m.group(1)
                status = _read_fm_value(fm, "status") or "active"
                by_status[status] = by_status.get(status, 0) + 1
                total += 1
                if status == "candidate":
                    cand_days.append(stale_days(_read_fm_value(fm, "updated")))
    active = by_status.get("active", 0)
    return {
        "by_status": by_status,
        "candidate_pending": by_status.get("candidate", 0),
        "candidate_oldest_days": max(cand_days) if cand_days else 0,
        "non_active_ratio": round((total - active) / total, 3) if total else 0.0,
    }
