"""적재 계열 시간 커버리지 (2026-08-06 고도화 2).

이 파일의 기준점은 **2026-08-06 실측**이다. 그날 옵션체인 약 1,500다리와 수급 264행이
영구 소실됐는데 무결성 리포트가 완벽하게 조용했다 — 그 계열들을 아예 안 봤기 때문이다.
아래 테스트들은 "그날 데이터를 넣으면 그 사고가 잡히는가"와 "정상일에 헛경고가 없는가"를
같은 무게로 본다. 후자가 깨지면 이 축은 늑대소년이 되어 결국 아무도 안 본다.
"""

from datetime import date, datetime, timedelta

import polars as pl

from messiah.core.timeutil import KST
from messiah.ops import series_coverage as sc

_DAY = date(2026, 8, 6)


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 8, 6, hour, minute, tzinfo=KST)


def _window(start_hour: int = 8, start_minute: int = 35):
    return sc.session_window(_DAY, start=_at(start_hour, start_minute))


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
