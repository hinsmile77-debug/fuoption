from datetime import datetime, timedelta

import numpy as np
from messiah.core.timeutil import KST
from messiah.models.search import PRODUCTION_SEARCH_SPACE, search_hyperparameters

_START = datetime(2026, 7, 27, 9, 0, tzinfo=KST)

_TOY_SEARCH_SPACE: dict[str, tuple[str, float, float]] = {
    "num_leaves": ("int", 3, 7),
    "min_data_in_leaf": ("int", 1, 3),
    "learning_rate": ("log", 0.05, 0.2),
}


def _synthetic_dataset(n_per_class: int = 15) -> tuple[np.ndarray, np.ndarray, np.ndarray, list]:
    rows: list[list[float]] = []
    labels: list[int] = []
    for label, base in ((-1, -5.0), (0, 0.0), (1, 5.0)):
        for i in range(n_per_class):
            rows.append([base + i * 0.01, (i % 2) * 0.01])
            labels.append(label)
    x = np.array(rows, dtype=float)
    y = np.array(labels, dtype=int)
    weight = np.ones(len(labels), dtype=float)
    event_times = [
        (_START + timedelta(minutes=i), _START + timedelta(minutes=i)) for i in range(len(labels))
    ]
    return x, y, weight, event_times


def test_search_returns_all_keys_from_custom_search_space():
    x, y, weight, event_times = _synthetic_dataset()
    best = search_hyperparameters(
        x, y, weight, event_times, n_splits=3, n_trials=4, search_space=_TOY_SEARCH_SPACE, seed=0
    )
    assert set(best.keys()) == set(_TOY_SEARCH_SPACE.keys())


def test_search_returned_values_are_within_bounds():
    x, y, weight, event_times = _synthetic_dataset()
    best = search_hyperparameters(
        x, y, weight, event_times, n_splits=3, n_trials=4, search_space=_TOY_SEARCH_SPACE, seed=0
    )
    assert 3 <= best["num_leaves"] <= 7
    assert 1 <= best["min_data_in_leaf"] <= 3
    assert 0.05 <= best["learning_rate"] <= 0.2


def test_search_is_deterministic_given_same_seed():
    x, y, weight, event_times = _synthetic_dataset()
    kwargs = dict(n_splits=3, n_trials=4, search_space=_TOY_SEARCH_SPACE, seed=42)
    first = search_hyperparameters(x, y, weight, event_times, **kwargs)
    second = search_hyperparameters(x, y, weight, event_times, **kwargs)
    assert first == second


def test_search_handles_degenerate_small_dataset_without_crashing():
    # 폴드 수(5) 대비 표본이 극히 적어 일부 폴드가 train/test 어느 한쪽이 빌 수 있음.
    x, y, weight, event_times = _synthetic_dataset(n_per_class=2)
    best = search_hyperparameters(
        x, y, weight, event_times, n_splits=5, n_trials=3, search_space=_TOY_SEARCH_SPACE
    )
    assert set(best.keys()) == set(_TOY_SEARCH_SPACE.keys())


def test_search_survives_single_row_training_fold_bagging_crash():
    """실측 회귀(2026-07-28, Ver 2.0 §9 W39~40 잔여 Horizon 검증 — 3m 실제 아카이브
    11봉 → 레이블 6건 → `PurgedKFold(n_splits=2)`의 한쪽 폴드가 학습 1행). 학습 폴드가
    1행이면 `bagging_freq=1`에 `bagging_fraction<1`(생산 탐색공간 0.5~0.9)이 그 1행을
    0행으로 반올림해 LightGBM이 `Check failed: (num_data) > (0)`으로 네이티브 크래시를
    낸다 — 아주 작은 표본(n=3, n_splits=2)으로도 fold0의 train이 정확히 1행이 되어
    재현 가능(고른 bagging_fraction이 0.5~0.9 어느 값이든 1행에는 항상 0으로 반올림).
    `objective()`가 `lgb.basic.LightGBMError`를 잡아 그 폴드만 건너뛰어야 하고, 전체
    탐색은 죽지 않고 결과를 반환해야 한다."""
    x = np.array([[0.0], [1.0], [2.0]])
    y = np.array([-1, 0, 1])
    weight = np.ones(3)
    event_times = [(_START + timedelta(minutes=i), _START + timedelta(minutes=i)) for i in range(3)]
    best = search_hyperparameters(
        x, y, weight, event_times, n_splits=2, n_trials=5, search_space=PRODUCTION_SEARCH_SPACE
    )
    assert set(best.keys()) == set(PRODUCTION_SEARCH_SPACE.keys())


def test_default_search_space_matches_production_space_keys():
    x, y, weight, event_times = _synthetic_dataset()
    best = search_hyperparameters(x, y, weight, event_times, n_splits=3, n_trials=2)
    assert set(best.keys()) == set(PRODUCTION_SEARCH_SPACE.keys())
