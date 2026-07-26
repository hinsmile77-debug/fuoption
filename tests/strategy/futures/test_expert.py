from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from messiah.core.messages import FeatureVector, Horizon
from messiah.core.timeutil import KST
from messiah.models.calibration import ProbabilityCalibrator
from messiah.strategy.futures.expert import (
    DEFAULT_ENSEMBLE_SIZE,
    PROTOTYPE_LGB_PARAMS,
    FeatureSetMismatchError,
    HorizonExpert,
    _ExpertMetadata,
)

_FEATURE_NAMES = ["px_ret_5", "px_mom_5", "px_rsi_5"]
_FEATURE_SET = "v-test"


def _synthetic_training_data() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    # feature0이 레이블과 강하게 상관되도록 구성 — 얕은 트리로도 분리 가능한 장난감 데이터.
    rows = []
    labels = []
    for label, base in ((-1, -5.0), (0, 0.0), (1, 5.0)):
        for i in range(5):
            rows.append([base + i * 0.01, base * 2, 50 + base])
            labels.append(label)
    x = np.array(rows, dtype=float)
    y = np.array(labels, dtype=int)
    weight = np.ones(len(labels), dtype=float)
    return x, y, weight


def _train_expert(**overrides) -> HorizonExpert:
    x, y, weight = _synthetic_training_data()
    kwargs = dict(
        horizon=Horizon.M5,
        feature_set=_FEATURE_SET,
        model_version="test-v1",
        feature_names=_FEATURE_NAMES,
        x=x,
        y=y,
        sample_weight=weight,
    )
    kwargs.update(overrides)
    return HorizonExpert.train(**kwargs)


def _feature_vector(values: dict, feature_set: str = _FEATURE_SET) -> FeatureVector:
    return FeatureVector(
        symbol="A05608",
        horizon=Horizon.M5,
        feature_set=feature_set,
        values=values,
        valid_until=datetime(2026, 7, 27, 9, 5, tzinfo=KST),
    )


def test_train_and_predict_produce_valid_probability_distribution():
    expert = _train_expert()
    fv = _feature_vector({"px_ret_5": 5.0, "px_mom_5": 10.0, "px_rsi_5": 55.0})

    view = expert.predict(fv)

    assert view.symbol == "A05608"
    assert view.horizon == Horizon.M5
    total = view.p_up + view.p_flat + view.p_down
    assert total == pytest.approx(1.0, abs=1e-6)
    assert 0.0 <= view.p_up <= 1.0
    assert 0.0 <= view.p_flat <= 1.0
    assert 0.0 <= view.p_down <= 1.0


def test_default_ensemble_size_is_five_members():
    expert = _train_expert()
    assert expert.n_members == DEFAULT_ENSEMBLE_SIZE == 5


def test_custom_n_members_and_base_seed():
    expert = _train_expert(n_members=2, base_seed=100)
    assert expert.n_members == 2


def test_ens_std_is_nonnegative_and_zero_for_single_member_ensemble():
    solo = _train_expert(n_members=1)
    fv = _feature_vector({"px_ret_5": 0.0, "px_mom_5": 0.0, "px_rsi_5": 50.0})
    view = solo.predict(fv)
    assert view.ens_std == 0.0  # 멤버 1개면 분산 정의상 0

    ensemble = _train_expert(n_members=5)
    view5 = ensemble.predict(fv)
    assert view5.ens_std >= 0.0


def test_ensemble_placeholders_still_documented():
    expert = _train_expert()
    fv = _feature_vector({"px_ret_5": 0.0, "px_mom_5": 0.0, "px_rsi_5": 50.0})
    view = expert.predict(fv)

    assert view.meta_passed is True  # Meta-Labeler는 별도 컴포넌트 — 항상 통과 표시
    assert view.model_version == "test-v1"
    assert view.valid_until == fv.valid_until  # FeatureVector의 값을 그대로 전달


def test_rejects_empty_boosters():
    with pytest.raises(ValueError):
        HorizonExpert(
            [],
            _ExpertMetadata(
                horizon="5m",
                feature_set=_FEATURE_SET,
                feature_names=_FEATURE_NAMES,
                model_version="v1",
            ),
        )


def test_feature_set_mismatch_raises_and_does_not_predict():
    expert = _train_expert()
    fv = _feature_vector({"px_ret_5": 0.0, "px_mom_5": 0.0, "px_rsi_5": 50.0}, feature_set="other")

    with pytest.raises(FeatureSetMismatchError):
        expert.predict(fv)


def test_feature_row_maps_missing_and_none_to_nan():
    row = HorizonExpert.feature_row(
        {"px_ret_5": 1.5, "px_mom_5": None}, ["px_ret_5", "px_mom_5", "px_rsi_5"]
    )
    assert row[0] == 1.5
    assert np.isnan(row[1])  # 명시적 None
    assert np.isnan(row[2])  # 키 자체가 없음


def test_top_features_returns_at_most_n_entries_sorted_descending():
    expert = _train_expert()
    top = expert.top_features(2)
    assert len(top) <= 2
    scores = [score for _, score in top]
    assert scores == sorted(scores, reverse=True)


def test_save_load_round_trip_preserves_predictions_and_metadata(tmp_path: Path):
    expert = _train_expert()
    fv = _feature_vector({"px_ret_5": -4.9, "px_mom_5": -10.0, "px_rsi_5": 45.0})
    before = expert.predict(fv)

    model_path = tmp_path / "5m_expert_v1.lgb"
    expert.save(model_path)
    reloaded = HorizonExpert.load(model_path)
    after = reloaded.predict(fv)

    assert reloaded.feature_names == expert.feature_names
    assert reloaded.feature_set == expert.feature_set
    assert reloaded.model_version == expert.model_version
    assert reloaded.horizon == expert.horizon
    assert reloaded.n_members == expert.n_members
    assert after.p_up == pytest.approx(before.p_up, abs=1e-9)
    assert after.p_flat == pytest.approx(before.p_flat, abs=1e-9)
    assert after.p_down == pytest.approx(before.p_down, abs=1e-9)


def test_save_load_round_trip_without_calibrator_has_none(tmp_path: Path):
    expert = _train_expert()
    expert.save(tmp_path / "no_cal.lgb")
    reloaded = HorizonExpert.load(tmp_path / "no_cal.lgb")
    assert reloaded.calibrator is None


def test_save_load_round_trip_with_calibrator_preserves_calibration(tmp_path: Path):
    probs = np.array([[0.7, 0.2, 0.1], [0.3, 0.3, 0.4], [0.1, 0.1, 0.8]] * 4)
    true_idx = np.array([0, 1, 2] * 4)
    calibrator = ProbabilityCalibrator.fit(probs, true_idx)

    expert = _train_expert()
    expert.set_calibrator(calibrator)
    fv = _feature_vector({"px_ret_5": 0.0, "px_mom_5": 0.0, "px_rsi_5": 50.0})
    before = expert.predict(fv)

    path = tmp_path / "with_cal.lgb"
    expert.save(path)
    reloaded = HorizonExpert.load(path)

    assert reloaded.calibrator is not None
    after = reloaded.predict(fv)
    assert after.p_up == pytest.approx(before.p_up, abs=1e-9)
    assert after.p_flat == pytest.approx(before.p_flat, abs=1e-9)
    assert after.p_down == pytest.approx(before.p_down, abs=1e-9)


def test_calibrator_changes_predicted_probabilities():
    expert = _train_expert()
    fv = _feature_vector({"px_ret_5": 0.0, "px_mom_5": 0.0, "px_rsi_5": 50.0})
    uncalibrated = expert.predict(fv)

    # 항상 p_down 쪽으로 강하게 밀어붙이는 인위적 교정기 — 값이 실제로 바뀌는지만 확인.
    probs = np.tile([0.99, 0.005, 0.005], (10, 1))
    true_idx = np.array([0] * 10)
    calibrator = ProbabilityCalibrator.fit(probs, true_idx)
    expert.set_calibrator(calibrator)

    calibrated = expert.predict(fv)
    assert (calibrated.p_up, calibrated.p_flat, calibrated.p_down) != (
        uncalibrated.p_up,
        uncalibrated.p_flat,
        uncalibrated.p_down,
    )


def test_set_calibrator_none_removes_calibration():
    probs = np.array([[0.7, 0.2, 0.1], [0.3, 0.3, 0.4], [0.1, 0.1, 0.8]] * 4)
    true_idx = np.array([0, 1, 2] * 4)
    calibrator = ProbabilityCalibrator.fit(probs, true_idx)

    expert = _train_expert()
    expert.set_calibrator(calibrator)
    assert expert.calibrator is not None
    expert.set_calibrator(None)
    assert expert.calibrator is None


def test_custom_params_override_prototype_defaults():
    custom = dict(PROTOTYPE_LGB_PARAMS, num_leaves=3)
    expert = _train_expert(params=custom)
    fv = _feature_vector({"px_ret_5": 0.0, "px_mom_5": 0.0, "px_rsi_5": 50.0})
    view = expert.predict(fv)
    assert view.p_up + view.p_flat + view.p_down == pytest.approx(1.0, abs=1e-6)
