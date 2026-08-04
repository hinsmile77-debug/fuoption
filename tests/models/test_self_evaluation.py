from messiah.core.messages import Fill, OrderAck, OrderKind, OrderRequest, Side
from messiah.models.self_evaluation import reconcile_slippage, run_self_evaluation
from messiah.models.wiring_completeness import WiringCompleteness
from messiah.risk.cost_model import CostModel, CostModelConfig


def _order(msg_id_suffix: str, limit_price_ticks: int | None) -> OrderRequest:
    req = OrderRequest(
        intent_id="intent-1",
        symbol="TEST",
        kind=OrderKind.ENTRY,
        side=Side.LONG,
        qty=1,
        limit_price_ticks=limit_price_ticks,
    )
    return req


def _ack(order: OrderRequest, broker_order_no: str) -> OrderAck:
    return OrderAck(request_id=order.msg_id, broker_order_no=broker_order_no, pending_key="k")


def _fill(broker_order_no: str, price_ticks: int) -> Fill:
    from datetime import datetime

    from messiah.core.timeutil import KST

    return Fill(
        broker_order_no=broker_order_no,
        symbol="TEST",
        qty=1,
        price_ticks=price_ticks,
        ts_exchange=datetime(2026, 7, 27, 9, 0, tzinfo=KST),
        pending_matched=True,
    )


# ---------------------------------------------------------------- reconcile_slippage


def test_reconcile_slippage_computes_mean_abs_diff_for_limit_orders():
    order = _order("1", limit_price_ticks=1000)
    ack = _ack(order, "B1")
    fill = _fill("B1", price_ticks=1003)

    result = reconcile_slippage([order], [ack], [fill], cost_model=CostModel())
    assert result.n_samples == 1
    assert result.realized_ticks == 3.0
    assert result.predicted_ticks == CostModelConfig().expected_spread_ticks


def test_reconcile_slippage_ignores_market_orders():
    order = _order("1", limit_price_ticks=None)
    ack = _ack(order, "B1")
    fill = _fill("B1", price_ticks=1003)

    result = reconcile_slippage([order], [ack], [fill])
    assert result.n_samples == 0
    # 표본 0 = "잴 수 없었다"(None)이지 "슬리피지 0틱"이 아니다 — 2026-08-03 정정.
    assert result.realized_ticks is None


def test_reconcile_slippage_ignores_unmatched_fills():
    order = _order("1", limit_price_ticks=1000)
    ack = _ack(order, "B1")
    unrelated_fill = _fill("OTHER", price_ticks=999)

    result = reconcile_slippage([order], [ack], [unrelated_fill])
    assert result.n_samples == 0


def test_reconcile_slippage_empty_inputs():
    result = reconcile_slippage([], [], [])
    assert result.n_samples == 0
    assert result.realized_ticks is None  # 위와 같은 이유 — 0.0으로 뭉개지 않는다


# ---------------------------------------------------------------- run_self_evaluation


def _measurable() -> WiringCompleteness:
    """손익을 측정해도 되는 결선 상태 — 이 값이 있어야 4지표가 숫자로 나온다(2026-08-05)."""
    return WiringCompleteness(
        live_bundles=["5m@v1"], n_decisions=5, n_orders=3, fills_countable=True
    )


def test_run_self_evaluation_aggregates_metrics():
    report = run_self_evaluation(
        date="2026-07-27",
        symbol="TEST",
        champion_returns=[0.01, -0.005, 0.02, -0.002],
        n_shadow_bundles=2,
        wiring=_measurable(),
    )
    assert report.date == "2026-07-27"
    assert report.symbol == "TEST"
    assert report.n_return_samples == 4
    assert report.win_rate is not None and 0.0 <= report.win_rate <= 1.0
    assert report.n_shadow_bundles == 2


def test_run_self_evaluation_with_no_trades_is_degenerate_but_safe():
    """표본이 없어도 죽지 않는다 — 다만 결선이 됐다고 주장한 경우엔 0.0이 정직한 값이다."""
    report = run_self_evaluation(
        date="2026-07-27",
        symbol="TEST",
        champion_returns=[],
        n_shadow_bundles=0,
        wiring=_measurable(),
    )
    assert report.n_return_samples == 0
    assert report.sharpe == 0.0
    assert report.win_rate == 0.0
    assert report.profit_factor == 0.0


def test_pnl_metrics_are_none_when_not_measurable():
    """2026-07-29~08-04 회귀 — 5거래일 연속 `sharpe=0.0`이 JSON에 남았고 "성적 0"으로
    읽혔다. 실제로는 `live 번들 결선: []`, 모델이 하나도 안 붙은 채 파이프라인만 돈 것이다.

    `pnl_measurable` 플래그를 08-03에 넣고도 오독이 계속됐다 — 플래그는 같이 안 읽히면
    소용이 없다. `n_fills`·`slippage_realized_ticks`에서 이미 두 번 쓴 해법(모르는 것은
    None)을 손익 4지표에도 적용한다."""
    report = run_self_evaluation(
        date="2026-08-04", symbol="A05608", champion_returns=[0.0] * 5, n_shadow_bundles=0
    )

    assert report.pnl_measurable is False
    assert report.n_return_samples == 5  # 표본 수는 사실이므로 그대로 남는다
    assert report.win_rate is None
    assert report.profit_factor is None
    assert report.sharpe is None
    assert report.max_drawdown is None


def test_fill_count_is_none_when_it_was_never_measured():
    """2026-07-31 실측 회귀 — 주문 0건·체결 0건인 날의 리포트가 `n_trades=3`으로 찍혀
    "거래 3건"으로 읽혔다. 실제로는 수익률 표본 3개(=거래일 3일)였다. 세지 않은 것은
    0이 아니라 **모름**이어야 한다(L18)."""
    report = run_self_evaluation(
        date="2026-07-31", symbol="A05608", champion_returns=[0.0, 0.0, 0.0], n_shadow_bundles=0
    )

    assert report.n_return_samples == 3
    assert report.n_fills is None  # 0이 아니다 — Position Reconciler 부재로 못 셌다


def test_fill_count_is_reported_when_fills_are_supplied():
    order = _order("1", limit_price_ticks=1000)
    ack = _ack(order, "B1")

    report = run_self_evaluation(
        date="2026-07-27",
        symbol="TEST",
        champion_returns=[0.01],
        n_shadow_bundles=0,
        orders=[order],
        acks=[ack],
        fills=[_fill("B1", price_ticks=1002)],
    )

    assert report.n_fills == 1


def test_run_self_evaluation_includes_slippage_reconciliation():
    order = _order("1", limit_price_ticks=1000)
    ack = _ack(order, "B1")
    fill = _fill("B1", price_ticks=1002)

    report = run_self_evaluation(
        date="2026-07-27",
        symbol="TEST",
        champion_returns=[0.01],
        n_shadow_bundles=0,
        orders=[order],
        acks=[ack],
        fills=[fill],
    )
    assert report.slippage_realized_ticks == 2.0
