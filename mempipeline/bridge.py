# -*- coding: utf-8 -*-
"""bridge.py — 通用投稿桥（E4'）：mempipeline Note ↔ Obsidian vault 双 schema 映射（G2）。

审查结论 G2：mempipeline Note 字段（title/summary/memory_tier/importance/source_agent/
project_id/domain/kind/status）与 vault 体检六字段（type/summary/status/updated/
aliases/tags）是两套体系——资产化直接搬会丢字段。本模块定义双向映射：

投稿方向（vault → mempipeline）：vault md → ingest（_parse_fm 已支持解析；
vault 的 tags/type 忽略，memory_tier 缺失回落 medium）。

资产化方向（mempipeline → vault，本模块核心）：
    type: note                    # 保留
    title / summary: 保留
    memory_tier / importance: 保留（vault 兼容附加字段）
    status: vault_status(status)  # promoted→active（governance，G8）
    updated: 保留或刷新
    aliases: 不默认生成（vault aliases 覆盖率仅 2%，避免噪声）
    tags: [kind, 层级中文名] 派生  # 补 vault tags 缺口（覆盖率 39%）
    project_id / domain / kind: 保留（mempipeline 溯源字段）

导出目标（E3 设计）：vault_root / 99-DSP记忆 / projects/<pid>/ 与 global/ 分流。
写盘经 engine.write_atomic（幂等 + 可选审计），零删除。
数据无关：mem_root / vault_root 全部注入。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Iterable

from .protocol import _fmt_scalar, content_key, now_iso, safe_project_id, strip_frontmatter, title_token
from .engine import write_atomic
from .audit import AuditBackend
from .recall import scan_tier_dirs
from .governance import vault_status

VALUE_TAGS = {"long": "长期记忆", "medium": "中期记忆"}
EXPORT_ROOT = "99-DSP记忆"
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---", re.S)


def _fm_get(fm: str, key: str) -> str:
    mm = re.search(rf"(?m)^\s*{key}:\s*(.+)$", fm)
    if not mm:
        return ""
    val = mm.group(1).strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        val = val[1:-1]
    return val


def _parse_note(md: Path) -> dict:
    """解析镜像笔记 frontmatter 为 dict（与 ingest._parse_fm 互补，供导出用）。"""
    try:
        txt = md.read_text(encoding="utf-8")
    except Exception:
        return {}
    m = _FM_RE.match(txt)
    if not m:
        return {}
    fm = m.group(1)
    keys = ("title", "summary", "memory_tier", "importance", "source_agent",
            "status", "project_id", "domain", "kind", "updated")
    out = {k: _fm_get(fm, k) for k in keys}
    out["_body"] = strip_frontmatter(txt).strip()
    out["_path"] = str(md.resolve())
    return out


def vault_frontmatter(note: dict) -> str:
    """把解析后的笔记 dict 序列化为 vault 兼容 frontmatter（六字段 + 溯源字段）。"""
    tier = note.get("memory_tier") or "medium"
    kind = note.get("kind") or "note"
    tags = [kind, VALUE_TAGS.get(tier, tier)]
    status = vault_status(note.get("status") or "draft")
    lines = [
        "---",
        f"type: {_fmt_scalar('note')}",
        f"title: {_fmt_scalar(note.get('title') or 'untitled')}",
        f"summary: {_fmt_scalar(note.get('summary') or '（无摘要）')}",
        f"memory_tier: {_fmt_scalar(tier)}",
        f"importance: {note.get('importance') or 0.6}",
        f"status: {_fmt_scalar(status)}",
        f"updated: {_fmt_scalar(note.get('updated') or now_iso())}",
        f"tags: {_fmt_scalar(';'.join(tags))}",
    ]
    for k in ("project_id", "domain", "kind", "source_agent"):
        if note.get(k):
            lines.append(f"{k}: {_fmt_scalar(note[k])}")
    lines.append("---")
    return "\n".join(lines)


def export_promoted(mem_root: Path, vault_root: Path,
                    tiers: Iterable[str] | None = None,
                    projects: Iterable[str] | None = None,
                    audit: AuditBackend | None = None,
                    dry_run: bool = False,
                    log: Callable[[str], None] | None = None) -> dict:
    """资产化导出：扫描镜像 status=promoted 笔记 → 写 vault 风格 md（分流 99-DSP记忆）。

    分流：project_id 存在 → 99-DSP记忆/projects/<pid>/；否则（或 domain=global）→
    99-DSP记忆/global/。文件名 {token}-{content_key}.md 与镜像一致（幂等：同内容跳过）。
    返回 {"exported": n, "skipped": n, "dry_run": n}。
    """
    from .protocol import TIER_DIR
    if tiers is None:
        tiers = TIER_DIR.values()
    stats = {"exported": 0, "skipped": 0, "dry_run": 0}
    for tier in tiers:
        for d in scan_tier_dirs(mem_root, tier, projects):
            for md in d.glob("*.md"):
                note = _parse_note(md)
                if note.get("status") != "promoted":
                    continue
                fm = vault_frontmatter(note)
                body = note.get("_body") or ""
                text = fm + "\n\n" + body + "\n"
                pid = safe_project_id(note.get("project_id"))
                domain = note.get("domain")
                if domain == "global" or not pid:
                    out_dir = vault_root / EXPORT_ROOT / "global"
                else:
                    out_dir = vault_root / EXPORT_ROOT / "projects" / pid
                token = title_token(note.get("title") or "untitled")
                out = out_dir / f"{token}-{content_key(text)}.md"
                (log or print)(f"  -> {out.relative_to(vault_root)}")
                if dry_run:
                    stats["dry_run"] += 1
                    continue
                if out.exists():
                    stats["skipped"] += 1
                    continue
                st, _ = write_atomic(out, text, audit or _NullAudit(),
                                     source="bridge:export")
                stats["exported" if st == "wrote" else "skipped"] += 1
    return stats


class _NullAudit(AuditBackend):
    """audit 未注入时的静默后端（导出为副本资产，审计可选）。"""

    def mark(self, path, kind, source="manual", change=None):
        return {"path": str(path), "kind": kind, "source": source}


def main(argv=None) -> int:
    """CLI：导出 promoted 笔记到 vault。--dry-run 预览。"""
    import argparse
    p = argparse.ArgumentParser(description="mempipeline 资产化导出桥")
    p.add_argument("--mem-root", required=True, help="记忆镜像根")
    p.add_argument("--vault-root", required=True, help="Obsidian vault 根")
    p.add_argument("--dry-run", action="store_true", help="仅预览不写盘")
    a = p.parse_args(argv)
    stats = export_promoted(Path(a.mem_root), Path(a.vault_root),
                            dry_run=a.dry_run)
    print(stats)
    return 0
