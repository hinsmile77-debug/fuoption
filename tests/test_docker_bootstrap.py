"""docker_bootstrap 검증 — 실제 docker CLI·실제 대기 없이 전부 주입된 페이크로 확인."""

from __future__ import annotations

import subprocess

import pytest

from messiah.core.docker_bootstrap import (
    ensure_container_running,
    ensure_docker_ready,
    is_docker_daemon_ready,
    launch_docker_desktop,
)


def _completed(returncode: int) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode)


def _runner(returncode: int):
    def _run(*args, **kwargs):
        return _completed(returncode)

    return _run


def _raising_runner(exc: Exception):
    def _run(*args, **kwargs):
        raise exc

    return _run


# ---------------------------------------------------------------- is_docker_daemon_ready


def test_is_docker_daemon_ready_true_on_returncode_zero():
    assert is_docker_daemon_ready(runner=_runner(0)) is True


def test_is_docker_daemon_ready_false_on_nonzero_returncode():
    assert is_docker_daemon_ready(runner=_runner(1)) is False


def test_is_docker_daemon_ready_false_on_timeout():
    timeout_exc = subprocess.TimeoutExpired("docker", 10)
    assert is_docker_daemon_ready(runner=_raising_runner(timeout_exc)) is False


def test_is_docker_daemon_ready_false_when_docker_cli_missing():
    assert is_docker_daemon_ready(runner=_raising_runner(FileNotFoundError())) is False


# ---------------------------------------------------------------- launch_docker_desktop


def test_launch_docker_desktop_calls_popen_when_exe_exists(tmp_path):
    exe = tmp_path / "Docker Desktop.exe"
    exe.write_text("stub")
    calls = []
    launch_docker_desktop(exe, popen=lambda *a, **k: calls.append((a, k)))
    assert len(calls) == 1
    assert calls[0][0][0] == [str(exe)]


def test_launch_docker_desktop_raises_when_exe_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        launch_docker_desktop(tmp_path / "nope.exe", popen=lambda *a, **k: None)


# ---------------------------------------------------------------- ensure_container_running


def test_ensure_container_running_true_on_success():
    assert ensure_container_running(runner=_runner(0)) is True


def test_ensure_container_running_false_on_failure():
    assert ensure_container_running(runner=_runner(1)) is False


# ---------------------------------------------------------------- ensure_docker_ready


def test_ensure_docker_ready_returns_immediately_when_already_running(tmp_path):
    launched = []
    result = ensure_docker_ready(
        exe_path=tmp_path / "unused.exe",
        runner=_runner(0),
        popen=lambda *a, **k: launched.append(a),
    )
    assert result.ready is True
    assert result.already_running is True
    assert result.waited_seconds == 0.0
    assert result.container_started is True
    assert launched == []  # 이미 떠 있으니 Docker Desktop을 다시 띄우지 않는다


def test_ensure_docker_ready_launches_and_waits_until_ready(tmp_path):
    exe = tmp_path / "Docker Desktop.exe"
    exe.write_text("stub")
    launched = []
    # 처음 두 번은 daemon 미준비(returncode 1), 세 번째부터 준비(returncode 0) — daemon
    # 체크 두 번(초기 1회 + 폴링 루프 1회) + 컨테이너 기동 확인 1회 = 총 3콜 순서로 설계.
    calls = {"n": 0}

    def runner(*args, **kwargs):
        calls["n"] += 1
        return _completed(0 if calls["n"] >= 3 else 1)

    fake_clock = {"t": 0.0}

    def now():
        return fake_clock["t"]

    def sleep(seconds):
        fake_clock["t"] += seconds

    result = ensure_docker_ready(
        exe_path=exe,
        runner=runner,
        popen=lambda *a, **k: launched.append(a),
        sleep=sleep,
        now=now,
        poll_interval_seconds=1.0,
        timeout_seconds=30.0,
    )
    assert result.ready is True
    assert result.already_running is False
    assert launched == [([str(exe)],)]  # 정확히 한 번만 기동 시도


def test_ensure_docker_ready_times_out_if_never_ready(tmp_path):
    exe = tmp_path / "Docker Desktop.exe"
    exe.write_text("stub")
    fake_clock = {"t": 0.0}

    result = ensure_docker_ready(
        exe_path=exe,
        runner=_runner(1),  # 영원히 미준비
        popen=lambda *a, **k: None,
        sleep=lambda s: fake_clock.__setitem__("t", fake_clock["t"] + s),
        now=lambda: fake_clock["t"],
        poll_interval_seconds=5.0,
        timeout_seconds=12.0,
    )
    assert result.ready is False
    assert result.container_started is False
