# -*- coding: utf-8 -*-
"""panel.py — 零依赖记忆面板（v0.7.0）：只读仪表盘 + 审核 + 浏览/投稿/索引治理。

能力：
- GET  /            → 内嵌 HTML 仪表盘（dark 主题，fetch JSON 接口）
- GET  /api/stats   → 镜像统计（总数 + status/project/tier 分布）
- GET  /api/queue   → candidate 审核队列（promoted/rejected 一键操作）
- GET  /api/audit?n=→ 审计日志 tail N
- GET  /api/search?q=&project=&k= → hybrid_recall（语义失败自动回落 TF-IDF）
- GET  /api/browse?page=&limit= → 全量记忆浏览（分页，按更新时间倒序）
- GET  /api/index_status → 语义索引 vs 镜像篇数
- GET  /api/activity → 记忆活跃度热力（updated 月度分桶 + 窗口外数 + 最新一篇）
- GET  /api/exposure?days= → 曝光分布（近 N 天 top + 未曝光面，AMV-P1 观测面）
- POST /api/transition {path, to} → governance.transition（仅 candidate→promoted /
  candidate→rejected，路径校验在 mem_root 内，防穿越）
- POST /api/submit {title, content, tier, project} → 面板投稿到 staging 投稿位
- POST /api/reindex → 一键重建语义索引

安全：仅监听 127.0.0.1；transition 白名单（candidate 起点）；数据无关（路径注入）。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .audit import NullAudit
from .governance import review_queue, transition
from .panel_ops import (
    _activity,
    _audit_tail,
    _browse,
    _exposure,
    _index_status,
    _json,
    _read_frontmatter,
    _reindex,
    _submit_note,
    collect_stats,
)
from .recall import MemoryRecall

# 面板投稿位（与 WorkBuddy 投稿契约一致，TRAE 端 staging_ingest.py 熔合）
def _resolve_staging_root() -> Path:
    """面板投稿位根：可选本地 config（gitignored 的 config.py）→ 环境变量 → 中性默认。

    真实部署路径不入库；本仓库开源不泄个人绝对路径。
    """
    try:
        from config import STAGING_ROOT  # type: ignore
        return Path(STAGING_ROOT)
    except Exception:
        return Path(os.environ.get("MEMPIPELINE_STAGING") or "runtime/staging")


STAGING_ROOT = _resolve_staging_root()

try:
    from .semantic import SemanticIndex, hybrid_recall
    _HAS_SEMANTIC = True
except Exception:  # 语义层可选（如依赖异常时面板仍可用）
    _HAS_SEMANTIC = False


_PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>mempipeline 工作台</title>
<style>
:root{
--color-canvas:#07080a;--color-canvas-gradient:#0c0e10;
--color-surface-1:rgba(13,14,16,0.78);--color-surface-2:rgba(22,24,28,0.72);--color-surface-3:rgba(32,35,40,0.78);
--color-glass:rgba(255,255,255,0.06);--color-glass-border:rgba(255,255,255,0.12);
--color-hairline:rgba(255,255,255,0.08);--color-hairline-soft:#242728;--color-hairline-strong:rgba(255,255,255,0.18);
--color-field-border:#6b7684;
--color-ink:#f4f4f6;--color-ink-body:#cdcdcd;--color-ink-muted:#9c9c9d;--color-ink-subtle:#8a8b8d;
--color-brand:#22d3ee;--color-brand-hover:#67e8f9;--color-brand-active:#06b6d4;--color-brand-soft:rgba(34,211,238,0.16);
--color-accent:#a78bfa;--color-success:#59d499;--color-warning:#ffc533;--color-danger:#ff6161;--color-info:#57c1ff;
--font-sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei","Hiragino Sans GB",Inter,sans-serif;
--font-mono:ui-monospace,"SF Mono","Cascadia Code",Consolas,monospace;
--radius-control:8px;--radius-card:18px;--radius-container:14px;
--shadow-glass:0 10px 40px rgba(0,0,0,0.42),0 1px 0 rgba(255,255,255,0.06) inset;
--shadow-focus:0 0 0 2px rgba(34,211,238,0.5);--shadow-overlay:0 16px 48px rgba(0,0,0,0.6);
--z-sticky:100;
}
*{box-sizing:border-box}
body{background:radial-gradient(1400px 700px at 85% -10%,rgba(34,211,238,0.07),transparent 55%),radial-gradient(1100px 600px at 5% 120%,rgba(129,140,248,0.06),transparent 45%),var(--color-canvas);color:var(--color-ink-body);font-family:var(--font-sans);font-size:13px;line-height:1.6;margin:0}
.app{display:flex;min-height:100vh}
.sidebar{width:220px;flex:0 0 220px;background:linear-gradient(180deg,var(--color-surface-1),rgba(8,9,11,0.88));border-right:1px solid var(--color-hairline);padding:18px 0;position:sticky;top:0;height:100vh;overflow-y:auto;backdrop-filter:blur(22px)}
.sidebar__brand{padding:0 18px 18px;border-bottom:1px solid var(--color-hairline);margin-bottom:12px;display:flex;align-items:center;gap:12px}
.sidebar__title{font-size:16px;font-weight:700;line-height:1.4;letter-spacing:-0.02em;color:var(--color-ink)}
.sidebar__sub{font-size:11px;line-height:1.5;color:var(--color-ink-subtle);margin-top:2px}
.nav{padding:0 10px}
.nav-group-label{font-size:11px;color:var(--color-ink-subtle);padding:12px 10px 4px;letter-spacing:0.01em}
.nav-item{display:flex;align-items:center;width:100%;min-height:46px;padding:0 14px;margin-bottom:8px;border:0;border-radius:var(--radius-control);background:transparent;color:var(--color-ink-muted);font-family:inherit;font-size:16px;font-weight:700;text-align:left;cursor:pointer;transition:background 140ms ease-out,color 140ms ease-out;gap:12px}
.nav-item:hover{background:var(--color-surface-3);color:var(--color-ink)}
.nav-item.is-active{background:var(--color-surface-3);color:var(--color-ink);box-shadow:inset 2px 0 0 var(--color-brand)}
.main{flex:1;min-width:0;padding:0 0 48px}
.container{max-width:1280px;margin:0 auto;padding:0 24px}
.page-head{margin-bottom:24px;padding-bottom:16px;border-bottom:1px solid var(--color-hairline)}
.display{font-size:24px;font-weight:700;line-height:1.25;letter-spacing:-0.03em;color:var(--color-ink);margin:0}
.page-sub{font-size:12px;color:var(--color-ink-muted);margin:4px 0 0}
.view{display:none}.view.is-active{display:block}
.h1{font-size:15px;font-weight:700;line-height:1.33;letter-spacing:-0.01em;color:var(--color-ink);margin:28px 0 12px;display:flex;align-items:center;gap:8px}
.h1::before{content:"";width:4px;height:14px;border-radius:2px;background:var(--color-brand)}
.kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:18px;margin-bottom:28px}
.kpi{background:linear-gradient(135deg,rgba(34,211,238,0.16),rgba(34,211,238,0.02));border:1px solid var(--color-glass-border);border-radius:var(--radius-card);padding:16px;box-shadow:var(--shadow-glass);backdrop-filter:blur(10px);position:relative;overflow:hidden;transition:transform 160ms ease-out}
.kpi:hover{transform:translateY(-2px)}
.kpi-grid .kpi:nth-child(2){background:linear-gradient(135deg,rgba(167,139,250,0.16),rgba(167,139,250,0.02))}
.kpi-grid .kpi:nth-child(3){background:linear-gradient(135deg,rgba(89,212,153,0.16),rgba(89,212,153,0.02))}
.kpi-grid .kpi:nth-child(4){background:linear-gradient(135deg,rgba(255,158,64,0.16),rgba(255,158,64,0.02))}
.kpi-grid .kpi:nth-child(n+5){background:linear-gradient(135deg,rgba(244,114,182,0.16),rgba(244,114,182,0.02))}
.kpi::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--color-ink-subtle);opacity:.6}
.kpi--ok::before{background:var(--color-success)}
.kpi--warn::before{background:var(--color-warning)}
.kpi--error::before{background:var(--color-danger)}
.kpi__label{font-size:12px;color:var(--color-ink-muted);line-height:1.5}
.kpi__value{font-size:28px;font-weight:800;line-height:1.25;letter-spacing:-0.02em;color:var(--color-ink);font-variant-numeric:tabular-nums;margin-top:4px}
.kpi--ok .kpi__value{color:var(--color-success)}
.kpi--warn .kpi__value{color:var(--color-warning)}
.kpi--error .kpi__value{color:var(--color-danger)}
.card{background:var(--color-glass);border:1px solid var(--color-glass-border);border-radius:var(--radius-card);padding:18px;margin-bottom:18px;box-shadow:var(--shadow-glass);backdrop-filter:blur(10px)}
table{width:100%;border-collapse:collapse;font-size:15px}
th{text-align:left;font-size:13px;font-weight:700;color:var(--color-ink-muted);padding:12px;border-bottom:1px solid var(--color-hairline-strong);text-transform:uppercase;letter-spacing:.03em}
td{padding:12px;border-bottom:1px solid var(--color-hairline);vertical-align:top}
tr:last-child td{border-bottom:0}
tr:hover td{background:rgba(255,255,255,0.03)}
.btn{display:inline-flex;align-items:center;justify-content:center;min-height:36px;min-width:36px;padding:0 14px;border-radius:var(--radius-control);font-family:inherit;font-size:14px;font-weight:600;border:1px solid transparent;cursor:pointer;transition:background 120ms ease-out,border-color 120ms ease-out,color 120ms ease-out}
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
.input{height:38px;background:var(--color-surface-2);border:1px solid var(--color-field-border);border-radius:var(--radius-control);padding:0 12px;color:var(--color-ink);font-family:inherit;font-size:14px;min-width:240px}
.input::placeholder{color:var(--color-ink-subtle)}
.input:focus{outline:none;border-color:var(--color-brand);box-shadow:var(--shadow-focus)}
.search-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.textarea{width:100%;min-height:120px;background:var(--color-surface-2);border:1px solid var(--color-field-border);border-radius:var(--radius-control);padding:10px 12px;color:var(--color-ink);font-family:var(--font-sans);font-size:14px;line-height:1.6;resize:vertical}
.textarea::placeholder{color:var(--color-ink-subtle)}
.textarea:focus{outline:none;border-color:var(--color-brand);box-shadow:var(--shadow-focus)}
.form-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.flow{display:flex;align-items:stretch;gap:0;flex-wrap:wrap;padding:8px 0}
.flow__node{background:var(--color-surface-2);border:1px solid var(--color-hairline);border-radius:var(--radius-control);padding:10px 14px;min-width:96px;text-align:center}
.flow__node b{display:block;font-size:22px;color:var(--color-ink);font-variant-numeric:tabular-nums}
.flow__node span{font-size:12px;color:var(--color-ink-muted)}
.flow__arrow{align-self:center;color:var(--color-ink-subtle);padding:0 6px;font-size:14px}
.bar{display:inline-block;height:8px;background:linear-gradient(90deg,var(--color-brand),var(--color-success));border-radius:999px;vertical-align:middle;margin-right:8px;min-width:2px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px;vertical-align:middle}
.dot--up{background:var(--color-success);box-shadow:0 0 6px var(--color-success)}
.dot--down{background:var(--color-ink-subtle)}
.badge{display:inline-flex;align-items:center;height:22px;padding:0 10px;border-radius:999px;font-size:12px;font-weight:600}
.badge--brand{background:var(--color-brand-soft);color:var(--color-brand)}
.badge--semantic{background:rgba(167,139,250,0.12);color:var(--color-accent)}
.subtle{font-size:12px;color:var(--color-ink-subtle)}
.num{font-family:var(--font-mono);font-variant-numeric:tabular-nums;font-size:14px;color:var(--color-ink-subtle)}
.empty{padding:48px 16px;text-align:center;color:var(--color-ink-muted)}
.empty__hint{font-size:12px;color:var(--color-ink-subtle);margin-top:4px}
.hit{padding:10px 0;border-bottom:1px solid var(--color-hairline)}
.hit:last-child{border-bottom:0}
details summary{cursor:pointer;padding:4px 0}
:focus-visible{outline:2px solid var(--color-brand);outline-offset:2px;border-radius:4px}
:focus:not(:focus-visible){outline:none}
.btn:focus-visible,.nav-item:focus-visible{outline:none;box-shadow:var(--shadow-focus)}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media (prefers-reduced-motion:reduce){*{transition-duration:.01ms!important;animation-duration:.01ms!important}}
@media (max-width:1279px){.container{padding:0 16px}}
@media (max-width:1100px){.kpi-grid{grid-template-columns:repeat(2,1fr)}}
@media (max-width:1023px){
.sidebar{width:64px;flex:0 0 64px;padding:14px 0}
.sidebar__brand{flex-direction:column;gap:6px;padding:0 10px 14px}
.sidebar__title,.sidebar__sub{display:none}
.nav-group-label{display:none}
.nav-item{justify-content:center;padding:0;font-size:0;gap:0}
}
@media (max-width:767px){
.app{display:block}
.sidebar{position:fixed;left:0;right:0;bottom:0;top:auto;width:auto;height:auto;flex:none;border-right:0;border-top:1px solid var(--color-hairline);background:var(--color-surface-2);padding:8px;z-index:var(--z-sticky)}
.nav{display:flex;gap:8px;padding:0}
.nav-item{flex:1;min-height:44px;margin-bottom:0}
.nav-item.is-active{box-shadow:inset 0 -2px 0 var(--color-brand)}
.main{padding:16px 0 96px}
.page-head{padding-bottom:12px;margin-bottom:16px}
.kpi-grid{grid-template-columns:1fr 1fr}
table,tbody,tr,td{display:block;width:100%}
thead{display:none}
tr{border-bottom:1px solid var(--color-hairline);padding:8px 0}
tr:last-child{border-bottom:0}
td{border:0;padding:2px 0;display:flex;gap:8px;align-items:baseline}
td::before{content:attr(data-label);flex:0 0 76px;color:var(--color-ink-subtle);font-size:13px}
.search-row .input{min-width:0;flex:1 1 100%}
}

</style>
</head>
<body>
<div class="app">
<aside class="sidebar">
<div class="sidebar__brand">
<div class="sidebar__title">mempipeline</div>
<div class="sidebar__sub">本地记忆系统 v0.7.0</div>
</div>
<nav class="nav" aria-label="工作台视图切换">
<div class="nav-group-label">记忆</div>
<div role="tablist" aria-orientation="vertical">
<button id="nb-overview" class="nav-item is-active" role="tab" aria-selected="true" aria-controls="view-overview" data-short="概览" onclick="view('overview')">概览</button>
<button id="nb-memory" class="nav-item" role="tab" aria-selected="false" aria-controls="view-memory" data-short="记忆" onclick="view('memory')">记忆</button>
<button id="nb-governance" class="nav-item" role="tab" aria-selected="false" aria-controls="view-governance" data-short="治理" onclick="view('governance')">治理</button>
</div>
</nav>
</aside>

<main class="main">
<div class="container">
<header class="page-head">
<h1 class="display" id="pageTitle">概览</h1>
<p class="page-sub" id="pageSub">记忆资产总览 · 运维监控已迁至中枢工作台 :8791</p>
</header>

<section id="view-overview" class="view is-active" role="tabpanel" aria-labelledby="nb-overview" tabindex="0">
<div class="kpi-grid" id="kpi" aria-live="polite"></div>
<h2 class="h1">分布明细</h2>
<div class="card" id="stats" aria-live="polite">加载中…</div>
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

<section id="view-governance" class="view" role="tabpanel" aria-labelledby="nb-governance" tabindex="0">
<h2 class="h1">治理流程</h2>
<div class="card" id="govFlow" aria-live="polite">加载中…</div>
<h2 class="h1">语义索引</h2>
<div class="card" id="idxStatus" aria-live="polite">加载中…</div>
<h2 class="h1">面板投稿（写入 staging，经治理入镜像）</h2>
<div class="card">
<div class="form-row">
<label for="sub-title" class="sr-only">投稿标题</label>
<input id="sub-title" class="input" placeholder="标题" style="flex:2;min-width:200px">
<label for="sub-tier" class="sr-only">记忆层级</label>
<select id="sub-tier" class="input" style="flex:0 0 110px;padding:0 8px">
<option value="long">长期</option><option value="medium" selected>中期</option>
</select>
</div>
<label for="sub-content" class="sr-only">投稿内容</label>
<textarea id="sub-content" class="textarea" placeholder="投稿内容（Markdown）"></textarea>
<div class="btn-row" style="margin-top:8px">
<button class="btn btn--primary" onclick="submitNote()">投稿</button>
<span id="sub-msg" class="subtle" aria-live="polite"></span>
</div>
</div>
<h2 class="h1">全部记忆</h2>
<div class="card" id="browse" aria-live="polite">加载中…</div>
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
const VIEWS=['overview','memory','governance'];
const META={overview:['概览','记忆资产总览 · 运维监控已迁至中枢工作台 :8791'],memory:['记忆','审核队列、语义检索与审计记录'],governance:['治理','流程可视化、语义索引与面板投稿']};
function view(n){VIEWS.forEach(function(k){
document.getElementById('view-'+k).classList.toggle('is-active',k===n);
const b=document.getElementById('nb-'+k);
b.classList.toggle('is-active',k===n);
b.setAttribute('aria-selected',k===n?'true':'false');});
document.getElementById('pageTitle').textContent=META[n][0];
document.getElementById('pageSub').textContent=META[n][1];
if(n==='memory'){loadQueue();loadAudit()}
if(n==='governance'){loadGov()}}
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
let browsePage=1;
async function loadGov(){const s=await j('/api/stats');const st=s.by_status||{};
const flow=document.getElementById('govFlow');
flow.innerHTML=`<div class="flow">
<div class="flow__node"><b>${st.candidate||0}</b><span>候选 candidate</span></div>
<div class="flow__arrow">→</div>
<div class="flow__node"><b>${st.promoted||0}</b><span>晋升 promoted</span></div>
<div class="flow__node"><b>${st.rejected||0}</b><span>拒绝 rejected</span></div>
</div>
<div class="subtle">活跃 ${st.active||0} 篇 · 待审 ${s.queue} 篇 · 进入治理流程的候选可在此晋升或拒绝</div>`;
const ix=await j('/api/index_status');
const id=document.getElementById('idxStatus');
id.innerHTML=`<div class="form-row">
<span class="subtle">已索引 ${ix.indexed<0?'未启用':ix.indexed} / 镜像 ${ix.mirror} 篇${ix.gap!=null?(ix.gap>0?' · <b style="color:var(--color-warning)">差 ${ix.gap} 篇</b>':' · 已同步'):''}</span>
</div>
<button class="btn btn--ghost" onclick="reindex()">一键重建索引</button>
<span id="idx-msg" class="subtle" aria-live="polite"></span>`;
loadBrowse(1)}
async function loadBrowse(page){browsePage=page;const b=await j('/api/browse?page='+page+'&limit=50');
const el=document.getElementById('browse');
el.innerHTML=b.rows.length?`<div class="subtle" style="margin-bottom:8px">共 ${b.total} 篇 · 第 ${b.page} 页</div>`+
`<table><tr><th scope="col">标题</th><th scope="col">层级</th><th scope="col">状态</th><th scope="col">更新时间</th></tr>`+
b.rows.map(function(r){return `<tr><td data-label="标题">${esc(r.title)}</td><td data-label="层级" class="subtle">${esc(r.tier)}</td><td data-label="状态" class="subtle">${esc(r.status)}</td><td data-label="更新时间" class="subtle">${esc(r.updated||'—')}</td></tr>`}).join('')+'</table>'+
`<div class="btn-row" style="margin-top:8px">${b.page>1?'<button class="btn btn--ghost" onclick="loadBrowse('+(b.page-1)+')">上一页</button>':''}${b.page*50<b.total?'<button class="btn btn--ghost" onclick="loadBrowse('+(b.page+1)+')">下一页</button>':''}</div>`
:empty('暂无记忆','投稿或写入后会出现在这里')}
async function submitNote(){const msg=document.getElementById('sub-msg');
msg.textContent='提交中…';
const r=await j('/api/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:document.getElementById('sub-title').value,content:document.getElementById('sub-content').value,tier:document.getElementById('sub-tier').value})});
msg.textContent=r.ok?('已写入 '+r.path.split('/').pop()):('失败：'+r.error);
if(r.ok){document.getElementById('sub-title').value='';document.getElementById('sub-content').value='';loadGov()}}
async function reindex(){const msg=document.getElementById('idx-msg');
msg.textContent='重建中（约数十秒，请勿关闭页面）…';
const r=await j('/api/reindex',{method:'POST'});
msg.textContent=r.ok?('重建完成：已索引 '+r.indexed+' 篇'):('失败：'+r.error);
loadGov()}
loadStats();
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    mem_root: Path = Path(".")
    audit_log: Path | None = None
    semantic_index: object | None = None
    tfidf_index: object | None = None  # TF-IDF 倒排快路径（MemoryRecall 注入）
    audit: object | None = None  # 真实 AuditBackend（面板晋升写审计）
    access_log: object | None = None  # P1 曝光 sidecar（AccessLog 实例，None=不打点）
    trust_rank: bool = False  # P0 信任降权开关（默认关，保持既有召回行为）
    _trusted: frozenset[str] = None  # 缺省在 _trust_of 内取 DEFAULT_TRUSTED_AGENTS

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
            self.send_header("Cache-Control", "no-store")
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
        elif path == "/api/search":
            q = qs.get("q", [""])[0]
            proj = qs.get("project", [None])[0]
            k = int(qs.get("k", ["8"])[0])
            if _HAS_SEMANTIC and self.semantic_index is not None:
                try:
                    res = hybrid_recall(q, k=k, mem_root=self.mem_root,
                                        index=self.semantic_index,
                                        memory=MemoryRecall(
                                            self.mem_root,
                                            projects=[proj] if proj else None,
                                            index=self.tfidf_index),
                                        projects=[proj] if proj else None,
                                        trust_rank=self.trust_rank,
                                        trusted_agents=self._trusted)
                except Exception:
                    res = self._tfidf(q, k, proj)
            else:
                res = self._tfidf(q, k, proj)
            # P1 曝光打点（AMV-P1）：面板搜索是人真正看到结果的唯一出口，
            # 只记这里；golden/内部扫描不算曝光。sidecar 失败不阻断响应。
            if self.access_log is not None:
                try:
                    self.access_log.record([p for p, _ in res], source="panel_search")
                except Exception:
                    pass
            _json(self, [{"path": p, "score": round(s, 4)} for p, s in res])
        elif path == "/api/browse":
            page = int(qs.get("page", ["1"])[0])
            limit = min(int(qs.get("limit", ["50"])[0]), 200)
            _json(self, _browse(self.mem_root, page, limit))
        elif path == "/api/index_status":
            _json(self, _index_status(self.semantic_index, self.mem_root))
        elif path == "/api/activity":
            _json(self, _activity(self.mem_root))
        elif path == "/api/exposure":
            days = min(max(int(qs.get("days", ["30"])[0]), 1), 365)
            _json(self, _exposure(self.access_log, self.mem_root, days))
        else:
            _json(self, {"error": "not found"}, 404)

    def _tfidf(self, q: str, k: int, proj: str | None) -> list:
        m = MemoryRecall(self.mem_root, projects=[proj] if proj else None,
                         index=self.tfidf_index)
        hits = m.recall(q, k)
        if self.trust_rank:
            from .trust_rank import rank_with_trust
            hits = rank_with_trust(hits, self._trust_of, k=k)
        return [(p, 1.0 / (i + 1)) for i, (p, _) in enumerate(hits)]

    def _trust_of(self, path: str) -> str:
        from .protocol import DEFAULT_TRUSTED_AGENTS, TRUST_UNKNOWN
        from .trust_rank import trust_of_path
        trusted = self._trusted if self._trusted is not None else DEFAULT_TRUSTED_AGENTS
        try:
            return trust_of_path(path, trusted)
        except Exception:
            return TRUST_UNKNOWN

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8")) \
                if length else {}
        except Exception:
            _json(self, {"error": "bad request"}, 400)
            return
        if parsed.path == "/api/transition":
            self._post_transition(data)
        elif parsed.path == "/api/submit":
            self._post_submit(data)
        elif parsed.path == "/api/reindex":
            _json(self, _reindex(self.semantic_index, self.mem_root))
        else:
            _json(self, {"error": "not found"}, 404)

    def _post_transition(self, data: dict) -> None:
        path = Path(data.get("path", ""))
        to = data.get("to", "")
        try:
            path.resolve().relative_to(self.mem_root.resolve())
        except Exception:
            _json(self, {"error": "path outside mem_root"}, 400)
            return
        st, frm = transition(path, to, self.audit or NullAudit(), source="panel")
        ok = st in ("wrote", "skipped") and frm == "candidate"
        _json(self, {"ok": ok, "status": st, "from": frm})

    def _post_submit(self, data: dict) -> None:
        r = _submit_note(data.get("title", ""), data.get("content", ""),
                         data.get("tier", "medium"),
                         data.get("project", ""), STAGING_ROOT)
        _json(self, r, 200 if r.get("ok") else 400)


def serve(mem_root: Path, audit_log: Path | None = None,
          index: object | None = None, port: int = 8790,
          host: str = "127.0.0.1",
          audit: object | None = None,
          tfidf_index: object | None = None,
          access_log_db: Path | None = None) -> None:
    """起面板服务（仅本机）。Ctrl+C 停止。

    access_log_db：P1 曝光 sidecar 的 SQLite 路径（None=不打点，行为同旧版）。
    """
    _Handler.mem_root = mem_root
    _Handler.audit_log = audit_log
    _Handler.semantic_index = index
    _Handler.tfidf_index = tfidf_index
    _Handler.audit = audit
    _Handler.access_log = None
    if access_log_db is not None:
        from .access_log import AccessLog
        _Handler.access_log = AccessLog(Path(access_log_db))
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
    p.add_argument("--tfidf-db", default=None, help="TF-IDF 倒排索引 SQLite（可选，P1-A2 快路径）")
    p.add_argument("--access-log-db", default=None,
                   help="P1 曝光 sidecar SQLite（可选；缺省不打点，生产由 launch_panel 注入）")
    p.add_argument("--port", type=int, default=8790)
    a = p.parse_args(argv)
    idx = None
    if _HAS_SEMANTIC and a.index_db:
        idx = SemanticIndex(Path(a.index_db))
    tidx = None
    if a.tfidf_db:
        from .recall import TFIDFIndex
        tidx = TFIDFIndex(Path(a.tfidf_db))
    audit = None
    if a.audit_log and a.mem_root:
        from .audit import FileAudit
        audit = FileAudit(Path(a.audit_log),
                          Path(a.audit_log).with_suffix(".manifest.json"),
                          Path(a.mem_root))
    serve(Path(a.mem_root), Path(a.audit_log) if a.audit_log else None,
          idx, port=a.port, audit=audit, tfidf_index=tidx,
          access_log_db=Path(a.access_log_db) if a.access_log_db else None)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
