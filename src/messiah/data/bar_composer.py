"""다중 Horizon 완성봉 합성 — Master Plan Ver 2.0 §9 W6~8 "시간 바 생성".

`TickCollector`(+`MinuteBarAggregator`)가 만드는 1분봉(`bar.1m.{symbol}`)만이 원시 틱을
직접 본다. 3/5/10/15/30분봉은 그 1분봉들을 합성해서 만든다 — OHLCV는 구성 1분봉들만으로
정확히 재구성 가능하다(open=첫 봉의 open, high/low=구성봉들의 max/min, close=마지막 봉의
close, volume=합계). 원시 틱을 다시 구독할 필요가 없다.

**봉 확정은 "다음 1분봉 도착"이 아니라 시각 기반**(Ver 1.2 §2.2 완성봉 규율: 거래소 시각
경계 + 지연 틱 유예 500ms 후 확정). `MinuteBarAggregator`는 틱이 없는 분은 봉 자체를 발행하지
않으므로, 상위 Horizon 경계 판정을 "구성봉 개수 세기"로만 하면 조용한 구간에서 경계가 밀린다
— 이미 테스트를 거친 `core/scheduler.py`의 `FixedTickScheduler`를 Horizon마다 하나씩 붙여
절대시각 기준으로 "지금 이 버킷을 닫아라"라는 트리거만 받고, 실제 봉 경계(`bar_open_kst`)는
누적된 1분봉들의 시각으로부터 계산한다 — 스케줄러 트리거 시점의 미세한 지연에 값이
좌우되지 않는다.

KST(UTC+9:00)는 5/10/15/30분 격자와 정수 배로 맞아떨어져(540분 = 9시간이 5·10·15·30 전부로
나누어떨어짐) UTC epoch 기준 정렬이 곧 KST 벽시계 경계와 일치한다 — 별도의 장 시작 시각(09:00)
앵커링이 필요 없다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_BAR, MessageBus
from messiah.core.messages import HORIZON_SECONDS, BarClosed, Horizon
from messiah.core.scheduler import FixedTickScheduler
from messiah.core.timeutil import KST, UTC, ensure_aware
from messiah.data.archiver import ParquetArchiver

# Horizon.M1로부터 합성하는 상위 Horizon과 그 길이(초, core/messages.py HORIZON_SECONDS 재사용).
# M1 자신은 합성 대상이 아니다(원본).
_TARGET_HORIZON_SECONDS: dict[Horizon, int] = {
    h: s for h, s in HORIZON_SECONDS.items() if h != Horizon.M1
}

_BOUNDARY_GRACE_SECONDS = 0.5  # Ver 1.2 §2.2 "지연 틱 유예 500ms"


def floor_to_horizon(dt: datetime, horizon_seconds: int) -> datetime:
    """
    계산: dt를 UTC epoch 기준 horizon_seconds의 배수로 내림(floor)한다 — 해당 Horizon
         버킷의 시작 시각.
    실패 조건: dt가 naive면 ValueError(ensure_aware).
    """
    ensure_aware(dt)
    epoch = dt.timestamp()
    floored_epoch = (epoch // horizon_seconds) * horizon_seconds
    return datetime.fromtimestamp(floored_epoch, tz=UTC).astimezone(KST)


class MultiHorizonBarComposer:
    """단일 심볼용 — 1분봉을 구독해 3/5/10/15/30분봉을 합성·적재·발행한다."""

    def __init__(
        self,
        symbol: str,
        archiver: ParquetArchiver,
        bus: MessageBus,
        target_horizons: dict[Horizon, int] | None = None,
    ) -> None:
        """
        입력: target_horizons를 생략하면 3/5/10/15/30분 전부(_TARGET_HORIZON_SECONDS) 합성.
             테스트에서 일부만 골라 검증하고 싶을 때만 명시적으로 축소.
        """
        self._symbol = symbol
        self._archiver = archiver
        self._bus = bus
        self._targets = target_horizons or dict(_TARGET_HORIZON_SECONDS)
        self._bucket_start: dict[Horizon, datetime | None] = dict.fromkeys(self._targets)
        self._constituents: dict[Horizon, list[BarClosed]] = {h: [] for h in self._targets}

    async def handle_one_minute_bar(self, bar: BarClosed) -> None:
        """
        입력: 완성된 1분봉(`bar.1m.{symbol}`에서 수신) — 다른 심볼의 봉이 섞여 들어오면
             무시한다(단일 심볼 구독을 전제하지만 방어적으로 한 번 더 확인).
        계산: Horizon별 누적 버킷에 추가한다. 버킷이 비어 있으면(새 버킷 시작) 이 봉의
             시각으로 버킷 시작 시각을 고정한다. 발행은 여기서 하지 않는다 —
             `flush_due_horizon()`이 스케줄러 트리거로 담당.
        """
        if bar.symbol != self._symbol or bar.horizon != Horizon.M1:
            return
        for horizon, seconds in self._targets.items():
            if self._bucket_start[horizon] is None:
                self._bucket_start[horizon] = floor_to_horizon(bar.bar_open_kst, seconds)
            self._constituents[horizon].append(bar)

    async def flush_due_horizon(self, horizon: Horizon) -> None:
        """
        계산: 해당 Horizon의 누적 버킷을 합성봉으로 확정해 적재·발행하고 버킷을 비운다.
             누적된 1분봉이 하나도 없으면(조용한 구간) 아무 것도 발행하지 않는다 — 가짜
             OHLC를 만들지 않는다.
        해석: quality_ok는 구성봉이 전부 quality_ok=True이고, 개수가 이 Horizon의 분(分) 수와
             정확히 일치할 때만(=누락된 분이 없을 때만) True.
        """
        bars = self._constituents[horizon]
        bucket_start = self._bucket_start[horizon]
        self._constituents[horizon] = []
        self._bucket_start[horizon] = None
        if not bars or bucket_start is None:
            return

        expected_minutes = self._targets[horizon] // 60
        composite = BarClosed(
            symbol=self._symbol,
            horizon=horizon,
            bar_open_kst=bucket_start,
            o_ticks=bars[0].o_ticks,
            h_ticks=max(b.h_ticks for b in bars),
            l_ticks=min(b.l_ticks for b in bars),
            c_ticks=bars[-1].c_ticks,
            volume=sum(b.volume for b in bars),
            quality_ok=len(bars) == expected_minutes and all(b.quality_ok for b in bars),
        )
        await self._archive_and_publish(composite)

    async def _archive_and_publish(self, bar: BarClosed) -> None:
        """TickCollector._archive_and_publish_bar()와 동일 원칙 — 적재/발행 실패는 독립
        try/except로 로깅만 하고 계속(L22)."""
        try:
            self._archiver.append_bar(bar)
        except Exception as exc:  # noqa: BLE001
            mlog.log("CollectorProcessingError", f"합성봉 적재 실패: {exc}", symbol=bar.symbol)

        try:
            await self._bus.publish(f"{TOPIC_BAR}.{bar.horizon.value}.{bar.symbol}", bar)
        except Exception as exc:  # noqa: BLE001
            mlog.log("CollectorProcessingError", f"합성봉 발행 실패: {exc}", symbol=bar.symbol)

    async def flush_all_final(self) -> None:
        """graceful shutdown 시 남은 모든 버킷을 강제 flush — 종료 시퀀스에서 호출."""
        for horizon in self._targets:
            await self.flush_due_horizon(horizon)

    async def run_forever(self) -> None:
        """
        계산: `bar.1m.{symbol}` 구독 태스크 1개 + Horizon마다 `FixedTickScheduler`
             (phase_offset=500ms) 태스크 1개, 총 1+len(targets)개를 asyncio.gather로 동시
             구동한다. 어느 하나가 예외로 죽으면(재연결은 이 클래스의 책임이 아님) 전부
             함께 종료된다.
        """
        subscribe_task = self._bus.subscribe(
            [f"{TOPIC_BAR}.{Horizon.M1.value}.{self._symbol}"], self.handle_one_minute_bar
        )
        scheduler_tasks = [
            FixedTickScheduler(
                tick_seconds=seconds, phase_offset_seconds=_BOUNDARY_GRACE_SECONDS
            ).run_forever(lambda h=horizon: self.flush_due_horizon(h))
            for horizon, seconds in self._targets.items()
        ]
        await asyncio.gather(subscribe_task, *scheduler_tasks)
