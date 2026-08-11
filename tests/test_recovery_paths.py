"""아침 복구와 재가동 — 관측이 있는데 **되돌릴 방법이 없던** 자리들 (2026-08-11 G-4·G-5·resume).

세 축이 같은 형태의 공백을 메운다:

    G-4     유예 상수가 관측 최대(1.3964초)보다 작았다   → 승격하면 매일 틱을 버렸을 것
    G-5     웜업 회색에 **시한이 없었다**                 → 09:30에도 "모른다"였다
    resume  sys.kill의 반대편이 없었다                    → 닫힌 게이트는 재기동으로만 열렸다
"""

from __future__ import annotations

import sys
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_l1_daily import alert_if_no_first_tick  # noqa: E402

from messiah.core.event_calendar import DEFAULT_SESSION  # noqa: E402
from messiah.core.health import HealthLevel, staleness_status  # noqa: E402
from messiah.core.timeutil import KST  # noqa: E402
from messiah.data.normalizer import MINUTE_CLOSE_GRACE_SECONDS  # noqa: E402

# ---------------------------------------------------------------- G-4 유예 상수


# 2026-08-05·08-06·08-10 실측 최대(`logs/daily_integrity_*.json`의 `delivery_latency.max`).
# 유예가 이 값보다 작으면 `timer` 승격이 곧 매일의 틱 유실이다.
_OBSERVED_MAX_DELIVERY_LATENCY = 1.3964


def test_the_grace_covers_the_worst_observed_delivery_latency():
    """**이것이 G-4의 전부다.** 종전 1.0초는 관측 최대보다 작았다 — 그 상태로 `timer`에
    승격했다면 유예 뒤 도착한 틱을 매일 버렸고, 유실을 고치려던 변경이 다른 유실을
    들여왔을 것이다."""
    assert MINUTE_CLOSE_GRACE_SECONDS > _OBSERVED_MAX_DELIVERY_LATENCY


def test_the_grace_stays_well_inside_the_next_minute():
    """유예가 다음 분 경계를 침범하면 봉 하나가 통째로 밀린다."""
    assert MINUTE_CLOSE_GRACE_SECONDS < 30.0


# ---------------------------------------------------------------- G-5 웜업의 시한


def test_warmup_without_a_deadline_is_unknown_not_ok():
    """기존 규율 — 첫 수신 전을 초록으로 칠하지 않는다(2026-08-05 고도화 3)."""
    status = staleness_status(None, warn_after=10, critical_after=20)

    assert status.level is HealthLevel.UNKNOWN


def test_an_expired_warmup_is_critical_not_unknown():
    """**끝나지 않는 웜업도 UNKNOWN이었다.** 08:43에 회색인 것은 옳지만 09:30에도 회색이면
    그건 정상 웜업이 아니라 회선이 죽은 것이고, 화면·상태판·G2의 CB 억제 근거가 전부
    그것을 "모른다"로 다뤘다."""
    status = staleness_status(None, warn_after=10, critical_after=20, warmup_expired=True)

    assert status.level is HealthLevel.CRITICAL


def test_the_expired_warmup_detail_says_what_to_do():
    """비상 문구가 상태만 말하고 처방을 안 주면 사람이 그때부터 찾기 시작한다."""
    status = staleness_status(
        None,
        warn_after=10,
        critical_after=20,
        warmup_expired=True,
        warmup_expired_detail="첫 틱이 09:00까지 없다 — 회선/구독 확인(scripts\\recover_now.bat)",
    )

    assert "recover_now" in status.detail


def test_a_received_tick_is_never_treated_as_expired_warmup():
    """시한이 지났어도 **받은 적이 있으면** 웜업이 아니다 — 그 경우는 신선도 판정이 맡는다."""
    status = staleness_status(1.0, warn_after=10, critical_after=20, warmup_expired=True)

    assert status.level is HealthLevel.OK


# ---------------------------------------------------------------- G-5 첫 틱 시한 경보


class _FakeCollector:
    def __init__(self, overdue: bool):
        self._overdue = overdue
        self._symbol = "A05608"

    def first_tick_overdue(self, *, now=None) -> bool:  # noqa: ARG002
        return self._overdue


@pytest.mark.asyncio
async def test_the_alert_fires_when_the_deadline_passes_with_no_tick():
    slept: list[float] = []

    async def _sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["t"] = datetime(2026, 8, 11, 9, 0, tzinfo=KST)

    clock = {"t": datetime(2026, 8, 11, 8, 30, tzinfo=KST)}

    fired = await alert_if_no_first_tick(
        _FakeCollector(overdue=True), sleep=_sleep, now=lambda: clock["t"]
    )

    assert fired is True
    assert slept and slept[0] == pytest.approx(30 * 60, abs=1)  # 08:30 → 09:00


@pytest.mark.asyncio
async def test_the_alert_stays_quiet_on_a_normal_morning():
    """정상일에 조용해야 한다 — 매일 우는 경보는 아무도 안 본다."""
    clock = {"t": datetime(2026, 8, 11, 9, 30, tzinfo=KST)}

    async def _sleep(_seconds: float) -> None:  # 이미 시한을 지났으므로 안 불린다
        raise AssertionError("시한이 지난 뒤에는 자지 않는다")

    fired = await alert_if_no_first_tick(
        _FakeCollector(overdue=False), sleep=_sleep, now=lambda: clock["t"]
    )

    assert fired is False


def test_the_deadline_is_the_regular_session_open():
    """시한이 코드 두 곳에 따로 있으면 하나만 고쳐진다 — 정본은 `SessionHours`다."""
    assert DEFAULT_SESSION.open_time == time(9, 0)
    assert DEFAULT_SESSION.first_tick_time < DEFAULT_SESSION.open_time


# ---------------------------------------------------------------- sys.resume


def _pipeline_bits():
    """`tests/strategy/test_pipeline.py`와 같은 조립 — 여기서는 게이트 개폐만 본다."""
    from messiah.broker.simulator.adapter import SimBroker
    from messiah.execution.order_gateway import OrderGateway
    from messiah.simulator.inprocess_bus import InProcessBus
    from messiah.strategy.pipeline import TradingPipeline

    bus = InProcessBus()
    broker = SimBroker(cash=50_000_000)
    gateway = OrderGateway(broker)
    pipeline = TradingPipeline("A05608", broker, gateway, bus)
    return bus, broker, gateway, pipeline


@pytest.mark.asyncio
async def test_resume_reopens_a_gate_that_kill_closed():
    """**이것이 resume의 요점이다.** 2026-08-11 09:27에 점검용 kill 한 번으로 운영 G2의
    게이트가 닫혔고, 그것을 여는 유일한 방법이 프로세스 재기동이었다."""
    from messiah.core.messages import KillSignal, ResumeSignal

    _bus, broker, gateway, pipeline = _pipeline_bits()
    await broker.connect()
    await pipeline.handle_kill(KillSignal(reason="테스트", triggered_by="manual"))
    assert gateway.halted is True

    opened = await pipeline.handle_resume(ResumeSignal(operator="MW0601", reason="점검 종료"))

    assert opened is True
    assert gateway.halted is False
    # KillSwitch도 같이 풀려야 한다 — 안 풀면 다음 kill이 재진입 가드에 걸려 청산을 건너뛴다.
    assert pipeline._kill_switch.triggered is False


@pytest.mark.asyncio
async def test_resume_without_an_operator_is_refused():
    """이름 없는 확인은 확인이 아니다(Ver 1.1 §4-4)."""
    from messiah.core.messages import KillSignal, ResumeSignal

    _bus, broker, gateway, pipeline = _pipeline_bits()
    await broker.connect()
    await pipeline.handle_kill(KillSignal(reason="테스트", triggered_by="manual"))

    opened = await pipeline.handle_resume(ResumeSignal(operator="   "))

    assert opened is False
    assert gateway.halted is True


@pytest.mark.asyncio
async def test_resume_is_refused_while_the_circuit_breaker_is_suspected():
    """**사람이 눌렀다는 사실이 위험을 없애지 않는다.** 데이터가 끊긴 채로 게이트를 열면
    시장 상태를 모르고 주문이 나간다 — 화면의 CB 배지를 보고도 습관적으로 누를 수 있고,
    그때 막는 것이 이 분기의 일이다."""
    from messiah.broker.simulator.adapter import SimBroker
    from messiah.core.messages import BarClosed, Horizon, KillSignal, ResumeSignal
    from messiah.execution.order_gateway import OrderGateway
    from messiah.risk.circuit_breaker_monitor import CircuitBreakerMonitor, CircuitBreakerPhase
    from messiah.simulator.inprocess_bus import InProcessBus
    from messiah.strategy.pipeline import TradingPipeline

    start = datetime(2026, 8, 11, 9, 0, tzinfo=KST)
    now_holder = {"t": start}
    broker = SimBroker(cash=50_000_000)
    await broker.connect()
    gateway = OrderGateway(broker)
    pipeline = TradingPipeline(
        "A05608",
        broker,
        gateway,
        InProcessBus(),
        circuit_breaker_monitor=CircuitBreakerMonitor(),
        now=lambda: now_holder["t"],
    )
    bar = BarClosed(
        symbol="A05608",
        horizon=Horizon.M1,
        bar_open_kst=start,
        o_ticks=100,
        h_ticks=101,
        l_ticks=99,
        c_ticks=100,
        volume=10,
    )
    await pipeline.handle_bar(bar)
    # 데이터 나이는 **봉 확정 시각** 기준이라 250초 뒤여도 190초다(봉 길이 60초를 뺀다) —
    # suspected 임계(150초)는 넘고 confirmed(240초)는 아직이다. 거부는 둘 다에서 걸린다.
    now_holder["t"] = start + timedelta(seconds=250)
    await pipeline.observe_circuit_breaker_tick()
    assert pipeline._circuit_breaker_monitor.phase is CircuitBreakerPhase.SUSPECTED
    await pipeline.handle_kill(KillSignal(reason="테스트", triggered_by="manual"))

    opened = await pipeline.handle_resume(ResumeSignal(operator="MW0601"))

    assert opened is False
    assert gateway.halted is True


@pytest.mark.asyncio
async def test_resume_arrives_through_the_dispatcher():
    """구독 분기가 빠지면 메시지는 발행돼도 아무 일도 안 일어난다 — `sys.kill`이 2026-08-07
    이전에 정확히 그 상태였다(받고 있었는데 `_dispatch`에 분기가 없었다)."""
    from messiah.core.messages import KillSignal, ResumeSignal

    _bus, broker, gateway, pipeline = _pipeline_bits()
    await broker.connect()
    await pipeline.handle_kill(KillSignal(reason="테스트", triggered_by="manual"))

    await pipeline._dispatch(ResumeSignal(operator="MW0601"))

    assert gateway.halted is False


def test_the_pipeline_subscribes_to_the_resume_topic():
    """버스는 `sys.resume`을 자동 배달하지 않는다 — 패턴에서 빠지면 조용히 안 온다."""
    import inspect

    from messiah.core.bus import TOPIC_RESUME
    from messiah.strategy import pipeline as pipeline_module

    source = inspect.getsource(pipeline_module.TradingPipeline.run_forever)

    assert "TOPIC_RESUME" in source
    assert TOPIC_RESUME == "sys.resume"
