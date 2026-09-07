"""OptionsAIService (신규, Ver 2.0 §9 W30~31) — `tests/strategy/futures/test_futures_service.py`
와 동일한 InProcessBus 배선 검증 스타일."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from messiah.core import logging as mlog
from messiah.core.messages import BarClosed, FuturesView, Horizon
from messiah.core.timeutil import KST
from messiah.simulator.inprocess_bus import InProcessBus
from messiah.strategy.options.service import OptionsAIService
from messiah.strategy.options.surface import fit_smile
from messiah.strategy.options.vol_metrics import IVHistory

_SYMBOL = "TEST"
_UNDERLYING = "KOSPI200"
_NOW = datetime(2026, 7, 30, 10, 5, tzinfo=KST)


def _smile(iv: float = 0.20):
    points = [(k, iv) for k in (300.0, 320.0, 340.0, 350.0, 360.0, 380.0, 400.0)]
    fit = fit_smile(350.0, dte=20, strike_iv_points=points)
    assert fit is not None
    return fit


def _futures_view(score: float) -> FuturesView:
    return FuturesView(
        symbol=_SYMBOL,
        score=score,
        agg_p_up=0.6,
        agg_p_down=0.4,
        uncertainty=0.1,
        dispersion=0.1,
        regime="TREND_UP",
        n_experts=1,
        valid_until=_NOW + timedelta(minutes=5),
    )


def _bar(horizon: Horizon = Horizon.M5) -> BarClosed:
    return BarClosed(
        symbol=_SYMBOL,
        horizon=horizon,
        bar_open_kst=_NOW,
        o_ticks=100,
        h_ticks=110,
        l_ticks=90,
        c_ticks=105,
        volume=10,
    )


async def _collect(bus: InProcessBus) -> list:
    published: list = []

    async def collector(msg):
        published.append(msg)

    await bus.subscribe(["intel.options"], collector)
    return published


class FakeEventCalendar:
    def __init__(self, *, is_expiry: bool) -> None:
        self._is_expiry = is_expiry

    def is_expiry_day(self, d) -> bool:
        return self._is_expiry


# ---------------------------------------------------------------- 미수신/미준비 가드


async def test_bar_before_any_futures_view_publishes_no_option():
    bus = InProcessBus()
    published = await _collect(bus)
    service = OptionsAIService(_SYMBOL, _UNDERLYING, lambda: _smile(), bus)

    await service.handle_bar(_bar())

    assert len(published) == 1
    assert published[0].no_option_reason == "Futures AI 방향 뷰 미수신"
    assert published[0].candidates == []


async def test_futures_view_with_no_smile_publishes_no_option():
    bus = InProcessBus()
    published = await _collect(bus)
    service = OptionsAIService(_SYMBOL, _UNDERLYING, lambda: None, bus)

    await service.handle_futures_view(_futures_view(0.5))

    assert published[-1].no_option_reason == "IV Surface 미준비"


async def test_first_call_has_insufficient_iv_history():
    bus = InProcessBus()
    published = await _collect(bus)
    service = OptionsAIService(_SYMBOL, _UNDERLYING, lambda: _smile(), bus, iv_history=IVHistory())

    await service.handle_futures_view(_futures_view(0.5))

    assert published[-1].no_option_reason == "IV Rank 이력 부족"


# ---------------------------------------------------------------- 정상 경로


async def test_second_call_with_enough_history_produces_candidates():
    bus = InProcessBus()
    published = await _collect(bus)
    service = OptionsAIService(_SYMBOL, _UNDERLYING, lambda: _smile(), bus, iv_history=IVHistory())

    await service.handle_futures_view(_futures_view(0.5))  # 이력 1개 → 미판정
    await service.handle_futures_view(_futures_view(0.5))  # 이력 2개(동일 IV) → rank=100

    assert published[-1].no_option_reason is None
    assert len(published[-1].candidates) >= 1
    assert published[-1].symbol == _SYMBOL
    assert published[-1].underlying == _UNDERLYING


async def test_m5_bar_also_triggers_publish_after_futures_view_seen():
    bus = InProcessBus()
    published = await _collect(bus)
    service = OptionsAIService(_SYMBOL, _UNDERLYING, lambda: _smile(), bus, iv_history=IVHistory())
    await service.handle_futures_view(_futures_view(0.5))
    await service.handle_futures_view(_futures_view(0.5))
    before = len(published)

    await service.handle_bar(_bar(Horizon.M5))

    assert len(published) == before + 1


async def test_m1_bar_ignored():
    bus = InProcessBus()
    published = await _collect(bus)
    service = OptionsAIService(_SYMBOL, _UNDERLYING, lambda: _smile(), bus, iv_history=IVHistory())
    await service.handle_futures_view(_futures_view(0.5))

    await service.handle_bar(_bar(Horizon.M1))

    assert len(published) == 1  # futures_view 발행분 하나뿐, bar는 무시됨


async def test_other_symbol_ignored():
    bus = InProcessBus()
    published = await _collect(bus)
    service = OptionsAIService(_SYMBOL, _UNDERLYING, lambda: _smile(), bus, iv_history=IVHistory())

    other = _futures_view(0.5).model_copy(update={"symbol": "OTHER"})
    await service.handle_futures_view(other)

    assert published == []


# ---------------------------------------------------------------- 안전규칙 전량 기각


async def test_expiry_day_rejects_all_candidates():
    bus = InProcessBus()
    published = await _collect(bus)
    service = OptionsAIService(
        _SYMBOL,
        _UNDERLYING,
        lambda: _smile(),
        bus,
        iv_history=IVHistory(),
        event_calendar=FakeEventCalendar(is_expiry=True),
    )
    await service.handle_futures_view(_futures_view(0.5))
    await service.handle_futures_view(_futures_view(0.5))  # 이력 2개 → 후보 생성됨

    assert published[-1].no_option_reason == "생성된 후보가 전부 안전규칙에서 기각됨"


# ---------------------------------------------------------------- run_forever 배선


async def test_run_forever_wires_subscriptions_end_to_end():
    bus = InProcessBus()
    published = await _collect(bus)
    service = OptionsAIService(_SYMBOL, _UNDERLYING, lambda: _smile(), bus, iv_history=IVHistory())
    await service.run_forever()

    await bus.publish("intel.futures", _futures_view(0.5))

    assert len(published) == 1
    assert published[0].no_option_reason == "IV Rank 이력 부족"


# ---------------------------------------------------------------- 무결정 사유 로깅 (F-93)


@pytest.fixture
def captured(monkeypatch):
    """`mlog.log` 호출을 그대로 받는다 — `tests/risk/test_sizer_shortfall.py`와 같은 방식."""
    records: list[tuple[str, str, dict]] = []

    def fake_log(tag, message="", **fields):
        records.append((tag, message, fields))

    monkeypatch.setattr(mlog, "log", fake_log)
    return records


def _no_candidate(records) -> list[tuple[str, str, dict]]:
    return [r for r in records if r[0] == "OptionsNoCandidate"]


async def test_no_option_paths_each_leave_a_tag(captured):
    """무결정 경로 셋이 각각 사유를 태그로 남긴다 — 2026-09-07 이상점 1-2.

    이 셋(방향 뷰 미수신 · IV Surface 미준비 · IV Rank 이력 부족)은 종전에 버스로만
    발행됐고, 발행된 뷰는 다음 발행이 덮는다. 그래서 09-07 장중 46사이클 중 "왜 안 샀나"가
    로그로 재구성되는 것이 최대 1건이었다.
    """
    bus = InProcessBus()
    await _collect(bus)
    await OptionsAIService(_SYMBOL, _UNDERLYING, lambda: _smile(), bus).handle_bar(_bar())
    await OptionsAIService(_SYMBOL, _UNDERLYING, lambda: None, bus).handle_futures_view(
        _futures_view(0.5)
    )
    await OptionsAIService(
        _SYMBOL, _UNDERLYING, lambda: _smile(), bus, iv_history=IVHistory()
    ).handle_futures_view(_futures_view(0.5))

    assert [fields["reason"] for _, _, fields in _no_candidate(captured)] == [
        "Futures AI 방향 뷰 미수신",
        "IV Surface 미준비",
        "IV Rank 이력 부족",
    ]
    assert all(fields["symbol"] == _SYMBOL for _, _, fields in _no_candidate(captured))


async def test_all_candidates_rejected_leaves_a_tag(captured):
    """넷째 경로 — 후보는 만들었는데 안전규칙이 전부 걸렀다. 위 셋과 고칠 곳이 다르다."""
    bus = InProcessBus()
    await _collect(bus)
    service = OptionsAIService(
        _SYMBOL,
        _UNDERLYING,
        lambda: _smile(),
        bus,
        iv_history=IVHistory(),
        event_calendar=FakeEventCalendar(is_expiry=True),
    )
    await service.handle_futures_view(_futures_view(0.5))
    await service.handle_futures_view(_futures_view(0.5))

    assert _no_candidate(captured)[-1][2]["reason"] == "생성된 후보가 전부 안전규칙에서 기각됨"


async def test_successful_publish_leaves_no_no_candidate_tag(captured):
    """후보가 나온 사이클엔 이 태그가 없다 — 있으면 분포 집계가 그만큼 부풀려진다."""
    bus = InProcessBus()
    published = await _collect(bus)
    service = OptionsAIService(_SYMBOL, _UNDERLYING, lambda: _smile(), bus, iv_history=IVHistory())

    await service.handle_futures_view(_futures_view(0.5))
    await service.handle_futures_view(_futures_view(0.5))

    assert published[-1].no_option_reason is None
    assert len(_no_candidate(captured)) == 1, "첫 사이클(이력 부족) 1건뿐"
