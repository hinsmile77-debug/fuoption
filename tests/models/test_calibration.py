import numpy as np
import pytest
from messiah.models.calibration import ConformalCalibrator, ProbabilityCalibrator

# ---------------------------------------------------------------- ProbabilityCalibrator


def test_calibrate_returns_probabilities_summing_to_one():
    probs = np.array([[0.7, 0.2, 0.1], [0.3, 0.3, 0.4], [0.1, 0.1, 0.8]] * 5)
    true_idx = np.array([0, 1, 2] * 5)
    calibrator = ProbabilityCalibrator.fit(probs, true_idx)

    calibrated = calibrator.calibrate(probs)

    assert calibrated.shape == probs.shape
    assert np.allclose(calibrated.sum(axis=1), 1.0, atol=1e-6)
    assert np.all(calibrated >= 0.0) and np.all(calibrated <= 1.0)


def test_calibrate_accepts_1d_single_sample_input():
    probs = np.array([[0.7, 0.2, 0.1], [0.3, 0.3, 0.4]] * 5)
    true_idx = np.array([0, 1] * 5)
    calibrator = ProbabilityCalibrator.fit(probs, true_idx)

    single = calibrator.calibrate(np.array([0.7, 0.2, 0.1]))

    assert single.shape == (3,)
    assert single.sum() == pytest.approx(1.0, abs=1e-6)


def test_calibrate_corrects_overconfidence_hand_computed():
    """class0 확률이 항상 0.9로 과신되지만 실제로는 절반만 맞는 경우 — Isotonic이
    입력 x=0.9(전부 동일값)를 pooled 평균(0.5)으로 눌러야 한다(PAVA가 동률 x를 묶어
    평균내는 성질, 손으로 검증 가능한 값)."""
    n = 10
    probs = np.tile([0.9, 0.05, 0.05], (n, 1))
    true_idx = np.array([0] * 5 + [1] * 5)  # class0이 실제로는 50%만 맞음

    calibrator = ProbabilityCalibrator.fit(probs, true_idx)
    calibrated = calibrator.calibrate(probs[:1])

    assert calibrated[0][0] == pytest.approx(0.5, abs=1e-6)


# ---------------------------------------------------------------- ConformalCalibrator


def test_nonconformity_scores_hand_computed():
    scores = ConformalCalibrator().nonconformity_scores([0.9, 0.2, 0.5], [1.0, 0.0, 1.0])
    assert scores == pytest.approx([0.1, 0.2, 0.5])


def test_nonconformity_scores_rejects_length_mismatch():
    with pytest.raises(ValueError):
        ConformalCalibrator().nonconformity_scores([0.9, 0.2], [1.0])


def test_quantile_width_hand_computed():
    scores = [round(0.1 * i, 1) for i in range(1, 11)]  # 0.1..1.0
    width = ConformalCalibrator(alpha=0.1).quantile_width(scores)
    assert width == pytest.approx(0.9)  # ceil(0.9*10)=9번째(1-indexed) 값


def test_quantile_width_empty_history_is_maximally_conservative():
    assert ConformalCalibrator(alpha=0.1).quantile_width([]) == 1.0


def test_interval_clips_to_valid_probability_bounds():
    calibrator = ConformalCalibrator(alpha=0.1)
    scores = [0.5] * 10  # width=0.5

    low = calibrator.interval(0.05, scores)
    assert low.lower == 0.0  # 0.05-0.5 < 0 → 0으로 클립
    assert low.upper == pytest.approx(0.55)

    high = calibrator.interval(0.95, scores)
    assert high.upper == 1.0  # 0.95+0.5 > 1 → 1로 클립
    assert high.lower == pytest.approx(0.45)


def test_rejects_invalid_alpha():
    with pytest.raises(ValueError):
        ConformalCalibrator(alpha=0.0)
    with pytest.raises(ValueError):
        ConformalCalibrator(alpha=1.0)
    with pytest.raises(ValueError):
        ConformalCalibrator(alpha=-0.1)
