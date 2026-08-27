# -*- coding: utf-8 -*-
"""protocol.py — 记忆管线前端协议：frontmatter 字段、层映射、幂等判据、命名规范。

本模块数据无关：只描述一条记忆长什么样、落哪层、如何判重、如何命名，
不含任何具体数据源/目标库路径。是可复用契约本体。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

TIER_DIR = {"long": "01-长期记忆", "medium": "02-中期记忆"}
DEFAULT_TIER = "medium"
IMPORTANCE = {"long": 0.9, "medium": 0.6}
STALE_DAYS = 30


def now_iso() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Note:
    title: str
    summary: str
    tier: str
    importance: float
    source_agent: str = "writer"
    status: str = "active"
    body: str = ""
    updated: Optional[str] = None
    extra: dict = field(default_factory=dict)

    @property
    def tier_dir(self) -> str:
        return TIER_DIR.get(self.tier, TIER_DIR[DEFAULT_TIER])

    def to_frontmatter(self) -> str:
        import json
        lines = [
            "---",
            "type: note",
            f"title: {self.title}",
            f"summary: {self.summary}",
            f"memory_tier: {self.tier}",
            f"importance: {self.importance}",
            f"source_agent: {self.source_agent}",
            f"status: {self.status}",
        ]
        for k, v in self.extra.items():
            lines.append(f"{k}: {v}" if isinstance(v, str) else f"{k}: {json.dumps(v, ensure_ascii=False)}")
        lines += [f"updated: {self.updated or now_iso()}", "---"]
        return "\n".join(lines)


def stable_body(text: str) -> str:
    """剔除 frontmatter 实时时间戳行(updated:)，返回幂等判定的稳定正文。"""
    return "\n".join(ln for ln in text.splitlines() if not ln.startswith("updated:"))


def content_key(text: str) -> str:
    import hashlib
    return hashlib.sha256(stable_body(text).encode("utf-8")).hexdigest()[:8]


def title_token(title: str) -> str:
    import re
    token = re.sub(r"[^\w\u4e00-\u9fa5]+", "", title).strip()
    return token or now_iso().replace(" ", "").replace(":", "").replace("-", "")
