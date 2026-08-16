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
async def test_cold_start_stays_unknown_until_minimum_bars():
    """현행 동작 보존 — 웜스타트 없이는 하한 전까지 UNKNOWN이다 (2026-08-12 F-1).

    이게 무해한 워밍업으로 보였던 것이 문제의 핵심이었다: 하루가 만드는 30m 봉(15개)이
    하한(22)보다 적어 **실제 운영에서는 이 구간이 하루 종일**이었다.
    """
    fit_bars = _bars(120)
    regime_ai = RegimeAI.fit(fit_bars, n_states_candidates=(2, 3))
    bus = InProcessBus()
    published = []

    async def collector(msg):
        published.append(msg)

    await bus.subscribe(["intel.regime"], collector)
    runtime = RegimeRuntime(_SYMBOL, regime_ai, bus)

    minimum = regime_ai.min_bars_for_classify
    for bar in fit_bars[: minimum - 1]:
        await runtime.handle_bar(bar)

    assert len(published) == minimum - 1
    assert all(
        p.regime is Regime.UNKNOWN for p in published
    ), "하한 미만에서는 UNKNOWN이어야 한다 — 이 동작 자체는 설계대로다"


@pytest.mark.asyncio
async def test_warm_start_classifies_from_the_very_first_bar():
    """웜스타트를 채우면 **첫 봉부터** 판정이 난다 — F-1이 겨냥한 그 차이다."""
    fit_bars = _bars(120)
    regime_ai = RegimeAI.fit(fit_bars, n_states_candidates=(2, 3))
    bus = InProcessBus()
    published = []

    async def collector(msg):
        published.append(msg)

    await bus.subscribe(["intel.regime"], collector)
    runtime = RegimeRuntime(_SYMBOL, regime_ai, bus)

    history = _bars(200)[:100]
    loaded = runtime.warm_start(history)
    assert loaded == 100
    assert not published, "웜스타트는 발행하지 않는다 — 채우기만 한다"

    await runtime.handle_bar(fit_bars[0])

    assert len(published) == 1
    assert (
        published[0].regime is not Regime.UNKNOWN
    ), "충전 후 첫 봉이 UNKNOWN이면 웜스타트가 안 먹은 것이다"


def test_warm_start_filters_and_sorts_like_feature_engine():
    """심볼/Horizon이 안 맞는 봉은 버리고, 시간순이 아니어도 정렬한다."""
    fit_bars = _bars(100)
    regime_ai = RegimeAI.fit(fit_bars, n_states_candidates=(2, 3))
    runtime = RegimeRuntime(_SYMBOL, regime_ai, InProcessBus())

    mixed = [
        *_bars(3, symbol="OTHER"),
        *_bars(2, horizon=Horizon.M5),
        *reversed(_bars(5)),
    ]
    assert runtime.warm_start(mixed) == 5

    bars = runtime._bars()  # noqa: SLF001 — 정렬 결과를 직접 확인
    assert [b.bar_open_kst for b in bars] == sorted(b.bar_open_kst for b in bars)


def test_warm_start_accepts_the_roll_chain_when_told():
    """롤 경계에서 **선행 월물 봉이 적재돼야 한다** (2026-08-16 P0 회귀).

    로더(`load_recent_bars_by_source`)는 이어 읽은 봉의 심볼을 바꾸지 않는데 이 필터가
    자기 심볼만 받아 전량 버렸다. 2026-08-16 리허설 실측: 로더 200봉(A05609 15 ·
    A05608 185) → 적재 15봉 < 하한 22봉. F-1이 고쳤다고 판정한 상태 그대로였다.
    """
    fit_bars = _bars(100)
    regime_ai = RegimeAI.fit(fit_bars, n_states_candidates=(2, 3))
    runtime = RegimeRuntime(_SYMBOL, regime_ai, InProcessBus())

    mixed = [*_bars(7, symbol="PRECEDING"), *_bars(5)]

    assert runtime.warm_start(mixed, accept_symbols=[_SYMBOL, "PRECEDING"]) == 12
    assert runtime.warm_start(mixed) == 5, "체인을 안 주면 기존 동작 그대로"


def test_warm_start_still_drops_symbols_outside_the_chain():
    """체인을 줘도 **체인 밖 심볼**은 여전히 버린다 — 필터를 없앤 게 아니다."""
    fit_bars = _bars(100)
    regime_ai = RegimeAI.fit(fit_bars, n_states_candidates=(2, 3))
    runtime = RegimeRuntime(_SYMBOL, regime_ai, InProcessBus())

    mixed = [*_bars(3, symbol="PRECEDING"), *_bars(4, symbol="STRANGER"), *_bars(2)]

    assert runtime.warm_start(mixed, accept_symbols=[_SYMBOL, "PRECEDING"]) == 5


def test_warm_start_respects_capacity():
    """용량을 넘으면 최신 것만 남는다 — `deque(maxlen)` 계약."""
    fit_bars = _bars(100)
    regime_ai = RegimeAI.fit(fit_bars, n_states_candidates=(2, 3))
    runtime = RegimeRuntime(_SYMBOL, regime_ai, InProcessBus(), history_limit=30)

    assert runtime.history_capacity == 30
    assert runtime.warm_start(_bars(120)) == 30
    assert runtime._bars()[-1].bar_open_kst == _bars(120)[-1].bar_open_kst  # noqa: SLF001


@pytest.mark.asyncio
async def test_classification_is_logged_for_the_report_axis(monkeypatch):
    """판정마다 `RegimeClassified`를 남긴다 (2026-08-12 F-2).

    이 태그가 리포트의 `regime_distribution`의 유일한 재료다 — 없으면 "14건 전부 UNKNOWN"
    같은 상태를 사람이 로그를 눈으로 읽어야만 발견한다.
    """
    from messiah.strategy.regime import runtime as runtime_module

    logged: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        runtime_module.mlog, "log", lambda tag, msg, **fields: logged.append((tag, fields))
    )

    fit_bars = _bars(120)
    regime_ai = RegimeAI.fit(fit_bars, n_states_candidates=(2, 3))
    runtime = RegimeRuntime(_SYMBOL, regime_ai, InProcessBus())
    runtime.warm_start(_bars(200)[:100])

    await runtime.handle_bar(fit_bars[0])

    tags = [tag for tag, _ in logged]
    assert tags == ["RegimeClassified"]
    fields = logged[0][1]
    assert fields["regime"] in {r.value for r in Regime}
    assert fields["bars_used"] == 101
    assert fields["min_bars"] == regime_ai.min_bars_for_classify


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
