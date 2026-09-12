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

from mempipeline.audit import FileAudit
from mempipeline.governance import (
    filter_note, score_note, stale_days, transition, review_queue, vault_status,
    scan_stale_notes, governance_health, VALID_TRANSITIONS,
)
from mempipeline.protocol import Note, TIER_DIR
from mempipeline.engine import write_atomic


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

        # 5.5 治理健康度快照（P1-诊断新增，周检③）——需在临时目录销毁前调用
        # 此时镜像两篇：规则(rejected) + 经验(candidate)，无 promoted
        print("== 治理健康度 ==")
        h = governance_health(mem_root)
        check(h["by_status"].get("candidate") == 1 and
              h["by_status"].get("rejected") == 1,
              f"health 统计 candidate/rejected（{h['by_status']}）")
        check(h["candidate_pending"] == 1, f"候选队列计数 1（{h['candidate_pending']}）")
        check(h["non_active_ratio"] > 0, f"非 active 占比>0（{h['non_active_ratio']}）")
        check(sum(h["by_status"].values()) == 2, "health 总计 2 篇")

    # ---- 5. vault 映射 ----
    print("== vault 映射 ==")
    check(vault_status("promoted") == "active", "promoted→active")
    check(vault_status("draft") == "draft", "draft 保留")
    check(vault_status("archived") == "archived", "archived 保留")

    # 状态机拓扑完整性
    check("promoted" in VALID_TRANSITIONS["candidate"], "candidate→promoted 合法")
    check("rejected" in VALID_TRANSITIONS["candidate"], "candidate→rejected 合法")

    # ---- 6. P1 自动化审计：状态转移轨迹自动登记 + 生命周期还原 ----
    print("== P1 自动化审计 ==")
    with tempfile.TemporaryDirectory() as td2:
        tmp2 = Path(td2)
        mem2 = tmp2 / "mem"
        (mem2 / TIER_DIR["long"]).mkdir(parents=True)
        aud2 = FileAudit(tmp2 / "audit" / "log.md", tmp2 / "audit" / "manifest.json", mem2)
        note_a = Note(title="轨迹", summary="审计还原", tier="long", importance=0.9,
                      body="正文。", status="draft")
        out_a = mem2 / TIER_DIR["long"] / "轨迹-abcd1234ef.md"
        write_atomic(out_a, note_a.to_frontmatter() + "\n\n" + note_a.body + "\n", aud2)

        # 连续状态迁移：draft→project→candidate→promoted
        for to in ("project", "candidate", "promoted"):
            st, _ = transition(out_a, to, aud2)
            check(st in ("wrote", "skipped"), f"P1: 迁移到 {to} 成功（{st}）")

        # 轨迹还原：能按发生顺序还原完整生命周期
        rel_a = str(out_a.resolve().relative_to(mem2.resolve())).replace("\\", "/")
        life = aud2.lifecycle(rel_a)
        seq = [(e.get("from"), e.get("to")) for e in life]
        check(seq == [("draft", "project"), ("project", "candidate"), ("candidate", "promoted")],
              f"P1: 生命周期轨迹按序还原（{seq}）")
        check(all("auto:transition" in (e.get("reason") or "") for e in life),
              "P1: 转移轨迹 reason 由系统自动生成（免人工填写）")
        # 良性验证：同一笔记再走 promoted→archived，轨迹追加而非覆盖
        st, _ = transition(out_a, "archived", aud2)
        check(st in ("wrote", "skipped"), f"P1: promoted→archived 成功（{st}）")
        life2 = aud2.lifecycle(rel_a)
        check(len(life2) == 4 and life2[-1]["to"] == "archived",
              "P1: 轨迹 append-only，追加而非覆盖")

    # ---- 7. P1 质量扫描：scan_stale_notes 只出清单、不自动改状态 ----
    print("== P1 质量扫描 ==")
    with tempfile.TemporaryDirectory() as td3:
        tmp3 = Path(td3)
        mem3 = tmp3 / "mem"
        (mem3 / TIER_DIR["long"]).mkdir(parents=True)
        aud3 = FileAudit(tmp3 / "audit" / "log.md", tmp3 / "audit" / "manifest.json", mem3)
        # 一篇旧 + 低 importance → 应进 stale 候选
        stale_note = Note(title="旧经验", summary="久未用", tier="long", importance=0.3,
                          body="很旧了。", status="active",
                          updated=(datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d %H:%M:%S"))
        out_s = mem3 / TIER_DIR["long"] / "旧经验-ff00ff00ef.md"
        write_atomic(out_s, stale_note.to_frontmatter() + "\n\n" + stale_note.body + "\n", aud3)
        # 一篇新 + 高 importance → 不应进候选
        fresh_note = Note(title="新记忆", summary="刚记", tier="long", importance=0.9,
                          body="新鲜的。", status="active")
        out_f = mem3 / TIER_DIR["long"] / "新记忆-00ff00ffee.md"
        write_atomic(out_f, fresh_note.to_frontmatter() + "\n\n" + fresh_note.body + "\n", aud3)

        cand = scan_stale_notes(mem3, threshold=0.6)
        cand_paths = [c["path"] for c in cand]
        check(str(out_s) in cand_paths, "P1: 旧+低分笔记进入 stale 候选清单")
        check(str(out_f) not in cand_paths, "P1: 新+高分笔记不进入候选")
        # 只出清单、不自动改状态（rank 完整性）
        check(all(c["suggestion"] == "archive" for c in cand) and out_s.exists(),
              "P1: 扫描只标记候选，不自动执行 transition（文件保留）")
        # 结构完整性
        check(all({"path", "status", "stale_days", "score", "suggestion"} <= set(c) for c in cand),
              "P1: 每项候选含完整结构化字段")

    # ---- 8. P3-③ 时间图谱信号使入治理：drift/revived 并入候选清单、不改状态 ----
    print("== P3 时间图谱信号使入治理 ==")
    from mempipeline.timegrap import build_timeline
    with tempfile.TemporaryDirectory() as td4:
        tmp4 = Path(td4)
        mem4 = tmp4 / "mem"
        (mem4 / TIER_DIR["long"]).mkdir(parents=True)
        aud4 = FileAudit(tmp4 / "audit" / "log.md", tmp4 / "audit" / "manifest.json", mem4)
        now = datetime.now()

        def _w(filename, title, summary, project_id, days_ago):
            n = Note(title=title, summary=summary, tier="long", importance=0.9,
                     body="正文。", status="active", project_id=project_id,
                     updated=(now - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S"))
            p = mem4 / TIER_DIR["long"] / filename
            write_atomic(p, n.to_frontmatter() + "\n\n" + n.body + "\n", aud4)
            return p

        # 话题 A（project_id=cc33cc01）：同层两稿、摘要不同 → 结论漂移(re-review)
        drift_a = _w("决策A-drift.md", "决策A", "方案甲裁定执行", "cc33cc01", 2)
        drift_b = _w("决策B-drift.md", "决策B", "重构实施模块乙替代", "cc33cc01", 1)
        # 话题 B（project_id=dd44ee55）：两稿间隔 35 天(>gap_days=30) → 断更后复活(review)
        rev_p = _w("约束C-rev.md", "约束C", "约束初稿确立", "dd44ee55", 40)
        rev_v = _w("约束D-rev.md", "约束D", "约束修订到期", "dd44ee55", 5)

        tl = build_timeline(mem4, gap_days=30, drift_threshold=0.98)
        cand = scan_stale_notes(mem4, threshold=0.2, timeline=tl)

        drift = [c for c in cand if c.get("signal") == "conclusion_drift"]
        revived = [c for c in cand if c.get("signal") == "revived"]
        check(len(drift) == 1 and drift[0]["path"] == str(drift_b)
              and drift[0]["suggestion"] == "re-review",
              f"P3: 结论漂移进入候选清单（re-review，{len(drift)}条）")
        check(len(revived) == 1 and revived[0]["path"] == str(rev_v)
              and revived[0]["suggestion"] == "review",
              f"P3: 断更后复活进入候选清单（review，{len(revived)}条）")
        check(all({"path", "status", "stale_days", "score", "suggestion", "signal",
                   "subject"} <= set(c) for c in drift + revived),
              "P3: 信号候选含完整结构化字段")
        # 不自动改状态：扫描后所有文件 status 仍为 active（未真触发 transition）
        still_active = all(
            ("status: \"active\"" in p.read_text(encoding="utf-8")
             or "status: active" in p.read_text(encoding="utf-8"))
            for p in (drift_a, drift_b, rev_p, rev_v))
        check(still_active, "P3: 只出清单、未自动改状态（4 篇仍为 active）")

    # ---- 8b. D3 软失效状态显式化：valid_to 已取代的过时旧稿 → superseded 候选 ----
    print("== D3 软失效状态显式化 ==")
    with tempfile.TemporaryDirectory() as td5:
        tmp5 = Path(td5)
        mem5 = tmp5 / "mem"
        (mem5 / TIER_DIR["long"]).mkdir(parents=True)
        aud5 = FileAudit(tmp5 / "audit" / "log.md", tmp5 / "audit" / "manifest.json", mem5)
        now = datetime.now()

        def _w5(filename, title, summary, project_id, days_ago):
            n = Note(title=title, summary=summary, tier="long", importance=0.9,
                     body="正文。", status="active", project_id=project_id,
                     updated=(now - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S"))
            p = mem5 / TIER_DIR["long"] / filename
            write_atomic(p, n.to_frontmatter() + "\n\n" + n.body + "\n", aud5)
            return p

        # 话题 X：旧稿 + 新稿（同主题同 project，旧稿被后续稿取代）
        old_x = _w5("结论X-旧.md", "结论X", "旧方案定版", "ee55ff66", 30)
        new_x = _w5("结论X-新.md", "结论X", "新方案替换旧版", "ee55ff66", 1)
        tl5 = build_timeline(mem5, gap_days=90, drift_threshold=0.5)
        cand5 = scan_stale_notes(mem5, threshold=0.2, timeline=tl5)
        superseded = [c for c in cand5 if c.get("signal") == "superseded"]
        check(str(old_x) in {c["path"] for c in superseded},
              "D3: 被后续稿取代的旧稿进入 superseded 候选")
        check(not any(c["path"] == str(new_x) and c.get("signal") == "superseded"
                      for c in superseded),
              "D3: 最新有效稿不被标 superseded")
        check(superseded and superseded[0]["suggestion"] == "supersede",
              "D3: superseded 候选带 supersede 建议")
        check(("status: \"active\"" in old_x.read_text(encoding="utf-8")
               or "status: active" in old_x.read_text(encoding="utf-8")),
              "D3: 只出候选、未改写文件状态")

    print("\nGOVERNANCE (E3'):", "ALL PASS" if ok else "SOME FAILED")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
