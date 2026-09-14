# -*- coding: utf-8 -*-
"""access_log.py — P1 召回未曝光度打点（AMV-P1，sidecar 独立存储，只记录不消费）。

补 score_note 缺失的「访问维度」：现状评分只有 importance × 时新（updated/mtime），
从未被召回曝光的笔记与高频曝光笔记不可区分。本模块把「曝光事件」落到内容库
之外的 SQLite sidecar（对齐社区一手实践：AWS AgentCore lifecycle 独立表、
Generative Agents recency 查询时计算——访问态不落内容 frontmatter）。

为什么必须 sidecar（2026-09-14 v2 论证 N1，P0 缺口）：
- 写回 frontmatter 会破坏读侧只读边界（每次召回都改文件）；
- mtime 变化污染 time_factor / stale_days / timegrap 时间基准；
- 召回结果反馈进自身排序，形成无对照的反馈回路。

口径（先记录、不消费）：
- 唯一记录点 = 面板 /api/search 出口（人真正看到结果的通道）；golden 回归、
  内部批量扫描（crossref/act/timegrap）**不算曝光**，避免自动化流量稀释信号；
- 不进 score_note / 不进治理候选评分——单人本地场景召回频率低，信号先积累
  数周分布，数据证明有区分度后另行裁决升格（v2 反题合题）。

失败绝不阻断召回：record() 内部吞异常（旁路观测件，非关键路径）。
数据无关：db 路径由调用方注入。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable


class AccessLog:
    """曝光事件流水：exposure(ts, path, source) 单表 append-only。

    path 与召回返回一致（笔记绝对路径字符串），后续与镜像对账直接用。
    check_same_thread=False：面板 ThreadingHTTPServer 每请求一线程。
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS exposure ("
            " ts TEXT NOT NULL, path TEXT NOT NULL, source TEXT NOT NULL)")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_exposure_path ON exposure(path)")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def record(self, paths: Iterable[str], source: str) -> int:
        """追加一批曝光事件，返回写入行数。异常吞掉（旁路观测，不阻断召回）。"""
        ts = datetime.now().isoformat(timespec="seconds")
        rows = [(ts, str(p), source) for p in paths]
        if not rows:
            return 0
        try:
            self._conn.executemany(
                "INSERT INTO exposure (ts, path, source) VALUES (?,?,?)", rows)
            self._conn.commit()
        except Exception:
            return 0
        return len(rows)

    def counts(self) -> dict[str, int]:
        """path -> 累计曝光次数（全时段）。"""
        return {p: n for p, n in self._conn.execute(
            "SELECT path, COUNT(*) FROM exposure GROUP BY path")}

    def last_seen(self, path: str) -> str | None:
        """某笔记最近一次曝光时间（ISO 串）；从未曝光返回 None。"""
        row = self._conn.execute(
            "SELECT MAX(ts) FROM exposure WHERE path=?", (str(path),)).fetchone()
        return row[0] if row and row[0] else None

    def unexposed(self, all_paths: Iterable[str]) -> list[str]:
        """给定全量笔记路径，返回其中从未曝光的子集（未曝光度候选面）。"""
        seen = {r[0] for r in self._conn.execute(
            "SELECT DISTINCT path FROM exposure")}
        return [str(p) for p in all_paths if str(p) not in seen]

    def sources(self) -> dict[str, int]:
        """source -> 事件数（观测自动化流量是否误入，口径自检用）。"""
        return {s: n for s, n in self._conn.execute(
            "SELECT source, COUNT(*) FROM exposure GROUP BY source")}

    def distribution(self, days: int = 7) -> dict:
        """近 days 天曝光分布（升格裁决的观测面，只读，不进评分链路）。

        返回 {since, total, distinct, top}：total=事件数、distinct=被曝光笔记数、
        top=按次数降序前 10 条 (path, n)。ts 为定宽 ISO 串，字典序即时间序，
        字符串比较安全。
        """
        since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        total, distinct = self._conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT path) FROM exposure WHERE ts >= ?",
            (since,)).fetchone()
        top = self._conn.execute(
            "SELECT path, COUNT(*) FROM exposure WHERE ts >= ? "
            "GROUP BY path ORDER BY COUNT(*) DESC, path LIMIT 10",
            (since,)).fetchall()
        return {"since": since, "total": total, "distinct": distinct, "top": top}
