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

    result = compare_day(archived, official)
    assert result.ratio == 1.0, "받은 것은 정확했다 — 그래서 비율만으로는 안 보인다"
    assert result.missing_minutes == 120, "받아야 할 것을 다 받았는지는 별개 축이다"
    # 그리고 그 120분이 **어디였는지**까지 말한다 (2026-08-10 B-1) — 이 경우는 꼬리다.
    assert result.tail_missing_minutes == 120
    assert result.head_missing_minutes == 0


def test_volume_compare_ignores_archive_only_minutes():
    """장전 구간은 아카이브에만 있다 — 미수집으로 세면 매일 오탐이다."""
    from scripts.verify_archive_volume import compare_day

    official = {"09:00": 10, "09:01": 10}
    archived = {"08:45": 5, "09:00": 10, "09:01": 10}

    assert compare_day(archived, official).missing_minutes == 0


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


# ------------------------- G-2(2026-08-10): 세 축이 같은 답을 하는가


def _cross(**kwargs):
    from messiah.ops.integrity_report import cross_check_head_truncation

    base = dict(
        start_lag_minutes=None, series_head_gap_minutes=None, volume_head_missing_minutes=None
    )
    base.update(kwargs)
    return cross_check_head_truncation(**base)


def test_all_three_axes_agreeing_is_silent():
    """2026-08-10을 A-1 적용 뒤로 재산출한 상태 — 셋 다 "잘렸다"고 말한다."""
    assert (
        _cross(start_lag_minutes=38.5, series_head_gap_minutes=41.0, volume_head_missing_minutes=13)
        == []
    )


def test_a_normal_day_is_silent_too():
    assert (
        _cross(start_lag_minutes=0.4, series_head_gap_minutes=2.0, volume_head_missing_minutes=0)
        == []
    )


def test_the_2026_08_10_bug_would_have_been_caught():
    """그날 실제로 나온 값들이다 — 커버리지 0분(안 잘렸다) vs 거래량 13분(잘렸다).

    A-1이 커버리지를 고쳤지만, **고쳤다는 것을 무엇이 보증하나**가 남는다. 축 하나가 다시
    조용해져도 나머지가 우는 한 그 불일치는 관측 가능해야 한다.
    """
    (finding,) = _cross(
        start_lag_minutes=38.5, series_head_gap_minutes=0.0, volume_head_missing_minutes=13
    )

    assert "축마다 다르다" in finding
    assert "계열 머리 구멍 0분" in finding, "조용한 축이 무엇인지 이름이 나와야 한다"
    assert "기동 지연 +38.5분" in finding


def test_a_reboot_day_splits_the_axes_and_that_is_the_diagnosis():
    """2026-08-06형 — 기동은 정시였는데 재부팅으로 계열 머리가 111분 비었다.

    그 갈림이 곧 **"늦게 뜬 게 아니라 뜬 뒤에 잃었다"**는 진단이다.
    """
    (finding,) = _cross(
        start_lag_minutes=0.4, series_head_gap_minutes=111.0, volume_head_missing_minutes=21
    )

    assert "잘렸다: 계열 머리 구멍 111분" in finding
    assert "아니다: 기동 지연 +0.4분" in finding


def test_an_unmeasurable_axis_does_not_vote():
    """못 잰 것을 "아니오"로 세면 그 축이 죽은 날 나머지가 우는 것을 불일치로 오인한다(L18)."""
    assert _cross(start_lag_minutes=38.5, series_head_gap_minutes=41.0) == []
    assert _cross(start_lag_minutes=38.5) == [], "잴 수 있는 축이 하나뿐이면 비교 자체가 없다"


# ------------------------- G-6(2026-08-10): 하루치 손실


def _loss(**kwargs):
    from messiah.ops.integrity_report import irrecoverable_loss_minutes

    return irrecoverable_loss_minutes(**kwargs)


def _cov(name: str, head: float) -> sc.SeriesCoverage:
    return sc.SeriesCoverage(name=name, rows=100, measured=True, head_gap_minutes=head)


def test_the_daily_loss_is_the_worst_axis_not_the_sum():
    """더하면 38분짜리 사고가 77분이 되고, 그러면 예산이라는 축을 아무도 못 믿는다.

    세 계열이 동시에 39·40·41분 비었다면 잃은 **시간**은 41분이지 120분이 아니다.
    """
    coverages = [
        _cov("flow_intraday/K2I", 39.0),
        _cov("option_chain/regular", 40.0),
        _cov("option_chain/weekly_mon", 41.0),
    ]

    assert _loss(start_lag_minutes=38.5, coverages=coverages) == 41.0


def test_a_late_launch_alone_still_counts():
    """계열이 아직 아무것도 안 쌓인 시점에도 기동 지연은 이미 확정된 손실이다."""
    assert _loss(start_lag_minutes=38.5, coverages=[]) == 38.5


def test_recoverable_series_do_not_inflate_the_loss():
    """봉은 백필로 되메울 수 있다 — 이 축이 세는 것은 **영원히 없는 것**뿐이다."""
    assert _loss(start_lag_minutes=None, coverages=[_cov("bars/A05608", 120.0)]) == 0.0


def test_a_clean_day_is_zero_not_none():
    """0분도 기록해야 5거래일 이동합이 성립한다 — None이면 그날이 창에서 빠진다."""
    assert _loss(start_lag_minutes=0.4, coverages=[_cov("ticks", 0.0)]) == 0.4


# ------------------------- F-0818P-5(2026-08-18): 카덴스는 손실이 아니다


def _cov_with_cadence(name: str, head: float, cadence: float) -> sc.SeriesCoverage:
    return sc.SeriesCoverage(
        name=name, rows=100, measured=True, head_gap_minutes=head, cadence_minutes=cadence
    )


def test_waiting_one_cycle_is_not_a_loss():
    """5분 카덴스 계열의 첫 행은 창 시작 5분 뒤가 정상이다 — 기다린 시간이지 잃은 시간이 아니다.

    2026-08-11·08-12·08-18이 전부 이 형태로 5.0분씩 예산을 깎았고, 그동안 장중 화면은
    **"오늘 소급 불가 손실 없음"** 이라고 말하고 있었다. 같은 이름의 두 표면이 다른 답을
    내면 그 이름은 관측 도구가 아니다.
    """
    assert (
        _loss(
            start_lag_minutes=None, coverages=[_cov_with_cadence("option_chain/regular", 5.0, 5.0)]
        )
        == 0.0
    )


def test_a_real_truncation_survives_the_cadence_subtraction():
    """차감이 사고까지 지우면 그건 과도한 차감이다 — 08-14 실측이 판정 기준이다.

    그날 `option_chain/weekly_thu`는 카덴스 10분에 머리 33분이었다(첫 행 08:53). 23분은
    기다림이 아니라 잃은 시간이고, 예산은 그 23분을 그대로 세야 한다.
    """
    coverages = [
        _cov_with_cadence("option_chain/regular", 25.0, 5.0),
        _cov_with_cadence("option_chain/weekly_mon", 31.0, 10.0),
        _cov_with_cadence("option_chain/weekly_thu", 33.0, 10.0),
    ]

    assert _loss(start_lag_minutes=None, coverages=coverages) == 23.0


def test_a_series_without_a_cadence_estimate_is_counted_whole():
    """카덴스를 못 잰 계열에서 임의의 값을 빼면 손실을 조용히 줄이게 된다(L18)."""
    assert _loss(start_lag_minutes=None, coverages=[_cov("flow_intraday/K2I", 39.0)]) == 39.0
