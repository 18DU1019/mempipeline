# -*- coding: utf-8 -*-
"""ingest.py — 投稿汇聚后端：扫描 staging 目录，用前端协议 + 引擎把裸 md 熔合进镜像。

数据无关：staging_dir / tier_dirs / 生成的落盘命名全部由调用方传入，不写死任何路径。
on_ingest 供调用方在每条熔合后执行精确 git add 等副作用。
层映射统一取自 protocol.TIER_DIR，避免重复定义。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from .protocol import (DEFAULT_TIER, DEFAULT_TRUSTED_AGENTS, TIER_DIR,
                       Note, cap_trust, content_key, normalize_trust,
                       parse_frontmatter, safe_project_id, strip_frontmatter,
                       title_token)
from .engine import write_atomic
from .audit import AuditBackend
from .writer_contracts import baseline_trust_of


def _parse_fm(text: str) -> dict:
    """解析首个 frontmatter 块为投稿字段 dict（P2：统一走 protocol.parse_frontmatter）。

    白名单特化：只取 ingest 关心的键；缺键 None（fm.get 天然 None）、空值 ""。
    保留 BOM/前导空白容忍（解析器内置）。与旧实现的差异：
    - 旧实现的值捕获对空值行会跨行吞下一行；新解析器行锚定，杜绝（行为修正）；
    - 缺键/空值对 `or "untitled"` / `or "workbuddy"` 回落语义不变（均为 falsy）。
    """
    fm = parse_frontmatter(text)
    return {
        "title": fm.get("title"),
        "summary": fm.get("summary"),
        "tier": fm.get("memory_tier"),
        "source_agent": fm.get("source_agent") or "workbuddy",
        "writer_id": fm.get("writer_id"),
        "project_id": fm.get("project_id"),
        "domain": fm.get("domain"),
        "kind": fm.get("kind"),
        "created": fm.get("created"),
        "trust": fm.get("trust"),
    }


def ingest(staging_dir: Path, mem_root: Path, tier_dirs: dict[str, str] | None,
           audit: AuditBackend, on_ingest=None, dry_run: bool = False,
           crossref: bool = False, log: Callable[[str], None] | None = None,
           require_project: bool = False,
           trusted_agents: frozenset[str] | None = None) -> dict:
    """扫描 staging 目录，用前端协议 + 引擎把带 frontmatter 的裸 md 熔合进镜像。

    ...（tier_dirs 等说明同上）

    trusted_agents（P0）：写侧信任分层。投稿落盘时把信任档写入 frontmatter：
    - 投稿显式声明 trust 字段 → 归一后按写者基线**封顶**（P1 修复：min 语义，
      自声明不得越过登记基线，杜绝 trust:"trusted" 自我升档直通）；
    - 否则取写者基线（writer_id 登记表优先，无 writer_id 回落 source_agent 名单）；
    - trusted_agents 缺省 None ⇒ 仅信任 "workbuddy"（单写者蒸馏主链高信任）。
    trust 写入 note.extra（经 to_frontmatter 透传），不参与桥导出白名单。
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
        source_agent = fm.get("source_agent") or "workbuddy"
        writer_id = fm.get("writer_id")
        # P0 写侧信任分层（P1 封顶修订）：基线 = writer_id 登记表 > source_agent 名单。
        # source_agent 名单法无法区分 distill/staging 同源(workbuddy)的信任冲突，
        # 故带 writer_id 的登记投稿一律以登记表 baseline_trust 为准。
        # 显式 trust 声明归一后按基线封顶（cap_trust/min）：自声明是又一种自报
        # 证据，不得越过登记基线——堵住投稿写 trust:"trusted" 自我升档的直通道。
        trusted = trusted_agents if trusted_agents is not None else DEFAULT_TRUSTED_AGENTS
        if writer_id:
            baseline = normalize_trust(baseline_trust_of(writer_id), trusted)
        else:
            baseline = normalize_trust(source_agent, trusted)
        if fm.get("trust") is not None:
            trust = cap_trust(normalize_trust(fm["trust"], trusted), baseline)
        else:
            trust = baseline
        note = Note(
            title=fm.get("title") or "untitled",
            summary=fm.get("summary") or "（无摘要）",
            tier=tier,
            importance=0.6,
            source_agent=source_agent,
            project_id=project_id,
            domain=fm.get("domain"),
            created=fm.get("created"),
            body=strip_frontmatter(raw).strip(),
        )
        note.extra["trust"] = trust  # P0：信任档随 frontmatter 落盘（桥导出不透传）
        if writer_id:
            note.extra["writer_id"] = writer_id  # 溯源标签：登记表信任判定维度随稿落盘
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
