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

# `min_data_in_leaf` 상한을 정할 때 쓰는 나눗수 — "잎 하나에 최소 몇 개"의 상한이
# 표본 수의 1/50을 넘지 않게 한다. 즉 최소 50개 잎을 만들 여지는 항상 남긴다.
# 미검증 초기값이며, 근거는 `scale_space_to_samples()` docstring의 실측이다.
_MIN_LEAF_SAMPLE_DIVISOR = 50
_MIN_LEAF_FLOOR = 5


def scale_space_to_samples(
    space: Mapping[str, tuple[str, float, float]], n_samples: int
) -> dict[str, tuple[str, float, float]]:
    """탐색 공간의 `min_data_in_leaf` 범위를 **실제 표본 수에 맞춰 좁힌다** (2026-08-04 신설).

    ## 왜 필요했나

    `PRODUCTION_SEARCH_SPACE`의 `min_data_in_leaf`는 Ver 1.6 §2.2 원문인 (200, 2000)이다.
    그 값은 다년치 데이터를 전제한 것인데, 2026-08-04 시점 실제 학습 표본은 15m 기준
    3,364봉이고 `PurgedKFold` 폴드 안에서는 **약 2,200행**이다. 그 규모에서 탐색이
    `min_data_in_leaf=1285`를 고르면 잎을 두 개 이상 만들 수가 없다.

    실제로 그렇게 됐다 — 학습된 부스터를 열어보니 **트리 75개가 전부 잎 2개짜리
    그루터기**였고, 그 결과 Expert 출력이 검증 842건 **전부 동일**했다:

        p_up   0.1861 ~ 0.1861   (min == max)
        p_flat 0.6537 ~ 0.6537
        p_down 0.1601 ~ 0.1601

    이게 `|p_up − p_down| ≈ 0.026` 고정 → 집계 |S| ≈ 0 → `SCORE_THRESHOLD=0.20`에서
    전량 NO_TRADE로 이어진 사슬의 출발점이다. 탐색 예산을 늘리면 오히려 나빠졌던 것도
    같은 이유다(더 많은 시도가 전부 그루터기라 우연히 더 큰 `min_data_in_leaf`를 고름).

    **모델에 우위가 없어서 안 거래한 게 아니라, 모델이 학습 자체를 못 하고 있었다.**

    계산: `min_data_in_leaf`의 상한을 `n_samples // 50`으로 낮추고(원래 상한보다 클 순 없음),
         하한이 그 상한을 넘으면 함께 내린다. 다른 키는 건드리지 않는다.
    해석: 표본이 충분히 커지면(10만 행 이상) 원래 (200, 2000)이 그대로 복원된다 — 이
         함수는 규모가 작을 때만 개입한다.
    """
    scaled = dict(space)
    entry = scaled.get("min_data_in_leaf")
    if entry is None or n_samples <= 0:
        return scaled
    kind, low, high = entry
    # **좁히기만 한다** — 호출자가 준 상한을 이 함수가 올리는 일은 없어야 한다(테스트가
    # 작은 전용 공간을 주는 경우까지 망가진다). 바닥은 표본이 극소일 때 0이 되는 것만 막는다.
    capped_high = min(int(high), max(_MIN_LEAF_FLOOR, n_samples // _MIN_LEAF_SAMPLE_DIVISOR))
    capped_low = min(int(low), max(1, capped_high // 4))
    scaled["min_data_in_leaf"] = (kind, capped_low, capped_high)
    return scaled


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
    # 탐색 공간을 **실제 표본 수에 맞춰** 좁힌다 — 안 그러면 작은 데이터에서
    # `min_data_in_leaf`가 표본의 절반을 넘어 트리가 그루터기가 된다
    # (`scale_space_to_samples()` docstring의 2026-08-04 실측).
    space = scale_space_to_samples(
        dict(search_space) if search_space is not None else PRODUCTION_SEARCH_SPACE,
        len(y),
    )
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
