import math
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from hmmlearn.hmm import GaussianHMM
from messiah.core.messages import BarClosed, Horizon
from messiah.core.timeutil import KST
from messiah.strategy.regime.hmm_model import (
    OBSERVATION_WINDOW,
    RegimeHMM,
    _bic,
    build_observations,
)

_SYMBOL = "TEST"
_START = datetime(2026, 7, 27, 9, 0, tzinfo=KST)


def _bars(n: int) -> list[BarClosed]:
    out = []
    price = 100.0
    for i in range(n):
        price += math.sin(i / 3) * 2 + ((i * 37) % 5 - 2) * 0.3  # 결정론적 준-잡음
        out.append(
            BarClosed(
                symbol=_SYMBOL,
                horizon=Horizon.M30,
                bar_open_kst=_START + timedelta(minutes=30 * i),
                o_ticks=round(price),
                h_ticks=round(price) + 2,
                l_ticks=round(price) - 2,
                c_ticks=round(price),
                volume=10 + i,
            )
        )
    return out


def _two_cluster_observations(n_per_cluster: int = 30) -> np.ndarray:
    rng = np.random.default_rng(0)
    cluster_a = rng.normal(loc=[0.0, 0.0, 1.0], scale=0.05, size=(n_per_cluster, 3))
    cluster_b = rng.normal(loc=[0.8, 0.5, 3.0], scale=0.05, size=(n_per_cluster, 3))
    return np.vstack([cluster_a, cluster_b])


# ---------------------------------------------------------------- build_observations


def test_build_observations_skips_warmup_and_aligns_indices():
    bars = _bars(40)
    observations, indices = build_observations(bars)

    assert observations.shape[1] == 3
    assert observations.shape[0] == len(indices)
    assert all(idx >= OBSERVATION_WINDOW for idx in indices)  # 워밍업 이전은 전부 제외
    assert indices == sorted(indices)


def test_build_observations_empty_bars_returns_empty():
    observations, indices = build_observations([])
    assert observations.shape == (0,)
    assert indices == []


def test_build_observations_insufficient_bars_returns_empty():
    observations, indices = build_observations(_bars(5))
    assert len(indices) == 0


# ---------------------------------------------------------------- RegimeHMM.fit


def test_fit_rejects_fewer_than_two_observations():
    with pytest.raises(ValueError):
        RegimeHMM.fit(np.zeros((1, 3)))


def test_fit_rejects_when_no_candidate_has_enough_observations():
    with pytest.raises(ValueError):
        RegimeHMM.fit(np.zeros((3, 3)), n_states_candidates=(4, 5, 6))


def test_fit_selects_state_count_from_candidates():
    observations = _two_cluster_observations()
    model = RegimeHMM.fit(observations, n_states_candidates=(2, 3, 4))
    assert model.n_states in (2, 3, 4)


def test_fit_selects_the_candidate_with_lowest_independently_recomputed_bic():
    observations = _two_cluster_observations()
    fitted = RegimeHMM.fit(observations, n_states_candidates=(2, 6), random_state=0)

    candidates_bic = {}
    for n in (2, 6):
        model = GaussianHMM(n_components=n, covariance_type="diag", n_iter=100, random_state=0)
        model.fit(observations)
        candidates_bic[n] = _bic(model, observations)

    assert fitted.n_states == min(candidates_bic, key=lambda k: candidates_bic[k])


# ---------------------------------------------------------------- predict / transition matrix


def test_predict_states_and_proba_shapes():
    observations = _two_cluster_observations()
    model = RegimeHMM.fit(observations, n_states_candidates=(2,))

    states = model.predict_states(observations)
    proba = model.predict_proba(observations)

    assert states.shape == (observations.shape[0],)
    assert proba.shape == (observations.shape[0], model.n_states)
    assert np.allclose(proba.sum(axis=1), 1.0, atol=1e-6)


def test_transition_matrix_rows_sum_to_one():
    observations = _two_cluster_observations()
    model = RegimeHMM.fit(observations, n_states_candidates=(2,))
    assert np.allclose(model.transition_matrix.sum(axis=1), 1.0, atol=1e-6)


# ---------------------------------------------------------------- save/load


def test_save_load_round_trip_preserves_predictions(tmp_path: Path):
    observations = _two_cluster_observations()
    model = RegimeHMM.fit(observations, n_states_candidates=(2,))
    before = model.predict_proba(observations)

    path = tmp_path / "regime_hmm.pkl"
    model.save(path)
    reloaded = RegimeHMM.load(path)

    after = reloaded.predict_proba(observations)
    assert np.allclose(before, after)
    assert reloaded.n_states == model.n_states


# ---------------------------------------------------------------- _bic


def test_bic_hand_computed():
    class _FakeModel:
        n_components = 2
        n_features = 3

        def score(self, observations: np.ndarray) -> float:
            return -100.0

    # n_params = k(k-1) + (k-1) + kd + kd = 2*1 + 1 + 2*3 + 2*3 = 2+1+6+6 = 15
    # bic = -2*(-100) + 15*ln(50)
    observations = np.zeros((50, 3))
    expected = -2 * (-100.0) + 15 * math.log(50)

    assert _bic(_FakeModel(), observations) == pytest.approx(expected)
