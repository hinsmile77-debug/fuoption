"""강제 종료를 정상 종료로 센다 — 2026-08-21 F-16 ②③ (이상점 1-16)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from messiah.ops import shutdown_watchdog as sw

# 실제 `logs/shutdown_watchdog.log`의 형식 그대로 — 헤더 한 줄 + 매치 기록 n줄.
_UI_CMD = "C:\\repo\\.venv\\Scripts\\streamlit.exe run C:\\repo\\src\\messiah\\ui\\app.py"
_L1_CMD = '"C:\\repo\\.venv\\Scripts\\python.exe" run_l1_daily.py'
_LOG = "\n".join(
    [
        "[2026-08-20 15:40:01.21] ===== MESSIAH shutdown watchdog start =====",
        f"command-line match, stopping: PID 11580 - {_UI_CMD}",
        "[2026-08-20 15:40:02.12] ===== MESSIAH shutdown watchdog done =====",
        "[2026-08-21 15:40:01.15] ===== MESSIAH shutdown watchdog start =====",
        f"command-line match, stopping: PID 10732 - {_UI_CMD}",
        f"command-line match, stopping: PID 9972 - {_L1_CMD}",
        "[2026-08-21 15:40:02.02] ===== MESSIAH shutdown watchdog done =====",
        "",
    ]
)


def _log(tmp_path: Path) -> Path:
    path = tmp_path / "shutdown_watchdog.log"
    path.write_text(_LOG, encoding="utf-8")
    return path


def test_forced_kills_are_read_from_that_days_records(tmp_path: Path) -> None:
    """**목록을 코드에 박지 않는다** — 박으면 정적 선언이 코드보다 낡는다(1-12와 같은 형태).

    그날 워치독이 **실제로 무엇을 죽였는지**에서 파생시킨다. 관측이 선언을 이긴다.
    """
    kills = sw.forced_kills(date(2026, 8, 21), log_path=_log(tmp_path))

    assert [(k.process, k.pid) for k in kills] == [("ui", 10732), ("l1_daily", 9972)]
    assert all(k.at_kst == "15:40:01" for k in kills)


def test_other_days_are_not_borrowed(tmp_path: Path) -> None:
    """어제 죽인 기록으로 오늘을 면제하면 진짜 사고를 덮는다."""
    kills = sw.forced_kills(date(2026, 8, 19), log_path=_log(tmp_path))
    assert kills == []


def test_an_unreadable_log_is_not_the_same_as_zero_kills(tmp_path: Path) -> None:
    """못 읽은 것과 강제 종료 0건은 다른 사실이다(L18)."""
    missing = tmp_path / "nope.log"
    assert sw.forced_kills(date(2026, 8, 21), log_path=missing) == []
    assert sw.watchdog_log_readable(log_path=missing) is False
    assert sw.watchdog_log_readable(log_path=_log(tmp_path)) is True


def test_a_forced_kill_is_not_an_abnormal_exit(tmp_path: Path) -> None:
    """**UI에는 정상 종료 경로가 없다** — 15:40에 워치독이 죽이는 것이 설계다.

    그것을 「비정상 종료 의심」이라 부르면 매일 적신호 한 줄이 생기고, 진짜 신호가
    목록 아래로 밀려난다(2026-08-21 1-16 · 1-15와 같은 형태).
    """
    forced = sw.forced_processes(date(2026, 8, 21), log_path=_log(tmp_path))
    verdict = sw.session_end_verdict("ui", has_start=True, has_end=False, forced=forced["ui"])

    assert verdict.verdict == "forced_by_design"
    assert verdict.is_finding is False
    # **사유를 명시한다.** 조용히 빼면 「안 봤다」와 「봐서 괜찮았다」가 같아진다.
    assert "15:40:01" in verdict.reason and "10732" in verdict.reason


def test_a_real_abnormal_exit_still_cries(tmp_path: Path) -> None:
    """**원래 잡으려던 것을 놓치면 안 된다** — 워치독이 안 죽였는데 SessionEnd가 없으면
    그건 진짜로 죽은 것이다."""
    forced = sw.forced_processes(date(2026, 8, 21), log_path=_log(tmp_path))
    verdict = sw.session_end_verdict(
        "g2_daily", has_start=True, has_end=False, forced=forced.get("g2_daily")
    )

    assert verdict.verdict == "abnormal"
    assert verdict.is_finding is True


def test_a_clean_exit_needs_no_excuse(tmp_path: Path) -> None:
    verdict = sw.session_end_verdict("l1_daily", has_start=True, has_end=True, forced=None)
    assert verdict.verdict == "clean"
    assert verdict.is_finding is False


def test_a_process_that_never_marks_is_not_judged() -> None:
    """2026-08-07 이전 로그에는 마커 자체가 없다 — 옛 이력을 소급해 빨갛게 칠하지 않는다."""
    verdict = sw.session_end_verdict(
        "old_proc", has_start=True, has_end=False, forced=None, ever_marks_end=False
    )
    assert verdict.verdict == "never_marks"
    assert verdict.is_finding is False
