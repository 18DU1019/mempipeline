# -*- coding: utf-8 -*-
"""recall.py — 读取后端抽象 + 轻量子串召回骨架（含同义映射扩展位）。

开源解释：RecallBackend 是所有读取入口的接口。MemoryRecall 用「查询词在笔记
纯文本中的子串词频」做轻量相关度打分（对中文无分词场景稳健），并预留
synonyms 词形映射位，把同义词统一映射到主词后再匹配。
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable


class RecallBackend(ABC):
    @abstractmethod
    def recall(self, query: str, k: int = 8) -> list[tuple[str, float]]:
        ...


def _raw_terms(s: str) -> list[str]:
    return [w for w in re.findall(r"[\w\u4e00-\u9fa5]+", s)]


class MemoryRecall(RecallBackend):
    """按查询词子串词频打分；synonyms 把同义词归一为主词后再匹配。"""

    def __init__(self, mem_root: Path, tiers: Iterable[str] = ("01-长期记忆", "02-中期记忆"),
                 synonyms: dict[str, list[str]] | None = None):
        self.mem_root = mem_root
        self.tiers = list(tiers)
        self._norm = {}
        for head, alts in (synonyms or {}).items():
            self._norm[head] = head
            for a in alts:
                self._norm[a] = head

    def _query_terms(self, query: str) -> list[str]:
        return [self._norm.get(t, t) for t in _raw_terms(query)]

    def recall(self, query: str, k: int = 8) -> list[tuple[str, float]]:
        qterms = self._query_terms(query)
        if not qterms:
            return []
        scored: list[tuple[str, float]] = []
        for tier in self.tiers:
            for md in (self.mem_root / tier).glob("*.md"):
                try:
                    txt = md.read_text(encoding="utf-8")
                except Exception:
                    continue
                text = "".join(txt.split())  # 去空白，中文子串可连续匹配
                if not text:
                    continue
                score = sum(text.count(q) for q in qterms) / len(text)
                if score > 0:
                    scored.append((str(md), score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]
