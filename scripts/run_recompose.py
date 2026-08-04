"""백필한 1분봉으로 상위 Horizon(3/5/10/15/30m)을 다시 만든다.

KIS 분봉조회는 1분·30초만 준다(`FID_HOUR_CLS_CODE`) — 상위 Horizon은 라이브 경로와 **똑같은
규칙**으로 합성해야 아카이브 안에서 같은 Horizon의 봉이 두 가지 규칙으로 섞이지 않는다.
그래서 이 스크립트는 자체 합성 로직을 갖지 않고 `data/bar_composer.compose_offline()`을
부른다(그 함수는 실시간 경로인 `MultiHorizonBarComposer.flush_due_horizon()`과
`compose_composite_bar()` 한 곳을 공유한다).

**대상 날짜의 상위 Horizon 통합본을 덮어쓴다.** 백필로 1분봉이 바뀌었으면 거기서 파생된
상위 봉도 전부 다시 만들어야 한다 — 안 그러면 1분봉은 거래소 공식값인데 5분봉은 옛
수집값(거래량 절반)인 상태가 남는다.

사용:
    python scripts/run_recompose.py                      # 아카이브에 1분봉이 있는 모든 날
    python scripts/run_recompose.py --symbol A05608      # 특정 심볼만
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.core.messages import HORIZON_SECONDS, Horizon  # noqa: E402
from messiah.core.timeutil import now_kst  # noqa: E402
from messiah.data.archiver import ParquetArchiver  # noqa: E402
from messiah.data.bar_composer import compose_offline  # noqa: E402
from messiah.ops import session_guard  # noqa: E402

_DATA_DIR = Path("data") / "bars"
_TARGETS = [h for h in HORIZON_SECONDS if h is not Horizon.M1]


def _parse_day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()  # noqa: DTZ007 — 날짜만 다루는 CLI 인자


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--base-dir", default=str(_DATA_DIR))
    p.add_argument("--symbol", default=None, help="생략하면 아카이브의 모든 심볼")
    p.add_argument("--start", type=_parse_day, default=None)
    p.add_argument("--end", type=_parse_day, default=None)
    p.add_argument(
        "--include-today",
        action="store_true",
        help="오늘도 재합성한다. 기본은 제외 — 라이브 수집이 조각을 쓰는 중이라 통합본으로 "
        "덮으면 이후 수집분이 read_day()에서 조각으로만 남아 뒤섞인다.",
    )
    session_guard.add_force_intraday_argument(p)
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    session_guard.refuse_if_regular_session("상위 Horizon 재합성", force=args.force_intraday)
    base = Path(args.base_dir)
    archiver = ParquetArchiver(base)
    today = now_kst().date()

    symbols = [args.symbol] if args.symbol else sorted(p.name for p in base.iterdir() if p.is_dir())
    total_days = 0
    total_rows = 0
    skipped_short: list[tuple[str, date, int]] = []

    for symbol in symbols:
        days = archiver.available_days(symbol, Horizon.M1)
        days = [d for d in days if not (args.start and d < args.start)]
        days = [d for d in days if not (args.end and d > args.end)]
        if not args.include_today:
            days = [d for d in days if d != today]
        if not days:
            continue
        print(f"{symbol}: {len(days)}일")

        for day in days:
            minute_bars = archiver.read_day_bars(symbol, Horizon.M1, day)
            if not minute_bars:
                continue
            if len(minute_bars) < 30:
                # 30분봉 하나도 못 채우는 날 — 재합성은 하되 눈에 띄게 남긴다.
                skipped_short.append((symbol, day, len(minute_bars)))
            written = []
            for horizon in _TARGETS:
                composites = compose_offline(symbol, horizon, minute_bars)
                rows = archiver.write_day(symbol, horizon, composites)
                written.append(f"{horizon.value}={rows}")
                total_rows += rows
            total_days += 1
            print(f"  {day}  1m={len(minute_bars)}  →  " + " ".join(written))

    print(f"\n완료 — {total_days}일 / 상위봉 {total_rows}행 재합성")
    if skipped_short:
        print("1분봉이 30개 미만인 날(상위 Horizon이 불완전):")
        for symbol, day, count in skipped_short:
            print(f"  {symbol} {day}  {count}봉")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
