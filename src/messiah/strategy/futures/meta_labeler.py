"""Meta-Labeler — Ver 1.2 §5, Ver 1.6 §5, Ver 2.0 §9 W17~19.

1차 모델(HorizonExpert)은 "어느 쪽인가"에는 강하지만 "지금이 배팅할 때인가"에는 약하다
(Ver 1.2 §5.1) — 역할을 분리해 승률 필터를 독립적으로 학습한다. Ver 1.2 §1 "전문가는
서로를 모른다" 원칙대로 `HorizonExpert`는 이 클래스를 모르고, 이 클래스도 특정
`HorizonExpert` 인스턴스에 결합되지 않는다(숫자 feature dict만 주고받는다).

## Look-ahead 방지 (Ver 1.6 §5.1)

"1차 모델이 학습에 쓴 구간의 신호는 Meta 학습에서 제외해야 한다"는 원칙을 지난주 만든
`PurgedKFold`(models/cv.py)로 실제 구현한다 — `OutOfFoldRecord`는 **그 샘플이 속하지 않은
폴드로 학습한 앙상블의 예측**이다(`models/trainer.py`의 `generate_out_of_fold_predictions()`
가 만든다). 이 파일은 그 산출물을 받아 메타 학습 데이터로 변환하고 학습·임계값 선택만
담당한다.

## 메타 Feature (Ver 1.2 §5.2 "입력" 항목 중 지금 낼 수 있는 것만)

원문은 "1차 확률·마진, 앙상블 분산, Regime, 실현변동성, 스프레드, 이벤트 근접도(D-day),
시간대"를 든다. 이 중 **Regime**(HMM, W20~21 미구현)·**스프레드**(호가 WS 미구독)·
**이벤트 근접도**(Event Calendar 미구현)는 아직 못 낸다(capability_matrix.md 알려진 갭) —
`META_FEATURE_NAMES`는 지금 실제로 계산 가능한 5개(1차 확률·마진·앙상블 분산·실현변동성
근사·시간대)만 담는다. Regime 등이 갖춰지면 이 목록에 추가하면 된다(모델 재학습 필요,
레슨런 L25).

## 임계값 선택 (Ver 1.6 §5.2)

"정확도 최대화가 아니라 비용 차감 후 기대수익 최대화"로 뽑는다 — `select_threshold()`가
그 그리드서치를 한다.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Mapping, Sequence

import lightgbm as lgb
import numpy as np

from messiah.core.messages import HORIZON_SECONDS, BarClosed, FeatureVector, Horizon
from messiah.models.labeling import TripleBarrierLabel

# Ver 1.2 §5.2 "모델: 얕은 LightGBM (깊이 3~4)" / Ver 1.6 §5.1 "max_depth 3~4, num_leaves
# ≤ 15 고정 — 필터는 단순해야 필터답다".
META_LGB_PARAMS: dict[str, object] = {
    "objective": "binary",
    "metric": "binary_logloss",
    "verbosity": -1,
    "max_depth": 4,
    "num_leaves": 15,
    "min_data_in_leaf": 5,  # 소규모 데이터 기본값(expert.py PROTOTYPE_LGB_PARAMS와 같은 이유)
}

META_FEATURE_NAMES: tuple[str, ...] = (
    "meta_p_primary",
    "meta_margin",
    "meta_ens_std",
    "meta_realized_vol",
    "meta_minutes_since_open",
)

_REALIZED_VOL_FEATURE_KEY = "px_bb_width_20"  # 실현변동성 근사 — 별도 ATR 재계산 없이
# FeatureVector가 이미 갖고 있는 변동성 계열 Feature를 재사용(모듈 docstring 알려진 갭 참고)
_SESSION_OPEN_HOUR = 9  # KRX 정규장 09:00 KST
_CLASS_TO_LABEL: dict[int, int] = {0: -1, 1: 0, 2: 1}


@dataclass(frozen=True)
class OutOfFoldRecord:
    """`models/trainer.py`의 `generate_out_of_fold_predictions()` 산출물 1건 — 그 샘플이
    속하지 않은 폴드로 학습한 앙상블의 예측(look-ahead 없음)."""

    bar: BarClosed
    feature_vector: FeatureVector
    label: TripleBarrierLabel
    probs: np.ndarray  # (3,) 클래스 인덱스 순서(0=down,1=flat,2=up)
    ens_std: float  # 그 폴드-외 앙상블의 P(+1) 표준편차 — HorizonExpert.predict()와 동일 정의


def build_meta_features(
    probs: np.ndarray, ens_std: float, feature_vector: FeatureVector, bar: BarClosed
) -> dict[str, float]:
    """`META_FEATURE_NAMES` 순서에 대응하는 값 dict — `MetaLabeler.train()`/`predict_pass_
    probability()`가 공유하는 유일한 조립 경로(HorizonExpert.feature_row()와 같은 이유)."""
    sorted_probs = sorted(probs, reverse=True)
    realized_vol = feature_vector.values.get(_REALIZED_VOL_FEATURE_KEY)
    return {
        "meta_p_primary": float(sorted_probs[0]),
        "meta_margin": float(sorted_probs[0] - sorted_probs[1]),
        "meta_ens_std": float(ens_std),
        "meta_realized_vol": float(realized_vol) if realized_vol is not None else 0.0,
        "meta_minutes_since_open": _minutes_since_session_open(bar.bar_open_kst),
    }


def build_meta_features_from_feature_vector(
    probs: np.ndarray, ens_std: float, feature_vector: FeatureVector
) -> dict[str, float]:
    """`build_meta_features()`의 실시간 추론 경로 전용 버전(Ver 2.0 §9 W24~26, `strategy/
    futures/service.py`가 호출) — Trainer 경로는 실제 `BarClosed`를 갖고 있지만(look-ahead
    없는 학습 파이프라인), 실시간 추론 경로는 `FeatureVector`만 받는다. `bar.bar_open_kst`는
    `FeatureVector.valid_until − Horizon 길이`로 정확히 역산 가능하다(`valid_until`이
    `core/messages.py`의 `bar_confirm_time()`과 동일한 공식으로 채워지기 때문, `features/
    engine.py` 참고) — 별도 `BarClosed` 구독 없이 이 함수 하나로 시간대 Feature까지 낸다."""
    if feature_vector.valid_until is None:
        raise ValueError("valid_until 없는 FeatureVector로는 시간대 Feature를 계산할 수 없음")
    bar_open_kst = feature_vector.valid_until - timedelta(
        seconds=HORIZON_SECONDS[feature_vector.horizon]
    )
    sorted_probs = sorted(probs, reverse=True)
    realized_vol = feature_vector.values.get(_REALIZED_VOL_FEATURE_KEY)
    return {
        "meta_p_primary": float(sorted_probs[0]),
        "meta_margin": float(sorted_probs[0] - sorted_probs[1]),
        "meta_ens_std": float(ens_std),
        "meta_realized_vol": float(realized_vol) if realized_vol is not None else 0.0,
        "meta_minutes_since_open": _minutes_since_session_open(bar_open_kst),
    }


def compute_net_return(predicted_class: int, label: TripleBarrierLabel, cost_ticks: float) -> float:
    """예측 방향을 그대로 따랐을 때의 비용차감 후 손익(틱). `predicted_class`가 flat(0)이면
    신호 자체가 아니므로 0.0(호출자가 애초에 걸러야 함 — `build_meta_training_data()` 참고)."""
    if predicted_class == 1:
        directional = label.ret_ticks
    elif predicted_class == -1:
        directional = -label.ret_ticks
    else:
        return 0.0
    return directional - cost_ticks


def build_meta_training_data(
    records: Sequence[OutOfFoldRecord], *, cost_ticks: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    계산: out-of-fold 레코드 중 1차 모델이 방향 신호를 낸 것만(예측 클래스가 flat이 아닌
         것) 골라 메타 학습 행 하나씩을 만든다. y=1은 "그 신호를 따랐다면 실제로 맞았는가"
         (예측 클래스와 실제 Triple Barrier 레이블 일치 — 레이블 자체가 이미 비용반영
         강등을 거쳤으므로 Ver 1.6 §5.1 ③ "비용 차감 후 이익 여부"를 그대로 담고 있다).
    반환: (X, y, net_returns) — X/y는 `MetaLabeler.train()` 입력, net_returns는
         `select_threshold()` 입력(같은 순서로 대응).
    """
    rows: list[list[float]] = []
    y_values: list[int] = []
    net_returns: list[float] = []
    for record in records:
        predicted_class = _argmax_direction(record.probs)
        if predicted_class == 0:
            continue
        features = build_meta_features(
            record.probs, record.ens_std, record.feature_vector, record.bar
        )
        rows.append([features[name] for name in META_FEATURE_NAMES])
        y_values.append(1 if predicted_class == record.label.label else 0)
        net_returns.append(compute_net_return(predicted_class, record.label, cost_ticks))

    x = np.array(rows, dtype=float) if rows else np.empty((0, len(META_FEATURE_NAMES)))
    return x, np.array(y_values, dtype=int), np.array(net_returns, dtype=float)


DEFAULT_MIN_SUPPORT_FRACTION = 0.05
"""임계값 후보가 남겨야 하는 최소 신호 비율 (2026-08-04 신설).

이 하한이 없던 동안 `select_threshold()`는 **신호 1건만 남는 임계값도 후보로 인정**했다.
그 1건이 우연히 수익이었으면 평균 net_return이 최대가 되어 그 극단값이 선택된다 — 근거가
표본 1개인 임계값이다. 2026-08-04 실측: 선택된 임계값에 도달하는 학습 표본이 **0.5%**뿐이었고
(약 3건), 검증 구간에선 당연히 0%였다(`models/threshold_report.py`).

5%는 미검증 초기값이다 — "표본 몇 개면 평균을 믿을 만한가"에 정답이 없어, 신호가 수백 건인
현재 규모에서 최소 십수 건은 남게 하는 값으로 잡았다. 실측이 쌓이면 재조정 대상.
"""


def select_threshold(
    pass_probabilities: Sequence[float],
    net_returns: Sequence[float],
    *,
    candidates: Sequence[float] | None = None,
    min_support_fraction: float = DEFAULT_MIN_SUPPORT_FRACTION,
) -> float:
    """
    입력: `pass_probabilities`(신호별 MetaLabeler 통과확률)와 `net_returns`(그 신호를
         따랐을 때 비용차감 후 손익, 같은 순서로 대응). `min_support_fraction`은 후보
         임계값이 남겨야 할 최소 신호 비율(위 상수 참고, 0이면 하한 없음 — 종전 동작).
    계산: 후보 임계값(기본 0.0~1.0, 0.05 간격 — 21개)마다 "그 임계값 이상만 통과"했을 때
         남는 신호들의 평균 net_return을 구해, 최댓값을 내는 임계값을 고른다(Ver 1.6 §5.2
         "비용 차감 후 기대수익 최대화" — 정확도 최대화가 아니다). 동률이면 더 큰(보수적인)
         임계값을 남긴다.
    해석: **지지도(support) 하한을 못 채우는 후보는 제외한다** — 표본 몇 개의 우연을
         "기대수익"이라 부르지 않기 위함이다. 하한을 채우는 후보가 하나도 없으면(신호 자체가
         적은 경우) 하한을 무시하고 가장 많은 신호를 남기는 후보로 폴백한다 — 임계값 선택이
         아예 불가능해지는 것보다 낫고, 그 상황은 `models/threshold_report.py`의 선택도달률로
         드러난다.
    """
    if len(pass_probabilities) != len(net_returns):
        raise ValueError("pass_probabilities와 net_returns 길이가 다르다")
    if not pass_probabilities:
        raise ValueError("빈 입력으로는 임계값을 선택할 수 없다")

    grid = candidates if candidates is not None else [i / 20 for i in range(21)]
    min_support = max(1, int(len(pass_probabilities) * min_support_fraction))

    best_threshold: float | None = None
    best_score = float("-inf")
    fallback_threshold, fallback_support = grid[0], -1
    for threshold in grid:
        selected = [r for p, r in zip(pass_probabilities, net_returns) if p >= threshold]
        if not selected:
            continue
        if len(selected) > fallback_support:
            fallback_threshold, fallback_support = threshold, len(selected)
        if len(selected) < min_support:
            continue
        score = statistics.fmean(selected)
        if score >= best_score:
            best_score = score
            best_threshold = threshold
    return best_threshold if best_threshold is not None else fallback_threshold


@dataclass(frozen=True)
class _MetaMetadata:
    horizon: str
    feature_names: list[str]
    threshold: float

    def to_json(self) -> str:
        return json.dumps(
            {
                "horizon": self.horizon,
                "feature_names": self.feature_names,
                "threshold": self.threshold,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, raw: str) -> _MetaMetadata:
        return cls(**json.loads(raw))


class MetaLabeler:
    """Horizon마다 별도로 두는 얕은 이진 분류기(Ver 1.2 §5.2) — 특정 `HorizonExpert`
    인스턴스를 모르고 feature dict(`build_meta_features()` 산출물)만 받는다."""

    def __init__(self, booster: lgb.Booster, metadata: _MetaMetadata) -> None:
        self._booster = booster
        self._meta = metadata

    @classmethod
    def train(
        cls,
        *,
        horizon: Horizon,
        x: np.ndarray,
        y: np.ndarray,
        sample_weight: np.ndarray | None = None,
        threshold: float = 0.5,
        params: Mapping[str, object] | None = None,
        num_boost_round: int = 50,
        feature_names: Sequence[str] = META_FEATURE_NAMES,
    ) -> MetaLabeler:
        train_set = lgb.Dataset(x, label=y, weight=sample_weight, feature_name=list(feature_names))
        lgb_params = {**META_LGB_PARAMS, **(params or {})}
        booster = lgb.train(lgb_params, train_set, num_boost_round=num_boost_round)
        metadata = _MetaMetadata(
            horizon=horizon.value, feature_names=list(feature_names), threshold=threshold
        )
        return cls(booster, metadata)

    def predict_pass_probability(self, meta_features: Mapping[str, float]) -> float:
        row = [meta_features.get(name, float("nan")) for name in self._meta.feature_names]
        return float(self._booster.predict(np.array([row], dtype=float))[0])

    def passes(self, meta_features: Mapping[str, float]) -> bool:
        return self.predict_pass_probability(meta_features) >= self._meta.threshold

    def with_threshold(self, threshold: float) -> MetaLabeler:
        """새 임계값을 가진 얕은 복사본 — 부스터는 그대로 공유(재학습 없이 임계값만
        교체, `select_threshold()`가 고른 값을 붙일 때 사용)."""
        return MetaLabeler(self._booster, replace(self._meta, threshold=threshold))

    @property
    def threshold(self) -> float:
        return self._meta.threshold

    @property
    def horizon(self) -> Horizon:
        return Horizon(self._meta.horizon)

    @property
    def feature_names(self) -> list[str]:
        return list(self._meta.feature_names)

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._booster.save_model(str(path))
        path.with_suffix(".json").write_text(self._meta.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> MetaLabeler:
        path = Path(path)
        booster = lgb.Booster(model_file=str(path))
        metadata = _MetaMetadata.from_json(path.with_suffix(".json").read_text(encoding="utf-8"))
        return cls(booster, metadata)


def _argmax_direction(probs: np.ndarray) -> int:
    return _CLASS_TO_LABEL[int(np.argmax(probs))]


def _minutes_since_session_open(bar_open_kst: datetime) -> float:
    return float((bar_open_kst.hour - _SESSION_OPEN_HOUR) * 60 + bar_open_kst.minute)
