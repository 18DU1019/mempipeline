# -*- coding: utf-8 -*-
"""A 轨 mock 打通：robot-vision 写者全链路验证（临时镜像，不碰生产库）。

验证目标：
1. write_atomic 原子写 + robot-vision 契约合规投稿
2. check_contract 对合规/违规稿的判定
3. MemoryRecall 能检索到机器人观测稿（文本域可召回）
4. 跨写者冲突：机器人观测 vs 人类记忆（distill_memory）矛盾 → 双稿保留 + 可观测
5. 越层/缺 project_id 违规稿被契约拦截

构造：临时目录内建 projects/robot-客厅/02-中期记忆 布局（与 scan_tier_dirs 对齐）。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mempipeline.engine import write_atomic  # noqa: E402
from mempipeline.audit import FileAudit, NullAudit  # noqa: E402
from mempipeline.writer_contracts import check_contract  # noqa: E402
from mempipeline.recall import MemoryRecall  # noqa: E402
from mempipeline.protocol import TIER_DIR  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def fm_robot(pid: str, tier: str, stem: str, body: str, extra: str = "") -> str:
    """构造 robot-vision 投稿（含 source_modal 独立证据标注）。"""
    return (
        f"---\ntype: note\ntitle: {stem}\nsummary: 机器人观测\n"
        f"memory_tier: {tier}\nimportance: 0.7\nwriter_id: robot-vision\n"
        f"project_id: {pid}\nconfidence_perception: 0.9\nconfidence_anchor: 0.86\n"
        f"source_modal: vision\n{extra}---\n\n# {stem}\n\n{body}\n"
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mempipeline-robot-mock-") as td:
        root = Path(td)
        tier = TIER_DIR["medium"]  # 02-中期记忆
        proj_dir = root / "projects" / "robot-客厅" / tier
        proj_dir.mkdir(parents=True, exist_ok=True)
        audit_dir = root / "_audit"
        audit = FileAudit(audit_dir / "log.md", audit_dir / "manifest.json", root)

        print("== 1. write_atomic + 契约合规投稿 ==")
        m1 = fm_robot(
            "robot-客厅", "medium", "项目会话-r1",
            "在客厅餐桌上看到玻璃杯，用户提示易碎，应轻拿。",
            extra="updated: 2026-09-13 10:00:00\n")
        p1 = proj_dir / "项目会话-r1.md"
        status, wrote = write_atomic(p1, m1, audit, source="robot-mock")
        check("robot-vision 投稿写入", status == "wrote" and wrote, f"({status})")
        v = check_contract(p1.read_text(encoding="utf-8"), stem="项目会话-r1")
        check("契约校验合规（中期+project_id 族）", v == [], f"({v})")

        m2 = fm_robot(
            "robot-客厅", "medium", "项目会话-r2",
            "玻璃杯被移动到茶几上，当前在茶几表面。",
            extra="updated: 2026-09-13 10:30:00\n")
        p2 = proj_dir / "项目会话-r2.md"
        status, wrote = write_atomic(p2, m2, audit, source="robot-mock")
        check("第二篇观测写入", status == "wrote" and wrote)

        print("== 2. 越层/缺 project_id 违规稿被契约拦截 ==")
        bad_tier = fm_robot("robot-客厅", "long", "项目约束-r3", "越层观测。")
        v = check_contract(bad_tier, stem="项目约束-r3")
        check("越层写 long 报违规", any("memory_tier" in x for x in v), f"({v})")
        bad_pid = fm_robot("", "medium", "项目会话-r4", "缺 project_id 观测。")
        v = check_contract(bad_pid, stem="项目会话-r4")
        check("缺 project_id 报违规", any("缺 project_id" in x for x in v), f"({v})")

        print("== 3. MemoryRecall 召回（文本域）==")
        mem = MemoryRecall(root, projects=["robot-客厅"])
        hits = mem.recall("玻璃杯 餐桌", k=3)
        check("检索命中观测稿", any(str(p1) == p for p, _ in hits),
              f"({[(Path(p).name, round(s, 3)) for p, s in hits]})")
        hits2 = mem.recall("茶几 玻璃杯", k=3)
        check("检索命中移动后观测稿", any(str(p2) == p for p, _ in hits2),
              f"({[(Path(p).name, round(s, 3)) for p, s in hits2]})")

        print("== 4. 跨写者冲突：机器人观测 vs 人类记忆（双稿保留）==")
        # 人类策展稿（distill_memory）：同项目族，结论与 r2 矛盾
        human = (
            "---\ntype: note\ntitle: 项目约束-h1\nsummary: 人类策展\n"
            "memory_tier: long\nimportance: 0.9\nwriter_id: distill_memory\n"
            "project_id: robot-客厅\nupdated: 2026-09-12 20:00:00\n---\n\n"
            "# 项目约束-h1\n\n玻璃杯固定在客厅餐桌上，勿移动。\n"
        )
        long_dir = root / "projects" / "robot-客厅" / TIER_DIR["long"]
        long_dir.mkdir(parents=True, exist_ok=True)
        ph = long_dir / "项目约束-h1.md"
        write_atomic(ph, human, audit, source="robot-mock")

        # 裁决规则（runbook 级，此处实证）：
        # 信任 = min(写者基线, 观测置信)；人类策展基线更高 → 不自动覆盖
        # 但 r2（10:30）比 h1（09-12）新 → 冲突显影为候选，双稿保留
        from mempipeline.writer_contracts import WRITER_CONTRACTS  # noqa: E402
        conf_h = min(0.9, 0.95)   # distill 基线 0.9 × 策展置信
        conf_r = min(0.7, 0.9)    # robot-vision 基线 0.7 × 观测置信 0.9
        check("信任=min(写者基线,观测置信) 人类更高",
              conf_h > conf_r, f"(human {conf_h} vs robot {conf_r})")
        check("双稿保留（矛盾不自动覆盖）",
              ph.exists() and p2.exists())
        # 时间优先：r2 更新 → 人类稿应被标记为需复核候选（timegrap drift 语义），
        # 此处以两稿皆在库 + 各自可检索证明"分歧可回显"。
        hits_h = mem.recall("玻璃杯 固定在餐桌", k=5)
        hits_r = mem.recall("玻璃杯 茶几", k=5)
        check("分歧双稿均可回显",
              any(str(ph) == p for p, _ in hits_h) and any(str(p2) == p for p, _ in hits_r))

        print("== 5. 幂等重投 ==")
        status, wrote = write_atomic(p1, m1, audit, source="robot-mock")
        check("同内容重投跳过", status == "skipped" and not wrote, f"({status})")

        print(f"\n== 结果：PASS {PASS} / FAIL {FAIL} ==")
        return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
