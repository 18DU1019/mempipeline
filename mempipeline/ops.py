# -*- coding: utf-8 -*-
"""ops.py — 运维数据聚合（零依赖标准库）。

给面板的「运维看板」页提供数据：WorkBuddy 自动化 / Windows 计划任务 /
体检报告 / 服务端口探针。全部只读，失败降级不抛错。

设计：每个采集函数独立 try，单点失败不影响其他项（面板要永远打得开）。
"""
from __future__ import annotations

import json
import socket
import sqlite3
import subprocess
import time
from pathlib import Path

# 本机常量（可用参数覆盖，便于测试）
WORKWUDDY_DB = Path(r"C:\Users\Administrator\.workbuddy\workbuddy.db")
REPORTS_DIR = Path(r"H:\AI agent\_agent运行台\输出")
TASK_KEYWORDS = ("mempipeline", "onequant_health", "ragflow_health",
                 "ollama_update", "draft_stats")
SERVICES = [("mempipeline 面板", 8790), ("OneQuant signal_review", 5001),
            ("Ollama", 11434), ("RAGFlow Web", 80), ("ComfyUI", 8188)]

_CACHE: dict = {"at": 0.0, "data": {}}
_TTL = 30.0  # 计划任务查询较慢，30 秒缓存


def collect_automations(db: Path = WORKWUDDY_DB) -> list[dict]:
    """读 WorkBuddy 自动化（automations 表，读全表——绕开 list 接口的显示 bug）。"""
    rows = []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        cur = con.cursor()
        cur.execute(
            "SELECT id, name, status, rrule, schedule_type FROM automations "
            "WHERE deleted_at IS NULL ORDER BY status, name")
        for aid, name, status, rrule, stype in cur.fetchall():
            rows.append({"id": aid, "name": name, "status": status,
                         "rrule": rrule or "", "type": stype})
        con.close()
    except Exception as e:  # db 不可读时降级
        rows = [{"id": "-", "name": f"（读取失败：{e}）", "status": "UNKNOWN",
                 "rrule": "", "type": ""}]
    return rows


def collect_tasks(keywords: tuple = TASK_KEYWORDS) -> list[dict]:
    """读 Windows 计划任务（PowerShell Get-ScheduledTask，带短缓存）。"""
    now = time.time()
    if _CACHE["data"].get("tasks") and now - _CACHE["at"] < _TTL:
        return _CACHE["data"]["tasks"]
    tasks = []
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-ScheduledTask | Select-Object TaskName,State | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=60)
        data = json.loads(p.stdout.strip() or "[]")
        if isinstance(data, dict):
            data = [data]
        for t in data:
            nm = t.get("TaskName", "")
            if any(k.lower() in nm.lower() for k in keywords):
                tasks.append({"name": nm, "state": t.get("State", "?")})
    except Exception as e:
        tasks = [{"name": f"（读取失败：{e}）", "state": "UNKNOWN"}]
    _CACHE["data"]["tasks"] = tasks
    _CACHE["at"] = now
    return tasks


def collect_reports(reports_dir: Path = REPORTS_DIR, limit: int = 8) -> list[dict]:
    """列最新体检/监控报告（按修改时间倒序）。"""
    out = []
    try:
        files = list(reports_dir.rglob("*.md"))
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        for f in files[:limit]:
            out.append({"name": f.name, "dir": f.parent.name,
                        "mtime": time.strftime("%m-%d %H:%M",
                                               time.localtime(f.stat().st_mtime)),
                        "path": str(f)})
    except Exception:
        pass
    return out


def probe_services(services: list = SERVICES, host: str = "127.0.0.1",
                   timeout: float = 0.6) -> list[dict]:
    """端口存活探针（TCP 连接即断开）。"""
    res = []
    for name, port in services:
        ok = False
        try:
            with socket.create_connection((host, port), timeout=timeout):
                ok = True
        except Exception:
            ok = False
        res.append({"name": name, "port": port, "online": ok})
    return res


def collect_ops(db: Path = WORKWUDDY_DB, reports_dir: Path = REPORTS_DIR,
                services: list = SERVICES) -> dict:
    """聚合全部运维数据（供 /api/ops 使用）。"""
    autos = collect_automations(db)
    return {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "automations": {
            "active": [a for a in autos if a["status"] == "ACTIVE"],
            "paused": [a for a in autos if a["status"] != "ACTIVE"],
        },
        "tasks": collect_tasks(),
        "reports": collect_reports(reports_dir),
        "services": probe_services(services),
    }
