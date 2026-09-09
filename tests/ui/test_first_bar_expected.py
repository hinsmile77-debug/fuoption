"""「봉이 있어야 하는 시각」은 첫 틱이 아니라 **첫 봉 완성 시각**이다 (2026-09-01 F-82).

2026-09-01 장전 점검 이상점 1-2: 08:45~08:50 사이 화면이 매 거래일 **확정적으로**
`🛑 오늘 봉이 없다` 적색을 띄웠다. 그 5분 동안 아무 사고도 없었다 — 틱은 08:45부터
들어오지만 5분봉의 첫 봉은 08:50에야 완성되기 때문이다(실측: 1m 08:46 · 3m 08:48 ·
5m 08:50). 매일 뜨는 경보는 경보가 아니라 배경이고, 그 학습은 진짜 사고가 난 날에 값을
치른다 — 2026-08-11 F-3이 정확히 그 병을 고치려고 이 문구를 갈랐었다.

**임계를 늦추는 변경이므로 반대 방향의 회귀도 함께 못 박는다**: 첫 봉 예정 시각이 지난
뒤에도 봉이 없으면 문구는 F-3이 쓴 `alert` 그대로여야 한다(월물 롤·수집기 두 후보 유지).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from messiah.core.event_calendar import DEFAULT_SESSION, EventCalendar
from messiah.ui.app import _first_bar_expected_at, _live_date_notice

_KST = timezone(timedelta(hours=9))


def _at(hh: int, mm: int) -> datetime:
    """2026-09-01(화, 거래일) 그 시각 — 전일 08-31 차트를 보고 있는 상황."""
    return datetime(2026, 9, 1, hh, mm, tzinfo=_KST)


_YESTERDAY = date(2026, 8, 31)


def test_five_minute_bar_is_not_yet_due_at_0847() -> None:
    """5분봉의 첫 봉은 08:45 + 5분 + 유예 5초 = 08:50:05다 — 08:47엔 없는 게 맞는다."""
    severity, notice = _live_date_notice(_YESTERDAY, now=_at(8, 47), horizon="5m")

    assert severity == "expected"
    assert "08:50" in notice
    assert "5m" in notice


def test_five_minute_bar_missing_at_0852_is_still_an_alert() -> None:
    """**임계를 늦춘 만큼만 늦춘다.** 예정 시각이 지났는데 없으면 종전과 같은 P0다."""
    severity, notice = _live_date_notice(_YESTERDAY, now=_at(8, 52), horizon="5m", symbol="A05609")

    assert severity == "alert"
    assert "A05609" in notice
    assert notice.index("월물 롤") < notice.index("수집기")  # F-3의 순서를 지킨다


def test_one_minute_bar_missing_at_0847_is_an_alert() -> None:
    """1분봉의 첫 봉은 08:46:02다 — 같은 08:47이라도 이쪽은 이미 늦은 것이다.
    Horizon마다 답이 달라야 한다는 것이 F-82의 요점이다."""
    severity, _notice = _live_date_notice(_YESTERDAY, now=_at(8, 47), horizon="1m")

    assert severity == "alert"


def test_a_market_holiday_branch_is_untouched() -> None:
    """휴장일 판정은 시각 판정보다 먼저다 — Horizon을 줘도 그 순서가 바뀌면 안 된다."""
    calendar = EventCalendar.from_file()

    severity, notice = _live_date_notice(
        date(2026, 8, 7),
        now=datetime(2026, 8, 8, 8, 47, tzinfo=_KST),
        calendar=calendar,
        horizon="5m",
    )

    assert severity == "expected"
    assert "휴장" in notice


def test_todays_bar_present_is_still_ok() -> None:
    """정상 갈래는 Horizon과 무관하게 그대로다."""
    severity, notice = _live_date_notice(date(2026, 9, 1), now=_at(9, 30), horizon="5m")

    assert severity == "ok"
    assert "오늘(2026-09-01)" in notice


def test_an_unknown_horizon_keeps_the_old_threshold() -> None:
    """모르는 Horizon에 예정 시각을 지어내지 않는다 — 옛 임계(첫 틱)로 되돌아간다(L18)."""
    assert _first_bar_expected_at(DEFAULT_SESSION.first_tick_time, "7m") is None
    assert _first_bar_expected_at(DEFAULT_SESSION.first_tick_time, None) is None

    severity, _notice = _live_date_notice(_YESTERDAY, now=_at(8, 47), horizon="7m")
    assert severity == "alert"


def test_expected_times_match_the_observed_first_bars() -> None:
    """2026-09-01 실측(1m 08:46 · 3m 08:48 · 5m 08:50)과 계산이 같은 분에 떨어진다 —
    유예를 화면이 따로 적지 않고 `data/close_grace`에서 가져오기 때문이다.

    **분까지만 잰다.** 2026-09-10 F-94로 상위 유예가 5,000 → 11,500ms가 되어 초 자리는
    08:48:05 → 08:48:11.5로 움직였지만, 이 임계가 답하는 질문은 「첫 봉이 도착할 만한
    분이 지났는가」다. 초 단위로 고정하면 유예를 조정할 때마다 이 테스트가 깨지면서
    정작 재려던 것(실측과 같은 분인가)을 말하지 못한다.
    """
    first_tick = DEFAULT_SESSION.first_tick_time

    observed = {"1m": time(8, 46), "3m": time(8, 48), "5m": time(8, 50)}
    for horizon, seen in observed.items():
        expected = _first_bar_expected_at(first_tick, horizon)
        assert expected is not None
        assert (expected.hour, expected.minute) == (seen.hour, seen.minute), horizon

    # 유예가 커져도 **1분봉은 첫 틱 다음 분**을 넘지 않아야 한다 — 그쪽 유예는 2초 그대로다.
    assert _first_bar_expected_at(first_tick, "1m") == time(8, 46, 2)
