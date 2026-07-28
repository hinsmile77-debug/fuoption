"""OptionsAIService — bar.5m·intel.futures 구독 → surface→matrix→evaluator→safety →
intel.options 발행 진입점 (Ver 1.3 §10, §2 갱신주기표, Ver 2.0 §9 W30~31).

`FuturesAIService`(strategy/futures/service.py)와 같은 배선 스타일: 구독 → 캐시 갱신 →
계산 → 발행. 다른 점은 이 서비스가 방향을 스스로 만들지 않고 `intel.futures`를 그대로
재사용한다는 것(Ver 1.3 §1 "방향의 단일 출처") — `handle_futures_view()`는 점수만 캐싱한다.

## `smile_provider`로 Vol Engine 데이터 출처를 분리한다 (핵심 설계 결정)

`OptionQuoteSnapshot`(core/messages.py)이 아직 필드 미해석 상태라(L16 처방) 이 서비스가
`raw.option_chain.*`를 직접 구독해 실시간으로 `SmileFit`을 만들 수 없다 — 그 배선(원시
호가→중간가→`surface.fit_smile()`)은 필드 매핑이 확정된 뒤의 별도 작업이다(알려진 갭).
그래서 이 서비스는 "지금 쓸 수 있는 스마일이 뭔지" 자체를 묻지 않고, `smile_provider:
Callable[[], SmileFit | None]`라는 콜백에 위임한다 — 실데이터 배선이 생기면 그 콜백만
갈아끼우면 되고, 지금은 백테스트·시뮬레이터·단위테스트가 합성 스마일을 주입해 이 서비스의
나머지 로직(매트릭스→평가→안전규칙→발행)을 전부 검증할 수 있다(`broker/base.BrokerAdapter`가
KIS/시뮬레이터를 갈아끼우는 것과 같은 "동일 인터페이스" 철학, Ver 1.0.1 §2.1).

## `intel.futures` 미수신 상태에서는 항상 NO_OPTION

방향 뷰가 한 번도 안 왔으면 `_latest_score` 기본값(0.0)이 우연히 NEUTRAL로 분류돼 버릴 수
있다 — "아직 모른다"를 "중립 확인됨"으로 낙관 해석하면 안 된다(`strategy/futures/service.py`
의 `_UNSEEN_REGIME`과 동일 철학). `_has_futures_view` 플래그로 이 상태를 명시적으로 막는다.

## 안전규칙(safety.py) 통과 여부만 필터링 — 매도 전략 유예 정책 없음

`evaluate_candidate_safety()`가 거부한 후보는 조용히 버려진다(로그만 남김). 후보가 전부
기각되면 `NO_OPTION`으로 그 사실을 명시한다(Ver 1.3 §5.2 "NO_OPTION도 명시적 출력이다").
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_BAR, TOPIC_FUTURES, TOPIC_OPTIONS, BusLike
from messiah.core.event_calendar import EventCalendar
from messiah.core.messages import BarClosed, BusMessage, FuturesView, Horizon, OptionsView
from messiah.strategy.options.config import OptionsConfig
from messiah.strategy.options.evaluator import (
    EvaluatorConfig,
    evaluate_candidate,
    rank_candidates,
)
from messiah.strategy.options.matrix import candidate_specs
from messiah.strategy.options.safety import evaluate_candidate_safety
from messiah.strategy.options.surface import SmileFit
from messiah.strategy.options.vol_metrics import IVHistory

SmileProvider = Callable[[], SmileFit | None]


class OptionsAIService:
    def __init__(
        self,
        symbol: str,
        underlying: str,
        smile_provider: SmileProvider,
        bus: BusLike,
        *,
        iv_history: IVHistory | None = None,
        options_config: OptionsConfig = OptionsConfig(),
        evaluator_config: EvaluatorConfig = EvaluatorConfig(),
        r: float = 0.03,
        event_calendar: EventCalendar | None = None,
        top_n: int = 3,
    ) -> None:
        self._symbol = symbol
        self._underlying = underlying
        self._smile_provider = smile_provider
        self._bus = bus
        self._iv_history = iv_history or IVHistory()
        self._options_config = options_config
        self._evaluator_config = evaluator_config
        self._r = r
        self._event_calendar = event_calendar
        self._top_n = top_n
        self._latest_score: float = 0.0
        self._has_futures_view = False

    async def handle_futures_view(self, msg: BusMessage) -> None:
        if not isinstance(msg, FuturesView) or msg.symbol != self._symbol:
            return
        self._latest_score = msg.score
        self._has_futures_view = True
        await self._publish_view(as_of=msg.valid_until or msg.ts_utc)

    async def handle_bar(self, msg: BusMessage) -> None:
        if not isinstance(msg, BarClosed) or msg.symbol != self._symbol:
            return
        if msg.horizon != Horizon.M5:
            return
        await self._publish_view(as_of=msg.bar_open_kst)

    async def _publish_view(self, *, as_of: datetime) -> None:
        if not self._has_futures_view:
            await self._publish_no_option("Futures AI 방향 뷰 미수신")
            return

        smile = self._smile_provider()
        if smile is None:
            await self._publish_no_option("IV Surface 미준비")
            return

        atm_iv = smile.iv_at(smile.forward)
        self._iv_history.add(atm_iv)
        iv_rank = self._iv_history.rank(atm_iv)

        specs = candidate_specs(self._latest_score, iv_rank, self._options_config)
        if not specs:
            reason = "IV Rank 이력 부족" if iv_rank is None else "매트릭스 셀 후보 없음(관망)"
            await self._publish_no_option(reason)
            return
        assert iv_rank is not None  # specs가 비지 않았다는 것 자체가 iv_rank 판정됨의 증거

        is_expiry_day = (
            self._event_calendar.is_expiry_day(as_of.date()) if self._event_calendar else False
        )

        candidates = []
        for spec in specs:
            rationale = {
                "structure": spec.structure,
                "iv_rank": iv_rank,
                "score": self._latest_score,
            }
            candidate = evaluate_candidate(
                spec,
                smile,
                r=self._r,
                score=self._latest_score,
                config=self._evaluator_config,
                rationale=rationale,
            )
            if candidate is None:
                continue
            verdict = evaluate_candidate_safety(
                candidate,
                iv_rank=iv_rank,
                config=self._options_config,
                is_expiry_day=is_expiry_day,
            )
            if not verdict.allowed:
                mlog.log(
                    "OptionsCandidateRejected",
                    "; ".join(verdict.violations),
                    symbol=self._symbol,
                    structure=spec.structure,
                )
                continue
            candidates.append(candidate)

        if not candidates:
            await self._publish_no_option("생성된 후보가 전부 안전규칙에서 기각됨")
            return

        ranked = rank_candidates(candidates, top_n=self._top_n)
        view = OptionsView(symbol=self._symbol, underlying=self._underlying, candidates=ranked)
        await self._bus.publish(TOPIC_OPTIONS, view)

    async def _publish_no_option(self, reason: str) -> None:
        view = OptionsView(
            symbol=self._symbol, underlying=self._underlying, no_option_reason=reason
        )
        await self._bus.publish(TOPIC_OPTIONS, view)

    async def run_forever(self) -> None:
        patterns = [f"{TOPIC_BAR}.{Horizon.M5.value}.{self._symbol}", TOPIC_FUTURES]
        await self._bus.subscribe(patterns, self._dispatch)

    async def _dispatch(self, msg: BusMessage) -> None:
        if isinstance(msg, BarClosed):
            await self.handle_bar(msg)
        elif isinstance(msg, FuturesView):
            await self.handle_futures_view(msg)
