"""TradingPipeline — L3→L4→L5 전 경로 관통 오케스트레이터 (Ver 2.0 §1·§2, Ver 2.0 §9 W24~26
"Aggregator·Meta Decision·Risk Engine·Sizer·Kill Switch (전 경로 관통)").

`intel.futures`(Aggregator 산출물) 하나가 도착할 때마다 Ver 2.0 §2 워크스루를 그대로 실행한다:

    FuturesView 도착
      → Kill Switch 재평가(일일손실·데이터단절 지속) → 발동 시 즉시 보유 전량 청산
      → Meta Decision Engine.decide() → decision.intent 발행(항상, NO TRADE 포함)
      → NO TRADE면 종료
      → CostModel(왕복비용) + ATR(기대이동폭 근사) → Net Expected Return
      → Risk Engine.evaluate()(거부권) → 거부면 종료(RiskReject 로그로 이미 기록됨)
      → Position Sizer.size() → 0계약이면 종료(SizerZeroQty로 이미 기록됨)
      → OrderGateway.submit() → capital.order_request 경로(OrderGateway 내부)

## Net Expected Return 산출 (알려진 갭 — 명시적 근사)

Ver 1.1 §4-1 "Meta 의도의 기대수익에서 비용을 차감"이 요구하는 "기대수익"은 이 시스템
어디에도 아직 크기(magnitude) 예측 모델이 없다 — `HorizonExpert`는 방향 확률만 내고
움직임 폭은 예측하지 않는다(`strategy/futures/expert.py` 설계 자체가 그렇다). 이 파이프라인은
`edge = clip(2×confidence−1, 0, 1)`(Sizer와 동일한 근사, `risk/sizer.py` 참고) ×
`ATR(M1, 14봉)`(기대이동폭 근사)로 기대수익을 추정한다 — 원문이 명시한 공식이 아니라 이
구현의 명시적 선택이다. 크기 예측 Expert나 실측 캘리브레이션이 생기면 교체할 자리.

## ATR·시장충격 재료는 M1봉 전용

`CostModel`(시장충격)과 위 기대수익 근사(ATR)는 전부 `bar.1m.{symbol}`만 구독해 계산한다 —
Aggregator의 S는 이미 여러 Horizon을 통합한 값이라 "어느 Horizon 기준으로 비용·기대이동폭을
잴 것인가"에 정답이 없다(`strategy/decision/meta_decision.py`가 `horizon=None`을 항상 내는
것과 같은 이유) — 가장 촘촘하고 항상 최신인 M1을 공통 재료로 택했다.

## R4·R6(오버나이트) 결선 — Event Calendar 도입(2026-07-27)

`event_calendar`를 생성자에 주입하면 `handle_futures_view()`가 매 호출마다
`EventCalendar.minutes_to_close(view.ts_utc)`를 계산해 `RiskEngine.evaluate()`에 넘긴다
— 정규장 중이 아니면(또는 미주입 시) `None`이라 R4/R6 게이트는 조용히 비활성 상태로
남는다(risk_engine.py 모듈 docstring 참고). 재생/스모크(`run_full_path_smoke.py` 등)처럼
KRX 세션 개념이 무의미한 경로는 지금까지처럼 `event_calendar`를 안 넘기면 된다.

## R10(연속손실) 결선은 이번 스코프 밖

`RiskEngine.record_trade_result()`는 진입가·청산가를 매칭해 실현손익을 계산하는 포지션
추적기(Ver 1.1 §5-3 Position Reconciler)가 있어야 호출 가능하다 — 그 컴포넌트가 아직 없어
(알려진 갭) 이 파이프라인은 호출하지 않는다. `record_order_error()`(R12)는 `gateway.submit()`
실패를 그대로 반영해 결선했다(브로커 거부·pending 이중등록 등 — `execution/order_gateway.py`
`submit()`이 `None`을 반환하는 모든 경우).

## 거래소 서킷브레이커(CB) 자동 대응 ("미륵" 대응 설계 반영)

`circuit_breaker_monitor`를 주입하면(미주입 시 기존 동작 그대로, `event_calendar`와 동일한
옵션 패턴) 매 `handle_futures_view()` 호출마다 이미 계산해둔 `data_age_seconds`를
`CircuitBreakerMonitor.observe()`에 넘겨 CB 여부를 재평가한다.

- CONFIRMED 최초 도달(`just_confirmed`) → `gateway.halt()`. 청산은 시도하지 않는다 — 정지
  중엔 거래소가 매매 자체를 막으므로 주문이 거부될 뿐이다(미륵의 설계와 동일한 판단).
- 데이터 재수신으로 해제 추정(`just_resumed`) → **사람 확인 없이 자동으로**
  `_liquidate_after_circuit_breaker()`가 보유 포지션 전량을 EMERGENCY로 강제청산한 뒤
  게이트를 재개한다. `KillSwitch`(이상 상황 → 사람 확인 후 재가동)와 다른 철학을 의도적으로
  택했다 — CB는 알려진·시장 전체·일시적 이벤트라 자동 복구가 합당하다는 판단
  (`risk/circuit_breaker_monitor.py` 모듈 docstring 참고).
- 데이터가 끊긴 동안은 이 메서드 자체가 호출되지 않으므로(완전 이벤트 구동 구조), 정지 중에도
  단계적으로 phase가 올라가려면 별도 벽시계 워치독이 필요하다 — `watch_circuit_breaker_forever()`
  가 `core/scheduler.py`의 `FixedTickScheduler`로 이를 담당한다.
- **콜드스타트 오탐 방지**: `_last_bar_confirm_at is None`(봉을 한 번도 못 본 워밍업 구간)이면
  CB 판정 자체를 건너뛴다 — 이 경우 `_data_age_seconds()`가 `inf`를 반환하는데(R11 관점에선
  "최대 위험"이 맞는 값), CB 판정에 그대로 흘리면 "기준선 자체가 없음"을 "CB로 갑자기
  끊김"으로 오판한다. 2026-07-29 실전 재시작 직후 워치독 첫 틱이 정확히 이 경로로
  `CircuitBreakerConfirmed(데이터단절 infs)` → 60초 뒤 `CircuitBreakerResumed`라는 거짓
  CB 이벤트를 실제로 찍은 것을 보고 발견·수정.

**`KillSwitch`의 R11(전면정지, 30초 지속)과의 충돌 회피**: `circuit_breaker_monitor`가
WARNING 이상이거나 이번 호출이 `just_resumed`면, `kill_switch.evaluate()`에는
`data_age_seconds=0.0`을 넘겨 R11이 같은 데이터단절로 별도 발동하지 못하게 한다. 이게 없으면
"CB 해제 감지 → 자동청산 → `gateway.resume()`"을 마치자마자 같은 호출 안에서 KillSwitch의
R11(같은 데이터단절 값을 봄)이 뒤이어 `gateway.halt("kill switch triggered")`를 걸어버려
자동 복구가 무의미해진다(사람이 KillSwitch를 `reset()`해야만 다시 풀림 — 미륵 방식 자동복구를
선택한 취지와 모순). 즉 `circuit_breaker_monitor`를 주입하면 데이터단절에 대한 전면정지
판단은 이 컴포넌트가 전담하고, KillSwitch는 R2(일일손실)·수동·모델이상만 계속 감시한다.

**Command Center UI 배지 (2026-07-29)**: `observe()`를 호출할 때마다(이벤트 구동 경로 +
벽시계 워치독 양쪽) `sys.circuit_breaker`에 `CircuitBreakerStatus`를 발행한다 — `ui/app.py`
가 이를 구독해 phase별 색상 배지로 보여준다(`core/messages.py`의 `CircuitBreakerStatus`
docstring 참고).

**스코프 밖(알려진 갭)**: 코스피 현물지수 기반 선제 감지(RG 데이터소스 미착수), 재개 후
피처/국면 버퍼 리셋(`FeatureEngine`/`RegimeRuntime`에 reset() API 없음), 능동 알림(Slack 등
인프라 없음), halt 이력 DB 영속화(EOD exporter 없음). 임계값(90/150/240초, 재진입 관망
10분)은 미륵의 실측 보정값을 차용한 미검증 초기값.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from decimal import Decimal

from messiah.broker.base import BrokerAdapter, BrokerPosition
from messiah.core.bus import TOPIC_BAR, TOPIC_CIRCUIT_BREAKER, TOPIC_FUTURES, TOPIC_INTENT, BusLike
from messiah.core.event_calendar import EventCalendar
from messiah.core.logging import log
from messiah.core.messages import (
    BarClosed,
    BusMessage,
    CircuitBreakerStatus,
    FuturesView,
    Horizon,
    Side,
    bar_confirm_time,
)
from messiah.core.scheduler import FixedTickScheduler
from messiah.core.timeutil import now_utc
from messiah.execution.order_gateway import OrderGateway
from messiah.features.px_core import atr as compute_atr
from messiah.models.labeling import DEFAULT_ATR_WINDOW
from messiah.risk.circuit_breaker_monitor import (
    CircuitBreakerEvent,
    CircuitBreakerMonitor,
    CircuitBreakerPhase,
)
from messiah.risk.cost_model import CostModel
from messiah.risk.kill_switch import KillSwitch
from messiah.risk.risk_engine import RiskEngine
from messiah.risk.sizer import PositionSizer
from messiah.strategy.decision.meta_decision import MetaDecisionEngine

_BAR_HISTORY_LIMIT = 200


class TradingPipeline:
    def __init__(
        self,
        symbol: str,
        broker: BrokerAdapter,
        gateway: OrderGateway,
        bus: BusLike,
        *,
        cost_model: CostModel | None = None,
        risk_engine: RiskEngine | None = None,
        sizer: PositionSizer | None = None,
        decision_engine: MetaDecisionEngine | None = None,
        kill_switch: KillSwitch | None = None,
        tick_size: Decimal = Decimal("0.02"),
        atr_window: int = DEFAULT_ATR_WINDOW,
        event_calendar: EventCalendar | None = None,
        circuit_breaker_monitor: CircuitBreakerMonitor | None = None,
    ) -> None:
        self._symbol = symbol
        self._broker = broker
        self._gateway = gateway
        self._bus = bus
        self._cost_model = cost_model or CostModel()
        self._risk_engine = risk_engine or RiskEngine()
        self._sizer = sizer or PositionSizer()
        self._decision_engine = decision_engine or MetaDecisionEngine()
        self._kill_switch = kill_switch or KillSwitch(bus)
        self._tick_size = tick_size
        self._atr_window = atr_window
        # 미지정(None)이면 R4/R6는 RiskEngine.evaluate()가 그냥 건너뛴다(기존 동작, 회귀
        # 없음) — 재생/스모크처럼 실제 KRX 세션 개념이 없는 경로를 위한 기본값(모듈
        # docstring에 근거 없음 — risk_engine.py 쪽 docstring 참고).
        self._event_calendar = event_calendar
        # 미지정(None)이면 CB 대응 전체가 비활성(기존 동작, 회귀 없음) — 모듈 docstring
        # "거래소 서킷브레이커 자동 대응" 절 참고.
        self._circuit_breaker_monitor = circuit_breaker_monitor
        self._bars: deque[BarClosed] = deque(maxlen=_BAR_HISTORY_LIMIT)
        self._last_bar_confirm_at: datetime | None = None
        self._daily_start_equity: Decimal | None = None

    async def start_day(self) -> None:
        """장 시작 시 1회 호출 — 당일 시작 자본을 현재 계좌 스냅샷으로 고정하고(R2·Kill
        Switch 기준선) Risk Engine의 일일 상태(R10·R12)를 리셋한다."""
        account = await self._broker.account()
        self._daily_start_equity = account.total_equity
        self._risk_engine.reset_daily()

    async def handle_bar(self, bar: BarClosed) -> None:
        if bar.symbol != self._symbol or bar.horizon != Horizon.M1:
            return
        self._bars.append(bar)
        self._last_bar_confirm_at = bar_confirm_time(bar)

    async def handle_futures_view(self, view: FuturesView) -> None:
        if view.symbol != self._symbol:
            return
        if self._daily_start_equity is None:
            await self.start_day()

        account = await self._broker.account()
        positions = await self._broker.positions()
        data_age_seconds = self._data_age_seconds(view.ts_utc)

        circuit_breaker_active = False
        kill_switch_data_age_seconds = data_age_seconds
        # `self._last_bar_confirm_at is None`(봉을 한 번도 못 봄 — 콜드스타트/워밍업)이면
        # `data_age_seconds`가 inf가 되는데(§ 아래 `_data_age_seconds` 참고), 이건 "이전에
        # 흐르던 데이터가 끊겼다"가 아니라 "아직 기준선 자체가 없다"는 뜻이라 CB 판정 대상이
        # 아니다 — 실측: 2026-07-29 14:40 재시작 직후 워치독 첫 틱이 이걸 오판해 시작하자마자
        # "CircuitBreakerConfirmed(데이터단절 infs)"를 찍고 60초 뒤 "해제"되는 거짓 CB
        # 이벤트가 실전에서 재현됨 — R11(RiskEngine)은 이 경우에도 이미 신규진입을 막으므로
        # (`data_age_seconds > 30s`) 여기서 건너뛰어도 안전 공백은 없다.
        if self._circuit_breaker_monitor is not None and self._last_bar_confirm_at is not None:
            cb_event = self._circuit_breaker_monitor.observe(data_age_seconds, view.ts_utc)
            await self._publish_circuit_breaker_status(cb_event)
            if cb_event.just_confirmed:
                await self._gateway.halt("circuit breaker confirmed")
            if cb_event.just_resumed:
                await self._liquidate_after_circuit_breaker(positions)
                positions = await self._broker.positions()  # 청산 반영된 최신 스냅샷
            circuit_breaker_active = self._circuit_breaker_monitor.blocks_entry(view.ts_utc)
            # KillSwitch R11과의 충돌 회피 — 모듈 docstring 참고.
            if cb_event.phase != CircuitBreakerPhase.NORMAL or cb_event.just_resumed:
                kill_switch_data_age_seconds = 0.0

        kill_triggered = await self._kill_switch.evaluate(
            account=account,
            daily_start_equity=self._daily_start_equity or account.total_equity,
            data_age_seconds=kill_switch_data_age_seconds,
        )
        if kill_triggered:
            await self._gateway.halt("kill switch triggered")
            for request in self._kill_switch.liquidate(positions):
                await self._gateway.submit(request)

        intent = self._decision_engine.decide(view, kill_active=kill_triggered)
        await self._bus.publish(TOPIC_INTENT, intent)
        if intent.side == Side.NO_TRADE:
            return

        bars = list(self._bars)
        atr_ticks = compute_atr(bars, self._atr_window) if bars else None
        if atr_ticks is None or atr_ticks <= 0:
            return  # ATR 워밍업 미달 — 다른 컴포넌트의 워밍업 관례와 동일하게 조용히 대기

        cost = self._cost_model.estimate_round_trip_from_bars(bars, qty=1)
        edge = max(0.0, min(1.0, 2.0 * intent.confidence - 1.0))
        net_expected_return_ticks = edge * atr_ticks - cost.total_ticks

        minutes_to_close = (
            self._event_calendar.minutes_to_close(view.ts_utc) if self._event_calendar else None
        )
        risk_decision = self._risk_engine.evaluate(
            intent=intent,
            net_expected_return_ticks=net_expected_return_ticks,
            account=account,
            positions=positions,
            daily_start_equity=self._daily_start_equity or account.total_equity,
            data_age_seconds=data_age_seconds,
            as_of=view.ts_utc,
            minutes_to_close=minutes_to_close,
            circuit_breaker_active=circuit_breaker_active,
        )
        if not risk_decision.approved:
            return

        qty = self._sizer.size(
            intent=intent,
            equity=account.total_equity,
            tick_size=self._tick_size,
            stop_distance_ticks=atr_ticks,
        )
        if qty == 0:
            return

        order = self._sizer.build_order_request(
            intent=intent,
            qty=qty,
            net_expected_return=Decimal(str(net_expected_return_ticks)),
        )
        ack = await self._gateway.submit(order)
        if ack is None:
            self._risk_engine.record_order_error(view.ts_utc)

    def _data_age_seconds(self, as_of: datetime) -> float:
        if self._last_bar_confirm_at is None:
            return float("inf")  # 데이터를 한 번도 못 봤음 — R11 관점에서 최대 위험
        return (as_of - self._last_bar_confirm_at).total_seconds()

    async def _liquidate_after_circuit_breaker(self, positions: list[BrokerPosition]) -> None:
        """CB 해제 추정 직후 사람 확인 없이 자동 강제청산(모듈 docstring "거래소 서킷브레이커
        자동 대응" 참고). `KillSwitch.liquidate()`는 순수 변환 함수라 KillSwitch를 트리거하지
        않고도 재사용 가능 — EMERGENCY는 `gateway.halted`와 무관하게 통과하므로(`OrderGateway.
        submit()` 기존 로직) `resume()`보다 먼저 제출해도 안전하다."""
        requests = self._kill_switch.liquidate(positions)
        for request in requests:
            log(
                "CircuitBreakerLiquidating",
                f"{request.symbol} {request.qty}계약 CB 해제 강제청산",
                symbol=request.symbol,
                qty=request.qty,
            )
            await self._gateway.submit(request)
        await self._gateway.resume(operator="circuit_breaker_monitor")

    async def _publish_circuit_breaker_status(self, event: CircuitBreakerEvent) -> None:
        """Command Center UI 배지용 heartbeat (`sys.circuit_breaker`) — phase가 그대로여도
        매번 발행한다(모듈 `core/messages.py`의 `CircuitBreakerStatus` docstring 근거,
        `Health`와 같은 heartbeat 철학)."""
        await self._bus.publish(
            TOPIC_CIRCUIT_BREAKER,
            CircuitBreakerStatus(
                symbol=self._symbol,
                phase=event.phase.value,
                reentry_cooldown_until=event.reentry_cooldown_until,
            ),
        )

    async def watch_circuit_breaker_forever(self) -> None:
        """`handle_futures_view()`는 `FuturesView` 도착이 있어야만 실행되는 완전 이벤트 구동
        경로라, 데이터가 끊긴 동안은 CB phase가 전혀 갱신되지 않는다 — 이 메서드가
        `FixedTickScheduler`(드리프트 없는 고정 틱, `core/scheduler.py`)로 벽시계 기준 관찰을
        더해 정지 중에도 단계적으로 phase가 올라가게 한다. 청산은 시도하지 않는다(재개 시점에만
        — 모듈 docstring 근거와 동일: 정지 중엔 주문 자체가 거부된다). 모니터 미주입이면
        즉시 반환."""
        monitor = self._circuit_breaker_monitor
        if monitor is None:
            return

        async def _tick() -> None:
            if self._last_bar_confirm_at is None:
                return  # 콜드스타트/워밍업 — 기준선 없음, CB 판정 대상 아님(위 docstring 근거)
            data_age_seconds = self._data_age_seconds(now_utc())
            event = monitor.observe(data_age_seconds, now_utc())
            await self._publish_circuit_breaker_status(event)
            if event.just_confirmed:
                await self._gateway.halt("circuit breaker confirmed")

        await FixedTickScheduler(tick_seconds=30.0).run_forever(_tick)

    async def run_forever(self) -> None:
        patterns = [f"{TOPIC_BAR}.{Horizon.M1.value}.{self._symbol}", TOPIC_FUTURES]
        await self._bus.subscribe(patterns, self._dispatch)

    async def _dispatch(self, msg: BusMessage) -> None:
        if isinstance(msg, BarClosed):
            await self.handle_bar(msg)
        elif isinstance(msg, FuturesView):
            await self.handle_futures_view(msg)
