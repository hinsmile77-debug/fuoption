"""F-23 — 0계약이 1계약까지 얼마나 모자랐는지를 잰다 (2026-08-24 이상점 1-11).

건수는 종전에도 있었다. 그 건수만으로는 **문턱이 손에 닿는 거리인지 몇 배 떨어져
있는지**를 가를 수 없었고, 그 구별의 부재가 18거래일 연속 주문 0건을 "정상 동작"으로
읽히게 했다.
"""

from decimal import Decimal

import pytest

from messiah.core import logging as mlog
from messiah.core.messages import DecisionIntent, Side
from messiah.risk.sizer import PositionSizer, SizerConfig

_SYMBOL = "A05609"
_EQUITY = Decimal("50000000")
_TICK = Decimal("0.02")


@pytest.fixture
def captured(monkeypatch):
    records: list[tuple[str, str, dict]] = []

    def fake_log(tag, message="", **fields):
        records.append((tag, message, fields))

    monkeypatch.setattr(mlog, "log", fake_log)
    return records


def _intent(confidence=0.1802385621843883, uncertainty=0.007884453774212702):
    return DecisionIntent(
        symbol=_SYMBOL, side=Side.SHORT, confidence=confidence, uncertainty=uncertainty
    )


def test_zero_qty_log_carries_the_distance_to_one_contract(captured):
    """2026-08-24 10:30 실측 재현 — shortfall_ratio 0.349 · edge_needed 0.3974."""
    qty = PositionSizer().size(
        intent=_intent(),
        equity=_EQUITY,
        tick_size=_TICK,
        stop_distance_ticks=98.57142857142857,
        edge=0.13861943606422475,
        edge_source="directional",
    )
    assert qty == 0
    tag, _, fields = captured[-1]
    assert tag == "SizerZeroQty"
    assert fields["shortfall_ratio"] == pytest.approx(0.349, abs=1e-3)
    assert fields["edge_needed_for_min_qty"] == pytest.approx(0.3974, abs=1e-3)
    assert fields["edge_source"] == "directional"
    # vol_target_qty·kelly_scaled도 같은 줄에 있어야 한다 — 셋 중 하나만 있으면
    # "왜 그 거리인가"를 되짚을 수 없다.
    assert fields["vol_target_qty"] == pytest.approx(10.1449, abs=1e-3)
    assert fields["kelly_scaled"] == pytest.approx(0.13861943606422475 * 0.25, abs=1e-6)


def test_second_cycle_distance(captured):
    """11:00 실측 — shortfall_ratio 0.365."""
    PositionSizer().size(
        intent=_intent(confidence=0.22074659009332376, uncertainty=0.008542502450421928),
        equity=_EQUITY,
        tick_size=_TICK,
        stop_distance_ticks=110.35714285714286,
        edge=0.16259870782483848,
        edge_source="directional",
    )
    assert captured[-1][2]["shortfall_ratio"] == pytest.approx(0.365, abs=1e-3)


def test_session_summary_is_silent_when_nothing_was_folded(captured):
    """0계약이 0건인 날은 아무것도 안 낸다 — 안 그러면 늑대소년이 된다."""
    sizer = PositionSizer()
    assert sizer.log_session_summary() is None
    assert not [r for r in captured if r[0] == "SizerZeroQtyStreak"]


def test_session_summary_reports_streak_and_best_reach(captured):
    sizer = PositionSizer()
    for edge, atr in ((0.13861943606422475, 98.57142857142857), (0.16259870782483848, 110.35714)):
        sizer.size(
            intent=_intent(),
            equity=_EQUITY,
            tick_size=_TICK,
            stop_distance_ticks=atr,
            edge=edge,
            edge_source="directional",
        )
    payload = sizer.log_session_summary()
    assert payload is not None
    assert payload["zero_qty_cycles"] == 2
    assert payload["sized_calls"] == 2
    streak = [r for r in captured if r[0] == "SizerZeroQtyStreak"]
    assert len(streak) == 1
    assert streak[0][2]["shortfall_ratio_max"] == pytest.approx(payload["shortfall_ratio_max"])


def test_streak_tag_is_registered():
    """R6 — 미등록 태그는 `ValueError`. 등록 누락이면 세션 종료가 터진다."""
    from messiah.core.logging import TAG_LEVELS

    assert "SizerZeroQtyStreak" in TAG_LEVELS


def test_min_qty_of_two_scales_the_ratio(captured):
    """`shortfall_ratio`는 `min_qty` 대비 비율이다 — 상수 1을 박아 두지 않았다."""
    cfg = SizerConfig(min_qty=2)
    PositionSizer(cfg).size(
        intent=_intent(),
        equity=_EQUITY,
        tick_size=_TICK,
        stop_distance_ticks=98.57142857142857,
        edge=0.13861943606422475,
        edge_source="directional",
    )
    fields = captured[-1][2]
    assert fields["min_qty"] == 2
    assert fields["shortfall_ratio"] == pytest.approx(0.349 / 2, abs=1e-3)
