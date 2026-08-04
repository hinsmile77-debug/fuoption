"""변동성 축 일일 채점 실행 — 고도화 4 (2026-08-05 신설).

    python scripts/run_vol_scorecard.py                 # 어제(마지막 거래일)
    python scripts/run_vol_scorecard.py --date 2026-08-05
    python scripts/run_vol_scorecard.py --start 2026-07-27 --end 2026-08-04

## 무엇을 답하는가

2026-08-04 피처 관문이 "변동성 축엔 예측력이 있고 방향 축엔 없다"를 **8개월 단일 국면의
in-sample**로 확정했다. 이 스크립트는 그 위에 모델을 얹기 **전에** 물어야 할 질문을
매 거래일 자동으로 답한다: **"그 예측력이 새 데이터에서도 존재하는가."**

SYSTEM.md R18("게이트·차단 로직 신설은 섀도 계측 20거래일 후 승격")과 같은 규율이고,
마흐디가 `find_gamma_flip()`을 넉 달간 죽은 채로 둔 사고의 예방책이기도 하다 — 아무도
"그게 값을 내는가"를 예측치로 안 적었기 때문에 넉 달을 잃었다.

## 왜 워밍업 구간이 필요한가

하루치 봉만으로 피처를 만들면 롤링 윈도가 그날 안에서 처음부터 채워진다. `px_kurt_r`·
`px_ema_dev`처럼 긴 창을 쓰는 피처는 그날 내내 NaN이 되고, 결과적으로 **시간 계열(EV)만
측정되는** 편향된 채점이 된다. 그래서 대상일 앞의 거래일을 함께 흘려 엔진을 데운 뒤,
**대상일 구간만 잘라** 채점한다.

## 왜 장후 종료 절차에 안 넣었나

전 Horizon × 워밍업 구간의 피처를 다시 만드는 계산이라 15:35~15:40 종료 예산에 넣으면
그 예산을 예측 불가능하게 만든다. 대신 이 스크립트가 남긴 `VolAxisScorecard` 로그를
무결성 리포트가 읽고, **안 돌린 날은 "미측정"으로 리포트에 남는다**(고도화 2 원칙 —
측정 불능이 조용히 지나가지 않는다).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.backtest.harness import aggregate_to_horizon  # noqa: E402
from messiah.core.config import load_instance  # noqa: E402
from messiah.core.event_calendar import EventCalendar  # noqa: E402
from messiah.core.messages import BarClosed, Horizon  # noqa: E402
from messiah.core.timeutil import now_kst  # noqa: E402
from messiah.data.archiver import ParquetArchiver  # noqa: E402
from messiah.models import vol_scorecard  # noqa: E402
from messiah.models.trainer import build_feature_vectors  # noqa: E402
from messiah.ops import session_guard  # noqa: E402

_DATA_DIR = Path("data") / "bars"

# 채점 대상 Horizon — 관문을 돌린 셋과 같다(`scripts/run_feature_gate.py` 기본값).
_HORIZONS = (Horizon.M5, Horizon.M15, Horizon.M30)

# 워밍업에 쓸 앞선 거래일 수. `features/engine._MAX_HISTORY`(200봉)를 30m 기준으로 채우려면
# 하루 15봉이라 14거래일이 필요하다 — 20일이면 셋 다 여유 있게 덮는다.
WARMUP_TRADING_DAYS = 20

# 채점 구간의 거래일 수 — 하루로는 15m(20표본)·30m(7표본)이 매일 "표본 부족"이 된다
# (2026-08-05 실측). 20거래일은 R18의 섀도 계측 기간과 같은 값이고, 매일 창이 하루씩
# 밀리므로 값의 날짜별 변화가 곧 예측력의 드리프트다.
SCORE_TRADING_DAYS = 20


def _parse_day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()  # noqa: DTZ007


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="변동성 축 일일 채점")
    p.add_argument("--date", type=_parse_day, default=None)
    p.add_argument("--start", type=_parse_day, default=None)
    p.add_argument("--end", type=_parse_day, default=None)
    p.add_argument("--symbol", default="A05608")
    p.add_argument("--base-dir", default=str(_DATA_DIR))
    p.add_argument("--configs", default="configs")
    p.add_argument("--log-dir", default="logs")
    p.add_argument("--warmup-days", type=int, default=WARMUP_TRADING_DAYS)
    p.add_argument("--score-days", type=int, default=SCORE_TRADING_DAYS)
    session_guard.add_force_intraday_argument(p)
    return p.parse_args()


def load_m1_window(
    archiver: ParquetArchiver,
    symbol: str,
    day: date,
    *,
    warmup_days: int,
    score_days: int,
    calendar: EventCalendar,
) -> tuple[list[BarClosed], set[date]]:
    """(워밍업+채점 구간 전체 1분봉, 채점 대상 날짜 집합) — 대상일이 비면 둘 다 빈 값.

    구간이 셋으로 나뉜다:

        [워밍업 warmup_days일][채점 score_days일 ····· 대상일]
         엔진을 데우기만 함        여기만 IC를 잰다

    워밍업 구간에 결손일이 있어도 그냥 넘어간다. 엔진을 데우는 것이 목적이라 며칠 빠져도
    판정은 유효하다. 채점 구간의 결손일은 표본이 그만큼 줄 뿐이고, 줄어든 표본 수는
    `VolScorecard.samples`에 그대로 남아 사람이 볼 수 있다.
    """
    if not archiver.read_day_bars(symbol, Horizon.M1, day):
        return [], set()

    scored: list[date] = []
    cursor = day
    for _ in range(score_days):
        scored.append(cursor)
        cursor = calendar.previous_trading_day(cursor)
    warmup: list[date] = []
    for _ in range(warmup_days):
        warmup.append(cursor)
        cursor = calendar.previous_trading_day(cursor)

    bars: list[BarClosed] = []
    for target_day in sorted(warmup + scored):
        bars.extend(archiver.read_day_bars(symbol, Horizon.M1, target_day) or [])
    return bars, set(scored)


async def score_day(
    archiver: ParquetArchiver,
    symbol: str,
    day: date,
    *,
    feature_set: str,
    warmup_days: int,
    score_days: int,
    calendar: EventCalendar,
) -> list[vol_scorecard.VolScorecard]:
    """`day`로 끝나는 최근 `score_days` 거래일 구간의 전 Horizon 채점.

    아카이브가 없으면 빈 목록(그날은 수집이 안 돌았다는 뜻).
    """
    window, scored_days = load_m1_window(
        archiver,
        symbol,
        day,
        warmup_days=warmup_days,
        score_days=score_days,
        calendar=calendar,
    )
    if not scored_days:
        return []

    cards: list[vol_scorecard.VolScorecard] = []
    for horizon in _HORIZONS:
        bars = aggregate_to_horizon(window, horizon)
        if not bars:
            continue
        vectors = await build_feature_vectors(bars, feature_set=feature_set)
        # **채점 구간만** 남긴다 — 워밍업은 엔진을 데우는 용도지 성적이 아니다.
        sliced = [(b, v) for b, v in zip(bars, vectors) if b.bar_open_kst.date() in scored_days]
        if not sliced:
            continue
        cards.append(
            vol_scorecard.score_horizon(
                [b for b, _ in sliced],
                [v for _, v in sliced],
                horizon=horizon,
                window_days=len(scored_days),
            )
        )
    return cards


async def main() -> int:
    args = _parse_args()
    session_guard.refuse_if_regular_session("변동성 축 채점", force=args.force_intraday)

    calendar = EventCalendar.from_file()
    if args.date is not None:
        days = [args.date]
    elif args.start is not None:
        end = args.end or args.start
        days = []
        cursor = args.start
        while cursor <= end:
            if calendar.is_trading_day(cursor):
                days.append(cursor)
            cursor += timedelta(days=1)
    else:
        days = [calendar.previous_trading_day(now_kst().date() + timedelta(days=1))]

    cfg = load_instance(args.configs)
    archiver = ParquetArchiver(Path(args.base_dir))

    exit_code = 0
    for day in days:
        cards = await score_day(
            archiver,
            args.symbol,
            day,
            feature_set=cfg.feature_set,
            warmup_days=args.warmup_days,
            score_days=args.score_days,
            calendar=calendar,
        )
        print(f"=== 변동성 축 채점 {day.isoformat()} ({args.symbol}) ===")
        if not cards:
            print("  아카이브 없음 — 그날은 수집이 안 돌았다")
            exit_code = 1
            continue
        for line in vol_scorecard.format_scorecards(cards):
            print(line)
        out = vol_scorecard.write_scorecards(
            cards, symbol=args.symbol, day=day, log_dir=Path(args.log_dir)
        )
        print(f"  → {out}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
