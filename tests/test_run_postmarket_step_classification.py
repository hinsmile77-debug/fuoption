"""장후 배치가 「크래시」와 「발견」을 가르는가 — 2026-09-11 F-101 (`scripts/run_postmarket.py`).

2026-09-11 15:45, 3/7단계(`verify_archive_volume.py`)가 KIS 500으로 죽었는데 요약은
**"완료 — 볼 것이 있다"**라고 말했다. 미처리 예외도 파이썬 기본 동작상 종료 코드 1이라,
`one_means_finding=True` 단계에서 설계된 발견과 구분되지 않았기 때문이다.

여기서 고정하는 것은 세 갈래다: 정상(0) · 설계된 발견(1, 역추적 없음) · 크래시(1, 역추적
있음). 그리고 F-100이 새로 낸 「재지 못함」(3)이 발견으로 둔갑하지 않는다는 것.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run_postmarket  # noqa: E402

_FINDING_STEP = run_postmarket.Step("3/7 거래량 대조", ["python", "x.py"], one_means_finding=True)
_PLAIN_STEP = run_postmarket.Step("2/7 재합성", ["python", "y.py"], one_means_finding=False)

_REAL_TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File ".../scripts/verify_archive_volume.py", line 222, in main\n'
    "    official = backfill.fetch_day_bars(...)\n"
    "httpx.HTTPStatusError: Server error '500 Internal Server Error'\n"
)


def _completed(returncode: int, stderr: str = ""):
    return subprocess.CompletedProcess(args=["python"], returncode=returncode, stderr=stderr)


def _run(monkeypatch, step, returncode: int, stderr: str = ""):
    monkeypatch.setattr(
        run_postmarket.subprocess, "run", lambda *a, **k: _completed(returncode, stderr)
    )
    return run_postmarket._run_step(step)


# ------------------------------------------------------------ 세 갈래 분류


def test_exit_zero_is_plain_success(monkeypatch):
    result = _run(monkeypatch, _FINDING_STEP, 0)
    assert (result.ok, result.finding, result.mark) == (True, False, "✅")


def test_designed_finding_stays_a_finding(monkeypatch):
    """역추적이 없는 exit 1은 종전 그대로 「볼 것이 있다」다 — **판정 불변**.

    이 줄이 깨지면 F-101이 늑대소년을 되살린 것이다(임계 초과가 있는 날은 매일 1이 난다).
    """
    result = _run(monkeypatch, _FINDING_STEP, 1)
    assert (result.ok, result.finding, result.mark) == (True, True, "⚠")


def test_crash_with_traceback_is_not_a_finding(monkeypatch):
    """2026-09-11 15:45의 그 실행 — 같은 exit 1이지만 실패로 읽혀야 한다."""
    result = _run(monkeypatch, _FINDING_STEP, 1, _REAL_TRACEBACK)
    assert (result.ok, result.finding, result.mark) == (False, False, "❌")
    assert "Traceback" in result.detail


def test_crash_beats_exit_zero(monkeypatch):
    """역추적을 찍고도 0으로 끝나는 도구가 있으면 그쪽이 더 위험하다 — 크래시가 우선이다."""
    result = _run(monkeypatch, _PLAIN_STEP, 0, _REAL_TRACEBACK)
    assert result.ok is False


def test_unmeasured_exit_code_is_failure_not_finding(monkeypatch):
    """F-100이 낸 종료 코드 3 — 「재지 못함」은 발견이 아니라 결손이다."""
    result = _run(monkeypatch, _FINDING_STEP, run_postmarket._CHILD_UNMEASURED_EXIT_CODE)
    assert (result.ok, result.finding) == (False, False)
    assert "재지 못함" in result.detail


def test_session_guard_refusal_unchanged(monkeypatch):
    result = _run(monkeypatch, _FINDING_STEP, 2)
    assert result.ok is False and "session_guard" in result.detail


# ------------------------------------------------------------ 오탐 방지


def test_word_traceback_in_prose_is_not_a_crash(monkeypatch):
    """안내문에 "Traceback"이라는 **단어**가 섞여도 크래시가 아니다 — 줄 전체가 정확한
    헤더일 때만 센다(F-101 회귀 위험 항목)."""
    prose = "역추적(Traceback) 문자열이 0건인지 확인할 것\n"
    result = _run(monkeypatch, _FINDING_STEP, 1, prose)
    assert (result.ok, result.finding) == (True, True)


def test_captured_stderr_is_echoed_not_swallowed(monkeypatch, capsys):
    """받아 둔 표준오류는 그대로 다시 나가야 한다 — 삼키면 R10 위반이고, 배치 로그에서
    역추적 원문이 사라진다(그 원문이 오늘 점검의 유일한 증거였다)."""
    _run(monkeypatch, _FINDING_STEP, 1, _REAL_TRACEBACK)

    assert _REAL_TRACEBACK in capsys.readouterr().err
