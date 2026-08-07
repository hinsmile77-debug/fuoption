"""`sys.kill` 배달 계약 — 2026-08-07 실사고를 회귀로 못박는다.

그날 13:41, 이 시스템 역사상 처음으로 `sys.kill`이 흘렀다. `MessageBus.subscribe()`가
**모든** 구독자 패턴에 `TOPIC_KILL`을 자동으로 끼워 넣고 있었고(*"어떤 구독자도 kill을
놓치지 않는다"*), `FeatureEngine.handle_bar`가 그것을 받아 `bar.symbol`에서
`AttributeError`를 냈다. 예외가 구독 루프째 무너뜨려 **수집 프로세스가 종료됐고**
1시간 54분이 유실됐다(체결틱·옵션체인·장중수급은 소급 경로가 없어 영구 소실).

이 파일이 지키는 두 가지:
  1. kill은 **원한 구독자에게만** 간다.
  2. 핸들러가 무엇으로 죽든 **루프는 산다**.
"""

from __future__ import annotations

import pytest

from messiah.core.bus import TOPIC_BAR, TOPIC_KILL, MessageBus, encode
from messiah.core.messages import Health, HealthLevel, KillSignal

pytestmark = pytest.mark.asyncio


class _FakePubSub:
    """`psubscribe`로 받은 패턴을 기록하고, 준비된 메시지를 흘려보낸다."""

    def __init__(self, payloads: list[bytes]) -> None:
        self.subscribed: set[str] = set()
        self._payloads = payloads

    async def psubscribe(self, *patterns: str) -> None:
        self.subscribed.update(patterns)

    async def listen(self):
        for payload in self._payloads:
            yield {"type": "pmessage", "data": payload}


class _FakeRedis:
    def __init__(self, payloads: list[bytes]) -> None:
        self.pubsub_obj = _FakePubSub(payloads)

    def pubsub(self):
        return self.pubsub_obj


def _bus(payloads: list[bytes]) -> tuple[MessageBus, _FakeRedis]:
    bus = MessageBus("redis://unused/0", instance_id="test")
    fake = _FakeRedis(payloads)
    bus._redis = fake  # noqa: SLF001 — connect()를 우회해 Redis 없이 루프만 검증
    return bus, fake


def _kill() -> bytes:
    return encode(KillSignal(reason="테스트", triggered_by="manual"))


def _health() -> bytes:
    return encode(Health(component="l1.collector", level=HealthLevel.OK, detail=""))


async def test_kill_is_not_delivered_to_a_subscriber_that_did_not_ask():
    """**2026-08-07 사고의 정확한 재현 조건.** 봉만 보겠다는 구독자에게 kill이 가면 안 된다."""
    seen: list[str] = []

    async def handler(msg):
        seen.append(type(msg).__name__)

    bus, fake = _bus([_kill(), _health()])
    await bus.subscribe([f"{TOPIC_BAR}.1m.A05608", "sys.health"], handler)

    assert "KillSignal" not in seen, "원하지 않은 구독자에게 kill이 배달됐다"
    assert seen == ["Health"]
    assert TOPIC_KILL not in fake.pubsub_obj.subscribed, "구독조차 하지 말아야 한다"


async def test_kill_goes_to_on_kill_when_requested():
    handled: list[str] = []
    killed: list[KillSignal] = []

    async def handler(msg):
        handled.append(type(msg).__name__)

    async def on_kill(msg):
        killed.append(msg)

    bus, fake = _bus([_health(), _kill()])
    await bus.subscribe(["sys.health"], handler, on_kill=on_kill)

    assert TOPIC_KILL in fake.pubsub_obj.subscribed
    assert len(killed) == 1 and killed[0].triggered_by == "manual"
    assert handled == ["Health"], "kill이 일반 핸들러로도 새면 안 된다"


async def test_explicit_kill_pattern_still_reaches_the_handler():
    """전용 kill 리스너 — `patterns`에 직접 넣으면 `handler`가 받는다."""
    seen: list[str] = []

    async def handler(msg):
        seen.append(type(msg).__name__)

    bus, _ = _bus([_kill()])
    await bus.subscribe([TOPIC_KILL], handler)

    assert seen == ["KillSignal"]


async def test_handler_exception_does_not_kill_the_loop():
    """**이 격리가 있었다면 2026-08-07 손실은 0이었다.**"""
    seen: list[str] = []

    async def handler(msg):
        seen.append(type(msg).__name__)
        if len(seen) == 1:
            raise AttributeError("'KillSignal' object has no attribute 'symbol'")

    bus, _ = _bus([_health(), _health(), _health()])
    await bus.subscribe(["sys.health"], handler)

    assert len(seen) == 3, "첫 메시지의 예외가 나머지를 막았다 — 그날 사고 그대로다"


async def test_broken_payload_does_not_kill_the_loop():
    seen: list[str] = []

    async def handler(msg):
        seen.append(type(msg).__name__)

    bus, _ = _bus([b"{not json", _health()])
    await bus.subscribe(["sys.health"], handler)

    assert seen == ["Health"]


async def test_failure_is_logged_at_least_once(monkeypatch):
    """살아남는 대신 **로그가 유일한 증거**가 된다 — 조용히 버리면 안 된다."""
    logged: list[tuple[str, dict]] = []
    monkeypatch.setattr("messiah.core.logging.log", lambda tag, msg, **f: logged.append((tag, f)))

    async def handler(msg):
        raise RuntimeError("boom")

    bus, _ = _bus([_health()])
    await bus.subscribe(["sys.health"], handler)

    assert [tag for tag, _ in logged] == ["SubscriberHandlerFailed"]
    assert logged[0][1]["failures"] == 1


async def test_repeated_failures_are_throttled_but_counted(monkeypatch):
    """건당 한 줄이면 초당 수십 줄이 되어 로그가 못 쓰게 된다 — 접되 크기는 잃지 않는다."""
    logged: list[dict] = []
    monkeypatch.setattr("messiah.core.logging.log", lambda tag, msg, **f: logged.append(f))

    async def handler(msg):
        raise RuntimeError("boom")

    bus, _ = _bus([_health()] * 30)
    await bus.subscribe(["sys.health"], handler)

    counts = [entry["failures"] for entry in logged]
    assert counts[0] == 1
    assert len(counts) < 30, "매 건마다 찍으면 늑대소년이 된다"
    assert counts[-1] == 30, "마지막 누적 건수는 실제 실패 수와 같아야 한다"
