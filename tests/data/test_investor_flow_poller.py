"""투자자매매동향(FL) REST 폴러 (신규, 2026-07-27)."""

from __future__ import annotations

import pytest

from messiah.core.messages import InvestorFlowSnapshot
from messiah.core.scheduler import FixedTickScheduler
from messiah.data import poll_retry
from messiah.data.investor_flow_poller import InvestorFlowPoller


class FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, object]] = []

    async def publish(self, topic: str, msg: object) -> None:
        self.published.append((topic, msg))

    async def subscribe(self, patterns, handler) -> None:  # pragma: no cover — 미사용
        raise NotImplementedError


class FakeRestClient:
    def __init__(
        self,
        responses: dict[str, dict] | None = None,
        fail_for: set[str] | None = None,
        fail_times: dict[str, int] | None = None,
    ):
        self._responses = responses or {}
        self._fail_for = fail_for or set()
        # 업종별 **처음 N번만** 실패 — 재시도로 살아나는 경로를 재현한다(2026-08-10 A-4).
        self._fail_times = dict(fail_times or {})
        self.calls: list[tuple[str, str]] = []

    def get_investor_flow(self, market_code: str, sector_code: str) -> dict:
        self.calls.append((market_code, sector_code))
        if self._fail_times.get(sector_code, 0) > 0:
            self._fail_times[sector_code] -= 1
            raise RuntimeError(f"KIS 500: {sector_code}")
        if sector_code in self._fail_for:
            raise RuntimeError(f"KIS 4xx: {sector_code}")
        return self._responses.get(sector_code, {"rt_cd": "0", "sector": sector_code})


async def _no_sleep(_seconds: float) -> None:
    """재시도 지연을 실제로 기다리면 스위트가 초 단위로 느려진다."""


def test_rejects_empty_sector_codes():
    with pytest.raises(ValueError, match="sector_codes"):
        InvestorFlowPoller(FakeRestClient(), "K2I", [], FakeBus())


def test_rejects_negative_retry_attempts():
    with pytest.raises(ValueError, match="retry_attempts"):
        InvestorFlowPoller(FakeRestClient(), "K2I", ["F001"], FakeBus(), retry_attempts=-1)


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
        "messiah.data.poll_retry.mlog.log",
        lambda tag, msg, **f: logged.append((tag, msg)),
    )
    rest_client = FakeRestClient({"OC01": {"b": 2}}, fail_for={"F001"})
    bus = FakeBus()
    poller = InvestorFlowPoller(rest_client, "K2I", ["F001", "OC01"], bus, sleep=_no_sleep)

    await poller.poll_once()  # 예외를 밖으로 전파하지 않아야 함

    # F001은 재시도 예산을 다 쓰고(2026-08-10 A-4), 그래도 실패하면 OC01로 넘어간다.
    # 횟수는 정본 상수에서 끌어온다 — 박아두면 예산을 조정한 날 폴러가 아니라 이 줄이
    # 깨진다(2026-08-12 F-4로 총 2회 → 3회가 되며 실제로 그랬다).
    tries = 1 + poll_retry.RETRY_ATTEMPTS
    assert rest_client.calls == [("K2I", "F001")] * tries + [("K2I", "OC01")]
    assert len(bus.published) == 1  # OC01만 발행됨
    assert bus.published[0][1].sector_code == "OC01"
    assert any(tag == "InvestorFlowPollError" for tag, _ in logged)


async def test_retry_recovers_a_failed_sector_and_logs_a_different_tag(monkeypatch):
    """2026-08-10에 이 폴러엔 재시도가 없어 3행을 그대로 잃었다 — 그 자리를 채운 회귀 테스트.

    **태그가 갈리는지**까지 본다. 살아난 조회를 `InvestorFlowPollError`로 남기면 리포트의
    WARNING 수가 "잃은 행 수"를 더 이상 뜻하지 않게 된다(`data/poll_retry.py` docstring).
    """
    logged: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "messiah.data.poll_retry.mlog.log",
        lambda tag, msg, **f: logged.append((tag, msg)),
    )
    rest_client = FakeRestClient({"F001": {"a": 1}}, fail_times={"F001": 1})
    bus = FakeBus()
    poller = InvestorFlowPoller(rest_client, "K2I", ["F001"], bus, sleep=_no_sleep)

    await poller.poll_once()

    assert rest_client.calls == [("K2I", "F001"), ("K2I", "F001")]
    assert len(bus.published) == 1  # 재시도가 살렸으므로 행이 안 빈다
    assert bus.published[0][1].raw == {"a": 1}
    tags = [tag for tag, _ in logged]
    assert tags == ["InvestorFlowPollRetried"]  # Error가 아니다


async def test_first_try_success_is_silent(monkeypatch):
    """정상은 조용해야 한다 — 성공마다 한 줄씩 남기면 하루 1,188줄이 되고 아무도 안 읽는다."""
    logged: list[str] = []
    monkeypatch.setattr(
        "messiah.data.poll_retry.mlog.log", lambda tag, msg, **f: logged.append(tag)
    )
    poller = InvestorFlowPoller(
        FakeRestClient({"F001": {"a": 1}}), "K2I", ["F001"], FakeBus(), sleep=_no_sleep
    )

    await poller.poll_once()

    assert logged == []


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
