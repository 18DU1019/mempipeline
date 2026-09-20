# -*- coding: utf-8 -*-
"""test_frontmatter.py — P0 frontmatter 读侧契约行为快照（零依赖，自运行 + pytest 双轨）。

规格来源：检验报告《mempipeline架构优化与重构方案_2026-09-21》第六节 P0 行为快照清单。
每次迁移阶段（P1-P6）后复跑本文件，防止解析行为漂移。

清单映射：
1. 跨行吞值回归（writer_contracts 历史 bug 真源语义）
2. DQ 反转义闭环（与写侧 _fmt_scalar 往返一致）
3. 单引号只剥不反转义
4. BOM 前缀容忍
5. 多块只取首个 vs 取全部
6. 无 frontmatter / 块不闭合 → 空 dict 不抛错
7. 正文含 `key:` 行不误当键
8. status 判定：引号/无引号两形态解析一致
9. 缺键 None / 空值 "" 语义区分
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mempipeline.protocol import _fmt_scalar, parse_frontmatter, parse_frontmatter_blocks


def test_crossline_swallow():
    # 1. 空值行后接下一行字段，不得把下一行吞成空值键的值（writer_contracts 历史 bug）
    fm = parse_frontmatter("---\nproject_id: \nconfidence_perception: 0.9\n---\n正文\n")
    assert fm.get("project_id") == "", f"空值键值应为 ''，实得 {fm.get('project_id')!r}"
    assert fm.get("confidence_perception") == "0.9", "下一行字段须独立成键"
    assert "confidence_perception" in fm


def test_dq_roundtrip():
    # 2. DQ 反转义闭环：写侧 _fmt_scalar 产物经解析器反转义往返一致
    for raw in ("a\nb", 'say "hi"', "back\\slash", "tab\there"):
        text = f"---\ntitle: {_fmt_scalar(raw)}\n---\n"
        assert parse_frontmatter(text)["title"] == raw, f"往返失真: {raw!r}"
    # 未知转义序列保留原样不丢信息
    fm = parse_frontmatter('---\nkey: "a\\qb"\n---\n')
    assert fm["key"] == "a\\qb", f"未知转义应保留字面，实得 {fm['key']!r}"


def test_single_quote_no_unescape():
    # 3. 单引号只剥不反转义
    fm = parse_frontmatter("---\nkey: 'a\\nb'\n---\n")
    assert fm["key"] == "a\\nb", f"单引号不反转义，实得 {fm['key']!r}"


def test_bom_tolerance():
    # 4. BOM 前缀容忍
    fm = parse_frontmatter("\ufeff---\ntitle: x\n---\n")
    assert fm == {"title": "x"}, f"BOM 容忍，实得 {fm!r}"


def test_multi_blocks():
    # 5. 多块：parse_frontmatter 只取首个，parse_frontmatter_blocks 取全部
    text = "---\na: 1\n---\n正文\n---\nb: 2\n---\n"
    assert parse_frontmatter(text) == {"a": "1"}
    blocks = parse_frontmatter_blocks(text)
    assert blocks == [{"a": "1"}, {"b": "2"}], f"全部块，实得 {blocks!r}"


def test_malformed_returns_empty():
    # 6. 无 frontmatter / 块不闭合 → 空 dict 不抛错
    assert parse_frontmatter("纯正文，没有围栏\n") == {}
    assert parse_frontmatter("---\ntitle: 未闭合\n") == {}
    assert parse_frontmatter("") == {}


def test_body_key_line_not_parsed():
    # 7. 正文含 `key:` 行不误当键（键名正则约束 + 只解析首个围栏内）
    fm = parse_frontmatter("---\ntitle: t\n---\n正文里 title: 不要当键\n")
    assert fm == {"title": "t"}, f"正文 key 行不误当键，实得 {fm!r}"
    # 键名白名单：非法键名（含空格/冒号）不解析
    fm2 = parse_frontmatter("---\nbad key: v\nok: y\n---\n")
    assert "bad key" not in fm2 and fm2.get("ok") == "y"


def test_status_quote_variants():
    # 8. status 判定：引号/无引号两形态解析一致
    assert parse_frontmatter('---\nstatus: "redacted"\n---\n')["status"] == "redacted"
    assert parse_frontmatter("---\nstatus: redacted\n---\n")["status"] == "redacted"
    assert parse_frontmatter("---\nstatus: 'active'\n---\n")["status"] == "active"


def test_missing_vs_empty():
    # 9. 缺键 = 不在 dict（None 语义由 fm.get() 天然获得）；空值键 = ""（区别于缺键）
    fm = parse_frontmatter("---\nproject_id: \n---\n")
    assert "project_id" in fm and fm["project_id"] == ""
    assert fm.get("writer_id") is None, "缺键 get() 应返回 None"


def main() -> bool:
    ok = True

    def check(cond: bool, msg: str):
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {msg}")
        if not cond:
            ok = False

    print("== frontmatter 读侧契约行为快照（P0）==")
    try:
        test_crossline_swallow()
        check(True, "1. 跨行吞值回归（空值 '' + 下一行独立成键）")
    except AssertionError as e:
        check(False, f"1. 跨行吞值回归（{e}）")
    try:
        test_dq_roundtrip()
        check(True, "2. DQ 反转义与写侧 _fmt_scalar 往返闭环")
    except AssertionError as e:
        check(False, f"2. DQ 反转义闭环（{e}）")
    try:
        test_single_quote_no_unescape()
        check(True, "3. 单引号只剥不反转义")
    except AssertionError as e:
        check(False, f"3. 单引号语义（{e}）")
    try:
        test_bom_tolerance()
        check(True, "4. BOM 前缀容忍")
    except AssertionError as e:
        check(False, f"4. BOM 容忍（{e}）")
    try:
        test_multi_blocks()
        check(True, "5. 多块只取首个 vs 取全部")
    except AssertionError as e:
        check(False, f"5. 多块语义（{e}）")
    try:
        test_malformed_returns_empty()
        check(True, "6. 无 frontmatter / 不闭合 → 空 dict 不抛错")
    except AssertionError as e:
        check(False, f"6. 畸形输入（{e}）")
    try:
        test_body_key_line_not_parsed()
        check(True, "7. 正文 key: 行不误当键 + 键名白名单")
    except AssertionError as e:
        check(False, f"7. 正文误判（{e}）")
    try:
        test_status_quote_variants()
        check(True, "8. status 引号/无引号两形态一致")
    except AssertionError as e:
        check(False, f"8. status 形态（{e}）")
    try:
        test_missing_vs_empty()
        check(True, "9. 缺键 None / 空值 '' 语义区分")
    except AssertionError as e:
        check(False, f"9. 缺键/空值语义（{e}）")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
