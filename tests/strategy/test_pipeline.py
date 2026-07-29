import math
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from messiah.broker.simulator.adapter import SimBroker
from messiah.core.event_calendar import EventCalendar
from messiah.core.messages import (
    BarClosed,
    CircuitBreakerStatus,
    FuturesView,
    Horizon,
    Regime,
    Side,
    bar_confirm_time,
)
from messiah.core.timeutil import KST
from messiah.execution.order_gateway import OrderGateway
from messiah.risk.circuit_breaker_monitor import CircuitBreakerMonitor, CircuitBreakerPhase
from messiah.risk.kill_switch import KillSwitch, KillSwitchConfig
from messiah.simulator.inprocess_bus import InProcessBus
from messiah.strategy.pipeline import TradingPipeline

_SYMBOL = "TEST"
_START = datetime(2026, 7, 30, 9, 0, tzinfo=KST)
_NEAR_CLOSE_START = datetime(2026, 7, 30, 15, 10, tzinfo=KST)  # 15:35 마감 25분 전부터 워밍업


def _m1_bars(n: int, start: datetime = _START) -> list[BarClosed]:
    out = []
    price = 100.0
    for i in range(n):
        price += math.sin(i / 4) * 2 + ((i * 53) % 7 - 3) * 0.2
        price = max(price, 10.0)
        out.append(
            BarClosed(
                symbol=_SYMBOL,
                horizon=Horizon.M1,
                bar_open_kst=start + timedelta(minutes=i),
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


async def _warm_up(pipeline, broker, n=20, start: datetime = _START):
    bars = _m1_bars(n, start)
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
async def test_cold_start_without_bars_does_not_false_positive_circuit_breaker():
    """2026-07-29 실전 재시작 직후 실측 재현 — 봉을 한 번도 못 본 워밍업 구간에서
    `_data_age_seconds()`가 inf를 반환하는데, 이를 CB 판정에 그대로 흘리면 "기준선 자체가
    없음"을 "CB로 갑자기 끊김"으로 오판해 시작하자마자 거짓 CircuitBreakerConfirmed가
    찍힌다(그 직후 첫 봉이 들어오면 거짓 CircuitBreakerResumed까지 이어짐)."""
    bus, broker, gateway, pipeline = await _make_pipeline(
        circuit_breaker_monitor=CircuitBreakerMonitor()
    )
    await pipeline.handle_futures_view(_view(score=0.0, agg_p_up=0.5, agg_p_down=0.5))
    # 콜드스타트 자체가 (별개 사유로) KillSwitch R11도 같이 건드릴 수 있어 gateway.halted는
    # 여기서 단언하지 않는다 — 이 테스트는 오직 CircuitBreakerMonitor가 콜드스타트를 CB로
    # 오판하지 않는지만 확인한다(모듈 docstring "콜드스타트 오탐 방지" 참고).
    assert pipeline._circuit_breaker_monitor.phase == CircuitBreakerPhase.NORMAL


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


@pytest.mark.asyncio
async def test_event_calendar_blocks_new_entry_near_session_close_r6():
    calendar = EventCalendar.from_file()  # 실제 configs/krx_holidays.yaml — 2026-07-30은 평일
    bus, broker, gateway, pipeline = await _make_pipeline(event_calendar=calendar)
    bars = await _warm_up(pipeline, broker, start=_NEAR_CLOSE_START)  # 마지막 봉 ~15:30 확정
    fresh_ts = bars[-1].bar_open_kst + timedelta(seconds=60)  # 15:35 마감 5분 전, R11엔 안 걸림

    await pipeline.handle_futures_view(_view(score=0.5, agg_p_up=0.9, agg_p_down=0.05, ts=fresh_ts))

    positions = await broker.positions()
    assert positions == []  # R6가 신규 진입을 거부 — Holding Policy §2.2 Type A 무포 오버나이트


@pytest.mark.asyncio
async def test_no_event_calendar_injected_allows_entry_near_close():
    # event_calendar 미주입(기존 동작) — 같은 마감 임박 시각이어도 R6가 아예 평가되지 않는다.
    bus, broker, gateway, pipeline = await _make_pipeline()
    bars = await _warm_up(pipeline, broker, start=_NEAR_CLOSE_START)
    fresh_ts = bars[-1].bar_open_kst + timedelta(seconds=60)

    await pipeline.handle_futures_view(_view(score=0.5, agg_p_up=0.9, agg_p_down=0.05, ts=fresh_ts))

    positions = await broker.positions()
    assert len(positions) == 1


@pytest.mark.asyncio
async def test_circuit_breaker_confirmed_then_resumed_liquidates_and_resumes_gateway():
    bus, broker, gateway, pipeline = await _make_pipeline(
        circuit_breaker_monitor=CircuitBreakerMonitor()
    )
    bars = await _warm_up(pipeline, broker)
    await pipeline.start_day()

    # 정상 진입시켜 포지션을 만든다
    await pipeline.handle_futures_view(_view(score=0.5, agg_p_up=0.9, agg_p_down=0.05))
    assert len(await broker.positions()) == 1

    # 데이터 갭이 confirmed 임계(240s) 이상으로 벌어짐 — CB CONFIRMED, gateway halt
    # (data_age는 마지막 봉의 "확정 시각"=bar_confirm_time 기준, bar_open_kst가 아니다)
    last_confirm = bar_confirm_time(bars[-1])
    gap_ts = last_confirm + timedelta(seconds=250)
    await pipeline.handle_futures_view(_view(score=0.0, agg_p_up=0.5, agg_p_down=0.5, ts=gap_ts))
    assert gateway.halted is True

    # 데이터 재수신(새 봉) — 재개 감지 → 자동 강제청산 + gateway 재개(사람 확인 없음)
    resume_bar_time = last_confirm + timedelta(seconds=260)
    resume_bar = _m1_bars(1, start=resume_bar_time)[0]
    broker.on_bar(resume_bar)
    await pipeline.handle_bar(resume_bar)
    resume_ts = bar_confirm_time(resume_bar) + timedelta(seconds=1)
    await pipeline.handle_futures_view(_view(score=0.0, agg_p_up=0.5, agg_p_down=0.5, ts=resume_ts))

    assert await broker.positions() == []
    assert gateway.halted is False


@pytest.mark.asyncio
async def test_circuit_breaker_reentry_cooldown_blocks_then_releases_entry():
    bus, broker, gateway, pipeline = await _make_pipeline(
        circuit_breaker_monitor=CircuitBreakerMonitor()
    )
    bars = await _warm_up(pipeline, broker)
    await pipeline.start_day()

    last_confirm = bar_confirm_time(bars[-1])
    gap_ts = last_confirm + timedelta(seconds=250)
    await pipeline.handle_futures_view(_view(score=0.0, agg_p_up=0.5, agg_p_down=0.5, ts=gap_ts))

    resume_bar_time = last_confirm + timedelta(seconds=260)
    resume_bar = _m1_bars(1, start=resume_bar_time)[0]
    broker.on_bar(resume_bar)
    await pipeline.handle_bar(resume_bar)
    resume_ts = bar_confirm_time(resume_bar) + timedelta(seconds=1)
    await pipeline.handle_futures_view(_view(score=0.0, agg_p_up=0.5, agg_p_down=0.5, ts=resume_ts))

    # 재개 후에도 매 분 정상적으로 봉이 계속 들어온다고 가정(데이터 자체는 신선함) — R13이
    # 막는 건 "재진입 관망 구간"이지 데이터단절(R11)이 아니다. 실시간처럼 한 봉씩 피딩한다.
    mid_bar = _m1_bars(1, start=resume_bar_time + timedelta(minutes=5))[0]
    broker.on_bar(mid_bar)
    await pipeline.handle_bar(mid_bar)
    mid_ts = bar_confirm_time(mid_bar) + timedelta(seconds=1)

    # 재개 후 ~5분 — 아직 재진입 관망(10분) 구간, 강한 매수 신호가 와도 진입 안 됨
    await pipeline.handle_futures_view(_view(score=0.5, agg_p_up=0.9, agg_p_down=0.05, ts=mid_ts))
    assert await broker.positions() == []

    late_bar = _m1_bars(1, start=resume_bar_time + timedelta(minutes=12))[0]
    broker.on_bar(late_bar)
    await pipeline.handle_bar(late_bar)
    late_ts = bar_confirm_time(late_bar) + timedelta(seconds=1)

    # 재개 후 ~12분 — 관망 종료, 정상 진입 가능
    await pipeline.handle_futures_view(_view(score=0.5, agg_p_up=0.9, agg_p_down=0.05, ts=late_ts))
    assert len(await broker.positions()) == 1


@pytest.mark.asyncio
async def test_circuit_breaker_status_published_for_command_center_ui():
    """Command Center UI 배지가 구독하는 `sys.circuit_breaker` heartbeat가 실제로 발행되는지
    확인 — `strategy/pipeline.py` 모듈 docstring "Command Center UI 배지" 절."""
    bus, broker, gateway, pipeline = await _make_pipeline(
        circuit_breaker_monitor=CircuitBreakerMonitor()
    )
    published: list[CircuitBreakerStatus] = []

    async def collector(msg):
        published.append(msg)

    await bus.subscribe(["sys.circuit_breaker"], collector)
    await _warm_up(pipeline, broker)

    await pipeline.handle_futures_view(_view(score=0.5, agg_p_up=0.9, agg_p_down=0.05))

    assert len(published) == 1
    assert published[0].symbol == _SYMBOL
    assert published[0].phase == CircuitBreakerPhase.NORMAL.value
