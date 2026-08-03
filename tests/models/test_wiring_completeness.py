"""G2 결선 완성도 (2026-08-03 고도화 C).

핵심 계약은 하나다 — **결선이 안 끝났으면 손익 지표를 성적이라고 주장하지 않는다.**
2026-07-29~08-03에 같은 실패를 세 번 겪었다(`n_trades` → `slippage_realized_ticks` →
손익 지표 전체).
"""

from __future__ import annotations

from messiah.models.self_evaluation import run_self_evaluation
from messiah.models.wiring_completeness import (
    STAGE_MEASURABLE,
    STAGE_NO_BUNDLE,
    STAGE_NO_DECISION,
    STAGE_NO_FILL_ACCOUNTING,
    STAGE_NO_ORDER,
    WiringCompleteness,
)


def test_the_real_2026_08_03_state_is_not_measurable():
    """2026-08-03을 그대로 재현 — `live 번들 결선: []`, 판단·주문·체결 0건인데 리포트는
    `sharpe=0.0`을 4거래일 연속 출력했다. 그 숫자는 성적이 아니라 자리표시자였다."""
    wiring = WiringCompleteness()

    assert wiring.stage == STAGE_NO_BUNDLE
    assert not wiring.pnl_measurable
    assert "손익 측정 단계 아님" in wiring.summary()


def test_stage_points_at_the_first_missing_link():
    """단계는 **지금 막혀 있는 첫 지점**이어야 한다 — 그게 곧 다음에 할 일이다."""
    assert WiringCompleteness(live_bundles=["b1"]).stage == STAGE_NO_DECISION
    assert WiringCompleteness(live_bundles=["b1"], n_decisions=5).stage == STAGE_NO_ORDER
    assert (
        WiringCompleteness(live_bundles=["b1"], n_decisions=5, n_orders=2).stage
        == STAGE_NO_FILL_ACCOUNTING
    )


def test_fully_wired_is_measurable():
    wiring = WiringCompleteness(
        live_bundles=["b1"], n_decisions=5, n_orders=2, fills_countable=True
    )

    assert wiring.stage == STAGE_MEASURABLE
    assert wiring.pnl_measurable
    assert wiring.summary().startswith("손익 측정 가능")


def test_decisions_count_includes_no_trade():
    """ "판단이 나왔나"와 "거래가 나왔나"는 다른 질문이다 — NO_TRADE도 판단이므로 번들이
    붙었다는 증거가 된다(`strategy/pipeline.py`의 계측 지점 주석)."""
    wiring = WiringCompleteness(live_bundles=["b1"], n_decisions=1)

    assert wiring.stage == STAGE_NO_ORDER  # 판단 단계는 통과했다


# ---------------------------------------------------------------- SelfEvalReport 결선


def test_self_eval_marks_pnl_unmeasurable_when_wiring_is_incomplete():
    report = run_self_evaluation(
        date="2026-08-03",
        symbol="A05608",
        champion_returns=[0.0, 0.0, 0.0, 0.0],
        n_shadow_bundles=0,
        wiring=WiringCompleteness(),
    )

    assert report.pnl_measurable is False
    assert report.wiring_stage == STAGE_NO_BUNDLE
    assert report.wiring_summary is not None


def test_self_eval_without_wiring_does_not_claim_measurability():
    """호출자가 결선 상태를 안 넘기면 "측정 가능"이라고 주장하지 않는다 — 모르는 것을
    좋은 쪽으로 가정하지 않는다."""
    report = run_self_evaluation(
        date="2026-08-03", symbol="A05608", champion_returns=[0.01], n_shadow_bundles=0
    )

    assert report.pnl_measurable is False
    assert report.wiring_stage is None


def test_self_eval_reports_measurable_when_fully_wired():
    report = run_self_evaluation(
        date="2026-08-03",
        symbol="A05608",
        champion_returns=[0.01, -0.005],
        n_shadow_bundles=1,
        wiring=WiringCompleteness(
            live_bundles=["5m_A"], n_decisions=12, n_orders=3, fills_countable=True
        ),
    )

    assert report.pnl_measurable is True
    assert report.wiring_stage == STAGE_MEASURABLE
