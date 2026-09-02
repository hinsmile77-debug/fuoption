from datetime import datetime, timedelta

import pytest

from messiah.core.messages import Horizon, Regime
from messiah.core.timeutil import KST
from messiah.models.label_geometry import (
    DEFAULT_SCORE_GATE,
    LabelGeometry,
    check_horizon_ladder,
    regime_reachability,
    summarise_regime_reachability,
    time_barrier_minutes,
)
from messiah.models.labeling import BARRIER_PARAMS, BarrierParams, TripleBarrierLabel
from messiah.strategy.decision.meta_decision import SCORE_THRESHOLD
from messiah.strategy.futures.aggregator import REGIME_WEIGHTS

_START = datetime(2026, 8, 4, 9, 0, tzinfo=KST)


def _labels(down: int, flat: int, up: int, *, ret_ticks: int = 100, width: int = 100):
    """지정한 클래스 비율의 레이블 집합. flat은 시간배리어, 나머지는 터치로 만든다."""
    out = []
    plan = [(-1, "lower", -width)] * down + [(0, "time", ret_ticks)] * flat
    plan += [(1, "upper", width)] * up
    for i, (label, barrier, ret) in enumerate(plan):
        out.append(
            TripleBarrierLabel(
                symbol="TEST",
                horizon=Horizon.M30,
                t_start=_START + timedelta(minutes=30 * i),
                t_end=_START + timedelta(minutes=30 * (i + 3)),
                entry_price_ticks=10_000,
                label=label,
                barrier=barrier,
                ret_ticks=ret,
            )
        )
    return out


# --------------------------------------------------------------- |S| 천장


def test_score_ceiling_is_one_minus_flat_share():
    """모듈의 핵심 항등식: |p_up − p_down| <= 1 − p_flat."""
    geom = LabelGeometry.build(_labels(12, 76, 12), cost_ticks=1.6)

    assert geom.flat_share == pytest.approx(0.76)
    assert geom.score_ceiling == pytest.approx(0.24)


def test_flat_heavy_label_makes_gate_structurally_unreachable():
    """2026-08-04 실측 재현 — 30m flat 76.3%면 천장 0.237로 게이트 0.20 바로 위다."""
    geom = LabelGeometry.build(_labels(110, 763, 127), cost_ticks=1.6)

    assert geom.score_ceiling == pytest.approx(0.237, abs=0.001)
    assert geom.gate_is_reachable is False
    assert "게이트 도달 불가" in geom.verdict


def test_balanced_label_leaves_room_for_the_gate():
    geom = LabelGeometry.build(_labels(33, 34, 33), cost_ticks=1.6, score_gate=0.20)

    assert geom.score_ceiling == pytest.approx(0.66)
    assert geom.gate_is_reachable is True
    assert "도달 여지 있음" in geom.verdict


def test_reachable_gate_is_not_claimed_to_be_an_edge():
    """필요조건일 뿐이라는 것을 판정문이 스스로 말해야 한다 — flat만 낮춰 놓고 '고쳤다'고
    읽히면 2026-08-04에 실제로 나빠진 그 변형(flat 33%, 초과손익 t 2.38→1.38)을 반복한다."""
    geom = LabelGeometry.build(_labels(33, 34, 33), cost_ticks=1.6)

    assert geom.gate_is_reachable is True
    assert "ScoreCalibration" in geom.verdict


# --------------------------------------------------------------- 비용 강등 규칙


def test_cost_rule_is_dead_when_barriers_dwarf_cost():
    """배리어 폭 150틱 vs 왕복 비용 1.6틱 — 강등이 한 건도 안 나는 게 정상인 상태."""
    geom = LabelGeometry.build(_labels(30, 40, 30, width=150), cost_ticks=1.6)

    assert geom.n_cost_demoted == 0
    assert geom.barrier_cost_ratio == pytest.approx(150 / 1.6, rel=0.01)
    assert geom.cost_rule_is_live is False
    assert "비용 강등 규칙이 죽어 있다" in geom.verdict


def test_cost_rule_is_live_when_barrier_is_comparable_to_cost():
    geom = LabelGeometry.build(_labels(30, 40, 30, width=8), cost_ticks=1.6)

    assert geom.cost_rule_is_live is True
    assert "비용 강등 규칙이 죽어 있다" not in geom.verdict


def test_actual_demotion_counts_as_live_regardless_of_ratio():
    labels = _labels(30, 40, 30, width=150)
    labels = [labels[0].__class__(**{**labels[0].__dict__, "cost_demoted": True})] + labels[1:]

    geom = LabelGeometry.build(labels, cost_ticks=1.6)

    assert geom.cost_rule_is_live is True


# --------------------------------------------------------------- 버려지는 방향 정보


def test_flat_labels_holding_tradable_moves_are_reported():
    """flat 안에 비용을 넘는 이동이 남아 있으면 방향 정보를 버리고 있다는 뜻이다."""
    geom = LabelGeometry.build(_labels(10, 80, 10, ret_ticks=232), cost_ticks=1.6)

    assert geom.flat_above_cost_share == pytest.approx(1.0)
    assert geom.flat_abs_ret_median_ticks == pytest.approx(232)
    assert "0으로 뭉개졌다" in geom.verdict


# --------------------------------------------------------------- Horizon 사다리


def test_production_barrier_table_has_a_collapsed_ladder():
    """현행 표의 실제 상태 — 분으로는 3~90분이지만 봉 수로는 전부 3봉이다."""
    ladder = check_horizon_ladder(BARRIER_PARAMS)

    assert set(ladder.bars_by_horizon.values()) == {3}
    assert ladder.is_collapsed is True
    assert "사다리 붕괴" in ladder.verdict


def test_ladder_is_not_collapsed_when_bars_actually_differ():
    params = {
        Horizon.M5: BarrierParams(time_barrier_bars=3, width_atr_mult=1.0),
        Horizon.M15: BarrierParams(time_barrier_bars=9, width_atr_mult=1.0),
    }

    ladder = check_horizon_ladder(params)

    assert ladder.is_collapsed is False
    assert "Horizon별로 다르다" in ladder.verdict


def test_bar_counts_still_round_trip_to_the_ver12_minute_table():
    """봉 수를 정본으로 바꿨어도 Ver 1.2 §3.2 표(분)와 대조 가능해야 한다."""
    expected = {
        Horizon.M1: 3,
        Horizon.M3: 9,
        Horizon.M5: 15,
        Horizon.M10: 30,
        Horizon.M15: 45,
        Horizon.M30: 90,
    }

    for horizon, minutes in expected.items():
        assert time_barrier_minutes(horizon, BARRIER_PARAMS[horizon]) == minutes


# --------------------------------------------------------------- 게이트 상수 동기


def test_default_gate_matches_the_decision_engine():
    """이 모듈은 게이트 상수를 일부러 복제해 둔다(모듈 주석) — 갈라지면 여기서 잡는다."""
    assert DEFAULT_SCORE_GATE == SCORE_THRESHOLD


# --------------------------------------------------------------- 빈 입력


def test_empty_labels_refuse_to_look_healthy():
    geom = LabelGeometry.build([], cost_ticks=1.6)

    assert geom.n == 0
    assert geom.verdict == "레이블 0건 — 판정 불가"


# --------------------------------------------------------------- 국면가중 실효 천장


def _geometry_with_flat(flat_share: float, *, n: int = 1000) -> LabelGeometry:
    flat = round(n * flat_share)
    rest = n - flat
    return LabelGeometry.build(_labels(rest // 2, flat, rest - rest // 2), cost_ticks=1.6)


def test_regime_weight_moves_the_ceiling_both_ways():
    """**하나의 천장이 두 방향으로 동시에 틀려 있었다** (2026-09-02).

    30m flat 76.4%면 가중치 1 기준 천장은 0.236이고 종전 판정은 "게이트 0.20에 여유 1.18배,
    사실상 도달 불가"였다. 그런데 실제 S에는 국면가중이 곱해진다 — 추세(×1.5)에서는 0.354로
    **열려 있고**, 횡보(×0.4)에서는 0.094로 **게이트에 닿지도 못한다.**
    """
    geometry = _geometry_with_flat(0.764)
    cards = {
        card.regime: card
        for card in regime_reachability([geometry], REGIME_WEIGHTS, score_gate=0.20)
    }

    trend = cards[Regime.TREND_UP]
    assert trend.best_solo == pytest.approx(1.5 * geometry.score_ceiling, abs=1e-9)
    assert trend.is_closed is False
    assert trend.gate_is_reachable_solo is True  # 1.77배 >= MIN_CEILING_RATIO

    range_regime = cards[Regime.RANGE]
    assert range_regime.best_solo == pytest.approx(0.4 * geometry.score_ceiling, abs=1e-9)
    assert range_regime.is_closed is True
    assert "닫힘" in range_regime.verdict

    high_vol = cards[Regime.HIGH_VOL]
    assert high_vol.is_closed is True  # 0.188 < 0.20 — 라이브 실측이 이 모양이었다


def test_a_horizon_the_weight_table_does_not_know_is_left_out():
    """가중치표에 없는 Horizon은 천장에 안 들어간다 — 조용히 1을 곱하지 않는다."""
    geometry = _geometry_with_flat(0.5)
    weights = {Regime.RANGE: {Horizon.M1: 1.2}}

    (card,) = regime_reachability([geometry], weights)

    assert card.solo == {}  # 30m 기하만 줬는데 표는 1m만 안다
    assert card.best_solo == 0.0
    assert card.is_closed is True


def test_the_summary_carries_what_the_screen_needs():
    """화면 ⑤가 읽는 칸 — 없으면 화면이 "게이트 도달성"을 못 그린다."""
    summary = summarise_regime_reachability(
        regime_reachability([_geometry_with_flat(0.764)], REGIME_WEIGHTS)
    )

    assert summary["HIGH_VOL"]["closed"] is True
    assert summary["TREND_UP"]["closed"] is False
    for payload in summary.values():
        assert {"score_gate", "ceiling_solo", "closed", "verdict"} <= set(payload)
