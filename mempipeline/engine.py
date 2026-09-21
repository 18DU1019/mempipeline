# -*- coding: utf-8 -*-
"""engine.py — 幂等写引擎：原子写 + 稳定正文判定 + 审计登记。数据无关。

与本地实现同构（distill_memory._write / _stable），但不绑定任何具体源/目标路径。
所有路径经 AuditBackend 与调用方注入。
"""
from __future__ import annotations

import atexit
import difflib
import os
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

from .protocol import stable_body
from .audit import AuditBackend, sha256


_BAK_SUFFIX = ".bak"


def _bak(out: Path) -> Path:
    return out.with_name(out.name + _BAK_SUFFIX)


_NEAR_MISS_KIND = "idempotent_near_miss"

# 位置感知分流（2026-09-21 拍板合题）：良性演进按字段计入进程级统计，退出时
# 一行汇总（防糊墙——实测单轮 175 条全良性）；正文区差异即时逐条显形不走此计数。
_near_miss_benign: Counter = Counter()
_near_miss_body = 0
_NEAR_DIFF_CAP = 64  # 差异行收集上限：仅用于位置分类，超限保守判 body_diff


def _near_miss_field(line: str) -> str:
    """差异行 → 字段名（良性事件按字段计数用）；非字段形态归 other。"""
    m = re.match(r"([A-Za-z_][A-Za-z0-9_-]*):", line.strip())
    return m.group(1) if m else "other"


def _fm_end_index(lines: list[str]) -> int:
    """首个 frontmatter 块的结束行号（0-based，即第二个 --- 所在行）；无块返 -1。"""
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return i
    return -1


def _report_near_miss_stats() -> None:
    """进程退出汇总：良性事件一行统计；正文差异已逐条显形，此处仅计数复核。"""
    total = sum(_near_miss_benign.values())
    if not total and not _near_miss_body:
        return
    fields = " ".join(f"{k}:{v}" for k, v in _near_miss_benign.most_common()) or "-"
    print(f"[{_NEAR_MISS_KIND}] 进程汇总: benign={total}({fields}) "
          f"body_diff={_near_miss_body}(正文区差异已逐条显形)；"
          f"逐条见审计链 class 段", file=sys.stderr)


atexit.register(_report_near_miss_stats)


def _near_miss_detail(old_raw: str, new_raw: str) -> tuple[str, bool, Counter]:
    """near-miss 机读摘要 + 位置感知分流判定（2026-09-21 拍板合题）。

    返回 (detail, benign, fields)：
    - detail：行数差 + 首处差异行（截 60 字符，不落全文）+ 差异行数 + 分流类别，
      供 stderr 告警行与审计链 detail 字段共用；竖线/换行压平，防破坏
      FileAudit 竖线分隔的 append-only 日志行。
    - benign：位置感知分流判定。stable 相等只保证差异行全部命中剔除前缀，但
      剔除是全篇 startswith（正文同名行同剔）——正文里以剔除前缀开头的行
      （如正文引用示例）被吞属 N03 威胁形态「静默丢内容」。故全部差异行均落
      在首个 frontmatter 块内 → 设计意图内演进（benign=True）；任一差异行在
      正文区 → benign=False，stderr 必须逐条显形。仅行尾风格/尾随换行差异
      （无逐行差异）视为 benign。差异行收集封顶 _NEAR_DIFF_CAP，超限保守判
      body_diff（显形无害，静音才危险）。
    - fields：全部差异行的字段名计数（良性事件按字段汇总用）。

    对齐算法（2026-09-21 首跑教训）：初版逐行 idx 配对，FM 模板字段演进使行数差
    ±1/-2 时错位点之后全部行错配判差（首跑 benign=3/body_diff=171，真实差异全在
    FM 内却几乎全判 body_diff 糊墙）——改 difflib.SequenceMatcher 对齐，只把真实
    replace/insert/delete 块的行算差异行。autojunk 关闭防 "---" 等高频行被当
    junk 产生错误对齐。
    """
    old_ln, new_ln = old_raw.splitlines(), new_raw.splitlines()
    delta = len(new_ln) - len(old_ln)
    fm_end_old, fm_end_new = _fm_end_index(old_ln), _fm_end_index(new_ln)
    diff_rows: list[tuple[int, str]] = []  # (行号 0-based, 展示文本)
    body_diff = False
    sm = difflib.SequenceMatcher(a=old_ln, b=new_ln, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        # 真实差异块：replace 两侧逐位配对，insert 只有新侧，delete 只有旧侧
        for k in range(max(i2 - i1, j2 - j1)):
            new_row = new_ln[j1 + k] if j1 + k < j2 else "<缺行>"
            old_row = old_ln[i1 + k] if i1 + k < i2 else "<缺行>"
            shown = (new_row if new_row != "<缺行>" else old_row).strip()[:60]
            idx = j1 + k if j1 + k < j2 else i1 + k
            diff_rows.append((idx, shown))
            # 位置判定：行存在侧任一落在正文区（FM 块结束后）即 body_diff；
            # 缺行侧天然不判（存在侧分支已覆盖），双侧异常形态保守显形。
            if (i1 + k < i2 and i1 + k > fm_end_old) or \
               (j1 + k < j2 and j1 + k > fm_end_new):
                body_diff = True
            if len(diff_rows) >= _NEAR_DIFF_CAP:
                body_diff = True  # 差异面超分类预算：保守显形
                break
        if len(diff_rows) >= _NEAR_DIFF_CAP:
            break
    fields: Counter = Counter()
    for _idx, shown in diff_rows:
        fields[_near_miss_field(shown)] += 1
    if diff_rows:
        first_idx, first = diff_rows[0]
        first = " ".join(first.split()).replace("|", "/") or "<空行差异>"
    else:
        # raw 不等但逐行相等：差异仅在行尾风格/尾随换行（splitlines 视角不可见）
        first_idx, first = 0, "<仅行尾风格或尾随换行差异>"
    cls = "body_diff" if body_diff else "benign"
    detail = (f"lines_delta={delta:+d} first_diff=L{first_idx + 1}:{first} "
              f"diffs={len(diff_rows)} class={cls}")
    return detail, not body_diff, fields


def write_atomic(out: Path, text: str, audit: AuditBackend,
                 source: str = "engine", skip_if_same: bool = True) -> tuple[str, bool]:
    """幂等原子写：目标已存在且稳定正文相同 → 跳过（返回 ("skipped", False)）。

    原子性：临时文件 + os.replace，读者只会见全旧或全新，杜绝半截读。
    崩溃恢复 sidecar：为防止进程在 os.replace 之后、审计标记之前中断而留下
    「新文件已落、审计未登记」的不一致，写入前把目标前一版快照为 `.bak`
    sidecar；成功路径删除，崩溃/异常路径保留为恢复点。下次写前检测到残留
    `.bak` 即登记一条 `recover` 审计（把崩溃遗留显式化），再清理。

    幂等跳过时无新增写入，不产生 sidecar；若残留 `.bak` 指向的正是当前内容，
    一并登记后清理，保持镜像无累积性侧车。

    V2-3 near-miss 观测（2026-09-21 三标尺检验 N03）：raw 不等但 stable 相等
    （差异全落在被剔除的幂等不敏感行上，如仅 certainty/updated 演进）时仍跳过
    （默认语义不破坏、告警不阻断），另发 stderr 机读告警并登记
    `idempotent_near_miss` 审计事件（含两文本行数差与首处差异行摘要，不落全文），
    把「误判重复 → 静默丢内容」类失效变成可计数事件。

    返回 (status, wrote)。
    """
    global _near_miss_body  # augassign 模块级计数器；漏声明会被 skip 块 except 吞成落写路径
    out.parent.mkdir(parents=True, exist_ok=True)
    bak = _bak(out)
    if skip_if_same and out.exists():
        try:
            old_raw = out.read_text(encoding="utf-8")
            if stable_body(old_raw) == stable_body(text):
                if old_raw != text:
                    # V2-3（N03）+ 位置感知分流（2026-09-21 拍板合题）：near-miss =
                    # 差异全在被剔除行上。按差异行位置分流 stderr 人读面：良性演进
                    # （差异行全在 FM 块内）不再逐条打印（实测单轮 175 条全良性已
                    # 糊墙），按字段计数入进程级统计、退出时一行汇总；正文区差异
                    # （N03 要抓的「正文同名行被吞」形态）立即逐条显形。审计链事件
                    # 保持逐条不变（detail 带 class 段可 grep 对账）；旧式 mark 签名
                    # 后端降级为仅登记事件（detail 丢失，事件仍可计数）。
                    detail, benign, fields = _near_miss_detail(old_raw, text)
                    if benign:
                        _near_miss_benign.update(fields)
                    else:
                        _near_miss_body += 1
                        print(f"[{_NEAR_MISS_KIND}] file={out.name} {detail}",
                              file=sys.stderr)
                    try:
                        audit.mark(out, _NEAR_MISS_KIND, source=source, detail=detail)
                    except TypeError:
                        audit.mark(out, _NEAR_MISS_KIND, source=source)
                audit.mark(out, "skip", source=source, change=None)
                _clear_stale_recover(out, bak, audit, source)
                return "skipped", False
        except Exception:
            pass
    # 崩溃/中断遗留检测：上一轮未清理的 `.bak` = 一次未完成写入的恢复点
    if bak.exists():
        audit.mark(out, "recover", source=source)
        try:
            bak.unlink(missing_ok=True)
        except OSError:
            pass
    # 写前快照前一版为恢复点（目标已存在时）；P1-3：同步记写前指纹入审计版本链
    prev_hash: str | None = None
    if out.exists():
        bak.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out, bak)
        prev_hash = sha256(out)  # 覆盖前的上一版全文指纹（sha256），配合快照/备份可回滚定位
    tmp = out.with_name(out.name + ".tmp-%d" % os.getpid())
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, out)
    except Exception:
        # 异常路径：保留 `.bak` 作为恢复点，供下次检测登记；仅清理临时碎片
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    # 成功：先登记审计，再销毁恢复点，保证一致性窗口可追溯。
    audit.mark(out, "write", source=source, prev_hash=prev_hash)
    try:
        bak.unlink(missing_ok=True)
    except OSError:
        pass
    return "wrote", True


def _clear_stale_recover(out: Path, bak: Path, audit: AuditBackend, source: str) -> None:
    """幂等跳过分支的遗留清理：若残留 `.bak` 内容与当前目标一致，登记后删除。

    幂等分支不会重写目标，故这里只清理、不覆盖；内容不一致的 `.bak`（一个
    真正失败的恢复点）保留，留给下次实际写入时的 recover 检测处理。
    """
    if not bak.exists():
        return
    try:
        if bak.read_bytes() == out.read_bytes():
            audit.mark(out, "recover", source=source)
            bak.unlink(missing_ok=True)
    except OSError:
        pass
