# -*- coding: utf-8 -*-
"""test_stable_body.py — B1 口径统一后 stable_body 行为快照（零依赖，自运行 + pytest 双轨）。

规格来源：检验报告《mempipeline幂等口径统一方案_2026-09-21》第四节方案一。
每次 stable_body 口径改动后复跑本文件，防止与 dm._stable 同构语义漂移。

清单映射：
1. 7 类前缀行剔除（FM 内 + 正文同名行，与 dm._stable 冻结语义一致）
2. 无 frontmatter 原样返回（字节级）
3. 剔除后行序不变、其余行逐字保留
4. 块尾换行保真（旧 B 实现会吃块尾换行的历史坑，回归快照）
5. CRLF 输入按 \n 归一（与 dm splitlines 行为一致）
6. strip 参数覆盖：() 关闭 / 自定义剔除集
7. 幂等端到端：updated/created/writer_id 变化不改变 content_key
8. content_key 截断长度 = 8（对齐 A 栈 staging_ingest；6 位 hex 是 project_id 非内容 key）
9. 冻结样例字节对比（行为快照锚）
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mempipeline.protocol import STRIP_PREFIXES, content_key, stable_body

_FM = "---\ntitle: t\nsummary: s\nupdated: 2026-01-01 00:00:00\ncreated: 2026-01-01\ncreated_src: backfill\nproject_id: p1\nwriter_id: workbuddy\ncertainty: 0.8\nlast_active: 2026-09-21\n---\n"
_BODY = "# 标题\n\n正文第一段。\nupdated: 正文同名行也剔\ncreated: 正文同名行\n\n尾段。\n"


def test_seven_prefixes_stripped():
    # 1. FM 内 7 类字段行全部剔除，普通字段保留
    out = stable_body(_FM + "body\n")
    for f in STRIP_PREFIXES:
        assert not any(ln.startswith(f) for ln in out.splitlines()), f"未剔除: {f}"
    assert "title: t" in out and "summary: s" in out


def test_body_same_name_lines_stripped():
    # 1b. 正文同名行同样剔除（dm._stable 冻结语义；2026-09-21 实测全库 0 篇命中）
    out = stable_body(_FM + _BODY)
    assert "updated: 正文同名行也剔" not in out
    assert "created: 正文同名行" not in out
    assert "正文第一段" in out


def test_no_frontmatter_passthrough():
    # 2. 无 frontmatter：无剔除前缀行时内容不丢。与 dm 同构语义 = 输出相等，
    #    splitlines/join 剥尾随 \n 是两侧一致行为（key 不受影响）
    text = "# 标题\n\n普通正文，无前缀行。"
    assert stable_body(text) == text
    assert stable_body(text + "\n") == text


def test_line_order_and_content_preserved():
    # 3. 剔除后行序不变，其余行逐字保留
    text = "a\ncreated: x\nb\nupdated: y\nc\n"
    assert stable_body(text) == "a\nb\nc"


def test_block_tail_newline_preserved():
    # 4. 块尾结构保真：旧 B 实现 text[m.end():] 会丢块尾换行（'---# 标题'），
    #    新实现与 dm 同构自然保真。注意 splitlines/join 固有剥尾随 \n
    #    （A 栈冻结语义的一部分，两侧一致，key 不受影响）
    text = "---\nupdated: x\n---\n# 标题\n"
    assert stable_body(text) == "---\n---\n# 标题"


def test_crlf_normalized():
    # 5. CRLF 输入 → \n 归一（与 dm splitlines 行为一致，行为快照）
    text = "---\r\nupdated: x\r\n---\r\nbody\r\n"
    assert stable_body(text) == "---\n---\nbody"


def test_strip_override():
    # 6. strip=() 关闭剔除；自定义剔除集生效；默认参数即 STRIP_PREFIXES
    text = "a\nupdated: x\nb\nwriter_id: w\nc\n"
    assert stable_body(text, strip=()) == text[:-1]  # 仅剥尾 \n，前缀行全保留
    assert stable_body(text, strip=("updated:",)) == "a\nb\nwriter_id: w\nc"
    assert stable_body(text) == "a\nb\nc"


def test_content_key_idempotent():
    # 7. 幂等端到端：时间戳/识别字段演进不改变 content_key
    base = _FM + _BODY
    evolved = _FM.replace("updated: 2026-01-01 00:00:00", "updated: 2026-09-21 12:00:00") \
                 .replace("last_active: 2026-09-21", "last_active: 2026-09-22") \
                 .replace("certainty: 0.8", "certainty: 0.95") + _BODY
    assert content_key(base) == content_key(evolved), "字段演进不得改变 content_key"
    # 正文变化必须改变 key
    assert content_key(base) != content_key(base + "新段落\n")


def test_content_key_length_aligned():
    # 8. content_key 截断 8 位 hex（B1 拍板：对齐 A 栈 staging_ingest [:8]；
    #    镜像里 6 位 hex 尾缀是 distill_memory 的 project_id，与内容 key 无关）
    k = content_key(_FM + _BODY)
    assert re.fullmatch(r"[0-9a-f]{8}", k), f"content_key 须 8 位 hex，实得 {k!r}"


def test_frozen_sample():
    # 9. 冻结样例字节对比（行为快照锚）：改口径后此样例不得漂移
    text = "---\ntype: note\nwriter_id: workbuddy\n---\nkeep1\ncreated: x\nkeep2\n"
    assert stable_body(text) == "---\ntype: note\n---\nkeep1\nkeep2"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"{len(fns)}/{len(fns)} PASS")
