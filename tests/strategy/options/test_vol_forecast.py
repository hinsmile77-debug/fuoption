"""Vol Forecaster HAR-RV 기준모델 (신규, Ver 2.0 §9 W27~29).

핵심 검증: 알려진 계수로 "정확히" 생성한(잡음 없는) HAR-RV 시계열을 `fit_har_rv()`에 먹이면
같은 계수를 복원해야 한다 — 데이터가 그 선형관계로 완벽히 설명되므로 최소자승 잔차가 0에
수렴해야 한다는 강한 known-value 테스트."""

from __future__ import annotations

import math

import pytest

from messiah.strategy.options.vol_forecast import HARRVModel, fit_har_rv

_TRUE_BETA0 = 0.02
_TRUE_BETA_D = 0.3
_TRUE_BETA_W = 0.4
_TRUE_BETA_M = 0.25
_WEEKLY = 5
_MONTHLY = 22


def _generate_noiseless_har_series(n: int, *, seed_len: int = _MONTHLY) -> list[float]:
    """앞 seed_len개는 결정론적 시드값(사인파, 재현 가능), 이후는 참 HAR-RV 관계식으로
    재귀 생성 — `fit_har_rv()`가 이 관계를 정확히 복원할 수 있는지 검증하는 재료."""
    series = [0.15 + 0.05 * math.sin(i / 3.0) for i in range(seed_len)]
    for t in range(seed_len - 1, n - 1):
        window = series[: t + 1]
        rv_d = window[-1]
        rv_w = sum(window[-_WEEKLY:]) / _WEEKLY
        rv_m = sum(window[-_MONTHLY:]) / _MONTHLY
        series.append(_TRUE_BETA0 + _TRUE_BETA_D * rv_d + _TRUE_BETA_W * rv_w + _TRUE_BETA_M * rv_m)
    return series


def test_fit_recovers_true_coefficients_from_noiseless_series():
    series = _generate_noiseless_har_series(n=80)

    model = fit_har_rv(series, weekly_window=_WEEKLY, monthly_window=_MONTHLY)

    assert model is not None
    assert model.beta0 == pytest.approx(_TRUE_BETA0, abs=1e-4)
    assert model.beta_d == pytest.approx(_TRUE_BETA_D, abs=1e-4)
    assert model.beta_w == pytest.approx(_TRUE_BETA_W, abs=1e-4)
    assert model.beta_m == pytest.approx(_TRUE_BETA_M, abs=1e-4)


def test_predict_matches_hand_computed_value_for_known_model():
    model = HARRVModel(
        beta0=0.02, beta_d=0.3, beta_w=0.4, beta_m=0.25, weekly_window=2, monthly_window=4
    )
    history = [0.10, 0.20, 0.30, 0.40]  # rv_d=0.40, rv_w=mean(0.30,0.40)=0.35, rv_m=mean(all)=0.25
    expected = 0.02 + 0.3 * 0.40 + 0.4 * 0.35 + 0.25 * 0.25
    assert model.predict(history) == pytest.approx(expected)


def test_predict_none_when_history_shorter_than_monthly_window():
    model = HARRVModel(beta0=0.0, beta_d=0.0, beta_w=0.0, beta_m=0.0, monthly_window=22)
    assert model.predict([0.1] * 21) is None


def test_fit_returns_none_with_too_few_training_rows():
    short_series = [0.15 + 0.05 * math.sin(i / 3.0) for i in range(_MONTHLY + 1)]  # 학습행 0~1개뿐
    assert fit_har_rv(short_series, weekly_window=_WEEKLY, monthly_window=_MONTHLY) is None
