"""W1 골격 검증 — timeutil(R3) · messages 스키마 · OrderGateway(계명 1/L1)."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from messiah.broker.simulator.adapter import SimBroker
from messiah.core import logging as mlog
from messiah.core.config import InstanceConfig
from messiah.core.messages import (
    BarClosed,
    DecisionIntent,
    Fill,
    Horizon,
    OrderKind,
    OrderRequest,
    Side,
)
from messiah.core.timeutil import KST, ensure_aware, now_kst, now_utc
from messiah.execution.order_gateway import OrderGateway

mlog.setup("test-instance")


# ---------------------------------------------------------------- timeutil (R3 / L21)


def test_now_utc_is_aware() -> None:
    assert now_utc().tzinfo is not None
    assert now_kst().utcoffset().total_seconds() == 9 * 3600


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError, match="naive"):
        ensure_aware(datetime(2026, 7, 21, 9, 0, 0))  # noqa: DTZ001 — 의도적 naive


# ---------------------------------------------------------------- messages


def test_message_rejects_naive_ts() -> None:
    with pytest.raises(Exception):
        DecisionIntent(
            symbol="K200_MINI_FUT",
            side=Side.LONG,
            confidence=0.7,
            uncertainty=0.1,
            ts_utc=datetime(2026, 7, 21, 9, 0, 0),  # noqa: DTZ001 — naive → 거부돼야 함
        )


def test_no_trade_carries_rationale() -> None:
    """NO TRADE도 근거와 함께 — 침묵이 아니라 판단 (Ver 2.0 §3.2)."""
    intent = DecisionIntent(
        symbol="K200_MINI_FUT",
        side=Side.NO_TRADE,
        confidence=0.5,
        uncertainty=0.3,
        rationale="expert disagreement 0.31 > 0.25",
    )
    assert intent.rationale


def test_instance_config_defaults() -> None:
    cfg = InstanceConfig()
    assert cfg.universe == ["K200_MINI_FUT", "K200_OPT"]  # 미니선물 표준 (Holding Policy)
    assert cfg.capital.daily_loss_limit_pct == 2.0  # R2


# ---------------------------------------------------------------- OrderGateway (L1)


def _req(qty: int = 3) -> OrderRequest:
    return OrderRequest(
        intent_id="i1",
        symbol="K200_MINI_FUT",
        kind=OrderKind.ENTRY,
        side=Side.LONG,
        qty=qty,
        limit_price_ticks=41500,
    )


def _primed_broker() -> SimBroker:
    """SimBroker는 W9~11부터 재생봉으로 시계·기준가를 받기 전엔 주문을 거부한다
    (simulator/adapter.py) — 이 파일의 나머지 테스트는 SimBroker 자체가 아니라
    OrderGateway 로직 검증이 목적이라 봉 1개로 시계만 진행시켜 둔다."""
    broker = SimBroker()
    broker.on_bar(
        BarClosed(
            symbol="K200_MINI_FUT",
            horizon=Horizon.M1,
            bar_open_kst=datetime(2026, 7, 21, 9, 0, tzinfo=KST),
            o_ticks=41500,
            h_ticks=41500,
            l_ticks=41500,
            c_ticks=41500,
            volume=1,
        )
    )
    return broker


def test_pending_registered_before_send_and_matched_fill() -> None:
    async def run() -> None:
        gw = OrderGateway(_primed_broker())
        ack = await gw.submit(_req())
        assert ack is not None and ack.broker_order_no.startswith("SIM")

        fill = Fill(
            broker_order_no=ack.broker_order_no,
            symbol="K200_MINI_FUT",
            qty=3,
            price_ticks=41500,
            ts_exchange=now_kst(),
            pending_matched=False,
        )
        out = await gw.on_fill(fill)
        assert out.pending_matched is True
        assert not gw.halted

    asyncio.run(run())


def test_unmatched_fill_halts_gateway_not_ghost_position() -> None:
    """미매칭 체결 = CRITICAL 정지 — 유령 포지션 생성 금지 (미륵이 -675만원 사건 재발 방지)."""

    async def run() -> None:
        gw = OrderGateway(SimBroker())
        ghost = Fill(
            broker_order_no="UNKNOWN999",
            symbol="K200_MINI_FUT",
            qty=8,
            price_ticks=41000,
            ts_exchange=now_kst(),
            pending_matched=False,
        )
        out = await gw.on_fill(ghost)
        assert out.pending_matched is False
        assert gw.halted  # 거래 정지
        assert await gw.submit(_req()) is None  # 정지 중 신규 주문 거부

        await gw.resume(operator="human")  # 사람 확인 후에만 재개
        assert not gw.halted

    asyncio.run(run())


def test_halt_blocks_new_entries_but_not_emergency_liquidation() -> None:
    """Kill Switch가 halt() 후에도 자기 청산 주문(EMERGENCY)은 낼 수 있어야 한다 —
    halted가 EMERGENCY까지 막으면 청산 자체가 불가능해지는 모순 (Ver 2.0 §9 W24~26 실측 발견)."""

    async def run() -> None:
        gw = OrderGateway(_primed_broker())
        await gw.halt("test")
        assert gw.halted
        assert await gw.submit(_req()) is None  # 일반 진입은 여전히 거부

        emergency = OrderRequest(
            intent_id="kill-switch",
            symbol="K200_MINI_FUT",
            kind=OrderKind.EMERGENCY,
            side=Side.SHORT,
            qty=3,
        )
        ack = await gw.submit(emergency)
        assert ack is not None  # 청산은 통과

    asyncio.run(run())


def test_failed_submit_rolls_back_pending() -> None:
    async def run() -> None:
        gw = OrderGateway(_primed_broker())
        assert await gw.submit(_req(qty=0)) is None  # 브로커 거부
        # pending이 롤백되어 다음 정상 주문에 지장 없음
        assert (await gw.submit(_req())) is not None

    asyncio.run(run())


def test_unregistered_log_tag_rejected() -> None:
    """태그 1개=심각도 1개 — 미등록 태그는 사용 불가 (R6 / L10)."""
    with pytest.raises(ValueError, match="미등록 태그"):
        mlog.log("RandomNewTag", "should fail")
