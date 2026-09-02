"""확정된 옵션 후보 → `OrderRequest` — 주문 경로 4a-3 (2026-09-02 신설).

## 이 파일이 하는 일과 **하지 않는 일**

한다: `leg_resolution.ResolvedCandidate`(종목코드까지 확정된 후보)를 `OrderRequest`로 옮긴다.
하지 않는다: 제출하지 않고(그건 `OrderGateway`), 수량을 스스로 정하지 않으며(그건 Sizer),
**어떤 자동 경로에도 배선되지 않는다.**

지금 이 함수를 부르는 것은 `scripts/probe_option_order.py`(사람이 손으로 돌리는 검증)뿐이다.
`MetaDecisionEngine`에는 여전히 규칙 ⑥⑦이 없어 `Side.OPTION`이 나올 경로가 없고, 그래서
파이프라인이 이 경로를 자동으로 타는 일은 없다 — 4c까지 가야 생긴다.

## 4a는 **단일 다리 매수만** 낸다

다리가 둘 이상이면 거부한다. 이유는 `OrderRequest`가 단일 다리 계약이기 때문이다
(symbol/side/qty 하나씩) — 여러 다리를 쪼개 내면 **부분체결 시 의도한 적 없는 네이키드**가
생기고 §6-1이 사후에 깨진다. 묶음 주문·부분체결 처리는 4b의 일이다.

매도 다리도 거부한다. 4a의 범위는 최대손실이 프리미엄으로 유한한 **매수**뿐이고, 매도는
증거금·강제청산·만기 처리를 함께 요구한다(4c).

## 지정가는 **호가 격자에 맞춰** 낸다

`contract_spec.round_to_tick()`으로 격자에 올린 뒤 `to_price_units()`로 환산한다. 격자를
벗어난 지정가는 거래소가 거부하고, 그 거부는 주문 실패로만 보여 원인을 찾기 어렵다.

매수 지정가는 **올림**(`mode="up"`)이 기본이다 — 내림하면 격자 한 칸 아래로 내려가 체결이
안 되고, 4a의 판정 기준이 "체결 1건"이라 안 되는 쪽으로 기울면 검증 자체가 막힌다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from messiah.core.messages import OrderKind, OrderRequest, Side
from messiah.strategy.options.contract_spec import round_to_tick, to_price_units
from messiah.strategy.options.leg_resolution import ResolvedCandidate

# 4a가 허용하는 구조 — 단일 다리 **매수**뿐.
SINGLE_LEG_LONG_STRUCTURES = frozenset({"LONG_CALL", "LONG_PUT"})


class OptionOrderRefused(ValueError):
    """4a 범위를 벗어난 후보로 주문을 만들려 했다 — 조용히 만들지 않고 거부한다."""


@dataclass(frozen=True, slots=True)
class OptionOrderPlan:
    """제출 **전** 사람이 읽고 승인할 수 있는 형태 — 검증 스크립트가 이걸 찍어 준다."""

    request: OrderRequest
    symbol: str
    premium_points: Decimal  # 격자에 맞춘 지정가(지수 포인트)
    reference_premium: Decimal  # 체인이 말한 현재가 — 얼마나 올렸는지 보이게
    qty: int

    def describe(self) -> str:
        return (
            f"{self.symbol} {self.qty}계약 매수 · 지정가 {self.premium_points}pt "
            f"(체인 현재가 {self.reference_premium}pt, {self.request.limit_price_ticks}단위)"
        )


def build_option_order(
    resolved: ResolvedCandidate,
    *,
    qty: int,
    intent_id: str = "",
    slippage_ticks: int = 0,
    risk_approved_by: str = "",
) -> OptionOrderPlan:
    """단일 다리 매수 후보 → `OrderRequest`.

    입력: `qty`는 **호출측이 정해서 넘긴다** — 이 함수는 사이징을 하지 않는다(승수가 아직
         미측정이라 원화 기준 사이징 자체가 불가능하다, `contract_spec` §②).
         `slippage_ticks`는 현재가 위로 몇 **표현 단위**(0.01)를 얹을지 — 체결 확률과 지불의
         맞바꿈이라 호출측이 정한다.
    실패 조건: 다리가 둘 이상이거나 매도 다리가 섞이면 `OptionOrderRefused`.
    """
    if qty <= 0:
        raise OptionOrderRefused(f"수량은 1 이상이어야 한다 — 받은 값: {qty}")

    structure = resolved.candidate.structure
    if structure not in SINGLE_LEG_LONG_STRUCTURES:
        raise OptionOrderRefused(
            f"{structure}는 4a 범위 밖이다 — 단일 다리 매수만 낼 수 있다"
            f"({', '.join(sorted(SINGLE_LEG_LONG_STRUCTURES))}). 다중 다리는 부분체결 시 "
            f"네이키드가 생겨 묶음 주문이 선행돼야 한다(4b)"
        )
    if len(resolved.legs) != 1:
        raise OptionOrderRefused(
            f"{structure}인데 다리가 {len(resolved.legs)}개다 — 구조 이름과 다리 수가 어긋났다"
        )

    leg = resolved.legs[0]
    if leg.leg.is_short:
        raise OptionOrderRefused(
            "매도 다리는 4a 범위 밖이다 — 증거금·강제청산·만기 처리가 선행(4c)"
        )
    symbol = leg.leg.symbol
    if not symbol:
        raise OptionOrderRefused("다리에 종목코드가 없다 — leg_resolution을 먼저 통과해야 한다")

    reference = Decimal(str(leg.chain.price))
    # 매수는 올림 — 격자 아래로 내려가면 체결이 안 된다(모듈 docstring).
    limit = round_to_tick(reference + Decimal(slippage_ticks) * Decimal("0.01"), mode="up")
    return OptionOrderPlan(
        request=OrderRequest(
            intent_id=intent_id,
            symbol=symbol,
            kind=OrderKind.ENTRY,
            side=Side.LONG,  # 매수 — 어댑터가 BUY로 옮긴다
            qty=qty,
            limit_price_ticks=to_price_units(limit),
            net_expected_return=resolved.candidate.net_expected_return,
            risk_approved_by=risk_approved_by,
        ),
        symbol=symbol,
        premium_points=limit,
        reference_premium=reference,
        qty=qty,
    )
