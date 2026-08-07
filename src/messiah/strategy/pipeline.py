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
`EventCalendar.minutes_to_close(self._now())`를 계산해 `RiskEngine.evaluate()`에 넘긴다
— 정규장 중이 아니면(또는 미주입 시) `None`이라 R4/R6 게이트는 조용히 비활성 상태로
남는다(risk_engine.py 모듈 docstring 참고). 재생/스모크(`run_full_path_smoke.py` 등)처럼
KRX 세션 개념이 무의미한 경로는 지금까지처럼 `event_calendar`를 안 넘기면 된다.

## 장전 구간(08:45~09:00)은 웜업만 — 거래는 안 한다 (2026-07-30, 사용자 결정)

실측으로 3거래일 연속 **08:45:00부터** 틱이 들어온다는 것이 확인됐다(`scripts/run_l1_daily.py`
모듈 docstring). 그 데이터를 버리지 않기로 했다 — 수집·아카이브·봉차트·피처 웜업에는 **그대로
쓴다**. 다만 정규장(09:00) 전에는 **주문을 내지 않는다**: 진입도 청산도 없이 시스템만 데워
놓고 개장을 기다린다.

게이트는 `decision.intent` 발행 **뒤**에 둔다. 판단 자체는 평소와 똑같이 수행하고 그 결과도
그대로 발행하되 주문 제출만 건너뛰는 것 — 그래야 "장전에 시스템이 무엇을 하려 했는가"가
화면과 로그에 남아 개장 직후 동작을 예측·검증할 수 있다(`OutOfSessionNoTrade` 로그).

같은 판정이 마감(15:35) 이후도 함께 막는다 — `EventCalendar.is_regular_session()`이
반개구간 [09:00, 15:35)이라 자연히 양쪽을 덮는다. `event_calendar` 미주입이면 이 게이트는
비활성(재생/스모크의 기존 동작 유지, R4/R6·CB와 같은 옵션 패턴).

**적용 범위**: 이 게이트는 `handle_futures_view()`의 신규 주문 경로만 막는다. KillSwitch·CB의
비상 청산 경로는 그대로 둔다 — 안전 이벤트에 대한 반응이고, 어차피 장 밖에서는 거래소가
주문을 거부하므로 막을 실익이 없다.

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
- **위 정지·해제 반응은 두 경로가 반드시 똑같이 한다** — `_apply_circuit_breaker_event()`
  하나만 통과하기 때문이다(2026-07-31 수정). 그 전에는 워치독 쪽에 해제 처리가 통째로 빠져
  있었고, live 번들이 0개라 `intel.futures`가 아예 안 오는 날에는 **해제 코드가 있는 유일한
  경로 자체가 죽어 있어** 게이트가 하루 종일 안 풀렸다(2026-07-31 실측 6시간 42분).

## "한산"과 "단절"의 구분 — 수집기 heartbeat 결선 (2026-07-31)

이 파이프라인은 자체 WS 연결이 없어(`scripts/run_g2_paper_trading.py`) 데이터 흐름의 실제
상태를 직접 잴 근거가 없다 — 아는 건 "마지막 **봉**이 언제 확정됐나"뿐이다. 그런데 봉은 체결이
있어야 생기므로, 체결이 뜸한 시장에서는 **연결이 멀쩡해도 봉이 안 만들어진다**. 2026-07-31의
CB 오탐 15건(의심 10·확정 5)이 전부 이 형태였다.

그래서 `run_forever()`가 `sys.health`를 함께 구독해 `l1.collector`의 heartbeat를 받아두고,
`observe(collector_healthy=...)`로 넘긴다 — 수집기가 OK라고 말하는 동안엔 CB가 CONFIRMED로
승격하지 못한다(SUSPECTED까지는 그대로 올라간다). 판정 근거와 완화 범위는
`risk/circuit_breaker_monitor.py` 모듈 docstring "한산과 단절" 절 참고.

heartbeat가 아예 없거나(구독 전/미발행) 끊긴 지 오래면 **"모른다"로 다뤄 억제하지 않는다** —
수집기 프로세스가 죽은 상황을 정상으로 오해하면 안 되기 때문이다(`_collector_healthy()`).
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
from typing import Callable

from messiah.broker.base import BrokerAdapter, BrokerPosition
from messiah.core.bus import (
    TOPIC_BAR,
    TOPIC_CIRCUIT_BREAKER,
    TOPIC_FUTURES,
    TOPIC_HEALTH,
    TOPIC_INTENT,
    BusLike,
)
from messiah.core.event_calendar import EventCalendar
from messiah.core.health import COLLECTOR_COMPONENT, HEALTH_STALE_AFTER_SECONDS
from messiah.core.logging import log
from messiah.core.messages import (
    BarClosed,
    BusMessage,
    CircuitBreakerStatus,
    FuturesView,
    Health,
    HealthLevel,
    Horizon,
    KillSignal,
    Side,
    bar_confirm_time,
)
from messiah.core.scheduler import FixedTickScheduler
from messiah.core.timeutil import now_utc, to_kst
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
        now: Callable[[], datetime] = now_utc,
    ) -> None:
        self._symbol = symbol
        self._broker = broker
        self._gateway = gateway
        self._bus = bus
        # 결선 완성도 계측 (2026-08-03 고도화 C) — `models/wiring_completeness.py` 참고.
        self._decisions_emitted = 0
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
        # 벽시계 주입점 (2026-07-30). 데이터 신선도(`data_age`)와 CB 판정의 기준시각은
        # **전부 이 시계 하나**에서 온다 — `handle_futures_view()`도, 순수 벽시계 폴링인
        # `watch_circuit_breaker_forever()`도.
        #
        # 2026-08-04까지 `handle_futures_view()`만 `view.ts_utc`(메시지 스탬프)를 썼는데,
        # 그게 백테스트를 구조적으로 무거래로 만들었다: `BusMessage.ts_utc`의 기본값이
        # `now_utc()`라 **재생 메시지가 지금 시각으로 스탬프**되는데 봉은 과거 것이라
        # `data_age = 지금 − 과거봉확정시각`이 된다. 실측(2026-07-27 재생): 672,056초(7.8일)
        # → KillSwitch R11(임계 30초)이 매 판단마다 발동 → 게이트웨이 전면정지 → 신규진입
        # 0건. `backtest/harness.py`가 만들어진 2026-07-27부터 있던 문제이며, 그동안
        # "무거래로 수익률 0%"가 데이터가 짧은 탓으로 기록돼 있었다(capability_matrix.md).
        #
        # 라이브에서는 두 값이 사실상 같다 — 방금 발행된 메시지의 스탬프와 지금 시각의 차이는
        # 밀리초다. 그래서 이 변경은 **라이브 동작을 바꾸지 않으면서** 재생 경로를 성립시킨다
        # (미해결 ②를 `TradingPipeline`에 시계 주입으로 푼 것과 같은 방향의 연장).
        self._now = now
        self._bars: deque[BarClosed] = deque(maxlen=_BAR_HISTORY_LIMIT)
        self._last_bar_confirm_at: datetime | None = None
        self._daily_start_equity: Decimal | None = None
        # 수집기의 마지막 heartbeat — CB 오탐 억제의 유일한 입력(모듈 docstring "한산과 단절",
        # `risk/circuit_breaker_monitor.py` 동명 절). 이 필드가 None이면 판정 자체를 안 한다.
        self._last_collector_health: Health | None = None

    @property
    def decisions_emitted(self) -> int:
        """그날 발행한 `DecisionIntent` 수(NO_TRADE 포함) — 결선 완성도 판정 입력.

        이 값이 0이면 승률·Sharpe 같은 손익 지표는 성적이 아니라 자리표시자다
        (`models/wiring_completeness.py`)."""
        return self._decisions_emitted

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
        # 신선도·CB 판정의 기준시각은 주입된 시계 하나로 통일한다(`__init__`의 `now` 주석
        # — 2026-08-04 이전엔 여기만 `view.ts_utc`를 써서 재생 경로가 항상 무거래였다).
        as_of = self._now()
        data_age_seconds = self._data_age_seconds(as_of)

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
            cb_event = self._circuit_breaker_monitor.observe(
                data_age_seconds,
                as_of,
                collector_healthy=self._collector_healthy(as_of),
            )
            await self._apply_circuit_breaker_event(cb_event, positions)
            if cb_event.just_resumed:
                positions = await self._broker.positions()  # 청산 반영된 최신 스냅샷
            circuit_breaker_active = self._circuit_breaker_monitor.blocks_entry(as_of)
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
        # NO_TRADE도 센다 — "판단이 나왔나"와 "거래가 나왔나"는 다른 질문이고, 결선 완성도가
        # 보려는 건 전자다(`models/wiring_completeness.py`). 2026-08-03에 이 값이 0이었다는
        # 사실이 "번들이 하나도 안 붙었다"는 진단의 근거였다.
        self._decisions_emitted += 1
        await self._bus.publish(TOPIC_INTENT, intent)
        if intent.side == Side.NO_TRADE:
            return

        # 정규장(09:00~15:35) 밖이면 여기서 멈춘다 — 판단은 다 하고 주문만 안 낸다.
        # `decision.intent`는 이미 발행됐으므로 "그때 시스템이 무엇을 하려 했는지"는 화면·
        # 로그에 그대로 남는다(모듈 docstring "장전 구간" 참고).
        if not self._in_regular_session(as_of):
            log(
                "OutOfSessionNoTrade",
                f"정규장 밖({to_kst(as_of):%H:%M}) — 판단은 수행, 주문은 생략",
                symbol=self._symbol,
                side=intent.side.value,
            )
            return

        bars = list(self._bars)
        atr_ticks = compute_atr(bars, self._atr_window) if bars else None
        if atr_ticks is None or atr_ticks <= 0:
            return  # ATR 워밍업 미달 — 다른 컴포넌트의 워밍업 관례와 동일하게 조용히 대기

        cost = self._cost_model.estimate_round_trip_from_bars(bars, qty=1)
        edge = max(0.0, min(1.0, 2.0 * intent.confidence - 1.0))
        net_expected_return_ticks = edge * atr_ticks - cost.total_ticks

        minutes_to_close = (
            self._event_calendar.minutes_to_close(as_of) if self._event_calendar else None
        )
        risk_decision = self._risk_engine.evaluate(
            intent=intent,
            net_expected_return_ticks=net_expected_return_ticks,
            account=account,
            positions=positions,
            daily_start_equity=self._daily_start_equity or account.total_equity,
            data_age_seconds=data_age_seconds,
            as_of=as_of,
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
            self._risk_engine.record_order_error(as_of)

    def _in_regular_session(self, as_of: datetime) -> bool:
        """`event_calendar` 미주입이면 항상 True — 재생/스모크처럼 KRX 세션 개념이 없는
        경로의 기존 동작을 그대로 둔다(R4/R6·CB 주입과 같은 옵션 패턴)."""
        if self._event_calendar is None:
            return True
        return self._event_calendar.is_regular_session(as_of)

    def _collector_healthy(self, as_of: datetime) -> bool | None:
        """수집기가 "내 연결은 멀쩡하다"고 말하고 있는가 — `None`은 "모른다"(정상 아님).

        세 가지를 구분한다:

        - `None` — heartbeat를 아직 못 받았거나 끊긴 지 오래됐다(`HEALTH_STALE_AFTER_SECONDS`).
          수집기 프로세스가 죽었을 수도 있는 상황이라 **정상으로 봐서는 절대 안 된다**.
          `CircuitBreakerMonitor.observe()`는 `None`이면 기존대로 CONFIRMED까지 승격한다.
        - `True` — OK. 소켓도 살아 있고 틱도 자기 임계 안에 들어오는데 봉만 안 만들어지는
          상태 = 한산이다. CB 승격을 막는다.
        - `False` — WARN/CRITICAL. 수집기 스스로 "흐름이 이상하다"고 말하는 중이라 CB가
          정지시키는 게 맞다. WARN도 정상으로 안 쳐주는 건, WARN이 뜨는 시점이 이미 적응
          임계의 절반이라 곧 강제 재연결이 걸릴 구간이기 때문이다(`data/collector.py`
          `health()`).

        ## `UNKNOWN`은 `None`과 같은 갈래다 (2026-08-05 2차, 고도화 3)

        이 함수는 처음부터 3분법이었는데 **발행 쪽에 "모른다"가 없었다.** 그래서 수집기가
        한 건도 못 받은 웜업 상태가 `OK`로 와서 여기 `True`가 됐고, 결과적으로
        **데이터가 0건인 것이 CB 억제 근거로 쓰였다** — 08:36~08:45의 9분, 그리고 재연결
        직후마다(워치독이 리셋된다). 실제 수정은 `core/health.staleness_status()`가 그
        구간을 `UNKNOWN`으로 내보내게 한 것이다.

        여기서 `False`가 아니라 `None`으로 접는 이유는 **의미** 때문이다. `False`는 "수집기가
        이상하다고 말하는 중"이라는 적극적 주장인데 `UNKNOWN`은 그런 주장을 한 적이 없다.
        (오늘의 `CircuitBreakerMonitor.observe()`는 `not collector_healthy`로 읽어 둘을
        구분하지 않지만, 그건 그쪽 구현의 사정이지 이 함수의 계약이 아니다.)
        """
        health = self._last_collector_health
        if health is None:
            return None
        if (as_of - health.ts_utc).total_seconds() > HEALTH_STALE_AFTER_SECONDS:
            return None
        if health.level is HealthLevel.UNKNOWN:
            return None
        return health.level is HealthLevel.OK

    def _data_age_seconds(self, as_of: datetime) -> float:
        if self._last_bar_confirm_at is None:
            return float("inf")  # 데이터를 한 번도 못 봤음 — R11 관점에서 최대 위험
        return (as_of - self._last_bar_confirm_at).total_seconds()

    async def _apply_circuit_breaker_event(
        self, event: CircuitBreakerEvent, positions: list[BrokerPosition] | None = None
    ) -> None:
        """CB 이벤트에 대한 **실제 실행**(정지·청산·재개·화면 발행)을 한 곳에 모은 것
        (2026-07-31 신설).

        이벤트 구동 경로(`handle_futures_view`)와 벽시계 워치독(`observe_circuit_breaker_tick`)
        둘 다 여기를 통과한다. 원래는 각자 자기 반응을 갖고 있었고, 그 결과 **워치독 쪽에만
        `just_resumed` 처리가 통째로 빠져 있었다** — 2026-07-31엔 Registry에 live 번들이 0개라
        `intel.futures`가 아예 발행되지 않아 `handle_futures_view()`가 하루 종일 한 번도 안
        불렸고(= 해제 코드가 있는 유일한 경로가 죽어 있었고), 워치독이 08:53에 건 정지가 15:35
        종료까지 6시간 42분간 안 풀렸다. 두 경로가 갈릴 수 있는 구조 자체를 없앤다.

        `positions`가 `None`이면 필요할 때(해제 청산 시점) 직접 조회한다 — 워치독은 평소엔
        브로커를 안 건드리는 순수 폴링이라 매 틱 조회를 강제하지 않기 위함이다.

        발행(`_publish_circuit_breaker_status`)은 **반응 뒤**에 한다 — `gateway_halted`를 함께
        싣기 때문에, 먼저 발행하면 화면이 한 틱 늦은 게이트 상태를 본다.
        """
        if event.just_confirmed:
            await self._gateway.halt("circuit breaker confirmed")
        if event.just_resumed:
            if positions is None:
                positions = await self._broker.positions()
            await self._liquidate_after_circuit_breaker(positions)
        await self._publish_circuit_breaker_status(event)

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
        `Health`와 같은 heartbeat 철학).

        `gateway_halted`를 함께 싣는다(2026-07-31) — 추정 phase가 정상으로 돌아와도 게이트가
        실제로 열렸는지는 별개 사실이고, 2026-07-31엔 그 둘이 6시간 42분간 어긋나 있었다."""
        await self._bus.publish(
            TOPIC_CIRCUIT_BREAKER,
            CircuitBreakerStatus(
                symbol=self._symbol,
                phase=event.phase.value,
                reentry_cooldown_until=event.reentry_cooldown_until,
                gateway_halted=self._gateway.halted,
                # "거래소가 멈춤"과 "우리가 죽음"을 화면에서 가르는 유일한 근거
                # (2026-08-07 고도화 2, `CircuitBreakerStatus.collector_healthy` 주석).
                collector_healthy=self._collector_healthy(self._now()),
            ),
        )

    async def watch_circuit_breaker_forever(self) -> None:
        """`handle_futures_view()`는 `FuturesView` 도착이 있어야만 실행되는 완전 이벤트 구동
        경로라, 데이터가 끊긴 동안은 CB phase가 전혀 갱신되지 않는다 — 이 메서드가
        `FixedTickScheduler`(드리프트 없는 고정 틱, `core/scheduler.py`)로 벽시계 기준 관찰을
        더해 정지 중에도 단계적으로 phase가 올라가게 한다. 정지 **중**에는 청산을 시도하지
        않는다(모듈 docstring 근거와 동일: 정지 중엔 주문 자체가 거부된다) — 다만 재개 시점의
        청산·게이트 재개는 이 경로도 반드시 수행한다(2026-07-31 수정,
        `_apply_circuit_breaker_event()` docstring 참고). 모니터 미주입이면 즉시 반환."""
        if self._circuit_breaker_monitor is None:
            return
        await FixedTickScheduler(tick_seconds=30.0).run_forever(self.observe_circuit_breaker_tick)

    async def observe_circuit_breaker_tick(self) -> None:
        """워치독의 **1틱**만 수행 — `watch_circuit_breaker_forever()`가 30초 격자로 부른다.

        루프에서 떼어낸 이유는 검증 때문이다: 주입된 시계와 짝지으면 실제로 몇 분을 기다리지
        않고도 "데이터가 안 오는 동안 phase가 단계적으로 올라가는가"를 그대로 재현할 수 있다
        (`now` 주입 근거는 `__init__` 참고)."""
        monitor = self._circuit_breaker_monitor
        if monitor is None or self._last_bar_confirm_at is None:
            return  # 콜드스타트/워밍업 — 기준선 없음, CB 판정 대상 아님(위 docstring 근거)
        as_of = self._now()
        event = monitor.observe(
            self._data_age_seconds(as_of),
            as_of,
            collector_healthy=self._collector_healthy(as_of),
        )
        await self._apply_circuit_breaker_event(event)

    async def run_forever(self) -> None:
        patterns = [
            f"{TOPIC_BAR}.{Horizon.M1.value}.{self._symbol}",
            TOPIC_FUTURES,
            # 수집기 heartbeat — CB 오탐 억제용(모듈 docstring "한산과 단절"). 이 프로세스는
            # 자체 WS 연결이 없어(`scripts/run_g2_paper_trading.py`) 데이터 흐름의 실제
            # 상태를 직접 잴 근거가 없다 — 그걸 아는 쪽의 보고를 받는다.
            TOPIC_HEALTH,
        ]
        # `on_kill`을 **명시적으로** 준다 (2026-08-07 P0-1). 종전엔 버스가 모든 구독자에게
        # kill을 자동 배달했고, 그 자동 배달이 그날 수집 프로세스를 죽였다. 이제 kill은
        # 원한 구독자에게만 가고 — 이 파이프라인은 원한다. 비상 청산이 그 일이다.
        await self._bus.subscribe(patterns, self._dispatch, on_kill=self.handle_kill)

    async def _dispatch(self, msg: BusMessage) -> None:
        if isinstance(msg, BarClosed):
            await self.handle_bar(msg)
        elif isinstance(msg, FuturesView):
            await self.handle_futures_view(msg)
        elif isinstance(msg, Health) and msg.component == COLLECTOR_COMPONENT:
            self._last_collector_health = msg
        elif isinstance(msg, KillSignal):
            # 버스가 `on_kill`로 보내므로 보통 여기 안 온다. `InProcessBus`에 직접
            # `_dispatch`를 물린 경로(재생·스모크)와 테스트를 위해 남긴다.
            await self.handle_kill(msg)

    async def handle_kill(self, signal: KillSignal) -> None:
        """`sys.kill` 수신 → 게이트 정지 + 전량 청산 (2026-08-07 고도화 6).

        ## 이 분기가 없던 동안 무슨 일이 있었나

        `core/bus.py`의 `subscribe()`는 **처음부터** `TOPIC_KILL`을 모든 패턴에 끼워 넣었다
        (그쪽 docstring: "어떤 구독자도 kill을 놓치지 않는다"). 즉 이 파이프라인은 `KillSignal`을
        **받고 있었고**, `_dispatch`에 분기가 없어 조용히 버렸다. 화면의 Kill Switch가
        "미배선"이었던 진짜 이유는 발행자가 없어서였지만, 발행자를 붙였어도 수신측이 이 상태면
        아무 일도 안 일어났을 것이다 — 양쪽을 같이 붙인다.

        ## R2/R11 자동 발동과의 관계

        `handle_futures_view()`의 `kill_switch.evaluate()`는 **판단이 돌 때만** 실행된다.
        지금처럼 번들이 0개라 판단이 안 나오는 상태에서는 그 경로가 영영 안 돈다. 이 핸들러는
        판단과 무관하게 도는 유일한 청산 경로이고, 그래서 수동 발동의 값이 여기 있다.

        `KillSwitch._triggered`도 함께 세운다(`evaluate(manual=True)`) — 안 그러면 다음 판단이
        `kill_active=False`로 돌아가 곧바로 신규 진입을 낸다.

        실패해도 예외를 위로 안 던진다: `_dispatch`가 죽으면 구독 루프가 끊겨 **나머지 감시가
        전부 멈춘다.** 대신 실패를 로그로 남긴다 — 비상 경로가 실패했다는 사실 자체가 가장
        먼저 보여야 하는 정보다.
        """
        log(
            "KillSignalReceived",
            f"sys.kill 수신 — {signal.reason}",
            triggered_by=signal.triggered_by,
            reason=signal.reason,
        )
        # **재진입 차단** (2026-08-07, 카오스 점검이 잡은 결함).
        #
        # 아래 `evaluate(manual=True)`는 `KillSwitch._trigger()`를 거쳐 **자기도 `sys.kill`을
        # 발행한다.** 그 발행이 버스를 돌아 이 핸들러로 되돌아오고, 두 번째 호출이 아직
        # 체결되지 않은 포지션을 다시 보고 **청산 주문을 한 번 더** 낸다. 실측: 보유 +1이
        # 청산 뒤 **−1**이 됐다(반대매매가 두 번 나가 반대 포지션이 생긴 것) —
        # 비상 청산이 오히려 새 포지션을 만드는, 가장 나쁜 형태의 결함이다.
        #
        # `KillSwitch.triggered`가 이미 서 있으면 게이트만 확인하고 돌아간다. 게이트 정지는
        # 멱등이라 다시 불러도 안전하다.
        if self._kill_switch.triggered:
            await self._gateway.halt(f"kill signal(재진입): {signal.reason}")
            return
        try:
            await self._gateway.halt(f"kill signal: {signal.reason}")
            account = await self._broker.account()
            positions = await self._broker.positions()
            await self._kill_switch.evaluate(
                account=account,
                daily_start_equity=self._daily_start_equity or account.total_equity,
                data_age_seconds=0.0,
                manual=True,
            )
            for request in self._kill_switch.liquidate(positions):
                await self._gateway.submit(request)
        except Exception as exc:  # noqa: BLE001 — 구독 루프를 죽이면 나머지 감시가 멈춘다
            log(
                "KillSignalHandlingFailed",
                f"sys.kill 처리 실패 — 브로커 화면에서 직접 청산 필요: {exc}",
                triggered_by=signal.triggered_by,
            )
