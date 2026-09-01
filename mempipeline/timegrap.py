# -*- coding: utf-8 -*-
"""timegrap.py — 时间维度图谱（P2）：主题脉络的时间演化视图。

现有读侧（recall/semantic/crossref）都是"此刻一拍"的空间相似度；治理状态机
（governance）是"当前状态"的治理维度。缺的是跨笔记的主题时间演化：同一主题
的多条记忆（不同日期投稿）如何串联成一条"脉络"、能否按时间回放、识别断更/
复活/重复/结论漂移。

时间维度图谱的本质 = **主题脉络的时间演化模型**：
    subject_key(标题+摘要 规整) ——> 同一主题下所有笔记按时间聚簇
                              ——> 蕴含的演化信号（延续/断更/复活/重复/漂移）

与治理状态机（空间/治理维度）互为正交补位：状态机回答"这条记忆现在处于什么
治理态"，时间图谱回答"这个主题随时间怎么演化的、结论有没有漂移"。

零新增依赖（纯标准库 + 复用 recall/crossref 的字符相似度），只读不改文件：
- 时间基准：updated / created 优先，均缺失回落文件 mtime，并打 time_src 标记
  （mtime 被 git checkout / 同步改写时，time_src=mtime 可供审计追溯），绝不
  丢弃缺时间字段的笔记（覆盖完备 > 极端精确）。
- 不改既有文件格式；扫描只读管道，与治理纪律一致。

联网核对（一手证据支持取舍）：文件系统 mtime/ctime 在文件迁移/同步时不可靠
（Obsidian 官方论坛一手反馈），所以 mtime 仅作回退且标注 source；漂移/相似度
阈值做成显式可配置常量（非散埋 magic number），初值取保守值。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

# --- 时间维度图谱配置（显式可配置，非散埋 magic number）---
GAP_DAYS = 90            # 断更阈值：距上一稿超过 N 天视为断更
DRIFT_THRESHOLD = 0.15   # 结论漂移阈值：相邻摘要 bigram Jaccard 低于该值视为漂移
DUP_THRESHOLD = 0.92     # 重复阈值：相邻摘要 bigram Jaccard 高于该值视为重复


def _read_fm(fm: str, key: str) -> str | None:
    """从 frontmatter 读某字段标量值（剥外层引号），缺省 None。"""
    import re
    mm = re.search(r"(?m)^\s*" + key + r":\s*(.+?)\s*$", fm)
    if not mm:
        return None
    return mm.group(1).strip().strip(chr(34) + chr(39))


_FM_RE = __import__("re").compile(r"^---\s*\n(.*?)\n---", __import__("re").S)


def _title_of(md: Path) -> str:
    """从 frontmatter 取 title，缺省回落文件名。"""
    import re
    try:
        txt = md.read_text(encoding="utf-8")
    except Exception:
        return md.stem
    m = _FM_RE.match(txt)
    if m:
        fm = m.group(1)
        mm = re.search(r"(?m)^\s*title:\s*(.+)$", fm)
        if mm:
            v = mm.group(1).strip().strip(chr(34) + chr(39))
            if v:
                return v
    return md.stem


def subject_key(title: str, summary: str = "") -> str:
    """把标题规整为稳定主题键（用于聚簇同一时间脉络）。

    只取 title、做字符级规范化：去空白/标点/停用字，保留 CJK+字母数字，
    排序合并成规范化指纹。这样：
    - 同一主题不同投稿标题即使带标点/空白差异（"韩餐动线。" vs "韩餐动线"）
      也归同键；
    - **摘要不参与主题键**（关键）：结论漂移的投稿其摘要变了，但它们必须
      仍落在同一时间脉络里，才能被"结论漂移"信号看见。
    空输入回落固定哨兵。
    """
    from .recall import _STOPCHARS
    keep: list[str] = []
    for c in title or "":
        if c in _STOPCHARS or c.isspace():
            continue
        if c.isalnum():
            keep.append(c.lower())
    if not keep:
        return "__empty__"
    return "|".join(sorted(set(keep)))


def _norm(s: str) -> str:
    return "".join(s.split())


def _bigrams(s: str) -> set[str]:
    n = _norm(s)
    return {n[i:i + 2] for i in range(len(n) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def summary_similarity(a: str, b: str) -> float:
    """两条摘要的字符 bigram Jaccard 相似度（中文无需分词即稳健）。"""
    return _jaccard(_bigrams(a), _bigrams(b))


@dataclass
class TimelineNode:
    """一条记忆投稿在时间脉络上的节点（只读视图）。"""
    path: str
    title: str
    summary: str
    when: datetime | None       # 投稿时间（updated/created 优先，回落 mtime）
    time_src: str               # updated | created | mtime
    status: str = "active"
    importance: float = 0.6
    project_id: str | None = None


@dataclass
class TopicTimeline:
    """一个主题的时间脉络：所有投稿按时间升序 + 蕴含的演化信号。"""
    subject: str
    nodes: list[TimelineNode] = field(default_factory=list)
    signals: list[dict] = field(default_factory=list)

    def recall(self) -> list[str]:
        """按时间升序回放该主题的完整脉络（返回各投稿路径）。"""
        return [n.path for n in sorted(self.nodes, key=lambda n: (n.when or datetime.min))]

    def spans_days(self) -> int:
        """脉络跨越的天数（首末投稿差）；单节点返回 0。"""
        times = sorted(n.when for n in self.nodes if n.when)
        if len(times) < 2:
            return 0
        return max(0, (times[-1] - times[0]).days)


# --- 时间解析 ---
_TIME_FMTS = ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


def _parse_time(s: str) -> datetime | None:
    for fmt in _TIME_FMTS:
        try:
            d = datetime.strptime(s, fmt)
            if d.tzinfo:
                d = d.replace(tzinfo=None)
            return d
        except ValueError:
            continue
    return None


def _mtime_of(md: Path) -> datetime:
    try:
        st = md.stat()
        from datetime import datetime as _dt
        return _dt.fromtimestamp(st.st_mtime)
    except Exception:
        return datetime.min


def _fm_snippet(md: Path) -> str:
    try:
        txt = md.read_text(encoding="utf-8")
    except Exception:
        return ""
    m = _FM_RE.match(txt)
    if m:
        return m.group(1)
    return ""


def _summary_of(fm: str) -> str:
    import re
    m = re.search(r"(?m)^\s*summary:\s*(.+)$", fm)
    if not m:
        return ""
    return m.group(1).strip().strip(chr(34) + chr(39))


def _importance_of(fm: str) -> float:
    import re
    m = re.search(r"(?m)^\s*importance:\s*(.+)$", fm)
    if not m:
        return 0.6
    try:
        return float(m.group(1).strip().strip(chr(34) + chr(39)))
    except ValueError:
        return 0.6


def _status_of(fm: str) -> str:
    import re
    m = re.search(r"(?m)^\s*status:\s*(.+)$", fm)
    if not m:
        return "active"
    return m.group(1).strip().strip(chr(34) + chr(39))


def _project_of(fm: str) -> str | None:
    import re
    m = re.search(r"(?m)^\s*project_id:\s*(.+)$", fm)
    if not m:
        return None
    v = m.group(1).strip().strip(chr(34) + chr(39))
    return v or None


class TimelineGraph:
    """只读扫描器：把全库笔记按主题键聚簇成时间脉络。

    纯只读，不改任何文件。数据无关：mem_root / tiers / projects 由调用方注入。
    """

    def __init__(self, mem_root: Path, tiers: Iterable[str] | None = None,
                 projects: Iterable[str] | None = None,
                 gap_days: int = GAP_DAYS,
                 drift_threshold: float = DRIFT_THRESHOLD,
                 dup_threshold: float = DUP_THRESHOLD):
        self.mem_root = mem_root
        if tiers is None:
            from .protocol import TIER_DIR
            tiers = TIER_DIR.values()
        self.tiers = list(tiers)
        self.projects = list(projects) if projects is not None else None
        self.gap_days = gap_days
        self.drift_threshold = drift_threshold
        self.dup_threshold = dup_threshold

    def scan(self) -> dict[str, TopicTimeline]:
        """全库聚簇：返回 {subject_key: TopicTimeline}。"""
        from .recall import scan_tier_dirs
        regroup: dict[str, list[TimelineNode]] = {}
        for tier in self.tiers:
            for d in scan_tier_dirs(self.mem_root, tier, self.projects):
                for md in d.glob("*.md"):
                    if not md.is_file():
                        continue
                    node = self._node_for(md)
                    if node is None:
                        continue
                    sk = subject_key(node.title, node.summary)
                    regroup.setdefault(sk, []).append(node)
        out: dict[str, TopicTimeline] = {}
        for sk, nodes in regroup.items():
            tl = TopicTimeline(subject=sk, nodes=nodes)
            self._derive_signals(tl)
            out[sk] = tl
        return out

    def _node_for(self, md: Path) -> TimelineNode | None:
        fm = _fm_snippet(md)
        title = _title_of(md)
        summary = _summary_of(fm)
        when: datetime | None
        src: str
        # 时间基准：updated / created / mtime 回退 + 标记
        updated = _read_fm(fm, "updated")
        created = _read_fm(fm, "created")
        if updated:
            when = _parse_time(updated)
            src = "updated"
        elif created:
            when = _parse_time(created)
            src = "created"
        else:
            when = _mtime_of(md)
            src = "mtime"
        return TimelineNode(
            path=str(md), title=title, summary=summary, when=when, time_src=src,
            status=_status_of(fm), importance=_importance_of(fm),
            project_id=_project_of(fm),
        )

    def _derive_signals(self, tl: TopicTimeline) -> None:
        """推导脉络演化信号（只读，不改文件）：延续/断更/复活/重复/结论漂移。"""
        nodes = sorted(tl.nodes, key=lambda n: (n.when or datetime.min))
        tl.nodes = nodes
        tl.signals = []
        for i, n in enumerate(nodes):
            rec: dict = {"idx": i, "path": n.path, "time_src": n.time_src}
            if n.when is None:
                rec["flag"] = "untimed"          # 无时间戳（极小概率）
                tl.signals.append(rec)
                continue
            prev = nodes[i - 1] if i > 0 else None
            if prev is not None and prev.when is not None:
                gap = max(0, (n.when - prev.when).days)
                if gap >= self.gap_days:
                    rec["flag"] = "revived"      # 断更后再次投稿 → 复活
                elif gap > 0:
                    rec["flag"] = "continued"    # 时间上连续 → 延续
                else:
                    rec["flag"] = "same_day"     # 同天多次投稿
                sim = summary_similarity(n.summary, prev.summary)
                if sim >= self.dup_threshold:
                    rec["relation"] = "duplicate"
                elif sim < self.drift_threshold:
                    rec["relation"] = "drift"    # 结论漂移
                else:
                    rec["relation"] = "stable"
                rec["gap_days"] = gap
                rec["sim_prev"] = round(sim, 3)
                rec["drift_threshold"] = self.drift_threshold
            else:
                rec["flag"] = "first"            # 首稿
            tl.signals.append(rec)


def build_timeline(mem_root: Path, *, tiers: Iterable[str] | None = None,
                   projects: Iterable[str] | None = None,
                   gap_days: int = GAP_DAYS,
                   drift_threshold: float = DRIFT_THRESHOLD,
                   dup_threshold: float = DUP_THRESHOLD) -> dict[str, TopicTimeline]:
    """一站式入口：扫描全库返回 {subject: TopicTimeline}。"""
    g = TimelineGraph(mem_root, tiers=tiers, projects=projects,
                      gap_days=gap_days, drift_threshold=drift_threshold,
                      dup_threshold=dup_threshold)
    return g.scan()