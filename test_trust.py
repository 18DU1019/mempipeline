# -*- coding: utf-8 -*-
"""test_trust.py — P0 写侧信任分层（方案 B/3a）单元验证。

验证范围：
1. protocol.normalize_trust —— 来源/标记 → 三档（trusted/unknown/untrusted）归一，
   兼容显式档值、可信名单映射、缺省 unknown。
2. trust_rank.rank_with_trust —— 调用方二段降权：trusted 优先、unknown 次之、
   untrusted 仅在 trusted 不足 k 时补入，稳定排序不破坏同档内原序。

全部纯函数沙盒断言，不触碰真实镜像。3a = 不改 RecallBackend 契约，
降权是在调用方对 recall 结果做二段（依赖 prototol.normalize_trust 与
trust_rank 两处新函数，均为本次 P0 新增；写入侧 contemporaneous）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# 目标：normalize_trust 落在 protocol（写读共享契约），rank_with_trust 落在 trust_rank。
from mempipeline.protocol import normalize_trust, TRUST_KNOWN, TRUST_UNKNOWN, TRUST_UNTRUSTED
from mempipeline.trust_rank import rank_with_trust, trust_of_path

_TRUSTED = frozenset({"workbuddy"})


def main() -> bool:
    ok = True

    def check(cond, msg):
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {msg}")
        if not cond:
            ok = False

    # ---- 1. normalize_trust：三档归一 ----
    print("== normalize_trust 档位归一 ==")
    check(normalize_trust(None, _TRUSTED) == TRUST_UNKNOWN,
          f"None -> {TRUST_UNKNOWN}")
    check(normalize_trust("", _TRUSTED) == TRUST_UNKNOWN, "空串 -> unknown")
    check(normalize_trust("trusted", _TRUSTED) == TRUST_KNOWN, "显式 trusted -> trusted")
    check(normalize_trust("UNTRUSTED", _TRUSTED) == TRUST_UNTRUSTED,
          "显式 untrusted（大小写不敏感）-> untrusted")
    check(normalize_trust("unknown", _TRUSTED) == TRUST_UNKNOWN, "显式 unknown -> unknown")
    check(normalize_trust("workbuddy", _TRUSTED) == TRUST_KNOWN,
          "非档值但在可信名单 -> trusted")
    check(normalize_trust("social_ingest", _TRUSTED) == TRUST_UNTRUSTED,
          "非档值且不在可信名单 -> untrusted")
    check(normalize_trust("  trusted  ", _TRUSTED) == TRUST_KNOWN, "去空白后 trusted")

    # ---- 2. rank_with_trust：降权稳定排序 ----
    print("== rank_with_trust 调用方二段降权 ==")
    # 构造 (path, score)，path 末段内嵌标识，trust 来自 trust_of_path 回调。
    hits = [(f"{p}_mem.md", s) for p, s in
            [("t_trusted_a", 0.3), ("t_trusted_b", 0.5),
             ("u_unknown_a", 0.9), ("n_untr_a", 0.99), ("t_trusted_c", 0.1)]]

    def trust_of(path: str) -> str:
        if path.startswith("t_"):
            return TRUST_KNOWN
        if path.startswith("u_"):
            return TRUST_UNKNOWN
        return TRUST_UNTRUSTED

    # 2a. k 足够：untrusted 也保留，但 trusted 整体排到 untrusted 之前
    ranked = rank_with_trust(hits, trust_of, k=5)
    names = [Path(p).stem.split("_")[0] for p, _ in ranked]
    first_trusted = names.index("t")
    first_untr = names.index("n")
    check(first_trusted < first_untr,
          f"trusted 排在 untrusted 之前 (ranked={names})")
    check(len(ranked) == 5, f"k=5 全量保留 len={len(ranked)}")

    # 2b. k 收紧：untrusted 先被裁掉，trusted+unknown 保留满 k
    ranked2 = rank_with_trust(hits, trust_of, k=4)
    names2 = [Path(p).stem.split("_")[0] for p, _ in ranked2]
    check("n" not in names2 and "t" in names2 and "u" in names2,
          f"k=4 裁掉 untrusted，trusted+unknown 保留 (ranked={names2})")

    # 2c. 稳定排序：同档内保持原相对序（不重排语义等价项）
    ranked3 = rank_with_trust(hits, trust_of, k=6)
    t_ordered = [p for p, _ in ranked3 if p.startswith("t_")]
    check(t_ordered == ["t_trusted_a_mem.md", "t_trusted_b_mem.md", "t_trusted_c_mem.md"],
          f"trusted 同档内按原序列稳定 (t_ordered={t_ordered})")

    # 2d. unknown 排在 untrusted 之前
    first_unk = names.index("u")
    check(first_unk < first_untr, f"unknown 排在 untrusted 之前 (ranked={names})")

    # ---- 3. trust_of_path：读 frontmatter behind 默认名单 ----
    print("== trust_of_path 从笔记读取 ==")
    import tempfile
    from mempipeline.protocol import Note
    from mempipeline import protocol as _p
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        a = root / "trusted.md";  a.write_text(
            "---\ntype: note\ntitle: A\nmemory_tier: medium\nimportance: 0.6\n"
            "source_agent: workbuddy\n---\n\n正文甲。\n", encoding="utf-8")
        b = root / "social.md";   b.write_text(
            "---\ntype: note\ntitle: B\nmemory_tier: medium\nimportance: 0.6\n"
            "source_agent: social_ingest\n---\n\n正文乙。\n", encoding="utf-8")
        c = root / "plain.md";    c.write_text(
            "---\ntype: note\ntitle: C\nmemory_tier: medium\nimportance: 0.6\n---\n\n正文丙。\n",
            encoding="utf-8")
        d = root / "explicit.md"; d.write_text(
            "---\ntype: note\ntitle: D\nmemory_tier: medium\nimportance: 0.6\n"
            "source_agent: workbuddy\ntrust: untrusted\n---\n\n正文丁。\n", encoding="utf-8")
        e = root / "emptytrust.md"; e.write_text(
            "---\ntype: note\ntitle: E\nmemory_tier: medium\nimportance: 0.6\n"
            "source_agent: workbuddy\ntrust:\n---\n\n正文戊。\n", encoding="utf-8")
        f = root / "labelover.md"; f.write_text(
            "---\ntype: note\ntitle: F\nmemory_tier: medium\nimportance: 0.6\n"
            "source_agent: workbuddy\ntrust: unknown\n---\n\n正文己。\n", encoding="utf-8")
        # 登记表权威（writer_id 覆盖 source_agent 名单）：
        # g：source=workbuddy(名单内) 但 writer_id=staging_ingest(登记 untrusted) → untrusted
        g = root / "stage.md"; g.write_text(
            "---\ntype: note\ntitle: G\nmemory_tier: medium\nimportance: 0.6\n"
            "source_agent: workbuddy\nwriter_id: staging_ingest\n---\n\n正文庚。\n", encoding="utf-8")
        # h：source=social_ingest(名单外) 但 writer_id=distill_memory(登记 trusted) → trusted
        h = root / "distill.md"; h.write_text(
            "---\ntype: note\ntitle: H\nmemory_tier: medium\nimportance: 0.6\n"
            "source_agent: social_ingest\nwriter_id: distill_memory\n---\n\n正文辛。\n", encoding="utf-8")
        # i：未登记 writer_id → 回落 unknown（不误判档位）
        i = root / "alien.md"; i.write_text(
            "---\ntype: note\ntitle: I\nmemory_tier: medium\nimportance: 0.6\n"
            "source_agent: workbuddy\nwriter_id: some_future_writer\n---\n\n正文壬。\n", encoding="utf-8")
        check(trust_of_path(a, _TRUSTED) == TRUST_KNOWN,
              "source_agent=workbuddy(默认名单) -> trusted")
        check(trust_of_path(b, _TRUSTED) == TRUST_UNTRUSTED,
              "source_agent=social_ingest(不在名单) -> untrusted")
        check(trust_of_path(c, _TRUSTED) == TRUST_UNKNOWN,
              "无 source_agent/trust -> unknown")
        # 显式 trust 覆盖来源映射（D 虽 source=workbuddy 但显式 untrusted→untrusted）
        check(trust_of_path(d, _TRUSTED) == TRUST_UNTRUSTED,
              "显式 trust=untrusted 覆盖来源映射")
        # 单读重构边界：trust 无标量值（空）→ 回落 source_agent；显式档值覆盖可信来源
        check(trust_of_path(e, _TRUSTED) == TRUST_KNOWN,
              "显式 trust 空值回落 source_agent=workbuddy -> trusted")
        check(trust_of_path(f, _TRUSTED) == TRUST_UNKNOWN,
              "显式 trust=unknown 覆盖可信来源 -> unknown")
        # 登记表权威：writer_id 覆盖 source_agent 名单
        check(trust_of_path(g, _TRUSTED) == TRUST_UNTRUSTED,
              "writer_id=staging_ingest 覆盖 source=workbuddy(名单内) -> untrusted")
        check(trust_of_path(h, _TRUSTED) == TRUST_KNOWN,
              "writer_id=distill_memory 覆盖 source=social_ingest(名单外) -> trusted")
        check(trust_of_path(i, _TRUSTED) == TRUST_UNKNOWN,
              "未登记 writer_id 回落 unknown（不误判档位）")

    # ---- 4. ingest 写侧落盘：投稿 frontmatter 含 trust 档 ----
    print("== ingest 写侧 trust 落盘 ==")
    import tempfile as _tf
    from mempipeline.ingest import ingest as _ingest
    from mempipeline.audit import FileAudit as _FA
    with _tf.TemporaryDirectory() as td2:
        p = Path(td2)
        staging = p / "staging"; (staging / "a").mkdir(parents=True)
        mem = p / "mem"; (mem / "02-中期记忆").mkdir(parents=True)
        # 投稿1：无 trust、source=workbuddy → trusted
        (staging / "a" / "s1.md").write_text(
            "---\ntitle: 单写者A\nmemory_tier: medium\nsource_agent: workbuddy\n---\n\n正文。\n",
            encoding="utf-8")
        # 投稿2：无 trust、source=social_ingest → untrusted（默认名单不含）
        (staging / "a" / "s2.md").write_text(
            "---\ntitle: 投稿B\nmemory_tier: medium\nsource_agent: social_ingest\n---\n\n正文。\n",
            encoding="utf-8")
        # 投稿3：显式 trust=unknown → unknown（覆盖来源映射）
        (staging / "a" / "s3.md").write_text(
            "---\ntitle: 投稿C\nmemory_tier: medium\nsource_agent: workbuddy\ntrust: unknown\n---\n\n正文。\n",
            encoding="utf-8")
        # 投稿4：无 trust、source=workbuddy(名单内) 但 writer_id=staging_ingest(登记 untrusted)
        # → untrusted（登记表覆盖 source_agent 名单，同源冲突的权威裁决）
        (staging / "a" / "s4.md").write_text(
            "---\ntitle: 投稿D\nmemory_tier: medium\nsource_agent: workbuddy\nwriter_id: staging_ingest\n---\n\n正文。\n",
            encoding="utf-8")
        a2 = _FA(p / "audit" / "log.md", p / "audit" / "man.json", mem)
        st = _ingest(staging, mem, None, a2)
        texts = {f.read_text(encoding="utf-8") for f in (mem / "02-中期记忆").glob("*.md")}
        check(st["wrote"] == 4, f"ingest 写入 4 篇 wrote={st['wrote']}")
        check(any('trust: "trusted"' in t and "单写者A" in t for t in texts),
              "workbuddy 投稿 -> trust: trusted")
        check(any('trust: "untrusted"' in t and "投稿B" in t for t in texts),
              "social_ingest 投稿 -> trust: untrusted")
        check(any('trust: "unknown"' in t and "投稿C" in t for t in texts),
              "显式 trust=unknown -> trust: unknown")
        check(any('trust: "untrusted"' in t and "投稿D" in t for t in texts) and
              any('writer_id: "staging_ingest"' in t and "投稿D" in t for t in texts),
              "writer_id=staging_ingest 落盘 trust: untrusted + 溯源 writer_id")

    # ---- 5. hybrid_recall 信任降权开关冒烟（纯 TF-IDF 路径，trust_rank=True） ----
    print("== hybrid_recall trust_rank 开关冒烟 ==")
    import tempfile as _tf2
    from mempipeline.semantic import hybrid_recall as _hr
    with _tf2.TemporaryDirectory() as td3:
        p2 = Path(td3)
        mem2 = p2 / "mem"; (mem2 / "02-中期记忆").mkdir(parents=True)
        # 一篇 trusted、一篇 untrusted，语义无关的高分未来由降权重排
        (mem2 / "02-中期记忆" / "项目会话-好稿-aaaa1111.md").write_text(
            "---\ntype: note\ntitle: 好稿\nmemory_tier: medium\nimportance: 0.6\n"
            "source_agent: social_ingest\n---\n\n仓位规则 风险 ATR。\n", encoding="utf-8")
        (mem2 / "02-中期记忆" / "项目会话-蒸馏-cccc2222.md").write_text(
            "---\ntype: note\ntitle: 蒸馏\nmemory_tier: medium\nimportance: 0.6\n"
            "source_agent: workbuddy\n---\n\n仓位规则 单笔 风险。\n", encoding="utf-8")
        # trust_rank=False（默认）：按相关度原样，未启用不改变既有输出
        base = _hr("仓位 风险", k=4, mem_root=mem2)
        check(len(base) == 2, f"默认路径返回全部命中 len={len(base)}")
        # trust_rank=True：trusted（蒸馏）排到 untrusted（好稿）之前
        rankedhr = _hr("仓位 风险", k=4, mem_root=mem2, trust_rank=True)
        top_stem = Path(rankedhr[0][0]).stem
        check("蒸馏" in top_stem, f"trust_rank=True: trusted 蒸馏排首位 (stem={top_stem})")

    # ---- 6. P1 信任封顶（2026-09-17）：自声明不得越过登记基线（min 语义） ----
    print("== cap_trust 封顶原语 ==")
    from mempipeline.protocol import cap_trust as _cap
    check(_cap(TRUST_KNOWN, TRUST_KNOWN) == TRUST_KNOWN,
          "declared=baseline=trusted -> trusted")
    check(_cap(TRUST_KNOWN, TRUST_UNTRUSTED) == TRUST_UNTRUSTED,
          "declared trusted 越过 untrusted 基线 -> 压回 untrusted")
    check(_cap(TRUST_UNTRUSTED, TRUST_KNOWN) == TRUST_UNTRUSTED,
          "declared untrusted 低于 trusted 基线 -> 保持 untrusted（可下压）")
    check(_cap(TRUST_UNKNOWN, TRUST_KNOWN) == TRUST_UNKNOWN,
          "declared unknown 低于 trusted 基线 -> unknown")

    print("== ingest 写侧封顶：投稿自声明 trust 越权被压回 ==")
    with _tf.TemporaryDirectory() as td4:
        p4 = Path(td4)
        staging4 = p4 / "staging"; staging4.mkdir()
        mem4 = p4 / "mem"; (mem4 / "02-中期记忆").mkdir(parents=True)
        # 越权稿：staging_ingest（登记 untrusted）自声明 trust: trusted → 必须压回
        (staging4 / "evil.md").write_text(
            "---\ntitle: 越权稿\nmemory_tier: medium\nsource_agent: social_ingest\n"
            "writer_id: staging_ingest\ntrust: trusted\n---\n\n正文。\n", encoding="utf-8")
        # 正当稿：distill_memory（登记 trusted）自声明 trusted == 基线，不受影响
        (staging4 / "legit.md").write_text(
            "---\ntitle: 正当稿\nmemory_tier: medium\nsource_agent: workbuddy\n"
            "writer_id: distill_memory\ntrust: trusted\n---\n\n正文。\n", encoding="utf-8")
        a4 = _FA(p4 / "audit" / "log.md", p4 / "audit" / "man.json", mem4)
        st4 = _ingest(staging4, mem4, None, a4)
        texts4 = {f.read_text(encoding="utf-8") for f in (mem4 / "02-中期记忆").glob("*.md")}
        check(st4["wrote"] == 2, f"ingest 写入 2 篇 wrote={st4['wrote']}")
        check(any('trust: "untrusted"' in t and "越权稿" in t for t in texts4),
              "staging_ingest 自声明 trusted 被封顶为 untrusted")
        check(any('trust: "trusted"' in t and "正当稿" in t for t in texts4),
              "distill_memory 自声明 trusted == 基线，不受影响")

    print("== trust_of_path 读侧封顶：存量越权 frontmatter 被压回 ==")
    with tempfile.TemporaryDirectory() as td5:
        root5 = Path(td5)
        # 存量稿：writer_id=staging_ingest，frontmatter 被旧版写侧灌入 trust: trusted
        j = root5 / "legacy.md"; j.write_text(
            "---\ntype: note\ntitle: J\nmemory_tier: medium\nimportance: 0.6\n"
            "source_agent: social_ingest\nwriter_id: staging_ingest\ntrust: trusted\n---\n\n正文癸。\n",
            encoding="utf-8")
        # 裸自声明：无 writer_id 无 source_agent，trust: trusted → unknown（零身份不升档）
        k = root5 / "bare.md"; k.write_text(
            "---\ntype: note\ntitle: K\nmemory_tier: medium\nimportance: 0.6\n"
            "trust: trusted\n---\n\n正文子。\n", encoding="utf-8")
        check(trust_of_path(j, _TRUSTED) == TRUST_UNTRUSTED,
              "存量 staging_ingest + trust:trusted 读侧封顶 -> untrusted")
        check(trust_of_path(k, _TRUSTED) == TRUST_UNKNOWN,
              "裸 trust:trusted（无 writer/source）-> unknown")

    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)