# -*- coding: utf-8 -*-
"""semantic.py — 零依赖语义检索层（E2'）：bge-m3 嵌入 + SQLite 向量索引 + RRF 融合。

补 mempipeline 读侧的 semantic 空缺（现有 lexical 三通道：ngram TF-IDF /
bigram Jaccard / tag_relevance 均为字符级）。本模块：
- embed()：调本地 Ollama bge-m3（/api/embed，urllib 标准库，零新增依赖）
- SemanticIndex：SQLite 持久化向量（path 主键，vec BLOB），支持项目作用域召回
- rrf_fuse()：Reciprocal Rank Fusion，把 lexical + semantic 两路排序融合（免调参）
- hybrid_recall()：一站式融合召回（语义层不可用时自动回落纯 TF-IDF，可逆）

数据无关：所有路径由调用方注入；不写死任何私有路径。
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from .recall import MemoryRecall, scan_tier_dirs

OLLAMA_EMBED_URL = "http://127.0.0.1:11434/api/embed"
EMBED_MODEL = "bge-m3"
DIM = 1024  # bge-m3 输出维度（实测确认）

EmbedFn = Callable[[list[str]], list[list[float]]]


def embed(texts: list[str], url: str = OLLAMA_EMBED_URL,
          model: str = EMBED_MODEL) -> list[list[float]]:
    """调本地 Ollama bge-m3 批量嵌入。失败抛异常（调用方决定是否回落）。"""
    req = urllib.request.Request(
        url,
        data=json.dumps({"model": model, "input": texts}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["embeddings"]


def cos_sim(a: list[float], b: list[float]) -> float:
    """余弦相似度（纯 Python，1024 维在笔记级规模下足够快）。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def rrf_fuse(ranked_lists: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion：多路排序按 1/(k+rank) 累加融合，免调参。"""
    scores: dict[str, float] = {}
    for lst in ranked_lists:
        for rank, item in enumerate(lst):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: -kv[1])


class SemanticIndex:
    """SQLite 持久化的向量索引：path 主键 + vec BLOB（struct 紧凑序列化）。

    支持 build 全量重建（scan_tier_dirs 复用，兼容 legacy/项目二维布局），
    recall 按项目作用域过滤。零新增依赖（sqlite3 标准库）。
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        # check_same_thread=False：面板走 ThreadingHTTPServer，每个请求一个
        # 线程，索引连接须可跨线程使用（sqlite3 连接自带上锁，安全）
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS emb (path TEXT PRIMARY KEY,"
            " vec BLOB NOT NULL, tier TEXT, project TEXT)")
        # A1 语义缓存：qemb 缓存查询嵌入（省最贵的 Ollama 网络调用）；
        # qres 缓存 top-k 结果，按 gen 代数失效（与索引 build 新鲜度一致）；
        # meta 存 gen 计数器。
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS qemb (q TEXT PRIMARY KEY, vec BLOB NOT NULL, ts TEXT)")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS qres (q TEXT, scope TEXT, k INTEGER, gen INTEGER,"
            " results TEXT, ts TEXT, PRIMARY KEY (q, scope, k))")
        self._conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _pack(vec: list[float]) -> bytes:
        import struct
        return struct.pack(f"<{len(vec)}f", *vec)

    @staticmethod
    def _unpack(blob: bytes) -> list[float]:
        import struct
        return list(struct.unpack(f"<{len(blob)//4}f", blob))

    def add(self, path: str, vec: list[float], tier: str = "",
            project: str = "") -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO emb (path, vec, tier, project) VALUES (?,?,?,?)",
            (path, self._pack(vec), tier, project))
        self._conn.commit()

    def build(self, mem_root: Path, tiers: Iterable[str] | None = None,
              projects: Iterable[str] | None = None,
              embed_fn: EmbedFn | None = None,
              limit: int = 0) -> int:
        """扫描镜像构建索引（追加式：已索引路径跳过）。返回新增条数。"""
        from .protocol import TIER_DIR
        if tiers is None:
            tiers = TIER_DIR.values()
        embed_fn = embed_fn or embed
        if embed_fn is embed:
            known = {r[0] for r in self._conn.execute("SELECT path FROM emb")}
        else:
            known = set()
        docs: list[tuple[str, str, str, str]] = []  # (path, text, tier, project)
        for tier in tiers:
            for d in scan_tier_dirs(mem_root, tier, projects):
                for md in d.glob("*.md"):
                    if str(md) in known:
                        continue
                    try:
                        txt = md.read_text(encoding="utf-8")
                    except Exception:
                        continue
                    rel = str(md)  # 与 recall._recall 主键一致，RRF 融合不再分叉
                    rel_parts = md.relative_to(mem_root).parts
                    proj = rel_parts[1] if "projects" in rel_parts else ""
                    docs.append((rel, txt, tier, proj))
        if not docs:
            return 0
        if limit and len(docs) > limit:
            docs = docs[:limit]
        # 分批嵌入（每批 8 条），失败即中止（调用方决定回落）
        batch = 8
        for i in range(0, len(docs), batch):
            chunk = docs[i:i + batch]
            vecs = embed_fn([t for _, t, _, _ in chunk])
            for (rel, _t, tier, proj), vec in zip(chunk, vecs):
                self.add(rel, vec, tier, proj)
        self._bump_gen()  # 建了索引才换代，检索/top-k 缓存随代数失效
        return len(docs)

    def recall(self, query_vec: list[float], k: int = 8,
               projects: Iterable[str] | None = None) -> list[tuple[str, float]]:
        """按余弦相似度召回 Top-K（可项目过滤）。projects=None 返回全库。"""
        rows = self._conn.execute(
            "SELECT path, vec, project FROM emb").fetchall()
        scored: list[tuple[str, float]] = []
        proj_set = set(projects or [])
        for path, blob, proj in rows:
            if proj_set and proj not in proj_set:
                continue
            s = cos_sim(query_vec, self._unpack(blob))
            if s > 0:
                scored.append((path, s))
        scored.sort(key=lambda kv: kv[1], reverse=True)
        return scored[:k]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM emb").fetchone()[0]

    def gen(self) -> int:
        """语义索引代数：build 有新增时 +1，作为查询/结果缓存的失效键。"""
        row = self._conn.execute("SELECT v FROM meta WHERE k='gen'").fetchone()
        return int(row[0]) if row and row[0].isdigit() else 1

    def _bump_gen(self) -> int:
        g = self.gen() + 1
        self._conn.execute(
            "INSERT INTO meta (k, v) VALUES ('gen', ?)"
            " ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(g),))
        self._conn.commit()
        return g

    def query_embed(self, query: str, embed_fn: EmbedFn) -> list[float]:
        """查询嵌入缓存（A1）：同查询命中复用，否则调 embed_fn 并落库。幂等。失败抛异常。"""
        h = hashlib.sha256(query.encode("utf-8")).hexdigest()
        row = self._conn.execute("SELECT vec FROM qemb WHERE q=?", (h,)).fetchone()
        if row:
            return self._unpack(row[0])
        vec = embed_fn([query])[0]
        self._conn.execute(
            "INSERT OR REPLACE INTO qemb (q, vec, ts) VALUES (?,?,?)",
            (h, self._pack(vec), datetime.now().isoformat(timespec="seconds")))
        self._conn.commit()
        return vec

    def recall_cached(self, query: str, qvec: list[float], k: int = 8,
                      projects: Iterable[str] | None = None) -> list[tuple[str, float]]:
        """top-k 结果缓存（A1）：gen 一致时复用，否则现算并落库。失效只影响首次。"""
        scope = "|".join(sorted(projects or []))
        g = self.gen()
        h = hashlib.sha256(query.encode("utf-8")).hexdigest()
        row = self._conn.execute(
            "SELECT gen, results FROM qres WHERE q=? AND scope=? AND k=?",
            (h, scope, k)).fetchone()
        if row and int(row[0]) == g:
            return [(p, float(s)) for p, s in json.loads(row[1])]
        res = self.recall(qvec, k, projects=projects)
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO qres (q, scope, k, gen, results, ts)"
                " VALUES (?,?,?,?,?,?)",
                (h, scope, k, g, json.dumps(res),
                 datetime.now().isoformat(timespec="seconds")))
            self._conn.commit()
        except Exception:
            pass  # 缓存写失败不阻断召回
        return res


def hybrid_recall(query: str, k: int = 8, mem_root: Path | None = None,
                  index: SemanticIndex | None = None,
                  tiers: Iterable[str] | None = None,
                  projects: Iterable[str] | None = None,
                  embed_fn: EmbedFn | None = None,
                  memory: MemoryRecall | None = None) -> list[tuple[str, float]]:
    """lexical(TF-IDF) + semantic(bge-m3) 双路 RRF 融合召回。

    - index 缺省 → 纯 TF-IDF 路径（语义不可用时自动回落，可逆）
    - memory 可注入现成实例（复用同义归一配置），缺省按参数新建
    - 返回 [(path, fused_score)]，降序
    """
    embed_fn = embed_fn or embed
    if memory is None:
        assert mem_root is not None, "memory 与 mem_root 至少给一个"
        memory = MemoryRecall(mem_root, tiers=tiers, projects=projects)
    lex = [p for p, _ in memory.recall(query, k * 3)]
    if index is None:
        return [(p, 1.0 / (i + 1)) for i, p in enumerate(lex[:k])]
    try:
        qvec = index.query_embed(query, embed_fn)
    except Exception:
        return [(p, 1.0 / (i + 1)) for i, p in enumerate(lex[:k])]  # 语义失败回落
    sem = [p for p, _ in index.recall_cached(query, qvec, k * 3, projects=projects)]
    return rrf_fuse([lex, sem])[:k]
