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


# ---------------------------------------------- 기준가의 나이 (2026-09-02 F-73)


def test_as_of_travels_with_the_price_it_belongs_to():
    """값과 시각이 갈라지면 나이가 다른 순간을 가리킨다 — 한 메서드가 둘을 같이 낸다."""
    tracker = _tracker()
    seen_at = _NOW - timedelta(seconds=30)

    tracker.update(50100, seen_at=seen_at)

    assert tracker.price_point_as_of(now=_NOW) == (pytest.approx(1002.0), seen_at)
    # `price_points()`는 같은 선택 규칙의 첫 원소여야 한다(둘로 갈라 두지 않는다).
    assert tracker.price_points(now=_NOW) == tracker.price_point_as_of(now=_NOW)[0]


def test_a_preopen_seed_carries_the_moment_it_was_true():
    """08:22~08:45의 그 값이 전 거래일 15:34봉이었다는 사실이 나이로 드러나야 한다.

    08-28·08-31에 이 나이를 아무도 재지 않아 462·420다리가 조용히 나갔다.
    """
    tracker = _tracker()
    bar_at = _NOW - timedelta(hours=17)

    tracker.seed_preopen(50100, as_of=bar_at)

    price, as_of = tracker.price_point_as_of(now=_NOW)
    assert price == pytest.approx(1002.0)
    assert as_of == bar_at
    assert (_NOW - as_of).total_seconds() == pytest.approx(17 * 3600)


def test_a_seed_without_a_time_reports_unknown_not_now():
    """시각을 안 주면 `None` — 「모른다」이지 「지금」이 아니다(L18)."""
    tracker = _tracker()

    tracker.seed_preopen(50100)

    assert tracker.price_point_as_of(now=_NOW) == (pytest.approx(1002.0), None)


def test_naive_seed_time_is_refused():
    """R3 — naive datetime을 받아 두면 그것과의 뺄셈이 나중에 터진다."""
    with pytest.raises(ValueError):
        _tracker().seed_preopen(50100, as_of=datetime(2026, 9, 1, 15, 34))  # noqa: DTZ001


def test_no_price_means_no_time():
    """값이 없으면 시각도 없다 — 사이클 스킵과 「오래된 값으로 발행」은 다른 사건이다."""
    assert _tracker().price_point_as_of(now=_NOW) == (None, None)
