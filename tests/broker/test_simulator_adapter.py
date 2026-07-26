from datetime import datetime

from messiah.broker.base import BrokerPosition
from messiah.broker.simulator.adapter import SimBroker
from messiah.core.messages import BarClosed, Horizon, OrderKind, OrderRequest, Side
from messiah.core.timeutil import KST


def _bar(minute: int, o=100, h=105, lo=95, c=102, horizon=Horizon.M1) -> BarClosed:
    return BarClosed(
        symbol="A05608",
        horizon=horizon,
        bar_open_kst=datetime(2026, 7, 27, 9, minute, tzinfo=KST),
        o_ticks=o,
        h_ticks=h,
        l_ticks=lo,
        c_ticks=c,
        volume=10,
    )


def _order(**overrides) -> OrderRequest:
    defaults = dict(
        intent_id="intent-1",
        symbol="A05608",
        kind=OrderKind.ENTRY,
        side=Side.LONG,
        qty=1,
        limit_price_ticks=100,
    )
    defaults.update(overrides)
    return OrderRequest(**defaults)


async def test_submit_before_any_bar_is_rejected():
    broker = SimBroker()
    result = await broker.submit(_order())
    assert result.ok is False
    assert "market data" in result.error


async def test_market_order_rejected_without_reference_price():
    broker = SimBroker()
    broker.on_bar(_bar(30, horizon=Horizon.M5))  # 1분봉이 아니므로 기준가 갱신 안 됨
    result = await broker.submit(_order(limit_price_ticks=None))
    assert result.ok is False


async def test_market_order_fills_immediately_at_last_close_plus_slippage():
    broker = SimBroker(slippage_ticks=2)
    broker.on_bar(_bar(30, c=102))

    result = await broker.submit(_order(limit_price_ticks=None, side=Side.LONG))
    assert result.ok is True

    positions = await broker.positions()
    assert positions == [BrokerPosition(symbol="A05608", qty=1, avg_price_ticks=104)]


async def test_limit_buy_fills_when_bar_low_touches_limit():
    broker = SimBroker()
    broker.on_bar(_bar(30, lo=99, h=105, c=102))  # 시계 기동

    result = await broker.submit(_order(side=Side.LONG, limit_price_ticks=98))
    assert result.ok is True
    order_no = result.broker_order_no

    fills = broker.on_bar(_bar(31, lo=97, h=101, c=99))  # 저가 97 <= 지정가 98 → 터치
    assert len(fills) == 1
    fill = fills[0]
    assert fill.broker_order_no == order_no
    assert fill.price_ticks == 98  # 지정가 그대로(보수적 가정)
    assert fill.qty == 1

    positions = await broker.positions()
    assert positions[0].qty == 1


async def test_limit_sell_fills_when_bar_high_touches_limit():
    broker = SimBroker()
    broker.on_bar(_bar(30, lo=99, h=105, c=102))

    result = await broker.submit(_order(side=Side.SHORT, limit_price_ticks=110))
    fills = broker.on_bar(_bar(31, lo=100, h=111, c=105))
    assert len(fills) == 1
    assert fills[0].price_ticks == 110

    positions = await broker.positions()
    assert positions[0].qty == -1
    assert result.ok is True


async def test_limit_order_not_touched_stays_pending_until_ttl_expires():
    broker = SimBroker()
    broker.on_bar(_bar(30, lo=99, h=105, c=102))

    result = await broker.submit(
        _order(side=Side.LONG, limit_price_ticks=50, ttl_ms=60_000)  # 1분 TTL, 절대 안 닿는 가격
    )
    order_no = result.broker_order_no

    fills = broker.on_bar(_bar(31, lo=90, h=100, c=95))  # 아직 TTL 안 지남(1분 경과)
    assert fills == []

    fills = broker.on_bar(_bar(32, lo=90, h=100, c=95))  # 2분 경과 — TTL(1분) 초과
    assert fills == []

    positions = await broker.positions()
    assert positions == []
    cancel_result = await broker.cancel(order_no)
    assert cancel_result is False  # 이미 만료돼 pending에서 제거됨


async def test_cancel_removes_pending_order_before_touch():
    broker = SimBroker()
    broker.on_bar(_bar(30, lo=99, h=105, c=102))
    result = await broker.submit(_order(side=Side.LONG, limit_price_ticks=98))

    cancelled = await broker.cancel(result.broker_order_no)
    assert cancelled is True

    fills = broker.on_bar(_bar(31, lo=90, h=100, c=95))  # 취소됐으니 터치해도 체결 안 됨
    assert fills == []


async def test_non_m1_bars_are_ignored_for_fill_checking():
    broker = SimBroker()
    broker.on_bar(_bar(30, lo=99, h=105, c=102))
    result = await broker.submit(_order(side=Side.LONG, limit_price_ticks=98))

    # 5분봉이 저가 90으로 지정가를 스쳤어도 1분봉이 아니므로 체결 판정하지 않는다.
    fills = broker.on_bar(_bar(35, lo=90, h=100, c=95, horizon=Horizon.M5))
    assert fills == []

    pending_positions = await broker.positions()
    assert pending_positions == []
    assert result.ok is True


async def test_exit_full_closes_existing_long_position():
    broker = SimBroker()
    broker.on_bar(_bar(30, c=100))
    await broker.submit(_order(side=Side.LONG, limit_price_ticks=None))  # market entry

    broker.on_bar(_bar(31, c=100))
    result = await broker.submit(
        _order(kind=OrderKind.EXIT_FULL, side=Side.SHORT, limit_price_ticks=None)
    )
    assert result.ok is True

    positions = await broker.positions()
    assert positions == []


async def test_qty_must_be_positive():
    broker = SimBroker()
    broker.on_bar(_bar(30, c=100))
    result = await broker.submit(_order(qty=0))
    assert result.ok is False
