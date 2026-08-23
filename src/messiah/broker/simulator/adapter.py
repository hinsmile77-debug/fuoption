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

## 손익은 **틱**으로 계산한다 (2026-08-23)

2026-08-23까지 이 브로커는 손익을 아예 계산하지 않았다 — `_apply()`가 포지션만 갱신하고
`self._cash`는 `__init__` 이후 한 번도 안 바뀌었다. 2,000틱을 먹고 청산해도 0원이었다
(실측). 그 0이 `backtest/harness.py`를 타고 `Validator.validate_performance()`에 들어가면
`max_drawdown`과 `negative_window_ratio`가 **둘 다 PASS로 나온다**(각 0.0 < 임계) —
아무것도 안 잰 계기가 초록 도장 두 개를 찍는 형태다(마흐디 L18 · 2026-08-21 F-14).

### 왜 원이 아니라 틱인가

원으로 환산하려면 **계약 승수(원/지수포인트)** 가 필요한데 그 값이 이 저장소 어디에도
없다(`configs/instance.yaml`에 `futures_tick_size: 0.02`는 있지만 승수는 없다).
없는 상수를 코드가 지어내는 것이 R4가 금지하는 바로 그것이다. 비용 모델
(`risk/cost_model.py`)이 이미 전부 틱으로 계산하므로 단위를 그쪽에 맞춘다.

`realized_pnl_ticks`가 그 값이고, `pnl_unit`이 단위를 코드로 말한다. **`_cash`는
그대로 둔다** — 원과 틱을 한 필드에 섞으면 그게 더 나쁜 거짓말이다.

### 이 단위로 무엇을 잴 수 있고 무엇을 못 재나

- **Sharpe**: 잰다. 척도 불변이라 틱으로 충분하다.
- **negative_window_ratio**: 잰다. 부호만 본다.
- **max_drawdown**: **못 잰다.** `ValidatorConfig.max_drawdown_limit = 0.3`은 *자본 대비
  비율*이고, 틱을 자본 비율로 바꾸려면 승수가 있어야 한다. 승수가 정해지기 전까지 이
  관문은 미측정이며, `run_g1_walk_forward.py`가 그렇게 보고한다.
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

    #: 이 브로커가 손익을 계산하는가 (2026-08-23부터 True). 성과 관문을 채점하려는
    #: 소비자는 이 값을 먼저 물어야 한다.
    computes_pnl = True

    #: 손익의 **단위** — 원이 아니라 틱이다(모듈 docstring "왜 원이 아니라 틱인가").
    #: 자본 대비 비율을 요구하는 관문(`max_drawdown`)은 이 단위로 채점할 수 없다.
    pnl_unit = "ticks"

    def __init__(self, cash: int = 50_000_000, slippage_ticks: int = 1) -> None:
        self._seq = itertools.count(1)
        self._positions: dict[str, BrokerPosition] = {}
        self._cash = Decimal(cash)
        self._slippage_ticks = slippage_ticks
        self._pending: dict[str, _PendingOrder] = {}
        self._last_close: dict[str, int] = {}
        self._now: datetime | None = None
        self.connected = False
        # **거래가 몇 번 일어났는가** (2026-08-23). 손익이 0이라는 사실과 "한 번도 거래
        # 안 했다"는 사실은 다르고, 자본 곡선만 보면 둘이 같은 모양이다 — 2026-08-21까지
        # 17거래일 연속 주문 0건이 그 형태로 숨어 있었다.
        self._n_accepted = 0
        self._n_fills = 0
        self._n_expired = 0
        # 실현손익(틱) — 포지션을 줄이거나 닫은 만큼만 쌓인다. 미실현은 여기 안 들어간다
        # (`unrealized_pnl_ticks()`가 따로 답한다).
        self._realized_pnl_ticks = 0.0

    @property
    def n_accepted_orders(self) -> int:
        """거부되지 않고 접수된 주문 수 — 거절(수량 0·시세 없음)은 세지 않는다."""
        return self._n_accepted

    @property
    def n_fills(self) -> int:
        """실제로 체결된 건수(시장가 즉시 체결 + 지정가 터치 체결)."""
        return self._n_fills

    @property
    def n_expired_orders(self) -> int:
        """TTL로 자동 취소된 지정가 주문 수 — 「주문은 냈는데 안 붙었다」를 「주문 자체가
        없었다」와 가른다. 둘 다 체결 0이지만 진단이 정반대다."""
        return self._n_expired

    @property
    def realized_pnl_ticks(self) -> float:
        """지금까지 **닫은 만큼**의 손익(틱). 열려 있는 포지션은 안 들어간다."""
        return self._realized_pnl_ticks

    def unrealized_pnl_ticks(self) -> float | None:
        """열려 있는 포지션의 평가손익(틱) — 마지막 종가 기준. 종가를 모르면 None.

        창 끝에 포지션이 열린 채 끝나면 실현손익만으로는 그 창의 성과가 왜곡된다.
        못 재면 0이 아니라 **None**이다(L18) — 호출부가 "평가 못 했다"를 알아야 한다.
        """
        total = 0.0
        for position in self._positions.values():
            if position.qty == 0:
                continue
            last = self._last_close.get(position.symbol)
            if last is None:
                return None
            total += (last - position.avg_price_ticks) * position.qty
        return total

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
            result = self._fill_market(order_no, req, now)
            if result.ok:
                self._n_accepted += 1
            return result

        self._pending[order_no] = _PendingOrder(req=req, limit_price_ticks=limit, submitted_at=now)
        self._n_accepted += 1
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
                self._n_expired += 1
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
        # 시장가 즉시 체결과 지정가 터치 체결이 **둘 다** 여기를 지난다 — 계수기를 여기
        # 하나에만 두면 두 경로가 갈릴 수 없다.
        self._n_fills += 1
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
        """실제 체결가로 포지션을 갱신하고 **닫힌 만큼의 손익을 실현한다** (2026-08-23).

        `req.limit_price_ticks`가 아니라 `price_ticks`를 쓴다 — 전자는 시장가 주문에서
        None이라 여기 쓰면 체결가가 아니라 0으로 기록되는 버그가 난다.

        ## 평균단가를 덮어쓰지 않는다

        종전엔 매 체결마다 `avg_price_ticks=price_ticks`로 **통째로 덮었다.** 같은 방향으로
        물타기하면 원래 진입가가 사라지고, 그 상태로 청산하면 손익이 마지막 진입가 기준이
        된다. 손익을 계산하지 않던 동안에는 드러나지 않던 결함이다.

        ## 네 갈래

        기존 수량 `q0`, 이번 체결의 부호 있는 수량 `s`일 때:

        - `q0 == 0` — 신규 진입. 평균단가 = 체결가.
        - `sign(s) == sign(q0)` — 물타기. 평균단가 = 수량가중평균. 실현 없음.
        - `sign(s) != sign(q0)` 이고 `|s| <= |q0|` — 부분/전량 청산.
          닫힌 `|s|`계약만큼 실현하고 평균단가는 유지한다.
        - `sign(s) != sign(q0)` 이고 `|s| > |q0|` — 전량 청산 후 반대로 뒤집기.
          `|q0|`만큼 실현하고, 남는 수량의 평균단가는 이번 체결가다.

        실현손익 = `닫은수량 × (청산가 − 진입가) × sign(기존포지션)`. LONG은 오를 때
        이익이고 SHORT은 그 반대라 부호 곱이 필요하다. 단위는 **틱**이다(모듈 docstring).
        """
        cur = self._positions.get(req.symbol)
        signed = req.qty if req.side == Side.LONG else -req.qty
        if req.kind in (OrderKind.EXIT_FULL, OrderKind.EXIT_PARTIAL) and cur is not None:
            signed = -cur.qty if req.kind == OrderKind.EXIT_FULL else signed

        q0 = cur.qty if cur else 0
        entry = cur.avg_price_ticks if cur else price_ticks
        new_qty = q0 + signed

        if q0 == 0 or (q0 > 0) == (signed > 0):
            # 신규 진입 또는 같은 방향 물타기 — 실현 없음, 평균단가만 갱신.
            if q0 == 0:
                avg = price_ticks
            else:
                avg = round((abs(q0) * entry + abs(signed) * price_ticks) / (abs(q0) + abs(signed)))
        else:
            closed = min(abs(signed), abs(q0))
            direction = 1 if q0 > 0 else -1
            self._realized_pnl_ticks += closed * (price_ticks - entry) * direction
            # 뒤집혔으면 남은 수량은 이번 체결가가 진입가다. 아니면 원래 진입가를 지킨다.
            avg = price_ticks if (new_qty != 0 and (new_qty > 0) != (q0 > 0)) else entry

        self._positions[req.symbol] = BrokerPosition(
            symbol=req.symbol, qty=new_qty, avg_price_ticks=avg
        )
