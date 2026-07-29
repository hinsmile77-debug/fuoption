"""Command Center Streamlit 앱 스모크 테스트 (신규, Ver 2.0 §9 W32~34).

`streamlit.testing.v1.AppTest`가 실제로 스크립트를 실행하고 예외를 잡아준다 — Streamlit
앱은 일반 pytest로 못 돌린다고 흔히들 생각하지만(이 작업의 원래 계획 문서도 그렇게 가정했다),
AppTest는 공식 테스트 API로 정확히 이런 용도를 위해 있다. 브라우저·실제 서버 포트 없이
스크립트 전체를 한 번 실행해 `at.exception`으로 예외 발생 여부를 확인할 수 있다."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

_APP_PATH = str(Path(__file__).resolve().parents[2] / "src" / "messiah" / "ui" / "app.py")


def test_app_runs_without_exception_in_default_live_mode():
    # 2026-07-29 사용자 요청으로 기본값이 REPLAY→LIVE로 바뀌었다(`app.py` 모듈 docstring) —
    # LIVE는 백그라운드 스레드에서 Redis 접속을 시도하지만, 그 실패는 스레드 안 try/except로
    # 캐치돼(`_run_live_subscriber`) 메인 스크립트 실행 자체는 예외 없이 끝나야 한다.
    at = AppTest.from_file(_APP_PATH)
    at.run(timeout=30)
    assert at.sidebar.radio[0].value == "LIVE"
    assert list(at.exception) == []


def test_app_runs_without_exception_when_switched_to_replay_mode():
    at = AppTest.from_file(_APP_PATH)
    at.run(timeout=30)
    at.sidebar.radio[0].set_value("REPLAY").run(timeout=30)
    assert list(at.exception) == []


def test_circuit_breaker_badge_shows_unused_when_no_status_published():
    # `circuit_breaker_monitor`를 안 쓰는 경로(스모크·재생 등)에서는 sys.circuit_breaker
    # 자체가 발행되지 않는다 — "정상"이 아니라 "미사용/데이터 없음"으로 명시돼야 한다
    # (마흐디 L18 — 값 없음과 정상을 혼동하지 않는다, `app.py` 모듈 docstring 참고).
    at = AppTest.from_file(_APP_PATH)
    at.run(timeout=30)
    assert any("서킷브레이커" in md.value for md in at.markdown)
    assert any("미사용/데이터 없음" in md.value for md in at.markdown)


def test_kill_switch_two_step_confirm_flow_does_not_raise():
    at = AppTest.from_file(_APP_PATH)
    at.run(timeout=30)
    assert len(at.button) == 1  # 최초엔 KILL SWITCH 버튼 하나뿐

    at.button[0].click().run(timeout=30)
    assert list(at.exception) == []
    assert len(at.button) == 2  # 2단 확인 버튼이 추가로 나타남

    at.button[-1].click().run(timeout=30)
    assert list(at.exception) == []
