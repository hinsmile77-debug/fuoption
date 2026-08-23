"""거래를 세지 않으면 「본전」과 「거래 없음」이 같은 값이 된다 — 2026-08-23.

2026-08-21까지 실전 **17거래일 연속 주문 0건**이었는데 어느 축도 그것을 세지 않았다.
`g2_daily_returns.jsonl`에는 `"return": 0.0`이 열일곱 줄 있었고, 그 0은 "거래해서
본전"이 아니라 "거래를 안 했다"였다. 자본 곡선만 보는 눈으로는 둘이 구별되지 않는다.

백테스트도 같은 눈이었다. 이 파일은 그 눈을 고친 것이 되돌려지지 않게 못 박는다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from messiah.broker.simulator.adapter import SimBroker
from messiah.core.messages import BarClosed, Horizon, OrderKind, OrderRequest, Side

_KST = timezone(timedelta(hours=9))


def _bar(minute: int, close: int) -> BarClosed:
    opened = datetime(2026, 8, 21, 9, 0, tzinfo=_KST) + timedelta(minutes=minute)
    return BarClosed(
        symbol="A05609",
        horizon=Horizon.M1,
        bar_open_kst=opened,
        o_ticks=close,
        h_ticks=close + 5,
        l_ticks=close - 5,
        c_ticks=close,
        volume=100,
    )


def _order(side: Side, kind: OrderKind, *, limit: int | None = None, ttl_ms: int = 60_000):
    return OrderRequest(
        intent_id="test-intent",
        symbol="A05609",
        side=side,
        qty=1,
        kind=kind,
        ttl_ms=ttl_ms,
        limit_price_ticks=limit,
    )


def test_a_quiet_broker_and_a_busy_one_are_not_the_same_zero():
    """**이 테스트가 요점이다.** 두 브로커 모두 자본 변화 0인데 하나는 거래했고 하나는
    안 했다. 자본만 보면 구별할 수 없고, 계수기가 있어야 갈린다."""

    async def run() -> tuple[SimBroker, SimBroker]:
        quiet = SimBroker(cash=50_000_000)
        await quiet.connect()
        quiet.on_bar(_bar(0, 34_000))

        busy = SimBroker(cash=50_000_000)
        await busy.connect()
        busy.on_bar(_bar(0, 34_000))
        await busy.submit(_order(Side.LONG, OrderKind.ENTRY))
        busy.on_bar(_bar(1, 36_000))
        await busy.submit(_order(Side.SHORT, OrderKind.EXIT_FULL))
        busy.on_bar(_bar(2, 36_000))
        return quiet, busy

    quiet, busy = asyncio.run(run())

    async def equity(broker: SimBroker) -> int:
        return int((await broker.account()).total_equity)

    # 자본은 둘 다 그대로다 — 손익을 계산하지 않기 때문이다(아래 테스트 참고).
    assert asyncio.run(equity(quiet)) == asyncio.run(equity(busy)) == 50_000_000

    # 그런데 한쪽은 두 번 거래했다. **그 사실이 어딘가에는 남아야 한다.**
    assert quiet.n_accepted_orders == 0
    assert quiet.n_fills == 0
    assert busy.n_accepted_orders == 2
    assert busy.n_fills == 2


def test_pnl_is_computed_in_ticks_not_won():
    """**2026-08-23 이전에는 2,000틱을 먹고 청산해도 0원이었다.**

    `_apply()`가 포지션만 갱신하고 `_cash`를 안 건드렸기 때문이다. 그 0이
    `backtest/harness.py`를 타고 `Validator.validate_performance()`에 들어가면
    `max_drawdown`과 `negative_window_ratio`가 **둘 다 PASS**로 나왔다 — 아무것도 안 잰
    계기가 초록 도장 두 개를 찍는 형태다(마흐디 L18 · 2026-08-21 F-14와 같은 계열).

    단위가 **틱**인 것은 타협이 아니라 규율이다: 원으로 바꾸려면 계약 승수(원/지수포인트)가
    필요한데 그 값이 이 저장소 어디에도 없다. 없는 상수를 코드가 지어내는 것이 R4가
    금지하는 바로 그것이라, 비용 모델이 이미 쓰는 단위에 맞췄다.
    """
    assert SimBroker.computes_pnl is True
    assert (
        SimBroker.pnl_unit == "ticks"
    ), "원으로 바꾸려면 계약 승수를 정본에 먼저 넣어야 한다 — 코드가 지어내면 안 된다"

    async def run() -> SimBroker:
        broker = SimBroker(cash=50_000_000, slippage_ticks=0)
        await broker.connect()
        broker.on_bar(_bar(0, 34_000))
        await broker.submit(_order(Side.LONG, OrderKind.ENTRY))
        broker.on_bar(_bar(1, 36_000))  # +2,000틱
        await broker.submit(_order(Side.SHORT, OrderKind.EXIT_FULL))
        broker.on_bar(_bar(2, 36_000))
        return broker

    broker = asyncio.run(run())
    assert broker.realized_pnl_ticks == 2_000.0

    # 원은 여전히 안 건드린다 — 원과 틱을 한 필드에 섞는 것이 더 나쁜 거짓말이다.
    assert int(asyncio.run(broker.account()).total_equity) == 50_000_000


def test_a_short_that_falls_is_a_profit():
    """부호를 뒤집어 세면 SHORT의 이익이 손실로 기록된다."""

    async def run() -> SimBroker:
        broker = SimBroker(cash=50_000_000, slippage_ticks=0)
        await broker.connect()
        broker.on_bar(_bar(0, 34_000))
        await broker.submit(_order(Side.SHORT, OrderKind.ENTRY))
        broker.on_bar(_bar(1, 33_000))  # 내려갔다 = SHORT 이익
        await broker.submit(_order(Side.LONG, OrderKind.EXIT_FULL))
        broker.on_bar(_bar(2, 33_000))
        return broker

    assert asyncio.run(run()).realized_pnl_ticks == 1_000.0


def test_scaling_in_keeps_the_original_entry_price():
    """**평균단가를 덮어쓰면 원래 진입가가 사라진다** (2026-08-23 함께 고친 결함).

    종전 `_apply()`는 매 체결마다 `avg_price_ticks=price_ticks`로 통째로 덮었다. 같은
    방향으로 물타기한 뒤 청산하면 손익이 **마지막 진입가 기준**이 된다. 손익을 계산하지
    않던 동안에는 드러나지 않던 결함이다.
    """

    async def run() -> tuple[SimBroker, int]:
        broker = SimBroker(cash=50_000_000, slippage_ticks=0)
        await broker.connect()
        broker.on_bar(_bar(0, 34_000))
        await broker.submit(_order(Side.LONG, OrderKind.ENTRY))
        broker.on_bar(_bar(1, 36_000))
        await broker.submit(_order(Side.LONG, OrderKind.ENTRY))  # 물타기
        broker.on_bar(_bar(2, 36_000))
        avg = (await broker.positions())[0].avg_price_ticks
        return broker, avg

    broker, avg = asyncio.run(run())
    assert avg == 35_000, "34,000과 36,000의 수량가중평균"
    assert broker.realized_pnl_ticks == 0.0, "닫은 것이 없으면 실현도 없다"
    # 종가 36,000 기준 2계약 × 1,000틱
    assert broker.unrealized_pnl_ticks() == 2_000.0


def test_unrealized_pnl_is_none_when_it_cannot_be_priced():
    """창 끝에 포지션이 열린 채 끝났는데 종가를 모르면 **0이 아니라 None**이다(L18) —
    0으로 세면 그 창의 성과가 조용히 왜곡된다."""

    async def run() -> tuple[float | None, float | None]:
        broker = SimBroker(cash=50_000_000, slippage_ticks=0)
        await broker.connect()
        flat = broker.unrealized_pnl_ticks()  # 포지션 없음 → 0
        broker.on_bar(_bar(0, 34_000))
        await broker.submit(_order(Side.LONG, OrderKind.ENTRY))
        broker._last_close.clear()  # 종가 정보를 잃은 상태를 만든다
        return flat, broker.unrealized_pnl_ticks()

    flat, unpriced = asyncio.run(run())
    assert flat == 0.0, "포지션이 없으면 평가손익은 측정된 0이다"
    assert unpriced is None, "포지션이 있는데 값을 모르면 미측정이다"


def test_expired_orders_are_counted_apart_from_fills():
    """「주문은 냈는데 안 붙었다」와 「주문 자체가 없었다」는 둘 다 체결 0이지만 진단이
    정반대다 — 전자는 가격 배치 문제고 후자는 판단 계층 문제다."""

    async def run() -> SimBroker:
        broker = SimBroker(cash=50_000_000)
        await broker.connect()
        broker.on_bar(_bar(0, 34_000))
        # 저가(33,995)보다 한참 낮은 지정가 — 절대 안 붙는다.
        await broker.submit(_order(Side.LONG, OrderKind.ENTRY, limit=30_000, ttl_ms=60_000))
        broker.on_bar(_bar(1, 34_000))  # 아직 TTL 안 지남
        broker.on_bar(_bar(5, 34_000))  # TTL 경과
        return broker

    broker = asyncio.run(run())

    assert broker.n_accepted_orders == 1
    assert broker.n_fills == 0
    assert broker.n_expired_orders == 1
