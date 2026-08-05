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

from messiah.core.event_calendar import DEFAULT_SESSION  # noqa: E402
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
        help="오늘도 재합성한다. 기본은 **연속거래 종료(15:35) 이후면 자동 포함** — 그때는 "
        "라이브 수집이 이미 끝나 조각을 쓰지 않는다. 장중에 굳이 돌리려면 이 플래그를 준다.",
    )
    session_guard.add_force_intraday_argument(p)
    return p.parse_args()


def _should_include_today(*, explicit: bool, now: datetime) -> tuple[bool, str]:
    """오늘도 재합성할 것인가, 그리고 그 판단의 근거 문장.

    ## 왜 자동 판단으로 바꿨나 (2026-08-05 실측)

    이 스크립트는 **장후 일일 절차의 2단계**다(`dev_memory/NEXT_TODO.md`). 그런데 기본값이
    "오늘 제외"라 장 마감 뒤에 그대로 돌리면 `완료 — 0일 / 상위봉 0행`만 찍고 끝났다 —
    **그날 망가진 상위 Horizon을 고치라고 만든 절차가 아무것도 안 하고 성공처럼 보였다.**
    2026-08-05에 실제로 그렇게 한 번 지나갔고, 출력만 봐서는 알 수가 없었다.

    "오늘 제외"의 원래 이유는 유효하다: 라이브 수집이 조각을 쓰는 중에 통합본으로 덮으면
    이후 수집분이 조각으로만 남아 뒤섞인다. 그런데 그 이유는 **연속거래가 끝나면 사라진다** —
    `run_l1_daily.py`가 15:35에 `_compact_archive()`로 조각을 통합하고 종료하기 때문이다.

    그래서 시각으로 판단하고, **어느 쪽이든 이유를 출력한다**(조용한 무동작 금지, L18).
    """
    if explicit:
        return True, "오늘 포함 — --include-today"
    if now.timetz().replace(tzinfo=None) >= DEFAULT_SESSION.close_time:
        return True, f"오늘 포함 — 연속거래 종료({DEFAULT_SESSION.close_time:%H:%M}) 이후"
    return False, (
        f"오늘 제외 — 연속거래 종료({DEFAULT_SESSION.close_time:%H:%M}) 전이라 라이브 수집이 "
        "조각을 쓰는 중이다(장중에도 돌리려면 --include-today)"
    )


def main() -> int:
    args = _parse_args()
    session_guard.refuse_if_regular_session("상위 Horizon 재합성", force=args.force_intraday)
    base = Path(args.base_dir)
    archiver = ParquetArchiver(base)
    now = now_kst()
    today = now.date()
    include_today, today_reason = _should_include_today(explicit=args.include_today, now=now)
    print(today_reason)

    symbols = [args.symbol] if args.symbol else sorted(p.name for p in base.iterdir() if p.is_dir())
    total_days = 0
    total_rows = 0
    excluded_today = 0
    skipped_short: list[tuple[str, date, int]] = []

    for symbol in symbols:
        days = archiver.available_days(symbol, Horizon.M1)
        days = [d for d in days if not (args.start and d < args.start)]
        days = [d for d in days if not (args.end and d > args.end)]
        if not include_today and today in days:
            days = [d for d in days if d != today]
            excluded_today += 1
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
    # 0일로 끝난 이유를 반드시 말한다 — "돌렸는데 아무 일도 안 일어났다"가 성공처럼 보이면
    # 안 된다(2026-08-05에 실제로 그렇게 한 번 지나갔다).
    if total_days == 0:
        print(
            f"  ** 재합성한 날이 없다 ** — {today.isoformat()}이(가) 제외됐고 그 외 대상일도 "
            "없었다."
            if excluded_today
            else "  ** 재합성한 날이 없다 ** — 구간에 1분봉이 있는 날이 없다."
        )
    if skipped_short:
        print("1분봉이 30개 미만인 날(상위 Horizon이 불완전):")
        for symbol, day, count in skipped_short:
            print(f"  {symbol} {day}  {count}봉")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
