# -*- coding: utf-8 -*-
"""coverage_gate.py — 架构债 P3：覆盖率门禁（零依赖，coverage 标准库级调用）。

背景：多数测试为自运行式（`if __name__ == "__main__"`），pytest 只收集到 7 个
函数，`pytest --cov` 单独跑会严重低估真实覆盖（实测 40% vs 逐文件累积 85%）。
故本门禁逐测试文件在 coverage 下运行并累积，再对 TOTAL 判阈值。

阈值（2026-09-14 立）：COV_FLOOR = 80.0。基线实测 85%，留 5pp 余量防抖动；
只准升不准降（改阈值须连带改本 docstring 的裁决日期）。

用法：
    python coverage_gate.py            # 跑全量并判定，退出码 0/1
    python coverage_gate.py --report   # 只打印分文件明细
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COV_FLOOR = 80.0
PY = sys.executable


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    report_only = "--report" in argv
    cov_file = ROOT / ".coverage"
    if cov_file.exists():
        cov_file.unlink()

    tests = sorted(ROOT.glob("test_*.py"))
    if not tests:
        print("no test_*.py found", file=sys.stderr)
        return 2
    failed: list[str] = []
    for t in tests:
        r = subprocess.run(
            [PY, "-m", "coverage", "run", "--append", "--source=mempipeline",
             str(t)],
            cwd=str(ROOT), capture_output=True, text=True)
        if r.returncode != 0:
            failed.append(t.name)
        print(f"  [{'PASS' if r.returncode == 0 else 'FAIL'}] {t.name}")
    if failed:
        print(f"❌ {len(failed)} 套测试失败：{failed}", file=sys.stderr)
        return 1

    rep = subprocess.run([PY, "-m", "coverage", "report"],
                         cwd=str(ROOT), capture_output=True, text=True)
    print(rep.stdout or rep.stderr)
    total_line = next((ln for ln in (rep.stdout or "").splitlines()
                       if ln.strip().startswith("TOTAL")), "")
    try:
        pct = float(total_line.split()[-1].rstrip("%"))
    except (ValueError, IndexError):
        print("❌ 无法解析 TOTAL 覆盖率", file=sys.stderr)
        return 1
    if report_only:
        print(f"TOTAL = {pct}%（门禁阈值 {COV_FLOOR}%，本次仅报告）")
        return 0
    ok = pct >= COV_FLOOR
    print(f"COVERAGE: {pct}% / floor {COV_FLOOR}% -> {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
