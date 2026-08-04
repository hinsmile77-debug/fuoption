"""Walk-Forward 백테스트 하니스 (신규, 2026-07-27)."""

from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta

import pytest

from messiah.backtest.harness import (
    WindowResult,
    _slice_by_date,
    aggregate_to_horizon,
    equity_curve_from_windows,
    run_walk_forward_backtest,
    window_returns_from_windows,
)
from messiah.core.messages import BarClosed, Horizon
from messiah.core.timeutil import KST

_SYMBOL = "TEST"


def _m1_bars(n_days: int, bars_per_day: int, *, seed: int = 7) -> list[BarClosed]:
    """여러 "거래일"에 걸친 연속 M1봉 — 하루=`bars_per_day`분, 날짜만 갈라지면 되므로
    실제 세션 시각과 무관하게 매일 09:00부터 시작(EventCalendar 무관, 이 하니스의 스코프
    밖 — 모듈 docstring 참고)."""
    rng = random.Random(seed)
    out: list[BarClosed] = []
    price = 1000.0
    for day in range(n_days):
        day_start = datetime(2026, 1, 5, 9, 0, tzinfo=KST) + timedelta(days=day)
        for i in range(bars_per_day):
            price += 0.3 * math.sin((day * bars_per_day + i) / 6) + rng.uniform(-1.0, 1.0)
            price = max(price, 100.0)
            close = round(price)
            out.append(
                BarClosed(
                    symbol=_SYMBOL,
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


# ---------------------------------------------------------------- 순수 헬퍼


def test_aggregate_to_horizon_rolls_up_ohlcv_correctly():
    bars = _m1_bars(n_days=1, bars_per_day=10)
    m5 = aggregate_to_horizon(bars, Horizon.M5)
    assert len(m5) == 2  # 10분 -> 5분봉 2개
    first_chunk = bars[:5]
    assert m5[0].o_ticks == first_chunk[0].o_ticks
    assert m5[0].c_ticks == first_chunk[-1].c_ticks
    assert m5[0].h_ticks == max(b.h_ticks for b in first_chunk)
    assert m5[0].l_ticks == min(b.l_ticks for b in first_chunk)
    assert m5[0].volume == sum(b.volume for b in first_chunk)


def test_aggregate_to_horizon_drops_incomplete_trailing_chunk():
    bars = _m1_bars(n_days=1, bars_per_day=12)  # 12분 -> 5분봉 2개, 나머지 2분은 버림
    m5 = aggregate_to_horizon(bars, Horizon.M5)
    assert len(m5) == 2


def test_aggregate_to_horizon_m1_is_identity():
    bars = _m1_bars(n_days=1, bars_per_day=5)
    assert aggregate_to_horizon(bars, Horizon.M1) == list(bars)


def test_slice_by_date_is_half_open_interval():
    bars = _m1_bars(n_days=3, bars_per_day=5)
    sliced = _slice_by_date(bars, date(2026, 1, 5), date(2026, 1, 6))
    assert all(b.bar_open_kst.date() == date(2026, 1, 5) for b in sliced)
    assert len(sliced) == 5


def test_equity_curve_from_windows_prepends_starting_cash():
    results = [
        WindowResult(
            train_start=date(2026, 1, 1),
            train_end=date(2026, 1, 10),
            test_start=date(2026, 1, 10),
            test_end=date(2026, 1, 12),
            start_equity=50_000_000,
            end_equity=51_000_000,
            n_train_bars=100,
            n_test_bars=20,
        )
    ]
    curve = equity_curve_from_windows(results, starting_cash=50_000_000)
    assert curve == [50_000_000.0, 51_000_000.0]


def test_window_returns_from_windows_computes_pct_change():
    results = [
        WindowResult(
            train_start=date(2026, 1, 1),
            train_end=date(2026, 1, 10),
            test_start=date(2026, 1, 10),
            test_end=date(2026, 1, 12),
            start_equity=50_000_000,
            end_equity=52_500_000,
            n_train_bars=100,
            n_test_bars=20,
        )
    ]
    assert window_returns_from_windows(results) == pytest.approx([0.05])


def test_window_result_return_pct_zero_when_start_equity_non_positive():
    result = WindowResult(
        train_start=date(2026, 1, 1),
        train_end=date(2026, 1, 10),
        test_start=date(2026, 1, 10),
        test_end=date(2026, 1, 12),
        start_equity=0,
        end_equity=1000,
        n_train_bars=1,
        n_test_bars=1,
    )
    assert result.return_pct == 0.0


# ---------------------------------------------------------------- 통합(실제 학습+재생)


@pytest.mark.asyncio
async def test_run_walk_forward_backtest_produces_windows_end_to_end():
    # 학습·검증 창 2개가 나오도록 16일치 데이터(학습 8일/검증 4일/embargo 1일, step=4일
    # -> 창1: train[0,8) test[8,12), 창2: train[4,12) test[12,16)).
    bars = _m1_bars(n_days=16, bars_per_day=40)  # 하루 40분(M5 8건) — 학습 폭주 방지용 최소량

    results = await run_walk_forward_backtest(
        bars,
        symbol=_SYMBOL,
        train_days=8,
        test_days=4,
        embargo_days=1,
        n_splits=2,
        n_search_trials=1,
        search_num_boost_round=5,
        final_num_boost_round=5,
        n_members=2,
        meta_num_boost_round=5,
    )

    assert len(results) >= 1
    for r in results:
        assert r.train_start < r.test_start <= r.test_end
        assert r.n_train_bars > 0
        assert r.n_test_bars > 0
        assert r.start_equity > 0


@pytest.mark.asyncio
async def test_run_walk_forward_backtest_feeds_validator_performance_gates():
    from messiah.models.validator import Validator

    bars = _m1_bars(n_days=16, bars_per_day=40)
    results = await run_walk_forward_backtest(
        bars,
        symbol=_SYMBOL,
        train_days=8,
        test_days=4,
        embargo_days=1,
        n_splits=2,
        n_search_trials=1,
        search_num_boost_round=5,
        final_num_boost_round=5,
        n_members=2,
        meta_num_boost_round=5,
    )
    assert results  # 사전 조건 — 비어 있으면 아래 관문 호출 자체가 무의미

    equity_curve = equity_curve_from_windows(results, starting_cash=50_000_000)
    window_returns = window_returns_from_windows(results)

    gates = Validator().validate_performance(
        daily_returns=window_returns,  # 근사 — 모듈 docstring "창=기간 근사" 참고
        periods_per_year=365 / 5,
        equity_curve=equity_curve,
        window_returns=window_returns,
    )
    gate_names = {g.name for g in gates}
    assert gate_names == {"cost_adjusted_sharpe", "max_drawdown", "negative_window_ratio"}
    # 성능 주장이 아니라 "관문이 실제로 계산 가능한 입력을 받는다"만 확인 — pass/fail 무관.
    for gate in gates:
        assert math.isfinite(gate.value)


# ---------------------------------------------------------------- 재생 시계 (2026-08-04)


def test_replay_clock_falls_back_to_wall_clock_before_the_first_bar():
    from messiah.backtest.harness import ReplayClock
    from messiah.core.timeutil import now_utc

    clock = ReplayClock()

    assert (now_utc() - clock()).total_seconds() < 5


def test_replay_clock_reports_the_confirm_time_of_the_bar_being_fed():
    """`bar_open_kst`가 아니라 확정시각 — 완성봉은 그때부터 소비 가능하다(Ver 1.2 §2.2)."""
    from messiah.backtest.harness import ReplayClock
    from messiah.core.messages import bar_confirm_time

    bar = _m1_bars(1, 1)[0]
    clock = ReplayClock()
    clock.advance_to(bar)

    assert clock() == bar_confirm_time(bar)
    assert clock() == bar.bar_open_kst + timedelta(minutes=1)


@pytest.mark.asyncio
async def test_replayed_bars_do_not_look_stale_to_the_pipeline():
    """이 하니스가 2026-08-04까지 **구조적으로 무거래**였던 원인의 회귀 테스트.

    재생 메시지는 `BusMessage.ts_utc` 기본값(`now_utc()`) 때문에 "지금"으로 스탬프되는데
    봉은 과거 것이라, 파이프라인이 벽시계로 신선도를 재면 `data_age`가 수십 일이 된다 —
    KillSwitch R11(임계 30초)이 매 판단마다 발동해 신규 진입이 영영 안 났다.
    재생 시계를 주입하면 그 값이 봉 간격 수준으로 떨어진다.
    """
    from messiah.backtest.harness import ReplayClock
    from messiah.core.messages import bar_confirm_time
    from messiah.risk.kill_switch import KillSwitchConfig

    bars = _m1_bars(1, 30)
    clock = ReplayClock()
    for bar in bars:
        clock.advance_to(bar)

    data_age = (clock() - bar_confirm_time(bars[-1])).total_seconds()

    assert data_age == 0
    assert data_age < KillSwitchConfig().data_disconnect_limit_seconds
