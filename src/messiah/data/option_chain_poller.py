"""옵션체인 시세호가(OP) REST 폴링 — `InvestorFlowPoller`와 동일 패턴의 두 번째 실사용처
(Ver 2.0 §9 W27~29, Options AI 선행 인프라 갭 해소, `Docs/capability_matrix.md`
"OP(옵션체인 그릭스) REST 폴링 수집기 미착수" 항목).

`FixedTickScheduler.run_forever(poller.poll_once)`로 구동된다. 매 호출마다 **확정 유니버스의
옵션 시리즈 3종**(2026-08-04: 먼쓰리·월위클리·목위클리, `core/universe.py`)을 돌며
`IndexDerivativesMaster.nearest_expiry_chain()`(이미 실측된 종목코드 마스터 조회,
2026-07-22)으로 콜/풋 근월물 체인을 얻고, 각 다리(leg)를 `KISRestClient.
get_asking_price()`(이미 TR_ID·헤더 실측된 REST, `tests/broker/test_kis_rest_client.py`)로
순차 조회해 `raw.option_chain.{underlying}`에 `OptionQuoteSnapshot`을 발행한다.

**여전히 어떤 스크립트에도 결선돼 있지 않다**(2026-08-04 확인 — 테스트에서만 인스턴스화).
유니버스 확정으로 "무엇을 수집해야 하는가"는 정해졌지만 "언제 켜는가"는 별개이고,
`run_l1_daily.py` 결선은 같은 계좌 WS 다중연결 문제(capability_matrix.md)와 함께 풀어야
한다. `InvestorFlowPoller`가 폴러만 만들고 7개월을 날린 전례가 있으므로 이건 결선 과제로
`NEXT_TODO.md`에 남긴다.

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
from typing import Sequence

from messiah.broker.kis.rest_client import KISRestClient
from messiah.broker.kis.symbol_master import IndexDerivativesMaster, OptionLeg
from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_RAW, BusLike
from messiah.core.messages import OptionQuoteSnapshot
from messiah.core.universe import DEFAULT_UNIVERSE, option_series

# 확정 유니버스(2026-08-04)의 옵션 시리즈 — 먼쓰리·월위클리·목위클리.
DEFAULT_SERIES: tuple[str, ...] = tuple(option_series(list(DEFAULT_UNIVERSE)))


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
        series: Sequence[str] = DEFAULT_SERIES,
    ) -> None:
        """`series`는 확정 유니버스의 옵션 시리즈 목록이다(2026-08-04: 먼쓰리·월위클리·
        목위클리). `core/universe.option_series(cfg.universe)`의 반환값을 그대로 넘기면
        된다 — 기본값도 그 확정 유니버스와 같다.

        예전엔 `series: str = "regular"` 하나였는데, 그러면 폴러 하나가 먼쓰리만 보고
        위클리 둘은 **설정에 적혀 있어도 조회 자체가 안 됐다.** 시리즈마다 폴러를 따로
        띄우는 방법도 있지만, 그러면 유량(초당 건수)을 셋이 각자 모른 채 나눠 쓰게 된다 —
        한 폴러가 순차로 도는 편이 REST 페이싱과 맞다.
        """
        if not series:
            raise ValueError("series가 비어 있음 — 조회할 옵션 시리즈가 없다")
        self._rest_client = rest_client
        self._master = master
        self._bus = bus
        self._underlying = underlying
        self._series = tuple(series)

    async def poll_once(self) -> None:
        """시리즈마다 근월물 체인(콜+풋 전 행사가)을 조회해 다리별로 순차 발행한다.
        다리 하나의 실패가 나머지를 막지 않고(L22), **시리즈 하나가 비어도 나머지는
        계속 돈다** — 위클리는 만기 주간에 따라 체인이 실제로 빌 수 있어서, 그걸 전체
        폴링 중단으로 번지게 하면 먼쓰리까지 같이 멈춘다."""
        for series in self._series:
            chain = self._master.nearest_expiry_chain(self._underlying, series=series)
            if not chain:
                mlog.log(
                    "OptionChainPollEmpty",
                    "근월물 체인이 비어 있음 — 마스터파일 갱신 필요할 수 있음",
                    underlying=self._underlying,
                    series=series,
                )
                continue
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
