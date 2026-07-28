"""Delta Hedging 정책 (신규, Ver 2.0 §9 W30~31)."""

from __future__ import annotations

from messiah.strategy.options.hedging import (
    HedgingConfig,
    compute_hedge_qty,
    effective_delta_band,
    is_hedge_eligible,
)

_CFG = HedgingConfig(delta_band=5.0, gamma_shrink_dte_threshold=5, gamma_shrink_factor=0.5)


def test_is_hedge_eligible_only_direction_neutral_structures():
    assert is_hedge_eligible("IRON_CONDOR") is True
    assert is_hedge_eligible("CALENDAR") is True
    assert is_hedge_eligible("LONG_CALL") is False
    assert is_hedge_eligible("BULL_CALL_SPREAD") is False


def test_effective_delta_band_shrinks_near_expiry():
    assert effective_delta_band(20, _CFG) == 5.0
    assert effective_delta_band(5, _CFG) == 2.5  # 임계값 포함(<=)
    assert effective_delta_band(6, _CFG) == 5.0  # 임계값 초과는 아직 축소 안 됨


def test_compute_hedge_qty_none_inside_band():
    assert compute_hedge_qty(3.0, dte=20, config=_CFG) is None
    assert compute_hedge_qty(-5.0, dte=20, config=_CFG) is None  # 경계값 포함


def test_compute_hedge_qty_sells_futures_to_offset_positive_net_delta():
    qty = compute_hedge_qty(8.0, dte=20, config=_CFG)
    assert qty == -8  # 순델타 양수 → 선물 매도(음수)로 중화


def test_compute_hedge_qty_buys_futures_to_offset_negative_net_delta():
    qty = compute_hedge_qty(-7.0, dte=20, config=_CFG)
    assert qty == 7  # 순델타 음수 → 선물 매수(양수)로 중화


def test_compute_hedge_qty_uses_shrunk_band_near_expiry():
    # dte=5 → 밴드 2.5. net_delta=3.0은 원래 밴드(5.0) 안이지만 축소된 밴드 밖.
    assert compute_hedge_qty(3.0, dte=20, config=_CFG) is None
    assert compute_hedge_qty(3.0, dte=5, config=_CFG) == -3


def test_compute_hedge_qty_rounds_to_nearest_contract():
    qty = compute_hedge_qty(8.6, dte=20, config=_CFG)
    assert qty == -9  # round(8.6) = 9
