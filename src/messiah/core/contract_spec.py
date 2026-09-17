"""계약 명세 — 거래승수와 호가단위의 **정본** (2026-09-17, F-114 마무리).

## 왜 설정이 아니라 코드인가

2026-09-17에 `InstanceConfig.contract_multiplier`(기본값 `None`)를 먼저 만들었다. "없는
상수를 코드가 지어내지 않는다"(R4)는 판단은 옳았지만, 그 결과 **거래소가 공표한 사실이
PC별 설정값처럼** 보이게 됐다. 승수는 이 PC의 취향이 아니라 KRX 명세다 — 사람이 매 PC에
적어야 하는 값이면 PC마다 다른 승수로 손익을 계산하는 상태가 구조적으로 가능해진다.

그래서 **출처를 달아 코드에 못 박고**, `configs/instance.yaml`에서는 그 필드를 없앴다.
`universe.py`가 어휘의 정본인 것과 같은 자리다 — 토큰이 여기 키가 된다.

## 출처 (2026-09-17 확인)

2017-03-27 KRX 거래승수 인하 시행: 코스피200선물·옵션 50만 → **25만원**, 미니코스피200
선물·옵션 10만 → **5만원**. 아래 표는 한국투자증권·유진투자선물의 상품명세 페이지에서
확인한 값이다(`docs/` 별도 인용 없음 — 이 docstring이 출처 기록이다).

    상품                     거래승수    호가단위                최소가격변동금액
    코스피200선물            250,000원   0.05                    12,500원
    미니 코스피200선물        50,000원   0.02                     1,000원
    코스피200옵션(먼쓰리)    250,000원   <10pt 0.01 / >=10 0.05   2,500 / 12,500원
    코스피200위클리옵션      250,000원   <10pt 0.01 / >=10 0.05   2,500 / 12,500원
    미니 코스피200옵션        50,000원   <3 0.01 /<10 0.02 />= 0.05  500/1,000/2,500원

## 이 표가 스스로를 검산한다

`거래승수 × 호가단위 = 최소가격변동금액`은 거래소가 **세 값을 다 공표**하기 때문에 성립하는
항등식이다. 세 값을 다 적어 두고 그 곱을 단언하면, 옮겨 적다 틀린 값이 기동 전에 깨진다 —
승수 하나만 적어 두면 10배 오타(25만을 25,000으로)가 조용히 손익 10분의 1로 나타난다.
`tests/test_contract_spec.py`가 전 상품에 대해 이 항등식을 지킨다.

## 옵션의 틱 가치는 **상수가 아니다**

프리미엄 구간에 따라 호가단위가 달라지므로 "옵션 1틱 = N원"이라는 값은 존재하지 않는다.
`tick_value_won()`이 가격을 요구하는 이유이고, 가격 없이 부르면 옵션에 대해서는 예외를
던진다 — 임의로 한 구간을 골라 답하면 그게 바로 R4가 금지하는 지어낸 상수다.

지금 손익을 계산하는 경로(G2 페이퍼 트레이딩)는 **미니선물 단일 상품**이라 구간이 하나뿐이고,
그래서 가격 없이도 답할 수 있다. 옵션 실행 경로가 생기면 그때 가격을 넘기면 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from messiah.core import universe

# ---------------------------------------------------------------- 자료 구조


@dataclass(frozen=True)
class TickBand:
    """한 가격 구간의 호가단위와 그 구간에서의 최소가격변동금액.

    `upper_bound_points`가 `None`이면 상한 없음(마지막 구간). 경계는 **미만**이다 —
    거래소 표기가 "프리미엄 10포인트 미만 0.01 / 10포인트 이상 0.05"이므로 10.0은 위 구간이다.
    """

    upper_bound_points: Decimal | None
    tick_size_points: Decimal
    #: 거래소가 함께 공표하는 값. 검산용으로 **같이** 적는다(모듈 docstring "스스로를 검산한다").
    min_price_move_won: int


@dataclass(frozen=True)
class ContractSpec:
    """한 상품의 계약 명세."""

    token: str
    name: str
    #: 원 / 지수포인트.
    multiplier_won: int
    bands: tuple[TickBand, ...]

    @property
    def has_single_tick(self) -> bool:
        return len(self.bands) == 1

    def band_for(self, price_points: Decimal | float | None) -> TickBand:
        """그 가격이 속한 구간. 구간이 하나뿐이면 가격을 안 물어도 된다.

        구간이 둘 이상인데 가격이 없으면 **답하지 않는다**(예외) — 한 구간을 임의로 고르면
        그게 지어낸 상수다(모듈 docstring "옵션의 틱 가치는 상수가 아니다").
        """
        if self.has_single_tick:
            return self.bands[0]
        if price_points is None:
            raise ValueError(
                f"{self.token}은 프리미엄 구간마다 호가단위가 다르다 — 가격 없이는 "
                f"틱 가치를 정할 수 없다(구간 {len(self.bands)}개)"
            )
        price = Decimal(str(price_points))
        for band in self.bands:
            if band.upper_bound_points is None or price < band.upper_bound_points:
                return band
        return self.bands[-1]

    def tick_value_won(self, price_points: Decimal | float | None = None) -> int:
        """1틱이 몇 원인가 = `거래승수 × 호가단위`.

        거래소가 공표한 `min_price_move_won`과 같아야 하고, 그 단언은
        `tests/test_contract_spec.py`가 전 구간에 대해 건다.
        """
        band = self.band_for(price_points)
        return int(Decimal(self.multiplier_won) * band.tick_size_points)


def _band(upper: str | None, tick: str, won: int) -> TickBand:
    return TickBand(
        upper_bound_points=None if upper is None else Decimal(upper),
        tick_size_points=Decimal(tick),
        min_price_move_won=won,
    )


# ---------------------------------------------------------------- 표 (정본)

#: 옵션 3종(먼쓰리·월위클리·목위클리)은 승수·호가단위가 **동일하다** — 다른 것은 만기 주기다
#: (`universe.py` "왜 옵션을 시리즈별 토큰으로 쪼갰나"). 그래도 토큰마다 항목을 두는 이유는
#: 유니버스에 토큰이 하나 늘 때 여기가 비면 기동이 깨져야 하기 때문이다(아래 `spec_for`).
_OPTION_BANDS: Final[tuple[TickBand, ...]] = (
    _band("10", "0.01", 2_500),
    _band(None, "0.05", 12_500),
)

SPECS: Final[dict[str, ContractSpec]] = {
    universe.K200_MINI_FUT: ContractSpec(
        token=universe.K200_MINI_FUT,
        name="미니 코스피200선물",
        multiplier_won=50_000,
        bands=(_band(None, "0.02", 1_000),),
    ),
    universe.K200_OPT_MONTHLY: ContractSpec(
        token=universe.K200_OPT_MONTHLY,
        name="코스피200옵션(먼쓰리)",
        multiplier_won=250_000,
        bands=_OPTION_BANDS,
    ),
    universe.K200_OPT_WEEKLY_MON: ContractSpec(
        token=universe.K200_OPT_WEEKLY_MON,
        name="코스피200위클리옵션(월)",
        multiplier_won=250_000,
        bands=_OPTION_BANDS,
    ),
    universe.K200_OPT_WEEKLY_THU: ContractSpec(
        token=universe.K200_OPT_WEEKLY_THU,
        name="코스피200위클리옵션(목)",
        multiplier_won=250_000,
        bands=_OPTION_BANDS,
    ),
}

#: 유니버스에 **없는** 상품. 거래 대상이 아니지만 교차검증·비교에 쓰이므로 값은 남긴다
#: (`universe.FUTURES_TOKENS` 주석의 `K200_FUT`와 같은 취급). `SPECS`에 넣지 않는 것이
#: 의도다 — 넣으면 유니버스 정합성 검사가 이 둘을 거래 대상으로 착각한다.
REFERENCE_SPECS: Final[dict[str, ContractSpec]] = {
    "K200_FUT": ContractSpec(
        token="K200_FUT",
        name="코스피200선물(정규)",
        multiplier_won=250_000,
        bands=(_band(None, "0.05", 12_500),),
    ),
    "K200_MINI_OPT": ContractSpec(
        token="K200_MINI_OPT",
        name="미니 코스피200옵션",
        multiplier_won=50_000,
        bands=(_band("3", "0.01", 500), _band("10", "0.02", 1_000), _band(None, "0.05", 2_500)),
    ),
}


class UnknownContractError(KeyError):
    """명세 없는 토큰으로 손익을 환산하려 했다 — 조용히 기본값을 쓰면 그 손익이 거짓이 된다."""


def spec_for(token: str) -> ContractSpec:
    """토큰의 계약 명세. 없으면 **예외**다.

    `universe.validate()`가 "소비자 없는 토큰"을 막는 것과 같은 규율의 반대편이다 —
    이쪽은 "명세 없는 토큰"을 막는다. 유니버스에 토큰이 하나 늘었는데 여기를 안 채우면
    손익 환산이 조용히 틀리는 대신 기동이 깨진다.
    """
    spec = SPECS.get(token) or REFERENCE_SPECS.get(token)
    if spec is None:
        raise UnknownContractError(
            f"계약 명세가 없는 토큰: {token} — 사용 가능: {sorted(SPECS)} "
            f"(참고용: {sorted(REFERENCE_SPECS)})"
        )
    return spec
