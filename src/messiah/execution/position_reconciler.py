"""Position Reconciler — 「오늘 얼마를 벌었나」에 답할 수 있게 만드는 장부 (2026-09-17 F-114).

## 왜 없었고, 없어서 무엇이 안 됐나

2026-07-29부터 오늘까지 `logs/g2_daily_returns.jsonl` **36행 전부** `"return": 0.0`이었다.
그 0은 본전이 아니라 **아무것도 안 잰 값**이다 — `_daily_close()`가 수익률을 `SimBroker.
account().total_equity`의 변화율로 계산하는데, 그 `total_equity`는 `SimBroker._cash`이고
`_cash`는 `__init__` 이후 한 번도 바뀌지 않는다(그 브로커는 손익을 `_realized_pnl_ticks`에
**틱으로** 따로 쌓는다, 2026-08-23). 즉 손익을 세는 축과 보고하는 축이 서로 안 닿아 있었다.

같은 이유로 `SelfEvalReport.n_fills`는 항상 `None`, `WiringCompleteness.fills_countable`은
`scripts/run_g2_paper_trading.py`에 **상수 False**로 박혀 있었다. 2026-09-16 첫 실거래
(진입 2·청산 2 완주)와 2026-09-17 실거래(진입 1·EOD 강제청산 1)가 그 상태로 지나갔다.

## 이 모듈이 세는 것과, 왜 게이트웨이에 붙나

`Fill` 메시지에는 **방향이 없다**(`core/messages.Fill`: symbol·qty·price_ticks·
broker_order_no뿐). 방향은 그 체결을 만든 `OrderRequest`에만 있고, 그 둘을 이어 주는 곳은
시스템에 딱 하나 — `OrderGateway.on_fill()`의 pending 매칭이다(계명 1: 주문 경로는 하나).
그래서 이 장부는 버스를 구독하지 않고 게이트웨이가 직접 먹인다. `accepted_orders` 계수기를
게이트웨이 안에 둔 것과 같은 이유다: *"호출자마다 세면 경로가 하나 늘 때마다 조용히 빠진다"*.

## 대사(reconcile)가 잡으려는 것은 「내 장부 != 브로커 장부」다

L12 — **브로커가 진실원천이고 로컬 기억은 언제나 그보다 못하다.** 이 장부는 게이트웨이가
본 `Fill`만으로 세워지므로, 체결이 새거나(이벤트 유실) 겹쳐 들어오면(중복 배달) 브로커
포지션과 어긋난다. 그 어긋남이 미륵이 최대 단일 손실 사건(유령 포지션)의 형태였다.

계산 규칙 자체는 `execution/position_math.py` 한 곳에만 있고 `SimBroker`도 같은 것을 쓴다 —
두 벌로 두면 불일치가 「사건의 불일치」인지 「구현의 불일치」인지 못 가린다.

## 못 재는 것을 재는 척하지 않는다

- **단위는 틱이다.** 원 환산에 필요한 계약 승수(원/지수포인트)가 이 저장소 어디에도 없다
  (`configs/instance.yaml`에 `futures_tick_size`는 있지만 승수는 없다). 없는 상수를 코드가
  지어내는 것이 R4가 금지하는 바로 그것이다 — 승수가 정해지기 전까지 **자본 대비 수익률로
  환산하지 않는다.** 그래서 이 모듈이 결선돼도 `pnl_measurable`은 아직 True가 되지 않는다
  (`models/wiring_completeness.py`의 `STAGE_NO_PNL_UNIT`).
- **미실현손익은 종가를 모르면 None이다.** 0.0("평가손익이 0이었다")과 다르다(L18).
- **대사를 안 돌렸으면 `reconciled`는 False가 아니라 `None`이다.** 안 해 본 것과 해 보니
  틀린 것은 전혀 다른 사실이고, 그 둘을 섞은 것이 이 프로젝트가 네 번 겪은 실패 형태다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Sequence

from messiah.broker.base import BrokerAdapter, BrokerPosition
from messiah.core import logging as mlog
from messiah.core.messages import Fill, OrderAck, OrderRequest
from messiah.core.timeutil import now_utc
from messiah.execution.position_math import (
    PositionState,
    apply_fill,
    closes_position,
    signed_fill_qty,
)


@dataclass(frozen=True)
class PositionDelta:
    """한 심볼에서 로컬 장부와 브로커 장부가 갈린 폭. 한쪽에만 있으면 없는 쪽을 0으로 본다 —
    "장부에 없다"와 "0계약"은 **수량에 관한 한** 같은 사실이다."""

    symbol: str
    local_qty: int
    broker_qty: int

    @property
    def qty_diff(self) -> int:
        return self.local_qty - self.broker_qty


@dataclass(frozen=True)
class Reconciliation:
    """대사 1회의 결과.

    `matched`는 **수량만** 본다 — 평균단가는 브로커가 반올림 규칙을 달리 쓸 수 있고
    (`SimBroker`는 `round()`), 그 차이는 유령 포지션이 아니다. 수량 불일치만이 "체결 사건이
    새거나 겹쳤다"를 뜻한다."""

    checked_at: datetime
    matched: bool
    deltas: list[PositionDelta] = field(default_factory=list)

    @property
    def mismatches(self) -> list[PositionDelta]:
        return [d for d in self.deltas if d.qty_diff != 0]


class PositionReconciler:
    """게이트웨이가 먹이는 주문·접수·체결로 장부를 세우고, 브로커 진실원천과 대사한다."""

    def __init__(self) -> None:
        self._orders: list[OrderRequest] = []
        self._acks: list[OrderAck] = []
        self._fills: list[Fill] = []
        self._positions: dict[str, PositionState] = {}
        self._realized_pnl_ticks = 0.0
        # 매칭된 요청 없이 들어온 체결 — 게이트웨이가 이미 CRITICAL 정지를 걸지만
        # (`on_fill()` 미매칭 경로), 장부 쪽에서도 **반영 못 한 체결의 수**를 남겨야
        # `n_fills`를 믿어도 되는지가 리포트에서 판정된다.
        self._unattributed_fills = 0
        # 닫힌 거래의 실현손익(틱) — R10(연속손실 3회) 공급원 (2026-09-22 F-120).
        # 누계(`_realized_pnl_ticks`)와 **따로 든다**: R10이 필요한 것은 총액이 아니라
        # "직전 거래가 손실이었나"의 순서열이고, 누계 하나로는 그걸 되돌릴 수 없다.
        self._closed_trades: list[float] = []
        self._last: Reconciliation | None = None

    # ---- 게이트웨이가 먹인다 -------------------------------------------
    def record_order(self, req: OrderRequest) -> None:
        self._orders.append(req)

    def record_ack(self, ack: OrderAck) -> None:
        self._acks.append(ack)

    def record_fill(self, fill: Fill, request: OrderRequest | None) -> None:
        """체결 1건을 장부에 반영한다.

        `request`가 `None`이면 **포지션을 움직이지 않는다** — 방향을 모르는 체결을 임의
        방향으로 반영하면 그게 바로 유령 포지션이다(L1). 대신 센다.
        """
        self._fills.append(fill)
        if request is None:
            self._unattributed_fills += 1
            mlog.log(
                "PositionLedgerUnattributedFill",
                "매칭된 주문이 없는 체결 — 방향을 모르므로 장부에 반영하지 않는다",
                symbol=fill.symbol,
                broker_order_no=fill.broker_order_no,
                qty=fill.qty,
            )
            return
        current = self._positions.get(fill.symbol)
        signed = signed_fill_qty(
            side=request.side,
            kind=request.kind,
            qty=fill.qty,
            current_qty=current.qty if current else 0,
        )
        closed = closes_position(current_qty=current.qty if current else 0, signed_qty=signed)
        updated, realized = apply_fill(current, signed_qty=signed, price_ticks=fill.price_ticks)
        self._realized_pnl_ticks += realized
        self._positions[fill.symbol] = updated
        if closed:
            # **닫힌 것만** 쌓는다. 본전 청산(realized == 0.0)도 여기 들어온다 — R10은
            # 그것을 "손실이 아니다"로 읽어 스트릭을 끊어야 하고, 미청산과 섞이면 못 읽는다
            # (`execution/position_math.closes_position()` docstring).
            self._closed_trades.append(realized)

    # ---- 읽기 ----------------------------------------------------------
    @property
    def orders(self) -> Sequence[OrderRequest]:
        return tuple(self._orders)

    @property
    def acks(self) -> Sequence[OrderAck]:
        return tuple(self._acks)

    @property
    def fills(self) -> Sequence[Fill]:
        return tuple(self._fills)

    @property
    def n_fills(self) -> int:
        """오늘 게이트웨이를 통과한 체결 건수. **0은 진짜 0이다** — 이 장부가 결선된 이상
        「셀 수 없음」은 더 이상 이 값으로 표현되지 않는다(장부 자체를 안 붙였을 때만 호출자가
        None을 내보내고, 그 판정은 호출자 몫이다)."""
        return len(self._fills)

    @property
    def unattributed_fills(self) -> int:
        return self._unattributed_fills

    @property
    def realized_pnl_ticks(self) -> float:
        """오늘 **닫은 만큼**의 손익(틱). 열려 있는 포지션은 안 들어간다."""
        return self._realized_pnl_ticks

    def drain_closed_trades(self) -> list[float]:
        """마지막 호출 이후 **새로 닫힌** 거래의 실현손익(틱)을 반환하고 비운다.

        `models/shadow_manager.py`의 `drain_fills()`와 같은 꼴이다 — 같은 사건을 두 번
        먹이지 않으려면 읽은 쪽이 비워야 하고, 그 규약이 이름에 있어야 한다.

        R10(연속손실 3회)이 유일한 소비자다. 누계 `realized_pnl_ticks`를 대신 쓰면
        안 되는 이유는 `_closed_trades` 선언부 주석에 있다.
        """
        drained = self._closed_trades
        self._closed_trades = []
        return drained

    @property
    def positions(self) -> dict[str, PositionState]:
        return {s: p for s, p in self._positions.items() if p.qty != 0}

    @property
    def last_reconciliation(self) -> Reconciliation | None:
        return self._last

    def unrealized_pnl_ticks(self, last_close_ticks: Mapping[str, int]) -> float | None:
        """열려 있는 포지션의 평가손익(틱) — 주어진 종가 기준. 종가를 모르는 심볼이 하나라도
        있으면 **None**이다(0.0이 아니다, L18). 포지션이 아예 없으면 0.0 — 그건 아는 0이다."""
        total = 0.0
        for symbol, position in self._positions.items():
            if position.qty == 0:
                continue
            last = last_close_ticks.get(symbol)
            if last is None:
                return None
            total += (last - position.avg_price_ticks) * position.qty
        return total

    # ---- 대사 ----------------------------------------------------------
    async def reconcile(self, broker: BrokerAdapter) -> Reconciliation:
        """브로커 포지션(진실원천, L12)과 로컬 장부를 맞춰 본다.

        조회가 실패하면 **일치했다고 말하지 않는다** — 예외를 그대로 올린다. 호출자
        (`_daily_close()`)가 "대사 못 함"을 리포트에 적을 수 있어야 하고, 여기서 삼켜
        `matched=False`로 돌려주면 "해 보니 틀렸다"와 구별이 사라진다.
        """
        broker_positions: list[BrokerPosition] = await broker.positions()
        broker_qty = {p.symbol: p.qty for p in broker_positions if p.qty != 0}
        local_qty = {s: p.qty for s, p in self._positions.items() if p.qty != 0}
        deltas = [
            PositionDelta(
                symbol=symbol,
                local_qty=local_qty.get(symbol, 0),
                broker_qty=broker_qty.get(symbol, 0),
            )
            for symbol in sorted(set(local_qty) | set(broker_qty))
        ]
        result = Reconciliation(
            checked_at=now_utc(),
            matched=all(d.qty_diff == 0 for d in deltas),
            deltas=deltas,
        )
        self._last = result
        if result.matched:
            mlog.log(
                "PositionReconciled",
                f"장부 일치 — 포지션 {len(deltas)}종목 · 체결 {self.n_fills}건 · "
                f"실현손익 {self._realized_pnl_ticks:+.1f}틱",
                n_positions=len(deltas),
                n_fills=self.n_fills,
                realized_pnl_ticks=self._realized_pnl_ticks,
            )
        else:
            mlog.log(
                "PositionReconcileMismatch",
                "로컬 장부와 브로커 포지션이 다르다 — 체결 이벤트가 새거나 겹쳤다(L12)",
                mismatches=[
                    {"symbol": d.symbol, "local_qty": d.local_qty, "broker_qty": d.broker_qty}
                    for d in result.mismatches
                ],
                n_fills=self.n_fills,
                unattributed_fills=self._unattributed_fills,
            )
        return result

    def summary(self, *, last_close_ticks: Mapping[str, int] | None = None) -> dict:
        """리포트·JSONL에 그대로 실을 수 있는 한 덩어리.

        `pnl_unit`을 **값과 같은 자리에** 둔다 — 틱을 원으로 오독하는 것을 막는 유일한
        구조적 장치다(`SimBroker.pnl_unit`이 같은 이유로 클래스 속성이다)."""
        unrealized: float | None = None
        if last_close_ticks is not None:
            measured = self.unrealized_pnl_ticks(last_close_ticks)
            unrealized = None if measured is None else round(measured, 4)
        return {
            "pnl_unit": "ticks",
            "n_fills": self.n_fills,
            "unattributed_fills": self._unattributed_fills,
            "realized_pnl_ticks": round(self._realized_pnl_ticks, 4),
            "unrealized_pnl_ticks": unrealized,
            "open_positions": {s: p.qty for s, p in sorted(self.positions.items())},
            # 안 해 본 것(None)과 해 보니 틀린 것(False)을 섞지 않는다 — 모듈 docstring.
            "reconciled": self._last.matched if self._last is not None else None,
            "reconcile_mismatches": (
                None
                if self._last is None
                else [
                    {"symbol": d.symbol, "local_qty": d.local_qty, "broker_qty": d.broker_qty}
                    for d in self._last.mismatches
                ]
                or None
            ),
        }
