"""FixedTickScheduler 검증 — SYSTEM.md R9, 레슨런 L20 (절대시각 고정 틱 폴링)."""

from __future__ import annotations

import time
from datetime import datetime

import pytest

from messiah.core.scheduler import FixedTickScheduler
from messiah.core.timeutil import UTC

# ---------------------------------------------------------------- next_tick_at (순수 계산)


def test_tick_seconds_must_be_positive() -> None:
    with pytest.raises(ValueError, match="tick_seconds"):
        FixedTickScheduler(tick_seconds=0)
    with pytest.raises(ValueError, match="tick_seconds"):
        FixedTickScheduler(tick_seconds=-1)


def test_next_tick_at_aligns_to_absolute_epoch_boundary() -> None:
    scheduler = FixedTickScheduler(tick_seconds=60)
    after = datetime(2026, 7, 22, 12, 0, 37, tzinfo=UTC)  # 분 경계에서 37초 지난 시각

    tick = scheduler.next_tick_at(after)

    assert tick == datetime(2026, 7, 22, 12, 1, 0, tzinfo=UTC)  # "37초 뒤"가 아니라 다음 분 정각


def test_next_tick_at_is_strictly_after_input_even_on_boundary() -> None:
    scheduler = FixedTickScheduler(tick_seconds=60)
    on_boundary = datetime(2026, 7, 22, 12, 1, 0, tzinfo=UTC)

    tick = scheduler.next_tick_at(on_boundary)

    assert tick == datetime(2026, 7, 22, 12, 2, 0, tzinfo=UTC)  # 같은 시각 반환 금지(busy-loop)


def test_next_tick_at_supports_phase_offset() -> None:
    scheduler = FixedTickScheduler(tick_seconds=60, phase_offset_seconds=15)
    after = datetime(2026, 7, 22, 12, 0, 20, tzinfo=UTC)

    tick = scheduler.next_tick_at(after)

    assert tick == datetime(2026, 7, 22, 12, 1, 15, tzinfo=UTC)


def test_next_tick_at_rejects_naive_datetime() -> None:
    scheduler = FixedTickScheduler(tick_seconds=60)
    with pytest.raises(ValueError, match="naive"):
        scheduler.next_tick_at(datetime(2026, 7, 22, 12, 0, 0))  # noqa: DTZ001 — 의도적 naive


def test_next_tick_at_is_independent_of_process_start_time() -> None:
    # 절대 좌표라는 핵심 성질: 같은 tick_seconds면 임의의 시각에서 계산해도 같은 격자 위에 있다.
    scheduler = FixedTickScheduler(tick_seconds=60)
    t1 = scheduler.next_tick_at(datetime(2026, 7, 22, 12, 0, 1, tzinfo=UTC))
    t2 = scheduler.next_tick_at(datetime(2099, 1, 1, 9, 0, 1, tzinfo=UTC))
    assert t1.second == 0
    assert t2.second == 0


# ---------------------------------------------------------------- run_forever


async def test_run_forever_invokes_callback_at_each_tick() -> None:
    scheduler = FixedTickScheduler(tick_seconds=0.05)
    call_times: list[float] = []

    async def callback() -> None:
        call_times.append(time.monotonic())

    await scheduler.run_forever(callback, max_iterations=3)

    assert len(call_times) == 3
    gaps = [b - a for a, b in zip(call_times, call_times[1:])]
    for gap in gaps:
        assert gap > 0.03  # 지터 감안 — tick_seconds(0.05)보다 크게 작으면 안 됨


async def test_run_forever_continues_after_callback_exception(monkeypatch) -> None:
    scheduler = FixedTickScheduler(tick_seconds=0.02)
    call_count = {"n": 0}
    logged: list[tuple[str, str]] = []

    async def callback() -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ValueError("첫 콜백 고의 실패")

    def fake_log(tag: str, msg: str, **fields) -> None:
        logged.append((tag, msg))

    monkeypatch.setattr("messiah.core.scheduler.mlog.log", fake_log)

    await scheduler.run_forever(callback, max_iterations=2)

    assert call_count["n"] == 2  # 첫 콜백이 죽었어도 두 번째가 실행됨
    assert any(tag == "SchedulerCallbackError" for tag, _ in logged)


async def test_run_forever_logs_missed_ticks_after_slow_callback(monkeypatch) -> None:
    scheduler = FixedTickScheduler(tick_seconds=0.03)
    call_count = {"n": 0}
    logged: list[tuple[str, dict]] = []

    async def callback() -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            import asyncio

            await asyncio.sleep(0.15)  # tick_seconds의 몇 배 — 다음 틱들을 건너뛰게 만듦

    def fake_log(tag: str, msg: str, **fields) -> None:
        logged.append((tag, fields))

    monkeypatch.setattr("messiah.core.scheduler.mlog.log", fake_log)

    await scheduler.run_forever(callback, max_iterations=2)

    missed_logs = [fields for tag, fields in logged if tag == "SchedulerTickMissed"]
    assert len(missed_logs) == 1
    assert missed_logs[0]["missed_ticks"] >= 1


async def test_run_forever_zero_iterations_never_calls_callback() -> None:
    scheduler = FixedTickScheduler(tick_seconds=0.02)
    calls = []

    async def callback() -> None:
        calls.append(1)

    await scheduler.run_forever(callback, max_iterations=0)

    assert calls == []
