"""장중 청산 판정 (2026-09-22 F-119).

이 파일이 지키는 것 여섯:
  ① 손절선에 닿기 전에는 절대 안 쏜다 — 그리고 정확히 닿으면 쏜다
  ② LONG·SHORT가 **대칭**이다 — 부호 하나로 두 경우를 같이 쓰는 것이 옳은지 잰다
  ③ 우선순위는 손절 > 시간배리어 > 익절 (Holding Policy §4)
  ④ 시간배리어 숫자는 **라벨에서 온다** — 여기 복사돼 있지 않다
  ⑤ 모르는 것(ATR 워밍업·구동 Horizon 미상)으로는 청산하지 않는다
  ⑥ 방금 보낸 것을 또 보내지 않는다 — 중복 청산은 미청산보다 고치기 어렵다
  ⑦ 논지 소멸(2026-10-01)은 **뒤집힘**만 센다 — 약화·방향 무관한 국면 변화는 아니다
  ⑧ 트레일링(2026-10-02)은 **지킬 이익이 생긴 뒤의 되밀림**에만 걸린다
"""

from datetime import timedelta

import pytest

from messiah.broker.base import BrokerPosition
from messiah.core.messages import Horizon, Regime, Side
from messiah.core.timeutil import KST, now_utc
from messiah.models.labeling import BARRIER_PARAMS
from messiah.strategy.position_exit import (
    DEFAULT_RESUBMIT_COOLDOWN_SECONDS,
    ExitReason,
    ExitStateTracker,
    HeldFutures,
    HeldThesis,
    decide,
    decide_thesis,
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
    """모듈 기본값은 익절 판정 없음 — 2.4×ATR을 이기고 있어도 안 자른다. 운영값(2.0배·섀도)은
    `configs/holding_policy.yaml`이 넣는다."""
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


# --- ⑦ 논지 소멸 (2026-10-01, Holding Policy §4 ④) ---------------------------


def _thesis(qty: int = 1, entry_regime: Regime | None = Regime.TREND_UP) -> HeldThesis:
    return HeldThesis(position=_pos(qty), entry_regime=entry_regime, minutes_held=30.0)


def test_opposite_intent_is_a_reversal():
    """ⓐ 진입시킨 판단 엔진이 반대 방향을 냈다 — LONG 보유 중 SHORT."""
    plan = decide_thesis(held=[_thesis()], intent_side=Side.SHORT, current_regime=Regime.TREND_UP)

    assert [s.reason for s in plan.slices] == [ExitReason.THESIS_REVERSAL]
    assert plan.slices[0].qty == 1


def test_reversal_is_symmetric_for_shorts():
    plan = decide_thesis(
        held=[_thesis(qty=-2, entry_regime=Regime.TREND_DOWN)],
        intent_side=Side.LONG,
        current_regime=Regime.TREND_DOWN,
    )

    assert plan.slices[0].reason is ExitReason.THESIS_REVERSAL
    assert plan.slices[0].qty == 2, "전량이다 — 논지가 무너졌는데 절반만 남길 근거가 없다"


def test_weakening_is_not_a_reversal():
    """**핵심 회귀 — 2026-09-22.** 14:30 LONG(S=0.31) 뒤 15:00 S=0.19로 임계 미달(NO_TRADE),
    국면 판정은 RANGE. 그 포지션은 EOD +113틱으로 끝났다. 약해진 것과 뒤집힌 것은 다르다."""
    plan = decide_thesis(
        held=[_thesis(entry_regime=Regime.HIGH_VOL)],
        intent_side=Side.NO_TRADE,
        current_regime=Regime.RANGE,
    )

    assert plan.should_exit is False


def test_same_direction_intent_keeps_the_thesis():
    plan = decide_thesis(held=[_thesis()], intent_side=Side.LONG, current_regime=Regime.TREND_UP)

    assert plan.should_exit is False


def test_adverse_trend_after_entry_is_a_regime_exit():
    """ⓑ 진입 때 TREND_UP → 지금 TREND_DOWN. LONG의 국면 근거가 돌아섰다."""
    plan = decide_thesis(
        held=[_thesis(entry_regime=Regime.TREND_UP)],
        intent_side=Side.NO_TRADE,
        current_regime=Regime.TREND_DOWN,
    )

    assert plan.slices[0].reason is ExitReason.THESIS_REGIME
    assert plan.slices[0].detail["entry_regime"] == "TREND_UP"
    assert plan.slices[0].detail["current_regime"] == "TREND_DOWN"


def test_entering_against_the_trend_is_not_a_transition():
    """**핵심 회귀 — 2026-09-16.** TREND_DOWN 안에서 LONG 진입, 30분 뒤에도 TREND_DOWN.
    "전환"이 없었다 — 그 포지션은 +346틱(6.9×ATR)으로 끝났다."""
    plan = decide_thesis(
        held=[_thesis(entry_regime=Regime.TREND_DOWN)],
        intent_side=Side.LONG,
        current_regime=Regime.TREND_DOWN,
    )

    assert plan.should_exit is False


def test_direction_neutral_regime_change_is_not_an_exit():
    """HIGH_VOL·RANGE·EVENT·UNKNOWN은 방향을 말하지 않는다 — LONG의 논지를 무너뜨리지 않는다."""
    for regime in (Regime.RANGE, Regime.HIGH_VOL, Regime.EVENT, Regime.UNKNOWN):
        plan = decide_thesis(
            held=[_thesis(entry_regime=Regime.TREND_UP)],
            intent_side=Side.NO_TRADE,
            current_regime=regime,
        )
        assert plan.should_exit is False, regime


def test_unknown_entry_regime_disables_only_the_regime_rule():
    """기준이 없으면 "전환"을 말할 수 없다 — 그러나 반전(ⓐ)은 기준 없이도 성립한다."""
    regime_only = decide_thesis(
        held=[_thesis(entry_regime=None)],
        intent_side=Side.NO_TRADE,
        current_regime=Regime.TREND_DOWN,
    )
    reversal = decide_thesis(
        held=[_thesis(entry_regime=None)],
        intent_side=Side.SHORT,
        current_regime=Regime.TREND_DOWN,
    )

    assert regime_only.should_exit is False
    assert reversal.slices[0].reason is ExitReason.THESIS_REVERSAL


def test_reversal_wins_over_regime_when_both_fire():
    """둘 다 걸리면 더 강한 근거(최종 판단의 반전)를 사유로 남긴다."""
    plan = decide_thesis(
        held=[_thesis(entry_regime=Regime.TREND_UP)],
        intent_side=Side.SHORT,
        current_regime=Regime.TREND_DOWN,
    )

    assert [s.reason for s in plan.slices] == [ExitReason.THESIS_REVERSAL]


def test_thesis_respects_cooling():
    """⑥과 같은 규율 — 방금 낸 청산이 아직 안 잡혔으면 또 내지 않는다."""
    plan = decide_thesis(
        held=[_thesis()],
        intent_side=Side.SHORT,
        current_regime=Regime.TREND_UP,
        cooling={_SYMBOL: True},
    )

    assert plan.should_exit is False


def test_thesis_detail_reports_adverse_ticks_when_price_is_known():
    plan = decide_thesis(
        held=[_thesis()],
        intent_side=Side.SHORT,
        current_regime=Regime.TREND_UP,
        last_price_ticks=_ENTRY - 10,
    )

    assert plan.slices[0].detail["adverse_ticks"] == pytest.approx(10.0)
    assert plan.slices[0].detail["intent_side"] == "SHORT"


def test_tracker_remembers_the_regime_at_first_sight():
    """진입 국면 = 포지션을 **처음 본 순간**의 마지막 뷰 국면. 뒤에 국면이 바뀌어도 안 바뀐다."""
    tracker = ExitStateTracker()
    t0 = _t0()
    tracker.observe(
        [_pos()], as_of=t0, atr_ticks=_ATR, cadence_seconds=1800.0, regime=Regime.TREND_UP
    )
    tracker.observe(
        [_pos()],
        as_of=t0 + timedelta(minutes=5),
        atr_ticks=_ATR,
        cadence_seconds=1800.0,
        regime=Regime.TREND_DOWN,
    )

    [held] = tracker.thesis_inputs([_pos()], as_of=t0 + timedelta(minutes=30))

    assert held.entry_regime is Regime.TREND_UP
    assert held.minutes_held == pytest.approx(30.0)


def test_tracker_fills_the_regime_late_if_the_bar_beat_the_view():
    tracker = ExitStateTracker()
    t0 = _t0()
    tracker.observe([_pos()], as_of=t0, atr_ticks=_ATR, cadence_seconds=None, regime=None)
    tracker.observe(
        [_pos()],
        as_of=t0 + timedelta(minutes=1),
        atr_ticks=_ATR,
        cadence_seconds=None,
        regime=Regime.RANGE,
    )

    [held] = tracker.thesis_inputs([_pos()], as_of=t0 + timedelta(minutes=2))

    assert held.entry_regime is Regime.RANGE


def test_thesis_inputs_do_not_trust_a_flipped_position():
    """부호가 바뀐 포지션은 새 포지션이다 — 옛 진입 국면을 물려주지 않는다."""
    tracker = ExitStateTracker()
    t0 = _t0()
    tracker.observe(
        [_pos(1)], as_of=t0, atr_ticks=_ATR, cadence_seconds=1800.0, regime=Regime.TREND_UP
    )

    [held] = tracker.thesis_inputs([_pos(-1)], as_of=t0 + timedelta(minutes=1))

    assert held.entry_regime is None
    assert held.minutes_held is None


def test_shadow_is_logged_once_per_reason():
    tracker = ExitStateTracker()
    tracker.observe([_pos()], as_of=_t0(), atr_ticks=_ATR, cadence_seconds=1800.0)

    assert tracker.first_shadow(_SYMBOL, ExitReason.TAKE_PROFIT) is True
    assert tracker.first_shadow(_SYMBOL, ExitReason.TAKE_PROFIT) is False
    assert tracker.first_shadow(_SYMBOL, ExitReason.THESIS_REGIME) is True, "사유는 따로 센다"


# --- ⑧ 트레일링 스톱 (2026-10-02) ---------------------------------------------


def _trail(*, qty: int = 1, best: int | None, minutes_held: float = 10.0) -> HeldFutures:
    return HeldFutures(
        position=_pos(qty),
        entry_price_ticks=_ENTRY,
        stop_distance_ticks=_ATR,
        minutes_held=minutes_held,
        time_barrier_minutes=90.0,
        best_price_ticks=best,
    )


def _decide_trail(last: int, held: HeldFutures, **kw):
    return decide(last_price_ticks=last, held=[held], trailing_atr_mult=2.0, **kw)


def test_trailing_is_off_by_default():
    plan = decide(last_price_ticks=_ENTRY + 10, held=[_trail(best=_ENTRY + 300)])

    assert plan.should_exit is False


def test_trailing_fires_on_a_retrace_after_a_real_gain():
    """+4×ATR까지 갔다가 2×ATR 되밀림 → 나간다(이익 +2×ATR을 지킨다)."""
    plan = _decide_trail(_ENTRY + 100, _trail(best=_ENTRY + 200))

    [piece] = plan.slices
    assert piece.reason is ExitReason.TRAILING_STOP
    assert piece.detail["peak_gain_ticks"] == pytest.approx(200.0)
    assert piece.detail["retrace_ticks"] == pytest.approx(100.0)
    assert piece.detail["trail_ticks"] == pytest.approx(100.0)


def test_retrace_one_tick_short_of_the_trail_does_not_fire():
    plan = _decide_trail(_ENTRY + 101, _trail(best=_ENTRY + 200))

    assert plan.should_exit is False


def test_trailing_is_not_armed_before_the_activation_gain():
    """**핵심** — 진입 직후 잔물결에는 걸리지 않는다. 그 전엔 원래 손절이 지킨다.

    최고 이익 0.9×ATR(45틱)에서 되밀림 — 활성 문턱(1.0×ATR) 미달이라 트레일이 아니다.
    """
    plan = decide(
        last_price_ticks=_ENTRY + 45 - 30,
        held=[_trail(best=_ENTRY + 45)],
        trailing_atr_mult=0.5,
    )

    assert plan.should_exit is False


def test_activation_multiple_is_honoured():
    held = _trail(best=_ENTRY + 60)  # 최고 이익 1.2×ATR
    off = decide(
        last_price_ticks=_ENTRY + 10,
        held=[held],
        trailing_atr_mult=1.0,
        trailing_activation_atr_mult=1.5,
    )
    on = decide(
        last_price_ticks=_ENTRY + 10,
        held=[held],
        trailing_atr_mult=1.0,
        trailing_activation_atr_mult=1.0,
    )

    assert off.should_exit is False
    assert on.slices[0].reason is ExitReason.TRAILING_STOP


def test_trailing_is_symmetric_for_shorts():
    """SHORT의 최고점은 **최저가**다 — 내려갔다가 다시 오르면 나간다."""
    plan = _decide_trail(_ENTRY - 100, _trail(qty=-1, best=_ENTRY - 200))

    assert plan.slices[0].reason is ExitReason.TRAILING_STOP


def test_unknown_peak_never_trails():
    plan = _decide_trail(_ENTRY + 100, _trail(best=None))

    assert plan.should_exit is False


def test_stop_loss_wins_over_trailing():
    """되밀림이 진입가 아래 손절선까지 내려왔으면 사유는 손절이다(§4 ①)."""
    plan = _decide_trail(_ENTRY - 60, _trail(best=_ENTRY + 60))

    assert plan.slices[0].reason is ExitReason.STOP_LOSS


def test_trailing_wins_over_take_profit():
    """같은 봉에 둘 다 걸리면 되밀림(이미 내려오는 중)이 더 급하다."""
    plan = _decide_trail(_ENTRY + 150, _trail(best=_ENTRY + 300), take_profit_atr_mult=2.0)

    assert [s.reason for s in plan.slices] == [ExitReason.TRAILING_STOP]


def test_tracker_follows_the_best_close_for_longs():
    tracker = ExitStateTracker()
    t0 = _t0()
    for i, close in enumerate((_ENTRY + 20, _ENTRY + 90, _ENTRY + 40)):
        held, _ = tracker.observe(
            [_pos()],
            as_of=t0 + timedelta(minutes=i),
            atr_ticks=_ATR,
            cadence_seconds=1800.0,
            last_price_ticks=close,
        )

    assert held[0].best_price_ticks == _ENTRY + 90, "내려와도 최고점은 그대로다"


def test_tracker_follows_the_lowest_close_for_shorts():
    tracker = ExitStateTracker()
    t0 = _t0()
    for i, close in enumerate((_ENTRY - 20, _ENTRY - 90, _ENTRY - 40)):
        held, _ = tracker.observe(
            [_pos(-1)],
            as_of=t0 + timedelta(minutes=i),
            atr_ticks=_ATR,
            cadence_seconds=1800.0,
            last_price_ticks=close,
        )

    assert held[0].best_price_ticks == _ENTRY - 90


def test_tracker_peak_starts_at_entry_not_at_a_losing_close():
    """첫 종가가 손실 쪽이어도 최고점은 진입가다 — 손실 구간을 「고점」으로 세지 않는다."""
    tracker = ExitStateTracker()
    held, _ = tracker.observe(
        [_pos()], as_of=_t0(), atr_ticks=_ATR, cadence_seconds=1800.0, last_price_ticks=_ENTRY - 30
    )

    assert held[0].best_price_ticks == _ENTRY


def test_flipping_direction_resets_the_peak():
    """뒤집힌 포지션은 새 포지션이다 — LONG의 최고점을 SHORT가 물려받으면 즉시 트레일이 걸린다."""
    tracker = ExitStateTracker()
    t0 = _t0()
    tracker.observe(
        [_pos(1)], as_of=t0, atr_ticks=_ATR, cadence_seconds=1800.0, last_price_ticks=_ENTRY + 300
    )
    held, _ = tracker.observe(
        [_pos(-1)],
        as_of=t0 + timedelta(minutes=1),
        atr_ticks=_ATR,
        cadence_seconds=1800.0,
        last_price_ticks=_ENTRY,
    )

    assert held[0].best_price_ticks == _ENTRY
