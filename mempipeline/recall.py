# -*- coding: utf-8 -*-
"""recall.py — 读取后端抽象 + ngram TF-IDF 召回（含同义映射扩展位）。

开源解释：RecallBackend 是所有读取入口的接口。MemoryRecall 用「查询 ngram
TF-IDF」做相关度打分：把 query 与候选笔记都切成 2-4 字字符 ngram（中文无分词
稳健），IDF 抑制全库通用词以提升区分度；synonyms 把同义词归一为主词后再匹配。
打分核心只服务 recall；交叉引用（crossref）用独立 bigram Jaccard，两处为不同
相似度策略，不共用核心。层目录默认取自 protocol.TIER_DIR。
"""
from __future__ import annotations

import hashlib
import math
import re
import sqlite3
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Iterable

from .protocol import parse_frontmatter


class RecallBackend(ABC):
    @abstractmethod
    def recall(self, query: str, k: int = 8) -> list[tuple[str, float]]:
        # return 首元素 = 候选笔记的绝对文件路径（非标题），次元素 = 相似度分，降序。
        ...


_STOPCHARS = frozenset("的了在是我你他她它与和及或对为从到个着都很也就而并且这那有其该被把让向于以不会能要已等还又再")


def is_redacted(text: str) -> bool:
    """判读一条笔记 frontmatter `status` 是否为 redacted（P5：统一走 protocol 解析器）。

    红act 的软终态标记；命中即从「活跃可检索」面剔除。无 frontmatter /
    status 非 redacted 一律返回 False（不误伤存量笔记）。与旧实现的差异：
    旧实现只 strip 引号不反转义；新实现经 unquote，`"redacted"` 与 `redacted`
    两形态解析一致（行为快照测试 8 覆盖）。
    """
    return parse_frontmatter(text).get("status") == "redacted"


def _tokens(text: str) -> list[str]:
    """字符 ngram（2-4 字），剔除全由停用字构成的窗口。零分词依赖。

    CJK/字母数字连续片内滑窗；空格分隔的独立 run 天然不跨词，避免跨词噪声
    ngram 借 IDF 放大。
    """
    out: list[str] = []
    for run in re.findall(r"[\w\u4e00-\u9fa5]+", text):
        n = len(run)
        for w in (2, 3, 4):
            if n < w:
                continue
            for i in range(n - w + 1):
                gram = run[i:i + w]
                if any(c not in _STOPCHARS for c in gram):
                    out.append(gram)
    return out


def build_idf(candidates: list[str]) -> dict[str, float]:
    """候选集现算 IDF（df 按出现文档数）。通用词 df 高 -> idf 被压小。"""
    n = max(1, len(candidates))
    df: dict[str, int] = {}
    for text in candidates:
        for g in set(_tokens(text)):
            df[g] = df.get(g, 0) + 1
    return {g: math.log((n + 1) / (c + 1)) + 1.0 for g, c in df.items()}


def _score_tfidf(qfreq: Counter[str], qmax: float, idf: dict[str, float],
                 doc_tf: Counter[str], doc_len: int) -> float:
    """TF-IDF 重叠打分：query 侧归一 × doc 侧显著性 × IDF；缺失词不贡献。"""
    if not doc_len:
        return 0.0
    s = 0.0
    for g, qc in qfreq.items():
        d = doc_tf.get(g)
        if not d:
            continue
        s += idf.get(g, 0.0) * (qc / qmax) * (d / doc_len)
    return s


def _raw_terms(s: str) -> list[str]:
    return [w for w in re.findall(r"[\w\u4e00-\u9fa5]+", s)]


# AGI-②（2026-09-18）：默认同义表。head=语料高频形式（317 篇定向 df 实测），
# alts=查询侧词汇（多为 df=0 的纯查询词或跨语言对）。取舍依据与排除组
# （快照↔备份 / IRR↔年化 / ETF↔指数基金等语义冲突不合并）见 test_default_synonyms。
DEFAULT_SYNONYMS: dict[str, list[str]] = {
    "commit": ["提交"],
    "frontmatter": ["metadata"],
    "audit": ["审计"],
    "sandbox": ["沙箱"],
    "闭环": ["收尾", "收口"],
    "记忆入库": ["沉淀"],
    "回撤": ["回吐"],
    "估值": ["低估值", "低估"],
}


def syn_norm_map(synonyms: dict[str, list[str]] | None) -> dict[str, str]:
    """head/alts 展平为 term->主词映射；None/空返回空 dict（无归一语义）。"""
    m: dict[str, str] = {}
    for head, alts in (synonyms or {}).items():
        m[head] = head
        for a in alts:
            m[a] = head
    return m


def map_terms(query: str, norm: dict[str, str]) -> str:
    """查询侧整词归一：_raw_terms 切 run 后查映射，以空格重连。

    整词语义：中文连写（如「回吐了多少」）不触发归一，与 _norm_doc 口径一致。
    空映射原样返回。
    """
    if not norm:
        return query
    return " ".join(norm.get(t, t) for t in _raw_terms(query))


def scan_tier_dirs(mem_root: Path, tier: str,
                   projects: Iterable[str] | None = None) -> list[Path]:
    """返回某 tier 下实际要扫描的目录列表（项目作用域解析）。

    projects=None → 全库：顶层 legacy 目录 + projects/ 下全部项目的同 tier 目录
    （E1 二维化后两种布局兼容）。projects 给定 → 仅对应项目目录。
    仅返回存在的目录；数据无关，不写死任何路径。
    """
    if projects is not None:
        return [mem_root / "projects" / p / tier for p in projects
                if (mem_root / "projects" / p / tier).is_dir()]
    dirs = [mem_root / tier] if (mem_root / tier).is_dir() else []
    proj_root = mem_root / "projects"
    if proj_root.is_dir():
        for p in sorted(proj_root.iterdir()):
            if p.is_dir() and (p / tier).is_dir():
                dirs.append(p / tier)
    return dirs


class MemoryRecall(RecallBackend):
    """查询 ngram TF-IDF 打分；synonyms 把同义词归一为主词后再匹配。"""

    def __init__(self, mem_root: Path, tiers: Iterable[str] | None = None,
                 synonyms: dict[str, list[str]] | None = None,
                 projects: Iterable[str] | None = None,
                 index: "TFIDFIndex | None" = None):
        self.mem_root = mem_root
        self.read_errors: int = 0  # G 项（2026-09-17）：读失败累计——静默缺席候选的条目在此显形
        if tiers is None:
            from .protocol import TIER_DIR
            tiers = TIER_DIR.values()
        self.tiers = list(tiers)
        self.projects = list(projects) if projects is not None else None
        self.index = index  # 可选倒排快路径；缺省 None = 每查询全库读盘
        self._norm = syn_norm_map(synonyms)

    def _query_terms(self, query: str) -> list[str]:
        return [self._norm.get(t, t) for t in _raw_terms(query)]

    def _norm_doc(self, text: str) -> str:
        """文档侧同义归一：对原始文本按空白切 term、映射主词后以空格重连。

        接收含换行/空白的原文（非去空白拼接），英文同义词凭空格独立成 term
        才能被 _norm 命中；如 "positioning 管理" -> "仓位 管理"，使查询 "仓位"
        能命中。IDF 与打分都在统一主词空间计算，查询/文档两侧对等。
        """
        return " ".join(self._norm.get(t, t) for t in _raw_terms(text))

    def recall(self, query: str, k: int = 8) -> list[tuple[str, float]]:
        # return 首元素 = 候选笔记的绝对文件路径（非标题），次元素 = 相似度分，降序。
        # 快路径：配置了倒排索引且口径对齐——无归一（索引按原词建），或索引与
        # 本实例用同一张同义表双侧归一（TFIDFIndex.build 落盘归一文本 + recall
        # 查询侧归一，与 MemoryRecall 全扫同构，主词空间对等）。异表索引回落全扫。
        if self.index is not None and (
                not self._norm or getattr(self.index, "_norm", None) == self._norm):
            try:
                hits = self.index.recall(query, k=k)
                if hits:
                    return hits
            except Exception:
                pass  # 索引异常回落全扫
        qterms = self._query_terms(query)
        if not qterms:
            return []
        qgrams = _tokens(" ".join(qterms))
        if not qgrams:
            return []
        # 候选集现算 IDF（两遍：扫 docs 建 idf -> 逐 doc 打分）。docs 存归一化文本，
        # 保证 idf 的 df 与打分都在统一主词空间，查询/文档两侧完全对等。
        docs: list[tuple[str, str]] = []
        for tier in self.tiers:
            for d in scan_tier_dirs(self.mem_root, tier, self.projects):
                for md in d.glob("*.md"):
                    try:
                        txt = md.read_text(encoding="utf-8")
                    except Exception:
                        self.read_errors += 1  # G 项：计数不改变行为（缺席语义不变）
                        continue
                    if is_redacted(txt):
                        continue  # P3.12 红act：不进候选、不打分、不进 Top-K
                    docs.append((str(md), self._norm_doc(txt)))
        idf = build_idf([t for _, t in docs])
        qfreq = Counter(qgrams)
        qmax = max(qfreq.values()) or 1.0
        scored: list[tuple[str, float]] = []
        for path, text in docs:
            s = _score_tfidf(qfreq, qmax, idf, Counter(_tokens(text)), len(text))
            if s > 0:
                scored.append((path, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]


class TFIDFIndex:
    """SQLite 持久化的 TF-IDF 倒排索引（零 embedding 依赖）。

    解决 recall() 每查询「全库读盘 + 全库 ngram + 全库 IDF 双遍」的 O(N) 问题：
    预先把归一化文本的 ngram 及文档级 df 落盘，查询时只扫描命中 ngram 的文档
    即可排序取 Top-K，不再每查询触碰全部笔记。

    - build()：扫描镜像，把 (path, norm_text, grams, doc_len) 与 gram→df 写入 SQLite。
      追加式：已 index 的跳过。同义归一复用 MemoryRecall._norm_doc，保证查询/
      文档两侧主词空间对等，与 MemoryRecall 打分结果一致（本方为快路径，非另一
      套语义策略）。F-vacuum（2026-09-17 二期 F 项）：父目录在本轮扫描
      范围内的已知路径若已不存在于磁盘，随本轮一并从倒排清除，清单落
      self.last_vacuum（调用方可打印/上报；范围外路径保守不动防 mem_root
      变更误清）。返回本轮新增索引数。
    - recall()：查询同义归一后切 ngram，倒排查命中文档，按 TF-IDF 打分取 Top-K。
      返回值与 MemoryRecall.recall 同型（path, score），可用作同构替换。

    数据无关：所有路径由调用方注入；不回退时需确保索引已 build（未 build 时空索引，
    调用方应自行判断回退 MemoryRecall 全扫）。
    """

    def __init__(self, db_path: Path,
                 synonyms: dict[str, list[str]] | None = None):
        self.db_path = db_path
        # AGI-②（2026-09-18）：同义表驱动查询/文档双侧归一，保持主词空间对等。
        # 文档侧归一在 build() 落盘（ckey 随归一文本变化，增量重算自动迁移）；
        # 查询侧归一在 _query_terms。空表时两侧行为与旧版完全一致。
        self._norm = syn_norm_map(synonyms)
        self.last_vacuum: list[str] = []  # F-vacuum：最近一次 build() 清除的幽灵路径清单（供调用方打印）
        self.last_read_errors: int = 0  # G 项（2026-09-17）：最近一次 build() 读失败计数
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        # schema 演进迁移（A3 ASI06）：旧版 DB 的 doc 表可能缺 ckey 列，
        # CREATE TABLE IF NOT EXISTS 不会补列，导致 UNIQUE INDEX 建崩。
        # 索引是可重算的派生数据，检测缺列即整体丢弃 doc/gram，下次 build() 全量重建（最稳）。
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(doc)")}
        if cols and "ckey" not in cols:
            self._conn.execute("DROP TABLE IF EXISTS doc")
            self._conn.execute("DROP TABLE IF EXISTS gram")
            self._conn.commit()
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS doc (path TEXT PRIMARY KEY,"
            " ckey TEXT, norm TEXT NOT NULL, ngrams TEXT NOT NULL)")
        # A3 ASI06：内容键 UNIQUE 索引 —— 相同归一内容的笔记跨路径只能入库一次，
        # 在 schema 层兜住「同内容重复/污染注入」，不靠 Python 逻辑单一兜底。
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_doc_ckey"
            " ON doc(ckey) WHERE ckey IS NOT NULL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS gram "
            "(gram TEXT NOT NULL, df REAL NOT NULL)")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_gram ON gram(gram)")
        # P0-1 真倒排：docgram 关联表（gram→path 多对多）。recall 以 SQL 预筛
        # 候选文档（含任一查询 ngram），不再全表拉 doc 后 Python 过滤（原实现
        # 注释称"只扫描命中 ngram 的文档"实为全表拉取，属伪倒排）。主键
        # (gram, path) 的前缀索引天然覆盖「按 gram 取 path」的候选查询。
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS docgram "
            "(gram TEXT NOT NULL, path TEXT NOT NULL,"
            " PRIMARY KEY (gram, path))")
        self._conn.commit()
        # schema 迁移（P0-1）：docgram 为纯派生表。doc 已有数据而 docgram 为空
        # （旧库升级 / 异常中断残留）时从 doc 表全量回填，保证 recall 候选集
        # 不塌缩为空（否则旧库索引会静默零召回）。
        dg_n = self._conn.execute("SELECT COUNT(*) FROM docgram").fetchone()[0]
        dd_n = self._conn.execute("SELECT COUNT(*) FROM doc").fetchone()[0]
        if dg_n == 0 and dd_n > 0:
            self._rebuild_docgram()

    def close(self) -> None:
        self._conn.close()

    def _rebuild_docgram(self) -> int:
        """从 doc 表 ngrams 全量重建 docgram 关联表（派生表回填，幂等）。

        旧库升级 / docgram 与 doc 失步时由 __init__ 触发；亦可由 build() 复用
        作失步修复。返回写入行数（= gram 去重后的文档 gram 对总数）。
        """
        self._conn.execute("DELETE FROM docgram")
        pairs: list[tuple[str, str]] = []
        for path, ngrams_str in self._conn.execute(
                "SELECT path, ngrams FROM doc"):
            for pair in ngrams_str.split():
                if ":" in pair:
                    g = pair.split(":", 1)[0]
                    pairs.append((g, path))
        if pairs:
            self._conn.executemany(
                "INSERT OR IGNORE INTO docgram (gram, path) VALUES (?,?)",
                pairs)
        self._conn.commit()
        return len(pairs)

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM doc").fetchone()[0]

    def build(self, mem_root: Path, tiers: Iterable[str] | None = None,
              projects: Iterable[str] | None = None,
              norm: Callable[[str], str] | None = None) -> int:
        """扫描镜像重建倒排索引，返回索引笔记数。追加式：已 index 的跳过。"""
        from .protocol import TIER_DIR
        if tiers is None:
            tiers = TIER_DIR.values()
        _norm = norm or (lambda t: map_terms(t, self._norm)
                         if self._norm else _default_norm(t))
        self.last_read_errors = 0  # G 项：每轮 build 重置读失败计数
        known = {r[0] for r in self._conn.execute("SELECT path FROM doc")}
        known_ckeys = {r[0] for r in self._conn.execute(
            "SELECT ckey FROM doc WHERE ckey IS NOT NULL")}
        # F-vacuum（二期 F 项，2026-09-17）：known 中已不存在于磁盘的路径收集待清，
        # 否则快路径召回返回幽灵文件（镜像物理删除/外迁后索引无感知）。
        # 仅检查父目录落在本轮扫描范围内的已知路径：mem_root/tiers 变更场景
        # （known 全部越界）保守不动，防整体误清。真删延后到本函数末尾与红act
        # 剔除同批提交（原子）；清单显式落 self.last_vacuum 供调用方打印，索引层
        # 自身不做输出。
        scanned_dirs = {d for tier in tiers
                        for d in scan_tier_dirs(mem_root, tier, projects)}
        vacuum_paths = [p for p in sorted(known)
                        if Path(p).parent in scanned_dirs and not Path(p).is_file()]
        docs: list[tuple[str, str, str, dict, int]] = []  # (path, ckey, norm, grams, len)
        drop_paths: set[str] = set()  # P3.12 红act：已索引的 redacted 文档须从倒排剔除
        for tier in tiers:
            for d in scan_tier_dirs(mem_root, tier, projects):
                for md in d.glob("*.md"):
                    try:
                        txt = md.read_text(encoding="utf-8")
                    except Exception:
                        self.last_read_errors += 1  # G 项：计数不改变行为
                        continue
                    if is_redacted(txt):
                        if str(md) in known:
                            drop_paths.add(str(md))
                        continue  # 红act 文档不新索引；脱敏后重建索引即完成剔除（不可省略）
                    if str(md) in known:
                        continue
                    norms = _norm(txt)
                    grams = Counter(_tokens(norms))
                    if not grams:
                        continue
                    ckey = hashlib.sha256(norms.encode("utf-8")).hexdigest()
                    if ckey in known_ckeys:
                        continue  # A3 ASI06：同内容已索引，防重复/污染
                    known_ckeys.add(ckey)
                    docs.append((str(md), ckey, norms, grams, len(norms)))
        # P3.12 红act：从倒排剔除已 redacted 的旧索引条目（否则倒排仍残留该秘文）。
        # 必须在「新文档为空即早退」之前执行，保证重建索引仅剔除脱敏稿时也生效。
        # F-vacuum：幽灵路径与红act剔除同批 DELETE + 一次 commit（原子）。
        # P0-1：doc 删除须同步删 docgram（派生表失步会让候选集指向幽灵路径）。
        if drop_paths:
            for p in drop_paths:
                self._conn.execute("DELETE FROM doc WHERE path=?", (p,))
                self._conn.execute("DELETE FROM docgram WHERE path=?", (p,))
        if vacuum_paths:
            for p in vacuum_paths:
                self._conn.execute("DELETE FROM doc WHERE path=?", (p,))
                self._conn.execute("DELETE FROM docgram WHERE path=?", (p,))
        self.last_vacuum = vacuum_paths
        if drop_paths or vacuum_paths:
            self._conn.commit()
        if not docs and not drop_paths and not vacuum_paths:
            return 0
        # 文档级写入（doc 与 docgram 同批提交，保持派生表同步）
        dg_pairs: list[tuple[str, str]] = []
        for path, _ckey, norms, grams, ln in docs:
            self._conn.execute(
                "INSERT OR REPLACE INTO doc (path, ckey, norm, ngrams) VALUES (?,?,?,?)",
                (path, _ckey, norms, " ".join(
                    f"{g}:{c}" for g, c in grams.items())))
            dg_pairs.extend((g, path) for g in grams)
        if dg_pairs:
            self._conn.executemany(
                "INSERT OR IGNORE INTO docgram (gram, path) VALUES (?,?)",
                dg_pairs)
        # gram 级 df（全量重算）：doc 表含历史已索引文档，逐批 REPLACE 会丢失旧批
        # 贡献导致 idf 随增量 build 漂移，故每轮从 doc 表全量统计，语义恒等于全库。
        df: dict[str, int] = {}
        for (ngrams_str,) in self._conn.execute("SELECT ngrams FROM doc"):
            for pair in ngrams_str.split():
                if ":" in pair:
                    g = pair.split(":", 1)[0]
                    df[g] = df.get(g, 0) + 1
        n = self._conn.execute("SELECT COUNT(*) FROM doc").fetchone()[0]
        self._conn.execute("DELETE FROM gram")
        self._conn.executemany(
            "INSERT INTO gram (gram, df) VALUES (?,?)",
            ((g, math.log((n + 1) / (d + 1)) + 1.0) for g, d in df.items()))
        self._conn.commit()
        return len(docs)

    def recall(self, query: str, k: int = 8) -> list[tuple[str, float]]:
        """查询归一后切 ngram，按 TF-IDF 打分取 Top-K。

        - 查询归一（同 MemoryRecall._query_terms）保持主词空间对等；
        - P0-1 真倒排：docgram 关联表以 SQL 预筛含任一查询 ngram 的候选文档
          （候选集 = 旧「any(g in grams)」过滤的 SQL 等价），再只对候选读 doc
          表打分，不触碰镜像文件，也无需重算全库 IDF（已预先落 gram 表）；
        - 打分逻辑与旧全表扫描逐行同构（同一 _score_tfidf、同一过滤条件），
          保证与 MemoryRecall 全扫基准结果完全一致（双路径一致性测试守住）。
        """
        qterms = self._query_terms(query)
        if not qterms:
            return []
        qgrams = _tokens(" ".join(qterms))
        if not qgrams:
            return []
        qfreq = Counter(qgrams)
        qmax = max(qfreq.values()) or 1.0
        # 一次性取回查询 ngram 的 idf（缺失的 ngram 无 df，视为 0 贡献）
        idf: dict[str, float] = {}
        for g in set(qfreq):
            rows = self._conn.execute(
                "SELECT df FROM gram WHERE gram=?", (g,)).fetchall()
            if rows:
                idf[g] = rows[0][0]
        # P0-1：SQL 候选预筛（真倒排）。docgram 主键 (gram, path) 前缀索引
        # 覆盖本查询；候选集为空即无命中，直接返回，无需触碰 doc 表。
        cand: set[str] = set()
        for g in set(qfreq):
            for (path,) in self._conn.execute(
                    "SELECT path FROM docgram WHERE gram=?", (g,)):
                cand.add(path)
        if not cand:
            return []
        scored: list[tuple[str, float]] = []
        # 候选 doc 分批 IN 查询打分（每批 500，远低于 SQLite 变量数上限 999）
        paths = sorted(cand)
        for i in range(0, len(paths), 500):
            chunk = paths[i:i + 500]
            ph = ",".join("?" * len(chunk))
            for path, norms, ngrams_str in self._conn.execute(
                    f"SELECT path, norm, ngrams FROM doc WHERE path IN ({ph})",
                    chunk):
                grams: dict[str, float] = {}
                for pair in ngrams_str.split():
                    if ":" in pair:
                        g, c = pair.split(":", 1)
                        grams[g] = float(c)
                if not any(g in grams for g in qfreq):
                    continue
                s = _score_tfidf(qfreq, qmax, idf, Counter(grams), len(norms))
                if s > 0:
                    scored.append((path, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]

    def _query_terms(self, query: str) -> list[str]:
        return [self._norm.get(t, t) for t in _raw_terms(query)]


def _default_norm(text: str) -> str:
    return text