"""적재 계열 캘린더 계약 — 2026-08-07 실측을 회귀 테스트로 못박는다.

그날의 사실관계(`ops/series_expectation.py` 모듈 docstring):
  8월 둘째 목요일 = 8/13 = 코스피200옵션 8월물 최종거래일 → 그날 만기 목위클리는 미상장.
  그 물이 상장될 차례이던 8/7(금)부터 8/13까지 5거래일간 목위클리가 존재하지 않는다.
"""

from __future__ import annotations

from datetime import date

import pytest

from messiah.core import universe
from messiah.core.event_calendar import EventCalendar
from messiah.ops import series_expectation as se

_THU_WEEKLY = "option_chain/weekly_thu"


@pytest.fixture
def calendar() -> EventCalendar:
    return EventCalendar.from_file()


def test_august_2026_monthly_expiry_is_second_thursday(calendar):
    """전제 확인 — 이게 틀리면 아래 전부가 의미 없다."""
    expiry = calendar.monthly_expiry(2026, 8)
    assert expiry == date(2026, 8, 13)
    assert expiry.weekday() == 3  # 목요일


@pytest.mark.parametrize(
    "day, listed",
    [
        (date(2026, 8, 5), True),  # 수 — 8/6 만기물이 상장 중 (아카이브에 파일 있음)
        (date(2026, 8, 6), True),  # 목 — 만기 당일, 그날도 거래된다 (파일 있음)
        (date(2026, 8, 7), False),  # 금 — 다음 물이 8/13 만기라 미상장 (파일 없음, 실측)
        (date(2026, 8, 10), False),
        (date(2026, 8, 11), False),
        (date(2026, 8, 12), False),
        (date(2026, 8, 13), False),  # 먼슬리 만기 당일
        (date(2026, 8, 14), True),  # 금 — 8/20 만기물로 재개
        (date(2026, 8, 20), True),
    ],
)
def test_thursday_weekly_listing_window(calendar, day, listed):
    """2026-08-07 실측 + 사용자 제공 KRX 규정 대조표 그대로."""
    assert calendar.thursday_weekly_listed(day) is listed


def test_listing_resumes_on_the_friday_after_monthly_expiry(calendar):
    assert calendar.thursday_weekly_listing_resumes(date(2026, 8, 7)) == date(2026, 8, 14)
    assert calendar.thursday_weekly_listing_resumes(date(2026, 8, 13)) == date(2026, 8, 14)


def test_has_thursday_weekly_answers_a_different_question(calendar):
    """두 함수가 **다른 질문**이라는 것이 이 설계의 핵심이다.

    8/7(금)은 ISO 32주차이고 그 주에는 8/6 만기가 있었다 — `has_thursday_weekly`는 True.
    그런데 그날 폴링해서 받을 체인은 없다 — `thursday_weekly_listed`는 False.
    이 구분을 놓친 것이 2026-08-07 오판의 직접 원인이다.
    """
    assert calendar.has_thursday_weekly(date(2026, 8, 7)) is True
    assert calendar.thursday_weekly_listed(date(2026, 8, 7)) is False


def test_contract_marks_only_thursday_weekly_as_absent(calendar):
    contract = se.for_day(date(2026, 8, 7), list(universe.DEFAULT_UNIVERSE), calendar)
    assert contract[_THU_WEEKLY].required is False
    assert contract[_THU_WEEKLY].resumes_on == date(2026, 8, 14)
    # 나머지는 전부 필수 — 하나가 미상장이라고 옆 계열까지 면제되면 안 된다.
    for name, item in contract.items():
        if name != _THU_WEEKLY:
            assert item.required is True, name


def test_contract_is_all_required_on_a_normal_day(calendar):
    contract = se.for_day(date(2026, 8, 6), list(universe.DEFAULT_UNIVERSE), calendar)
    assert all(item.required for item in contract.values())
    assert se.summarize(contract) == []  # 정상일엔 한 줄도 안 찍는다


def test_baseline_series_are_in_the_contract_even_without_tokens(calendar):
    """수급·틱은 유니버스 토큰이 없다 — 그렇다고 계약에서 빠지면 안 된다."""
    contract = se.for_day(date(2026, 8, 7), [], calendar)
    for name in se.BASELINE_SERIES:
        assert contract[name].required is True


def test_note_has_no_series_name_but_label_does(calendar):
    """커버리지 표가 이름을 이미 찍으므로 `note`에는 이름이 없어야 한다."""
    item = se.for_day(date(2026, 8, 7), list(universe.DEFAULT_UNIVERSE), calendar)[_THU_WEEKLY]
    assert _THU_WEEKLY not in item.note
    assert item.label.startswith(f"{_THU_WEEKLY}: ")
