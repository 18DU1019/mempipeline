# -*- coding: utf-8 -*-
"""protocol.py — 记忆管线前端协议：frontmatter 字段、层映射、幂等判据、命名规范。

本模块数据无关：只描述一条记忆长什么样、落哪层、如何判重、如何命名，
不含任何具体数据源/目标库路径。是可复用契约本体。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

TIER_DIR = {"long": "01-长期记忆", "medium": "02-中期记忆"}
DEFAULT_TIER = "medium"


def now_iso() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _fmt_scalar(v: str) -> str:
    """把字符串值编码为 YAML 双引号标量（无条件引号）。

    强制双引号同时消除两类失真：plain 解析对 `#`/`:` 等指示符的歧义，
    以及 YAML core schema 把纯数字/布尔字符串解析成 int/bool 的类型失真。
    """
    esc = (
        v.replace("\\", "\\\\")
         .replace('"', '\\"')
         .replace("\n", "\\n")
         .replace("\r", "\\r")
         .replace("\t", "\\t")
    )
    return '"' + esc + '"'


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
        body = {
            "type": "note",
            "title": self.title,
            "summary": self.summary,
            "memory_tier": self.tier,
            "importance": self.importance,
            "source_agent": self.source_agent,
            "status": self.status,
        }
        lines = ["---"]
        for k, v in body.items():
            lines.append(f"{k}: {_fmt_scalar(v) if isinstance(v, str) else v}")
        for k, v in self.extra.items():
            val = _fmt_scalar(v) if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            lines.append(f"{k}: {val}")
        lines += [f"updated: {_fmt_scalar(self.updated or now_iso())}", "---"]
        return "\n".join(lines)


def stable_body(text: str) -> str:
    """剔除 frontmatter 实时时间戳行（含 updated: 变体），返回幂等判定的稳定正文。"""
    return "\n".join(ln for ln in text.splitlines()
                     if not re.match(r"^\s*updated\s*:", ln))


def strip_frontmatter(text: str) -> str:
    """剥离首个 frontmatter 块，返回其后的正文；无 frontmatter 则返回原文。"""
    m = re.match(r"^---\s*\n.*?\n---\s*\n?", text, re.S)
    if not m:
        return text
    return text[m.end():]


def content_key(text: str) -> str:
    return hashlib.sha256(stable_body(text).encode("utf-8")).hexdigest()[:8]


def title_token(title: str) -> str:
    token = re.sub(r"[^\w\u4e00-\u9fa5]+", "", title).strip()
    return token or now_iso().replace(" ", "").replace(":", "").replace("-", "")
