"""매도 전략 안전규칙 — Hard Rules (Ver 1.3 §6, Ver 2.0 §9 W30~31).

"ML이 아니라 고정 규칙"(§6 원문) — `matrix.py`(신규 후보 생성)와 `lifecycle.py`(보유 포지션
관리) 양쪽에서 호출되는 독립 모듈이다. 어느 한쪽이 실수로 안전규칙을 우회해도 다른 쪽
호출 경로가 여전히 막는다는 것이 "독립 모듈"의 의미 — 그래서 이 모듈은 `matrix`/`evaluator`/
`lifecycle` 어느 것도 import하지 않고 `StrategyCandidate`/`StrategyLeg`(core/messages.py)
값만으로 판정한다.

## §6-3(이벤트 캘린더)은 부분 구현 — 알려진 갭

`core/event_calendar.py`는 KRX 개장일·정규장 판정만 다룬다(그 모듈 자체 docstring
"FOMC·CPI 등 경제지표 캘린더는 스코프 밖"). 이 모듈의 `check_event_window()`는 그래서
호출측이 `is_macro_event_window`를 명시적으로 넘겨야 판정하고, `None`(미판정)이면 항상
통과시킨다 — 없는 데이터를 있다고 가정해 거짓 안전감을 주지 않는다.

## §6-6(포트폴리오 순 Vega·감마 한도)은 이 모듈의 책임이 아니다

원문 그대로 "L4 Risk Engine이 보유 — Options AI는 후보에 Greeks를 첨부할 의무만 갖는다"
— `risk/risk_engine.py`의 R7/R8이 그 자리다(Ver 2.0 §9 W30~31에서 함께 구현).
"""

from __future__ import annotations

from dataclasses import dataclass

from messiah.core.messages import StrategyCandidate
from messiah.strategy.options.config import OptionsConfig
from messiah.strategy.options.matrix import is_credit_structure

DEFAULT_FORCED_CLOSE_DTE = 2  # §6-4 후반부 "DTE≤2 매도 포지션은 이익/손실 무관 청산"
DEFAULT_LOSS_MULTIPLE = 2.0  # §6-5 "수취 프리미엄의 2배 도달 → 무조건 청산"


@dataclass(frozen=True)
class SafetyVerdict:
    allowed: bool
    violations: list[str]


# ---------------------------------------------------------------- 신규 진입 게이트 (matrix.py 호출)


def check_naked_short(candidate: StrategyCandidate) -> str | None:
    """§6-1 "네이키드 매도 금지 — 예외 없음". `max_loss`가 None이면(무한 최대손실) 위반.
    반환: None=통과, 문자열=위반 사유(사람이 읽을 로그/rationale에 그대로 쓸 수 있게)."""
    if candidate.max_loss is None:
        return "§6-1 네이키드 매도 금지 — 최대손실이 정의되지 않은 구조"
    return None


def check_credit_iv_floor(
    candidate: StrategyCandidate, iv_rank: float, config: OptionsConfig = OptionsConfig()
) -> str | None:
    """§6-2 "IV Rank < 50에서 credit 전략 금지" — 싼 보험을 파는 것은 우위가 아니다."""
    if is_credit_structure(candidate.structure) and iv_rank < config.credit_iv_rank_floor:
        return f"§6-2 IV Rank {iv_rank:.1f} < {config.credit_iv_rank_floor} — credit 전략 금지"
    return None


def check_event_window(
    candidate: StrategyCandidate, is_macro_event_window: bool | None
) -> str | None:
    """§6-3 "이벤트 캘린더 D-1~D0: 신규 매도 진입 금지". `is_macro_event_window=None`(미판정,
    모듈 docstring 갭)이면 항상 통과 — 매도 다리가 없는 순수 매수 후보는 애초에 대상이 아님."""
    if is_macro_event_window is not True:
        return None
    has_short_leg = any(leg.is_short for leg in candidate.legs)
    if has_short_leg:
        return "§6-3 이벤트 캘린더 D-1~D0 — 신규 매도 진입 금지"
    return None


def check_expiry_day_entry(candidate: StrategyCandidate, is_expiry_day: bool) -> str | None:
    """§6-4 전반부 "만기일(DTE=0) 신규 진입 금지". `is_expiry_day`는 호출측이
    `EventCalendar.is_expiry_day()`(이미 구현됨)로 판정해 넘긴다."""
    if is_expiry_day:
        return "§6-4 만기일(DTE=0) 신규 진입 금지"
    return None


def evaluate_candidate_safety(
    candidate: StrategyCandidate,
    *,
    iv_rank: float,
    config: OptionsConfig = OptionsConfig(),
    is_macro_event_window: bool | None = None,
    is_expiry_day: bool = False,
) -> SafetyVerdict:
    """4개 신규진입 규칙(§6-1~4)을 전부 적용 — 하나라도 위반이면 `allowed=False`.
    `matrix.py`/`service.py`가 후보를 `intel.options`로 내보내기 전 마지막 관문."""
    violations = [
        v
        for v in (
            check_naked_short(candidate),
            check_credit_iv_floor(candidate, iv_rank, config),
            check_event_window(candidate, is_macro_event_window),
            check_expiry_day_entry(candidate, is_expiry_day),
        )
        if v is not None
    ]
    return SafetyVerdict(allowed=not violations, violations=violations)


# ---------------------------------------------------------------- 보유 포지션 감시 (lifecycle.py)


def requires_forced_close_by_dte(
    dte: int, is_short: bool, *, dte_threshold: int = DEFAULT_FORCED_CLOSE_DTE
) -> bool:
    """§6-4 후반부 "DTE≤2 매도 포지션은 이익/손실 무관 청산(감마 폭발 구간)". 매수 포지션은
    대상이 아니다(손실이 프리미엄으로 한정돼 감마 폭발의 피해자가 아니라 수혜자일 수도 있다)."""
    return is_short and dte <= dte_threshold


def exceeds_loss_limit(
    entry_credit: float, current_value: float, *, multiple: float = DEFAULT_LOSS_MULTIPLE
) -> bool:
    """§6-5 "매도 포지션 손실이 수취 프리미엄의 2배 도달 → 무조건 청산(물타기·롤 금지)".

    입력: entry_credit(진입 시 수취한 프리미엄, 양수), current_value(지금 그 포지션을 되사는
         데 드는 비용 — 포지션 시가, 양수). 손실 = current_value − entry_credit.
    실패 조건: entry_credit<=0이면(수취 프리미엄 자체가 없거나 데이터 이상) 판정 불가로
              보수적으로 False(강제청산 신호를 못 만든다 — 잘못된 신호보다 무신호가 낫다는
              뜻이 아니라, 이 함수의 입력 계약 위반이라 호출측 버그를 먼저 의심해야 함)."""
    if entry_credit <= 0:
        return False
    loss = current_value - entry_credit
    return loss >= entry_credit * multiple
