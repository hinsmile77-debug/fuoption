"""Options AI 안전규칙 — Hard Rules (신규, Ver 2.0 §9 W30~31)."""

from __future__ import annotations

from decimal import Decimal

from messiah.core.messages import GreeksProfile, StrategyCandidate, StrategyLeg
from messiah.strategy.options.config import OptionsConfig
from messiah.strategy.options.safety import (
    SafetyVerdict,
    check_credit_iv_floor,
    check_event_window,
    check_expiry_day_entry,
    check_naked_short,
    evaluate_candidate_safety,
    exceeds_loss_limit,
    requires_forced_close_by_dte,
)

_CFG = OptionsConfig()
_ZERO_GREEKS = GreeksProfile(delta=0.0, gamma=0.0, theta=0.0, vega=0.0, iv=0.2)


def _candidate(
    structure: str,
    *,
    max_loss: Decimal | None = Decimal("10"),
    legs: list[StrategyLeg] | None = None,
) -> StrategyCandidate:
    default_legs = [
        StrategyLeg(option_type="C", strike=350.0, dte=20, is_short=False, delta=0.4),
        StrategyLeg(option_type="C", strike=360.0, dte=20, is_short=True, delta=0.2),
    ]
    return StrategyCandidate(
        structure=structure,
        legs=legs if legs is not None else default_legs,
        net_expected_return=Decimal("1.5"),
        pop=0.6,
        max_loss=max_loss,
        reward_risk=0.5,
        greeks=_ZERO_GREEKS,
    )


# ---------------------------------------------------------------- check_naked_short


def test_naked_short_rejected_when_max_loss_none():
    candidate = _candidate("BULL_PUT_SPREAD", max_loss=None)
    assert check_naked_short(candidate) is not None


def test_naked_short_passes_when_max_loss_defined():
    candidate = _candidate("BULL_PUT_SPREAD", max_loss=Decimal("5"))
    assert check_naked_short(candidate) is None


# ---------------------------------------------------------------- check_credit_iv_floor


def test_credit_iv_floor_rejects_credit_structure_below_floor():
    candidate = _candidate("BULL_PUT_SPREAD")
    assert check_credit_iv_floor(candidate, iv_rank=40.0, config=_CFG) is not None


def test_credit_iv_floor_passes_credit_structure_above_floor():
    candidate = _candidate("BULL_PUT_SPREAD")
    assert check_credit_iv_floor(candidate, iv_rank=60.0, config=_CFG) is None


def test_credit_iv_floor_ignores_debit_structure_regardless_of_iv_rank():
    candidate = _candidate("BULL_CALL_SPREAD")
    assert check_credit_iv_floor(candidate, iv_rank=10.0, config=_CFG) is None


# ---------------------------------------------------------------- check_event_window


def test_event_window_blocks_short_leg_candidate_when_true():
    candidate = _candidate("BULL_PUT_SPREAD")  # 매도 다리 있음(기본 legs 중 하나가 is_short=True)
    assert check_event_window(candidate, is_macro_event_window=True) is not None


def test_event_window_passes_when_unknown_none():
    candidate = _candidate("BULL_PUT_SPREAD")
    assert check_event_window(candidate, is_macro_event_window=None) is None


def test_event_window_passes_when_false():
    candidate = _candidate("BULL_PUT_SPREAD")
    assert check_event_window(candidate, is_macro_event_window=False) is None


def test_event_window_passes_pure_long_even_when_true():
    legs = [StrategyLeg(option_type="C", strike=350.0, dte=20, is_short=False, delta=0.4)]
    candidate = _candidate("LONG_CALL", legs=legs)
    assert check_event_window(candidate, is_macro_event_window=True) is None


# ---------------------------------------------------------------- check_expiry_day_entry


def test_expiry_day_entry_blocked():
    candidate = _candidate("BULL_CALL_SPREAD")
    assert check_expiry_day_entry(candidate, is_expiry_day=True) is not None


def test_expiry_day_entry_allowed_on_normal_day():
    candidate = _candidate("BULL_CALL_SPREAD")
    assert check_expiry_day_entry(candidate, is_expiry_day=False) is None


# ---------------------------------------------------------------- evaluate_candidate_safety


def test_evaluate_candidate_safety_aggregates_all_violations():
    candidate = _candidate("BULL_PUT_SPREAD", max_loss=None)
    verdict = evaluate_candidate_safety(
        candidate,
        iv_rank=10.0,
        config=_CFG,
        is_macro_event_window=True,
        is_expiry_day=True,
    )
    assert verdict.allowed is False
    assert len(verdict.violations) == 4  # 네이키드 + credit IV + 이벤트 + 만기일 전부 위반


def test_evaluate_candidate_safety_allows_clean_candidate():
    candidate = _candidate("BULL_CALL_SPREAD", max_loss=Decimal("5"))
    verdict = evaluate_candidate_safety(candidate, iv_rank=50.0, config=_CFG)
    assert verdict == SafetyVerdict(allowed=True, violations=[])


# ---------------------------------------------------------------- requires_forced_close_by_dte


def test_forced_close_short_at_or_below_threshold():
    assert requires_forced_close_by_dte(2, is_short=True) is True
    assert requires_forced_close_by_dte(0, is_short=True) is True


def test_forced_close_not_required_above_threshold_or_for_long():
    assert requires_forced_close_by_dte(3, is_short=True) is False
    assert requires_forced_close_by_dte(1, is_short=False) is False


# ---------------------------------------------------------------- exceeds_loss_limit


def test_exceeds_loss_limit_true_at_exactly_two_times_premium():
    # 진입 수취 10, 되사는 비용 30 → 손실 20 = 10×2 → 경계값 포함(>=)
    assert exceeds_loss_limit(entry_credit=10.0, current_value=30.0, multiple=2.0) is True


def test_exceeds_loss_limit_false_below_threshold():
    assert exceeds_loss_limit(entry_credit=10.0, current_value=25.0, multiple=2.0) is False


def test_exceeds_loss_limit_false_when_entry_credit_non_positive():
    assert exceeds_loss_limit(entry_credit=0.0, current_value=100.0) is False
    assert exceeds_loss_limit(entry_credit=-5.0, current_value=100.0) is False
