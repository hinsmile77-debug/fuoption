"""G1 관문 심사 — 백필한 근월물 연속 시계열로 walk-forward 백테스트를 돌리고
`Validator.validate_performance()`의 3개 관문을 실제 데이터로 계산한다.

이 스크립트가 `run_backtest_harness.py`와 다른 점은 **합성 데이터를 안 쓴다**는 것 하나다.
기존 하니스 스크립트는 "실제 아카이브 시도 → 데이터 부족 실패 → 합성으로 배관 확인"이라는
2단계였고, 그 실패가 정상이었다(2026-08-03까지 아카이브가 7거래일뿐). 백필로 그 전제가
사라졌으므로(`data/backfill.py`) 이 스크립트는 **실제 데이터만** 쓰고, 데이터가 모자라면
합성으로 도망치지 않고 그대로 실패한다.

시계열은 `backfill.load_continuous_series()`가 만든 **후방조정 연속물**이다 — 롤 경계의
가짜 급등이 제거된 상태이며, 조정 내역(롤별 basis)을 실행할 때마다 출력한다. 조정이 조용히
일어나면 나중에 그 성과가 어디서 왔는지 알 수 없게 된다.

**성과 주장이 아니다.** 이 스크립트가 확인하는 것은 "관문이 실제 데이터로 계산된다"와
그 결과값이며, PASS가 곧 우위의 증거는 아니다(표본이 8개월·창 1~2개뿐). live 승격은 여전히
사람이 `ModelRegistry.promote_to_live()`를 불러야 일어난다.

사용:
    python scripts/run_g1_walk_forward.py --train-days 180 --test-days 30   # 프로덕션 기본값
    python scripts/run_g1_walk_forward.py --train-days 120 --test-days 20   # 창을 더 얻고 싶을 때
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.backtest.harness import (  # noqa: E402
    aggregate_to_horizon,
    equity_curve_from_windows,
    run_walk_forward_backtest,
    window_returns_from_windows,
)
from messiah.core.messages import Horizon  # noqa: E402
from messiah.data import backfill  # noqa: E402
from messiah.data.archiver import ParquetArchiver  # noqa: E402
from messiah.models.validator import Validator  # noqa: E402
from messiah.ops import session_guard  # noqa: E402
from messiah.strategy.regime.service import RegimeAI  # noqa: E402

_DATA_DIR = Path("data") / "bars"

# 합성 연속물의 이름 — **거래 가능한 종목코드가 아니다**(`backfill.back_adjust` docstring).
# 실제 월물 코드(A056xx)와 눈으로 구분되게 짓는다.
_CONTINUOUS_SYMBOL = "K200MFC"

# 거래일 1일 ≈ 405분. 연율화에 쓰는 "1년 거래일 수"는 KRX 기준 대략값이다.
_TRADING_DAYS_PER_YEAR = 245.0


def _parse_day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()  # noqa: DTZ007 — 날짜만 다루는 CLI 인자


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=_parse_day, default=date(2025, 12, 12))
    p.add_argument("--end", type=_parse_day, default=None, help="기본값 = 아카이브의 마지막 날")
    p.add_argument("--base-dir", default=str(_DATA_DIR))
    p.add_argument("--train-days", type=int, default=180)
    p.add_argument("--test-days", type=int, default=30)
    p.add_argument("--embargo-days", type=int, default=1)
    p.add_argument("--train-horizon", default=Horizon.M5.value)
    p.add_argument("--cash", type=int, default=50_000_000)
    p.add_argument("--n-splits", type=int, default=3)
    p.add_argument("--n-search-trials", type=int, default=5)
    p.add_argument("--search-num-boost-round", type=int, default=20)
    p.add_argument("--final-num-boost-round", type=int, default=30)
    p.add_argument("--n-members", type=int, default=3)
    p.add_argument("--meta-num-boost-round", type=int, default=20)
    p.add_argument(
        "--meta-threshold-splits",
        type=int,
        default=5,
        help="임계값 선택용 통과확률을 out-of-fold로 만들 폴드 수(1이면 종전 in-sample)",
    )
    p.add_argument(
        "--meta-min-support",
        type=float,
        default=0.05,
        help="임계값 후보가 남겨야 할 최소 신호 비율 — 표본 몇 개짜리 극단 임계값 방지",
    )
    p.add_argument(
        "--regime",
        default="off",
        choices=["off", "on"],
        help="on이면 RegimeAI를 학습해 RegimeRuntime을 결선한다. off는 중립이 아니라 "
        "'항상 UNKNOWN(가중치 0.5 고정)'이라는 특정 가정이다.",
    )
    p.add_argument("--out", default=None, help="결과 JSON 저장 경로")
    session_guard.add_force_intraday_argument(p)
    return p.parse_args()


async def main() -> int:
    args = _parse_args()
    session_guard.refuse_if_regular_session("G1 워크포워드", force=args.force_intraday)
    archiver = ParquetArchiver(Path(args.base_dir))

    end = args.end
    if end is None:
        # 아카이브에 실제로 있는 마지막 날 — 월물 전체에서 가장 늦은 날짜.
        base = Path(args.base_dir)
        days = [
            d
            for symbol in (p.name for p in base.iterdir() if p.is_dir())
            for d in archiver.available_days(symbol, Horizon.M1)
        ]
        if not days:
            print(
                "아카이브가 비어 있다 — scripts/run_backfill.py 를 먼저 실행할 것",
                file=sys.stderr,
            )
            return 2
        end = max(days)

    segments = backfill.front_month_days(args.start, end)
    bars, rolls = backfill.load_continuous_series(archiver, segments, symbol_out=_CONTINUOUS_SYMBOL)
    if not bars:
        print("연속 시계열이 비어 있다 — 백필이 안 됐거나 구간이 틀렸다", file=sys.stderr)
        return 2

    span_days = (bars[-1].bar_open_kst.date() - bars[0].bar_open_kst.date()).days
    first_day, last_day = bars[0].bar_open_kst.date(), bars[-1].bar_open_kst.date()
    print(f"연속 시계열: {len(bars)}봉  {first_day} ~ {last_day}")
    print(f"  구간 길이 {span_days} 캘린더일 / 월물 {len(segments)}개")
    print("\n롤 조정 내역 (basis = 같은 날 같은 분의 들어오는-나가는, 틱):")
    for info in rolls:
        where = f"{info.matched_minute} 기준" if info.matched_minute else "겹침 없음 — 조정 0"
        print(
            f"  {info.outgoing} → {info.incoming}  {info.day}  {info.offset_ticks:+d}틱  ({where})"
        )
    unmatched = [r for r in rolls if r.matched_minute is None]
    if unmatched:
        print(
            f"\n경고: 겹침 데이터가 없는 롤 {len(unmatched)}건 — 그 경계의 가짜 급등이 "
            f"조정되지 않은 채 남아 있다(run_backfill.py를 --skip-existing으로 재실행하면 "
            f"롤 겹침을 채운다).",
            file=sys.stderr,
        )

    needed = args.train_days + args.embargo_days + args.test_days
    print(
        f"\nwalk-forward 요건: train {args.train_days} + embargo {args.embargo_days} + "
        f"test {args.test_days} = {needed} 캘린더일 (보유 {span_days}일)"
    )
    if span_days < needed:
        print(
            "데이터 부족 — 창이 하나도 안 나온다. 합성으로 대체하지 않고 여기서 멈춘다.",
            file=sys.stderr,
        )
        return 3

    regime_ai = None
    if args.regime == "on":
        # 구동 Horizon(30m) 봉으로 학습한다. **첫 창의 학습 구간만** 쓰는 게 이상적이지만
        # 그러면 창마다 RegimeAI를 다시 학습해야 해 런타임이 배로 든다 — 지금은 전 구간으로
        # 한 번 학습하고 그 사실을 여기 남긴다. 국면 판정에 검증 구간 정보가 새어 들어가는
        # 약한 look-ahead이며, 성과를 주장할 때 반드시 함께 언급해야 하는 한계다.
        regime_bars = aggregate_to_horizon(bars, Horizon.M30)
        print(f"\nRegimeAI 학습 — 30m {len(regime_bars)}봉 (알려진 한계: 전 구간 학습)")
        regime_ai = RegimeAI.fit(regime_bars)
        print(f"  상태 수 {regime_ai.n_states} · 명명 {regime_ai.labels}")

    print("\n백테스트 시작 (창마다 재학습 — 수 분 걸린다)")
    results = await run_walk_forward_backtest(
        bars,
        symbol=_CONTINUOUS_SYMBOL,
        train_days=args.train_days,
        test_days=args.test_days,
        embargo_days=args.embargo_days,
        train_horizon=Horizon(args.train_horizon),
        n_splits=args.n_splits,
        n_search_trials=args.n_search_trials,
        search_num_boost_round=args.search_num_boost_round,
        final_num_boost_round=args.final_num_boost_round,
        n_members=args.n_members,
        meta_num_boost_round=args.meta_num_boost_round,
        meta_threshold_splits=args.meta_threshold_splits,
        meta_min_support_fraction=args.meta_min_support,
        starting_cash=args.cash,
        regime_ai=regime_ai,
    )
    if not results:
        print("창이 0개 — train/test 일수를 데이터 규모에 맞게 줄일 것", file=sys.stderr)
        return 3

    print(f"\n창 {len(results)}개:")
    for r in results:
        print(
            f"  train {r.train_start}~{r.train_end} ({r.n_train_bars}봉) → "
            f"test {r.test_start}~{r.test_end} ({r.n_test_bars}봉)  "
            f"수익률 {r.return_pct:+.4%}"
        )

    window_returns = window_returns_from_windows(results)
    equity = equity_curve_from_windows(results, args.cash)
    # 창별 수익률을 표본으로 연율화한다 — 창 하나가 test_days 만큼의 기간이므로
    # 1년에 그 창이 몇 번 들어가는지가 periods_per_year다.
    periods_per_year = _TRADING_DAYS_PER_YEAR / max(1, args.test_days)
    gates = Validator().validate_performance(
        daily_returns=window_returns,
        periods_per_year=periods_per_year,
        equity_curve=equity,
        window_returns=window_returns,
    )

    print("\nG1 관문 (Ver 1.2 §8.3):")
    for gate in gates:
        mark = "PASS" if gate.passed else "FAIL"
        print(f"  [{mark}] {gate.name}: {gate.value:.4f} (임계 {gate.threshold})")
    passed = all(g.passed for g in gates)
    print(f"\nG1 종합: {'PASS' if passed else 'FAIL'}")
    print(
        "주의: 이 결과는 '관문이 실제 데이터로 계산됐다'는 사실이지 우위의 증거가 아니다 — "
        f"표본은 창 {len(results)}개뿐이고, `negative_window_ratio`는 창이 3개 이상일 때부터 "
        "의미를 갖는다."
    )

    if args.out:
        payload = {
            "generated_at": datetime.now().astimezone().isoformat(),  # noqa: DTZ005
            "symbol": _CONTINUOUS_SYMBOL,
            "bars": len(bars),
            "span_days": span_days,
            "rolls": [
                {
                    "outgoing": r.outgoing,
                    "incoming": r.incoming,
                    "day": r.day.isoformat(),
                    "offset_ticks": r.offset_ticks,
                    "matched_minute": r.matched_minute,
                }
                for r in rolls
            ],
            "windows": [
                {
                    "train_start": r.train_start.isoformat(),
                    "test_start": r.test_start.isoformat(),
                    "test_end": r.test_end.isoformat(),
                    "return_pct": r.return_pct,
                }
                for r in results
            ],
            "gates": [
                {"name": g.name, "passed": g.passed, "value": g.value, "threshold": g.threshold}
                for g in gates
            ],
            "passed": passed,
        }
        Path(args.out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n결과 저장: {args.out}")

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
