# -*- coding: utf-8 -*-
"""panel_ops.py — 面板纯逻辑层（B5 从 panel.py 抽出）。

集中面板的数据读取 / 格式化 / 投稿 / 索引治理等无 HTTP 依赖的叶子函数，
panel.py 只保留 `_Handler` / `serve` 与视图层，借此把原 602 行主模块拆薄。
函数体与拆分前逐字一致（行为零变化）；外部代码经 `mempipeline.panel` 命名空间
re-export 继续可用（如 weekly_health 用 collect_stats、test_panel 用
_submit_note/_read_frontmatter）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from .audit import NullAudit
from .governance import review_queue


def _read_frontmatter(md: Path) -> dict:
    _FM = re.compile(r"^---\s*\n(.*?)\n---", re.S)
    try:
        txt = md.read_text(encoding="utf-8")
    except Exception:
        return {}
    m = _FM.match(txt)
    if not m:
        return {}
    fm = m.group(1)
    out = {}

    def get(k):
        from .protocol import unquote
        mm = re.search(rf"(?m)^\s*{k}:\s*(.+)$", fm)
        if not mm:
            return ""
        return unquote(mm.group(1).strip())

    for k in ("title", "memory_tier", "status", "project_id", "domain", "kind",
              "source_agent", "updated", "importance"):
        out[k] = get(k)
    return out


def collect_stats(mem_root: Path, tiers: Iterable[str] | None = None) -> dict:
    """镜像统计：总数 + status/project/tier 分布 + 审核队列数。"""
    from .protocol import TIER_DIR
    from .recall import scan_tier_dirs
    if tiers is None:
        tiers = TIER_DIR.values()
    total = 0
    by_status: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    by_project: dict[str, int] = {}
    for tier in tiers:
        for d in scan_tier_dirs(mem_root, tier, None):
            for md in d.glob("*.md"):
                fm = _read_frontmatter(md)
                total += 1
                st = fm.get("status") or "active"
                by_status[st] = by_status.get(st, 0) + 1
                by_tier[tier] = by_tier.get(tier, 0) + 1
                pid = fm.get("project_id") or "(legacy)"
                by_project[pid] = by_project.get(pid, 0) + 1
    queue = len(review_queue(mem_root, tiers, None))
    return {"total": total, "by_status": by_status, "by_tier": by_tier,
            "by_project": by_project, "queue": queue}


def _audit_tail(log_path: Path, n: int = 30) -> list[str]:
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
        return lines[-n:]
    except Exception:
        return []


def _browse(mem_root: Path, page: int = 1, limit: int = 50) -> dict:
    """全量记忆浏览（分页，按更新时间倒序）。"""
    from .protocol import TIER_DIR
    from .recall import scan_tier_dirs
    rows = []
    for tier in TIER_DIR.values():
        for d in scan_tier_dirs(mem_root, tier, None):
            for md in d.glob("*.md"):
                fm = _read_frontmatter(md)
                if fm.get("status") == "redacted":
                    continue  # P3.12 §2.5 展示收敛：redacted 全程退出浏览列表
                try:
                    mtime = md.stat().st_mtime
                except Exception:
                    mtime = 0.0
                rows.append({
                    "title": fm.get("title") or md.stem,
                    "tier": tier,
                    "status": fm.get("status") or "active",
                    "updated": fm.get("updated") or "",
                    "path": str(md),
                    "mtime": mtime,
                })
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    total = len(rows)
    start = (page - 1) * limit
    return {"total": total, "page": page, "limit": limit,
            "rows": rows[start:start + limit]}


def _submit_note(title: str, body: str, tier: str, project: str,
                 staging_root: Path) -> dict:
    """面板直接投稿：写入 staging 投稿位（契约对齐 bridge）。

    幂等：同 token+内容哈希 的既有文件不重复写。
    DQ 转义：用户可控字符串字段一律经 _fmt_scalar，与 protocol 的
    「字符串字段一律 YAML 双引号标量」契约一致（2026-08-27 起生效）。
    """
    from .engine import write_atomic
    from .protocol import _fmt_scalar, content_key, now_iso, title_token
    if not title or not body:
        return {"ok": False, "error": "标题与内容不能为空"}
    tier = tier if tier in ("long", "medium") else "medium"
    token = title_token(title)
    fname = f"项目会话-{token}-{content_key(body)}.md"
    out = staging_root / fname
    summary = " ".join(body.strip().splitlines()[:2])[:120]
    text = (f"---\ntype: note\ntitle: {_fmt_scalar(title)}\n"
            f"summary: {_fmt_scalar(summary)}\n"
            f"memory_tier: {_fmt_scalar(tier)}\n"
            f"project_id: {_fmt_scalar(project or '')}\n"
            f"importance: 0.5\n"
            f"source_staging: {_fmt_scalar('workbuddy-panel')}\n"
            f"status: {_fmt_scalar('candidate')}\n"
            f"updated: {_fmt_scalar(now_iso())}\n---\n\n{body.strip()}\n")
    st, _ = write_atomic(out, text, NullAudit(), source="panel-submit")
    return {"ok": st in ("wrote", "skipped"), "status": st,
            "path": str(out)}


def _index_status(index, mem_root: Path) -> dict:
    """语义索引 vs 镜像篇数。"""
    from .protocol import TIER_DIR
    from .recall import scan_tier_dirs
    total = 0
    for tier in TIER_DIR.values():
        for d in scan_tier_dirs(mem_root, tier, None):
            total += len(list(d.glob("*.md")))
    indexed = index.count() if index is not None else -1
    return {"indexed": indexed, "mirror": total,
            "gap": (total - indexed) if indexed >= 0 else None,
            "enabled": index is not None}


def _reindex(index, mem_root: Path) -> dict:
    """一键重建语义索引（同步执行；129 篇量级约数秒）。"""
    if index is None:
        return {"ok": False, "error": "语义索引未启用"}
    try:
        index.build(mem_root)
        return {"ok": True, "indexed": index.count()}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _json(handler, obj: dict, status: int = 200) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)