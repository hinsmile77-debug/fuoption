from decimal import Decimal

import pytest

from messiah.core.messages import DecisionIntent, OrderKind, Side
from messiah.risk.sizer import PositionSizer, SizerConfig

_SYMBOL = "TEST"


def _intent(*, side=Side.LONG, confidence=0.7, uncertainty=0.11) -> DecisionIntent:
    return DecisionIntent(symbol=_SYMBOL, side=side, confidence=confidence, uncertainty=uncertainty)


# `edge`는 사이저가 계산하지 않는다(F-22 · 2026-08-24). 종전 테스트는 `confidence`로
# edge를 **유도**했다 — 그 유도가 곧 사이저 안의 사본이었다. 이제 전부 명시해서 넘긴다.
def _edge(p_favorable: float, p_adverse: float = 0.0) -> float:
    """정본과 같은 산식 — `strategy/pipeline._directional_edge()`."""
    return p_favorable - p_adverse


def test_zero_uncertainty_high_confidence_gives_positive_qty():
    sizer = PositionSizer()
    qty = sizer.size(
        intent=_intent(confidence=0.9, uncertainty=0.0),
        equity=Decimal("50000000"),
        tick_size=Decimal("0.02"),
        stop_distance_ticks=10.0,
        edge=_edge(0.9, 0.05),
        edge_source="directional",
    )
    assert qty > 0


def test_zero_edge_gives_zero_qty():
    """우위가 0이면 계약수도 0 — 판정하는 값이 `confidence`가 아니라 `edge`다(F-22)."""
    sizer = PositionSizer()
    qty = sizer.size(
        intent=_intent(confidence=0.5),
        equity=Decimal("50000000"),
        tick_size=Decimal("0.02"),
        stop_distance_ticks=10.0,
        edge=0.0,
        edge_source="directional",
    )
    assert qty == 0


def test_negative_edge_is_clamped_to_zero():
    sizer = PositionSizer()
    qty = sizer.size(
        intent=_intent(confidence=0.5),
        equity=Decimal("50000000"),
        tick_size=Decimal("0.02"),
        stop_distance_ticks=10.0,
        edge=_edge(0.20, 0.55),
        edge_source="directional",
    )
    assert qty == 0


def test_high_confidence_with_low_edge_still_gives_zero():
    """사이저가 `confidence`로 우위를 되유도하지 않는다는 회귀 방지 —
    종전 산식이면 `2×0.9−1 = 0.8`로 큰 값이 나왔다."""
    sizer = PositionSizer()
    qty = sizer.size(
        intent=_intent(confidence=0.9, uncertainty=0.0),
        equity=Decimal("50000000"),
        tick_size=Decimal("0.02"),
        stop_distance_ticks=10.0,
        edge=0.0,
        edge_source="directional",
    )
    assert qty == 0


def test_full_uncertainty_gives_zero_qty():
    sizer = PositionSizer()
    qty = sizer.size(
        intent=_intent(confidence=0.9, uncertainty=1.0),
        equity=Decimal("50000000"),
        tick_size=Decimal("0.02"),
        stop_distance_ticks=10.0,
        edge=_edge(0.9, 0.05),
        edge_source="directional",
    )
    assert qty == 0


def test_higher_edge_gives_larger_or_equal_qty():
    sizer = PositionSizer()
    low = sizer.size(
        intent=_intent(confidence=0.6),
        equity=Decimal("50000000"),
        tick_size=Decimal("0.02"),
        stop_distance_ticks=10.0,
        edge=_edge(0.40, 0.30),
        edge_source="directional",
    )
    high = sizer.size(
        intent=_intent(confidence=0.95),
        equity=Decimal("50000000"),
        tick_size=Decimal("0.02"),
        stop_distance_ticks=10.0,
        edge=_edge(0.60, 0.10),
        edge_source="directional",
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
        edge=1.0,
        edge_source="directional",
    )
    assert sizer_generous.size(**kwargs) == sizer_capped.size(**kwargs)


def test_zero_equity_gives_zero_qty():
    sizer = PositionSizer()
    qty = sizer.size(
        intent=_intent(),
        equity=Decimal("0"),
        tick_size=Decimal("0.02"),
        stop_distance_ticks=10.0,
        edge=_edge(0.70, 0.20),
        edge_source="directional",
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
            edge=_edge(0.70, 0.20),
            edge_source="directional",
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
        intent=_intent(confidence=0.9),
        equity=Decimal("50000000"),
        tick_size=Decimal("0.02"),
        edge=_edge(0.90, 0.05),
        edge_source="directional",
    )
    tight = sizer.size(stop_distance_ticks=5.0, **kwargs)
    wide = sizer.size(stop_distance_ticks=50.0, **kwargs)
    assert wide <= tight


def test_size_requires_explicit_edge():
    """F-22 — 기본값이 있으면 안 넘긴 호출부가 조용히 옛 동작을 한다.

    사본을 지우는 것이 이 Fix의 본체이고, 그 사본이 되살아나는 유일한 경로가
    「기본값」이다. 인자 누락은 호출 시점에 즉시 실패해야 한다.
    """
    sizer = PositionSizer()
    with pytest.raises(TypeError):
        sizer.size(
            intent=_intent(confidence=0.9),
            equity=Decimal("50000000"),
            tick_size=Decimal("0.02"),
            stop_distance_ticks=10.0,
        )
