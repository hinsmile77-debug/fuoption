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


# 절대 살아 있는 Redis를 가리키면 안 되는 주소 — 포트 1은 예약 포트라 즉시 연결 거부된다.
#
# **이 상수가 이 파일에서 가장 중요한 줄이다.** 2026-08-07에 Kill Switch 발행 경로를
# 결선하면서, 사이드바 기본값(`redis://localhost:6380/0` = 운영 버스)을 그대로 둔 채
# 2단 확인을 클릭하는 테스트가 **실제 `sys.kill`을 구동 중인 시스템에 쏠 뻔했다.**
# 그날은 구동 중이던 G2가 수신 분기 없는 구버전이라 우연히 무해했다 — 우연에 기대지 않는다.
_UNREACHABLE_REDIS = "redis://127.0.0.1:1/0"


def test_kill_switch_is_disabled_in_replay_mode():
    """**재생 화면의 버튼이 살아 있는 계좌를 청산하면 안 된다** (2026-08-07 고도화 6).

    2026-08-05 3차의 규율("못 하는 일은 못 하게 보인다")은 그대로다 — 겨냥하는 대상만
    "미배선"에서 "REPLAY"로 바뀌었다. 발행 경로가 생긴 지금, 재생 모드에서 눌리는 것이
    종전의 미배선 상태보다 나쁘다.

    `AppTest.click()`은 `disabled`를 무시하므로 클릭 결과가 아니라 **위젯의 disabled 속성
    자체**를 못박는다 — 그게 실제 사용자가 부딪히는 사실이다.
    """
    at = AppTest.from_file(_APP_PATH)
    at.run(timeout=30)
    at.sidebar.radio[0].set_value("REPLAY").run(timeout=30)

    assert len(at.button) == 1  # KILL SWITCH 버튼 하나뿐
    assert at.button[0].disabled is True
    assert any("REPLAY 모드" in c.value for c in at.caption)
    assert list(at.exception) == []


def test_kill_switch_is_enabled_in_live_mode():
    at = AppTest.from_file(_APP_PATH)
    at.run(timeout=30)

    assert at.sidebar.radio[0].value == "LIVE"
    assert at.button[0].disabled is False


def test_kill_switch_two_step_confirm_surfaces_publish_failure():
    """2단 확인 흐름 + **실패가 화면에 남는가**.

    비상시 최악은 "눌렀는데 아무 일도 안 일어났고 그것을 모르는 것"이다. 그래서 발행
    실패도 세션 상태에 남아야 하고, 다음 fragment 재실행에도 살아 있어야 한다
    (종전 "알려진 갭" 에러가 5초 뒤 사라지던 것이 바로 그 실패 형태였다).

    닿지 않는 Redis를 가리켜 **실패 경로를 실제로 통과시킨다** — 성공 경로를 여기서
    돌리면 운영 버스에 진짜 kill이 나간다(`_UNREACHABLE_REDIS` 주석). 성공 경로는
    `test_publish_kill_sends_a_manual_kill_signal()`이 가짜 버스로 검증한다.
    """
    at = AppTest.from_file(_APP_PATH)
    at.run(timeout=30)
    at.sidebar.text_input[1].set_value(_UNREACHABLE_REDIS).run(timeout=30)

    at.button[0].click().run(timeout=30)
    assert list(at.exception) == []
    assert len(at.button) == 2  # 2단 확인 버튼이 추가로 나타남

    at.button[-1].click().run(timeout=30)
    assert list(at.exception) == [], "발행이 실패해도 화면이 죽으면 안 된다"
    assert any("발행 실패" in err.value for err in at.error)

    at.run(timeout=30)  # 다음 재실행에서도 그대로 남아 있는지
    assert any("발행 실패" in err.value for err in at.error)


def test_publish_kill_sends_a_manual_kill_signal():
    """발행 경로 자체 — 가짜 버스로 성공 경로를 검증한다(운영 버스를 절대 안 탄다)."""
    from messiah.core.bus import TOPIC_KILL
    from messiah.core.messages import KillSignal
    from messiah.ui.app import _publish_kill

    sent: list[tuple[str, object]] = []
    closed: list[bool] = []

    class FakeBus:
        def __init__(self, redis_url: str, instance_id: str) -> None:
            self.redis_url = redis_url

        async def connect(self) -> None:
            pass

        async def publish(self, topic, msg) -> None:
            sent.append((topic, msg))

        async def close(self) -> None:
            closed.append(True)

    _publish_kill("redis://fake/0", "테스트 발동", bus_factory=FakeBus)

    assert len(sent) == 1
    topic, msg = sent[0]
    assert topic == TOPIC_KILL
    assert isinstance(msg, KillSignal)
    assert msg.triggered_by == "manual"
    assert msg.reason == "테스트 발동"
    assert closed == [True], "커넥션은 실패 여부와 무관하게 닫힌다"


def test_top_bar_shows_which_code_version_is_running():
    """2026-08-05엔 11:03·11:57 커밋이 장중 내내 안 돌았는데 화면에 그 축이 없었다."""
    at = AppTest.from_file(_APP_PATH)
    at.run(timeout=30)

    assert any("코드" in md.value for md in at.markdown)
    assert any("화면 기동 후" in md.value for md in at.markdown)
