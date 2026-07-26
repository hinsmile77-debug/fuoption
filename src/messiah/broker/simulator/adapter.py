"""Digital Twin 브로커 — 완성봉 재생 기반 체결 모사 (Ver 2.0 §9 W9~11).

Ver 1.0.1 §2.1이 제안한 "호가창 수준 재생"은 MESSIAH가 아직 호가(orderbook) WS를 구독하지
않아(capability_matrix.md 알려진 갭) 이번 스코프에서 불가능하다 — 대신 지금 있는 데이터(완성봉
OHLCV, 최소 단위 1분봉)만으로 낼 수 있는 가장 정직한 근사를 택한다:

- **지정가**: submit() 시점엔 pending 등록만 하고 즉시 체결하지 않는다. 이후 `on_bar()`가
  1분봉을 하나씩 받을 때마다 그 봉의 고가/저가가 지정가를 스쳤는지(터치) 판정해 체결한다
  (BUY는 저가 ≤ 지정가, SELL은 고가 ≥ 지정가). 체결가는 지정가 그대로 — 터치 이후 더 유리한
  가격에 체결됐을 가능성을 반영하지 않는 보수적 가정이다.
- **시장가**: submit() 시점에 마지막으로 관측한 종가 ± slippage_ticks(매수는 불리하게 +,
  매도는 -)로 즉시 체결. 아직 관측된 봉이 하나도 없으면(시세 없이 시장가 제출) 거부.
- **TTL**: 매 1분봉마다 (봉 확정시각 − 제출시각)이 ttl_ms를 넘겼는지 확인해 넘겼으면 자동
  취소(OrderExpired 로그, 체결 아님). 체결 판정이 취소 판정보다 먼저다 — 같은 봉에서 터치와
  TTL 만료가 동시에 발생하면 체결을 우선한다.
- **체결 판정은 1분봉으로만 한다**: 3/5/10/15/30분봉은 FeatureEngine 등 다른 소비자용으로
  버스에 그대로 흘려보내되 `on_bar()`는 무시한다 — 더 굵은 Horizon으로 같은 구간을 중복
  판정하면 이미 1분봉으로 체결·취소된 주문에 대해 아무 의미가 없고, 굳이 최소 단위가 아닌
  다른 판정 기준을 추가로 둘 이유가 없다.

슬리피지·체결가 모델은 Cost Model v1(Ver 2.0 §9 W14~16)이 나오기 전까지의 임시 근사다 —
실제 체결 품질 대사가 쌓이면 교체될 자리(고정 틱이 아니라 스프레드·거래량 함수)로 남겨둔다.
부분체결은 모델링하지 않는다(전량 체결 또는 미체결) — 필요해지면 확장.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from messiah.broker.base import (
    BrokerAccount,
    BrokerAdapter,
    BrokerPosition,
    SubmitResult,
)
from messiah.core import logging as mlog
from messiah.core.messages import (
    HORIZON_SECONDS,
    BarClosed,
    Fill,
    Horizon,
    OrderKind,
    OrderRequest,
    Side,
)


@dataclass
class _PendingOrder:
    """limit_price_ticks: req와 별개로 non-null 고정 — pending은 항상 지정가라 Optional이 없다."""

    req: OrderRequest
    limit_price_ticks: int
    submitted_at: datetime


class SimBroker(BrokerAdapter):
    """Digital Twin — DigitalTwinEngine이 재생하는 봉으로 `on_bar()`를 호출해 시간을 진행시킨다."""

    name = "simulator"

    def __init__(self, cash: int = 50_000_000, slippage_ticks: int = 1) -> None:
        self._seq = itertools.count(1)
        self._positions: dict[str, BrokerPosition] = {}
        self._cash = Decimal(cash)
        self._slippage_ticks = slippage_ticks
        self._pending: dict[str, _PendingOrder] = {}
        self._last_close: dict[str, int] = {}
        self._now: datetime | None = None
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def submit(self, req: OrderRequest) -> SubmitResult:
        if req.qty <= 0:
            return SubmitResult(ok=False, error="qty must be positive")
        now = self._now
        if now is None:
            # on_bar()가 한 번도 안 불림 — 재생 시작 전(또는 시세 없는) 제출은 거부한다.
            # 시장가의 "기준가 없음" 거부와 같은 이유: TTL 기산점도 없다.
            return SubmitResult(ok=False, error="no market data yet — on_bar() 선행 필요")

        order_no = f"SIM{next(self._seq):08d}"
        limit = req.limit_price_ticks
        if limit is None:
            return self._fill_market(order_no, req, now)

        self._pending[order_no] = _PendingOrder(req=req, limit_price_ticks=limit, submitted_at=now)
        return SubmitResult(ok=True, broker_order_no=order_no)

    async def cancel(self, broker_order_no: str) -> bool:
        return self._pending.pop(broker_order_no, None) is not None

    async def positions(self) -> list[BrokerPosition]:
        return [p for p in self._positions.values() if p.qty != 0]

    async def account(self) -> BrokerAccount:
        return BrokerAccount(cash=self._cash, margin_used=Decimal(0), total_equity=self._cash)

    async def probe_front_month(self, product: str) -> str:
        return f"{product}_FRONT_SIM"

    # ------------------------------------------------------------------ 재생 전용 API

    def on_bar(self, bar: BarClosed) -> list[Fill]:
        """
        입력: 재생 중인(과거) 완성봉 1개. DigitalTwinEngine이 시간 순서대로 호출한다.
        계산: 시뮬레이션 시계·최근 종가를 갱신하고, 이 심볼의 pending 지정가 주문을 전부
             순회해 터치 체결 → 아니면 TTL 만료 순으로 판정한다. 1분봉이 아니면(더 굵은
             Horizon) 시계·종가만 갱신하지 않고 그대로 무시한다 — 모듈 docstring 참고.
        반환: 이번 봉에서 새로 발생한 Fill 목록(순서 보장 없음, 없으면 빈 리스트).
        """
        if bar.horizon != Horizon.M1:
            return []

        self._now = bar.bar_open_kst + timedelta(seconds=HORIZON_SECONDS[bar.horizon])
        self._last_close[bar.symbol] = bar.c_ticks

        now = self._now
        fills: list[Fill] = []
        for order_no in [no for no, p in self._pending.items() if p.req.symbol == bar.symbol]:
            pending = self._pending[order_no]
            if self._touched(pending, bar):
                del self._pending[order_no]
                fills.append(self._settle(order_no, pending.req, pending.limit_price_ticks, now))
            elif now - pending.submitted_at >= timedelta(milliseconds=pending.req.ttl_ms):
                del self._pending[order_no]
                mlog.log(
                    "OrderExpired",
                    "TTL 경과 — 미체결 자동 취소",
                    broker_order_no=order_no,
                    symbol=bar.symbol,
                )
        return fills

    # ------------------------------------------------------------------ 내부

    @staticmethod
    def _touched(pending: _PendingOrder, bar: BarClosed) -> bool:
        limit = pending.limit_price_ticks
        if pending.req.side == Side.LONG:
            return bar.l_ticks <= limit
        return bar.h_ticks >= limit

    def _fill_market(self, order_no: str, req: OrderRequest, now: datetime) -> SubmitResult:
        ref_price = self._last_close.get(req.symbol)
        if ref_price is None:
            return SubmitResult(ok=False, error="no market data yet — 시장가 체결 기준가 없음")
        slip = self._slippage_ticks if req.side == Side.LONG else -self._slippage_ticks
        price = ref_price + slip
        self._settle(order_no, req, price, now)
        return SubmitResult(ok=True, broker_order_no=order_no)

    def _settle(self, order_no: str, req: OrderRequest, price_ticks: int, ts: datetime) -> Fill:
        self._apply(req, price_ticks)
        return Fill(
            broker_order_no=order_no,
            symbol=req.symbol,
            qty=req.qty,
            price_ticks=price_ticks,
            ts_exchange=ts,
            pending_matched=False,  # OrderGateway.on_fill()이 실제 매칭 결과로 덮어씀
        )

    def _apply(self, req: OrderRequest, price_ticks: int) -> None:
        """실제 체결가(price_ticks)로 포지션을 갱신한다 — req.limit_price_ticks는 시장가
        주문에서 None이라 여기 쓰면 체결가가 아니라 0으로 기록되는 버그가 난다."""
        cur = self._positions.get(req.symbol)
        signed = req.qty if req.side == Side.LONG else -req.qty
        if req.kind in (OrderKind.EXIT_FULL, OrderKind.EXIT_PARTIAL) and cur is not None:
            signed = -cur.qty if req.kind == OrderKind.EXIT_FULL else signed
        new_qty = (cur.qty if cur else 0) + signed
        self._positions[req.symbol] = BrokerPosition(
            symbol=req.symbol, qty=new_qty, avg_price_ticks=price_ticks
        )
