"""규칙층 — 통계층(HMM) 결과를 덮어쓰는 오버라이드 (Ver 1.6 §3.1, Ver 2.0 §9 W20~21).

원문 3규칙(우선순위 순):
  1. `ev_econ_grade ≥ 2` 이고 `ev_econ_prox ≤ 1`일 → 강제 EVENT
  2. 위클리/동시만기 당일 → 강제 EVENT
  3. `vl_vol_ratio > 극단 임계` → 즉시 HIGH_VOL (HMM 갱신 지연 보완)

**1·2번은 구조만 준비하고 지금은 절대 발동하지 않는다** — Event Calendar Service(경제
지표·휴장일)와 옵션 만기 캘린더가 둘 다 미구현이라(오래된 기존 갭, capability_matrix.md)
입력 자체가 없다. `RuleContext`의 해당 필드는 항상 `None`이고, 각 규칙 함수는 `None`이면
"모른다"로 보고 침묵 없이 그냥 통과(다음 규칙으로)시킨다 — 나중에 두 서비스가 생기면
`RuleContext`를 채워 넣기만 하면 되도록 인터페이스를 지금 확정해 둔다. **3번만 지금
실제로 계산 가능**(vl_core.vl_vol_ratio, 이번 주 신규)해서 유일하게 살아있는 규칙이다.

규칙 체인은 "첫 매치 우선"이다 — 여러 규칙이 동시에 맞아도 먼저 나열된 것(=원문의
우선순위)이 이긴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from messiah.core.messages import Regime

# Ver 1.6 §3.1 "극단 임계" — 구체 수치 미명시, 판단으로 정함. 명명층(naming.py)의 통계적
# 고변동성 배정 기준(HIGH_VOL_MULTIPLIER=1.5배)보다 규칙층의 "즉시" 오버라이드는 더 확실한
# 극단이어야 한다고 보고 더 큰 값을 씀 — 사람 검수 대상 1순위(naming.py와 동일 사유).
VOL_EXTREME_THRESHOLD = 3.0


@dataclass(frozen=True)
class RuleContext:
    vol_ratio: float | None = None
    econ_grade: int | None = None  # 미구현(Event Calendar 없음) — 항상 None
    econ_prox_days: int | None = None  # 〃
    is_expiry_day: bool | None = None  # 미구현(옵션 만기 캘린더 없음) — 항상 None


Rule = Callable[[RuleContext], Regime | None]


def rule_economic_event(context: RuleContext) -> Regime | None:
    """Ver 1.6 §3.1 "ev_econ_grade≥2 이고 ev_econ_prox≤1일 → 이벤트" — 입력이 항상
    None이라(모듈 docstring) 지금은 절대 발동하지 않는다. 로직 자체는 데이터가 생기면
    바로 쓸 수 있게 완성해 둠."""
    if (
        context.econ_grade is not None
        and context.econ_grade >= 2
        and context.econ_prox_days is not None
        and context.econ_prox_days <= 1
    ):
        return Regime.EVENT
    return None


def rule_expiry_day(context: RuleContext) -> Regime | None:
    """Ver 1.6 §3.1 "위클리/동시만기 당일 → 이벤트" — 입력이 항상 None이라(모듈
    docstring) 지금은 절대 발동하지 않는다."""
    if context.is_expiry_day:
        return Regime.EVENT
    return None


def rule_volatility_extreme(context: RuleContext) -> Regime | None:
    """Ver 1.6 §3.1 "vl_vol_ratio > 극단 임계 → 즉시 고변동성" — 지금 유일하게 살아있는
    규칙(vl_core.vl_vol_ratio가 실제로 계산 가능)."""
    if context.vol_ratio is not None and context.vol_ratio > VOL_EXTREME_THRESHOLD:
        return Regime.HIGH_VOL
    return None


DEFAULT_RULE_CHAIN: tuple[Rule, ...] = (
    rule_economic_event,
    rule_expiry_day,
    rule_volatility_extreme,
)  # 순서 = 우선순위(Ver 1.6 §3.1 원문 나열 순서 그대로)


@dataclass(frozen=True)
class RuleOverrideResult:
    regime: Regime
    reason: str  # 발동한 규칙 함수명 — RegimeState.rule_override에 그대로 기록


def apply_rules(
    context: RuleContext, chain: Sequence[Rule] = DEFAULT_RULE_CHAIN
) -> RuleOverrideResult | None:
    """체인을 순서대로 평가해 첫 번째로 발동한 규칙의 결과를 반환(첫 매치 우선). 아무
    규칙도 발동하지 않으면 None — 호출자(service.py)가 통계층 결과를 그대로 쓴다."""
    for rule in chain:
        regime = rule(context)
        if regime is not None:
            return RuleOverrideResult(regime=regime, reason=rule.__name__)
    return None
