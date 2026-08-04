"""ui_launcher 검증 — 실제 streamlit·실제 소켓 없이 전부 주입된 페이크로 확인."""

from __future__ import annotations

import subprocess

import pytest

from messiah.core.ui_launcher import (
    DEFAULT_PORT,
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


def test_skips_when_env_var_set(tmp_path, monkeypatch):
    monkeypatch.setenv("MESSIAH_SKIP_UI", "1")
    popen, calls = _fake_popen()
    result = launch_command_center(
        caller_tag="test",
        project_root=tmp_path,
        log_path=tmp_path / "ui.log",
        is_running=lambda port: False,
        popen=popen,
    )
    assert result is None
    assert calls == []


def test_skips_when_already_running(tmp_path):
    popen, calls = _fake_popen()
    result = launch_command_center(
        caller_tag="test",
        project_root=tmp_path,
        log_path=tmp_path / "ui.log",
        is_running=lambda port: True,
        popen=popen,
    )
    assert result is None
    assert calls == []  # 이미 떠 있으니 새로 안 띄운다


def test_checks_the_configured_port(tmp_path):
    seen_ports = []
    popen, _ = _fake_popen()
    launch_command_center(
        caller_tag="test",
        project_root=tmp_path,
        log_path=tmp_path / "ui.log",
        port=9999,
        is_running=lambda port: seen_ports.append(port) or True,
        popen=popen,
    )
    assert seen_ports == [9999]


def test_skips_when_streamlit_exe_missing(tmp_path):
    popen, calls = _fake_popen()
    (tmp_path / "src" / "messiah" / "ui").mkdir(parents=True)
    (tmp_path / "src" / "messiah" / "ui" / "app.py").write_text("")
    result = launch_command_center(
        caller_tag="test",
        project_root=tmp_path,
        log_path=tmp_path / "ui.log",
        streamlit_exe=tmp_path / "nope.exe",
        is_running=lambda port: False,
        popen=popen,
    )
    assert result is None
    assert calls == []


def test_skips_when_app_path_missing(tmp_path):
    popen, calls = _fake_popen()
    exe = tmp_path / "streamlit.exe"
    exe.write_text("stub")
    result = launch_command_center(
        caller_tag="test",
        project_root=tmp_path,  # src/messiah/ui/app.py 없음
        log_path=tmp_path / "ui.log",
        streamlit_exe=exe,
        is_running=lambda port: False,
        popen=popen,
    )
    assert result is None
    assert calls == []


def test_launches_when_not_running_and_files_exist(tmp_path):
    exe = tmp_path / "streamlit.exe"
    exe.write_text("stub")
    app_dir = tmp_path / "src" / "messiah" / "ui"
    app_dir.mkdir(parents=True)
    app_path = app_dir / "app.py"
    app_path.write_text("")
    popen, calls = _fake_popen(pid=1234)

    result = launch_command_center(
        caller_tag="test",
        project_root=tmp_path,
        log_path=tmp_path / "logs" / "ui.log",
        streamlit_exe=exe,
        is_running=lambda port: False,
        popen=popen,
    )

    assert result is not None
    assert result.pid == 1234
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == [str(exe), "run", str(app_path), "--server.port", str(DEFAULT_PORT)]
    assert kwargs["cwd"] == str(tmp_path)
    assert (tmp_path / "logs" / "ui.log").exists()  # 로그 디렉터리까지 만들어짐


def test_default_port_constant_is_messiah_dedicated_not_streamlit_default():
    # Streamlit 자체 기본값(8501)을 그대로 쓰면 로컬의 다른 Streamlit 프로젝트와 겹칠 수
    # 있다(2026-07-29 실측: 다른 프로젝트가 8501을 선점해 이 UI가 하루 종일 안 뜬 사례) —
    # MESSIAH 전용 고정값이어야 한다.
    assert DEFAULT_PORT == 8511


def test_launches_with_explicit_server_port_arg(tmp_path):
    exe = tmp_path / "streamlit.exe"
    exe.write_text("stub")
    app_dir = tmp_path / "src" / "messiah" / "ui"
    app_dir.mkdir(parents=True)
    app_path = app_dir / "app.py"
    app_path.write_text("")
    popen, calls = _fake_popen()

    launch_command_center(
        caller_tag="test",
        project_root=tmp_path,
        log_path=tmp_path / "ui.log",
        port=9999,
        streamlit_exe=exe,
        is_running=lambda port: False,
        popen=popen,
    )

    args, _ = calls[0]
    assert args[0] == [str(exe), "run", str(app_path), "--server.port", "9999"]


def test_launch_failure_is_caught_and_returns_none(tmp_path):
    exe = tmp_path / "streamlit.exe"
    exe.write_text("stub")
    app_dir = tmp_path / "src" / "messiah" / "ui"
    app_dir.mkdir(parents=True)
    (app_dir / "app.py").write_text("")

    def _raising_popen(*args, **kwargs):
        raise OSError("boom")

    result = launch_command_center(
        caller_tag="test",
        project_root=tmp_path,
        log_path=tmp_path / "ui.log",
        streamlit_exe=exe,
        is_running=lambda port: False,
        popen=_raising_popen,
    )
    assert result is None


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
