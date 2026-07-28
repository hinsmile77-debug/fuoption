"""전략 후보 생성 — 방향×IV 매트릭스 (Ver 1.3 §4, Ver 2.0 §9 W27~29).

방향은 Futures AI(`FuturesView.score`)를 그대로 재사용한다 — Options AI는 방향을 다시
예측하지 않는다(Ver 1.3 §1 "방향의 단일 출처"). IV 상태는 `vol_metrics.IVHistory.rank()`의
산출물을 입력으로 받는다.

## Ver 1.3 §4.1 표와 이 구현의 차이 — 네이키드 매도 라벨을 쓰지 않는다

원문 표는 IV 높음 칸에 "풋매도"(상승)·"콜매도"(하락)·"Strangle 매도"(중립)를 후보로
적지만, Ver 1.3 §6-1 "네이키드 매도 금지 — 모든 매도는 스프레드 구조로만, 예외 없음"과
정면으로 충돌한다(그 규칙 자체가 "학습 대상 아님"·"독립 모듈"이라 예외를 허용 안 함,
`safety.py`). 두 절 사이의 이 긴장을 여기서 조용히 두지 않고 §6을 우선했다 — 매트릭스가
애초에 네이키드 라벨을 만들지 않으면 `safety.py`가 사후에 걸러낼 필요도 없다: "풋매도"는
`BULL_PUT_SPREAD`(신용 풋 스프레드)로, "콜매도"는 `BEAR_CALL_SPREAD`로, "Strangle 매도"는
`IRON_CONDOR`로 치환했다. 셋 다 원문이 말하려던 "IV 높음 → 파는 전략"이라는 논리는
그대로 유지하면서 최대손실을 구조적으로 정의한다.

## Ver 1.3 §4.2 델타 배정도 신용 스프레드에는 문자 그대로 못 쓴다

원문: "매도 다리는 15~30Δ, 매수 다리는 30~50Δ". 차변(debit) 스프레드(`BULL_CALL_SPREAD`
등)엔 그대로 맞는다 — 매수(근접 등가격, 델타 큼) 다리가 30~50Δ, 매도(날개, 델타 작음)
다리가 15~30Δ. 하지만 신용(credit) 스프레드(`BULL_PUT_SPREAD` 등)에 그대로 적용하면
행사가 순서가 뒤집힌다: 풋은 행사가가 높을수록(등가격에 가까울수록) 델타 절대값이
커진다(`surface.py`의 델타-행사가 단조성) — `BULL_PUT_SPREAD`는 "더 높은 행사가 풋을
매도, 더 낮은 행사가 풋을 매수(보호)"라야 순수취(net credit) 구조가 성립하는데, 매도
다리에 원문 그대로 작은 델타(15~30Δ, 낮은 행사가)를 배정하면 매도 행사가가 매수 행사가보다
**낮아져** 버려 구조 자체가 무효가 된다. 그래서 `_build_spec()`은 신용 스프레드에서 두
델타 밴드를 바꿔 배정한다(매도=근접 등가격 30~50Δ, 매수=날개 15~30Δ) — 원문을 신용 쪽까지
문자 그대로 확장하면 계산이 안 맞는다는 걸 검증하며 발견한 것이라 근거를 남긴다."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from messiah.strategy.options.config import OptionsConfig

# ---------------------------------------------------------------- 구조 이름 (safety.py도 참조)

LONG_CALL = "LONG_CALL"
LONG_PUT = "LONG_PUT"
BULL_CALL_SPREAD = "BULL_CALL_SPREAD"
BULL_PUT_SPREAD = "BULL_PUT_SPREAD"
BEAR_PUT_SPREAD = "BEAR_PUT_SPREAD"
BEAR_CALL_SPREAD = "BEAR_CALL_SPREAD"
IRON_CONDOR = "IRON_CONDOR"
CALENDAR = "CALENDAR"

# 신용(credit, 순수취 프리미엄) 구조 — §6-2 "IV Rank < 50에서 credit 전략 금지"의 적용
# 대상이자 DTE 15~45(Theta 수확 구간, §4.2) 대상. 전부 정의된 매도 다리를 가지면서도 보호
# 다리(long leg)로 최대손실을 한정한다(§6-1 네이키드 금지를 만족하는 스프레드 구조).
_CREDIT_STRUCTURES = frozenset({BULL_PUT_SPREAD, BEAR_CALL_SPREAD, IRON_CONDOR})
# 차변(debit, 순지불 프리미엄) 구조 — DTE 20 이상(감마 폭발 구간 회피, §4.2)
_DEBIT_STRUCTURES = frozenset({LONG_CALL, LONG_PUT, BULL_CALL_SPREAD, BEAR_PUT_SPREAD})
# 매도 다리를 실제로 갖는 구조(스프레드는 순매수/순매도 구조든 전부 매도 다리 하나를 낀다 —
# 매수 다리만 있는 LONG_CALL/LONG_PUT만 예외) — §4.2 "매도 다리는 15~30Δ" 적용 대상
_HAS_SHORT_LEG = frozenset(
    {BULL_CALL_SPREAD, BULL_PUT_SPREAD, BEAR_PUT_SPREAD, BEAR_CALL_SPREAD, IRON_CONDOR}
)
# 매수 다리를 실제로 갖는 구조(스프레드 전부 + 순수 매수 둘) — §4.2 "매수 다리는 30~50Δ" 대상
_HAS_LONG_LEG = frozenset(
    {
        LONG_CALL,
        LONG_PUT,
        BULL_CALL_SPREAD,
        BULL_PUT_SPREAD,
        BEAR_PUT_SPREAD,
        BEAR_CALL_SPREAD,
        IRON_CONDOR,
    }
)
# 풋매도 다리를 포함해 Skew 극단 필터(§4.2) 대상인 구조
_SHORT_PUT_LEG_STRUCTURES = frozenset({BULL_PUT_SPREAD, IRON_CONDOR})


class Direction(str, Enum):
    UP = "UP"
    NEUTRAL = "NEUTRAL"
    DOWN = "DOWN"


class IVState(str, Enum):
    LOW = "LOW"
    MID = "MID"
    HIGH = "HIGH"


_MATRIX: dict[tuple[Direction, IVState], list[str]] = {
    (Direction.UP, IVState.LOW): [LONG_CALL, BULL_CALL_SPREAD],
    (Direction.UP, IVState.MID): [BULL_CALL_SPREAD],
    (Direction.UP, IVState.HIGH): [BULL_PUT_SPREAD],
    (Direction.NEUTRAL, IVState.LOW): [CALENDAR],
    (Direction.NEUTRAL, IVState.MID): [],  # 관망 — 우위 없음(Ver 1.3 §4 "논리" 항)
    (Direction.NEUTRAL, IVState.HIGH): [IRON_CONDOR],
    (Direction.DOWN, IVState.LOW): [LONG_PUT, BEAR_PUT_SPREAD],
    (Direction.DOWN, IVState.MID): [BEAR_PUT_SPREAD],
    (Direction.DOWN, IVState.HIGH): [BEAR_CALL_SPREAD],
}


def classify_direction(score: float, config: OptionsConfig = OptionsConfig()) -> Direction:
    if score > config.direction_score_threshold:
        return Direction.UP
    if score < -config.direction_score_threshold:
        return Direction.DOWN
    return Direction.NEUTRAL


def classify_iv_state(
    iv_rank: float | None, config: OptionsConfig = OptionsConfig()
) -> IVState | None:
    """`iv_rank`가 None(이력 부족, `vol_metrics.IVHistory.rank()` 계약)이면 판정 불가 — 후보
    생성 자체를 보류해야 한다는 신호로 None을 반환한다."""
    if iv_rank is None:
        return None
    if iv_rank < config.iv_rank_low:
        return IVState.LOW
    if iv_rank > config.iv_rank_high:
        return IVState.HIGH
    return IVState.MID


def is_credit_structure(structure: str) -> bool:
    return structure in _CREDIT_STRUCTURES


def has_short_put_leg(structure: str) -> bool:
    return structure in _SHORT_PUT_LEG_STRUCTURES


@dataclass(frozen=True)
class CandidateSpec:
    """후보 구조 1개의 생성 파라미터 — `evaluator.py`가 이걸로 실제 다리(strike/만기)를
    만든다(Ver 1.3 §4는 "생성 규칙", §5 "평가"는 다음 서브페이즈 책임 분리)."""

    structure: str
    is_credit: bool
    short_leg_delta_range: tuple[float, float] | None  # None = 매도 다리 없음(순수 매수 구조)
    long_leg_delta_range: tuple[float, float] | None  # None = 매수 다리 없음(순수 매도 없음, §6-1)
    dte_low: int
    dte_high: int | None  # None = 상한 없음(순수 매수 구조, Ver 1.3 §4.2 "DTE 20 이상")


def candidate_specs(
    score: float, iv_rank: float | None, config: OptionsConfig = OptionsConfig()
) -> list[CandidateSpec]:
    """`FuturesView.score`와 IV Rank로 매트릭스 셀을 찾아 구조별 생성 파라미터를 만든다.
    IV 상태 미판정(iv_rank=None)이면 빈 목록 — "우위를 판단할 재료가 없다"는 §5.2
    NO_OPTION과 동형(호출측이 rationale에 "IV 이력 부족"을 남길 수 있도록 빈 목록으로
    구분, 예외를 던지지 않는다)."""
    direction = classify_direction(score, config)
    iv_state = classify_iv_state(iv_rank, config)
    if iv_state is None:
        return []

    structures = _MATRIX[(direction, iv_state)]
    specs = [_build_spec(structure, config) for structure in structures]
    return specs[: config.max_candidates]


def _build_spec(structure: str, config: OptionsConfig) -> CandidateSpec:
    wing_range = (config.short_leg_delta_low, config.short_leg_delta_high)  # 15~30Δ
    near_money_range = (config.long_leg_delta_low, config.long_leg_delta_high)  # 30~50Δ
    has_short_leg = structure in _HAS_SHORT_LEG
    has_long_leg = structure in _HAS_LONG_LEG
    is_credit = structure in _CREDIT_STRUCTURES
    if is_credit:
        # 신용 스프레드는 매도 다리가 행사가상 매수 다리보다 등가격에 가까워야 한다(풋은
        # 행사가가 높을수록, 콜은 낮을수록 델타 절대값이 커진다 — surface.py 모듈의 델타·
        # 행사가 단조성 그대로) — 그래야 순수취(net credit)가 나오고 행사가 순서가 성립한다.
        # 매도 다리 = 근접 등가격(30~50Δ), 매수(보호) 다리 = 날개(15~30Δ) — Ver 1.3 §4.2
        # 원문 "매도 다리는 15~30Δ, 매수 다리는 30~50Δ"를 문자 그대로 신용 스프레드에
        # 적용하면 매도/매수 행사가 순서가 뒤집혀 구조 자체가 무효가 된다(모듈 docstring에
        # 근거 계산 기록). 원문은 차변(debit) 스프레드 기준 서술로 해석해 그쪽만 문자 그대로
        # 따른다.
        short_range = near_money_range if has_short_leg else None
        long_range = wing_range if has_long_leg else None
    else:
        short_range = wing_range if has_short_leg else None
        long_range = near_money_range if has_long_leg else None

    if is_credit:
        dte_low, dte_high = config.short_structure_dte_low, config.short_structure_dte_high
    elif structure in _DEBIT_STRUCTURES:
        dte_low, dte_high = config.long_structure_dte_min, None
    else:
        # CALENDAR 등 위 두 분류에 안 든 구조 — Ver 1.3 §4.2 "Weekly는 이벤트 플레이 전용"류
        # 특수 규칙은 아직 없음(알려진 갭, evaluator.py 확장 시 다룰 자리).
        dte_low, dte_high = config.short_structure_dte_low, None
    return CandidateSpec(
        structure=structure,
        is_credit=is_credit,
        short_leg_delta_range=short_range,
        long_leg_delta_range=long_range,
        dte_low=dte_low,
        dte_high=dte_high,
    )


def skew_excludes_short_put(
    structure: str, skew_value: float, config: OptionsConfig = OptionsConfig()
) -> bool:
    """Skew 절대값 극단 시 풋매도 다리 포함 구조 제외(Ver 1.3 §4.2). 극단은 방향(급락 공포는
    양의 skew) 무관하게 절대값으로 판정 — 아직 부호별 비대칭 근거가 없어서다(초기값,
    Walk-Forward 재추정 대상)."""
    if not has_short_put_leg(structure):
        return False
    return abs(skew_value) > config.skew_extreme_threshold
