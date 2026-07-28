"""전략 후보 생성 매트릭스 (신규, Ver 2.0 §9 W27~29)."""

from __future__ import annotations

from messiah.strategy.options.config import OptionsConfig
from messiah.strategy.options.matrix import (
    BEAR_CALL_SPREAD,
    BEAR_PUT_SPREAD,
    BULL_CALL_SPREAD,
    BULL_PUT_SPREAD,
    CALENDAR,
    IRON_CONDOR,
    LONG_CALL,
    LONG_PUT,
    Direction,
    IVState,
    candidate_specs,
    classify_direction,
    classify_iv_state,
    is_credit_structure,
    skew_excludes_short_put,
)

_CFG = OptionsConfig()

# ---------------------------------------------------------------- classify_direction/iv_state


def test_classify_direction_boundaries():
    assert classify_direction(0.21, _CFG) == Direction.UP
    assert classify_direction(-0.21, _CFG) == Direction.DOWN
    assert classify_direction(0.20, _CFG) == Direction.NEUTRAL  # 경계값은 중립(strict >)
    assert classify_direction(0.0, _CFG) == Direction.NEUTRAL


def test_classify_iv_state_boundaries():
    assert classify_iv_state(29.9, _CFG) == IVState.LOW
    assert classify_iv_state(30.0, _CFG) == IVState.MID  # 경계값 30은 저변동 아님(strict <)
    assert classify_iv_state(70.0, _CFG) == IVState.MID  # 경계값 70은 고변동 아님(strict >)
    assert classify_iv_state(70.1, _CFG) == IVState.HIGH
    assert classify_iv_state(50.0, _CFG) == IVState.MID


def test_classify_iv_state_none_when_rank_unavailable():
    assert classify_iv_state(None, _CFG) is None


# ---------------------------------------------------------------- candidate_specs (매트릭스 9칸)


def _structures(score: float, iv_rank: float) -> list[str]:
    return [spec.structure for spec in candidate_specs(score, iv_rank, _CFG)]


def test_matrix_cell_up_low():
    assert _structures(0.5, 10.0) == [LONG_CALL, BULL_CALL_SPREAD]


def test_matrix_cell_up_mid():
    assert _structures(0.5, 50.0) == [BULL_CALL_SPREAD]


def test_matrix_cell_up_high_uses_spread_not_naked_short():
    assert _structures(0.5, 90.0) == [BULL_PUT_SPREAD]


def test_matrix_cell_neutral_low():
    assert _structures(0.0, 10.0) == [CALENDAR]


def test_matrix_cell_neutral_mid_is_empty_no_edge():
    assert _structures(0.0, 50.0) == []


def test_matrix_cell_neutral_high_uses_iron_condor_not_naked_strangle():
    assert _structures(0.0, 90.0) == [IRON_CONDOR]


def test_matrix_cell_down_low():
    assert _structures(-0.5, 10.0) == [LONG_PUT, BEAR_PUT_SPREAD]


def test_matrix_cell_down_mid():
    assert _structures(-0.5, 50.0) == [BEAR_PUT_SPREAD]


def test_matrix_cell_down_high_uses_spread_not_naked_short():
    assert _structures(-0.5, 90.0) == [BEAR_CALL_SPREAD]


def test_candidate_specs_empty_when_iv_rank_none():
    assert candidate_specs(0.5, None, _CFG) == []


def test_candidate_specs_respects_max_candidates_cap():
    cfg = OptionsConfig(max_candidates=1)
    specs = candidate_specs(0.5, 10.0, cfg)  # UP/LOW 칸은 원래 2개 구조
    assert len(specs) == 1


# ---------------------------------------------------------------- CandidateSpec 필드


def test_credit_structure_spec_swaps_delta_bands_vs_debit():
    # 신용 스프레드는 매도 다리가 등가격에 가까워야(델타 절대값이 커야) 행사가 순서가
    # 성립한다 — matrix.py 모듈 docstring "Ver 1.3 §4.2 델타 배정도 신용 스프레드에는
    # 문자 그대로 못 쓴다" 참고. 그래서 매도=long_leg_delta 밴드(근접 등가격, 30~50Δ),
    # 매수=short_leg_delta 밴드(날개, 15~30Δ)로 debit 구조와 반대로 배정된다.
    spec = candidate_specs(0.5, 90.0, _CFG)[0]  # BULL_PUT_SPREAD
    assert spec.structure == BULL_PUT_SPREAD
    assert spec.is_credit is True
    assert spec.short_leg_delta_range == (_CFG.long_leg_delta_low, _CFG.long_leg_delta_high)
    assert spec.long_leg_delta_range == (_CFG.short_leg_delta_low, _CFG.short_leg_delta_high)
    assert spec.dte_low == _CFG.short_structure_dte_low
    assert spec.dte_high == _CFG.short_structure_dte_high


def test_pure_long_structure_spec_has_no_short_leg_and_unbounded_dte():
    spec = candidate_specs(0.5, 10.0, _CFG)[0]  # LONG_CALL
    assert spec.structure == LONG_CALL
    assert spec.is_credit is False
    assert spec.short_leg_delta_range is None
    assert spec.long_leg_delta_range == (_CFG.long_leg_delta_low, _CFG.long_leg_delta_high)
    assert spec.dte_low == _CFG.long_structure_dte_min
    assert spec.dte_high is None


def test_debit_spread_spec_matches_ver13_literal_delta_bands():
    # Ver 1.3 §4.2 원문 그대로: 매도(날개)=15~30Δ, 매수(근접 등가격)=30~50Δ — debit
    # 스프레드는 스왑 없이 원문 그대로 적용된다(신용 스프레드와의 대비, 위 테스트 참고).
    spec = candidate_specs(0.5, 50.0, _CFG)[0]  # BULL_CALL_SPREAD
    assert spec.structure == BULL_CALL_SPREAD
    assert spec.is_credit is False
    assert spec.short_leg_delta_range == (_CFG.short_leg_delta_low, _CFG.short_leg_delta_high)
    assert spec.long_leg_delta_range == (_CFG.long_leg_delta_low, _CFG.long_leg_delta_high)
    assert spec.dte_low == _CFG.long_structure_dte_min
    assert spec.dte_high is None


# ---------------------------------------------------------------- is_credit_structure / skew filter


def test_is_credit_structure():
    assert is_credit_structure(BULL_PUT_SPREAD) is True
    assert is_credit_structure(LONG_CALL) is False


def test_skew_excludes_short_put_only_for_short_put_leg_structures():
    cfg = OptionsConfig(skew_extreme_threshold=0.10)
    assert skew_excludes_short_put(BULL_PUT_SPREAD, 0.15, cfg) is True
    assert skew_excludes_short_put(BULL_PUT_SPREAD, 0.05, cfg) is False
    assert skew_excludes_short_put(IRON_CONDOR, -0.15, cfg) is True  # 절대값 판정
    assert skew_excludes_short_put(BEAR_CALL_SPREAD, 0.99, cfg) is False  # 풋매도 다리 없음
