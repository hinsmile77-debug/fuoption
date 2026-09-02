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

④⑤의 임계는 이제 **국면별 표**(`SCORE_THRESHOLD_BY_REGIME`)에서 온다 — 값은 전 국면 0.20이라
동작은 종전과 같고, 바뀐 것은 그 결합이 코드에 드러났다는 것뿐이다. 왜 표로 만들었는지는 그
상수의 주석에 있다(요약: 이 임계와 `aggregator.REGIME_WEIGHTS`가 곱해져 횡보·고변동에서는
게이트가 산술적으로 닫혀 있다). 실효 천장은 `models/label_geometry.regime_reachability()`가
계산하고, 국면별 방향 적중률은 `models/regime_direction.py`가 매일 센다.

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
from typing import Mapping

from messiah.core import logging as mlog
from messiah.core.messages import DecisionIntent, FuturesView, Regime, Side
from messiah.core.timeutil import now_utc

DISPERSION_THRESHOLD = 0.25  # Ver 2.0 §3.1 ③
SCORE_THRESHOLD = 0.20  # Ver 2.0 §3.1 ④⑤

# **국면별 우위 게이트 — 지금은 전 국면이 같은 값이라 동작은 종전과 한 톨도 다르지 않다.**
#
# ## 왜 값이 다 같은데 표를 만드나 (2026-09-02)
#
# 이 상수(0.20, §3.1)와 `strategy/futures/aggregator.REGIME_WEIGHTS`(§7.1)는 **서로를 모른 채**
# 다른 절에서 정해졌는데, S의 정의상 둘은 곱해진다. 단일 30m 전문가일 때
#
#     |S| <= w(국면, 30m) x (1 - flat_share)   ← flat_share는 레이블이 정하는 상한
#
# 이고 30m flat 76.4%(2025-12-12~2026-09-01 실측)이므로 국면별 실효 천장은
#
#     추세 1.5 x 0.236 = 0.355   고변동 0.8 x 0.236 = 0.190   횡보 0.4 x 0.236 = 0.095
#
# 즉 **횡보·고변동에서는 모델이 아무리 확신해도 게이트 0.20에 산술적으로 못 닿는다.** 라이브
# 실측이 정확히 그 모양이다 — 2026-08-21~09-02의 123 사이클에서 게이트를 넘은 6건이 **전부**
# 추세 국면이고, 횡보 60건·고변동 34건은 한 건도 없다.
#
# 그건 설계가 아니라 두 상수의 우연한 곱이었다. 표를 명시해 두는 이유는 값을 바꾸려는 게
# 아니라, **다음에 폭이나 가중치를 만지는 사람이 이 결합을 보고 결정하게** 하려는 것이다.
# 실효 천장 자체는 `models/label_geometry.regime_reachability()`가 매번 다시 계산한다 —
# 위 숫자는 그 도구가 낸 값의 사본이고, 갈라지면 `tests/models/test_label_geometry.py`가 잡는다.
#
# **값을 바꾸는 것은 위험 성향을 바꾸는 변경이다**(DECISION_LOG 2026-08-24와 같은 규율) —
# 특히 낮추는 방향은, 2026-09-02 반사실 측정에서 고변동 국면의 방향 적중률이 34건 중 21%
# (이항 p=0.0004)로 나왔기 때문에 **지금 열면 가장 나쁜 국면부터 열린다.**
SCORE_THRESHOLD_BY_REGIME: dict[Regime, float] = {
    Regime.TREND_UP: SCORE_THRESHOLD,  # 실효 천장 0.355 — 도달 가능
    Regime.TREND_DOWN: SCORE_THRESHOLD,  # 실효 천장 0.355 — 도달 가능
    Regime.HIGH_VOL: SCORE_THRESHOLD,  # 실효 천장 0.190 — **도달 불가**
    Regime.RANGE: SCORE_THRESHOLD,  # 실효 천장 0.095 — **도달 불가**
    Regime.EVENT: SCORE_THRESHOLD,  # 게이트 ②가 먼저 접는다
    Regime.UNKNOWN: SCORE_THRESHOLD,  # 게이트 ②가 먼저 접는다
}

_EVENT_LIKE_REGIMES = frozenset({Regime.EVENT, Regime.UNKNOWN})

# 판단 사슬의 **관문 이름** (2026-08-12 G-1). 로그의 구조화 필드로 나가고 무결성 리포트가
# 이것을 세어 `decision_funnel`을 만든다.
#
# ## 왜 사유 문자열을 파싱하지 않고 코드가 직접 세나
#
# 2026-08-12에 `decide()`의 다섯 갈래 중 **②에서 14/14가 접혔다.** 그런데 그날 리포트가
# 아는 것은 `DecisionEmitted: 14`뿐이었다 — ③·④·⑤가 **한 번도 평가되지 않았다**는 사실,
# 즉 Risk Engine·Sizer·OrderGateway가 통째로 미검증 상태라는 사실이 어디에도 안 남았다.
#
# 사유 문자열(`rationale`)에 ①~⑤가 이미 들어 있지만 그것을 사후에 정규식으로 긁는 것은
# 다른 실패다: 문구를 다듬는 순간 조용히 0이 된다. **코드가 자기 갈래를 직접 말한다.**
GATE_KILL = "kill"  # ① sys.kill 활성
# **입력 부재는 우위 부족이 아니다** (2026-08-18 F-0818I-1, 원안은 2026-08-13 장중 F-1).
#
# 기여 전문가가 0명이면 Aggregator 폴백이 `score=0.0 · dispersion=0.0`을 내고, 그 0.0이
# ③(분산 0.25)을 무사통과해 ④에서 "|S|=0.000 — 우위 부족"으로 접혔다. 2026-08-18 실측
# 9사이클 전부가 이 형태로 `gate=score`에 잡혀, Go/No-Go ④("우위 부족이 며칠인가") 채점이
# 판단력과 무관한 배선 문제로 오염됐다. 의견이 약한 것과 의견이 없는 것은 다른 사건이다.
#
# ②(regime) **앞**에 두는 이유: 국면 전파가 어긋난 사이클(2026-08-18 장중 1-1)에서
# regime 갈래가 이 갈래를 가리면, 입력 부재가 국면 문제로 또 한 번 위장된다.
GATE_NO_EXPERT = "no_expert"  # ①′ 기여 전문가 0명 — 입력 부재
GATE_REGIME = "regime"  # ② 이벤트/미판정 국면
GATE_DISPERSION = "dispersion"  # ③ 전문가 의견 분산
GATE_SCORE = "score"  # ④ |S| 미달
GATE_PASS = "pass"  # ⑤ 통과 — 여기까지 와야 Risk·Sizer·OrderGateway가 돈다
DECISION_GATES = (GATE_KILL, GATE_NO_EXPERT, GATE_REGIME, GATE_DISPERSION, GATE_SCORE, GATE_PASS)


@dataclass(frozen=True)
class MetaDecisionConfig:
    dispersion_threshold: float = DISPERSION_THRESHOLD
    score_threshold: float = SCORE_THRESHOLD
    # 국면별 게이트(위 표). 표에 없는 국면은 `score_threshold`로 떨어진다 — 새 국면이
    # 생겨도 게이트가 조용히 0이 되지 않는다.
    score_threshold_by_regime: Mapping[Regime, float] | None = None

    def __post_init__(self) -> None:
        """표를 안 넘긴 호출부의 `score_threshold`가 **말한 대로 동작하게** 한다.

        표를 무조건 기본값으로 채우면 `MetaDecisionConfig(score_threshold=0.5)`가 조용히
        0.20으로 돌아간다 — 인자가 이름과 다르게 동작하는 형태라, 스윕·테스트가 재는 것이
        재려던 것과 달라진다. 그래서: 표를 명시했으면 그대로, 아니면 `score_threshold`로
        **균일한 표를 짓는다**(기본값 0.20이면 결과적으로 `SCORE_THRESHOLD_BY_REGIME`과
        같은 표다 — 지금 그 표가 균일하기 때문이고, 균일하지 않게 바꾸는 순간
        `tests/strategy/decision/test_meta_decision.py`가 이 문장을 다시 보게 만든다).
        """
        if self.score_threshold_by_regime is None:
            table = (
                dict(SCORE_THRESHOLD_BY_REGIME)
                if self.score_threshold == SCORE_THRESHOLD
                else {regime: self.score_threshold for regime in Regime}
            )
            object.__setattr__(self, "score_threshold_by_regime", table)

    def threshold_for(self, regime: Regime) -> float:
        """그 국면의 우위 게이트 — 표에 없으면 `score_threshold`."""
        table = self.score_threshold_by_regime or {}
        return table.get(regime, self.score_threshold)


class MetaDecisionEngine:
    def __init__(self, config: MetaDecisionConfig | None = None) -> None:
        self._config = config or MetaDecisionConfig()

    def decide(self, view: FuturesView, *, kill_active: bool) -> DecisionIntent:
        cfg = self._config
        # 어느 갈래로 접히든 **그 사이클에 적용됐을 게이트**를 로그에 싣는다 — 국면별 표를
        # 도입한 뒤로는 "임계가 얼마였나"가 사이클마다 다를 수 있고, 사후에 재구성할 수
        # 있어야 `models/regime_direction.py`의 채점이 로그만으로 성립한다.
        threshold = cfg.threshold_for(view.regime)

        if kill_active:
            return self._no_trade(
                view, "① sys.kill 활성 — 무조건 NO TRADE", gate=GATE_KILL, threshold=threshold
            )
        if view.n_experts == 0:
            return self._no_trade(
                view,
                "①′ n_experts=0 — 기여 의견 없음(입력 부재, 우위 부족 아님)",
                gate=GATE_NO_EXPERT,
                threshold=threshold,
            )
        if view.regime in _EVENT_LIKE_REGIMES:
            return self._no_trade(
                view,
                f"② Regime={view.regime.value} — 이벤트/미판정 국면",
                gate=GATE_REGIME,
                threshold=threshold,
            )
        if view.dispersion > cfg.dispersion_threshold:
            return self._no_trade(
                view,
                f"③ 전문가 의견 분산 {view.dispersion:.3f} > {cfg.dispersion_threshold} — "
                "의견이 갈리면 배팅하지 않는다",
                gate=GATE_DISPERSION,
                threshold=threshold,
            )
        if abs(view.score) < threshold:
            return self._no_trade(
                view,
                f"④ |S|={abs(view.score):.3f} < {threshold:g} — 우위 부족",
                gate=GATE_SCORE,
                threshold=threshold,
            )

        side = Side.LONG if view.score >= threshold else Side.SHORT
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
                f"⑤ S={view.score:.3f} (임계 ±{threshold:g}) → {side.value}, "
                f"n_experts={view.n_experts}, dispersion={view.dispersion:.3f}"
            ),
            # 판단은 뷰 도착에만 나간다 — 갱신 간격은 뷰의 것 그대로다
            # (`DecisionIntent.cadence_seconds` 주석).
            cadence_seconds=view.cadence_seconds,
        )
        mlog.log(
            "DecisionEmitted",
            intent.rationale,
            symbol=view.symbol,
            side=side.value,
            confidence=confidence,
            gate=GATE_PASS,
            **self._view_fields(view, threshold),
        )
        return intent

    @staticmethod
    def _view_fields(view: FuturesView, threshold: float) -> dict[str, object]:
        """판단 값 계측 (2026-08-18 F-0818I-1) — 모든 `DecisionEmitted`가 같은 필드를 싣는다.

        종전엔 통과 경로와 차단 경로의 관측 스키마가 달랐고, 차단 경로는 값 자체를 안 실었다.
        그래서 2026-08-18까지 `gate=score` 9건이 실제로 |S|가 얼마였는지(0.000인지 0.19인지)를
        rationale 문자열을 파싱해야만 알 수 있었다 — 문구를 다듬는 순간 조용히 0이 되는
        바로 그 형태다(`DECISION_GATES` 주석). rationale은 사람 몫, 필드는 집계 몫이다.

        ## `regime`·`score_threshold`를 여기 싣는 이유 (2026-09-02)

        2026-09-02 반사실 분석은 **국면별 손익**을 재려고 `DecisionEmitted`와
        `RegimeClassified`를 시각으로 조인해야 했다. 판단이 자기가 어느 국면에서 내려졌는지를
        안 적었기 때문이다 — 조인은 두 로그의 시각이 밀리초 단위로 어긋나는 순간 조용히
        틀리고, 실제로 42건이 국면 미상으로 빠졌다. 판단은 자기 조건을 스스로 말해야 한다.
        """
        return {
            "n_experts": view.n_experts,
            "score": view.score,
            "dispersion": view.dispersion,
            "uncertainty": view.uncertainty,
            "regime": view.regime.value,
            "score_threshold": threshold,
        }

    def _no_trade(
        self, view: FuturesView, rationale: str, *, gate: str, threshold: float
    ) -> DecisionIntent:
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
            # NO_TRADE도 **같은 주기로 나간다** — 화면에서 이쪽만 STALE로 남으면 "판단이
            # 멈췄다"로 읽힌다. 실제 발행의 대부분이 이 경로다.
            cadence_seconds=view.cadence_seconds,
        )
        mlog.log(
            "DecisionEmitted",
            rationale,
            symbol=view.symbol,
            side="NO_TRADE",
            gate=gate,
            **self._view_fields(view, threshold),
        )
        return intent
