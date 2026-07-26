from decimal import Decimal

import pytest
from messiah.broker.base import BrokerAccount, BrokerPosition
from messiah.core.messages import KillSignal, OrderKind, Side
from messiah.risk.kill_switch import KillSwitch, KillSwitchConfig
from messiah.simulator.inprocess_bus import InProcessBus

_SYMBOL = "TEST"


def _account(equity: Decimal) -> BrokerAccount:
    return BrokerAccount(cash=equity, margin_used=Decimal("0"), total_equity=equity)


@pytest.mark.asyncio
async def test_daily_loss_triggers_and_publishes_kill_signal():
    bus = InProcessBus()
    published = []

    async def collector(msg):
        published.append(msg)

    await bus.subscribe(["sys.kill"], collector)

    kill_switch = KillSwitch(bus, config=KillSwitchConfig(daily_loss_limit_pct=2.0))
    triggered = await kill_switch.evaluate(
        account=_account(Decimal("48900000")),
        daily_start_equity=Decimal("50000000"),
        data_age_seconds=0.0,
    )
    assert triggered is True
    assert kill_switch.triggered is True
    assert len(published) == 1
    assert isinstance(published[0], KillSignal)
    assert published[0].triggered_by == "R2"


@pytest.mark.asyncio
async def test_sustained_data_disconnect_triggers_r11():
    bus = InProcessBus()
    kill_switch = KillSwitch(bus, config=KillSwitchConfig(data_disconnect_limit_seconds=30.0))
    triggered = await kill_switch.evaluate(
        account=_account(Decimal("50000000")),
        daily_start_equity=Decimal("50000000"),
        data_age_seconds=45.0,
    )
    assert triggered is True


@pytest.mark.asyncio
async def test_manual_trigger():
    bus = InProcessBus()
    kill_switch = KillSwitch(bus)
    triggered = await kill_switch.evaluate(
        account=_account(Decimal("50000000")),
        daily_start_equity=Decimal("50000000"),
        data_age_seconds=0.0,
        manual=True,
    )
    assert triggered is True


@pytest.mark.asyncio
async def test_model_anomaly_trigger():
    bus = InProcessBus()
    kill_switch = KillSwitch(bus)
    triggered = await kill_switch.evaluate(
        account=_account(Decimal("50000000")),
        daily_start_equity=Decimal("50000000"),
        data_age_seconds=0.0,
        model_anomaly=True,
    )
    assert triggered is True


@pytest.mark.asyncio
async def test_no_trigger_when_all_clean():
    bus = InProcessBus()
    kill_switch = KillSwitch(bus)
    triggered = await kill_switch.evaluate(
        account=_account(Decimal("50000000")),
        daily_start_equity=Decimal("50000000"),
        data_age_seconds=1.0,
    )
    assert triggered is False
    assert kill_switch.triggered is False


@pytest.mark.asyncio
async def test_already_triggered_short_circuits_without_republishing():
    bus = InProcessBus()
    published = []

    async def collector(msg):
        published.append(msg)

    await bus.subscribe(["sys.kill"], collector)

    kill_switch = KillSwitch(bus, config=KillSwitchConfig(daily_loss_limit_pct=2.0))
    await kill_switch.evaluate(
        account=_account(Decimal("48000000")),
        daily_start_equity=Decimal("50000000"),
        data_age_seconds=0.0,
    )
    await kill_switch.evaluate(
        account=_account(Decimal("50000000")),
        daily_start_equity=Decimal("50000000"),
        data_age_seconds=0.0,
    )
    assert len(published) == 1  # 두 번째 평가는 이미 triggered라 재평가·재발행 없음


def test_reset_clears_triggered_state():
    bus = InProcessBus()
    kill_switch = KillSwitch(bus)
    kill_switch._triggered = True  # noqa: SLF001 — 테스트 셋업
    kill_switch.reset(operator="test-operator")
    assert kill_switch.triggered is False


def test_liquidate_long_position_flattens_via_opposite_side():
    bus = InProcessBus()
    kill_switch = KillSwitch(bus)
    positions = [BrokerPosition(symbol=_SYMBOL, qty=3, avg_price_ticks=100)]
    requests = kill_switch.liquidate(positions)
    assert len(requests) == 1
    assert requests[0].side == Side.SHORT
    assert requests[0].qty == 3
    assert requests[0].kind == OrderKind.EMERGENCY
    assert requests[0].limit_price_ticks is None


def test_liquidate_short_position_flattens_via_opposite_side():
    bus = InProcessBus()
    kill_switch = KillSwitch(bus)
    positions = [BrokerPosition(symbol=_SYMBOL, qty=-2, avg_price_ticks=100)]
    requests = kill_switch.liquidate(positions)
    assert requests[0].side == Side.LONG
    assert requests[0].qty == 2


def test_liquidate_skips_flat_positions():
    bus = InProcessBus()
    kill_switch = KillSwitch(bus)
    positions = [BrokerPosition(symbol=_SYMBOL, qty=0, avg_price_ticks=100)]
    assert kill_switch.liquidate(positions) == []
