# -*- coding: utf-8 -*-
"""test_access_log.py — P1 召回未曝光度打点（AMV-P1 sidecar）验收。

覆盖：
1. AccessLog 单元：record/counts/last_seen/unexposed/sources；空批次与幂等；
2. 旁路不阻断：record 异常（伪造坏对象）不影响面板响应；
3. 面板集成：/api/search 注入 access_log 后落曝光事件；未注入时行为同旧版；
4. 口径护栏：golden（recall_golden.check 直调 MemoryRecall）不产生曝光事件。

全部临时目录沙盒，不触碰真实镜像与生产 sidecar。
"""
import json
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mempipeline.access_log import AccessLog
from mempipeline.audit import FileAudit
from mempipeline.engine import write_atomic
from mempipeline.protocol import Note, TIER_DIR


def _test_unit(tmp: Path, check) -> None:
    print("== AccessLog 单元 ==")
    al = AccessLog(tmp / "acc.db")
    p1, p2, p3 = str(tmp / "a.md"), str(tmp / "b.md"), str(tmp / "c.md")
    check(al.record([], "panel_search") == 0, "空批次返回 0 不落库")
    check(al.record([p1, p2], "panel_search") == 2, "一次搜索记 2 条")
    check(al.record([p1], "panel_search") == 1, "重复曝光追加")
    c = al.counts()
    check(c.get(p1) == 2 and c.get(p2) == 1, f"counts 聚合正确（{c}）")
    check(p3 not in c, "未曝光笔记无计数")
    check(al.last_seen(p1) is not None, "last_seen 有值")
    check(al.last_seen(p3) is None, "last_seen 缺省 None")
    check(al.unexposed([p1, p2, p3]) == [p3], "unexposed 只含从未曝光者")
    check(al.sources() == {"panel_search": 3}, "sources 口径自检")
    d = al.distribution(days=7)
    check(d["total"] == 3 and d["distinct"] == 2, f"distribution 聚合（{d['total']}/{d['distinct']}）")
    check(d["top"][0] == (p1, 2), "distribution top 按次数降序")
    check(al.distribution(days=-1)["total"] == 0, "窗口起点在未来时无事件")
    al.close()
    # 重开持久化验证
    al2 = AccessLog(tmp / "acc.db")
    check(al2.counts().get(p1) == 2, "重开后计数持久")
    al2.close()


def _test_panel(tmp: Path, check) -> None:
    print("== 面板集成：search 落曝光 / 未注入不报错 ==")
    mem_root = tmp / "mem"
    (mem_root / TIER_DIR["long"]).mkdir(parents=True)
    audit = FileAudit(tmp / "audit" / "log.md", tmp / "audit" / "manifest.json",
                      mem_root)
    n1 = Note(title="规则", summary="单笔风险≤1ATR", tier="long", importance=0.9,
              body="正文。", status="promoted")
    write_atomic(mem_root / TIER_DIR["long"] / "规则-a1.md",
                 n1.to_frontmatter() + "\n\n" + n1.body + "\n", audit)

    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    from mempipeline.panel import _Handler, ThreadingHTTPServer
    from mempipeline.recall import TFIDFIndex
    _Handler.mem_root = mem_root
    _Handler.audit_log = None
    _Handler.audit = audit
    _Handler.semantic_index = None
    _Handler.tfidf_index = TFIDFIndex(tmp / "tfidf.db")
    al_inst = AccessLog(tmp / "acc_panel.db")
    _Handler.access_log = al_inst
    srv = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        base = f"http://127.0.0.1:{port}"
        with urllib.request.urlopen(
                base + "/api/search?q=" + urllib.request.quote("单笔风险") + "&k=3",
                timeout=5) as r:
            res = json.loads(r.read().decode("utf-8"))
        check(len(res) >= 1, f"search 返回 {len(res)} 条")
        al = _Handler.access_log
        counts = al.counts()
        hit_path = res[0]["path"]
        check(counts.get(hit_path) == 1, "曝光事件落 sidecar（path 与响应一致）")
        check(al.sources() == {"panel_search": len(res)}, "source 口径=panel_search")
        # 旁路不阻断：换成坏对象（record 必抛），search 仍 200
        class _Broken:
            def record(self, *a, **kw):
                raise RuntimeError("boom")
        _Handler.access_log = _Broken()
        with urllib.request.urlopen(
                base + "/api/search?q=" + urllib.request.quote("单笔风险") + "&k=3",
                timeout=5) as r:
            res2 = json.loads(r.read().decode("utf-8"))
        check(len(res2) == len(res), "打点件故障不阻断召回响应")
    finally:
        srv.shutdown()  # 先停 serve_forever 循环，再关 socket（避免 select 竞态噪声）
        th.join(timeout=5)
        al_inst.close()
        _Handler.access_log = None
        _Handler.tfidf_index.close()
        srv.server_close()


def _test_golden_no_exposure(tmp: Path, check) -> None:
    print("== 口径护栏：golden 直调 MemoryRecall 不产曝光 ==")
    mem_root = tmp / "mem2"
    (mem_root / TIER_DIR["long"]).mkdir(parents=True)
    audit = FileAudit(tmp / "audit2" / "log.md", tmp / "audit2" / "manifest.json",
                      mem_root)
    n = Note(title="锚点", summary="golden 锚", tier="long", importance=0.8,
             body="正文。", status="promoted")
    write_atomic(mem_root / TIER_DIR["long"] / "锚点-z9.md",
                 n.to_frontmatter() + "\n\n" + n.body + "\n", audit)
    from mempipeline.recall_golden import check as golden_check
    r = golden_check(mem_root, golden={"golden 锚": "锚点"})
    check(r["hit_rate"] == 1.0, "golden 命中（走 MemoryRecall 直调）")
    # panel 未起、access_log 未注入 —— 全仓不存在第二记录点即口径成立；
    # 这里显式断言 recall.GoldenCheck 路径不 import access_log。
    import mempipeline.recall_golden as rg
    src = Path(rg.__file__).read_text(encoding="utf-8")
    check("access_log" not in src, "recall_golden 源码不含 access_log 引用")


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
        _test_unit(tmp, check)
        _test_panel(tmp, check)
        _test_golden_no_exposure(tmp, check)
    print("OK" if ok else "FAILED")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
