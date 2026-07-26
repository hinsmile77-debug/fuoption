from datetime import datetime

from messiah.broker.simulator.adapter import SimBroker
from messiah.core.messages import BarClosed, Horizon, OrderKind, OrderRequest, Side
from messiah.core.timeutil import KST
from messiah.execution.order_gateway import OrderGateway
from messiah.simulator.engine import DigitalTwinEngine
from messiah.simulator.inprocess_bus import InProcessBus

_SYMBOL = "A05608"


def _bar(minute: int, o=100, h=105, lo=95, c=102, horizon=Horizon.M1) -> BarClosed:
    return BarClosed(
        symbol=_SYMBOL,
        horizon=horizon,
        bar_open_kst=datetime(2026, 7, 27, 9, minute, tzinfo=KST),
        o_ticks=o,
        h_ticks=h,
        l_ticks=lo,
        c_ticks=c,
        volume=10,
    )


def _engine() -> tuple[DigitalTwinEngine, SimBroker, OrderGateway, InProcessBus]:
    broker = SimBroker()
    gateway = OrderGateway(broker)
    bus = InProcessBus()
    engine = DigitalTwinEngine(symbol=_SYMBOL, broker=broker, gateway=gateway, bus=bus)
    return engine, broker, gateway, bus


async def test_bars_are_published_to_bus_for_downstream_consumers():
    engine, _broker, _gateway, bus = _engine()
    received: list[BarClosed] = []

    async def handler(bar: BarClosed) -> None:
        received.append(bar)

    await bus.subscribe(["bar.1m.A05608"], handler)
    await engine.run([_bar(30)])

    assert len(received) == 1
    assert received[0].bar_open_kst.minute == 30


async def test_bars_for_other_symbols_are_ignored():
    engine, _broker, _gateway, bus = _engine()
    received: list[BarClosed] = []
    await bus.subscribe(["bar.1m.OTHER"], lambda b: received.append(b))  # type: ignore[arg-type]

    other_symbol_bar = _bar(30).model_copy(update={"symbol": "OTHER"})
    await engine.run([other_symbol_bar])

    assert received == []


async def test_limit_fill_flows_through_gateway_into_position():
    engine, broker, gateway, _bus = _engine()

    # 09:30봉으로 시계를 진행시켜 시장데이터 기준가를 만든다.
    await engine.run([_bar(30, lo=99, h=105, c=102)])

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

    # 09:31봉 저가 97이 지정가 98을 터치 — engine.run()이 체결→gateway.on_fill()까지 처리.
    await engine.run([_bar(31, lo=97, h=101, c=99)])

    positions = await broker.positions()
    assert len(positions) == 1
    assert positions[0].qty == 2
    assert positions[0].avg_price_ticks == 98
    assert gateway.halted is False


async def test_unmatched_fill_halts_gateway():
    """DigitalTwinEngine이 broker.on_bar()가 만든 Fill을 빠짐없이 gateway.on_fill()로
    넘긴다는 걸, pending 매칭이 없는(=OrderGateway.submit()을 거치지 않은) 주문의 체결로
    확인한다 — OrderGateway의 L1 안전장치(미매칭 체결 CRITICAL 정지)가 재생 경로에서도
    그대로 작동해야 한다."""
    engine, broker, gateway, _bus = _engine()
    await engine.run([_bar(30, lo=99, h=105, c=102)])  # 시계 진행(기준가 확보)

    # 게이트웨이를 우회해 브로커에 직접 지정가 제출 — pending 원자 등록이 없다.
    await broker.submit(
        OrderRequest(
            intent_id="intent-2",
            symbol=_SYMBOL,
            kind=OrderKind.ENTRY,
            side=Side.LONG,
            qty=1,
            limit_price_ticks=98,
        )
    )

    assert gateway.halted is False
    await engine.run([_bar(31, lo=97, h=101, c=99)])  # 터치 → Fill 발생 → gateway.on_fill()

    assert gateway.halted is True
