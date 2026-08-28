# -*- coding: utf-8 -*-
"""recall.py — 读取后端抽象 + ngram TF-IDF 召回（含同义映射扩展位）。

开源解释：RecallBackend 是所有读取入口的接口。MemoryRecall 用「查询 ngram
TF-IDF」做相关度打分：把 query 与候选笔记都切成 2-4 字字符 ngram（中文无分词
稳健），IDF 抑制全库通用词以提升区分度；synonyms 把同义词归一为主词后再匹配。
打分核心只服务 recall；交叉引用（crossref）用独立 bigram Jaccard，两处为不同
相似度策略，不共用核心。层目录默认取自 protocol.TIER_DIR。
"""
from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from collections import Counter
from pathlib import Path
from typing import Iterable


class RecallBackend(ABC):
    @abstractmethod
    def recall(self, query: str, k: int = 8) -> list[tuple[str, float]]:
        # return 首元素 = 候选笔记的绝对文件路径（非标题），次元素 = 相似度分，降序。
        ...


_STOPCHARS = frozenset("的了在是我你他她它与和及或对为从到个着都很也就而并且这那有其该被把让向于以不会能要已等还又再")


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


class MemoryRecall(RecallBackend):
    """查询 ngram TF-IDF 打分；synonyms 把同义词归一为主词后再匹配。"""

    def __init__(self, mem_root: Path, tiers: Iterable[str] | None = None,
                 synonyms: dict[str, list[str]] | None = None):
        self.mem_root = mem_root
        if tiers is None:
            from .protocol import TIER_DIR
            tiers = TIER_DIR.values()
        self.tiers = list(tiers)
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
            for md in (self.mem_root / tier).glob("*.md"):
                try:
                    txt = md.read_text(encoding="utf-8")
                except Exception:
                    continue
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