"""Validator 골격 — Ver 1.1 §6-2, Ver 1.2 §8.3 관문표, Ver 1.6 §8 (Ver 2.0 §9 W14~16).

Registry 등록 전 모든 후보 모델이 통과해야 하는 관문을 실행하는 오케스트레이터. Ver 1.2
§8.3 표(성과 관문 3종 — Deflated Sharpe 제외, `models/metrics.py` 모듈 docstring 참고)와
Ver 1.6 §8의 4개 추가검사(교정 품질·Feature 의존도·추론지연·직렬화 왕복)를 전부 구현한다.

**성과 관문(Sharpe·MDD·창별 일관성)은 호출자가 이미 계산해 온 성과 시계열을 받는다** — 이
시계열을 만드는 실제 walk-forward 백테스트 루프(Digital Twin + Expert + Cost Model
전체 연결)는 이번 "골격" 스코프 밖이다. 5m Expert가 이번 주 프로토타입 1호일 뿐이라
의미 있는 백테스트 자체가 아직 불가능하다(실측 아카이브가 하루치뿐 — capability_matrix.md
알려진 갭). Validator "골격"의 목적은 관문 계산 로직 자체의 정확성을 합성 데이터로 증명해
두는 것이고, 실제 walk-forward 백테스트 하니스가 생기면(W17~19 이후) 그 산출물을 그대로
`validate_performance()`에 흘려 넣기만 하면 된다.

모델 자체를 검사하는 4개 관문(교정·Feature 의존도·추론지연·직렬화)은 이번 주 실제로 학습된
`HorizonExpert` 프로토타입으로 지금 바로 실행 가능하다(합성 백테스트가 필요 없다).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from messiah.core.messages import FeatureVector
from messiah.models.metrics import (
    max_drawdown,
    multiclass_brier_score,
    negative_window_ratio,
    sharpe_ratio,
)
from messiah.strategy.futures.expert import HorizonExpert


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    value: float
    threshold: float
    detail: str = ""


@dataclass(frozen=True)
class ValidationReport:
    gates: list[GateResult]

    @property
    def passed(self) -> bool:
        """전 관문 통과 시에만 True — 하나라도 미달이면 Registry 등록 거부(Ver 1.2 §8.3)."""
        return all(gate.passed for gate in self.gates)

    def failed_gates(self) -> list[GateResult]:
        return [gate for gate in self.gates if not gate.passed]


@dataclass(frozen=True)
class ValidatorConfig:
    """관문 임계값 — 전부 Ver 1.2 §8.3·Ver 1.6 §8 원문 수치(초기값, Walk-Forward로 재추정
    대상) 또는 Ver 1.6 §8의 명시적 예산(추론지연 10ms)."""

    min_sharpe: float = 1.0
    max_drawdown_limit: float = 0.3  # 학습기 자본 기준 한도 — 실제 값은 Risk 설정과 연동 필요
    max_negative_window_ratio: float = 0.4
    max_brier_score: float = 0.5  # 3-class 무작위 추측 기준선(각 1/3)의 Brier ≈0.667보다 엄격
    max_feature_importance_share: float = 0.4
    latency_budget_ms: float = 10.0  # Ver 1.6 §2.3 "추론 지연(×5)이 예산(10ms) 내"


class Validator:
    def __init__(self, config: ValidatorConfig | None = None) -> None:
        self._config = config or ValidatorConfig()

    # ---------------------------------------------------------------- Ver 1.2 §8.3 성과 관문

    def validate_performance(
        self,
        *,
        daily_returns: Sequence[float],
        periods_per_year: float,
        equity_curve: Sequence[float],
        window_returns: Sequence[float],
    ) -> list[GateResult]:
        """비용차감 Sharpe·최대낙폭·검증창별 일관성 — 셋 다 이미 계산된 시계열이 입력.
        Deflated Sharpe는 없다(모듈 docstring)."""
        cfg = self._config
        sharpe = sharpe_ratio(daily_returns, periods_per_year=periods_per_year)
        mdd = max_drawdown(equity_curve)
        neg_ratio = negative_window_ratio(window_returns)
        return [
            GateResult("cost_adjusted_sharpe", sharpe > cfg.min_sharpe, sharpe, cfg.min_sharpe),
            GateResult("max_drawdown", mdd < cfg.max_drawdown_limit, mdd, cfg.max_drawdown_limit),
            GateResult(
                "negative_window_ratio",
                neg_ratio < cfg.max_negative_window_ratio,
                neg_ratio,
                cfg.max_negative_window_ratio,
            ),
        ]

    # ---------------------------------------------------------------- Ver 1.6 §8 추가검사

    def validate_calibration(
        self, probs: Sequence[Sequence[float]], true_class_idx: Sequence[int]
    ) -> GateResult:
        """교정 품질 — 다중클래스 Brier Score(낮을수록 좋음)."""
        cfg = self._config
        score = multiclass_brier_score(probs, true_class_idx)
        return GateResult(
            "calibration_brier", score < cfg.max_brier_score, score, cfg.max_brier_score
        )

    def validate_feature_dependency(self, expert: HorizonExpert) -> GateResult:
        """Feature 의존 건전성 — 단일 Feature 중요도 > 40%면 경고(한 재료에 목숨 건 모델은
        취약, Ver 1.6 §8-2)."""
        cfg = self._config
        shares = expert.feature_importance_shares()
        if not shares:
            return GateResult("feature_dependency", False, 1.0, cfg.max_feature_importance_share)
        name, share = max(shares.items(), key=lambda item: item[1])
        return GateResult(
            "feature_dependency",
            share <= cfg.max_feature_importance_share,
            share,
            cfg.max_feature_importance_share,
            detail=f"최다 의존 Feature: {name}",
        )

    def validate_latency(
        self, expert: HorizonExpert, sample: FeatureVector, *, n_calls: int = 1000
    ) -> GateResult:
        """추론 지연 — 번들 로드 후 n_calls회 추론 벤치마크(Ver 1.6 §8-3), 평균이 예산
        (기본 10ms) 초과 시 기각."""
        cfg = self._config
        avg_ms = _benchmark_latency_ms(expert, sample, n_calls=n_calls)
        return GateResult(
            "inference_latency_ms", avg_ms < cfg.latency_budget_ms, avg_ms, cfg.latency_budget_ms
        )

    def validate_serialization(
        self, expert: HorizonExpert, sample: FeatureVector, tmp_path: Path
    ) -> GateResult:
        """직렬화 왕복 — 저장→로드→동일 출력(Ver 1.6 §8-4, 배포 사고의 고전적 원인)."""
        ok = _verify_serialization_round_trip(expert, sample, tmp_path)
        return GateResult("serialization_round_trip", ok, 1.0 if ok else 0.0, 1.0)

    # ---------------------------------------------------------------- 전체 오케스트레이션

    def validate_all(
        self,
        *,
        expert: HorizonExpert,
        sample_feature_vector: FeatureVector,
        tmp_path: Path,
        daily_returns: Sequence[float],
        periods_per_year: float,
        equity_curve: Sequence[float],
        window_returns: Sequence[float],
        calibration_probs: Sequence[Sequence[float]],
        calibration_true_idx: Sequence[int],
    ) -> ValidationReport:
        gates = [
            *self.validate_performance(
                daily_returns=daily_returns,
                periods_per_year=periods_per_year,
                equity_curve=equity_curve,
                window_returns=window_returns,
            ),
            self.validate_calibration(calibration_probs, calibration_true_idx),
            self.validate_feature_dependency(expert),
            self.validate_latency(expert, sample_feature_vector),
            self.validate_serialization(expert, sample_feature_vector, tmp_path),
        ]
        return ValidationReport(gates=gates)


def _benchmark_latency_ms(expert: HorizonExpert, sample: FeatureVector, *, n_calls: int) -> float:
    start = time.perf_counter()
    for _ in range(n_calls):
        expert.predict(sample)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return elapsed_ms / n_calls


def _verify_serialization_round_trip(
    expert: HorizonExpert, sample: FeatureVector, tmp_path: Path
) -> bool:
    before = expert.predict(sample)
    path = Path(tmp_path) / "validator_round_trip_check.lgb"
    expert.save(path)
    reloaded = HorizonExpert.load(path)
    after = reloaded.predict(sample)
    return (
        math.isclose(before.p_up, after.p_up, abs_tol=1e-9)
        and math.isclose(before.p_flat, after.p_flat, abs_tol=1e-9)
        and math.isclose(before.p_down, after.p_down, abs_tol=1e-9)
    )
