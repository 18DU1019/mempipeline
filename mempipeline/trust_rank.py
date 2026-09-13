# -*- coding: utf-8 -*-
"""trust_rank.py — 写侧信任分层（P0/方案 B/3a）读取侧二段降权。

3a 约束：不改 RecallBackend 契约（recall 仍只返回 (path, score)），降权在
调用方对召回结果做二段——先由 trust_of_path 解析单条笔记的信任档，再由
rank_with_trust 稳定重排：trusted > unknown > untrusted，同档保持原序；
k 收紧时 untrusted 先被裁，unknown 次之，trusted 最后裁。

数据无关：路径全部注入；默认可信名单由调用方提供，本模块不写死任何来源。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Iterable

from .protocol import (TRUST_KNOWN, TRUST_UNKNOWN, TRUST_UNTRUSTED,
                       normalize_trust)

# 档位→排序权重：越小越优先。untrusted 权重最大（排最后）。
_WEIGHT = {TRUST_KNOWN: 0, TRUST_UNKNOWN: 1, TRUST_UNTRUSTED: 2}


def _scan_fm(path: Path) -> tuple[str | None, str | None]:
    """单次读文件，解析首个 frontmatter 块内的 trust 与 source_agent 两字段。

    与 ingest 契约一致（行锚定、经 unquote 剥引号反 DQ 转义）。一次文件读
    避免 trust_of_path 旧实现的双读（先 _fm_trust 再重扫 source_agent）。
    返回 (trust, source_agent)，读不到/无 frontmatter 对应 None。
    """
    try:
        txt = path.read_text(encoding="utf-8")
    except Exception:
        return None, None
    m = re.match(r"^---\s*\n(.*?)\n---", txt, re.S)
    if not m:
        return None, None
    fm = m.group(1)
    from .protocol import unquote
    trust = source = None
    mm = re.search(r"(?m)^\s*trust\s*:\s*(.+)$", fm)
    if mm:
        trust = unquote(mm.group(1).strip())
    mm = re.search(r"(?m)^\s*source_agent\s*:\s*(.+)$", fm)
    if mm:
        source = unquote(mm.group(1).strip())
    return trust, source


def trust_of_path(md_path: str | Path, trusted: frozenset[str]) -> str:
    """解析单条笔记的信任档：显式 trust 优先，否则按来源名单映射。

    规则（与方案 B 一致）：
    - frontmatter 含 trust 字段 → 直接归一（显式覆盖来源映射）；
    - 无 trust 字段 → 读 source_agent，在 trusted 名单内→trusted，否则 untrusted；
    - 两者都无 → unknown（存量无标记保守回落）。
    """
    path = Path(md_path)
    trust, source = _scan_fm(path)
    if trust is not None:
        return normalize_trust(trust, trusted)
    if source is not None:
        return normalize_trust(source, trusted)
    return TRUST_UNKNOWN


def rank_with_trust(hits: Iterable[tuple[str, float]],
                    trust_of: Callable[[str], str],
                    k: int = 8) -> list[tuple[str, float]]:
    """对 recall 结果 (path, score) 做信任降权稳定排序，返回前 k。

    排序键：(档位权重, 原索引) —— 档位优先，同档内保持原相对序（稳定）。
    语义：trusted 全量先排，untrusted 仅在 trusted+unknown 不足 k 时才补入。
    """
    scored = [(w, idx, p, s) for idx, (p, s) in enumerate(hits)
              for w in [_WEIGHT.get(trust_of(p), 2)]]
    scored.sort(key=lambda x: (x[0], x[1]))
    return [(p, s) for _, _, p, s in scored[:k]]