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


# --- 청산 확인 칸 (2026-09-14 G-65, 대응 1-3) ---------------------------------


def _report_with_tags(submitted: int | None, **tags: int) -> dict:
    report = _report(submitted)
    report["tag_counts"] = dict(tags)
    return report


def test_submission_without_exit_evidence_counts_as_unclosed():
    """진입만 나가고 청산 흔적이 없는 날은 **못 닫은 날**이다 — 이것이 1-3의 형태다.

    09-09·09-10·09-14 사흘이 정확히 이랬고, 세는 칸이 없어 사흘 동안 안 걸렸다.
    """
    reports = {
        date(2026, 9, 9): _report_with_tags(1, OrderSubmit=1),
        date(2026, 9, 10): _report_with_tags(1, OrderSubmit=1),
        date(2026, 9, 14): _report_with_tags(2, OrderSubmit=2),
    }
    v = judge(day=date(2026, 9, 14), reports=reports, window_days=5)

    assert v.days_with_submission == 3
    assert v.days_with_exit_evidence == 0
    assert v.exit_dates == ()
    assert "청산 확인 0/3일" in summary_line(v.to_dict())
    assert "F-104" in summary_line(v.to_dict()), "미구현이라는 사실을 사람이 바로 읽어야 한다"


def test_exit_evidence_is_counted_only_on_submission_days():
    """분모는 제출일이다 — 제출이 없던 날에 청산이 없는 것은 당연하고 셀 값이 아니다."""
    reports = {
        date(2026, 9, 9): _report_with_tags(0, KillSwitchLiquidating=1),
        date(2026, 9, 10): _report_with_tags(1, CircuitBreakerLiquidating=1),
        date(2026, 9, 14): _report_with_tags(1),
    }
    v = judge(day=date(2026, 9, 14), reports=reports, window_days=5)

    assert v.days_with_submission == 2
    assert v.exit_dates == (date(2026, 9, 10),)
    assert "청산 확인 1/2일" in summary_line(v.to_dict())


def test_fill_matched_is_not_exit_evidence():
    """`FillMatched`는 **진입 체결에도 뜬다** — 증거로 쓰면 이 축이 자기 구멍을 덮는다."""
    reports = {date(2026, 9, 14): _report_with_tags(1, FillMatched=2, OrderSubmit=1)}
    v = judge(day=date(2026, 9, 14), reports=reports, window_days=5)

    assert v.days_with_exit_evidence == 0


def test_today_tag_counts_come_from_the_caller():
    """오늘 리포트는 지금 만드는 중이라 파일에 없다 — 건네받은 태그가 이긴다."""
    reports = {date(2026, 9, 14): _report_with_tags(1)}  # 파일본에는 청산 흔적이 없다
    v = judge(
        day=date(2026, 9, 14),
        reports=reports,
        today_submitted=1,
        today_tag_counts={"KillSwitchLiquidating": 1},
        window_days=5,
    )

    assert v.exit_dates == (date(2026, 9, 14),)


def test_no_submission_says_nothing_about_closing():
    """열지도 않은 것을 닫았는지 묻는 칸은 소음이다 — 제출 0일이면 꼬리표를 안 단다."""
    reports = {date(2026, 9, 14): _report_with_tags(0)}
    line = summary_line(judge(day=date(2026, 9, 14), reports=reports).to_dict())

    assert "청산 확인" not in line


# --- 판정 불변 (G-65가 기존 판정을 건드리지 않았는가) --------------------------


def test_existing_counts_and_prefix_are_unchanged():
    """**판정 불변** — 제출 집계와 요약 앞부분은 G-65 이전과 한 글자도 다르지 않다.

    이 축의 소비처는 `breaches`가 아니라 표시 한 줄이다. 앞부분이 바뀌면 그 한 줄을
    읽어 온 기존 판정(장후 배치 요약·사람 눈)이 같이 흔들린다.
    """
    reports = {
        date(2026, 9, 1): _report(0),
        date(2026, 9, 2): _report(2),
        date(2026, 9, 3): _report(None),
        date(2026, 9, 4): _report(1),
    }
    v = judge(day=date(2026, 9, 4), reports=reports, window_days=5)

    assert v.days_measured == 3
    assert v.days_with_submission == 2
    assert v.dates == (date(2026, 9, 2), date(2026, 9, 4))
    assert summary_line(v.to_dict()).startswith(
        "주문 제출 발생일수: 잰 3일 중 2일 (창 5거래일) — 2026-09-02, 2026-09-04"
    )


def test_reports_without_the_new_field_render_as_before():
    """옛 리포트 dict(그 필드가 없던 날)는 꼬리표 없이 종전 그대로 찍힌다 (L18)."""
    legacy = {
        "window_days": 5,
        "days_measured": 3,
        "days_with_submission": 2,
        "dates": ["2026-09-02", "2026-09-04"],
    }

    assert summary_line(legacy) == (
        "주문 제출 발생일수: 잰 3일 중 2일 (창 5거래일) — 2026-09-02, 2026-09-04"
    )
