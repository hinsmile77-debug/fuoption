"""옵션 계약 명세 — 호가단위(실측 확정)와 계약승수(미측정, 차단) (2026-09-02 신설, 4a-2).

주문 경로 4a가 요구한 두 값이다. **하나는 재서 확정했고, 하나는 못 재서 막았다** — 못 잰
값을 선물 값으로 대신 쓰던 상태가 이 파일이 생긴 이유다.

# ① 호가단위 — 실거래가에서 역산해 확정 (실계좌 호출 없이)

`Docs/capability_matrix.md`가 *"미니선물 0.02를 옵션·타 근월물에도 동일하다고 가정하지 말 것 —
상품·행사가 구간별 실측 필요"*라고 경고해 뒀다. 그 실측을 **이미 수집된 체결가**로 했다:
2026-08-05~09-02 3시리즈 아카이브의 고유 관측가 2,484개(0.01~123.95)를 프리미엄 구간별로
갈라 인접 관측가의 최소 간격을 봤다.

    프리미엄 구간     고유 관측가   최소 관측 간격
    < 1.00              99          0.01
    1.00 ~ 3.00        200          0.01
    3.00 ~ 10.00       700          0.01
    >= 10.00         1,485          0.05      ← 0.01~0.04 간격이 **한 번도 없다**

즉 **10.00 미만은 0.01, 10.00 이상은 0.05**다. 관측가가 전부 그 배수라는 것(필요조건)에
더해, 10.00 이상 구간에서 0.05보다 촘촘한 간격이 1,485개 표본에서 한 번도 안 나온 것이
경계의 근거다(충분조건 쪽).

**주의**: 이건 *관측된* 규칙이지 KIS·거래소가 문서로 보장한 값이 아니다. 경계(10.00)를 낀
주문이 거부되면 그 거부가 이 상수를 다시 재라는 신호다 — `OptionTickSizeRejected` 계열로
남기고 여기 표를 고칠 것.

# ② 계약승수 — **못 쟀다. 그래서 막는다.**

`risk/sizer.DEFAULT_POINT_VALUE_KRW`(50,000)는 미니**선물** 값이고, `risk_engine`이
`option_point_value_krw`에 그 값을 그대로 쓰면서 주석으로 *"옵션 전용 계약승수는 아직
미실측"*이라 자백해 뒀다. 정규 KOSPI200 옵션의 승수는 미니선물과 다르다.

찾을 수 있는 곳을 전부 봤고 **없었다**:
  - 종목 마스터파일(`fo_idx_code_mts.mst`): 전 8,943행이 **정확히 9필드**이고 승수 필드가 없다.
  - 체인 시세(`get_quote(O)` output1~3): 가격·그릭스·OI·잔존일수뿐.
  - 잔고(`get_balance`): 포지션이 있어야 나오는데 옵션 포지션이 없다(닭과 달걀).

**그래서 상수를 지어내지 않는다.** `OPTION_POINT_VALUE_KRW = None`으로 두고, 이 값을 필요로
하는 계산(수량 산정·원화 환산)은 **거부**한다. 틀린 승수로 계산한 수량은 조용히 5배 크거나
작은 주문이 되고, 그건 이 프로젝트가 가장 피하려는 종류의 사고다(마흐디 L16 "단위를 확인
없이 스키마부터 정한 사고"와 같은 형태).

측정 방법은 `scripts/probe_option_contract.py`가 안내한다 — 실계좌에서 **1계약을 실제로
체결**시켜 잔고·증거금 변화로 역산하는 것이 유일하게 확실한 경로다(4a-4의 판정 기준과 같은
행위라 따로 비용이 들지 않는다).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# 프리미엄 경계와 그 위/아래 호가단위 — §① 실측.
TICK_BOUNDARY_PREMIUM = Decimal("10.00")
TICK_BELOW_BOUNDARY = Decimal("0.01")
TICK_AT_OR_ABOVE_BOUNDARY = Decimal("0.05")

# §② 미측정. **None은 "모른다"이고, 0이나 선물 값으로 대체하지 않는다.**
OPTION_POINT_VALUE_KRW: Decimal | None = None


class OptionContractSpecUnknown(RuntimeError):
    """승수를 모르는 채로 원화 환산·수량 산정을 시도했다 — 계산이 아니라 거부가 맞다."""


def tick_size_for_premium(premium: Decimal | float) -> Decimal:
    """그 프리미엄 수준에서의 호가단위 (§① 실측).

    입력: 지수 포인트 단위 프리미엄(0 이상).
    실패 조건: 음수면 ValueError — 옵션 프리미엄은 음수가 될 수 없고, 음수가 들어왔다는 것은
              호출부가 손익과 가격을 섞은 것이다.
    """
    value = Decimal(str(premium))
    if value < 0:
        raise ValueError(f"프리미엄은 0 이상이어야 한다 — 받은 값: {premium!r}")
    return TICK_BELOW_BOUNDARY if value < TICK_BOUNDARY_PREMIUM else TICK_AT_OR_ABOVE_BOUNDARY


def round_to_tick(premium: Decimal | float, *, mode: str = "nearest") -> Decimal:
    """호가단위 격자에 맞춘 가격 — 지정가 주문이 거부되지 않게 하는 마지막 관문.

    `mode`: "nearest"(기본) · "down"(매수 지정가를 보수적으로) · "up"(매도 지정가를 보수적으로).

    경계(10.00) 근처를 조심한다: 9.99를 올림하면 10.00이 되고 그 순간 격자가 0.05로 바뀐다 —
    반올림 결과가 다시 그 구간의 격자에 맞는지 한 번 더 확인한다.
    """
    value = Decimal(str(premium))
    if value < 0:
        raise ValueError(f"프리미엄은 0 이상이어야 한다 — 받은 값: {premium!r}")
    for _ in range(2):  # 경계를 넘나들면 한 번 더 맞춘다(두 번이면 반드시 수렴한다)
        tick = tick_size_for_premium(value)
        quotient = value / tick
        if mode == "down":
            snapped = quotient.to_integral_value(rounding="ROUND_FLOOR") * tick
        elif mode == "up":
            snapped = quotient.to_integral_value(rounding="ROUND_CEILING") * tick
        else:
            snapped = quotient.to_integral_value(rounding="ROUND_HALF_UP") * tick
        if snapped == value or tick_size_for_premium(snapped) == tick:
            return snapped
        value = snapped
    return value


# **가격 표현 단위 — 호가 격자와 다른 것이다.** (2026-09-02, 왕복 검사가 잡은 설계 결함)
#
# 처음엔 `limit_price_ticks`를 "그 가격대의 호가단위 개수"로 두려 했는데, 그러면 **정수 하나가
# 두 가격을 뜻한다**: 200틱은 0.01×200 = 2.00이기도 하고 0.05×200 = 10.00이기도 하다. 실제로
# 왕복 검사에서 10.00 · 10.05 · 26.30이 전부 1/5로 되돌아왔다.
#
# 그래서 둘을 가른다:
#   - **표현 단위**(이 상수, 0.01 고정): `limit_price_ticks`가 세는 것. 유일하고 왕복한다.
#   - **호가 격자**(`tick_size_for_premium`, 가격대별): 거래소가 **받아주는** 값인가.
# 10.00 이상에서는 표현 단위 5개가 격자 1칸이다 — 그 제약은 `round_to_tick()`이 강제한다.
#
# 선물은 둘이 같아서(0.02 하나) 이 구분이 필요 없었고, 그래서 `OrderRequest.limit_price_ticks`
# 계약에 그 구분이 없다. 옵션이 처음으로 그것을 요구한다.
OPTION_PRICE_UNIT = Decimal("0.01")


def to_price_units(premium: Decimal | float) -> int:
    """지수 포인트 프리미엄 → `OrderRequest.limit_price_ticks`(0.01 단위 개수).

    호가 격자에 먼저 맞춘 뒤 환산한다 — 격자를 벗어난 지정가는 거래소가 거부한다.
    """
    snapped = round_to_tick(premium)
    return int(snapped / OPTION_PRICE_UNIT)


def from_price_units(units: int) -> Decimal:
    """`to_price_units()`의 역. 표현 단위가 상수라 **항상 왕복한다.**"""
    return OPTION_PRICE_UNIT * units


def is_on_tick_grid(premium: Decimal | float) -> bool:
    """거래소가 받아줄 가격인가 — 표현은 가능해도 격자를 벗어난 값이 있다(예: 10.03)."""
    value = Decimal(str(premium))
    tick = tick_size_for_premium(value)
    return value == (value / tick).to_integral_value(rounding="ROUND_HALF_UP") * tick


@dataclass(frozen=True, slots=True)
class OptionContract:
    """주문 한 건이 알아야 하는 계약 명세 — 승수가 없으면 원화 환산을 **거부**한다."""

    symbol: str
    point_value_krw: Decimal | None = OPTION_POINT_VALUE_KRW

    @property
    def multiplier_is_measured(self) -> bool:
        return self.point_value_krw is not None

    def premium_to_krw(self, premium: Decimal | float) -> Decimal:
        """프리미엄(지수 포인트) → 원화. **승수 미측정이면 예외** — 추정치로 계산하지 않는다."""
        if self.point_value_krw is None:
            raise OptionContractSpecUnknown(
                f"{self.symbol}: 옵션 계약승수가 아직 실측되지 않았다 — 원화 환산·수량 산정 불가. "
                "선물 승수(50,000)로 대신하면 주문 크기가 조용히 틀린다. "
                "`scripts/probe_option_contract.py` 참고(4a-2)"
            )
        return Decimal(str(premium)) * self.point_value_krw
