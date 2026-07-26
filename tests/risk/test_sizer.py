from decimal import Decimal

import pytest
from messiah.core.messages import DecisionIntent, OrderKind, Side
from messiah.risk.sizer import PositionSizer, SizerConfig

_SYMBOL = "TEST"


def _intent(*, side=Side.LONG, confidence=0.7, uncertainty=0.11) -> DecisionIntent:
    return DecisionIntent(symbol=_SYMBOL, side=side, confidence=confidence, uncertainty=uncertainty)


def test_zero_uncertainty_high_confidence_gives_positive_qty():
    sizer = PositionSizer()
    qty = sizer.size(
        intent=_intent(confidence=0.9, uncertainty=0.0),
        equity=Decimal("50000000"),
        tick_size=Decimal("0.02"),
        stop_distance_ticks=10.0,
    )
    assert qty > 0


def test_confidence_at_or_below_half_gives_zero_qty():
    sizer = PositionSizer()
    qty = sizer.size(
        intent=_intent(confidence=0.5),
        equity=Decimal("50000000"),
        tick_size=Decimal("0.02"),
        stop_distance_ticks=10.0,
    )
    assert qty == 0


def test_full_uncertainty_gives_zero_qty():
    sizer = PositionSizer()
    qty = sizer.size(
        intent=_intent(confidence=0.9, uncertainty=1.0),
        equity=Decimal("50000000"),
        tick_size=Decimal("0.02"),
        stop_distance_ticks=10.0,
    )
    assert qty == 0


def test_higher_confidence_gives_larger_or_equal_qty():
    sizer = PositionSizer()
    low = sizer.size(
        intent=_intent(confidence=0.6),
        equity=Decimal("50000000"),
        tick_size=Decimal("0.02"),
        stop_distance_ticks=10.0,
    )
    high = sizer.size(
        intent=_intent(confidence=0.95),
        equity=Decimal("50000000"),
        tick_size=Decimal("0.02"),
        stop_distance_ticks=10.0,
    )
    assert high >= low


def test_r1_caps_sizing_even_with_generous_vol_target():
    generous = SizerConfig(vol_target_pct=100.0, max_position_loss_pct=2.0, fractional_kelly=1.0)
    capped = SizerConfig(vol_target_pct=2.0, max_position_loss_pct=2.0, fractional_kelly=1.0)
    sizer_generous = PositionSizer(generous)
    sizer_capped = PositionSizer(capped)
    kwargs = dict(
        intent=_intent(confidence=1.0, uncertainty=0.0),
        equity=Decimal("50000000"),
        tick_size=Decimal("0.02"),
        stop_distance_ticks=10.0,
    )
    assert sizer_generous.size(**kwargs) == sizer_capped.size(**kwargs)


def test_zero_equity_gives_zero_qty():
    sizer = PositionSizer()
    qty = sizer.size(
        intent=_intent(),
        equity=Decimal("0"),
        tick_size=Decimal("0.02"),
        stop_distance_ticks=10.0,
    )
    assert qty == 0


def test_non_positive_stop_distance_raises():
    sizer = PositionSizer()
    with pytest.raises(ValueError):
        sizer.size(
            intent=_intent(),
            equity=Decimal("50000000"),
            tick_size=Decimal("0.02"),
            stop_distance_ticks=0.0,
        )


def test_build_order_request_maps_fields():
    sizer = PositionSizer()
    intent = _intent(side=Side.SHORT)
    order = sizer.build_order_request(intent=intent, qty=3, net_expected_return=Decimal("2.1"))
    assert order.intent_id == intent.msg_id
    assert order.symbol == _SYMBOL
    assert order.side == Side.SHORT
    assert order.qty == 3
    assert order.kind == OrderKind.ENTRY
    assert order.net_expected_return == Decimal("2.1")


def test_build_order_request_rejects_no_trade_intent():
    sizer = PositionSizer()
    with pytest.raises(ValueError):
        sizer.build_order_request(
            intent=_intent(side=Side.NO_TRADE), qty=1, net_expected_return=Decimal("1")
        )


def test_larger_stop_distance_reduces_qty():
    sizer = PositionSizer()
    kwargs = dict(
        intent=_intent(confidence=0.9), equity=Decimal("50000000"), tick_size=Decimal("0.02")
    )
    tight = sizer.size(stop_distance_ticks=5.0, **kwargs)
    wide = sizer.size(stop_distance_ticks=50.0, **kwargs)
    assert wide <= tight
