from messiah.core.messages import Fill, OrderAck, OrderKind, OrderRequest, Side
from messiah.models.self_evaluation import reconcile_slippage, run_self_evaluation
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
    assert result.realized_ticks == 0.0


def test_reconcile_slippage_ignores_unmatched_fills():
    order = _order("1", limit_price_ticks=1000)
    ack = _ack(order, "B1")
    unrelated_fill = _fill("OTHER", price_ticks=999)

    result = reconcile_slippage([order], [ack], [unrelated_fill])
    assert result.n_samples == 0


def test_reconcile_slippage_empty_inputs():
    result = reconcile_slippage([], [], [])
    assert result.n_samples == 0
    assert result.realized_ticks == 0.0


# ---------------------------------------------------------------- run_self_evaluation


def test_run_self_evaluation_aggregates_metrics():
    report = run_self_evaluation(
        date="2026-07-27",
        symbol="TEST",
        champion_returns=[0.01, -0.005, 0.02, -0.002],
        n_shadow_bundles=2,
    )
    assert report.date == "2026-07-27"
    assert report.symbol == "TEST"
    assert report.n_trades == 4
    assert 0.0 <= report.win_rate <= 1.0
    assert report.n_shadow_bundles == 2


def test_run_self_evaluation_with_no_trades_is_degenerate_but_safe():
    report = run_self_evaluation(
        date="2026-07-27", symbol="TEST", champion_returns=[], n_shadow_bundles=0
    )
    assert report.n_trades == 0
    assert report.sharpe == 0.0
    assert report.win_rate == 0.0
    assert report.profit_factor == 0.0


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
