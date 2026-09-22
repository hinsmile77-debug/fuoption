"""Position Reconciler — 「오늘 얼마를 벌었나」 (2026-09-17 F-114).

이 파일이 지키는 계약은 넷이다:

1. **버그를 먼저 재현한다** — 장부를 안 붙이면 09-17까지의 동작(체결 수 모름)이 그대로다.
2. 방향은 `OrderRequest`에만 있다 — `Fill`만으로는 포지션을 못 움직인다.
3. 브로커 장부와 어긋나면 **말한다**(L12). 안 해 본 것과 해 보니 틀린 것을 섞지 않는다.
4. 체결을 세게 됐다고 손익 4지표가 자동으로 측정값이 되지는 않는다(계약 승수 미정).
"""

from __future__ import annotations

import pytest

from messiah.broker.base import BrokerAccount, BrokerAdapter, BrokerPosition, SubmitResult
from messiah.core.messages import Fill, OrderKind, OrderRequest, Side
from messiah.core.timeutil import now_utc
from messiah.execution.order_gateway import OrderGateway
from messiah.execution.position_reconciler import PositionReconciler

_SYMBOL = "A05610"


class _StubBroker(BrokerAdapter):
    """주문을 순서대로 받아 주문번호만 돌려주는 최소 브로커. 포지션은 테스트가 직접 세운다 —
    이 테스트가 보려는 것은 **장부와 브로커가 갈렸을 때**이므로 둘을 독립으로 둬야 한다."""

    name = "stub"

    def __init__(self, positions: list[BrokerPosition] | None = None) -> None:
        self._n = 0
        self._positions = positions or []
        self.fail_positions = False

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def submit(self, req: OrderRequest) -> SubmitResult:
        self._n += 1
        return SubmitResult(ok=True, broker_order_no=f"B{self._n:04d}")

    async def cancel(self, broker_order_no: str) -> bool:
        return True

    async def positions(self) -> list[BrokerPosition]:
        if self.fail_positions:
            raise RuntimeError("broker down")
        return list(self._positions)

    async def account(self) -> BrokerAccount:
        from decimal import Decimal

        return BrokerAccount(cash=Decimal(0), margin_used=Decimal(0), total_equity=Decimal(0))

    async def probe_front_month(self, product: str) -> str:
        return _SYMBOL


def _order(side: Side, qty: int, kind: OrderKind = OrderKind.ENTRY) -> OrderRequest:
    return OrderRequest(
        intent_id="intent-test",
        symbol=_SYMBOL,
        side=side,
        kind=kind,
        qty=qty,
        risk_approved_by="test",
    )


def _fill(broker_order_no: str, qty: int, price_ticks: int) -> Fill:
    return Fill(
        broker_order_no=broker_order_no,
        symbol=_SYMBOL,
        qty=qty,
        price_ticks=price_ticks,
        ts_exchange=now_utc(),
        pending_matched=False,
    )


def _m1_bar(minute: int, close: int, *, low: int | None = None):
    """`SimBroker.on_bar()`용 1분봉 — 시장가 기준가(마지막 종가)를 세우거나 지정가 터치를
    만든다. 2026-09-17 15:00~15:25 구간을 시각까지 같은 모양으로 쓴다."""
    from datetime import datetime

    from messiah.core.messages import BarClosed, Horizon
    from messiah.core.timeutil import KST

    return BarClosed(
        symbol=_SYMBOL,
        horizon=Horizon.M1,
        bar_open_kst=datetime(2026, 9, 17, 15, minute, tzinfo=KST),
        o_ticks=close,
        h_ticks=max(close, low or close),
        l_ticks=min(close, low if low is not None else close),
        c_ticks=close,
        volume=1,
    )


# ---------------------------------------------------------------- 1. 버그 재현


@pytest.mark.asyncio
async def test_without_a_ledger_the_gateway_behaves_exactly_as_before():
    """**수정 전 상태를 먼저 고정한다.**

    2026-09-17까지 `OrderGateway`에는 장부가 없었고, 그래서 `run_g2_paper_trading.py`가
    `fills_countable=False`를 상수로 쓸 수밖에 없었다. 장부를 안 넘기면 그 동작이 지금도
    그대로여야 한다 — 백테스트·리플레이 등 장부가 필요 없는 호출자가 영향을 안 받는다는
    것이 이 변경의 안전 조건이다."""
    gateway = OrderGateway(_StubBroker())

    ack = await gateway.submit(_order(Side.LONG, 2))
    assert ack is not None
    out = await gateway.on_fill(_fill(ack.broker_order_no, 2, 100))

    assert gateway.reconciler is None  # 셀 수단이 없다 = 리포트는 "모름"을 내야 한다
    assert out.pending_matched is True  # 나머지 동작은 종전과 완전히 같다


# ---------------------------------------------------------------- 2. 방향은 주문에만 있다


@pytest.mark.asyncio
async def test_round_trip_realizes_pnl_in_ticks():
    """진입 2계약 @100 → 청산 2계약 @130 = +60틱. 2026-09-16 실거래와 같은 모양이다."""
    reconciler = PositionReconciler()
    gateway = OrderGateway(_StubBroker(), reconciler)

    entry = await gateway.submit(_order(Side.LONG, 2))
    assert entry is not None
    await gateway.on_fill(_fill(entry.broker_order_no, 2, 100))
    assert reconciler.positions[_SYMBOL].qty == 2
    assert reconciler.realized_pnl_ticks == 0.0  # 아직 닫은 게 없다

    exit_ = await gateway.submit(_order(Side.SHORT, 2, OrderKind.EXIT_FULL))
    assert exit_ is not None
    await gateway.on_fill(_fill(exit_.broker_order_no, 2, 130))

    assert reconciler.positions == {}
    assert reconciler.realized_pnl_ticks == pytest.approx(60.0)
    assert reconciler.n_fills == 2


@pytest.mark.asyncio
async def test_short_round_trip_has_the_opposite_sign():
    """SHORT은 내릴 때 이익이다 — 부호 곱을 빠뜨리면 이 테스트만 뒤집힌다."""
    reconciler = PositionReconciler()
    gateway = OrderGateway(_StubBroker(), reconciler)

    entry = await gateway.submit(_order(Side.SHORT, 1))
    assert entry is not None
    await gateway.on_fill(_fill(entry.broker_order_no, 1, 200))
    exit_ = await gateway.submit(_order(Side.LONG, 1, OrderKind.EXIT_FULL))
    assert exit_ is not None
    await gateway.on_fill(_fill(exit_.broker_order_no, 1, 180))

    assert reconciler.realized_pnl_ticks == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_scaling_in_keeps_the_original_entry_in_the_average():
    """물타기는 평균단가를 **가중평균**으로 옮긴다 — 덮어쓰면 원래 진입가가 사라지고
    그 상태의 청산 손익은 마지막 진입가 기준이 된다(2026-08-23 `SimBroker._apply()`가
    고친 결함, 같은 규칙을 `position_math`가 한 곳에서 지킨다)."""
    reconciler = PositionReconciler()
    gateway = OrderGateway(_StubBroker(), reconciler)

    for qty, price in ((2, 100), (2, 140)):
        ack = await gateway.submit(_order(Side.LONG, qty))
        assert ack is not None
        await gateway.on_fill(_fill(ack.broker_order_no, qty, price))

    assert reconciler.positions[_SYMBOL].qty == 4
    assert reconciler.positions[_SYMBOL].avg_price_ticks == 120


@pytest.mark.asyncio
async def test_an_unmatched_fill_never_moves_the_book():
    """**방향을 모르는 체결을 임의 방향으로 반영하면 그게 유령 포지션이다** (L1).

    게이트웨이는 이미 CRITICAL 정지를 걸지만, 장부 쪽에서도 포지션을 안 움직이고 세기만
    해야 `n_fills`를 믿어도 되는지가 리포트에서 판정된다."""
    reconciler = PositionReconciler()
    gateway = OrderGateway(_StubBroker(), reconciler)

    out = await gateway.on_fill(_fill("UNKNOWN-1", 3, 100))

    assert out.pending_matched is False
    assert gateway.halted is True
    assert reconciler.positions == {}  # 장부는 한 칸도 안 움직였다
    assert reconciler.n_fills == 1  # 그러나 **있었던 일로는 센다**
    assert reconciler.unattributed_fills == 1


# ---------------------------------------------------------------- 3. 대사


@pytest.mark.asyncio
async def test_reconcile_matches_when_the_books_agree():
    reconciler = PositionReconciler()
    broker = _StubBroker([BrokerPosition(symbol=_SYMBOL, qty=2, avg_price_ticks=100)])
    gateway = OrderGateway(broker, reconciler)

    ack = await gateway.submit(_order(Side.LONG, 2))
    assert ack is not None
    await gateway.on_fill(_fill(ack.broker_order_no, 2, 100))

    result = await reconciler.reconcile(broker)

    assert result.matched is True
    assert result.mismatches == []


@pytest.mark.asyncio
async def test_reconcile_reports_a_lost_fill_event():
    """체결 이벤트가 하나 새면 로컬 장부가 브로커보다 **적다**. 이것이 잡으려는 사고다."""
    reconciler = PositionReconciler()
    broker = _StubBroker([BrokerPosition(symbol=_SYMBOL, qty=3, avg_price_ticks=100)])
    gateway = OrderGateway(broker, reconciler)

    ack = await gateway.submit(_order(Side.LONG, 1))
    assert ack is not None
    await gateway.on_fill(_fill(ack.broker_order_no, 1, 100))

    result = await reconciler.reconcile(broker)

    assert result.matched is False
    assert [(d.symbol, d.local_qty, d.broker_qty) for d in result.mismatches] == [(_SYMBOL, 1, 3)]


@pytest.mark.asyncio
async def test_a_broker_lookup_failure_is_not_a_mismatch():
    """조회 실패를 삼켜 `matched=False`로 돌려주면 "해 보니 틀렸다"와 구별이 사라진다 —
    없는 사고를 만드는 쪽이다. 예외는 호출자에게 그대로 올라간다."""
    reconciler = PositionReconciler()
    broker = _StubBroker()
    broker.fail_positions = True

    with pytest.raises(RuntimeError):
        await reconciler.reconcile(broker)

    assert reconciler.last_reconciliation is None
    assert reconciler.summary()["reconciled"] is None  # False가 아니라 **미실시**


# ---------------------------------------------------------------- 4. 못 재는 것


def test_unrealized_pnl_is_none_when_the_close_is_unknown():
    """평가손익을 못 재면 0.0이 아니라 None이다(L18) — 0.0은 "평가손익이 0이었다"는
    전혀 다른 사실이다."""
    reconciler = PositionReconciler()
    reconciler.record_fill(_fill("B0001", 2, 100), _order(Side.LONG, 2))

    assert reconciler.unrealized_pnl_ticks({}) is None
    assert reconciler.unrealized_pnl_ticks({_SYMBOL: 130}) == pytest.approx(60.0)


def test_no_position_means_a_known_zero_not_an_unknown():
    """포지션이 없으면 평가손익은 **아는 0**이다 — 여기서 None을 내면 L18을 반대로 쓰는 것."""
    assert PositionReconciler().unrealized_pnl_ticks({}) == 0.0


def test_summary_carries_the_unit_next_to_the_value():
    """틱을 원으로 오독하는 것을 막는 유일한 구조적 장치가 `pnl_unit`이 값 옆에 있는 것이다."""
    reconciler = PositionReconciler()
    reconciler.record_fill(_fill("B0001", 1, 100), _order(Side.LONG, 1))

    summary = reconciler.summary(last_close_ticks={_SYMBOL: 110})

    assert summary["pnl_unit"] == "ticks"
    assert summary["n_fills"] == 1
    assert summary["unrealized_pnl_ticks"] == pytest.approx(10.0)
    assert summary["open_positions"] == {_SYMBOL: 1}


# --------------------------------------- 5. 진짜 브로커와 맞춰 본다 (교차검증)


@pytest.mark.asyncio
async def test_a_market_order_fill_reaches_the_gateway_at_all():
    """**2026-09-17 실거래를 그대로 재현한다** — 그날 15:00:01 진입 1계약과 15:25:00 EOD
    강제청산 1계약은 둘 다 시장가였고, 그날 G2 로그에 `FillMatched`·`FillUnmatched`가
    **0건**이다. `SimBroker._fill_market()`이 만든 `Fill`을 아무에게도 안 주고 버렸기
    때문이다(09-16 첫 실거래 4계약도 같다).

    수정 전이라면 이 테스트는 `n_fills == 0`이고 pending 한 건이 영영 남는다."""
    from messiah.broker.simulator.adapter import SimBroker

    broker = SimBroker(cash=50_000_000, slippage_ticks=0)
    await broker.connect()
    reconciler = PositionReconciler()
    gateway = OrderGateway(broker, reconciler)
    broker.on_bar(_m1_bar(0, 100))  # 시장가 기준가(마지막 종가)를 만든다

    ack = await gateway.submit(_order(Side.LONG, 1))

    assert ack is not None
    assert reconciler.n_fills == 1, "시장가 체결이 게이트웨이를 지나야 셀 수 있다"
    assert reconciler.unattributed_fills == 0, "자기 주문이 미매칭으로 잡히면 안 된다"
    assert gateway.halted is False, "rekey 전에 on_fill을 부르면 여기서 스스로 정지한다"
    assert gateway._pending.snapshot() == {}, "pending 누수 — 09-17에 두 건이 이렇게 남았다"


@pytest.mark.asyncio
async def test_the_two_ledgers_agree_on_a_full_market_round_trip():
    """**두 장부가 같은 답을 내야 대사가 의미를 갖는다.**

    `SimBroker`는 자기 체결로 `realized_pnl_ticks`를 쌓고(2026-08-23), 이 장부는 게이트웨이가
    본 `Fill`만으로 독립적으로 쌓는다. 규칙은 `position_math`에 한 벌만 있으므로 두 값이
    갈리면 그것은 **사건의 불일치**(체결이 새거나 겹침)다.

    09-17과 같은 모양 — 시장가 진입 1계약 @100 → EOD 강제청산 @130 = +30틱."""
    from messiah.broker.simulator.adapter import SimBroker

    broker = SimBroker(cash=50_000_000, slippage_ticks=0)
    await broker.connect()
    reconciler = PositionReconciler()
    gateway = OrderGateway(broker, reconciler)

    broker.on_bar(_m1_bar(0, 100))
    await gateway.submit(_order(Side.LONG, 1))
    broker.on_bar(_m1_bar(25, 130))
    await gateway.submit(_order(Side.SHORT, 1, OrderKind.EMERGENCY))

    result = await reconciler.reconcile(broker)

    assert result.matched is True
    assert reconciler.n_fills == 2
    assert reconciler.realized_pnl_ticks == pytest.approx(30.0)
    assert reconciler.realized_pnl_ticks == pytest.approx(broker.realized_pnl_ticks)
    assert reconciler.summary()["reconciled"] is True


@pytest.mark.asyncio
async def test_a_limit_fill_still_arrives_the_old_way():
    """지정가는 `on_bar()`가 돌려주고 `LiveSimBrokerFeed`가 게이트웨이로 넘긴다 — 즉시
    체결 경로를 새로 열면서 이 경로가 깨지지 않았는지 같이 본다."""
    from messiah.broker.simulator.adapter import SimBroker

    broker = SimBroker(cash=50_000_000, slippage_ticks=0)
    await broker.connect()
    reconciler = PositionReconciler()
    gateway = OrderGateway(broker, reconciler)

    broker.on_bar(_m1_bar(0, 100))
    ack = await gateway.submit(
        OrderRequest(
            intent_id="intent-test",
            symbol=_SYMBOL,
            side=Side.LONG,
            kind=OrderKind.ENTRY,
            qty=1,
            limit_price_ticks=98,
            risk_approved_by="test",
        )
    )
    assert ack is not None
    assert reconciler.n_fills == 0, "지정가는 제출 시점에 안 붙는다"

    for fill in broker.on_bar(_m1_bar(1, 99, low=97)):  # 저가가 지정가를 스친다
        await gateway.on_fill(fill)

    assert reconciler.n_fills == 1
    assert reconciler.positions[_SYMBOL].avg_price_ticks == 98


# ---------------------------------------------------------------- R10 공급 (2026-09-22 F-120)
#
# `RiskEngine.record_trade_result()`는 2026-07-27부터 있었으나 호출자가 없었다. R10이 필요한
# 것은 누계가 아니라 **닫힌 거래의 순서열**이고, 그 축을 여기서 고정한다.


@pytest.mark.asyncio
async def test_closed_trades_are_recorded_one_per_close():
    from messiah.broker.simulator.adapter import SimBroker

    broker = SimBroker(cash=50_000_000, slippage_ticks=0)
    await broker.connect()
    reconciler = PositionReconciler()
    gateway = OrderGateway(broker, reconciler)

    broker.on_bar(_m1_bar(0, 100))
    await gateway.submit(_order(Side.LONG, 1))
    assert reconciler.drain_closed_trades() == [], "진입은 닫은 것이 아니다"

    broker.on_bar(_m1_bar(1, 90))
    await gateway.submit(_order(Side.SHORT, 1, OrderKind.EXIT_FULL))

    assert reconciler.drain_closed_trades() == [-10.0]


@pytest.mark.asyncio
async def test_draining_empties_so_the_same_close_is_never_fed_twice():
    """읽은 쪽이 비운다 — 안 그러면 손실 하나가 매 사이클 스트릭을 늘린다."""
    from messiah.broker.simulator.adapter import SimBroker

    broker = SimBroker(cash=50_000_000, slippage_ticks=0)
    await broker.connect()
    reconciler = PositionReconciler()
    gateway = OrderGateway(broker, reconciler)

    broker.on_bar(_m1_bar(0, 100))
    await gateway.submit(_order(Side.LONG, 1))
    broker.on_bar(_m1_bar(1, 90))
    await gateway.submit(_order(Side.SHORT, 1, OrderKind.EXIT_FULL))

    assert reconciler.drain_closed_trades() == [-10.0]
    assert reconciler.drain_closed_trades() == []


@pytest.mark.asyncio
async def test_a_breakeven_close_is_still_a_closed_trade():
    """**핵심** — 본전 청산의 실현손익은 0.0이고, 그건 「닫은 게 없다」와 같은 숫자다.

    R10은 그 둘을 반드시 갈라야 한다: 본전 청산은 연속손실 스트릭을 끊고, 미청산은
    아무 일도 아니다. `position_math.closes_position()`이 그 구분을 하는 유일한 자리다.
    """
    from messiah.broker.simulator.adapter import SimBroker

    broker = SimBroker(cash=50_000_000, slippage_ticks=0)
    await broker.connect()
    reconciler = PositionReconciler()
    gateway = OrderGateway(broker, reconciler)

    broker.on_bar(_m1_bar(0, 100))
    await gateway.submit(_order(Side.LONG, 1))
    broker.on_bar(_m1_bar(1, 100))  # 같은 가격에 되판다
    await gateway.submit(_order(Side.SHORT, 1, OrderKind.EXIT_FULL))

    assert reconciler.drain_closed_trades() == [0.0]


def test_closes_position_agrees_with_apply_fill():
    """술어와 계산이 **같은 분기**를 본다 — 따로 적혀 있으면 조용히 어긋난다."""
    from messiah.execution.position_math import PositionState, apply_fill, closes_position

    cases = [(0, 1), (0, -1), (2, 1), (2, -1), (2, -5), (-2, -1), (-2, 1), (-2, 5)]
    for q0, signed in cases:
        current = PositionState(qty=q0, avg_price_ticks=100) if q0 else None
        _state, realized = apply_fill(current, signed_qty=signed, price_ticks=110)
        predicted = closes_position(current_qty=q0, signed_qty=signed)
        if not predicted:
            assert realized == 0.0, f"안 닫았다고 했는데 실현이 났다: {q0=} {signed=}"
        else:
            assert realized != 0.0, f"닫았다고 했는데 실현이 0이다: {q0=} {signed=}"
