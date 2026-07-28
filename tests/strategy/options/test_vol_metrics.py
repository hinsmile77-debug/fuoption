"""Vol Engine 파생 지표 (신규, Ver 2.0 §9 W27~29) — 전부 known-value 단위테스트."""

from __future__ import annotations

import math

import pytest
from messiah.strategy.options.vol_metrics import (
    IVHistory,
    iv_rv_spread,
    realized_vol,
    skew,
    term_structure,
)

# ---------------------------------------------------------------- skew/term_structure/iv_rv_spread


def test_skew_is_put_iv_minus_call_iv():
    assert skew(put_iv_25d=0.28, call_iv_25d=0.20) == pytest.approx(0.08)


def test_term_structure_is_near_minus_far():
    assert term_structure(near_month_atm_iv=0.25, far_month_atm_iv=0.20) == pytest.approx(0.05)


def test_iv_rv_spread_is_iv_minus_rv():
    assert iv_rv_spread(iv=0.22, rv=0.15) == pytest.approx(0.07)


# ---------------------------------------------------------------- realized_vol


def test_realized_vol_matches_hand_computed_value():
    # closes=[100,110,100] → log returns ±ln(1.1), mean=0, sample var(n-1=1)=2*ln(1.1)^2
    closes = [100.0, 110.0, 100.0]
    expected = math.sqrt(2) * abs(math.log(1.1))
    assert realized_vol(closes, annualization_factor=1.0) == pytest.approx(expected, abs=1e-6)


def test_realized_vol_scales_with_sqrt_of_annualization_factor():
    closes = [100.0, 110.0, 100.0]
    base = realized_vol(closes, annualization_factor=1.0)
    scaled = realized_vol(closes, annualization_factor=4.0)
    assert scaled == pytest.approx(base * 2.0, abs=1e-9)


def test_realized_vol_none_with_fewer_than_three_closes():
    assert realized_vol([100.0, 101.0]) is None
    assert realized_vol([100.0]) is None
    assert realized_vol([]) is None


# ---------------------------------------------------------------- IVHistory


def test_iv_history_rank_none_with_fewer_than_two_values():
    history = IVHistory()
    assert history.rank(0.2) is None
    history.add(0.2)
    assert history.rank(0.2) is None


def test_iv_history_rank_percentile_matches_hand_count():
    history = IVHistory()
    for v in (0.1, 0.2, 0.3, 0.4, 0.5):
        history.add(v)
    assert history.rank(0.35) == pytest.approx(60.0)  # 0.1/0.2/0.3 <= 0.35 → 3/5
    assert history.rank(0.05) == pytest.approx(0.0)
    assert history.rank(0.5) == pytest.approx(100.0)


def test_iv_history_respects_maxlen_and_drops_oldest():
    history = IVHistory(maxlen=3)
    for v in (0.9, 0.9, 0.9, 0.1, 0.1):  # 앞의 0.9 두 개는 밀려나야 함
        history.add(v)
    assert len(history) == 3
    # maxlen=3이라 남은 값은 마지막 3개(0.9, 0.1, 0.1) — 0.1 이하는 2/3.
    assert history.rank(0.1) == pytest.approx(200.0 / 3.0)
