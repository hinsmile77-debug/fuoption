"""Command Center UI(Streamlit) 백그라운드 기동 — `run_l1_daily.py`/`run_g2_paper_trading.py`
공용 (2026-07-29 통합, 2026-07-30 중복기동 방지 보강).

데이터 수집·페이퍼 트레이딩(호출 프로세스)과 화면은 완전히 분리된 별도 프로세스다 — UI
기동 실패가 호출자의 본 임무를 막지 않는다(부가 기능이 전제조건이 되지 않는다, `core/
docker_bootstrap.py`와 같은 설계 철학).

## 중복 기동 방지

두 스크립트 다 이 모듈을 쓰므로, 하나가 이미 띄운 UI가 살아있는 동안 다른 하나가 또
띄우려는 상황이 실제로 생길 수 있다(예: L1 데일리 운영 중 사람이 G2도 수동 실행) — 실측
결과 Streamlit/Windows는 이미 점유된 포트에 두 번째 프로세스가 바인드를 시도해도 에러 없이
그냥 진행되고, 두 프로세스가 동시에 같은 포트에서 LISTENING 상태로 남는 상황이 실제로
재현됐다(2026-07-29 실측 — 어느 쪽이 실제 요청을 받는지 예측 불가능해짐, 자원만 두 배로
낭비). 그래서 기동 전 포트 응답 여부를 먼저 확인해 이미 떠 있으면 새로 안 띄운다.

## MESSIAH 전용 고정 포트 (2026-07-29 오전 실측 사고, 2026-07-29 [MW0601] 수정)

`DEFAULT_PORT`는 원래 Streamlit 자체의 기본값(8501)을 그대로 썼다 — 그런데 이 PC에서
`localhost:8501`을 쓰는 건 MESSIAH만이 아니다: 같은 날 아침 이 스크립트가 "이미 응답
중(포트 8501) — 중복 기동 생략"으로 판단해 자기 UI를 하루 종일 못 띄웠는데, 실측 결과
그 포트를 점유하고 있던 건 MESSIAH의 UI가 아니라 **완전히 다른 프로젝트**(`PycharmProjects/
options`)의 Streamlit이었다 — 그 프로젝트도 포트를 지정하지 않아 Streamlit 기본값을 그대로
썼을 뿐이다. `is_ui_already_running()`은 애초부터 "어떤 프로세스든 상관 안 함"을 설계
원칙으로 삼았기 때문에(바로 아래 문단) 이 상황 자체는 예상된 트레이드오프였지만, 그 대가가
"MESSIAH 자신의 화면이 조용히 하루 종일 안 뜬다"로 실제 발생했다. 근본 대응은 애초에 남들과
공유하는 Streamlit 기본값을 안 쓰는 것 — `DEFAULT_PORT`를 8511(MESSIAH 전용, 표준 Streamlit
기본값과 겹치지 않는 임의 고정값)로 바꾸고 `launch_command_center()`가 `streamlit run`에
`--server.port`로 명시 전달한다. 이제 "충돌"은 MESSIAH의 두 스크립트끼리(위 문단)만 정상
시나리오로 남고, 제3의 무관한 프로젝트와 우연히 겹칠 확률은 사실상 사라진다(단, 그 프로젝트가
하필 8511을 쓰면 여전히 같은 클래스의 문제가 재발할 수 있음 — 근본적으로 포트 네임스페이스가
전역 공유 자원이라는 한계 자체는 해소되지 않는다).

## 테스트 용이성

`core/docker_bootstrap.py`와 같은 원칙 — `is_running`/`popen` 콜러블을 주입받아 실제
streamlit·실제 소켓 없이 순수하게 테스트 가능.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Awaitable, Callable

from messiah.core import logging as mlog

DEFAULT_PORT = 8511  # MESSIAH 전용 고정 포트 — Streamlit 기본값(8501)과 의도적으로 다름
DEFAULT_SKIP_ENV_VAR = "MESSIAH_SKIP_UI"

# 감시 주기 — UI가 죽은 걸 늦어도 30초 안에 알아채면 사람이 화면을 보고 이상을 느끼기 전에
# 대개 복구된다. 재기동 한도는 "고쳐지지 않는 크래시 루프"를 무한 반복하지 않기 위한 것으로,
# 한도를 넘으면 재기동을 포기하고 ERROR로 남긴다(조용히 멈추지 않는다).
_UI_CHECK_INTERVAL_SECONDS = 30.0
_UI_MAX_RESTARTS = 5
# 한도를 세는 창 (2026-07-31 추가) — 예전엔 **하루 누적 절대치**였다. 2026-07-31엔 10:42~12:35
# 사이에 UI가 6번 네이티브 크래시했고 12:35:53에 한도가 소진되자 **그날 다시는 안 떴다**
# (12:35~15:35, 3시간 무화면). 한도의 목적은 "고칠 수 없는 크래시 루프를 무한 반복하지 않기"
# 인데, 그건 "짧은 시간에 반복될 때"의 이야기지 "오전에 다섯 번 죽었으니 오후엔 포기한다"가
# 아니다. 창 밖으로 밀려난 재기동은 잊는다.
_UI_RESTART_WINDOW_SECONDS = 3600.0


def is_ui_already_running(
    port: int = DEFAULT_PORT, *, host: str = "localhost", timeout: float = 1.0
) -> bool:
    """포트가 응답하면(연결 성공) 이미 뭔가 떠 있는 것으로 판단 — 어떤 프로세스가 띄웠는지는
    상관하지 않는다(다른 프로젝트가 우연히 같은 포트를 쓰고 있어도 "이미 뭔가 있다"는 신호로는
    충분히 보수적인 선택 — `DEFAULT_PORT`를 MESSIAH 전용값으로 고정한 것도 바로 이 자체를
    없애기보다 "겹칠 확률"을 낮추는 완화책일 뿐이라는 점에 유의)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def launch_command_center(
    *,
    caller_tag: str,
    project_root: Path,
    log_path: Path,
    port: int = DEFAULT_PORT,
    streamlit_exe: Path | None = None,
    skip_env_var: str = DEFAULT_SKIP_ENV_VAR,
    is_running: Callable[[int], bool] = is_ui_already_running,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> subprocess.Popen | None:
    """`None`은 "새로 띄운 프로세스가 없다"는 뜻이다 — 생략(환경변수·이미 실행 중)과
    실패(실행파일 없음·기동 예외) 세 경우를 구분하지 않는다. 호출자 입장에서는 셋 다 "화면을
    새로 열 필요가 없다"는 점에서 동일하기 때문(메시지는 각 경우마다 다르게 출력해 사람은
    구분 가능)."""
    if os.environ.get(skip_env_var) == "1":
        print(f"[{caller_tag}] {skip_env_var}=1 — Command Center UI 기동 생략", flush=True)
        return None

    if is_running(port):
        print(
            f"[{caller_tag}] WARN: 포트 {port}에 이미 무언가 응답 중 — Command Center UI "
            f"기동 생략. MESSIAH 전용 포트라 다른 프로젝트와 겹칠 확률은 낮지만, 응답 중인 "
            f"프로세스가 실제로 MESSIAH UI인지는 확인하지 않는다 — 화면이 예상과 다르면 "
            f"http://localhost:{port} 을 직접 열어 확인할 것 (2026-07-29 다른 프로젝트의 "
            f"Streamlit이 구 기본 포트 8501을 선점해 이 UI가 하루 종일 안 뜬 사례 실측).",
            flush=True,
        )
        return None

    exe = streamlit_exe or (Path(sys.executable).parent / "streamlit.exe")
    app_path = project_root / "src" / "messiah" / "ui" / "app.py"
    if not exe.exists() or not app_path.exists():
        print(
            f"[{caller_tag}] Streamlit 실행파일/앱을 찾을 수 없어 UI 생략 "
            f"({exe} / {app_path}) — 본 작업은 계속 진행",
            flush=True,
        )
        return None

    log_path.parent.mkdir(parents=True, exist_ok=True)
    # `with`로 감싸 **성공 경로에서도** 닫는다(2026-08-03 수정). 예전엔 실패 경로에서만
    # 닫아서, 재기동할 때마다 이 프로세스에 파일 핸들이 하나씩 남았다 — 08-03에 2개, 크래시가
    # 잦은 날엔 더. 자식은 `Popen`이 fd를 이미 복제해 갔으므로 부모가 닫아도 자식의 로그
    # 쓰기에는 영향이 없다(수집 프로세스는 이 파일에 직접 쓰지 않는다).
    try:
        with open(log_path, "a", encoding="utf-8") as log_file:
            process = popen(
                [str(exe), "run", str(app_path), "--server.port", str(port)],
                cwd=str(project_root),
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
    except OSError as e:
        print(f"[{caller_tag}] Streamlit 기동 실패(본 작업은 계속): {e}", flush=True)
        return None

    print(
        f"[{caller_tag}] Command Center UI 기동(PID={process.pid}) — "
        f"http://localhost:{port} (로그: {log_path})",
        flush=True,
    )
    return process


async def watch_command_center_forever(
    *,
    caller_tag: str,
    project_root: Path,
    log_path: Path,
    port: int = DEFAULT_PORT,
    check_interval_seconds: float = _UI_CHECK_INTERVAL_SECONDS,
    max_restarts: int = _UI_MAX_RESTARTS,
    restart_window_seconds: float = _UI_RESTART_WINDOW_SECONDS,
    streamlit_exe: Path | None = None,
    skip_env_var: str = DEFAULT_SKIP_ENV_VAR,
    is_running: Callable[[int], bool] = is_ui_already_running,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    on_gave_up: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """UI가 죽으면 다시 띄운다 (2026-07-30 사고 대응).

    `launch_command_center()`는 프로세스를 띄우고 나면 그 뒤를 아무도 안 봤다. 2026-07-30
    08:57:05에 UI가 네이티브 크래시(access violation, `data/archiver.py` 모듈 docstring 참고)로
    죽었는데 **32분간 아무 로그도 알림도 재기동도 없었고**, 사용자가 브라우저의 "Connection
    error"를 보고서야 발견했다. 크래시의 근본 원인은 따로 고쳤지만(원자적 쓰기 + 읽기 방어층),
    "UI가 어떤 이유로든 죽으면 조용히 사라진다"는 구조 자체가 문제라 여기서 별도로 막는다.

    수집 프로세스의 본 임무를 절대 막지 않는다 — 이 코루틴은 예외를 밖으로 내지 않는다
    (호출측 `asyncio.gather`를 죽이지 않는다).

    ## 한도의 의미가 바뀌었다 (2026-07-31 수정)

    **① 한도는 하루 누적이 아니라 최근 `restart_window_seconds` 안의 횟수다.** 그 전에는
    절대치라, 오전에 크래시가 몰리면 오후 내내 화면이 안 떴다 — 2026-07-31엔 10:42~12:35에
    6번 죽어 12:35:53에 한도가 소진됐고 그날 다시는 안 떴다(3시간 무화면). 창 밖으로 밀려난
    재기동은 잊으므로, 같은 날의 크래시 폭풍이 지나가면 자동으로 다시 시도한다.

    **② 한도를 넘어도 반환하지 않는다 — 재기동만 접고 관측은 계속한다.** "감시를 접는다"는
    원래 의도는 "고칠 수 없는 크래시 루프를 무한 반복하지 않는다"였지 "이 프로세스가 UI를
    영영 안 본다"가 아니었다. 반환해 버리면 그 뒤로는 UI가 죽어 있다는 사실조차 아무도 다시
    말해주지 않는다(침묵도 상태다 — `core/health.py`). 매 점검마다 `on_gave_up`을 호출해
    "여전히 화면이 없다"를 계속 알리고, 사람이 수동으로 띄우면 그때부터 정상 감시로 복귀한다.

    `on_gave_up` 훅이 있는 이유도 같다 — 포기 사실을 로그로만 남기면 **그 로그를 볼 화면이
    바로 그 죽은 UI라서** 아무에게도 안 닿는다(2026-07-31에 실제로 그랬다).

    포트 응답만 보고 판단하는 한계는 `is_ui_already_running()`과 동일하다 — 제3의 프로세스가
    8511을 선점하면 "살아있다"로 오판해 재기동을 안 한다. 그 경우는 원래도 UI가 안 뜨는
    상황이고 `launch_command_center()`가 기동 시점에 이미 WARN을 찍어둔다.
    """
    if os.environ.get(skip_env_var) == "1":
        return

    recent_restarts: deque[float] = deque()
    reported_give_up = False
    while True:
        await sleep(check_interval_seconds)
        if is_running(port):
            reported_give_up = False  # 사람이 수동으로 띄웠다 — 다음 사망은 다시 알린다
            continue

        now = monotonic()
        while recent_restarts and now - recent_restarts[0] > restart_window_seconds:
            recent_restarts.popleft()

        if len(recent_restarts) >= max_restarts:
            if not reported_give_up:
                mlog.log(
                    "CommandCenterUIRestartGaveUp",
                    f"Command Center UI 자동 재기동 {max_restarts}회"
                    f"({restart_window_seconds / 60:.0f}분 내) 소진 — 재기동 중단, 수동 확인 필요",
                    port=port,
                    caller=caller_tag,
                    window_seconds=restart_window_seconds,
                )
                reported_give_up = True
            if on_gave_up is not None:
                await on_gave_up()
            continue  # 재기동은 접되 **관측은 계속한다**(아래 docstring 근거)

        reported_give_up = False
        recent_restarts.append(now)
        mlog.log(
            "CommandCenterUIDown",
            f"포트 {port} 무응답 — Command Center UI 재기동 시도 "
            f"({len(recent_restarts)}/{max_restarts}, 최근 "
            f"{restart_window_seconds / 60:.0f}분 기준)",
            port=port,
            caller=caller_tag,
        )
        process = launch_command_center(
            caller_tag=caller_tag,
            project_root=project_root,
            log_path=log_path,
            port=port,
            streamlit_exe=streamlit_exe,
            skip_env_var=skip_env_var,
            is_running=is_running,
            popen=popen,
        )
        if process is not None:
            mlog.log(
                "CommandCenterUIRestarted",
                "Command Center UI 재기동 완료",
                port=port,
                pid=process.pid,
                caller=caller_tag,
            )
