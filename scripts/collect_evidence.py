#!/usr/bin/env python3
"""MESSIAH 일일 점검 증거 수집기 — 런처.

본체는 `.claude/skills/messiah-daily-check/scripts/collect_evidence.py` 에 있다.
스킬 문서와 스크립트를 한 곳에 묶어두기 위해서다.

이 파일은 손에 익은 `scripts/` 경로와 Windows 작업 스케줄러에서
본체를 그대로 부를 수 있게 해주는 얇은 껍데기다. 로직은 여기에 두지 않는다.

    python scripts/collect_evidence.py --phase pre
    python scripts/collect_evidence.py --phase post --date 2026-08-11 --out logs/dailycheck/ev.md
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REAL = ROOT / ".claude" / "skills" / "messiah-daily-check" / "scripts" / "collect_evidence.py"

if not REAL.exists():
    raise SystemExit(
        f"수집기 본체를 찾지 못했다: {REAL}\nmessiah-daily-check 스킬이 설치돼 있는지 확인하라."
    )

if "--root" not in sys.argv:
    sys.argv += ["--root", str(ROOT)]
sys.argv[0] = str(REAL)

runpy.run_path(str(REAL), run_name="__main__")
