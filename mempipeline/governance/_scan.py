# -*- coding: utf-8 -*-
"""governance._scan — 候选扫描（stale 清单 + 时间图谱信号合并）。只出清单不改状态。

自 governance.py 拆分（2026-09-14 架构债整改），行为零变化。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable

from ._score import (SUPERSEDED_PENALTY, TIER_POLICY, score_note, stale_days)
from ._state import _FM_RE, _read_fm_value


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
    from ..protocol import TIER_DIR
    from ..recall import scan_tier_dirs
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
