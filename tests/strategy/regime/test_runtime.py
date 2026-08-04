import math
from datetime import datetime, timedelta

import pytest

from messiah.core.messages import BarClosed, Horizon, Regime
from messiah.core.timeutil import KST
from messiah.simulator.inprocess_bus import InProcessBus
from messiah.strategy.regime.runtime import RegimeRuntime
from messiah.strategy.regime.service import RegimeAI

_SYMBOL = "TEST"
_START = datetime(2026, 7, 27, 9, 0, tzinfo=KST)


def _bars(n: int, *, symbol: str = _SYMBOL, horizon: Horizon = Horizon.M30) -> list[BarClosed]:
    out = []
    price = 100.0
    for i in range(n):
        price += math.sin(i / 4) * 2 + ((i * 53) % 7 - 3) * 0.2
        price = max(price, 10.0)
        out.append(
            BarClosed(
                symbol=symbol,
                horizon=horizon,
                bar_open_kst=_START + timedelta(minutes=30 * i),
                o_ticks=round(price),
                h_ticks=round(price) + 2,
                l_ticks=round(price) - 2,
                c_ticks=round(price),
                volume=10 + i,
            )
        )
    return out


@pytest.mark.asyncio
async def test_bar_updates_publish_regime_state():
    fit_bars = _bars(100)
    regime_ai = RegimeAI.fit(fit_bars, n_states_candidates=(2, 3, 4))

    bus = InProcessBus()
    published = []

    async def collector(msg):
        published.append(msg)

    await bus.subscribe(["intel.regime"], collector)

    runtime = RegimeRuntime(_SYMBOL, regime_ai, bus)
    for bar in fit_bars[:5]:
        await runtime.handle_bar(bar)

    assert len(published) == 5
    assert all(p.symbol == _SYMBOL for p in published)


@pytest.mark.asyncio
async def test_other_symbol_and_horizon_ignored():
    fit_bars = _bars(100)
    regime_ai = RegimeAI.fit(fit_bars, n_states_candidates=(2, 3))
    bus = InProcessBus()
    runtime = RegimeRuntime(_SYMBOL, regime_ai, bus)

    await runtime.handle_bar(_bars(1, symbol="OTHER")[0])
    await runtime.handle_bar(_bars(1, horizon=Horizon.M5)[0])
    assert len(runtime._history) == 0  # noqa: SLF001 — 테스트에서 내부 상태 직접 확인


@pytest.mark.asyncio
async def test_state_duration_accumulates_across_calls():
    fit_bars = _bars(100)
    regime_ai = RegimeAI.fit(fit_bars, n_states_candidates=(2, 3))
    bus = InProcessBus()
    published = []

    async def collector(msg):
        published.append(msg)

    await bus.subscribe(["intel.regime"], collector)
    runtime = RegimeRuntime(_SYMBOL, regime_ai, bus)
    for bar in fit_bars:
        await runtime.handle_bar(bar)

    # RegimeAI 자체가 상태 전이를 스스로 추적(state_duration_bars)한다는 걸 배선 경로로도 확인
    assert published[-1].state_duration_bars >= 1


@pytest.mark.asyncio
async def test_run_forever_subscribes_to_driving_horizon_topic():
    fit_bars = _bars(100)
    regime_ai = RegimeAI.fit(fit_bars, n_states_candidates=(2, 3))
    bus = InProcessBus()
    published = []

    async def collector(msg):
        published.append(msg)

    await bus.subscribe(["intel.regime"], collector)
    runtime = RegimeRuntime(_SYMBOL, regime_ai, bus)
    await runtime.run_forever()

    await bus.publish("bar.30m.TEST", fit_bars[0])
    assert len(published) == 1
    assert published[0].regime in Regime
