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

## 재시도 (2026-08-10 A-4)

2026-08-05부터 `OptionChainPoller`에는 재시도 계층이 있었는데 **이쪽엔 없었다.** 두 폴러가
같은 KIS REST의 같은 `500 Internal Server Error`를 받는데 한쪽만 처방을 받고 있었던 셈이다.

대가는 실측됐다 — 2026-08-10에 옵션체인은 52건을 재시도로 살리고 1건만 잃었고, 이 폴러는
3건을 실패해 **3행을 그대로 잃었다**(그날 아카이브 1,185행 = 396분 × 3업종 − 3). 2026-08-06엔
같은 이유로 4행이 사라졌다. 수급은 과거 조회 경로가 없어 **지금 없으면 영원히 없다.**

정본은 `data/poll_retry.py`다. 옮기면서 복사하지 않았다 — 같은 코드가 두 곳에 있으면
한쪽만 고쳐지고, 그게 이 저장소가 반복한 실패 형태다.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Sequence

from messiah.broker.kis.rest_client import KISRestClient
from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_RAW, BusLike
from messiah.core.messages import InvestorFlowSnapshot
from messiah.data import poll_retry


class InvestorFlowPoller:
    """`FixedTickScheduler.run_forever(poller.poll_once)`로 구동된다 — 콜백은 무인자
    코루틴이어야 하는 스케줄러 계약(scheduler.py) 그대로 `poll_once()`가 그 시그니처."""

    def __init__(
        self,
        rest_client: KISRestClient,
        market_code: str,
        sector_codes: Sequence[str],
        bus: BusLike,
        *,
        retry_attempts: int = poll_retry.RETRY_ATTEMPTS,
        retry_delay_seconds: float = poll_retry.RETRY_DELAY_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """`sleep`을 주입받는 이유는 `OptionChainPoller`와 같다 — 테스트가 재시도 지연을
        실제로 기다리면 스위트가 초 단위로 느려진다."""
        if not sector_codes:
            raise ValueError("sector_codes가 비어 있음 — 폴링할 업종 최소 1개 필요")
        if retry_attempts < 0:
            raise ValueError("retry_attempts는 0 이상이어야 한다")
        self._rest_client = rest_client
        self._market_code = market_code
        self._sector_codes = list(sector_codes)
        self._bus = bus
        self._retry_attempts = retry_attempts
        self._retry_delay_seconds = retry_delay_seconds
        self._sleep = sleep

    async def poll_once(self) -> None:
        """등록된 sector_code 전부를 순차 조회·발행한다. 하나가 실패해도 나머지는
        계속 시도한다(L22 — 항목 하나의 실패가 루프 전체를 죽이면 안 됨)."""
        for sector_code in self._sector_codes:
            await self._poll_one(sector_code)

    async def _poll_one(self, sector_code: str) -> None:
        raw = await poll_retry.fetch_with_retry(
            lambda: asyncio.to_thread(
                self._rest_client.get_investor_flow, self._market_code, sector_code
            ),
            retried_tag="InvestorFlowPollRetried",
            error_tag="InvestorFlowPollError",
            loss_series=f"flow_intraday/{self._market_code}",
            retry_attempts=self._retry_attempts,
            retry_delay_seconds=self._retry_delay_seconds,
            sleep=self._sleep,
            market_code=self._market_code,
            sector_code=sector_code,
        )
        if raw is None:
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
