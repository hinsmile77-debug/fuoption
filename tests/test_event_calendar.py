"""Event Calendar — KRX 휴장일·세션 판정 (신규, 2026-07-27)."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from messiah.core.event_calendar import (
    EventCalendar,
    SessionHours,
    coverage_runway_days,
    covered_through,
    load_document,
    load_holidays,
)
from messiah.core.timeutil import KST, UTC

_HOLIDAYS_2026 = frozenset(
    {
        date(2026, 1, 1),  # 신정 (목요일)
        date(2026, 2, 17),  # 설날 (화요일)
    }
)


@pytest.fixture
def calendar() -> EventCalendar:
    return EventCalendar(_HOLIDAYS_2026)


# ---------------------------------------------------------------- is_trading_day


def test_is_trading_day_true_on_ordinary_weekday(calendar: EventCalendar) -> None:
    assert calendar.is_trading_day(date(2026, 7, 27)) is True  # 월요일, 휴장일 아님


def test_is_trading_day_false_on_weekend(calendar: EventCalendar) -> None:
    assert calendar.is_trading_day(date(2026, 7, 25)) is False  # 토요일
    assert calendar.is_trading_day(date(2026, 7, 26)) is False  # 일요일


def test_is_trading_day_false_on_registered_holiday(calendar: EventCalendar) -> None:
    assert calendar.is_trading_day(date(2026, 1, 1)) is False
    assert calendar.is_trading_day(date(2026, 2, 17)) is False


def test_is_trading_day_raises_for_year_without_data(calendar: EventCalendar) -> None:
    with pytest.raises(ValueError, match="2027"):
        calendar.is_trading_day(date(2027, 1, 4))


def test_explicit_years_covers_a_zero_holiday_year() -> None:
    # holidays가 비어 있으면 `d.year for d in holidays`로는 어떤 연도도 "데이터 있음"이
    # 될 수 없다 — 실제로 휴장일이 0건인 연도(이론상 가능)를 "데이터 없음"과 구분하려면
    # years를 명시해야 한다.
    cal = EventCalendar(frozenset(), years=frozenset({2026}))
    assert cal.is_trading_day(date(2026, 7, 27)) is True
    with pytest.raises(ValueError, match="2027"):
        cal.is_trading_day(date(2027, 1, 4))


# ---------------------------------------------------------------- next/previous_trading_day


def test_next_trading_day_skips_weekend_and_holiday(calendar: EventCalendar) -> None:
    # 2025-12-31(목) 다음 -> 2026-01-01(신정, 휴장) -> 01-02(금, 실제로는 조기폐장 특이일
    # 이지만 캘린더 데이터엔 휴장으로 안 올라있음) 이 됨. 별도 연도(2025)엔 데이터가 없어
    # ValueError가 나므로 2026년 내에서만 검증.
    assert calendar.next_trading_day(date(2026, 1, 1)) == date(2026, 1, 2)


def test_next_trading_day_skips_full_weekend(calendar: EventCalendar) -> None:
    assert calendar.next_trading_day(date(2026, 7, 24)) == date(2026, 7, 27)  # 금 -> 월


def test_previous_trading_day_skips_holiday(calendar: EventCalendar) -> None:
    # 2026-02-18(수) 하루 전은 설날(02-17, 휴장) -> 02-16(월, 평일)로 건너뛴다.
    assert calendar.previous_trading_day(date(2026, 2, 18)) == date(2026, 2, 16)


# ---------------------------------------------------------------- is_regular_session / to_close


def test_is_regular_session_true_during_open_hours(calendar: EventCalendar) -> None:
    dt = datetime(2026, 7, 27, 10, 0, tzinfo=KST)  # 월요일 10시
    assert calendar.is_regular_session(dt) is True


def test_is_regular_session_false_before_open_and_at_close(calendar: EventCalendar) -> None:
    before_open = datetime(2026, 7, 27, 8, 59, tzinfo=KST)
    at_close = datetime(2026, 7, 27, 15, 35, tzinfo=KST)  # 반개구간 — close_time 미포함
    assert calendar.is_regular_session(before_open) is False
    assert calendar.is_regular_session(at_close) is False


def test_is_regular_session_false_on_holiday_even_during_hours(calendar: EventCalendar) -> None:
    dt = datetime(2026, 1, 1, 10, 0, tzinfo=KST)
    assert calendar.is_regular_session(dt) is False


def test_is_regular_session_converts_from_utc(calendar: EventCalendar) -> None:
    # 2026-07-27 01:00 UTC == 2026-07-27 10:00 KST
    dt = datetime(2026, 7, 27, 1, 0, tzinfo=UTC)
    assert calendar.is_regular_session(dt) is True


def test_is_regular_session_rejects_naive_datetime(calendar: EventCalendar) -> None:
    with pytest.raises(ValueError, match="naive"):
        calendar.is_regular_session(datetime(2026, 7, 27, 10, 0))  # noqa: DTZ001


def test_minutes_to_close_none_outside_session(calendar: EventCalendar) -> None:
    assert calendar.minutes_to_close(datetime(2026, 7, 27, 8, 0, tzinfo=KST)) is None


def test_minutes_to_close_counts_down_to_regular_stop(calendar: EventCalendar) -> None:
    dt = datetime(2026, 7, 27, 15, 25, tzinfo=KST)
    assert calendar.minutes_to_close(dt) == pytest.approx(10.0)


def test_custom_session_hours() -> None:
    cal = EventCalendar(_HOLIDAYS_2026, session=SessionHours())
    assert cal.minutes_to_close(datetime(2026, 7, 27, 9, 5, tzinfo=KST)) == pytest.approx(390.0)


# ---------------------------------------------------------------- is_expiry_day


def test_is_expiry_day_true_on_weekly_monday(calendar: EventCalendar) -> None:
    assert calendar.is_expiry_day(date(2026, 7, 27)) is True  # 월요일


def test_is_expiry_day_true_on_weekly_thursday(calendar: EventCalendar) -> None:
    assert calendar.is_expiry_day(date(2026, 7, 23)) is True  # 목요일


def test_is_expiry_day_false_on_non_expiry_weekday(calendar: EventCalendar) -> None:
    assert calendar.is_expiry_day(date(2026, 7, 28)) is False  # 화요일


def test_is_expiry_day_false_on_holiday_even_if_would_be_monday(calendar: EventCalendar) -> None:
    assert calendar.is_expiry_day(date(2026, 2, 17)) is False  # 화요일이자 설날(휴장일)


def test_is_expiry_day_true_on_second_thursday_of_month(calendar: EventCalendar) -> None:
    assert calendar.is_expiry_day(date(2026, 7, 9)) is True  # 2026-07 두 번째 목요일


def test_is_expiry_day_true_on_first_thursday_via_weekly_rule(calendar: EventCalendar) -> None:
    # 매월 첫 목요일은 "두 번째 목요일"(월물) 규칙엔 안 걸리지만, 위클리 목요일(L/M) 규칙은
    # 요일 자체로 매주 발동하므로 여전히 True — 두 규칙은 배타적이지 않다(모듈 docstring).
    assert calendar.is_expiry_day(date(2026, 7, 2)) is True


# ---------------------------------------------------------------- load_holidays


def test_load_holidays_reads_real_config_file() -> None:
    holidays = load_holidays()
    assert date(2026, 1, 1) in holidays
    assert date(2026, 12, 25) in holidays
    assert date(2026, 7, 1) not in holidays  # 평범한 거래일은 목록에 없어야 함


def test_load_holidays_missing_file_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        load_holidays(tmp_path / "no_such_file.yaml")


def test_load_holidays_includes_year_end_exchange_closure() -> None:
    """연말 휴장(12-31)은 **관공서 공휴일이 아니라 거래소 고유 휴장**이라 놓치기 쉽다.

    2026-08-17에 등재했다 — 그 전까지 파일 헤더는 "조기폐장이지 휴장 아님"이라고 적고
    있었고, 같은 파일의 2025-12-31 실측(완전 휴장)과 어긋난 채 남아 있었다.
    """
    holidays = load_holidays()
    assert date(2025, 12, 31) in holidays
    assert date(2026, 12, 31) in holidays


def test_load_holidays_skips_non_year_keys(tmp_path) -> None:
    """`covered_through` 같은 메타 키가 데이터로 읽히면 안 된다.

    종전 로더는 `raw.values()`를 그대로 훑어서, 문자열 값을 **문자 단위로** 순회하며
    `date.fromisoformat("2")`로 죽었다 — 메타 키를 넣는 것 자체가 불가능했다.
    """
    path = tmp_path / "cal.yaml"
    path.write_text(
        'covered_through: "2026-12-31"\nversion: "1"\n2026:\n  - "2026-01-01"\n',
        encoding="utf-8",
    )
    assert load_holidays(path) == frozenset({date(2026, 1, 1)})


def test_load_holidays_still_raises_on_broken_date(tmp_path) -> None:
    """메타 키를 건너뛰는 것과 **깨진 날짜**를 건너뛰는 것은 다른 일이다 (L3).

    조용히 넘기면 그 한 줄 때문에 그날 시스템이 휴장일에 뜬다.
    """
    path = tmp_path / "cal.yaml"
    path.write_text('2026:\n  - "2026-13-99"\n  - "januaryish"\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_holidays(path)


# ---------------------------------------------------------------- covered_through (2026-08-17)


def test_coverage_runway_days_sign_convention(tmp_path) -> None:
    """부호가 뜻이다 — 양수는 남은 날수, 음수는 만료된 날수, None은 선언 부재."""
    path = tmp_path / "cal.yaml"
    path.write_text('covered_through: "2026-12-31"\n2026:\n  - "2026-01-01"\n', encoding="utf-8")
    doc = load_document(path)

    assert covered_through(doc) == date(2026, 12, 31)
    assert coverage_runway_days(doc, date(2026, 12, 1)) == 30
    assert coverage_runway_days(doc, date(2026, 12, 31)) == 0  # 오늘이 마지막 확인일
    assert coverage_runway_days(doc, date(2027, 1, 10)) == -10  # 만료 10일째


def test_coverage_runway_days_none_without_declaration(tmp_path) -> None:
    """**None과 과거 날짜를 가른다** — 후자는 "확인했고 만료됐다", 전자는 "확인 안 했다"다."""
    path = tmp_path / "cal.yaml"
    path.write_text('2026:\n  - "2026-01-01"\n', encoding="utf-8")
    doc = load_document(path)
    assert covered_through(doc) is None
    assert coverage_runway_days(doc, date(2026, 12, 1)) is None


def test_coverage_runway_days_none_on_unparseable_declaration(tmp_path) -> None:
    path = tmp_path / "cal.yaml"
    path.write_text('covered_through: "not-a-date"\n2026: []\n', encoding="utf-8")
    assert coverage_runway_days(load_document(path), date(2026, 12, 1)) is None


def test_real_config_declares_coverage() -> None:
    """정본 파일에 선언이 **있어야** 한다 — 지우면 자가 점검이 그 사실을 경고한다."""
    doc = load_document()
    assert covered_through(doc) is not None, "covered_through를 지우면 만료 경고가 죽는다"
