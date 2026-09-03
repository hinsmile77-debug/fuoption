"""부수 태스크 격리 (2026-09-03 신설, F-89 — `core/supervise.py`).

2026-09-03 08:25:38에 화면 표시 전용 옵션 제공자의 예외 하나가 `asyncio.gather()`를 타고
올라가 G2 세션 전체를 내렸다. 여기서 고정하는 것은 셋이다:
  ① 부수 태스크의 예외가 형제를 죽이지 않는다,
  ② 그런데 **조용하지도 않다**(ERROR 한 줄이 반드시 남는다 — 금지계명 12),
  ③ 취소(`CancelledError`)는 삼키지 않는다(종료·kill이 먹어야 한다).
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from messiah.core.logging import TAG_LEVELS
from messiah.core.supervise import run_isolated


async def _boom() -> None:
    raise TypeError("MessageBus.subscribe() missing 1 required positional argument: 'handler'")


async def _live(marker: list[str]) -> None:
    for _ in range(3):
        await asyncio.sleep(0)
    marker.append("살아남았다")


async def test_a_crashing_side_task_does_not_take_its_siblings_down():
    """2026-09-03 사고의 재현 — 이번엔 형제가 살아야 한다."""
    marker: list[str] = []

    await asyncio.gather(run_isolated("options.smile_provider", _boom()), _live(marker))

    assert marker == ["살아남았다"]


async def test_the_crash_is_recorded_as_an_error_with_the_traceback(caplog):
    """삼키는 것은 전파뿐이다 — 사실은 남는다(R6·R10)."""
    with caplog.at_level(logging.ERROR):
        await run_isolated("options.smile_provider", _boom())

    records = [r for r in caplog.records if getattr(r, "tag", None) == "IsolatedTaskCrashed"]
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.ERROR
    fields = record.fields
    assert fields["task"] == "options.smile_provider"
    assert fields["error_type"] == "TypeError"
    assert "MessageBus.subscribe()" in fields["traceback"]


def test_the_tag_is_registered():
    """미등록 태그면 `mlog.log()`가 ValueError를 던져, 격리하려던 자리가 되레 터진다(R6)."""
    assert TAG_LEVELS.get("IsolatedTaskCrashed") == logging.ERROR


async def test_cancellation_is_not_swallowed():
    """바깥이 내린 종료 지시를 삼키면 세션 종료·kill이 안 먹는다."""

    async def _forever() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(run_isolated("forever", _forever()))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
