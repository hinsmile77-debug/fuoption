"""FuturesAIService — feat.* 구독 → Expert → MetaLabeler → Aggregator → intel.futures 발행
(Ver 1.2 §9 모듈 구조의 `service.py`, "프로세스 진입점" — Ver 2.0 §9 W24~26에서 처음 배선).

`strategy/futures/expert.py`(HorizonExpert)와 `strategy/futures/meta_labeler.py`
(MetaLabeler)는 각자 W14~19에 이미 구현됐지만 실시간 파이프라인에 연결된 적이 없었다(각
모듈 docstring). 이 클래스가 그 결선이다:

    feat.{h}.{symbol} 도착
      → HorizonExpert.predict()                (meta_passed는 항상 True — expert.py 원칙)
      → MetaLabeler.passes()로 실제 판정해 meta_passed 덮어쓰기
      → 최신 Horizon별 ExpertView 캐시 갱신
      → Aggregator.compute(캐시 전체, 최신 RegimeState) → FuturesView
      → intel.futures 발행

intel.regime은 별도로 구독해(`strategy/regime/runtime.py`가 발행) 최신 `RegimeState`만
캐시한다 — 아직 한 번도 안 왔으면 UNKNOWN으로 대체(Ver 1.1 §3-1 "판단 불가 → 하위 AI 보수
모드"와 동일 철학, `_UNSEEN_REGIME`).

## BarClosed 재구독 없음

`MetaLabeler`의 시간대 메타 Feature는 원래 `BarClosed`가 필요하지만(`build_meta_features()`),
이 서비스는 `build_meta_features_from_feature_vector()`(meta_labeler.py 신규, W24~26)를 써서
`FeatureVector.valid_until`만으로 역산한다 — `bar.*`를 별도 구독하면 `FeatureEngine`과 구독
순서에 의존하게 돼(둘 다 같은 `bar.*`를 구독하면 `InProcessBus`의 핸들러 등록 순서가 곧
실행 순서라 결과가 취약해진다) 이 방식을 택했다.

## Meta-Labeler 없는 Horizon

`meta_labelers`에 없는 Horizon은 필터링 없이(`meta_passed=True` 그대로) 집계에 포함한다 —
아직 정식 학습을 안 거친 Horizon(예: 프로토타입만 있는 경우)도 배관 자체는 끊기지 않게 하기
위함이다. 프로덕션에서는 전 Horizon에 MetaLabeler를 붙이는 것이 원칙(Ver 1.2 §5.2 "Horizon
마다 별도 Meta-Labeler").
"""

from __future__ import annotations

from typing import Mapping

import numpy as np

from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_FEAT, TOPIC_FUTURES, TOPIC_REGIME, BusLike
from messiah.core.messages import (
    BusMessage,
    ExpertView,
    FeatureVector,
    Horizon,
    Regime,
    RegimeState,
)
from messiah.strategy.futures.aggregator import Aggregator
from messiah.strategy.futures.expert import HorizonExpert
from messiah.strategy.futures.meta_labeler import (
    MetaLabeler,
    build_meta_features_from_feature_vector,
)

_UNSEEN_REGIME = RegimeState(
    symbol="", regime=Regime.UNKNOWN, confidence=0.0, state_duration_bars=0
)


class FuturesAIService:
    def __init__(
        self,
        symbol: str,
        experts: Mapping[Horizon, HorizonExpert],
        bus: BusLike,
        *,
        meta_labelers: Mapping[Horizon, MetaLabeler] | None = None,
        aggregator: Aggregator | None = None,
    ) -> None:
        self._symbol = symbol
        self._experts = dict(experts)
        self._meta_labelers = dict(meta_labelers) if meta_labelers else {}
        self._aggregator = aggregator or Aggregator()
        self._bus = bus
        self._latest_regime: RegimeState = _UNSEEN_REGIME.model_copy(update={"symbol": symbol})
        self._latest_views: dict[Horizon, ExpertView] = {}

    @property
    def latest_views(self) -> dict[Horizon, ExpertView]:
        """조회 전용 사본 — 테스트·진단용."""
        return dict(self._latest_views)

    async def handle_regime(self, msg: BusMessage) -> None:
        if isinstance(msg, RegimeState) and msg.symbol == self._symbol:
            self._latest_regime = msg

    async def handle_feature(self, msg: BusMessage) -> None:
        if not isinstance(msg, FeatureVector) or msg.symbol != self._symbol:
            return
        expert = self._experts.get(msg.horizon)
        if expert is None:
            return
        view = expert.predict(msg)
        view = self._apply_meta_labeler(msg, view)
        self._latest_views[msg.horizon] = view
        await self._publish(msg)

    def _apply_meta_labeler(self, feature_vector: FeatureVector, view: ExpertView) -> ExpertView:
        meta = self._meta_labelers.get(feature_vector.horizon)
        if meta is None:
            return view  # 모듈 docstring: MetaLabeler 미보유 Horizon은 필터링 없이 통과
        probs = np.array([view.p_down, view.p_flat, view.p_up])
        meta_features = build_meta_features_from_feature_vector(probs, view.ens_std, feature_vector)
        # **확률을 버리지 않는다** (2026-08-18 F-0818I-1). 종전엔 `meta.passes()`가 내부에서
        # 확률을 계산하고 bool만 돌려줘, 이 파이프라인 어디에도 확률값이 남지 않았다.
        # 그 대가: 2026-08-11~18 관측 내내 `blocked_by_meta`가 13/14 사이클을 막는 동안
        # "임계 0.7에 얼마나 가까운가"를 아무도 몰랐다 — 벽이 내일 열릴지 몇 주 걸릴지를
        # 판단할 유일한 근거가 여기서 매 사이클 계산되고 그대로 증발하고 있었다.
        # 임계 자체는 건드리지 않는다(R18) — 말하게만 한다. 배선 Horizon이 30m 하나라
        # 하루 14줄이다.
        probability = meta.predict_pass_probability(meta_features)
        passed = probability >= meta.threshold
        mlog.log(
            "MetaGateEvaluated",
            f"meta {feature_vector.horizon.value} p={probability:.3f} "
            f"(임계 {meta.threshold:g}) → {'통과' if passed else '차단'}",
            symbol=view.symbol,
            horizon=feature_vector.horizon.value,
            probability=probability,
            threshold=meta.threshold,
            passed=passed,
            model_version=view.model_version,
        )
        return view.model_copy(update={"meta_passed": passed})

    async def _publish(self, trigger: FeatureVector) -> None:
        # as_of는 trigger.ts_utc(wall clock)가 아니라 trigger.valid_until(봉 도메인 시각,
        # = bar_confirm_time — features/engine.py가 채운 값)을 우선 사용한다 —
        # aggregator.py 모듈 docstring "FuturesView.ts_utc = as_of" 참고. 재생/스모크처럼
        # 봉 시각이 wall clock과 동떨어진 상황에서도 신선도·데이터단절(R11) 판정이 정확하려면
        # 이 파이프라인 전체가 같은 시각 도메인을 써야 한다.
        as_of = trigger.valid_until or trigger.ts_utc
        aggregate = self._aggregator.compute(
            self._symbol, self._latest_views, self._latest_regime, as_of=as_of
        )
        await self._bus.publish(TOPIC_FUTURES, aggregate)

    async def run_forever(self) -> None:
        patterns = [f"{TOPIC_FEAT}.{h.value}.{self._symbol}" for h in self._experts] + [
            TOPIC_REGIME
        ]
        await self._bus.subscribe(patterns, self._dispatch)

    async def _dispatch(self, msg: BusMessage) -> None:
        if isinstance(msg, FeatureVector):
            await self.handle_feature(msg)
        elif isinstance(msg, RegimeState):
            await self.handle_regime(msg)
