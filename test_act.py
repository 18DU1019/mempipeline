# -*- coding: utf-8 -*-
"""test_act.py — P3-② Act 建议面（仅诊断、Act 默认关）单元验证。

全部沙盒注入，不触碰真实镜像；只读断言，验证三档 Act 杠杆分类与期望缺词定位。
"""
import sys
from mempipeline import act


def _check(cond, msg):
    print(("[PASS] " if cond else "[FAIL] ") + msg)
    return cond


def main():
    ok = True

    # ---- 1. synonym 杠杆：期望笔记缺「专用词」→ 归 synonym ----
    expect_txt = "项目 汇总 明细 对账 完成"
    corpus = ["项目 汇总 明细 对账 完成",
              "汇总 对账 模板 表格",
              "项目 对账 规则 说明"]
    c = act.diagnose_miss("汇总 明细 判重", expect_txt, corpus)
    ok &= _check(c["levers"] == ["synonym"],
                 f"synonym 杠杆：{c['absent_terms']} -> levers={c['levers']}")
    ok &= _check(len(c["absent_terms"]) == 1
                 and c["absent_terms"][0]["term"] == "判重"
                 and c["absent_terms"][0]["lever"] == "synonym",
                 f"缺词定位为「判重」且归 synonym")
    ok &= _check("synonym 杠杆" in c["suggestion"], "suggestion 含 synonym 杠杆文案")

    # ---- 2. stopword 杠杆：期望笔记缺「全库通用词」→ 归 stopword ----
    expect2 = "清单 明细 归档 结项"
    corpus2 = [expect2,
               "项目 甲 进度 汇报", "项目 乙 进度 汇报",
               "项目 丙 状态 标记", "项目 丁 状态 标记"]
    c2 = act.diagnose_miss("项目 清单 归档", expect2, corpus2)
    ok &= _check(c2["levers"] == ["stopword"],
                 f"stopword 杠杆：{c2['absent_terms']} -> levers={c2['levers']}")
    ok &= _check(len(c2["absent_terms"]) == 1
                 and c2["absent_terms"][0]["term"] == "项目"
                 and c2["absent_terms"][0]["lever"] == "stopword"
                 and c2["absent_terms"][0]["mean_df"] >= 0.3,
                 f"缺词定位为「项目」且归 stopword（mean_df={c2['absent_terms'][0]['mean_df']}）")
    ok &= _check("stopword 杠杆" in c2["suggestion"], "suggestion 含 stopword 杠杆文案")

    # ---- 3. threshold 杠杆：期望笔记已获分但未进 top-k ----
    c3 = act.diagnose_miss("汇总 明细", expect_txt, corpus,
                           scored=True, ranked_below_k=True)
    ok &= _check(c3["threshold_lever"] and "threshold" in c3["levers"]
                 and not c3["never_scored"],
                 f"threshold 杠杆：{c3['levers']}")

    # ---- 4. never_scored：期望笔记从未获分 ----
    c4 = act.diagnose_miss("汇总 明细", expect_txt, corpus, scored=False)
    ok &= _check(c4["never_scored"] and "threshold" not in c4["levers"],
                 "never_scored 判定正确且无 threshold")

    # ---- 5. trace_recall：自动由 recall_fn 推 scored/ranked_below_k ----
    # 5a. 期望笔记在 top-k 内 -> 非 ranked_below_k
    hits_in = [("other_a", 1.0), ("other_b", 0.9), (EXPECT, 0.5)]
    c5a = act.trace_recall("汇总 明细", EXPECT, expect_txt,
                           lambda q, kb: hits_in, corpus, k=3)
    ok &= _check(c5a["never_scored"] is False and c5a["threshold_lever"] is False,
                 "trace: 期望笔记在 top-k -> 非 threshold")
    # 5b. 期望笔记已获分但排名 >= k -> ranked_below_k
    c5b = act.trace_recall("汇总 明细", EXPECT, expect_txt,
                           lambda q, kb: hits_in, corpus, k=2)
    ok &= _check(c5b["threshold_lever"] is True,
                 "trace: 期望笔记在 top-k 外(rank>=k) -> threshold")
    # 5c. 期望笔记从未获分
    c5c = act.trace_recall("汇总 明细", EXPECT, expect_txt,
                           lambda q, kb: [("other", 1.0)], corpus, k=3)
    ok &= _check(c5c["never_scored"] is True, "trace: 期望笔记未获分 -> never_scored")

    # ---- 6. A2 候选去留效用诊断（只读、Advisory）----
    print("== A2 候选去留 ==")
    r = act.advise_retention(importance=0.9, stale_days=10, recurrence_count=2)
    ok &= _check(r["action"] == "retain" and 0.6 <= r["utility"] <= 1.0,
                 f"高重要+较新+活脉络 -> retain（utility={r['utility']}）")
    r2 = act.advise_retention(importance=0.1, stale_days=400, recurrence_count=1)
    ok &= _check(r2["action"] == "dismiss_candidate",
                 f"低重要+极旧+孤立 -> dismiss_candidate（utility={r2['utility']}）")
    r3 = act.advise_retention(importance=0.8, stale_days=200, recurrence_count=1)
    ok &= _check(r3["action"] == "merge_candidate",
                 f"高重要但陈旧单稿 -> merge_candidate（utility={r3['utility']}）")
    ok &= _check(r["advisory"] is True and r["relevance"] > r2["relevance"],
                 "advisory 恒真，且活脉络 relevance 高于孤立稿")
    ok &= _check(0.0 <= r["recency"] <= 1.0 and 0.0 <= r["relevance"] <= 1.0,
                 "recency/relevance 归一 0..1")

    print("\nACT (P3-②):", "ALL PASS" if ok else "SOME FAILED")
    return ok


# 供 trace_recall 测试用的常量路径（与生产无耦合）
EXPECT = "/tmp/expect.md"

if __name__ == "__main__":
    sys.exit(0 if main() else 1)