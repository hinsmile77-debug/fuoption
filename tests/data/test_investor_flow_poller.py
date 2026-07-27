"""투자자매매동향(FL) REST 폴러 (신규, 2026-07-27)."""

from __future__ import annotations

import pytest
from messiah.core.messages import InvestorFlowSnapshot
from messiah.core.scheduler import FixedTickScheduler
from messiah.data.investor_flow_poller import InvestorFlowPoller


class FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, object]] = []

    async def publish(self, topic: str, msg: object) -> None:
        self.published.append((topic, msg))

    async def subscribe(self, patterns, handler) -> None:  # pragma: no cover — 미사용
        raise NotImplementedError


class FakeRestClient:
    def __init__(self, responses: dict[str, dict] | None = None, fail_for: set[str] | None = None):
        self._responses = responses or {}
        self._fail_for = fail_for or set()
        self.calls: list[tuple[str, str]] = []

    def get_investor_flow(self, market_code: str, sector_code: str) -> dict:
        self.calls.append((market_code, sector_code))
        if sector_code in self._fail_for:
            raise RuntimeError(f"KIS 4xx: {sector_code}")
        return self._responses.get(sector_code, {"rt_cd": "0", "sector": sector_code})


def test_rejects_empty_sector_codes():
    with pytest.raises(ValueError, match="sector_codes"):
        InvestorFlowPoller(FakeRestClient(), "K2I", [], FakeBus())


async def test_poll_once_queries_every_sector_code_and_publishes_raw():
    rest_client = FakeRestClient({"F001": {"a": 1}, "OC01": {"b": 2}})
    bus = FakeBus()
    poller = InvestorFlowPoller(rest_client, "K2I", ["F001", "OC01"], bus)

    await poller.poll_once()

    assert rest_client.calls == [("K2I", "F001"), ("K2I", "OC01")]
    assert len(bus.published) == 2
    topics = [t for t, _ in bus.published]
    assert topics == ["raw.investor_flow.K2I", "raw.investor_flow.K2I"]
    snapshots = [m for _, m in bus.published]
    assert all(isinstance(s, InvestorFlowSnapshot) for s in snapshots)
    assert snapshots[0].raw == {"a": 1}
    assert snapshots[0].sector_code == "F001"
    assert snapshots[1].raw == {"b": 2}
    assert snapshots[1].sector_code == "OC01"


async def test_poll_once_continues_after_one_sector_fails(monkeypatch):
    logged: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "messiah.data.investor_flow_poller.mlog.log",
        lambda tag, msg, **f: logged.append((tag, msg)),
    )
    rest_client = FakeRestClient({"OC01": {"b": 2}}, fail_for={"F001"})
    bus = FakeBus()
    poller = InvestorFlowPoller(rest_client, "K2I", ["F001", "OC01"], bus)

    await poller.poll_once()  # 예외를 밖으로 전파하지 않아야 함

    assert rest_client.calls == [("K2I", "F001"), ("K2I", "OC01")]  # F001 실패해도 OC01은 계속 시도
    assert len(bus.published) == 1  # OC01만 발행됨
    assert bus.published[0][1].sector_code == "OC01"
    assert any(tag == "InvestorFlowPollError" for tag, _ in logged)


async def test_poll_once_logs_but_does_not_raise_on_publish_failure(monkeypatch):
    logged: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "messiah.data.investor_flow_poller.mlog.log",
        lambda tag, msg, **f: logged.append((tag, msg)),
    )

    class FailingBus(FakeBus):
        async def publish(self, topic: str, msg: object) -> None:
            raise ConnectionError("Redis 다운")

    rest_client = FakeRestClient({"F001": {"a": 1}})
    poller = InvestorFlowPoller(rest_client, "K2I", ["F001"], FailingBus())

    await poller.poll_once()  # 발행 실패에도 예외 전파 없이 조용히 로깅만

    assert any(tag == "InvestorFlowPollError" for tag, _ in logged)


async def test_scheduler_drives_poller_poll_once_repeatedly():
    # FixedTickScheduler(W3~5)의 "아직 실제 폴러에 안 물려봄" 갭을 이 폴러가 처음 메운다는
    # 것을 스케줄러와 실제로 엮어 확인 — tick_seconds를 작게 잡아 실제 대기는 짧게 유지.
    rest_client = FakeRestClient({"F001": {"a": 1}})
    bus = FakeBus()
    poller = InvestorFlowPoller(rest_client, "K2I", ["F001"], bus)
    scheduler = FixedTickScheduler(tick_seconds=0.05)

    await scheduler.run_forever(poller.poll_once, max_iterations=3)

    assert rest_client.calls == [("K2I", "F001")] * 3
    assert len(bus.published) == 3
