"""Risk Engine — Ver 1.1 §4-2, Ver 2.0 §5 통합 리스크 한도표 (Ver 2.0 §9 W24~26).

"거부권 보유": Net Expected Return ≤ 0 또는 한도 위반 → 무조건 거부(Ver 1.1 §4-2 원문).
`evaluate()`는 순수 판정 함수다 — 승인/거부만 정하고 사이징은 하지 않는다(`risk/sizer.py`의
책임, Ver 2.0 §1 파이프라인 "Cost Model → Risk Engine(거부권) → Position Sizer" 순서 그대로).

## R1은 여기서 거부하지 않는다 — Sizer의 사이징 상한으로 구조적으로 강제한다

Ver 2.0 §5 표는 R1(단일 포지션 최대손실 2%)의 "위반 시"를 "진입 거부"로 적지만, 이 파이프라인은
Risk Engine 통과 **후**에 Sizer가 수량을 정한다(순서상 R1을 어길 수량 자체가 아직 없다) — 매번
새로 사이징하는 이 시스템에서는 R1을 게이트가 아니라 사이징 상한으로 구현하는 쪽이 "위반이
발생할 수 없게 만든다"는 점에서 더 강한 보장이다(`risk/sizer.py`의 `max_position_loss_pct`).
이 모듈은 R1을 검사하지 않는다 — 사이징 이전이라 검사할 대상(수량)이 없다.

## R3·R5는 "현재 상태" 기준 게이트다 (사이징 전 순환 의존 회피)

R3(증거금 40%)·R5(오버나이트 포지션 2개)는 "이 주문을 추가하면 얼마가 될지"가 아니라
**지금 이미 한도를 넘었는지**를 검사한다 — 이미 한도 안이면 통과시키고, 실제로 한도를 넘지
않게 만드는 일은 Sizer(가용 증거금 예산 내로 수량을 깎는다)의 몫이다. Risk Engine이 사이징
전 수량으로 "예상 증거금"을 계산하려 하면 Sizer가 아직 안 정한 수량을 미리 가정해야 하는
순환 의존이 생긴다 — 이 분리로 피한다.

## R4(오버나이트 증거금)·R6(오버나이트 자격) — Event Calendar 도입(2026-07-27)으로 구현

`core/event_calendar.py`(EventCalendar)가 생기면서 "지금이 장마감 임박인가"를 처음으로
판단할 수 있게 됐다. Holding Policy Ver 1.0 §2.2 "A. 선물 데이트레이딩"(이 프로젝트의
현재 유일한 포지션 유형 — Options AI는 Phase 4까지 없다)은 **오버나이트 금지가 기본값**
("장 마감 전 강제 청산, 예: 마감 10분 전")이라고 명시한다. 이를 반영해 두 단계로 나눴다:

- **R6(오버나이트 자격, `overnight_flatten_lead_minutes`=10분 기본)**: 장마감이 이 시간
  이내로 남으면 신규 진입 자체를 거부한다 — Type A는 "최대손실이 정의된 구조"(Ver 1.0 §1.2
  "오버나이트 자격 원칙")가 아니라 자격이 아예 없어서다.
- **R4(오버나이트 증거금, `overnight_margin_window_minutes`=30분 기본)**: R6보다 넓은
  구간에서 R3의 증거금 한도(`margin_cap_pct`=40%) 대신 더 보수적인
  `overnight_margin_cap_pct`(25%)를 적용한다 — R6가 어떤 이유로든 못 걸러도(예: 두 상수를
  다르게 튜닝하는 향후 변경) 이중 방어가 되도록 R6보다 먼저(더 이른 시각부터) 발동하는
  별개 구간으로 설계했다.

`minutes_to_close`가 `None`(호출자가 세션 정보를 안 넘김 — 예: 재생/스모크처럼 실제
KRX 세션 개념이 무의미한 경로)이면 두 게이트 모두 조용히 건너뛴다(기존 동작 그대로,
회귀 없음) — "모른다"를 "오버나이트 아님"으로 낙관 해석하지 않고 그냥 검사 자체를
생략한다(Regime rules.py의 `RuleContext` None 처리와 같은 철학).

## R7(순델타)·R8(순베가)·R9(매도옵션 손실) — Options AI 착수(Ver 2.0 §9 W30~31)로 구현

`evaluate()`(방향 의도용)와 분리된 별도 메서드 `evaluate_options_portfolio()`로 구현했다 —
`evaluate()`의 R1~R6·R10~R12는 선물 단일 심볼·수량 기준인데, R7·R8은 "옵션 포트폴리오
전체의 합산 Greeks"가 입력이라 성격이 다르다(선물 의도 하나를 평가하는 흐름에 옵션 포트폴리오
집계를 끼워 넣으면 오히려 두 관심사가 뒤섞인다). R9는 신규 게이트가 아니라 **탐지** 메서드
`positions_requiring_forced_liquidation()`로 뒀다 — `strategy/options/safety.py`의
`exceeds_loss_limit()`(§6-5, 동일 임계)를 그대로 재사용해 같은 규칙이 두 곳에서 따로
구현되어 어긋나는 위험을 없앴다(그 함수가 이미 Options AI Lifecycle Manager의 판정 근거이기도
하다 — Risk Engine은 그 판정을 브로커 포지션 전체에 대해 한 번 더 확인하는 이중 방어선).

**여전히 남은 갭**: 옵션 주문 실행 경로 자체가 없어(`strategy/options/service.py` 모듈
docstring — Options AI는 후보 산출까지만) `BrokerPosition.greeks`를 실제로 채우는 어댑터가
아직 없다 — 이 메서드들은 게이트만 준비됐고, 살아있는 옵션 포지션이 흘러들어오는 배선은
옵션 주문 실행이 생긴 뒤의 몫이다.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Sequence

from messiah.broker.base import BrokerAccount, BrokerPosition
from messiah.core import logging as mlog
from messiah.core.config import CapitalConfig
from messiah.core.messages import DecisionIntent, GreeksProfile, Side
from messiah.risk.sizer import DEFAULT_POINT_VALUE_KRW
from messiah.strategy.options.safety import DEFAULT_LOSS_MULTIPLE, exceeds_loss_limit


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str


@dataclass(frozen=True)
class RiskEngineConfig:
    capital: CapitalConfig = field(default_factory=CapitalConfig)  # R2·R3·R4·R5 한도 재사용
    consecutive_loss_limit: int = 3  # R10
    data_staleness_limit_seconds: float = 30.0  # R11
    order_error_limit: int = 3  # R12
    order_error_window_seconds: float = 300.0  # R12 "5분 내 3회"
    # Holding Policy Ver 1.0 §2.2 "A. 선물 데이트레이딩" 예시("마감 10분 전") 그대로.
    overnight_flatten_lead_minutes: float = 10.0  # R6 — 이 안쪽이면 신규 진입 전면 거부
    overnight_margin_window_minutes: float = 30.0  # R4 — 이 안쪽이면 증거금 한도가 25%로 강화
    net_delta_limit_contracts: float = 20.0  # R7 "포트폴리오 순델타 ±미니 20계약 상당"
    net_vega_limit_pct_of_equity: float = 0.5  # R8 "계좌 0.5%/IV 1%p"
    # R8 순베가를 KRW로 환산하는 승수 — 옵션 전용 계약승수는 아직 미실측이라 선물과 동일한
    # placeholder를 잠정 적용한다(`risk/sizer.py` DEFAULT_POINT_VALUE_KRW 모듈 docstring
    # "실측 전 placeholder"와 같은 성격의 갭, 별개 상수로 분리해 옵션 실측이 먼저 끝나도
    # 선물 쪽 값에 영향 없게 했다).
    option_point_value_krw: Decimal = DEFAULT_POINT_VALUE_KRW
    short_option_loss_multiple: float = DEFAULT_LOSS_MULTIPLE  # R9 — §6-5와 동일 임계 재사용


class RiskEngine:
    """상태(연속손실·주문오류 이력)는 인스턴스가 갖되, 사이징 인프라(계좌·포지션)는 매
    `evaluate()` 호출마다 호출자가 넘긴다 — CostModel과 같은 "주입된 상태 + 순수 계산"
    스타일(모듈 docstring 원칙)."""

    def __init__(self, config: RiskEngineConfig | None = None) -> None:
        self._config = config or RiskEngineConfig()
        self._consecutive_losses = 0
        self._order_errors: deque[datetime] = deque()

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses

    def record_trade_result(self, pnl: Decimal) -> None:
        """R10 — 청산 손익을 기록해 연속손실 스트릭을 갱신. 손실(음수)이면 누적, 이익이면
        리셋(0 초과만 승리로 취급 — 손익 0은 스트릭을 끊지 않는다, 진짜 승리가 아니므로)."""
        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

    def record_order_error(self, at: datetime) -> None:
        """R12 — 주문 오류 발생 시각 기록. `evaluate()`가 매번 윈도우 밖 이력을 정리한다."""
        self._order_errors.append(at)

    def reset_daily(self) -> None:
        """장 시작 시 호출 — R10 스트릭·R12 이력을 새 거래일 기준으로 초기화."""
        self._consecutive_losses = 0
        self._order_errors.clear()

    def evaluate(
        self,
        *,
        intent: DecisionIntent,
        net_expected_return_ticks: float,
        account: BrokerAccount,
        positions: Sequence[BrokerPosition],
        daily_start_equity: Decimal,
        data_age_seconds: float,
        as_of: datetime,
        minutes_to_close: float | None = None,
    ) -> RiskDecision:
        cfg = self._config

        if intent.side == Side.NO_TRADE:
            return self._reject("NO_TRADE 의도 — 평가 대상 아님")

        if net_expected_return_ticks <= 0:
            return self._reject(f"Net ER {net_expected_return_ticks:.2f}틱 ≤ 0 (Ver 1.1 §4-2)")

        if data_age_seconds > cfg.data_staleness_limit_seconds:
            return self._reject(
                f"R11 데이터 단절 {data_age_seconds:.0f}s > "
                f"{cfg.data_staleness_limit_seconds:.0f}s — 신규 진입 차단"
            )

        if self._consecutive_losses >= cfg.consecutive_loss_limit:
            return self._reject(
                f"R10 연속손실 {self._consecutive_losses}회 ≥ {cfg.consecutive_loss_limit}회 — "
                "당일 신규 진입 중단"
            )

        self._prune_order_errors(as_of)
        if len(self._order_errors) >= cfg.order_error_limit:
            return self._reject(
                f"R12 주문오류 {len(self._order_errors)}회/{cfg.order_error_window_seconds:.0f}s "
                f"≥ {cfg.order_error_limit}회 — 신규 진입 차단"
            )

        daily_loss_pct = _loss_pct(account.total_equity, daily_start_equity)
        if daily_loss_pct >= cfg.capital.daily_loss_limit_pct:
            return self._reject(
                f"R2 일일손실 {daily_loss_pct:.2f}% ≥ {cfg.capital.daily_loss_limit_pct}% — "
                "Kill Switch 영역(별도 감시자가 청산 판단)"
            )

        if minutes_to_close is not None and minutes_to_close <= cfg.overnight_flatten_lead_minutes:
            return self._reject(
                f"R6 오버나이트 자격 없음(Type A) — 장마감 {minutes_to_close:.1f}분 전 "
                f"(≤{cfg.overnight_flatten_lead_minutes:.0f}분) 신규 진입 거부, Holding "
                "Policy §2.2 A"
            )

        overnight_margin_window = (
            minutes_to_close is not None and minutes_to_close <= cfg.overnight_margin_window_minutes
        )
        margin_cap = (
            cfg.capital.overnight_margin_cap_pct
            if overnight_margin_window
            else cfg.capital.margin_cap_pct
        )
        margin_pct = _margin_pct(account)
        if margin_pct > margin_cap:
            gate = "R4 오버나이트증거금" if overnight_margin_window else "R3 증거금사용률"
            return self._reject(f"{gate} {margin_pct:.1f}% > {margin_cap}% — 진입 거부")

        held_symbols = {p.symbol for p in positions if p.qty != 0}
        projected_count = len(held_symbols | {intent.symbol})
        if projected_count > cfg.capital.max_overnight_positions:
            return self._reject(
                f"R5 포지션수(예상) {projected_count} > {cfg.capital.max_overnight_positions} — "
                "진입 거부"
            )

        return RiskDecision(True, "승인")

    def evaluate_options_portfolio(
        self,
        *,
        option_positions: Sequence[BrokerPosition],
        equity: Decimal,
        prospective_greeks: GreeksProfile | None = None,
    ) -> RiskDecision:
        """R7(순델타)·R8(순베가) 게이트 — `evaluate()`와 별개 메서드인 이유는 모듈 docstring
        참고. `.greeks`가 없는(선물 또는 아직 실측 연동 안 된) 포지션은 집계에서 제외한다
        (모듈 docstring "여전히 남은 갭"). `prospective_greeks`가 None이면 신규 진입 없이
        "지금 보유분만으로 이미 한도를 넘었는가"만 검사한다(R3·R5와 동일한 "현재 상태 기준"
        원칙, 모듈 docstring 위 절 참고)."""
        cfg = self._config
        positions_with_greeks = [p for p in option_positions if p.greeks is not None]

        net_delta = sum(p.greeks.delta * p.qty for p in positions_with_greeks if p.greeks)
        net_vega = sum(p.greeks.vega * p.qty for p in positions_with_greeks if p.greeks)
        if prospective_greeks is not None:
            net_delta += prospective_greeks.delta
            net_vega += prospective_greeks.vega

        if abs(net_delta) > cfg.net_delta_limit_contracts:
            return self._reject(
                f"R7 포트폴리오 순델타 {net_delta:.2f} > ±{cfg.net_delta_limit_contracts} "
                "미니 계약 상당 — 헤지 지시"
            )

        if equity > 0:
            vega_krw = Decimal(str(net_vega)) * cfg.option_point_value_krw
            vega_pct = float(vega_krw / equity * 100)
            if abs(vega_pct) > cfg.net_vega_limit_pct_of_equity:
                return self._reject(
                    f"R8 포트폴리오 순베가 {vega_pct:.2f}% > "
                    f"±{cfg.net_vega_limit_pct_of_equity}%/IV1%p — 옵션 진입 거부"
                )

        return RiskDecision(True, "승인")

    def positions_requiring_forced_liquidation(
        self, option_positions: Sequence[BrokerPosition], current_values: dict[str, float]
    ) -> list[BrokerPosition]:
        """R9 "매도옵션 손실 프리미엄 2배 → 강제 청산" 탐지(모듈 docstring — 실제 청산 주문
        구성은 옵션 실행 경로가 생긴 뒤의 몫, 이 메서드는 대상만 추린다).

        입력: `current_values`는 symbol → 지금 그 포지션을 되사는 데 드는 절대값(양수) —
             `BrokerPosition.avg_price_ticks`와 같은 단위여야 한다(호출측 책임, 실시간 옵션
             시세가 아직 안 물려있어 이 메서드 자체는 시세를 모른다)."""
        flagged: list[BrokerPosition] = []
        for position in option_positions:
            if position.qty >= 0:  # R9는 "매도옵션"만 대상
                continue
            current_value = current_values.get(position.symbol)
            if current_value is None:
                continue
            entry_credit = float(position.avg_price_ticks)
            if exceeds_loss_limit(
                entry_credit, current_value, multiple=self._config.short_option_loss_multiple
            ):
                flagged.append(position)
        return flagged

    def _prune_order_errors(self, as_of: datetime) -> None:
        window = self._config.order_error_window_seconds
        while self._order_errors and (as_of - self._order_errors[0]).total_seconds() > window:
            self._order_errors.popleft()

    def _reject(self, reason: str) -> RiskDecision:
        mlog.log("RiskReject", reason)
        return RiskDecision(False, reason)


def _loss_pct(current_equity: Decimal, start_equity: Decimal) -> float:
    if start_equity <= 0:
        return 0.0
    loss = start_equity - current_equity
    return float(loss / start_equity * 100)


def _margin_pct(account: BrokerAccount) -> float:
    if account.total_equity <= 0:
        return 100.0  # 자본 정보 없음 — 보수적으로 한도 초과 취급
    return float(account.margin_used / account.total_equity * 100)
