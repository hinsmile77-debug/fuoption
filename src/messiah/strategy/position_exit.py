"""선물 방향 포지션의 **장중** 청산 — 손절과 시간배리어 (2026-09-22 F-119).

## 무엇이 없었나

2026-09-15에 F-104(장마감 강제청산)가 들어오면서 "연 것을 그날 안에 닫는다"는 보장은
생겼다. 그러나 그것이 **유일한** 청산이었다 — 09-16·09-17·09-22 세 거래일의 실거래
전부가 예외 없이 15:25 EOD 강제청산으로 나갔고, 손절·익절·논지소멸로 나간 포지션은
한 건도 없다.

`Derivatives_AI_Position_Holding_Policy_Ver1.0.md` §4는 청산 우선순위를 넷으로 적는다:
① 손절 ② 시간 상한 ③ 익절 ④ 논지 소멸. 구현돼 있던 것은 ②의 **하루치 상한**(장마감)
하나뿐이었다.

## 그리고 그 공백이 사이저의 전제를 깨고 있었다

`risk/sizer.py`는 계약수를 이렇게 정한다:

    loss_per_contract_krw = stop_distance_ticks × tick_size × point_value

호출부(`strategy/pipeline.py`)가 넘기는 `stop_distance_ticks`는 `ATR(M1,14) × 1.0`이다.
**즉 "1×ATR에서 손절한다"는 전제로 크기가 정해지고 있었는데, 그 손절이 어디에도 없었다.**
`risk/risk_engine.py` 모듈 docstring이 "R1(단일 포지션 최대손실 2%)은 사이징 상한으로
구조적으로 강제한다"고 적는데, 그 문장은 **손절이 있을 때만 참**이다. 실제로는 마감까지의
최대 이동폭이 손실 한도였다.

이 모듈은 새 위험 정책이 아니다 — **코드가 이미 전제하고 있던 것을 실행이 지키게 한다.**
그래서 기본 손절폭이 `1.0 × ATR`이고, 사이저는 건드리지 않는다(2026-09-22 사용자 결정).

## 왜 완성봉인가 — EOD 청산과 반대로 간다

`strategy/eod_flatten.py`는 **벽시계**에 건다. 데이터가 끊겨도 시계는 가고 마감은 오기
때문이다. 이 모듈은 반대다: **손절은 가격이 있어야 판정된다.** 없는 가격으로 손절할
방법은 없으므로 완성봉 도착이 곧 판정 시점이다(SYSTEM.md §4-3 완성봉 규율).

그 대가를 명시한다 — **봉이 끊기면 이 청산도 같이 멈춘다.** 그 구간은 다른 층이 받는다:
R11(데이터단절 30초, 신규진입 차단) · `CircuitBreakerMonitor` · KillSwitch R11(지속 단절
전면정지) · 그리고 최후 보루로 EOD 강제청산(벽시계). 이 모듈이 데이터 단절까지 혼자
책임지려 들면 EOD 청산을 벽시계에 건 이유가 무의미해진다.

## 분할하지 않는다

EOD 청산은 분할 시장가다 — 마감까지 10분이 있고, 호가를 훑고 내려가는 손해를 줄일 시간이
있기 때문이다. **손절은 시간이 적이다.** 분할은 시간을 사는 일이고, 손절 중에 시간을 사면
사려던 그 이유(더 나쁜 가격)가 그대로 실현된다. 그래서 여기서는 남은 전량을 한 번에
내보낸다.

## 시간배리어는 라벨에서 그대로 가져온다 — 숫자를 지어내지 않는다

폭과 봉 수의 정본은 `models/labeling.py`의 `BARRIER_PARAMS`다(전 Horizon 3봉). 어느
Horizon인가는 `FuturesView.cadence_seconds`(구동 Horizon 길이)가 말해 준다 — 30분 주기로
갱신되는 판단이면 3 × 30분 = 90분이다.

**경과는 벽시계 분으로 잰다**(봉 수가 아니라). 봉이 빠지는 날에도 나이는 같이 늙어야
하고, 이 판정 자체가 이미 봉 도착에 걸려 있어 봉 수로 세면 같은 결손을 두 번 맞는다.

`cadence_seconds`가 없거나 아는 Horizon과 안 맞으면 **시간배리어를 적용하지 않는다** —
모르는 값으로 청산 시각을 지어내지 않는다(L18). 그 경우에도 손절과 EOD 청산은 그대로다.

## 익절(상단 배리어)은 이번 스코프에 없다

`take_profit_atr_mult`를 받게 열어 두되 **기본값은 None(비적용)**이다. 손절은 사이저가
이미 전제한 값을 지키는 일이라 새 정책이 아니지만, 익절은 **이기고 있는 포지션을 자르는**
새 정책이다 — 2026-09-22 실거래(+117틱, ATR의 약 2.4배)가 1×ATR 익절이었다면 +49틱에서
끝났을 것이다. 그 결정은 표본을 보고 사람이 할 일이고, 그때 이 인자에 값을 넣으면 된다.

## 이 모듈은 판정만 한다 — 그러나 기억 한 덩이는 여기 둔다

주문 생성은 `KillSwitch.liquidate()`(순수 변환 함수), 제출은 `OrderGateway.submit()`
(계명 1 — 유일한 주문 경로). `strategy/eod_flatten.py`가 세운 선례를 그대로 따른다:
I/O가 없어야 "손절선 바로 위에서는 안 쏜다" 같은 판정을 실제 시장 없이 테스트할 수 있다.

다만 `ExitStateTracker`는 이 파일에 있다. EOD 청산의 같은 역할(미체결 잔량)은
`strategy/pipeline.py`가 직접 dict 세 개로 들고 있는데, 그 파일은 이미 1,014줄이고
(R5의 500줄 상한을 이미 넘겼다) 여기에 같은 크기를 또 얹으면 배선과 규칙이 한 덩어리로
엉긴다. 추적기는 **I/O가 없다** — 시계와 ATR을 주입받아 기억만 한다. 그래서 이 모듈의
"판정만 한다"는 약속을 깨지 않으면서, 무엇을 왜 기억하는지가 규칙 옆에 남는다.

**EOD 쪽은 건드리지 않았다.** 그쪽은 6거래일 실전 검증을 통과한 경로이고, 공통화하겠다고
지금 손대는 것은 검증된 것을 미검증으로 되돌리는 일이다.

## 재기동하면 시계가 다시 시작한다 (알려진 갭)

R12(프로세스는 무상태, 재시작 복원은 브로커 재조회)대로 진입가·수량은 재기동 후에도
브로커가 그대로 답한다. 그러나 **언제 들어갔는지는 브로커가 모른다** — 그래서 재기동
직후 첫 관측 시각이 새 기준이 되고, 시간배리어가 그만큼 늦게 닿는다. 손절은 가격 기준이라
영향이 없고, 늦어진 시간배리어는 EOD 강제청산이 그날 안에 받는다. 이 갭을 없애려면 진입
시각을 영속화해야 하는데, 그건 무상태 원칙과 맞바꾸는 결정이라 사람 몫으로 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence

from messiah.broker.base import BrokerPosition
from messiah.core.messages import HORIZON_SECONDS, Horizon
from messiah.models.labeling import BARRIER_PARAMS

#: 손절폭 = 이 배수 × 진입 시점 ATR. **사이저가 `stop_distance_ticks`로 쓰는 값과 같은
#: 축이어야 한다**(`risk/sizer.py` — `loss_per_contract_krw`). 어긋나면 R1(단일 포지션
#: 최대손실 2%)이 다시 종이 위의 문장이 된다. 2026-09-22 사용자 결정으로 1.0 고정.
DEFAULT_STOP_ATR_MULT = 1.0

#: 익절 배수 — None이면 상단 배리어를 적용하지 않는다(모듈 docstring "익절은 이번
#: 스코프에 없다"). 켜려면 사람이 값을 넣는다.
DEFAULT_TAKE_PROFIT_ATR_MULT: float | None = None

#: 청산 주문을 낸 뒤 같은 심볼을 다시 보기까지 기다리는 시간(초). 판정 주기가 60초
#: (M1 완성봉)라 120초면 한 봉을 거른다. 포지션이 실제로 줄면 이 시간과 무관하게 풀린다.
DEFAULT_RESUBMIT_COOLDOWN_SECONDS = 120.0


class ExitReason(str, Enum):
    """왜 나가는가. 로그·리포트가 **사유별로** 셀 수 있어야 「손절이 잦다」와 「시간배리어가
    잦다」가 갈린다 — 둘은 전혀 다른 처방을 부른다."""

    STOP_LOSS = "STOP_LOSS"
    TIME_BARRIER = "TIME_BARRIER"
    TAKE_PROFIT = "TAKE_PROFIT"


@dataclass(frozen=True)
class HeldFutures:
    """청산 판정에 필요한 보유 1건의 재료.

    `entry_price_ticks`는 **브로커 평균단가를 매번 그대로** 받는다(L12 — 브로커가
    진실원천이고 로컬 기억은 언제나 그보다 못하다). 로컬이 드는 것은 시계(나이)와 진입
    시점 ATR뿐이고, 그 둘은 브로커가 답할 수 없는 값이다.
    """

    position: BrokerPosition
    entry_price_ticks: int
    #: 진입 시점 ATR(틱). 사이저가 계약수를 정할 때 쓴 그 값이다. 0 이하면 판정 불가
    #: (ATR 워밍업 미달) — 그 포지션은 이번 판정에서 조용히 빠진다.
    stop_distance_ticks: float
    #: 최초 관측 이후 경과(분). None이면 모른다 — 시간배리어를 적용하지 않는다.
    minutes_held: float | None = None
    #: 이 포지션에 적용할 시간배리어(분). None이면 비적용 — `time_barrier_minutes()` 참고.
    time_barrier_minutes: float | None = None


@dataclass(frozen=True)
class ExitSlice:
    """이번 판정에서 한 심볼을 얼마나, 왜 내보내는가."""

    position: BrokerPosition
    qty: int
    reason: ExitReason
    #: 판정 근거 수치 — 로그에 그대로 실어 "왜 그때 나갔나"를 나중에 잴 수 있게 한다.
    #: 이 저장소가 반복해 배운 것: 판정만 남기고 입력을 안 남기면 재현이 불가능하다.
    detail: dict[str, Any]


@dataclass(frozen=True)
class PositionExitPlan:
    slices: tuple[ExitSlice, ...]
    reason: str

    @property
    def should_exit(self) -> bool:
        return bool(self.slices)


def time_barrier_minutes(
    cadence_seconds: float | None,
    *,
    barrier_params: Mapping[Horizon, Any] | None = None,
) -> float | None:
    """구동 Horizon 길이(초) → 시간배리어(분). 모르면 None이다.

    `FuturesView.cadence_seconds`가 곧 "이 판단이 몇 초마다 갱신되는가" = 구동 Horizon
    길이다(`core/messages.py`). 그 Horizon의 시간배리어 봉 수는 `models/labeling.py`의
    `BARRIER_PARAMS`가 정본이고 — 지금은 전 Horizon 3봉이지만 그 표가 바뀌면 여기도
    저절로 따라간다. 숫자를 여기 복사해 두지 않는 이유다.

    아는 Horizon과 안 맞는 값(예: 집계 주기가 바뀐 미래의 값)은 **None**이다 — 모르는
    값으로 청산 시각을 지어내지 않는다.
    """
    if cadence_seconds is None or cadence_seconds <= 0:
        return None
    params = barrier_params if barrier_params is not None else BARRIER_PARAMS
    for horizon, seconds in HORIZON_SECONDS.items():
        if abs(float(seconds) - float(cadence_seconds)) < 1.0:
            spec = params.get(horizon)
            bars = getattr(spec, "time_barrier_bars", None)
            if not bars:
                return None
            return float(bars) * float(seconds) / 60.0
    return None


def _adverse_ticks(*, entry_price_ticks: int, last_price_ticks: int, qty: int) -> float:
    """진입가 대비 **불리한** 방향으로 몇 틱 갔나(양수 = 손실 중).

    LONG(qty>0)은 가격이 내리면 손실, SHORT(qty<0)는 오르면 손실이다 — 부호 하나로
    두 경우를 같이 쓴다(`execution/position_math.py`가 실현손익에 쓰는 규칙과 같은 꼴).
    """
    sign = 1 if qty > 0 else -1
    return float(entry_price_ticks - last_price_ticks) * sign


def decide(
    *,
    last_price_ticks: int | None,
    held: Sequence[HeldFutures],
    stop_atr_mult: float = DEFAULT_STOP_ATR_MULT,
    take_profit_atr_mult: float | None = DEFAULT_TAKE_PROFIT_ATR_MULT,
    cooling: Mapping[str, bool] | None = None,
) -> PositionExitPlan:
    """지금 무엇을, 왜 내보내야 하는가.

    입력:
        last_price_ticks    방금 확정된 완성봉의 종가(틱). None이면 판정하지 않는다 —
                            가격 없이 손절할 방법은 없다.
        held                보유 1건씩의 재료. 수량 0은 호출자가 이미 걸렀다고 가정하지
                            않는다(여기서도 건너뛴다).
        stop_atr_mult       손절폭 배수. 사이저의 `stop_distance_ticks`와 같은 축이어야
                            한다(모듈 docstring).
        take_profit_atr_mult  익절 배수. None이면 상단 배리어 비적용(기본).
        cooling             심볼별 "직전 청산 주문이 아직 반영 안 됐다" 표식. True인 심볼은
                            건너뛴다 — **중복 청산은 미청산보다 고치기 어렵다**(없던 방향의
                            포지션이 새로 생긴다). 이 판정은 순수하게 남고, 무엇이 식는
                            중인지는 호출자가 안다.

    우선순위는 Holding Policy §4 그대로 — 손절 > 시간배리어 > 익절. 자본 보존이 수익
    확정보다 앞선다는 원칙을 코드 순서로 못 박는다(`strategy/options/lifecycle.py`의
    `evaluate_position()`이 같은 이유로 같은 모양을 하고 있다).

    실패 조건: 없다 — 판정만 하므로 예외를 던지지 않는다.
    """
    if last_price_ticks is None:
        return PositionExitPlan((), "완성봉 종가가 없다 — 청산 판정 안 함")

    cools = cooling or {}
    slices: list[ExitSlice] = []
    for item in held:
        position = item.position
        if position.qty == 0:
            continue
        if cools.get(position.symbol):
            continue  # 방금 보낸 것이 아직 포지션에 안 잡혔다 — 또 보내면 반대로 뒤집힌다
        if item.stop_distance_ticks <= 0:
            continue  # ATR 워밍업 미달 — 잴 자가 없으면 재지 않는다(0으로 대신하지 않는다)

        qty = abs(position.qty)
        adverse = _adverse_ticks(
            entry_price_ticks=item.entry_price_ticks,
            last_price_ticks=last_price_ticks,
            qty=position.qty,
        )
        stop_ticks = item.stop_distance_ticks * stop_atr_mult
        base: dict[str, Any] = {
            "entry_price_ticks": item.entry_price_ticks,
            "last_price_ticks": last_price_ticks,
            "adverse_ticks": adverse,
            "stop_ticks": stop_ticks,
            "minutes_held": item.minutes_held,
            "time_barrier_minutes": item.time_barrier_minutes,
        }

        if adverse >= stop_ticks:
            slices.append(
                ExitSlice(position=position, qty=qty, reason=ExitReason.STOP_LOSS, detail=base)
            )
            continue

        if (
            item.minutes_held is not None
            and item.time_barrier_minutes is not None
            and item.minutes_held >= item.time_barrier_minutes
        ):
            slices.append(
                ExitSlice(position=position, qty=qty, reason=ExitReason.TIME_BARRIER, detail=base)
            )
            continue

        if take_profit_atr_mult is not None:
            target = item.stop_distance_ticks * take_profit_atr_mult
            if -adverse >= target:
                slices.append(
                    ExitSlice(
                        position=position,
                        qty=qty,
                        reason=ExitReason.TAKE_PROFIT,
                        detail={**base, "target_ticks": target},
                    )
                )

    if not slices:
        return PositionExitPlan((), f"보유 {len(held)}건 — 청산 조건 미도달")

    how = " · ".join(f"{s.position.symbol} {s.qty}계약 {s.reason.value}" for s in slices)
    return PositionExitPlan(tuple(slices), f"Holding Policy §4 — {how}")


# ------------------------------------------------------------------ 기억 (모듈 docstring 참고)


@dataclass
class _Tracked:
    """한 심볼에 대해 **브로커가 답할 수 없는 것만** 기억한다."""

    sign: int  # +1 LONG / -1 SHORT. 부호가 바뀌면 그건 새 포지션이다
    opened_at: datetime  # 최초 관측 시각 — 시간배리어의 기준
    stop_distance_ticks: float  # 진입 시점 ATR. 0이면 아직 못 잡았다(워밍업)
    time_barrier_minutes: float | None
    last_abs_qty: int  # 직전 관측 수량 — 줄었으면 청산이 체결된 것이다
    submitted_at: datetime | None = None  # 마지막 청산 주문 시각(쿨다운 기준)
    announced: bool = False  # 손절선이 실제로 잡힌 순간을 한 번만 알린다


@dataclass(frozen=True)
class ArmedNotice:
    """손절선이 **실제로 잡힌** 순간의 알림 — 포지션 하나당 한 번.

    "조건에 안 닿았다"와 "조건 자체가 안 잡혔다"를 로그에서 가르는 유일한 근거다.
    """

    symbol: str
    entry_price_ticks: int
    stop_ticks: float
    time_barrier_minutes: float | None


class ExitStateTracker:
    """보유 심볼별 나이·손절폭·쿨다운을 기억한다. I/O 없음 — 시계와 ATR을 주입받는다."""

    def __init__(self, *, cooldown_seconds: float = DEFAULT_RESUBMIT_COOLDOWN_SECONDS) -> None:
        self._cooldown_seconds = cooldown_seconds
        self._tracked: dict[str, _Tracked] = {}

    def observe(
        self,
        positions: Sequence[BrokerPosition],
        *,
        as_of: datetime,
        atr_ticks: float | None,
        cadence_seconds: float | None,
        stop_atr_mult: float = DEFAULT_STOP_ATR_MULT,
    ) -> tuple[list[HeldFutures], list[ArmedNotice]]:
        """브로커 스냅샷 하나를 먹이고, 판정 재료와 새로 무장된 포지션을 돌려준다.

        여기서 일어나는 일 넷:
          ① 사라진 심볼의 기억을 버린다 — 어제 것이 오늘을 막으면 안 된다
          ② 부호가 바뀌었으면 **새 포지션**으로 본다(시계를 다시 시작)
          ③ 수량이 줄었으면 청산이 체결된 것이다 — 쿨다운을 즉시 푼다
          ④ ATR을 아직 못 잡았으면 이번 봉의 것으로 잡아 본다(워밍업 중엔 계속 재시도)

        ②가 필요한 이유: 뒤집기(`position_math` — 전량 청산 후 반대로)는 같은 심볼에
        전혀 다른 포지션이 서는 일이다. 옛 진입 시각을 물려주면 새 포지션이 태어나자마자
        시간배리어에 걸린다.

        ③이 **수량 증가**를 시계 초기화로 보지 않는 것이 중요하다 — 물타기로 시계를
        되돌릴 수 있으면 시간배리어를 무한히 미룰 수 있다(모듈 docstring).
        """
        live = {p.symbol: p for p in positions if p.qty != 0}
        for symbol in list(self._tracked):
            if symbol not in live:
                del self._tracked[symbol]  # ①

        held: list[HeldFutures] = []
        armed: list[ArmedNotice] = []
        for symbol, position in live.items():
            sign = 1 if position.qty > 0 else -1
            state = self._tracked.get(symbol)
            if state is None or state.sign != sign:  # ②
                state = _Tracked(
                    sign=sign,
                    opened_at=as_of,
                    stop_distance_ticks=0.0,
                    time_barrier_minutes=time_barrier_minutes(cadence_seconds),
                    last_abs_qty=abs(position.qty),
                )
                self._tracked[symbol] = state
            elif abs(position.qty) < state.last_abs_qty:  # ③
                state.submitted_at = None
            state.last_abs_qty = abs(position.qty)

            if state.stop_distance_ticks <= 0 and atr_ticks is not None and atr_ticks > 0:  # ④
                state.stop_distance_ticks = float(atr_ticks)
            if state.time_barrier_minutes is None:
                # 첫 관측 때 `cadence_seconds`가 아직 없었을 수 있다(봉이 뷰보다 먼저 온다).
                state.time_barrier_minutes = time_barrier_minutes(cadence_seconds)

            if state.stop_distance_ticks > 0 and not state.announced:
                state.announced = True
                armed.append(
                    ArmedNotice(
                        symbol=symbol,
                        entry_price_ticks=position.avg_price_ticks,
                        stop_ticks=state.stop_distance_ticks * stop_atr_mult,
                        time_barrier_minutes=state.time_barrier_minutes,
                    )
                )

            held.append(
                HeldFutures(
                    position=position,
                    entry_price_ticks=position.avg_price_ticks,
                    stop_distance_ticks=state.stop_distance_ticks,
                    minutes_held=(as_of - state.opened_at).total_seconds() / 60.0,
                    time_barrier_minutes=state.time_barrier_minutes,
                )
            )
        return held, armed

    def cooling(self, as_of: datetime) -> dict[str, bool]:
        """심볼별 "직전 청산 주문이 아직 반영 안 됐다" 표식 — `decide(cooling=...)`에 그대로."""
        return {
            symbol: (
                state.submitted_at is not None
                and (as_of - state.submitted_at).total_seconds() < self._cooldown_seconds
            )
            for symbol, state in self._tracked.items()
        }

    def mark_submitted(self, symbol: str, at: datetime) -> None:
        """청산 주문이 **접수된** 뒤에만 부른다.

        거부된 주문(`OrderGateway.submit()`이 None)에 이걸 부르면 그 포지션은 쿨다운
        동안 손절 없이 남는다 — EOD 청산이 "거부를 「보냈다」로 세면 그 수량이 영영
        안 나간다"로 배운 것과 같은 함정이다.
        """
        state = self._tracked.get(symbol)
        if state is not None:
            state.submitted_at = at
