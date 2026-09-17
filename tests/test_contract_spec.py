"""계약 명세 정본 (2026-09-17, F-114 마무리).

이 파일이 지키는 것은 하나다 — **옮겨 적다 틀린 값이 기동 전에 깨진다.**

거래소는 거래승수·호가단위·최소가격변동금액 **셋을 다 공표**하므로
`승수 × 호가단위 = 최소가격변동금액`은 항등식이다. 셋을 다 적어 두고 그 곱을 단언하면
10배 오타(25만을 25,000으로)가 여기서 잡힌다. 승수만 적어 두면 그 오타는 손익이
10분의 1로 나오는 형태로만 드러나고, 그때는 이미 성적표가 틀린 뒤다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import yaml

from messiah.core import universe
from messiah.core.contract_spec import (
    REFERENCE_SPECS,
    SPECS,
    UnknownContractError,
    spec_for,
)

_ALL = {**SPECS, **REFERENCE_SPECS}


# ---------------------------------------------------------------- 자기 검산


@pytest.mark.parametrize("token", sorted(_ALL))
def test_multiplier_times_tick_equals_the_published_minimum_move(token: str):
    """거래소가 공표한 세 값이 서로 맞는가 — 전 상품·전 구간."""
    spec = _ALL[token]
    for band in spec.bands:
        computed = Decimal(spec.multiplier_won) * band.tick_size_points
        assert computed == band.min_price_move_won, (
            f"{spec.name} 구간(<{band.upper_bound_points}): "
            f"{spec.multiplier_won} × {band.tick_size_points} = {computed}인데 "
            f"공표값은 {band.min_price_move_won}원"
        )


def test_the_numbers_are_the_exchange_numbers():
    """2017-03-27 KRX 거래승수 인하 이후 값. **이 테스트가 곧 출처 기록이다** —
    누가 나중에 승수를 바꾸면 여기서 의도를 묻게 된다."""
    assert SPECS[universe.K200_MINI_FUT].multiplier_won == 50_000
    assert REFERENCE_SPECS["K200_FUT"].multiplier_won == 250_000
    assert SPECS[universe.K200_OPT_MONTHLY].multiplier_won == 250_000
    assert SPECS[universe.K200_OPT_WEEKLY_MON].multiplier_won == 250_000
    assert SPECS[universe.K200_OPT_WEEKLY_THU].multiplier_won == 250_000
    assert REFERENCE_SPECS["K200_MINI_OPT"].multiplier_won == 50_000


def test_mini_futures_one_tick_is_one_thousand_won():
    """지금 손익을 계산하는 유일한 상품이다. 0.02pt × 50,000원 = 1,000원."""
    assert SPECS[universe.K200_MINI_FUT].tick_value_won() == 1_000


# ---------------------------------------------------------------- 옵션은 상수가 아니다


def test_an_option_tick_is_not_a_constant():
    """프리미엄 구간마다 호가단위가 다르므로 "옵션 1틱 = N원"이라는 값은 없다.
    가격 없이 물으면 **답하지 않는다** — 한 구간을 임의로 고르면 그게 지어낸 상수다(R4)."""
    spec = SPECS[universe.K200_OPT_MONTHLY]

    with pytest.raises(ValueError, match="가격 없이는"):
        spec.tick_value_won()

    assert spec.tick_value_won(2.50) == 2_500  # 10pt 미만 → 0.01
    assert spec.tick_value_won(12.0) == 12_500  # 10pt 이상 → 0.05


def test_the_band_boundary_is_exclusive_below():
    """거래소 표기가 "10포인트 **미만** 0.01 / 10포인트 **이상** 0.05"이므로 10.0은 위 구간."""
    spec = SPECS[universe.K200_OPT_MONTHLY]

    assert spec.tick_value_won(Decimal("9.99")) == 2_500
    assert spec.tick_value_won(Decimal("10")) == 12_500


def test_mini_options_have_three_bands():
    """미니옵션만 구간이 셋이다(3pt·10pt). 유니버스에는 없지만 값은 남긴다 — 2026-07-22
    실측에서 상장 종목이 0/0이라 뺀 것이지 설계상 배제한 게 아니다(`universe.py`)."""
    spec = REFERENCE_SPECS["K200_MINI_OPT"]

    assert [spec.tick_value_won(p) for p in (1.0, 5.0, 20.0)] == [500, 1_000, 2_500]


# ---------------------------------------------------------------- 정합성


def test_every_traded_token_has_a_spec():
    """유니버스에 토큰이 하나 늘었는데 명세를 안 채우면 **손익 환산이 조용히 틀리는 대신**
    여기서 깨진다 — `universe.validate()`가 「소비자 없는 토큰」을 막는 것의 반대편이다."""
    assert set(SPECS) == set(universe.KNOWN_TOKENS)


def test_reference_specs_are_not_mistaken_for_traded_products():
    """정규선물·미니옵션은 거래 대상이 아니다. `SPECS`에 섞으면 위 검사가 그 둘을
    유니버스로 착각한다."""
    assert set(REFERENCE_SPECS).isdisjoint(SPECS)
    assert spec_for("K200_FUT").name == "코스피200선물(정규)"


def test_the_configured_tick_size_matches_the_traded_contract():
    """`configs/instance.yaml`의 `futures_tick_size`와 명세표가 갈리면 손익 환산과 호가
    계산이 서로 다른 틱을 쓰게 된다 — `universe.py`가 "정합성은 테스트가 지킨다"로 세운
    규율을 여기에도 건다."""
    cfg = yaml.safe_load(open("configs/instance.yaml", encoding="utf-8"))

    configured = Decimal(str(cfg["futures_tick_size"]))
    assert configured == SPECS[universe.K200_MINI_FUT].bands[0].tick_size_points


def test_an_unknown_token_raises_instead_of_defaulting():
    """조용히 기본값을 쓰면 그 손익이 거짓이 된다."""
    with pytest.raises(UnknownContractError):
        spec_for("K200_SOMETHING_NEW")
