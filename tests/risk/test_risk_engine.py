from datetime import datetime, timedelta
from decimal import Decimal

from messiah.broker.base import BrokerAccount, BrokerPosition
from messiah.core.config import CapitalConfig
from messiah.core.messages import DecisionIntent, GreeksProfile, Side
from messiah.core.timeutil import KST
from messiah.risk.risk_engine import RiskEngine, RiskEngineConfig

_SYMBOL = "TEST"
_NOW = datetime(2026, 7, 30, 10, 0, tzinfo=KST)


def _intent(side: Side = Side.LONG) -> DecisionIntent:
    return DecisionIntent(symbol=_SYMBOL, side=side, confidence=0.7, uncertainty=0.1)


def _account(
    equity: Decimal = Decimal("50000000"), margin_used: Decimal = Decimal("0")
) -> BrokerAccount:
    return BrokerAccount(cash=equity, margin_used=margin_used, total_equity=equity)


def _default_kwargs(**overrides) -> dict:
    kwargs = dict(
        intent=_intent(),
        net_expected_return_ticks=2.0,
        account=_account(),
        positions=[],
        daily_start_equity=Decimal("50000000"),
        data_age_seconds=1.0,
        as_of=_NOW,
    )
    kwargs.update(overrides)
    return kwargs


def test_approves_clean_intent():
    engine = RiskEngine()
    decision = engine.evaluate(**_default_kwargs())
    assert decision.approved is True


def test_no_trade_intent_is_not_approved():
    engine = RiskEngine()
    decision = engine.evaluate(**_default_kwargs(intent=_intent(Side.NO_TRADE)))
    assert decision.approved is False


def test_rejects_non_positive_net_expected_return():
    engine = RiskEngine()
    decision = engine.evaluate(**_default_kwargs(net_expected_return_ticks=-0.5))
    assert decision.approved is False
    assert "Net ER" in decision.reason


def test_rejects_stale_data_r11():
    engine = RiskEngine()
    decision = engine.evaluate(**_default_kwargs(data_age_seconds=45.0))
    assert decision.approved is False
    assert "R11" in decision.reason


def test_rejects_when_circuit_breaker_active_r13():
    engine = RiskEngine()
    decision = engine.evaluate(**_default_kwargs(circuit_breaker_active=True))
    assert decision.approved is False
    assert "R13" in decision.reason


def test_rejects_after_consecutive_loss_streak_r10():
    engine = RiskEngine(RiskEngineConfig(consecutive_loss_limit=3))
    for _ in range(3):
        engine.record_trade_result(Decimal("-1000"))
    decision = engine.evaluate(**_default_kwargs())
    assert decision.approved is False
    assert "R10" in decision.reason


def test_winning_trade_resets_loss_streak():
    engine = RiskEngine(RiskEngineConfig(consecutive_loss_limit=3))
    engine.record_trade_result(Decimal("-1000"))
    engine.record_trade_result(Decimal("-1000"))
    engine.record_trade_result(Decimal("500"))
    decision = engine.evaluate(**_default_kwargs())
    assert decision.approved is True
    assert engine.consecutive_losses == 0


def test_rejects_order_error_rate_r12():
    engine = RiskEngine(RiskEngineConfig(order_error_limit=3, order_error_window_seconds=300))
    for i in range(3):
        engine.record_order_error(_NOW - timedelta(seconds=60 * i))
    decision = engine.evaluate(**_default_kwargs())
    assert decision.approved is False
    assert "R12" in decision.reason


def test_order_errors_outside_window_do_not_count_r12():
    engine = RiskEngine(RiskEngineConfig(order_error_limit=3, order_error_window_seconds=300))
    for i in range(3):
        engine.record_order_error(_NOW - timedelta(seconds=1000 + i))
    decision = engine.evaluate(**_default_kwargs())
    assert decision.approved is True


def test_rejects_daily_loss_limit_r2():
    engine = RiskEngine(RiskEngineConfig(capital=CapitalConfig(daily_loss_limit_pct=2.0)))
    decision = engine.evaluate(
        **_default_kwargs(
            account=_account(equity=Decimal("48900000")),
            daily_start_equity=Decimal("50000000"),
        )
    )
    assert decision.approved is False
    assert "R2" in decision.reason


def test_rejects_margin_cap_r3():
    engine = RiskEngine(RiskEngineConfig(capital=CapitalConfig(margin_cap_pct=40.0)))
    decision = engine.evaluate(**_default_kwargs(account=_account(margin_used=Decimal("21000000"))))
    assert decision.approved is False
    assert "R3" in decision.reason


def test_rejects_new_symbol_when_position_count_would_exceed_r5():
    engine = RiskEngine(RiskEngineConfig(capital=CapitalConfig(max_overnight_positions=2)))
    positions = [
        BrokerPosition(symbol="A", qty=1, avg_price_ticks=100),
        BrokerPosition(symbol="B", qty=1, avg_price_ticks=100),
    ]
    decision = engine.evaluate(**_default_kwargs(positions=positions))
    assert decision.approved is False
    assert "R5" in decision.reason


def test_adding_to_existing_symbol_does_not_trip_r5():
    engine = RiskEngine(RiskEngineConfig(capital=CapitalConfig(max_overnight_positions=2)))
    positions = [
        BrokerPosition(symbol=_SYMBOL, qty=1, avg_price_ticks=100),
        BrokerPosition(symbol="B", qty=1, avg_price_ticks=100),
    ]
    decision = engine.evaluate(**_default_kwargs(positions=positions))
    assert decision.approved is True


def test_reset_daily_clears_streak_and_errors():
    engine = RiskEngine(RiskEngineConfig(consecutive_loss_limit=1))
    engine.record_trade_result(Decimal("-1000"))
    engine.reset_daily()
    decision = engine.evaluate(**_default_kwargs())
    assert decision.approved is True


def test_minutes_to_close_none_skips_overnight_gates_r4_r6():
    # 호출자가 세션 정보를 안 넘기면(기존 동작) R4/R6 둘 다 조용히 건너뛴다 — 회귀 없음.
    engine = RiskEngine(RiskEngineConfig(overnight_flatten_lead_minutes=60.0))
    decision = engine.evaluate(**_default_kwargs(minutes_to_close=None))
    assert decision.approved is True


def test_rejects_new_entry_near_close_r6():
    engine = RiskEngine(RiskEngineConfig(overnight_flatten_lead_minutes=10.0))
    decision = engine.evaluate(**_default_kwargs(minutes_to_close=5.0))
    assert decision.approved is False
    assert "R6" in decision.reason


def test_r6_boundary_is_inclusive():
    engine = RiskEngine(RiskEngineConfig(overnight_flatten_lead_minutes=10.0))
    decision = engine.evaluate(**_default_kwargs(minutes_to_close=10.0))
    assert decision.approved is False
    assert "R6" in decision.reason


def test_approves_new_entry_well_before_close():
    engine = RiskEngine(RiskEngineConfig(overnight_flatten_lead_minutes=10.0))
    decision = engine.evaluate(**_default_kwargs(minutes_to_close=120.0))
    assert decision.approved is True


def test_rejects_overnight_margin_window_r4_at_stricter_cap():
    # 40%(R3 평시 한도)는 통과하지만 25%(R4 오버나이트 한도)는 초과하는 증거금 사용률 —
    # margin_used=15,000,000/50,000,000=30% (>25%, <40%).
    engine = RiskEngine(
        RiskEngineConfig(
            overnight_flatten_lead_minutes=10.0,
            overnight_margin_window_minutes=30.0,
            capital=CapitalConfig(margin_cap_pct=40.0, overnight_margin_cap_pct=25.0),
        )
    )
    decision = engine.evaluate(
        **_default_kwargs(
            account=_account(margin_used=Decimal("15000000")),
            minutes_to_close=20.0,  # R6(10분) 밖, R4(30분) 안
        )
    )
    assert decision.approved is False
    assert "R4" in decision.reason


def test_same_margin_usage_passes_outside_overnight_window():
    # 위와 같은 30% 증거금 사용률이지만 마감까지 여유가 있으면(R4 구간 밖) 평시 40%
    # 한도만 적용돼 통과한다.
    engine = RiskEngine(
        RiskEngineConfig(
            overnight_margin_window_minutes=30.0,
            capital=CapitalConfig(margin_cap_pct=40.0, overnight_margin_cap_pct=25.0),
        )
    )
    decision = engine.evaluate(
        **_default_kwargs(
            account=_account(margin_used=Decimal("15000000")),
            minutes_to_close=120.0,
        )
    )
    assert decision.approved is True


# ---------------------------------------------------------------- R7/R8 (options portfolio)

_OPTION_EQUITY = Decimal("50000000")


def _greeks(delta: float = 0.0, vega: float = 0.0) -> GreeksProfile:
    return GreeksProfile(delta=delta, gamma=0.0, theta=0.0, vega=vega, iv=0.2)


def _option_position(symbol: str = "A", *, delta: float = 0.0, vega: float = 0.0) -> BrokerPosition:
    return BrokerPosition(symbol=symbol, qty=1, avg_price_ticks=100, greeks=_greeks(delta, vega))


def test_r7_approves_net_delta_within_limit():
    engine = RiskEngine(RiskEngineConfig(net_delta_limit_contracts=20.0))
    positions = [_option_position(delta=15.0)]
    decision = engine.evaluate_options_portfolio(option_positions=positions, equity=_OPTION_EQUITY)
    assert decision.approved is True


def test_r7_rejects_net_delta_beyond_limit():
    engine = RiskEngine(RiskEngineConfig(net_delta_limit_contracts=20.0))
    positions = [_option_position(delta=25.0)]
    decision = engine.evaluate_options_portfolio(option_positions=positions, equity=_OPTION_EQUITY)
    assert decision.approved is False
    assert "R7" in decision.reason


def test_r7_boundary_is_approved_not_rejected():
    engine = RiskEngine(RiskEngineConfig(net_delta_limit_contracts=20.0))
    positions = [_option_position(delta=20.0)]
    decision = engine.evaluate_options_portfolio(option_positions=positions, equity=_OPTION_EQUITY)
    assert decision.approved is True


def test_r7_ignores_positions_without_greeks():
    engine = RiskEngine(RiskEngineConfig(net_delta_limit_contracts=20.0))
    positions = [BrokerPosition(symbol="FUT", qty=100, avg_price_ticks=100)]  # 선물 — greeks 없음
    decision = engine.evaluate_options_portfolio(option_positions=positions, equity=_OPTION_EQUITY)
    assert decision.approved is True


def test_r7_includes_prospective_greeks_in_net_delta():
    engine = RiskEngine(RiskEngineConfig(net_delta_limit_contracts=20.0))
    positions = [_option_position(delta=15.0)]
    decision = engine.evaluate_options_portfolio(
        option_positions=positions, equity=_OPTION_EQUITY, prospective_greeks=_greeks(delta=10.0)
    )
    assert decision.approved is False  # 15+10=25 > 20
    assert "R7" in decision.reason


def test_r8_rejects_net_vega_beyond_equity_pct():
    engine = RiskEngine(
        RiskEngineConfig(net_vega_limit_pct_of_equity=0.5, option_point_value_krw=Decimal("50000"))
    )
    positions = [_option_position(vega=100.0)]
    # vega_krw = 100 × 50000 = 5,000,000 ; equity=50,000,000 → 10% ≫ 0.5%
    decision = engine.evaluate_options_portfolio(option_positions=positions, equity=_OPTION_EQUITY)
    assert decision.approved is False
    assert "R8" in decision.reason


def test_r8_approves_net_vega_within_equity_pct():
    engine = RiskEngine(
        RiskEngineConfig(net_vega_limit_pct_of_equity=0.5, option_point_value_krw=Decimal("50000"))
    )
    positions = [_option_position(vega=1.0)]
    # vega_krw = 1 × 50000 = 50,000 ; equity=50,000,000 → 0.1% < 0.5%
    decision = engine.evaluate_options_portfolio(option_positions=positions, equity=_OPTION_EQUITY)
    assert decision.approved is True


def test_r8_skipped_when_equity_zero():
    engine = RiskEngine(RiskEngineConfig(net_vega_limit_pct_of_equity=0.5))
    positions = [_option_position(vega=1000.0)]
    decision = engine.evaluate_options_portfolio(option_positions=positions, equity=Decimal("0"))
    assert decision.approved is True


# ---------------------------------------------------------------- R9 (forced liquidation)


def test_r9_flags_short_option_position_at_two_times_loss():
    engine = RiskEngine(RiskEngineConfig(short_option_loss_multiple=2.0))
    positions = [BrokerPosition(symbol="A", qty=-1, avg_price_ticks=10)]  # 수취 프리미엄 10틱
    flagged = engine.positions_requiring_forced_liquidation(positions, {"A": 30.0})  # 손실 20=10×2
    assert flagged == positions


def test_r9_does_not_flag_short_position_below_threshold():
    engine = RiskEngine(RiskEngineConfig(short_option_loss_multiple=2.0))
    positions = [BrokerPosition(symbol="A", qty=-1, avg_price_ticks=10)]
    flagged = engine.positions_requiring_forced_liquidation(positions, {"A": 25.0})  # 손실 15<20
    assert flagged == []


def test_r9_ignores_long_positions_even_with_large_loss():
    engine = RiskEngine(RiskEngineConfig(short_option_loss_multiple=2.0))
    positions = [BrokerPosition(symbol="A", qty=1, avg_price_ticks=10)]  # 매수 — R9 대상 아님
    flagged = engine.positions_requiring_forced_liquidation(positions, {"A": 100.0})
    assert flagged == []


def test_r9_skips_positions_missing_current_value():
    engine = RiskEngine(RiskEngineConfig(short_option_loss_multiple=2.0))
    positions = [BrokerPosition(symbol="A", qty=-1, avg_price_ticks=10)]
    flagged = engine.positions_requiring_forced_liquidation(positions, {})
    assert flagged == []
