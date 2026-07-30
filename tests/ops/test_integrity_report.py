"""일일 무결성 리포트 검증 — 고도화 2 (2026-07-30).

이 리포트의 존재 이유는 "2026-07-30에 사람이 손으로 찾아낸 것을 코드가 대신 찾게 한다"이다
(`ops/integrity_report.py` 모듈 docstring). 그래서 테스트도 **그날의 실제 사고 형태**를
재현해 잡히는지 확인하는 쪽으로 짰다 — 29분 공백, 6회 재기동, 하루 종일 높은 nan_ratio.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from messiah.core.messages import BarClosed, Horizon
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


def _no_crashes(_day: date) -> NativeCrashes:
    return NativeCrashes(available=True, count=0)


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
    """2026-07-29를 그대로 재현 — 29분 공백 + 6회 재기동 + 네이티브 크래시 2건."""
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
        crash=lambda _day: NativeCrashes(available=True, count=2, details=["13:28:32 x.pyd"]),
    )

    joined = " | ".join(report.breaches)
    assert "결손 29분" in joined
    assert "최장 공백 29분" in joined
    assert "l1_daily 재기동 6회" in joined
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

    assert report.restarts_by_process == {"l1_daily": 3, "g2_paper": 1}
    assert report.restarts == 3  # 임계 판정용 스칼라는 최댓값
    assert any("l1_daily 재기동 3회" in b for b in report.breaches)
    assert not any("g2_paper" in b for b in report.breaches)


def test_uncountable_crashes_are_not_reported_as_zero(tmp_path: Path):
    """ "못 셌다"와 "0건"을 구분한다 — 이 사고의 유일한 흔적이 그 로그였다(L18)."""
    _write_bars(tmp_path / "bars", list(range(30)))
    log = tmp_path / "l1.log"
    _write_log(log, [{"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"}])

    report = _report(
        tmp_path,
        logs={"l1_daily": [log]},
        crash=lambda _d: NativeCrashes(False, 0, ["Windows 전용"]),
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

    assert len(findings) == 1
    assert "아무도 손대지 않음" in findings[0]


def test_cb_confirmed_with_an_l1_disconnect_is_consistent():
    """양쪽이 같은 사건을 봤다면 계층 분리가 의도대로 동작한 것이다."""
    assert (
        analyze_data_flow_ownership(
            {
                "CircuitBreakerConfirmed": 1,
                "CollectorWSDisconnected": 1,
                "CollectorWSReconnected": 1,
            }
        )
        == []
    )


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
