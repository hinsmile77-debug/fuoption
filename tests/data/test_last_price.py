from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from messiah.core.messages import Tick
from messiah.core.timeutil import KST
from messiah.data.last_price import LastPriceTracker

_SYMBOL = "A05608"
_TICK_SIZE = Decimal("0.02")
_NOW = datetime(2026, 8, 4, 10, 0, tzinfo=KST)


def _tracker(**kw):
    return LastPriceTracker(_SYMBOL, _TICK_SIZE, **kw)


def _tick(price_ticks: int, symbol: str = _SYMBOL) -> Tick:
    return Tick(symbol=symbol, ts_exchange=_NOW, price_ticks=price_ticks, qty=1)


def test_converts_ticks_to_index_points():
    """미니선물 49904틱 x 0.02 = 998.08 — 2026-08-04 실측값."""
    tracker = _tracker()

    tracker.update(49904, seen_at=_NOW)

    assert tracker.price_points(now=_NOW) == pytest.approx(998.08)


def test_returns_none_before_any_tick():
    assert _tracker().price_points(now=_NOW) is None


def test_stale_price_is_treated_as_missing():
    """WS가 끊겨도 마지막 값은 메모리에 남는다 — 그걸로 ATM을 잡으면 옛 창을 계속 조회한다."""
    tracker = _tracker(max_age_seconds=180.0)
    tracker.update(49904, seen_at=_NOW)

    assert tracker.price_points(now=_NOW + timedelta(seconds=179)) is not None
    assert tracker.price_points(now=_NOW + timedelta(seconds=181)) is None


def test_max_age_is_shorter_than_the_option_poll_grid():
    """폴링 격자(300초)보다 짧아야 한 사이클을 통째로 건너뛰기 전에 먼저 드러난다."""
    from messiah.data.last_price import DEFAULT_MAX_AGE_SECONDS

    assert DEFAULT_MAX_AGE_SECONDS < 300.0


@pytest.mark.asyncio
async def test_handles_ticks_for_its_own_symbol_only():
    tracker = _tracker()

    await tracker.handle_tick(_tick(50000, symbol="OTHER"))
    assert tracker.price_points(now=_NOW) is None

    await tracker.handle_tick(_tick(50000))
    assert tracker.price_points(now=_NOW) == pytest.approx(1000.0)


@pytest.mark.asyncio
async def test_latest_tick_wins():
    tracker = _tracker()

    await tracker.handle_tick(_tick(49904))
    await tracker.handle_tick(_tick(50100))

    assert tracker.price_points(now=_NOW) == pytest.approx(1002.0)


@pytest.mark.asyncio
async def test_subscribes_to_the_symbols_tick_topic():
    seen: list[list[str]] = []

    class FakeBus:
        async def subscribe(self, patterns, handler):
            seen.append(patterns)

        async def publish(self, topic, msg):  # pragma: no cover — 미사용
            raise NotImplementedError

    await _tracker().run_forever(FakeBus())

    assert seen == [["md.tick.A05608"]]
