"""Vol Engine — Black-76 프라이싱·내재변동성 역산·IV Surface 피팅 (Ver 1.3 §3, Ver 2.0 §9
W27~29).

## 왜 Black-Scholes(현물)가 아니라 Black-76(선물)인가

KOSPI200 옵션은 지수 자체가 아니라 지수선물(A05608 등, 이미 실시간 수집 중)을 기준으로
시장에서 프라이싱되는 관행이 일반적이고, 이 프로젝트엔 애초에 현물지수 실시간 피드가 없다
(`Docs/capability_matrix.md` "RG 현물지수·매크로 데이터 소스 미착수" — 별도 착수 필요한
갭). Black-76은 배당수익률 가정 없이 선물가 하나로 프라이싱하므로 이미 있는 데이터만으로
성립한다 — 없는 데이터(현물지수)를 있다고 가정하는 대신, 있는 데이터에 맞는 모델을 골랐다.

## 왜 KIS의 원시 Greeks/IV 필드를 안 쓰는가

`core/messages.py`의 `OptionQuoteSnapshot`/`GreeksProfile` docstring 참고 — 마흐디 L16
(단위를 확인 안 하고 스키마부터 정해 5일간 데이터가 조용히 잘린 사고) 재발 방지. 이
모듈에서 계산한 IV·Greeks만 신뢰한다 — 단위가 처음부터 이 프로젝트가 정의한 값이기 때문.

## theta는 유한차분으로 계산한다

델타·감마·베가는 Black-76 표준 해석식을 쓰지만, theta는 손으로 옮겨 적은 공식의 부호 실수
위험을 피하려 `black76_price()` 자기 자신을 하루(1/365년) 앞당겨 재평가하는 유한차분으로
계산한다 — 별도 공식을 못 미더워하는 게 아니라, "같은 프라이서로 계산한 값끼리는 최소한
내적 정합성이 보장된다"는 게 더 강한 보장이라서다(`tests/strategy/options/test_surface.py`의
델타 유한차분 교차검증과 같은 철학).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from messiah.core.messages import GreeksProfile

_SQRT_2PI = math.sqrt(2.0 * math.pi)
_MIN_T = 1.0 / 365.0  # DTE=0 근방 수치불안정 방지 하한 (Ver 1.3 §3.2 "DTE≤1 별도 취급")
_THETA_DT = 1.0 / 365.0  # theta 유한차분 스텝 (1일)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def black76_price(
    *, forward: float, strike: float, r: float, sigma: float, t: float, option_type: str
) -> float:
    """
    입력: forward(선물가), strike(행사가), r(연 무위험이자율), sigma(연율화 변동성),
         t(연 단위 잔존만기), option_type("C"|"P").
    실패 조건: sigma<=0 또는 t<=0이면 ValueError — 만기 임박·변동성 0은 호출측이 §3.2
              규칙(DTE≤1 별도 취급)으로 걸러야 할 대상이지 이 함수가 근사할 대상이 아니다.
    """
    if sigma <= 0:
        raise ValueError("sigma는 0보다 커야 함")
    if t <= 0:
        raise ValueError("t는 0보다 커야 함")
    if option_type not in ("C", "P"):
        raise ValueError("option_type은 'C' 또는 'P'여야 함")

    sqrt_t = math.sqrt(t)
    d1 = (math.log(forward / strike) + 0.5 * sigma * sigma * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    discount = math.exp(-r * t)
    if option_type == "C":
        return discount * (forward * _norm_cdf(d1) - strike * _norm_cdf(d2))
    return discount * (strike * _norm_cdf(-d2) - forward * _norm_cdf(-d1))


def black76_greeks(
    *, forward: float, strike: float, r: float, sigma: float, t: float, option_type: str
) -> GreeksProfile:
    """`GreeksProfile` 단위 계약(core/messages.py) 그대로: delta는 무차원, gamma는 pt^-1,
    theta는 하루 경과당 pt(음수 통상), vega는 IV 1%p(0.01)당 pt."""
    t = max(t, _MIN_T)
    sqrt_t = math.sqrt(t)
    d1 = (math.log(forward / strike) + 0.5 * sigma * sigma * t) / (sigma * sqrt_t)
    discount = math.exp(-r * t)

    if option_type == "C":
        delta = discount * _norm_cdf(d1)
    else:
        delta = -discount * _norm_cdf(-d1)
    gamma = discount * _norm_pdf(d1) / (forward * sigma * sqrt_t)
    vega_per_unit_sigma = forward * discount * _norm_pdf(d1) * sqrt_t
    vega = vega_per_unit_sigma * 0.01  # GreeksProfile 계약: IV 1%p(0.01)당 pt

    t_minus = max(t - _THETA_DT, _MIN_T / 2)

    def _price_at(t_value: float) -> float:
        return black76_price(
            forward=forward, strike=strike, r=r, sigma=sigma, t=t_value, option_type=option_type
        )

    theta = _price_at(t_minus) - _price_at(t)  # 하루 경과 후 가치 - 지금 가치 (통상 음수)

    return GreeksProfile(delta=delta, gamma=gamma, theta=theta, vega=vega, iv=sigma)


def implied_vol(
    *,
    price: float,
    forward: float,
    strike: float,
    r: float,
    t: float,
    option_type: str,
    initial_guess: float = 0.2,
    tol: float = 1e-6,
    max_iter: int = 50,
) -> float | None:
    """Newton-Raphson(베가 기반) 우선, 실패 시 이분법 폴백([1e-4, 5.0] 구간).
    반환: 수렴한 sigma, 또는 무차익 구간 밖 가격 등으로 못 찾으면 None(호출측이 해당 다리를
         Surface 피팅에서 제외해야 한다는 신호 — Ver 1.3 §3.2 "피팅 잔차 급증" 계열 방어와
         같은 성격)."""
    if price <= 0 or t <= 0:
        return None

    def _price_at(sigma_value: float) -> float:
        return black76_price(
            forward=forward, strike=strike, r=r, sigma=sigma_value, t=t, option_type=option_type
        )

    def _greeks_at(sigma_value: float) -> GreeksProfile:
        return black76_greeks(
            forward=forward, strike=strike, r=r, sigma=sigma_value, t=t, option_type=option_type
        )

    sigma = initial_guess
    for _ in range(max_iter):
        try:
            model_price = _price_at(sigma)
        except ValueError:
            break
        diff = model_price - price
        if abs(diff) < tol:
            return sigma
        greeks = _greeks_at(sigma)
        vega_per_unit_sigma = greeks.vega / 0.01
        if vega_per_unit_sigma < 1e-8:
            break  # 뉴턴 발산 위험 — 이분법으로 폴백
        sigma -= diff / vega_per_unit_sigma
        if sigma <= 0:
            break

    return _implied_vol_bisection(
        price=price, forward=forward, strike=strike, r=r, t=t, option_type=option_type, tol=tol
    )


def _implied_vol_bisection(
    *, price: float, forward: float, strike: float, r: float, t: float, option_type: str, tol: float
) -> float | None:
    def _price_at(sigma_value: float) -> float:
        return black76_price(
            forward=forward, strike=strike, r=r, sigma=sigma_value, t=t, option_type=option_type
        )

    lo, hi = 1e-4, 5.0
    price_lo = _price_at(lo)
    price_hi = _price_at(hi)
    if not (price_lo - price) * (price_hi - price) <= 0:
        return None  # 무차익 가격 구간 밖 — 해가 없음(예: 가격이 내재가치보다도 작음)

    for _ in range(100):
        mid = (lo + hi) / 2.0
        price_mid = _price_at(mid)
        if abs(price_mid - price) < tol:
            return mid
        if (price_lo - price) * (price_mid - price) <= 0:
            hi = mid
        else:
            lo, price_lo = mid, price_mid
    return (lo + hi) / 2.0


# ---------------------------------------------------------------- 유동성 필터 (Ver 1.3 §3.2)


def is_liquid_quote(bid: float | None, ask: float | None, *, max_spread_pct: float = 20.0) -> bool:
    """호가 스프레드가 mid 대비 max_spread_pct%를 넘으면(또는 호가 자체가 없으면) 비유동으로
    판정 — Surface 피팅에서 제외 대상(§3.2 "호가 스프레드가 임계 초과인 행사가는 제외")."""
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return False
    mid = (bid + ask) / 2.0
    spread_pct = (ask - bid) / mid * 100.0
    return spread_pct <= max_spread_pct


# ---------------------------------------------------------------- IV Surface 피팅 (Ver 1.3 §3.1)

SURFACE_RESIDUAL_WARN_THRESHOLD = 0.05  # IV 포인트 기준 RMS 잔차 — 초과 시 "피팅 신뢰불가"


@dataclass(frozen=True)
class SmileFit:
    """만기 1개(DTE)의 스마일 피팅 결과 — 2차 다항(log-moneyness 기준)으로 SVI/스플라인을
    단순화(Ver 1.3 §10 문서의 SVI 대신, 이 구현의 명시적 선택 — 모듈 docstring 참고)."""

    dte: int
    forward: float
    coeffs: tuple[float, float, float]  # a,b,c of a*x^2+b*x+c, x=log(strike/forward)
    rms_residual: float
    n_points: int

    def iv_at(self, strike: float) -> float:
        x = math.log(strike / self.forward)
        a, b, c = self.coeffs
        return a * x * x + b * x + c

    @property
    def is_reliable(self) -> bool:
        return self.rms_residual <= SURFACE_RESIDUAL_WARN_THRESHOLD


def fit_smile(
    forward: float, dte: int, strike_iv_points: list[tuple[float, float]]
) -> SmileFit | None:
    """
    입력: forward(해당 만기의 선물가 근사 — 근월물 선물가를 그대로 씀), dte, (strike, iv) 목록
         (이미 `is_liquid_quote()`로 걸러진 점들이어야 함, 호출측 책임).
    실패 조건: 점이 3개 미만이면 2차 다항 피팅 자체가 과적합이라 None.
    """
    if len(strike_iv_points) < 3:
        return None
    xs = np.array([math.log(k / forward) for k, _ in strike_iv_points])
    ys = np.array([iv for _, iv in strike_iv_points])
    coeffs = np.polyfit(xs, ys, 2)
    fitted = np.polyval(coeffs, xs)
    rms_residual = float(np.sqrt(np.mean((ys - fitted) ** 2)))
    a, b, c = (float(v) for v in coeffs)
    return SmileFit(
        dte=dte,
        forward=forward,
        coeffs=(a, b, c),
        rms_residual=rms_residual,
        n_points=len(strike_iv_points),
    )


class IVSurface:
    """만기(DTE)별 `SmileFit` 모음. Ver 1.3 §3.2 "DTE≤1은 별도 취급"에 따라 DTE<=1인
    만기는 등록해도 `is_reliable`이 항상 False로 취급된다(호출측이 진입 판단에서 배제)."""

    _DTE_UNSTABLE_MAX = 1

    def __init__(self) -> None:
        self._smiles: dict[int, SmileFit] = {}

    def add(self, fit: SmileFit) -> None:
        self._smiles[fit.dte] = fit

    def expiries(self) -> list[int]:
        return sorted(self._smiles)

    def is_reliable(self, dte: int) -> bool:
        fit = self._smiles.get(dte)
        if fit is None:
            return False
        if dte <= self._DTE_UNSTABLE_MAX:
            return False
        return fit.is_reliable

    def iv_at(self, dte: int, strike: float) -> float | None:
        fit = self._smiles.get(dte)
        if fit is None:
            return None
        return fit.iv_at(strike)

    def atm_iv(self, dte: int) -> float | None:
        fit = self._smiles.get(dte)
        if fit is None:
            return None
        return fit.iv_at(fit.forward)


def find_strike_for_delta(
    smile: SmileFit,
    *,
    r: float,
    t: float,
    target_delta: float,
    option_type: str,
    strike_lo_mult: float = 0.5,
    strike_hi_mult: float = 1.5,
    tol: float = 1e-4,
    max_iter: int = 100,
) -> float | None:
    """스마일 위에서 델타가 `target_delta`(예: 콜은 +0.25, 풋은 -0.25)에 가장 가까운 행사가를
    찾는다 — `vol_metrics.skew()`의 "25Δ 풋/콜 IV" 입력 재료(Ver 1.3 §3.1). 델타가 strike에
    대해 단조(콜: 1→0 감소, 풋: 0→-1 감소)라는 성질을 이용한 이분법.
    실패 조건: 탐색 구간(forward × [strike_lo_mult, strike_hi_mult]) 안에 해당 델타가 없으면
              None(예: 스마일이 너무 평평하거나 극단적인 target_delta)."""
    forward = smile.forward
    t = max(t, _MIN_T)

    def delta_at(strike: float) -> float:
        sigma = max(smile.iv_at(strike), 1e-4)
        return black76_greeks(
            forward=forward, strike=strike, r=r, sigma=sigma, t=t, option_type=option_type
        ).delta

    lo, hi = forward * strike_lo_mult, forward * strike_hi_mult
    delta_lo, delta_hi = delta_at(lo), delta_at(hi)
    if (delta_lo - target_delta) * (delta_hi - target_delta) > 0:
        return None

    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        delta_mid = delta_at(mid)
        if abs(delta_mid - target_delta) < tol:
            return mid
        if (delta_lo - target_delta) * (delta_mid - target_delta) <= 0:
            hi = mid
        else:
            lo, delta_lo = mid, delta_mid
    return (lo + hi) / 2.0
