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
                       cap_trust, normalize_trust)
from .writer_contracts import baseline_trust_of

# 档位→排序权重：越小越优先。untrusted 权重最大（排最后）。
_WEIGHT = {TRUST_KNOWN: 0, TRUST_UNKNOWN: 1, TRUST_UNTRUSTED: 2}


def _scan_fm(path: Path) -> tuple[str | None, str | None, str | None]:
    """单次读文件，解析首个 frontmatter 块内的 trust / source_agent / writer_id。

    与 ingest 契约一致（行锚定、经 unquote 剥引号反 DQ 转义）。一次文件读
    避免逐字段重复读。返回 (trust, source_agent, writer_id)，读不到对应 None。
    """
    try:
        txt = path.read_text(encoding="utf-8")
    except Exception:
        return None, None, None
    m = re.match(r"^---\s*\n(.*?)\n---", txt, re.S)
    if not m:
        return None, None, None
    fm = m.group(1)
    from .protocol import unquote
    trust = source = writer = None
    mm = re.search(r"(?m)^\s*trust\s*:\s*(.+)$", fm)
    if mm:
        trust = unquote(mm.group(1).strip())
    mm = re.search(r"(?m)^\s*source_agent\s*:\s*(.+)$", fm)
    if mm:
        source = unquote(mm.group(1).strip())
    mm = re.search(r"(?m)^\s*writer_id\s*:\s*(.+)$", fm)
    if mm:
        writer = unquote(mm.group(1).strip())
    return trust, source, writer


def trust_of_path(md_path: str | Path, trusted: frozenset[str]) -> str:
    """解析单条笔记的信任档：显式 trust 归一后**封顶**于写者基线（P1 修订）。

    规则（与方案 B/P0 + P1 封顶一致）：
    - 基线解析：writer_id 登记表 baseline_trust_of 优先（登记表是信任唯一
      权威源，source_agent 名单法无法区分 distill/staging 同源冲突）；
      无 writer_id → 回退 source_agent 名单映射，保持向后兼容；两者皆无 →
      unknown（零身份背书保守上限）。
    - frontmatter 含 trust 字段 → 归一后按基线封顶（cap_trust/min）：自声明
      是又一种自报证据，不得越过基线——存量由旧版写入的越权 trust 值在读取
      侧同样被压回，无需重灌。
    - 无 trust → 直接取基线。
    """
    path = Path(md_path)
    trust, source, writer = _scan_fm(path)
    if writer:
        baseline = normalize_trust(baseline_trust_of(writer), trusted)
    elif source is not None:
        baseline = normalize_trust(source, trusted)
    else:
        baseline = TRUST_UNKNOWN
    if trust is not None:
        return cap_trust(normalize_trust(trust, trusted), baseline)
    return baseline


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