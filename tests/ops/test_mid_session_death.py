"""장중에 죽었다 돌아온 날 (2026-08-19 장후 F-1·F-2·F-3·F-4·G-4).

기준점은 그날의 실제 사건이다:

    08:20:29  l1_daily 정시 기동          08:25:34  g2_paper 기동
    09:30:01  g2_paper 마지막 활동  ┐
    09:50:29  l1_daily 마지막 활동  ├─ Windows Update 재시작 (사람이 12:14에 확정)
    12:29:23  l1_daily 재기동       ┘
    12:30:14  g2_paper 재기동
    15:35:26  l1_daily 정상 종료          15:34:58  g2_paper 정상 종료

그날 계기들이 말한 것:

    abnormal_exits              []          ← 두 프로세스가 각각 세션 하나를 잃었는데
    irrecoverable_loss_minutes  0.5         ← 사고 없는 08-18과 **같은 값**
    incomplete_day              (필드 없음)  ← 커버리지 61%인 날이 정상일로 롤링 창에
    등록부                       재발 4건     ← 정확히 잰 계기 넷이 「듣지 않았다」

그리고 `no-silent-process-death`는 그날 **「7거래일 연속 기준 충족」** 을 선고했다.
"""

from __future__ import annotations

import json
from datetime import date

from messiah.ops import incomplete_days
from messiah.ops import integrity_report as ir

_DAY = date(2026, 8, 19)


def _process(starts, ends, activity):
    return {
        "session_starts": list(starts),
        "session_ends": list(ends),
        "activity_kst": list(activity),
    }


# ---------------------------------------------------------------- F-1


def test_a_death_in_the_middle_of_the_day_is_counted():
    """**이 파일의 핵심.** 죽었다 돌아와 정상 종료하면 종전 판정은 아무것도 못 봤다 —
    사망 시각을 `activity[-1]`(그날 마지막 로그 = 15:35 정상 종료)로 잡았기 때문이다."""
    exits = ir._abnormal_exits(
        _DAY,
        {
            "l1_daily": _process(
                ["08:20:29", "12:29:23"],
                ["15:35:26"],
                ["08:21:00", "09:50:29", "12:29:30", "15:35:26"],
            )
        },
    )

    [item] = exits
    assert item["mid_session"] is True
    assert item["died_at_kst"] == "09:50:29"
    assert item["recovered_at_kst"] == "12:29:23"
    assert item["minutes_lost"] == 158.9


def test_both_processes_get_their_own_entry():
    """프로세스별로 센다 — 합치면 어느 쪽이 불안정한지가 사라진다."""
    exits = ir._abnormal_exits(
        _DAY,
        {
            "l1_daily": _process(["08:20:29", "12:29:23"], ["15:35:26"], ["09:50:29", "15:35:26"]),
            "g2_paper": _process(["08:25:34", "12:30:14"], ["15:34:58"], ["09:30:01", "15:34:58"]),
        },
    )

    assert {item["process"] for item in exits} == {"l1_daily", "g2_paper"}
    assert [item["minutes_lost"] for item in sorted(exits, key=lambda i: i["process"])] == [
        180.2,
        158.9,
    ]


def test_a_clean_day_stays_silent():
    """08-18처럼 한 번 뜨고 한 번 끝난 날은 0건 — 회귀 방지."""
    assert (
        ir._abnormal_exits(
            date(2026, 8, 18),
            {"l1_daily": _process(["08:20:31"], ["15:35:12"], ["08:21:00", "15:35:12"])},
        )
        == []
    )


def test_a_process_that_never_came_back_is_still_caught():
    """종전 판정(「하루 끝에 안 돌아온 프로세스」)이 그대로 살아 있어야 한다 —
    2026-08-07에 l1_daily가 13:41에 죽고 안 돌아온 그 자리다."""
    [item] = ir._abnormal_exits(
        date(2026, 8, 7),
        {"l1_daily": _process(["08:35:23"], ["07:10:00"], ["08:36:00", "13:41:12"])},
    )

    assert item["mid_session"] is False
    assert item["died_at_kst"] == "13:41:12"
    assert item["recovered_at_kst"] is None
    assert item["minutes_lost"] > 100


def test_the_marker_less_era_is_not_painted_red_retroactively():
    """`SessionEnd`를 한 번도 안 낸 프로세스는 판정 대상이 아니다 — 2026-08-07 이전
    로그를 소급해 빨갛게 칠하면 등록부 채점이 통째로 무의미해진다."""
    assert (
        ir._abnormal_exits(_DAY, {"l1_daily": _process(["08:20:29", "12:29:23"], [], ["09:50:29"])})
        == []
    )


def test_a_process_still_running_is_not_an_accident():
    """장중에 리포트를 돌리면 당연히 `SessionEnd`가 없다 — 마지막 로그가 마감 직전이면
    죽은 것이 아니다(임계는 `bar_tail_gap_minutes` 20분)."""
    assert (
        ir._abnormal_exits(_DAY, {"l1_daily": _process(["08:20:29"], ["15:35:26"], ["15:30:00"])})
        == []
    )


# ---------------------------------------------------------------- F-2


def test_the_loss_meter_stops_calling_a_159_minute_day_half_a_minute():
    """159분을 잃은 날과 사고 없는 날이 같은 0.5분이었다 — 318배 과소계상."""
    exits = ir._abnormal_exits(
        _DAY,
        {
            "l1_daily": _process(["08:20:29", "12:29:23"], ["15:35:26"], ["09:50:29", "15:35:26"]),
            "g2_paper": _process(["08:25:34", "12:30:14"], ["15:34:58"], ["09:30:01", "15:34:58"]),
        },
    )
    representative, by_process = ir.mid_session_gap_minutes(exits)

    # 겹치는 구간의 합집합이 곧 최댓값이다 — g2 09:30~12:30이 l1 09:50~12:29를 포함한다.
    assert representative == 180.2
    assert by_process == {"g2_paper": 180.2, "l1_daily": 158.9}
    # 아침 축(기동 지연 0.5분)과는 **더한다** — 시간대부터 겹치지 않는 별개 사건이다.
    assert (
        ir.irrecoverable_loss_minutes(
            start_lag_minutes=0.5, coverages=[], mid_session_minutes=representative
        )
        == 180.7
    )


def test_a_day_without_mid_session_death_keeps_the_old_number():
    """08-18은 0.5 그대로 — 과거 판정을 소급해 뒤집지 않는다(R18)."""
    assert ir.mid_session_gap_minutes([]) == (0.0, {})
    assert (
        ir.irrecoverable_loss_minutes(start_lag_minutes=0.5, coverages=[], mid_session_minutes=0.0)
        == 0.5
    )


# ---------------------------------------------------------------- F-3


class _Coverage:
    """`series_coverage.SeriesCoverage`의 판정에 필요한 최소 형태."""

    def __init__(self, name: str, pct: float) -> None:
        self.name = name
        self.coverage_pct = pct
        self.measured = True
        self.expected = True


def test_a_half_day_says_so():
    """불완전일을 표시할 필드가 **아예 없었다** — `provisional`은 다른 축이다."""
    incomplete, reasons, worst = incomplete_days.judge(
        coverages=[_Coverage("ticks", 61.2), _Coverage("flow_intraday/K2I", 63.2)],
        abnormal_exits=[],
    )

    assert incomplete is True
    assert worst == 61.2
    assert "61.2" in reasons[0]


def test_a_short_death_makes_the_day_incomplete_even_at_full_coverage():
    """커버리지는 **적재**를 보고 이 축은 **관측**을 본다 — 죽어 있던 구간이 짧아
    커버리지가 멀쩡해도 그 사이 판단·주문 경로는 통째로 없다."""
    incomplete, reasons, _worst = incomplete_days.judge(
        coverages=[_Coverage("ticks", 99.4)],
        abnormal_exits=[
            {
                "process": "l1_daily",
                "mid_session": True,
                "minutes_lost": 12.0,
                "died_at_kst": "10:00:00",
                "recovered_at_kst": "10:12:00",
            }
        ],
    )

    assert incomplete is True
    assert "장중 사망" in reasons[0]


def test_a_normal_day_is_not_incomplete():
    incomplete, reasons, worst = incomplete_days.judge(
        coverages=[_Coverage("ticks", 99.1)], abnormal_exits=[]
    )
    assert (incomplete, reasons, worst) == (False, [], 99.1)


def test_an_unmeasured_coverage_day_is_not_silently_full():
    """한 계열도 못 잰 날의 최솟값은 None이지 0.0이 아니다(L18)."""
    _incomplete, _reasons, worst = incomplete_days.judge(coverages=[], abnormal_exits=[])
    assert worst is None


def test_only_confirmed_incomplete_days_leave_the_window(tmp_path):
    """판정 불가(축이 없던 옛 리포트)는 **안 뺀다** — 소급해 전부 버리면 30m처럼 창이
    좁은 축이 영영 판정 불가가 된다."""
    usable, excluded = incomplete_days.usable_days(
        [date(2026, 8, 14), date(2026, 8, 18), date(2026, 8, 19)],
        log_dir=tmp_path,  # 파일이 없으므로 전부 판정 불가
        known={date(2026, 8, 19): True},
    )

    assert usable == [date(2026, 8, 14), date(2026, 8, 18)]
    assert excluded == [date(2026, 8, 19)]


# ---------------------------------------------------------------- J-3b (2026-08-20)


def _report(tmp_path, stamp: str, *, coverage: list[float] | None = None, flag=None) -> None:
    """저장된 무결성 리포트 한 벌 — `incomplete_day` 불리언 유무를 골라 쓴다."""
    payload: dict = {"date": stamp}
    if coverage is not None:
        payload["series_coverage"] = [
            {"name": f"s{i}", "coverage_pct": pct, "measured": True, "expected": True}
            for i, pct in enumerate(coverage)
        ]
    if flag is not None:
        payload["incomplete_day"] = flag
    (tmp_path / f"daily_integrity_{stamp.replace('-', '')}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_a_day_from_before_the_axis_is_still_judged(tmp_path):
    """**이 절의 핵심.** 축을 만든 이유인 2026-08-19 리포트는 그 축이 생기기 전에 쓰여
    `incomplete_day` 필드가 없다. 그래서 실전에서 08-19가 롤링 창에 그대로 남았다 —
    오염을 막으려 만든 축이 정작 그 오염을 못 막았다.

    판정의 **입력**(`series_coverage`)은 그날 리포트에 처음부터 다 있었다(최솟값 61.2%).
    """
    _report(tmp_path, "2026-08-19", coverage=[61.2, 63.2, 99.0])  # 불리언 없음

    assert incomplete_days.load(log_dir=tmp_path) == {date(2026, 8, 19): True}


def test_a_clean_day_from_before_the_axis_stays_in(tmp_path):
    """계산해 보니 온전한 날은 창에 남는다 — 옛 날짜를 싸잡아 버리지 않는다."""
    _report(tmp_path, "2026-08-18", coverage=[99.1, 99.4])

    assert incomplete_days.load(log_dir=tmp_path) == {date(2026, 8, 18): False}


def test_the_stored_boolean_wins_over_derivation(tmp_path):
    """그날 축이 실제로 판정했으면 그것이 정본이다 — 계산이 판정을 덮지 않는다(R18)."""
    _report(tmp_path, "2026-08-20", coverage=[61.0], flag=False)

    assert incomplete_days.load(log_dir=tmp_path) == {date(2026, 8, 20): False}


def test_a_report_without_the_inputs_is_still_unjudgeable(tmp_path):
    """계산할 입력이 없는 것과 계산해 보니 온전한 것은 다르다(L18).
    `series_coverage`가 없던 시절(2026-08-06 이전) 리포트는 여전히 판정 불가다."""
    _report(tmp_path, "2026-07-30")  # 커버리지도 불리언도 없음

    assert incomplete_days.load(log_dir=tmp_path) == {date(2026, 7, 30): None}


def test_the_motivating_day_finally_leaves_the_window(tmp_path):
    """2026-08-20 실전 회귀 — `excluded_days=[]`로 08-19가 3거래일 창에 남아 있었다."""
    _report(tmp_path, "2026-08-18", coverage=[99.1])
    _report(tmp_path, "2026-08-19", coverage=[61.2])  # 불리언 없음(옛 리포트)
    _report(tmp_path, "2026-08-20", coverage=[99.3], flag=False)

    usable, excluded = incomplete_days.usable_days(
        [date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20)], log_dir=tmp_path
    )

    assert usable == [date(2026, 8, 18), date(2026, 8, 20)]
    assert excluded == [date(2026, 8, 19)]
