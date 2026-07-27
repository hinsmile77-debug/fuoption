"""Walk-Forward 백테스트 하니스 수동 스모크 — Validator 성과 관문(Sharpe·MDD·창별
일관성)을 실제로 실행 (Master Plan Ver 1.2 §8.2/§8.3, Ver 2.0 §9, 2026-07-27 신설).

W14~16부터 W24~26까지 계속 남아있던 공통 갭("Digital Twin·Expert·Cost Model·Validator가
전부 갖춰졌지만 이들을 엮어 실제 성과 시계열을 뽑는 백테스트 하니스 자체가 없다",
models/validator.py 모듈 docstring)을 이 스크립트가 처음으로 메운다 —
`messiah.backtest.harness.run_walk_forward_backtest()`로 창마다 재학습+재생한 뒤,
그 산출물을 `Validator.validate_performance()`에 실제로 흘려 넣는다.

다른 모든 W17~ 스크립트와 같은 2단계 패턴을 따른다:
1) 실제 아카이브로 먼저 시도 — 예상대로 데이터 부족 실패, 정직하게 보고.
2) 합성(사인파+지터) 다개월 데이터로 전체 walk-forward 루프 실행 — **실제 시장 데이터
   아님**, "성과가 좋다"는 주장이 아니라 "이 루프가 실제로 도는가"가 목적.

사용: python scripts/run_backtest_harness.py --symbol A05608 --start 2026-07-24 --end 2026-07-24
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.backtest.harness import (  # noqa: E402
    equity_curve_from_windows,
    run_walk_forward_backtest,
    window_returns_from_windows,
)
from messiah.core.messages import BarClosed, Horizon  # noqa: E402
from messiah.core.timeutil import KST  # noqa: E402
from messiah.models.validator import Validator  # noqa: E402
from messiah.simulator.replay import ParquetBarReplaySource  # noqa: E402

_DATA_DIR = Path("data") / "bars"
_SYNTHETIC_SYMBOL = "SYNWFA"


# ---------------------------------------------------------------- 1) 실제 아카이브 시도


async def _try_real_archive(args: argparse.Namespace) -> None:
    source = ParquetBarReplaySource(Path(args.base_dir), args.symbol, horizons=[Horizon.M1])
    bars = source.load(date.fromisoformat(args.start), date.fromisoformat(args.end))
    print(f"[실제 아카이브] 입력 M1봉: {len(bars)}건 ({args.symbol})")
    span_days = (bars[-1].bar_open_kst.date() - bars[0].bar_open_kst.date()).days if bars else 0
    needed_days = args.train_days + args.test_days
    try:
        if span_days < needed_days:
            raise ValueError(
                f"실제 기간 {span_days}일 — walk-forward 창 하나(학습{args.train_days}+"
                f"검증{args.test_days}일)에도 못 미침"
            )
        print("[실제 아카이브] 성공(데이터가 매우 많아 흔치 않은 결과)")
    except ValueError as exc:
        print(f"[실제 아카이브] 예상대로 실패(데이터 부족, 정상): {exc}")


# ---------------------------------------------------------------- 2) 합성 다개월 데이터


def _synthetic_m1_bars(n_days: int, bars_per_day: int, *, seed: int) -> list[BarClosed]:
    """여러 "거래일"에 걸친 연속 M1봉 — 하루=`bars_per_day`분(실제 세션 시각과 무관, 날짜
    경계만 있으면 WalkForwardSplitter가 성립). **실제 시장 데이터 아님**."""
    rng = random.Random(seed)
    out: list[BarClosed] = []
    price = 1000.0
    for day in range(n_days):
        day_start = datetime(2026, 1, 5, 9, 0, tzinfo=KST) + timedelta(days=day)
        for i in range(bars_per_day):
            price += 0.5 * math.sin((day * bars_per_day + i) / 8) + rng.uniform(-1.5, 1.5)
            price = max(price, 100.0)
            close = round(price)
            out.append(
                BarClosed(
                    symbol=_SYNTHETIC_SYMBOL,
                    horizon=Horizon.M1,
                    bar_open_kst=day_start + timedelta(minutes=i),
                    o_ticks=close,
                    h_ticks=close + 5,
                    l_ticks=close - 5,
                    c_ticks=close,
                    volume=50 + i,
                )
            )
    return out


async def _run_synthetic(args: argparse.Namespace) -> None:
    total_days = args.train_days + args.test_days * (args.n_windows) + args.embargo_days
    print(
        f"\n[합성 데이터] {total_days}일치 M1봉 생성(하루 {args.bars_per_day}분) — "
        f"학습{args.train_days}일/검증{args.test_days}일/embargo{args.embargo_days}일 창을 "
        f"약 {args.n_windows}개 뽑을 수 있는 최소 폭"
    )
    bars = _synthetic_m1_bars(total_days, args.bars_per_day, seed=args.seed)

    print("[Walk-Forward] 창마다 재학습(train_formal_expert) + 검증구간 실시간 재생 시작")
    results = await run_walk_forward_backtest(
        bars,
        symbol=_SYNTHETIC_SYMBOL,
        train_days=args.train_days,
        test_days=args.test_days,
        embargo_days=args.embargo_days,
        n_splits=args.n_splits,
        n_search_trials=args.n_search_trials,
        search_num_boost_round=args.search_num_boost_round,
        final_num_boost_round=args.final_num_boost_round,
        n_members=args.n_members,
        meta_num_boost_round=args.meta_num_boost_round,
        starting_cash=args.cash,
    )

    if not results:
        print("[Walk-Forward] 창이 0개 — 합성 데이터 폭이 부족했을 가능성, 종료")
        return

    print(f"\n창 {len(results)}개 완료:")
    for r in results:
        print(
            f"  train[{r.train_start}~{r.train_end}) test[{r.test_start}~{r.test_end}) "
            f"수익률={r.return_pct:+.4%} (학습봉 {r.n_train_bars}·검증봉 {r.n_test_bars})"
        )

    equity_curve = equity_curve_from_windows(results, starting_cash=args.cash)
    window_returns = window_returns_from_windows(results)
    periods_per_year = 365.0 / args.test_days

    print(
        f"\n[Validator] validate_performance() 실행 — 창별 수익률을 '기간별 수익률'로 근사"
        f"(periods_per_year={periods_per_year:.1f}, harness.py 모듈 docstring 근사 참고)"
    )
    gates = Validator().validate_performance(
        daily_returns=window_returns,
        periods_per_year=periods_per_year,
        equity_curve=equity_curve,
        window_returns=window_returns,
    )
    for gate in gates:
        status = "PASS" if gate.passed else "FAIL"
        print(f"  [{status}] {gate.name}: {gate.value:.4f} (임계 {gate.threshold})")
    print(
        "\n합성 데이터(사인파+지터)라 실제 예측력이 없는 게 정상 — 이 스크립트의 목적은"
        " 성과 주장이 아니라 Validator의 3개 성과 관문이 난생 처음 실제 walk-forward"
        " 산출물을 입력받아 계산되는지 확인하는 것이다(Deflated Sharpe는 여전히 미구현,"
        " 알려진 갭)."
    )


async def main(args: argparse.Namespace) -> None:
    await _try_real_archive(args)
    await _run_synthetic(args)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MESSIAH Walk-Forward 백테스트 하니스 스모크")
    parser.add_argument("--symbol", default="A05608")
    parser.add_argument("--start", default="2026-07-24")
    parser.add_argument("--end", default="2026-07-24")
    parser.add_argument("--base-dir", default=str(_DATA_DIR))
    parser.add_argument("--train-days", type=int, default=8)
    parser.add_argument("--test-days", type=int, default=4)
    parser.add_argument("--embargo-days", type=int, default=1)
    parser.add_argument("--n-windows", type=int, default=3, help="생성할 합성 데이터 폭 계산용")
    parser.add_argument("--bars-per-day", type=int, default=60)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--cash", type=int, default=50_000_000)
    parser.add_argument("--n-splits", type=int, default=2)
    parser.add_argument("--n-search-trials", type=int, default=3)
    parser.add_argument("--search-num-boost-round", type=int, default=15)
    parser.add_argument("--final-num-boost-round", type=int, default=25)
    parser.add_argument("--n-members", type=int, default=3)
    parser.add_argument("--meta-num-boost-round", type=int, default=15)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main(_parse_args()))
