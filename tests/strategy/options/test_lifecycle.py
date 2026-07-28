"""Position Lifecycle Manager (신규, Ver 2.0 §9 W30~31)."""

from __future__ import annotations

from decimal import Decimal

from messiah.core.messages import GreeksProfile, StrategyCandidate, StrategyLeg
from messiah.strategy.options.lifecycle import (
    HeldPosition,
    LifecycleAction,
    LifecycleConfig,
    evaluate_position,
)

_GREEKS = GreeksProfile(delta=0.0, gamma=0.0, theta=0.0, vega=0.0, iv=0.2)
_CFG = LifecycleConfig()


def _credit_candidate(*, net_credit: float = 10.0) -> StrategyCandidate:
    # BULL_PUT_SPREAD 형태: [매도풋(등가격), 매수풋(날개)]
    legs = [
        StrategyLeg(option_type="P", strike=345.0, dte=20, is_short=True, delta=-0.30),
        StrategyLeg(option_type="P", strike=330.0, dte=20, is_short=False, delta=-0.15),
    ]
    return StrategyCandidate(
        structure="BULL_PUT_SPREAD",
        legs=legs,
        net_expected_return=Decimal("1.0"),
        pop=0.6,
        max_loss=Decimal("5"),
        reward_risk=2.0,
        greeks=_GREEKS,
        rationale={"net_credit_points": net_credit},
    )


def _long_call_candidate() -> StrategyCandidate:
    legs = [StrategyLeg(option_type="C", strike=350.0, dte=20, is_short=False, delta=0.40)]
    return StrategyCandidate(
        structure="LONG_CALL",
        legs=legs,
        net_expected_return=Decimal("1.0"),
        pop=0.5,
        max_loss=Decimal("8"),
        reward_risk=None,
        greeks=_GREEKS,
        rationale={"net_credit_points": -8.0},
    )


def _position(candidate, *, days_held=0, current_value=10.0, deltas=None, adjust_count=0):
    return HeldPosition(
        candidate=candidate,
        days_held=days_held,
        current_value=current_value,
        current_leg_deltas=deltas if deltas is not None else [leg.delta for leg in candidate.legs],
        adjust_count=adjust_count,
    )


# ---------------------------------------------------------------- STOP_LOSS


def test_stop_loss_triggered_at_two_times_premium_loss():
    candidate = _credit_candidate(net_credit=10.0)
    position = _position(candidate, current_value=30.0)  # 손실 20 = 10×2
    signal = evaluate_position(position, config=_CFG)
    assert signal.action == LifecycleAction.STOP_LOSS


def test_stop_loss_not_triggered_below_threshold():
    candidate = _credit_candidate(net_credit=10.0)
    position = _position(candidate, current_value=25.0)  # 손실 15 < 20
    signal = evaluate_position(position, config=_CFG)
    assert signal.action != LifecycleAction.STOP_LOSS


def test_stop_loss_takes_priority_over_expiry_force_close():
    candidate = _credit_candidate(net_credit=10.0)
    # DTE도 강제청산 대상(20-19=1<=2)이면서 손절 조건도 동시 충족 — 손절이 우선해야 함.
    position = _position(candidate, days_held=19, current_value=30.0)
    signal = evaluate_position(position, config=_CFG)
    assert signal.action == LifecycleAction.STOP_LOSS


# ---------------------------------------------------------------- EXPIRY_FORCE_CLOSE


def test_expiry_force_close_when_short_leg_dte_at_threshold():
    candidate = _credit_candidate(net_credit=10.0)
    position = _position(candidate, days_held=18, current_value=10.0)  # dte 남음=2, 손익 무관
    signal = evaluate_position(position, config=_CFG)
    assert signal.action == LifecycleAction.EXPIRY_FORCE_CLOSE


def test_no_expiry_force_close_above_threshold():
    candidate = _credit_candidate(net_credit=10.0)
    position = _position(candidate, days_held=10, current_value=10.0)  # dte 남음=10
    signal = evaluate_position(position, config=_CFG)
    assert signal.action != LifecycleAction.EXPIRY_FORCE_CLOSE


def test_expiry_force_close_not_triggered_for_long_only_position():
    candidate = _long_call_candidate()
    position = _position(candidate, days_held=19, current_value=1.0)  # dte 남음=1이지만 매수만
    signal = evaluate_position(position, config=_CFG)
    assert signal.action != LifecycleAction.EXPIRY_FORCE_CLOSE


# ---------------------------------------------------------------- PRE_EVENT_CLOSE


def test_pre_event_close_for_long_only_when_event_window_true():
    candidate = _long_call_candidate()
    position = _position(candidate, current_value=5.0)
    signal = evaluate_position(position, config=_CFG, is_macro_event_window=True)
    assert signal.action == LifecycleAction.PRE_EVENT_CLOSE


def test_pre_event_close_not_triggered_when_event_window_none_or_false():
    candidate = _long_call_candidate()
    position = _position(candidate, current_value=5.0)
    signal_none = evaluate_position(position, is_macro_event_window=None)
    signal_false = evaluate_position(position, is_macro_event_window=False)
    assert signal_none.action != LifecycleAction.PRE_EVENT_CLOSE
    assert signal_false.action != LifecycleAction.PRE_EVENT_CLOSE


def test_pre_event_close_not_triggered_when_short_leg_present():
    candidate = _credit_candidate(net_credit=10.0)
    position = _position(candidate, current_value=10.0)
    signal = evaluate_position(position, config=_CFG, is_macro_event_window=True)
    assert signal.action != LifecycleAction.PRE_EVENT_CLOSE


# ---------------------------------------------------------------- ADJUST


def test_adjust_triggered_when_short_leg_delta_doubles():
    candidate = _credit_candidate(net_credit=10.0)
    # 매도풋 진입델타 -0.30 → 2배(-0.60) 도달
    position = _position(candidate, current_value=10.0, deltas=[-0.60, -0.15])
    signal = evaluate_position(position, config=_CFG)
    assert signal.action == LifecycleAction.ADJUST


def test_adjust_not_triggered_when_adjust_count_exhausted():
    candidate = _credit_candidate(net_credit=10.0)
    position = _position(candidate, current_value=10.0, deltas=[-0.60, -0.15], adjust_count=1)
    signal = evaluate_position(position, config=_CFG)
    assert signal.action != LifecycleAction.ADJUST


# ---------------------------------------------------------------- TAKE_PROFIT


def test_take_profit_at_fifty_percent_of_credit_captured():
    candidate = _credit_candidate(net_credit=10.0)
    position = _position(candidate, current_value=5.0)  # 절반만 남음 = 50% 확보
    signal = evaluate_position(position, config=_CFG)
    assert signal.action == LifecycleAction.TAKE_PROFIT


def test_take_profit_not_triggered_below_fifty_percent_captured():
    candidate = _credit_candidate(net_credit=10.0)
    position = _position(candidate, current_value=8.0)  # 20%만 확보
    signal = evaluate_position(position, config=_CFG)
    assert signal.action != LifecycleAction.TAKE_PROFIT


def test_take_profit_not_applicable_to_debit_structure():
    candidate = _long_call_candidate()  # net_credit_points=-8.0 (차변)
    position = _position(candidate, current_value=0.5)
    signal = evaluate_position(position, config=_CFG)
    assert signal.action != LifecycleAction.TAKE_PROFIT


# ---------------------------------------------------------------- HOLD 기본값


def test_hold_when_nothing_triggers():
    candidate = _credit_candidate(net_credit=10.0)
    position = _position(candidate, current_value=9.0)  # 손실 없음, 이익실현 미달, 조정 미달
    signal = evaluate_position(position, config=_CFG)
    assert signal.action == LifecycleAction.HOLD


# ---------------------------------------------------------------- HeldPosition.min_dte_remaining


def test_min_dte_remaining_computed_from_shortest_leg_minus_days_held():
    candidate = _credit_candidate()
    position = _position(candidate, days_held=5)
    assert position.min_dte_remaining == 15  # min(20,20) - 5
