"""Strategy Evaluator — 후보 다리 구성·시나리오 그리드 손익·순위화 (Ver 1.3 §5, Ver 2.0 §9
W30~31).

`matrix.CandidateSpec`(구조 이름 + 델타/DTE 파라미터)를 받아 `surface.SmileFit`(피팅된
스마일) 위에서 실제 행사가를 찾고(`find_strike_for_delta`), 시나리오 그리드로 확률가중
기대손익을 계산해 `StrategyCandidate`를 만든다.

## 단위는 지수 포인트 — KRW 아님

`GreeksProfile`과 동일 계약: `net_expected_return`·`max_loss`는 지수 포인트다. 옵션 승수
(계약당 몇 원인지)는 이 프로젝트가 아직 실측하지 않았다(`risk/sizer.py`의 선물 승수
`point_value_krw`와 같은 성격의 갭 — 옵션은 그 필드조차 아직 없다) — 포인트로 남겨 소비측이
승수를 곱하게 한다.

## Max Loss는 그리드가 아니라 구조에서 직접 계산한다

Ver 1.3 §5.1 표가 Max Loss를 "구조상 최대손실"이라 부르는 그대로 — 그리드(±3σ)가 극단을
다 못 담을 수도 있는 확률적 근사인 반면, 정의된 스프레드의 최대손실은 (폭 − 순수취) 또는
(순지불액) 같은 닫힌 형태로 항상 정확히 구해진다. §6-1(네이키드 금지)를 만족하는 구조만
`matrix.py`가 내놓으므로 이 구현의 `max_loss`는 항상 유한하다.

## 시간축은 단일 시점(“평가 시계”)이다, 3차원 그리드가 아니다

Ver 1.3 §5.1 "가격 × IV × 시간 경과"의 시간 축을 별도 그리드 차원으로 두지 않고
`EvaluatorConfig.evaluation_horizon_days`(기본 5거래일) 뒤 단일 시점으로 고정했다 — 가격×IV
2차원(21×7=147점)에 시간까지 3차원으로 늘리면 계산량이 커지는 데 비해, "진입 후 며칠 뒤
재평가"라는 질문 자체가 단일 호라이즌으로도 충분히 답이 된다는 판단(초기값, Walk-Forward
재추정 대상 — Ver 1.3 §9와 같은 성격의 캘리브레이션 갭).

## `spec.dte_low`가 곧 채택 DTE다 — 스마일의 실제 만기와 다를 수 있다 (알려진 갭)

`build_legs()`는 다리의 `dte`를 `spec.dte_low`(매트릭스가 제안한 목표 DTE 범위의 하한)로
그대로 채택한다 — 넘겨받은 `smile`이 실제로 그 DTE에 피팅된 것인지는 검사하지 않는다.
지금은 만기별로 여러 `SmileFit`을 동시에 들고 그중 목표 DTE에 가장 가까운 것을 고르는
로직이 없어서다(`service.py`가 아직 만기 1개짜리 `SmileFit`만 다룬다, W32~34 이전 스코프).
여러 만기 동시 취급(예: `CALENDAR` 구조의 정상 지원)은 다음 단계 갭."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from messiah.core.messages import GreeksProfile, StrategyCandidate, StrategyLeg
from messiah.strategy.options import matrix
from messiah.strategy.options.matrix import CandidateSpec
from messiah.strategy.options.surface import (
    SmileFit,
    black76_greeks,
    black76_price,
    find_strike_for_delta,
)

_MIN_T = 1.0 / 365.0


@dataclass(frozen=True)
class EvaluatorConfig:
    price_grid_points: int = 21  # Ver 1.3 §5.1 "가격 변화(±3σ, 21포인트)"
    price_sigma_range: float = 3.0
    iv_grid_points: int = 7  # Ver 1.3 §5.1 "IV 변화(−30%~+30%, 7포인트)"
    iv_change_range_pct: float = 0.30
    iv_change_std_pct: float = 0.10  # IV 변화 확률가중의 정규분포 표준편차(초기값)
    evaluation_horizon_days: float = 5.0  # "시간 경과" — 단일 재평가 시점(모듈 docstring)
    direction_drift_scale: float = 1.0  # FuturesView.score → 가격그리드 드리프트(z, σ단위) 변환


# ---------------------------------------------------------------- 다리 구성


def _mid(delta_range: tuple[float, float] | None) -> float | None:
    if delta_range is None:
        return None
    return (delta_range[0] + delta_range[1]) / 2.0


def _leg_templates(spec: CandidateSpec) -> list[tuple[str, bool, float]]:
    """구조별 (option_type, is_short, 부호 포함 목표델타) 목록. 콜은 델타 양수, 풋은 음수 —
    `matrix.py` 모듈 docstring "Ver 1.3 §4.2 델타 배정도 신용 스프레드에는 문자 그대로 못
    쓴다"의 결론(신용 스프레드는 매도=근접등가격, 매수=날개)이 이미 `spec.short_leg_delta_range`/
    `long_leg_delta_range`에 반영돼 있으므로 여기서는 구조별 다리 역할만 배정한다."""
    s = spec.structure
    long_d = _mid(spec.long_leg_delta_range)
    short_d = _mid(spec.short_leg_delta_range)

    if s == matrix.LONG_CALL and long_d is not None:
        return [("C", False, long_d)]
    if s == matrix.LONG_PUT and long_d is not None:
        return [("P", False, -long_d)]
    if s == matrix.BULL_CALL_SPREAD and long_d is not None and short_d is not None:
        return [("C", False, long_d), ("C", True, short_d)]
    if s == matrix.BULL_PUT_SPREAD and long_d is not None and short_d is not None:
        return [("P", True, -short_d), ("P", False, -long_d)]
    if s == matrix.BEAR_PUT_SPREAD and long_d is not None and short_d is not None:
        return [("P", False, -long_d), ("P", True, -short_d)]
    if s == matrix.BEAR_CALL_SPREAD and long_d is not None and short_d is not None:
        return [("C", True, short_d), ("C", False, long_d)]
    if s == matrix.IRON_CONDOR and long_d is not None and short_d is not None:
        return [
            ("P", True, -short_d),
            ("P", False, -long_d),
            ("C", True, short_d),
            ("C", False, long_d),
        ]
    return []  # CALENDAR 등 미지원 구조 — matrix.py 모듈 docstring의 알려진 갭


def build_legs(spec: CandidateSpec, smile: SmileFit, *, r: float) -> list[StrategyLeg] | None:
    """실패 조건: 구조가 다리 템플릿을 못 찾거나(CALENDAR 등), 어느 한 다리라도
    `find_strike_for_delta()`가 해당 델타에 맞는 행사가를 못 찾으면(스마일이 그 델타에
    안 닿음) None — 호출측이 이 후보를 건너뛰어야 한다는 신호."""
    templates = _leg_templates(spec)
    if not templates:
        return None

    legs: list[StrategyLeg] = []
    for option_type, is_short, target_delta in templates:
        strike = find_strike_for_delta(
            smile, r=r, t=spec.dte_low / 365.0, target_delta=target_delta, option_type=option_type
        )
        if strike is None:
            return None
        legs.append(
            StrategyLeg(
                option_type=option_type,
                strike=strike,
                dte=spec.dte_low,
                is_short=is_short,
                delta=target_delta,
            )
        )
    return legs


# ---------------------------------------------------------------- 시나리오 그리드


def _linspace(lo: float, hi: float, n: int) -> list[float]:
    if n == 1:
        return [0.0]
    step = (hi - lo) / (n - 1)
    return [lo + i * step for i in range(n)]


def _normal_weights(points: list[float], *, mean: float, std: float) -> list[float]:
    """정규분포 확률가중(합=1). std<=0이면 mean에 가장 가까운 점에 전량 배정(퇴화 케이스
    방어 — 0 나눗셈 대신 결정론적 극한 취급)."""
    if std <= 0:
        idx = min(range(len(points)), key=lambda i: abs(points[i] - mean))
        return [1.0 if i == idx else 0.0 for i in range(len(points))]
    raw = [math.exp(-0.5 * ((p - mean) / std) ** 2) for p in points]
    total = sum(raw)
    return [v / total for v in raw]


def _leg_value(
    *, forward: float, strike: float, r: float, sigma: float, t: float, option_type: str
) -> float:
    """만기(t<=최소 임계)에 근접하면 내재가치로, 아니면 Black-76 이론가로. 그리드의 시간
    경과 시점(§ 모듈 docstring)이 다리의 잔존만기보다 길어지는 경우(짧은 DTE 다리)를 대비한
    방어 경로다."""
    if t <= _MIN_T / 2:
        if option_type == "C":
            return max(forward - strike, 0.0)
        return max(strike - forward, 0.0)
    return black76_price(
        forward=forward, strike=strike, r=r, sigma=sigma, t=t, option_type=option_type
    )


# ---------------------------------------------------------------- 평가


def evaluate_candidate(
    spec: CandidateSpec,
    smile: SmileFit,
    *,
    r: float,
    score: float,
    entry_cost_points: Decimal = Decimal("0"),
    config: EvaluatorConfig = EvaluatorConfig(),
    rationale: dict[str, object] | None = None,
) -> StrategyCandidate | None:
    """실패 조건: `build_legs()`가 None이면 이 함수도 None(후보 자체가 성립 안 함)."""
    legs = build_legs(spec, smile, r=r)
    if legs is None:
        return None

    entry_ivs = [max(smile.iv_at(leg.strike), 1e-4) for leg in legs]
    entry_prices = [
        black76_price(
            forward=smile.forward,
            strike=leg.strike,
            r=r,
            sigma=iv,
            t=leg.dte / 365.0,
            option_type=leg.option_type,
        )
        for leg, iv in zip(legs, entry_ivs)
    ]

    horizon_t = config.evaluation_horizon_days / 365.0
    atm_iv = max(smile.iv_at(smile.forward), 1e-4)
    price_sigma_abs = atm_iv * smile.forward * math.sqrt(max(horizon_t, 1e-9))

    price_zs = _linspace(
        -config.price_sigma_range, config.price_sigma_range, config.price_grid_points
    )
    iv_pcts = _linspace(
        -config.iv_change_range_pct, config.iv_change_range_pct, config.iv_grid_points
    )
    raw_drift_z = score * config.direction_drift_scale
    drift_z = max(-config.price_sigma_range, min(config.price_sigma_range, raw_drift_z))
    price_weights = _normal_weights(price_zs, mean=drift_z, std=1.0)
    iv_weights = _normal_weights(iv_pcts, mean=0.0, std=config.iv_change_std_pct)

    weighted_payoff = 0.0
    prob_profit = 0.0
    for price_z, price_w in zip(price_zs, price_weights):
        forward_scenario = smile.forward + price_z * price_sigma_abs
        for iv_pct, iv_w in zip(iv_pcts, iv_weights):
            joint_w = price_w * iv_w
            payoff = 0.0
            for leg, entry_price, entry_iv in zip(legs, entry_prices, entry_ivs):
                t_scenario = max(leg.dte / 365.0 - horizon_t, 0.0)
                sigma_scenario = max(entry_iv * (1.0 + iv_pct), 1e-4)
                scenario_price = _leg_value(
                    forward=forward_scenario,
                    strike=leg.strike,
                    r=r,
                    sigma=sigma_scenario,
                    t=t_scenario,
                    option_type=leg.option_type,
                )
                sign = -1.0 if leg.is_short else 1.0
                payoff += sign * (scenario_price - entry_price)
            weighted_payoff += joint_w * payoff
            if payoff > 0:
                prob_profit += joint_w

    net_expected_return = Decimal(str(weighted_payoff)) - entry_cost_points
    max_loss = _structural_max_loss(spec.structure, legs, entry_prices)
    reward_risk = float(net_expected_return / max_loss) if max_loss and max_loss > 0 else None

    greeks = _aggregate_entry_greeks(legs, entry_ivs, forward=smile.forward, r=r)
    merged_rationale: dict[str, object] = dict(rationale or {})
    merged_rationale.setdefault("net_credit_points", _net_credit(legs, entry_prices))

    return StrategyCandidate(
        structure=spec.structure,
        legs=legs,
        net_expected_return=net_expected_return,
        pop=prob_profit,
        max_loss=max_loss,
        reward_risk=reward_risk,
        greeks=greeks,
        rationale=merged_rationale,
    )


def rank_candidates(
    candidates: list[StrategyCandidate], *, top_n: int = 3
) -> list[StrategyCandidate]:
    """Net Expected Return 내림차순 상위 `top_n`개 (Ver 1.3 §5.2 "상위 후보 3개")."""
    return sorted(candidates, key=lambda c: c.net_expected_return, reverse=True)[:top_n]


# ---------------------------------------------------------------- 구조상 최대손실/Greeks


def _net_credit(legs: list[StrategyLeg], entry_prices: list[float]) -> float:
    """양수=순수취(credit), 음수=순지불(debit)."""
    return sum(ep if leg.is_short else -ep for leg, ep in zip(legs, entry_prices))


def _structural_max_loss(
    structure: str, legs: list[StrategyLeg], entry_prices: list[float]
) -> Decimal:
    if structure in (matrix.LONG_CALL, matrix.LONG_PUT):
        return Decimal(str(entry_prices[0]))  # 순수 매수 — 최대손실 = 지불한 프리미엄

    if structure in (
        matrix.BULL_CALL_SPREAD,
        matrix.BULL_PUT_SPREAD,
        matrix.BEAR_PUT_SPREAD,
        matrix.BEAR_CALL_SPREAD,
    ):
        width = abs(legs[0].strike - legs[1].strike)
        net_credit = _net_credit(legs, entry_prices)
        if net_credit >= 0:  # 신용 스프레드
            return Decimal(str(max(0.0, width - net_credit)))
        return Decimal(str(max(0.0, -net_credit)))  # 차변 스프레드 — 최대손실 = 순지불액

    if structure == matrix.IRON_CONDOR:
        # _leg_templates() 순서: [매도풋, 매수풋, 매도콜, 매수콜]
        put_width = abs(legs[0].strike - legs[1].strike)
        call_width = abs(legs[2].strike - legs[3].strike)
        net_credit = _net_credit(legs, entry_prices)
        return Decimal(str(max(0.0, max(put_width, call_width) - net_credit)))

    return Decimal("0")  # 도달 안 함 — build_legs()가 미지원 구조는 이미 None 반환


def _aggregate_entry_greeks(
    legs: list[StrategyLeg], entry_ivs: list[float], *, forward: float, r: float
) -> GreeksProfile:
    delta = gamma = theta = vega = 0.0
    for leg, iv in zip(legs, entry_ivs):
        g = black76_greeks(
            forward=forward,
            strike=leg.strike,
            r=r,
            sigma=iv,
            t=leg.dte / 365.0,
            option_type=leg.option_type,
        )
        sign = -1.0 if leg.is_short else 1.0
        delta += sign * g.delta
        gamma += sign * g.gamma
        theta += sign * g.theta
        vega += sign * g.vega
    iv_avg = sum(entry_ivs) / len(entry_ivs) if entry_ivs else 0.0
    return GreeksProfile(delta=delta, gamma=gamma, theta=theta, vega=vega, iv=iv_avg)
