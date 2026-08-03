"""네이티브 크래시 포렌식 무장 검증 (2026-08-03).

이 모듈의 존재 이유가 "크래시 순간에 반드시 켜져 있어야 한다"이므로, 검증도 그 성질에
집중한다: ① 무장이 실제로 성립하는가 ② Streamlit의 반복 실행에 멱등한가 ③ 실패해도 본
기능을 안 막는가 ④ 실제 access violation에서 전 스레드 스택이 나오는가(별도 프로세스).
"""

from __future__ import annotations

import faulthandler
import subprocess
import sys
import textwrap

import pytest
from messiah.core import crash_forensics


@pytest.fixture(autouse=True)
def _reset():
    crash_forensics._reset_for_tests()
    yield
    crash_forensics._reset_for_tests()


def test_enable_arms_faulthandler(tmp_path):
    # pytest가 stderr를 캡처하면 fileno()가 없어 폴백 파일로 간다 — 어느 쪽이든 무장은 된다.
    description = crash_forensics.enable(tag="unit", log_dir=tmp_path)

    assert crash_forensics.is_armed()
    assert faulthandler.is_enabled()
    assert description


def test_enable_is_idempotent(tmp_path):
    """Streamlit은 재실행마다 app.py를 통째로 다시 돌린다(5초 주기) — 두 번째 호출부터는
    아무 일도 일어나지 않아야 한다. 안 그러면 5초마다 파일 핸들이 하나씩 쌓인다."""
    first = crash_forensics.enable(tag="unit", log_dir=tmp_path)
    second = crash_forensics.enable(tag="unit", log_dir=tmp_path)
    third = crash_forensics.enable(tag="다른태그", log_dir=tmp_path / "다른경로")

    assert first == second == third
    assert len(list(tmp_path.glob("crash_*.log"))) <= 1


def test_falls_back_to_file_when_stream_has_no_fileno(tmp_path):
    """faulthandler는 파일 객체가 아니라 fd를 잡는다 — fileno()가 없는 스트림은 못 쓴다.
    그 경우 무장을 포기하지 않고 전용 파일로 내려간다."""

    class _NoFileno:
        def write(self, _text: str) -> int:
            return 0

        def flush(self) -> None:
            pass

    crash_forensics.enable(tag="unit", log_dir=tmp_path, stream=_NoFileno())

    assert crash_forensics.is_armed()
    assert (tmp_path / "crash_unit.log").exists()


def test_marker_line_is_written_exactly_once(tmp_path):
    """무장 마커가 없는 로그 = '무장 안 된 채로 돈 세션'이라는 신호가 되어야 하므로 정확히
    한 번 남아야 한다(매 재실행마다 쌓이면 신호가 아니라 잡음이 된다)."""
    path = tmp_path / "stream.log"
    with path.open("w+", encoding="utf-8") as stream:
        for _ in range(5):
            crash_forensics.enable(tag="unit", log_dir=tmp_path, stream=stream)

    assert path.read_text(encoding="utf-8").count("[crash_forensics]") == 1


def test_enable_never_raises_when_log_dir_is_unusable(tmp_path):
    """포렌식 도구 실패가 본 기능(UI 렌더·데이터 수집)을 막으면 안 된다 — 예외 대신
    설명 문자열로 실패를 알린다."""
    blocker = tmp_path / "blocked"
    blocker.write_text("나는 디렉터리가 아니다", encoding="utf-8")

    class _NoFileno:
        def write(self, _text: str) -> int:
            return 0

    description = crash_forensics.enable(tag="unit", log_dir=blocker, stream=_NoFileno())

    assert description.startswith(crash_forensics.UNAVAILABLE_PREFIX)
    assert not crash_forensics.is_armed()


# ---------------------------------------------------------------- 실제 access violation

_AV_PROBE = textwrap.dedent(
    """
    import ctypes, threading, time
    from messiah.core.crash_forensics import enable

    enable(tag="probe")
    threading.Thread(target=lambda: time.sleep(30), daemon=True).start()
    time.sleep(0.2)
    ctypes.string_at(0)   # EXCEPTION_ACCESS_VIOLATION (0xc0000005)
    """
)


@pytest.mark.skipif(sys.platform != "win32", reason="0xc0000005는 Windows 고유 예외 코드")
def test_access_violation_dumps_all_thread_stacks():
    """이 테스트가 이 모듈 전체의 존재 이유다.

    2026-07-29~08-03 UI를 5거래일 연속 죽인 것이 바로 `_polars_runtime.pyd`의
    0xc0000005였고, 그때마다 로그에 **아무 흔적도 없어서** 세 번이나 잘못된 가설로 고쳤다.
    같은 예외를 인위적으로 일으켜 ① 덤프가 나오고 ② 죽은 스레드 말고 **다른 스레드의
    스택까지** 나오는지 못박는다 — 후자가 "polars에 동시에 들어간 스레드가 몇 개였나"라는
    미해결 질문의 판정 수단이다.
    """
    result = subprocess.run(
        [sys.executable, "-c", _AV_PROBE],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )

    assert "Windows fatal exception: access violation" in result.stderr
    assert "Current thread 0x" in result.stderr
    # 죽은 스레드 말고도 스택이 찍혔다 = `all_threads=True`가 실제로 걸렸다는 증거.
    # 스레드 블록마다 정확히 한 번 나오는 머리말을 센다(`Current thread`는 소문자 t라
    # `"Thread 0x"` 카운트로는 안 잡힌다 — 실측으로 확인).
    assert result.stderr.count("(most recent call first)") >= 2
