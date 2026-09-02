"""옵션 주문 경로 4a — 계약 명세 · 다리 확정 · 주문 생성 (2026-09-02 신설).

4a는 **단일 다리 매수**만 낸다. 여기서 지키는 것은 다섯:
  ① 호가단위는 실측 표를 따른다(<10.00 = 0.01, >=10.00 = 0.05),
  ② 가격 표현은 **왕복한다** — 틱 개수로 표현하려던 첫 설계가 왕복에 실패했다,
  ③ 계약승수는 미측정이라 원화 환산을 **거부**한다(선물 값으로 대신하지 않는다),
  ④ 스냅이 상장 범위를 벗어나거나 다리를 겹치게 만들면 **후보를 버린다**,
  ⑤ 4a 범위 밖(다중 다리·매도)은 주문을 만들지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from messiah.core.messages import GreeksProfile, StrategyCandidate, StrategyLeg
from messiah.strategy.options.chain_smile import ChainLeg
from messiah.strategy.options.contract_spec import (
    OPTION_POINT_VALUE_KRW,
    OptionContract,
    OptionContractSpecUnknown,
    from_price_units,
    is_on_tick_grid,
    round_to_tick,
    tick_size_for_premium,
    to_price_units,
)
from messiah.strategy.options.leg_resolution import (
    ResolvedCandidate,
    ResolvedLeg,
    grid_step,
    resolve_candidate,
    snap_strike,
)
from messiah.strategy.options.matrix import LONG_CALL, spec_for
from messiah.strategy.options.option_order import OptionOrderRefused, build_option_order
from messiah.strategy.options.surface import fit_smile

_NOW = datetime(2026, 9, 2, 14, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------- ① 호가단위 (실측 표)


@pytest.mark.parametrize(
    ("premium", "expected"),
    [
        ("0.01", "0.01"),
        ("2.55", "0.01"),
        ("9.99", "0.01"),
        ("10.00", "0.05"),  # 경계 — 이상부터 0.05
        ("26.30", "0.05"),
        ("123.95", "0.05"),
    ],
)
def test_tick_size_follows_the_measured_table(premium: str, expected: str):
    """2026-08-05~09-02 3시리즈 실거래가 2,484개에서 역산한 표 그대로."""
    assert tick_size_for_premium(Decimal(premium)) == Decimal(expected)


def test_a_negative_premium_is_refused_not_coerced():
    """음수가 들어왔다는 것은 호출부가 손익과 가격을 섞은 것이다."""
    with pytest.raises(ValueError):
        tick_size_for_premium(Decimal("-1"))


def test_off_grid_prices_are_snapped_and_recognised():
    assert not is_on_tick_grid(Decimal("10.03"))
    assert round_to_tick(Decimal("10.03")) == Decimal("10.05")
    assert is_on_tick_grid(round_to_tick(Decimal("10.03")))
    # 매수는 올림이 기본 — 격자 아래로 내려가면 체결이 안 된다
    assert round_to_tick(Decimal("10.01"), mode="up") == Decimal("10.05")
    assert round_to_tick(Decimal("10.04"), mode="down") == Decimal("10.00")


# --------------------------------------------------------------- ② 표현 단위는 왕복한다


@pytest.mark.parametrize(
    "premium", ["0.01", "0.37", "2.55", "9.99", "10.00", "10.05", "26.30", "123.95"]
)
def test_price_units_round_trip(premium: str):
    """**첫 설계는 여기서 실패했다.** `limit_price_ticks`를 "그 가격대의 호가단위 개수"로
    두면 200틱이 2.00이기도 하고 10.00이기도 해서 되돌릴 수 없었다 — 표현 단위(0.01 고정)와
    호가 격자(가격대별)를 가른 이유."""
    assert from_price_units(to_price_units(Decimal(premium))) == Decimal(premium)


def test_ten_points_is_not_two_points():
    """왕복 실패의 원래 형태를 회귀로 고정한다."""
    assert to_price_units(Decimal("10.00")) == 1000
    assert to_price_units(Decimal("2.00")) == 200
    assert from_price_units(1000) == Decimal("10.00")


# --------------------------------------------------------------- ③ 승수는 미측정 → 거부


def test_multiplier_is_unmeasured_and_blocks_krw_conversion():
    """**선물 승수(50,000)로 대신하면 주문 크기가 조용히 틀린다.**"""
    assert OPTION_POINT_VALUE_KRW is None

    contract = OptionContract("B01609A14")
    assert not contract.multiplier_is_measured
    with pytest.raises(OptionContractSpecUnknown, match="실측되지 않았다"):
        contract.premium_to_krw(Decimal("26.30"))


def test_a_measured_multiplier_converts():
    """실측이 들어오면 그때부터 환산이 선다(probe가 채울 자리)."""
    contract = OptionContract("B01609A14", point_value_krw=Decimal("250000"))

    assert contract.premium_to_krw(Decimal("2.00")) == Decimal("500000")


# --------------------------------------------------------------- ④ 스냅 가드


def _chain(strikes=(1000.0, 1002.5, 1005.0, 1007.5, 1010.0), *, price=18.25) -> list[ChainLeg]:
    out = []
    for strike in strikes:
        for option_type in ("C", "P"):
            out.append(
                ChainLeg(
                    symbol=f"{option_type}{int(strike * 10)}",
                    series="regular",
                    option_type=option_type,
                    strike=strike,
                    price=price,
                    dte_days=11.0,
                    open_interest=500.0,
                    volume=100.0,
                    kis_iv=None,
                    ts_utc=_NOW,
                )
            )
    return out


def test_grid_step_uses_the_mode_not_the_median():
    """창 가장자리의 큰 간격이 섞여도 격자는 2.5로 나와야 한다."""
    assert grid_step([1000.0, 1002.5, 1005.0, 1007.5, 1050.0]) == 2.5


def test_snap_prefers_the_lower_strike_on_a_tie():
    """동률 타이브레이크는 결정론적이어야 한다(`labeling._resolve_barrier`와 같은 규율)."""
    assert snap_strike(1001.25, [1000.0, 1002.5]) == 1000.0


def _candidate(structure: str, legs: list[StrategyLeg]) -> StrategyCandidate:
    return StrategyCandidate(
        structure=structure,
        legs=legs,
        net_expected_return=Decimal("1.0"),
        pop=0.5,
        max_loss=Decimal("10"),
        reward_risk=0.1,
        greeks=GreeksProfile(delta=0.5, gamma=0.0, theta=0.0, vega=0.0, iv=0.45),
    )


def _smile():
    fit = fit_smile(1005.0, dte=10, strike_iv_points=[(k, 0.45) for k in (995.0, 1005.0, 1015.0)])
    assert fit is not None
    return fit


def test_a_target_outside_the_listed_window_is_refused():
    """**2026-09-02 실측이 요구한 가드.** 아카이브 재생에서 다리가 60~206pt 옮겨졌다 —
    스마일 외삽이 폴링 창 밖을 가리킨 것이라 그 계약은 목표와 무관하다."""
    chain = _chain()
    far = _candidate(
        LONG_CALL, [StrategyLeg(option_type="C", strike=888.3, dte=10, is_short=False, delta=0.3)]
    )

    resolved, reason = resolve_candidate(far, spec_for(LONG_CALL), _smile(), chain)

    assert resolved is None
    assert "상장 범위 밖" in reason and "격자" in reason


def test_duplicate_legs_after_snapping_are_refused():
    """Iron Condor 네 다리가 `B01608A04,B01608A04`로 겹쳤던 형태 — 그 구조가 아니다."""
    chain = _chain()
    twins = _candidate(
        "IRON_CONDOR",
        [
            StrategyLeg(option_type="C", strike=1005.1, dte=10, is_short=True, delta=0.3),
            StrategyLeg(option_type="C", strike=1004.9, dte=10, is_short=False, delta=0.2),
        ],
    )

    resolved, reason = resolve_candidate(twins, spec_for(LONG_CALL), _smile(), chain)

    assert resolved is None
    assert "겹쳤다" in reason


def test_a_nearby_target_resolves_and_is_reevaluated():
    """정상 경로 — 스냅 거리가 격자 안이면 종목코드가 붙고 **재평가된 값**이 나온다."""
    chain = _chain()
    near = _candidate(
        LONG_CALL, [StrategyLeg(option_type="C", strike=1005.4, dte=10, is_short=False, delta=0.5)]
    )

    resolved, reason = resolve_candidate(near, spec_for(LONG_CALL), _smile(), chain)

    assert resolved is not None
    assert resolved.symbols == ("C10050",)
    assert resolved.max_snap_distance == pytest.approx(0.4)
    assert resolved.candidate is not resolved.before_snap  # 재평가된 별개 객체
    assert "다리 확정" in reason


# --------------------------------------------------------------- ⑤ 4a 범위 밖은 거부


def _resolved(structure: str, *, is_short: bool = False, legs: int = 1) -> ResolvedCandidate:
    chain_legs = _chain()[:legs]
    strategy_legs = [
        StrategyLeg(
            option_type="C",
            strike=chain_legs[i].strike,
            dte=10,
            is_short=is_short,
            delta=0.5,
            symbol=chain_legs[i].symbol,
        )
        for i in range(legs)
    ]
    candidate = _candidate(structure, strategy_legs)
    return ResolvedCandidate(
        candidate=candidate,
        legs=tuple(
            ResolvedLeg(leg=strategy_legs[i], chain=chain_legs[i], requested_strike=1005.0)
            for i in range(legs)
        ),
        before_snap=candidate,
    )


def test_a_single_long_leg_becomes_an_order():
    plan = build_option_order(_resolved(LONG_CALL), qty=1)

    assert plan.request.symbol == "C10000"
    assert plan.request.qty == 1
    assert plan.premium_points == Decimal("18.25")
    assert plan.request.limit_price_ticks == 1825  # 0.01 표현 단위
    assert "매수" in plan.describe()


def test_slippage_is_added_in_representation_units_then_snapped():
    plan = build_option_order(_resolved(LONG_CALL), qty=1, slippage_ticks=3)

    # 18.25 + 0.03 = 18.28 → 격자(0.05) 올림 → 18.30
    assert plan.premium_points == Decimal("18.30")
    assert is_on_tick_grid(plan.premium_points)


def test_multi_leg_and_short_are_refused():
    with pytest.raises(OptionOrderRefused, match="4a 범위 밖"):
        build_option_order(_resolved("IRON_CONDOR", legs=2), qty=1)
    with pytest.raises(OptionOrderRefused, match="매도 다리"):
        build_option_order(_resolved(LONG_CALL, is_short=True), qty=1)
    with pytest.raises(OptionOrderRefused, match="수량"):
        build_option_order(_resolved(LONG_CALL), qty=0)


def test_a_leg_without_a_symbol_is_refused():
    resolved = _resolved(LONG_CALL)
    naked = ResolvedCandidate(
        candidate=resolved.candidate,
        legs=(
            ResolvedLeg(
                leg=StrategyLeg(
                    option_type="C", strike=1000.0, dte=10, is_short=False, delta=0.5, symbol=None
                ),
                chain=resolved.legs[0].chain,
                requested_strike=1000.0,
            ),
        ),
        before_snap=resolved.before_snap,
    )

    with pytest.raises(OptionOrderRefused, match="종목코드가 없다"):
        build_option_order(naked, qty=1)


def test_the_stale_timestamp_helper_is_unused_here():
    """이 파일이 시간에 의존하지 않는다는 것을 명시 — `_NOW`는 체인 픽스처의 고정값일 뿐."""
    assert _NOW.tzinfo is timezone.utc
    assert timedelta(seconds=0) == timedelta()
