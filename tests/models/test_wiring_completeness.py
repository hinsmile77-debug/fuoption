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
    STAGE_NO_PNL_UNIT,
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
    assert (
        WiringCompleteness(
            live_bundles=["b1"], n_decisions=5, n_orders=2, fills_countable=True
        ).stage
        == STAGE_NO_PNL_UNIT
    )


def test_counting_fills_alone_does_not_make_pnl_measurable():
    """F-114가 체결을 셀 수 있게 만든 다음 날 이 테스트가 필요해졌다.

    `PositionReconciler`는 실현손익을 **틱**으로 낸다. 승률·PF·Sharpe·MDD가 먹는
    `champion_returns`는 **자본 대비 비율**이고, 틱을 비율로 바꾸려면 계약 승수가 있어야
    하는데 그 값은 이 저장소에 없다. 이 칸이 없으면 `fills_countable=True`가 되는 순간
    36행 전부 0.0인 수익률 파일로 계산한 `sharpe=0.0`이 「측정값」 도장을 받는다 — 이
    모듈이 막으려고 만들어진 바로 그 형태다."""
    wiring = WiringCompleteness(
        live_bundles=["b1"], n_decisions=5, n_orders=2, fills_countable=True
    )

    assert wiring.stage == STAGE_NO_PNL_UNIT
    assert not wiring.pnl_measurable
    assert "계약 승수" in wiring.summary()


def test_reconciliation_has_three_values_not_two():
    """대사는 **안 함/일치/불일치**다. 「안 함」을 「불일치」로 쓰면 없는 사고가 생기고,
    「불일치」를 「안 함」으로 쓰면 유령 포지션이 침묵으로 읽힌다(L12)."""
    base = dict(live_bundles=["b1"], n_decisions=5, n_orders=2, fills_countable=True)

    assert "포지션대사 미실시" in WiringCompleteness(**base).summary()
    assert "포지션대사 일치" in WiringCompleteness(**base, positions_reconciled=True).summary()
    assert "포지션대사 **불일치**" in (
        WiringCompleteness(**base, positions_reconciled=False).summary()
    )


def test_fully_wired_is_measurable():
    wiring = WiringCompleteness(
        live_bundles=["b1"],
        n_decisions=5,
        n_orders=2,
        fills_countable=True,
        returns_convertible=True,
        positions_reconciled=True,
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
            live_bundles=["5m_A"],
            n_decisions=12,
            n_orders=3,
            fills_countable=True,
            returns_convertible=True,
            positions_reconciled=True,
        ),
    )

    assert report.pnl_measurable is True
    assert report.wiring_stage == STAGE_MEASURABLE


# ------------------- 결선이 끝나도 표본이 없으면 측정값이 아니다 (2026-09-17)


def test_a_finished_wiring_with_no_samples_still_is_not_measurable():
    """**같은 실패 형태의 다섯 번째를 여기서 막는다.**

    2026-09-17에 계약 명세가 확정되면서 `returns_convertible`이 True가 될 수 있게 됐고,
    같은 날 옛 정의의 36행이 표본에서 빠지면서 `champion_returns`가 한동안 **빈 리스트**가
    된다. `sharpe_ratio`는 표본 2개 미만에 0.0을 돌려주므로(계산 불능 대신 0.0 규약),
    가드가 없으면 그 0.0이 `pnl_measurable=True`를 달고 나간다 — `n_trades`(07-31) ·
    `slippage_realized_ticks`(08-03) · 손익 4지표(08-05) · `fills_countable`(09-17)에
    이은 다섯 번째다."""
    wiring = WiringCompleteness(
        live_bundles=["real-20260820-2053-30m"],
        n_decisions=14,
        n_orders=2,
        fills_countable=True,
        returns_convertible=True,
        positions_reconciled=True,
    )

    report = run_self_evaluation(
        date="2026-09-18",
        symbol="A05610",
        champion_returns=[],
        n_shadow_bundles=0,
        wiring=wiring,
    )

    assert wiring.pnl_measurable is True, "결선 자체는 끝났다"
    assert report.pnl_measurable is False, "그러나 잰 것이 없다"
    assert report.sharpe is None and report.win_rate is None
    assert report.n_return_samples == 0


def test_one_day_is_not_enough_either():
    """표본 1개의 Sharpe는 표준편차가 0이라 0.0이 된다 — 그것도 성적이 아니다.
    임계는 `metrics.sharpe_ratio`가 스스로 "계산 불능"이라고 적은 수(2)와 같다."""
    wiring = WiringCompleteness(
        live_bundles=["b"],
        n_decisions=1,
        n_orders=1,
        fills_countable=True,
        returns_convertible=True,
        positions_reconciled=True,
    )

    one = run_self_evaluation(
        date="2026-09-18",
        symbol="A05610",
        champion_returns=[0.0006],
        n_shadow_bundles=0,
        wiring=wiring,
    )
    two = run_self_evaluation(
        date="2026-09-21",
        symbol="A05610",
        champion_returns=[0.0006, -0.0002],
        n_shadow_bundles=0,
        wiring=wiring,
    )

    assert one.pnl_measurable is False
    assert two.pnl_measurable is True and two.sharpe is not None
