"""**잘림**을 보는 축들 — 2026-08-07 P0-2·P0-4·고도화 1.

그날 13:41에 수집이 죽어 1시간 54분이 날아갔는데 네 축이 전부 초록이었다:

    봉 연속성    296개 08:45~13:40 · 결손 0분 ✅   (관측 구간 안쪽만 본다)
    거래량 대조  비율 0.998 · 전 구간 정상 ✅       (공통 분만 비교한다)
    관측 공백    없음 ✅                            (마지막 기동 이후는 안 센다)
    크래시       0건 ✅                             (파이썬 예외는 네이티브가 아니다)

전부 "구멍"을 묻고 아무도 "끊김"을 안 물었다. 이 파일은 그 질문을 지킨다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from messiah.core.timeutil import KST
from messiah.ops import series_coverage as sc
from messiah.ops.integrity_report import _tail_gap_minutes

_WINDOW = (datetime(2026, 8, 7, 8, 35, tzinfo=KST), datetime(2026, 8, 7, 15, 35, tzinfo=KST))


def _minutes(start: datetime, end: datetime, step: int = 1) -> list[datetime]:
    out, cur = [], start
    while cur <= end:
        out.append(cur)
        cur += timedelta(minutes=step)
    return out


# ---------------------------------------------------------------- P0-2 봉 꼬리


@pytest.mark.parametrize(
    "last_open, step, expected",
    [
        (datetime(2026, 8, 7, 15, 34, tzinfo=KST), 1, 0),  # 정상일 마지막 1분봉
        (datetime(2026, 8, 7, 13, 40, tzinfo=KST), 1, 114),  # 2026-08-07 실측
        (datetime(2026, 8, 7, 15, 5, tzinfo=KST), 30, 0),  # 정상일 마지막 30분봉
        (datetime(2026, 8, 7, 16, 0, tzinfo=KST), 1, 0),  # 마감 뒤(재생) — 음수는 접는다
    ],
)
def test_bar_tail_gap(last_open: datetime, step: int, expected: int):
    assert _tail_gap_minutes(last_open, step) == expected


# ---------------------------------------------------------------- P0-4 거래량 미수집


def test_volume_compare_counts_minutes_the_archive_never_got():
    from scripts.verify_archive_volume import compare_day

    official = {f"{h:02d}:{m:02d}": 100 for h in range(9, 16) for m in range(60)}
    archived = {f"{h:02d}:{m:02d}": 100 for h in range(9, 14) for m in range(60)}

    ratio, common, mine, theirs, missing = compare_day(archived, official)
    assert ratio == 1.0, "받은 것은 정확했다 — 그래서 비율만으로는 안 보인다"
    assert missing == 120, "받아야 할 것을 다 받았는지는 별개 축이다"


def test_volume_compare_ignores_archive_only_minutes():
    """장전 구간은 아카이브에만 있다 — 미수집으로 세면 매일 오탐이다."""
    from scripts.verify_archive_volume import compare_day

    official = {"09:00": 10, "09:01": 10}
    archived = {"08:45": 5, "09:00": 10, "09:01": 10}

    _ratio, _common, _mine, _theirs, missing = compare_day(archived, official)
    assert missing == 0


# ---------------------------------------------------------------- 고도화 1 세션 커버리지


def test_continuous_series_covering_the_session_is_full():
    coverage = sc.measure(
        "ticks",
        _minutes(datetime(2026, 8, 7, 8, 45, tzinfo=KST), datetime(2026, 8, 7, 15, 34, tzinfo=KST)),
        window=_WINDOW,
    )
    assert coverage.coverage_pct >= 95.0
    assert sc.findings_for(coverage) == []


def test_series_cut_mid_session_drops_below_the_floor():
    """2026-08-07 실측 재현 — 이 값이 70%인데 세 축은 100%라고 답했다."""
    coverage = sc.measure(
        "ticks",
        _minutes(datetime(2026, 8, 7, 8, 45, tzinfo=KST), datetime(2026, 8, 7, 13, 38, tzinfo=KST)),
        window=_WINDOW,
    )
    assert coverage.coverage_pct == pytest.approx(70.0, abs=1.0)
    assert any("세션 커버리지" in f for f in sc.findings_for(coverage))


def test_intermittent_poller_is_not_punished_for_its_cadence():
    """10분 격자 폴러는 창의 30%에만 행이 있다 — 그걸 커버리지 30%로 읽으면 매일 오탐이다."""
    stamps: list[datetime] = []
    cur = datetime(2026, 8, 7, 8, 40, tzinfo=KST)
    while cur < _WINDOW[1]:
        stamps += [cur, cur + timedelta(minutes=1), cur + timedelta(minutes=2)]
        cur += timedelta(minutes=10)

    coverage = sc.measure("option_chain/regular", stamps, window=_WINDOW)
    assert coverage.cadence_minutes == 10.0
    assert coverage.coverage_pct == 100.0
    assert sc.findings_for(coverage) == []


def test_unlisted_series_is_not_dragged_down_by_coverage():
    """미상장일은 0행이 정답 — 커버리지 축이 그것까지 사고로 만들면 안 된다."""
    from messiah.ops.series_expectation import Expectation

    unlisted = Expectation(
        series="option_chain/weekly_thu",
        required=False,
        reason="먼슬리 만기 주",
        resumes_on=date(2026, 8, 14),
    )
    coverage = sc.measure("option_chain/weekly_thu", [], window=_WINDOW, expectation=unlisted)
    assert sc.findings_for(coverage) == []
