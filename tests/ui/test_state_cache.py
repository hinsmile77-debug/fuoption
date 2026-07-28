"""State Cache (신규, Ver 2.0 §9 W32~34)."""

from __future__ import annotations

from datetime import timedelta

from messiah.core.messages import DecisionIntent, FuturesView, Side
from messiah.core.timeutil import now_utc
from messiah.simulator.inprocess_bus import InProcessBus
from messiah.ui.state_cache import CacheSubscriber, StateCache

_SYMBOL = "TEST"


def _intent() -> DecisionIntent:
    return DecisionIntent(symbol=_SYMBOL, side=Side.LONG, confidence=0.7, uncertainty=0.1)


def _futures_view() -> FuturesView:
    return FuturesView(
        symbol=_SYMBOL,
        score=0.5,
        agg_p_up=0.6,
        agg_p_down=0.4,
        uncertainty=0.1,
        dispersion=0.1,
        regime="TREND_UP",
        n_experts=1,
    )


# ---------------------------------------------------------------- StateCache


def test_get_returns_none_before_any_update():
    cache = StateCache()
    assert cache.get("DecisionIntent") is None


def test_update_then_get_returns_latest_message():
    cache = StateCache()
    intent = _intent()
    cache.update("DecisionIntent", intent)
    assert cache.get("DecisionIntent") is intent


def test_update_overwrites_previous_value_for_same_key():
    cache = StateCache()
    cache.update("DecisionIntent", _intent())
    second = _intent().model_copy(update={"confidence": 0.9})
    cache.update("DecisionIntent", second)
    assert cache.get("DecisionIntent").confidence == 0.9


def test_age_seconds_none_when_never_updated():
    cache = StateCache()
    assert cache.age_seconds("DecisionIntent") is None


def test_age_seconds_reflects_elapsed_time():
    cache = StateCache()
    cache.update("DecisionIntent", _intent())
    later = now_utc() + timedelta(seconds=12)
    assert cache.age_seconds("DecisionIntent", now=later) == 12.0


def test_snapshot_keys_lists_all_updated_keys():
    cache = StateCache()
    cache.update("DecisionIntent", _intent())
    cache.update("FuturesView", _futures_view())
    assert sorted(cache.snapshot_keys()) == ["DecisionIntent", "FuturesView"]


# ---------------------------------------------------------------- CacheSubscriber


async def test_cache_subscriber_updates_cache_by_message_type_name():
    bus = InProcessBus()
    cache = StateCache()
    subscriber = CacheSubscriber(bus, ["decision.intent", "intel.futures"], cache)
    await subscriber.run_forever()

    await bus.publish("decision.intent", _intent())
    await bus.publish("intel.futures", _futures_view())

    assert cache.get("DecisionIntent") is not None
    assert cache.get("FuturesView") is not None


async def test_cache_subscriber_uses_custom_topic_key_fn():
    bus = InProcessBus()
    cache = StateCache()
    subscriber = CacheSubscriber(
        bus, ["decision.intent"], cache, topic_key_fn=lambda msg: f"intent:{msg.symbol}"
    )
    await subscriber.run_forever()

    await bus.publish("decision.intent", _intent())

    assert cache.get(f"intent:{_SYMBOL}") is not None
    assert cache.get("DecisionIntent") is None
