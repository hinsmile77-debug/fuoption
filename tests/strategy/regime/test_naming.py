from datetime import datetime, timedelta

import numpy as np
from messiah.core.messages import BarClosed, Horizon, Regime
from messiah.core.timeutil import KST
from messiah.strategy.regime.naming import describe_labels, label_states

_SYMBOL = "TEST"
_START = datetime(2026, 7, 27, 9, 0, tzinfo=KST)


def _bars_from_closes(closes: list[float]) -> list[BarClosed]:
    return [
        BarClosed(
            symbol=_SYMBOL,
            horizon=Horizon.M30,
            bar_open_kst=_START + timedelta(minutes=30 * i),
            o_ticks=round(c),
            h_ticks=round(c) + 1,
            l_ticks=round(c) - 1,
            c_ticks=round(c),
            volume=10,
        )
        for i, c in enumerate(closes)
    ]


def test_label_states_hand_computed_four_archetypes():
    # closes: idx0=100(기준), idx1=110(+수익,state0→상승 기대), idx2=90(-수익,state1→하락
    # 기대), idx3=100(+수익이지만 vol_ratio를 압도적으로 높여 state2→고변동성 기대),
    # idx4=100(무변동,state3→횡보 기대).
    bars = _bars_from_closes([100.0, 110.0, 90.0, 100.0, 100.0])
    indices = [1, 2, 3, 4]
    states = np.array([0, 1, 2, 3])
    observations = np.array(
        [
            [0.0, 0.0, 1.0],  # state0: 평균 vol_ratio 1.0(평범) — 상승 기대
            [0.0, 0.0, 1.0],  # state1: 평범 — 하락 기대
            [0.0, 0.0, 5.0],  # state2: vol_ratio 5.0 — 압도적으로 높음 → 고변동성 기대
            [0.0, 0.0, 1.0],  # state3: 평범, 수익률 0 — 횡보 기대
        ]
    )

    labels = label_states(observations, indices, bars, states)

    assert labels == {
        0: Regime.TREND_UP,
        1: Regime.TREND_DOWN,
        2: Regime.HIGH_VOL,
        3: Regime.RANGE,
    }


def test_label_states_empty_input_returns_empty():
    bars = _bars_from_closes([100.0])
    assert label_states(np.empty((0, 3)), [], bars, np.array([])) == {}


def test_label_states_zero_returns_fall_back_to_range():
    bars = _bars_from_closes([100.0, 100.0, 100.0])
    indices = [1, 2]
    states = np.array([0, 1])
    observations = np.array([[0.0, 0.0, 1.0], [0.0, 0.0, 1.0]])

    labels = label_states(observations, indices, bars, states)

    assert labels == {0: Regime.RANGE, 1: Regime.RANGE}


def test_label_states_single_state_positive_return_is_trend_up():
    bars = _bars_from_closes([100.0, 110.0, 121.0])
    indices = [1, 2]
    states = np.array([0, 0])  # 단일 상태
    # vol_ratio가 커도 상태가 1개뿐이면 고변동성으로 배정하지 않는다(len(remaining)>1 가드).
    observations = np.array([[0.0, 0.0, 10.0], [0.0, 0.0, 10.0]])

    labels = label_states(observations, indices, bars, states)

    assert labels == {0: Regime.TREND_UP}


def test_label_states_ignores_states_never_observed():
    bars = _bars_from_closes([100.0, 110.0])
    indices = [1]
    states = np.array([0])
    observations = np.array([[0.0, 0.0, 1.0]])

    labels = label_states(observations, indices, bars, states)

    assert set(labels.keys()) == {0}  # 관측 안 된 상태(예: state1)는 결과에 없음


def test_describe_labels_produces_readable_summary():
    bars = _bars_from_closes([100.0, 110.0, 90.0])
    indices = [1, 2]
    states = np.array([0, 1])
    observations = np.array([[0.0, 0.0, 1.0], [0.0, 0.0, 1.0]])
    labels = label_states(observations, indices, bars, states)

    summary = describe_labels(labels, observations, indices, states)

    assert "state 0" in summary
    assert "state 1" in summary
    assert Regime.TREND_UP.value in summary
    assert Regime.TREND_DOWN.value in summary
