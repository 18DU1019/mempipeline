"""读端注入面（二期 A 项，2026-09-17）：规则层常驻 + 混排 + 冷启动注入等价物。

评分正本 = 公司机 A 脚本版 recall.py 字面通道常数（ARCHITECTURE.md §8 A 行）：
    score(t) = W_R·exp(-LAMBDA·Δdays) + W_I·importance + W_S·rel
常数 W_R/LAMBDA/W_I/W_S = 0.4/0.05/0.4/0.2、LAYER_IMP、RULE_REL_BOOST = 2.5、
ABSTAIN 线 = W_S·0.5 全部逐字面复刻，禁止调参（改常数即破坏跨机一致性验收）。

范围外（口径红线）：语义层双口径、版本链 boost、JUDGE overlay、域 MOC 导航层——
读端无语义引擎与 MOC 生成物，硬抄 = 假一致。

规则层语义（正本 docstring 摘录）：01-规则为常驻硬约束，无活跃度语义——
Recency 恒 0（否则 updated 新鲜度绕过 RULE_REL_BOOST 的相关性闸门霸榜）；
rel 达实质重叠线（W_S·0.5）时 ×RULE_REL_BOOST 上浮，无关时不干扰镜像排序。
长期/中期镜像 recency 封顶 0.2（不越过 rel 分量上限 W_S，防零相关当日稿
结构性淹没强相关旧稿）。activity_date 链 = last_active > created > updated
（created 免疫管线批量重写压平，updated 是落盘戳不作活跃度正源）。
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .protocol import parse_frontmatter
from .recall import _tokens, disclose_l0, is_redacted, scan_tier_dirs

W_R, LAMBDA, W_I, W_S = 0.4, 0.05, 0.4, 0.2  # 正本字面常数，勿改
LAYER_IMP = {"01-长期记忆": 0.9, "02-中期记忆": 0.6, "01-规则": 0.9}
RULE_REL_BOOST = 2.5  # 规则层相关性 boost（rel >= W_S·0.5 生效）
ABSTAIN_REL = W_S * 0.5  # 弃权线：最高 rel 低于此值时提示无实质重叠
RECENCY_CAP = 0.2  # 长期/中期 recency 封顶（防淹没强相关旧稿）
RULE_REL_GATE = W_S * 0.5  # 规则 boost 的相关性闸门（与正本同源阈值）
RULE_LAYER = "01-规则"


def _fm_all(raw: str) -> dict:
    """全键 frontmatter 解析（P4：统一走 protocol.parse_frontmatter）。

    与旧实现的差异：旧实现跳过空值行（空值键不在 dict）；新解析器空值键 = ""。
    inject 读取 title/summary/tag_relevance 等活跃度链字段，调用点均以真值/
    get 默认值消费，"" 与缺键行为一致。
    """
    return parse_frontmatter(raw)


def _body_after_fm(raw: str) -> str:
    """剥 frontmatter 取正文（无 FM 或畸形时退回原文）。"""
    if raw.startswith("---") and raw.count("---") >= 2:
        return raw.split("---", 2)[2].strip()
    return raw.strip()


def _days_since(last_active: str) -> int | None:
    """距 last_active 的天数；空/坏格式返回 None（无 Recency 证据，不当作今天）。"""
    if not last_active:
        return None
    try:
        dt = datetime.strptime(last_active[:10], "%Y-%m-%d")
    except ValueError:
        try:
            dt = datetime.strptime(last_active[:10], "%Y%m%d")
        except ValueError:
            return None
    return max(0, (datetime.now() - dt).days)


def activity_date(fm: dict) -> str:
    """活跃度时间锚，单一口径：last_active > created > updated，全缺返回空串。"""
    return fm.get("last_active") or fm.get("created") or fm.get("updated") or ""


def collect_rules(rule_root: Path) -> list[tuple[Path, dict, str]]:
    """收集规则层平铺规则源（数据无关：目录由调用方注入，经只读挂载可达）。

    返回 (path, frontmatter, text) 列表，text = title+summary+tag_relevance+正文。
    红act 防线与包内其他扫描一致（脚本版无此滤，规则层不含秘文属异常态，
    过滤不影响正常一致性断言）。
    """
    out: list[tuple[Path, dict, str]] = []
    if not rule_root.is_dir():
        return out
    for p in sorted(rule_root.glob("*.md")):
        if not p.is_file():
            continue
        try:
            raw = p.read_text(encoding="utf-8")
        except Exception:
            continue  # G 项范式：读失败静默跳过；inject 返回面带 read_errors 计数
        if is_redacted(raw):
            continue
        fm = _fm_all(raw)
        text = " ".join([fm.get("title", ""), fm.get("summary", ""), fm.get("tag_relevance", "")])
        text = (text + " " + _body_after_fm(raw)).strip()
        out.append((p, fm, text))
    return out


def _layer_of(path: Path) -> str:
    """按路径目录段精确判层（正本同款，域 MOC 判定划为范围外）。"""
    pp = Path(path)
    for folder in LAYER_IMP:
        if folder in pp.parts:
            return folder
    return "01-长期记忆"


def _build_idf_mix(texts: list[str]) -> dict[str, float]:
    """候选集 IDF（正本 build_idf 字面复刻：log((n+1)/(c+1))+1.0，现算不落盘）。"""
    n = len(texts)
    df: dict[str, int] = {}
    for txt in texts:
        for w in set(_tokens(txt)):
            df[w] = df.get(w, 0) + 1
    return {w: math.log((n + 1) / (c + 1)) + 1.0 for w, c in df.items()}


def _rel(query: str, text: str, idf: dict[str, float]) -> float:
    """Relevance：query 与文档的 ngram TF-IDF 重叠，按 query 侧归一（0..1）。"""
    if not query or not text:
        return 0.0
    qt = set(_tokens(query))
    dt = set(_tokens(text))
    inter = qt & dt
    if not inter:
        return 0.0
    num = sum(idf.get(w, 0.0) for w in inter)
    den = sum(idf.get(w, 0.0) for w in qt)
    return min(1.0, num / den) if den else 0.0


def score_mixed(path: Path, fm: dict, text: str, query: str,
                idf: dict[str, float], layer: str | None = None) -> dict:
    """混排打分（正本 score 字面复刻，砍语义/JUDGE/MOC 分支）。

    layer=None 时按路径目录段自动判层（_layer_of，依赖规范目录名）；规则层
    目录名由调用方注入（数据无关），故 inject 对规则条目显式传 layer。
    返回 {path, layer, total, rec, imp, rel, lex}。lex 为未 boost 的原始
    字面通道值（通道证据），rel 为参与 total 的分量（规则层可能已 ×2.5）。
    """
    layer = layer or _layer_of(path)
    days = _days_since(activity_date(fm))
    rec = 0.0 if days is None else W_R * math.exp(-LAMBDA * days)
    if layer == RULE_LAYER:
        rec = 0.0  # 规则层常驻硬约束：无活跃度语义，防新鲜度绕过 boost 闸门
    else:
        rec = min(rec, RECENCY_CAP)
    imp_raw = fm.get("importance")
    imp = LAYER_IMP.get(layer, 0.5)
    if imp_raw is not None:
        try:
            imp = float(imp_raw)
        except (TypeError, ValueError):
            pass  # 正本同款解析兜底：坏 importance 回落层默认值
    lex = _rel(query, text, idf)
    rel = W_S * lex
    if layer == RULE_LAYER and rel >= RULE_REL_GATE:
        rel *= RULE_REL_BOOST  # 阈值版 boost：真相关进 Top-K，无关不干扰镜像排序
    total = rec + W_I * imp + rel
    return {"path": path, "layer": layer, "total": total, "rec": rec,
            "imp": W_I * imp, "rel": rel, "lex": lex}


def inject(query: str, mem_root: Path, rule_root: Path,
           tiers: Iterable[str] | None = None, k: int = 8,
           warmup_days: int = 14) -> dict:
    """注入等价物：规则层 + 镜像统一混排；无 query 时退化冷启动注入面。

    返回 {query, top, anchor, warmup, abstain, read_errors, cards}：
    - top：混排 total 降序前 k（每条带 score_mixed 全通道明细）；
    - anchor：top 中长期层条目（长期锚点）；
    - warmup：activity_date 落在近 warmup_days 天内的条目按新→旧序（近期预热），
      冷启动（query 为空）时的主要注入面；
    - abstain：最高字面重叠分量低于 ABSTAIN_REL（正本弃权线）时 True；
    - read_errors：镜像+规则读失败计数（G 项可见性范式）；
    - cards：B 系列（2026-09-21）接线渐进披露——top 条目的 L0 摘要卡
      （recall.disclose_l0 扩卡，score 取混排 total）。注入文案默认用卡片，
      需要细节时由消费方按 recall.disclose_l1 / disclose_l2 对卡片 path
      逐层升（懒加深，不预取）。红act 同源拒答穿透披露层（任一层拒答）；
      披露层不可用/异常时回落为 []，top/anchor/warmup 既有六面零变化，
      打分链路（全量读）不依赖披露层，行为与接线前一致。
    """
    if tiers is None:
        tiers = ("01-长期记忆", "02-中期记忆")
    read_errors = 0
    entries: list[tuple[Path, dict, str]] = []
    for tier in tiers:
        for d in scan_tier_dirs(mem_root, tier, None):
            for md in d.glob("*.md"):
                try:
                    txt = md.read_text(encoding="utf-8")
                except Exception:
                    read_errors += 1
                    continue
                if is_redacted(txt):
                    continue
                fm = _fm_all(txt)
                body = _body_after_fm(txt)
                text = " ".join([fm.get("title", ""), fm.get("summary", ""),
                                 fm.get("tag_relevance", ""), body]).strip()
                entries.append((md, fm, text))
    rules = collect_rules(rule_root)
    entries.extend(rules)
    # 规则层读失败计数：collect_rules 内部吞掉的坏档条目在此显形（重扫一遍代价低）
    scanned = {p for p, _fm, _t in rules}
    for p in sorted(rule_root.glob("*.md")) if rule_root.is_dir() else []:
        if p.is_file() and p not in scanned:
            try:
                p.read_text(encoding="utf-8")
            except Exception:
                read_errors += 1

    idf = _build_idf_mix([t for _p, _fm, t in entries])
    scored = [score_mixed(p, fm, t, query, idf,
                          layer=RULE_LAYER if p in scanned else None)
              for p, fm, t in entries]
    scored.sort(key=lambda x: x["total"], reverse=True)
    top = scored[:k]
    anchor = [s for s in top if s["layer"] == "01-长期记忆"][:3]

    # B 系列接线（2026-09-21）：top 条目默认附 L0 摘要卡（渐进披露第七面）。
    # 回落保护：披露层异常时 cards=[]，既有六面与打分链路零影响。
    try:
        cards = disclose_l0([(str(s["path"]), s["total"]) for s in top])
    except Exception:
        cards = []

    warmup: list[dict] = []
    for p, fm, _t in entries:
        age = _days_since(activity_date(fm))
        if age is not None and age <= warmup_days:
            warmup.append({"path": p, "days": age,
                           "layer": RULE_LAYER if p in scanned else _layer_of(p),
                           "activity_date": activity_date(fm)})
    warmup.sort(key=lambda x: x["days"])

    max_rel = max((s["lex"] for s in scored), default=0.0) * W_S
    return {"query": query, "top": top, "anchor": anchor, "warmup": warmup,
            "abstain": bool(query) and max_rel < ABSTAIN_REL,
            "read_errors": read_errors, "cards": cards}
