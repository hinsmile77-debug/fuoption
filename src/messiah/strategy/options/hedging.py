"""Delta Hedging 정책 — 밴드 헤징 (Ver 1.3 §7, Ver 2.0 §9 W30~31).

목적: 옵션 포지션의 방향 노출을 의도한 범위로 유지("변동성 베팅이 방향 베팅으로 변질되는
것 방지", §7 원문). **방향 중립 의도의 구조만 대상** — 콜매수처럼 방향 자체가 논지인
포지션은 헤지하지 않는다(§7 "방향 의도 포지션은 헤지하지 않는다 — 방향이 논지다").

이 모듈도 §0 아키텍처 원칙 그대로 **계산만 한다**: "헤지 주문도 L4→L5 정상 경로를 탄다
(Options AI가 직접 주문하지 않는다)" — `compute_hedge_qty()`는 미니선물 계약수(부호 포함)만
반환하고, 실제 주문 생성·제출은 호출측(L4 Risk Engine·Sizer 경유)의 몫이다."""

from __future__ import annotations

from dataclasses import dataclass

from messiah.strategy.options.matrix import CALENDAR, IRON_CONDOR

# Ver 1.3 §7 "대상: 방향 중립 의도의 포지션 (Iron Condor, Straddle/Strangle, Calendar)".
# Straddle/Strangle 매도는 이 프로젝트가 안 만든다(matrix.py 모듈 docstring — §6-1 네이키드
# 금지로 IRON_CONDOR로 치환) — 그 구조가 담당하던 "방향 중립 신용" 역할을 IRON_CONDOR가
# 대신하므로 헤지 대상에서도 그대로 이어받는다.
_DIRECTION_NEUTRAL_STRUCTURES = frozenset({IRON_CONDOR, CALENDAR})


@dataclass(frozen=True)
class HedgingConfig:
    delta_band: float = 5.0  # 순델타 밴드 폭(미니선물 계약 상당) — 초기값, 백테스트 재조정 대상
    gamma_shrink_dte_threshold: int = 5  # §7 "만기 임박 고감마 구간" 진입 기준
    gamma_shrink_factor: float = 0.5  # 그 구간에서 밴드를 이 배율로 축소(초기값)


def is_hedge_eligible(structure: str) -> bool:
    """§7 "방향 의도 포지션(콜매수 등)은 헤지하지 않는다" — 대상 구조만 True."""
    return structure in _DIRECTION_NEUTRAL_STRUCTURES


def effective_delta_band(dte: int, config: HedgingConfig = HedgingConfig()) -> float:
    """§7 "만기 임박 고감마 구간에서는 밴드 자동 축소" — DTE가 임계 이하면 밴드를 좁혀
    더 적극적으로 헤지하게 만든다(고감마 구간은 델타가 빠르게 움직여 방치 위험이 크다)."""
    if dte <= config.gamma_shrink_dte_threshold:
        return config.delta_band * config.gamma_shrink_factor
    return config.delta_band


def compute_hedge_qty(
    net_delta: float, *, dte: int, config: HedgingConfig = HedgingConfig()
) -> int | None:
    """반환: 미니선물 계약수(정수, 부호 포함 — 양수=매수, 음수=매도) 또는 밴드 안이면
    None(방치 — §7 "틱마다 헤지 금지, 밴드 안이면 방치, 수수료로 죽는다"). 부호는 옵션
    포트폴리오의 순델타를 상쇄하는 방향 — 순델타가 양수(가격 상승에 유리)면 선물을 매도
    (음수 반환)해 중화한다."""
    band = effective_delta_band(dte, config)
    if abs(net_delta) <= band:
        return None
    return -round(net_delta)
