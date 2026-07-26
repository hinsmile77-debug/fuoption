"""HorizonExpert — Master Plan Ver 1.2 §9 스켈레톤의 정식 구현 (Ver 2.0 §9 W17~19).

W14~16 프로토타입 1호(단일 LightGBM)를 Ver 1.6 §2.3 미니 앙상블(×5)로 확장하고, out-of-fold
데이터로 학습한 `ProbabilityCalibrator`(Ver 1.6 §6.1)를 선택적으로 붙일 수 있게 했다.
다음은 여전히 스코프 밖이다:
- **Meta-Labeler**(신호 검증기)는 별도 컴포넌트다(`strategy/futures/meta_labeler.py`) —
  Ver 1.2 §1 "전문가는 서로를 모른다" 원칙대로 `HorizonExpert` 자신은 `meta_passed`를 항상
  `True`로만 표시하고, 실제 필터링은 그 컴포넌트가 이 출력을 입력받아 별도로 한다.
- **Conformal 불확실성**(Ver 1.6 §6.2)은 매일 갱신되는 운영 절차라 실시간 예측 이력이
  필요하다(`models/calibration.py`의 `ConformalCalibrator` — 메커니즘은 구현됐지만 아직
  아무 운영 루프에도 안 붙어 있음, G2 페이퍼트레이딩부터 실사용, 알려진 갭).
- **Optuna 탐색**(`models/search.py`)의 산출물을 `train()`의 `params`로 넘기는 건 호출자
  (`models/trainer.py`)의 책임 — 이 클래스 자체는 탐색을 모른다.

## 미니 앙상블 (Ver 1.6 §2.3)

동일 하이퍼파라미터 설정으로 seed만 다른 LightGBM 5개를 학습한다. `predict()`는 확률
평균을 최종 출력으로, **P(+1)의 표준편차**(Ver 1.2 §6 원문 그대로 — 다른 클래스가 아니라
+1 확률만)를 `ens_std`(불확실성 원료)로 낸다.

## 클래스 인덱스 매핑

Triple Barrier 레이블({-1,0,+1})과 LightGBM 멀티클래스 학습 레이블({0,1,2})은 다르다 —
`_LABEL_TO_CLASS`로 고정 변환한다(순서를 바꾸면 저장된 기존 모델과 어긋나므로 모듈 상수로
고정 — 레슨런 L25 "라벨/enum 변경은 코드+데이터+화이트리스트 3종 세트").

## Feature 불일치 방지 (계명 6)

학습 시점의 feature_names(정렬된 키 목록)를 모델과 함께 저장한다. 추론 시 FeatureVector의
`feature_set` 문자열이 학습 시점과 다르면 `FeatureSetMismatch`(ERROR) 로그 후 예외 —
침묵한 채 엉뚱한 순서로 벡터를 만들지 않는다.
"""

from __future__ import annotations

import json
import pickle
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import lightgbm as lgb
import numpy as np

from messiah.core import logging as mlog
from messiah.core.messages import ExpertView, FeatureVector, Horizon
from messiah.models.calibration import ProbabilityCalibrator

_LABEL_TO_CLASS: dict[int, int] = {-1: 0, 0: 1, 1: 2}

# Ver 1.6 §2.2 탐색 공간(num_leaves 15~63, min_data_in_leaf 200~2000 등)은 실 데이터 규모
# (Ver 1.2 §8.1 "2년치")가 있어야 의미가 있다 — 이 값은 소규모/스모크 학습·테스트 전용
# 기본값이다. 프로덕션 설정은 `models/search.py`의 `search_hyperparameters()`가 찾아
# 호출자가 `train(params=...)`로 넘긴다.
PROTOTYPE_LGB_PARAMS: dict[str, object] = {
    "num_leaves": 7,
    "max_depth": 3,
    "min_data_in_leaf": 3,
    "learning_rate": 0.1,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
}

DEFAULT_ENSEMBLE_SIZE = 5  # Ver 1.6 §2.3 "미니 앙상블(×5)"


class FeatureSetMismatchError(Exception):
    """추론 시점 FeatureVector.feature_set이 학습 시점과 다름 (계명 6)."""


@dataclass(frozen=True)
class _ExpertMetadata:
    horizon: str
    feature_set: str
    feature_names: list[str]
    model_version: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "horizon": self.horizon,
                "feature_set": self.feature_set,
                "feature_names": self.feature_names,
                "model_version": self.model_version,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, raw: str) -> _ExpertMetadata:
        return cls(**json.loads(raw))


class HorizonExpert:
    """한 Horizon의 방향 전문가 — 미니 앙상블(×5) + 선택적 확률 교정. 학습은
    `models/trainer.py`가, 추론은 이 클래스가 한다."""

    def __init__(
        self,
        boosters: Sequence[lgb.Booster],
        metadata: _ExpertMetadata,
        calibrator: ProbabilityCalibrator | None = None,
    ) -> None:
        if not boosters:
            raise ValueError("boosters가 비어 있음 — 앙상블 멤버가 최소 1개 필요")
        self._boosters = list(boosters)
        self._meta = metadata
        self._calibrator = calibrator

    # ---------------------------------------------------------------- 생성(학습)

    @classmethod
    def train(
        cls,
        *,
        horizon: Horizon,
        feature_set: str,
        model_version: str,
        feature_names: Sequence[str],
        x: np.ndarray,
        y: np.ndarray,
        sample_weight: np.ndarray,
        params: Mapping[str, object] | None = None,
        num_boost_round: int = 50,
        n_members: int = DEFAULT_ENSEMBLE_SIZE,
        base_seed: int = 0,
        calibrator: ProbabilityCalibrator | None = None,
    ) -> HorizonExpert:
        """
        입력: `x`는 (n_samples, len(feature_names)) — 컬럼 순서가 feature_names와 일치해야
             한다(`feature_row()`로 조립할 것). `y`는 {-1,0,1} 원본 Triple Barrier 레이블
             (내부에서 클래스 인덱스로 변환). `sample_weight`는 고유도×클래스불균형 가중치
             (models/trainer.py가 조립해 넘긴다 — 이 메서드는 가중치를 스스로 계산하지 않음).
        계산: 동일 `params`로 seed만 `base_seed`부터 `n_members`개 순차 증가시켜 독립
             LightGBM을 학습한다(Ver 1.6 §2.3 "seed 별로 상이").
        """
        y_class = np.array([_LABEL_TO_CLASS[int(label)] for label in y])
        base_params: dict[str, object] = {
            "objective": "multiclass",
            "num_class": 3,
            "verbosity": -1,
            **(params if params is not None else PROTOTYPE_LGB_PARAMS),
        }
        boosters = []
        for member_idx in range(n_members):
            member_params = {**base_params, "seed": base_seed + member_idx}
            train_set = lgb.Dataset(
                x, label=y_class, weight=sample_weight, feature_name=list(feature_names)
            )
            boosters.append(lgb.train(member_params, train_set, num_boost_round=num_boost_round))
        metadata = _ExpertMetadata(
            horizon=horizon.value,
            feature_set=feature_set,
            feature_names=list(feature_names),
            model_version=model_version,
        )
        return cls(boosters, metadata, calibrator=calibrator)

    # ---------------------------------------------------------------- 추론

    def predict(self, feature_vector: FeatureVector) -> ExpertView:
        if feature_vector.feature_set != self._meta.feature_set:
            mlog.log(
                "FeatureSetMismatch",
                f"학습 feature_set={self._meta.feature_set!r} != "
                f"추론 feature_set={feature_vector.feature_set!r}",
                horizon=self._meta.horizon,
                symbol=feature_vector.symbol,
            )
            raise FeatureSetMismatchError(
                f"학습={self._meta.feature_set!r} != 추론={feature_vector.feature_set!r}"
            )

        row = self.feature_row(feature_vector.values, self._meta.feature_names)
        x_row = np.array([row], dtype=float)
        member_probs = np.array([booster.predict(x_row)[0] for booster in self._boosters])
        # P(+1) 표준편차 — Ver 1.2 §6 "앙상블 분산: 미니 앙상블 5개의 P(+1) 표준편차"
        ens_std = float(statistics.pstdev(member_probs[:, _LABEL_TO_CLASS[1]]))

        mean_probs = member_probs.mean(axis=0)
        if self._calibrator is not None:
            mean_probs = self._calibrator.calibrate(mean_probs)

        return ExpertView(
            symbol=feature_vector.symbol,
            horizon=feature_vector.horizon,
            p_down=float(mean_probs[_LABEL_TO_CLASS[-1]]),
            p_flat=float(mean_probs[_LABEL_TO_CLASS[0]]),
            p_up=float(mean_probs[_LABEL_TO_CLASS[1]]),
            ens_std=ens_std,
            meta_passed=True,  # Meta-Labeler는 별도 컴포넌트(모듈 docstring) — 항상 통과 표시
            model_version=self._meta.model_version,
            top_features=self.top_features(5),
            valid_until=feature_vector.valid_until,
        )

    @staticmethod
    def feature_row(
        values: Mapping[str, float | None], feature_names: Sequence[str]
    ) -> list[float]:
        """FeatureVector.values(dict, None=NaN 마킹)를 학습 시점과 동일한 컬럼 순서의 행으로
        변환 — `train()`(models/trainer.py가 행렬 조립 시)과 `predict()`가 공유하는 유일한
        경로다(두 곳에서 순서가 어긋나면 계명 6 위반이므로 로직을 하나로 묶어 둠)."""
        return [_as_float_or_nan(values.get(name)) for name in feature_names]

    def top_features(self, n: int) -> list[tuple[str, float]]:
        """앙상블 멤버 평균 중요도(gain) 상위 n개 — 개별 예측별 로컬 기여도(SHAP 등)는
        스코프 밖(모듈 docstring)."""
        names, importances = self._aggregate_importances()
        pairs = sorted(zip(names, importances), key=lambda p: p[1], reverse=True)
        return [(name, float(score)) for name, score in pairs[:n]]

    def feature_importance_shares(self) -> dict[str, float]:
        """앙상블 멤버 평균 중요도(gain)를 합이 1이 되도록 정규화한 비율 —
        `models/validator.py`의 "단일 Feature 의존도" 검사(Ver 1.6 §8) 입력. 중요도 합이
        0이면(트리가 전혀 못 갈라진 경우) 전부 0.0으로 반환한다."""
        names, importances = self._aggregate_importances()
        total = float(importances.sum())
        if total <= 0:
            return dict.fromkeys(names, 0.0)
        return {name: float(score) / total for name, score in zip(names, importances)}

    def _aggregate_importances(self) -> tuple[list[str], np.ndarray]:
        names = self._boosters[0].feature_name()
        stacked = np.array([b.feature_importance(importance_type="gain") for b in self._boosters])
        return names, stacked.mean(axis=0)

    # ---------------------------------------------------------------- 교정

    def set_calibrator(self, calibrator: ProbabilityCalibrator | None) -> None:
        """out-of-fold 데이터로 학습한 `ProbabilityCalibrator`를 붙인다(Ver 1.6 §6.1) —
        `models/trainer.py`의 `generate_out_of_fold_predictions()` 산출물로 학습해서 넘길
        것(학습에 쓴 데이터 자체로 교정하면 과신을 그대로 통과시켜 효과가 없다)."""
        self._calibrator = calibrator

    @property
    def calibrator(self) -> ProbabilityCalibrator | None:
        return self._calibrator

    # ---------------------------------------------------------------- 영속화

    def save(self, path: Path) -> None:
        """`path`를 stem으로 앙상블 멤버마다 `{stem}_e{i}{suffix}`(LightGBM 네이티브
        포맷), 메타데이터는 `{stem}.json`, 교정기가 있으면 `{stem}_calibrator.pkl`(sklearn
        객체라 pickle — Ver 1.6 §9.1 번들도 calibrator를 `.pkl`로 명시)로 저장한다. Ver 1.6
        §9.1 정식 번들(manifest.yaml + 다중 파일)의 축소판 — Registry가 생기는 시점(W17~19
        이후)에 재검토 대상."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        for idx, booster in enumerate(self._boosters):
            booster.save_model(str(_member_path(path, idx)))
        path.with_suffix(".json").write_text(self._meta.to_json(), encoding="utf-8")
        if self._calibrator is not None:
            with _calibrator_path(path).open("wb") as f:
                pickle.dump(self._calibrator, f)

    @classmethod
    def load(cls, path: Path) -> HorizonExpert:
        path = Path(path)
        metadata = _ExpertMetadata.from_json(path.with_suffix(".json").read_text(encoding="utf-8"))
        boosters: list[lgb.Booster] = []
        idx = 0
        while _member_path(path, idx).exists():
            boosters.append(lgb.Booster(model_file=str(_member_path(path, idx))))
            idx += 1
        if not boosters:
            raise FileNotFoundError(f"앙상블 멤버 파일을 찾을 수 없음: {_member_path(path, 0)}")

        calibrator = None
        cal_path = _calibrator_path(path)
        if cal_path.exists():
            with cal_path.open("rb") as f:
                calibrator = pickle.load(f)  # noqa: S301 — 우리가 저장한 신뢰 파일만 로드

        return cls(boosters, metadata, calibrator=calibrator)

    # ---------------------------------------------------------------- 조회 전용

    @property
    def feature_names(self) -> list[str]:
        return list(self._meta.feature_names)

    @property
    def feature_set(self) -> str:
        return self._meta.feature_set

    @property
    def horizon(self) -> Horizon:
        return Horizon(self._meta.horizon)

    @property
    def model_version(self) -> str:
        return self._meta.model_version

    @property
    def n_members(self) -> int:
        return len(self._boosters)


def _member_path(base: Path, idx: int) -> Path:
    return base.with_name(f"{base.stem}_e{idx}{base.suffix}")


def _calibrator_path(base: Path) -> Path:
    return base.with_name(f"{base.stem}_calibrator.pkl")


def _as_float_or_nan(value: float | None) -> float:
    return float("nan") if value is None else float(value)
