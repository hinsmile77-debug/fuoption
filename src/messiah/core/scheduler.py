"""절대시각 고정 틱 스케줄러 — SYSTEM.md R9, 마흐디 레슨런 L20.

"작업 후 sleep" 방식(현재 시각 기준 상대 간격 — 콜백 실행 후 time.sleep(interval))은 처리
시간이 매 회 조금씩 누적돼 실제 폴링 시각이 서서히 밀린다. 마흐디가 5분 간격 폴링에서 이
드리프트 누적으로 실제로는 4분치 데이터를 유실한 사고(L20)의 근본 원인이 이것이었다 —
"5분 뒤"가 처리 지연 때문에 실제로는 그보다 훨씬 늦게 도착하는 일이 반복되면서 격차가 벌어짐.

해법: 매 실행 시점을 "마지막 실행 후 N초"가 아니라 UNIX epoch 기준 절대 시각의 배수로 고정한다
(완성봉 규율 — Horizon 확정이 봉 시작 시각의 절대 배수인 것과 같은 원리, SYSTEM.md §2 참고).
한 틱의 콜백이 오래 걸려 다음 틱을 이미 지나쳤으면, 그 틱들을 몰아서 실행하지 않고 그다음 유효한
미래 틱으로 바로 건너뛴다 — 몰아서 실행하면 그 순간 REST 호출이 몰려 공유 RateLimiter가 급격히
감속되고, 결국 L20이 다른 형태로 재발한다.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from messiah.core import logging as mlog
from messiah.core.timeutil import UTC, ensure_aware, now_utc


class FixedTickScheduler:
    def __init__(self, tick_seconds: float, phase_offset_seconds: float = 0.0) -> None:
        """
        입력: tick_seconds(틱 주기, 초). phase_offset_seconds(틱 격자를 이 초만큼 밀어 정렬 —
             여러 폴링 루프를 서로 다른 위상으로 나눠 배치하고 싶을 때만 지정, 기본 0이면 UNIX
             epoch 정각의 배수, 예: tick_seconds=60이면 매 분 00초).
        실패 조건: tick_seconds <= 0이면 ValueError — 고정 틱 자체가 성립하지 않는다.
        """
        if tick_seconds <= 0:
            raise ValueError("tick_seconds는 0보다 커야 함")
        self._tick_seconds = tick_seconds
        self._phase_offset = phase_offset_seconds

    def next_tick_at(self, after: datetime) -> datetime:
        """
        계산: after 시각보다 반드시 이후(같은 시각 제외)인 가장 가까운 절대시각 틱을 반환한다.
             원점은 UNIX epoch(1970-01-01 UTC) — 프로세스 시작 시각이 아니라 고정된 절대
             좌표라서 여러 프로세스·재시작 사이에도 같은 틱 격자를 공유한다.
        실패 조건: after가 naive datetime이면 ValueError(ensure_aware, SYSTEM.md R3).
        """
        ensure_aware(after)
        epoch = after.timestamp()
        n = math.floor((epoch - self._phase_offset) / self._tick_seconds) + 1
        next_epoch = n * self._tick_seconds + self._phase_offset
        return datetime.fromtimestamp(next_epoch, tz=UTC)

    async def run_forever(
        self,
        callback: Callable[[], Awaitable[None]],
        *,
        max_iterations: int | None = None,
    ) -> None:
        """
        계산: next_tick_at()으로 다음 틱까지 대기 후 callback()을 실행 — 무한 반복(max_iterations는
             테스트 전용, 프로덕션 호출은 생략).
        해석: 직전 틱 이후 콜백 처리가 tick_seconds를 넘겨 하나 이상의 틱을 건너뛰게 되면
             SchedulerTickMissed로 로깅한다(침묵 금지 — DecisionIntent가 NO_TRADE에도 rationale을
             남기는 것과 같은 원칙). callback이 예외를 던지면 SchedulerCallbackError로 로깅하고
             루프는 계속한다(L22: 항목 하나의 실패가 루프 전체를 죽이면 안 됨).
        """
        iterations = 0
        previous_target: datetime | None = None
        while max_iterations is None or iterations < max_iterations:
            target = self.next_tick_at(now_utc())
            if previous_target is not None:
                expected = previous_target + timedelta(seconds=self._tick_seconds)
                if target > expected:
                    missed = round((target - expected).total_seconds() / self._tick_seconds)
                    mlog.log(
                        "SchedulerTickMissed",
                        f"틱 처리 지연으로 {missed}개 틱을 건너뜀",
                        expected=expected.isoformat(),
                        actual_next=target.isoformat(),
                        missed_ticks=missed,
                    )

            delay = (target - now_utc()).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)

            try:
                await callback()
            except Exception as exc:  # noqa: BLE001 — 콜백 실패로 스케줄러 전체가 죽으면 안 됨
                mlog.log("SchedulerCallbackError", str(exc), target=target.isoformat())

            previous_target = target
            iterations += 1
