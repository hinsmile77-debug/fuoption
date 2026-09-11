"""완성봉을 몇 명이 받았나 — 2026-09-11 G-6 (`core/bus._log_bar_receivers`).

2026-09-09·09-11에 `g2_daily`가 5분 그리드에서 조용히 비었다. 수신 쪽 진단 계측
(`OptionsDispatchIgnored`·`OptionsHandleBarFailed`)이 둘 다 0건이라 "메시지가 도달하지
않았다(㉠)"까지는 확정됐는데, **"발행 시점에 구독자가 없었다"와 "구독자는 있었는데
유실됐다"를 가를 자료가 없어** 거기서 조사가 멈췄다.

Redis `PUBLISH`는 그 답(수신 클라이언트 수)을 이미 돌려주고 있었다. 여기서 고정하는 것은
① 그 값이 로그로 남는가 ② 0이 다른 태그·다른 심각도로 갈라지는가(R6) ③ **발행 자체는
하나도 안 변했는가**(판정 불변)이다.
"""

from __future__ import annotations

import logging
from datetime import datetime

import pytest

from messiah.core.bus import MessageBus, decode
from messiah.core.messages import BarClosed, BarSession, Horizon
from messiah.core.timeutil import KST


def _bar(symbol: str = "A05610") -> BarClosed:
    return BarClosed(
        symbol=symbol,
        horizon=Horizon.M5,
        bar_open_kst=datetime(2026, 9, 11, 14, 40, tzinfo=KST),
        o_ticks=1_750_000,
        h_ticks=1_750_010,
        l_ticks=1_749_990,
        c_ticks=1_750_005,
        volume=123,
        quality_ok=True,
        session=BarSession.REGULAR,
    )


class _FakeRedis:
    """`publish`가 수신자 수를 돌려주는 것까지 흉내낸다 — 그게 이 변경의 관심사다."""

    def __init__(self, receivers: int) -> None:
        self._receivers = receivers
        self.published: list[tuple[str, bytes]] = []
        self.xadded: list[str] = []

    async def publish(self, topic: str, data: bytes) -> int:
        self.published.append((topic, data))
        return self._receivers

    async def xadd(self, topic: str, fields, **kwargs) -> str:
        self.xadded.append(topic)
        return "1-1"


def _bus(receivers: int) -> tuple[MessageBus, _FakeRedis]:
    bus = MessageBus("redis://unused", "test")
    fake = _FakeRedis(receivers)
    bus._redis = fake
    return bus, fake


# ------------------------------------------------------------ 계측


@pytest.mark.asyncio
async def test_subscriber_count_is_logged(caplog):
    bus, _ = _bus(receivers=2)

    with caplog.at_level(logging.DEBUG):
        await bus.publish("bar.5m.A05610", _bar())

    counted = [r for r in caplog.records if getattr(r, "tag", "") == "BarPublishSubscriberCount"]
    assert len(counted) == 1
    assert counted[0].levelno == logging.DEBUG


@pytest.mark.asyncio
async def test_zero_subscribers_gets_its_own_tag_and_severity(caplog):
    """0은 **다른 사건**이다 — 태그 하나에 심각도 하나(R6)."""
    bus, _ = _bus(receivers=0)

    with caplog.at_level(logging.DEBUG):
        await bus.publish("bar.5m.A05610", _bar())

    tags = [getattr(r, "tag", "") for r in caplog.records]
    assert "BarPublishNoSubscriber" in tags
    assert "BarPublishSubscriberCount" not in tags
    empty = next(r for r in caplog.records if getattr(r, "tag", "") == "BarPublishNoSubscriber")
    assert empty.levelno == logging.WARNING


@pytest.mark.asyncio
async def test_non_bar_topics_are_not_counted(caplog):
    """폴링 계열까지 세면 하루 수만 줄이 된다 — 질문이 걸린 계열은 완성봉뿐이다."""
    bus, _ = _bus(receivers=0)

    with caplog.at_level(logging.DEBUG):
        await bus.publish("md.quote.A05610", _bar())

    tags = [getattr(r, "tag", "") for r in caplog.records]
    assert "BarPublishNoSubscriber" not in tags
    assert "BarPublishSubscriberCount" not in tags


# ------------------------------------------------------------ 판정 불변


@pytest.mark.asyncio
async def test_publish_payload_and_routing_unchanged():
    """계측을 얹었을 뿐 **발행은 한 글자도 안 변했다** — 토픽·페이로드·pub/sub 대 stream 분기."""
    bus, fake = _bus(receivers=0)
    bar = _bar()

    await bus.publish("bar.5m.A05610", bar)

    assert [topic for topic, _ in fake.published] == ["bar.5m.A05610"]
    assert fake.xadded == []
    restored = decode(fake.published[0][1])
    assert (restored.symbol, restored.volume, restored.bar_open_kst) == (
        bar.symbol,
        bar.volume,
        bar.bar_open_kst,
    )
