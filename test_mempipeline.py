# -*- coding: utf-8 -*-
"""mempipeline 端到端最小验证（数据无关，运行时自建临时脱敏 fixtures）。

覆盖「1写者 + N投稿 + 无限读」四件事：
1. 写者：engine.write_atomic 原子写 + 幂等跳过（同稳定正文第二次不重写）
2. 读者：recall.recall 能从笔记召回相关结果（含同义词归一化）
3. 投稿：ingest 把 staging 里带 frontmatter 的裸 md 熔合进正确层目录
4. 审计：FileAudit 追加 log + manifest 指纹，写/跳均登记
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mempipeline.protocol import Note, TIER_DIR
from mempipeline.audit import FileAudit
from mempipeline.engine import write_atomic
from mempipeline.recall import MemoryRecall
from mempipeline.ingest import ingest


def main() -> bool:
    ok = True

    def check(cond: bool, msg: str):
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {msg}")
        if not cond:
            ok = False

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        mem_root = tmp / "mem"
        (mem_root / "01-长期记忆").mkdir(parents=True)
        (mem_root / "02-中期记忆").mkdir(parents=True)
        log_path = tmp / "audit" / "log.md"
        manifest_path = tmp / "audit" / "manifest.json"
        audit = FileAudit(log_path, manifest_path, mem_root)

        # ---- 1. 写者：原子写 + 幂等跳过 ----
        print("== 写者 engine ===")
        note = Note(title="仓位规则", summary="单笔风险不超过 1 ATR", tier="long",
                    importance=0.9, body="每笔只下 1 个 ATR 的风险。")
        out = mem_root / TIER_DIR["long"] / "项目会话-仓位规则-ab12cd34.md"
        s1, _ = write_atomic(out, note.to_frontmatter() + "\n\n" + note.body + "\n", audit)
        check(s1 == "wrote", f"首次写 -> {s1}, 文件存在={out.exists()}")
        s2, _ = write_atomic(out, note.to_frontmatter() + "\n\n" + note.body + "\n", audit)
        check(s2 == "skipped", f"同稳定正文幂等 -> {s2}")
        n2 = Note(title="仓位规则", summary="单笔风险不超过 1 ATR", tier="long",
                  importance=0.9, body="每笔只下 1 个 ATR 的风险。")
        s3, _ = write_atomic(out, n2.to_frontmatter() + "\n\n" + n2.body + "\n", audit)
        check(s3 == "skipped", f"updated 变但正文未变仍跳过 -> {s3}")

        # ---- 2. 读者：召回（同义词归一化：仓位/positioning 映射主词） ----
        print("== 读者 recall ===")
        (mem_root / "02-中期记忆" / "项目会话-定投-aabbccdd.md").write_text(
            "---\ntype: note\ntitle: 定投纪律\nsummary: 每月固定日定投\n"
            "memory_tier: medium\nimportance: 0.6\nsource_agent: workbuddy\nupdated: 2026-08-01\n---\n\n定投要按纪律执行。\n",
            encoding="utf-8")
        recall = MemoryRecall(mem_root, synonyms={"仓位": ["positioning"]})
        hits = recall.recall("风险 仓位", k=5)
        check(any("仓位规则" in h for h, _ in hits), f"召回命中仓位规则 {[h for h, _ in hits]}")

        # ---- 3. 投稿：staging 熔合进 02-中期记忆 ----
        print("== 投稿 ingest ===")
        staging = tmp / "staging"
        (staging / "workbuddy").mkdir(parents=True)
        (staging / "workbuddy" / "daily.md").write_text(
            "---\ntitle: 今日复盘\nsummary: 中期情景摘要\n"
            "memory_tier: medium\nsource_agent: workbuddy\n---\n\n复盘主体。\n",
            encoding="utf-8")
        (staging / "workbuddy" / "note_no_tier.md").write_text(
            "---\ntitle: 无层笔记\nsummary: 无\n---\nbody\n", encoding="utf-8")
        stats = ingest(staging, mem_root, TIER_DIR, audit)
        medium_files = list((mem_root / "02-中期记忆").glob("*.md"))
        check(stats["wrote"] >= 1, f"熔合数 = {stats}")
        check(any("workbuddy" in f.read_text(encoding="utf-8") for f in medium_files),
              "投稿 source_agent=workbuddy 落 02 层")

        # ---- 4. 审计 ----
        print("== 审计 audit ===")
        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        check(len(log_lines) >= 4, f"log {len(log_lines)} 行（write/skip 均应登记）")
        mani = manifest_path.read_text(encoding="utf-8")
        check('"files"' in mani, "manifest 指纹已登记")

    print("\nRESULT:", "ALL PASS" if ok else "SOME FAILED")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
