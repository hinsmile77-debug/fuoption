"""Trainer — Ver 1.6 §7.1 파이프라인 (Ver 2.0 §9 W14~16 프로토타입 → W17~19 정식).

Ver 1.6 §7.1 전체 파이프라인은 6단계다: [1] 데이터 준비 [2] 레이블 생성 [3] 탐색·학습
[4] 교정 [5] 번들 패키징 [6] Validator 제출.

- `train_prototype_expert()`(W14~16): [1]~[3]만, 그것도 [3]은 고정 설정 단일 모델 — 배관
  검증용 빠른 경로. 여전히 유지한다(단위테스트·스모크 등 빠른 확인용).
- `train_formal_expert()`(W17~19, **이번 주 정식 경로**): [1]~[4]를 전부 구현 — Optuna
  탐색(`models/search.py`) → out-of-fold "가상 운용"(Ver 1.6 §5.1, `PurgedKFold` 재사용) →
  전체 데이터로 최종 앙상블(×5) 재학습 → out-of-fold 예측으로 `ProbabilityCalibrator` 학습
  → 같은 out-of-fold 데이터로 `MetaLabeler` 학습+임계값 선택. [5] 정식 번들 포맷·[6]
  Registry 자동 제출은 여전히 스코프 밖(Registry 자체가 없음, W17~19 이후).

## 파이프라인 결선

```
bars(단일 심볼·단일 Horizon 완성봉)
  │
  ├─→ build_feature_vectors(): FeatureEngine(InProcessBus 재사용, Ver 2.0 §9 W9~11)
  │      → FeatureVector 시퀀스. bars[i] ↔ feature_vectors[i] 1:1.
  │
  ├─→ CostModel.estimate_round_trip_from_bars() → cost_ticks
  │      → label_and_weight(bars, cost_ticks=...) → TripleBarrierLabel 시퀀스
  │      → bar_confirm_time으로 feature_vectors와 정렬 매칭(`_align`) → (X, y, sample_weight)
  │
  ├─→ search_hyperparameters(X, y, sample_weight, event_times) — PurgedKFold 내부 탐색
  │
  ├─→ generate_out_of_fold_predictions() — 같은 PurgedKFold로 "가상 운용"(look-ahead 없음)
  │      → ProbabilityCalibrator.fit() + build_meta_training_data() → MetaLabeler.train()
  │      + select_threshold()
  │
  └─→ HorizonExpert.train(x=전체, params=탐색 결과) — 배포용 최종 앙상블(전체 데이터 재학습)
```

`sample_weight` = 고유도 가중치(`label.weight`, Ver 1.2 §3.3) × 클래스불균형 가중치
(Ver 1.2 §4.1 "0 레이블이 다수가 되는 게 정상 — class weight로 보정", 표준 inverse-frequency
공식).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np

from messiah.core.bus import TOPIC_FEAT
from messiah.core.messages import BarClosed, FeatureVector, Horizon, bar_confirm_time
from messiah.features.engine import FeatureEngine
from messiah.models.calibration import ProbabilityCalibrator
from messiah.models.cv import PurgedKFold
from messiah.models.labeling import DEFAULT_ATR_WINDOW, TripleBarrierLabel, label_and_weight
from messiah.models.search import search_hyperparameters
from messiah.risk.cost_model import CostModel
from messiah.simulator.inprocess_bus import InProcessBus
from messiah.strategy.futures.expert import DEFAULT_ENSEMBLE_SIZE, HorizonExpert
from messiah.strategy.futures.meta_labeler import (
    DEFAULT_MIN_SUPPORT_FRACTION,
    META_FEATURE_NAMES,
    MetaLabeler,
    OutOfFoldRecord,
    build_meta_training_data,
    select_threshold,
)

Aligned = tuple[BarClosed, FeatureVector, TripleBarrierLabel]


async def build_feature_vectors(
    bars: Sequence[BarClosed],
    *,
    feature_set: str,
    sidecars: Mapping[str, object] | None = None,
) -> list[FeatureVector]:
    """
    계산: `bars`(단일 심볼·단일 Horizon, 오래된 것 → 최신 순)를 실제 운영과 동일한
         FeatureEngine에 순서대로 직접 흘려(bus 구독이 아니라 `handle_bar()` 직접 호출 —
         재생 아님) FeatureVector를 얻는다. FeatureEngine이 내부적으로 발행하는
         `feat.{h}.{symbol}`을 InProcessBus로 가로채 수집하므로, 반환값은 `bars`와 정확히
         1:1(개수·순서 동일) — 별도 정렬 없이 `zip(bars, feature_vectors)` 가능.
    입력: `sidecars`는 `feature_set`이 요구하는 봉 밖 상태(`features/sidecar.build()`로
         조립). PX+VL만 쓰는 이름이면 생략 — 요구/주입이 어긋나면 엔진이 **생성 시점에**
         거부하므로 여기서 따로 검사하지 않는다(검사가 두 곳이면 한쪽만 고쳐진다).
    """
    if not bars:
        return []
    symbol, horizon = bars[0].symbol, bars[0].horizon
    _assert_single_series(bars, symbol, horizon)

    bus = InProcessBus()
    collected: list[FeatureVector] = []

    async def _collect(vector: FeatureVector) -> None:
        collected.append(vector)

    await bus.subscribe([f"{TOPIC_FEAT}.{horizon.value}.{symbol}"], _collect)
    engine = FeatureEngine(
        symbol, bus, feature_set=feature_set, horizons=[horizon], sidecars=sidecars
    )
    for bar in bars:
        await engine.handle_bar(bar)
    return collected


def build_training_data(
    bars: Sequence[BarClosed],
    feature_vectors: Sequence[FeatureVector],
    *,
    cost_model: CostModel,
    qty: int = 1,
    atr_window: int = DEFAULT_ATR_WINDOW,
    labels: Sequence[TripleBarrierLabel] | None = None,
) -> tuple[list[str], np.ndarray, np.ndarray, np.ndarray]:
    """
    입력: `feature_vectors`는 `build_feature_vectors(bars, ...)`의 출력이어야 한다(길이가
         다르면 그 계약이 깨졌다는 뜻이라 ValueError). `labels`를 넘기면 그 레이블을 그대로
         쓰고(재계산 없음 — `train_formal_expert()`가 out-of-fold 생성과 공유하려고 씀),
         생략하면 `cost_model`로 직접 계산한다.
    계산: `bar_confirm_time`으로 레이블과 FeatureVector를 매칭한다(레이블이 없는 봉 —
         ATR 워밍업·꼬리 트림 — 은 학습 행렬에서 빠진다).
    반환: (feature_names, X, y, sample_weight). `feature_names`는 첫 FeatureVector의 키를
         정렬한 목록 — 이후 추론 시점(HorizonExpert.predict)에도 이 순서를 그대로 쓴다.
    """
    if len(bars) != len(feature_vectors):
        raise ValueError(
            "bars와 feature_vectors 길이가 다르다 — build_feature_vectors(bars, ...)의 "
            "출력을 그대로 넘겼는지 확인할 것"
        )
    if not bars:
        return [], np.empty((0, 0)), np.empty(0), np.empty(0)

    if labels is None:
        cost_ticks = cost_model.estimate_round_trip_from_bars(bars, qty=qty).total_ticks
        labels = label_and_weight(bars, atr_window=atr_window, cost_ticks=cost_ticks)

    feature_names = sorted(feature_vectors[0].values.keys())
    aligned = _align(bars, feature_vectors, labels)
    x, y, sample_weight = _rows_from_aligned(aligned, feature_names)
    return feature_names, x, y, sample_weight


async def train_prototype_expert(
    bars: Sequence[BarClosed],
    *,
    feature_set: str,
    model_version: str,
    sidecars: Mapping[str, object] | None = None,
    cost_model: CostModel | None = None,
    qty: int = 1,
    atr_window: int = DEFAULT_ATR_WINDOW,
    lgb_params: Mapping[str, object] | None = None,
    num_boost_round: int = 50,
) -> HorizonExpert:
    """
    W14~16 프로토타입 경로(그대로 유지) — Optuna 탐색·out-of-fold 교정·Meta-Labeler 없이
    고정 설정 하나로 빠르게 배관을 확인한다. 정식 경로는 `train_formal_expert()`.

    실패 조건: 레이블이 0건이면(데이터 부족 — ATR 워밍업+시간배리어 봉수보다 짧은 시계열)
         학습 자체가 무의미하므로 ValueError.
    """
    if not bars:
        raise ValueError("bars가 비어 있음")
    horizon = bars[0].horizon

    feature_vectors = await build_feature_vectors(bars, feature_set=feature_set, sidecars=sidecars)
    feature_names, x, y, sample_weight = build_training_data(
        bars,
        feature_vectors,
        cost_model=cost_model or CostModel(),
        qty=qty,
        atr_window=atr_window,
    )
    if len(y) == 0:
        raise ValueError(
            f"학습 가능한 레이블이 0건 — 데이터 부족(bars={len(bars)}건, "
            f"atr_window={atr_window}, horizon={horizon.value})"
        )

    return HorizonExpert.train(
        horizon=horizon,
        feature_set=feature_set,
        model_version=model_version,
        feature_names=feature_names,
        x=x,
        y=y,
        sample_weight=sample_weight,
        params=lgb_params,
        num_boost_round=num_boost_round,
        n_members=1,  # 프로토타입은 단일 모델 유지(속도 우선) — 정식은 train_formal_expert()
    )


async def generate_out_of_fold_predictions(
    bars: Sequence[BarClosed],
    feature_vectors: Sequence[FeatureVector],
    labels: Sequence[TripleBarrierLabel],
    *,
    feature_set: str,
    feature_names: Sequence[str],
    n_splits: int = 5,
    embargo_bars: int = 0,
    ensemble_params: Mapping[str, object] | None = None,
    n_members: int = DEFAULT_ENSEMBLE_SIZE,
    num_boost_round: int = 50,
) -> list[OutOfFoldRecord]:
    """
    계산: `labels`의 (t_start,t_end)로 `PurgedKFold(n_splits)`를 나누고, 각 폴드마다 그
         폴드의 train 인덱스만으로 앙상블을 학습해 test 인덱스에 대해 예측한다 — "1차
         모델이 학습에 쓴 구간의 신호는 Meta 학습에서 제외"(Ver 1.6 §5.1)를 purge/embargo로
         구조적으로 보장한다(칸닝 방지). 학습/테스트 어느 한쪽이 빈 폴드는 건너뛴다.
    반환: 폴드 순서대로 이어붙인 `OutOfFoldRecord` 목록 — `ProbabilityCalibrator.fit()`과
         `build_meta_training_data()` 양쪽의 입력으로 재사용된다(같은 out-of-fold 데이터를
         두 목적에 공유 — Ver 1.6 §6.1 "검증 폴드에서"와 §5.1 "가상 운용"이 사실상 같은
         메커니즘을 요구하기 때문).
    """
    if len(bars) != len(feature_vectors):
        raise ValueError("bars와 feature_vectors 길이가 다르다")
    aligned = _align(bars, feature_vectors, labels)
    if not aligned:
        return []

    x, y, sample_weight = _rows_from_aligned(aligned, feature_names)
    event_times = [(lbl.t_start, lbl.t_end) for _, _, lbl in aligned]
    horizon = aligned[0][0].horizon

    records: list[OutOfFoldRecord] = []
    folds = PurgedKFold(n_splits=n_splits, embargo_bars=embargo_bars).split(event_times)
    for train_idx, test_idx in folds:
        if not train_idx or not test_idx:
            continue
        fold_expert = HorizonExpert.train(
            horizon=horizon,
            feature_set=feature_set,
            model_version="oof-fold",
            feature_names=feature_names,
            x=x[train_idx],
            y=y[train_idx],
            sample_weight=sample_weight[train_idx],
            params=ensemble_params,
            num_boost_round=num_boost_round,
            n_members=n_members,
        )
        for idx in test_idx:
            bar, fv, lbl = aligned[idx]
            view = fold_expert.predict(fv)
            probs = np.array([view.p_down, view.p_flat, view.p_up])
            records.append(
                OutOfFoldRecord(
                    bar=bar, feature_vector=fv, label=lbl, probs=probs, ens_std=view.ens_std
                )
            )
    return records


@dataclass(frozen=True)
class ExpertTrainingResult:
    expert: HorizonExpert
    meta_labeler: MetaLabeler
    best_params: dict[str, object]
    n_oof_records: int
    n_meta_signals: int
    # 임계값 선택에 **실제로 쓴** 확률(out-of-fold)과, 같은 행을 최종 모델로 예측한
    # in-sample 확률. 둘을 나란히 두는 이유는 `models/threshold_report.py`가 그 격차로
    # "임계값 과적합"과 "모델에 우위 없음"을 구분하기 때문이다 — 두 증상이 똑같이 무거래다.
    threshold_selection_probabilities: list[float] = field(default_factory=list)
    threshold_insample_probabilities: list[float] = field(default_factory=list)


async def train_formal_expert(
    bars: Sequence[BarClosed],
    *,
    feature_set: str,
    model_version: str,
    sidecars: Mapping[str, object] | None = None,
    cost_model: CostModel | None = None,
    qty: int = 1,
    atr_window: int = DEFAULT_ATR_WINDOW,
    n_splits: int = 5,
    embargo_bars: int = 0,
    meta_threshold_splits: int = 5,
    meta_min_support_fraction: float = DEFAULT_MIN_SUPPORT_FRACTION,
    n_search_trials: int = 20,
    search_space: Mapping[str, tuple[str, float, float]] | None = None,
    search_num_boost_round: int = 100,
    final_num_boost_round: int = 100,
    n_members: int = DEFAULT_ENSEMBLE_SIZE,
    meta_num_boost_round: int = 50,
    search_seed: int = 0,
) -> ExpertTrainingResult:
    """
    Ver 1.6 §7.1 [3]~[4]단계 전체(정식 경로, 모듈 docstring 다이어그램 참고).
    실패 조건: 레이블이 0건이거나(데이터 부족) out-of-fold 신호가 0건(Meta-Labeler 학습
         불가 — n_splits 대비 데이터가 너무 적어 유효 폴드가 하나도 안 나온 경우)이면
         ValueError. 정식 경로는 이 단계들이 실제로 작동했다는 보장이 핵심이라(칸닝 방지)
         조용히 빈 Meta-Labeler를 만들지 않는다.

    `meta_threshold_splits`: Meta-Labeler 임계값을 고를 때 쓸 통과확률을 **out-of-fold**로
         만들 폴드 수(2026-08-04 신설, 근거는 `_meta_selection_probabilities()`). 2 미만이면
         종전대로 in-sample 확률을 쓴다 — 그 경로는 임계값이 실제 추론에서 도달 불가능해질
         수 있으므로 재현·비교 목적으로만 쓸 것.
    `meta_min_support_fraction`: 임계값 후보가 남겨야 할 최소 신호 비율
         (`meta_labeler.DEFAULT_MIN_SUPPORT_FRACTION`) — 표본 몇 개짜리 극단 임계값을 막는다.
    """
    if not bars:
        raise ValueError("bars가 비어 있음")
    cost_model = cost_model or CostModel()
    horizon = bars[0].horizon

    feature_vectors = await build_feature_vectors(bars, feature_set=feature_set, sidecars=sidecars)
    cost_ticks = cost_model.estimate_round_trip_from_bars(bars, qty=qty).total_ticks
    labels = label_and_weight(bars, atr_window=atr_window, cost_ticks=cost_ticks)
    aligned = _align(bars, feature_vectors, labels)
    if not aligned:
        raise ValueError(
            f"학습 가능한 레이블이 0건 — 데이터 부족(bars={len(bars)}건, "
            f"atr_window={atr_window}, horizon={horizon.value})"
        )

    feature_names = sorted(feature_vectors[0].values.keys())
    x, y, sample_weight = _rows_from_aligned(aligned, feature_names)
    event_times = [(lbl.t_start, lbl.t_end) for _, _, lbl in aligned]

    best_params = search_hyperparameters(
        x,
        y,
        sample_weight,
        event_times,
        n_splits=n_splits,
        embargo_bars=embargo_bars,
        n_trials=n_search_trials,
        num_boost_round=search_num_boost_round,
        search_space=search_space,
        seed=search_seed,
    )

    oof_records = await generate_out_of_fold_predictions(
        bars,
        feature_vectors,
        labels,
        feature_set=feature_set,
        feature_names=feature_names,
        n_splits=n_splits,
        embargo_bars=embargo_bars,
        ensemble_params=best_params,
        n_members=n_members,
        num_boost_round=final_num_boost_round,
    )

    expert = HorizonExpert.train(
        horizon=horizon,
        feature_set=feature_set,
        model_version=model_version,
        feature_names=feature_names,
        x=x,
        y=y,
        sample_weight=sample_weight,
        params=best_params,
        num_boost_round=final_num_boost_round,
        n_members=n_members,
    )

    if oof_records:
        oof_probs = np.array([r.probs for r in oof_records])
        # {-1,0,1} 원본 레이블 → 클래스 인덱스(0=down,1=flat,2=up) — expert.py/search.py와
        # 같은 매핑이지만 이 모듈도 결합도 최소화 원칙(두 모듈 docstring 참고)으로 독립 보유.
        oof_true_class = np.array([{-1: 0, 0: 1, 1: 2}[r.label.label] for r in oof_records])
        expert.set_calibrator(ProbabilityCalibrator.fit(oof_probs, oof_true_class))

    meta_x, meta_y, meta_returns = build_meta_training_data(oof_records, cost_ticks=cost_ticks)
    if len(meta_y) == 0:
        raise ValueError(
            "Meta-Labeler 학습 가능한 out-of-fold 신호가 0건 — n_splits 대비 데이터가 "
            "너무 적어 유효 폴드가 없었을 가능성(n_splits를 줄이거나 데이터를 늘릴 것)"
        )

    meta_labeler = MetaLabeler.train(
        horizon=horizon, x=meta_x, y=meta_y, num_boost_round=meta_num_boost_round
    )
    selection_probs = _meta_selection_probabilities(
        horizon=horizon,
        meta_x=meta_x,
        meta_y=meta_y,
        num_boost_round=meta_num_boost_round,
        n_splits=meta_threshold_splits,
        fitted=meta_labeler,
    )
    threshold = select_threshold(
        selection_probs, list(meta_returns), min_support_fraction=meta_min_support_fraction
    )
    meta_labeler = meta_labeler.with_threshold(threshold)
    inference_probs = [
        meta_labeler.predict_pass_probability(dict(zip(META_FEATURE_NAMES, row))) for row in meta_x
    ]

    return ExpertTrainingResult(
        expert=expert,
        meta_labeler=meta_labeler,
        best_params=best_params,
        n_oof_records=len(oof_records),
        n_meta_signals=len(meta_y),
        threshold_selection_probabilities=selection_probs,
        threshold_insample_probabilities=inference_probs,
    )


def _meta_selection_probabilities(
    *,
    horizon: Horizon,
    meta_x: np.ndarray,
    meta_y: np.ndarray,
    num_boost_round: int,
    n_splits: int,
    fitted: MetaLabeler,
) -> list[float]:
    """임계값 선택에 쓸 통과확률 — **out-of-fold**로 만든다 (2026-08-04 수정).

    ## 왜 바꿨나

    그 전에는 방금 학습한 Meta-Labeler로 **자기 학습 행(`meta_x`)을 그대로 예측**해 그
    확률로 임계값을 골랐다. Expert 쪽은 `PurgedKFold`로 look-ahead를 막았지만 **메타 모델
    자신의 예측은 in-sample**이었던 것이다. LightGBM은 학습 행을 잘 맞히므로 확률이 0/1
    쪽으로 밀리고, 그 위에서 고른 임계값은 새 데이터에서 아무도 못 넘는 높이가 된다.

    2026-08-04 실측이 정확히 그 모양이었다 — 선택된 임계 0.6000, 검증 구간 통과확률
    **최대치 0.5422**, 통과 0/1006건. 판단은 매번 나오는데 거래는 영원히 0건이었다
    (`models/threshold_report.py` 모듈 docstring).

    ## 어떻게

    메타 학습 데이터를 `n_splits`로 나눠 각 폴드를 나머지로 학습한 모델로 예측한다.
    여기서 `PurgedKFold`가 아니라 단순 연속 분할을 쓰는 이유: 메타 행은 이미 out-of-fold
    Expert 예측에서 나온 것이라 배리어 겹침에 의한 누수는 상위 단계에서 이미 제거됐고,
    이 단계가 막으려는 것은 **자기 자신을 예측하는 것** 하나다.

    실패 조건: 없다 — `n_splits < 2`이거나 표본이 폴드 수보다 적으면 종전처럼 in-sample
              확률로 폴백한다(임계값 선택이 아예 불가능해지는 것보다 낫고, 그 경우
              `ExpertTrainingResult`의 두 확률 목록이 같아져 진단에서 바로 드러난다).
    """
    n = len(meta_y)
    if n_splits < 2 or n < n_splits * 2:
        return [
            fitted.predict_pass_probability(dict(zip(META_FEATURE_NAMES, row))) for row in meta_x
        ]

    probs = [0.0] * n
    bounds = [round(i * n / n_splits) for i in range(n_splits + 1)]
    for i in range(n_splits):
        lo, hi = bounds[i], bounds[i + 1]
        if lo >= hi:
            continue
        train_rows = np.concatenate([meta_x[:lo], meta_x[hi:]])
        train_labels = np.concatenate([meta_y[:lo], meta_y[hi:]])
        if len(train_labels) == 0 or len(set(train_labels.tolist())) < 2:
            # 한쪽 클래스만 남은 폴드 — 학습이 성립하지 않는다. 그 구간은 전체 모델로 채운다.
            for idx in range(lo, hi):
                probs[idx] = fitted.predict_pass_probability(
                    dict(zip(META_FEATURE_NAMES, meta_x[idx]))
                )
            continue
        fold_model = MetaLabeler.train(
            horizon=horizon,
            x=train_rows,
            y=train_labels,
            num_boost_round=num_boost_round,
        )
        for idx in range(lo, hi):
            probs[idx] = fold_model.predict_pass_probability(
                dict(zip(META_FEATURE_NAMES, meta_x[idx]))
            )
    return probs


def _rows_from_aligned(
    aligned: Sequence[Aligned], feature_names: Sequence[str]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """정렬된 (bar, feature_vector, label) 삼중을 feature_names 순서의 (X,y,sample_weight)로
    조립 — `build_training_data()`와 `generate_out_of_fold_predictions()`가 공유."""
    if not aligned:
        return np.empty((0, len(feature_names))), np.empty(0), np.empty(0)
    rows = [HorizonExpert.feature_row(fv.values, feature_names) for _, fv, _ in aligned]
    y_values = [lbl.label for _, _, lbl in aligned]
    uniqueness_weights = [lbl.weight for _, _, lbl in aligned]
    class_weight = _class_balance_weights(y_values)
    sample_weight = np.array(
        [u * class_weight[label] for u, label in zip(uniqueness_weights, y_values)]
    )
    return np.array(rows, dtype=float), np.array(y_values, dtype=int), sample_weight


def _align(
    bars: Sequence[BarClosed],
    feature_vectors: Sequence[FeatureVector],
    labels: Sequence[TripleBarrierLabel],
) -> list[Aligned]:
    """bars/feature_vectors를 `bar_confirm_time`으로 labels와 정렬 매칭 — 레이블이 없는 봉
    (ATR 워밍업·시간배리어 꼬리 트림)은 빠진다."""
    label_by_t_start = {lbl.t_start: lbl for lbl in labels}
    aligned: list[Aligned] = []
    for bar, fv in zip(bars, feature_vectors):
        lbl = label_by_t_start.get(bar_confirm_time(bar))
        if lbl is not None:
            aligned.append((bar, fv, lbl))
    return aligned


def _class_balance_weights(labels: Sequence[int]) -> dict[int, float]:
    """Ver 1.2 §4.1 "클래스 불균형: class weight로 보정" — 표준 inverse-frequency 공식:
    weight_c = N / (등장한 클래스 수 × count_c)."""
    counts = Counter(labels)
    n = len(labels)
    num_classes = len(counts)
    return {label: n / (num_classes * count) for label, count in counts.items()}


def _assert_single_series(bars: Sequence[BarClosed], symbol: str, horizon: Horizon) -> None:
    for bar in bars:
        if bar.symbol != symbol or bar.horizon != horizon:
            raise ValueError("build_feature_vectors는 단일 심볼·단일 Horizon 시퀀스만 받는다")
