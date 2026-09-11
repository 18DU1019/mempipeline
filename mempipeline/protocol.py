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


_DQ_ESC = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}


def unquote(v: str) -> str:
    """剥 YAML 标量外层引号（与 _fmt_scalar 写侧契约闭环）。

    双引号串按 DQ 转义反转义（`\\n`→换行等，单趟正则避免多轮 replace 顺序坑，
    未知转义序列保留原样不丢信息）；单引号只剥引号不反转义（YAML 单引号
    转义规则不同，值内本不该出现转义序列）。仅匹配外层成对引号，plain 标量原样。
    """
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        quote = v[0]
        v = v[1:-1]
        if quote == '"':
            v = re.sub(r"\\(.)", lambda m: _DQ_ESC.get(m.group(1), m.group(0)), v)
    return v


@dataclass
class Note:
    title: str
    summary: str
    tier: str
    importance: float
    source_agent: str = "writer"
    status: str = "active"
    body: str = ""
    created: Optional[str] = None         # P3-0：条目首次落盘时刻。🔴 当前无生产消费方（timegrap 取时间基准时 updated 优先，实测 224/224 走 updated），保留为备用基准，勿据此判断笔记年龄
    updated: Optional[str] = None
    project_id: Optional[str] = None      # E1：项目隔离维度（None=legacy/全局）
    domain: Optional[str] = None          # E1：project | global（None=legacy 推断）
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
        if self.project_id:
            body["project_id"] = self.project_id
        if self.domain:
            body["domain"] = self.domain
        lines = ["---"]
        for k, v in body.items():
            lines.append(f"{k}: {_fmt_scalar(v) if isinstance(v, str) else v}")
        for k, v in self.extra.items():
            val = _fmt_scalar(v) if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            lines.append(f"{k}: {val}")
        lines += [
            f"created: {_fmt_scalar(self.created or now_iso())}",
            f"updated: {_fmt_scalar(self.updated or now_iso())}",
            "---",
        ]
        return "\n".join(lines)


def stable_body(text: str) -> str:
    """剔除「首个 frontmatter 块」内的时间戳行（updated: 与 created:）。

    两个字段都不参与 content_key，理由分别是：
    - updated 是实时字段，每次重写都会变，参入会让幂等失效；
    - created 是后加字段，存量笔记没有它（2026-09-11 实测 0/225）。若参入 key，
      存量笔记首次带 created 重写时 key 会变化，被判为新笔记而重复写入。
      剔除后新旧口径一致，P3-0 的字段补齐不会触发存量重复。
    只处理首个 --- 块，绝不触碰正文中以同名字段开头的普通段落。无 frontmatter 则原样返回。
    """
    m = re.match(r"^---\s*\n.*?\n---\s*\n?", text, re.S)
    if not m:
        return text
    fm = m.group(0)
    stripped = "\n".join(
        ln for ln in fm.splitlines()
        if not re.match(r"^\s*(?:updated|created)\s*:", ln)
    )
    return stripped + text[m.end():]


def strip_frontmatter(text: str) -> str:
    """剥离首个 frontmatter 块，返回其后的正文；无 frontmatter 则返回原文。"""
    m = re.match(r"^---\s*\n.*?\n---\s*\n?", text, re.S)
    if not m:
        return text
    return text[m.end():]


def content_key(text: str) -> str:
    return hashlib.sha256(stable_body(text).encode("utf-8")).hexdigest()[:12]


_SAFE_PROJECT_RE = re.compile(r"^[\w.-]+$")


def safe_project_id(pid) -> str | None:
    """安全化 project_id：只放行安全字符集，拒绝路径穿越/绝对路径。

    用于把用户可控 project_id 拼接到写盘路径前的筛。非法值返回 None，
    由调用方回落（legacy/global），而不是拒绝整条信息——保持幂等可用。
    """
    if not pid:
        return None
    if not isinstance(pid, str):
        pid = str(pid)
    if "\\" in pid or "/" in pid or ".." in pid:
        return None
    if not _SAFE_PROJECT_RE.fullmatch(pid):
        return None
    return pid


def title_token(title: str) -> str:
    token = re.sub(r"[^\w\u4e00-\u9fa5]+", "", title).strip()
    return token or now_iso().replace(" ", "").replace(":", "").replace("-", "")
