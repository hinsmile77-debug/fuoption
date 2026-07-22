"""L1 Collector — KIS WS 실시간체결가 구독 → 정규화 → 완성봉 적재/발행 오케스트레이션
(Master Plan Ver 2.0 §9 "L1 DATA: Collector").

단일 연결·단일 심볼용 골격이다. 마흐디 mahdi/main.py가 run_observation_loop(단일 연결, 끊기면
예외 전파)과 run_observation_loop_forever(재연결 래퍼)를 분리한 것과 같은 설계로, 이 클래스는
전자에 해당한다 — WS 재연결·지수 백오프는 이 클래스의 책임이 아니고 별도 후속 작업이다
(NEXT_TODO 참고). ATM±N 옵션 체인 구독 롤링(RollingSubscriptionManager 이식)도 마찬가지로
범위 밖 — 이 클래스는 생성 시 주어진 심볼 1개만 구독한다.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any, Callable

import websockets

from messiah.broker.kis import tr_codes
from messiah.broker.kis.credentials import KISCredentials
from messiah.broker.kis.ws_client import ApprovalKeyIssuer, KISWebSocketClient, Subscription
from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_BAR, TOPIC_TICK, MessageBus
from messiah.core.messages import BarClosed, Horizon, Tick
from messiah.data.archiver import ParquetArchiver
from messiah.data.normalizer import MinuteBarAggregator


class TickCollector:
    def __init__(
        self,
        creds: KISCredentials,
        symbol: str,
        tr_id: str,
        parse_tick: Callable[..., Tick | None],
        tick_size: Decimal,
        archiver: ParquetArchiver,
        bus: MessageBus | None = None,
        horizon: Horizon = Horizon.M1,
        approval_issuer: ApprovalKeyIssuer | None = None,
        ws_connect: Callable[[str], Any] = websockets.connect,
    ) -> None:
        """
        입력: tr_id/parse_tick은 짝을 맞춰 넘긴다 — 선물은
             (tr_codes.WS_TR_FUTURES_CONTRACT, normalizer.parse_futures_tick), 옵션은
             (tr_codes.WS_TR_OPTION_CONTRACT, normalizer.parse_option_tick). bus를 생략하면
             Redis 발행 없이 Parquet 적재만 한다(테스트·오프라인 실행에 유용). ws_connect는
             테스트에서 실제 네트워크 없이 가짜 연결을 주입하기 위한 것(기본값
             websockets.connect).
        """
        self._creds = creds
        self._symbol = symbol
        self._tr_id = tr_id
        self._parse_tick = parse_tick
        self._tick_size = tick_size
        self._archiver = archiver
        self._bus = bus
        self._approval_issuer = approval_issuer or ApprovalKeyIssuer(creds)
        self._ws_connect = ws_connect
        self._aggregator = MinuteBarAggregator(symbol, horizon)

    async def run_once(self) -> None:
        """
        계산: approval_key 발급(동기 httpx 호출이라 asyncio.to_thread로 감쌈) → WS 연결 →
             구독 → listen()의 무한 수신 루프.
        실패 조건: 연결이 끊기면(또는 다른 예외) 그대로 전파된다 — 재연결은 이 클래스의
                  책임이 아니다(모듈 docstring 참고).
        """
        approval_key = await asyncio.to_thread(self._approval_issuer.issue)
        async with self._ws_connect(tr_codes.MARKET_DATA_WS_DOMAIN) as ws:
            client = KISWebSocketClient(approval_key, ws)
            await client.subscribe(Subscription(self._tr_id, self._symbol))
            await client.listen(self._handle_message)

    async def flush_final_bar(self) -> None:
        """graceful shutdown 시 마지막 미완성 분봉을 강제 flush — 호출측(재연결 래퍼 등)이
        종료 시퀀스에서 부른다."""
        bar = self._aggregator.flush_final()
        if bar is not None:
            await self._archive_and_publish_bar(bar)

    async def _handle_message(self, message: dict) -> None:
        raw = message.get("raw")
        if raw is None:
            return  # JSON 제어 메시지(구독응답/PINGPONG) — 정규화 대상 아님

        tick = self._parse_tick(raw, self._tick_size)
        if tick is None:
            return  # 정규화 실패 — normalizer 계약과 동일하게 조용히 무시

        if self._bus is not None:
            try:
                await self._bus.publish(f"{TOPIC_TICK}.{tick.symbol}", tick)
            except Exception as exc:  # noqa: BLE001 — 발행 실패로 수신 루프가 죽으면 안 됨
                mlog.log("CollectorProcessingError", f"틱 발행 실패: {exc}", symbol=tick.symbol)

        bar = self._aggregator.add_tick(tick)
        if bar is not None:
            await self._archive_and_publish_bar(bar)

    async def _archive_and_publish_bar(self, bar: BarClosed) -> None:
        """완성봉 적재/발행은 파싱과 달리 인프라 실패라 침묵하면 안 됨(L22) — 잡아서 로깅하고
        계속한다. 적재 실패와 발행 실패는 서로 독립(하나가 실패해도 다른 하나는 시도)."""
        try:
            self._archiver.append_bar(bar)
        except Exception as exc:  # noqa: BLE001
            mlog.log("CollectorProcessingError", f"완성봉 적재 실패: {exc}", symbol=bar.symbol)

        if self._bus is not None:
            try:
                await self._bus.publish(f"{TOPIC_BAR}.{bar.horizon.value}.{bar.symbol}", bar)
            except Exception as exc:  # noqa: BLE001
                mlog.log("CollectorProcessingError", f"완성봉 발행 실패: {exc}", symbol=bar.symbol)
