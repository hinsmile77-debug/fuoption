"""투자자매매동향(FL) REST 폴링 — `FixedTickScheduler` 첫 실사용처 (Ver 2.0 §9 L1 DATA,
2026-07-27 신설).

`core/scheduler.py`의 `FixedTickScheduler`는 W3~5부터 존재했지만 "아직 실제 L1 수집
루프(옵션체인/선물/투자자매매동향 폴링)에 물려본 적은 없다"는 갭이 W6~8부터 계속
남아있었다(모듈 docstring 자체 기록). 이 폴러가 그 첫 실사용처다 — 이미 실측된
`get_investor_flow()`(2026-07-21, KISBrokerAdapter 실계좌 확인)를 감싸 주기적으로
호출·발행한다.

## 스코프 경계 — 필드 파싱은 별도 과제

`InvestorFlowSnapshot`(core/messages.py) 모듈 docstring 참고 — 이 폴러는 KIS 응답의
구체 필드(외국인/기관/개인 순매수 수량 등)를 해석하지 않고 `raw` 그대로 발행한다. 필드
매핑을 확정할 근거(docs/efriend 엑셀·실계좌 실측 캡처)가 이 세션에 없어서다. Ver 1.5 §3.5
FL Feature(`fl_frgn_cum` 등)로 이어지는 정규화는 그 근거가 생긴 뒤 별도 세션의 몫.

## 유량제한

`KISRestClient`가 이미 내부적으로 `_RateLimiter`(또는 주입된 `RedisRateLimiter`)로
페이싱하므로 이 폴러는 별도 유량 제어를 하지 않는다 — `sector_codes`를 순차 조회할
때마다 그 페이싱이 자연히 적용된다(공유 자원 재사용, 중복 구현 없음).
"""

from __future__ import annotations

import asyncio
from typing import Sequence

from messiah.broker.kis.rest_client import KISRestClient
from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_RAW, BusLike
from messiah.core.messages import InvestorFlowSnapshot


class InvestorFlowPoller:
    """`FixedTickScheduler.run_forever(poller.poll_once)`로 구동된다 — 콜백은 무인자
    코루틴이어야 하는 스케줄러 계약(scheduler.py) 그대로 `poll_once()`가 그 시그니처."""

    def __init__(
        self,
        rest_client: KISRestClient,
        market_code: str,
        sector_codes: Sequence[str],
        bus: BusLike,
    ) -> None:
        if not sector_codes:
            raise ValueError("sector_codes가 비어 있음 — 폴링할 업종 최소 1개 필요")
        self._rest_client = rest_client
        self._market_code = market_code
        self._sector_codes = list(sector_codes)
        self._bus = bus

    async def poll_once(self) -> None:
        """등록된 sector_code 전부를 순차 조회·발행한다. 하나가 실패해도 나머지는
        계속 시도한다(L22 — 항목 하나의 실패가 루프 전체를 죽이면 안 됨)."""
        for sector_code in self._sector_codes:
            await self._poll_one(sector_code)

    async def _poll_one(self, sector_code: str) -> None:
        try:
            raw = await asyncio.to_thread(
                self._rest_client.get_investor_flow, self._market_code, sector_code
            )
        except Exception as exc:  # noqa: BLE001 — REST 실패로 폴링 루프가 죽으면 안 됨
            mlog.log(
                "InvestorFlowPollError",
                f"조회 실패: {exc}",
                market_code=self._market_code,
                sector_code=sector_code,
            )
            return

        snapshot = InvestorFlowSnapshot(
            market_code=self._market_code, sector_code=sector_code, raw=raw
        )
        try:
            await self._bus.publish(f"{TOPIC_RAW}.investor_flow.{self._market_code}", snapshot)
        except Exception as exc:  # noqa: BLE001
            mlog.log(
                "InvestorFlowPollError",
                f"발행 실패: {exc}",
                market_code=self._market_code,
                sector_code=sector_code,
            )
