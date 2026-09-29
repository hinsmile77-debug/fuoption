"""일일 무결성 리포트 생성 — 고도화 2 진입점 (2026-07-30).

집계 로직과 그 근거는 `src/messiah/ops/integrity_report.py` 모듈 docstring 참고 — 이 파일은
인자 해석과 종료코드만 담당하는 얇은 껍데기다(산출 자체는 `generate_and_write()`가 하고,
`run_l1_daily.py`의 장후 절차도 **같은 함수**를 부른다 — 손으로 돌린 리포트와 자동 리포트가
갈리지 않게 하기 위함).

종료코드: 임계 초과 항목이 있으면 1 — 작업 스케줄러/CI가 "그날 뭔가 있었다"를 코드로 알 수
있게 한다(사람이 요약을 읽어야만 알 수 있으면 결국 안 읽는다).

사용: python scripts/daily_integrity_report.py [--date 2026-07-30] [--symbol A05608]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.core import logging as mlog  # noqa: E402
from messiah.core.config import load_instance  # noqa: E402
from messiah.core.timeutil import now_kst  # noqa: E402
from messiah.ops import meta_gate_shadow_rolling  # noqa: E402
from messiah.ops.integrity_report import generate_and_write  # noqa: E402

_DEFAULT_SYMBOL = "A05608"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MESSIAH 일일 무결성 리포트")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (기본: 오늘 KST)")
    parser.add_argument("--symbol", default=None, help="기본: A05608")
    parser.add_argument("--configs", default="configs")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    day = date.fromisoformat(args.date) if args.date else now_kst().date()
    try:
        instance_id = load_instance(args.configs).instance_id
    except Exception:  # noqa: BLE001 — 설정을 못 읽어도 리포트 자체는 낼 수 있어야 한다
        instance_id = "unset"
    mlog.setup(instance_id)

    report = generate_and_write(
        day=day, symbol=args.symbol or _DEFAULT_SYMBOL, instance_id=instance_id
    )
    _write_shadow_rolling(day)
    return 1 if report.breaches else 0


def _write_shadow_rolling(day: date) -> None:
    """섀도 메타게이트 20거래일 요약 (2026-09-29 G-8).

    **오늘 확정본을 쓴 뒤에** 부른다 — 요약은 일별 리포트를 다시 읽어 만들기 때문이다.
    관측 축일 뿐이라 여기서 무엇이 실패해도 종료 코드(임계 초과 여부)는 바뀌지 않는다.
    """
    try:
        rolling = meta_gate_shadow_rolling.judge(day=day)
        written = meta_gate_shadow_rolling.write(rolling, day=day)
    except Exception as exc:  # noqa: BLE001 — 관측 축 실패가 리포트 종료 코드를 흔들면 안 된다
        print(f"섀도 메타게이트 롤링: 실패 — {exc!r}", file=sys.stderr)
        return
    tail = "" if written else " (파일 쓰기 실패)"
    print(rolling.summary_line() + tail)


if __name__ == "__main__":
    raise SystemExit(main())
