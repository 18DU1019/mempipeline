# -*- coding: utf-8 -*-
"""auto_commit.py — git 本地提交门禁入口（one delivery one commit）。

把「交付完成度」落成一条可调用的本地提交入口：仅当暂存区构成一个语义闭合的
交付、且 pre-commit 门禁绿时，才执行本地提交；否则阻塞并给出阻塞原因。
绝不自动 push（push 留人工）。

对齐跨会话提交纪律，不重造引擎：
- 精确提交：提交路径严格限定在 staged 清单内，绝不 `git add .`/`add -A`/
  `--no-verify`（pre-commit 由 git 原生执行，未绿即中止，天然复用既有门禁）。
- 语义闭合：staged 文件非空，且每个 staged 文件的索引版本 == 工作区版本
  （无「改了一半还没补充暂存」的半成品快照）。
- 可逆/薄：不引新依赖，只编排既有 `git` 与 `.githooks/pre-commit`。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Callable

_GIT = ["git"]
_SH = Callable[[list[str], Path], subprocess.CompletedProcess]


def _run(git_args: list[str], repo: Path) -> subprocess.CompletedProcess:
    """在仓库根执行 git 命令并返回结果（不抛异常，供调用方判读）。"""
    return subprocess.run(
        [*_GIT, "-C", str(repo), *git_args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def staged_files(repo: Path) -> list[str]:
    """暂存区文件清单（含 新增/修改/删除/重命名），顺序稳定。"""
    r = _run(["diff", "--cached", "--name-only", "-z"], repo)
    if r.returncode != 0:
        return []
    return [p for p in r.stdout.split("\0") if p]


def _unstaged_files(repo: Path) -> list[str]:
    """工作区相对索引的未暂存改动文件（用于发现同文件半成品）。"""
    r = _run(["diff", "--name-only", "-z"], repo)
    if r.returncode != 0:
        return []
    return [p for p in r.stdout.split("\0") if p]


def semantic_closure(repo: Path) -> tuple[bool, str, list[str]]:
    """判定暂存区是否构成语义闭合交付。返回 (通过?, 原因, staged 清单)。

    - 暂存区为空 → 阻塞（无交付可提交）。
    - 存在「已 staged 但仍有未暂存改动」的同文件 → 阻塞（快照非最终版，
      提示先 `git add` 补齐，或 `git restore --staged` 拆分为独立交付）。
    """
    staged = staged_files(repo)
    if not staged:
        return False, "暂存区为空：无交付可提交（先精确 git add <files>）", staged
    overlap = [p for p in staged if p in _unstaged_files(repo)]
    if overlap:
        return (False,
                "下列 staged 文件仍有未暂存改动，快照非最终版，请先 git add 补齐"
                f"或 git restore --staged 拆分：{' '.join(overlap)}",
                staged)
    return True, "ok", staged


def _default_message(staged: list[str]) -> str:
    """staged 未提供 --message 时的默认提交消息（可被 --message 覆盖）。"""
    base = "存储交付" if len(staged) == 1 else f"内部交付（{len(staged)} 文件）"
    rel = staged[0] if len(staged) == 1 else ", ".join(staged)
    return f"chore(mempipeline): {base} — {rel}"


def commit(repo: Path, files: list[str], message: str) -> tuple[int, str, str]:
    """执行本地提交（精确路径限定，git 原生跑 pre-commit）。绝不 push。

    返回 (returncode, stdout, stderr)。pre-commit 未绿时 git 会中止提交，
    returncode 非 0，stdout/stderr 中带门禁失败输出 —— 即阻塞于此并给 reason。
    """
    r = _run(["commit", "-m", message, "--", *files], repo)
    return r.returncode, r.stdout, r.stderr


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="mempipeline 本地提交门禁入口")
    p.add_argument("--repo", default=".", help="git 仓库根（默认当前目录）")
    p.add_argument("--message", "-m", default=None, help="提交消息（缺省自动生成）")
    p.add_argument("--dry-run", action="store_true",
                   help="只做门禁判定与预览，不实际提交")
    a = p.parse_args(argv)

    repo = Path(a.repo).resolve()
    if not (repo / ".git").exists() and not (repo / ".git").is_dir():
        print(f"[auto-commit] 阻塞：{repo} 不是 git 仓库根")
        return 2

    ok, reason, staged = semantic_closure(repo)
    if not ok:
        print(f"[auto-commit] 阻塞：{reason}")
        return 1

    message = a.message or _default_message(staged)
    print(f"[auto-commit] 暂存区语义闭合：{len(staged)} 个文件")
    for f in staged:
        print(f"    {f}")
    print(f"[auto-commit] 提交消息：{message}")

    if a.dry_run:
        print("[auto-commit] dry-run：未提交（真实提交时 git 将先跑 pre-commit，未绿即中止）")
        return 0

    code, out, err = commit(repo, staged, message)
    if code != 0:
        detail = (out or err).strip()
        print(f"[auto-commit] 阻塞：pre-commit 门禁未绿，未提交\n{detail}")
        return 1
    print(f"[auto-commit] 已本地提交（未 push，push 留人工）\n{out.strip()}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))