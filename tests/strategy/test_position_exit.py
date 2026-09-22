"""장중 청산 판정 (2026-09-22 F-119).

이 파일이 지키는 것 여섯:
  ① 손절선에 닿기 전에는 절대 안 쏜다 — 그리고 정확히 닿으면 쏜다
  ② LONG·SHORT가 **대칭**이다 — 부호 하나로 두 경우를 같이 쓰는 것이 옳은지 잰다
  ③ 우선순위는 손절 > 시간배리어 > 익절 (Holding Policy §4)
  ④ 시간배리어 숫자는 **라벨에서 온다** — 여기 복사돼 있지 않다
  ⑤ 모르는 것(ATR 워밍업·구동 Horizon 미상)으로는 청산하지 않는다
  ⑥ 방금 보낸 것을 또 보내지 않는다 — 중복 청산은 미청산보다 고치기 어렵다
"""

from datetime import timedelta

import pytest

from messiah.broker.base import BrokerPosition
from messiah.core.messages import Horizon
from messiah.core.timeutil import KST, now_utc
from messiah.models.labeling import BARRIER_PARAMS
from messiah.strategy.position_exit import (
    DEFAULT_RESUBMIT_COOLDOWN_SECONDS,
    ExitReason,
    ExitStateTracker,
    HeldFutures,
    decide,
    time_barrier_minutes,
)

_SYMBOL = "A05610"
_ENTRY = 41_000
_ATR = 50.0


def _pos(qty: int = 1, symbol: str = _SYMBOL, avg: int = _ENTRY) -> BrokerPosition:
    return BrokerPosition(symbol=symbol, qty=qty, avg_price_ticks=avg)


def _held(
    *,
    qty: int = 1,
    avg: int = _ENTRY,
    atr: float = _ATR,
    minutes_held: float | None = 1.0,
    barrier: float | None = 90.0,
) -> HeldFutures:
    return HeldFutures(
        position=_pos(qty, avg=avg),
        entry_price_ticks=avg,
        stop_distance_ticks=atr,
        minutes_held=minutes_held,
        time_barrier_minutes=barrier,
    )


# --- ① 손절선 경계 -----------------------------------------------------------


def test_one_tick_above_the_stop_does_not_fire():
    """손절선 **바로 위**에서는 안 쏜다. 이 경계가 새면 정상 변동이 손절로 둔갑한다."""
    plan = decide(last_price_ticks=_ENTRY - 49, held=[_held()])

    assert plan.should_exit is False
    assert plan.slices == ()


def test_exactly_at_the_stop_fires():
    """정확히 1.0×ATR 불리하면 **닿은 것**이다 — `>=`로 판정한다."""
    plan = decide(last_price_ticks=_ENTRY - 50, held=[_held()])

    assert plan.should_exit is True
    assert plan.slices[0].reason is ExitReason.STOP_LOSS
    assert plan.slices[0].qty == 1


def test_stop_multiple_scales_the_line():
    """배수는 그대로 폭이 된다 — 2.0이면 100틱까지는 안 나간다."""
    assert decide(last_price_ticks=_ENTRY - 99, held=[_held()], stop_atr_mult=2.0).should_exit is (
        False
    )
    assert decide(last_price_ticks=_ENTRY - 100, held=[_held()], stop_atr_mult=2.0).should_exit is (
        True
    )


def test_exit_sends_the_whole_position_not_a_slice():
    """손절은 분할하지 않는다 — 분할은 시간을 사는 일이고, 손절은 시간이 적이다."""
    plan = decide(last_price_ticks=_ENTRY - 80, held=[_held(qty=5)])

    assert plan.slices[0].qty == 5


# --- ② LONG·SHORT 대칭 -------------------------------------------------------


def test_short_loses_when_price_rises():
    """SHORT는 **오를 때** 손실이다. 부호 하나로 두 경우를 쓰는 것이 옳은지 재는 자리."""
    plan = decide(last_price_ticks=_ENTRY + 50, held=_held_short())

    assert plan.should_exit is True
    assert plan.slices[0].reason is ExitReason.STOP_LOSS


def test_short_does_not_exit_when_price_falls():
    """SHORT가 이기고 있는데 나가면 손절의 부호가 뒤집힌 것이다."""
    plan = decide(last_price_ticks=_ENTRY - 500, held=_held_short())

    assert plan.should_exit is False


def _held_short():
    return [
        HeldFutures(
            position=_pos(qty=-1),
            entry_price_ticks=_ENTRY,
            stop_distance_ticks=_ATR,
            minutes_held=1.0,
            time_barrier_minutes=90.0,
        )
    ]


# --- ③ 우선순위 (Holding Policy §4) ------------------------------------------


def test_time_barrier_fires_when_price_is_quiet():
    """가격이 조용해도 시간이 다 되면 나간다 — Type A는 신호 유효기간을 넘겨 들지 않는다."""
    plan = decide(last_price_ticks=_ENTRY, held=[_held(minutes_held=90.0, barrier=90.0)])

    assert plan.slices[0].reason is ExitReason.TIME_BARRIER


def test_stop_loss_wins_over_time_barrier():
    """둘 다 걸리면 **손절**이 사유다 — 자본 보존이 규율보다 앞선다(§4 ①>②)."""
    plan = decide(last_price_ticks=_ENTRY - 80, held=[_held(minutes_held=999.0, barrier=90.0)])

    assert plan.slices[0].reason is ExitReason.STOP_LOSS


def test_take_profit_is_off_by_default():
    """익절은 이번 스코프에 없다 — 2.4×ATR을 이기고 있어도 기본값으로는 안 자른다."""
    plan = decide(last_price_ticks=_ENTRY + 120, held=[_held()])

    assert plan.should_exit is False


def test_take_profit_fires_only_when_switched_on():
    plan = decide(last_price_ticks=_ENTRY + 50, held=[_held()], take_profit_atr_mult=1.0)

    assert plan.slices[0].reason is ExitReason.TAKE_PROFIT
    assert plan.slices[0].detail["target_ticks"] == pytest.approx(50.0)


def test_time_barrier_wins_over_take_profit():
    """§4 ②>③ — 시간 상한은 예외가 없고, 익절은 협상 가능한 쪽이다."""
    plan = decide(
        last_price_ticks=_ENTRY + 120,
        held=[_held(minutes_held=90.0, barrier=90.0)],
        take_profit_atr_mult=1.0,
    )

    assert plan.slices[0].reason is ExitReason.TIME_BARRIER


# --- ④ 시간배리어는 라벨에서 온다 --------------------------------------------


def test_time_barrier_minutes_comes_from_the_label_table():
    """30분 주기 판단 → 3봉 × 30분 = 90분. 숫자가 아니라 **출처**를 고정한다."""
    expected = BARRIER_PARAMS[Horizon.M30].time_barrier_bars * 30.0

    assert time_barrier_minutes(1800.0) == pytest.approx(expected)


def test_time_barrier_minutes_tracks_every_known_horizon():
    """Horizon 사다리 전체가 라벨 표를 따라간다 — 표가 바뀌면 여기도 저절로 따라간다."""
    for horizon, seconds in ((Horizon.M1, 60), (Horizon.M5, 300), (Horizon.M15, 900)):
        expected = BARRIER_PARAMS[horizon].time_barrier_bars * seconds / 60.0
        assert time_barrier_minutes(float(seconds)) == pytest.approx(expected)


def test_unknown_cadence_yields_no_time_barrier():
    """아는 Horizon과 안 맞으면 None이다 — 모르는 값으로 청산 시각을 지어내지 않는다."""
    assert time_barrier_minutes(1234.0) is None
    assert time_barrier_minutes(None) is None
    assert time_barrier_minutes(0.0) is None


def test_missing_time_barrier_still_allows_stop_loss():
    """구동 Horizon을 몰라도 **손절은 산다** — 하나를 모른다고 다른 하나를 끄지 않는다."""
    quiet = decide(last_price_ticks=_ENTRY, held=[_held(minutes_held=999.0, barrier=None)])
    losing = decide(last_price_ticks=_ENTRY - 60, held=[_held(minutes_held=999.0, barrier=None)])

    assert quiet.should_exit is False
    assert losing.slices[0].reason is ExitReason.STOP_LOSS


# --- ⑤ 모르는 것으로는 청산하지 않는다 ---------------------------------------


def test_no_price_means_no_judgment():
    """완성봉 종가가 없으면 판정 자체를 안 한다 — 없는 가격으로 손절할 방법은 없다."""
    plan = decide(last_price_ticks=None, held=[_held(atr=_ATR)])

    assert plan.should_exit is False
    assert "종가" in plan.reason


def test_atr_warmup_skips_the_position_instead_of_using_zero():
    """ATR을 아직 못 잡았으면 그 포지션은 빠진다 — 0으로 대신하면 **전량 즉시 손절**이다."""
    plan = decide(last_price_ticks=_ENTRY - 5_000, held=[_held(atr=0.0)])

    assert plan.should_exit is False


def test_zero_qty_position_is_ignored():
    plan = decide(last_price_ticks=_ENTRY - 5_000, held=[_held(qty=0)])

    assert plan.should_exit is False


def test_unknown_age_never_triggers_the_time_barrier():
    """나이를 모르면(`minutes_held=None`) 시간배리어는 비적용이다."""
    plan = decide(last_price_ticks=_ENTRY, held=[_held(minutes_held=None, barrier=1.0)])

    assert plan.should_exit is False


# --- ⑥ 중복 청산 방지 --------------------------------------------------------


def test_cooling_symbol_is_skipped():
    """방금 보낸 것이 아직 포지션에 안 잡혔다 — 또 보내면 **반대 포지션이 생긴다**."""
    plan = decide(last_price_ticks=_ENTRY - 80, held=[_held()], cooling={_SYMBOL: True})

    assert plan.should_exit is False


# --- 추적기 (ExitStateTracker) -----------------------------------------------


def _t0():
    return now_utc().astimezone(KST).replace(microsecond=0)


def test_tracker_starts_the_clock_at_first_sight():
    tracker = ExitStateTracker()
    t0 = _t0()

    held, armed = tracker.observe([_pos()], as_of=t0, atr_ticks=_ATR, cadence_seconds=1800.0)

    assert held[0].minutes_held == pytest.approx(0.0)
    assert held[0].stop_distance_ticks == _ATR
    assert held[0].time_barrier_minutes == pytest.approx(90.0)
    assert [n.symbol for n in armed] == [_SYMBOL]


def test_tracker_announces_each_position_once():
    """`PositionExitArmed`는 포지션당 한 번이다 — 매 봉 울면 로그가 못 쓰게 된다."""
    tracker = ExitStateTracker()
    t0 = _t0()
    tracker.observe([_pos()], as_of=t0, atr_ticks=_ATR, cadence_seconds=1800.0)

    _held_2, armed = tracker.observe(
        [_pos()], as_of=t0 + timedelta(minutes=1), atr_ticks=_ATR, cadence_seconds=1800.0
    )

    assert armed == []


def test_tracker_ages_the_position():
    tracker = ExitStateTracker()
    t0 = _t0()
    tracker.observe([_pos()], as_of=t0, atr_ticks=_ATR, cadence_seconds=1800.0)

    held, _ = tracker.observe(
        [_pos()], as_of=t0 + timedelta(minutes=45), atr_ticks=_ATR, cadence_seconds=1800.0
    )

    assert held[0].minutes_held == pytest.approx(45.0)


def test_adding_contracts_does_not_rewind_the_clock():
    """**핵심 회귀** — 물타기로 시계를 되돌릴 수 있으면 시간배리어를 무한히 미룰 수 있다.

    2026-09-16 실거래가 정확히 이 형태였다(14:30 1계약 → 15:00 1계약, 같은 심볼 2계약).
    """
    tracker = ExitStateTracker()
    t0 = _t0()
    tracker.observe([_pos(qty=1)], as_of=t0, atr_ticks=_ATR, cadence_seconds=1800.0)

    held, _ = tracker.observe(
        [_pos(qty=2, avg=41_020)],
        as_of=t0 + timedelta(minutes=30),
        atr_ticks=_ATR,
        cadence_seconds=1800.0,
    )

    assert held[0].minutes_held == pytest.approx(30.0), "수량이 늘어도 나이는 그대로여야 한다"
    assert held[0].entry_price_ticks == 41_020, "진입가는 매번 브로커 평균단가를 그대로 쓴다"


def test_flipping_direction_starts_a_new_position():
    """뒤집기는 같은 심볼에 **전혀 다른 포지션**이 서는 일이다 — 시계를 다시 시작한다."""
    tracker = ExitStateTracker()
    t0 = _t0()
    tracker.observe([_pos(qty=1)], as_of=t0, atr_ticks=_ATR, cadence_seconds=1800.0)

    held, armed = tracker.observe(
        [_pos(qty=-1)], as_of=t0 + timedelta(minutes=60), atr_ticks=_ATR, cadence_seconds=1800.0
    )

    assert held[0].minutes_held == pytest.approx(0.0)
    assert [n.symbol for n in armed] == [_SYMBOL], "새 포지션이므로 손절선을 다시 알린다"


def test_tracker_forgets_closed_symbols():
    tracker = ExitStateTracker()
    t0 = _t0()
    tracker.observe([_pos()], as_of=t0, atr_ticks=_ATR, cadence_seconds=1800.0)

    held, _ = tracker.observe(
        [], as_of=t0 + timedelta(minutes=1), atr_ticks=_ATR, cadence_seconds=1800.0
    )

    assert held == []
    assert tracker.cooling(t0 + timedelta(minutes=1)) == {}


def test_atr_is_picked_up_late_when_warmup_finishes():
    """첫 관측 때 ATR이 없었어도 **다음 봉에 다시 잡는다** — 영구히 무장 해제되면 안 된다."""
    tracker = ExitStateTracker()
    t0 = _t0()
    held, armed = tracker.observe([_pos()], as_of=t0, atr_ticks=None, cadence_seconds=1800.0)
    assert held[0].stop_distance_ticks == 0.0
    assert armed == [], "손절선을 못 잡았으면 무장했다고 말하지 않는다"

    held, armed = tracker.observe(
        [_pos()], as_of=t0 + timedelta(minutes=1), atr_ticks=_ATR, cadence_seconds=1800.0
    )

    assert held[0].stop_distance_ticks == _ATR
    assert [n.symbol for n in armed] == [_SYMBOL]


def test_cooldown_expires_on_the_clock():
    tracker = ExitStateTracker(cooldown_seconds=DEFAULT_RESUBMIT_COOLDOWN_SECONDS)
    t0 = _t0()
    tracker.observe([_pos()], as_of=t0, atr_ticks=_ATR, cadence_seconds=1800.0)
    tracker.mark_submitted(_SYMBOL, t0)

    assert tracker.cooling(t0 + timedelta(seconds=60))[_SYMBOL] is True
    assert tracker.cooling(t0 + timedelta(seconds=121))[_SYMBOL] is False


def test_cooldown_lifts_as_soon_as_the_position_shrinks():
    """체결이 잡히면 시간과 무관하게 즉시 풀린다 — 남은 수량을 쿨다운이 붙들면 안 된다."""
    tracker = ExitStateTracker()
    t0 = _t0()
    tracker.observe([_pos(qty=2)], as_of=t0, atr_ticks=_ATR, cadence_seconds=1800.0)
    tracker.mark_submitted(_SYMBOL, t0)

    t1 = t0 + timedelta(seconds=60)
    tracker.observe([_pos(qty=1)], as_of=t1, atr_ticks=_ATR, cadence_seconds=1800.0)

    assert tracker.cooling(t1)[_SYMBOL] is False
