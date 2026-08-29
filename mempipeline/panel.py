# -*- coding: utf-8 -*-
"""panel.py — 零依赖记忆面板（v0.5.0）：只读仪表盘 + 审核操作（http.server 标准库）。

能力：
- GET  /            → 内嵌 HTML 仪表盘（dark 主题，fetch JSON 接口）
- GET  /api/stats   → 镜像统计（总数 + status/project/tier 分布）
- GET  /api/queue   → candidate 审核队列（promoted/rejected 一键操作）
- GET  /api/audit?n=→ 审计日志 tail N
- GET  /api/search?q=&project=&k= → hybrid_recall（语义失败自动回落 TF-IDF）
- POST /api/transition {path, to} → governance.transition（仅 candidate→promoted /
  candidate→rejected，路径校验在 mem_root 内，防穿越）

安全：仅监听 127.0.0.1；transition 白名单（candidate 起点）；数据无关（路径注入）。
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable

from .governance import review_queue, transition
from .recall import MemoryRecall

try:
    from .semantic import SemanticIndex, hybrid_recall
    _HAS_SEMANTIC = True
except Exception:  # 语义层可选（如依赖异常时面板仍可用）
    _HAS_SEMANTIC = False

try:
    from .ops import collect_ops
    _HAS_OPS = True
except Exception:  # 运维模块可选（面板降级仍可打开）
    _HAS_OPS = False


def _read_frontmatter(md: Path) -> dict:
    _FM = re.compile(r"^---\s*\n(.*?)\n---", re.S)
    try:
        txt = md.read_text(encoding="utf-8")
    except Exception:
        return {}
    m = _FM.match(txt)
    if not m:
        return {}
    fm = m.group(1)
    out = {}

    def get(k):
        mm = re.search(rf"(?m)^\s*{k}:\s*(.+)$", fm)
        if not mm:
            return ""
        v = mm.group(1).strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        return v

    for k in ("title", "memory_tier", "status", "project_id", "domain", "kind",
              "source_agent", "updated", "importance"):
        out[k] = get(k)
    return out


def collect_stats(mem_root: Path, tiers: Iterable[str] | None = None) -> dict:
    """镜像统计：总数 + status/project/tier 分布 + 审核队列数。"""
    from .protocol import TIER_DIR
    from .recall import scan_tier_dirs
    if tiers is None:
        tiers = TIER_DIR.values()
    total = 0
    by_status: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    by_project: dict[str, int] = {}
    for tier in tiers:
        for d in scan_tier_dirs(mem_root, tier, None):
            for md in d.glob("*.md"):
                fm = _read_frontmatter(md)
                total += 1
                st = fm.get("status") or "active"
                by_status[st] = by_status.get(st, 0) + 1
                by_tier[tier] = by_tier.get(tier, 0) + 1
                pid = fm.get("project_id") or "(legacy)"
                by_project[pid] = by_project.get(pid, 0) + 1
    queue = len(review_queue(mem_root, tiers, None))
    return {"total": total, "by_status": by_status, "by_tier": by_tier,
            "by_project": by_project, "queue": queue}


def _audit_tail(log_path: Path, n: int = 30) -> list[str]:
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
        return lines[-n:]
    except Exception:
        return []


def _json(handler, obj: dict, status: int = 200) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


_PAGE = """<!DOCTYPE html>
<html lang="zh">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>mempipeline 工作台</title>
<style>
body{background:#11151c;color:#d3d1c7;font-family:system-ui,sans-serif;margin:0;padding:24px}
h1{font-size:18px;font-weight:500;color:#b5d4f4} h2{font-size:14px;font-weight:500;margin-top:20px;color:#9fe1cb}
.card{background:#1a2029;border:1px solid #2c323d;border-radius:8px;padding:14px;margin:10px 0}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:#888780;font-weight:500;padding:6px 8px;border-bottom:1px solid #2c323d}
td{padding:6px 8px;border-bottom:1px solid #222834}
button{background:#185fa5;border:0;color:#fff;border-radius:4px;padding:6px 12px;min-height:28px;cursor:pointer;margin-right:6px}
button.danger{background:#a32d2d} input{background:#11151c;border:1px solid #6b7684;color:#d3d1c7;border-radius:4px;padding:6px;min-height:28px}
.bar{display:inline-block;height:10px;background:#2f7fd0;border-radius:3px;vertical-align:middle}
.tabs{margin:12px 0 4px}
.tabs button{background:#20293a;color:#9fb0c7;border:1px solid #2c323d}
.tabs button.on{background:#185fa5;color:#fff}
.muted{color:#888780;font-size:12px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.up{background:#3fb950} .down{background:#6e7681}
:focus-visible{outline:2px solid #22d3ee;outline-offset:2px;border-radius:4px}
:focus:not(:focus-visible){outline:none}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media (prefers-reduced-motion:reduce){*{transition-duration:.01ms!important;animation-duration:.01ms!important}}
</style>
<body>
<h1>mempipeline 工作台 <span class="muted">v0.6.0 · 记忆 + 运维统一入口</span></h1>
<nav class="tabs" role="tablist" aria-label="工作台视图切换">
<button id="tb-memory" class="on" role="tab" aria-selected="true" aria-controls="tab-memory" onclick="tab('memory')">记忆</button>
<button id="tb-ops" role="tab" aria-selected="false" aria-controls="tab-ops" onclick="tab('ops')">运维看板</button>
</nav>

<main>
<div id="tab-memory" role="tabpanel" aria-labelledby="tb-memory">
<div class="card" id="stats" aria-live="polite">加载中…</div>
<h2>审核队列（candidate）</h2><div class="card" id="queue" aria-live="polite">加载中…</div>
<h2>语义检索（hybrid）</h2><div class="card">
<label for="q" class="sr-only">检索记忆关键词</label>
<input id="q" placeholder="查询…" style="width:60%">
<button onclick="search()">检索</button>
<div id="sr" style="margin-top:10px" aria-live="polite"></div></div>
<h2>审计日志</h2><div class="card" id="audit" aria-live="polite">加载中…</div>
</div>

<div id="tab-ops" role="tabpanel" aria-labelledby="tb-ops" style="display:none">
<h2>WorkBuddy 自动化</h2><div class="card" id="opsAuto" aria-live="polite">加载中…</div>
<h2>Windows 计划任务（零 agent 运维）</h2><div class="card" id="opsTask" aria-live="polite">加载中…</div>
<h2>服务端口</h2><div class="card" id="opsSvc" aria-live="polite">加载中…</div>
<h2>最新体检 / 监控报告</h2><div class="card" id="opsRep" aria-live="polite">加载中…</div>
</div>
</main>

<script>
async function j(u,o){const r=await fetch(u,o);return r.json()}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
function bar(cnt,total){const w=Math.round(cnt/total*300);return `<span class="bar" style="width:${w}px"></span> ${cnt}`}
function tab(n){document.getElementById('tab-memory').style.display=n==='memory'?'':'none';
document.getElementById('tab-ops').style.display=n==='ops'?'':'none';
document.getElementById('tb-memory').className=n==='memory'?'on':'';
document.getElementById('tb-ops').className=n==='ops'?'on':'';
document.getElementById('tb-memory').setAttribute('aria-selected',n==='memory');
document.getElementById('tb-ops').setAttribute('aria-selected',n==='ops');
if(n==='ops')loadOps()}
async function loadStats(){const s=await j('/api/stats');
document.getElementById('stats').innerHTML=`<b>${s.total}</b> 篇笔记 · 审核队列 <b>${s.queue}</b><br><br>
<table><tr><th scope="col">状态</th><th scope="col">项目</th><th scope="col">层级</th></tr><tr>
<td>${Object.entries(s.by_status).map(([k,v])=>esc(k)+' '+bar(v,s.total)).join('<br>')}</td>
<td>${Object.entries(s.by_project).map(([k,v])=>esc(k)+' '+v).join('<br>')}</td>
<td>${Object.entries(s.by_tier).map(([k,v])=>esc(k)+' '+v).join('<br>')}</td></tr></table>`}
async function loadQueue(){const q=await j('/api/queue');
document.getElementById('queue').innerHTML=q.length?`<table><tr><th scope="col">笔记</th><th scope="col">项目</th><th scope="col">操作</th></tr>`+
q.map(n=>`<tr><td>${esc(n.title)}</td><td>${esc(n.project||'-')}</td><td>
<button onclick="go('${esc(n.path)}','promoted')">晋升全局</button>
<button class="danger" onclick="go('${esc(n.path)}','rejected')">拒绝</button></td></tr>`).join('')+'</table>':'<i>无待审 candidate</i>'}
async function go(path,to){await j('/api/transition',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path,to})});loadQueue();loadStats()}
async function search(){const q=document.getElementById('q').value;const r=await j('/api/search?q='+encodeURIComponent(q));
document.getElementById('sr').innerHTML=r.map(x=>`<div>${esc(x.path)} <span style="color:#888780">${x.score.toFixed(4)}</span></div>`).join('')||'<i>无结果</i>'}
async function loadAudit(){const a=await j('/api/audit?n=30');
document.getElementById('audit').innerHTML=`<table><tr><th scope="col">最近审计记录</th></tr>${a.map(l=>`<tr><td>${esc(l)}</td></tr>`).join('')}</table>`}
async function loadOps(){const o=await j('/api/ops');
if(o.error){const m='读取失败：'+o.error;['opsAuto','opsTask','opsSvc','opsRep'].forEach(id=>document.getElementById(id).innerHTML=esc(m));return}
const A=o.automations.active,P=o.automations.paused;
document.getElementById('opsAuto').innerHTML=
`<div class="muted">生效 ${A.length} 条 · 已停用 ${P.length} 条 · 采集于 ${esc(o.generated||'')}</div>`+
(A.length?`<table style="margin-top:8px"><tr><th scope="col">生效中</th><th scope="col">调度</th></tr>`+
A.map(a=>`<tr><td>${esc(a.name)}</td><td class="muted">${esc(a.rrule||'(单次/未设)')}</td></tr>`).join('')+'</table>':'')+
(P.length?`<details style="margin-top:8px"><summary class="muted">已停用 ${P.length} 条（展开查看）</summary>
<table style="margin-top:6px"><tr><th scope="col">名称</th><th scope="col">状态</th></tr>`+
P.map(a=>`<tr><td>${esc(a.name)}</td><td class="muted">${esc(a.status)}</td></tr>`).join('')+'</table></details>':'');
document.getElementById('opsTask').innerHTML=`<table><tr><th scope="col">任务</th><th scope="col">状态</th></tr>`+
o.tasks.map(t=>`<tr><td>${esc(t.name)}</td><td class="muted">${esc(t.state)}</td></tr>`).join('')+'</table>';
document.getElementById('opsSvc').innerHTML=`<table><tr><th scope="col">服务</th><th scope="col">端口</th><th scope="col">状态</th></tr>`+
o.services.map(s=>`<tr><td>${esc(s.name)}</td><td class="muted">${s.port}</td>
<td><span class="dot ${s.online?'up':'down'}" aria-hidden="true"></span>${s.online?'在线':'离线'}</td></tr>`).join('')+'</table>';
document.getElementById('opsRep').innerHTML=o.reports.length?`<table><tr><th scope="col">报告</th><th scope="col">类型</th><th scope="col">时间</th></tr>`+
o.reports.map(r=>`<tr><td>${esc(r.name)}</td><td class="muted">${esc(r.dir)}</td><td class="muted">${esc(r.mtime)}</td></tr>`).join('')+'</table>':'<i>暂无报告</i>'}
loadStats();loadQueue();loadAudit();
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    mem_root: Path = Path(".")
    audit_log: Path | None = None
    semantic_index: object | None = None
    audit: object | None = None  # 真实 AuditBackend（面板晋升写审计）

    def log_message(self, *a):  # 静默访问日志
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        if path == "/":
            body = _PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/stats":
            _json(self, collect_stats(self.mem_root))
        elif path == "/api/queue":
            q = review_queue(self.mem_root)
            _json(self, [{"path": str(p), "title": _read_frontmatter(p).get("title"),
                          "project": _read_frontmatter(p).get("project_id") or "-"}
                         for p in q])
        elif path == "/api/audit":
            n = int(qs.get("n", ["30"])[0])
            _json(self, _audit_tail(self.audit_log, n) if self.audit_log else [])
        elif path == "/api/ops":
            if _HAS_OPS:
                try:
                    _json(self, collect_ops())
                except Exception as e:
                    _json(self, {"error": f"ops 采集失败：{e}"}, 500)
            else:
                _json(self, {"error": "ops module unavailable"}, 503)
        elif path == "/api/search":
            q = qs.get("q", [""])[0]
            proj = qs.get("project", [None])[0]
            k = int(qs.get("k", ["8"])[0])
            if _HAS_SEMANTIC and self.semantic_index is not None:
                try:
                    res = hybrid_recall(q, k=k, mem_root=self.mem_root,
                                        index=self.semantic_index,
                                        projects=[proj] if proj else None)
                except Exception:
                    res = self._tfidf(q, k, proj)
            else:
                res = self._tfidf(q, k, proj)
            _json(self, [{"path": p, "score": round(s, 4)} for p, s in res])
        else:
            _json(self, {"error": "not found"}, 404)

    def _tfidf(self, q: str, k: int, proj: str | None) -> list:
        m = MemoryRecall(self.mem_root, projects=[proj] if proj else None)
        return [(p, 1.0 / (i + 1)) for i, (p, _) in enumerate(m.recall(q, k))]

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/transition":
            _json(self, {"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            _json(self, {"error": "bad request"}, 400)
            return
        path = Path(data.get("path", ""))
        to = data.get("to", "")
        try:
            path.resolve().relative_to(self.mem_root.resolve())
        except Exception:
            _json(self, {"error": "path outside mem_root"}, 400)
            return
        st, frm = transition(path, to, self.audit or _NullAudit(), source="panel")
        ok = st in ("wrote", "skipped") and frm == "candidate"
        _json(self, {"ok": ok, "status": st, "from": frm})


class _NullAudit:
    def mark(self, path, kind, source="manual", change=None):
        return {}


def serve(mem_root: Path, audit_log: Path | None = None,
          index: object | None = None, port: int = 8790,
          host: str = "127.0.0.1",
          audit: object | None = None) -> None:
    """起面板服务（仅本机）。Ctrl+C 停止。"""
    _Handler.mem_root = mem_root
    _Handler.audit_log = audit_log
    _Handler.semantic_index = index
    _Handler.audit = audit
    srv = ThreadingHTTPServer((host, port), _Handler)
    print(f"mempipeline 面板: http://{host}:{port}/  (mem_root={mem_root})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="mempipeline 记忆面板")
    p.add_argument("--mem-root", required=True, help="记忆镜像根")
    p.add_argument("--audit-log", default=None, help="审计日志路径（可选）")
    p.add_argument("--index-db", default=None, help="语义索引 SQLite（可选）")
    p.add_argument("--port", type=int, default=8790)
    a = p.parse_args(argv)
    idx = None
    if _HAS_SEMANTIC and a.index_db:
        idx = SemanticIndex(Path(a.index_db))
    audit = None
    if a.audit_log and a.mem_root:
        from .audit import FileAudit
        audit = FileAudit(Path(a.audit_log),
                          Path(a.audit_log).with_suffix(".manifest.json"),
                          Path(a.mem_root))
    serve(Path(a.mem_root), Path(a.audit_log) if a.audit_log else None,
          idx, port=a.port, audit=audit)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
