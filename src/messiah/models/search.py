"""Optuna 하이퍼파라미터 탐색 — Ver 1.6 §2.2, Ver 2.0 §9 W17~19.

Trainer 파이프라인(Ver 1.6 §7.1) [3]단계 "탐색·학습" 중 탐색 절반. Ver 1.6 §2.2가 명시한
LightGBM 3-class 탐색 공간을 그대로 인코딩하고, "창 내부 CV로만 탐색"(같은 절)이라는
원칙을 지난주 만든 `PurgedKFold`(models/cv.py)로 실제 구현한다 — 검증 구간과 겹치는
샘플은 애초에 학습에 들어가지 않으므로, 탐색 단계에서 검증 데이터가 새는 경로가 구조적으로
막힌다.

**early_stopping은 이번 스코프에 없다**(Ver 1.6 §2.2가 명시한 항목이지만, 폴드 내부에
학습/조기종료용 홀드아웃을 추가로 쪼개는 복잡도 대비 지금 데이터 규모(하루~며칠치)에서
실익이 작다고 판단 — 프로덕션 데이터 규모(Ver 1.2 §8.1 "최소 확보 목표: 틱/호가 2년치")가
쌓이면 재검토 대상, capability_matrix.md 알려진 갭). 대신 `num_boost_round`를 고정값으로
쓴다.
"""

from __future__ import annotations

import statistics
from typing import Mapping

import lightgbm as lgb
import numpy as np
import optuna
from sklearn.metrics import log_loss

from messiah.models.cv import EventTimes, PurgedKFold

optuna.logging.set_verbosity(optuna.logging.WARNING)  # 매 trial 로그로 화면을 뒤덮지 않음

# Ver 1.6 §2.2 "탐색 공간 (Optuna, 창 내부 CV로만 탐색)" 원문 그대로 — (분포종류, low, high).
# "int"=정수균등, "uniform"=선형균등, "log"=로그균등. lambda_l1/l2는 원문이 [0,10]이지만
# 로그분포는 0을 못 받아(log(0) 정의 안 됨) 하한을 1e-8로 대체.
PRODUCTION_SEARCH_SPACE: dict[str, tuple[str, float, float]] = {
    "num_leaves": ("int", 15, 63),
    "max_depth": ("int", 3, 8),
    "min_data_in_leaf": ("int", 200, 2000),
    "learning_rate": ("log", 0.01, 0.1),
    "feature_fraction": ("uniform", 0.5, 0.9),
    "bagging_fraction": ("uniform", 0.5, 0.9),
    "lambda_l1": ("log", 1e-8, 10),
    "lambda_l2": ("log", 1e-8, 10),
}

_FIXED_PARAMS: dict[str, object] = {
    "objective": "multiclass",
    "num_class": 3,
    "metric": "multi_logloss",
    "boosting": "gbdt",
    "bagging_freq": 1,
    "verbosity": -1,
}

# expert.py의 _LABEL_TO_CLASS와 값은 같지만 이 모듈은 expert.py를 import하지 않는다 —
# cv.py가 labeling.py에 의존하지 않는 것과 같은 결합도 최소화 원칙(models/cv.py 모듈
# docstring 참고). 매핑 자체가 바뀌면(레슨런 L25 대상) 두 곳 다 손대야 함을 인지할 것.
_LABEL_TO_CLASS: dict[int, int] = {-1: 0, 0: 1, 1: 2}


def search_hyperparameters(
    x: np.ndarray,
    y: np.ndarray,
    sample_weight: np.ndarray,
    event_times: EventTimes,
    *,
    n_splits: int = 5,
    embargo_bars: int = 0,
    n_trials: int = 20,
    num_boost_round: int = 100,
    search_space: Mapping[str, tuple[str, float, float]] | None = None,
    seed: int = 0,
) -> dict[str, object]:
    """
    입력: `x`(n,d)·`y`({-1,0,1} 원본 레이블)·`sample_weight`(n,)·`event_times`(n개
         (t_start,t_end) — `PurgedKFold`에 그대로 전달되며 Triple Barrier 레이블의
         t_start/t_end와 순서가 같아야 한다).
    계산: 매 trial마다 하이퍼파라미터 하나를 뽑아 `PurgedKFold(n_splits)`의 전 폴드에 대해
         학습 → out-of-fold 예측 → multi_logloss를 구해 평균한다. Optuna(TPE)는 이 평균을
         최소화하는 방향으로 탐색한다. 학습/테스트 어느 한쪽이 빈 폴드는 건너뛴다(작은
         데이터셋에서 발생 가능 — models/cv.py PurgedKFold의 purge/embargo가 극단적으로
         깎아낸 경우).
    반환: 최적 하이퍼파라미터만(고정 파라미터 제외) — `HorizonExpert.train()`의 `params`
         인자에 바로 병합해 쓸 수 있는 형태.
    """
    space = dict(search_space) if search_space is not None else PRODUCTION_SEARCH_SPACE
    y_class = np.array([_LABEL_TO_CLASS[int(label)] for label in y])
    folds = list(PurgedKFold(n_splits=n_splits, embargo_bars=embargo_bars).split(event_times))

    def objective(trial: optuna.Trial) -> float:
        params = {**_FIXED_PARAMS, **_sample_params(trial, space)}
        fold_losses: list[float] = []
        for train_idx, test_idx in folds:
            if not train_idx or not test_idx:
                continue
            train_set = lgb.Dataset(
                x[train_idx], label=y_class[train_idx], weight=sample_weight[train_idx]
            )
            try:
                booster = lgb.train(params, train_set, num_boost_round=num_boost_round)
            except lgb.basic.LightGBMError:
                # 극소 표본 폴드(예: purge/embargo가 학습 폴드를 1행까지 깎은 경우)에서
                # 샘플된 bagging_fraction이 그 1행을 0행으로 반올림해 네이티브 크래시가
                # 나는 경계 조건 — 실측 발견(2026-07-28, Ver 2.0 §9 W39~40 잔여 Horizon
                # 검증 중 3m: 실제 아카이브 11봉 → 레이블 6건 → PurgedKFold(2) 한쪽 폴드
                # 학습 1행). 프로덕션 데이터 규모(Ver 1.2 §8.1 "2년치")에서는 폴드가 이
                # 정도로 작아지지 않아 발생하지 않는다 — 이 trial의 이 폴드만 건너뛴다
                # (전체 탐색을 죽이지 않는다, 다른 폴드/trial은 계속 유효).
                continue
            probs = booster.predict(x[test_idx])
            fold_losses.append(log_loss(y_class[test_idx], probs, labels=[0, 1, 2]))
        return statistics.fmean(fold_losses) if fold_losses else float("inf")

    study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(objective, n_trials=n_trials)
    return study.best_params


def _sample_params(
    trial: optuna.Trial, space: Mapping[str, tuple[str, float, float]]
) -> dict[str, object]:
    sampled: dict[str, object] = {}
    for name, (kind, low, high) in space.items():
        if kind == "int":
            sampled[name] = trial.suggest_int(name, int(low), int(high))
        elif kind == "log":
            sampled[name] = trial.suggest_float(name, low, high, log=True)
        else:
            sampled[name] = trial.suggest_float(name, low, high)
    return sampled
