"""ui_launcher 검증 — 실제 streamlit·실제 소켓 없이 전부 주입된 페이크로 확인."""

from __future__ import annotations

import subprocess

from messiah.core.ui_launcher import DEFAULT_PORT, launch_command_center


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
