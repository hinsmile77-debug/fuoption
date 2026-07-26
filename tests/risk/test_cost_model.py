from datetime import datetime, timedelta

import pytest
from messiah.core.messages import BarClosed, Horizon
from messiah.core.timeutil import KST
from messiah.risk.cost_model import CostEstimate, CostModel, CostModelConfig

_DEFAULT = CostModelConfig()  # commission=0.3, tax=0.0, spread=1.0, impact_coefficient=2.0


def _bar(idx: int, volume: int) -> BarClosed:
    return BarClosed(
        symbol="A05608",
        horizon=Horizon.M1,
        bar_open_kst=datetime(2026, 7, 27, 9, 0, tzinfo=KST) + timedelta(minutes=idx),
        o_ticks=100,
        h_ticks=101,
        l_ticks=99,
        c_ticks=100,
        volume=volume,
    )


def test_estimate_one_way_known_values():
    model = CostModel()
    est = model.estimate(qty=2, avg_volume=100)

    assert est.commission_ticks == pytest.approx(0.6)  # 0.3 * 2
    assert est.tax_ticks == pytest.approx(0.0)
    assert est.slippage_ticks == pytest.approx(0.5)  # spread(1.0)/2, 수량 무관
    assert est.market_impact_ticks == pytest.approx(0.04)  # 2.0 * (2/100)
    assert est.total_ticks == pytest.approx(1.14)


def test_estimate_round_trip_is_exactly_double_one_way():
    model = CostModel()
    leg = model.estimate(qty=3, avg_volume=50)
    round_trip = model.estimate_round_trip(qty=3, avg_volume=50)

    assert round_trip.commission_ticks == pytest.approx(leg.commission_ticks * 2)
    assert round_trip.tax_ticks == pytest.approx(leg.tax_ticks * 2)
    assert round_trip.slippage_ticks == pytest.approx(leg.slippage_ticks * 2)
    assert round_trip.market_impact_ticks == pytest.approx(leg.market_impact_ticks * 2)
    assert round_trip.total_ticks == pytest.approx(leg.total_ticks * 2)


def test_market_impact_scales_with_qty_over_avg_volume():
    model = CostModel()
    small = model.estimate(qty=1, avg_volume=200)
    large = model.estimate(qty=10, avg_volume=200)
    assert large.market_impact_ticks == pytest.approx(small.market_impact_ticks * 10)


def test_market_impact_falls_back_to_coefficient_when_no_volume_data():
    model = CostModel()
    est = model.estimate(qty=5, avg_volume=0)
    assert est.market_impact_ticks == pytest.approx(_DEFAULT.impact_coefficient)


def test_qty_must_be_positive():
    model = CostModel()
    with pytest.raises(ValueError):
        model.estimate(qty=0, avg_volume=100)
    with pytest.raises(ValueError):
        model.estimate(qty=-1, avg_volume=100)


def test_estimate_round_trip_from_bars_averages_recent_volume_window():
    model = CostModel()
    bars = [_bar(0, volume=10), _bar(1, volume=20), _bar(2, volume=30)]  # 평균 20

    result = model.estimate_round_trip_from_bars(bars, qty=1, volume_window=3)
    expected = model.estimate_round_trip(qty=1, avg_volume=20)

    assert result == expected


def test_estimate_round_trip_from_bars_uses_only_last_n_when_window_smaller_than_history():
    model = CostModel()
    bars = [_bar(0, volume=1000), _bar(1, volume=10), _bar(2, volume=20)]  # window=2 → 평균 15

    result = model.estimate_round_trip_from_bars(bars, qty=1, volume_window=2)
    expected = model.estimate_round_trip(qty=1, avg_volume=15)

    assert result == expected


def test_estimate_round_trip_from_bars_empty_history_falls_back():
    model = CostModel()
    result = model.estimate_round_trip_from_bars([], qty=1, volume_window=20)
    expected = model.estimate_round_trip(qty=1, avg_volume=0)
    assert result == expected


def test_custom_config_overrides_defaults():
    model = CostModel(
        CostModelConfig(
            commission_ticks_per_contract=1.0,
            tax_ticks_per_contract=0.5,
            expected_spread_ticks=2.0,
            impact_coefficient=1.0,
        )
    )
    est = model.estimate(qty=1, avg_volume=10)
    assert est.commission_ticks == pytest.approx(1.0)
    assert est.tax_ticks == pytest.approx(0.5)
    assert est.slippage_ticks == pytest.approx(1.0)
    assert est.market_impact_ticks == pytest.approx(0.1)


def test_cost_estimate_addition():
    a = CostEstimate(commission_ticks=1, tax_ticks=2, slippage_ticks=3, market_impact_ticks=4)
    b = CostEstimate(
        commission_ticks=0.5, tax_ticks=0.5, slippage_ticks=0.5, market_impact_ticks=0.5
    )
    total = a + b
    assert total == CostEstimate(1.5, 2.5, 3.5, 4.5)
    assert total.total_ticks == pytest.approx(12.0)
