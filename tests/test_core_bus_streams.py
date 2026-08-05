"""Stream 소비의 사각지대 — `$` 재해석으로 메시지를 잃던 경로 (2026-08-05 3차, P0-2).

페이크 Redis는 **`$`의 실제 시맨틱을 그대로 흉내낸다**: 고정된 위치가 아니라 "호출 시점의
마지막 ID"로 매번 다시 해석된다. 그래야 이 테스트가 실제 버그를 재는 것이 된다 — 단순히
"우리가 짠 대로 동작한다"를 확인하는 자기충족 테스트가 아니라.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from messiah.core.bus import MessageBus, encode
from messiah.core.messages import DecisionIntent, Fill, Horizon, Side
from messiah.core.state_cache import StateCache
from messiah.ui.app import _poll_streams_forever

_KST = timezone(timedelta(hours=9))


class _Stop(Exception):
    """무한 폴링 루프를 테스트에서 끊는 신호."""


def _intent(rationale: str = "테스트 판단") -> DecisionIntent:
    return DecisionIntent(
        symbol="A05608",
        side=Side.LONG,
        confidence=0.7,
        uncertainty=0.1,
        horizon=Horizon.M5,
        rationale=rationale,
    )


def _fill() -> Fill:
    return Fill(
        broker_order_no="o-1",
        symbol="A05608",
        qty=1,
        price_ticks=52200,
        ts_exchange=datetime(2026, 8, 5, 12, 15, tzinfo=_KST),
        pending_matched=True,
    )


def _id_tuple(entry_id: str) -> tuple[int, int]:
    left, _, right = entry_id.partition("-")
    return int(left), int(right or 0)


class _FakeRedis:
    """XREAD/XREVRANGE만 흉내내는 최소 페이크 — `$` 재해석 규칙이 핵심이다."""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, bytes]]] = {}
        self.xread_calls: list[dict[str, str]] = []
        self.on_block = None  # 블록 구간에 개입하는 훅(그 사이 도착을 재현)

    def add(self, topic: str, entry_id: str, message) -> None:
        self.streams.setdefault(topic, []).append((entry_id, encode(message)))

    def _last_id(self, topic: str) -> str:
        entries = self.streams.get(topic)
        return entries[-1][0] if entries else "0-0"

    async def xread(self, streams: dict[str, str], block: int = 0, count: int | None = None):
        self.xread_calls.append(dict(streams))
        # **`$`는 여기서 다시 해석된다** — 실제 Redis와 같다.
        resolved = {t: (self._last_id(t) if i == "$" else i) for t, i in streams.items()}

        out = []
        for topic, after in resolved.items():
            hits = [
                (eid.encode(), {b"data": data})
                for eid, data in self.streams.get(topic, [])
                if _id_tuple(eid) > _id_tuple(after)
            ]
            if hits:
                out.append((topic.encode(), hits[: count or len(hits)]))
        if out:
            return out

        # 블록 구간 — 실제 운영에서 메시지가 도착하던 바로 그 창이다.
        if self.on_block is not None:
            self.on_block()
        return None

    async def xrevrange(self, topic: str, count: int = 1):
        entries = list(reversed(self.streams.get(topic) or []))[:count]
        return [(eid.encode(), {b"data": data}) for eid, data in entries]


def _bus_with(fake: _FakeRedis) -> MessageBus:
    bus = MessageBus("redis://unused", instance_id="test")
    bus._redis = fake
    return bus


# ---------------------------------------------------------------- `$` 시맨틱 자체


async def test_dollar_sign_loses_whatever_arrives_between_calls():
    """**이 테스트가 P0-2 그 자체다.** `$`로 두 번 읽으면 그 사이 도착분이 영영 안 온다."""
    fake = _FakeRedis()
    bus = _bus_with(fake)

    fake.on_block = lambda: fake.add("decision.intent", "1000-0", _intent())

    first = await bus.read_streams({"decision.intent": "$"}, block_ms=0)
    assert first == []  # 블록 도중 도착 — 이 호출은 못 받는다

    fake.on_block = None
    second = await bus.read_streams({"decision.intent": "$"}, block_ms=0)
    assert second == []  # 그리고 다음 호출에서도 영영 안 온다


async def test_concrete_last_id_delivers_the_same_message():
    """같은 상황에서 구체 ID로 따라가면 하나도 안 잃는다 — 처방이 실제로 듣는지의 대조군."""
    fake = _FakeRedis()
    bus = _bus_with(fake)

    start = await bus.stream_last_id("decision.intent")
    fake.on_block = lambda: fake.add("decision.intent", "1000-0", _intent())
    assert await bus.read_streams({"decision.intent": start}, block_ms=0) == []

    fake.on_block = None
    delivered = await bus.read_streams({"decision.intent": start}, block_ms=0)
    assert [topic for topic, _eid, _msg in delivered] == ["decision.intent"]


async def test_stream_last_id_on_empty_stream_starts_from_the_beginning():
    """비어 있으면 `0-0` — `$`(사각지대 있음)로 시작하지 않는다."""
    bus = _bus_with(_FakeRedis())
    assert await bus.stream_last_id("decision.intent") == "0-0"


async def test_stream_last_id_skips_history_when_the_stream_is_not_empty():
    fake = _FakeRedis()
    fake.add("decision.intent", "1000-0", _intent("옛 판단"))
    fake.add("decision.intent", "2000-0", _intent("최근 판단"))
    bus = _bus_with(fake)

    assert await bus.stream_last_id("decision.intent") == "2000-0"


async def test_read_streams_returns_the_topic_each_entry_came_from():
    fake = _FakeRedis()
    fake.add("decision.intent", "1000-0", _intent())
    fake.add("exec.fill", "1000-1", _fill())
    bus = _bus_with(fake)

    entries = await bus.read_streams({"decision.intent": "0-0", "exec.fill": "0-0"}, block_ms=0)

    assert {topic for topic, _eid, _msg in entries} == {"decision.intent", "exec.fill"}
    assert {type(msg).__name__ for _t, _e, msg in entries} == {"DecisionIntent", "Fill"}


async def test_read_stream_single_topic_still_works():
    """기존 호출부(`read_stream`)의 계약은 안 바뀐다 — 내부만 다중 읽기로 위임."""
    fake = _FakeRedis()
    fake.add("decision.intent", "1000-0", _intent())
    bus = _bus_with(fake)

    entries = await bus.read_stream("decision.intent", last_id="0-0", block_ms=0)

    assert len(entries) == 1
    entry_id, message = entries[0]
    assert entry_id == "1000-0"
    assert isinstance(message, DecisionIntent)


# ---------------------------------------------------------------- UI 폴링 루프


async def test_ui_poll_loop_blocks_once_for_all_topics():
    """토픽별로 따로 블록하면 서로의 사각지대가 된다 — 한 번의 XREAD가 전부를 덮어야 한다."""
    fake = _FakeRedis()
    bus = _bus_with(fake)
    calls = {"blocks": 0}

    def _stop_after_one_block():
        calls["blocks"] += 1
        raise _Stop

    fake.on_block = _stop_after_one_block

    with pytest.raises(_Stop):
        await _poll_streams_forever(bus, StateCache(), poll_ms=0)

    assert len(fake.xread_calls) == 1
    assert set(fake.xread_calls[0]) == {"decision.intent", "exec.fill"}


async def test_ui_poll_loop_never_asks_for_dollar_sign():
    """회귀 방지 — 루프 어디에서도 `$`가 다시 등장하면 사각지대가 되살아난 것이다."""
    fake = _FakeRedis()
    fake.add("decision.intent", "1000-0", _intent())
    bus = _bus_with(fake)

    state = {"rounds": 0}

    def _stop_after_a_few():
        state["rounds"] += 1
        if state["rounds"] >= 3:
            raise _Stop

    fake.on_block = _stop_after_a_few

    with pytest.raises(_Stop):
        await _poll_streams_forever(bus, StateCache(), poll_ms=0)

    assert fake.xread_calls  # 실제로 읽긴 했고
    for call in fake.xread_calls:
        assert "$" not in call.values()


async def test_ui_poll_loop_delivers_a_decision_that_arrives_during_the_block():
    """운영에서 가장 놓치기 쉬운 순간 — 하루 첫 판단이 블록 도중 도착하는 경우."""
    fake = _FakeRedis()
    bus = _bus_with(fake)
    cache = StateCache()

    state = {"blocks": 0}

    def _publish_then_stop():
        state["blocks"] += 1
        if state["blocks"] == 1:
            fake.add("decision.intent", "1000-0", _intent("첫 판단"))
        elif state["blocks"] >= 3:
            raise _Stop

    fake.on_block = _publish_then_stop

    with pytest.raises(_Stop):
        await _poll_streams_forever(bus, cache, poll_ms=0)

    cached = cache.get("DecisionIntent")
    assert isinstance(cached, DecisionIntent)
    assert cached.rationale == "첫 판단"


async def test_ui_poll_loop_does_not_replay_history_from_before_startup():
    """기동 전 이력까지 끌어오면 화면이 "방금 판단이 났다"고 거짓말한다."""
    fake = _FakeRedis()
    fake.add("decision.intent", "1000-0", _intent("어제 판단"))
    bus = _bus_with(fake)
    cache = StateCache()

    fake.on_block = lambda: (_ for _ in ()).throw(_Stop())

    with pytest.raises(_Stop):
        await _poll_streams_forever(bus, cache, poll_ms=0)

    assert cache.get("DecisionIntent") is None
