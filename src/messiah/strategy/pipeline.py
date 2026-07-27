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
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from decimal import Decimal

from messiah.broker.base import BrokerAdapter
from messiah.core.bus import TOPIC_BAR, TOPIC_FUTURES, TOPIC_INTENT, BusLike
from messiah.core.event_calendar import EventCalendar
from messiah.core.messages import (
    BarClosed,
    BusMessage,
    FuturesView,
    Horizon,
    Side,
    bar_confirm_time,
)
from messiah.execution.order_gateway import OrderGateway
from messiah.features.px_core import atr as compute_atr
from messiah.models.labeling import DEFAULT_ATR_WINDOW
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

        kill_triggered = await self._kill_switch.evaluate(
            account=account,
            daily_start_equity=self._daily_start_equity or account.total_equity,
            data_age_seconds=data_age_seconds,
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

    async def run_forever(self) -> None:
        patterns = [f"{TOPIC_BAR}.{Horizon.M1.value}.{self._symbol}", TOPIC_FUTURES]
        await self._bus.subscribe(patterns, self._dispatch)

    async def _dispatch(self, msg: BusMessage) -> None:
        if isinstance(msg, BarClosed):
            await self.handle_bar(msg)
        elif isinstance(msg, FuturesView):
            await self.handle_futures_view(msg)
