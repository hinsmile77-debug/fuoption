from datetime import datetime

from messiah.broker.simulator.adapter import SimBroker
from messiah.core.messages import BarClosed, Horizon, OrderKind, OrderRequest, Side
from messiah.core.timeutil import KST
from messiah.execution.order_gateway import OrderGateway
from messiah.simulator.engine import LiveSimBrokerFeed
from messiah.simulator.inprocess_bus import InProcessBus

_SYMBOL = "A05608"


def _bar(minute: int, o=100, h=105, lo=95, c=102, horizon=Horizon.M1, symbol=_SYMBOL) -> BarClosed:
    return BarClosed(
        symbol=symbol,
        horizon=horizon,
        bar_open_kst=datetime(2026, 7, 27, 9, minute, tzinfo=KST),
        o_ticks=o,
        h_ticks=h,
        l_ticks=lo,
        c_ticks=c,
        volume=10,
    )


def _feed() -> tuple[LiveSimBrokerFeed, SimBroker, OrderGateway, InProcessBus]:
    broker = SimBroker()
    gateway = OrderGateway(broker)
    bus = InProcessBus()
    feed = LiveSimBrokerFeed(_SYMBOL, broker, gateway, bus)
    return feed, broker, gateway, bus


async def test_ignores_other_symbol_bars():
    feed, broker, _gateway, _bus = _feed()
    await feed.handle_bar(_bar(30, symbol="OTHER"))
    # 시세를 못 봤으니 시장가 제출은 거부돼야 정상 — 심볼이 실제로 무시됐다는 간접 증거.
    result = await broker.submit(
        OrderRequest(intent_id="i", symbol=_SYMBOL, kind=OrderKind.ENTRY, side=Side.LONG, qty=1)
    )
    assert result.ok is False


async def test_ignores_non_m1_bars():
    feed, broker, _gateway, _bus = _feed()
    await feed.handle_bar(_bar(30, horizon=Horizon.M5))
    result = await broker.submit(
        OrderRequest(intent_id="i", symbol=_SYMBOL, kind=OrderKind.ENTRY, side=Side.LONG, qty=1)
    )
    assert result.ok is False  # M5는 무시되므로 broker 시계가 여전히 안 진행됨


async def test_m1_bar_drives_fills_into_gateway():
    feed, broker, gateway, _bus = _feed()
    await feed.handle_bar(_bar(30, lo=99, h=105, c=102))  # 기준가 확보

    req = OrderRequest(
        intent_id="intent-1",
        symbol=_SYMBOL,
        kind=OrderKind.ENTRY,
        side=Side.LONG,
        qty=2,
        limit_price_ticks=98,
        ttl_ms=120_000,
    )
    ack = await gateway.submit(req)
    assert ack is not None

    await feed.handle_bar(_bar(31, lo=97, h=101, c=99))  # 저가 97이 지정가 98 터치

    positions = await broker.positions()
    assert len(positions) == 1
    assert positions[0].qty == 2
    assert gateway.halted is False


async def test_run_forever_subscribes_only_to_m1_topic_for_its_symbol():
    feed, broker, _gateway, bus = _feed()
    await feed.run_forever()

    await bus.publish(f"bar.{Horizon.M1.value}.{_SYMBOL}", _bar(30, lo=99, h=105, c=102))
    await bus.publish(f"bar.{Horizon.M5.value}.{_SYMBOL}", _bar(30, horizon=Horizon.M5))
    await bus.publish(f"bar.{Horizon.M1.value}.OTHER", _bar(30, symbol="OTHER"))

    result = await broker.submit(
        OrderRequest(intent_id="i", symbol=_SYMBOL, kind=OrderKind.ENTRY, side=Side.LONG, qty=1)
    )
    assert result.ok is True  # M1 본인 심볼 봉만 반영돼 시세가 잡혔어야 함
