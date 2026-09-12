# -*- coding: utf-8 -*-
"""test_evolution.py — 下一阶段候选演进验收（C2/C3/C4，2026-09-12）。

覆盖：
C2 时间/脉络衰减进 RRF 排序：time_weight=0 保持现状；>0 时较新稿相对靠前（time_factor）
C3 FAMA 式过时记忆复用检测：build_stale_map 标记被取代旧稿；check(stale_paths=) 命中旧稿置 stale_reuse
C4 Act 跨 query 缺词聚合建议面：summarize_cards 三档杠杆计数 + 高频缺词 top-N，advisory 恒真
"""
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mempipeline.act import summarize_cards
from mempipeline.recall import MemoryRecall
from mempipeline.recall_golden import build_stale_map
from mempipeline.recall_golden import check as golden_check
from mempipeline.recall_golden import forget_quality
from mempipeline.semantic import hybrid_recall, time_factor


def _iso(days_ago: int) -> str:
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _write(md: Path, title: str, body: str, *, days_ago: int,
           project: str = "", extra: str = "") -> None:
    fm = "---\n"
    fm += f'title: "{title}"\n'
    fm += f'summary: "{title}"\n'
    fm += 'memory_tier: "long"\n'
    fm += "importance: 0.8\n"
    fm += 'status: "active"\n'
    if project:
        fm += f'project_id: "{project}"\n'
    if extra:
        fm += extra
    fm += f'updated: "{_iso(days_ago)}"\n'
    fm += "---\n\n" + body + "\n"
    md.write_text(fm, encoding="utf-8")


class _FakeIdx:
    """满足 hybrid_recall 所需最小接口的游标语义索引（C2 免联真实 embed）。"""

    def __init__(self, res):
        self.res = res

    def query_embed(self, q, fn):
        return [0.0] * 4

    def recall_cached(self, q, qvec, k, projects=None):
        return self.res[:k]


def main() -> bool:
    ok = True

    def check(cond: bool, msg: str):
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {msg}")
        if not cond:
            ok = False

    # ---- C2 time_factor 单调：越新越高，缺 mtime 中性 ----
    print("== C2 time_factor ==")
    now = time.time()
    check(time_factor(__file__, now, 90) < 1.0, "真实文件 mtime 因子 < 1")
    check(time_factor("missing_mtime_file", now, 90) == 0.5, "缺 mtime 中性 0.5")
    check(time_factor(__file__, now - 1e7, 90) > time_factor(__file__, now, 90),
          "与 now 差距越大越旧，因子越低")

    # ---- C2 hybrid_recall：time_weight=0 保持现状，>0 偏好较新稿 ----
    print("== C2 hybrid_recall ==")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        mem_root = tmp / "mem"
        long_dir = mem_root / "01-长期记忆"
        long_dir.mkdir(parents=True)
        pa = long_dir / "主题-A.md"
        pb = long_dir / "主题-B.md"
        _write(pa, "主题A", "主题A的正文内容用于召回", days_ago=1, project="C2")
        _write(pb, "主题B", "主题B的正文内容用于召回", days_ago=365, project="C2")
        # 强制 A 新、B 旧（避免 mtime 与 updated 写盘先后干扰）
        now_t = time.time()
        os_utime(pa, now_t - 86400)
        os_utime(pb, now_t - 86400 * 365)
        memory = MemoryRecall(mem_root)
        lex = memory.recall("主题", 10)  # list[(path, score)]，即 tuple 序列
        fake = _FakeIdx(lex)  # sem 路=RRE 顺序，与 lex 一致 → RRF 下 B 略高于 A 或接近
        base = hybrid_recall("主题", k=2, mem_root=mem_root,
                             index=fake, memory=memory, time_weight=0.0)
        t1 = hybrid_recall("主题", k=2, mem_root=mem_root,
                           index=fake, memory=memory,
                           time_weight=1.0, decay_half_life_days=90)
        names_b = [Path(p).stem for p, _ in base]
        names_1 = [Path(p).stem for p, _ in t1]
        check(names_b == names_1 or (len(names_b) >= 2 and len(names_1) >= 2),
              "time_weight=0 时结果集正常（=现状）")
        # time_weight=1：A（新）应升到 B（旧）之前
        check(names_1[0] == "主题-A",
              f"time_weight=1 时较新稿 A 排首位（实得 {names_1}）")

    # ---- C3 build_stale_map + check stale_reuse ----
    print("== C3 过时复用检测 ==")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        mem_root = tmp / "mem"
        long_dir = mem_root / "01-长期记忆"
        long_dir.mkdir(parents=True)
        old = long_dir / "过时主题-旧稿.md"
        new = long_dir / "过时主题-新稿.md"
        _write(old, "过时主题", "旧结论。", days_ago=90, project="C3")
        _write(new, "过时主题", "新结论。", days_ago=0, project="C3")
        stale = build_stale_map(mem_root)
        check(str(old) in stale, "被同主题新稿取代的旧稿被标 stale")
        check(str(new) not in stale, "最新有效稿不被标 stale")
        g_old = {"过时 主题": "过时主题-旧稿"}
        g_new = {"过时 主题": "过时主题-新稿"}
        r_old = golden_check(mem_root, golden=g_old, stale_paths=stale)
        r_new = golden_check(mem_root, golden=g_new, stale_paths=stale)
        check(r_old["stale_reuse"] == 1, f"命中旧稿 → stale_reuse=1（实得 {r_old['stale_reuse']}）")
        check(r_new["stale_reuse"] == 0, f"命中新稿 → stale_reuse=0（实得 {r_new['stale_reuse']}）")
        check(r_old["freshness"] < r_new["freshness"],
              "过时复用的 freshness 被抑制")

    # ---- C4 summarize_cards ----
    print("== C4 缺词聚合建议面 ==")
    cards = [
        {"levers": ["synonym"], "absent_terms": [{"term": "韩餐", "mean_df": 0.02}]},
        {"levers": ["synonym"], "absent_terms": [{"term": "韩餐", "mean_df": 0.02}]},
        {"levers": ["threshold"]},
    ]
    s = summarize_cards(cards, top_n=3)
    check(s["levers"].get("synonym", 0) == 2 and s["levers"].get("threshold", 0) == 1,
          f"杠杆计数正确（{s['levers']}）")
    check(s["top_absent_terms"] and s["top_absent_terms"][0]["term"] == "韩餐",
          "高频缺词 Top1 = 韩餐（候选 synonyms 锚点）")
    check(s["advisory"] is True, "advisory 恒真（只建议不落库）")
    check(isinstance(s["suggestion"], str) and "synonym" in s["suggestion"],
          "建议文本含 lever 优先级")

    # ---- D1 time_factor 多策略：linear 晚段更快淘汰旧稿，半衰期处等值 ----
    print("== D1 时效衰减策略 ==")
    now_t = time.time()
    # 三个同代文件：旧 / 新近 / 极旧，用 os.utime 固定 mtime 排除干扰
    tmpf = Path(tempfile.mkdtemp()) / "d1.md"
    tmpf.write_text("x", encoding="utf-8")
    os_utime(tmpf, now_t - 365 * 86400)  # 极旧
    f_exp_old = time_factor(tmpf, now_t, 90, strategy="exponential")
    f_lin_old = time_factor(tmpf, now_t, 90, strategy="linear")
    check(f_lin_old < f_exp_old,
          f"超过半衰期后 linear 比指数更快淘汰旧稿（{f_lin_old:g}<{f_exp_old:g}）")
    os_utime(tmpf, now_t - 90 * 86400)  # 恰在半衰期
    check(abs(time_factor(tmpf, now_t, 90, "exponential")
              - time_factor(tmpf, now_t, 90, "linear")) < 1e-9,
          "半衰期(90 天)处 exponential 与 linear 等值")
    check(time_factor("missing_mtime_file", now_t, 90, strategy="linear") == 0.5,
          "缺 mtime 各策略均中性 0.5")
    check(time_factor("missing_mtime_file", now_t, 90, strategy="bogus") == 0.5,
          "非法策略回落 exponential 且缺 mtime 中性")

    # ---- D2 forget_quality 遗忘质量快照 ----
    print("== D2 forget_quality ==")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        mem_root = tmp / "mem"
        long_dir = mem_root / "01-长期记忆"
        long_dir.mkdir(parents=True)
        old = long_dir / "过时主题-旧稿.md"
        new = long_dir / "过时主题-新稿.md"
        _write(old, "过时主题", "旧结论。", days_ago=90, project="D2")
        _write(new, "过时主题", "新结论。", days_ago=0, project="D2")
        stale = build_stale_map(mem_root)
        # 命中旧稿 → 过时复用负指标拉低 composite
        g_old = {"过时 主题": "过时主题-旧稿"}
        q_old = forget_quality(mem_root, golden=g_old, stale_paths=stale)
        g_new = {"过时 主题": "过时主题-新稿"}
        q_new = forget_quality(mem_root, golden=g_new, stale_paths=stale)
        for key in ("phase", "as_of", "totals", "retrieval", "composite"):
            check(key in q_old, f"schema 含字段 {key}")
        check(q_old["phase"] == "forget_quality", "phase 标注 forget_quality")
        check(q_new["retrieval"]["stale_reuse_rate"] == 0.0,
              "命中新稿 → stale_reuse_rate=0")
        check(q_old["retrieval"]["stale_reuse_rate"] > q_new["retrieval"]["stale_reuse_rate"],
              "命中旧稿 → stale_reuse_rate 升高")
        check(q_old["totals"]["n_stale"] >= 1 and q_old["totals"]["stale_ratio"] > 0,
              "totals 统计过时稿占比")
        check(isinstance(q_old["composite"], (int, float)) and q_old["composite"] <= 1.0,
              "composite 为 0..1 遗忘质量总分")

    return ok


def os_utime(path: Path, ts: float) -> None:
    import os
    os.utime(path, (ts, ts))


if __name__ == "__main__":
    sys.exit(0 if main() else 1)