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

# P0-写侧信任分层（方案 B 三档）：trusted=单写者蒸馏主链等高信任来源；
# untrusted=投稿等未信任来源；unknown=无标记存量（既有笔记无该字段，保守回落）。
TRUST_KNOWN = "trusted"
TRUST_UNKNOWN = "unknown"
TRUST_UNTRUSTED = "untrusted"
TRUST_LABELS = (TRUST_KNOWN, TRUST_UNKNOWN, TRUST_UNTRUSTED)
# P0 默认可信任来源：单写者蒸馏主链唯一默认高信任。单一来源，供 ingest/semantic/panel
# 缺省时共用（原三处各自硬编码 {"workbuddy"}，改这里即全局生效，符合「不写死来源」原则）。
DEFAULT_TRUSTED_AGENTS: frozenset[str] = frozenset({"workbuddy"})


def normalize_trust(raw: str | None, trusted: frozenset[str]) -> str:
    """把原始信任标记归一为三档；trusted 允许来源名单由调用方注入。

    规则：
    - 空/None → unknown（无标记的保守回落）；
    - 与三档之一精确匹配（大小写不敏感、去空白）→ 原档；
    - 其余任意值 → 出现在 trusted 名单视为 trusted，否则 untrusted。
    数据无关：不写死任何来源名单，判定名单由调用方（ingest 等）注入。
    """
    if not raw:
        return TRUST_UNKNOWN
    v = str(raw).strip().lower()
    if v in TRUST_LABELS:
        return v
    return TRUST_KNOWN if v in {str(t).strip().lower() for t in trusted} else TRUST_UNTRUSTED


# P1-封顶（2026-09-17）：三档有序化，供 min 语义封顶。trusted > unknown > untrusted。
TRUST_ORDER = {TRUST_KNOWN: 2, TRUST_UNKNOWN: 1, TRUST_UNTRUSTED: 0}


def cap_trust(declared: str, baseline: str) -> str:
    """信任封顶：取两档中较低者（min 语义），显式声明不得越过登记基线。

    P1 修复：投稿自带的 trust 字段视为又一种自声明证据，与 writer_contracts
    「信任 = min(基线, 观测置信)」对齐——静态基线是上限，写读两侧共用本原语。
    两入参须已是 normalize_trust 归一后的三档值；未知档位按 unknown 保守处理。
    """
    da = TRUST_ORDER.get(declared, TRUST_ORDER[TRUST_UNKNOWN])
    ba = TRUST_ORDER.get(baseline, TRUST_ORDER[TRUST_UNKNOWN])
    return declared if da <= ba else baseline


def now_iso() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


# --- 读侧契约：与写侧 _fmt_scalar/unquote 闭环（P0 frontmatter 解析收敛）---
# 值捕获用 [ \t]*（不含换行）+ 逐行锚定，杜绝跨行吞值
# （writer_contracts 历史 bug 语义为真源：`\s*` 含换行会贪婪吃掉空值行的换行）。
# (?m) 使 ^ 锚定行首，findall 才能收齐全部 frontmatter 块（writer_contracts 语义）。
_FM_RE = re.compile(r"(?m)^---\s*\n(.*?)\n---\s*\n?", re.S)
_FM_LINE_RE = re.compile(r"^([A-Za-z_][\w-]*):[ \t]*(.*)$")


def parse_frontmatter(text: str) -> dict[str, str]:
    """解析首个 frontmatter 块 → {key: 反转义值}。

    行为规格（对齐全部既有语义的并集）：
    - 无 frontmatter / 块不闭合 → 空 dict（不抛错）；
    - 容忍 BOM/前导空白（ingest 专属语义上收）；
    - 键名限字母/下划线/数字/连字符，正文行不误当键（inject 语义）；
    - 值经 unquote 剥引号+反转义 DQ 序列，单引号只剥不反转义（protocol 既有契约）；
    - 空值（`key:` 后无内容）→ 值 ""，绝不吞下一行（行锚定）；
    - 只返回实际存在的键，缺键 = key 不在 dict（None 语义由消费者 fm.get() 天然获得）。
    """
    text = text.lstrip("\ufeff \t\r\n")
    m = _FM_RE.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for ln in m.group(1).splitlines():
        mm = _FM_LINE_RE.match(ln)
        if mm:
            out[mm.group(1)] = unquote(mm.group(2).strip())
    return out


def parse_frontmatter_blocks(text: str) -> list[dict[str, str]]:
    """全部 frontmatter 块（writer_contracts._fm_blocks 的 findall 语义）。"""
    return [parse_frontmatter(f"---\n{b}\n---") for b in _FM_RE.findall(text)]


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


# --- 写侧契约：frontmatter 键行插入/替换（二期收敛，检验报告第六节"相邻债务"）---
# 与读侧 _FM_RE 刻意不同：不吞块尾空白（重写时原样保留 frontmatter 与正文
# 之间的空行）。governance._upsert_status 与 crossref._upsert_links 的逐字
# 同构实现收敛于此。
_FM_WRITE_RE = re.compile(r"^---\s*\n(.*?)\n---", re.S)


def upsert_fm_value(text: str, key: str, value: str,
                    insert_before: tuple[str, ...] = ()) -> str:
    """在 frontmatter 内插入/替换 `key` 行（值经 _fmt_scalar 编码为 DQ 标量）。

    行为规格（对齐两处既有语义的并集）：
    - 无 frontmatter / 块不闭合 → 原文不动；
    - 键行已存在 → 整行替换为新值行（(?m) 行锚定，保留其余行不动）；
    - 键行不存在 → 插入到首个 `insert_before` 键行之前（默认追加到块尾）；
    - 块外正文（含 frontmatter 与正文间的空行）逐字保留。

    governance._upsert_status 的锚点 = ("updated",)，crossref._upsert_links 的
    锚点 = ("status", "updated")，两调用点语义完全保留。
    """
    m = _FM_WRITE_RE.match(text)
    if not m:
        return text
    fm = m.group(1)
    line = f"{key}: {_fmt_scalar(value)}"
    if re.search(rf"(?m)^\s*{re.escape(key)}:", fm):
        # 替换串用 callable 返回：re.sub 对 str repl 会解释 \n/\t/\\ 等转义，
        # 破坏 _fmt_scalar 的 DQ 转义（旧实现同样潜伏，状态值无特殊字符未被
        # 暴露；test_frontmatter.py 13 往返闭环先暴露此缺陷）
        fm = re.sub(rf"(?m)^\s*{re.escape(key)}:.*$", lambda m: line, fm)
    else:
        parts = fm.splitlines()
        insert_at = next(
            (i for i, l in enumerate(parts)
             if any(re.match(rf"^\s*{re.escape(k)}:", l) for k in insert_before)),
            len(parts))
        parts.insert(insert_at, line)
        fm = "\n".join(parts)
    return f"---\n{fm}\n---" + text[m.end():]


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
