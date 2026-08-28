# -*- coding: utf-8 -*-
"""engine.py — 幂等写引擎：原子写 + 稳定正文判定 + 审计登记。数据无关。

与本地实现同构（distill_memory._write / _stable），但不绑定任何具体源/目标路径。
所有路径经 AuditBackend 与调用方注入。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .protocol import stable_body
from .audit import AuditBackend


_BAK_SUFFIX = ".bak"


def _bak(out: Path) -> Path:
    return out.with_name(out.name + _BAK_SUFFIX)


def write_atomic(out: Path, text: str, audit: AuditBackend,
                 source: str = "engine", skip_if_same: bool = True) -> tuple[str, bool]:
    """幂等原子写：目标已存在且稳定正文相同 → 跳过（返回 ("skipped", False)）。

    原子性：临时文件 + os.replace，读者只会见全旧或全新，杜绝半截读。
    崩溃恢复 sidecar：为防止进程在 os.replace 之后、审计标记之前中断而留下
    「新文件已落、审计未登记」的不一致，写入前把目标前一版快照为 `.bak`
    sidecar；成功路径删除，崩溃/异常路径保留为恢复点。下次写前检测到残留
    `.bak` 即登记一条 `recover` 审计（把崩溃遗留显式化），再清理。

    幂等跳过时无新增写入，不产生 sidecar；若残留 `.bak` 指向的正是当前内容，
    一并登记后清理，保持镜像无累积性侧车。

    返回 (status, wrote)。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    bak = _bak(out)
    if skip_if_same and out.exists():
        try:
            if stable_body(out.read_text(encoding="utf-8")) == stable_body(text):
                audit.mark(out, "skip", source=source, change=None)
                _clear_stale_recover(out, bak, audit, source)
                return "skipped", False
        except Exception:
            pass
    # 崩溃/中断遗留检测：上一轮未清理的 `.bak` = 一次未完成写入的恢复点
    if bak.exists():
        audit.mark(out, "recover", source=source)
        try:
            bak.unlink(missing_ok=True)
        except OSError:
            pass
    # 写前快照前一版为恢复点（目标已存在时）
    if out.exists():
        bak.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out, bak)
    tmp = out.with_name(out.name + ".tmp-%d" % os.getpid())
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, out)
    except Exception:
        # 异常路径：保留 `.bak` 作为恢复点，供下次检测登记；仅清理临时碎片
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    # 成功：先登记审计，再销毁恢复点，保证一致性窗口可追溯。
    audit.mark(out, "write", source=source)
    try:
        bak.unlink(missing_ok=True)
    except OSError:
        pass
    return "wrote", True


def _clear_stale_recover(out: Path, bak: Path, audit: AuditBackend, source: str) -> None:
    """幂等跳过分支的遗留清理：若残留 `.bak` 内容与当前目标一致，登记后删除。

    幂等分支不会重写目标，故这里只清理、不覆盖；内容不一致的 `.bak`（一个
    真正失败的恢复点）保留，留给下次实际写入时的 recover 检测处理。
    """
    if not bak.exists():
        return
    try:
        if bak.read_bytes() == out.read_bytes():
            audit.mark(out, "recover", source=source)
            bak.unlink(missing_ok=True)
    except OSError:
        pass
