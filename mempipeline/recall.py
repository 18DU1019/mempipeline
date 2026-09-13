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


class RecallBackend(ABC):
    @abstractmethod
    def recall(self, query: str, k: int = 8) -> list[tuple[str, float]]:
        # return 首元素 = 候选笔记的绝对文件路径（非标题），次元素 = 相似度分，降序。
        ...


_STOPCHARS = frozenset("的了在是我你他她它与和及或对为从到个着都很也就而并且这那有其该被把让向于以不会能要已等还又再")


def is_redacted(text: str) -> bool:
    """判读一条笔记 frontmatter `status` 是否为 redacted（P3.12 检索切断）。

    红act 的软终态标记；命中即从「活跃可检索」面剔除。无 frontmatter /
    status 非 redacted 一律返回 False（不误伤存量笔记）。
    """
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return False
    mm = re.search(r"(?m)^\s*status:\s*(.+)$", m.group(1))
    if not mm:
        return False
    return mm.group(1).strip().strip("\"'") == "redacted"


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
        if tiers is None:
            from .protocol import TIER_DIR
            tiers = TIER_DIR.values()
        self.tiers = list(tiers)
        self.projects = list(projects) if projects is not None else None
        self.index = index  # 可选倒排快路径；缺省 None = 每查询全库读盘
        self._norm = {}
        for head, alts in (synonyms or {}).items():
            self._norm[head] = head
            for a in alts:
                self._norm[a] = head

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
        # 快路径：配置了倒排索引且无同义归一（索引按原词构建，归一场景需回落全扫保证口径）
        if self.index is not None and not self._norm:
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
      同义归一复用 MemoryRecall._norm_doc，保证查询/文档两侧主词空间对等，与
      MemoryRecall 打分结果一致（本方为快路径，非另一套语义策略）。
    - recall()：查询同义归一后切 ngram，倒排查命中文档，按 TF-IDF 打分取 Top-K。
      返回值与 MemoryRecall.recall 同型（path, score），可用作同构替换。

    数据无关：所有路径由调用方注入；不回退时需确保索引已 build（未 build 时空索引，
    调用方应自行判断回退 MemoryRecall 全扫）。
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
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
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM doc").fetchone()[0]

    def build(self, mem_root: Path, tiers: Iterable[str] | None = None,
              projects: Iterable[str] | None = None,
              norm: Callable[[str], str] | None = None) -> int:
        """扫描镜像重建倒排索引，返回索引笔记数。追加式：已 index 的跳过。"""
        from .protocol import TIER_DIR
        if tiers is None:
            tiers = TIER_DIR.values()
        _norm = norm or _default_norm
        known = {r[0] for r in self._conn.execute("SELECT path FROM doc")}
        known_ckeys = {r[0] for r in self._conn.execute(
            "SELECT ckey FROM doc WHERE ckey IS NOT NULL")}
        docs: list[tuple[str, str, str, dict, int]] = []  # (path, ckey, norm, grams, len)
        drop_paths: set[str] = set()  # P3.12 红act：已索引的 redacted 文档须从倒排剔除
        for tier in tiers:
            for d in scan_tier_dirs(mem_root, tier, projects):
                for md in d.glob("*.md"):
                    try:
                        txt = md.read_text(encoding="utf-8")
                    except Exception:
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
        if drop_paths:
            for p in drop_paths:
                self._conn.execute("DELETE FROM doc WHERE path=?", (p,))
            self._conn.commit()
        if not docs and not drop_paths:
            return 0
        # 文档级写入
        for path, _ckey, norms, grams, ln in docs:
            self._conn.execute(
                "INSERT OR REPLACE INTO doc (path, ckey, norm, ngrams) VALUES (?,?,?,?)",
                (path, _ckey, norms, " ".join(
                    f"{g}:{c}" for g, c in grams.items())))
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
        - 只扫描 doc 表（内存中 ngrams 计数 + norm 长度），不触碰镜像文件，
          也无需重算全库 IDF（已预先落 gram 表）；
        - 仅对含至少一个查询 ngram 的文档计分，其余跳过。
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
        scored: list[tuple[str, float]] = []
        for path, norms, ngrams_str in self._conn.execute(
                "SELECT path, norm, ngrams FROM doc"):
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
        return [t for t in _raw_terms(query)]


def _default_norm(text: str) -> str:
    return text