from datetime import datetime, timedelta

import numpy as np

from messiah.core.bus import TOPIC_SHADOW_FILL
from messiah.core.messages import BarClosed, ExpertView, FeatureVector, Horizon, Side
from messiah.core.timeutil import KST
from messiah.models.labeling import BARRIER_PARAMS
from messiah.models.shadow_manager import ShadowLedger, ShadowManager, evaluate_promotion
from messiah.risk.cost_model import CostModel
from messiah.simulator.inprocess_bus import InProcessBus
from messiah.strategy.futures.expert import HorizonExpert

_SYMBOL = "TEST"
_HORIZON = Horizon.M5
_START = datetime(2026, 7, 27, 9, 0, tzinfo=KST)


def _bar(idx: int, close: int) -> BarClosed:
    return BarClosed(
        symbol=_SYMBOL,
        horizon=_HORIZON,
        bar_open_kst=_START + timedelta(minutes=5 * idx),
        o_ticks=close,
        h_ticks=close + 3,
        l_ticks=close - 3,
        c_ticks=close,
        volume=100,
    )


def _view(p_up: float, p_down: float, *, meta_passed: bool = True) -> ExpertView:
    p_flat = max(0.0, 1.0 - p_up - p_down)
    return ExpertView(
        symbol=_SYMBOL,
        horizon=_HORIZON,
        p_down=p_down,
        p_flat=p_flat,
        p_up=p_up,
        ens_std=0.01,
        meta_passed=meta_passed,
        model_version="test",
        top_features=[],
        valid_until=_START,
    )


# ---------------------------------------------------------------- ShadowLedger


def test_on_prediction_ignores_weak_margin():
    ledger = ShadowLedger("bundle-1", _SYMBOL, _HORIZON)
    ledger.on_bar(_bar(0, 1000))
    ledger.on_prediction(_view(p_up=0.52, p_down=0.48), True)  # margin=0.04 < 기본 0.1
    assert ledger.fills == []


def test_on_prediction_ignores_when_meta_labeler_rejects():
    ledger = ShadowLedger("bundle-1", _SYMBOL, _HORIZON)
    ledger.on_bar(_bar(0, 1000))
    ledger.on_prediction(_view(p_up=0.9, p_down=0.05), False)
    assert ledger.fills == []


def test_on_prediction_opens_long_position_on_strong_up_signal():
    ledger = ShadowLedger("bundle-1", _SYMBOL, _HORIZON)
    ledger.on_bar(_bar(0, 1000))
    ledger.on_prediction(_view(p_up=0.9, p_down=0.05), True)
    # 시간배리어 경과 전까지는 아직 체결(청산) 없음.
    assert ledger.fills == []


def test_position_closes_after_time_barrier_bars_with_correct_side_and_prices():
    bars_needed = BARRIER_PARAMS[_HORIZON].time_barrier_bars
    ledger = ShadowLedger("bundle-1", _SYMBOL, _HORIZON, cost_model=CostModel())
    ledger.on_bar(_bar(0, 1000))
    ledger.on_prediction(_view(p_up=0.9, p_down=0.05), True)
    for i in range(1, bars_needed + 1):
        ledger.on_bar(_bar(i, 1000 + i))

    fills = ledger.fills
    assert len(fills) == 1
    fill = fills[0]
    assert fill.side == Side.LONG
    assert fill.entry_price_ticks == 1000
    assert fill.exit_price_ticks == 1000 + bars_needed
    raw_move = bars_needed
    cost = (
        CostModel()
        .estimate_round_trip_from_bars([_bar(i, 1000 + i) for i in range(bars_needed + 1)], qty=1)
        .total_ticks
    )
    assert fill.net_return_ticks == raw_move - cost


def test_no_pyramiding_while_position_open():
    ledger = ShadowLedger("bundle-1", _SYMBOL, _HORIZON)
    ledger.on_bar(_bar(0, 1000))
    ledger.on_prediction(_view(p_up=0.9, p_down=0.05), True)
    ledger.on_bar(_bar(1, 1010))
    ledger.on_prediction(_view(p_up=0.05, p_down=0.9), True)  # 반대 신호가 와도 무시
    bars_needed = BARRIER_PARAMS[_HORIZON].time_barrier_bars
    for i in range(2, bars_needed + 1):
        ledger.on_bar(_bar(i, 1010))
    assert len(ledger.fills) == 1
    assert ledger.fills[0].side == Side.LONG  # 반대 신호에 뒤집히지 않음


def test_drain_new_fills_returns_once_then_empty():
    bars_needed = BARRIER_PARAMS[_HORIZON].time_barrier_bars
    ledger = ShadowLedger("bundle-1", _SYMBOL, _HORIZON)
    ledger.on_bar(_bar(0, 1000))
    ledger.on_prediction(_view(p_up=0.9, p_down=0.05), True)
    for i in range(1, bars_needed + 1):
        ledger.on_bar(_bar(i, 1000 + i))

    drained = ledger.drain_new_fills()
    assert len(drained) == 1
    assert ledger.drain_new_fills() == []
    assert len(ledger.fills) == 1  # 전체 이력은 그대로 유지


# ---------------------------------------------------------------- ShadowManager wiring


def _tiny_expert() -> HorizonExpert:
    rows = [[i * 0.1, 0.0] for i in range(6)]
    labels = [-1, 0, 1, -1, 0, 1]
    return HorizonExpert.train(
        horizon=_HORIZON,
        feature_set="v-shadow-test",
        model_version="test",
        feature_names=["a", "b"],
        x=np.array(rows, dtype=float),
        y=np.array(labels, dtype=int),
        sample_weight=np.ones(6),
    )


def test_shadow_manager_add_remove_active_bundles():
    manager = ShadowManager(_SYMBOL, InProcessBus())
    manager.add_shadow_bundle("b1", _tiny_expert())
    assert manager.active_bundles == ["b1"]
    manager.remove_shadow_bundle("b1")
    assert manager.active_bundles == []


async def test_handle_feature_and_handle_bar_publish_fills_to_bus():
    bus = InProcessBus()
    published = []

    async def _collect(msg):
        published.append(msg)

    await bus.subscribe([TOPIC_SHADOW_FILL], _collect)

    manager = ShadowManager(_SYMBOL, bus)
    manager.add_shadow_bundle("b1", _tiny_expert())

    bars_needed = BARRIER_PARAMS[_HORIZON].time_barrier_bars
    await manager.handle_bar(_bar(0, 1000))
    fv = FeatureVector(
        symbol=_SYMBOL, horizon=_HORIZON, feature_set="v-shadow-test", values={"a": 5.0, "b": 0.0}
    )
    await manager.handle_feature(fv)
    for i in range(1, bars_needed + 1):
        await manager.handle_bar(_bar(i, 1000 + i * 5))

    # 예측력 없는 합성 데이터라 방향이 어느 쪽이든(margin 부족으로 진입 자체가 없을 수도)
    # 상관없다 — 핵심은 체결이 나면 반드시 버스에도 발행됐는지, 그리고 fills_for()와
    # 일치하는지다.
    assert manager.fills_for("b1") == published


# ---------------------------------------------------------------- evaluate_promotion


def test_evaluate_promotion_recommends_when_shadow_beats_champion_with_enough_days():
    proposal = evaluate_promotion(
        bundle_id="b1",
        horizon=_HORIZON,
        trading_days_observed=25,
        champion_returns=[0.001, -0.002, 0.0005, 0.001, -0.0003] * 4,
        shadow_returns=[0.01, 0.008, 0.012, 0.009, 0.011] * 4,
    )
    assert proposal.recommended is True
    assert proposal.shadow_sharpe > proposal.champion_sharpe


def test_evaluate_promotion_rejects_when_not_enough_trading_days():
    proposal = evaluate_promotion(
        bundle_id="b1",
        horizon=_HORIZON,
        trading_days_observed=5,
        champion_returns=[0.001, -0.001],
        shadow_returns=[0.01, 0.02],
    )
    assert proposal.recommended is False
    assert "최소 20" in proposal.rationale


def test_evaluate_promotion_rejects_when_shadow_underperforms():
    proposal = evaluate_promotion(
        bundle_id="b1",
        horizon=_HORIZON,
        trading_days_observed=25,
        champion_returns=[0.01, 0.012, 0.011, 0.009, 0.01] * 4,
        shadow_returns=[-0.001, 0.0005, -0.0008, 0.0002, -0.0003] * 4,
    )
    assert proposal.recommended is False
