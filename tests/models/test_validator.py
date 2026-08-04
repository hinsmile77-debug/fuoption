from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from messiah.core.messages import FeatureVector, Horizon
from messiah.core.timeutil import KST
from messiah.models.validator import GateResult, ValidationReport, Validator, ValidatorConfig
from messiah.strategy.futures.expert import HorizonExpert

_FEATURE_NAMES = ["dominant", "weak"]
_FEATURE_SET = "v-validator-test"


def _tiny_expert() -> HorizonExpert:
    """'dominant' Feature 하나가 레이블을 거의 다 설명하도록 구성(단일 Feature 의존도
    관문을 결정론적으로 실험하기 위한 장난감 데이터 — 'weak'는 레이블과 거의 무관해 두
    Feature의 중요도가 자연히 크게 갈린다). 두 관문 테스트(FAIL/PASS)는 이 동일한 전문가에
    임계값(ValidatorConfig)만 다르게 줘서 경계 로직을 검증한다 — LightGBM의 그리디 분할
    특성상 "두 Feature가 정확히 균형" 같은 값을 안정적으로 재현하는 것보다 훨씬 결정론적."""
    rows: list[list[float]] = []
    labels: list[int] = []
    for label, base in ((-1, -5.0), (0, 0.0), (1, 5.0)):
        for i in range(6):
            weak = (i % 2) * 0.01  # 레이블과 거의 무관
            rows.append([base + i * 0.001, weak])
            labels.append(label)
    x = np.array(rows, dtype=float)
    y = np.array(labels, dtype=int)
    weight = np.ones(len(labels), dtype=float)
    return HorizonExpert.train(
        horizon=Horizon.M5,
        feature_set=_FEATURE_SET,
        model_version="validator-test-v1",
        feature_names=_FEATURE_NAMES,
        x=x,
        y=y,
        sample_weight=weight,
    )


def _sample_feature_vector() -> FeatureVector:
    return FeatureVector(
        symbol="A05608",
        horizon=Horizon.M5,
        feature_set=_FEATURE_SET,
        values={"dominant": 5.0, "weak": 0.0},
        valid_until=datetime(2026, 7, 27, 9, 5, tzinfo=KST),
    )


# ---------------------------------------------------------------- GateResult/ValidationReport


def test_validation_report_passed_requires_all_gates_true():
    report = ValidationReport(
        gates=[
            GateResult("a", True, 1.0, 0.5),
            GateResult("b", True, 1.0, 0.5),
        ]
    )
    assert report.passed is True
    assert report.failed_gates() == []


def test_validation_report_passed_false_if_any_gate_fails():
    report = ValidationReport(
        gates=[
            GateResult("a", True, 1.0, 0.5),
            GateResult("b", False, 0.1, 0.5),
        ]
    )
    assert report.passed is False
    assert [g.name for g in report.failed_gates()] == ["b"]


# ---------------------------------------------------------------- validate_performance


def test_validate_performance_all_pass_with_good_synthetic_series():
    validator = Validator()
    gates = validator.validate_performance(
        daily_returns=[0.02, 0.04] * 30,  # sharpe=3.0(연율화 시 더 큼) > 1.0
        periods_per_year=252,
        equity_curve=[100, 101, 102, 103],  # 단조 증가 → MDD=0
        window_returns=[0.1, 0.2, 0.3, -0.05],  # 1/4=0.25 < 0.4
    )
    names = {g.name: g for g in gates}
    assert names["cost_adjusted_sharpe"].passed is True
    assert names["max_drawdown"].passed is True
    assert names["negative_window_ratio"].passed is True


def test_validate_performance_fails_below_sharpe_threshold():
    validator = Validator()
    gates = validator.validate_performance(
        daily_returns=[0.0, 0.0, 0.0],  # stdev=0 → sharpe=0.0
        periods_per_year=252,
        equity_curve=[100, 100],
        window_returns=[0.0],
    )
    sharpe_gate = next(g for g in gates if g.name == "cost_adjusted_sharpe")
    assert sharpe_gate.passed is False
    assert sharpe_gate.value == 0.0


def test_validate_performance_fails_when_drawdown_exceeds_limit():
    validator = Validator(ValidatorConfig(max_drawdown_limit=0.1))
    gates = validator.validate_performance(
        daily_returns=[0.02, 0.04] * 10,
        periods_per_year=252,
        equity_curve=[100, 50],  # 50% 낙폭 > 10% 한도
        window_returns=[0.1],
    )
    mdd_gate = next(g for g in gates if g.name == "max_drawdown")
    assert mdd_gate.passed is False
    assert mdd_gate.value == pytest.approx(0.5)


def test_validate_performance_fails_when_negative_window_ratio_too_high():
    validator = Validator()
    gates = validator.validate_performance(
        daily_returns=[0.02, 0.04] * 10,
        periods_per_year=252,
        equity_curve=[100, 101],
        window_returns=[-0.1, -0.2, -0.3, 0.1],  # 3/4=0.75 > 0.4
    )
    gate = next(g for g in gates if g.name == "negative_window_ratio")
    assert gate.passed is False
    assert gate.value == pytest.approx(0.75)


# ---------------------------------------------------------------- validate_calibration


def test_validate_calibration_passes_for_well_calibrated_predictions():
    validator = Validator()
    gate = validator.validate_calibration([[0.9, 0.05, 0.05]] * 10, [0] * 10)
    assert gate.passed is True


def test_validate_calibration_fails_for_confidently_wrong_predictions():
    validator = Validator()
    gate = validator.validate_calibration([[0.0, 0.0, 1.0]] * 10, [0] * 10)  # 항상 확신에 차서 틀림
    assert gate.passed is False
    assert gate.value == pytest.approx(2.0)


# ---------------------------------------------------------------- validate_feature_dependency


def test_validate_feature_dependency_fails_when_single_feature_dominates():
    expert = _tiny_expert()
    validator = Validator()  # 기본 임계값 0.4
    gate = validator.validate_feature_dependency(expert)
    assert gate.value > 0.4
    assert gate.passed is False
    assert "dominant" in gate.detail


def test_validate_feature_dependency_passes_when_threshold_loosened():
    """같은 전문가(단일 Feature 의존도가 높음)라도 임계값을 그 값 이상으로 완화하면
    통과한다 — 경계 비교 로직 자체(<=)를 검증(균형 잡힌 장난감 데이터를 LightGBM의
    그리디 분할로 안정적으로 재현하기보다 임계값을 바꿔 경계를 넘나드는 쪽이 결정론적)."""
    expert = _tiny_expert()
    validator = Validator(ValidatorConfig(max_feature_importance_share=1.0))
    gate = validator.validate_feature_dependency(expert)
    assert gate.passed is True


# ---------------------------------------------------------------- validate_latency


def test_validate_latency_passes_with_generous_budget():
    expert = _tiny_expert()
    validator = Validator(ValidatorConfig(latency_budget_ms=1000.0))
    gate = validator.validate_latency(expert, _sample_feature_vector(), n_calls=20)
    assert gate.passed is True
    assert gate.value > 0.0


def test_validate_latency_fails_with_impossible_budget():
    expert = _tiny_expert()
    validator = Validator(ValidatorConfig(latency_budget_ms=1e-9))
    gate = validator.validate_latency(expert, _sample_feature_vector(), n_calls=5)
    assert gate.passed is False


# ---------------------------------------------------------------- validate_serialization


def test_validate_serialization_round_trip_passes(tmp_path: Path):
    expert = _tiny_expert()
    validator = Validator()
    gate = validator.validate_serialization(expert, _sample_feature_vector(), tmp_path)
    assert gate.passed is True


# ---------------------------------------------------------------- validate_all


def test_validate_all_assembles_seven_gates_and_aggregates_pass(tmp_path: Path):
    expert = _tiny_expert()
    validator = Validator(ValidatorConfig(max_feature_importance_share=1.0))

    report = validator.validate_all(
        expert=expert,
        sample_feature_vector=_sample_feature_vector(),
        tmp_path=tmp_path,
        daily_returns=[0.02, 0.04] * 30,
        periods_per_year=252,
        equity_curve=[100, 101, 102, 103],
        window_returns=[0.1, 0.2, 0.3, -0.05],
        calibration_probs=[[0.9, 0.05, 0.05]] * 10,
        calibration_true_idx=[0] * 10,
    )

    assert len(report.gates) == 7
    assert report.passed is True
