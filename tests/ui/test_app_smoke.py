"""Command Center Streamlit 앱 스모크 테스트 (신규, Ver 2.0 §9 W32~34).

`streamlit.testing.v1.AppTest`가 실제로 스크립트를 실행하고 예외를 잡아준다 — Streamlit
앱은 일반 pytest로 못 돌린다고 흔히들 생각하지만(이 작업의 원래 계획 문서도 그렇게 가정했다),
AppTest는 공식 테스트 API로 정확히 이런 용도를 위해 있다. 브라우저·실제 서버 포트 없이
스크립트 전체를 한 번 실행해 `at.exception`으로 예외 발생 여부를 확인할 수 있다."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

_APP_PATH = str(Path(__file__).resolve().parents[2] / "src" / "messiah" / "ui" / "app.py")


def test_app_runs_without_exception_in_default_replay_mode():
    at = AppTest.from_file(_APP_PATH)
    at.run(timeout=30)
    assert list(at.exception) == []


def test_app_runs_without_exception_when_switched_to_live_mode():
    # LIVE 모드는 백그라운드 스레드에서 Redis 접속을 시도한다 — 이 테스트 환경엔 Redis가
    # 없을 수 있지만, 그 실패는 스레드 안 try/except로 캐치돼(`_run_live_subscriber`) 메인
    # 스크립트 실행 자체는 예외 없이 끝나야 한다(모듈 docstring 방어 그대로).
    at = AppTest.from_file(_APP_PATH)
    at.run(timeout=30)
    at.sidebar.radio[0].set_value("LIVE").run(timeout=30)
    assert list(at.exception) == []


def test_kill_switch_two_step_confirm_flow_does_not_raise():
    at = AppTest.from_file(_APP_PATH)
    at.run(timeout=30)
    assert len(at.button) == 1  # 최초엔 KILL SWITCH 버튼 하나뿐

    at.button[0].click().run(timeout=30)
    assert list(at.exception) == []
    assert len(at.button) == 2  # 2단 확인 버튼이 추가로 나타남

    at.button[-1].click().run(timeout=30)
    assert list(at.exception) == []
