# -*- coding: utf-8 -*-
"""test_auto_commit.py — 本地提交门禁入口（one delivery one commit）单元验证。

范围（纯沙盒 git 仓库，不触碰真实镜像/不推远端）：
1. semantic_closure —— 暂存区为空 → 阻塞；索引==工作区 → 闭合；同文件仍有
   未暂存改动 → 阻塞（半成品快照判定）。
2. commit —— 精确路径限定本地提交成功；绝不调用 push（无 --dry-run 额外副作用）。
3. main dry-run —— 只判定不提交（返回 0，HEAD 不变）。
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# pre-commit 下运行时的隔离修复（2026-09-14 实测假红根因）：git 钩子会给子进程
# 注入 GIT_DIR / GIT_INDEX_FILE 等环境变量，沙盒临时仓库的 git 命令会被其劫持到
# 真实仓库索引上（如"空暂存"读到真实暂存区），测试必假红。测试进程内统一剥离。
for _k in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_PREFIX",
           "GIT_OBJECT_DIR", "GIT_COMMON_DIR"):
    os.environ.pop(_k, None)

from mempipeline.auto_commit import semantic_closure, commit, \
    main as _autocommit_main


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, encoding="utf-8")


def _init_repo(repo: Path) -> None:
    _git(repo, "init", "-q", "-b", "master")
    # 干净仓库默认无 pre-commit（仅 .sample），保证沙盒提交不触发真实门禁副作用。
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")


def main() -> bool:
    ok = True

    def check(cond, msg):
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {msg}")
        if not cond:
            ok = False

    # ---- 1. 暂存区为空 → 阻塞 ----
    print("== semantic_closure：空暂存 ==")
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        _init_repo(repo)
        closed, reason, staged = semantic_closure(repo)
        check(not closed and staged == [] and "暂存区为空" in reason,
              f"空暂存阻塞 → {reason}")

    # ---- 2. 索引==工作区 → 闭合 ----
    print("== semantic_closure：闭合交付 ==")
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        _init_repo(repo)
        f = repo / "a.md"
        f.write_text("---\ntitle: A\n---\n甲。\n", encoding="utf-8")
        _git(repo, "add", "a.md")
        closed, reason, staged = semantic_closure(repo)
        check(closed and opted_ok(reason) and "a.md" in staged,
              f"闭合通过 → {reason}, {staged}")

    # ---- 3. staged 后仍有未暂存改动 → 阻塞（半成品） ----
    print("== semantic_closure：同文件半成品 ==")
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        _init_repo(repo)
        f = repo / "a.md"
        f.write_text("---\ntitle: A\n---\n甲。\n", encoding="utf-8")
        _git(repo, "add", "a.md")
        f.write_text("---\ntitle: A\n---\n甲·已改未暂存。\n", encoding="utf-8")  # 后续改动
        closed, reason, _ = semantic_closure(repo)
        check(not closed and "未暂存改动" in reason, f"半成品阻塞 → {reason}")

    # ---- 4. commit：精确路径本地提交，HEAD 前进，未 push ----
    print("== commit：本地提交成功 ==")
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        _init_repo(repo)
        f = repo / "b.md"
        f.write_text("---\ntitle: B\n---\n乙。\n", encoding="utf-8")
        _git(repo, "add", "b.md")
        _git(repo, "commit", "-q", "-m", "base", "--", "b.md")
        head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()

        g = repo / "c.md"
        g.write_text("---\ntitle: C\n---\n丙。\n", encoding="utf-8")
        _git(repo, "add", "c.md")
        code, out, err = commit(repo, ["c.md"], "deliver")
        head_after = _git(repo, "rev-parse", "HEAD").stdout.strip()
        bytes_out = (out + err)
        check(code == 0 and head_before != head_after,
              "commit 成功且 HEAD 前进")
        check("push" not in bytes_out.lower(),
              "commit 输出不含 push 动作（绝不自动 push）")

    # ---- 5. main dry-run：只判定不提交，HEAD 不变 ----
    print("== main --dry-run：不提交 ==")
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        _init_repo(repo)
        f = repo / "d.md"
        f.write_text("---\ntitle: D\n---\n丁。\n", encoding="utf-8")
        _git(repo, "add", "d.md")
        head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
        rc = _autocommit_main(["--repo", str(repo), "--message", "dry", "--dry-run"])
        head_after = _git(repo, "rev-parse", "HEAD").stdout.strip()
        check(rc == 0 and head_before == head_after,
              "dry-run 返回 0 且未改变 HEAD")

    # ---- 6. main --inspect：空暂存 rc0 / 半成品 rc1 且给补救建议 ----
    print("== main --inspect：巡检 ==")
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        _init_repo(repo)
        rc0 = _autocommit_main(["--repo", str(repo), "--inspect"])
        check(rc0 == 0, "空暂存巡检 rc 0（无阻塞）")
        file = repo / "wip.md"
        file.write_text("---\ntitle: W\n---\n甲。\n", encoding="utf-8")
        _git(repo, "add", "wip.md")
        file.write_text("---\ntitle: W\n---\n甲·再改。\n", encoding="utf-8")
        rc1 = _autocommit_main(["--repo", str(repo), "--inspect"])
        check(rc1 == 1, "半成品巡检 rc 1（未闭合）")

    print("AUTO_COMMIT_TESTS:", "PASS" if ok else "FAIL")
    return ok


def opted_ok(reason: str) -> bool:
    """闭合通过时 reason 应为 'ok'（独立小函数便于 check 内联）。"""
    return reason == "ok"


if __name__ == "__main__":
    sys.exit(0 if main() else 1)