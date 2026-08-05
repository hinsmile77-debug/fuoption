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


async def test_run_forever_never_fires_the_same_tick_twice(monkeypatch) -> None:
    """`asyncio.sleep`이 목표보다 몇 밀리초 일찍 깨도 같은 틱을 두 번 쏘면 안 된다.

    2026-08-05 실측(`logs/l1_daily_20260805.log`):

        08:43:19.998  OptionChainSkipped  series=weekly_thu
        08:43:20.013  OptionChainSkipped  series=weekly_thu   ← 15ms 뒤 같은 틱

    sleep은 이벤트 루프의 단조 시계로 자는데 목표는 벽시계로 계산하므로 실제로 일어난다.
    옵션체인에서는 42다리 REST 사이클이 통째로 두 번 도는 것이라 유량 예산이 2배가 된다.

    **관측 방법**: 콜백이 예외를 던지면 `SchedulerCallbackError`가 그 회차의 `target`을
    싣는다(L22 경로) — 그 값들이 서로 달라야 한다. 중복 발화 자체는 원래 조용하기 때문에
    이 경로 말고는 밖에서 볼 수단이 없다는 사실도 이 테스트가 기록한다.
    """
    base = datetime(2026, 8, 5, 12, 0, 30, tzinfo=UTC)

    def _at(minute: int, second: int, micro: int = 0) -> datetime:
        return datetime(2026, 8, 5, 12, minute, second, micro, tzinfo=UTC)

    # 회차마다 now_utc()가 정확히 두 번 불린다(target 계산 → delay 계산).
    now_values = iter(
        [
            base,  # 1회차 target → 12:01:00
            _at(0, 59, 999_000),  # 1회차 delay (1ms 남음)
            _at(0, 59, 999_000),  # 2회차 target ← **목표보다 1ms 일찍 깼다**
            _at(1, 59, 999_000),  # 2회차 delay
            _at(2, 0),  # 3회차 target
            _at(2, 59, 999_000),  # 3회차 delay
        ]
    )
    logged: list[tuple[str, dict]] = []
    monkeypatch.setattr("messiah.core.scheduler.now_utc", lambda: next(now_values))
    monkeypatch.setattr(
        "messiah.core.scheduler.mlog.log", lambda tag, msg, **f: logged.append((tag, f))
    )

    async def callback() -> None:
        raise RuntimeError("target을 로그로 끌어내기 위한 의도적 실패")

    scheduler = FixedTickScheduler(tick_seconds=60)
    await scheduler.run_forever(callback, max_iterations=3)

    targets = [f["target"] for tag, f in logged if tag == "SchedulerCallbackError"]
    assert len(targets) == 3
    assert len(set(targets)) == 3, f"같은 틱이 두 번 발화했다: {targets}"
    assert targets == sorted(targets)


async def test_run_forever_zero_iterations_never_calls_callback() -> None:
    scheduler = FixedTickScheduler(tick_seconds=0.02)
    calls = []

    async def callback() -> None:
        calls.append(1)

    await scheduler.run_forever(callback, max_iterations=0)

    assert calls == []
