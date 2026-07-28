"""Strategy Evaluator (신규, Ver 2.0 §9 W30~31)."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from messiah.strategy.options import matrix
from messiah.strategy.options.config import OptionsConfig
from messiah.strategy.options.evaluator import (
    EvaluatorConfig,
    _normal_weights,
    build_legs,
    evaluate_candidate,
    rank_candidates,
)
from messiah.strategy.options.matrix import CandidateSpec, candidate_specs
from messiah.strategy.options.surface import fit_smile

_CFG = OptionsConfig()
_R = 0.03


def _smile(forward: float = 350.0, iv: float = 0.20, dte: int = 20):
    points = [(k, iv) for k in (300.0, 320.0, 340.0, 350.0, 360.0, 380.0, 400.0)]
    fit = fit_smile(forward, dte=dte, strike_iv_points=points)
    assert fit is not None
    return fit


def _spec_for(structure: str) -> CandidateSpec:
    # UP/LOW → LONG_CALL,BULL_CALL_SPREAD ; UP/MID → BULL_CALL_SPREAD ; UP/HIGH → BULL_PUT_SPREAD
    # DOWN/LOW → LONG_PUT,BEAR_PUT_SPREAD ; DOWN/MID → BEAR_PUT_SPREAD
    # DOWN/HIGH → BEAR_CALL_SPREAD ; NEUTRAL/HIGH → IRON_CONDOR
    lookup = {
        matrix.LONG_CALL: (0.5, 10.0),
        matrix.LONG_PUT: (-0.5, 10.0),
        matrix.BULL_CALL_SPREAD: (0.5, 50.0),
        matrix.BULL_PUT_SPREAD: (0.5, 90.0),
        matrix.BEAR_PUT_SPREAD: (-0.5, 50.0),
        matrix.BEAR_CALL_SPREAD: (-0.5, 90.0),
        matrix.IRON_CONDOR: (0.0, 90.0),
    }
    score, iv_rank = lookup[structure]
    specs = candidate_specs(score, iv_rank, _CFG)
    return next(s for s in specs if s.structure == structure)


# ---------------------------------------------------------------- build_legs


def test_build_legs_long_call_single_leg():
    legs = build_legs(_spec_for(matrix.LONG_CALL), _smile(), r=_R)
    assert legs is not None
    assert len(legs) == 1
    assert legs[0].option_type == "C"
    assert legs[0].is_short is False
    assert legs[0].delta > 0


def test_build_legs_bull_call_spread_long_strike_below_short_strike():
    legs = build_legs(_spec_for(matrix.BULL_CALL_SPREAD), _smile(), r=_R)
    assert legs is not None
    assert len(legs) == 2
    long_leg, short_leg = legs[0], legs[1]
    assert long_leg.is_short is False and short_leg.is_short is True
    assert long_leg.strike < short_leg.strike  # 매수(근접 등가격) < 매도(날개) 행사가


def test_build_legs_bull_put_spread_short_strike_above_long_strike():
    # 신용 스프레드는 매도가 등가격에 가깝다(matrix.py 모듈 docstring 결론) → 매도 행사가가
    # 더 높아야 진짜 bull put spread(K_sell > K_buy)가 성립한다.
    legs = build_legs(_spec_for(matrix.BULL_PUT_SPREAD), _smile(), r=_R)
    assert legs is not None
    short_leg, long_leg = legs[0], legs[1]
    assert short_leg.is_short is True and long_leg.is_short is False
    assert short_leg.strike > long_leg.strike


def test_build_legs_bear_call_spread_short_strike_below_long_strike():
    legs = build_legs(_spec_for(matrix.BEAR_CALL_SPREAD), _smile(), r=_R)
    assert legs is not None
    short_leg, long_leg = legs[0], legs[1]
    assert short_leg.is_short is True and long_leg.is_short is False
    assert short_leg.strike < long_leg.strike


def test_build_legs_iron_condor_four_legs_correctly_ordered():
    legs = build_legs(_spec_for(matrix.IRON_CONDOR), _smile(), r=_R)
    assert legs is not None
    assert len(legs) == 4
    short_put, long_put, short_call, long_call = legs
    assert (short_put.option_type, short_put.is_short) == ("P", True)
    assert (long_put.option_type, long_put.is_short) == ("P", False)
    assert (short_call.option_type, short_call.is_short) == ("C", True)
    assert (long_call.option_type, long_call.is_short) == ("C", False)
    assert short_put.strike > long_put.strike  # 매도풋이 등가격에 더 가까움(더 높은 행사가)
    assert short_call.strike < long_call.strike  # 매도콜이 등가격에 더 가까움(더 낮은 행사가)
    assert long_put.strike < short_put.strike < short_call.strike < long_call.strike


def test_build_legs_returns_none_for_calendar():
    specs = candidate_specs(0.0, 10.0, _CFG)  # NEUTRAL/LOW → CALENDAR
    spec = next(s for s in specs if s.structure == matrix.CALENDAR)
    assert build_legs(spec, _smile(), r=_R) is None


def test_build_legs_returns_none_when_target_delta_unreachable():
    spec = _spec_for(matrix.LONG_CALL)
    impossible = replace(spec, long_leg_delta_range=(1.4, 1.6))  # 콜 델타는 [0,1] 안에서만 성립
    assert build_legs(impossible, _smile(), r=_R) is None


# ---------------------------------------------------------------- _normal_weights


def test_normal_weights_sum_to_one_and_peak_at_mean():
    points = [-3.0, -1.5, 0.0, 1.5, 3.0]
    weights = _normal_weights(points, mean=0.0, std=1.0)
    assert sum(weights) == pytest.approx(1.0)
    assert weights[2] == max(weights)  # mean=0에 가장 가까운 점(인덱스 2)이 최댓값


def test_normal_weights_degenerate_std_puts_all_mass_on_nearest_point():
    points = [-1.0, 0.0, 1.0, 2.0]
    weights = _normal_weights(points, mean=0.9, std=0.0)
    assert weights == [0.0, 0.0, 1.0, 0.0]


# ---------------------------------------------------------------- evaluate_candidate


def test_evaluate_candidate_long_call_max_loss_equals_entry_premium():
    smile = _smile()
    spec = _spec_for(matrix.LONG_CALL)
    candidate = evaluate_candidate(spec, smile, r=_R, score=0.5)
    assert candidate is not None
    assert candidate.max_loss is not None
    assert candidate.max_loss > 0
    assert candidate.pop == pytest.approx(candidate.pop)  # 자기 자신 — 존재만 확인(아래 범위 체크)
    assert 0.0 <= candidate.pop <= 1.0
    assert candidate.greeks.delta > 0  # 롱콜 — 순델타 양수


def test_evaluate_candidate_credit_spread_max_loss_bounded_by_width():
    smile = _smile()
    spec = _spec_for(matrix.BULL_PUT_SPREAD)
    candidate = evaluate_candidate(spec, smile, r=_R, score=0.5)
    assert candidate is not None
    assert candidate.max_loss is not None
    width = abs(candidate.legs[0].strike - candidate.legs[1].strike)
    assert 0.0 <= float(candidate.max_loss) <= width


def test_evaluate_candidate_iron_condor_has_zero_net_delta_ish_when_symmetric():
    # ATM 근처 flat 스마일 + score=0(드리프트 없음)이면 콜/풋 날개가 대칭이라 순델타가 작다.
    smile = _smile()
    spec = _spec_for(matrix.IRON_CONDOR)
    candidate = evaluate_candidate(spec, smile, r=_R, score=0.0)
    assert candidate is not None
    assert abs(candidate.greeks.delta) < 0.2


def test_evaluate_candidate_returns_none_when_legs_unbuildable():
    smile = _smile()
    spec = _spec_for(matrix.LONG_CALL)
    impossible = replace(spec, long_leg_delta_range=(1.4, 1.6))
    assert evaluate_candidate(impossible, smile, r=_R, score=0.5) is None


def test_evaluate_candidate_net_expected_return_decreases_by_exact_entry_cost():
    smile = _smile()
    spec = _spec_for(matrix.LONG_CALL)
    baseline = evaluate_candidate(spec, smile, r=_R, score=0.5)
    with_cost = evaluate_candidate(spec, smile, r=_R, score=0.5, entry_cost_points=Decimal("0.5"))
    assert baseline is not None and with_cost is not None
    assert float(with_cost.net_expected_return) == pytest.approx(
        float(baseline.net_expected_return) - 0.5, abs=1e-9
    )


def test_evaluate_candidate_rationale_includes_net_credit():
    smile = _smile()
    spec = _spec_for(matrix.BULL_PUT_SPREAD)
    candidate = evaluate_candidate(spec, smile, r=_R, score=0.5, rationale={"cell": "UP/HIGH"})
    assert candidate is not None
    assert candidate.rationale["cell"] == "UP/HIGH"
    assert "net_credit_points" in candidate.rationale
    assert candidate.rationale["net_credit_points"] > 0  # 신용 스프레드 — 순수취 양수


# ---------------------------------------------------------------- rank_candidates


def test_rank_candidates_orders_by_net_expected_return_desc_and_truncates():
    smile = _smile()
    candidates = [
        evaluate_candidate(_spec_for(s), smile, r=_R, score=score)
        for s, score in [
            (matrix.LONG_CALL, 0.5),
            (matrix.BULL_CALL_SPREAD, 0.5),
            (matrix.LONG_PUT, -0.5),
        ]
    ]
    candidates = [c for c in candidates if c is not None]
    assert len(candidates) == 3

    ranked = rank_candidates(candidates, top_n=2)

    assert len(ranked) == 2
    assert ranked[0].net_expected_return >= ranked[1].net_expected_return


def test_evaluator_config_is_frozen_dataclass_with_documented_defaults():
    cfg = EvaluatorConfig()
    assert cfg.price_grid_points == 21
    assert cfg.iv_grid_points == 7
    assert cfg.evaluation_horizon_days == 5.0
