import pytest
from messiah.models.metrics import (
    max_drawdown,
    multiclass_brier_score,
    negative_window_ratio,
    sharpe_ratio,
)

# ---------------------------------------------------------------- sharpe_ratio


def test_sharpe_ratio_hand_computed():
    # mean=0.03, pstdev([0.02,0.04])=0.01 → sharpe(periods=1)=3.0
    assert sharpe_ratio([0.02, 0.04], periods_per_year=1) == pytest.approx(3.0)


def test_sharpe_ratio_scales_with_sqrt_periods_per_year():
    base = sharpe_ratio([0.02, 0.04], periods_per_year=1)
    scaled = sharpe_ratio([0.02, 0.04], periods_per_year=4)
    assert scaled == pytest.approx(base * 2)  # sqrt(4)=2


def test_sharpe_ratio_zero_stdev_returns_zero():
    assert sharpe_ratio([0.01, 0.01, 0.01], periods_per_year=252) == 0.0


def test_sharpe_ratio_insufficient_samples_returns_zero():
    assert sharpe_ratio([], periods_per_year=252) == 0.0
    assert sharpe_ratio([0.01], periods_per_year=252) == 0.0


# ---------------------------------------------------------------- max_drawdown


def test_max_drawdown_hand_computed():
    # peak 추적: 100,110,110,110,120,120 / 최대낙폭은 마지막(120→80)=1/3
    curve = [100, 110, 90, 95, 120, 80]
    assert max_drawdown(curve) == pytest.approx(1 / 3)


def test_max_drawdown_monotonic_increase_is_zero():
    assert max_drawdown([100, 110, 120, 130]) == pytest.approx(0.0)


def test_max_drawdown_insufficient_samples_returns_zero():
    assert max_drawdown([100]) == 0.0
    assert max_drawdown([]) == 0.0


# ---------------------------------------------------------------- negative_window_ratio


def test_negative_window_ratio_hand_computed():
    assert negative_window_ratio([0.1, -0.2, 0.3, -0.1, -0.05]) == pytest.approx(0.6)


def test_negative_window_ratio_empty_is_zero():
    assert negative_window_ratio([]) == 0.0


def test_negative_window_ratio_all_positive_is_zero():
    assert negative_window_ratio([0.1, 0.2, 0.3]) == 0.0


# ---------------------------------------------------------------- multiclass_brier_score


def test_multiclass_brier_score_hand_computed():
    probs = [[0.7, 0.2, 0.1], [0.2, 0.2, 0.6]]
    true_idx = [0, 2]
    # sample1: 0.09+0.04+0.01=0.14 / sample2: 0.04+0.04+0.16=0.24 / mean=0.19
    assert multiclass_brier_score(probs, true_idx) == pytest.approx(0.19)


def test_multiclass_brier_score_perfect_prediction_is_zero():
    assert multiclass_brier_score([[1.0, 0.0, 0.0]], [0]) == pytest.approx(0.0)


def test_multiclass_brier_score_worst_case_is_two():
    assert multiclass_brier_score([[0.0, 0.0, 1.0]], [0]) == pytest.approx(2.0)


def test_multiclass_brier_score_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        multiclass_brier_score([[0.5, 0.5]], [0, 1])


def test_multiclass_brier_score_rejects_empty_input():
    with pytest.raises(ValueError):
        multiclass_brier_score([], [])
