"""ATM 기준가의 **나이**를 발행에 실어 보낸다 — F-73 (2026-09-02).

여기서 지키려는 것은 08-28·08-31에 실제로 벌어진 일이다: 08:22~08:45 동안 전 거래일 종가
시드로 ATM 창을 정해 462·420다리가 +3.12% 어긋난 창에서 나갔는데, 로그도 무결성 리포트도
완전히 조용했다(K-5 판정 — 무결성 리포트는 이 구간을 안 잡는다). 커버리지는 그날 100%였다.

**이 계측은 아무것도 막지 않는다.** 막으면 장전 옵션이 통째로 비고 옵션 스냅샷은 소급 조회
경로가 없다 — 그래서 「판정 불변」 테스트가 이 파일의 마지막 자리에 있다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

# 페이크 3종과 체인 생성기는 같은 디렉터리의 폴러 테스트가 정본이다 — 여기서 다시
# 만들면 두 벌이 갈라진다(`FakeRestClient`가 주는 `raw` 모양이 특히).
from test_option_chain_poller import FakeBus, FakeMaster, FakeRestClient, _chain

from messiah.core.timeutil import now_kst
from messiah.data.option_chain_poller import STALE_SPOT_SECONDS, OptionChainPoller


def _poller(bus, *, as_of_source=None, chain=None):
    return OptionChainPoller(
        FakeRestClient(),
        FakeMaster(chain if chain is not None else _chain([100.0, 102.5, 105.0])),
        bus,
        series="regular",
        reference_price=lambda: 102.5,
        reference_price_as_of=as_of_source,
        strike_window=1,
    )


def _tags(records, tag):
    return [fields for name, _msg, fields in records if name == tag]


@pytest.fixture
def logged(monkeypatch):
    seen: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        "messiah.core.logging.log", lambda tag, msg="", **f: seen.append((tag, msg, f))
    )
    return seen


@pytest.mark.asyncio
async def test_snapshot_carries_the_age_of_the_spot_that_chose_the_window(logged):
    """스냅샷 하나하나가 「이 창을 정한 기준가는 몇 초 전 값인가」를 들고 나간다."""
    bus = FakeBus()
    as_of = now_kst() - timedelta(seconds=5)

    await _poller(bus, as_of_source=lambda: as_of).poll_once()

    assert bus.published, "발행이 있어야 이 축을 볼 수 있다"
    for _topic, snapshot in bus.published:
        assert snapshot.spot_as_of == as_of
        assert 4.0 <= snapshot.spot_age_seconds <= 60.0
    # 사이클 요약에도 병기된다 — 하루를 되짚을 때 사람이 먼저 여는 것은 로그다.
    polled = _tags(logged, "OptionChainPolled")
    assert len(polled) == 1
    assert polled[0]["spot_as_of"] == as_of.isoformat()
    assert polled[0]["spot_age_seconds"] is not None


@pytest.mark.asyncio
async def test_a_spot_of_unknown_age_is_not_recorded_as_fresh(logged):
    """제공자가 없으면 **모른다**로 남는다 — 0초로 접으면 그날 전부가 거짓 통과다(L18)."""
    bus = FakeBus()

    await _poller(bus, as_of_source=None).poll_once()

    for _topic, snapshot in bus.published:
        assert snapshot.spot_as_of is None
        assert snapshot.spot_age_seconds is None
    assert _tags(logged, "OptionChainStaleSpot") == []
    assert _tags(logged, "OptionChainPolled")[0]["spot_age_seconds"] is None


@pytest.mark.asyncio
async def test_naive_as_of_is_refused_rather_than_compared(logged):
    """naive datetime은 비교 자체가 예외다(R3) — 사이클을 죽이지 않고 「못 쟀다」로 접는다."""
    bus = FakeBus()

    naive = datetime(2026, 9, 2, 8, 30)  # noqa: DTZ001 — 의도적 naive

    await _poller(bus, as_of_source=lambda: naive).poll_once()

    assert len(bus.published) == 6  # 3행사가 × 콜/풋 — 발행은 그대로다
    assert bus.published[0][1].spot_age_seconds is None
    assert _tags(logged, "OptionChainStaleSpot") == []


@pytest.mark.asyncio
async def test_a_stale_episode_rings_once_at_its_start_and_once_at_its_end(logged):
    """장전 22분이 10~11줄이 되면 아무도 안 읽는다 — 시작 1건 · 해소 1건 (F-73⑤).

    `OptionChainPollEmpty`가 2026-08-07에 22번 울고 DEBUG로 강등된 전례를 안 밟는다.
    """
    bus = FakeBus()
    stale_at = now_kst() - timedelta(hours=17)  # 전 거래일 15:34봉 시드의 실제 나이
    fresh_at = now_kst()
    ages = [stale_at] * 10 + [fresh_at]
    poller = _poller(bus, as_of_source=lambda: ages.pop(0))

    for _ in range(11):
        await poller.poll_once()

    opened = _tags(logged, "OptionChainStaleSpot")
    resolved = _tags(logged, "OptionChainStaleSpotResolved")
    assert len(opened) == 1, "사이클마다 울면 안 된다"
    assert opened[0]["spot_as_of"] == stale_at.isoformat()
    assert opened[0]["threshold_seconds"] == STALE_SPOT_SECONDS
    assert len(resolved) == 1
    assert resolved[0]["cycles"] == 10
    assert resolved[0]["max_age_seconds"] >= 17 * 3600 - 60


@pytest.mark.asyncio
async def test_a_fresh_spot_never_rings(logged):
    """정상 장중(미니선물 근월물 틱, 초 단위 갱신)에서는 이 태그가 아예 안 나온다."""
    bus = FakeBus()
    poller = _poller(bus, as_of_source=lambda: now_kst() - timedelta(seconds=3))

    for _ in range(5):
        await poller.poll_once()

    assert _tags(logged, "OptionChainStaleSpot") == []
    assert _tags(logged, "OptionChainStaleSpotResolved") == []


@pytest.mark.asyncio
async def test_staleness_changes_nothing_about_what_gets_published(logged):
    """**판정 불변** — 이 계측은 발행을 막지도 창을 바꾸지도 않는다.

    막으면 장전 옵션이 통째로 빈다. 훗날 이 값으로 무언가를 차단하게 되면 그때는 R18의
    섀도 20거래일 대상이고, 이 테스트가 그 경계선이다.
    """
    fresh_bus, stale_bus = FakeBus(), FakeBus()

    await _poller(fresh_bus, as_of_source=lambda: now_kst()).poll_once()
    await _poller(stale_bus, as_of_source=lambda: now_kst() - timedelta(hours=17)).poll_once()

    def identity(bus):
        return [
            (topic, snap.symbol, snap.strike, snap.option_type, snap.series, snap.raw)
            for topic, snap in bus.published
        ]

    assert identity(fresh_bus) == identity(stale_bus)
    assert len(fresh_bus.published) == 6
