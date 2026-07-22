"""KISBrokerAdapter — messiah.broker.base.BrokerAdapter를 KISRestClient로 구현.

REST 클라이언트가 동기(httpx.Client) 기반이라 asyncio.to_thread로 감싼다. 여러 async 호출이
동시에 to_thread로 들어와도 KISRestClient 내부의 공유 _RateLimiter가 직렬화한다(rest_client.py
설계 그대로 재사용 — 새 락 없음).

"구현됨 ≠ 검증됨" (base.py 원칙, capability_matrix.md 실측 후 사용). 이 어댑터 중 실계좌로 확인된
경로는 account()/positions()가 감싸는 get_balance() 하나뿐이다(2026-07-21, commit 3c6c9e3) — 단,
그때 실측한 것은 "호출이 성공한다"까지이고, 여기서 하는 output1/output2 필드 파싱은 공식 문서
(API ID v1_국내선물-004) Response Example 스키마 그대로이되 실응답으로 재검증되지 않았다.
submit()/cancel()은 공식 문서(v1_국내선물-001/002) 필드 구성을 따르되 전량 미검증 — 실주문은
실제 체결을 일으키므로 특히 신중히 실측할 것.
"""

from __future__ import annotations

import asyncio
from decimal import ROUND_HALF_UP, Decimal

from messiah.broker.base import BrokerAccount, BrokerAdapter, BrokerPosition, SubmitResult
from messiah.broker.kis.credentials import KISCredentials
from messiah.broker.kis.rest_client import KISRestClient
from messiah.broker.kis.token_daemon import TokenDaemon
from messiah.core.messages import OrderRequest, Side


class KISBrokerAdapter(BrokerAdapter):
    name = "kis"

    def __init__(
        self,
        creds: KISCredentials,
        tick_size: Decimal,
        token_daemon: TokenDaemon | None = None,
        rest_client: KISRestClient | None = None,
    ) -> None:
        """
        입력: KIS 인증정보, 종목 1틱의 실제 가격 크기(Decimal — 예: K200 미니선물 0.02).
             OrderRequest.limit_price_ticks(정수 틱, SYSTEM.md R2)와 KIS UNIT_PRICE(실가격)
             사이의 유일한 환산 계수다. 상품마다 다르고 이 프로젝트엔 아직 실측 확정된 상품별
             틱 크기 테이블이 없어(NEXT_TODO "KIS_RAW_FIELD_RANGES.md") 하드코딩하지 않고
             호출측이 주입한다(SYSTEM.md R4). token_daemon/rest_client는 KISRestClient와 동일한
             DI 패턴(테스트에서 MockTransport로 교체) — 생략 시 creds로 직접 구성한다.
        """
        self._creds = creds
        self._tick_size = tick_size
        self._token_daemon = token_daemon or TokenDaemon(creds)
        self._rest = rest_client or KISRestClient(creds, self._token_daemon)
        self.connected = False

    async def connect(self) -> None:
        """계산: 토큰 발급을 즉시 강제해, 주문 시점이 아니라 연결 시점에 인증 실패를 드러낸다."""
        await asyncio.to_thread(self._token_daemon.get_token)
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def submit(self, req: OrderRequest) -> SubmitResult:
        """
        계산: Side.LONG→BUY, Side.SHORT→SELL(SimBroker와 동일 규약 — req.side가 이미 이 주문의
             매매방향을 확정한 값이라 재해석하지 않는다). limit_price_ticks가 None이면 시장가
             (ORD_DVSN_CD="02", UNIT_PRICE=0 — 문서: "시장가나 최유리 지정가인 경우 0으로 입력"),
             아니면 지정가(ORD_DVSN_CD="01")로 tick_size를 곱해 실가격을 만든다.
        해석: rt_cd != "0"(KIS 논리적 거부, HTTP 200)은 SubmitResult(ok=False)로 변환한다.
             HTTP 자체 실패(4xx/5xx)는 rest_client가 예외로 던지므로 여기서 잡지 않고 그대로
             전파 — 상위 Order State Machine이 REJECTED로 기록할 재료.
        실패 조건: Side.OPTION/NO_TRADE는 이 레이어에서 무효 — Risk/Sizing이 LONG/SHORT로
             이미 확정해 넘겨야 한다(계명 1: 전략 코드가 직접 이 메서드를 부르면 안 됨).
        """
        if req.side not in (Side.LONG, Side.SHORT):
            raise ValueError(f"KIS 주문은 Side.LONG/SHORT만 허용 — 받은 값: {req.side!r}")

        side = "BUY" if req.side == Side.LONG else "SELL"
        if req.limit_price_ticks is None:
            order_dvsn_cd, price = "02", Decimal(0)
        else:
            order_dvsn_cd, price = "01", self._ticks_to_price(req.limit_price_ticks)

        response = await asyncio.to_thread(
            self._rest.submit_order,
            symbol=req.symbol,
            side=side,
            qty=req.qty,
            price=price,
            order_dvsn_cd=order_dvsn_cd,
        )
        if response.get("rt_cd") != "0":
            return SubmitResult(ok=False, error=response.get("msg1", "KIS 주문 거부(msg1 없음)"))
        return SubmitResult(ok=True, broker_order_no=response["output"]["ODNO"])

    async def cancel(self, broker_order_no: str) -> bool:
        """
        계산: rest_client.cancel_order()로 전량취소를 요청한다 — 이 인터페이스는 broker_order_no만
             받아 원주문의 수량/가격/호가유형을 모르므로 부분취소는 지원 범위 밖(rest_client.py
             cancel_order 문서 참고).
        해석: rt_cd == "0"이면 True. HTTP 자체 실패는 예외로 전파.
        """
        response = await asyncio.to_thread(self._rest.cancel_order, org_order_no=broker_order_no)
        return response.get("rt_cd") == "0"

    async def positions(self) -> list[BrokerPosition]:
        """
        계산: get_balance()의 output1(상품별 잔고 배열)을 파싱. sll_buy_dvsn_name이 "SLL"/"매도"면
             음수, 그 외(BUY/매수)면 양수로 부호를 만든다(BrokerPosition.qty 규약: +Long/-Short).
             잔고수량 0(당일 청산되어 sll_buy_dvsn_name이 빈칸인 행 포함)은 제외.
        해석: 필드명(shtn_pdno/cblc_qty/sll_buy_dvsn_name/ccld_avg_unpr1)은 공식 문서(API ID
             v1_국내선물-004) Layout 표 기준 — 실응답으로 재검증 전까지 미검증(모듈 docstring 참고).
        """
        response = await asyncio.to_thread(self._rest.get_balance)
        rows = response.get("output1") or []
        positions: list[BrokerPosition] = []
        for row in rows:
            qty = int(row.get("cblc_qty") or "0")
            if qty == 0:
                continue
            signed_qty = -qty if row.get("sll_buy_dvsn_name") in ("SLL", "매도") else qty
            avg_price = Decimal(row.get("ccld_avg_unpr1") or "0")
            positions.append(
                BrokerPosition(
                    symbol=row.get("shtn_pdno", ""),
                    qty=signed_qty,
                    avg_price_ticks=self._price_to_ticks(avg_price),
                )
            )
        return positions

    async def account(self) -> BrokerAccount:
        """
        계산: get_balance()의 output2(계좌 요약)에서 dnca_cash(예수금현금)를 cash, mgna_tota
             (증거금총액)를 margin_used, prsm_dpast(추정예탁자산)를 total_equity로 매핑한다
             (공식 문서 Layout 표 기준 — positions()와 동일하게 실응답 재검증 전 미검증).
        """
        response = await asyncio.to_thread(self._rest.get_balance)
        out2 = response.get("output2") or {}
        return BrokerAccount(
            cash=Decimal(out2.get("dnca_cash") or "0"),
            margin_used=Decimal(out2.get("mgna_tota") or "0"),
            total_equity=Decimal(out2.get("prsm_dpast") or "0"),
        )

    async def probe_front_month(self, product: str) -> str:
        raise NotImplementedError(
            "KIS 종목코드 마스터파일 연동 미구현 — 단일 종목 조회(get_quote)뿐이라 근월물 코드를"
            " 자체 확정할 수단이 없다(tr_codes.py PATH_FUTUREOPTION_QUOTE 주석·NEXT_TODO 참고)."
        )

    # ------------------------------------------------------------------ 틱 <-> 실가격
    def _ticks_to_price(self, ticks: int) -> Decimal:
        return Decimal(ticks) * self._tick_size

    def _price_to_ticks(self, price: Decimal) -> int:
        if self._tick_size == 0:
            return 0
        return int((price / self._tick_size).to_integral_value(rounding=ROUND_HALF_UP))
