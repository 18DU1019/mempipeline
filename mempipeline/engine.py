# -*- coding: utf-8 -*-
"""engine.py — 幂等写引擎：原子写 + 稳定正文判定 + 审计登记。数据无关。

与本地实现同构（distill_memory._write / _stable），但不绑定任何具体源/目标路径。
所有路径经 AuditBackend 与调用方注入。
"""
from __future__ import annotations

import os
from pathlib import Path

from .protocol import stable_body
from .audit import AuditBackend


def write_atomic(out: Path, text: str, audit: AuditBackend,
                 source: str = "engine", skip_if_same: bool = True) -> tuple[str, bool]:
    """幂等原子写：目标已存在且稳定正文相同 → 跳过（返回 ("skipped", False)）。

    原子性：临时文件 + os.replace，读者只会见全旧或全新，杜绝半截读。
    返回 (status, wrote)。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    if skip_if_same and out.exists():
        try:
            if stable_body(out.read_text(encoding="utf-8")) == stable_body(text):
                audit.mark(out, "skip", source=source, change=None)
                return "skipped", False
        except Exception:
            pass
    tmp = out.with_name(out.name + ".tmp-%d" % os.getpid())
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, out)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    audit.mark(out, "write", source=source)
    return "wrote", True
