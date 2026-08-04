import math
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from messiah.broker.simulator.adapter import SimBroker
from messiah.core.event_calendar import EventCalendar
from messiah.core.health import COLLECTOR_COMPONENT
from messiah.core.messages import (
    BarClosed,
    CircuitBreakerStatus,
    FuturesView,
    Health,
    HealthLevel,
    Horizon,
    Regime,
    Side,
    bar_confirm_time,
)
from messiah.core.timeutil import KST, now_utc
from messiah.execution.order_gateway import OrderGateway
from messiah.risk.circuit_breaker_monitor import CircuitBreakerMonitor, CircuitBreakerPhase
from messiah.risk.kill_switch import KillSwitch, KillSwitchConfig
from messiah.simulator.inprocess_bus import InProcessBus
from messiah.strategy.pipeline import TradingPipeline

_SYMBOL = "TEST"
_START = datetime(2026, 7, 30, 9, 0, tzinfo=KST)
_NEAR_CLOSE_START = datetime(2026, 7, 30, 15, 10, tzinfo=KST)  # 15:35 마감 25분 전부터 워밍업
_WARMUP_BARS = 20

# `_view()`의 기본 타임스탬프 — 워밍업 마지막 봉이 확정된 직후(09:20:05).
#
# 예전엔 기본값이 `FuturesView`의 필드 기본값(`now_utc()`, 즉 **실제 벽시계**)이었다. 봉은
# 2026-07-30 09:00~09:19로 고정돼 있는데 뷰만 실시간이라, `_data_age_seconds()`가
# "지금 − 09:20"이 되어 시간이 갈수록 커진다. 커밋 b1a366d(2026-07-27) 시점엔 그 날짜가
# **미래**라 값이 음수 → 임계 미달로 통과했지만, 2026-07-30이 도래하자 데이터단절 1355초가
# 되어 CB가 확정되고 R11이 신규진입을 막아 4건이 한꺼번에 깨졌다(그대로 두면 **매일** 실패).
#
# `FuturesView.ts_utc`는 "그 뷰가 대표하는 시장 데이터의 시각"이므로, 워밍업 봉과 같은
# 타임라인에 두는 것이 원래 의미에도 맞다 — 벽시계와 무관해져 언제 돌려도 결과가 같다.
_DEFAULT_VIEW_TS = _START + timedelta(minutes=_WARMUP_BARS, seconds=5)


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
    kwargs["ts_utc"] = ts if ts is not None else _DEFAULT_VIEW_TS
    # 파이프라인의 "지금"을 이 뷰의 시각에 맞춘다 — 2026-08-04부터 신선도·세션 판정이
    # `view.ts_utc`가 아니라 **주입된 시계** 하나로 통일됐다(`strategy/pipeline.py`의
    # `__init__` 주석). 그 전엔 두 값이 암묵적으로 같다고 가정하고 있었고, 재생 경로에서
    # 그 가정이 깨져 백테스트가 항상 무거래였다.
    _NOW["t"] = kwargs["ts_utc"]
    return FuturesView(**kwargs)


# 기본 시계 — `_view()`가 매번 그 뷰의 시각으로 갱신한다. 명시적으로 `now=`를 넘기는
# 테스트(CB 워치독 계열)는 `setdefault`가 건드리지 않는다.
_NOW = {"t": _DEFAULT_VIEW_TS}


async def _make_pipeline(**overrides):
    overrides.setdefault("now", lambda: _NOW["t"])
    bus = InProcessBus()
    broker = SimBroker(cash=50_000_000)
    await broker.connect()
    gateway = OrderGateway(broker)
    pipeline = TradingPipeline(_SYMBOL, broker, gateway, bus, **overrides)
    return bus, broker, gateway, pipeline


async def _warm_up(pipeline, broker, n=_WARMUP_BARS, start: datetime = _START):
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


# ---------------------------------------------------------------- 벽시계 주입 (2026-07-30)


@pytest.mark.asyncio
async def test_circuit_breaker_watchdog_uses_the_injected_clock():
    """`watch_circuit_breaker_forever()`는 "데이터가 안 오는 동안"을 재는 순수 벽시계
    폴링이라, 주입 없이는 실제로 몇 분을 기다려야만 검증할 수 있었다."""
    fake_now = _START + timedelta(minutes=_WARMUP_BARS)  # 마지막 봉 확정 직후
    bus, broker, gateway, pipeline = await _make_pipeline(
        circuit_breaker_monitor=CircuitBreakerMonitor(), now=lambda: fake_now
    )
    await _warm_up(pipeline, broker)
    published: list[CircuitBreakerStatus] = []

    async def collector(msg):
        published.append(msg)

    await bus.subscribe(["sys.circuit_breaker"], collector)

    await pipeline.observe_circuit_breaker_tick()  # 워치독 1틱만 수행

    assert published and published[0].phase == CircuitBreakerPhase.NORMAL.value


@pytest.mark.asyncio
async def test_circuit_breaker_watchdog_confirms_when_the_injected_clock_advances():
    now_holder = {"t": _START + timedelta(minutes=_WARMUP_BARS)}
    bus, broker, gateway, pipeline = await _make_pipeline(
        circuit_breaker_monitor=CircuitBreakerMonitor(), now=lambda: now_holder["t"]
    )
    await _warm_up(pipeline, broker)

    now_holder["t"] += timedelta(seconds=250)  # confirmed 임계(240초) 초과
    await pipeline.observe_circuit_breaker_tick()

    assert pipeline._circuit_breaker_monitor.phase == CircuitBreakerPhase.CONFIRMED
    assert gateway.halted is True


@pytest.mark.asyncio
async def test_watchdog_alone_completes_the_halt_resume_round_trip():
    """2026-07-31 실측 회귀 — 그날은 Registry에 live 번들이 0개라 `intel.futures`가 아예 발행되지
    않았고, 따라서 `handle_futures_view()`가 하루 종일 **한 번도 안 불렸다**. 해제 처리가 그
    경로에만 있었기 때문에 08:53에 걸린 정지가 15:35 종료까지 안 풀렸다. 이 테스트는 워치독
    틱만으로 정지→해제가 완결되는지를 본다(FuturesView 0건)."""
    now_holder = {"t": _START + timedelta(minutes=_WARMUP_BARS)}
    bus, broker, gateway, pipeline = await _make_pipeline(
        circuit_breaker_monitor=CircuitBreakerMonitor(), now=lambda: now_holder["t"]
    )
    bars = await _warm_up(pipeline, broker)

    now_holder["t"] += timedelta(seconds=250)
    await pipeline.observe_circuit_breaker_tick()
    assert gateway.halted is True

    # 데이터 재수신 — 새 봉이 들어오되, 확정 지연 때문에 data_age는 WARNING 대역(90~150초)에
    # 떨어진다(2026-07-31에 실제로 일어난 형태).
    resume_bar = _m1_bars(1, start=bar_confirm_time(bars[-1]) + timedelta(seconds=260))[0]
    broker.on_bar(resume_bar)
    await pipeline.handle_bar(resume_bar)
    now_holder["t"] = bar_confirm_time(resume_bar) + timedelta(seconds=100)

    await pipeline.observe_circuit_breaker_tick()

    assert pipeline._circuit_breaker_monitor.phase == CircuitBreakerPhase.WARNING  # noqa: SLF001
    assert gateway.halted is False  # 워치독 단독으로 게이트가 풀렸다


@pytest.mark.asyncio
async def test_watchdog_resume_liquidates_open_positions():
    """워치독 경로도 해제 시 강제청산을 한다 — 포지션 인자를 안 받으므로 스스로 조회해야 한다."""
    now_holder = {"t": _START + timedelta(minutes=_WARMUP_BARS)}
    bus, broker, gateway, pipeline = await _make_pipeline(
        circuit_breaker_monitor=CircuitBreakerMonitor(), now=lambda: now_holder["t"]
    )
    bars = await _warm_up(pipeline, broker)
    await pipeline.start_day()
    await pipeline.handle_futures_view(_view(score=0.5, agg_p_up=0.9, agg_p_down=0.05))
    assert len(await broker.positions()) == 1

    now_holder["t"] += timedelta(seconds=250)
    await pipeline.observe_circuit_breaker_tick()
    assert gateway.halted is True

    resume_bar = _m1_bars(1, start=bar_confirm_time(bars[-1]) + timedelta(seconds=260))[0]
    broker.on_bar(resume_bar)
    await pipeline.handle_bar(resume_bar)
    now_holder["t"] = bar_confirm_time(resume_bar) + timedelta(seconds=1)

    await pipeline.observe_circuit_breaker_tick()

    assert await broker.positions() == []
    assert gateway.halted is False


@pytest.mark.asyncio
async def test_circuit_breaker_status_carries_actual_gateway_state():
    """추정 phase와 실제 게이트 상태를 함께 싣는다 — 2026-07-31엔 둘이 6시간 42분간 어긋났는데
    화면에 그 사실이 전혀 안 보였다."""
    now_holder = {"t": _START + timedelta(minutes=_WARMUP_BARS)}
    bus, broker, gateway, pipeline = await _make_pipeline(
        circuit_breaker_monitor=CircuitBreakerMonitor(), now=lambda: now_holder["t"]
    )
    published: list[CircuitBreakerStatus] = []

    async def collector(msg):
        published.append(msg)

    await bus.subscribe(["sys.circuit_breaker"], collector)
    await _warm_up(pipeline, broker)

    await pipeline.observe_circuit_breaker_tick()
    assert published[-1].gateway_halted is False

    now_holder["t"] += timedelta(seconds=250)
    await pipeline.observe_circuit_breaker_tick()
    # 발행이 반응 뒤에 일어나므로 같은 틱에서 이미 정지 상태가 보여야 한다(한 틱 늦지 않는다)
    assert published[-1].phase == CircuitBreakerPhase.CONFIRMED.value
    assert published[-1].gateway_halted is True


# ------------------------------------------------ 수집기 heartbeat 결선 (2026-07-31, P0-2)


def _collector_health(level: HealthLevel, ts) -> Health:
    return Health(component=COLLECTOR_COMPONENT, level=level, detail="", pid=1, ts_utc=ts)


@pytest.mark.asyncio
async def test_healthy_collector_heartbeat_suppresses_false_circuit_breaker():
    """2026-07-31 실측 회귀 — 상한가 고착으로 체결이 뜸해 봉만 안 만들어지던 구간을 CB가
    데이터 단절로 오판해 하루 5회 정지시켰다. 수집기가 OK를 보내는 동안엔 정지까지 안 간다."""
    now_holder = {"t": _START + timedelta(minutes=_WARMUP_BARS)}
    bus, broker, gateway, pipeline = await _make_pipeline(
        circuit_breaker_monitor=CircuitBreakerMonitor(), now=lambda: now_holder["t"]
    )
    await _warm_up(pipeline, broker)

    now_holder["t"] += timedelta(seconds=250)  # confirmed 임계 초과
    await pipeline._dispatch(  # noqa: SLF001 — 실제 sys.health 구독 경로를 그대로 탄다
        _collector_health(HealthLevel.OK, now_holder["t"] - timedelta(seconds=5))
    )
    await pipeline.observe_circuit_breaker_tick()

    assert pipeline._circuit_breaker_monitor.phase == CircuitBreakerPhase.SUSPECTED  # noqa: SLF001
    assert gateway.halted is False


@pytest.mark.asyncio
async def test_unhealthy_collector_heartbeat_still_allows_circuit_breaker():
    now_holder = {"t": _START + timedelta(minutes=_WARMUP_BARS)}
    bus, broker, gateway, pipeline = await _make_pipeline(
        circuit_breaker_monitor=CircuitBreakerMonitor(), now=lambda: now_holder["t"]
    )
    await _warm_up(pipeline, broker)

    now_holder["t"] += timedelta(seconds=250)
    await pipeline._dispatch(  # noqa: SLF001
        _collector_health(HealthLevel.CRITICAL, now_holder["t"] - timedelta(seconds=5))
    )
    await pipeline.observe_circuit_breaker_tick()

    assert gateway.halted is True


@pytest.mark.asyncio
async def test_stale_collector_heartbeat_is_treated_as_unknown_not_healthy():
    """수집기 프로세스가 죽어 heartbeat가 끊긴 상황을 "정상"으로 오해하면, 진짜 단절에
    CB가 영영 안 걸린다 — 모르는 것은 정상이 아니다."""
    now_holder = {"t": _START + timedelta(minutes=_WARMUP_BARS)}
    bus, broker, gateway, pipeline = await _make_pipeline(
        circuit_breaker_monitor=CircuitBreakerMonitor(), now=lambda: now_holder["t"]
    )
    await _warm_up(pipeline, broker)

    # OK를 한 번 받았지만 그 뒤로 heartbeat가 끊겼다(임계 30초를 훨씬 넘김)
    await pipeline._dispatch(_collector_health(HealthLevel.OK, now_holder["t"]))  # noqa: SLF001
    now_holder["t"] += timedelta(seconds=250)
    await pipeline.observe_circuit_breaker_tick()

    assert gateway.halted is True


@pytest.mark.asyncio
async def test_health_from_other_components_is_ignored():
    """`sys.health`는 여러 컴포넌트가 같이 쓰는 토픽이다 — 피처엔진 heartbeat를 수집기 것으로
    잘못 읽으면 엉뚱한 근거로 CB를 억제하게 된다."""
    now_holder = {"t": _START + timedelta(minutes=_WARMUP_BARS)}
    bus, broker, gateway, pipeline = await _make_pipeline(
        circuit_breaker_monitor=CircuitBreakerMonitor(), now=lambda: now_holder["t"]
    )
    await _warm_up(pipeline, broker)

    now_holder["t"] += timedelta(seconds=250)
    await pipeline._dispatch(  # noqa: SLF001
        Health(
            component="l1.feature_engine",
            level=HealthLevel.OK,
            detail="",
            pid=1,
            ts_utc=now_holder["t"],
        )
    )
    await pipeline.observe_circuit_breaker_tick()

    assert gateway.halted is True  # 수집기 상태는 여전히 "모름"


@pytest.mark.asyncio
async def test_default_clock_is_the_real_wall_clock():
    """주입을 안 하면 기존 동작 그대로 — 회귀 방지.

    `_make_pipeline()`은 테스트 편의를 위해 시계를 기본 주입하므로(모듈 상단 `_NOW`),
    여기서는 **프로덕션 기본값**을 보려고 생성자를 직접 부른다."""
    broker = SimBroker(cash=50_000_000)
    await broker.connect()
    pipeline = TradingPipeline(_SYMBOL, broker, OrderGateway(broker), InProcessBus())

    assert pipeline._now is not None  # noqa: SLF001
    assert (now_utc() - pipeline._now()).total_seconds() < 5  # noqa: SLF001


# ---------------------------------------------------------------- 장전 세션 게이트 (2026-07-30)


_PRE_OPEN_START = datetime(2026, 7, 30, 8, 45, tzinfo=KST)  # 실측상 틱이 실제로 들어오는 시각


@pytest.mark.asyncio
async def test_pre_open_evaluates_but_does_not_trade():
    """장전 08:45~09:00은 웜업만 — 판단은 하되 주문은 내지 않는다(2026-07-30 사용자 결정)."""
    # `atr_window`를 줄인 이유는 아래 `test_gate_is_inactive_without_an_event_calendar`와
    # 동일 — 이 테스트가 검증하려는 건 게이트지 ATR 워밍업이 아니다.
    bus, broker, gateway, pipeline = await _make_pipeline(
        event_calendar=EventCalendar.from_file(), atr_window=5
    )
    intents = []

    async def collector(msg):
        intents.append(msg)

    await bus.subscribe(["decision.intent"], collector)
    bars = await _warm_up(pipeline, broker, n=10, start=_PRE_OPEN_START)  # 08:45~08:54
    fresh_ts = bar_confirm_time(bars[-1]) + timedelta(seconds=5)  # 08:55:05 — 아직 개장 전

    await pipeline.handle_futures_view(_view(score=0.5, agg_p_up=0.9, agg_p_down=0.05, ts=fresh_ts))

    assert len(intents) == 1
    assert intents[0].side == Side.LONG  # 판단은 평소대로 수행·발행된다
    assert await broker.positions() == []  # 그러나 주문은 나가지 않는다


@pytest.mark.asyncio
async def test_regular_session_still_trades_normally():
    """게이트가 정규장까지 막아버리면 시스템이 아무것도 못 한다 — 경계 확인."""
    bus, broker, gateway, pipeline = await _make_pipeline(event_calendar=EventCalendar.from_file())
    await _warm_up(pipeline, broker)  # 09:00~09:19

    await pipeline.handle_futures_view(_view(score=0.5, agg_p_up=0.9, agg_p_down=0.05))

    assert len(await broker.positions()) == 1


@pytest.mark.asyncio
async def test_pre_open_bars_still_feed_the_warmup():
    """거래는 막되 **데이터는 버리지 않는다** — 장전 봉으로 ATR 워밍업이 채워져야 개장
    직후 첫 뷰에서 바로 거래할 수 있다."""
    bus, broker, gateway, pipeline = await _make_pipeline(event_calendar=EventCalendar.from_file())
    await _warm_up(pipeline, broker, n=15, start=_PRE_OPEN_START)  # 08:45~08:59 전부 장전

    assert len(pipeline._bars) == 15  # noqa: SLF001 — 장전 봉이 그대로 쌓였다

    # 개장(09:00) 이후 첫 뷰 — 장전에 쌓인 봉만으로 ATR이 서서 즉시 진입된다
    open_ts = datetime(2026, 7, 30, 9, 0, 5, tzinfo=KST)
    await pipeline.handle_futures_view(_view(score=0.5, agg_p_up=0.9, agg_p_down=0.05, ts=open_ts))

    assert len(await broker.positions()) == 1


@pytest.mark.asyncio
async def test_after_close_is_blocked_by_the_same_gate():
    """[09:00, 15:35) 반개구간이라 마감 이후도 같은 판정이 막는다."""
    bus, broker, gateway, pipeline = await _make_pipeline(event_calendar=EventCalendar.from_file())
    after_close_start = datetime(2026, 7, 30, 15, 36, tzinfo=KST)
    bars = await _warm_up(pipeline, broker, start=after_close_start)
    fresh_ts = bar_confirm_time(bars[-1]) + timedelta(seconds=5)

    await pipeline.handle_futures_view(_view(score=0.5, agg_p_up=0.9, agg_p_down=0.05, ts=fresh_ts))

    assert await broker.positions() == []


@pytest.mark.asyncio
async def test_gate_is_inactive_without_an_event_calendar():
    """재생/스모크의 기존 동작 유지 — 미주입이면 세션 개념 자체가 없다.

    `atr_window`를 줄여 ATR 워밍업(기본 15봉 필요)이 이 테스트의 변수가 되지 않게 한다 —
    같은 장전 시각·같은 봉 수로 위 `test_pre_open_evaluates_but_does_not_trade`와 정확히
    대비시켜, 차이가 오직 `event_calendar` 주입 여부에서만 오도록 만든다."""
    bus, broker, gateway, pipeline = await _make_pipeline(atr_window=5)  # event_calendar 미주입
    bars = await _warm_up(pipeline, broker, n=10, start=_PRE_OPEN_START)
    fresh_ts = bar_confirm_time(bars[-1]) + timedelta(seconds=5)  # 08:55:05 — 장전

    await pipeline.handle_futures_view(_view(score=0.5, agg_p_up=0.9, agg_p_down=0.05, ts=fresh_ts))

    assert len(await broker.positions()) == 1
