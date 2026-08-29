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
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>mempipeline 工作台</title>
<style>
:root{
--color-canvas:#07080a;--color-surface-1:#0d0d0d;--color-surface-2:#121212;--color-surface-3:#171717;
--color-hairline:rgba(255,255,255,0.08);--color-hairline-soft:#242728;--color-hairline-strong:rgba(255,255,255,0.16);
--color-field-border:#6b7684;
--color-ink:#f4f4f6;--color-ink-body:#cdcdcd;--color-ink-muted:#9c9c9d;--color-ink-subtle:#7d7e80;
--color-brand:#22d3ee;--color-brand-hover:#67e8f9;--color-brand-active:#06b6d4;--color-brand-soft:rgba(34,211,238,0.12);
--color-accent:#a78bfa;--color-success:#59d499;--color-warning:#ffc533;--color-danger:#ff6161;--color-info:#57c1ff;
--font-sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei","Hiragino Sans GB",Inter,sans-serif;
--font-mono:ui-monospace,"SF Mono","Cascadia Code",Consolas,monospace;
--radius-control:6px;--radius-card:8px;--radius-container:12px;
--shadow-focus:0 0 0 2px rgba(34,211,238,0.5);--shadow-overlay:0 16px 48px rgba(0,0,0,0.6);
--z-sticky:100;
}
*{box-sizing:border-box}
body{background:var(--color-canvas);color:var(--color-ink-body);font-family:var(--font-sans);font-size:13px;line-height:1.6;margin:0}
.app{display:flex;min-height:100vh}
.sidebar{width:200px;flex:0 0 200px;background:var(--color-surface-1);border-right:1px solid var(--color-hairline);padding:16px 0;position:sticky;top:0;height:100vh;overflow-y:auto}
.sidebar__brand{padding:0 16px 16px;border-bottom:1px solid var(--color-hairline);margin-bottom:8px}
.sidebar__title{font-size:15px;font-weight:600;line-height:1.4;letter-spacing:-0.005em;color:var(--color-ink)}
.sidebar__sub{font-size:11px;line-height:1.5;color:var(--color-ink-subtle);margin-top:2px}
.nav{padding:0 8px}
.nav-group-label{font-size:11px;color:var(--color-ink-subtle);padding:12px 8px 4px;letter-spacing:0.01em}
.nav-item{display:flex;align-items:center;width:100%;min-height:32px;padding:0 12px;margin-bottom:2px;border:0;border-radius:var(--radius-control);background:transparent;color:var(--color-ink-muted);font-family:inherit;font-size:13px;font-weight:500;text-align:left;cursor:pointer;transition:background 120ms ease-out,color 120ms ease-out}
.nav-item:hover{background:var(--color-surface-3);color:var(--color-ink)}
.nav-item.is-active{background:var(--color-surface-3);color:var(--color-ink);box-shadow:inset 2px 0 0 var(--color-brand)}
.main{flex:1;min-width:0;padding:24px 0 48px}
.container{max-width:1200px;margin:0 auto;padding:0 24px}
.page-head{margin-bottom:24px;padding-bottom:16px;border-bottom:1px solid var(--color-hairline)}
.display{font-size:24px;font-weight:600;line-height:1.25;letter-spacing:-0.02em;color:var(--color-ink);margin:0}
.page-sub{font-size:12px;color:var(--color-ink-muted);margin:4px 0 0}
.view{display:none}.view.is-active{display:block}
.h1{font-size:18px;font-weight:600;line-height:1.33;letter-spacing:-0.01em;color:var(--color-ink);margin:24px 0 8px}
.kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px}
.kpi{background:var(--color-surface-1);border:1px solid var(--color-hairline);border-radius:var(--radius-card);padding:12px}
.kpi__label{font-size:12px;color:var(--color-ink-muted);line-height:1.5}
.kpi__value{font-size:24px;font-weight:600;line-height:1.25;letter-spacing:-0.02em;color:var(--color-ink);font-variant-numeric:tabular-nums;margin-top:4px}
.kpi--warn .kpi__value{color:var(--color-warning)}
.card{background:var(--color-surface-1);border:1px solid var(--color-hairline);border-radius:var(--radius-card);padding:16px;margin-bottom:16px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-size:12px;font-weight:500;color:var(--color-ink-muted);padding:8px;border-bottom:1px solid var(--color-hairline-soft)}
td{padding:8px;border-bottom:1px solid var(--color-hairline);vertical-align:top}
tr:last-child td{border-bottom:0}
.btn{display:inline-flex;align-items:center;justify-content:center;min-height:32px;min-width:32px;padding:0 12px;border-radius:var(--radius-control);font-family:inherit;font-size:13px;font-weight:500;border:1px solid transparent;cursor:pointer;transition:background 120ms ease-out,border-color 120ms ease-out,color 120ms ease-out}
.btn--primary{background:var(--color-brand);color:#07080a}
.btn--primary:hover{background:var(--color-brand-hover)}
.btn--primary:active{background:var(--color-brand-active)}
.btn--ghost{background:transparent;color:var(--color-ink-muted);border-color:var(--color-hairline-strong)}
.btn--ghost:hover{background:var(--color-surface-2);color:var(--color-ink)}
.btn--danger{background:var(--color-danger);color:#07080a}
.btn--danger:hover{background:#ff8080}
.btn--danger-outline{background:transparent;color:var(--color-danger);border-color:rgba(255,97,97,0.4)}
.btn--danger-outline:hover{background:rgba(255,97,97,0.12)}
.btn-row{display:flex;gap:8px;flex-wrap:wrap}
.input{height:36px;background:var(--color-surface-2);border:1px solid var(--color-field-border);border-radius:var(--radius-control);padding:0 12px;color:var(--color-ink);font-family:inherit;font-size:13px;min-width:240px}
.input::placeholder{color:var(--color-ink-subtle)}
.input:focus{outline:none;border-color:var(--color-brand);box-shadow:var(--shadow-focus)}
.search-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.bar{display:inline-block;height:8px;background:var(--color-brand);border-radius:999px;vertical-align:middle;margin-right:8px;min-width:2px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px;vertical-align:middle}
.dot--up{background:var(--color-success)}
.dot--down{background:var(--color-ink-subtle)}
.badge{display:inline-flex;align-items:center;height:20px;padding:0 8px;border-radius:999px;font-size:12px}
.badge--brand{background:var(--color-brand-soft);color:var(--color-brand)}
.badge--semantic{background:rgba(167,139,250,0.12);color:var(--color-accent)}
.subtle{font-size:12px;color:var(--color-ink-subtle)}
.num{font-family:var(--font-mono);font-variant-numeric:tabular-nums;font-size:12px;color:var(--color-ink-subtle)}
.empty{padding:48px 16px;text-align:center;color:var(--color-ink-muted)}
.empty__hint{font-size:12px;color:var(--color-ink-subtle);margin-top:4px}
.hit{padding:8px 0;border-bottom:1px solid var(--color-hairline)}
.hit:last-child{border-bottom:0}
details summary{cursor:pointer;padding:4px 0}
:focus-visible{outline:2px solid var(--color-brand);outline-offset:2px;border-radius:4px}
:focus:not(:focus-visible){outline:none}
.btn:focus-visible,.nav-item:focus-visible{outline:none;box-shadow:var(--shadow-focus)}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media (prefers-reduced-motion:reduce){*{transition-duration:.01ms!important;animation-duration:.01ms!important}}
@media (max-width:1279px){.container{padding:0 16px}}
@media (max-width:1023px){
.sidebar{width:56px;flex:0 0 56px;padding:8px 0}
.sidebar__brand{display:none}
.nav-group-label{display:none}
.nav-item{justify-content:center;padding:0;font-size:0}
.nav-item::before{content:attr(data-short);font-size:11px}
.kpi-grid{grid-template-columns:repeat(2,1fr)}
}
@media (max-width:767px){
.app{display:block}
.sidebar{position:fixed;left:0;right:0;bottom:0;top:auto;width:auto;height:auto;flex:none;border-right:0;border-top:1px solid var(--color-hairline);background:var(--color-surface-2);padding:8px;z-index:var(--z-sticky)}
.nav{display:flex;gap:8px;padding:0}
.nav-item{flex:1;min-height:44px;margin-bottom:0}
.nav-item.is-active{box-shadow:inset 0 -2px 0 var(--color-brand)}
.nav-item::before{content:attr(data-short);font-size:12px}
.main{padding:16px 0 96px}
.page-head{padding-bottom:12px;margin-bottom:16px}
table,tbody,tr,td{display:block;width:100%}
thead{display:none}
tr{border-bottom:1px solid var(--color-hairline);padding:8px 0}
tr:last-child{border-bottom:0}
td{border:0;padding:2px 0;display:flex;gap:8px;align-items:baseline}
td::before{content:attr(data-label);flex:0 0 76px;color:var(--color-ink-subtle);font-size:12px}
.search-row .input{min-width:0;flex:1 1 100%}
}
</style>
</head>
<body>
<div class="app">
<aside class="sidebar">
<div class="sidebar__brand">
<div class="sidebar__title">mempipeline</div>
<div class="sidebar__sub">本地记忆系统 v0.6.0</div>
</div>
<nav class="nav" aria-label="工作台视图切换">
<div class="nav-group-label">记忆</div>
<div role="tablist" aria-orientation="vertical">
<button id="nb-overview" class="nav-item is-active" role="tab" aria-selected="true" aria-controls="view-overview" data-short="概览" onclick="view('overview')">概览</button>
<button id="nb-memory" class="nav-item" role="tab" aria-selected="false" aria-controls="view-memory" data-short="记忆" onclick="view('memory')">记忆</button>
</div>
<div class="nav-group-label">运维</div>
<div role="tablist" aria-orientation="vertical">
<button id="nb-ops" class="nav-item" role="tab" aria-selected="false" aria-controls="view-ops" data-short="运维" onclick="view('ops')">运维看板</button>
</div>
</nav>
</aside>

<main class="main">
<div class="container">
<header class="page-head">
<h1 class="display" id="pageTitle">概览</h1>
<p class="page-sub" id="pageSub">记忆资产与服务状态总览</p>
</header>

<section id="view-overview" class="view is-active" role="tabpanel" aria-labelledby="nb-overview" tabindex="0">
<div class="kpi-grid" id="kpi" aria-live="polite"></div>
<h2 class="h1">分布明细</h2>
<div class="card" id="stats" aria-live="polite">加载中…</div>
<h2 class="h1">服务状态</h2>
<div class="card" id="ovSvc" aria-live="polite">加载中…</div>
</section>

<section id="view-memory" class="view" role="tabpanel" aria-labelledby="nb-memory" tabindex="0">
<h2 class="h1">审核队列（candidate）</h2>
<div class="card" id="queue" aria-live="polite">加载中…</div>
<h2 class="h1">语义检索（hybrid）</h2>
<div class="card">
<div class="search-row">
<label for="q" class="sr-only">检索记忆关键词</label>
<input id="q" class="input" placeholder="查询…">
<button class="btn btn--primary" onclick="search()">检索</button>
</div>
<div id="sr" aria-live="polite"></div>
</div>
<h2 class="h1">审计日志</h2>
<div class="card" id="audit" aria-live="polite">加载中…</div>
</section>

<section id="view-ops" class="view" role="tabpanel" aria-labelledby="nb-ops" tabindex="0">
<h2 class="h1">WorkBuddy 自动化</h2><div class="card" id="opsAuto" aria-live="polite">加载中…</div>
<h2 class="h1">Windows 计划任务（零 agent 运维）</h2><div class="card" id="opsTask" aria-live="polite">加载中…</div>
<h2 class="h1">服务端口</h2><div class="card" id="opsSvc" aria-live="polite">加载中…</div>
<h2 class="h1">最新体检 / 监控报告</h2><div class="card" id="opsRep" aria-live="polite">加载中…</div>
</section>
</div>
</main>
</div>

<script>
async function j(u,o){const r=await fetch(u,o);return r.json()}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
function jstr(s){return String(s).replace(/\\\\/g,'\\\\\\\\').replace(/'/g,"\\\\'")}
function bar(cnt,total){const w=Math.max(2,Math.round(cnt/total*120));return `<span class="bar" style="width:${w}px"></span> <span class="num">${cnt}</span>`}
function kpi(label,value,warn){return `<div class="kpi${warn?' kpi--warn':''}"><div class="kpi__label">${esc(label)}</div><div class="kpi__value">${esc(value)}</div></div>`}
function empty(msg,hint){return `<div class="empty">${esc(msg)}${hint?'<div class="empty__hint">'+esc(hint)+'</div>':''}</div>`}
function svcTable(list){return `<table><tr><th scope="col">服务</th><th scope="col">端口</th><th scope="col">状态</th></tr>`+
list.map(function(s){return `<tr><td data-label="服务">${esc(s.name)}</td><td data-label="端口" class="num">${s.port}</td>
<td data-label="状态"><span class="dot ${s.online?'dot--up':'dot--down'}" aria-hidden="true"></span>${s.online?'在线':'离线'}</td></tr>`}).join('')+'</table>'}
const VIEWS=['overview','memory','ops'];
const META={overview:['概览','记忆资产与服务状态总览'],memory:['记忆','审核队列、语义检索与审计记录'],ops:['运维看板','自动化、计划任务、服务端口与体检报告']};
function view(n){VIEWS.forEach(function(k){
document.getElementById('view-'+k).classList.toggle('is-active',k===n);
const b=document.getElementById('nb-'+k);
b.classList.toggle('is-active',k===n);
b.setAttribute('aria-selected',k===n?'true':'false');});
document.getElementById('pageTitle').textContent=META[n][0];
document.getElementById('pageSub').textContent=META[n][1];
if(n==='memory'){loadQueue();loadAudit()}
if(n==='ops'){loadOps()}}
async function loadStats(){const s=await j('/api/stats');
const tiers=Object.entries(s.by_tier||{});
document.getElementById('kpi').innerHTML=
kpi('笔记总数',s.total)+kpi('待审队列',s.queue,s.queue>0)+
tiers.slice(0,2).map(function(t){return kpi(t[0],t[1])}).join('');
document.getElementById('stats').innerHTML=`<table><tr><th scope="col">状态</th><th scope="col">项目</th><th scope="col">层级</th></tr><tr>
<td data-label="状态">${Object.entries(s.by_status).map(function(e){return esc(e[0])+' '+bar(e[1],s.total)}).join('<br>')}</td>
<td data-label="项目">${Object.entries(s.by_project).map(function(e){return esc(e[0])+' <span class="num">'+e[1]+'</span>'}).join('<br>')}</td>
<td data-label="层级">${tiers.map(function(e){return esc(e[0])+' <span class="num">'+e[1]+'</span>'}).join('<br>')}</td></tr></table>`}
async function loadQueue(){const q=await j('/api/queue');
document.getElementById('queue').innerHTML=q.length?`<table><tr><th scope="col">笔记</th><th scope="col">项目</th><th scope="col">操作</th></tr>`+
q.map(function(n){return `<tr><td data-label="笔记">${esc(n.title)}</td><td data-label="项目" class="subtle">${esc(n.project||'-')}</td><td data-label="操作">
<div class="btn-row"><button class="btn btn--primary" onclick="go('${jstr(n.path)}','promoted')">晋升全局</button>
<button class="btn btn--danger-outline" onclick="go('${jstr(n.path)}','rejected')">拒绝</button></div></td></tr>`}).join('')+'</table>'
:empty('暂无待审 candidate','记忆写入后若判定为候选，会出现在这里等待晋升')}
async function go(path,to){await j('/api/transition',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:path,to:to})});loadQueue();loadStats()}
async function search(){const q=document.getElementById('q').value;const r=await j('/api/search?q='+encodeURIComponent(q));
document.getElementById('sr').innerHTML=r.length?'<div style="margin-top:12px">'+r.map(function(x){return `<div class="hit">${esc(x.path)} <span class="num">${x.score.toFixed(4)}</span></div>`}).join('')+'</div>':empty('无结果','换个关键词，或确认语义索引已重建')}
async function loadAudit(){const a=await j('/api/audit?n=30');
document.getElementById('audit').innerHTML=a.length?`<table><tr><th scope="col">最近审计记录</th></tr>${a.map(function(l){return `<tr><td data-label="记录" class="subtle">${esc(l)}</td></tr>`}).join('')}</table>`:empty('暂无审计记录')}
async function loadOps(){const o=await j('/api/ops');
if(o.error){['opsAuto','opsTask','opsSvc','opsRep','ovSvc'].forEach(function(id){document.getElementById(id).innerHTML=empty('读取失败：'+o.error,'检查 workbuddy.db 是否可只读打开')});return}
const A=o.automations.active,P=o.automations.paused;
document.getElementById('opsAuto').innerHTML=
`<div class="subtle">生效 ${A.length} 条 · 已停用 ${P.length} 条 · 采集于 ${esc(o.generated||'')}</div>`+
(A.length?`<table style="margin-top:8px"><tr><th scope="col">生效中</th><th scope="col">调度</th></tr>`+
A.map(function(a){return `<tr><td data-label="名称">${esc(a.name)}</td><td data-label="调度" class="subtle">${esc(a.rrule||'(单次/未设)')}</td></tr>`}).join('')+'</table>':'')+
(P.length?`<details style="margin-top:8px"><summary class="subtle">已停用 ${P.length} 条（展开查看）</summary>
<table style="margin-top:8px"><tr><th scope="col">名称</th><th scope="col">状态</th></tr>`+
P.map(function(a){return `<tr><td data-label="名称">${esc(a.name)}</td><td data-label="状态" class="subtle">${esc(a.status)}</td></tr>`}).join('')+'</table></details>':'');
document.getElementById('opsTask').innerHTML=o.tasks.length?`<table><tr><th scope="col">任务</th><th scope="col">状态</th></tr>`+
o.tasks.map(function(t){return `<tr><td data-label="任务">${esc(t.name)}</td><td data-label="状态" class="subtle">${esc(t.state)}</td></tr>`}).join('')+'</table>':empty('未采集到计划任务');
document.getElementById('opsSvc').innerHTML=o.services.length?svcTable(o.services):empty('未配置服务探针');
const on=o.services.filter(function(s){return s.online}).length;
document.getElementById('ovSvc').innerHTML=o.services.length?
`<div class="subtle">${on} / ${o.services.length} 项服务在线</div><div style="margin-top:8px">`+svcTable(o.services)+'</div>'
:empty('未配置服务探针');
document.getElementById('opsRep').innerHTML=o.reports.length?`<table><tr><th scope="col">报告</th><th scope="col">类型</th><th scope="col">时间</th></tr>`+
o.reports.map(function(r){return `<tr><td data-label="报告">${esc(r.name)}</td><td data-label="类型" class="subtle">${esc(r.dir)}</td><td data-label="时间" class="subtle">${esc(r.mtime)}</td></tr>`}).join('')+'</table>':empty('暂无报告','每周体检将在周日 09:00 自动生成')}
loadStats();loadOps();
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
