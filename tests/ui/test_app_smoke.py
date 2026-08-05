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


def test_kill_switch_is_disabled_while_the_publish_path_is_unwired():
    """**미배선이면 눌리지 않아야 한다** (2026-08-05 3차, P2).

    종전엔 화면에서 가장 강한 요소(적색 primary)가 멀쩡히 눌렸고, 2단 확인까지 통과하면
    "알려진 갭" 에러가 떴다 — 그런데 그 에러는 세션 상태에 안 남아 5초 뒤 fragment
    재실행에 사라졌다. 비상시에 누르고 사라진 문구를 못 본 채 "발동됐다"고 믿는 것이 최악이다.

    `AppTest.click()`은 `disabled`를 무시하고 값을 넣으므로(브라우저와 다르다) 클릭 결과가
    아니라 **위젯의 disabled 속성 자체**를 못박는다 — 그게 실제 사용자가 부딪히는 사실이다.
    """
    at = AppTest.from_file(_APP_PATH)
    at.run(timeout=30)

    assert len(at.button) == 1  # 최초엔 KILL SWITCH 버튼 하나뿐
    assert at.button[0].disabled is True
    assert any("미배선" in c.value for c in at.caption)


def test_kill_switch_two_step_confirm_flow_does_not_raise():
    """배선되는 날을 위한 흐름 자체는 살아 있어야 한다 — 예외 없이 2단 확인까지 간다."""
    at = AppTest.from_file(_APP_PATH)
    at.run(timeout=30)

    at.button[0].click().run(timeout=30)
    assert list(at.exception) == []
    assert len(at.button) == 2  # 2단 확인 버튼이 추가로 나타남

    at.button[-1].click().run(timeout=30)
    assert list(at.exception) == []
    # 발동 요청은 **한 번 뜨고 사라지지 않는다** — 세션에 남는 사실이다(종전엔 5초 뒤 소멸).
    assert any("발동 요청됨" in err.value for err in at.error)
    at.run(timeout=30)  # 다음 재실행에서도 그대로 남아 있는지
    assert any("발동 요청됨" in err.value for err in at.error)


def test_top_bar_shows_which_code_version_is_running():
    """2026-08-05엔 11:03·11:57 커밋이 장중 내내 안 돌았는데 화면에 그 축이 없었다."""
    at = AppTest.from_file(_APP_PATH)
    at.run(timeout=30)

    assert any("코드" in md.value for md in at.markdown)
    assert any("화면 기동 후" in md.value for md in at.markdown)
