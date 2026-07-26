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

## 구현하지 않은 항목 (알려진 갭)

- **R4(오버나이트 증거금 25%)·R6(오버나이트 자격)**: 세션(장중/오버나이트) 구분을 아는
  컴포넌트가 없다(Event Calendar 미구현, 기존 갭) — "지금이 장마감 임박인가"를 판단할 수
  없어 R4/R6 게이트를 걸 수 없다. Holding Policy §2 A유형(미니선물)은 "당일 무포 원칙"이라
  실제 운영에서 오버나이트 자체가 드물지만, 판단 로직 자체는 없다.
- **R7(순델타)·R8(순베가)·R9(매도옵션 손실)**: 전부 옵션 포지션·Greeks 전제 — Options AI
  (Ver 1.3)가 Phase 4까지 없어 포트폴리오에 옵션 자체가 없다(부재이지 미달이 아님).
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
from messiah.core.messages import DecisionIntent, Side


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str


@dataclass(frozen=True)
class RiskEngineConfig:
    capital: CapitalConfig = field(default_factory=CapitalConfig)  # R2·R3·R5 한도 재사용
    consecutive_loss_limit: int = 3  # R10
    data_staleness_limit_seconds: float = 30.0  # R11
    order_error_limit: int = 3  # R12
    order_error_window_seconds: float = 300.0  # R12 "5분 내 3회"


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

        margin_pct = _margin_pct(account)
        if margin_pct > cfg.capital.margin_cap_pct:
            return self._reject(
                f"R3 증거금사용률 {margin_pct:.1f}% > {cfg.capital.margin_cap_pct}% — 진입 거부"
            )

        held_symbols = {p.symbol for p in positions if p.qty != 0}
        projected_count = len(held_symbols | {intent.symbol})
        if projected_count > cfg.capital.max_overnight_positions:
            return self._reject(
                f"R5 포지션수(예상) {projected_count} > {cfg.capital.max_overnight_positions} — "
                "진입 거부"
            )

        return RiskDecision(True, "승인")

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
