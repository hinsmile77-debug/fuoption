"""Vol Forecaster — HAR-RV 기준모델 (Ver 1.3 §9, Ver 2.0 §9 W27~29).

Corsi(2009) HAR-RV: RV_{t+1} = β0 + βd·RV_t + βw·mean(RV, 최근 weekly_window일) +
βm·mean(RV, 최근 monthly_window일). 실현변동성의 장단기 성분을 각각 회귀에 넣는
표준 기준모델 — 닫힌 형태 OLS라 학습 루프·검증 세트가 필요 없다(`numpy.linalg.lstsq`).

**LightGBM 잔차 보정은 이번 스코프에 없다** (Ver 1.3 §9 "HAR-RV 기준모델 + LightGBM
잔차 보정"의 후반부): `strategy/futures/expert.py`(`HorizonExpert`)와 동형으로
Trainer/Validator/Registry에 연결하는 별도의 상당한 작업이 필요해 — 15m/30m Expert가
축소된 Feature 집합으로 먼저 출항한 것과 같은 판단으로 — HAR-RV 기준모델만 이번에
완성하고 잔차 보정은 알려진 갭으로 남긴다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

DEFAULT_WEEKLY_WINDOW = 5
DEFAULT_MONTHLY_WINDOW = 22


@dataclass(frozen=True)
class HARRVModel:
    beta0: float
    beta_d: float
    beta_w: float
    beta_m: float
    weekly_window: int = DEFAULT_WEEKLY_WINDOW
    monthly_window: int = DEFAULT_MONTHLY_WINDOW

    def predict(self, daily_rv_history: Sequence[float]) -> float | None:
        """`daily_rv_history[-1]`을 오늘(RV_t)로 보고 내일(RV_{t+1})을 예측.
        실패 조건: 이력이 `monthly_window` 미만이면 월간 성분을 못 만들어 None."""
        if len(daily_rv_history) < self.monthly_window:
            return None
        rv_d = daily_rv_history[-1]
        rv_w = _mean(daily_rv_history[-self.weekly_window :])
        rv_m = _mean(daily_rv_history[-self.monthly_window :])
        return self.beta0 + self.beta_d * rv_d + self.beta_w * rv_w + self.beta_m * rv_m


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _har_design_matrix(
    daily_rv_history: Sequence[float], weekly_window: int, monthly_window: int
) -> tuple[np.ndarray, np.ndarray]:
    """`daily_rv_history[t+1]`을 타깃으로, `daily_rv_history[:t+1]`까지의 일/주/월 성분을
    설계행렬 한 행으로 — `HARRVModel.predict()`가 재구성하는 것과 동일한 성분이라야
    학습·예측이 정합적이다(둘 다 이 헬퍼를 거치진 않지만 동일 산식)."""
    rows: list[list[float]] = []
    targets: list[float] = []
    n = len(daily_rv_history)
    for t in range(monthly_window - 1, n - 1):
        window = daily_rv_history[: t + 1]
        rv_d = window[-1]
        rv_w = _mean(window[-weekly_window:])
        rv_m = _mean(window[-monthly_window:])
        rows.append([1.0, rv_d, rv_w, rv_m])
        targets.append(daily_rv_history[t + 1])
    return np.array(rows), np.array(targets)


def fit_har_rv(
    daily_rv_history: Sequence[float],
    *,
    weekly_window: int = DEFAULT_WEEKLY_WINDOW,
    monthly_window: int = DEFAULT_MONTHLY_WINDOW,
    min_training_rows: int = 4,
) -> HARRVModel | None:
    """실현변동성 일별 이력(오래된 것이 앞)에 HAR-RV OLS를 적합. `numpy.linalg.lstsq`로
    최소자승해(4개 파라미터라 정확히 결정되는 것보다 여유 있는 표본이 필요).
    실패 조건: 학습 가능한 행(표본)이 `min_training_rows` 미만이면 None(과적합 방지)."""
    x, y = _har_design_matrix(daily_rv_history, weekly_window, monthly_window)
    if len(y) < min_training_rows:
        return None
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    beta0, beta_d, beta_w, beta_m = (float(v) for v in beta)
    return HARRVModel(
        beta0=beta0,
        beta_d=beta_d,
        beta_w=beta_w,
        beta_m=beta_m,
        weekly_window=weekly_window,
        monthly_window=monthly_window,
    )
