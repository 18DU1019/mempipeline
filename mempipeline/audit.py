# -*- coding: utf-8 -*-
"""audit.py — 审计后端抽象：append-only 日志 + 外部指纹 manifest（可选 git add 回调）。

开源解释：AuditBackend 是"记录一次写入并支持回查"的可插拔接口。默认为 FileAudit
（日志文件 + manifest 指纹），与现有实现语义同构；使用者可替换为 DB/API 后端。
数据层面只登记文件相对路径与哈希指纹，不复制正文，不落业务内容。
"""
from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Optional


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1_000_000), b""):
            h.update(chunk)
    return h.hexdigest()


class AuditBackend(ABC):
    @abstractmethod
    def mark(self, path: Path, kind: str, source: str = "manual", change: Optional[str] = None) -> dict:
        ...


class FileAudit(AuditBackend):
    """基于文件的后端：log 只追加 + manifest.json 指纹登记（含谱幂）。

    构造参数全为可注入路径，天然可配。git_add 回调可选，用于写后立即精确暂存。
    """

    def __init__(self,
                 log_path: Path,
                 manifest_path: Path,
                 root_dir: Path,
                 git_add: Optional[Callable[[str], None]] = None):
        self.log_path = log_path
        self.manifest_path = manifest_path
        self.root_dir = root_dir
        self._git_add = git_add

    def _rel(self, p: Path) -> str:
        try:
            return str(p.resolve().relative_to(self.root_dir.resolve())).replace("\\", "/")
        except ValueError:
            return str(p.resolve())

    def _load(self) -> dict:
        if self.manifest_path.exists():
            try:
                return json.loads(self.manifest_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def mark(self, path: Path, kind: str, source: str = "manual", change: Optional[str] = None) -> dict:
        rel = self._rel(path)
        rec = {"path": rel, "kind": kind, "source": source,
               "hash": sha256(path) if path.exists() else None}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        chg = {"收敛": "收敛", "质变": "质变"}.get(change, "-")
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(f"| {rel} | {kind} | {source} | {(rec['hash'] or '-')[:12]} | {chg} |\n")
        m = self._load()
        m.setdefault("files", {})[rel] = {"kind": kind, "source": source, "hash": rec["hash"]}
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        if self._git_add is not None:
            self._git_add(rel)
        return rec
