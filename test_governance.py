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
    _half_life, TIER_POLICY, SUPERSEDED_PENALTY, redact,
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

    # ---- 2.5 三因子治理分档：TIER_POLICY + 半衰期自适应（tier > importance）----
    print("== 三因子分档 ==")
    check(TIER_POLICY["long"]["half_life"] == 1095 and
          TIER_POLICY["medium"]["half_life"] == 180,
          "TIER_POLICY 半衰期基座（long≈3年 / medium=180天）")
    check(TIER_POLICY["long"]["threshold"] == 0.1 and
          TIER_POLICY["medium"]["threshold"] == 0.2,
          "TIER_POLICY 归档线（long=0.1 / medium=0.2）")
    check(TIER_POLICY["long"]["stale_binary"] is False and
          TIER_POLICY["medium"]["stale_binary"] is True,
          "stale 二进制开关：long 禁用 / medium 保留")
    # importance 连续微调：HL = base × (0.5 + importance)
    check(abs(_half_life(0.9, tier="medium") - 180 * 1.4) < 1e-9 and
          abs(_half_life(0.6, tier="medium") - 180 * 1.1) < 1e-9 and
          abs(_half_life(0.3, tier="medium") - 180 * 0.8) < 1e-9,
          "half_life 连续微调（imp0.9→×1.4 / 0.6→×1.1 / 0.3→×0.8）")
    check(_half_life(0.6, half_life=90) == 90, "显式 half_life 覆盖微调")
    # 反向验证半衰期生效：同旧度下 long 半衰期更长 → 衰减更慢 → 分数更高
    same_days = 400
    s_long = score_note(importance=0.6, days_since_active=same_days, tier="long")
    s_med = score_note(importance=0.6, days_since_active=same_days, tier="medium")
    check(s_long > s_med, f"长期记忆衰减更慢（long {s_long:.3f} > medium {s_med:.3f}）")
    # 同 tier 下 importance 越高越慢衰减：imp0.9 分数 > imp0.3
    s_hi = score_note(importance=0.9, days_since_active=same_days, tier="medium")
    s_lo = score_note(importance=0.3, days_since_active=same_days, tier="medium")
    check(s_hi > s_lo, f"importance 微调更慢衰减（{s_hi:.3f} > {s_lo:.3f}）")

    # ---- 2.6 三因子联动：scan_stale_notes 按 tier 取归档线 + valid_to 降权 ----
    print("== 三因子联动扫描 ==")
    with tempfile.TemporaryDirectory() as td6:
        tmp6 = Path(td6)
        mem6 = tmp6 / "mem"
        (mem6 / TIER_DIR["long"]).mkdir(parents=True)
        (mem6 / TIER_DIR["medium"]).mkdir(parents=True)
        aud6 = FileAudit(tmp6 / "audit" / "log.md", tmp6 / "audit" / "manifest.json", mem6)
        now6 = datetime.now()
        old_ts = (now6 - timedelta(days=415)).strftime("%Y-%m-%d %H:%M:%S")

        # 同一大量旧 + 低 importance 的两篇：仅 medium 跌破其 0.2 归档线，
        # long 因半衰期更长+归档线更低(0.1) 不入选 → 证明 tier 分档真正生效
        note_lo_m = Note(title="冷稿medium", summary="久未用", tier="medium",
                         importance=0.3, body="很旧。", status="active", updated=old_ts)
        out_lo_m = mem6 / TIER_DIR["medium"] / "冷稿medium-aa11.md"
        write_atomic(out_lo_m, note_lo_m.to_frontmatter() + "\n\n" + note_lo_m.body + "\n", aud6)
        note_lo_l = Note(title="冷稿long", summary="久未用", tier="long",
                         importance=0.3, body="很旧。", status="active", updated=old_ts)
        out_lo_l = mem6 / TIER_DIR["long"] / "冷稿long-bb22.md"
        write_atomic(out_lo_l, note_lo_l.to_frontmatter() + "\n\n" + note_lo_l.body + "\n", aud6)

        # 一篇新 + 高 importance 的 long 稿，被 superseded 集命中 → 即使分高于归档线
        # 也单列 supersede 候选（valid_to 强失效轨道），且不改文件状态
        fresh_l = Note(title="新稿long", summary="刚记", tier="long", importance=0.9,
                       body="新鲜。", status="active")
        out_fresh = mem6 / TIER_DIR["long"] / "新稿long-cc33.md"
        write_atomic(out_fresh, fresh_l.to_frontmatter() + "\n\n"
                     + fresh_l.body + "\n", aud6)

        cand6 = scan_stale_notes(mem6, superseded={str(out_fresh)})
        paths6 = {c["path"] for c in cand6}
        # 默认按 tier 归档线：medium 冷稿入选(0.15<0.2)，long 冷稿不入选(0.32>0.1)
        m_row = next((c for c in cand6 if c["path"] == str(out_lo_m)), None)
        l_row = next((c for c in cand6 if c["path"] == str(out_lo_l)), None)
        check(m_row is not None and m_row["suggestion"] == "archive",
              "三因子: medium 冷稿跌破 0.2 归档线 → archive")
        check(l_row is None, "三因子: 同旧度 long 冷稿不跌破其 0.1 归档线 → 不入选")
        # valid_to 强失效轨道：降权 0.4 且高优先级单列（即使 score 未破线）
        sup_row = next((c for c in cand6 if c["path"] == str(out_fresh)), None)
        check(sup_row is not None and sup_row["suggestion"] == "supersede" and
              sup_row["signal"] == "superseded",
              "三因子: superseded 命中即单列 supersede 候选（不分是否破线）")
        check(abs(sup_row["score"] - round(
                score_note(importance=0.9, days_since_active=0)
                * SUPERSEDED_PENALTY, 3)) < 1e-9,
              f"三因子: superseded 降权 score=score×{SUPERSEDED_PENALTY}"
              f"（{sup_row['score']}）")
        check("status: \"active\"" in out_fresh.read_text(encoding="utf-8")
              or "status: active" in out_fresh.read_text(encoding="utf-8"),
              "三因子: superseded 只出候选、不改写文件状态")

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

    # ---- 9. P3.12 红act/记忆脱敏：recall 两路切断 + 软终态 + 审计轨迹 + 硬脱敏 ----
    print("== P3.12 红act/记忆脱敏 ==")
    from mempipeline.recall import MemoryRecall, TFIDFIndex
    with tempfile.TemporaryDirectory() as td7:
        tmp7 = Path(td7)
        mem7 = tmp7 / "mem"
        (mem7 / TIER_DIR["long"]).mkdir(parents=True)
        aud7 = FileAudit(tmp7 / "audit" / "log.md", tmp7 / "audit" / "manifest.json", mem7)
        secret = Note(title="密钥配置", summary="AKSK", tier="long", importance=0.9,
                      body="密钥 sk_live_abc123 与 token 属敏感泄露内容。", status="active")
        out7 = mem7 / TIER_DIR["long"] / "密钥-redact-abcd.md"
        write_atomic(out7, secret.to_frontmatter() + "\n\n" + secret.body + "\n", aud7)
        idx7 = TFIDFIndex(tmp7 / "idx.sqlite")
        idx7.build(mem7)
        mk7 = MemoryRecall(mem7, index=idx7)   # 倒排快路径
        full7 = MemoryRecall(mem7)             # 无索引 → 全扫路
        q7 = "密钥 sk_live"

        def _hit(backend):
            return any(str(out7) == p for p, _ in backend.recall(q7, k=20))

        check(_hit(full7) and _hit(mk7),
              "红act前: recall 全扫 + TFIDF 两路均命中该稿")

        # 红act（软脱敏默认：标记 + 重建索引）→ 两路均不再返回
        st_a, f_a = redact(out7, aud7)
        check(st_a in ("wrote", "skipped") and f_a == "active",
              f"红act 软脱敏迁移成功（{st_a}，{f_a}）")
        idx7.build(mem7)                       # 脱敏后重建索引（不可省略）
        check(not _hit(full7) and not _hit(mk7),
              "红act后: recall 全扫 + TFIDF 两路均不再命中该稿")

        # 审计轨迹可还原 …→redacted，历史条目不丢
        rel7 = str(out7.resolve().relative_to(mem7.resolve())).replace("\\", "/")
        life7 = aud7.lifecycle(rel7)
        check(bool(life7) and life7[-1]["to"] == "redacted",
              f"审计轨迹含 …→redacted（{life7}）")

        # redacted 是软终态：再迁出被 VALID_TRANSITIONS 合法拒绝
        st_b, f_b = transition(out7, "active", aud7)
        check(st_b == "invalid_transition" and f_b == "redacted",
              f"redacted→active 非法迁移被拒（{st_b}，{f_b}）")

        # 软脱敏默认保留正文（可逆、忠于审计）
        check("sk_live" in out7.read_text(encoding="utf-8"),
              "软脱敏默认保留正文（可逆，忠于审计取证）")
        idx7.close()

    # ---- 9b. 硬脱敏：独立显式通道，先备份到 git 外归档区，再抹正文，审计记新 hash ----
    with tempfile.TemporaryDirectory() as td8:
        tmp8 = Path(td8)
        mem8 = tmp8 / "mem"
        (mem8 / TIER_DIR["long"]).mkdir(parents=True)
        aud8 = FileAudit(tmp8 / "audit" / "log.md", tmp8 / "audit" / "manifest.json", mem8)
        secret2 = Note(title="硬脱敏稿", summary="token", tier="long", importance=0.9,
                       body="token sk_secret_xyz 需彻底抹除。", status="active")
        out8 = mem8 / TIER_DIR["long"] / "硬脱敏稿-0123abcd.md"
        write_atomic(out8, secret2.to_frontmatter() + "\n\n" + secret2.body + "\n", aud8)
        arch8 = tmp8 / "archive"
        st_w, f_w = redact(out8, aud8, wipe=True, archive_dir=arch8)
        check(st_w in ("wrote", "skipped") and f_w == "active",
              f"硬脱敏（显式 wipe）成功（{st_w}，{f_w}）")
        baks = list(arch8.glob("*.md"))
        check(len(baks) == 1 and "sk_secret_xyz" in baks[0].read_text(encoding="utf-8"),
              "硬脱敏先备份原稿正文到 git 外归档区（防丢失底线）")
        wiped = out8.read_text(encoding="utf-8")
        check("sk_secret_xyz" not in wiped and "redacted" in wiped,
              "硬脱敏后正文被抹为占位符、文件仍红act标记保留")

    print("\nGOVERNANCE (E3'):", "ALL PASS" if ok else "SOME FAILED")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
