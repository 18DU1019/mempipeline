# -*- coding: utf-8 -*-
"""governance._health — 治理健康度只读快照（P1 诊断，无副作用）。

自 governance.py 拆分（2026-09-14 架构债整改），行为零变化。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ._score import stale_days
from ._state import _FM_RE, _read_fm_value


def governance_health(mem_root: Path, tiers: Iterable[str] | None = None,
                      projects: Iterable[str] | None = None) -> dict:
    """治理健康度只读快照（P1 诊断，无副作用）。

    汇总：各 status 分布、candidate 待审数、candidate 最长停留天数、各状态
    stale 天数分位。只计数不改状态，供周检报告渲染"治理活跃度"段。

    返回：{"by_status": {...}, "read_errors": n, "candidate_pending": n, "candidate_oldest_days": int,
          "non_active_ratio": float(0~1)}。read_errors=读失败条目数（G 项可见性）。
    """
    from ..protocol import TIER_DIR
    from ..recall import scan_tier_dirs
    if tiers is None:
        tiers = TIER_DIR.values()
    by_status: dict[str, int] = {}
    cand_days: list[int] = []
    total = 0
    read_err = 0  # G 项（2026-09-17）：读失败计数，随报告显形
    for tier in tiers:
        for d in scan_tier_dirs(mem_root, tier, projects):
            for md in d.glob("*.md"):
                try:
                    txt = md.read_text(encoding="utf-8")
                except Exception:
                    read_err += 1  # G 项：计数不改变行为
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
        "read_errors": read_err,
        "candidate_pending": by_status.get("candidate", 0),
        "candidate_oldest_days": max(cand_days) if cand_days else 0,
        "non_active_ratio": round((total - active) / total, 3) if total else 0.0,
    }
