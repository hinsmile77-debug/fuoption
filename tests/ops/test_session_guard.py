"""장중 학습·백필 거부 — SYSTEM.md R11을 코드로 강제 (2026-08-05 신설).

2026-08-04에 정규장 중 백필 1회 + 모델 스윕 4회 + 워크포워드 1회가 돌았다. 그때까지 R11은
문서에만 있었고 아무것도 그것을 막지 않았다(`ops/session_guard.py` 모듈 docstring).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from messiah.core.event_calendar import EventCalendar
from messiah.core.timeutil import KST
from messiah.ops import session_guard
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


# ------------------- 아카이브 정합 가드 (2026-08-05 2차, 고도화 5)
#
# 2026-08-05에 상위 Horizon 봉의 3~17%가 잘린 채 아카이브에 들어갔다. 1분봉은 무손상이라
# `run_recompose.py`로 복구되지만, **복구 전에 학습을 돌리면 잘린 봉을 그대로 배운다.**


def _write_report(log_dir: Path, day: date, **fields) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    payload = {"date": day.isoformat(), "horizon_findings": [], "late_bar_drops": 0}
    payload.update(fields)
    (log_dir / f"daily_integrity_{day:%Y%m%d}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_a_day_with_horizon_findings_is_refused(tmp_path: Path, capsys):
    day = date(2026, 8, 5)
    _write_report(tmp_path, day, horizon_findings=["3m 거래량 합 19,000 ≠ 1분봉 합 22,858"])

    with pytest.raises(SystemExit) as exc:
        session_guard.refuse_if_archive_corrupt("모델 스윕", [day], log_dir=tmp_path)

    assert exc.value.code == session_guard.REFUSED_EXIT_CODE
    err = capsys.readouterr().err
    assert "run_recompose.py" in err
    assert "2026-08-05" in err


def test_late_bar_drops_alone_do_not_block_training(tmp_path: Path):
    """**재합성이 끝난 뒤에도 남는 지표로 막으면 그날이 영원히 학습 불가가 된다.**

    `late_bar_drops`는 수집 당시의 사건을 세는 지표라 재합성 후에도 남는다(그게 그 지표의
    존재 이유다). 아카이브가 실제로 손상됐는지는 `horizon_findings`가 말한다.
    """
    day = date(2026, 8, 5)
    _write_report(tmp_path, day, horizon_findings=[], late_bar_drops=26)

    session_guard.refuse_if_archive_corrupt("모델 스윕", [day], log_dir=tmp_path)  # 통과


def test_days_without_a_report_do_not_block(tmp_path: Path):
    """리포트는 2026-07-27부터 있고 학습 구간은 보통 수개월이다 — 없는 날을 전부 막으면
    아무것도 학습할 수 없다."""
    session_guard.refuse_if_archive_corrupt(
        "모델 스윕", [date(2026, 3, 2), date(2026, 3, 3)], log_dir=tmp_path
    )


def test_force_flag_passes_but_says_so(tmp_path: Path, capsys):
    day = date(2026, 8, 5)
    _write_report(tmp_path, day, horizon_findings=["3m 거래량 합 불일치"])

    session_guard.refuse_if_archive_corrupt("모델 스윕", [day], force=True, log_dir=tmp_path)

    assert "--force-corrupt-archive" in capsys.readouterr().out


def test_a_broken_report_does_not_block_the_guard(tmp_path: Path, capsys):
    """가드가 본 기능을 막는 쪽으로 실패하면 안 된다 — 깨진 리포트 하나는 건너뛴다."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "daily_integrity_20260805.json").write_text("{깨진 JSON", encoding="utf-8")

    session_guard.refuse_if_archive_corrupt("모델 스윕", [date(2026, 8, 5)], log_dir=tmp_path)


# --------------------------------- 기동 창 (2026-08-06 P0-2, 부팅 자동 복구)

from datetime import date as _date  # noqa: E402

from messiah.core.timeutil import KST as _KST  # noqa: E402


class _Cal:
    def __init__(self, trading: bool = True) -> None:
        self._trading = trading

    def is_trading_day(self, day: _date) -> bool:
        return self._trading


def _at(hour: int, minute: int):
    return datetime(2026, 8, 6, hour, minute, tzinfo=_KST)


def test_reboot_during_the_session_may_relaunch():
    """2026-08-06의 사건 그 자체 — 10:05 부팅 후 즉시 재개돼야 21분 공백이 안 생긴다."""
    allowed, reason = session_guard.launch_window_verdict(now=_at(10, 5), calendar=_Cal())

    assert allowed, reason


def test_scheduled_start_time_is_inside_the_window():
    """정시 트리거(08:35)가 자기 가드에 막히면 매일 아침 아무것도 안 뜬다."""
    assert session_guard.launch_window_verdict(now=_at(8, 35), calendar=_Cal())[0]


def test_boot_before_the_window_does_not_launch():
    """새벽 재부팅에 하루 종일 빈 프로세스가 KIS WS를 물고 있으면 안 된다."""
    allowed, reason = session_guard.launch_window_verdict(now=_at(3, 0), calendar=_Cal())

    assert not allowed
    assert "이전" in reason


def test_boot_after_the_close_does_not_launch():
    allowed, reason = session_guard.launch_window_verdict(now=_at(16, 30), calendar=_Cal())

    assert not allowed
    assert "run_postmarket.py" in reason, "대안 절차를 안 알려주면 사람이 헤맨다"


def test_close_time_itself_is_outside_the_window():
    """15:35는 수집 종료 시각 — 그 시각에 뜨면 수집할 구간이 0초다(반개구간 규율)."""
    assert not session_guard.launch_window_verdict(now=_at(15, 35), calendar=_Cal())[0]


def test_holiday_does_not_launch():
    allowed, reason = session_guard.launch_window_verdict(
        now=_at(10, 5), calendar=_Cal(trading=False)
    )

    assert not allowed
    assert "거래일이 아니다" in reason


def test_calendar_failure_allows_launch():
    """가드가 오판해서 수집을 막는 것이 오판해서 한 번 더 뜨는 것보다 나쁘다."""

    class _Broken:
        def is_trading_day(self, day):
            raise RuntimeError("달력 파일 손상")

    allowed, reason = session_guard.launch_window_verdict(now=_at(10, 5), calendar=_Broken())

    assert allowed
    assert "판정 불가" in reason
