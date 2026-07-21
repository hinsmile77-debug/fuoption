"""W1 골격 검증 — timeutil(R3) · messages 스키마 · OrderGateway(계명 1/L1)."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from messiah.broker.simulator.adapter import SimBroker
from messiah.core import logging as mlog
from messiah.core.config import InstanceConfig
from messiah.core.messages import (
    DecisionIntent,
    Fill,
    OrderKind,
    OrderRequest,
    Side,
)
from messiah.core.timeutil import ensure_aware, now_kst, now_utc
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


def test_pending_registered_before_send_and_matched_fill() -> None:
    async def run() -> None:
        gw = OrderGateway(SimBroker())
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


def test_failed_submit_rolls_back_pending() -> None:
    async def run() -> None:
        gw = OrderGateway(SimBroker())
        assert await gw.submit(_req(qty=0)) is None  # 브로커 거부
        # pending이 롤백되어 다음 정상 주문에 지장 없음
        assert (await gw.submit(_req())) is not None

    asyncio.run(run())


def test_unregistered_log_tag_rejected() -> None:
    """태그 1개=심각도 1개 — 미등록 태그는 사용 불가 (R6 / L10)."""
    with pytest.raises(ValueError, match="미등록 태그"):
        mlog.log("RandomNewTag", "should fail")
