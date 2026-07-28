"""Docker Desktop 자가 기동 — Windows 전용, `run_l1_daily.py`/`run_g2_paper_trading.py`
공용 전처리 단계 (2026-07-29, Task Scheduler·Docker 점검 중 발견한 취약점 대응).

## 배경

MESSIAH는 Docker Desktop의 `AutoStart`를 켜두지 않는다 — 지금까지는 사용자 환경의 다른
프로젝트가 07:30경 자기 필요로 Docker Desktop을 띄워주는 우연에 실질적으로 기대고 있었다
(Task Scheduler 감사 중 실측으로 발견: `messiah-redis` 컨테이너의 `StartedAt`이 항상 그
시각 근방이었다). 그 다른 프로젝트가 그날 안 뜨면 `scripts/self_check.py`의 Redis 점검이
실패해 그날 전체 수집이 조용히 통째로 빠진다 — 실패 자체는 로그에 정직하게 남지만(자가점검
원칙, `self_check.py` 모듈 docstring), 사람이 그 로그를 그날 확인하지 않으면 아무도 모른다.

## 이 모듈의 책임

MESSIAH 자신의 기동 시퀀스 맨 앞에서 Docker daemon이 응답 가능한지 직접 확인하고, 아니면
Docker Desktop을 스스로 띄운 뒤 준비될 때까지 기다린다 — 다른 프로젝트의 스케줄에 기대지
않는다. `messiah-redis` 컨테이너는 `restart policy=unless-stopped`라 daemon이 뜨면 보통
자동으로 같이 뜨지만, `docker start`를 한 번 더 명시적으로 호출해 확실히 한다(이미 실행
중이면 무해한 no-op).

## 타임아웃 시 조용히 진행하지 않는다

`timeout_seconds` 안에 daemon이 준비되지 않으면 `DockerReadyResult.ready=False`를 반환할
뿐, 이 모듈 스스로 재시도하거나 예외를 삼키지 않는다 — 호출자(`run_l1_daily.py`)가 그
사실을 명시적으로 보고 중단해야 한다(L18 "폴백은 시끄럽게"와 같은 정신).

## 테스트 용이성

모든 함수가 `runner`/`popen`/`sleep`/`now` 콜러블을 주입받는다 — 실제 `docker` CLI나 실제
대기 없이 순수하게 테스트 가능(`core/scheduler.py`의 `FixedTickScheduler`와 같은 설계 원칙).
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

DEFAULT_DOCKER_DESKTOP_EXE = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")
DEFAULT_CONTAINER_NAME = "messiah-redis"

CommandRunner = Callable[..., "subprocess.CompletedProcess[bytes]"]


def is_docker_daemon_ready(
    *, timeout_seconds: float = 10.0, runner: CommandRunner = subprocess.run
) -> bool:
    """`docker info`가 성공(returncode 0)하면 daemon이 응답 가능한 상태."""
    try:
        result = runner(["docker", "info"], capture_output=True, timeout=timeout_seconds)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def launch_docker_desktop(
    exe_path: Path = DEFAULT_DOCKER_DESKTOP_EXE,
    *,
    popen: Callable[..., object] = subprocess.Popen,
) -> None:
    if not Path(exe_path).exists():
        raise FileNotFoundError(f"Docker Desktop 실행파일을 찾을 수 없음: {exe_path}")
    popen([str(exe_path)], close_fds=True)


def ensure_container_running(
    container_name: str = DEFAULT_CONTAINER_NAME,
    *,
    timeout_seconds: float = 15.0,
    runner: CommandRunner = subprocess.run,
) -> bool:
    """daemon이 이미 준비된 상태에서 호출 — `docker start`는 이미 실행 중인 컨테이너에도
    안전(no-op, returncode 0)."""
    try:
        result = runner(
            ["docker", "start", container_name], capture_output=True, timeout=timeout_seconds
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


@dataclass(frozen=True)
class DockerReadyResult:
    ready: bool
    already_running: bool
    waited_seconds: float
    container_started: bool


def ensure_docker_ready(
    *,
    exe_path: Path = DEFAULT_DOCKER_DESKTOP_EXE,
    container_name: str = DEFAULT_CONTAINER_NAME,
    poll_interval_seconds: float = 5.0,
    timeout_seconds: float = 120.0,
    runner: CommandRunner = subprocess.run,
    popen: Callable[..., object] = subprocess.Popen,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> DockerReadyResult:
    """이미 떠 있으면 즉시 반환(기동 시도 없음). 아니면 Docker Desktop을 띄우고
    `timeout_seconds`까지 `poll_interval_seconds` 간격으로 폴링 — 준비되면 컨테이너까지
    기동 확인 후 반환, 시간 초과하면 `ready=False`로 반환(호출자가 중단 여부를 결정)."""
    if is_docker_daemon_ready(runner=runner):
        started = ensure_container_running(container_name, runner=runner)
        return DockerReadyResult(
            ready=True, already_running=True, waited_seconds=0.0, container_started=started
        )

    launch_docker_desktop(exe_path, popen=popen)

    start = now()
    deadline = start + timeout_seconds
    while now() < deadline:
        if is_docker_daemon_ready(runner=runner):
            started = ensure_container_running(container_name, runner=runner)
            return DockerReadyResult(
                ready=True,
                already_running=False,
                waited_seconds=now() - start,
                container_started=started,
            )
        sleep(poll_interval_seconds)

    return DockerReadyResult(
        ready=False, already_running=False, waited_seconds=now() - start, container_started=False
    )
