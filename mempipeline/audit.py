# -*- coding: utf-8 -*-
"""audit.py — 审计后端抽象：append-only 日志 + 外部指纹 manifest（可选 git add 回调）。

开源解释：AuditBackend 是"记录一次写入并支持回查"的可插拔接口。默认为 FileAudit
（日志文件 + manifest 指纹，manifest 采用原子替换写），与现有实现语义同构；
使用者可替换为 DB/API 后端。数据层面只登记文件相对路径与哈希指纹，不复制正文。
"""
from __future__ import annotations

import hashlib
import json
import os
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1_000_000), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _now_iso() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


class AuditBackend(ABC):
    @abstractmethod
    def mark(self, path: Path, kind: str, source: str = "manual", change: Optional[str] = None,
             prev_hash: Optional[str] = None, detail: Optional[str] = None) -> dict:
        ...


class NullAudit(AuditBackend):
    """无审计后端时的静默实现：mark/trace 均不落库（写/跳与状态轨迹忽略）。

    原在 bridge.py 与 panel.py 各存一份 duck 型副本，现合并为单一 AuditBackend 实现，
    供两者复用，保证"无审计后端不崩溃"行为全程一致。
    """

    def mark(self, path: Path, kind: str, source: str = "manual", change: Optional[str] = None,
             prev_hash: Optional[str] = None, detail: Optional[str] = None) -> dict:
        return {}

    def trace(self, path: Path, from_state: str, to_state: str, reason: str,
              source: str = "governance") -> dict:
        return {}


class FileAudit(AuditBackend):
    """基于文件的后端：log 只追加 + manifest.json 指纹登记（原子替换写）。

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
        """读取 manifest。损坏时备份到 .corrupt-{ts} 并抛错，绝不静默重置历史。"""
        if not self.manifest_path.exists():
            return {}
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8-sig"))
        except Exception:
            backup = self.manifest_path.with_name(f"{self.manifest_path.name}.corrupt-{_now()}")
            try:
                self.manifest_path.rename(backup)
            except OSError:
                pass
            raise OSError(
                f"manifest 损坏: {self.manifest_path}（已备份到 {backup}）；拒绝覆盖，请先修复该文件") from None

    def _save(self, data: dict) -> None:
        """原子写 manifest：临时文件 + os.replace，读者只会见全旧或全新。异常时清理临时文件。"""
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_name(self.manifest_path.name + ".tmp-%d" % os.getpid())
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.manifest_path)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def mark(self, path: Path, kind: str, source: str = "manual", change: Optional[str] = None,
             prev_hash: Optional[str] = None, detail: Optional[str] = None) -> dict:
        rel = self._rel(path)
        rec = {"path": rel, "kind": kind, "source": source,
               "hash": sha256(path) if path.exists() else None}
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        chg = {"收敛": "收敛", "质变": "质变"}.get(change, "-")
        # P1-3（2026-09-21）：prev_hash = 覆盖写前上一版全文指纹。log.md 是
        # append-only，每条覆盖写一行 prev 列，按时间序即成单条记忆版本链；
        # manifest 只记最近一次 prev（历史链以 log 行为准）。无覆盖写为 None。
        pv = (prev_hash or "-")[:12]
        # V2-3 near-miss 扩展：detail 非 None 时作为第 7 个竖线字段追加行尾
        # （机读行格式 `| path | kind | source | hash | chg | prev | detail |`）；
        # 其余事件行保持既有 6 字段格式逐字节不变。调用方保证 detail 单行无竖线。
        tail = f" {detail} |\n" if detail is not None else "\n"
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(f"| {rel} | {kind} | {source} | {(rec['hash'] or '-')[:12]} | {chg} | {pv} |" + tail)
        m = self._load()
        prev = m.setdefault("files", {}).get(rel, {})
        m.setdefault("files", {})[rel] = {
            "kind": kind,
            "source": source,
            "hash": rec["hash"],
            # P1-3：prev_hash 传 None（skip/非覆盖类登记）时保留旧值——磁盘内容
            # 未变则版本关系未变；write 传入新值则前进。防止 skip 行抹掉版本链。
            "prev_hash": prev_hash if prev_hash is not None else prev.get("prev_hash"),
            "trace": prev.get("trace", []),
        }
        self._save(m)
        if self._git_add is not None:
            self._git_add(rel)
        return rec

    def trace(self, path: Path, from_state: str, to_state: str,
              reason: str, source: str = "governance") -> dict:
        """登记一次状态转移轨迹（append-only）：from → to + 自动 reason。

        P1 自动化审计：转移轨迹由系统在转移时自动记录，人工无需填"为什么"。
        写进 manifest 的该文件 trace 列表，与 mark 复用的持久化格式兼容。
        """
        rel = self._rel(path)
        rec = {"at": _now_iso(), "from": from_state, "to": to_state,
               "reason": reason, "hash": sha256(path) if path.exists() else None}
        m = self._load()
        m.setdefault("files", {}).setdefault(rel, {})
        m["files"][rel]["trace"] = m["files"][rel].get("trace", []) + [rec]
        self._save(m)
        return rec

    def lifecycle(self, rel: str) -> list[dict]:
        """还原一条记忆的完整状态转移轨迹（按发生顺序）。无记录返回空表。

        支撑"5 分钟内从审计轨迹还原任意一条记忆完整生命周期"的验收信号。
        """
        m = self._load()
        return list(m.get("files", {}).get(rel, {}).get("trace", []))


class CompositeAudit(AuditBackend):
    """组合后端：一次 mark/trace 双登记（primary 为主、secondary 为跨栈补登）。

    B2（2026-09-21 整合审查 F2）：panel 晋升改写 03-记忆 只登记 prod/audit.jsonl
    （本库 FileAudit），vault 内 05-审计/log.md 对该写入不可见。CompositeAudit 让
    一个后端同时登记两处——primary 保持既有语义（含 prev_hash 版本链），secondary
    做镜像补登（如运行台 audit_core 的 05-审计链）。约定：
    - mark 全参透传给两侧，返回 primary 的回执（调用方语义不变）；
    - secondary 异常向上冒泡不静默（审计完整性优先；业务写已由 primary 成功登记，
      补登失败必须显形让人工介入，而非假装登记过）；
    - trace 只要求 primary 支持（panel/governance 的 primary 实际都是 FileAudit）；
      secondary 无 trace 方法时跳过（ duck 型，05-审计平面口径无轨迹表时以
      log_event 事件行替代由 bridge 自行实现）；
    - 是否过滤登记范围（如只补登 _agent 内路径）由 secondary 自行决定，组合器不感知。
    """

    def __init__(self, primary: AuditBackend, secondary: AuditBackend):
        self.primary = primary
        self.secondary = secondary

    def mark(self, path: Path, kind: str, source: str = "manual", change: Optional[str] = None,
             prev_hash: Optional[str] = None, detail: Optional[str] = None) -> dict:
        # detail 只进 primary（V2-3 near-miss 摘要属主审计链）；secondary 可能是
        # 库外 duck 型后端（旧 mark 签名），不透传以免 TypeError。detail 为 None
        # 时两侧均按旧签名调用，既有行为逐字节不变。
        if detail is not None:
            rec = self.primary.mark(path, kind, source=source, change=change,
                                    prev_hash=prev_hash, detail=detail)
        else:
            rec = self.primary.mark(path, kind, source=source, change=change, prev_hash=prev_hash)
        self.secondary.mark(path, kind, source=source, change=change, prev_hash=prev_hash)
        return rec

    def trace(self, path: Path, from_state: str, to_state: str,
              reason: str, source: str = "governance") -> dict:
        rec = self.primary.trace(path, from_state, to_state, reason, source=source)
        sec_trace = getattr(self.secondary, "trace", None)
        if callable(sec_trace):
            sec_trace(path, from_state, to_state, reason, source=source)
        return rec
