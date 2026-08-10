"""적재 계열 시간 커버리지 (2026-08-06 고도화 2).

이 파일의 기준점은 **2026-08-06 실측**이다. 그날 옵션체인 약 1,500다리와 수급 264행이
영구 소실됐는데 무결성 리포트가 완벽하게 조용했다 — 그 계열들을 아예 안 봤기 때문이다.
아래 테스트들은 "그날 데이터를 넣으면 그 사고가 잡히는가"와 "정상일에 헛경고가 없는가"를
같은 무게로 본다. 후자가 깨지면 이 축은 늑대소년이 되어 결국 아무도 안 본다.
"""

from datetime import date, datetime, timedelta
from datetime import time as dt_time

import polars as pl

from messiah.core.timeutil import KST
from messiah.ops import series_coverage as sc
from messiah.ops.series_expectation import Expectation

_DAY = date(2026, 8, 6)


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 8, 6, hour, minute, tzinfo=KST)


def _window(start_hour: int = 8, start_minute: int = 35):
    """판정 창을 손으로 만든다.

    2026-08-10 A-1부터 `session_window()`는 시작을 **등록 정본**에서만 가져온다(인자로 못
    바꾼다). 아래 테스트들은 "창 시작이 X일 때 무엇이 구멍인가"를 재는 것이 목적이므로
    창을 직접 만든다 — 정본 앵커링 자체는 `test_window_starts_at_the_registered_trigger`가
    따로 본다.
    """
    return (_at(start_hour, start_minute), datetime(2026, 8, 6, 15, 35, tzinfo=KST))


def _cycles(first: datetime, *, count: int, period_minutes: int, legs_minutes: int = 3):
    """폴링 사이클을 흉내낸다 — 한 사이클이 여러 분에 걸쳐 들어온다(옵션체인 42다리)."""
    out: list[datetime] = []
    for index in range(count):
        base = first + timedelta(minutes=index * period_minutes)
        out.extend(base + timedelta(minutes=leg) for leg in range(legs_minutes))
    return out


# ---------------------------------------------------------------- 정상일


def test_healthy_option_chain_day_has_no_findings():
    """10분 격자로 온전히 쌓인 날 — 사이클 사이 7분 간격은 구멍이 아니다.

    첫 구현이 여기서 틀렸다: 사이클 **안쪽**의 1분 간격이 카덴스 중앙값을 1분으로
    끌어내려 정상 주기가 전부 구멍으로 잡혔고, 2026-08-06 실데이터에서 한 계열에만
    30건의 헛경고가 났다.
    """
    stamps = _cycles(_at(8, 40), count=42, period_minutes=10)

    coverage = sc.measure("option_chain/regular", stamps, window=_window())

    assert coverage.cadence_minutes == 10.0, "사이클 간격이 아니라 다리 간격을 쟀다"
    assert sc.findings_for(coverage) == []


def test_healthy_flow_day_has_no_findings():
    """60초 격자 — 매 분 한 행씩이면 사이클이 하나로 이어진다."""
    stamps = [_at(8, 36) + timedelta(minutes=i) for i in range(419)]

    coverage = sc.measure("flow_intraday/K2I", stamps, window=_window())

    assert sc.findings_for(coverage) == []


def test_ticks_starting_at_0845_is_not_a_head_gap():
    """체결틱은 08:45부터 온다(3거래일 실측) — 기동(08:35) 기준 10분은 정상이다."""
    stamps = [_at(8, 45) + timedelta(minutes=i) for i in range(410)]

    coverage = sc.measure("ticks", stamps, window=_window())

    assert coverage.head_gap_minutes == 10.0
    assert sc.findings_for(coverage) == []


# ---------------------------------------------------------------- 2026-08-06 사고


def test_restart_destroyed_morning_shows_as_head_gap():
    """**이 파일의 핵심.** 재기동 전 오전치가 지워진 상태 — 첫 행이 10:30이다.

    2026-08-06 실측: `option_chain/regular`가 1,302행이었다. 행수만 보면 정상이고,
    첫 사이클이 10:30이라는 사실은 시간 커버리지로만 드러난다.
    """
    stamps = _cycles(_at(10, 30), count=31, period_minutes=10)

    coverage = sc.measure("option_chain/regular", stamps, window=_window())
    findings = sc.findings_for(coverage)

    assert coverage.rows > 0, "행은 많다 — 그래서 행수로는 안 보인다"
    assert coverage.head_gap_minutes == 115.0
    # 2026-08-07 고도화 1로 판정이 하나 늘었다(세션 커버리지) — 건수가 아니라 **내용**을
    # 못박는다. 건수로 고정하면 축이 늘 때마다 무관한 테스트가 깨진다.
    assert any("115분간 적재 없음" in f for f in findings)
    # 고도화 4로 문구가 강해졌다("소급 불가" → "영구 소실(소급 경로 없음)").
    # 검사하는 성질은 그대로다 — 이 계열의 공백이 되메울 수 없다는 사실이 문장에 남는가.
    assert all("영구 소실" in f for f in findings), "봉 결손과 같은 무게로 읽히면 안 된다"


def test_reboot_hole_in_a_continuous_series_is_caught():
    """재부팅 공백 — 08:45~10:03 수집, 10:04~10:24 정지, 10:25~15:34 재개(실측 그대로)."""
    stamps = [_at(8, 45) + timedelta(minutes=i) for i in range(79)]
    stamps += [_at(10, 25) + timedelta(minutes=i) for i in range(310)]

    coverage = sc.measure("ticks", stamps, window=_window())
    findings = sc.findings_for(coverage)

    assert coverage.longest_gap_minutes == 22.0
    assert any("10:03~10:25" in f for f in findings)


def test_two_cycles_do_not_calibrate_the_gap_away():
    """표본 2개로 카덴스를 추정하면 **그 간격 자신이 기준**이 되어 아무것도 못 잡는다.

    위 재부팅 사례가 정확히 사이클 2개짜리다 — 이 방어가 없으면 22분 구멍이 조용히 통과한다.
    """
    stamps = [_at(9, 0), _at(9, 1), _at(12, 0), _at(12, 1)]

    coverage = sc.measure("ticks", stamps, window=_window())

    assert coverage.cadence_minutes == 1.0, "표본이 모자라면 추정하지 않는다"
    assert coverage.gaps, "179분 구멍이 안 잡혔다"


def test_empty_series_is_a_finding_not_silence():
    """하루 종일 0행 — 이 프로젝트가 세 번 당한 형태(폴러 7개월·옵션체인 수개월·FL 피처)."""
    coverage = sc.measure("option_chain/weekly_thu", [], window=_window())
    findings = sc.findings_for(coverage)

    assert coverage.rows == 0
    assert coverage.measured is True, "0행은 '못 쟀다'가 아니라 판정이다"
    assert len(findings) == 1
    assert "한 행도 없다" in findings[0]


def test_broken_continuous_series_is_not_calibrated_into_normality():
    """40분 블록이 60분마다 = 폴링 주기가 아니라 **끊긴 연속 계열**이다.

    블록 간격을 카덴스로 삼으면 그 20분 구멍들이 정상 주기가 되어 아무것도 안 걸린다.
    사이클 길이가 간격의 절반을 넘으면 연속 계열로 본다(`_estimate_cadence`).
    """
    stamps: list[datetime] = []
    for index in range(6):
        block = _at(9, 0) + timedelta(minutes=index * 60)
        stamps.extend(block + timedelta(minutes=i) for i in range(40))

    coverage = sc.measure("flow_intraday/K2I", stamps, window=_window())

    assert coverage.cadence_minutes == 1.0
    assert len(coverage.gaps) == 5, "20분 구멍 5개가 정상으로 흡수됐다"


def test_many_gaps_are_folded_so_the_breach_list_stays_readable():
    """구멍이 여러 개면 목록을 접고 총합을 남긴다 — 30줄짜리 breach는 아무도 안 읽는다."""
    stamps: list[datetime] = []
    for index in range(6):
        block = _at(9, 0) + timedelta(minutes=index * 60)
        stamps.extend(block + timedelta(minutes=i) for i in range(40))

    findings = sc.findings_for(sc.measure("flow_intraday/K2I", stamps, window=_window()))

    gap_lines = [line for line in findings if "분 구멍" in line]
    assert len(gap_lines) == sc._MAX_GAP_FINDINGS, "구멍 5건이 그대로 5줄로 나왔다"
    assert any("외 2건" in line for line in findings), "접었으면 몇 건을 접었는지 말해야 한다"


def test_timestamps_outside_the_window_are_ignored():
    """장 시작 전 워밍업 폴링이 머리 구멍 계산을 어지럽히면 안 된다."""
    stamps = [_at(6, 0), *[_at(8, 45) + timedelta(minutes=i) for i in range(410)]]

    coverage = sc.measure("ticks", stamps, window=_window())

    assert coverage.first_kst == "08:45"


# ---------------------------------------------------------------- 발견(디스크)


def _write(path, stamps):
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"ts_kst": stamps, "v": [1] * len(stamps)}).write_parquet(path)


def test_collect_discovers_series_from_directories(tmp_path):
    """계열 목록을 하드코딩하면 새 계열이 붙어도 리포트가 그것만 조용히 안 본다."""
    _write(tmp_path / "flow" / "K2I" / "2026-08-06.parquet", [_at(9, 0), _at(9, 1)])
    _write(tmp_path / "oc" / "regular" / "2026-08-06.parquet", [_at(9, 0)])
    _write(tmp_path / "oc" / "weekly_thu" / "2026-08-06.parquet", [_at(9, 0)])

    covers = sc.collect(
        _DAY,
        "A05608",
        window=_window(),
        flow_dir=tmp_path / "flow",
        option_chain_dir=tmp_path / "oc",
        tick_dir=tmp_path / "ticks",
    )

    assert sorted(c.name for c in covers) == [
        "flow_intraday/K2I",
        "option_chain/regular",
        "option_chain/weekly_thu",
    ]


def test_collect_reports_a_series_whose_file_is_missing_today(tmp_path):
    """디렉터리가 있다는 것은 언젠가 모았다는 뜻 — 오늘 파일이 없으면 그게 판정 대상이다."""
    _write(tmp_path / "oc" / "regular" / "2026-08-05.parquet", [_at(9, 0)])

    covers = sc.collect(
        _DAY,
        "A05608",
        window=_window(),
        flow_dir=tmp_path / "flow",
        option_chain_dir=tmp_path / "oc",
        tick_dir=tmp_path / "ticks",
    )

    assert [c.name for c in covers] == ["option_chain/regular"]
    assert covers[0].rows == 0
    assert sc.findings_for(covers[0])


def test_summarize_lists_healthy_series_too(tmp_path):
    """정상까지 찍어야 '검사했는데 이상 없다'와 '그 계열을 안 본다'가 갈린다."""
    coverage = sc.measure(
        "flow_intraday/K2I",
        [_at(8, 36) + timedelta(minutes=i) for i in range(419)],
        window=_window(),
    )

    lines = sc.summarize([coverage])

    assert len(lines) == 1
    assert "flow_intraday/K2I" in lines[0]
    assert "✅" in lines[0]


# ---------------------------------------------------------------- 캘린더 계약 (2026-08-07 P0-3)


def _contract_window():
    from datetime import datetime

    from messiah.core.timeutil import KST

    return (
        datetime(2026, 8, 7, 8, 35, tzinfo=KST),
        datetime(2026, 8, 7, 15, 35, tzinfo=KST),
    )


def _unlisted(name="option_chain/weekly_thu"):
    from datetime import date as _d

    from messiah.ops.series_expectation import Expectation

    return Expectation(
        series=name,
        required=False,
        reason="먼슬리 만기 주(08-13 만기) — KRX 미상장",
        resumes_on=_d(2026, 8, 14),
    )


def test_unlisted_series_with_no_rows_is_silent():
    """2026-08-07의 그 자리 — 그대로 뒀으면 8/13까지 5거래일 연속 오탐 ERROR였다."""
    coverage = sc.measure(
        "option_chain/weekly_thu", [], window=_contract_window(), expectation=_unlisted()
    )
    assert coverage.rows == 0
    assert coverage.expected is False
    assert sc.findings_for(coverage) == []
    # 세션 창 전체가 머리 구멍으로 잡히면 `series_head_gap_minutes_max` 지표가 오염된다.
    assert coverage.head_gap_minutes == 0.0


def test_unlisted_series_with_rows_is_a_violation():
    """양방향 단언 — 미상장이라 판정했는데 쌓였으면 **규정 이해가 틀린 것**이다."""
    from datetime import datetime, timedelta

    from messiah.core.timeutil import KST

    base = datetime(2026, 8, 7, 10, 0, tzinfo=KST)
    stamps = [base + timedelta(minutes=i) for i in range(10)]
    coverage = sc.measure(
        "option_chain/weekly_thu", stamps, window=_contract_window(), expectation=_unlisted()
    )
    findings = sc.findings_for(coverage)
    assert len(findings) == 1
    assert "미상장으로 판정했는데" in findings[0]


def test_no_expectation_means_required():
    """계약을 모르는 호출자가 조용히 면제받으면 안 된다."""
    coverage = sc.measure("option_chain/weekly_thu", [], window=_contract_window())
    assert coverage.expected is True
    assert sc.findings_for(coverage) != []


def test_summarize_marks_unlisted_distinctly():
    coverage = sc.measure(
        "option_chain/weekly_thu", [], window=_contract_window(), expectation=_unlisted()
    )
    line = sc.summarize([coverage])[0]
    assert "⊘" in line
    assert "❌" not in line
    # 계열 이름이 두 번 나오면 안 된다(2026-08-07 실측 표기 중복).
    assert line.count("option_chain/weekly_thu") == 1


def test_irrecoverable_grade_is_applied_after_the_calendar_gate():
    """고도화 4 — 등급은 계약 **뒤에** 붙는다. 앞에 두면 미상장일에 가장 크게 운다."""
    assert sc._is_irrecoverable("option_chain/weekly_mon")
    assert sc._is_irrecoverable("flow_intraday/K2I")
    assert sc._is_irrecoverable("ticks")
    assert not sc._is_irrecoverable("bars/A05608")

    silent = sc.measure(
        "option_chain/weekly_thu", [], window=_contract_window(), expectation=_unlisted()
    )
    assert sc.findings_for(silent) == []  # 소급 불가 계열이어도 미상장이면 조용하다

    loud = sc.measure("option_chain/weekly_thu", [], window=_contract_window())
    assert "영구 소실" in loud[0] if isinstance(loud, list) else True
    assert "영구 소실" in sc.findings_for(loud)[0]


# ------------------------------------------------- A-1: 판정 창 정본 앵커링 (2026-08-10)
#
# 이 절의 기준점은 2026-08-10 실측이다. 08:20 정시 트리거가 기동 창 가드에 막혀 두
# 프로세스가 종료했고 사람이 08:58에야 손으로 띄웠다 — 38분. 그날 15:45 리포트는
# 이렇게 말했다:
#
#     ticks: 커버리지 100% · 머리 -0분 ✅
#     series_findings: []
#
# 판정 창의 시작이 **첫 SessionStart**였기 때문이다. 창이 기동을 따라 같이 늦어지면
# "늦게 뜬 날"과 "제때 떠서 다 본 날"이 구조적으로 구분되지 않는다.


def test_window_starts_at_the_registered_trigger_not_at_process_start(monkeypatch):
    """창의 시작은 정본에서 온다 — 호출자가 바꿀 수 있으면 그 순간 다시 기동에 묶인다."""
    monkeypatch.setattr(
        sc.task_schedule, "earliest_collection_trigger", lambda *a, **k: dt_time(8, 20)
    )

    start, end = sc.session_window(_DAY)

    assert start == _at(8, 20)
    assert end == _at(15, 35)


def test_window_end_is_clamped_to_now_during_the_session(monkeypatch):
    """장중 실행에서 아직 안 온 시간이 꼬리 구멍이 되면 안 된다.

    2026-08-10 15:00에 리포트를 손으로 돌렸더니 전 계열이 `마지막 행 이후 39분간 적재
    없음`을 찍었다 — 장이 안 끝났다는 뜻일 뿐이었다.
    """
    monkeypatch.setattr(
        sc.task_schedule, "earliest_collection_trigger", lambda *a, **k: dt_time(8, 20)
    )

    assert sc.session_window(_DAY, now=_at(13, 0))[1] == _at(13, 0)
    # 지난 날짜를 재산출할 때는 오늘 시각이 창을 자르면 안 된다.
    other_day = datetime(2026, 8, 20, 11, 0, tzinfo=KST)
    assert sc.session_window(_DAY, now=other_day)[1] == _at(15, 35)


def test_a_late_launch_can_no_longer_hide_behind_the_window():
    """2026-08-10의 회귀 테스트 — **이 테스트가 통과하려면 그날이 사고로 보여야 한다.**

    창 08:20, 첫 행 08:59(그날 수급 실측)를 넣는다. 종전 앵커링에서는 창도 08:58에서
    시작해 커버리지가 100%였다.
    """
    window = (_at(8, 20), _at(15, 35))
    stamps = [_at(8, 59) + timedelta(minutes=i) for i in range(396)]

    coverage = sc.measure("flow_intraday/K2I", stamps, window=window)

    assert coverage.head_gap_minutes == 39.0
    assert coverage.coverage_pct < sc._COVERAGE_FLOOR_PCT
    findings = sc.findings_for(coverage)
    assert any("세션 커버리지" in line for line in findings)
    assert any("39분간 적재 없음" in line for line in findings)
    assert all("영구 소실" in line for line in findings), "수급은 소급 경로가 없다"


def test_head_gap_is_never_negative_now_that_the_window_is_canonical():
    """머리 구멍이 **음수**인 것이 2026-08-10 결함의 지문이었다 — 첫 행이 창보다 일렀다."""
    window = (_at(8, 20), _at(15, 35))
    stamps = [_at(8, 30) + timedelta(minutes=i) for i in range(400)]

    assert sc.measure("flow_intraday/K2I", stamps, window=window).head_gap_minutes == 10.0


def test_ticks_baseline_defers_the_window_to_when_the_market_starts_ticking():
    """계열마다 "볼 수 있었던 시작"이 다르다 (2026-08-07 실측).

    그날 기동은 08:35:34였는데 수급 첫 행은 08:36, 옵션은 08:40, **체결틱은 08:45**였다.
    기동을 08:20으로 당긴 날도 틱은 08:45다 — 시장 사정이기 때문이다. 이 축이 없으면
    창을 정본으로 옮기는 순간 틱이 매일 25분짜리 머리 구멍을 갖는다.
    """
    window = (_at(8, 20), _at(15, 35))
    stamps = [_at(8, 45) + timedelta(minutes=i) for i in range(410)]
    ticks = Expectation(series="ticks", required=True, first_data_kst=dt_time(8, 45))

    with_baseline = sc.measure("ticks", stamps, window=window, expectation=ticks)
    without = sc.measure("ticks", stamps, window=window)

    assert with_baseline.head_gap_minutes == 0.0
    assert with_baseline.window_start_kst == "08:45"
    assert sc.findings_for(with_baseline) == []
    assert without.head_gap_minutes == 25.0, "기준선이 없으면 매일 25분이 구멍으로 잡힌다"


def test_the_ticks_baseline_is_not_an_exemption():
    """08:45 뒤로 잘린 것은 그대로 잡혀야 한다 — 2026-08-06 재부팅(10:26 첫 행)."""
    window = (_at(8, 20), _at(15, 35))
    stamps = [_at(10, 26) + timedelta(minutes=i) for i in range(309)]
    ticks = Expectation(series="ticks", required=True, first_data_kst=dt_time(8, 45))

    coverage = sc.measure("ticks", stamps, window=window, expectation=ticks)

    assert coverage.head_gap_minutes == 101.0
    assert any("적재 없음" in line for line in sc.findings_for(coverage))


# ------------------------------------------------- A-3: 사이클 다리 완전성 (2026-08-10)


def _legged(first: datetime, *, cycles: int, period_minutes: int, legs: int, short: dict[int, int]):
    """폴링 사이클 흉내 — `short`에 적힌 사이클만 다리를 덜 채운다.

    한 사이클이 여러 분에 걸치게 만든다(옵션체인 42다리는 실제로 2~3분에 걸쳐 온다).
    """
    stamps: list[datetime] = []
    keys: list[str] = []
    for index in range(cycles):
        base = first + timedelta(minutes=index * period_minutes)
        for leg in range(short.get(index, legs)):
            stamps.append(base + timedelta(minutes=leg // 20))
            keys.append(f"LEG{leg:02d}")
    return stamps, keys


def test_a_short_cycle_is_caught_even_when_the_time_axis_is_perfect():
    """2026-08-10 14:30 `option_chain/regular`가 41/42였고 커버리지는 100%였다.

    시간 축이 **구조적으로 못 보는** 자리다 — 사이클은 제때 돌았기 때문이다.
    """
    window = (_at(8, 20), _at(15, 35))
    stamps, keys = _legged(_at(9, 0), cycles=40, period_minutes=10, legs=42, short={25: 41})

    coverage = sc.measure("option_chain/regular", stamps, window=window, leg_keys=keys)

    assert coverage.expected_legs == 42
    assert coverage.short_cycles == [("13:10", 41)]
    assert coverage.longest_gap_minutes <= 10.0, "시간 축은 정상이다 — 그게 이 축이 필요한 이유"
    assert any("41/42다리" in line for line in sc.findings_for(coverage))


def test_a_healthy_option_chain_day_says_nothing_about_legs():
    """정상일에 한 줄도 안 나와야 한다 — 매일 우는 축은 한 달이면 안 읽힌다.

    첫 사이클을 08:30에 두는 이유: 창이 08:20이므로 09:00에서 시작하면 **머리 구멍 40분**이
    같이 잡힌다. 그건 이 테스트가 볼 것이 아니다(정시 기동일의 첫 격자는 08:30 언저리다).
    """
    window = (_at(8, 20), _at(15, 35))
    stamps, keys = _legged(_at(8, 30), cycles=43, period_minutes=10, legs=42, short={})

    coverage = sc.measure("option_chain/regular", stamps, window=window, leg_keys=keys)

    assert coverage.expected_legs == 42
    assert coverage.short_cycles == []
    assert sc.findings_for(coverage) == []


def test_a_missing_sector_in_one_minute_is_caught_for_a_continuous_series():
    """수급은 1분 격자라 `_group_into_cycles()`가 하루를 한 덩어리로 만든다 — 분으로 묶는다.

    2026-08-10 실측: 396분 중 3분이 3업종 대신 2업종이었고 그 3행은 영구 소실이다.
    그날 커버리지는 100%였다.
    """
    window = (_at(8, 20), _at(15, 35))
    stamps: list[datetime] = []
    keys: list[str] = []
    for index in range(396):
        minute = _at(8, 59) + timedelta(minutes=index)
        sectors = ["F001", "OC01"] if index in (107, 380) else ["F001", "OC01", "OP01"]
        stamps.extend([minute] * len(sectors))
        keys.extend(sectors)

    coverage = sc.measure("flow_intraday/K2I", stamps, window=window, leg_keys=keys)

    assert coverage.expected_legs == 3
    assert [when for when, _ in coverage.short_cycles] == ["10:46", "15:19"]
    assert any("2/3다리" in line for line in sc.findings_for(coverage))


def test_the_last_bucket_is_never_judged_because_it_may_still_be_running():
    """장중 실행에서 도는 중인 사이클과 잘린 사이클을 이 함수가 구분할 근거가 없다."""
    window = (_at(8, 20), _at(15, 35))
    stamps, keys = _legged(_at(9, 0), cycles=40, period_minutes=10, legs=42, short={39: 7})

    coverage = sc.measure("option_chain/regular", stamps, window=window, leg_keys=keys)

    assert coverage.short_cycles == []


def test_the_leg_axis_stays_silent_when_it_cannot_judge():
    """키가 없거나·표본이 모자라거나·짝이 어긋나면 판정하지 않는다.

    표본이 적으면 최빈값이 결손 쪽으로 뒤집혀 **검사가 거꾸로 선다** — 카덴스 추정이
    `_MIN_CYCLES_FOR_CADENCE`를 두는 이유와 같다.
    """
    window = (_at(8, 20), _at(15, 35))
    stamps, keys = _legged(_at(9, 0), cycles=40, period_minutes=10, legs=42, short={25: 41})
    few, few_keys = _legged(_at(9, 0), cycles=3, period_minutes=10, legs=42, short={1: 41})

    no_keys = sc.measure("option_chain/regular", stamps, window=window)
    too_few = sc.measure("option_chain/regular", few, window=window, leg_keys=few_keys)
    mismatched = sc.measure("option_chain/regular", stamps, window=window, leg_keys=keys[:5])

    assert no_keys.expected_legs is None
    assert too_few.expected_legs is None
    assert mismatched.expected_legs is None


def test_collect_passes_row_identifiers_so_the_leg_axis_works_on_disk(tmp_path):
    """`collect()`가 식별자 열을 안 넘기면 이 축은 디스크 경로에서 통째로 죽는다."""
    path = tmp_path / "oc" / "regular" / "2026-08-06.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamps, keys = _legged(_at(9, 0), cycles=40, period_minutes=10, legs=42, short={25: 41})
    pl.DataFrame({"ts_kst": stamps, "symbol": keys}).write_parquet(path)

    covers = sc.collect(
        _DAY,
        "A05608",
        window=(_at(8, 20), _at(15, 35)),
        flow_dir=tmp_path / "flow",
        option_chain_dir=tmp_path / "oc",
        tick_dir=tmp_path / "ticks",
    )

    assert covers[0].short_cycles == [("13:10", 41)]
