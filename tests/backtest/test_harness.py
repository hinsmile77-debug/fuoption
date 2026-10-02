"""Walk-Forward 백테스트 하니스 (신규, 2026-07-27)."""

from __future__ import annotations

import math
import random
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from messiah.backtest.harness import (
    WindowResult,
    _slice_by_date,
    aggregate_to_horizon,
    aggregate_to_horizon_legacy,
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


def test_aggregate_to_horizon_keeps_a_short_trailing_bucket_like_live():
    """2026-10-02 P0-1 — 실시간 조립기처럼 짧은 버킷도 봉으로 남되 `quality_ok=False`다.

    종전엔 "나머지 2분은 버림"이었다. 실전 FeatureEngine은 15:30–15:35 같은 짧은 버킷을
    받으므로, 학습이 그 봉을 버리면 같은 분포가 아니다.
    """
    bars = _m1_bars(n_days=1, bars_per_day=12)  # 12분 -> 5분봉 2개 + 2분짜리 1개
    m5 = aggregate_to_horizon(bars, Horizon.M5)
    assert len(m5) == 3
    assert [b.quality_ok for b in m5] == [True, True, False]


def _session_m1(day: date, start_hhmm: tuple[int, int], n: int) -> list[BarClosed]:
    start = datetime(day.year, day.month, day.day, *start_hhmm, tzinfo=KST)
    return [
        BarClosed(
            symbol=_SYMBOL,
            horizon=Horizon.M1,
            bar_open_kst=start + timedelta(minutes=i),
            o_ticks=1000 + i,
            h_ticks=1001 + i,
            l_ticks=999 + i,
            c_ticks=1000 + i,
            volume=10,
        )
        for i in range(n)
    ]


def test_buckets_sit_on_the_live_clock_grid():
    """**핵심 회귀** — 08:45 시작·410분 세션이어도 30m 봉은 :00/:30에 선다(서빙봉과 같은 격자).

    종전(개수 자르기)은 08:45·09:15·…로 시작해 실시간의 08:30·09:00·…과 어긋났고, 하루
    410분이 30의 배수가 아니라 다음 날엔 또 20분 밀렸다.
    """
    bars = _session_m1(date(2026, 9, 22), (8, 45), 410) + _session_m1(
        date(2026, 9, 23), (8, 45), 410
    )
    m30 = aggregate_to_horizon(bars, Horizon.M30)

    assert all(b.bar_open_kst.astimezone(KST).minute in (0, 30) for b in m30)
    assert m30[0].bar_open_kst.astimezone(KST).strftime("%H:%M") == "08:30"


def test_no_bucket_straddles_two_trading_days():
    """**핵심 회귀** — 장 마감 봉과 다음 날 아침 봉을 한 봉에 섞지 않는다(밤사이 갭 차단)."""
    bars = _session_m1(date(2026, 9, 22), (8, 45), 410) + _session_m1(
        date(2026, 9, 23), (8, 45), 410
    )
    m30 = aggregate_to_horizon(bars, Horizon.M30)

    day_one = [b for b in bars if b.bar_open_kst.astimezone(KST).date() == date(2026, 9, 22)]
    last_of_day_one = [
        b for b in m30 if b.bar_open_kst.astimezone(KST).date() == date(2026, 9, 22)
    ][-1]
    assert last_of_day_one.c_ticks == day_one[-1].c_ticks, "다음 날 봉이 섞이면 종가가 바뀐다"
    assert last_of_day_one.volume == sum(
        b.volume for b in day_one if b.bar_open_kst >= last_of_day_one.bar_open_kst
    )

    # 옛 방식은 실제로 섞었다 — 첫날에 시작한 마지막 봉의 종가가 **다음 날** 1분봉의 것이다.
    legacy = aggregate_to_horizon_legacy(bars, Horizon.M30)
    straddling = [b for b in legacy if b.bar_open_kst.astimezone(KST).date() == date(2026, 9, 22)][
        -1
    ]
    assert straddling.c_ticks != day_one[-1].c_ticks


def test_legacy_aggregation_reproduces_the_old_count_based_grid():
    """비교 실험(`--legacy-bars`)용 — 옛 방식이 정말로 개수로 잘랐음을 고정해 둔다."""
    bars = _session_m1(date(2026, 9, 22), (8, 45), 410) + _session_m1(
        date(2026, 9, 23), (8, 45), 410
    )
    legacy = aggregate_to_horizon_legacy(bars, Horizon.M30)

    assert len(legacy) == 820 // 30
    assert legacy[0].bar_open_kst.astimezone(KST).strftime("%H:%M") == "08:45"
    day_two_first = [
        b for b in legacy if b.bar_open_kst.astimezone(KST).date() == date(2026, 9, 23)
    ][0]
    assert day_two_first.bar_open_kst.astimezone(KST).minute not in (0, 30, 45)


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


# ---------------------------------------------------------------- 왕복 거래 복원 (2026-10-02 P1)


def _fill(minute: int, qty: int, price: int):
    from messiah.broker.simulator.adapter import SimFillRecord

    return SimFillRecord(
        ts=datetime(2026, 9, 22, 14, 30, tzinfo=KST) + timedelta(minutes=minute),
        symbol=_SYMBOL,
        signed_qty=qty,
        price_ticks=price,
        kind="ENTRY",
    )


def test_round_trip_pnl_is_the_cash_flow_of_the_cycle():
    """LONG 2계약 100 → 1계약씩 110·120에 분할 청산 = (110−100)+(120−100) = +30틱."""
    from messiah.backtest.harness import round_trips_from_fills

    [trip] = round_trips_from_fills([_fill(0, 2, 100), _fill(55, -1, 110), _fill(56, -1, 120)])

    assert trip.pnl_ticks == 30.0
    assert trip.direction == 1 and trip.max_abs_qty == 2
    assert trip.entry_hhmm == "14:30"
    assert trip.holding_minutes == 56.0


def test_a_flip_closes_one_trip_and_opens_the_next():
    """+1 → −1 한 번에 뒤집으면 바퀴가 둘이다 — 닫힌 쪽 손익은 앞 바퀴의 것이다."""
    from messiah.backtest.harness import round_trips_from_fills

    trips = round_trips_from_fills([_fill(0, 1, 100), _fill(30, -2, 90), _fill(40, 1, 80)])

    assert [t.direction for t in trips] == [1, -1]
    assert [t.pnl_ticks for t in trips] == [-10.0, 10.0]


def test_an_unclosed_trip_is_not_counted():
    from messiah.backtest.harness import round_trips_from_fills

    assert round_trips_from_fills([_fill(0, 1, 100)]) == []


@pytest.mark.asyncio
async def test_replay_drives_the_eod_watchdog_and_the_daily_reset():
    """실전 조건 재생(2026-10-02 P0-3): 1분봉마다 EOD 틱 1번, 날짜가 바뀔 때마다 `start_day()`.

    종전엔 둘 다 없었다 — 백테스트 포지션이 15:25에 닫히지 않고 밤을 넘겼다.
    """
    from messiah.backtest.harness import _feed_m1_bars
    from messiah.broker.simulator.adapter import SimBroker
    from messiah.data.archiver import ParquetArchiver
    from messiah.data.bar_composer import MultiHorizonBarComposer
    from messiah.simulator.inprocess_bus import InProcessBus

    class _Probe:
        def __init__(self):
            self.ticks = 0
            self.days = 0

        async def observe_eod_flatten_tick(self):
            self.ticks += 1

        async def start_day(self):
            self.days += 1

    bars = _m1_bars(n_days=3, bars_per_day=10)
    bus = InProcessBus()
    broker = SimBroker()
    await broker.connect()
    probe = _Probe()
    with tempfile.TemporaryDirectory() as tmp:
        composer = MultiHorizonBarComposer(_SYMBOL, ParquetArchiver(Path(tmp)), bus)
        await _feed_m1_bars(bars, composer, broker, bus, pipeline=probe)

    assert probe.ticks == len(bars)
    assert probe.days == 2, "첫날은 호출자가 이미 start_day()를 불렀다 — 날짜가 바뀐 두 번만"
