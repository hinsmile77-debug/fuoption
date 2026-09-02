"""세션 밖의 침묵을 「죽음」이라 부르지 않는다 (2026-09-01 F-85).

2026-09-01 08:20 화면 실측: 판단 배지 아래에 `죽음(1036분 침묵) · 프로세스 확인`이
찍혔는데, 그 시각 G2는 살아 있었다. 판단은 30분 격자로 **장중에만** 나가므로 밤사이
침묵은 정상이다. 매 거래일 08:20~09:00 40분간 확정적으로 뜨는 문구였고, 그 40분 내내
「프로세스를 확인하라」는 틀린 처방이었다.

## 이 파일의 절반은 **안 바뀐 것**을 지킨다

「죽음」 판정은 진짜 사고 탐지 경로다. 감도를 낮추는 변경에서 진짜 사고를 「대기」로
덮으면 원래 결함보다 나쁘다(2026-08-21 F-11 ㉠와 같은 규율). 그래서 세션 안 갈래의
판정 불변을 먼저 못 박고, 그 다음에 세션 밖 갈래를 잰다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from messiah.core.event_calendar import DEFAULT_SESSION
from messiah.core.state_cache import StateCache
from messiah.ui.app import _off_session_caption
from messiah.ui.data_source import FreshnessBadge, LiveDataSource, TopicSnapshot

_KST = timezone(timedelta(hours=9))
_CADENCE = 1800.0  # 판단 격자 30분 (2026-08-27 Redis 실측)


def _snap(age_minutes: float, *, in_session: bool | None) -> TopicSnapshot:
    return TopicSnapshot(
        message=None,
        badge=FreshnessBadge.STALE,
        age_seconds=age_minutes * 60.0,
        cadence_seconds=_CADENCE,
        in_session=in_session,
    )


# ------------------------------------------------ 안 바뀌는 쪽(먼저 못 박는다)


def test_a_real_stall_inside_the_session_is_still_dead() -> None:
    """**이 테스트가 F-85의 안전선이다.** 장중 95분 침묵(주기 30분의 3배 초과)은
    종전과 똑같이 「죽음」이어야 한다 — 여기가 바뀌면 변경이 잘못된 것이다."""
    assert _snap(95, in_session=True).dead is True


def test_a_short_gap_inside_the_session_is_not_dead() -> None:
    """31분은 한 주기를 갓 넘긴 것이라 「느려졌다」이지 「죽었다」가 아니다 — 종전 그대로."""
    assert _snap(31, in_session=True).dead is False


def test_an_unknown_cadence_is_still_never_dead() -> None:
    """주기를 모르면 판정하지 않는다 (2026-08-14 G-4) — 이 규율도 그대로다."""
    snap = TopicSnapshot(
        message=None,
        badge=FreshnessBadge.STALE,
        age_seconds=99999.0,
        cadence_seconds=None,
        in_session=True,
    )
    assert snap.dead is False


def test_an_unmeasured_session_axis_keeps_the_old_judgement() -> None:
    """`in_session=None`은 「세션 밖이 아니다」가 아니라 「모른다」다 — 옛 판정 그대로."""
    assert _snap(95, in_session=None).dead is True


# ------------------------------------------------ 바뀌는 쪽(세션 밖 하나뿐)


def test_overnight_silence_before_the_open_is_not_dead() -> None:
    """1036분 = 전일 15:29 판단을 오늘 08:45에 보고 있는 상태. 사고가 아니다."""
    assert _snap(1036, in_session=False).dead is False


def test_silence_after_the_close_is_not_dead() -> None:
    """마감 뒤에도 격자는 안 돈다 — 20분 침묵을 사고로 부르지 않는다."""
    assert _snap(20, in_session=False).dead is False


def test_the_caption_says_when_not_how_long() -> None:
    """세션 밖이라면 사람이 알아야 할 것은 경과가 아니라 **언제 것인가**다."""
    caption = _off_session_caption(
        _snap(1011, in_session=False), now=datetime(2026, 9, 1, 8, 20, tzinfo=_KST)
    )
    assert caption is not None
    assert "장 개시 전" in caption
    assert "08-31 15:29" in caption


def test_the_caption_names_the_after_close_phase() -> None:
    caption = _off_session_caption(
        _snap(20, in_session=False), now=datetime(2026, 9, 1, 15, 50, tzinfo=_KST)
    )
    assert caption is not None
    assert "장 마감 후" in caption


def test_the_caption_stays_out_of_the_way_inside_the_session() -> None:
    assert (
        _off_session_caption(
            _snap(95, in_session=True), now=datetime(2026, 9, 1, 10, 0, tzinfo=_KST)
        )
        is None
    )


def test_a_fresh_value_outside_the_session_keeps_the_second_counter() -> None:
    """마감 직후 방금 받은 값까지 「언제 것인가」로 바꾸면 정보가 줄어든다."""
    assert (
        _off_session_caption(
            _snap(0.1, in_session=False), now=datetime(2026, 9, 1, 15, 40, tzinfo=_KST)
        )
        is None
    )


# ------------------------------------------------ 배선 — 화면이 실제로 이 축을 받는가


def _source_at(hh: int, mm: int) -> LiveDataSource:
    return LiveDataSource(StateCache(), now_fn=lambda: datetime(2026, 9, 1, hh, mm, tzinfo=_KST))


def test_the_live_source_marks_the_session_window() -> None:
    """경계는 `SessionHours` 정본을 쓴다 — 화면이 숫자를 따로 들고 있으면 갈라진다."""
    assert _source_at(8, 20).snapshot("DecisionIntent").in_session is False
    assert _source_at(8, 45).snapshot("DecisionIntent").in_session is True
    assert _source_at(10, 0).snapshot("DecisionIntent").in_session is True
    assert _source_at(15, 35).snapshot("DecisionIntent").in_session is True
    assert _source_at(15, 50).snapshot("DecisionIntent").in_session is False


def test_the_session_boundaries_are_the_shared_constants() -> None:
    assert DEFAULT_SESSION.first_tick_time.hour == 8
    assert DEFAULT_SESSION.first_tick_time.minute == 45
    assert DEFAULT_SESSION.close_time.hour == 15
    assert DEFAULT_SESSION.close_time.minute == 35
