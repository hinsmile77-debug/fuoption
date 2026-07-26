import math
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from messiah.broker.simulator.adapter import SimBroker
from messiah.core.messages import BarClosed, FuturesView, Horizon, Regime, Side
from messiah.core.timeutil import KST
from messiah.execution.order_gateway import OrderGateway
from messiah.risk.kill_switch import KillSwitch, KillSwitchConfig
from messiah.simulator.inprocess_bus import InProcessBus
from messiah.strategy.pipeline import TradingPipeline

_SYMBOL = "TEST"
_START = datetime(2026, 7, 30, 9, 0, tzinfo=KST)


def _m1_bars(n: int) -> list[BarClosed]:
    out = []
    price = 100.0
    for i in range(n):
        price += math.sin(i / 4) * 2 + ((i * 53) % 7 - 3) * 0.2
        price = max(price, 10.0)
        out.append(
            BarClosed(
                symbol=_SYMBOL,
                horizon=Horizon.M1,
                bar_open_kst=_START + timedelta(minutes=i),
                o_ticks=round(price),
                h_ticks=round(price) + 2,
                l_ticks=round(price) - 2,
                c_ticks=round(price),
                volume=100 + i,
            )
        )
    return out


def _view(*, score=0.5, agg_p_up=0.8, agg_p_down=0.1, uncertainty=0.0, dispersion=0.0, ts=None):
    kwargs = dict(
        symbol=_SYMBOL,
        score=score,
        agg_p_up=agg_p_up,
        agg_p_down=agg_p_down,
        uncertainty=uncertainty,
        dispersion=dispersion,
        regime=Regime.TREND_UP,
        n_experts=2,
        model_versions=["v1"],
    )
    if ts is not None:
        kwargs["ts_utc"] = ts
    return FuturesView(**kwargs)


async def _make_pipeline(**overrides):
    bus = InProcessBus()
    broker = SimBroker(cash=50_000_000)
    await broker.connect()
    gateway = OrderGateway(broker)
    pipeline = TradingPipeline(_SYMBOL, broker, gateway, bus, **overrides)
    return bus, broker, gateway, pipeline


async def _warm_up(pipeline, broker, n=20):
    bars = _m1_bars(n)
    for bar in bars:
        broker.on_bar(bar)
        await pipeline.handle_bar(bar)
    return bars


@pytest.mark.asyncio
async def test_no_trade_view_publishes_intent_but_submits_no_order():
    bus, broker, gateway, pipeline = await _make_pipeline()
    intents = []

    async def collector(msg):
        intents.append(msg)

    await bus.subscribe(["decision.intent"], collector)
    await _warm_up(pipeline, broker)

    await pipeline.handle_futures_view(_view(score=0.05, agg_p_up=0.5, agg_p_down=0.5))

    assert len(intents) == 1
    assert intents[0].side == Side.NO_TRADE
    positions = await broker.positions()
    assert positions == []


@pytest.mark.asyncio
async def test_strong_long_view_submits_order_and_moves_position():
    bus, broker, gateway, pipeline = await _make_pipeline()
    await _warm_up(pipeline, broker)

    await pipeline.handle_futures_view(_view(score=0.5, agg_p_up=0.9, agg_p_down=0.05))

    positions = await broker.positions()
    assert len(positions) == 1
    assert positions[0].qty > 0


@pytest.mark.asyncio
async def test_without_bar_warmup_no_order_submitted():
    bus, broker, gateway, pipeline = await _make_pipeline()
    # 봉을 하나도 안 먹였으므로 ATR 계산 불가 — 조용히 대기해야 함
    await pipeline.handle_futures_view(_view(score=0.9, agg_p_up=0.95, agg_p_down=0.02))
    positions = await broker.positions()
    assert positions == []


@pytest.mark.asyncio
async def test_kill_switch_liquidates_open_position_and_halts_gateway():
    bus, broker, gateway, pipeline = await _make_pipeline(
        kill_switch=KillSwitch(InProcessBus(), config=KillSwitchConfig(daily_loss_limit_pct=2.0))
    )
    await _warm_up(pipeline, broker)
    await pipeline.start_day()

    # 먼저 정상 진입시켜 포지션을 만든다
    await pipeline.handle_futures_view(_view(score=0.5, agg_p_up=0.9, agg_p_down=0.05))
    positions_before = await broker.positions()
    assert len(positions_before) == 1

    # 계좌 잔고를 강제로 깎아 일일손실 한도(R2)를 넘긴 뒤 재평가를 유도
    broker._cash = Decimal("48000000")  # noqa: SLF001 — 테스트 전용 잔고 조작

    await pipeline.handle_futures_view(_view(score=0.5, agg_p_up=0.9, agg_p_down=0.05))

    assert gateway.halted is True


@pytest.mark.asyncio
async def test_high_dispersion_blocks_entry_via_decision_engine():
    bus, broker, gateway, pipeline = await _make_pipeline()
    await _warm_up(pipeline, broker)

    await pipeline.handle_futures_view(_view(score=0.5, dispersion=0.9))
    positions = await broker.positions()
    assert positions == []


@pytest.mark.asyncio
async def test_stale_futures_view_rejected_by_risk_engine():
    bus, broker, gateway, pipeline = await _make_pipeline()
    bars = await _warm_up(pipeline, broker)
    stale_ts = bars[-1].bar_open_kst + timedelta(minutes=5)  # 마지막 봉 확정보다 한참 뒤

    await pipeline.handle_futures_view(_view(score=0.5, agg_p_up=0.9, agg_p_down=0.05, ts=stale_ts))
    positions = await broker.positions()
    assert positions == []
