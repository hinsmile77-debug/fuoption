"""옵션체인 시세호가(OP) REST 폴러 (신규, Ver 2.0 §9 W27~29) — InvestorFlowPoller
(`tests/data/test_investor_flow_poller.py`)와 동일 패턴으로 검증."""

from __future__ import annotations

import pytest

from messiah.broker.kis.symbol_master import OptionLeg
from messiah.core.messages import OptionQuoteSnapshot
from messiah.core.scheduler import FixedTickScheduler
from messiah.data.option_chain_poller import OptionChainPoller


class FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, object]] = []

    async def publish(self, topic: str, msg: object) -> None:
        self.published.append((topic, msg))

    async def subscribe(self, patterns, handler) -> None:  # pragma: no cover — 미사용
        raise NotImplementedError


class FakeMaster:
    """`chain`은 전 시리즈 공통 체인, `chains`는 시리즈별 체인(확정 유니버스 3종 검증용)."""

    def __init__(
        self,
        chain: list[OptionLeg] | None = None,
        chains: dict[str, list[OptionLeg]] | None = None,
    ) -> None:
        self._chain = chain or []
        self._chains = chains
        self.calls: list[tuple[str, str]] = []

    def nearest_expiry_chain(self, underlying: str, *, series: str = "regular") -> list[OptionLeg]:
        self.calls.append((underlying, series))
        if self._chains is not None:
            return self._chains.get(series, [])
        return self._chain


class FakeRestClient:
    def __init__(self, responses: dict[str, dict] | None = None, fail_for: set[str] | None = None):
        self._responses = responses or {}
        self._fail_for = fail_for or set()
        self.calls: list[str] = []

    def get_asking_price(self, symbol: str) -> dict:
        self.calls.append(symbol)
        if symbol in self._fail_for:
            raise RuntimeError(f"KIS 4xx: {symbol}")
        return self._responses.get(symbol, {"rt_cd": "0", "symbol": symbol})


_CALL_LEG = OptionLeg(option_type="C", strike=350.0, symbol="201S06375", month_label="콜 202608")
_PUT_LEG = OptionLeg(option_type="P", strike=350.0, symbol="201S16375", month_label="풋 202608")


async def test_poll_once_queries_every_leg_and_publishes_raw():
    master = FakeMaster([_CALL_LEG, _PUT_LEG])
    rest_client = FakeRestClient({"201S06375": {"a": 1}, "201S16375": {"b": 2}})
    bus = FakeBus()
    poller = OptionChainPoller(rest_client, master, bus, series=["regular"])

    await poller.poll_once()

    assert master.calls == [("KOSPI200", "regular")]
    assert rest_client.calls == ["201S06375", "201S16375"]
    assert len(bus.published) == 2
    topics = [t for t, _ in bus.published]
    assert topics == ["raw.option_chain.KOSPI200", "raw.option_chain.KOSPI200"]
    snapshots = [m for _, m in bus.published]
    assert all(isinstance(s, OptionQuoteSnapshot) for s in snapshots)
    assert snapshots[0].symbol == "201S06375"
    assert snapshots[0].option_type == "C"
    assert snapshots[0].strike == 350.0
    assert snapshots[0].expiry == "콜 202608"
    assert snapshots[0].raw == {"a": 1}
    assert snapshots[1].symbol == "201S16375"
    assert snapshots[1].raw == {"b": 2}


async def test_poll_once_skips_quietly_but_logs_when_chain_empty(monkeypatch):
    logged: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "messiah.data.option_chain_poller.mlog.log",
        lambda tag, msg, **f: logged.append((tag, msg)),
    )
    master = FakeMaster([])
    rest_client = FakeRestClient()
    poller = OptionChainPoller(rest_client, master, FakeBus(), series=["regular"])

    await poller.poll_once()

    assert rest_client.calls == []
    assert any(tag == "OptionChainPollEmpty" for tag, _ in logged)


async def test_poll_once_continues_after_one_leg_fails(monkeypatch):
    logged: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "messiah.data.option_chain_poller.mlog.log",
        lambda tag, msg, **f: logged.append((tag, msg)),
    )
    master = FakeMaster([_CALL_LEG, _PUT_LEG])
    rest_client = FakeRestClient({"201S16375": {"b": 2}}, fail_for={"201S06375"})
    bus = FakeBus()
    poller = OptionChainPoller(rest_client, master, bus, series=["regular"])

    await poller.poll_once()  # 예외를 밖으로 전파하지 않아야 함

    assert rest_client.calls == ["201S06375", "201S16375"]  # 콜 실패해도 풋은 계속 시도
    assert len(bus.published) == 1  # 풋만 발행됨
    assert bus.published[0][1].symbol == "201S16375"
    assert any(tag == "OptionChainPollError" for tag, _ in logged)


async def test_poll_once_logs_but_does_not_raise_on_publish_failure(monkeypatch):
    logged: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "messiah.data.option_chain_poller.mlog.log",
        lambda tag, msg, **f: logged.append((tag, msg)),
    )

    class FailingBus(FakeBus):
        async def publish(self, topic: str, msg: object) -> None:
            raise ConnectionError("Redis 다운")

    master = FakeMaster([_CALL_LEG])
    rest_client = FakeRestClient({"201S06375": {"a": 1}})
    poller = OptionChainPoller(rest_client, master, FailingBus(), series=["regular"])

    await poller.poll_once()  # 발행 실패에도 예외 전파 없이 조용히 로깅만

    assert any(tag == "OptionChainPollError" for tag, _ in logged)


# ------------------------------------------- 확정 유니버스 3종 (2026-08-04)


async def test_default_series_covers_the_confirmed_option_universe():
    """기본값이 먼쓰리·월위클리·목위클리 셋을 다 돈다 — 종전엔 'regular' 하나뿐이라
    위클리 둘은 설정에 있어도 조회 자체가 안 됐다."""
    master = FakeMaster(chains={"regular": [_CALL_LEG], "weekly_mon": [], "weekly_thu": []})
    poller = OptionChainPoller(FakeRestClient(), master, FakeBus())

    await poller.poll_once()

    assert [s for _, s in master.calls] == ["regular", "weekly_mon", "weekly_thu"]


async def test_every_series_chain_is_polled(monkeypatch):
    monkeypatch.setattr("messiah.data.option_chain_poller.mlog.log", lambda *a, **k: None)
    weekly_leg = OptionLeg(option_type="C", strike=355.0, symbol="2AFS0355", month_label="C 2608W1")
    master = FakeMaster(
        chains={"regular": [_CALL_LEG], "weekly_mon": [weekly_leg], "weekly_thu": [_PUT_LEG]}
    )
    rest_client = FakeRestClient()
    bus = FakeBus()
    poller = OptionChainPoller(rest_client, master, bus)

    await poller.poll_once()

    assert rest_client.calls == ["201S06375", "2AFS0355", "201S16375"]
    assert len(bus.published) == 3


async def test_empty_weekly_chain_does_not_stop_the_other_series(monkeypatch):
    """위클리는 만기 주간에 따라 실제로 빌 수 있다 — 그게 먼쓰리 수집까지 멈추면 안 된다."""
    logged: list[str] = []
    monkeypatch.setattr(
        "messiah.data.option_chain_poller.mlog.log", lambda tag, msg, **f: logged.append(tag)
    )
    master = FakeMaster(
        chains={"regular": [], "weekly_mon": [], "weekly_thu": [_CALL_LEG, _PUT_LEG]}
    )
    rest_client = FakeRestClient()
    poller = OptionChainPoller(rest_client, master, FakeBus())

    await poller.poll_once()

    assert rest_client.calls == ["201S06375", "201S16375"]  # 마지막 시리즈까지 도달
    assert logged.count("OptionChainPollEmpty") == 2


async def test_empty_series_list_is_rejected():
    """조회할 시리즈가 없는 폴러는 조용히 아무것도 안 하는 대신 생성 시점에 거부한다."""
    with pytest.raises(ValueError):
        OptionChainPoller(FakeRestClient(), FakeMaster([]), FakeBus(), series=[])


async def test_scheduler_drives_poller_poll_once_repeatedly():
    master = FakeMaster([_CALL_LEG])
    rest_client = FakeRestClient({"201S06375": {"a": 1}})
    bus = FakeBus()
    poller = OptionChainPoller(rest_client, master, bus, series=["regular"])
    scheduler = FixedTickScheduler(tick_seconds=0.05)

    await scheduler.run_forever(poller.poll_once, max_iterations=3)

    assert rest_client.calls == ["201S06375"] * 3
    assert len(bus.published) == 3
