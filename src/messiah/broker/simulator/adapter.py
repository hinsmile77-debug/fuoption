"""Digital Twin의 최소 골격 — 즉시체결 시뮬레이터 (W1 단계).

W9~11에서 Parquet 재생·호가 기반 체결·시장충격 모사로 확장된다 (Ver 1.0.1 §2.1).
지금은 OrderGateway·Reconciler·테스트의 상대역으로 충분한 최소 구현만 둔다.
"""

from __future__ import annotations

import itertools
from decimal import Decimal

from messiah.broker.base import (
    BrokerAccount,
    BrokerAdapter,
    BrokerPosition,
    SubmitResult,
)
from messiah.core.messages import OrderKind, OrderRequest, Side


class SimBroker(BrokerAdapter):
    name = "simulator"

    def __init__(self, cash: int = 50_000_000) -> None:
        self._seq = itertools.count(1)
        self._positions: dict[str, BrokerPosition] = {}
        self._cash = Decimal(cash)
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def submit(self, req: OrderRequest) -> SubmitResult:
        if req.qty <= 0:
            return SubmitResult(ok=False, error="qty must be positive")
        order_no = f"SIM{next(self._seq):08d}"
        self._apply(req)
        return SubmitResult(ok=True, broker_order_no=order_no)

    async def cancel(self, broker_order_no: str) -> bool:
        return True  # 즉시체결 모델이라 취소 대상 없음

    async def positions(self) -> list[BrokerPosition]:
        return [p for p in self._positions.values() if p.qty != 0]

    async def account(self) -> BrokerAccount:
        return BrokerAccount(cash=self._cash, margin_used=Decimal(0), total_equity=self._cash)

    async def probe_front_month(self, product: str) -> str:
        return f"{product}_FRONT_SIM"

    # ------------------------------------------------------------------
    def _apply(self, req: OrderRequest) -> None:
        cur = self._positions.get(req.symbol)
        signed = req.qty if req.side == Side.LONG else -req.qty
        if req.kind in (OrderKind.EXIT_FULL, OrderKind.EXIT_PARTIAL) and cur is not None:
            signed = -cur.qty if req.kind == OrderKind.EXIT_FULL else signed
        new_qty = (cur.qty if cur else 0) + signed
        price = req.limit_price_ticks or (cur.avg_price_ticks if cur else 0)
        self._positions[req.symbol] = BrokerPosition(
            symbol=req.symbol, qty=new_qty, avg_price_ticks=price
        )
