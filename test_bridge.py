# -*- coding: utf-8 -*-
"""test_bridge.py — E4' 通用投稿桥验收（零依赖，自带临时 fixtures）。

覆盖：
1. 双 schema 映射（G2）：vault 六字段齐全（type/summary/status/updated/tags）+ promoted→active
2. 资产化导出分流：projects/<pid>/ 与 global/
3. 幂等：重复导出跳过（文件名含 content_key）
4. 反向：vault 风格 md 可被 ingest 解析（memory_tier/project_id/kind）
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mempipeline.bridge import export_promoted, vault_frontmatter
from mempipeline.protocol import Note, TIER_DIR
from mempipeline.engine import write_atomic
from mempipeline.audit import FileAudit
from mempipeline.ingest import _parse_fm


def main() -> bool:
    ok = True

    def check(cond: bool, msg: str):
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {msg}")
        if not cond:
            ok = False

    # ---- 1. 双 schema 映射（G2）----
    print("== vault frontmatter 映射 ==")
    fm = vault_frontmatter({
        "title": "韩餐动线", "summary": "出餐口到餐桌", "memory_tier": "long",
        "importance": "0.9", "source_agent": "worker", "status": "promoted",
        "project_id": "dora", "domain": "project", "kind": "decision",
        "updated": "2026-08-29 13:00:00",
    })
    for field in ("type:", "summary:", "status:", "updated:", "tags:"):
        check(field in fm, f"vault 字段 {field} 存在")
    check('status: "active"' in fm, "promoted→active 映射（G8）")
    check("decision" in fm and "长期记忆" in fm, "tags 派生含 kind+层级")
    check('project_id: "dora"' in fm, "溯源字段 project_id 保留")
    check("aliases" not in fm, "aliases 不默认生成（避免噪声）")

    # ---- 2-3. 导出分流 + 幂等 ----
    print("== 资产化导出 ==")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        mem_root = tmp / "mem"
        vault_root = tmp / "vault"
        (mem_root / "projects" / "dora" / TIER_DIR["long"]).mkdir(parents=True)
        (mem_root / "projects" / "onequant" / TIER_DIR["long"]).mkdir(parents=True)
        (mem_root / TIER_DIR["long"]).mkdir(parents=True)
        audit = FileAudit(tmp / "audit" / "log.md", tmp / "audit" / "manifest.json", mem_root)

        # 项目笔记（promoted）
        n1 = Note(title="动线规则", summary="出餐口到餐桌要短", tier="long",
                  importance=0.9, body="正文A。", status="promoted",
                  project_id="dora", domain="project")
        n1.extra["kind"] = "decision"
        out1 = mem_root / "projects" / "dora" / TIER_DIR["long"] / "动线规则-a1.md"
        write_atomic(out1, n1.to_frontmatter() + "\n\n" + n1.body + "\n", audit)

        # 全局笔记（promoted，无 project_id → global 分流）
        n2 = Note(title="通用经验", summary="跨项目复用", tier="long",
                  importance=0.7, body="正文B。", status="promoted",
                  domain="global")
        n2.extra["kind"] = "lesson"
        out2 = mem_root / TIER_DIR["long"] / "通用经验-b2.md"
        write_atomic(out2, n2.to_frontmatter() + "\n\n" + n2.body + "\n", audit)

        # 非 promoted 不入导出
        n3 = Note(title="草稿中", summary="未审核", tier="medium",
                  importance=0.5, body="正文C。", status="candidate")
        out3 = mem_root / TIER_DIR["long"] / "草稿中-c3.md"
        write_atomic(out3, n3.to_frontmatter() + "\n\n" + n3.body + "\n", audit)

        st1 = export_promoted(mem_root, vault_root)
        p1 = vault_root / "99-DSP记忆" / "projects" / "dora"
        pg = vault_root / "99-DSP记忆" / "global"
        check(st1["exported"] == 2, f"导出 2 条（实得 {st1['exported']}）")
        check(len(list(p1.glob("*.md"))) == 1, "dora 项目笔记分流到 projects/dora")
        check(len(list(pg.glob("*.md"))) == 1, "全局笔记分流到 global")
        check(not any("草稿" in f.name for f in vault_root.rglob("*.md")),
              "candidate 不入导出")

        # 幂等：重复导出跳过
        st2 = export_promoted(mem_root, vault_root)
        check(st2["exported"] == 0 and st2["skipped"] == 2,
              f"重复导出全跳过（exported={st2['exported']}, skipped={st2['skipped']}）")

        # ---- 4. 反向：vault 风格 md 可被 ingest 解析 ----
        print("== 反向解析 ==")
        v_md = list(p1.glob("*.md"))[0]
        fm_back = _parse_fm(v_md.read_text(encoding="utf-8"))
        check(fm_back.get("tier") == "long", "vault 导出可回读 memory_tier（→tier）")
        check(fm_back.get("project_id") == "dora", "vault 导出可回读 project_id")
        check(fm_back.get("kind") == "decision", "vault 导出可回读 kind")

    print("\nBRIDGE (E4'):", "ALL PASS" if ok else "SOME FAILED")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
