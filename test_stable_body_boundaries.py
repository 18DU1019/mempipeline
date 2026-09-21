# -*- coding: utf-8 -*-
"""test_stable_body_boundaries.py — 12 边界构造用例回归组（零依赖，自运行 + pytest 双轨）。

来源：《三标尺检验-mempipeline幂等口径统一方案-20260921》2.1 节 12 个边界构造
用例（当日实测：模拟升级版与 dm._stable 逐字节相等全 PASS），移植为库内自持
快照断言——dm 在库外（含个人路径，不入库不可 import），预期值由 2026-09-21
一手实测锚定，作为行为快照回归组。

N06（V2-6）：CRLF 块尾粘连与正文同名行用例固化在组内（用例名含 crlf /
body_same_name），防「只解析 FM 块」的口径回退重新引入
`---\\r\\n# 标题` → `---# 标题` 事故机制（dm._stable 冻结红线 docstring
记载的历史坑在旧 B 栈正则实现上的复现路径）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mempipeline.protocol import stable_body


def test_no_frontmatter_passthrough():
    # 边界 1：无 frontmatter → 无前缀行时逐字节原样（splitlines/join 剥尾 \n 为
    # A 栈冻结语义，两侧一致，key 不受影响）
    text = "# 标题\n\n正文。"
    assert stable_body(text) == text


def test_no_fm_body_updated_line():
    # 边界 2：无 frontmatter 但正文含 updated 行 → 仍被剔（全篇过滤口径）
    assert stable_body("前言\nupdated: 2026-01-01\n后记\n") == "前言\n后记"


def test_bom_with_frontmatter():
    # 边界 3：BOM+FM → BOM 附着首行原样保留（A 口径无 BOM 剥离；读盘路径由
    # read_text 处理，库级实测 BOM 文件 = 0）
    text = "\ufeff---\ntitle: t\nupdated: x\n---\n\nbody\n"
    assert stable_body(text) == "\ufeff---\ntitle: t\n---\n\nbody"


def test_crlf_full_text():
    # 边界 4（N06）：CRLF 全文 → \r 归一，块尾不粘连
    text = "---\r\nupdated: x\r\n---\r\n# 标题\r\n正文\r\n"
    out = stable_body(text)
    assert out == "---\n---\n# 标题\n正文"
    assert "---\r" not in out and "---# 标题" not in out


def test_crlf_block_tail_no_adhesion():
    # N06 专项：`---\r\n# 标题` 块尾不得粘连成 `---# 标题`（旧 B 正则 \s*\n
    # 吃 \r 的事故机制；本用例锚定 A 口径 splitlines 语义防口径回退重引入）
    text = "---\r\ntitle: t\r\n---\r\n# 标题\r\n"
    out = stable_body(text)
    assert out == "---\ntitle: t\n---\n# 标题"
    assert "---# 标题" not in out and "\r" not in out


def test_body_same_name_lines():
    # 边界 5（N06）：正文同名行同剔（dm._stable 冻结语义；2026-09-21 实测全库
    # 0 篇正文命中，失效模式偏安全侧）
    text = "---\ntitle: t\n---\n\ncreated: 正文首列普通文本\n保留行\n"
    out = stable_body(text)
    assert out == "---\ntitle: t\n---\n\n保留行"
    assert "created: 正文首列普通文本" not in out


def test_fm_indented_field_line():
    # 边界 6：FM 内缩进字段行不剔（startswith 严格无缩进容忍；2026-09-21 实测
    # 全库 295 篇缩进命中 0，统一到严格口径零影响）
    text = "---\ntitle: t\n  updated: x\n---\nbody\n"
    assert stable_body(text) == "---\ntitle: t\n  updated: x\n---\nbody"


def test_multi_fm_blocks_after():
    # 边界 7：首块之后的后续块内字段行同剔（区分 A 口径全篇过滤 vs 旧 B 口径
    # 只剔首个 FM 块）
    text = "---\ntitle: a\n---\nmid\n---\nupdated: y\n---\ntail\n"
    assert stable_body(text) == "---\ntitle: a\n---\nmid\n---\n---\ntail"


def test_non_ascii_field_value():
    # 边界 8：非 ASCII 字段值照常按前缀剔除（值内容不影响匹配）
    text = "---\ntitle: 仓位管理\nupdated: 2026年9月21日\n---\n正文①\n"
    assert stable_body(text) == "---\ntitle: 仓位管理\n---\n正文①"


def test_created_src_prefix_relation():
    # 边界 9：created_src: 与 created: 是两个独立前缀（差一个下划线）——默认
    # 剔除集两者都剔；只给 created: 时 created_src 行必须保留（历史坑锚定：
    # 不得靠 created: 顺带吃掉 created_src）
    assert stable_body("a\ncreated_src: backfill\ncreated: 2026-01-01\nb\n") == "a\nb"
    assert stable_body("x\ncreated_src: s\n", strip=("created:",)) == "x\ncreated_src: s"


def test_u2028_line_separator():
    # 边界 10：\u2028 行分隔符按 splitlines 语义切行（与 dm._stable 一致）
    assert stable_body("a\u2028updated: x\u2028b") == "a\nb"


def test_unclosed_frontmatter():
    # 边界 11：FM 不闭合 → A 口径不做块解析、照常全篇过滤（区分旧 B 口径的
    # 正则不匹配即原样返回）
    assert stable_body("---\ntitle: t\nupdated: x\nbody") == "---\ntitle: t\nbody"


def test_empty_string():
    # 边界 12：空字符串恒等
    assert stable_body("") == ""


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"{len(fns)}/{len(fns)} PASS")
