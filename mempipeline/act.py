# -*- coding: utf-8 -*-
"""act.py — P3-② 召回退化诊断与 Act 建议面（Act 默认关，人类触发）。

铁律：Act 默认关。本模块只「显影退化在哪、该看哪里」，**不自动改参、不写库**。

把「肉眼只有一条 golden 未命中」的黑盒，拆成人类可执行的三档 Act 杠杆：
  - synonym：期望笔记缺失的专用词 → 提示与镜像收录用词 / synonyms 映射不一致；
  - stopword：期望笔记缺失的全库通用词（高 df、缺区分度）→ 提示查询不够聚焦；
  - threshold：期望笔记已获分但未进 top-k → 提示扩 k 或改写更聚焦查询。

词级分析与 recall.MemoryRecall 同源（_raw_terms 空格切分 + synonyms 主词归一 +
_tokens 字符 ngram），保证「诊断口径 = 召回口径」。当人类决定采纳并写回
（synonyms / 阈值配置）时，写回仍走调用方的 write_atomic + 审计，本模块永不自动改参。
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from .recall import _raw_terms, _tokens

# 全库通用词判定阈值：某 query 词的 ngram mean_df 占比 >= 该值，视为缺区分度的泛词
STOPWORD_DF = 0.3


def _norm_map(synonyms: dict | None) -> dict[str, str]:
    """把 {head: [alts]} 展成「原词 -> 主词」映射，与 MemoryRecall.__init__ 同构。"""
    norm: dict[str, str] = {}
    for head, alts in (synonyms or {}).items():
        norm[head] = head
        for a in alts:
            norm[a] = head
    return norm


def _term_mean_df(term_grams: set[str], df: Counter, n_docs: int) -> float:
    """某 query 词覆盖的 ngram 在全库的文档频率占比均值（0..1），无 ngram 返回 0。"""
    if not term_grams:
        return 0.0
    return (sum(df.get(g, 0) for g in term_grams) / len(term_grams)) / max(1, n_docs)


def diagnose_miss(query: str, expect_text: str, corpus_texts: list[str], *,
                  scored: bool = False, ranked_below_k: bool = False,
                  synonyms: dict | None = None) -> dict:
    """一条 golden 未命中的分诊（只读），返回 Act 卡。

    - query: 原查询（空格分词，同 golden）。取词用 _raw_terms + synonyms 主词归一。
    - expect_text: 期望命中笔记的全文（归一后与 corpus_texts 同口径，调用方准备）。
    - corpus_texts: 全库归一文本，用于 df（Idf 口径）与 ngram 统计。
    - scored / ranked_below_k: 期望笔记在召回打分中的表现，由调用方按 recall 结果填入：
      scored=True 表示得分>0；ranked_below_k=True 表示得分>0 但未进 top-k。
      二者皆可由便利函数 trace_recall 自动推得。
    - synonyms: 现有同义映射（口径与 recall 对齐），缺省无。
    """
    norm = _norm_map(synonyms)
    qterms = [norm.get(t, t) for t in _raw_terms(query)]
    expect_grams = set(_tokens(expect_text))
    df: Counter = Counter()
    for txt in corpus_texts:
        for g in set(_tokens(txt)):
            df[g] += 1
    n_docs = len(corpus_texts)

    card: dict = {
        "query": query,
        "never_scored": not scored,
        "threshold_lever": bool(scored and ranked_below_k),
        "absent_terms": [],
        "levers": [],
    }
    for t in qterms:
        grams = set(_tokens(t))
        if not grams or (grams & expect_grams):
            continue  # 期望笔记含该词的 ngram，正常贡献
        ratio = _term_mean_df(grams, df, n_docs)
        lever = "stopword" if ratio >= STOPWORD_DF else "synonym"
        card["absent_terms"].append({"term": t, "mean_df": round(ratio, 3), "lever": lever})
        card["levers"].append(lever)
    if card["threshold_lever"]:
        card["levers"].append("threshold")
    card["levers"] = list(dict.fromkeys(card["levers"]))  # 去重保序
    card["suggestion"] = _render(card)
    return card


def trace_recall(query: str, expect_path: str, expect_text: str, recall_fn: Callable,
                 corpus_texts: list[str], k: int = 8, *,
                 synonyms: dict | None = None) -> dict:
    """便利：对一条 golden 未命中，自动推 scored/ranked_below_k 后产出 Act 卡。

    - recall_fn(query, k_big) -> list[(path, score)]：数据注入，返回按分降序的
      候选（k_big 传足够大以包含所有获分笔记）；期望笔记不在其中即 never_scored。
    - expect_text: 期望命中笔记的全文（归一后与 corpus_texts 同口径）。
    """
    hits = recall_fn(query, len(corpus_texts) + 1)  # 取全量获分集
    idx = next((i for i, (p, _) in enumerate(hits) if p == expect_path), None)
    return diagnose_miss(query, expect_text, corpus_texts,
                         scored=idx is not None,
                         ranked_below_k=idx is not None and idx >= k,
                         synonyms=synonyms)


def _render(card: dict) -> str:
    parts: list[str] = []
    if card["never_scored"]:
        parts.append("期望笔记从未获分（命中词为 0）——需人工核对该主题在镜像的收录方式")
    if card["threshold_lever"]:
        parts.append("期望笔记已获分但未进 top-k——threshold 杠杆：扩 k 或改写更聚焦查询")
    for at in card["absent_terms"]:
        if at["lever"] == "stopword":
            parts.append(
                f"stopword 杠杆：全库通用词「{at['term']}」(mean_df={at['mean_df']}) "
                f"缺区分度，建议查询提更具体术语")
        else:
            parts.append(
                f"synonym 杠杆：期望笔记缺该专用词「{at['term']}」(mean_df={at['mean_df']})，"
                f"提示与镜像收录用词 / synonyms 映射不一致")
    return "；".join(parts) if parts else "无明显缺词，需从查询表述或阈值层复核"


# 去留效用分档阈值（A2）：效用分 0..1，越高越值得保留
RETENTION_RETAIN = 0.6
RETENTION_MERGE = 0.4


def advise_retention(importance: float, stale_days: int,
                     recurrence_count: int = 0, *,
                     decay_half_life_days: int = 90,
                     w_importance: float = 0.4, w_recency: float = 0.3,
                     w_relevance: float = 0.3) -> dict:
    """候选去留效用诊断（A2）：对陈旧候选给「保留/合并/淘汰」建议分。只显影不落库。

    启发自 Intelligent Decay 的 combine(recency, relevance, utility) 效用模型，
    但保持 Act 默认关：输出效用分与分档建议，是否执行仍人工裁决（advisory=True）。
    该效用分可作为「候选误报率」的观测面——日后与人工去留决议比对即可累计误报率。

    输入为显式标量（调用方从 governance 候选 + timegrap 脉络相关度注入，保持解耦）：
    - importance: 0..1 笔记重要度
    - stale_days: 距最近更新天数
    - recurrence_count: 主题脉络跨稿出现次数（计当前稿为 1）；>=2 表示「活脉络」
      （多次续写），于是 relevance 分量抬高。
    - decay_half_life_days: 陈旧衰减半衰期（默认 90，与 timegrap 断更阈值一致）。
    """
    recency = 0.5 ** (stale_days / max(1, decay_half_life_days))  # 指数衰减 0..1
    relevance = min(1.0, max(0.0, recurrence_count) / 3.0)  # 活脉络多稿 => 相关度升
    utility = (w_importance * max(0.0, min(1.0, importance))
               + w_recency * recency
               + w_relevance * relevance)
    if utility >= RETENTION_RETAIN:
        action, reason = "retain", "效用分高，建议保留并续写/刷新"
    elif utility >= RETENTION_MERGE:
        action, reason = "merge_candidate", "效用中等，建议与同主题其他稿合并后再评"
    else:
        action, reason = "dismiss_candidate", "效用偏低，建议归档/降权或淘汰"
    return {
        "utility": round(utility, 3),
        "recency": round(recency, 3),
        "relevance": round(relevance, 3),
        "components": {"importance": importance, "stale_days": stale_days,
                       "recurrence_count": recurrence_count},
        "action": action,
        "reason": reason,
        "advisory": True,
    }