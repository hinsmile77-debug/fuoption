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
    analyze_horizon_consistency,
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


def _write_ticks(tmp_path: Path, rows: int) -> Path:
    """체결틱 조각 하나를 직접 써 넣는다 (2026-08-04, F2).

    `TickArchiver`를 거치지 않는 이유는 이 테스트의 관심사가 적재 로직이 아니라 **리포트가
    그 결과를 읽는가**이기 때문이다(적재 로직은 `tests/data/test_tick_archiver.py`가 본다).
    """
    import polars as pl

    tick_dir = tmp_path / "ticks" / "A05608" / _DAY.isoformat()
    tick_dir.mkdir(parents=True, exist_ok=True)
    # **세션 전체에 고르게 편다** (2026-08-06). 종전에는 09:00부터 1초 간격이라 5,000행이
    # 09:00~10:23에 몰려 있었다 — 행수 축(`tick_rows`)만 보던 시절엔 무해했지만, 시간
    # 커버리지 축(`ops/series_coverage.py`)이 생기면서 그 픽스처는 "장 시작 25분 뒤에
    # 시작해 10:23에 끊긴 날"이 됐다. 즉 픽스처 쪽이 "정상일"이 아니었다.
    base = datetime(_DAY.year, _DAY.month, _DAY.day, 8, 45, tzinfo=KST)
    span_seconds = (15 * 3600 + 34 * 60) - (8 * 3600 + 45 * 60)  # 08:45~15:34
    step = span_seconds / max(rows - 1, 1)
    pl.DataFrame(
        {
            "ts_kst": [base + timedelta(seconds=i * step) for i in range(rows)],
            "symbol": ["A05608"] * rows,
            "price_ticks": [54015] * rows,
            "qty": [1] * rows,
        }
    ).write_parquet(tick_dir / "09.parquet")
    return tmp_path / "ticks"


def _report(
    tmp_path: Path,
    *,
    logs: dict[str, list[Path]],
    crash=_no_crashes,
    tick_rows: int = 5000,
):
    """`tick_rows` 기본값이 0이 아닌 이유: **정상 운영일에는 틱이 쌓인다.** 0으로 두면
    "깨끗한 날"이라는 픽스처가 실제로는 수집이 끊긴 날을 모델링하게 된다."""
    return build_report(
        day=_DAY,
        symbol="A05608",
        instance_id="messiah-dev-01",
        bar_dir=tmp_path / "bars",
        log_paths=logs,
        crash_collector=crash,
        tick_dir=_write_ticks(tmp_path, tick_rows),
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
    """ "못 셌다"와 "0건"을 구분한다 — 이 사고의 유일한 흔적이 그 로그였다(L18).

    이 플랫폼에서 **원래 못 세는 경우**(`supported=False`, 비Windows)는 위반이 아니다 —
    매일 울리면 늑대소년이 된다. 질의가 실패한 경우는 아래 별도 테스트가 본다.
    """
    _write_bars(tmp_path / "bars", list(range(30)))
    log = tmp_path / "l1.log"
    _write_log(log, [{"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"}])

    report = _report(
        tmp_path,
        logs={"l1_daily": [log]},
        crash=lambda _d, **_kw: NativeCrashes(False, 0, ["Windows 전용"], supported=False),
    )

    assert report.native_crashes.available is False
    assert report.breaches == []  # 못 센 것을 위반으로 올리지 않는다
    assert "집계 불가" in format_summary(report)


def test_failed_crash_query_on_a_supported_platform_is_a_breach(tmp_path: Path):
    """2026-08-04 회귀 — **크래시가 0건인 날에만** 집계가 실패했고 그게 조용히 지나갔다.

    `Get-WinEvent`는 창 안에 이벤트가 하나도 없으면 비종료 오류를 내고 exit 1로 끝난다
    (`-ErrorAction SilentlyContinue`는 출력만 막지 종료 코드는 못 막는다). 그래서 UI 크래시
    격리 수정이 처음 성공한 날 성공을 증명할 수치가 사라졌고, "3거래일 연속 크래시 0건"을
    조건으로 건 등록부는 그 상태로 **영원히 판정을 못 채운다**.

    측정 불능은 0건이 아니다 — 지원되는 플랫폼에서 못 셌으면 그 자체가 임계 초과다.
    """
    _write_bars(tmp_path / "bars", list(range(30)))
    log = tmp_path / "l1.log"
    _write_log(log, [{"ts": "2026-08-04T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"}])

    report = _report(
        tmp_path,
        logs={"l1_daily": [log]},
        crash=lambda _d, **_kw: NativeCrashes(False, 0, ["Get-WinEvent 실패: exit=1"]),
    )

    assert any("집계 불가" in breach for breach in report.breaches)
    assert "집계 실패" in format_summary(report)


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


# ------------------------------------------------ 체결틱 적재량 (2026-08-04, F2)


def test_a_day_with_no_ticks_is_a_breach_even_when_bars_are_clean(tmp_path: Path):
    """봉과 틱은 **수집 경로가 다르다** — 결선이 조용히 끊겨도 봉 지표는 전부 정상으로
    보인다. 그리고 틱은 백필 경로가 없어 그 하루가 영구히 빈다.

    이 프로젝트는 폴러를 만들고 결선을 안 붙여 데이터를 잃은 전례가 셋 있다
    (InvestorFlowPoller 7개월 · OptionChainPoller 수개월 · FL 피처 모델 미도달).
    """
    log = tmp_path / "l1.log"
    log.write_text(
        '{"level": "INFO", "tag": "SessionStart", "ts": "2026-07-30T08:35:00+09:00"}\n',
        encoding="utf-8",
    )

    report = _report(tmp_path, logs={"l1_daily": [log]}, tick_rows=0)

    assert any("체결틱 적재" in b for b in report.breaches)
    assert report.tick_rows == 0


def test_tick_rows_are_counted_as_rows_not_files(tmp_path: Path):
    """파일 개수로 대신하면 "파일은 있는데 0행"을 못 잡는다 — 그게 이 지표가 막으려는
    상황이다."""
    log = tmp_path / "l1.log"
    log.write_text(
        '{"level": "INFO", "tag": "SessionStart", "ts": "2026-07-30T08:35:00+09:00"}\n',
        encoding="utf-8",
    )

    report = _report(tmp_path, logs={"l1_daily": [log]}, tick_rows=4321)

    assert report.tick_rows == 4321
    assert not any("체결틱 적재" in b for b in report.breaches)


def test_tick_rows_reaches_the_verification_registry(tmp_path: Path):
    """`fix_verification`이 실제로 이 필드를 읽을 수 있어야 등록부 항목이 작동한다 —
    지표를 리포트에만 추가하고 추출기를 빠뜨리면 등록부가 조용히 아무것도 안 본다."""
    from messiah.ops.fix_verification import METRIC_EXTRACTORS

    assert METRIC_EXTRACTORS["tick_rows"]({"tick_rows": 1234}) == 1234.0
    assert METRIC_EXTRACTORS["tick_rows"]({}) == 0.0  # 필드 자체가 없는 옛 리포트


# ------------------------------- Horizon 총합 항등식 (2026-08-04 실측 유실, 08-05 신설)


def _write_composite(
    bar_dir: Path, horizon: Horizon, buckets: list[tuple[int, int]], *, symbol: str = "A05608"
) -> None:
    """(시작 분, 거래량) 목록으로 상위 Horizon 봉을 직접 적재한다."""
    archiver = ParquetArchiver(bar_dir)
    for minute, volume in buckets:
        archiver.append_bar(
            BarClosed(
                symbol=symbol,
                horizon=horizon,
                bar_open_kst=datetime(2026, 7, 29, 9, 0, tzinfo=KST) + timedelta(minutes=minute),
                o_ticks=100,
                h_ticks=105,
                l_ticks=95,
                c_ticks=102,
                volume=volume,
                quality_ok=True,
            )
        )


def test_horizon_volume_identity_catches_the_lost_final_minute(tmp_path: Path):
    """2026-08-04 사고의 직접 회귀 — 그날 리포트는 이 유실을 볼 수단이 아예 없었다.

    실측: 1분봉 합 84,346 vs 3/5/10/15/30분봉 전부 84,209. 차이 137은 정확히 15:34봉
    하나였고, 원인은 종료 시퀀스에서 그 봉이 합성기에 도달하기 전에 flush가 돈 것이다.
    그날 리포트는 "1분봉 410개, 결손 0분, CRITICAL/ERROR/WARNING 0"으로 깨끗했다.

    상위 봉은 1분봉의 합이라는 것이 `compose_offline`의 정의이므로 **외부 기준이 필요 없다** —
    그래서 매일 자동으로 돌 수 있다.
    """
    bar_dir = tmp_path / "bars"
    _write_bars(bar_dir, list(range(10)))  # 1분봉 10개 × 10 = 100
    _write_composite(bar_dir, Horizon.M5, [(0, 50), (5, 40)])  # 90 — 마지막 1분(10)이 빠졌다

    findings = analyze_horizon_consistency(bar_dir, "A05608", _DAY)

    assert len(findings) == 1
    assert "5m 거래량 합 90 ≠ 1분봉 합 100" in findings[0]
    assert "마지막 봉 유실 또는 재합성 누락 의심" in findings[0]


def test_horizon_volume_identity_is_quiet_on_a_consistent_day(tmp_path: Path):
    bar_dir = tmp_path / "bars"
    _write_bars(bar_dir, list(range(10)))
    _write_composite(bar_dir, Horizon.M5, [(0, 50), (5, 50)])

    assert analyze_horizon_consistency(bar_dir, "A05608", _DAY) == []


def test_an_overwritten_bucket_leaves_no_row_trace_only_a_volume_shortfall(tmp_path: Path):
    """늦게 온 1분봉이 확정된 버킷을 다시 열면 **행이 늘지 않고 덮어쓰인다**.

    `ParquetArchiver`가 `(bar_open_kst, horizon)`으로 `unique(keep="last")` 하기 때문이다
    (`data/archiver.py`). 그래서 5분봉 하나가 구성봉 5개짜리에서 1개짜리로 조용히 바뀌고,
    행 수·봉 연속성·NaN 비율은 전부 정상으로 보인다. **총합만이 유일한 흔적**이라 이
    검사가 행 수가 아니라 거래량 합을 본다.
    """
    bar_dir = tmp_path / "bars"
    _write_bars(bar_dir, list(range(10)))
    # 09:00 버킷이 두 번 쓰였다(정상 50 → 늦은 봉 하나짜리 10). 뒤엣것만 남는다.
    _write_composite(bar_dir, Horizon.M5, [(0, 50), (0, 10), (5, 50)])

    frame = ParquetArchiver(bar_dir).read_day("A05608", Horizon.M5, _DAY)
    assert frame is not None and frame.height == 2  # 행 수로는 아무 흔적이 없다

    findings = analyze_horizon_consistency(bar_dir, "A05608", _DAY)
    assert len(findings) == 1
    assert "5m 거래량 합 60 ≠ 1분봉 합 100" in findings[0]


def test_horizon_findings_become_breaches(tmp_path: Path):
    bar_dir = tmp_path / "bars"
    _write_bars(bar_dir, list(range(10)))
    _write_composite(bar_dir, Horizon.M5, [(0, 50), (5, 40)])
    log = tmp_path / "l1.log"
    _write_log(log, [{"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"}])

    report = _report(tmp_path, logs={"l1_daily": [log]})

    assert report.horizon_findings
    assert any("1분봉 합" in breach for breach in report.breaches)
    assert "Horizon 정합" in format_summary(report)


# ------------------------------- 버킷 유실 로그 축 (2026-08-05 장중 점검 P0-2)
#
# 그날 `ComposerLateBarDropped` 26건이 나는 동안 리포트가 볼 수 있는 축은 아카이브 항등식
# 하나뿐이었다. 그 축은 장 종료 후 `run_recompose.py`를 돌리면 0이 된다 — 그러면 **수집이
# 실제로 손상됐다는 사실이 다음 날 사라진다**.


def test_late_bar_drops_are_counted_from_the_collection_logs(tmp_path: Path):
    _write_bars(tmp_path / "bars", list(range(30)))
    log = tmp_path / "l1.log"
    _write_log(
        log,
        [
            {"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"},
            *[
                {
                    "ts": f"2026-07-29T09:{minute:02d}:00+09:00",
                    "level": "WARNING",
                    "tag": "ComposerLateBarDropped",
                    "horizon": "3m",
                }
                for minute in range(3)
            ],
            {
                "ts": "2026-07-29T09:10:00+09:00",
                "level": "WARNING",
                "tag": "ComposerFlushedIncomplete",
                "horizon": "5m",
            },
        ],
    )

    report = _report(tmp_path, logs={"l1_daily": [log]})

    assert report.late_bar_drops == 4  # 늦은 봉 3 + 미완 확정 1
    assert any("1분봉 4개 유실" in breach for breach in report.breaches)
    assert "버킷 유실(늦은 봉·미완 확정): 4건" in format_summary(report)


def test_late_bar_drops_survive_a_recompose_that_cleans_the_archive(tmp_path: Path):
    """**이 테스트가 이 축이 따로 있어야 하는 이유다.**

    아카이브는 재합성으로 완전히 정합해졌는데(`horizon_findings`가 빈다) 그날 라이브 수집이
    잘렸다는 사실은 로그에 남아 있다. 두 축이 서로를 대체하면 안 된다.
    """
    bar_dir = tmp_path / "bars"
    _write_bars(bar_dir, list(range(10)))
    _write_composite(bar_dir, Horizon.M5, [(0, 50), (5, 50)])  # 재합성 후 = 항등식 만족
    log = tmp_path / "l1.log"
    _write_log(
        log,
        [
            {"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"},
            {
                "ts": "2026-07-29T09:03:00+09:00",
                "level": "WARNING",
                "tag": "ComposerLateBarDropped",
                "horizon": "5m",
            },
        ],
    )

    report = _report(tmp_path, logs={"l1_daily": [log]})

    assert report.horizon_findings == []  # 아카이브는 깨끗하다
    assert report.late_bar_drops == 1  # 그래도 그날 손상은 있었다
    assert any("유실" in breach for breach in report.breaches)


def test_a_clean_day_reports_zero_bucket_losses_explicitly(tmp_path: Path):
    """0건도 찍는다 — 없으면 "검사했는데 0건"과 "그 축이 없다"가 구분되지 않는다(L18)."""
    _write_bars(tmp_path / "bars", list(range(30)))
    log = tmp_path / "l1.log"
    _write_log(log, [{"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"}])

    report = _report(tmp_path, logs={"l1_daily": [log]})

    assert report.late_bar_drops == 0
    assert "버킷 유실(늦은 봉·미완 확정): 0건 ✅" in format_summary(report)


# ------------------------------------------------- 시계 스큐 (2026-08-05 신설)


def test_clock_skew_is_collected_from_logs_and_breaches_when_large(tmp_path: Path):
    """2026-08-04 실측값(+9.72초)이 그날 리포트에 아무 흔적도 안 남겼다."""
    _write_bars(tmp_path / "bars", list(range(30)))
    log = tmp_path / "l1.log"
    _write_log(
        log,
        [
            {"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"},
            {
                "ts": "2026-07-29T08:45:10+09:00",
                "level": "ERROR",
                "tag": "ClockSkewExceeded",
                "skew_seconds": 9.72,
            },
        ],
    )

    report = _report(tmp_path, logs={"l1_daily": [log]})

    assert report.clock_skew_seconds == 9.72
    assert any("거래소 시각 − 로컬 시계" in breach for breach in report.breaches)
    assert "+9.72초" in format_summary(report)


def test_small_clock_skew_is_recorded_without_a_breach(tmp_path: Path):
    """2026-08-05 w32time 복구 후의 정상 상태 — 값은 남기되 경보는 안 한다."""
    _write_bars(tmp_path / "bars", list(range(30)))
    log = tmp_path / "l1.log"
    _write_log(
        log,
        [
            {"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"},
            {
                "ts": "2026-07-29T08:45:10+09:00",
                "level": "INFO",
                "tag": "ClockSkewMeasured",
                "skew_seconds": -0.02,
            },
        ],
    )

    report = _report(tmp_path, logs={"l1_daily": [log]})

    assert report.clock_skew_seconds == -0.02
    assert report.breaches == []


def test_unmeasured_clock_skew_is_none_not_zero(tmp_path: Path):
    """못 잰 것과 0초는 다르다(L18) — 스큐 로그가 없는 날."""
    _write_bars(tmp_path / "bars", list(range(30)))
    log = tmp_path / "l1.log"
    _write_log(log, [{"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"}])

    report = _report(tmp_path, logs={"l1_daily": [log]})

    assert report.clock_skew_seconds is None
    assert "미측정" in format_summary(report)


def test_session_git_sha_is_recorded_as_a_fact_not_a_breach(tmp_path: Path):
    """2026-08-04엔 수집 프로세스가 08:35에 뜬 옛 커밋으로 하루를 돌았고, 그 사이 12건이
    커밋됐다(WS 프레임 절반 유실 수정 포함). 사후 조사에 반드시 필요한 사실이라 기록하되,
    연구 커밋이 잦은 이 프로젝트에서 매일 울리면 늑대소년이 되므로 판정은 하지 않는다."""
    _write_bars(tmp_path / "bars", list(range(30)))
    log = tmp_path / "l1.log"
    _write_log(
        log,
        [
            {
                "ts": "2026-07-29T08:35:10+09:00",
                "level": "INFO",
                "tag": "SessionStart",
                "git_sha": "d5e6b01",
            }
        ],
    )

    report = _report(tmp_path, logs={"l1_daily": [log]})

    assert report.session_git_shas == ["d5e6b01"]
    assert report.breaches == []
    assert "수집 커밋: d5e6b01" in format_summary(report)


# ================================ 고도화 1·2·3·4·5 (2026-08-05)


def _clean_log(tmp_path: Path) -> Path:
    log = tmp_path / "l1.log"
    _write_log(log, [{"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"}])
    return log


def _healthy_host():
    from messiah.ops.host_health import HostCheck, HostHealth

    return lambda: HostHealth(checks=[HostCheck("disk", True, True, "여유 500GB")])


def _report2(tmp_path: Path, *, logs, crash=_no_crashes, host=None, tick_rows: int = 5000):
    """고도화 축들을 함께 검증하는 판형 — 호스트 점검은 기본으로 **주입**한다.

    실제 `host_health.collect()`를 부르면 테스트가 그 PC의 디스크·전원 상태를 타서
    다른 기계에서 다르게 깨진다.
    """
    return build_report(
        day=_DAY,
        symbol="A05608",
        instance_id="messiah-dev-01",
        bar_dir=tmp_path / "bars",
        log_paths=logs,
        crash_collector=crash,
        tick_dir=_write_ticks(tmp_path, tick_rows),
        log_dir=tmp_path,
        host_collector=host or _healthy_host(),
    )


# ---------------------------------------------------------------- 고도화 1: 외부 대조


def test_volume_check_artifact_becomes_a_first_class_axis(tmp_path: Path):
    """2026-08-04 사고의 대응 — 리포트는 "결손 0분"으로 깨끗했는데 아카이브 거래량은
    공식값의 55%였다. 내부 정합성(Horizon 항등식)은 수집값끼리의 일치라 절반 유실이
    양쪽에 똑같이 반영돼 통과한다. 외부 기준이 있어야만 잡힌다."""
    _write_bars(tmp_path / "bars", list(range(30)))
    (tmp_path / "volume_check_20260729.json").write_text(
        json.dumps({"date": "2026-07-29", "ratio": 0.551, "warn_ratio": 0.95, "ok": False}),
        encoding="utf-8",
    )

    report = _report2(tmp_path, logs={"l1_daily": [_clean_log(tmp_path)]})

    assert report.volume_check is not None
    assert any("거래량 비율" in breach for breach in report.breaches)
    assert "공식 분봉 대비 거래량" in format_summary(report)


def test_a_passing_volume_check_is_recorded_without_a_breach(tmp_path: Path):
    _write_bars(tmp_path / "bars", list(range(30)))
    (tmp_path / "volume_check_20260729.json").write_text(
        json.dumps({"ratio": 1.0, "warn_ratio": 0.95, "ok": True}), encoding="utf-8"
    )

    report = _report2(tmp_path, logs={"l1_daily": [_clean_log(tmp_path)]})

    assert report.breaches == []
    assert "공식 분봉 대비 거래량 대조" not in " ".join(report.unmeasured)


# ---------------------------------------------------------------- 고도화 2: 미측정 승격


def test_everything_unmeasured_is_collected_in_one_place(tmp_path: Path):
    """2026-08-04에 크래시 집계가 조용히 사라진 것이 이 축의 계기다.

    "오늘 무엇을 모르는가"가 한 줄로 안 보이면 사람은 리포트를 "깨끗한 날"로 읽는다.
    """
    _write_bars(tmp_path / "bars", list(range(30)))

    report = _report2(tmp_path, logs={"l1_daily": [_clean_log(tmp_path)]})

    joined = " ".join(report.unmeasured)
    assert "시계 스큐" in joined
    assert "거래량 대조" in joined
    assert "변동성 축" in joined
    assert "피처 건강도" in joined
    assert "❓ 미측정:" in format_summary(report)


def test_measured_axes_drop_out_of_unmeasured(tmp_path: Path):
    """반대 방향 — 실제로 잰 축은 목록에서 빠져야 한다(안 그러면 매일 다 뜬다)."""
    _write_bars(tmp_path / "bars", list(range(30)))
    log = tmp_path / "l1.log"
    _write_log(
        log,
        [
            {"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"},
            {"level": "INFO", "tag": "ClockSkewMeasured", "skew_seconds": -0.01},
            {
                "level": "INFO",
                "tag": "TickDeliveryLatency",
                "measured": True,
                "p50": 0.05,
                "p90": 0.31,
                "p99": 0.88,
                "max": 2.4,
                "samples": 12000,
            },
            {
                "level": "INFO",
                "tag": "FeatureHealthSummary",
                "horizon": "1m",
                "always_nan": [],
                "constant": [],
            },
        ],
    )
    (tmp_path / "volume_check_20260729.json").write_text(
        json.dumps({"ratio": 1.0, "ok": True}), encoding="utf-8"
    )
    (tmp_path / "vol_scorecard_20260729.json").write_text(
        json.dumps(
            {"horizons": {"5m": {"baseline_ic": 0.4, "beats_baseline": [], "samples": 900}}}
        ),
        encoding="utf-8",
    )

    report = _report2(tmp_path, logs={"l1_daily": [log]})

    assert report.unmeasured == []
    assert report.breaches == []
    assert "변동성 축 5m" in format_summary(report)


# ---------------------------------------------------------------- 고도화 3: 죽은 피처


def test_degenerate_features_become_a_breach(tmp_path: Path):
    """`px_macd_h_5`는 **값을 내므로** nan_ratio에 흔적이 없었다 — 8거래일 내내 죽어 있었고
    무결성 리포트는 그걸 말할 수단이 아예 없었다."""
    _write_bars(tmp_path / "bars", list(range(30)))
    log = tmp_path / "l1.log"
    _write_log(
        log,
        [
            {"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"},
            {
                "level": "WARNING",
                "tag": "FeatureHealthDegenerate",
                "horizon": "5m",
                "always_nan": ["px_ema_cross_60"],
                "constant": ["px_macd_h_5"],
            },
        ],
    )

    report = _report2(tmp_path, logs={"l1_daily": [log]})

    assert report.degenerate_features["5m"]["constant"] == ["px_macd_h_5"]
    assert any("죽어 있었다" in breach for breach in report.breaches)


# ---------------------------------------------------------------- 고도화 5: 호스트 위생


def test_degraded_host_is_a_breach_but_unmeasured_host_is_not(tmp_path: Path):
    """디스크가 찼다는 것은 판정이고, 전원 계획을 못 읽었다는 것은 미판정이다 —
    둘을 합치면 오탐이 늘거나(후자를 실패로) 사고를 놓친다(전자를 무시로)."""
    from messiah.ops.host_health import HostCheck, HostHealth

    _write_bars(tmp_path / "bars", list(range(30)))
    host = lambda: HostHealth(  # noqa: E731
        checks=[
            HostCheck("disk", True, False, "여유 0.2GB (최소 5GB)"),
            HostCheck("power", False, True, "측정 실패(형식 불일치)"),
        ]
    )

    report = _report2(tmp_path, logs={"l1_daily": [_clean_log(tmp_path)]}, host=host)

    assert any("호스트 위생: disk" in breach for breach in report.breaches)
    assert any("power" in item for item in report.unmeasured)
    assert not any("power" in breach for breach in report.breaches)


# ------------------------- 회선 수신 지연 분포 (2026-08-05 2차, 고도화 1)
#
# `minute_bar_close: timer`(1분봉 시각 확정) 승격의 **유일한 근거 데이터**다. 2026-08-05까지
# 이 프로젝트엔 회선 지연을 잰 것이 하나도 없었다 — 틱 아카이브는 거래소 시각만 남긴다.


def test_delivery_latency_is_carried_into_the_report(tmp_path: Path):
    _write_bars(tmp_path / "bars", list(range(30)))
    log = tmp_path / "l1.log"
    _write_log(
        log,
        [
            {"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"},
            {
                "level": "INFO",
                "tag": "TickDeliveryLatency",
                "measured": True,
                "p50": 0.05,
                "p90": 0.31,
                "p99": 0.88,
                "max": 2.4,
                "samples": 12000,
            },
        ],
    )

    report = _report(tmp_path, logs={"l1_daily": [log]})

    assert report.delivery_latency is not None
    assert report.delivery_latency["p99"] == 0.88
    assert "회선 수신 지연 초과분" in format_summary(report)
    # **판정은 안 한다** — 임계를 정할 근거를 모으는 중이라 breach가 되면 안 된다.
    assert not any("지연" in breach for breach in report.breaches)


def test_unmeasured_latency_is_not_treated_as_zero(tmp_path: Path):
    """못 잰 것과 "지연 없음"을 합치면 승격 근거가 조용히 사라진다(L18)."""
    _write_bars(tmp_path / "bars", list(range(30)))
    log = tmp_path / "l1.log"
    _write_log(
        log,
        [
            {"ts": "2026-07-29T08:35:10+09:00", "level": "INFO", "tag": "SessionStart"},
            {"level": "INFO", "tag": "TickDeliveryLatency", "measured": False},
        ],
    )

    report = _report(tmp_path, logs={"l1_daily": [log]})

    assert report.delivery_latency is None
    assert any("회선 수신 지연" in item for item in report.unmeasured)


# ---------------------------------------------- 고도화 2(2026-08-06): 적재 계열 커버리지


def _write_series(tmp_path: Path, relative: str, stamps) -> None:
    """계열 파케이 하나 — `ops/series_coverage.py`가 발견해 읽는 배치 그대로."""
    import polars as pl

    path = tmp_path / relative / f"{_DAY.isoformat()}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"ts_kst": stamps, "v": [1] * len(stamps)}).write_parquet(path)


def _session_minutes(first_hour: int, first_minute: int, count: int, step: int = 1):
    base = datetime(_DAY.year, _DAY.month, _DAY.day, first_hour, first_minute, tzinfo=KST)
    return [base + timedelta(minutes=i * step) for i in range(count)]


def test_a_series_wiped_by_a_restart_becomes_a_breach(tmp_path: Path):
    """**결선 회귀.** 2026-08-06에 옵션체인 1,500다리와 수급 264행이 사라졌는데 리포트가
    완벽하게 조용했다 — 그 계열들을 아예 안 봤기 때문이다. 축을 만들고 `build_report()`에
    안 붙이면 같은 상태가 그대로 남는다(이 프로젝트가 반복한 실패 형태)."""
    _write_bars(tmp_path / "bars", list(range(30)))
    # 08:35 기동인데 첫 행이 10:30 — 재기동이 오전치를 덮어쓴 그날의 모양 그대로.
    _write_series(tmp_path, "option_chain/regular", _session_minutes(10, 30, 30, step=10))

    report = _report2(tmp_path, logs={"l1_daily": [_clean_log(tmp_path)]})

    assert any("option_chain/regular" in f for f in report.series_findings)
    assert any("option_chain/regular" in b for b in report.breaches), "breach로 안 올라갔다"


def test_series_coverage_is_recorded_even_when_healthy(tmp_path: Path):
    """정상 계열도 리포트에 남아야 "검사했는데 이상 없다"와 "안 본다"가 갈린다."""
    _write_bars(tmp_path / "bars", list(range(30)))
    _write_series(tmp_path, "flow_intraday/K2I", _session_minutes(8, 36, 419))

    report = _report2(tmp_path, logs={"l1_daily": [_clean_log(tmp_path)]})

    names = [entry["name"] for entry in report.series_coverage]
    assert "flow_intraday/K2I" in names
    assert not any("flow_intraday" in f for f in report.series_findings)


def test_series_dirs_are_derived_from_the_bar_dir_not_the_repo(tmp_path: Path):
    """tmp로 만든 리포트가 저장소의 진짜 `data/`를 집어 들면 테스트가 그 PC 상태를 탄다."""
    _write_bars(tmp_path / "bars", list(range(30)))

    report = _report2(tmp_path, logs={"l1_daily": [_clean_log(tmp_path)]})

    assert [entry["name"] for entry in report.series_coverage] == ["ticks"]


# ------------------- 관측 공백 결선 (2026-08-06 P1-1·P1-2)


def _reboot_collector(day):  # noqa: ANN001
    from messiah.ops.observation_gaps import HostEvent

    return (
        [
            HostEvent(1074, "10:03:49", "shutdown", "RuntimeBroker.exe / 다시 시작 / 기타"),
            HostEvent(13, "10:04:31", "shutdown"),
            HostEvent(12, "10:05:03", "boot"),
        ],
        True,
        "이벤트 3건",
    )


def _restart_log(tmp_path: Path) -> Path:
    """08:35 기동 → 10:04까지 활동 → 10:25 재기동 (2026-08-06 실측 형태)."""
    log = tmp_path / "l1_restart.log"
    _write_log(
        log,
        [
            {"ts": "2026-07-29T08:35:23+09:00", "level": "INFO", "tag": "SessionStart"},
            {"ts": "2026-07-29T10:04:00+09:00", "level": "DEBUG", "tag": "FeaturePublish"},
            {"ts": "2026-07-29T10:25:31+09:00", "level": "INFO", "tag": "SessionStart"},
        ],
    )
    return log


def test_an_observation_gap_becomes_a_breach_with_its_cause(tmp_path: Path):
    """**결선 회귀.** 2026-08-06 리포트가 말한 것은 "재기동 1회"뿐이었다 — 21분을 잃었다는
    것도, 왜 그랬는지도 없었다."""
    _write_bars(tmp_path / "bars", list(range(30)))

    report = build_report(
        day=_DAY,
        symbol="A05608",
        instance_id="messiah-dev-01",
        bar_dir=tmp_path / "bars",
        log_paths={"l1_daily": [_restart_log(tmp_path)]},
        crash_collector=_no_crashes,
        tick_dir=_write_ticks(tmp_path, 5000),
        log_dir=tmp_path,
        host_collector=_healthy_host(),
        host_event_collector=_reboot_collector,
    )

    [gap] = report.observation_gaps
    assert gap["minutes"] == 21.0
    assert gap["exact"] is True
    assert "RuntimeBroker.exe" in gap["cause"]
    assert any("관측 공백" in b for b in report.breaches), "breach로 안 올라갔다"


def test_host_events_are_recorded_as_facts(tmp_path: Path):
    """공백이 없는 날에도 호스트 생명주기는 남는다 — 사후 조사의 재료다."""
    _write_bars(tmp_path / "bars", list(range(30)))

    report = build_report(
        day=_DAY,
        symbol="A05608",
        instance_id="messiah-dev-01",
        bar_dir=tmp_path / "bars",
        log_paths={"l1_daily": [_clean_log(tmp_path)]},
        crash_collector=_no_crashes,
        tick_dir=_write_ticks(tmp_path, 5000),
        log_dir=tmp_path,
        host_collector=_healthy_host(),
        host_event_collector=_reboot_collector,
    )

    assert [e["event_id"] for e in report.host_events] == [1074, 13, 12]
    assert report.observation_gaps == []  # 재기동이 없으면 공백도 없다


def test_unreadable_host_events_land_in_unmeasured(tmp_path: Path):
    """못 읽은 것을 "공백 없음"으로 세면 검증이 거짓으로 통과한다(L18)."""
    _write_bars(tmp_path / "bars", list(range(30)))

    report = build_report(
        day=_DAY,
        symbol="A05608",
        instance_id="messiah-dev-01",
        bar_dir=tmp_path / "bars",
        log_paths={"l1_daily": [_clean_log(tmp_path)]},
        crash_collector=_no_crashes,
        tick_dir=_write_ticks(tmp_path, 5000),
        log_dir=tmp_path,
        host_collector=_healthy_host(),
        host_event_collector=lambda day: ([], False, "조회 실패"),
    )

    assert any("관측 공백 원인" in item for item in report.unmeasured)


# ------------------------------------------------- 기동 창 거절 (2026-08-07 P0-4)
#
# 2026-08-06에 붙인 at-startup 트리거가 2026-08-07 07:23에 처음 발화했고, 기동 창 가드가
# 설계대로 거절했다. 그런데 `SessionStart`는 이미 찍힌 뒤라 리포트는 그것을 기동으로 세고
# `재기동 1회` + `관측 공백 73분(원인 불명)` + 전 계열 `머리 구멍 72~82분`을 찍었다.
# 전부 오탐이고, `observation_gap_minutes_max max: 5` 등록부 항목을 뒤집을 값이었다.


def test_refused_launch_is_not_counted_as_a_session_start(tmp_path):
    from messiah.ops.integrity_report import analyze_logs

    log = tmp_path / "l1_daily_20260807.log"
    log.write_text(
        "\n".join(
            [
                '{"ts": "2026-08-07T07:23:31+09:00", "level": "INFO", "tag": "SessionStart"}',
                '{"ts": "2026-08-07T07:23:31+09:00", "level": "INFO", '
                '"tag": "LaunchWindowRefused", "msg": "기동 창 이전"}',
                '{"ts": "2026-08-07T08:35:34+09:00", "level": "INFO", "tag": "SessionStart"}',
            ]
        ),
        encoding="utf-8",
    )

    result = analyze_logs([log])

    assert result["session_starts"] == ["08:35:34"], "거절된 기동은 없던 것으로 친다"
    assert result["refused_starts"] == ["07:23:31"]


def test_legacy_plaintext_refusal_is_also_recognised(tmp_path):
    """구조화 태그가 생기기 **전에** 쓰인 로그도 읽는다.

    2026-08-07 07:23의 거절은 이 수정보다 먼저 로그에 쓰였고, 그날 15:45 리포트가 그 로그를
    읽는다. 폴백이 없으면 고친 당일의 리포트만 여전히 틀린 값을 낸다.
    """
    from messiah.ops.integrity_report import analyze_logs

    log = tmp_path / "l1_daily_20260807.log"
    log.write_text(
        "\n".join(
            [
                '{"ts": "2026-08-07T07:23:31+09:00", "level": "INFO", "tag": "SessionStart"}',
                "[기동 창] 기동 창(08:30~15:35) 이전 07:23:31 — 정시 트리거(08:35)에 맡긴다",
                '{"ts": "2026-08-07T08:35:34+09:00", "level": "INFO", "tag": "SessionStart"}',
            ]
        ),
        encoding="utf-8",
    )

    result = analyze_logs([log])

    assert result["session_starts"] == ["08:35:34"]


def test_refusal_after_a_real_start_does_not_erase_the_real_one():
    """개수만 빼면 살아 있어야 할 기동이 지워진다 — 시각으로 짝짓는 이유."""
    from messiah.ops.integrity_report import _drop_refused_starts

    # 08:35 정상 기동 → 15:50 부팅 트리거 발화 → 창 이후라 거절.
    assert _drop_refused_starts(["08:35:34", "15:50:02"], ["15:50:02"]) == ["08:35:34"]


def test_unmatched_refusal_is_ignored():
    """거절이 기동보다 많으면(로그가 잘림) 남은 기동을 마저 지우지 않는다."""
    from messiah.ops.integrity_report import _drop_refused_starts

    assert _drop_refused_starts(["09:00:00"], ["07:00:00", "08:00:00"]) == ["09:00:00"]
