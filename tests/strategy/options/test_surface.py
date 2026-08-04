"""Vol Engine 프라이서/IV 역산/Surface 피팅 (신규, Ver 2.0 §9 W27~29).

known-value 테스트(교과서 ATM 근사)와, 손으로 옮긴 해석식 Greeks가 프라이서 자기 자신의
유한차분과 일치하는지 교차검증하는 테스트를 함께 둔다(surface.py 모듈 docstring 참고 —
"같은 프라이서로 계산한 값끼리는 최소한 내적 정합성이 보장된다")."""

from __future__ import annotations

import pytest

from messiah.strategy.options.surface import (
    SURFACE_RESIDUAL_WARN_THRESHOLD,
    IVSurface,
    black76_greeks,
    black76_price,
    find_strike_for_delta,
    fit_smile,
    implied_vol,
    is_liquid_quote,
)

# ---------------------------------------------------------------- black76_price


def test_atm_price_matches_textbook_approximation():
    # ATM(F=K=100), r=0, sigma=0.2, t=1.0 → C = F*(N(d1)-N(d2)), d1=0.1, d2=-0.1
    price = black76_price(forward=100.0, strike=100.0, r=0.0, sigma=0.2, t=1.0, option_type="C")
    assert price == pytest.approx(7.9656, abs=0.01)


@pytest.mark.parametrize(
    "forward,strike,r,sigma,t",
    [
        (100.0, 100.0, 0.0, 0.2, 1.0),
        (105.0, 100.0, 0.03, 0.25, 0.5),
        (95.0, 100.0, 0.02, 0.35, 0.1),
    ],
)
def test_put_call_parity_holds(forward, strike, r, sigma, t):
    import math

    call = black76_price(forward=forward, strike=strike, r=r, sigma=sigma, t=t, option_type="C")
    put = black76_price(forward=forward, strike=strike, r=r, sigma=sigma, t=t, option_type="P")
    assert (call - put) == pytest.approx(math.exp(-r * t) * (forward - strike), abs=1e-8)


def test_rejects_non_positive_sigma():
    with pytest.raises(ValueError, match="sigma"):
        black76_price(forward=100.0, strike=100.0, r=0.0, sigma=0.0, t=1.0, option_type="C")


def test_rejects_non_positive_t():
    with pytest.raises(ValueError, match="t"):
        black76_price(forward=100.0, strike=100.0, r=0.0, sigma=0.2, t=0.0, option_type="C")


def test_rejects_invalid_option_type():
    with pytest.raises(ValueError, match="option_type"):
        black76_price(forward=100.0, strike=100.0, r=0.0, sigma=0.2, t=1.0, option_type="X")


# ---------------------------------------------------------------- black76_greeks


@pytest.mark.parametrize("option_type", ["C", "P"])
def test_delta_matches_finite_difference(option_type):
    kwargs = dict(strike=350.0, r=0.03, sigma=0.22, t=0.5, option_type=option_type)
    greeks = black76_greeks(forward=352.0, **kwargs)
    h = 0.01
    price_up = black76_price(forward=352.0 + h, **kwargs)
    price_down = black76_price(forward=352.0 - h, **kwargs)
    numeric_delta = (price_up - price_down) / (2 * h)
    assert greeks.delta == pytest.approx(numeric_delta, abs=1e-3)


def test_gamma_matches_finite_difference_of_delta():
    kwargs = dict(strike=350.0, r=0.03, sigma=0.22, t=0.5, option_type="C")
    h = 0.5
    g_up = black76_greeks(forward=352.0 + h, **kwargs)
    g_down = black76_greeks(forward=352.0 - h, **kwargs)
    numeric_gamma = (g_up.delta - g_down.delta) / (2 * h)
    greeks = black76_greeks(forward=352.0, **kwargs)
    assert greeks.gamma == pytest.approx(numeric_gamma, abs=1e-3)


def test_vega_matches_finite_difference():
    kwargs = dict(forward=352.0, strike=350.0, r=0.03, t=0.5, option_type="C")
    h = 1e-4
    price_up = black76_price(sigma=0.22 + h, **kwargs)
    price_down = black76_price(sigma=0.22 - h, **kwargs)
    numeric_vega_per_unit_sigma = (price_up - price_down) / (2 * h)
    greeks = black76_greeks(sigma=0.22, **kwargs)
    assert greeks.vega == pytest.approx(numeric_vega_per_unit_sigma * 0.01, abs=1e-3)


def test_theta_is_negative_for_atm_option_far_from_expiry():
    greeks = black76_greeks(forward=100.0, strike=100.0, r=0.02, sigma=0.2, t=0.5, option_type="C")
    assert greeks.theta < 0


# ---------------------------------------------------------------- implied_vol


@pytest.mark.parametrize(
    "true_sigma,option_type", [(0.15, "C"), (0.22, "C"), (0.35, "P"), (0.60, "P")]
)
def test_implied_vol_round_trips_through_black76_price(true_sigma, option_type):
    kwargs = dict(forward=350.0, strike=345.0, r=0.03, t=0.25, option_type=option_type)
    price = black76_price(sigma=true_sigma, **kwargs)
    recovered = implied_vol(price=price, **kwargs)
    assert recovered == pytest.approx(true_sigma, abs=1e-4)


def test_implied_vol_returns_none_for_price_outside_no_arbitrage_bounds():
    # 콜 가격이 선물가(F)보다 크면 무차익 상한 위반 — 해가 존재하지 않는다.
    recovered = implied_vol(price=999.0, forward=100.0, strike=100.0, r=0.0, t=0.5, option_type="C")
    assert recovered is None


def test_implied_vol_returns_none_for_non_positive_price():
    recovered = implied_vol(price=0.0, forward=100.0, strike=100.0, r=0.0, t=0.5, option_type="C")
    assert recovered is None


# ---------------------------------------------------------------- is_liquid_quote


def test_liquid_quote_within_spread_threshold():
    assert is_liquid_quote(bid=9.8, ask=10.2, max_spread_pct=20.0) is True


def test_illiquid_quote_beyond_spread_threshold():
    assert is_liquid_quote(bid=5.0, ask=10.0, max_spread_pct=20.0) is False


@pytest.mark.parametrize("bid,ask", [(None, 10.0), (5.0, None), (0.0, 10.0), (10.0, 5.0)])
def test_liquid_quote_rejects_missing_or_crossed_book(bid, ask):
    assert is_liquid_quote(bid, ask) is False


# ---------------------------------------------------------------- fit_smile / IVSurface


def test_fit_smile_recovers_low_residual_for_synthetic_quadratic_smile():
    import math

    forward = 350.0
    a, b, c = 0.05, -0.01, 0.20  # 합성 스마일 계수
    points = []
    for strike in (330.0, 340.0, 350.0, 360.0, 370.0):
        x = math.log(strike / forward)
        iv = a * x * x + b * x + c
        points.append((strike, iv))

    fit = fit_smile(forward, dte=10, strike_iv_points=points)

    assert fit is not None
    assert fit.rms_residual < 1e-6
    assert fit.is_reliable is True
    assert fit.iv_at(forward) == pytest.approx(c, abs=1e-6)


def test_fit_smile_returns_none_with_too_few_points():
    assert fit_smile(350.0, dte=10, strike_iv_points=[(350.0, 0.2), (360.0, 0.22)]) is None


def test_surface_dte_le_1_always_unreliable_even_with_good_fit():
    surface = IVSurface()
    points = [(k, 0.2) for k in (340.0, 345.0, 350.0, 355.0, 360.0)]  # 완벽한 flat smile
    fit = fit_smile(350.0, dte=1, strike_iv_points=points)
    assert fit is not None and fit.rms_residual < SURFACE_RESIDUAL_WARN_THRESHOLD
    surface.add(fit)
    assert surface.is_reliable(1) is False  # Ver 1.3 §3.2 "DTE≤1 별도 취급"


# ---------------------------------------------------------------- find_strike_for_delta


def _flat_smile(forward: float = 350.0, dte: int = 20, iv: float = 0.20):
    points = [(k, iv) for k in (300.0, 325.0, 350.0, 375.0, 400.0)]
    return fit_smile(forward, dte=dte, strike_iv_points=points)


@pytest.mark.parametrize("option_type,target_delta", [("C", 0.25), ("P", -0.25)])
def test_find_strike_for_delta_recovers_matching_delta(option_type, target_delta):
    smile = _flat_smile()
    strike = find_strike_for_delta(
        smile, r=0.03, t=0.3, target_delta=target_delta, option_type=option_type
    )

    assert strike is not None
    greeks = black76_greeks(
        forward=smile.forward, strike=strike, r=0.03, sigma=0.20, t=0.3, option_type=option_type
    )
    assert greeks.delta == pytest.approx(target_delta, abs=1e-3)


def test_find_strike_for_delta_returns_none_when_target_out_of_search_range():
    smile = _flat_smile()
    # 콜의 델타는 [0,1] 안에서만 움직인다 — 1.5는 탐색 구간 안 어디서도 도달 불가.
    assert find_strike_for_delta(smile, r=0.03, t=0.3, target_delta=1.5, option_type="C") is None


def test_surface_atm_iv_and_iv_at_missing_expiry():
    surface = IVSurface()
    points = [(k, 0.18 + 0.0001 * (k - 350.0)) for k in (330.0, 340.0, 350.0, 360.0, 370.0)]
    fit = fit_smile(350.0, dte=10, strike_iv_points=points)
    assert fit is not None
    surface.add(fit)

    assert surface.expiries() == [10]
    assert surface.is_reliable(10) is True
    assert surface.atm_iv(10) == pytest.approx(0.18, abs=1e-3)
    assert surface.iv_at(999, 350.0) is None
