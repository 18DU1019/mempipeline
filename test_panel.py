# -*- coding: utf-8 -*-
"""test_panel.py — v0.5.0 记忆面板验收（零依赖，临时 fixtures + 子线程 HTTP 服务）。

覆盖：
1. /api/stats：统计正确（总数/状态分布/审核队列数）
2. /api/queue：candidate 队列
3. /api/transition：candidate→promoted 成功 + 路径穿越拒绝
4. /api/search：TF-IDF 检索（不依赖 Ollama，语义失败回落）
5. /api/audit：审计 tail
"""
import json
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mempipeline.audit import FileAudit  # noqa: E402
from mempipeline.panel import serve  # noqa: E402
from mempipeline.protocol import Note, TIER_DIR  # noqa: E402
from mempipeline.engine import write_atomic  # noqa: E402


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
        (mem_root / TIER_DIR["long"]).mkdir(parents=True)
        audit = FileAudit(tmp / "audit" / "log.md", tmp / "audit" / "manifest.json",
                          mem_root)
        # 一篇 promoted + 一篇 candidate
        n1 = Note(title="规则", summary="单笔风险≤1ATR", tier="long", importance=0.9,
                  body="正文。", status="promoted")
        write_atomic(mem_root / TIER_DIR["long"] / "规则-a1.md",
                     n1.to_frontmatter() + "\n\n" + n1.body + "\n", audit)
        n2 = Note(title="经验", summary="待审核", tier="long", importance=0.7,
                  body="正文B。", status="candidate")
        out2 = mem_root / TIER_DIR["long"] / "经验-b2.md"
        write_atomic(out2, n2.to_frontmatter() + "\n\n" + n2.body + "\n", audit)

        # 起服务（随机端口）
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        from mempipeline.panel import _Handler, ThreadingHTTPServer
        _Handler.mem_root = mem_root
        _Handler.audit_log = tmp / "audit" / "log.md"
        _Handler.audit = audit
        srv = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        base = f"http://127.0.0.1:{port}"

        def get(p: str) -> dict:
            with urllib.request.urlopen(base + p, timeout=5) as r:
                return json.loads(r.read().decode("utf-8"))

        def post(p: str, data: dict) -> dict:
            req = urllib.request.Request(
                base + p, data=json.dumps(data).encode(),
                headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                return json.loads(e.read().decode("utf-8"))

        # 1. stats
        st = get("/api/stats")
        check(st["total"] == 2, f"stats 总数 2（实得 {st['total']}）")
        check(st["by_status"].get("promoted") == 1 and
              st["by_status"].get("candidate") == 1, "状态分布正确")
        check(st["queue"] == 1, f"审核队列 1（实得 {st['queue']}）")

        # 2. queue
        q = get("/api/queue")
        check(len(q) == 1 and q[0]["title"] == "经验", "candidate 入队列")

        # 3. transition（晋升 + 路径穿越）
        r = post("/api/transition", {"path": str(out2), "to": "promoted"})
        check(r["ok"] and r["status"] in ("wrote", "skipped"), f"面板晋升成功（{r}）")
        q2 = get("/api/queue")
        check(len(q2) == 0, "晋升后队列清空")
        r3 = post("/api/transition", {"path": "C:/Windows/evil.md", "to": "promoted"})
        check(r3.get("ok") is False or "outside" in json.dumps(r3),
              "路径穿越被拒")

        # 4. search（不依赖 Ollama：语义缺失回落 TF-IDF）
        sr = get("/api/search?q=" + urllib.request.quote("单笔风险") + "&k=3")
        check(len(sr) >= 1, f"检索返回结果（{len(sr)} 条）")

        # 5. audit tail
        au = get("/api/audit?n=10")
        check(len(au) >= 2, f"审计日志 tail（{len(au)} 行）")

        srv.shutdown()
        srv.server_close()

    print("\nPANEL (v0.5.0):", "ALL PASS" if ok else "SOME FAILED")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
