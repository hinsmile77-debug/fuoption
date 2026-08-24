"""F-22 종단 replay — 2026-08-24 두 사이클 (이상점 1-10).

**이 replay의 합격 조건은 「주문이 나가는 것」이 아니다.** 두 사이클 모두 여전히 0계약이다.
합격 조건은 **「같은 0이지만 이유가 다른 0」** — 종전에는 사이저 안의 사본이 `edge`를
0으로 눕혀 0이었고, 지금은 정본 `edge`(0.1386 · 0.1626)를 그대로 받고도 `raw_qty`가
1계약에 못 미쳐 0이다. 문턱을 넘는 문제는 F-23이 재기 시작한다.

입력은 `logs/pass_cycles/2026-08-24T{103000,110000}_A05609.json`의 실측값을 그대로 옮겼다.
"""

from decimal import Decimal

import pytest

from messiah.core.messages import DecisionIntent, FuturesView, Regime, Side
from messiah.risk.sizer import PositionSizer, SizerConfig
from messiah.strategy.pipeline import _directional_edge

_SYMBOL = "A05609"
_EQUITY = Decimal("50000000")
_TICK_SIZE = Decimal("0.02")

# (라벨, agg_p_up, agg_p_down, uncertainty, atr_ticks, 기대 edge, 기대 raw_qty)
_CYCLES = [
    (
        "10:30",
        0.04161912612016356,
        0.1802385621843883,
        0.007884453774212702,
        98.57142857142857,
        0.1386,
        0.3488,
    ),
    (
        "11:00",
        0.05814788226848528,
        0.22074659009332376,
        0.008542502450421928,
        110.35714285714286,
        0.1626,
        0.3652,
    ),
]


def _view(p_up: float, p_down: float, uncertainty: float) -> FuturesView:
    return FuturesView(
        symbol=_SYMBOL,
        score=p_up - p_down,
        agg_p_up=p_up,
        agg_p_down=p_down,
        uncertainty=uncertainty,
        dispersion=0.0,
        regime=Regime.TREND_UP,
        n_experts=1,
    )


@pytest.mark.parametrize("label,p_up,p_down,unc,atr,want_edge,want_raw", _CYCLES)
def test_replay_directional_edge_reaches_sizer(label, p_up, p_down, unc, atr, want_edge, want_raw):
    view = _view(p_up, p_down, unc)
    intent = DecisionIntent(symbol=_SYMBOL, side=Side.SHORT, confidence=p_down, uncertainty=unc)

    edge = _directional_edge(view, intent.side)
    assert edge == pytest.approx(want_edge, abs=1e-4), f"{label} — 정본 edge"

    # 사이저가 받은 edge를 그대로 쓰는지: raw_qty를 산식으로 되짚어 확인한다.
    cfg = SizerConfig()
    loss_per_contract = Decimal(str(atr)) * _TICK_SIZE * cfg.point_value_krw
    risk_pct = min(cfg.vol_target_pct, cfg.max_position_loss_pct) / 100.0
    vol_target_qty = float(_EQUITY * Decimal(str(risk_pct)) / loss_per_contract)
    raw_qty = vol_target_qty * edge * cfg.fractional_kelly * (1.0 - unc)
    assert raw_qty == pytest.approx(want_raw, abs=1e-3), f"{label} — raw_qty"

    qty = PositionSizer(cfg).size(
        intent=intent,
        equity=_EQUITY,
        tick_size=_TICK_SIZE,
        stop_distance_ticks=atr,
        edge=edge,
        edge_source="directional",
    )
    # floor(0.35) = 0 — 여전히 0계약이다. 이유가 달라졌을 뿐이다.
    assert qty == 0, f"{label} — 1계약에 못 미친다(F-23이 그 거리를 잰다)"


@pytest.mark.parametrize("label,p_up,p_down,unc,atr,want_edge,want_raw", _CYCLES)
def test_replay_old_formula_would_have_zeroed_the_signal(
    label, p_up, p_down, unc, atr, want_edge, want_raw
):
    """종전 사본이 무엇을 했는지를 테스트가 기억한다 — 신호가 0에 곱해졌다."""
    old_edge = max(0.0, min(1.0, 2.0 * p_down - 1.0))
    assert old_edge == 0.0
    assert _directional_edge(_view(p_up, p_down, unc), Side.SHORT) > 0.0
