"""ui_launcher 검증 — 실제 streamlit·실제 소켓 없이 전부 주입된 페이크로 확인."""

from __future__ import annotations

import json
import subprocess

import pytest

from messiah.core.ui_launcher import (
    DEFAULT_PORT,
    identify_port_holder,
    launch_command_center,
    watch_command_center_forever,
)


def _fake_popen(pid: int = 4242):
    calls = []

    def _popen(*args, **kwargs):
        calls.append((args, kwargs))
        proc = subprocess.Popen.__new__(subprocess.Popen)
        proc.pid = pid  # type: ignore[misc]
        return proc

    return _popen, calls


def _project(tmp_path):
    """실행파일·앱 경로가 실재하는 최소 프로젝트 — 기동 단계까지 가려면 둘 다 있어야 한다."""
    exe = tmp_path / "streamlit.exe"
    exe.write_text("stub")
    app_dir = tmp_path / "src" / "messiah" / "ui"
    app_dir.mkdir(parents=True)
    app_path = app_dir / "app.py"
    app_path.write_text("")
    return exe, app_path


def _launch(tmp_path, **overrides):
    """마커 경로를 **반드시 tmp_path로** 넘긴다 — 기본값은 실제 `logs/`라 테스트가 운영
    흔적 파일을 덮어쓴다."""
    kwargs = {
        "caller_tag": "test",
        "project_root": tmp_path,
        "log_path": tmp_path / "ui.log",
        "marker_path": tmp_path / "marker.json",
    }
    kwargs.update(overrides)
    return launch_command_center(**kwargs)


def test_skips_when_env_var_set(tmp_path, monkeypatch):
    monkeypatch.setenv("MESSIAH_SKIP_UI", "1")
    popen, calls = _fake_popen()
    result = _launch(tmp_path, is_running=lambda port: False, popen=popen)
    assert result.process is None
    assert result.status == "skipped"
    assert calls == []


def test_skips_when_our_own_ui_already_holds_the_port(tmp_path):
    """정상 시나리오 — L1이 08:20에 띄운 UI가 응답 중이고 G2가 08:25에 확인한다."""
    _exe, app_path = _project(tmp_path)
    popen, calls = _fake_popen()
    marker = tmp_path / "marker.json"
    marker.write_text(
        json.dumps({"port": DEFAULT_PORT, "pid": 4828, "app_path": str(app_path)}),
        encoding="utf-8",
    )

    result = _launch(tmp_path, is_running=lambda port: True, popen=popen)

    assert result.status == "already-ours"
    assert result.port == DEFAULT_PORT
    assert calls == []  # 우리 것으로 확인됐으니 새로 안 띄운다


def test_checks_the_configured_port(tmp_path):
    seen_ports = []
    popen, _ = _fake_popen()
    _launch(
        tmp_path,
        port=9999,
        is_running=lambda port: seen_ports.append(port) or True,
        popen=popen,
    )
    # 9999가 남의 것으로 판정되면 대체 포트를 훑는다 — 첫 질문은 여전히 설정된 포트다.
    assert seen_ports[0] == 9999


def test_skips_when_streamlit_exe_missing(tmp_path):
    popen, calls = _fake_popen()
    (tmp_path / "src" / "messiah" / "ui").mkdir(parents=True)
    (tmp_path / "src" / "messiah" / "ui" / "app.py").write_text("")
    result = _launch(
        tmp_path,
        streamlit_exe=tmp_path / "nope.exe",
        is_running=lambda port: False,
        popen=popen,
    )
    assert result.process is None
    assert calls == []


def test_skips_when_app_path_missing(tmp_path):
    popen, calls = _fake_popen()
    exe = tmp_path / "streamlit.exe"
    exe.write_text("stub")
    result = _launch(
        tmp_path,  # src/messiah/ui/app.py 없음
        streamlit_exe=exe,
        is_running=lambda port: False,
        popen=popen,
    )
    assert result.process is None
    assert calls == []


def test_launches_when_not_running_and_files_exist(tmp_path):
    exe, app_path = _project(tmp_path)
    popen, calls = _fake_popen(pid=1234)

    result = _launch(
        tmp_path,
        log_path=tmp_path / "logs" / "ui.log",
        streamlit_exe=exe,
        is_running=lambda port: False,
        popen=popen,
    )

    assert result.process is not None
    assert result.process.pid == 1234
    assert result.port == DEFAULT_PORT
    assert result.status == "launched"
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == [str(exe), "run", str(app_path), "--server.port", str(DEFAULT_PORT)]
    assert kwargs["cwd"] == str(tmp_path)
    assert (tmp_path / "logs" / "ui.log").exists()  # 로그 디렉터리까지 만들어짐


# ------------------------------------------- 포트 점유자 신원 확인 (2026-08-11 F-6)


def test_a_foreign_holder_pushes_the_ui_to_a_fallback_port(tmp_path):
    """**이것이 F-6의 요점이다.** 2026-07-29엔 남의 Streamlit이 포트를 선점했고 우리는
    경고만 남기고 물러났다 — 그 결과가 하루치 무화면이다. 이제 옆 포트로 뜬다."""
    exe, app_path = _project(tmp_path)
    popen, calls = _fake_popen()

    result = _launch(
        tmp_path,
        streamlit_exe=exe,
        is_running=lambda port: port == DEFAULT_PORT,  # 8511만 남이 점유
        popen=popen,
    )

    assert result.status == "launched"
    assert result.port == DEFAULT_PORT + 1
    args, _kwargs = calls[0]
    assert args[0][-1] == str(DEFAULT_PORT + 1)  # 실제로 그 포트로 띄웠다


def test_all_ports_taken_reports_foreign_and_does_not_launch(tmp_path):
    """전부 막혔으면 화면은 못 뜬다 — 그 사실을 `foreign`으로 말한다(조용히 성공한 척 금지)."""
    exe, _app_path = _project(tmp_path)
    popen, calls = _fake_popen()

    result = _launch(tmp_path, streamlit_exe=exe, is_running=lambda port: True, popen=popen)

    assert result.status == "foreign"
    assert result.process is None
    assert calls == []


def test_a_successful_launch_leaves_a_marker_for_the_next_process(tmp_path):
    """흔적이 없으면 다음 기동이 자기 UI를 남의 것으로 오판한다 — 이 파일이 그 연결고리다."""
    exe, app_path = _project(tmp_path)
    popen, _calls = _fake_popen(pid=777)
    marker = tmp_path / "marker.json"

    _launch(tmp_path, streamlit_exe=exe, is_running=lambda port: False, popen=popen)

    written = json.loads(marker.read_text(encoding="utf-8"))
    assert written["port"] == DEFAULT_PORT
    assert written["pid"] == 777
    assert written["app_path"] == str(app_path)


def test_identification_rejects_a_marker_from_another_checkout(tmp_path):
    """같은 PC의 다른 체크아웃이 8511을 쓰고 있으면 그건 우리 화면이 아니다."""
    marker = tmp_path / "marker.json"
    marker.write_text(
        json.dumps({"port": DEFAULT_PORT, "app_path": r"C:\elsewhere\app.py"}),
        encoding="utf-8",
    )

    assert not identify_port_holder(DEFAULT_PORT, marker_path=marker, app_path=tmp_path / "app.py")


def test_a_broken_marker_is_treated_as_no_marker(tmp_path):
    """깨진 JSON 하나로 기동이 막히면 안 된다 — 판정의 보조 근거일 뿐이다."""
    marker = tmp_path / "marker.json"
    marker.write_text("{{{ not json", encoding="utf-8")

    assert not identify_port_holder(DEFAULT_PORT, marker_path=marker, app_path=tmp_path / "app.py")


def test_default_port_constant_is_messiah_dedicated_not_streamlit_default():
    # Streamlit 자체 기본값(8501)을 그대로 쓰면 로컬의 다른 Streamlit 프로젝트와 겹칠 수
    # 있다(2026-07-29 실측: 다른 프로젝트가 8501을 선점해 이 UI가 하루 종일 안 뜬 사례) —
    # MESSIAH 전용 고정값이어야 한다.
    assert DEFAULT_PORT == 8511


def test_launches_with_explicit_server_port_arg(tmp_path):
    exe, app_path = _project(tmp_path)
    popen, calls = _fake_popen()

    _launch(
        tmp_path,
        port=9999,
        streamlit_exe=exe,
        is_running=lambda port: False,
        popen=popen,
    )

    args, _ = calls[0]
    assert args[0] == [str(exe), "run", str(app_path), "--server.port", "9999"]


def test_launch_failure_is_caught_and_reports_no_process(tmp_path):
    exe, _app_path = _project(tmp_path)

    def _raising_popen(*args, **kwargs):
        raise OSError("boom")

    result = _launch(
        tmp_path, streamlit_exe=exe, is_running=lambda port: False, popen=_raising_popen
    )
    assert result.process is None
    assert result.status == "failed"


# ---------------------------------------------------------------- UI 생존 감시 (2026-07-30)


class _StopLoop(Exception):
    """무한 감시 루프를 테스트에서 끊기 위한 신호 — 프로덕션 코드는 이 예외를 모른다."""


class _Ticker:
    """주입된 sleep 대역 — 매 호출마다 정해둔 부수효과(UI 사망 등)를 일으키고, 준비된
    행동이 끝나면 _StopLoop으로 루프를 끊는다."""

    def __init__(self, *actions):
        self._actions = list(actions)
        self.calls = 0

    async def __call__(self, _seconds: float) -> None:
        if self.calls >= len(self._actions):
            raise _StopLoop
        self._actions[self.calls]()
        self.calls += 1


def _port_state(alive: bool = True):
    """포트 응답 여부를 상태로 모델링 — launch_command_center()도 내부에서 같은 콜러블을
    한 번 더 부르므로, 호출 횟수 기반 시퀀스보다 상태 기반이 견고하다."""
    state = {"alive": alive}
    return state, lambda port: state["alive"]


def _reviving_popen(state, pid: int = 4242):
    """기동에 성공하면 포트가 다시 응답하게 되는 실제 동작을 재현."""
    calls = []

    def _popen(*args, **kwargs):
        calls.append((args, kwargs))
        state["alive"] = True
        proc = subprocess.Popen.__new__(subprocess.Popen)
        proc.pid = pid  # type: ignore[misc]
        return proc

    return _popen, calls


def _launchable_project(tmp_path):
    """`launch_command_center()`가 실제로 기동 단계까지 가려면 실행파일·앱 경로가 존재해야
    한다 — 감시 루프 자체를 보는 테스트라 둘 다 빈 스텁으로 만든다."""
    exe = tmp_path / "streamlit.exe"
    exe.write_text("stub")
    app_dir = tmp_path / "src" / "messiah" / "ui"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "app.py").write_text("")
    return exe


async def test_watcher_returns_immediately_when_skip_env_set(tmp_path, monkeypatch):
    monkeypatch.setenv("MESSIAH_SKIP_UI", "1")
    ticker = _Ticker()
    state, is_running = _port_state()
    popen, calls = _reviving_popen(state)

    await watch_command_center_forever(
        caller_tag="test",
        project_root=tmp_path,
        log_path=tmp_path / "ui.log",
        marker_path=tmp_path / "marker.json",
        is_running=is_running,
        popen=popen,
        sleep=ticker,
    )

    assert ticker.calls == 0
    assert calls == []


async def test_watcher_does_not_touch_a_healthy_ui(tmp_path):
    state, is_running = _port_state(alive=True)
    popen, calls = _reviving_popen(state)
    ticker = _Ticker(lambda: None, lambda: None, lambda: None)

    with pytest.raises(_StopLoop):
        await watch_command_center_forever(
            caller_tag="test",
            project_root=tmp_path,
            log_path=tmp_path / "ui.log",
            marker_path=tmp_path / "marker.json",
            is_running=is_running,
            popen=popen,
            sleep=ticker,
        )

    assert calls == []  # 살아있는 동안엔 아무 것도 안 한다


async def test_watcher_relaunches_after_the_ui_dies(tmp_path):
    """2026-07-30 08:57 회귀 방지 — UI가 죽으면 32분씩 방치되지 않고 다음 점검에서 살아난다."""
    exe = _launchable_project(tmp_path)
    state, is_running = _port_state(alive=True)
    popen, calls = _reviving_popen(state)
    # 1틱: 정상 → 2틱 직전 UI 사망 → 2틱에서 감지·재기동 → 3틱: 다시 정상
    ticker = _Ticker(lambda: None, lambda: state.__setitem__("alive", False), lambda: None)

    with pytest.raises(_StopLoop):
        await watch_command_center_forever(
            caller_tag="test",
            project_root=tmp_path,
            log_path=tmp_path / "ui.log",
            marker_path=tmp_path / "marker.json",
            streamlit_exe=exe,
            is_running=is_running,
            popen=popen,
            sleep=ticker,
        )

    assert len(calls) == 1
    assert state["alive"] is True


def _never_reviving_popen():
    """기동해도 포트가 안 살아나는 크래시 루프 재현."""
    dead_calls = []

    def _popen(*args, **kwargs):
        dead_calls.append(args)
        proc = subprocess.Popen.__new__(subprocess.Popen)
        proc.pid = 1  # type: ignore[misc]
        return proc

    return _popen, dead_calls


async def test_watcher_stops_relaunching_after_restart_limit(tmp_path):
    """고쳐지지 않는 크래시 루프를 무한 반복하지 않는다 — 한도를 넘으면 재기동을 접되,
    조용히가 아니라 ERROR 로그를 남긴다(태그 CommandCenterUIRestartGaveUp)."""
    exe = _launchable_project(tmp_path)
    popen, dead_calls = _never_reviving_popen()
    ticker = _Ticker(*[lambda: None] * 8)  # 포기 이후에도 몇 틱 더 돈다

    with pytest.raises(_StopLoop):
        await watch_command_center_forever(
            caller_tag="test",
            project_root=tmp_path,
            log_path=tmp_path / "ui.log",
            marker_path=tmp_path / "marker.json",
            max_restarts=3,
            streamlit_exe=exe,
            is_running=lambda port: False,
            popen=popen,
            sleep=ticker,
            monotonic=lambda: 0.0,  # 창이 절대 안 지나감 — 한도가 계속 유효
        )

    assert len(dead_calls) == 3  # 한도만큼만 시도한다


async def test_watcher_keeps_reporting_after_it_gives_up(tmp_path):
    """2026-07-31 회귀 — 예전엔 포기하면서 **반환**해버려, 그 뒤로는 화면이 없다는 사실조차
    아무도 다시 말해주지 않았다(12:35~15:35 3시간). 재기동만 접고 관측은 계속해야 한다."""
    exe = _launchable_project(tmp_path)
    popen, dead_calls = _never_reviving_popen()
    reports: list[int] = []
    ticker = _Ticker(*[lambda: None] * 8)

    async def _on_gave_up() -> None:
        reports.append(1)

    with pytest.raises(_StopLoop):  # 반환이 아니라 루프 소진으로 끝나야 한다
        await watch_command_center_forever(
            caller_tag="test",
            project_root=tmp_path,
            log_path=tmp_path / "ui.log",
            marker_path=tmp_path / "marker.json",
            max_restarts=2,
            streamlit_exe=exe,
            is_running=lambda port: False,
            popen=popen,
            sleep=ticker,
            monotonic=lambda: 0.0,
            on_gave_up=_on_gave_up,
        )

    assert len(dead_calls) == 2
    assert len(reports) >= 5  # 포기 이후 매 점검마다 계속 알린다


async def test_restart_limit_is_a_rolling_window_not_a_daily_total(tmp_path):
    """2026-07-31 회귀 — 그날 크래시는 10:42~12:35에 6번, 즉 **약 2시간에 걸쳐** 났다.
    하루 누적 한도였기 때문에 소진됐지, 1시간 창이었다면 소진되지 않았다."""
    exe = _launchable_project(tmp_path)
    popen, dead_calls = _never_reviving_popen()
    clock = {"t": 0.0}
    # 매 점검마다 30분씩 흐른다 — 1시간 창이면 항상 최근 재기동은 2회 이하로 유지된다
    ticker = _Ticker(*[lambda: clock.__setitem__("t", clock["t"] + 1800.0)] * 6)

    with pytest.raises(_StopLoop):
        await watch_command_center_forever(
            caller_tag="test",
            project_root=tmp_path,
            log_path=tmp_path / "ui.log",
            marker_path=tmp_path / "marker.json",
            max_restarts=3,
            restart_window_seconds=3600.0,
            streamlit_exe=exe,
            is_running=lambda port: False,
            popen=popen,
            sleep=ticker,
            monotonic=lambda: clock["t"],
        )

    assert len(dead_calls) == 6  # 한 번도 포기하지 않았다


async def _noop_sleep(_seconds: float) -> None:
    return None
