# -*- coding: utf-8 -*-
"""timegrap.py — 时间维度图谱（P2）：主题脉络的时间演化视图。

现有读侧（recall/semantic/crossref）都是"此刻一拍"的空间相似度；治理状态机
（governance）是"当前状态"的治理维度。缺的是跨笔记的主题时间演化：同一主题
的多条记忆（不同日期投稿）如何串联成一条"脉络"、能否按时间回放、识别断更/
复活/重复/结论漂移。

时间维度图谱的本质 = **主题脉络的时间演化模型**：
    subject_key(项目 token 优先) ——> 同一主题下所有笔记按时间聚簇
                                ——> 蕴含的演化信号（延续/断更/复活/重复/漂移）

主题键（P3-1，2026-09-11 摸底后修正）：**优先用「项目 token」这个精确键**，
不再用标题字符集合指纹做主键。实测依据（224 篇真实镜像）：
  - 标题字符集合指纹：224 簇 / **0 个多节点簇**（每篇自成一簇，图谱结构性空转）
  - 内容相似度（body 字符 bigram）：159 簇 / 13 多节点簇 / 最大簇 50 —— 最大簇是
    **假合并**（44 个不同项目的「项目约束」因共享模板结构与词汇被合并）
  - **项目 token：158 簇 / 66 个多节点簇 / 最大簇 2 / 零假合并**（精确键不可能假合并）
取值优先级：`project_id`（声明字段，权威）→ 文件名锚定模式 `(项目约束|项目会话)-{hex}`
→ 标题 `（{hex}）` → 以上皆无则回落原字符集合指纹（保住既有单测语义）。
**注意**：本镜像 frontmatter 的 `project_id` 覆盖率为 0/224，故当前实际生效的是
正则回落。这是**耦合生产者命名约定的临时补丁**——正确解是让两个写者
（`distill_memory._fm()` / `staging_ingest._as_note()`）输出 `project_id`。
标题/文件名格式一旦变动，token 提取会静默退化回「每篇一簇」且不报警。

信号合法性与时间 horizon（P3-1 实测）：
  - 时序信号（continued / revived / same_day）基于时间 gap，跨层异构对**合法**；
  - 内容关系信号（drift / duplicate）基于「同一条记录的两稿」，要求相邻对是**同层
    同类型记录**。跨层对（如长期「项目约束」vs 中期「项目会话」）实测 body 相似度
    仅 0.05-0.07，会产出全假 drift，故跨层对**只出时序信号**并标 relation_scope。
  - `revived` 需要脉络跨度 >= gap_days。本镜像跨度 15 天 < GAP_DAYS=90，
    **数学上不可达**（全库 24976 对笔记，能触发的 0 对）。这已如实标注在
    TopicTimeline.unreachable_signals，**不静默返回 0**。

与治理状态机（空间/治理维度）互为正交补位：状态机回答"这条记忆现在处于什么
治理态"，时间图谱回答"这个主题随时间怎么演化的、结论有没有漂移"。

零新增依赖（纯标准库 + 复用 recall/crossref 的字符相似度），只读不改文件：
- 时间基准：**updated 优先**，缺失时回落 created，再缺回落文件 mtime，并打 time_src 标记
  （mtime 被 git checkout / 同步改写时，time_src=mtime 可供审计追溯），绝不
  丢弃缺时间字段的笔记（覆盖完备 > 极端精确）。实测本镜像 224 篇均有 updated，
  故 created 与 mtime 两级回落当前命中数为 0（见 _node_for 内的封条注释）。
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


# --- 项目 token 提取（P3-1：主题键的精确来源）---
# 标题形态：`项目约束（6165c7）` / `项目会话（6165c7）`
_TOKEN_TITLE = __import__("re").compile(r"（([0-9a-f]{6,8})）")
# 文件名形态（锚定）：`项目约束-6165c7.md` / `项目会话-6165c7.md`
# 锚定是必要的：WorkBuddy 侧文件名 `项目会话-{中文标题}-{8hex}` 尾部的 8hex 是
# **内容哈希（条目身份）而非项目 token**，锚定模式天然把它排除，避免误当主题键。
_TOKEN_FILENAME = __import__("re").compile(r"^(?:项目约束|项目会话)-([0-9a-f]{6,8})$")


def project_topic_token(*, project_id: str | None = None, title: str = "",
                        path: str | None = None) -> str:
    """提取项目 token（本镜像的精确主题键）。无则返回空串。

    优先级：`project_id`（声明字段，权威）→ 文件名锚定模式 → 标题 `（hex）`。
    实测（2026-09-11，224 篇）：title 命中 170 篇、filename 命中 170 篇、
    `project_id` 命中 **0 篇**（字段已声明但生产者从不填）。
    """
    if project_id:
        v = str(project_id).strip()
        if v:
            return v
    if path:
        m = _TOKEN_FILENAME.match(Path(path).stem)
        if m:
            return m.group(1)
    if title:
        m = _TOKEN_TITLE.search(title)
        if m:
            return m.group(1)
    return ""


def subject_key(title: str, summary: str = "", *,
                path: str | None = None,
                project_id: str | None = None) -> str:
    """把一条记忆规整为稳定主题键（用于聚簇同一时间脉络）。

    **主路径（P3-1 起）：项目 token 精确键**，返回 `"topic:{token}"`。
    依据：token 是项目身份，不可能假合并；实测 224 篇得 66 个多节点簇、
    最大簇 2。而字符集合指纹得 0 个多节点簇、内容相似度得最大簇 50（假合并）。

    **回落路径**：无 token 时用原字符级规范化指纹（去空白/标点/停用字，
    保留 CJK+字母数字，排序合并）。这样保住既有语义：
    - 同题标题带标点/空白差异（"韩餐动线。" vs "韩餐动线"）仍归同键；
    - **摘要不参与主题键**（关键）：结论漂移的投稿其摘要变了，但它们必须
      仍落在同一时间脉络里，才能被"结论漂移"信号看见。
    空输入回落固定哨兵。
    """
    tok = project_topic_token(project_id=project_id, title=title, path=path)
    if tok:
        return "topic:" + tok
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
    tier: str = ""              # P3-1：所属层目录（01-长期记忆 / 02-中期记忆）
    topic_token: str = ""       # P3-1：主题键来源 token（空=走了指纹回落）


@dataclass
class TopicTimeline:
    """一个主题的时间脉络：所有投稿按时间升序 + 蕴含的演化信号。

    P3-1 新增两个如实标注字段：
    - `horizon_days`：本批扫描的时间跨度（天）。本镜像实测 15 天。
    - `unreachable_signals`：因数据不足**数学上不可能触发**的信号名 + 原因。
      避免「信号恒为 0」被误读为算法失效（实测 revived 恒为 0 的原因是
      跨度 15 天 < GAP_DAYS=90，不是 bug）。
    """
    subject: str
    nodes: list[TimelineNode] = field(default_factory=list)
    signals: list[dict] = field(default_factory=list)
    horizon_days: int = 0
    unreachable_signals: list[str] = field(default_factory=list)

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
        """全库聚簇：返回 {subject_key: TopicTimeline}。

        P3-1：主题键改走项目 token（见 subject_key）；批次扫描后统一计算时间
        horizon，并把因数据不足**数学上不可达**的信号如实标注到每条脉络上。
        """
        from .recall import scan_tier_dirs
        regroup: dict[str, list[TimelineNode]] = {}
        for tier in self.tiers:
            for d in scan_tier_dirs(self.mem_root, tier, self.projects):
                for md in d.glob("*.md"):
                    if not md.is_file():
                        continue
                    node = self._node_for(md, tier)
                    if node is None:
                        continue
                    sk = subject_key(node.title, node.summary, path=node.path,
                                     project_id=node.project_id)
                    regroup.setdefault(sk, []).append(node)
        # 时间 horizon：本批扫描的跨度，决定哪些 gap 类信号在数学上可达
        all_when = [n.when for nodes in regroup.values() for n in nodes if n.when]
        horizon = max(0, (max(all_when) - min(all_when)).days) if len(all_when) >= 2 else 0
        unreachable: list[str] = []
        if horizon < self.gap_days:
            unreachable.append(
                f"revived（数据不足：本批跨度 {horizon} 天 < gap_days {self.gap_days} 天，"
                f"数学上不可能触发；需镜像自然积累历史，非算法缺陷）"
            )
        out: dict[str, TopicTimeline] = {}
        for sk, nodes in regroup.items():
            tl = TopicTimeline(subject=sk, nodes=nodes,
                               horizon_days=horizon,
                               unreachable_signals=list(unreachable))
            self._derive_signals(tl)
            out[sk] = tl
        return out

    def _node_for(self, md: Path, tier: str = "") -> TimelineNode | None:
        fm = _fm_snippet(md)
        title = _title_of(md)
        summary = _summary_of(fm)
        when: datetime | None
        src: str
        # 时间基准：updated / created / mtime 回退 + 标记
        # 🔴 封条（2026-09-11 实证裁决）：下面的 created 分支是「缺 updated 的老笔记」的
        # 健壮性回落，**不是 created 字段的功能位**。实测本镜像 224 篇全部有 updated，
        # 故 time_src 分布 = {'updated': 224}，created 分支命中 0 次。
        # 不要改成 created 优先：断更/复活/漂移三类信号需要的是「版本写入时间序列」，
        # 那是 updated 的语义；created 是条目首次落盘时刻，换过去会让时间轴失去版本含义。
        # created 目前为备用字段，详见输出/mempipeline-P3论证/P3-ADR-001-created时间基准字段决策.md
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
        pid = _project_of(fm)
        return TimelineNode(
            path=str(md), title=title, summary=summary, when=when, time_src=src,
            status=_status_of(fm), importance=_importance_of(fm),
            project_id=pid, tier=tier,
            topic_token=project_topic_token(project_id=pid, title=title, path=str(md)),
        )

    def _derive_signals(self, tl: TopicTimeline) -> None:
        """推导脉络演化信号（只读，不改文件）：延续/断更/复活/重复/结论漂移。

        P3-1 层感知：分两类信号，适用前提不同——
        - **时序信号**（revived / continued / same_day）：只依赖时间 gap，跨层合法；
        - **内容关系信号**（drift / duplicate / stable）：依赖「同一条记录的两稿」，
          要求相邻对是**同层同类型记录**。跨层对（长期约束 vs 中期快照）实测
          body 相似度仅 0.05-0.07，比较必然产出全假 drift，故跨层对不出 relation，
          改标 relation_scope="cross-tier" 说明原因。
        """
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
                rec["gap_days"] = gap
                cross_tier = bool(prev.tier and n.tier and prev.tier != n.tier)
                if cross_tier:
                    # 跨层异构对：时序信号照出，内容关系信号不适用（不输出）
                    rec["relation_scope"] = "cross-tier"
                    rec["relation_scope_note"] = (
                        f"跨层异构对（{prev.tier} → {n.tier}）：drift/duplicate 的语义是"
                        "「同一记录在两稿间变化」，跨层对是同一项目的两类异构记录，"
                        "语义不成立故不输出"
                    )
                else:
                    sim = summary_similarity(n.summary, prev.summary)
                    if sim >= self.dup_threshold:
                        rec["relation"] = "duplicate"
                    elif sim < self.drift_threshold:
                        rec["relation"] = "drift"    # 结论漂移
                    else:
                        rec["relation"] = "stable"
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