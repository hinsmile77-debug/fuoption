"""F-23 — 판단 뒤 관문(리스크→사이징→제출)을 매일 자동으로 센다 (2026-08-24 이상점 1-11).

`decision_funnel`이 **판단까지**를 세고, 이 축이 그 뒤를 센다. 새로 답하는 것은 건수가
아니라 **1계약까지의 거리**다 — 건수만으로는 손에 닿는 0과 몇 배 떨어진 0이 같아 보인다.
"""

from __future__ import annotations

from messiah.ops.integrity_report import _sizer_funnel_axis


def _raw(**over):
    base = {
        "risk_rejects": 0,
        "zero_qty": 0,
        "submitted": 0,
        "shortfall_ratios": [],
        "streak": None,
    }
    base.update(over)
    return base


def test_no_chain_activity_is_unmeasured_not_zero():
    """L18 — 사슬이 판단까지도 못 갔으면 0건이 아니라 **못 잼**이다."""
    assert _sizer_funnel_axis(_raw(), {"regime": 14}) is None
    assert _sizer_funnel_axis(None, {"pass": 3}) is None


def test_todays_shape_two_zero_qty_no_orders():
    """2026-08-24 실측 — 판단 통과 2건 전부 리스크 승인, 둘 다 0계약, 주문 0건."""
    axis = _sizer_funnel_axis(
        _raw(zero_qty=2, shortfall_ratios=[0.3488, 0.3652]), {"pass": 2, "score": 6}
    )
    assert axis is not None
    assert axis["cycles"] == 2
    assert axis["risk_rejects"] == 0
    assert axis["risk_approved"] == 2
    assert axis["zero_qty"] == 2
    assert axis["submitted"] == 0
    assert axis["shortfall_ratio_max"] == 0.3652
    assert axis["shortfall_ratio_p50"] == 0.3488
    assert axis["shortfall_samples"] == 2


def test_zero_qty_without_ratios_is_unmeasured_not_zero_distance():
    """F-23 이전 로그 — 0계약은 있는데 거리가 없다.

    0.0으로 채우면 "1계약에 하나도 못 닿았다"는 **거짓말**이 된다. 실제로는
    안 쟀을 뿐이다(L18).
    """
    axis = _sizer_funnel_axis(_raw(zero_qty=5), {"pass": 5})
    assert axis is not None
    assert axis["zero_qty"] == 5
    assert axis["shortfall_ratio_max"] is None
    assert axis["shortfall_ratio_p50"] is None
    assert axis["shortfall_samples"] == 0


def test_order_submitted_shows_in_the_axis():
    axis = _sizer_funnel_axis(_raw(submitted=1), {"pass": 1})
    assert axis is not None and axis["submitted"] == 1


def test_session_streak_is_carried_through():
    streak = {"zero_qty_cycles": 2, "sized_calls": 2, "shortfall_ratio_max": 0.3652}
    axis = _sizer_funnel_axis(_raw(zero_qty=2, streak=streak), {"pass": 2})
    assert axis is not None
    assert axis["session_streak"] == streak
