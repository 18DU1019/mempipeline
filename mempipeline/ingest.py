# -*- coding: utf-8 -*-
"""ingest.py — 投稿汇聚后端：扫描 staging 目录，用前端协议 + 引擎把裸 md 熔合进镜像。

数据无关：staging_dir / tier_dirs / 生成的落盘命名全部由调用方传入，不写死任何路径。
on_ingest 供调用方在每条熔合后执行精确 git add 等副作用。
层映射统一取自 protocol.TIER_DIR，避免重复定义。
"""
from __future__ import annotations

from pathlib import Path

from .protocol import DEFAULT_TIER, TIER_DIR, Note, content_key, strip_frontmatter, title_token
from .engine import write_atomic
from .audit import AuditBackend


def _parse_fm(text: str) -> dict:
    import re
    text = text.lstrip("\ufeff \t\r\n")
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    body = m.group(1)
    out = {}

    def get(k):
        mm = re.search(rf"^\s*{k}:\s*(.+)$", body, re.M)
        return mm.group(1).strip() if mm else None

    out["title"] = get("title")
    out["summary"] = get("summary")
    out["tier"] = get("memory_tier")
    out["source_agent"] = get("source_agent") or "workbuddy"
    return out


def ingest(staging_dir: Path, mem_root: Path, tier_dirs: dict[str, str] | None,
           audit: AuditBackend, on_ingest=None, dry_run: bool = False) -> dict:
    """扫描 staging 目录，用前端协议 + 引擎把带 frontmatter 的裸 md 熔合进镜像。

    tier_dirs 传 None 时使用 protocol.TIER_DIR。非法/缺失的 memory_tier 回落 medium。
    落盘为「单一 frontmatter（Note 生成）+ 正文」，原始 frontmatter 不重复进入正文。
    返回 {"wrote": n, "skipped": n}。
    """
    tier_dirs = tier_dirs or TIER_DIR
    if not staging_dir.is_dir():
        return {"wrote": 0, "skipped": 0}
    stats = {"wrote": 0, "skipped": 0}
    for src in sorted(staging_dir.glob("**/*.md")):
        raw = src.read_text(encoding="utf-8")
        fm = _parse_fm(raw)
        tier = fm.get("tier")
        if tier not in tier_dirs:
            tier = DEFAULT_TIER
        note = Note(
            title=fm.get("title") or "untitled",
            summary=fm.get("summary") or "（无摘要）",
            tier=tier,
            importance=0.6,
            source_agent=fm.get("source_agent") or "workbuddy",
            body=strip_frontmatter(raw).strip(),
        )
        token = title_token(note.title)
        content = note.to_frontmatter() + "\n\n" + note.body + "\n"
        out = mem_root / tier_dirs[tier] / f"项目会话-{token}-{content_key(content)}.md"
        print(f"  -> {out.relative_to(mem_root)} (tier={tier})")
        if dry_run:
            continue
        status, _ = write_atomic(out, content, audit, source=f"staging:{src.name}")
        stats[status] += 1
        if on_ingest:
            on_ingest(out)
    return stats


def main(argv=None):
    """CLI 演示：--root MEMROOT --staging STAGING --audit-log LOG --manifest MANIFEST。"""
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True, help="记忆镜像根")
    p.add_argument("--staging", required=True, help="staging 待处理目录")
    p.add_argument("--audit-log", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    from .audit import FileAudit
    audit = FileAudit(Path(a.audit_log), Path(a.manifest), Path(a.root))
    stats = ingest(Path(a.staging), Path(a.root), None, audit, dry_run=a.dry_run)
    print(stats)
