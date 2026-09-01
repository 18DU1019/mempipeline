# -*- coding: utf-8 -*-
"""ingest.py — 投稿汇聚后端：扫描 staging 目录，用前端协议 + 引擎把裸 md 熔合进镜像。

数据无关：staging_dir / tier_dirs / 生成的落盘命名全部由调用方传入，不写死任何路径。
on_ingest 供调用方在每条熔合后执行精确 git add 等副作用。
层映射统一取自 protocol.TIER_DIR，避免重复定义。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from .protocol import DEFAULT_TIER, TIER_DIR, Note, content_key, safe_project_id, strip_frontmatter, title_token
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
        if not mm:
            return None
        val = mm.group(1).strip()
        # 契约强制双引号：剥匹配的外层引号，保留值本身
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        return val

    out["title"] = get("title")
    out["summary"] = get("summary")
    out["tier"] = get("memory_tier")
    out["source_agent"] = get("source_agent") or "workbuddy"
    out["project_id"] = get("project_id")
    out["domain"] = get("domain")
    out["kind"] = get("kind")
    return out


def ingest(staging_dir: Path, mem_root: Path, tier_dirs: dict[str, str] | None,
           audit: AuditBackend, on_ingest=None, dry_run: bool = False,
           crossref: bool = False, log: Callable[[str], None] | None = None,
           require_project: bool = False) -> dict:
    """扫描 staging 目录，用前端协议 + 引擎把带 frontmatter 的裸 md 熔合进镜像。

    tier_dirs 传 None 时使用 protocol.TIER_DIR。非法/缺失的 memory_tier 回落 medium。
    落盘为「单一 frontmatter（Note 生成）+ 正文」，原始 frontmatter 不重复进入正文。
    project_id 存在时落盘到 projects/<project_id>/<tier_dir>/（E1 二维布局），
    缺省回落 legacy 顶层 tier 目录；require_project=True 时无 project_id 投稿
    拒绝入库（对齐 DSP「无 ID 禁上报」），计入 stats["rejected"]。
    crossref=True 时在落盘前给新笔记注入到最相近旧笔记的 [[wikilinks]]（forward），
    并对每篇命中笔记回写 backlink；两者都经 write_atomic（幂等 + 审计）。
    交叉引用仅在同项目作用域内建链（G1：防止跨项目链接泄漏）。
    返回 {"wrote": n, "skipped": n, "rejected": n, "crossref": {...}（可选）}。
    """
    tier_dirs = tier_dirs or TIER_DIR
    if not staging_dir.is_dir():
        return {"wrote": 0, "skipped": 0, "rejected": 0}
    stats = {"wrote": 0, "skipped": 0, "rejected": 0}
    if crossref:
        stats["crossref"] = {"linked": 0, "skipped": 0, "forward": 0}
    for src in sorted(staging_dir.glob("**/*.md")):
        raw = src.read_text(encoding="utf-8")
        fm = _parse_fm(raw)
        project_id = safe_project_id(fm.get("project_id"))
        if require_project and not project_id:
            (log or print)(f"  !! 拒绝（无 project_id）：{src.name}")
            stats["rejected"] += 1
            continue
        tier = fm.get("tier")
        if tier not in tier_dirs:
            tier = DEFAULT_TIER
        note = Note(
            title=fm.get("title") or "untitled",
            summary=fm.get("summary") or "（无摘要）",
            tier=tier,
            importance=0.6,
            source_agent=fm.get("source_agent") or "workbuddy",
            project_id=project_id,
            domain=fm.get("domain"),
            body=strip_frontmatter(raw).strip(),
        )
        if fm.get("kind"):
            note.extra["kind"] = fm["kind"]
        token = title_token(note.title)
        body_raw = note.body
        related = []
        if crossref:
            # 落盘前先算相关笔记，把 forward 链接写进 frontmatter，
            # 保证 content_key 与最终内容一致、整篇单次写。
            from .crossref import find_related, links_string
            # exclude_title=note.title：排除与即将落盘笔记同标题的旧笔记，
            # 重跑时不会把已落盘的自身当最相似项（保住幂等）。
            # projects=[project_id]：G1 项目作用域（None=全库）。
            related = find_related(body_raw, mem_root, tier_dirs.values(),
                                   exclude_title=note.title,
                                   projects=[project_id] if project_id else None)
            if related:
                note.extra["links"] = links_string(related)
        content = note.to_frontmatter() + "\n\n" + note.body + "\n"
        if project_id:
            out = (mem_root / "projects" / project_id / tier_dirs[tier]
                   / f"{token}-{content_key(content)}.md")
        else:
            out = mem_root / tier_dirs[tier] / f"项目会话-{token}-{content_key(content)}.md"
        (log or print)(f"  -> {out.relative_to(mem_root)} (tier={tier}"
                       + (f", project={project_id}" if project_id else "")
                       + (f", links={len(related)}" if crossref else "") + ")")
        if dry_run:
            continue
        status, _ = write_atomic(out, content, audit, source=f"staging:{src.name}")
        stats[status] += 1
        if crossref and related:
            from .crossref import add_backlinks
            back = add_backlinks(mem_root, note.title, related, audit)
            stats["crossref"]["linked"] += back["linked"]
            stats["crossref"]["skipped"] += back["skipped"]
            stats["crossref"]["forward"] += 1
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
    p.add_argument("--require-project", action="store_true",
                   help="无 project_id 的投稿拒绝入库（DSP 无 ID 禁上报）")
    a = p.parse_args(argv)
    from .audit import FileAudit
    audit = FileAudit(Path(a.audit_log), Path(a.manifest), Path(a.root))
    stats = ingest(Path(a.staging), Path(a.root), None, audit, dry_run=a.dry_run,
                   require_project=a.require_project)
    print(stats)
