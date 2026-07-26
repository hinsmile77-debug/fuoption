"""Meta Decision Engine — Ver 2.0 §3 확정 사양, Ver 1.1 §3-4 (Ver 2.0 §9 W24~26).

`FuturesView`(Aggregator 산출물)를 받아 Ver 2.0 §3.1 우선순위 규칙을 그대로 적용해
`DecisionIntent`를 낸다. **NO TRADE도 근거와 함께 발행한다**(Ver 2.0 §3.2 "침묵이 아니라
판단이다") — 이 클래스는 어떤 경로로도 `rationale` 없는 `DecisionIntent`를 반환하지 않는다.

## 규칙 우선순위 (Ver 2.0 §3.1 원문 그대로, 상단이 우선)

```
① sys.kill 활성 → 무조건 NO TRADE
② Regime="이벤트" 또는 UNKNOWN → NO TRADE
③ 전문가 의견 분산 > 0.25 → NO TRADE
④ |S| < 0.20 → NO TRADE
⑤ S ≥ +0.20 → LONG 후보 / S ≤ −0.20 → SHORT 후보
⑥ Options AI 후보의 Net ER가 선물 Net ER × 1.3 초과 시 → OPTION 우선
⑦ 방향 후보 + 옵션 후보 병존 && 상관 노출 중복 → L4가 합산 노출로 판정
```

**⑥⑦은 이번 스코프에 없다.** Options AI(Ver 1.3)는 Phase 4(W27~31) 전까지 존재하지 않는다
— 이 엔진은 항상 선물 방향 의도(LONG/SHORT/NO_TRADE)만 내고, `Side.OPTION`을 낼 수 있는
경로 자체가 코드에 없다(선택이 아니라 부재). Options AI가 생기면 ⑥⑦을 이 지점에 추가한다.

## horizon 필드는 항상 None

`DecisionIntent.horizon`은 특정 Horizon을 지목하는 필드이지만, `FuturesView.score`는 이미
전 Horizon을 Regime 가중치로 통합한 값이라(Ver 1.2 §7.2) 이 엔진 수준에서 "어느 Horizon이
결정했는가"는 의미가 없다 — 의도적으로 항상 `None`을 채운다(XAI 근거는 `top_features`가
`{horizon}:{feature}` 형태로 이미 Horizon 출처를 담고 있어 정보 손실 없음, `aggregator.py`
참고).

## latency_trace는 재생/스모크에서 무의미할 수 있다

`FuturesView.ts_utc`는 `aggregator.py` 모듈 docstring이 설명하듯 실전에서는 wall clock과
같지만 재생·스모크에서는 봉 도메인 시각(과거/합성)이다 — 그 경우
`aggregator_to_decision_ms`는 실제 처리 지연이 아니라 "실행 시각−재생 시각"이 되어
수치가 커지거나 음수가 될 수 있다. 어떤 게이팅에도 안 쓰이는 순수 정보성 필드라 안전하다.

## confidence 산출

`Aggregator`가 이미 같은 가중치로 정규화한 `agg_p_up`/`agg_p_down`을 그대로 쓴다 — 선택된
방향의 가중평균 확률이 곧 "교정된 확률"(DecisionIntent.confidence 계약, Ver 1.6 §6.1 Isotonic
교정이 이미 Expert 단계에서 적용된 값의 가중평균이므로 재교정 불필요).
"""

from __future__ import annotations

from dataclasses import dataclass

from messiah.core import logging as mlog
from messiah.core.messages import DecisionIntent, FuturesView, Regime, Side
from messiah.core.timeutil import now_utc

DISPERSION_THRESHOLD = 0.25  # Ver 2.0 §3.1 ③
SCORE_THRESHOLD = 0.20  # Ver 2.0 §3.1 ④⑤

_EVENT_LIKE_REGIMES = frozenset({Regime.EVENT, Regime.UNKNOWN})


@dataclass(frozen=True)
class MetaDecisionConfig:
    dispersion_threshold: float = DISPERSION_THRESHOLD
    score_threshold: float = SCORE_THRESHOLD


class MetaDecisionEngine:
    def __init__(self, config: MetaDecisionConfig | None = None) -> None:
        self._config = config or MetaDecisionConfig()

    def decide(self, view: FuturesView, *, kill_active: bool) -> DecisionIntent:
        cfg = self._config

        if kill_active:
            return self._no_trade(view, "① sys.kill 활성 — 무조건 NO TRADE")
        if view.regime in _EVENT_LIKE_REGIMES:
            return self._no_trade(view, f"② Regime={view.regime.value} — 이벤트/미판정 국면")
        if view.dispersion > cfg.dispersion_threshold:
            return self._no_trade(
                view,
                f"③ 전문가 의견 분산 {view.dispersion:.3f} > {cfg.dispersion_threshold} — "
                "의견이 갈리면 배팅하지 않는다",
            )
        if abs(view.score) < cfg.score_threshold:
            return self._no_trade(
                view, f"④ |S|={abs(view.score):.3f} < {cfg.score_threshold} — 우위 부족"
            )

        side = Side.LONG if view.score >= cfg.score_threshold else Side.SHORT
        confidence = view.agg_p_up if side == Side.LONG else view.agg_p_down
        latency_ms = (now_utc() - view.ts_utc).total_seconds() * 1000.0

        intent = DecisionIntent(
            symbol=view.symbol,
            side=side,
            confidence=confidence,
            uncertainty=view.uncertainty,
            horizon=None,  # 모듈 docstring — Horizon 통합 이후라 특정 Horizon을 지목 안 함
            option_strategy=None,  # Options AI 미구현(⑥⑦ 스코프 밖) — 항상 None
            top_features=view.top_features,
            model_version="+".join(view.model_versions) if view.model_versions else "none",
            latency_trace={"aggregator_to_decision_ms": latency_ms},
            rationale=(
                f"⑤ S={view.score:.3f} (임계 ±{cfg.score_threshold}) → {side.value}, "
                f"n_experts={view.n_experts}, dispersion={view.dispersion:.3f}"
            ),
        )
        mlog.log(
            "DecisionEmitted",
            intent.rationale,
            symbol=view.symbol,
            side=side.value,
            confidence=confidence,
        )
        return intent

    def _no_trade(self, view: FuturesView, rationale: str) -> DecisionIntent:
        intent = DecisionIntent(
            symbol=view.symbol,
            side=Side.NO_TRADE,
            confidence=max(view.agg_p_up, view.agg_p_down),
            uncertainty=view.uncertainty,
            horizon=None,
            option_strategy=None,
            top_features=view.top_features,
            model_version="+".join(view.model_versions) if view.model_versions else "none",
            latency_trace={},
            rationale=rationale,
        )
        mlog.log("DecisionEmitted", rationale, symbol=view.symbol, side="NO_TRADE")
        return intent
