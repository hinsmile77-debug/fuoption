"""Position Lifecycle Manager — 만기 수명주기 상태기계 (Ver 1.3 §8, Ver 2.0 §9 W30~31).

```
[진입] → [정상 보유] → [이익실현 조건] → 청산
            │
            ├─ [조정 조건] → 다리 롤/부분 청산 신호 → 정상 보유로 복귀
            ├─ [손절 조건] → 강제 청산 (safety.py §6-5)
            └─ [만기 접근 DTE≤2] → 강제 청산 (safety.py §6-4 후반)
```

이 모듈은 **신호만 낸다** — 실제 청산·롤 주문은 L4/L5의 몫(Ver 1.3 §0 아키텍처 원칙,
Options AI는 "출력만 한다"). `evaluate_position()`은 순수 함수로, 보유 포지션의 현재
스냅샷(`HeldPosition`)을 받아 `LifecycleSignal` 하나를 반환한다 — 상태(조정 횟수 등)는
호출측이 `HeldPosition.adjust_count`로 매번 주입한다(`risk/risk_engine.py`의 "주입된 상태 +
순수 계산" 스타일과 동일).

## 안전규칙은 `safety.py`를 그대로 재사용한다

손절(§6-5)·DTE 강제청산(§6-4 후반) 판정 로직 자체는 `safety.py`의
`exceeds_loss_limit()`/`requires_forced_close_by_dte()`를 호출한다 — 같은 규칙을 두 곳에
따로 구현하면 어긋날 위험이 생긴다(모듈 docstring "독립 모듈" 원칙은 "다른 *경로*에서
불러도 규칙 자체는 하나"라는 뜻이지 "규칙을 복제하라"는 뜻이 아니다).

## 롤오버는 실행하지 않는다 — 재심사 신호만 낸다

Ver 1.3 §8 "롤오버는 '새 진입 심사'와 동일한 관문 통과 필요(관성으로 롤 금지)" — 이 모듈의
`ADJUST` 신호는 "반대쪽 다리를 롤하는 게 어떤지 검토하라"는 신호일 뿐, 실제 롤 다리 구성은
`matrix.py`→`evaluator.py`→`safety.py` 정규 경로를 다시 거쳐야 한다(자동 실행 금지).

## Weekly 전용 취급(§8 "보유 1~3일, 크기 절반")은 이번 스코프에 없다

위클리 옵션 여부를 판별할 근거가(만기 DTE 외에) 이 모듈에 없다 — `EventCalendar`가
위클리 만기일 자체는 알지만(`is_expiry_day()`) "이 포지션이 위클리 상품인가"는 심볼
메타데이터가 필요하고, 아직 `StrategyLeg`가 그 정보를 안 갖는다(알려진 갭)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from messiah.core.messages import StrategyCandidate
from messiah.strategy.options import safety

DEFAULT_PROFIT_TAKE_PCT = 0.50  # §8 "수취 프리미엄의 50% 도달 시 청산"
DEFAULT_ADJUST_DELTA_MULTIPLE = 2.0  # §8 "한쪽 다리 델타가 진입 시 2배 도달"
DEFAULT_MAX_ADJUST_COUNT = 1  # §8 "(1회 한도)"


class LifecycleAction(str, Enum):
    HOLD = "HOLD"
    TAKE_PROFIT = "TAKE_PROFIT"
    ADJUST = "ADJUST"
    STOP_LOSS = "STOP_LOSS"
    EXPIRY_FORCE_CLOSE = "EXPIRY_FORCE_CLOSE"
    PRE_EVENT_CLOSE = "PRE_EVENT_CLOSE"  # IV Crush 경계, 매수 포지션 전용


@dataclass(frozen=True)
class LifecycleSignal:
    action: LifecycleAction
    reason: str


@dataclass(frozen=True)
class LifecycleConfig:
    profit_take_pct: float = DEFAULT_PROFIT_TAKE_PCT
    adjust_delta_multiple: float = DEFAULT_ADJUST_DELTA_MULTIPLE
    max_adjust_count: int = DEFAULT_MAX_ADJUST_COUNT
    forced_close_dte: int = safety.DEFAULT_FORCED_CLOSE_DTE
    loss_multiple: float = safety.DEFAULT_LOSS_MULTIPLE


@dataclass(frozen=True)
class HeldPosition:
    """보유 중인 옵션 후보 1개의 현재 스냅샷 — `evaluator.evaluate_candidate()`가 낸
    `StrategyCandidate`(진입 시점 값, 불변)에 "지금" 재료를 얹는다."""

    candidate: StrategyCandidate
    days_held: int
    current_value: float  # 지금 이 포지션 전체를 청산(되사기/되팔기)하는 데 드는 절대값(양수)
    current_leg_deltas: list[float]  # candidate.legs와 동일 순서 — 현재 델타(부호 포함)
    adjust_count: int = 0

    @property
    def min_dte_remaining(self) -> int:
        return min(leg.dte for leg in self.candidate.legs) - self.days_held


def _entry_net_credit(candidate: StrategyCandidate) -> float:
    """`evaluator.evaluate_candidate()`가 `rationale["net_credit_points"]`에 이미 채워둔
    값을 재사용한다(중복 계산 없음) — 없거나 숫자가 아니면 0.0(차변 취급, 이익실현 규칙
    비대상이 되어 안전한 기본값)."""
    value = candidate.rationale.get("net_credit_points", 0.0)
    return float(value) if isinstance(value, (int, float)) else 0.0


def _breach_adjust_threshold(position: HeldPosition, config: LifecycleConfig) -> bool:
    for leg, current_delta in zip(position.candidate.legs, position.current_leg_deltas):
        if leg.is_short and abs(current_delta) >= abs(leg.delta) * config.adjust_delta_multiple:
            return True
    return False


def evaluate_position(
    position: HeldPosition,
    *,
    config: LifecycleConfig = LifecycleConfig(),
    is_macro_event_window: bool | None = None,
) -> LifecycleSignal:
    """우선순위: 손절(§6-5) > 만기 강제청산(§6-4 후반) > 이벤트 전 매수청산(§8 IV Crush) >
    조정(§8) > 이익실현(§8) > 정상 보유. 자본 보존(손절·감마 리스크)이 최우선이라는 원칙을
    그대로 코드 순서에 반영했다."""
    candidate = position.candidate
    net_credit = _entry_net_credit(candidate)
    has_short_leg = any(leg.is_short for leg in candidate.legs)

    if (
        has_short_leg
        and net_credit > 0
        and safety.exceeds_loss_limit(
            net_credit, position.current_value, multiple=config.loss_multiple
        )
    ):
        return LifecycleSignal(
            LifecycleAction.STOP_LOSS,
            f"§6-5 손실이 수취 프리미엄의 {config.loss_multiple}배 도달 — 무조건 청산",
        )

    forced_dte_close = any(
        safety.requires_forced_close_by_dte(
            leg.dte - position.days_held, leg.is_short, dte_threshold=config.forced_close_dte
        )
        for leg in candidate.legs
    )
    if forced_dte_close:
        return LifecycleSignal(
            LifecycleAction.EXPIRY_FORCE_CLOSE,
            f"§6-4 후반 DTE≤{config.forced_close_dte} 매도 다리 — 손익 무관 청산",
        )

    if not has_short_leg and is_macro_event_window:
        return LifecycleSignal(
            LifecycleAction.PRE_EVENT_CLOSE, "§8 IV Crush 경계 — 이벤트 전 매수 포지션 청산 기본값"
        )

    adjust_available = position.adjust_count < config.max_adjust_count
    if adjust_available and _breach_adjust_threshold(position, config):
        return LifecycleSignal(
            LifecycleAction.ADJUST,
            f"§8 다리 델타 진입 시 {config.adjust_delta_multiple}배 도달 — 반대쪽 롤 검토(1회한도)",
        )

    if net_credit > 0 and position.current_value <= net_credit * (1.0 - config.profit_take_pct):
        return LifecycleSignal(
            LifecycleAction.TAKE_PROFIT,
            f"§8 수취 프리미엄의 {config.profit_take_pct:.0%} 도달 — 청산(끝까지 쥐지 않는다)",
        )

    return LifecycleSignal(LifecycleAction.HOLD, "정상 보유")
