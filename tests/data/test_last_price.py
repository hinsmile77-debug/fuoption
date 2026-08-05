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


# ------------------------------------------- 장전 시드 (2026-08-05 장중 점검 P2-1)
#
# 수집은 08:35에 뜨는데 첫 틱은 08:45 정각이다 — 그 10분간 옵션체인 5사이클이 기준가 없이
# 통째로 비었고, 옵션 스냅샷은 소급 경로가 없어 영원히 빈다.


def test_preopen_seed_supplies_a_reference_price_before_the_first_tick():
    tracker = _tracker()
    tracker.seed_preopen(49904)

    assert tracker.has_seen_tick is False
    assert tracker.price_points(now=_NOW) == pytest.approx(998.08)


def test_the_first_real_tick_overrides_the_seed():
    tracker = _tracker()
    tracker.seed_preopen(49904)

    tracker.update(50000, seen_at=_NOW)

    assert tracker.has_seen_tick is True
    assert tracker.price_points(now=_NOW) == pytest.approx(1000.0)


def test_the_seed_never_resurrects_a_stale_price_mid_session():
    """**이 테스트가 시드 설계의 핵심 제약이다.**

    장중에 WS가 끊기면 신선도 규칙이 None을 돌려주고 폴러가 그 사이클을 건너뛴다. 시드가
    그 자리를 메우면, 이 모듈이 애초에 막으려던 실패(가격이 움직인 뒤에도 옛 창을 계속
    조회)를 시드가 우회해 버린다 — 그것도 **하필 사고 중에**.
    """
    tracker = _tracker(max_age_seconds=180.0)
    tracker.seed_preopen(49904)
    tracker.update(50000, seen_at=_NOW)

    assert tracker.price_points(now=_NOW + timedelta(seconds=181)) is None


def test_no_seed_means_no_reference_price():
    """시드를 안 넣으면 2026-08-05 이전과 완전히 같은 동작."""
    assert _tracker().price_points(now=_NOW) is None


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
