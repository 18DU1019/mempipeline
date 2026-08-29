# -*- coding: utf-8 -*-
"""crossref.py — 零向量件的「双向交叉引用」工具。

把 agent-memory-vault 的双向链接思想移植到 mempipeline 读者端，但不引入向量：
用「字符 bigram Jaccard」相似度（中文无需分词、子串即可命中），为新笔记找 Top-K
语义相近的旧笔记，把 [[wikilinks]] 双向写入——新笔记的 forward 链接、每篇命中
笔记的 backlink。

写入入口统一走 engine.write_atomic（幂等 + 审计），守住单写者闸门：
- forward：调用方在 ingest 落盘前先把 links 写进 frontmatter（保证文件名 hash
  与内容一致、单次写）；
- backlink：对每篇命中笔记经 write_atomic 追加，正文未变则幂等跳过。
数据无关：mem_root / tiers / 阈值全部由调用方注入，不写死任何路径。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .protocol import _fmt_scalar  # noqa: F401 保留 import 位（后续可直接用）
from .engine import write_atomic
from .audit import AuditBackend
from .recall import scan_tier_dirs

WIKILINK_MAX = 8    # 每条笔记最多挂的交叉引用数（对应 AMV 的 top-K）
MIN_SCORE = 0.05    # 相似度下限（bigram Jaccard）：零/极低重叠的直接丢弃

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---", re.S)


def _norm_text(md: Path) -> str:
    try:
        return "".join(md.read_text(encoding="utf-8").split())
    except Exception:
        return ""


def _norms(s: str) -> str:
    return "".join(s.split())


def _bigrams(s: str) -> set[str]:
    """取相邻 2 字符的集合。中文无需分词即稳健：子串（标点看似不同）仍共享词内 bigram。"""
    n = _norms(s)
    return {n[i:i + 2] for i in range(len(n) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _title_of(md: Path) -> str:
    """从笔记 frontmatter 取 title 作为 wikilink 锚文本；失败回落文件名。"""
    try:
        txt = md.read_text(encoding="utf-8")
    except Exception:
        return md.stem
    m = _FM_RE.match(txt)
    if m:
        mm = re.search(r"(?m)^\s*title:\s*(.+)$", m.group(1))
        if mm:
            val = mm.group(1).strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            if val:
                return val
    return md.stem


def _links_of(text: str) -> str:
    """读取现有 frontmatter 的 links 值（剥外层引号），无则返回空串。"""
    m = _FM_RE.match(text)
    if not m:
        return ""
    mm = re.search(r"(?m)^\s*links:\s*(.+)$", m.group(1))
    if not mm:
        return ""
    val = mm.group(1).strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        val = val[1:-1]
    return val


def _upsert_links(text: str, links: str) -> str:
    """在 frontmatter 内插入/替换 links 行（DQ 标量，符合转义契约）。"""
    m = _FM_RE.match(text)
    if not m:                       # 无 frontmatter 则不改动
        return text
    fm = m.group(1)
    line = f"links: {_fmt_scalar(links)}"
    if re.search(r"(?m)^\s*links:", fm):
        fm = re.sub(r"(?m)^\s*links:.*$", line, fm)
    else:
        parts = fm.splitlines()
        insert_at = next((i for i, l in enumerate(parts)
                          if re.match(r"^\s*status:", l) or re.match(r"^\s*updated:", l)),
                         len(parts))
        parts.insert(insert_at, line)
        fm = "\n".join(parts)
    return f"---\n{fm}\n---" + text[m.end():]


def find_related(note_text: str, mem_root: Path, tiers: Iterable[str] | None = None,
                 k: int = WIKILINK_MAX, min_score: float | None = MIN_SCORE,
                 exclude: Path | None = None,
                 exclude_title: str | None = None,
                 projects: Iterable[str] | None = None) -> list[Path]:
    """返回与 note_text 最相近的 Top-K 现有笔记（去空白全文的字符 bigram Jaccard）。

    结果按相似度降序，并过滤低于 min_score 的零/极低重叠项。
    exclude 排除指定路径；exclude_title 排除同标题笔记（重跑时已落盘的同一篇自身，
    避免自我链接破坏幂等）。projects 限定建链作用域（None=全库）——G1 修复：
    项目隔离下交叉引用必须只在同项目内建链，防止跨项目链接泄漏。
    无向量、无分词依赖，中文子串即可命中。
    """
    ga = _bigrams(note_text)
    if not ga:
        return []
    if tiers is None:
        from .protocol import TIER_DIR
        tiers = TIER_DIR.values()
    scored: list[tuple[Path, float]] = []
    for tier in tiers:
        for d in scan_tier_dirs(mem_root, tier, projects):
            for md in d.glob("*.md"):
                if exclude is not None and md.resolve() == exclude.resolve():
                    continue
                if exclude_title is not None and _title_of(md) == exclude_title:
                    continue
                s = _jaccard(ga, _bigrams(_norm_text(md)))
                if min_score is not None and s < min_score:
                    continue
                scored.append((md, s))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [md for md, _ in scored[:k]]


def links_string(paths: Iterable[Path]) -> str:
    """把一批笔记路径拼成 wikilink 串，形如 [[A]] [[B]]。"""
    return " ".join(f"[[{_title_of(p)}]]" for p in paths)


def add_backlinks(mem_root: Path, new_title: str, related: Iterable[Path],
                  audit: AuditBackend, source: str = "crossref") -> dict:
    """给每篇命中笔记追加指向 new_title 的 backlink（幂等 + 审计）。

    返回 {"linked": n, "skipped": n}。backlink 走 write_atomic：stable_body 未变
    （链接已存在）→ 跳过；
    """
    stats = {"linked": 0, "skipped": 0}
    back = f"[[{new_title}]]"
    for target in related:
        try:
            cur = target.read_text(encoding="utf-8")
        except Exception:
            continue
        exist = _links_of(cur)
        if back in exist:            # 已链接 → 幂等跳过（含反斜杠无关的精确匹配）
            stats["skipped"] += 1
            continue
        merged = f"{exist} {back}".strip()
        status, _ = write_atomic(target, _upsert_links(cur, merged), audit,
                                 source=f"backlink:{new_title}")
        if status == "wrote":
            stats["linked"] += 1     # 修复：wrote 计入 linked，不再误落 skipped
        else:
            stats["skipped"] += 1
    return stats


def upsert_crossrefs(note_text: str, out: Path, mem_root: Path, audit: AuditBackend,
                     tiers: Iterable[str] | None = None,
                     projects: Iterable[str] | None = None) -> tuple[str, dict]:
    """独立入口（engine 用户用）：对新笔记内容 upsert forward links，再回写 backlinks。

    返回 (links_str, backlinks_stats)。注意：这是就地改文本；若已落盘需调用方
    自行经 write_atomic 写回。ingest 走的是更优路径（落盘前注入，见 ingest）。
    """
    related = find_related(note_text, mem_root, tiers, exclude=out, projects=projects)
    fwd = links_string(related)
    back = add_backlinks(mem_root, _title_of(out), related, audit) if related else \
        {"linked": 0, "skipped": 0}
    return fwd, back