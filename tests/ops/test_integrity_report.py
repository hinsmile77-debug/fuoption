"""일일 무결성 리포트 검증 — 고도화 2 (2026-07-30).

이 리포트의 존재 이유는 "2026-07-30에 사람이 손으로 찾아낸 것을 코드가 대신 찾게 한다"이다
(`ops/integrity_report.py` 모듈 docstring). 그래서 테스트도 **그날의 실제 사고 형태**를
재현해 잡히는지 확인하는 쪽으로 짰다 — 29분 공백, 6회 재기동, 하루 종일 높은 nan_ratio.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from messiah.core.messages import BarClosed, BarSession, Horizon
from messiah.core.timeutil import KST
from messiah.data.archiver import ParquetArchiver
from messiah.ops.integrity_report import (
    NativeCrashes,
    analyze_bar_continuity,
    analyze_data_flow_ownership,
    analyze_logs,
    build_report,
    format_summary,
)

_DAY = date(2026, 7, 29)


def _write_bars(bar_dir: Path, minutes: list[int], *, symbol: str = "A05608") -> None:
    archiver = ParquetArchiver(bar_dir)
    for minute in minutes:
        archiver.append_bar(
            BarClosed(
                symbol=symbol,
                horizon=Horizon.M1,
                bar_open_kst=datetime(2026, 7, 29, 9, 0, tzinfo=KST) + timedelta(minutes=minute),
                o_ticks=100,
                h_ticks=105,
                l_ticks=95,
                c_ticks=102,
                volume=10,
                quality_ok=True,
            )
        )


def _write_log(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8"
    )


def _no_crashes(_day: date, **_kwargs) -> NativeCrashes:
    """`build_report`가 집계 창을 좁히려고 `since=`를 넘긴다(2026-07-31) — 가짜 수집기는
    그 인자를 무시하지만 시그니처는 받아줘야 한다."""
    return NativeCrashes(available=True, count=0)


def _crashes(count: int, *details: str):
    def _collector(_day: date, **_kwargs) -> NativeCrashes:
        return NativeCrashes(available=True, count=count, details=list(details))

    return _collector


# ---------------------------------------------------------------- 봉 연속성


def test_continuous_bars_report_no_gaps(tmp_path: Path):
    _write_bars(tmp_path, list(range(10)))

    [m1] = analyze_bar_continuity(tmp_path, "A05608", _DAY)

    assert m1.rows == 10
    assert m1.missing_minutes == 0
    assert m1.longest_gap_minutes == 0
    assert m1.gaps == []


def test_the_real_29_minute_outage_is_detected(tmp_path: Path):
    """2026-07-29 12:32~13:02 형태 — 소켓은 살아있는데 틱만 29분간 끊긴 사고."""
    _write_bars(tmp_path, list(range(5)) + list(range(34, 40)))

    [m1] = analyze_bar_continuity(tmp_path, "A05608", _DAY)

    assert m1.missing_minutes == 29
    assert m1.longest_gap_minutes == 29
    assert m1.gaps == [("09:04", "09:34", 29)]


def test_multiple_small_gaps_are_summed_and_the_longest_is_kept(tmp_path: Path):
    _write_bars(tmp_path, [0, 1, 3, 4, 10])

    [m1] = analyze_bar_continuity(tmp_path, "A05608", _DAY)

    assert m1.missing_minutes == 6  # 1분 + 5분
    assert m1.longest_gap_minutes == 5


def test_missing_day_file_is_reported_as_empty_not_crash(tmp_path: Path):
    [m1] = analyze_bar_continuity(tmp_path, "A05608", _DAY)

    assert m1.rows == 0
    assert m1.first_bar_kst is None


def test_torn_bar_file_does_not_crash_the_report(tmp_path: Path):
    _write_bars(tmp_path, [0, 1])
    # 물리 경로를 직접 조립하지 않는다 — 장중 조각/장후 통합본 어느 배치든 그날의 실제
    # 소스 파일을 아카이버에게 물어 그걸 훼손한다.
    for source in ParquetArchiver(tmp_path).day_sources("A05608", Horizon.M1, _DAY):
        source.write_bytes(source.read_bytes()[:16])

    [m1] = analyze_bar_continuity(tmp_path, "A05608", _DAY)

    assert m1.rows == 0  # 읽기 실패는 "데이터 없음"으로 — 리포트 전체가 죽지 않는다


# ---------------------------------------------------------------- 로그 집계


def test_session_starts_and_levels_are_counted(tmp_path: Path):
    log = tmp_path / "l1.log"
    _write_log(
        log,
        [
            {"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"},
            {"ts": "2026-07-29T12:07:57+09:00", "level": "INFO", "tag": "SessionStart"},
            {"ts": "2026-07-29T13:00:00+09:00", "level": "WARNING", "tag": "CollectorTickStall"},
            {"ts": "2026-07-29T13:01:00+09:00", "level": "CRITICAL", "tag": "KillSwitch"},
        ],
    )

    result = analyze_logs([log])

    assert result["session_starts"] == ["08:35:10", "12:07:57"]
    assert result["level_counts"]["CRITICAL"] == 1
    assert result["tag_counts"]["CollectorTickStall"] == 1


def test_nan_ratio_is_summarised_per_horizon(tmp_path: Path):
    log = tmp_path / "l1.log"
    _write_log(
        log,
        [
            {"level": "DEBUG", "tag": "FeaturePublish", "horizon": "1m", "nan_ratio": 0.9},
            {"level": "DEBUG", "tag": "FeaturePublish", "horizon": "1m", "nan_ratio": 0.1},
            {"level": "DEBUG", "tag": "FeaturePublish", "horizon": "1m", "nan_ratio": 0.5},
            {"level": "DEBUG", "tag": "FeaturePublish", "horizon": "30m", "nan_ratio": 0.96},
        ],
    )

    stats = analyze_logs([log])["nan_ratio_by_horizon"]

    assert stats["1m"] == {"median": 0.5, "min": 0.1, "last": 0.5, "samples": 3}
    assert stats["30m"]["median"] == 0.96


def test_non_json_lines_are_ignored(tmp_path: Path):
    """self_check의 사람용 출력(`[OK ] config ...`)이 섞여 있어도 집계가 깨지면 안 된다."""
    log = tmp_path / "l1.log"
    log.write_text(
        "[OK ] config     instance=messiah-dev-01\n"
        "self-check: PASS\n"
        '{"level": "INFO", "tag": "SessionStart", "ts": "2026-07-29T08:35:10+09:00"}\n',
        encoding="utf-8",
    )

    assert analyze_logs([log])["session_starts"] == ["08:35:10"]


def test_missing_log_file_is_not_an_error(tmp_path: Path):
    assert analyze_logs([tmp_path / "nope.log"])["session_starts"] == []


# ---------------------------------------------------------------- 임계 판정


def _report(tmp_path: Path, *, logs: dict[str, list[Path]], crash=_no_crashes):
    return build_report(
        day=_DAY,
        symbol="A05608",
        instance_id="messiah-dev-01",
        bar_dir=tmp_path / "bars",
        log_paths=logs,
        crash_collector=crash,
    )


def test_clean_day_has_no_breaches(tmp_path: Path):
    _write_bars(tmp_path / "bars", list(range(30)))
    log = tmp_path / "l1.log"
    _write_log(log, [{"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"}])

    report = _report(tmp_path, logs={"l1_daily": [log]})

    assert report.breaches == []
    assert "임계 초과 없음" in format_summary(report)


def test_the_real_incident_day_trips_every_relevant_threshold(tmp_path: Path):
    """2026-07-29를 그대로 재현 — 29분 공백 + 6회 기동(= 재기동 5회) + 네이티브 크래시 2건."""
    _write_bars(tmp_path / "bars", list(range(5)) + list(range(34, 40)))
    log = tmp_path / "l1.log"
    _write_log(
        log,
        [
            {"ts": f"2026-07-29T{hour:02d}:00:00+09:00", "level": "INFO", "tag": "SessionStart"}
            for hour in range(8, 14)
        ],
    )

    report = _report(
        tmp_path,
        logs={"l1_daily": [log]},
        crash=_crashes(2, "13:28:32 x.pyd"),
    )

    joined = " | ".join(report.breaches)
    assert "결손 29분" in joined
    assert "최장 공백 29분" in joined
    # 08시 기동은 예정된 것이므로 재기동은 5회다(2026-08-03 정정 — 그 전엔 기동 횟수를
    # 그대로 "재기동"이라 불러 정상일에도 "재기동 1회"가 찍혔다).
    assert "l1_daily 재기동 5회" in joined
    assert report.starts_by_process == {"l1_daily": 6}
    assert "네이티브 크래시 2건" in joined


def test_restarts_are_reported_per_process_not_summed(tmp_path: Path):
    """합치면 "L1 6회 + G2 5회"가 "11회"가 되어 어느 프로세스가 불안정한지 사라진다."""
    _write_bars(tmp_path / "bars", list(range(30)))
    l1 = tmp_path / "l1.log"
    g2 = tmp_path / "g2.log"
    _write_log(
        l1,
        [
            {"ts": f"2026-07-29T{h:02d}:00:00+09:00", "level": "INFO", "tag": "SessionStart"}
            for h in (8, 12, 13)
        ],
    )
    _write_log(g2, [{"ts": "2026-07-29T08:36:00+09:00", "level": "INFO", "tag": "SessionStart"}])

    report = _report(tmp_path, logs={"l1_daily": [l1], "g2_paper": [g2]})

    # 기동 3회 = 예정 1 + 재기동 2. g2는 예정대로 한 번만 떴으니 재기동 0회다.
    assert report.starts_by_process == {"l1_daily": 3, "g2_paper": 1}
    assert report.restarts_by_process == {"l1_daily": 2, "g2_paper": 0}
    assert report.restarts == 2  # 임계 판정용 스칼라는 최댓값
    assert any("l1_daily 재기동 2회" in b for b in report.breaches)
    assert not any("g2_paper" in b for b in report.breaches)


def test_ui_restarts_are_a_breach_on_their_own(tmp_path: Path):
    """2026-08-03 P1-1 — UI가 죽었다 다시 뜬 날이 "깨끗한 날"로 보고되면 안 된다.

    그날 UI는 2번 죽었는데(11:25:18·14:20:18) 이 리포트가 breach를 낸 건 순전히
    `native_crashes` 덕분이었다. 그건 **Windows 전용 집계**라, 다른 OS이거나 파이썬 레벨로
    죽었으면 화면이 두 번 사라진 날이 임계 초과 0건으로 지나갔을 것이다. 관측 도구가 관측
    공백을 못 보는 상태였다.
    """
    _write_bars(tmp_path / "bars", list(range(30)))
    log = tmp_path / "l1.log"
    _write_log(
        log,
        [
            {"ts": "2026-07-29T08:35:00+09:00", "level": "INFO", "tag": "SessionStart"},
            {"ts": "2026-07-29T11:25:51+09:00", "level": "INFO", "tag": "CommandCenterUIRestarted"},
            {"ts": "2026-07-29T14:20:29+09:00", "level": "INFO", "tag": "CommandCenterUIRestarted"},
        ],
    )

    # 네이티브 크래시 집계가 **불가능한** 환경을 일부러 만든다 — 그래도 잡혀야 한다.
    report = _report(
        tmp_path, logs={"l1_daily": [log]}, crash=lambda _d, **_kw: NativeCrashes(False, 0)
    )

    assert report.ui_restarts == 2
    assert any("UI 자동 재기동 2회" in b for b in report.breaches)


def test_a_single_scheduled_start_is_not_called_a_restart(tmp_path: Path):
    """2026-08-03 P1-2 — 08:35 예정 기동 1회짜리 정상일에 "재기동 1회"가 찍히면 안 된다.

    사람이 매일 그 줄을 보고 무시하는 법을 배우면, 진짜 재기동이 났을 때도 똑같이 무시한다.
    """
    _write_bars(tmp_path / "bars", list(range(30)))
    log = tmp_path / "l1.log"
    _write_log(log, [{"ts": "2026-07-29T08:35:00+09:00", "level": "INFO", "tag": "SessionStart"}])

    report = _report(tmp_path, logs={"l1_daily": [log]})
    summary = format_summary(report)

    assert report.starts_by_process == {"l1_daily": 1}
    assert report.restarts_by_process == {"l1_daily": 0}
    assert report.breaches == []
    assert "l1_daily 기동: 1회 · 재기동 0회" in summary


def test_uncountable_crashes_are_not_reported_as_zero(tmp_path: Path):
    """ "못 셌다"와 "0건"을 구분한다 — 이 사고의 유일한 흔적이 그 로그였다(L18)."""
    _write_bars(tmp_path / "bars", list(range(30)))
    log = tmp_path / "l1.log"
    _write_log(log, [{"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"}])

    report = _report(
        tmp_path,
        logs={"l1_daily": [log]},
        crash=lambda _d, **_kw: NativeCrashes(False, 0, ["Windows 전용"]),
    )

    assert report.native_crashes.available is False
    assert report.breaches == []  # 못 센 것을 위반으로 올리지 않는다
    assert "집계 불가" in format_summary(report)


def test_critical_log_lines_are_a_breach(tmp_path: Path):
    _write_bars(tmp_path / "bars", list(range(30)))
    log = tmp_path / "l1.log"
    _write_log(
        log,
        [
            {"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"},
            {"ts": "2026-07-29T10:00:00+09:00", "level": "CRITICAL", "tag": "KillSwitch"},
        ],
    )

    report = _report(tmp_path, logs={"l1_daily": [log]})

    assert any("CRITICAL 로그 1건" in b for b in report.breaches)


def test_report_serialises_to_json(tmp_path: Path):
    _write_bars(tmp_path / "bars", list(range(5)))
    log = tmp_path / "l1.log"
    _write_log(log, [{"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"}])

    payload = json.loads(json.dumps(_report(tmp_path, logs={"l1_daily": [log]}).to_dict()))

    assert payload["date"] == "2026-07-29"
    assert payload["instance_id"] == "messiah-dev-01"
    assert payload["bar_continuity"][0]["horizon"] == "1m"


# ---------------------------------------------------------------- 탐지·복구 소유권 (고도화 4)


def test_no_findings_when_nothing_happened():
    assert analyze_data_flow_ownership({}) == []


def test_stall_without_reconnect_is_flagged():
    """L1이 감지는 했는데 복구를 못 했다 — 강제 재연결이 걸렸으면 재연결 로그가 따라야 한다."""
    findings = analyze_data_flow_ownership({"CollectorTickStall": 2})

    assert len(findings) == 1
    assert "복구가 안 됨" in findings[0]


def test_stall_followed_by_reconnect_is_healthy():
    assert analyze_data_flow_ownership({"CollectorTickStall": 2, "CollectorWSReconnected": 2}) == []


def test_cb_confirmed_without_any_l1_trace_is_flagged():
    """2026-07-28·29의 30분 공백과 같은 구조 — 거래는 멈췄는데 데이터 흐름은 아무도 안 고쳤다."""
    findings = analyze_data_flow_ownership({"CircuitBreakerConfirmed": 1})

    assert any("아무도 손대지 않음" in f for f in findings)


def test_cb_confirmed_with_an_l1_disconnect_is_consistent():
    """양쪽이 같은 사건을 봤고 정지가 해제까지 갔다면 계층 분리가 의도대로 동작한 것이다."""
    assert (
        analyze_data_flow_ownership(
            {
                "CircuitBreakerConfirmed": 1,
                "CircuitBreakerResumed": 1,
                "CollectorWSDisconnected": 1,
                "CollectorWSReconnected": 1,
            }
        )
        == []
    )


def test_unpaired_cb_confirmations_are_flagged():
    """2026-07-31 실측 회귀 — 그날 확정 5회에 해제 3회였고, 짝이 안 맞는 2회 때문에 주문
    게이트가 6시간 42분간 풀리지 않은 채 장이 끝났다. 예전 규칙 둘은 "한쪽에 흔적이 **아예**
    없을 때"만 봐서 이 형태를 통과시켰다(그날 findings 0건)."""
    findings = analyze_data_flow_ownership(
        {
            "CircuitBreakerConfirmed": 5,
            "CircuitBreakerResumed": 3,
            "CollectorTickStall": 6,
            "CollectorWSDisconnected": 6,
            "CollectorWSReconnected": 6,
        }
    )

    assert len(findings) == 1
    assert "2회가 해제 없이 남음" in findings[0]


def test_reconnect_count_alone_is_not_treated_as_a_mismatch():
    """진짜 단절이 나서 L1이 복구하고 CB가 정지시킨 정상 시나리오와 구분이 안 되므로,
    "재연결 N회 대 CB 확정 M회" 같은 개수 비교는 의도적으로 안 한다(오탐만 늘린다)."""
    assert (
        analyze_data_flow_ownership(
            {
                "CircuitBreakerConfirmed": 3,
                "CircuitBreakerResumed": 3,
                "CollectorTickStall": 6,
                "CollectorWSReconnected": 6,
            }
        )
        == []
    )


# ---------------------------------------------------------------- 시장 상태 (2026-07-31)


def _write_flat_bars(bar_dir: Path, minutes: list[int], *, price: int = 51814) -> None:
    """2026-07-31 오후 형태 — o=h=l=c로 완전히 고정된 봉(상한가 고착/일방시장)."""
    archiver = ParquetArchiver(bar_dir)
    for minute in minutes:
        archiver.append_bar(
            BarClosed(
                symbol="A05608",
                horizon=Horizon.M1,
                bar_open_kst=datetime(2026, 7, 29, 9, 0, tzinfo=KST) + timedelta(minutes=minute),
                o_ticks=price,
                h_ticks=price,
                l_ticks=price,
                c_ticks=price,
                volume=2,
                quality_ok=True,
            )
        )


def test_flat_price_stretch_is_surfaced_as_a_market_state(tmp_path: Path):
    """2026-07-31 실측 회귀 — 그날 이상점(스톨 6회·CB 5회·NaN 33%)의 공통 원인이 "가격이
    1틱도 안 움직였다"였는데, 리포트엔 그걸 가리키는 숫자가 하나도 없어 사람이 Parquet을
    직접 열어야만 보였다."""
    _write_flat_bars(tmp_path / "bars", list(range(40)))
    log = tmp_path / "l1.log"
    _write_log(log, [{"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"}])

    report = _report(tmp_path, logs={"l1_daily": [log]})

    assert report.flat_price_minutes == 40
    assert any("가격 고정" in f for f in report.market_findings)
    assert any("가격 고정" in b for b in report.breaches)
    assert "가격 고정" in format_summary(report)


def test_pre_open_bar_count_is_surfaced(tmp_path: Path):
    """2026-07-31 08:45~09:04의 20봉이 전부 스테일 프린트로 보였다 — 매일 몇 봉이 장전
    구간에서 들어오는지를 남겨야 "그날 장전이 평소와 달랐는가"를 손으로 안 판다."""
    archiver = ParquetArchiver(tmp_path / "bars")
    for minute, session in [(0, BarSession.PRE_OPEN), (1, BarSession.PRE_OPEN), (2, None)]:
        archiver.append_bar(
            BarClosed(
                symbol="A05608",
                horizon=Horizon.M1,
                bar_open_kst=datetime(2026, 7, 29, 9, 0, tzinfo=KST) + timedelta(minutes=minute),
                o_ticks=100,
                h_ticks=105,
                l_ticks=95,
                c_ticks=102,
                volume=10,
                quality_ok=True,
                **({"session": session} if session else {}),
            )
        )
    log = tmp_path / "l1.log"
    _write_log(log, [{"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"}])

    report = _report(tmp_path, logs={"l1_daily": [log]})

    assert report.pre_open_minutes == 2
    assert "장전 봉: 2개" in format_summary(report)


def test_normal_price_movement_is_not_a_market_finding(tmp_path: Path):
    _write_bars(tmp_path / "bars", list(range(40)))  # o≠h≠l≠c
    log = tmp_path / "l1.log"
    _write_log(log, [{"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"}])

    report = _report(tmp_path, logs={"l1_daily": [log]})

    assert report.flat_price_minutes == 0
    assert report.market_findings == []


# ---------------------------------------------------------------- 크래시 집계 창 (2026-07-31)


def test_crash_window_starts_at_the_first_session_start(tmp_path: Path):
    """2026-07-31 실측 회귀 — 그날 리포트의 "크래시 8건" 중 2건은 08:35 기동 **두 시간 전**
    (06:34·06:36)에 이 PC의 다른 파이썬(3.10)이 낸 것이었다. MESSIAH가 돌지도 않던 시간은
    집계 창에서 빠져야 한다."""
    _write_bars(tmp_path / "bars", list(range(30)))
    log = tmp_path / "l1.log"
    _write_log(
        log,
        [
            {"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"},
            {"ts": "2026-07-29T12:00:00+09:00", "level": "INFO", "tag": "SessionStart"},
        ],
    )
    seen: dict[str, object] = {}

    def _collector(day, **kwargs):
        seen.update(kwargs)
        return NativeCrashes(available=True, count=0)

    _report(tmp_path, logs={"l1_daily": [log]}, crash=_collector)

    # naive가 맞다 — Windows 이벤트 로그 조회의 로컬 시각으로 그대로 쓰인다
    # (`_first_session_start` docstring).
    assert seen["since"] == datetime(2026, 7, 29, 8, 35, 10)  # noqa: DTZ001


def test_crash_window_is_open_when_there_was_no_session_start(tmp_path: Path):
    """기동 기록이 없으면 창을 좁힐 근거가 없다 — 임의로 좁혀 사고를 놓치느니 그대로 둔다."""
    _write_bars(tmp_path / "bars", list(range(30)))
    log = tmp_path / "l1.log"
    _write_log(log, [{"ts": "2026-07-29T09:00:00+09:00", "level": "INFO", "tag": "FeaturePublish"}])
    seen: dict[str, object] = {}

    def _collector(day, **kwargs):
        seen.update(kwargs)
        return NativeCrashes(available=True, count=0)

    _report(tmp_path, logs={"l1_daily": [log]}, crash=_collector)

    assert seen["since"] is None


def test_ui_give_up_is_a_breach(tmp_path: Path):
    """2026-07-31 12:35~15:35 3시간 무화면 — 관측이 통째로 사라진 날인데 리포트엔 아무
    표시도 안 났다."""
    _write_bars(tmp_path / "bars", list(range(30)))
    log = tmp_path / "l1.log"
    _write_log(
        log,
        [
            {"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"},
            {
                "ts": "2026-07-29T12:35:53+09:00",
                "level": "ERROR",
                "tag": "CommandCenterUIRestartGaveUp",
            },
        ],
    )

    report = _report(tmp_path, logs={"l1_daily": [log]})

    assert any("관측 공백" in b for b in report.breaches)


def test_ownership_findings_become_breaches_in_the_report(tmp_path: Path):
    _write_bars(tmp_path / "bars", list(range(30)))
    log = tmp_path / "l1.log"
    _write_log(
        log,
        [
            {"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"},
            {
                "ts": "2026-07-29T12:32:00+09:00",
                "level": "WARNING",
                "tag": "CircuitBreakerConfirmed",
            },
        ],
    )

    report = _report(tmp_path, logs={"l1_daily": [log]})

    assert report.data_flow_findings
    assert any("아무도 손대지 않음" in b for b in report.breaches)
    assert "탐지·복구 불일치" in format_summary(report)
