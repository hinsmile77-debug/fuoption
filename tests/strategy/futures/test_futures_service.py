from datetime import datetime, timedelta

import numpy as np
import pytest
from messiah.core.messages import FeatureVector, Horizon, Regime, RegimeState
from messiah.core.timeutil import KST
from messiah.simulator.inprocess_bus import InProcessBus
from messiah.strategy.futures.expert import HorizonExpert
from messiah.strategy.futures.meta_labeler import META_FEATURE_NAMES, MetaLabeler
from messiah.strategy.futures.service import FuturesAIService

_SYMBOL = "TEST"
_FEATURE_SET = "v-test"
_FEATURE_NAMES = ["px_ret_5", "px_mom_5", "px_rsi_5"]
_NOW = datetime(2026, 7, 30, 10, 0, tzinfo=KST)


def _collector(sink: list) -> callable:
    async def _handler(msg):
        sink.append(msg)

    return _handler


def _train_expert(horizon: Horizon) -> HorizonExpert:
    rows, labels = [], []
    for label, base in ((-1, -5.0), (0, 0.0), (1, 5.0)):
        for i in range(5):
            rows.append([base + i * 0.01, base * 2, 50 + base])
            labels.append(label)
    x = np.array(rows, dtype=float)
    y = np.array(labels, dtype=int)
    weight = np.ones(len(labels), dtype=float)
    return HorizonExpert.train(
        horizon=horizon,
        feature_set=_FEATURE_SET,
        model_version="test-v1",
        feature_names=_FEATURE_NAMES,
        x=x,
        y=y,
        sample_weight=weight,
    )


def _train_meta(horizon: Horizon, *, always_pass: bool) -> MetaLabeler:
    n = 20
    x = np.random.default_rng(0).normal(size=(n, len(META_FEATURE_NAMES)))
    y = np.ones(n, dtype=int) if always_pass else np.zeros(n, dtype=int)
    return MetaLabeler.train(horizon=horizon, x=x, y=y, threshold=0.5 if always_pass else 1.1)


def _feature_vector(horizon: Horizon, *, valid_until_offset_min: float = 5.0) -> FeatureVector:
    from messiah.core.messages import HORIZON_SECONDS

    return FeatureVector(
        symbol=_SYMBOL,
        horizon=horizon,
        feature_set=_FEATURE_SET,
        values={"px_ret_5": 5.0, "px_mom_5": 10.0, "px_rsi_5": 55.0},
        valid_until=_NOW + timedelta(seconds=HORIZON_SECONDS[horizon]),
    )


@pytest.mark.asyncio
async def test_feature_vector_produces_futures_view_on_bus():
    bus = InProcessBus()
    published = []

    async def collector(msg):
        published.append(msg)

    await bus.subscribe(["intel.futures"], collector)

    service = FuturesAIService(_SYMBOL, {Horizon.M5: _train_expert(Horizon.M5)}, bus)
    await service.handle_feature(_feature_vector(Horizon.M5))

    assert len(published) == 1
    assert published[0].symbol == _SYMBOL
    assert published[0].n_experts == 1


@pytest.mark.asyncio
async def test_meta_labeler_rejection_excludes_from_aggregate_but_still_publishes():
    bus = InProcessBus()
    published = []
    await bus.subscribe(["intel.futures"], _collector(published))

    meta = _train_meta(Horizon.M5, always_pass=False)
    service = FuturesAIService(
        _SYMBOL,
        {Horizon.M5: _train_expert(Horizon.M5)},
        bus,
        meta_labelers={Horizon.M5: meta},
    )
    await service.handle_feature(_feature_vector(Horizon.M5))

    assert service.latest_views[Horizon.M5].meta_passed is False
    assert published[-1].n_experts == 0


@pytest.mark.asyncio
async def test_regime_update_changes_subsequent_aggregation():
    bus = InProcessBus()
    published = []
    await bus.subscribe(["intel.futures"], _collector(published))

    service = FuturesAIService(_SYMBOL, {Horizon.M30: _train_expert(Horizon.M30)}, bus)
    await service.handle_regime(
        RegimeState(symbol=_SYMBOL, regime=Regime.TREND_UP, confidence=1.0, state_duration_bars=1)
    )
    await service.handle_feature(_feature_vector(Horizon.M30))

    assert published[-1].regime == Regime.TREND_UP


@pytest.mark.asyncio
async def test_other_symbol_ignored():
    bus = InProcessBus()
    service = FuturesAIService(_SYMBOL, {Horizon.M5: _train_expert(Horizon.M5)}, bus)
    fv = _feature_vector(Horizon.M5).model_copy(update={"symbol": "OTHER"})
    await service.handle_feature(fv)
    assert service.latest_views == {}


@pytest.mark.asyncio
async def test_horizon_without_expert_ignored():
    bus = InProcessBus()
    service = FuturesAIService(_SYMBOL, {Horizon.M5: _train_expert(Horizon.M5)}, bus)
    await service.handle_feature(_feature_vector(Horizon.M30))
    assert Horizon.M30 not in service.latest_views


@pytest.mark.asyncio
async def test_run_forever_wires_subscriptions_end_to_end():
    bus = InProcessBus()
    published = []
    await bus.subscribe(["intel.futures"], _collector(published))

    service = FuturesAIService(_SYMBOL, {Horizon.M5: _train_expert(Horizon.M5)}, bus)
    await service.run_forever()

    await bus.publish("feat.5m.TEST", _feature_vector(Horizon.M5))
    assert len(published) == 1
