# -*- coding: utf-8 -*-
"""test_panel.py — v0.7.0 记忆面板验收（零依赖，临时 fixtures + 子线程 HTTP 服务）。

覆盖：
1. /api/stats：统计正确（总数/状态分布/审核队列数）
2. /api/queue：candidate 队列
3. /api/transition：candidate→promoted 成功 + 路径穿越拒绝
4. /api/search：TF-IDF 检索（不依赖 Ollama，语义失败回落）
5. /api/audit：审计 tail
6. §8.3 H 项：activity 活跃度分桶 / exposure 曝光分布（纯逻辑层）
"""
import json
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mempipeline.audit import FileAudit
from mempipeline.protocol import Note, TIER_DIR
from mempipeline.engine import write_atomic


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
        r3 = post("/api/transition", {"path": "../evil.md", "to": "promoted"})
        check(r3.get("ok") is False or "outside" in json.dumps(r3),
              "路径穿越被拒")

        # 4. search（不依赖 Ollama：语义缺失回落 TF-IDF）
        sr = get("/api/search?q=" + urllib.request.quote("单笔风险") + "&k=3")
        check(len(sr) >= 1, f"检索返回结果（{len(sr)} 条）")

        # 5. audit tail
        au = get("/api/audit?n=10")
        check(len(au) >= 2, f"审计日志 tail（{len(au)} 行）")

        # 6. TF-IDF 索引注入路径（P1-A2）：注入 TFIDFIndex 后 search 落索引不报错
        from mempipeline.recall import TFIDFIndex
        tindex = TFIDFIndex(tmp / "tfidf_test.db")
        try:
            tindex.build(mem_root)  # 索引含"单笔风险"笔记
            _Handler.tfidf_index = tindex
            sr_idx = get("/api/search?q=" + urllib.request.quote("单笔风险") + "&k=3")
            check(len(sr_idx) >= 1 and sr_idx[0]["path"].endswith("规则-a1.md"),
                  f"注入索引后检索命中规则（{sr_idx[0]['path'] if sr_idx else 'none'}）")
        finally:
            _Handler.tfidf_index = None  # 复位，避免影响它例
            tindex.close()  # 关闭 SQLite 连接，避免临时目录清理被占用

        # 7. 其余真实端点（首页 / browse / index_status / 404）
        with urllib.request.urlopen(base + "/", timeout=5) as r:
            check(r.status == 200 and b"html" in r.read().lower(),
                  "首页返回 200 HTML")
        b = get("/api/browse?page=1&limit=10")
        check(b["total"] >= 2 and len(b["rows"]) >= 2,
              f"browse 返回笔记（total={b['total']}, rows={len(b['rows'])}）")

        # 7b. 展示收敛（P3.12 §2.5）：redacted 稿退出 /api/browse 列表，
        #      但仍留存在 stats 的 by_status 观测位（治理刻意保留 redacted 计数）
        n_red = Note(title="密钥", summary="敏感", tier="long", importance=0.9,
                     body="sk_live_secret。", status="redacted")
        red_path = mem_root / TIER_DIR["long"] / "密钥-r3.md"
        write_atomic(red_path, n_red.to_frontmatter() + "\n\n" + n_red.body + "\n",
                     audit)
        b2 = get("/api/browse?page=1&limit=50")
        red_paths = [r["path"] for r in b2["rows"]]
        check(not any(str(red_path) in p for p in red_paths),
              "browse 列表已剔除 redacted 稿（不外泄秘文稿）")
        st3 = get("/api/stats")
        check(st3["by_status"].get("redacted") == 1,
              f"stats by_status 仍保留 redacted 观测位（{st3['by_status']}）")
        ista = get("/api/index_status")
        check(isinstance(ista, dict), "index_status 返回对象")
        try:
            urllib.request.urlopen(base + "/no/such/route", timeout=5)
            check(False, "未知路由应 404")
        except urllib.error.HTTPError as e:
            check(e.code == 404, "未知路由返回 404")

        srv.shutdown()
        srv.server_close()

    print("\nPANEL (v0.7.0):", "ALL PASS" if ok else "SOME FAILED")
    return ok


def test_submit_dq_roundtrip() -> None:
    """P3-0 面板投稿 DQ 转义闭环：特殊字符标题/正文 → staging → ingest 读回一致。

    回归点：_submit_note 的字符串字段经 _fmt_scalar（写侧转义契约），
    读侧（ingest._parse_fm / panel._read_frontmatter）必须 unquote 反转义还原。
    """
    ok = True

    def check(cond: bool, msg: str):
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {msg}")
        if not cond:
            ok = False

    from mempipeline.panel import _submit_note, _read_frontmatter
    from mempipeline.ingest import _parse_fm, ingest
    from mempipeline.protocol import TIER_DIR

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        staging = tmp / "staging"
        staging.mkdir(parents=True)
        mem_root = tmp / "mem"
        (mem_root / TIER_DIR["medium"]).mkdir(parents=True)
        audit = FileAudit(tmp / "audit" / "log.md", tmp / "audit" / "manifest.json",
                          mem_root)

        # 含引号/反斜杠/冒号的标题 + 含引号换行的正文（挑战写侧转义 + 读侧反转义）
        title = '风险"敞口\\控制:一"号'
        body = '单笔风险不超过 1 ATR。他说"控制好"。\n第二行。'
        r = _submit_note(title, body, "medium", "dora", staging)
        check(r["ok"], f"面板投稿成功（{r.get('path')}）")
        out = Path(r["path"])
        check(out.exists(), "staging 投稿文件已写盘")

        # 写侧：frontmatter 全字段为 DQ 标量（无裸特殊字符行）
        raw = out.read_text(encoding="utf-8")
        check('title: "风险\\"敞口\\\\控制:一\\"号"' in raw
              and "summary: " in raw, "写侧 title 为 DQ 转义标量")

        # 读侧 1：ingest._parse_fm 反转义还原
        fm = _parse_fm(raw)
        check(fm.get("title") == title, f"ingest 读回 title 一致（{fm.get('title')!r}）")
        check(fm.get("project_id") == "dora", "project_id 读回一致")

        # 读侧 2：ingest 熔合进镜像后 _parse_fm 仍还原（project_id=dora → projects/dora/）
        stats = ingest(staging, mem_root, TIER_DIR, audit)
        check(stats["wrote"] >= 1, f"ingest 熔合（{stats}）")
        mirror = next(mem_root.glob("**/*.md"))
        fm2 = _parse_fm(mirror.read_text(encoding="utf-8"))
        check(fm2.get("title") == title, f"镜像读回 title 一致（{fm2.get('title')!r}）")

        # 读侧 3：panel._read_frontmatter 也反转义（面板审核队列/浏览显示一致）
        fm3 = _read_frontmatter(mirror)
        check(fm3.get("title") == title, f"面板读回 title 一致（{fm3.get('title')!r}）")
    print("\nSUBMIT DQ ROUNDTRIP (P3-0):", "ALL PASS" if ok else "SOME FAILED")
    if not ok:
        raise AssertionError("test_submit_dq_roundtrip: 子断言失败")


def test_null_audit_transition() -> None:
    """_NullAudit 无审计后端时面板晋升不崩溃（trace 由系统自动登记）。"""
    ok = True

    def check(cond: bool, msg: str):
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {msg}")
        if not cond:
            ok = False

    from mempipeline.audit import NullAudit
    from mempipeline.governance import transition
    from mempipeline.protocol import Note

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        mem_root = tmp / "mem"
        (mem_root / TIER_DIR["long"]).mkdir(parents=True)
        audit = FileAudit(tmp / "audit" / "log.md", tmp / "audit" / "manifest.json",
                          mem_root)
        n = Note(title="规则", summary="s", tier="long", importance=0.9,
                 body="正文。", status="candidate")
        out = mem_root / TIER_DIR["long"] / "规则-a1.md"
        write_atomic(out, n.to_frontmatter() + "\n\n" + n.body + "\n", audit)

        try:
            st, frm = transition(out, "promoted", NullAudit(), source="panel")
            check(st in ("wrote", "skipped") and frm == "candidate",
                  f"无审计后端晋升不崩溃（{st}/{frm}）")
        except AttributeError:
            check(False, "_NullAudit 缺 trace 导致崩溃")
        # 文件仍在（软标记零删除）
        check(out.exists() and 'promoted' in out.read_text(encoding="utf-8"),
              "晋升后文件保留且 status 已更新")
    print("\nNULL AUDIT TRANSITION:", "ALL PASS" if ok else "SOME FAILED")
    if not ok:
        raise AssertionError("test_null_audit_transition: 子断言失败")


def test_activity_exposure() -> None:
    """§8.3 H 项：/api/activity 活跃度分桶 + /api/exposure 曝光分布（含未打点降级）。

    HTTP 路由接线与既有端点同构（_json 管道），本测试聚焦纯逻辑层
    `_activity` / `_exposure`：分桶窗口、latest 提取、未曝光面对账。
    """
    ok = True

    def check(cond: bool, msg: str):
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {msg}")
        if not cond:
            ok = False

    from mempipeline.access_log import AccessLog
    from mempipeline.panel_ops import _activity, _exposure
    from mempipeline.protocol import Note, TIER_DIR
    from mempipeline.engine import write_atomic

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        mem_root = tmp / "mem"
        (mem_root / TIER_DIR["long"]).mkdir(parents=True)
        audit = FileAudit(tmp / "audit" / "log.md", tmp / "audit" / "manifest.json",
                          mem_root)
        n1 = Note(title="规则", summary="单笔风险≤1ATR", tier="long", importance=0.9,
                  body="正文。", status="promoted")
        md = mem_root / TIER_DIR["long"] / "规则-a1.md"
        write_atomic(md, n1.to_frontmatter() + "\n\n" + n1.body + "\n", audit)

        # activity：updated（DQ 标量 now_iso）当月分桶命中 + latest 提取
        act = _activity(mem_root)
        cur = act["months"][-1]
        check(act["buckets"].get(cur) == 1 and act["total"] == 1,
              f"activity 当月分桶命中（{act['buckets'].get(cur)}）")
        check(act["latest"].startswith(cur), f"latest 提取（{act['latest']!r}）")
        check(act["outside"] == 0, "无窗口外/不可得篇")
        check(len(act["months"]) == 12, "分桶窗口 12 个月")

        # exposure：未打点降级 + 打点后分布与未曝光面对账
        check(_exposure(None, mem_root) == {"enabled": False}, "未打点 → enabled=False 降级")
        al = AccessLog(tmp / "acc.db")
        al.record([str(md)], source="panel_search")
        ex = _exposure(al, mem_root, days=30)
        check(ex["enabled"] and ex["total"] == 1 and ex["distinct"] == 1,
              f"曝光分布（total={ex['total']}, distinct={ex['distinct']}）")
        check(ex["unexposed"] == 0 and ex["mirror"] == 1,
              f"未曝光面对账（unexposed={ex['unexposed']}/{ex['mirror']}）")
        n2 = Note(title="经验", summary="待审核", tier="long", importance=0.7,
                  body="正文B。", status="candidate")
        md2 = mem_root / TIER_DIR["long"] / "经验-b2.md"
        write_atomic(md2, n2.to_frontmatter() + "\n\n" + n2.body + "\n", audit)
        ex2 = _exposure(al, mem_root)
        check(ex2["unexposed"] == 1 and ex2["mirror"] == 2,
              f"新篇未曝光计入（unexposed={ex2['unexposed']}/{ex2['mirror']}）")
        al.close()
    print("\nACTIVITY+EXPOSURE (§8.3 H):", "ALL PASS" if ok else "SOME FAILED")
    if not ok:
        raise AssertionError("test_activity_exposure: 子断言失败")


if __name__ == "__main__":
    panel_ok = main()
    for _name, _fn in (("SUBMI-DQ", test_submit_dq_roundtrip),
                       ("NULL_AUDIT", test_null_audit_transition),
                       ("ACT+EXPO", test_activity_exposure)):
        try:
            _fn()
        except AssertionError as _e:
            print(f"  [ERR] {_name}: {_e}")
            panel_ok = False
    sys.exit(0 if panel_ok else 1)
