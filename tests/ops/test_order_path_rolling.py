"""주문 제출이 **재현되는가, 그날의 우연이었나** (2026-09-09 고도화).

하루 값(`sizer_funnel.submitted`)과 연속 충족일(`order-path-live`) 사이에 빠져 있던
발생 빈도를 센다. 판정하는 축이 아니라 관측 축이므로, 이 파일이 지키는 것은 **0과
못 잼을 섞지 않는 것**(L18)과 창이 오늘을 제대로 받는 것 둘이다.
"""

from __future__ import annotations

from datetime import date

from messiah.ops.order_path_rolling import judge, summary_line


def _report(submitted: int | None) -> dict:
    if submitted is None:
        return {"sizer_funnel": None}
    return {"sizer_funnel": {"cycles": 2, "submitted": submitted}}


def test_counts_days_with_a_submission_not_the_submissions():
    """세는 것은 **날 수**다 — 하루에 두 건 제출한 날도 1일이다."""
    reports = {
        date(2026, 9, 1): _report(0),
        date(2026, 9, 2): _report(2),
        date(2026, 9, 3): _report(0),
        date(2026, 9, 4): _report(1),
        date(2026, 9, 7): _report(0),
    }
    v = judge(day=date(2026, 9, 7), reports=reports, window_days=5)

    assert v.days_measured == 5
    assert v.days_with_submission == 2
    assert v.dates == (date(2026, 9, 2), date(2026, 9, 4))


def test_unmeasured_days_are_not_counted_as_zero():
    """`sizer_funnel`이 None인 날은 **못 잰 날**이다 (L18).

    0으로 세면 배선이 끊긴 날이 「주문 경로가 조용했다」로 둔갑한다 — 이 저장소가
    반복해서 맞은 형태다.
    """
    reports = {
        date(2026, 8, 27): _report(None),
        date(2026, 8, 28): _report(None),
        date(2026, 9, 1): _report(0),
        date(2026, 9, 2): _report(1),
    }
    v = judge(day=date(2026, 9, 2), reports=reports, window_days=5)

    assert v.days_measured == 2, "못 잰 이틀이 창을 채워서는 안 된다"
    assert v.days_with_submission == 1


def test_today_is_taken_from_the_caller_not_the_file():
    """오늘 리포트는 지금 만드는 중이라 파일에 없다 — 건네받은 값이 이긴다."""
    reports = {
        date(2026, 9, 8): _report(0),
        date(2026, 9, 9): _report(0),  # 파일본(예비본일 수 있다)
    }
    v = judge(day=date(2026, 9, 9), reports=reports, today_submitted=1, window_days=5)

    assert v.days_with_submission == 1
    assert v.dates == (date(2026, 9, 9),)


def test_window_takes_the_most_recent_days_only():
    reports = {date(2026, 9, d): _report(1) for d in (1, 2, 3, 4, 7, 8)}
    v = judge(day=date(2026, 9, 8), reports=reports, window_days=3)

    assert v.days_measured == 3
    assert v.dates == (date(2026, 9, 4), date(2026, 9, 7), date(2026, 9, 8))


def test_future_days_are_not_in_the_window():
    reports = {date(2026, 9, 8): _report(0), date(2026, 9, 10): _report(1)}
    v = judge(day=date(2026, 9, 8), reports=reports, window_days=5)

    assert v.days_measured == 1
    assert v.days_with_submission == 0


def test_nothing_measured_says_so_instead_of_zero():
    v = judge(day=date(2026, 9, 9), reports={date(2026, 9, 9): _report(None)}, window_days=5)

    assert v.days_measured == 0
    assert "미측정" in summary_line(v.to_dict())


def test_summary_line_names_the_days():
    reports = {date(2026, 9, 8): _report(0), date(2026, 9, 9): _report(1)}
    line = summary_line(judge(day=date(2026, 9, 9), reports=reports).to_dict())

    assert line is not None
    assert "2026-09-09" in line
    assert "잰 2일 중 1일" in line


def test_no_axis_no_line():
    assert summary_line(None) is None
