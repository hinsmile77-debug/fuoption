"""옵션체인 시세호가(OP) REST 폴링 — `InvestorFlowPoller`와 동일 패턴의 두 번째 실사용처
(Ver 2.0 §9 W27~29, Options AI 선행 인프라 갭 해소, `Docs/capability_matrix.md`
"OP(옵션체인 그릭스) REST 폴링 수집기 미착수" 항목).

`FixedTickScheduler.run_forever(poller.poll_once)`로 구동된다. 매 호출마다
`IndexDerivativesMaster.nearest_expiry_chain()`(이미 실측된 종목코드 마스터 조회,
2026-07-22)으로 현재 콜/풋 근월물 체인을 얻고, 각 다리(leg)를 `KISRestClient.
get_asking_price()`(이미 TR_ID·헤더 실측된 REST, `tests/broker/test_kis_rest_client.py`)로
순차 조회해 `raw.option_chain.{underlying}`에 `OptionQuoteSnapshot`을 발행한다.

## 스코프 경계 — 필드 파싱은 별도 과제 (InvestorFlowPoller와 동일)

`OptionQuoteSnapshot`(core/messages.py) 모듈 docstring 참고 — 이 폴러는 KIS 응답의 구체
필드(매도/매수 호가가 몇 번째 필드인지)를 해석하지 않고 `raw` 그대로 발행한다. 필드 매핑을
확정할 근거(docs/efriend 엑셀·실계좌 실측 캡처)가 이 세션에 없어서다. `strategy/options/
surface.py`로 이어지는 정규화는 그 근거가 생긴 뒤 별도 세션의 몫.

## 유량제한

`InvestorFlowPoller`와 동일 — `KISRestClient`가 내부 `_RateLimiter`(또는 주입된
`RedisRateLimiter`)로 이미 페이싱하므로 이 폴러는 별도 유량 제어를 하지 않는다. 체인 다리
수만큼 순차 호출하므로 체인이 클수록 한 번의 `poll_once()`가 걸리는 시간이 길어진다 —
5분 주기(Ver 1.3 §2 "Vol Engine: 5분봉 완성 시") 안에 전 다리를 끝내는지는 실측 대상
(알려진 갭, MultiSymbolTickCollector·InvestorFlowPoller와 같은 "라이브 미검증" 부류).
"""

from __future__ import annotations

import asyncio

from messiah.broker.kis.rest_client import KISRestClient
from messiah.broker.kis.symbol_master import IndexDerivativesMaster, OptionLeg
from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_RAW, BusLike
from messiah.core.messages import OptionQuoteSnapshot


class OptionChainPoller:
    """`FixedTickScheduler.run_forever(poller.poll_once)`로 구동 — 콜백은 무인자 코루틴이어야
    하는 스케줄러 계약(scheduler.py) 그대로 `poll_once()`가 그 시그니처."""

    def __init__(
        self,
        rest_client: KISRestClient,
        master: IndexDerivativesMaster,
        bus: BusLike,
        *,
        underlying: str = "KOSPI200",
        series: str = "regular",
    ) -> None:
        self._rest_client = rest_client
        self._master = master
        self._bus = bus
        self._underlying = underlying
        self._series = series

    async def poll_once(self) -> None:
        """현재 근월물 체인(콜+풋 전 행사가)을 조회해 다리별로 순차 발행한다. 다리 하나의
        실패가 나머지를 막지 않는다(L22 — 항목 하나의 실패가 루프 전체를 죽이면 안 됨)."""
        chain = self._master.nearest_expiry_chain(self._underlying, series=self._series)
        if not chain:
            mlog.log(
                "OptionChainPollEmpty",
                "근월물 체인이 비어 있음 — 마스터파일 갱신 필요할 수 있음",
                underlying=self._underlying,
            )
            return
        for leg in chain:
            await self._poll_one(leg)

    async def _poll_one(self, leg: OptionLeg) -> None:
        try:
            raw = await asyncio.to_thread(self._rest_client.get_asking_price, leg.symbol)
        except Exception as exc:  # noqa: BLE001 — REST 실패로 폴링 루프가 죽으면 안 됨
            mlog.log(
                "OptionChainPollError",
                f"조회 실패: {exc}",
                underlying=self._underlying,
                symbol=leg.symbol,
            )
            return

        snapshot = OptionQuoteSnapshot(
            underlying=self._underlying,
            option_type=leg.option_type,
            strike=leg.strike,
            expiry=leg.month_label,
            symbol=leg.symbol,
            raw=raw,
        )
        try:
            await self._bus.publish(f"{TOPIC_RAW}.option_chain.{self._underlying}", snapshot)
        except Exception as exc:  # noqa: BLE001
            mlog.log(
                "OptionChainPollError",
                f"발행 실패: {exc}",
                underlying=self._underlying,
                symbol=leg.symbol,
            )
