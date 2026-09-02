"""F-26 — 다리 하나가 빠지면 **그 자리에서** 운다 (2026-08-24 이상점 1-14).

2026-08-24 09:31에 수급 사이클이 3다리 중 2다리만 남겼다. **그 순간의 로그에는 아무
경보도 없었다** — 개별 실패 태그(`InvestorFlowPollError`)가 하나도 안 떴기 때문이다.
결손은 여섯 시간 뒤 장후 커버리지 집계에서야 드러났고, 수급은 소급 경로가 없어 그때는
이미 영구 소실이었다. 12거래일에 세 번째다.
"""

from __future__ import annotations

import logging

import pytest

from messiah.core.logging import TAG_LEVELS
from messiah.data.investor_flow_poller import InvestorFlowPoller
from tests.data.test_investor_flow_poller import FakeBus, FakeRestClient, _no_sleep


@pytest.fixture
def shortfalls(monkeypatch):
    """모든 로그를 잡고 태그로 거른다.

    폴러와 재시도 계층이 **같은 모듈 객체**(`messiah.core.logging`)를 참조하므로
    한쪽만 갈아끼울 수 없다 — 두 번 patch하면 뒤엣것이 앞엣것을 덮는다.
    """
    seen: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        "messiah.core.logging.log",
        lambda tag, msg="", **f: seen.append((tag, msg, f)),
    )
    return seen


@pytest.mark.asyncio
async def test_full_cycle_is_silent(shortfalls):
    """정상일에 매 사이클 한 줄이 늘면 이 태그가 로그를 못 읽게 만든다.

    `OptionChainPollEmpty`가 2026-08-07에 WARNING이라 22번 울고 강등된 전례를 따른다.
    """
    poller = InvestorFlowPoller(
        FakeRestClient({"F001": {"a": 1}, "OC01": {"b": 2}}),
        "K2I",
        ["F001", "OC01"],
        FakeBus(),
    )
    await poller.poll_once()
    assert [t for t, _, _ in shortfalls if t == "InvestorFlowLegShortfall"] == []


@pytest.mark.asyncio
async def test_missing_leg_rings_in_the_same_cycle(shortfalls):
    """2026-08-24 09:31의 형태 — 3다리 중 2다리."""
    rest_client = FakeRestClient({"F001": {"a": 1}, "OC01": {"b": 2}}, fail_for={"OP01"})
    poller = InvestorFlowPoller(
        rest_client, "K2I", ["F001", "OC01", "OP01"], FakeBus(), sleep=_no_sleep
    )

    await poller.poll_once()

    hits = [f for t, _, f in shortfalls if t == "InvestorFlowLegShortfall"]
    assert len(hits) == 1
    fields = hits[0]
    assert fields["market_code"] == "K2I"
    assert fields["expected_legs"] == 3
    assert fields["got_legs"] == 2
    assert fields["missing_sectors"] == ["OP01"]
    assert fields["cause"] == "retry_exhausted"
    assert "cycle_kst" in fields


@pytest.mark.asyncio
async def test_publish_failure_is_its_own_cause(shortfalls):
    class BrokenBus(FakeBus):
        async def publish(self, topic, msg):
            raise RuntimeError("bus down")

    poller = InvestorFlowPoller(
        FakeRestClient({"F001": {"a": 1}}), "K2I", ["F001"], BrokenBus(), sleep=_no_sleep
    )
    await poller.poll_once()

    fields = [f for t, _, f in shortfalls if t == "InvestorFlowLegShortfall"][0]
    assert fields["cause"] == "publish_failed"
    assert fields["got_legs"] == 0


@pytest.mark.asyncio
async def test_mixed_causes_are_labelled_mixed(shortfalls):
    class OneBadPublish(FakeBus):
        async def publish(self, topic, msg):
            if msg.sector_code == "F001":
                raise RuntimeError("bus down")
            await super().publish(topic, msg)

    rest_client = FakeRestClient({"F001": {"a": 1}, "OC01": {"b": 2}}, fail_for={"OP01"})
    poller = InvestorFlowPoller(
        rest_client, "K2I", ["F001", "OC01", "OP01"], OneBadPublish(), sleep=_no_sleep
    )
    await poller.poll_once()

    fields = [f for t, _, f in shortfalls if t == "InvestorFlowLegShortfall"][0]
    assert fields["cause"] == "mixed"
    assert fields["causes_by_sector"] == {"F001": "publish_failed", "OP01": "retry_exhausted"}
    assert fields["got_legs"] == 1


def test_tags_are_registered_with_single_severity():
    """R6 — 미등록 태그는 `ValueError`. 두 폴러가 같은 병을 앓았으므로 둘 다 있어야 한다."""
    assert TAG_LEVELS["InvestorFlowLegShortfall"] == logging.WARNING
    assert TAG_LEVELS["OptionChainLegShortfall"] == logging.WARNING


@pytest.mark.asyncio
async def test_option_chain_shortfall_rings_too(monkeypatch):
    """2026-08-10 14:30 `option_chain/regular` 41/42가 같은 병이었다.

    옵션 체인도 소급 조회 경로가 없다 — 결손이 장후 집계에서야 드러나면 이미 늦다.
    """
    from messiah.broker.kis.symbol_master import OptionLeg
    from messiah.data import option_chain_poller as ocp

    seen: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        "messiah.core.logging.log", lambda tag, msg="", **f: seen.append((tag, msg, f))
    )

    legs = [
        OptionLeg(option_type="C", strike=float(300 + i), symbol=f"S{i}", month_label="202609")
        for i in range(3)
    ]

    class _Poller(ocp.OptionChainPoller):
        def __init__(self):  # 폴러 조립 전체를 흉내 내지 않는다 — 세는 자리만 본다.
            self._underlying = "K200"
            self._series = "regular"
            self._strike_window = 1
            self._listed = None
            self._empty_streak = 0
            self._not_listed_announced = False
            # F-73 기준가 신선도 — 이 테스트가 보는 자리(결손 집계)와 무관하므로 "안 잰다"
            # 상태 그대로 둔다. 제공자가 없으면 나이가 `None`이고 스테일 판정도 없다.
            self._reference_price_as_of = None
            self._stale_spot_seconds = ocp.STALE_SPOT_SECONDS
            self._stale_spot_since = None
            self._stale_spot_cycles = 0
            self._stale_spot_max_age = 0.0

        def _reference_price(self):
            return 350.0

        async def _poll_one(self, leg, **_freshness):
            return None if leg.symbol != "S1" else "retry_exhausted"

    poller = _Poller()
    monkeypatch.setattr(poller, "_reference_price", lambda: 350.0)
    monkeypatch.setattr(ocp, "select_atm_window", lambda chain, spot, window: legs)

    class _Master:
        def nearest_expiry_chain(self, underlying, series):
            return legs

    poller._master = _Master()

    await poller.poll_once()

    hits = [f for t, _, f in seen if t == "OptionChainLegShortfall"]
    assert len(hits) == 1
    assert hits[0]["expected_legs"] == 3
    assert hits[0]["got_legs"] == 2
    assert hits[0]["missing_symbols"] == ["S1"]
    assert hits[0]["cause"] == "retry_exhausted"


@pytest.mark.asyncio
async def test_unexpected_failure_is_isolated_and_labelled_unknown(monkeypatch):
    """L22 — 다리 하나의 예상 밖 실패가 남은 업종을 막으면 안 된다.

    이 경로가 「알려진 실패 경로를 하나도 안 탄 결손」의 자리다. 종전에는 예외가
    그대로 밖으로 나가 남은 업종이 통째로 안 돌았고, 태그도 없었다.
    """
    seen: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        "messiah.core.logging.log", lambda tag, msg="", **f: seen.append((tag, msg, f))
    )
    rest_client = FakeRestClient({"F001": {"a": 1}, "OC01": {"b": 2}, "OP01": {"c": 3}})
    bus = FakeBus()
    poller = InvestorFlowPoller(rest_client, "K2I", ["F001", "OC01", "OP01"], bus)

    original = poller._poll_one

    async def _boom(sector_code):
        if sector_code == "OC01":
            raise RuntimeError("스냅샷 조립 실패")
        return await original(sector_code)

    monkeypatch.setattr(poller, "_poll_one", _boom)
    await poller.poll_once()

    # 남은 업종은 계속 돌았다 — 예외가 사이클을 죽이지 않는다.
    assert [s.sector_code for _, s in bus.published] == ["F001", "OP01"]
    fields = [f for t, _, f in seen if t == "InvestorFlowLegShortfall"][0]
    assert fields["cause"] == "unknown"
    assert fields["missing_sectors"] == ["OC01"]
