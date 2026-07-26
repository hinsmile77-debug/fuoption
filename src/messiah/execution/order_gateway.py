"""OrderGateway — 시스템 유일의 주문 경로 (계명 1, 레슨런 L1).

미륵이 최대 단일 손실 사건(유령 포지션, -675만원)의 근본 원인은
"pending 선등록 없이 주문 전송"이 여러 청산 경로에 흩어져 재발한 것이었다.

메시아의 해법: 주문 전송 함수는 이 클래스의 submit() 하나뿐이며,
pending 등록은 그 내부에서 원자적으로 수행된다 — 우회가 구조적으로 불가능하다.

흐름:
    submit(req):
        1) pending 원자 등록 (전송 전!)
        2) broker.submit()
        3) 실패 시 pending 롤백
    on_fill(fill):
        pending 매칭 성공 → 정상 체결 흐름
        매칭 실패        → FillUnmatched CRITICAL + 거래 정지 요청
                           (유령 포지션을 만들지 않는다 — 모르는 체결은 사람을 부른다)
"""

from __future__ import annotations

import asyncio

from messiah.broker.base import BrokerAdapter, SubmitResult
from messiah.core.logging import log
from messiah.core.messages import Fill, OrderAck, OrderKind, OrderRequest
from messiah.core.timeutil import now_utc


class PendingRegistry:
    """미체결 주문 장부. 키 = f"{symbol}:{broker_order_no}" (접수 전엔 요청 ID로 가등록)."""

    def __init__(self) -> None:
        self._pending: dict[str, OrderRequest] = {}
        self._lock = asyncio.Lock()

    async def register(self, key: str, req: OrderRequest) -> None:
        async with self._lock:
            if key in self._pending:
                raise RuntimeError(f"pending 중복 등록 시도: {key} — 이중 발주 차단")
            self._pending[key] = req
            log(
                "OrderPendingSet",
                "pending registered",
                key=key,
                kind=req.kind.value,
                symbol=req.symbol,
                qty=req.qty,
            )

    async def rekey(self, old: str, new: str) -> None:
        """브로커 접수 후 요청ID 키 → 주문번호 키로 전환."""
        async with self._lock:
            self._pending[new] = self._pending.pop(old)

    async def pop_match(self, symbol: str, broker_order_no: str) -> OrderRequest | None:
        async with self._lock:
            return self._pending.pop(f"{symbol}:{broker_order_no}", None)

    async def rollback(self, key: str) -> None:
        async with self._lock:
            self._pending.pop(key, None)

    def snapshot(self) -> dict[str, OrderRequest]:
        return dict(self._pending)


class OrderGateway:
    """모든 진입·청산·헤지·비상 주문의 유일한 관문."""

    def __init__(self, broker: BrokerAdapter) -> None:
        self._broker = broker
        self._pending = PendingRegistry()
        self._halted = False

    @property
    def halted(self) -> bool:
        return self._halted

    async def submit(self, req: OrderRequest) -> OrderAck | None:
        """pending 선등록 → 전송 → 실패 시 롤백. (L1 패턴의 유일한 구현처)

        `halted` 상태에서도 `kind=EMERGENCY`는 통과시킨다 — Ver 2.0 §5 "Kill Switch 트리거 시:
        신규 차단 → 전 포지션 청산 절차"(원문)는 청산 자체를 차단 대상으로 두지 않는다.
        `halted`가 EMERGENCY까지 막으면 Kill Switch가 스스로 청산을 못 하는 모순이 생긴다
        (Ver 2.0 §9 W24~26, `risk/kill_switch.py` 도입 중 실측으로 발견)."""
        if self._halted and req.kind != OrderKind.EMERGENCY:
            log("OrderSubmit", "rejected: gateway halted", request_id=req.msg_id)
            return None

        temp_key = f"{req.symbol}:req-{req.msg_id}"
        await self._pending.register(temp_key, req)  # 1) 전송 전 원자 등록

        result: SubmitResult = await self._broker.submit(req)  # 2) 전송
        if not result.ok:
            await self._pending.rollback(temp_key)  # 3) 실패 롤백
            log("OrderSubmit", "broker rejected", request_id=req.msg_id, error=result.error)
            return None

        final_key = f"{req.symbol}:{result.broker_order_no}"
        await self._pending.rekey(temp_key, final_key)
        log(
            "OrderSubmit", "accepted", request_id=req.msg_id, broker_order_no=result.broker_order_no
        )
        return OrderAck(
            instance_id=req.instance_id,
            request_id=req.msg_id,
            broker_order_no=result.broker_order_no,
            pending_key=final_key,
        )

    async def on_fill(self, fill: Fill) -> Fill:
        """체결 수신. 매칭 실패는 유령 포지션이 아니라 CRITICAL 정지다 (L1)."""
        matched = await self._pending.pop_match(fill.symbol, fill.broker_order_no)
        if matched is not None:
            log(
                "FillMatched",
                "fill matched",
                broker_order_no=fill.broker_order_no,
                symbol=fill.symbol,
                qty=fill.qty,
            )
            return fill.model_copy(update={"pending_matched": True})

        # 미매칭: 절대 반대방향 신규 포지션으로 해석하지 않는다.
        self._halted = True
        log(
            "FillUnmatched",
            "UNMATCHED FILL — trading halted, human required",
            broker_order_no=fill.broker_order_no,
            symbol=fill.symbol,
            qty=fill.qty,
            ts=now_utc(),
        )
        return fill.model_copy(update={"pending_matched": False})

    async def resume(self, operator: str) -> None:
        """사람 확인 후에만 재개 (Kill Switch와 동일 철학)."""
        log("OrderSubmit", "gateway resumed by operator", operator=operator)
        self._halted = False

    async def halt(self, reason: str) -> None:
        """긴급 정지 공개 진입점 — `resume()`과 대칭(Ver 2.0 §9 W24~26, `risk/kill_switch.py`
        전용). 기존엔 `on_fill()`의 미매칭 체결 CRITICAL 경로에서만 내부적으로 halted=True로
        전환됐음 — Kill Switch처럼 체결과 무관한 사유(일일손실 한도 등)로도 정지시켜야 하는
        상위 감시자를 위해 명시적 진입점을 신설했다."""
        self._halted = True
        log("OrderSubmit", f"gateway halted: {reason}", reason=reason)
