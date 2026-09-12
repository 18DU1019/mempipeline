# -*- coding: utf-8 -*-
"""test_writer_contracts.py — E2 写者约束数据化验收（零依赖，自带临时 fixtures）。

覆盖：
1. check_contract：distill 项目族缺/带 project_id、画像族禁带、staging 透传不强制、
   tier 违例、writer_id 缺失
2. non_governable_writers：分发（staging 不可治理、distill 可治理）
3. governance 过滤：scan_stale_notes 注入 non_governable_writers 后剔除 staging 候选
"""
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mempipeline.writer_contracts import (
    check_contract, writer_of, non_governable_writers,
)
from mempipeline.governance import scan_stale_notes


def _fm(writer: str, tier: str, title: str, project_id: str | None = None,
        updated_days_ago: int = 400) -> str:
    lines = [
        "---",
        f"type: note",
        f"title: {title}",
        f"summary: 测试笔记 {title} 的内容正文。",
        f"memory_tier: {tier}",
        f"importance: 0.3",
        f"writer_id: {writer}",
    ]
    if project_id:
        lines.append(f"project_id: {project_id}")
    if writer == "staging_ingest":
        lines.append("source_staging: workbuddy")
    lines.append("status: active")
    lines.append("updated: " + (datetime.now() - timedelta(days=updated_days_ago)).strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    return "\n".join(lines) + "\n正文内容。\n"


def main() -> bool:
    ok = True

    def check(cond: bool, msg: str):
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {msg}")
        if not cond:
            ok = False

    # ---- 1. check_contract ----
    print("== check_contract ==")
    # distill 项目约束族，带 project_id → 合规
    v = check_contract(_fm("distill_memory", "long", "项目约束-abc", "abc"), stem="项目约束-abc")
    check(v == [], f"distill 约束族带 project_id 合规（violations={v}）")
    # distill 项目会话族，缺 project_id → 违规
    v = check_contract(_fm("distill_memory", "medium", "项目会话-xyz"), stem="项目会话-xyz")
    check(any("缺 project_id" in x for x in v), f"distill 会话族缺 project_id 报违规（{v}）")
    # distill 用户画像族，不带 project_id → 合规
    v = check_contract(_fm("distill_memory", "long", "用户画像"), stem="用户画像")
    check(v == [], f"distill 画像族无 project_id 合规（{v}）")
    # distill 用户画像族误带 project_id → 违规
    v = check_contract(_fm("distill_memory", "long", "用户画像", "xx"), stem="用户画像")
    check(any("不应带" in x for x in v), f"distill 画像族误带 project_id 报违规（{v}）")
    # staging 透传：无 project_id 不强制 → 合规
    v = check_contract(_fm("staging_ingest", "medium", "项目会话-外部稿"), stem="项目会话-外部稿")
    check(v == [], f"staging 无 project_id 合规（{v}）")
    # staging 越层写 long → 违规
    v = check_contract(_fm("staging_ingest", "long", "项目会话-越界"), stem="项目会话-越界")
    check(any("memory_tier" in x for x in v), f"staging 越层写 long 报违规（{v}）")
    # 缺 writer_id → 违规（构造无该字段的裸文本）
    no_wid = _fm("distill_memory", "long", "项目约束-x", "x").replace("writer_id: distill_memory\n", "")
    v = check_contract(no_wid, stem="项目约束-x")
    check(any(x == "writer_id 缺失" for x in v), f"缺 writer_id 报违规（{v}）")
    # 未知写者（未登记）带 writer_id + 合法层 → 兜底不误报（_DEFAULT allowed_tiers 空）
    alien = _fm("some_future_writer", "medium", "项目会话-y", "y").replace(
        "project_id: y\n", "")
    v = check_contract(alien, stem="项目会话-y")
    check(not any("memory_tier" in x for x in v) and v == [],
          f"未登记写者兜底不误报（{v}）")
    # 无 frontmatter → 明确报缺
    v = check_contract("纯正文，无 frontmatter 块\n", stem="项目约束-z")
    check(v == ["frontmatter 缺失"], f"无 frontmatter 报缺（{v}）")
    # 空字段跨行吞噬回归：`project_id: `(空值) 后跟下一行字段，不得把下一行
    # 吞成 project_id（实测旧 `\s*(.+?)\s*$` 形态会把 confidence_perception 吞入，
    # 导致缺 project_id 判定失效）
    empty_pid = _fm("robot-vision", "medium", "项目会话-e1").replace(
        "status: active", "project_id: \nstatus: active", 1)
    v = check_contract(empty_pid, stem="项目会话-e1")
    check(any("缺 project_id" in x for x in v),
          f"空 project_id 字段不跨行吞噬下一行（{v}）")

    # ---- 1b. _DEFAULT 兜底不可变（set 共享引用防污染回归） ----
    print("== _DEFAULT 不可变 ==")
    from mempipeline import writer_contracts as wc
    base_default = wc._DEFAULT
    check(isinstance(base_default["allowed_tiers"], frozenset),
          f"_DEFAULT.allowed_tiers 为 frozenset（{type(base_default['allowed_tiers']).__name__}）")
    # 读侧拿到的兜底契约与 _DEFAULT 非同一 dict（浅拷贝隔离）
    alien2 = _fm("another_future_writer", "medium", "项目会话-w")
    contract = wc.WRITER_CONTRACTS.get("another_future_writer", dict(base_default))
    check(contract is not base_default, "兜底契约是浅拷贝新 dict（非共享同一对象）")
    check(contract["allowed_tiers"] is base_default["allowed_tiers"],
          "frozenset 值可安全共享（不可变，无变异风险）")

    # ---- 1c. robot-vision 契约（双写者） ----
    print("== robot-vision 契约 ==")
    # 机器人观测：中期 + 项目族带 project_id → 合规
    v = check_contract(_fm("robot-vision", "medium", "项目会话-r1", "robot-客厅"), stem="项目会话-r1")
    check(v == [], f"robot-vision 中期+项目族带 project_id 合规（{v}）")
    # 机器人越层写 long → 违规
    v = check_contract(_fm("robot-vision", "long", "项目会话-r2", "robot-客厅"), stem="项目会话-r2")
    check(any("memory_tier" in x for x in v), f"robot-vision 越层写 long 报违规（{v}）")
    # 机器人缺 project_id → 违规（观测稿必须归属项目族）
    v = check_contract(_fm("robot-vision", "medium", "项目会话-r3"), stem="项目会话-r3")
    check(any("缺 project_id" in x for x in v), f"robot-vision 缺 project_id 报违规（{v}）")
    # robot-vision 可治理 → 不在 non_governable 集合
    check("robot-vision" not in non_governable_writers(),
          "robot-vision 可治理（不进 non_governable）")

    # ---- 2. non_governable_writers ----
    print("== non_governable_writers ==")
    ng = non_governable_writers()
    check("staging_ingest" in ng and "distill_memory" not in ng,
          f"staging 不可治理、distill 可治理（ng={ng}）")

    # ---- 3. governance 过滤 ----
    print("== governance 过滤 ==")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        long_d = root / "01-长期记忆"
        medium_d = root / "02-中期记忆"
        long_d.mkdir()
        medium_d.mkdir()
        (long_d / "项目约束-aa.md").write_text(_fm("distill_memory", "long", "项目约束-aa", "aa"), encoding="utf-8")
        (medium_d / "项目会话-bb.md").write_text(_fm("staging_ingest", "medium", "项目会话-bb"), encoding="utf-8")
        both = scan_stale_notes(root)
        has_staging = any("项目会话-bb" in c.get("path", "") for c in both)
        check(has_staging, "未过滤时 staging 候选在列")
        filtered = scan_stale_notes(root, non_governable_writers=non_governable_writers())
        paths = [c.get("path", "") for c in filtered]
        check(not any("项目会话-bb" in p for p in paths) and any("项目约束-aa" in p for p in paths),
              f"过滤后剔除 staging、保留 distill（{len(filtered)} 条）")

    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)