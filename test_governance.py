# -*- coding: utf-8 -*-
"""test_governance.py — E3' 治理状态机验收（零依赖，自带临时 fixtures）。

覆盖：
1. 过滤规则：kind 白名单 + 黑名单特征
2. 打分：importance/时间衰减/反馈/命中 四因子
3. 状态迁移：合法/非法迁移、软标记零删除（rejected 后文件仍在）
4. 审核队列：candidate 扫描
5. vault status 映射（promoted→active）
"""
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mempipeline.audit import FileAudit  # noqa: E402
from mempipeline.governance import (  # noqa: E402
    filter_note, score_note, stale_days, transition, review_queue, vault_status,
    VALID_TRANSITIONS,
)
from mempipeline.protocol import Note, TIER_DIR  # noqa: E402
from mempipeline.engine import write_atomic  # noqa: E402


def main() -> bool:
    ok = True

    def check(cond: bool, msg: str):
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {msg}")
        if not cond:
            ok = False

    # ---- 1. 过滤规则 ----
    print("== 过滤 ==")
    p1, r1 = filter_note({"kind": "decision", "title": "韩餐动线", "summary": "出餐口到餐桌"})
    check(p1 and r1 == "ok", "kind=decision 白名单通过")
    p2, r2 = filter_note({"kind": "chat", "title": "闲聊", "summary": "今天天气"})
    check(not p2 and "白名单" in r2, "kind=chat 拒（不在白名单）")
    p3, r3 = filter_note({"kind": "result", "title": "临时草稿", "summary": "测试片段"})
    check(not p3 and "黑名单" in r3, "黑名单特征拒")

    # ---- 2. 打分 ----
    print("== 打分 ==")
    s_high = score_note(importance=0.9, days_since_active=0, hits=10, feedback=1.0)
    s_stale = score_note(importance=0.6, days_since_active=300, hits=0, feedback=0.0)
    check(s_high > 0.8, f"高价值新记忆高分（{s_high:.3f}）")
    check(s_stale < s_high, f"stale 记忆低于新记忆（{s_stale:.3f} < {s_high:.3f}）")
    check(abs(s_high - 1.0) < 1e-9, "满配四因子 = 1.0")
    d0 = stale_days(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    d300 = stale_days((datetime.now() - timedelta(days=300)).strftime("%Y-%m-%d %H:%M:%S"))
    check(d0 == 0 and d300 >= 299, f"stale_days 计算正确（0 / {d300}）")

    # ---- 3. 状态迁移 + 零删除 ----
    print("== 状态迁移 ==")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        mem_root = tmp / "mem"
        (mem_root / TIER_DIR["long"]).mkdir(parents=True)
        audit = FileAudit(tmp / "audit" / "log.md", tmp / "audit" / "manifest.json", mem_root)
        note = Note(title="规则", summary="单笔风险≤1ATR", tier="long",
                    importance=0.9, body="正文。", status="draft")
        out = mem_root / TIER_DIR["long"] / "规则-ab12cd34.md"
        write_atomic(out, note.to_frontmatter() + "\n\n" + note.body + "\n", audit)

        st1, f1 = transition(out, "project", audit)
        check(st1 in ("wrote", "skipped") and f1 == "draft",
              f"draft→project 迁移成功（{st1}）")
        st1b, f1b = transition(out, "candidate", audit)
        check(st1b in ("wrote", "skipped") and f1b == "project",
              f"project→candidate 迁移成功（{st1b}）")
        st2, f2 = transition(out, "promoted", audit)
        check(st2 in ("wrote", "skipped") and f2 == "candidate",
              f"candidate→promoted 迁移成功（{st2}）")
        # 非法迁移：promoted 不能再回 draft
        st3, f3 = transition(out, "draft", audit)
        check(st3 == "invalid_transition" and f3 == "promoted",
              f"promoted→draft 非法迁移被拒（{st3}）")
        # 软标记零删除：rejected 后文件仍在
        st4, _ = transition(out, "rejected", audit)
        check(st4 in ("wrote", "skipped") and out.exists(),
              f"rejected 软标记且文件保留（{st4}，exists={out.exists()}）")
        txt = out.read_text(encoding="utf-8")
        check('status: "rejected"' in txt or 'status: rejected' in txt,
              "frontmatter status 已更新为 rejected")

        # ---- 4. 审核队列 ----
        print("== 审核队列 ==")
        note2 = Note(title="经验", summary="最佳实践", tier="long", importance=0.7,
                     body="踩坑经验。", status="candidate")
        out2 = mem_root / TIER_DIR["long"] / "经验-ef5678ab.md"
        write_atomic(out2, note2.to_frontmatter() + "\n\n" + note2.body + "\n", audit)
        q = review_queue(mem_root)
        check(out2 in q and out not in q,
              f"候选队列含 candidate 笔记、不含 rejected 笔记（{len(q)} 条）")

    # ---- 5. vault 映射 ----
    print("== vault 映射 ==")
    check(vault_status("promoted") == "active", "promoted→active")
    check(vault_status("draft") == "draft", "draft 保留")
    check(vault_status("archived") == "archived", "archived 保留")

    # 状态机拓扑完整性
    check("promoted" in VALID_TRANSITIONS["candidate"], "candidate→promoted 合法")
    check("rejected" in VALID_TRANSITIONS["candidate"], "candidate→rejected 合法")

    print("\nGOVERNANCE (E3'):", "ALL PASS" if ok else "SOME FAILED")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
