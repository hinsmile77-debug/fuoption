"""장중 학습·백필 거부 — SYSTEM.md R11을 코드로 강제 (2026-08-05 신설).

2026-08-04에 정규장 중 백필 1회 + 모델 스윕 4회 + 워크포워드 1회가 돌았다. 그때까지 R11은
문서에만 있었고 아무것도 그것을 막지 않았다(`ops/session_guard.py` 모듈 docstring).
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from messiah.core.event_calendar import EventCalendar
from messiah.core.timeutil import KST
from messiah.ops.session_guard import (
    REFUSED_EXIT_CODE,
    is_regular_session_now,
    refuse_if_regular_session,
)

# 2026-08-04는 화요일(거래일). 휴장일 목록에 없다는 것은 아래 픽스처가 아니라
# `configs/krx_holidays.yaml`이 정한다 — 달력을 흉내내지 않고 실물을 쓴다.
_TRADING_DAY = date(2026, 8, 4)


def _at(hour: int, minute: int) -> datetime:
    day = _TRADING_DAY
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=KST)


@pytest.fixture
def calendar() -> EventCalendar:
    return EventCalendar.from_file()


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (_at(8, 35), False),  # 장전 웜업 — 백필해도 되는 시간
        (_at(9, 0), True),  # 개장
        (_at(13, 27), True),  # 2026-08-04에 실제로 모델 스윕이 돈 시각
        (_at(15, 34), True),
        (_at(15, 36), False),  # 연속거래 종료(15:35) 직후
        (_at(19, 13), False),  # 장후 연구 시간
    ],
)
def test_regular_session_window(moment: datetime, expected: bool, calendar: EventCalendar):
    assert is_regular_session_now(now=moment, calendar=calendar) is expected


def test_refuses_during_the_session(capsys, calendar: EventCalendar):
    """2026-08-04 13:27 회귀 — 그 시각의 모델 스윕은 이제 거부된다."""
    with pytest.raises(SystemExit) as excinfo:
        refuse_if_regular_session("모델 스윕", now=_at(13, 27), calendar=calendar)

    assert excinfo.value.code == REFUSED_EXIT_CODE
    message = capsys.readouterr().err
    assert "모델 스윕" in message
    assert "R11" in message
    assert "--force-intraday" in message


def test_force_passes_but_says_so(capsys, calendar: EventCalendar):
    """예외는 남기되 **조용히** 통과하지는 않는다(L18)."""
    refuse_if_regular_session("백필", force=True, now=_at(13, 27), calendar=calendar)

    assert "--force-intraday" in capsys.readouterr().out


def test_silent_outside_the_session(capsys, calendar: EventCalendar):
    refuse_if_regular_session("피처 관문", now=_at(19, 13), calendar=calendar)

    assert capsys.readouterr().out == ""


def test_holiday_is_never_a_regular_session(calendar: EventCalendar):
    """휴장일은 시각과 무관하게 통과 — 그날은 수집 파이프라인 자체가 안 돈다."""
    holiday = next(
        d
        for d in (date(2026, 1, 1), date(2026, 5, 5), date(2026, 12, 25))
        if not calendar.is_trading_day(d)
    )
    moment = datetime(holiday.year, holiday.month, holiday.day, 13, 0, tzinfo=KST)

    assert is_regular_session_now(now=moment, calendar=calendar) is False


def test_guard_failure_does_not_block_the_task(capsys):
    """가드가 본 기능을 막는 쪽으로 실패하면 안 된다 — 판정 불가면 통과시키되 남긴다."""

    class _BrokenCalendar:
        def is_trading_day(self, _d):  # noqa: ANN001
            raise RuntimeError("휴장일 파일 없음")

        def is_regular_session(self, _dt):  # noqa: ANN001
            raise RuntimeError("unreachable")

    refuse_if_regular_session("백필", now=_at(13, 0), calendar=_BrokenCalendar())  # type: ignore[arg-type]

    assert "판정 불가" in capsys.readouterr().out
