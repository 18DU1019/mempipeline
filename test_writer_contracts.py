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