"""옵션체인 시세호가(OP) REST 폴러 (신규, Ver 2.0 §9 W27~29) — InvestorFlowPoller
(`tests/data/test_investor_flow_poller.py`)와 동일 패턴으로 검증."""

from __future__ import annotations

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
    def __init__(self, chain: list[OptionLeg]) -> None:
        self._chain = chain
        self.calls: list[tuple[str, str]] = []

    def nearest_expiry_chain(self, underlying: str, *, series: str = "regular") -> list[OptionLeg]:
        self.calls.append((underlying, series))
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
    poller = OptionChainPoller(rest_client, master, bus)

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
    poller = OptionChainPoller(rest_client, master, FakeBus())

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
    poller = OptionChainPoller(rest_client, master, bus)

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
    poller = OptionChainPoller(rest_client, master, FailingBus())

    await poller.poll_once()  # 발행 실패에도 예외 전파 없이 조용히 로깅만

    assert any(tag == "OptionChainPollError" for tag, _ in logged)


async def test_scheduler_drives_poller_poll_once_repeatedly():
    master = FakeMaster([_CALL_LEG])
    rest_client = FakeRestClient({"201S06375": {"a": 1}})
    bus = FakeBus()
    poller = OptionChainPoller(rest_client, master, bus)
    scheduler = FixedTickScheduler(tick_seconds=0.05)

    await scheduler.run_forever(poller.poll_once, max_iterations=3)

    assert rest_client.calls == ["201S06375"] * 3
    assert len(bus.published) == 3
