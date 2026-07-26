"""Aggregator — Regime 가중 통합 + XAI (Ver 1.2 §7, Ver 2.0 §9 W24~26).

Horizon별 `ExpertView`(이미 Meta-Labeler가 `meta_passed`를 실제로 채운 것 — 그 채움은
`strategy/futures/service.py`가 한다, 이 클래스 자신은 "전문가는 서로를 모른다" 원칙대로
Meta-Labeler를 모른다)와 `RegimeState` 하나를 받아 통합 방향 점수 S를 낸다(Ver 1.2 §7.2):

    S = Σ_h [ w(regime, h) × (P_h(+1) − P_h(−1)) × meta_h × (1 − u_h) × f_h ]

## 가중치 매트릭스 (Ver 1.2 §7.1)

원문 표를 그대로 상수로 인코딩했다. "이 표 자체가 학습 대상"(원문)이라 분기마다 Walk-Forward로
재추정하는 게 정식 경로이지만, 그 재추정 파이프라인은 이번 스코프 밖이다(알려진 갭) — 지금은
원문 초기값을 고정 상수로 쓴다.

## 불확실성 정규화 (u_h)

`ExpertView.ens_std`(앙상블 P(+1) 표준편차, 0~0.5 이론 상한)를 `uncertainty_scale`로 나눠
[0,1]로 클립한다. Ver 1.2 §6이 말하는 "두 겹" 중 두 번째 겹(Conformal Prediction)은 아직
어떤 운영 루프에도 안 붙어 있어(models/calibration.py 모듈 docstring, G2부터) 앙상블 분산만
쓴다 — Conformal이 실사용되면 더 정확한 정규화 불확실성으로 교체할 자리.

## 신선도 (f_h)

완성봉 규율(Ver 1.2 §2.2) 때문에 Horizon마다 의견의 나이가 다르다. **`valid_until`은
이름과 달리 미래 만료 시점이 아니라 "이 의견이 확정된 시각"이다** — `FeatureVector.
valid_until`(`features/engine.py`)이 실제로 `bar_open_kst + Horizon길이`(=
`bar_confirm_time`, 그 봉 자신의 확정 시각)로 채워지고, `ExpertView.valid_until`은 그 값을
그대로 물려받는다(`strategy/futures/expert.py` `predict()`). 스키마 주석("다음 완성봉
시각")과 실제 채움값이 어긋나 보이지만, 봉이 연속이라는 전제에서 "이 봉의 확정 시각"과
"다음 봉의 시작 시각"은 수치가 같아 혼동하기 쉽다 — 이 클래스는 실제 채움값(확정 시각)
기준으로 신선도를 계산한다: `as_of − valid_until`(경과 시간)을 그 Horizon 길이로 나눠
선형 감쇠시킨다 — 확정 직후(경과 0) 1.0, 한 Horizon 길이만큼 지나면(같은 Horizon의 다음
의견이 나왔어야 할 시점) 0.0.

## FuturesView.ts_utc = as_of (BusMessage 관례의 명시적 예외)

다른 모든 메시지의 `ts_utc`는 "실제 생성 시각"(wall clock, `BusMessage` 기본값)이지만,
이 메시지만은 `as_of`를 그대로 채운다 — Aggregator 내부 계산(신선도 f_h)이 이미 `as_of`
기준으로 이뤄지고, `risk/risk_engine.py`·`risk/kill_switch.py`·`strategy/pipeline.py`의
데이터단절(R11) 판정이 봉 도메인 시각(`bar_confirm_time`)과 이 값을 직접 비교하기 때문이다.
실거래에선 wall clock ≈ 봉 시각이라 차이가 없지만, Digital Twin 재생·이 스모크 스크립트처럼
과거/합성 시각을 빠르게 재생하는 상황에서 `ts_utc`가 wall clock 그대로면 두 시각 도메인이
어긋나 R11이 항상(또는 결코) 발동하는 오탐이 난다 — Ver 1.0.1 §2.1 "동일 인터페이스"
원칙(재생과 실전이 같은 코드로 동작)을 지키려면 이 필드도 도메인을 맞춰야 한다.

## dispersion (Ver 2.0 §3.1 ③)

Meta Decision Engine의 "전문가 의견 분산 > 0.25 → NO TRADE" 판정 입력. Ver 2.0 원문은
분산의 정확한 계산식을 명시하지 않는다 — 이 구현은 실제로 집계에 기여한(가중치>0) Horizon들의
방향 점수(P_h(+1)−P_h(−1)) 표준편차로 정의한다(의견 자체가 갈리는 정도를 직접 측정). 기여
Horizon이 2개 미만이면 "분산"을 측정할 수 없으므로 0.0(의견 불일치 없음으로 취급, 다른
규칙(④ |S|<0.20)이 대신 걸러낸다).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from messiah.core.messages import (
    HORIZON_SECONDS,
    ExpertView,
    FuturesView,
    Horizon,
    Regime,
    RegimeState,
)

# Ver 1.2 §7.1 원문 표 그대로.
REGIME_WEIGHTS: dict[Regime, dict[Horizon, float]] = {
    Regime.TREND_UP: {
        Horizon.M1: 0.5,
        Horizon.M3: 0.8,
        Horizon.M5: 1.0,
        Horizon.M10: 1.2,
        Horizon.M15: 1.2,
        Horizon.M30: 1.5,
    },
    Regime.TREND_DOWN: {
        Horizon.M1: 0.5,
        Horizon.M3: 0.8,
        Horizon.M5: 1.0,
        Horizon.M10: 1.2,
        Horizon.M15: 1.2,
        Horizon.M30: 1.5,
    },
    Regime.RANGE: {
        Horizon.M1: 1.2,
        Horizon.M3: 1.0,
        Horizon.M5: 1.0,
        Horizon.M10: 0.8,
        Horizon.M15: 0.6,
        Horizon.M30: 0.4,
    },
    Regime.HIGH_VOL: {
        Horizon.M1: 0.8,
        Horizon.M3: 0.8,
        Horizon.M5: 1.0,
        Horizon.M10: 0.8,
        Horizon.M15: 0.8,
        Horizon.M30: 0.8,
    },
    Regime.EVENT: {
        Horizon.M1: 0.3,
        Horizon.M3: 0.3,
        Horizon.M5: 0.5,
        Horizon.M10: 0.5,
        Horizon.M15: 0.5,
        Horizon.M30: 0.5,
    },
    Regime.UNKNOWN: {
        Horizon.M1: 0.5,
        Horizon.M3: 0.5,
        Horizon.M5: 0.5,
        Horizon.M10: 0.5,
        Horizon.M15: 0.5,
        Horizon.M30: 0.5,
    },
}

# Ver 1.2 §7.1 "Meta 임계 보정" 열 — Meta-Labeler 임계값에 더할 보정치(호출자 재량 사용처,
# 이 클래스 자체는 임계값을 다루지 않는다 — 참고용 상수로 함께 노출).
META_THRESHOLD_ADJUSTMENT: dict[Regime, float] = {
    Regime.TREND_UP: 0.0,
    Regime.TREND_DOWN: 0.0,
    Regime.RANGE: 0.05,
    Regime.HIGH_VOL: 0.10,
    Regime.EVENT: 0.15,
    Regime.UNKNOWN: 0.10,
}

DEFAULT_UNCERTAINTY_SCALE = 0.5  # ens_std 이론 상한 근사(0.5 확률 표준편차) — 실측 전 placeholder


@dataclass(frozen=True)
class AggregatorConfig:
    uncertainty_scale: float = DEFAULT_UNCERTAINTY_SCALE
    top_features_n: int = 5  # Ver 1.2 §7.2 "기여 상위 5개"


@dataclass(frozen=True)
class _WeightedView:
    horizon: Horizon
    view: ExpertView
    weight: float
    u_h: float
    direction: float  # P_h(+1) − P_h(−1)


class Aggregator:
    def __init__(self, config: AggregatorConfig | None = None) -> None:
        self._config = config or AggregatorConfig()

    def compute(
        self,
        symbol: str,
        views: Mapping[Horizon, ExpertView],
        regime_state: RegimeState,
        *,
        as_of: datetime,
    ) -> FuturesView:
        """
        입력: `views`는 Horizon별 최신 `ExpertView`(meta_passed가 이미 실제 판정값). `as_of`는
             신선도(f_h) 계산 기준 시각 — 봉 도메인 시각(모듈 docstring "신선도" 절)을 넘길
             것(`strategy/futures/service.py`는 트리거가 된 FeatureVector의 `valid_until`을
             넘긴다), wall clock(`ts_utc`)이 아니다.
        """
        weight_table = REGIME_WEIGHTS.get(regime_state.regime, REGIME_WEIGHTS[Regime.UNKNOWN])
        weighted: list[_WeightedView] = []
        for horizon, view in views.items():
            if horizon not in weight_table:
                continue
            meta_h = 1.0 if view.meta_passed else 0.0
            u_h = min(1.0, max(0.0, view.ens_std) / self._config.uncertainty_scale)
            f_h = _freshness(view.valid_until, as_of, horizon)
            weight = weight_table[horizon] * meta_h * (1.0 - u_h) * f_h
            direction = view.p_up - view.p_down
            weighted.append(_WeightedView(horizon, view, weight, u_h, direction))

        active = [w for w in weighted if w.weight > 0]
        total_weight = sum(w.weight for w in active)

        if total_weight <= 0:
            return FuturesView(
                symbol=symbol,
                ts_utc=as_of,
                score=0.0,
                agg_p_up=0.0,
                agg_p_down=0.0,
                uncertainty=1.0,  # 기여 의견 없음 = 최대 보수(Ver 1.1 §3-1 "보수 모드"와 같은 철학)
                dispersion=0.0,
                regime=regime_state.regime,
                n_experts=0,
                model_versions=[],
                top_features=[],
                valid_until=None,
            )

        score = sum(w.weight * w.direction for w in weighted)
        agg_p_up = sum(w.weight * w.view.p_up for w in active) / total_weight
        agg_p_down = sum(w.weight * w.view.p_down for w in active) / total_weight
        uncertainty = sum(w.weight * w.u_h for w in active) / total_weight
        dispersion = statistics.pstdev(w.direction for w in active) if len(active) >= 2 else 0.0
        model_versions = sorted({w.view.model_version for w in active if w.view.model_version})
        valid_untils = [w.view.valid_until for w in active if w.view.valid_until is not None]

        return FuturesView(
            symbol=symbol,
            ts_utc=as_of,
            score=score,
            agg_p_up=agg_p_up,
            agg_p_down=agg_p_down,
            uncertainty=uncertainty,
            dispersion=dispersion,
            regime=regime_state.regime,
            n_experts=len(active),
            model_versions=model_versions,
            top_features=_top_features(active, self._config.top_features_n),
            valid_until=min(valid_untils) if valid_untils else None,
        )


def _freshness(valid_until: datetime | None, as_of: datetime, horizon: Horizon) -> float:
    """확정 직후(경과 0) 1.0, 한 Horizon 길이 경과 시 0.0으로 선형 감쇠(Ver 1.2 §7.2,
    모듈 docstring "신선도" 절 참고 — `valid_until`은 실제로 확정 시각이다). `valid_until`이
    없으면(발행자가 안 채움) 신선도를 판단할 수 없으므로 보수적으로 0.0."""
    if valid_until is None:
        return 0.0
    total_seconds = HORIZON_SECONDS[horizon]
    elapsed = (as_of - valid_until).total_seconds()
    return min(1.0, max(0.0, 1.0 - elapsed / total_seconds))


def _top_features(active: list[_WeightedView], n: int) -> list[tuple[str, float]]:
    """전문가×Feature 기여도 상위 n개(Ver 1.2 §7.2 XAI) — 각 Expert의 top_features를 그
    Expert의 최종 가중치로 스케일해 합산, Horizon을 접두어로 붙여 어느 전문가의 근거인지
    구분한다."""
    scores: dict[str, float] = {}
    for w in active:
        for name, importance in w.view.top_features:
            key = f"{w.horizon.value}:{name}"
            scores[key] = scores.get(key, 0.0) + w.weight * importance
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return [(name, score) for name, score in ranked[:n]]
