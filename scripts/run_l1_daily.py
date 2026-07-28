"""L1 데이터 파이프라인 일일 운영 — 장전 웜업 → 장중 수집 → 장후 종료.

Master Plan Ver 2.0 §9 W6~8까지의 산출물(TickCollector·MultiHorizonBarComposer·
FeatureEngine)을 실제 매매일 하루 동안 무인으로 돌리기 위한 진입점. 지금까지는 전부
스크래치 스크립트로 손으로 실행해 검증했을 뿐, "장전에 대기하다 자동으로 켜지는" 운영
흐름 자체는 없었다 — 이 스크립트가 그 자리를 채운다.

시간대(KST, 전부 하드코딩 아님 — 아래 상수만 바꾸면 됨):
- 08:35(작업 스케줄러 "Messiah" 실제 트리거 시각) ~ 09:00: 웜업 — **Docker Desktop 응답
  확인/자동 기동**(아래 항목), self_check, 근월물 심볼 확인, Redis 연결, Collector/Composer/
  Engine 구성, **WS는 이 시점에 이미 연결·구독까지 끝내 둔다**(9시 정각에 연결부터 새로
  맺느라 첫 틱을 놓치지 않도록 — "첫봉 대기 준비완료" 요건). 실제로 틱이 오기 시작하는 건
  장이 열려야 하므로 별도의 "9시까지 대기" 로직은 필요 없다.
- 09:00~15:35: 정규장 수집(REGULAR_SESSION_STOP까지 run_forever() 3개를 동시 구동).
- 15:35 도달: 수집 중단 신호 → daily_close()(미완성 봉 flush·버스 종료) → 15:40
  HARD_SHUTDOWN_DEADLINE까지 끝내지 못하면 강제 종료(운영 사고 시 무한정 떠 있는 프로세스
  방지 — 안전판). 독립 안전망으로 "Messiah-Shutdown" 작업이 같은 15:40에 별도로 잔여
  프로세스를 정리(`stop_l1_daily.bat`).

**Docker Desktop 자동 기동 (2026-07-29 추가)**: `_ensure_docker_ready()`가 self_check보다
먼저 실행돼 Docker daemon 응답 여부를 확인하고, 안 뜬 상태면 스스로 Docker Desktop을 띄운
뒤 최대 2분 기다린다 — Task Scheduler·Docker 점검 중 "지금까지는 다른 프로젝트가 07:30경
띄워주는 우연에 기대고 있었다"(`AutoStart=False`)는 취약점을 발견해 대응(`core/
docker_bootstrap.py`). 2분 안에도 안 뜨면 self_check 실행 전에 명시적으로 중단한다.

**KRX 휴장일 인식 (2026-07-27 추가)**: `main()` 시작 직후 `EventCalendar.is_trading_day()`로
오늘이 거래일인지부터 확인한다 — 휴장일이면 self_check조차 실행하지 않고(불필요한 KIS API
호출 회피) 즉시 종료한다. 휴장일 목록은 `configs/krx_holidays.yaml`(출처 한계는 그 파일
헤더 참고 — 공식 KRX 확인 아님).

**Command Center UI 자동 기동 (2026-07-29 추가)**: 거래일로 확인되면 `_launch_ui()`가
Streamlit Command Center(`src/messiah/ui/app.py`)를 완전히 별도의 백그라운드 프로세스로
띄운다 — 데이터 수집(이 프로세스)과 화면은 서로 독립적이다(ui/app.py 모듈 docstring
"동일 인터페이스" 원칙과 별개로, 프로세스 수준에서도 분리). UI 기동 실패는 데이터 수집을
막지 않는다(부가 기능이지 전제조건이 아님 — L18 정신과 동일하게 실패를 조용히 삼키지 않고
로그에는 남긴다). `MESSIAH_SKIP_UI=1` 환경변수로 UI 기동을 생략 가능. UI 프로세스는 이
스크립트가 끝나도 계속 살아있다가 "Messiah-Shutdown" 워치독(`stop_l1_daily.bat`, 15:40)이
`run_l1_daily.py`와 같은 방식(명령줄 패턴 매칭)으로 함께 정리한다 — 매일 자정 없이 쌓이지
않는다.

**아직 없는 것**: 스캘러/모델 로딩(_load_warmup_artifacts, Phase 3 이후 실제 모델이 생기면
채울 자리만 미리 파둠), 옵션(K200_OPT) 동시 수집(오늘 세션 실측으로 같은 계좌 WS 연결을
2개 열면 서로 끊기는 문제 확인됨 — 별도 연결이 아니라 단일 연결·다중 subscribe()로 풀어야
하는 별도 작업, 이 스크립트는 선물 1개만).

사용: python scripts/run_l1_daily.py [--configs configs]
Windows 작업 스케줄러에 "Messiah"(평일 08:35, `run_l1_daily.bat`)로 실제 등록·가동 중
(2026-07-29 감사로 확인 — 등록 시점 자체는 불명확하나 로그상 최소 2026-07-27부터 매 거래일
정상 트리거·CRITICAL 0건·정상 종료 확인됨).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.broker.kis import symbol_master, tr_codes  # noqa: E402
from messiah.broker.kis.credentials import KISCredentials  # noqa: E402
from messiah.core import logging as mlog  # noqa: E402
from messiah.core.bus import MessageBus  # noqa: E402
from messiah.core.config import InstanceConfig, load_instance  # noqa: E402
from messiah.core.docker_bootstrap import (  # noqa: E402
    DEFAULT_DOCKER_DESKTOP_EXE,
    ensure_docker_ready,
)
from messiah.core.event_calendar import DEFAULT_SESSION, EventCalendar  # noqa: E402
from messiah.core.timeutil import now_kst  # noqa: E402
from messiah.core.ui_launcher import launch_command_center  # noqa: E402
from messiah.data.archiver import ParquetArchiver  # noqa: E402
from messiah.data.bar_composer import MultiHorizonBarComposer  # noqa: E402
from messiah.data.collector import TickCollector  # noqa: E402
from messiah.data.normalizer import parse_futures_tick  # noqa: E402
from messiah.features.engine import FeatureEngine  # noqa: E402

# 정규장 마감(연속거래 종료) 시각 — event_calendar.DEFAULT_SESSION과 같은 값을 직접
# 참조해 단일 소스를 유지한다(두 곳이 따로 하드코딩돼 있다가 어긋나는 사고 방지).
REGULAR_SESSION_STOP = (DEFAULT_SESSION.close_time.hour, DEFAULT_SESSION.close_time.minute)
HARD_SHUTDOWN_DEADLINE = (15, 40)  # daily_close()가 이 시각까지 못 끝내면 강제 종료(안전판)

_MASTER_CACHE_DIR = Path(".cache/kis_symbol_master")
_DATA_DIR = Path("data") / "bars"


def _today_at(reference_kst: datetime, hour: int, minute: int) -> datetime:
    return reference_kst.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _ensure_docker_ready() -> None:
    """Redis(`messiah-redis`)가 Docker Desktop 위에서 돈다 — 지금까지는 다른 프로젝트가
    07:30경 자기 필요로 Docker Desktop을 띄워주는 우연에 실질적으로 기대고 있었다(Task
    Scheduler·Docker 점검 중 2026-07-29 발견, `AutoStart=False`가 그 증거). 그 다른
    프로젝트가 그날 안 뜨면 뒤이은 `self_check`의 Redis 점검이 실패해 그날 수집 전체가
    조용히 빠진다 — 이 함수가 그 의존성을 없앤다: MESSIAH 스스로 Docker daemon 응답 여부를
    먼저 확인하고, 안 뜬 상태면 스스로 띄운 뒤 최대 2분 기다린다. 실행파일 경로는
    `MESSIAH_DOCKER_DESKTOP_EXE` 환경변수로 오버라이드 가능(SYSTEM.md R4 "하드코딩 금지"
    — 이 경로는 시크릿은 아니지만 PC마다 다를 수 있어 같은 경로로 뺐다)."""
    exe_path = Path(os.environ.get("MESSIAH_DOCKER_DESKTOP_EXE", str(DEFAULT_DOCKER_DESKTOP_EXE)))
    result = ensure_docker_ready(exe_path=exe_path)
    if not result.ready:
        raise SystemExit("Docker Desktop이 대기 시간 내에 준비되지 않음 — 기동 중단 (Ver 1.1 §7.3)")
    if not result.already_running:
        print(
            f"[run_l1_daily] Docker Desktop 자동 기동 완료 ({result.waited_seconds:.0f}초 대기)",
            flush=True,
        )


def _run_self_check(config_dir: str) -> None:
    """self_check.py는 "모든 프로세스는 이 점검을 통과해야만 수집을 개시한다"고 스스로
    선언한 컴포넌트다(scripts/self_check.py 참고) — 그 계약대로 서브프로세스로 호출해
    exit code로 기동 여부를 결정한다.

    encoding="utf-8"을 명시하지 않으면 subprocess.run(text=True)이 시스템 로캘(한글
    Windows는 cp949)로 디코딩을 시도한다 — self_check.py는 자기 stdout을 utf-8로
    reconfigure해 두는데(같은 패턴을 이 스크립트도 씀) 그 출력을 cp949로 읽으려다
    UnicodeDecodeError가 나는 걸 실측으로 확인(2026-07-24, run_l1_daily.bat 실행 중)."""
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "self_check.py"), "--configs", config_dir],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        raise SystemExit("self_check 실패 — 기동 중단 (Ver 1.1 §7.3)")


def _resolve_front_month_symbol() -> str:
    """계좌·토큰 무관한 정적 마스터파일 다운로드라 self_check의 secrets 점검과 별개로 항상
    시도 가능(probe_front_month()와 동일 근거, adapter.py 참고)."""
    master = symbol_master.load_index_derivatives_master(_MASTER_CACHE_DIR)
    symbol = master.front_month_future_code(product_type=symbol_master.PRODUCT_TYPE_MINI_FUTURES)
    if symbol is None:
        raise RuntimeError("미니선물 근월물 심볼 확인 실패 — 마스터파일에 해당 상품 없음")
    return symbol


def _load_warmup_artifacts() -> None:
    """스캘러·모델 로딩 자리 — Phase 3(W17~) 이전엔 실제 모델이 없어 지금은 아무 것도 안 함.
    호출 시점(웜업 구간, 09:00 이전)만 미리 정해 둔다."""
    return


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _launch_ui(today_str: str) -> subprocess.Popen | None:
    """`core/ui_launcher.py`의 얇은 래퍼 — 이 프로세스(데이터 수집)와 화면은 서로
    독립적이다. 중복 기동 방지(포트 응답 확인)는 공용 모듈이 담당한다(2026-07-30 추가,
    `run_g2_paper_trading.py`와 UI를 동시에 켰을 때의 실측 발견 대응)."""
    return launch_command_center(
        caller_tag="run_l1_daily",
        project_root=_PROJECT_ROOT,
        log_path=Path("logs") / f"ui_{today_str}.log",
    )


async def _run_regular_session(
    collector: TickCollector, composer: MultiHorizonBarComposer, engine: FeatureEngine
) -> None:
    await asyncio.gather(collector.run_forever(), composer.run_forever(), engine.run_forever())


async def _daily_close(
    collector: TickCollector, composer: MultiHorizonBarComposer, bus: MessageBus
) -> None:
    await collector.flush_final_bar()
    await composer.flush_all_final()
    await bus.close()


async def main(cfg: InstanceConfig) -> None:
    mlog.setup(cfg.instance_id)

    today = now_kst().date()
    if not EventCalendar.from_file().is_trading_day(today):
        print(
            f"{today.isoformat()}은 KRX 휴장일(Event Calendar) — 수집 생략, 즉시 종료",
            flush=True,
        )
        return

    _launch_ui(today.strftime("%Y%m%d"))

    creds = KISCredentials.from_broker_config(cfg.broker)
    symbol = await asyncio.to_thread(_resolve_front_month_symbol)
    tick_size = Decimal(cfg.futures_tick_size)
    print(f"근월물 심볼: {symbol} (tick_size={tick_size})", flush=True)

    _load_warmup_artifacts()

    bus = MessageBus(cfg.redis_url, cfg.instance_id)
    await bus.connect()

    archiver = ParquetArchiver(_DATA_DIR)
    collector = TickCollector(
        creds=creds,
        symbol=symbol,
        tr_id=tr_codes.WS_TR_FUTURES_CONTRACT,
        parse_tick=parse_futures_tick,
        tick_size=tick_size,
        archiver=archiver,
        bus=bus,
    )
    composer = MultiHorizonBarComposer(symbol=symbol, archiver=archiver, bus=bus)
    engine = FeatureEngine(symbol, bus, feature_set=cfg.feature_set)

    now = now_kst()
    session_stop = _today_at(now, *REGULAR_SESSION_STOP)
    hard_deadline = _today_at(now, *HARD_SHUTDOWN_DEADLINE)

    remaining = (session_stop - now_kst()).total_seconds()
    if remaining > 0:
        print(f"정규장 수집 시작 — {session_stop.isoformat()}까지 ({remaining:.0f}초)", flush=True)
        try:
            await asyncio.wait_for(
                _run_regular_session(collector, composer, engine), timeout=remaining
            )
        except TimeoutError:
            print("수집 중단 신호 도달 — 장후 종료 절차 시작", flush=True)
    else:
        print(f"이미 {session_stop.isoformat()} 이후 — 수집 생략, 바로 종료 절차", flush=True)

    shutdown_budget = max((hard_deadline - now_kst()).total_seconds(), 30.0)
    try:
        await asyncio.wait_for(_daily_close(collector, composer, bus), timeout=shutdown_budget)
    except TimeoutError:
        mlog.log(
            "DailyCloseTimeout",
            f"daily_close()가 {shutdown_budget:.0f}초 내에 못 끝남 — 강제 종료",
        )
        raise SystemExit(1) from None

    print("정상 종료.", flush=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MESSIAH L1 일일 수집 진입점")
    parser.add_argument("--configs", default="configs")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    _ensure_docker_ready()
    _run_self_check(args.configs)
    instance_cfg = load_instance(args.configs)
    asyncio.run(main(instance_cfg))
