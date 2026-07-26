"""통계층 — Gaussian HMM 국면 추정 (Ver 1.6 §3.1, Ver 2.0 §9 W20~21).

Ver 1.0.1 §1.8 "HMM(Hidden Markov Model) 또는 클러스터링 기반" 중 HMM을 택했다 — 국면은
레이블 없는 문제라 비지도이고, 전이확률이 "국면의 끈적함"(관성)을 자연스럽게 표현한다
(Ver 1.6 §3.1 "HMM인 이유").

## 관측 Feature (Ver 1.6 §3.1 원문 중 지금 낼 수 있는 3개만)

원문: "vl_vol_ratio, px_trend_r2, px_autocorr, rg_corr_avg, rg_basis_z, op_ivrank …".
`rg_corr_avg`/`rg_basis_z`(타종목·현물 데이터 없음)·`op_ivrank`(Options AI 미구현)는 여전히
스코프 밖(capability_matrix.md 알려진 갭) — `px_trend_r2`·`px_autocorr`(px_core.py, W6~8
기존)·`vl_vol_ratio`(vl_core.py, 이번 주 신규) 3개만으로 관측 벡터를 구성한다.

## 상태 수 선정 (Ver 1.6 §3.1 "BIC + 사후 해석 가능성")

BIC(Bayesian Information Criterion)로 후보(기본 4~6) 중 하나를 고르는 부분은 이 모듈이
담당한다. "사후 해석 가능성" 검토(상태별 통계가 실제로 추세/횡보/고변동성처럼 말이 되는가)는
`naming.py`의 몫 — 이 모듈은 순수 통계 적합만 한다.
"""

from __future__ import annotations

import math
import pickle
from pathlib import Path
from typing import Sequence

import numpy as np
from hmmlearn.hmm import GaussianHMM

from messiah.core.messages import BarClosed
from messiah.features import px_core
from messiah.features.vl_core import vl_vol_ratio

OBSERVATION_WINDOW = 20  # px_trend_r2/px_autocorr/vl_vol_ratio(slow) 공통 윈도우
DEFAULT_N_STATES_CANDIDATES: tuple[int, ...] = (4, 5, 6)  # Ver 1.6 §3.1 "상태 수 4~6"


def build_observations(
    bars: Sequence[BarClosed], window: int = OBSERVATION_WINDOW
) -> tuple[np.ndarray, list[int]]:
    """
    계산: 매 봉 i에서 `bars[:i+1]` 이력으로 (px_trend_r2, px_autocorr, vl_vol_ratio)
         3차원 관측치를 만든다. 셋 중 하나라도 워밍업 부족으로 None이면 그 인덱스는
         건너뛴다(HMM은 결측 없는 연속열을 가정).
    반환: (관측 행렬(n,3), 대응하는 `bars` 인덱스 목록) — 인덱스 목록은 예측 결과를 원래
         봉으로 되매핑할 때 쓴다(naming.py/service.py 참고).
    """
    rows: list[list[float]] = []
    indices: list[int] = []
    for i in range(len(bars)):
        window_bars = bars[: i + 1]
        trend_r2 = px_core.px_trend_r2(window_bars, window)
        autocorr = px_core.px_autocorr(window_bars, window)
        vol_ratio = vl_vol_ratio(window_bars, slow_window=window)
        if trend_r2 is None or autocorr is None or vol_ratio is None:
            continue
        rows.append([trend_r2, autocorr, vol_ratio])
        indices.append(i)
    return np.array(rows, dtype=float), indices


class RegimeHMM:
    """`hmmlearn.GaussianHMM`(대각공분산) 래퍼 — 상태 수는 BIC로 자동 선정."""

    def __init__(self, model: GaussianHMM) -> None:
        self._model = model

    @property
    def n_states(self) -> int:
        return self._model.n_components

    @classmethod
    def fit(
        cls,
        observations: np.ndarray,
        *,
        n_states_candidates: Sequence[int] = DEFAULT_N_STATES_CANDIDATES,
        n_iter: int = 100,
        random_state: int = 0,
    ) -> RegimeHMM:
        """
        계산: 후보 상태 수마다 GaussianHMM(대각공분산)을 학습해 BIC를 계산하고, 최솟값을
             내는 상태 수를 채택한다. 관측치보다 상태 수가 많은 후보는 학습 불가라
             건너뛴다.
        실패 조건: 관측치가 2개 미만이거나, 어떤 후보로도 학습 가능한 상태 수가 없으면
             ValueError.
        """
        if observations.shape[0] < 2:
            raise ValueError("관측치가 최소 2개 이상 필요하다")

        best_model: GaussianHMM | None = None
        best_bic = float("inf")
        for n_states in n_states_candidates:
            if observations.shape[0] < n_states:
                continue
            model = GaussianHMM(
                n_components=n_states,
                covariance_type="diag",
                n_iter=n_iter,
                random_state=random_state,
            )
            model.fit(observations)
            bic = _bic(model, observations)
            if bic < best_bic:
                best_bic = bic
                best_model = model

        if best_model is None:
            raise ValueError(
                f"후보 상태 수 {list(n_states_candidates)} 중 학습 가능한 것이 없음 — "
                f"관측치 {observations.shape[0]}개가 너무 적음"
            )
        return cls(best_model)

    def predict_states(self, observations: np.ndarray) -> np.ndarray:
        return self._model.predict(observations)

    def predict_proba(self, observations: np.ndarray) -> np.ndarray:
        return self._model.predict_proba(observations)

    @property
    def transition_matrix(self) -> np.ndarray:
        return self._model.transmat_

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            pickle.dump(self._model, f)

    @classmethod
    def load(cls, path: Path) -> RegimeHMM:
        with Path(path).open("rb") as f:
            model = pickle.load(f)  # noqa: S301 — 우리가 저장한 신뢰 파일만 로드
        return cls(model)


def _bic(model: GaussianHMM, observations: np.ndarray) -> float:
    log_likelihood = model.score(observations)
    n_params = _n_free_parameters(model)
    n_samples = observations.shape[0]
    return -2 * log_likelihood + n_params * math.log(n_samples)


def _n_free_parameters(model: GaussianHMM) -> int:
    """GaussianHMM(대각공분산) 자유 파라미터 수 — 전이행렬(K·(K−1)) + 초기확률(K−1) +
    평균(K·D) + 대각공분산(K·D)."""
    k = model.n_components
    d = model.n_features
    return k * (k - 1) + (k - 1) + k * d + k * d
