"""확률 교정 — Ver 1.6 §6, Ver 1.2 §6, Ver 2.0 §9 W17~19.

두 층으로 쓰인다(Ver 1.6 §6 원문 그대로):
- `ProbabilityCalibrator`(Isotonic, §6.1): 부스팅 확률의 과신 경향을 검증(out-of-fold)
  데이터로 보정한다 — "P(long)=0.7"이 실제로 10번 중 7번 맞는 확률이 되게 만드는 게
  목적(Kelly 사이징의 전제조건). `HorizonExpert`에 붙어 매 학습마다 갱신된다
  (`models/trainer.py`의 `generate_out_of_fold_predictions()` 산출물로 학습).
- `ConformalCalibrator`(§6.2): 최근 N거래일의 (예측확률,실제결과) 이력으로 비적합도
  분위수를 내 다음 예측에 구간폭을 부여한다 — **재학습이 아니라 매일 값만 갱신하는 운영
  절차**다. 이 클래스는 그 계산(분위수 산출)만 담당한다. 실제 운영 이력(라이브/페이퍼
  예측 로그)은 아직 없다 — G2 페이퍼트레이딩(Ver 2.0 §9 W39~40)부터 쌓인다. 지금은 합성
  데이터로 정확성만 검증해 두고, 실사용은 그 시점부터(capability_matrix.md 알려진 갭).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
from sklearn.isotonic import IsotonicRegression


class ProbabilityCalibrator:
    """클래스별 Isotonic Regression(one-vs-rest) + 재정규화 — 다중클래스 확률 교정의
    표준 기법. sklearn `IsotonicRegression` 자체는 스칼라 대상만 다뤄 클래스 수만큼
    독립적으로 학습한 뒤, 교정 후 합이 1이 되도록 나눠준다(독립 이진 교정기 3개의 출력
    합이 1이라는 보장이 없기 때문)."""

    def __init__(self, models: Sequence[IsotonicRegression]) -> None:
        self._models = list(models)

    @classmethod
    def fit(cls, probs: np.ndarray, true_class_idx: np.ndarray) -> ProbabilityCalibrator:
        """
        입력: `probs`(n,k) — 교정 전(원시) 확률. `true_class_idx`(n,) — 정답 클래스 인덱스.
             **out-of-fold 데이터로 학습해야 한다** — 학습에 쓴 데이터로 교정하면 과적합된
             확신을 그대로 통과시켜 교정 효과가 없다(Ver 1.6 §6.1 "검증 폴드에서").
        """
        n_classes = probs.shape[1]
        models = []
        for c in range(n_classes):
            binary_target = (true_class_idx == c).astype(float)
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            iso.fit(probs[:, c], binary_target)
            models.append(iso)
        return cls(models)

    def calibrate(self, probs: np.ndarray) -> np.ndarray:
        """
        입력: `probs`는 (k,) 또는 (n,k).
        계산: 클래스별로 독립 교정한 뒤 합이 1이 되도록 정규화. 합이 0인 극단적 경우(전
             클래스가 0으로 교정)는 0-나눗셈을 피하려 분모를 1로 두고 0을 그대로 반환한다.
        반환: 입력과 같은 차원(1차원 입력 → 1차원 반환).
        """
        arr = np.atleast_2d(np.asarray(probs, dtype=float))
        calibrated = np.column_stack(
            [model.predict(arr[:, c]) for c, model in enumerate(self._models)]
        )
        totals = calibrated.sum(axis=1, keepdims=True)
        totals[totals <= 0] = 1.0
        normalized = calibrated / totals
        return normalized[0] if np.ndim(probs) == 1 else normalized


@dataclass(frozen=True)
class ConformalInterval:
    point: float
    lower: float
    upper: float
    width: float


class ConformalCalibrator:
    """Ver 1.2 §6 / Ver 1.6 §6.2 — 최근 N일 (예측확률,실제결과) 이력의 비적합도 점수
    분위수로 다음 예측의 구간폭을 낸다. 이력을 어디서 가져와 언제 갱신할지(운영 스케줄러
    연동)는 스코프 밖(모듈 docstring 알려진 갭) — 이 클래스는 계산만 한다."""

    def __init__(self, alpha: float = 0.1) -> None:
        if not 0 < alpha < 1:
            raise ValueError("alpha는 (0,1) 구간이어야 한다")
        self._alpha = alpha

    @staticmethod
    def nonconformity_scores(
        predicted_probs: Sequence[float], actual_outcomes: Sequence[float]
    ) -> list[float]:
        """비적합도 점수 = |예측확률 − 실제결과(0/1)| — 절대 잔차 방식(표준 conformal
        회귀 점수)."""
        if len(predicted_probs) != len(actual_outcomes):
            raise ValueError("predicted_probs와 actual_outcomes 길이가 다르다")
        return [abs(p - a) for p, a in zip(predicted_probs, actual_outcomes)]

    def quantile_width(self, scores: Sequence[float]) -> float:
        """비적합도 점수의 (1−alpha) 분위수 — 구간 반폭. 이력이 없으면 가장 보수적인 값
        (1.0, 확률 전체 폭)을 반환한다 — 운영 이력이 쌓이기 전까지는 "모른다"를 정직하게
        표현한다(침묵 대신 최대 불확실성, L18과 같은 정신)."""
        if not scores:
            return 1.0
        ordered = sorted(scores)
        rank = math.ceil((1 - self._alpha) * len(ordered))
        idx = min(max(rank, 1), len(ordered)) - 1
        return ordered[idx]

    def interval(self, point_prob: float, scores: Sequence[float]) -> ConformalInterval:
        width = self.quantile_width(scores)
        lower = max(0.0, point_prob - width)
        upper = min(1.0, point_prob + width)
        return ConformalInterval(point=point_prob, lower=lower, upper=upper, width=width)
