from messiah.core.messages import Regime
from messiah.strategy.regime.rules import (
    VOL_EXTREME_THRESHOLD,
    RuleContext,
    apply_rules,
    rule_economic_event,
    rule_expiry_day,
    rule_volatility_extreme,
)

# ---------------------------------------------------------------- rule_volatility_extreme


def test_rule_volatility_extreme_triggers_above_threshold():
    context = RuleContext(vol_ratio=VOL_EXTREME_THRESHOLD + 0.01)
    assert rule_volatility_extreme(context) == Regime.HIGH_VOL


def test_rule_volatility_extreme_none_at_or_below_threshold():
    assert rule_volatility_extreme(RuleContext(vol_ratio=VOL_EXTREME_THRESHOLD)) is None
    assert rule_volatility_extreme(RuleContext(vol_ratio=0.5)) is None


def test_rule_volatility_extreme_none_when_data_missing():
    assert rule_volatility_extreme(RuleContext()) is None


# ---------------------------------------------------------------- rule_economic_event (stub)


def test_rule_economic_event_never_triggers_with_default_context():
    """Event Calendar 미구현 — 기본 RuleContext(전부 None)에서는 항상 통과(None)."""
    assert rule_economic_event(RuleContext()) is None


def test_rule_economic_event_logic_correct_if_data_were_available():
    """데이터 소스가 없을 뿐 로직 자체는 완성돼 있음을 확인 — Event Calendar가 생기면
    바로 쓸 수 있어야 한다."""
    assert rule_economic_event(RuleContext(econ_grade=2, econ_prox_days=1)) == Regime.EVENT
    assert rule_economic_event(RuleContext(econ_grade=3, econ_prox_days=0)) == Regime.EVENT
    assert rule_economic_event(RuleContext(econ_grade=1, econ_prox_days=0)) is None  # 등급 미달
    assert rule_economic_event(RuleContext(econ_grade=2, econ_prox_days=2)) is None  # 근접일 미달


# ---------------------------------------------------------------- rule_expiry_day (stub)


def test_rule_expiry_day_never_triggers_with_default_context():
    assert rule_expiry_day(RuleContext()) is None


def test_rule_expiry_day_logic_correct_if_data_were_available():
    assert rule_expiry_day(RuleContext(is_expiry_day=True)) == Regime.EVENT
    assert rule_expiry_day(RuleContext(is_expiry_day=False)) is None


# ---------------------------------------------------------------- apply_rules


def test_apply_rules_returns_none_when_nothing_matches():
    assert apply_rules(RuleContext()) is None


def test_apply_rules_volatility_rule_fires_when_only_active_rule_matches():
    result = apply_rules(RuleContext(vol_ratio=10.0))
    assert result is not None
    assert result.regime == Regime.HIGH_VOL
    assert result.reason == "rule_volatility_extreme"


def test_apply_rules_first_match_wins_over_later_matching_rules():
    # economic_event와 volatility_extreme이 동시에 맞아도 체인 순서상 economic_event가 이긴다.
    context = RuleContext(econ_grade=3, econ_prox_days=0, vol_ratio=100.0)
    result = apply_rules(context)
    assert result is not None
    assert result.regime == Regime.EVENT
    assert result.reason == "rule_economic_event"


def test_apply_rules_custom_chain_order_changes_winner():
    custom_chain = (rule_volatility_extreme, rule_economic_event)  # 순서 반대로
    context = RuleContext(econ_grade=3, econ_prox_days=0, vol_ratio=100.0)
    result = apply_rules(context, chain=custom_chain)
    assert result is not None
    assert result.reason == "rule_volatility_extreme"
