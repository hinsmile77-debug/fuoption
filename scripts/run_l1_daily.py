"""L1 데이터 파이프라인 일일 운영 — 장전 웜업 → 장중 수집 → 장후 종료.

Master Plan Ver 2.0 §9 W6~8까지의 산출물(TickCollector·MultiHorizonBarComposer·
FeatureEngine)을 실제 매매일 하루 동안 무인으로 돌리기 위한 진입점. 지금까지는 전부
스크래치 스크립트로 손으로 실행해 검증했을 뿐, "장전에 대기하다 자동으로 켜지는" 운영
흐름 자체는 없었다 — 이 스크립트가 그 자리를 채운다.

시간대(KST, 전부 하드코딩 아님 — 아래 상수만 바꾸면 됨):
- 정시 트리거(작업 스케줄러 "Messiah" — 시각은 `configs/scheduled_tasks.json`이 정본이다.
  여기 적지 않는다: 2026-08-10에 이 줄이 "08:35"라고 말하는 동안 실제 등록은 08:20이었고,
  기동 창 가드가 정시 기동을 거부해 오전을 잃었다) ~ 09:00: 웜업 — **Docker Desktop 응답
  확인/자동 기동**(아래 항목), self_check, 근월물 심볼 확인, Redis 연결, Collector/Composer/
  Engine 구성, **피처 웜스타트**(아래 `_load_warmup_artifacts()`), **WS는 이 시점에 이미
  연결·구독까지 끝내 둔다**(9시 정각에 연결부터 새로 맺느라 첫 틱을 놓치지 않도록 — "첫봉
  대기 준비완료" 요건). 별도의 "9시까지 대기" 로직은 두지 않는다.
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

**장전 08:45~09:00 구간 (2026-07-30 실측, 미해결 결정 사항)**: 이 docstring은 원래 "실제로
틱이 오기 시작하는 건 장이 열려야 하므로"라고 적혀 있었는데 **틀린 가정이었다**. 3거래일
(07-28·07-29·07-30) 연속으로 **08:45:00 정각부터** 틱이 들어왔고, 그 15분치가 정규장 봉과
구분 없이(`quality_ok=True`) 아카이브·피처·차트에 그대로 섞여 들어가고 있다(07-30 08:45봉
거래량 526 — 09:00 개장봉 506보다 오히려 많다). 이 프린트가 **예상체결인지 실체결인지**에
따라 처리 방침이 갈린다: 예상체결이면 학습 데이터 오염이므로 09:00 이전 틱을 버려야 하고,
실체결이면 `BarClosed`에 세션 구분 필드를 더해 명시적으로 보존해야 한다. 원시 프레임 확인이
필요한데 라이브 세션과 WS 연결을 다툴 수 없어(같은 계좌 2연결 = 상호 단절, 아래 항목)
비거래시간 검증으로 미뤄 둔다. 그때까지 판단 근거를 쌓기 위해 `TickCollector`가 연결 후 첫
틱 시각을 `CollectorFirstTick`으로 매일 남긴다.

**옵션 수집 (2026-08-04 결선)**: 옵션 **체인 시세**는 REST 폴링으로 수집한다 — 먼쓰리·월위클리·
목위클리 3종을 각각 ATM±10 창으로, **서로 다른 주기·위상**에 태운다(`_option_chain_plan()`).
오래 "옵션은 WS 다중연결 문제 때문에 못 한다"고 적혀 있었는데 **그건 옵션 틱(체결) 구독
얘기였고 체인 시세와는 무관하다** — 이 경로는 WS 연결을 하나도 열지 않는다. 실제 제약은
REST 유량이었고(전량 폴링 시 1,356다리=22.6분) ATM 창으로 푼다.

**아직 없는 것**: 스캘러/모델 로딩(`_load_warmup_artifacts()`는 2026-07-30에 피처 웜스타트만
먼저 채웠고, 스캘러·모델 자리는 Phase 3 이후 실제 모델이 생기면 채움), 옵션 **틱**의 실시간
WS 구독(같은 계좌 WS 연결 2개는 서로 끊긴다 — 단일 연결·다중 subscribe()로 풀어야 하는
별도 작업. 위 체인 시세 폴링과는 무관한 과제다).

사용: python scripts/run_l1_daily.py [--configs configs]
Windows 작업 스케줄러에 "Messiah"(평일 정시 + 부팅 시, `run_l1_daily.bat`)로 실제 등록·가동 중.
등록 시각의 정본은 `configs/scheduled_tasks.json`이고 `scripts/install_scheduled_tasks.ps1`이
그대로 등록한다 — 실제 등록 상태와 어긋나면 `ops/host_health.py`의 `schedule_drift` 항목이
매일 아침 자가 점검에서 잡는다(2026-08-10 신설).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.broker.kis import symbol_master, tr_codes  # noqa: E402
from messiah.broker.kis.credentials import KISCredentials  # noqa: E402
from messiah.broker.kis.rest_client import (  # noqa: E402
    DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
    KISRestClient,
)
from messiah.broker.kis.token_daemon import TokenDaemon  # noqa: E402
from messiah.core import crash_forensics, universe  # noqa: E402
from messiah.core import logging as mlog  # noqa: E402
from messiah.core.bus import MessageBus  # noqa: E402
from messiah.core.config import InstanceConfig, load_instance  # noqa: E402
from messiah.core.docker_bootstrap import (  # noqa: E402
    DEFAULT_DOCKER_DESKTOP_EXE,
    ensure_docker_ready,
)
from messiah.core.event_calendar import DEFAULT_SESSION, EventCalendar  # noqa: E402
from messiah.core.health import (  # noqa: E402
    COLLECTOR_COMPONENT,
    HealthReporter,
    HealthStatus,
)
from messiah.core.messages import HealthLevel, Horizon  # noqa: E402
from messiah.core.scheduler import FixedTickScheduler  # noqa: E402
from messiah.core.timeutil import now_kst  # noqa: E402
from messiah.core.ui_launcher import (  # noqa: E402
    DEFAULT_PORT,
    LaunchedUI,
    is_ui_already_running,
    launch_command_center,
    watch_command_center_forever,
)
from messiah.data import normalizer  # noqa: E402
from messiah.data.archiver import ParquetArchiver  # noqa: E402
from messiah.data.bar_composer import MultiHorizonBarComposer, compose_offline  # noqa: E402
from messiah.data.collector import TickCollector  # noqa: E402
from messiah.data.flow_archiver import InvestorFlowArchiver  # noqa: E402
from messiah.data.investor_flow_poller import InvestorFlowPoller  # noqa: E402
from messiah.data.last_price import LastPriceTracker  # noqa: E402
from messiah.data.normalizer import parse_futures_ticks  # noqa: E402
from messiah.data.option_chain_archiver import OptionChainArchiver  # noqa: E402
from messiah.data.option_chain_poller import OptionChainPoller  # noqa: E402
from messiah.data.tick_archiver import TickArchiver  # noqa: E402
from messiah.features import sidecar  # noqa: E402
from messiah.features import spec as feature_spec  # noqa: E402
from messiah.features.engine import FeatureEngine  # noqa: E402
from messiah.ops import loss_ledger, series_expectation, session_guard  # noqa: E402
from messiah.ops.integrity_report import generate_and_write  # noqa: E402
from messiah.ops.status_board import run_status_board_forever  # noqa: E402

# 정규장 마감(연속거래 종료) 시각 — event_calendar.DEFAULT_SESSION과 같은 값을 직접
# 참조해 단일 소스를 유지한다(두 곳이 따로 하드코딩돼 있다가 어긋나는 사고 방지).
REGULAR_SESSION_STOP = (DEFAULT_SESSION.close_time.hour, DEFAULT_SESSION.close_time.minute)
HARD_SHUTDOWN_DEADLINE = (15, 40)  # daily_close()가 이 시각까지 못 끝내면 강제 종료(안전판)

_MASTER_CACHE_DIR = Path(".cache/kis_symbol_master")
_DATA_DIR = Path("data") / "bars"
_FLOW_DIR = Path("data") / "flow_intraday"
_OPTION_CHAIN_DIR = Path("data") / "option_chain"
# 체결틱 원본 (2026-08-04 신설, F2) — MS(마이크로구조) 카테고리의 유일한 원천이고 **백필
# 경로가 없다**(KIS 분봉 API는 OHLCV만 준다). 안 받은 날은 영원히 빈다.
_TICK_DIR = Path("data") / "ticks"
# 수급 폴링 격자 — 1분봉과 같은 주기. 3업종 순차 조회라 유량(모의투자 1건/초)에
# 여유가 크고, 이보다 촘촘히 받아도 원천이 "당일 누적"이라 정보가 늘지 않는다.
_FLOW_POLL_SECONDS = 60.0

# 기동 지연을 기동 로그에 한 줄 찍는 하한 (2026-08-10 B-2). `ops/loss_ledger._LAG_FLOOR_MINUTES`·
# `integrity_report.DEFAULT_THRESHOLDS["collection_start_lag_minutes"]`와 **같은 값**이다 —
# 세 자리가 같은 질문에 답하는데 임계가 다르면 화면·로그·리포트가 다른 말을 하게 된다.
_START_LAG_ALERT_MINUTES = 5.0

# ---------------------------------------------------------------- 옵션체인 폴링 계획
#
# 시리즈마다 **주기가 다르고 위상이 겹치지 않는다**. 마흐디(선행 프로젝트)가 같은 것을
# 균등하게 돌리다 두 번 크게 잃은 결과를 그대로 반영한 것이다:
#
#   2026-07-30  3북 균등 60초 → 총수요 0.663건/초(용량의 66%). 백오프가 최대 2.61배까지
#               벌어지자 옵션체인 25사이클(5.1%)이 통째로 유실.
#   2026-08-03  위클리 2북을 같은 분에 몰아 짝수분 30레그/홀수분 10레그로 3:1 쏠림 —
#               밀림 39건의 100%가 짝수 버킷, 결손 41분 중 39분이 홀수분. **총량이 아니라
#               분산의 문제**였고, 위상만 갈라서 총수요 증가 0으로 해결했다.
#
# 먼쓰리를 빠르게 두는 근거도 마흐디와 같다 — 먼슬리는 GEX/감마플립/감마월의 **주 입력**이고
# 위클리는 **핀 리스크 전용**이라(`options_intel.py`) 요구 해상도가 다르다.
#
# ATM±10인 이유는 `data/option_chain_poller.DEFAULT_STRIKE_WINDOW` docstring 참고 —
# 요약하면 마흐디의 ATM±2는 WS 슬롯 한도(41) 때문이지 피처 요구가 아니었고, 오히려 그
# 좁음이 감마플립 산출을 하한(6다리) 근처로 밀어 버그를 넉 달간 가렸다.
_OPTION_STRIKE_WINDOW = 10
_OPTION_FAST_SECONDS = 300.0  # 먼쓰리 — Ver 1.3 §2 "Vol Engine: 5분봉 완성 시"와 같은 격자
_OPTION_SLOW_SECONDS = 600.0  # 위클리 2북
_OPTION_PHASE_SECONDS = {"regular": 0.0, "weekly_mon": 100.0, "weekly_thu": 200.0}
# 위클리 만기 요일 — 이 요일엔 그 북이 0DTE가 되고, **핀 리스크는 바로 그 북에서만 나온다**
# (마흐디 `options_intel.py`: 만기 당일 북은 잔존만기 0이라 BS 감마가 정의되지 않는 대신
# 만기 Pinning이 거기서 나온다). 마흐디는 위클리도 WS로 체결을 받아 2분 REST로 충분했지만
# **MESSIAH는 옵션 WS가 없어** 그대로 두면 만기일 핀 리스크를 10분 해상도로 보게 된다.
# 그날은 그 북과 먼쓰리의 주기를 맞바꾼다 — 총수요는 1건도 안 늘고, 그날 정보가 가장 많은
# 북에 해상도를 준다.
_OPTION_EXPIRY_WEEKDAY = {"weekly_mon": 0, "weekly_thu": 3}  # Mon=0 … Thu=3


def _option_chain_plan(today: date) -> list[tuple[str, float, float]]:
    """(시리즈, 주기초, 위상초) 목록. 만기일이면 그 위클리와 먼쓰리의 주기를 맞바꾼다.

    만기 판정에 요일을 쓰는 이유: 마스터파일의 `expiry`는 문자열("202608"/"2608W2")이라
    실제 날짜가 아니고, 실제 최종거래일(`futs_last_tr_date`)은 폴링 응답을 한 번 받아야
    알 수 있다(그건 계획을 세운 뒤다). 요일은 휴장일에 어긋날 수 있지만 그런 날은
    `EventCalendar.is_trading_day()`가 이 스크립트를 아예 조기 종료시킨다.
    """
    expiring = [s for s, wd in _OPTION_EXPIRY_WEEKDAY.items() if today.weekday() == wd]
    fast = expiring[0] if expiring else "regular"
    return [
        (
            series,
            _OPTION_FAST_SECONDS if series == fast else _OPTION_SLOW_SECONDS,
            _OPTION_PHASE_SECONDS[series],
        )
        for series in universe.option_series(list(universe.DEFAULT_UNIVERSE))
    ]


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


def _previous_day_close(archiver: ParquetArchiver, symbol: str, today: date) -> int | None:
    """직전 거래일의 마지막 1분봉 종가(틱) — `px_gap_open`의 유일한 원천.

    ## 왜 웜스타트 창에 기대면 안 되나 (2026-08-05 실측)

    08:35 기동에서는 웜스타트 200봉이 통째로 전일 것이라 `SessionState`의 일자 롤오버가
    저절로 일어난다. 그런데 **장중 재기동**에서는 최근 200봉이 전부 오늘 것이라 경계가 창
    안에 없고, `prev_day_close_ticks`가 영영 None으로 남아 그날 나머지 시간 내내
    `px_gap_open`이 NaN이 된다. 2026-08-05 14:12 재기동 후 실제로 그랬다.

    아카이브에서 직접 읽으면 창 길이와 무관하게 항상 채워진다. 못 찾으면 None을 돌려주고
    (첫 거래일·아카이브 없음) 그건 `px_gap_open`이 정직하게 NaN인 정상 상황이다.
    """
    days = [d for d in archiver.available_days(symbol, Horizon.M1) if d < today]
    for day in reversed(days):  # 가장 가까운 과거부터
        bars = archiver.read_day_bars(symbol, Horizon.M1, day)
        if bars:
            return max(bars, key=lambda b: b.bar_open_kst).c_ticks
    return None


def _load_warmup_artifacts(
    engine: FeatureEngine, archiver: ParquetArchiver, symbol: str, today: date
) -> None:
    """웜업 구간(09:00 이전)에 FeatureEngine의 롤링 윈도우를 아카이브로 미리 채운다
    (2026-07-30 구현 — 그 전까지는 자리만 잡아둔 빈 스텁이었다).

    필요성의 근거는 `features/engine.py`의 `warm_start()` docstring 참고 — 요약하면 매 기동이
    콜드스타트라 15m/30m 피처는 하루 종일 2/3가 NaN이었고, 장중 재시작 한 번이면 그때까지의
    워밍업이 전부 날아갔다. **오늘 날짜 파일도 포함**해서 읽는 게 중요하다: 장중 재시작 복구가
    이 함수의 주 용도다.

    실패해도 수집은 계속한다 — 웜스타트는 부가 기능이지 기동 전제조건이 아니다
    (`core/docker_bootstrap.py`와 같은 원칙). 다만 조용히 넘어가지는 않는다(L18/L22).

    스캘러·모델 로딩 자리는 여전히 비어 있다 — Phase 3(W17~) 이전엔 실제 모델이 없다.
    """
    try:
        history = {
            horizon: archiver.load_recent_bars(
                symbol, horizon, on_or_before=today, max_bars=engine.history_capacity
            )
            for horizon in Horizon
        }
        loaded = engine.warm_start(
            history, prev_day_close_ticks=_previous_day_close(archiver, symbol, today)
        )
    except Exception as exc:  # noqa: BLE001 — 웜스타트 실패가 그날 수집을 막으면 안 됨
        mlog.log(
            "FeatureWarmStartFailed",
            f"피처 웜스타트 실패 — 콜드스타트로 진행: {exc}",
            symbol=symbol,
        )
        return

    summary = {horizon.value: count for horizon, count in loaded.items()}
    mlog.log(
        "FeatureWarmStart",
        f"과거 완성봉으로 롤링 윈도 사전 충전 (용량 {engine.history_capacity}봉)",
        symbol=symbol,
        bars_by_horizon=summary,
    )
    print(f"피처 웜스타트: {summary}", flush=True)


def _restore_composer_buckets(composer: MultiHorizonBarComposer, symbol: str, today: date) -> None:
    """장중 재기동 복원 — 아카이브에 있는데 상위 Horizon으로 안 나간 버킷을 되채운다
    (`data/bar_composer.py` 겹⑤, 2026-08-06 P0-3a).

    `_load_warmup_artifacts()` **뒤에** 부른다: 웜스타트는 피처 엔진의 롤링 윈도를 채우고
    이건 합성기의 미완 버킷을 채운다 — 둘 다 "재기동해도 아침부터 돌던 것과 같아진다"는
    같은 목적의 서로 다른 절반이다. 08:35 정시 기동에서는 그날 1분봉이 없어 조용히 0건이다.

    실패해도 수집은 계속한다 — 복원은 부가 기능이지 기동 전제조건이 아니다."""
    restored = composer.restore_open_buckets(today)
    if not restored:
        return
    summary = " ".join(f"{h}={n}" for h, n in sorted(restored.items()))
    mlog.log(
        "ComposerBucketsRestored",
        f"장중 재기동 복원 — 아카이브의 1분봉으로 미완 상위 버킷을 되채웠다 ({summary})",
        symbol=symbol,
        date=today.isoformat(),
        restored_bars_by_horizon=restored,
    )
    print(f"합성기 미완 버킷 복원: {summary}", flush=True)


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _ui_log_path(today_str: str) -> Path:
    return Path("logs") / f"ui_{today_str}.log"


async def alert_if_no_first_tick(
    collector,
    *,
    deadline: time = DEFAULT_SESSION.open_time,
    sleep=asyncio.sleep,
    now=now_kst,
) -> bool:
    """정규장 개시까지 첫 틱이 한 건도 없으면 **한 번** 크게 운다 (2026-08-11 G-5).

    ## 왜 헬스 판정만으로는 부족한가

    `TickCollector.health()`가 같은 시한으로 CRITICAL을 내게 됐지만(그쪽 `warmup_expired`),
    그건 **화면을 보는 사람에게만** 닿는다. 2026-08-10의 실패는 화면이 있는데도 아무도
    안 본 것이 아니라, 08:20~08:58 사이 그 화면이 아예 없었다는 것이다. 로그는 화면과
    독립적으로 남고 일일 무결성 리포트의 태그 집계에 잡히므로, 그날 놓쳐도 장후에 드러난다.

    ## 한 번만 운다

    주기적으로 반복하면 하루 수천 줄이 되고 그러면 아무도 안 본다(`FeaturePublish`가 그랬다).
    시한까지 자고, 한 번 보고, 끝낸다 — 상태의 지속은 헬스 heartbeat가 계속 말한다.

    ## 자동 재기동을 여기서 하지 않는 이유

    같은 계좌로 WS를 두 번 연결하면 **서로 끊는다**(`data/collector.py` 모듈 docstring,
    2026-07-29 실측). 이 함수가 프로세스를 다시 띄우면 그 사고를 자동화하는 셈이다 —
    죽었는지 살았는지 판정할 근거가 이 프로세스 안에는 없다(자기가 그 프로세스다).
    그래서 사람이 한 줄로 복구할 수단(`scripts/recover_now.bat`)을 주고, 판단은 사람이 한다.

    반환값은 테스트용이다: True면 "시한을 넘겼다"를 보고했다는 뜻.
    """
    while True:
        current = now()
        if current.time() >= deadline:
            break
        remaining = (
            datetime.combine(current.date(), deadline, tzinfo=current.tzinfo) - current
        ).total_seconds()
        await sleep(max(remaining, 0.0))

    if collector.first_tick_overdue(now=now()):
        mlog.log(
            "CollectorFirstTickOverdue",
            f"{deadline.strftime('%H:%M')}까지 첫 틱이 한 건도 없다 — 그때까지의 체결틱·"
            "수급·옵션체인은 영구 소실(소급 경로 없음). 회선/구독/토큰을 확인하고 "
            "scripts\\recover_now.bat로 복구할 것",
            symbol=getattr(collector, "_symbol", "unknown"),
            deadline=deadline.strftime("%H:%M"),
        )
        print(
            f"[손실] {deadline.strftime('%H:%M')}까지 첫 틱 0건 — scripts\\recover_now.bat",
            flush=True,
        )
        return True
    return False


def _launch_ui(today_str: str) -> LaunchedUI:
    """`core/ui_launcher.py`의 얇은 래퍼 — 이 프로세스(데이터 수집)와 화면은 서로
    독립적이다. 중복 기동 방지(포트 응답 확인)는 공용 모듈이 담당한다(2026-07-30 추가,
    `run_g2_paper_trading.py`와 UI를 동시에 켰을 때의 실측 발견 대응).

    반환값의 `port`를 **반드시 아래로 흘려야 한다**(2026-08-11 F-6) — 워치독과 상태판의 UI
    프로브가 기본 포트를 계속 쳐다보면, 화면이 대체 포트로 뜬 날 둘 다 남의 프로세스를 보며
    "정상"이라고 말한다."""
    return launch_command_center(
        caller_tag="run_l1_daily",
        project_root=_PROJECT_ROOT,
        log_path=_ui_log_path(today_str),
    )


@dataclass(frozen=True)
class _RestCollection:
    """REST 폴링 3종(수급 + 옵션체인 시리즈별)과 그 아카이버 묶음.

    **`KISRestClient`를 하나만 만들어 전부 공유한다** — 이게 이 묶음이 존재하는 이유다.
    `_RateLimiter`는 클라이언트 인스턴스마다 하나씩 생기므로, 폴러마다 클라이언트를 만들면
    페이서가 여러 개가 되어 실효 호출률이 그 배수만큼 뛴다. 마흐디가 2026-07-08에 정확히
    그렇게 하다 500 폭주로 **정규장 405분 중 203분치 옵션체인을 통째로 날렸고**, 그 뒤로
    "폴러별 페이서 분리"는 봉인됐다.
    """

    flow_poller: InvestorFlowPoller | None = None
    flow_archiver: InvestorFlowArchiver | None = None
    chain_pollers: tuple[tuple[OptionChainPoller, float, float], ...] = ()  # (폴러, 주기, 위상)
    chain_archiver: OptionChainArchiver | None = None
    price_tracker: LastPriceTracker | None = None
    # 시리즈 → 미상장 사유 문장 (2026-08-07 P0-2). 기동 로그가 "왜 안 모으는지"를 그대로
    # 인용하게 하려는 것 — 사유 없는 부재는 사고와 구분이 안 된다.
    contract_notes: dict[str, str] = field(default_factory=dict)

    @property
    def requests_per_second(self) -> float:
        """정상 상태의 평균 REST 수요 — 기동 로그가 찍는 값."""
        rps = 0.0
        if self.flow_poller is not None:
            rps += 3 / _FLOW_POLL_SECONDS  # 3업종
        for poller, period, _ in self.chain_pollers:
            # **선언이 아니라 오늘 실제로 나갈 호출 수**를 센다 (2026-08-07 P1-1) —
            # 미상장 시리즈는 0이다(`OptionChainPoller.expected_legs_per_cycle`).
            rps += poller.expected_legs_per_cycle / period
        return rps

    @property
    def backoff_headroom(self) -> float:
        """수요가 용량에 닿기까지 백오프가 몇 배까지 벌어져도 되는가.

        마흐디 2026-07-30 실측: 수요 0.663건/초일 때 내성 1.51배였는데 **실제 백오프가
        평균 1.29배·최대 2.61배**까지 올라 옵션체인 25사이클이 유실됐다. 이 값이 2.61 밑으로
        내려가면 같은 사고를 반복한다 — 그래서 기동 로그에 찍는다."""
        rps = self.requests_per_second
        if rps <= 0:
            return float("inf")
        return (1.0 / DEFAULT_MIN_REQUEST_INTERVAL_SECONDS) / rps


def _seed_preopen_reference_price(
    tracker: LastPriceTracker, archiver: ParquetArchiver, symbol: str, today: date
) -> None:
    """옵션체인 ATM 기준가를 **직전 완성 1분봉의 종가**로 미리 채운다 (2026-08-05).

    수집은 08:35에 뜨는데 미니선물 첫 틱은 08:45 정각에 온다 — 그 10분 동안 기준가가 없어
    옵션체인 폴러가 매 사이클을 건너뛴다(2026-08-05 실측 5사이클). 옵션 스냅샷은 과거 조회
    경로가 없으므로 **그 10분은 영원히 빈다**.

    `load_recent_bars`가 오늘 파일도 포함해 읽으므로(그쪽 docstring) 장중 재시작이면 오늘
    오전의 마지막 봉이, 정상 기동이면 전 거래일 15:34봉이 시드가 된다. 둘 다 ATM±10(50pt)
    창을 벗어나게 만들 만한 값이 아니다.

    실패해도 수집은 계속한다 — 시드는 부가 기능이지 기동 전제조건이 아니다(웜스타트와 같은
    원칙). 시드가 없으면 종전처럼 08:45까지 사이클을 건너뛸 뿐이다.
    """
    try:
        recent = archiver.load_recent_bars(symbol, Horizon.M1, on_or_before=today, max_bars=1)
    except Exception as exc:  # noqa: BLE001
        print(f"[run_l1_daily] 장전 기준가 시드 실패(옵션체인은 첫 틱까지 대기): {exc}", flush=True)
        return
    if not recent:
        print("[run_l1_daily] 장전 기준가 시드 없음 — 아카이브에 1분봉이 아직 없다", flush=True)
        return

    bar = recent[-1]
    tracker.seed_preopen(bar.c_ticks)
    print(
        f"장전 기준가 시드 — {bar.bar_open_kst:%Y-%m-%d %H:%M} 종가 "
        f"{tracker.price_points():.2f}pt (첫 실틱이 오면 무시된다)",
        flush=True,
    )


def _build_rest_collection(
    creds: KISCredentials,
    bus: MessageBus,
    symbol: str,
    tick_size: Decimal,
    archiver: ParquetArchiver | None = None,
    today: date | None = None,
    universe_tokens: list[str] | None = None,
) -> _RestCollection:
    """REST 폴러 묶음 — 만들다 실패해도 **수집 본 임무를 막지 않는다**.

    수급·옵션체인은 부가 데이터고 봉 수집이 본 임무다(`core/docker_bootstrap.py`류의 "부가
    정보 실패가 본 기능을 막지 않는다" 원칙). 다만 조용히 꺼지면 몇 달 뒤에야 "그동안 안
    모였네"를 알게 되므로, 실패도 성공도 기동 로그에 한 줄 남긴다.
    """
    try:
        client = KISRestClient(creds, token_daemon=TokenDaemon(creds))  # 공유(위 docstring)
        flow_poller = InvestorFlowPoller(
            rest_client=client,
            market_code=tr_codes.FID_MRKT_DIV_DERIVATIVES,
            sector_codes=[
                tr_codes.FID_INVESTOR_FLOW_FUTURES,
                tr_codes.FID_INVESTOR_FLOW_CALL_OPTION,
                tr_codes.FID_INVESTOR_FLOW_PUT_OPTION,
            ],
            bus=bus,
        )
        flow_archiver = InvestorFlowArchiver(_FLOW_DIR, tr_codes.FID_MRKT_DIV_DERIVATIVES)

        master = symbol_master.load_index_derivatives_master(_MASTER_CACHE_DIR)
        tracker = LastPriceTracker(symbol, tick_size)
        if archiver is not None and today is not None:
            _seed_preopen_reference_price(tracker, archiver, symbol, today)
        session_day = now_kst().date()
        plan = _option_chain_plan(session_day)
        # 그날 계약 (2026-08-07 P0-2·고도화 1) — 어느 시리즈가 오늘 상장돼 있는가.
        # 폴러는 `EventCalendar`를 직접 열지 않고 이 술어를 주입받는다(그쪽 docstring).
        contract = series_expectation.for_day(
            session_day,
            list(universe_tokens if universe_tokens is not None else universe.DEFAULT_UNIVERSE),
            EventCalendar.from_file(),
        )
        chain_pollers = tuple(
            (
                OptionChainPoller(
                    client,
                    master,
                    bus,
                    series=series,
                    reference_price=lambda: tracker.price_points(),
                    strike_window=_OPTION_STRIKE_WINDOW,
                    # 기본 인자로 묶어 늦은 바인딩을 막는다 — 안 그러면 세 폴러가 전부
                    # 마지막 `series`의 계약을 본다(전형적인 클로저 함정).
                    listed=(
                        lambda s=series: (
                            contract[
                                f"{series_expectation.ARCHIVE_PREFIX_OPTION_CHAIN}/{s}"
                            ].required
                        )
                    ),
                ),
                period,
                phase,
            )
            for series, period, phase in plan
        )
        chain_archiver = OptionChainArchiver(_OPTION_CHAIN_DIR)
    except Exception as exc:  # noqa: BLE001 — 부가 수집 실패가 봉 수집을 막으면 안 됨
        print(f"[run_l1_daily] REST 폴링 결선 실패(봉 수집은 계속): {exc}", flush=True)
        return _RestCollection()

    collection = _RestCollection(
        flow_poller=flow_poller,
        flow_archiver=flow_archiver,
        chain_pollers=chain_pollers,
        chain_archiver=chain_archiver,
        price_tracker=tracker,
        contract_notes={
            series: contract[f"{series_expectation.ARCHIVE_PREFIX_OPTION_CHAIN}/{series}"].note
            for series, _, _ in plan
        },
    )
    print(
        f"수급 수집 결선 — {tr_codes.FID_MRKT_DIV_DERIVATIVES} "
        f"3업종 / {_FLOW_POLL_SECONDS:.0f}초 격자 → {_FLOW_DIR}",
        flush=True,
    )
    # 미상장 시리즈는 **다른 문장으로** 찍는다 (2026-08-07 P0-2). 같은 "결선 —" 줄을 쓰면
    # "오늘 안 모으는 것을 알고 안 모은다"가 화면에서 안 보이고, 그러면 장후 리포트의 ⊘를
    # 보고서야 알게 된다. 그때는 이미 하루가 끝나 있다.
    for poller, period, phase in chain_pollers:
        if poller.expected_legs_per_cycle == 0:
            note = collection.contract_notes.get(poller.series, "미상장")
            print(f"옵션체인 — {poller.series} {note} · 단언 폴링만(수집 0)", flush=True)
            continue
        print(
            f"옵션체인 결선 — {poller.series} ATM±{_OPTION_STRIKE_WINDOW} "
            f"({poller.legs_per_cycle}다리) / {period:.0f}초 격자 위상 {phase:.0f}초",
            flush=True,
        )
    # 유량 예산을 기동 시점에 찍는다 — 설정을 잘못 잡으면 몇 달 뒤 데이터 유실로 발견되는
    # 대신 1일차 로그에서 보이게. 내성이 2.61배(마흐디 실측 최대 백오프) 밑이면 경고한다.
    rps, headroom = collection.requests_per_second, collection.backoff_headroom
    capacity = 1.0 / DEFAULT_MIN_REQUEST_INTERVAL_SECONDS
    warn = "  ** 내성 부족 — 마흐디 실측 최대 백오프 2.61배 미만 **" if headroom < 2.61 else ""
    print(
        f"REST 유량 예산 — 수요 {rps:.3f}건/초 / 용량 {capacity:.2f}건/초 "
        f"({rps / capacity:.0%}) · 백오프 내성 {headroom:.2f}배{warn}",
        flush=True,
    )
    return collection


async def _run_regular_session(
    collector: TickCollector,
    composer: MultiHorizonBarComposer,
    engine: FeatureEngine,
    bus: MessageBus,
    today_str: str,
    symbol: str,
    rest: _RestCollection | None = None,
    tick_archiver: TickArchiver | None = None,
    minute_bar_close: str = "tick",
    ui_port: int = DEFAULT_PORT,
) -> None:
    """수집 3종 + UI 생존 감시 + 컴포넌트 heartbeat를 동시에 돌린다.

    UI 감시와 heartbeat는 부가 임무다 — 둘 다 예외를 밖으로 내지 않으므로 이 gather를 죽이지
    않는다(UI 감시는 재기동 한도를 넘으면 ERROR 로그를 남기고 감시만 접고, heartbeat는 발행
    실패를 로깅하고 다음 주기에 재시도한다). 2026-07-30 08:57에 UI가 네이티브 크래시로 죽고
    32분간 아무도 몰랐던 사고의 대응 — 경위는 `core/ui_launcher.py`/`core/health.py` 참고.

    heartbeat의 `probe`는 각 컴포넌트가 스스로 구현한 `health()`다 — 데이터 흐름의 상태를
    판정할 근거(마지막 틱/발행 시각)를 실제로 갖고 있는 쪽이 판정한다(고도화 4의 계층 분리).

    UI 감시를 포기할 때 `sys.health`에 CRITICAL을 남긴다(2026-07-31 추가) — 그 전에는 ERROR
    로그 한 줄이 전부였고, **그 로그를 볼 화면이 바로 그 죽은 UI였다**(07-31 12:35~15:35
    3시간 무화면). 컴포넌트 목록에 고정으로 자리를 잡아두면 화면이 돌아왔을 때 "언제부터
    감시가 꺼져 있었는지"가 그대로 보인다."""

    async def _alert_if_no_first_tick() -> None:
        await alert_if_no_first_tick(collector)

    async def _report_ui_gave_up() -> None:
        await HealthReporter(
            bus,
            "l1.command_center_ui",
            probe=lambda: HealthStatus(
                HealthLevel.CRITICAL, "자동 재기동 한도 소진 — 화면 없음, 수동 확인 필요"
            ),
        ).publish_once()

    await asyncio.gather(
        # **틱 아카이버가 수집기보다 먼저다** (2026-08-04, F2). `asyncio.gather`는 인자
        # 순서대로 태스크를 시작하므로, 구독이 먼저 걸려야 첫 틱이 버스에서 증발하지 않는다.
        # 파생 수급에서 이 순서를 틀려 7개월을 날린 전례가 있고, 틱은 그보다 나쁘다 —
        # 봉은 KIS 분봉 API로 소급이 되지만 **체결 단위 과거 조회는 아예 없다**.
        *([tick_archiver.run_forever(bus)] if tick_archiver is not None else []),
        collector.run_forever(),
        composer.run_forever(),
        engine.run_forever(),
        # 첫 틱 시한 감시 (2026-08-11 G-5) — 09:00까지 한 건도 없으면 한 번 크게 운다.
        # 헬스 판정(`TickCollector.health()`)이 이미 같은 시한으로 CRITICAL을 내지만,
        # 그건 화면을 보는 사람에게만 닿는다. 이 줄은 **로그와 일일 리포트**에 남긴다.
        _alert_if_no_first_tick(),
        watch_command_center_forever(
            caller_tag="run_l1_daily",
            project_root=_PROJECT_ROOT,
            log_path=_ui_log_path(today_str),
            # 기본 포트가 아니라 **실제로 뜬 포트**를 본다(2026-08-11 F-6).
            port=ui_port,
            on_gave_up=_report_ui_gave_up,
        ),
        # 컴포넌트 이름은 상수로 — G2의 `TradingPipeline`이 이 heartbeat를 구독해 CB 오탐을
        # 억제한다(`strategy/pipeline.py` "한산과 단절"). 문자열이 갈리면 조용히 결선이 끊긴다.
        HealthReporter(bus, COLLECTOR_COMPONENT, probe=collector.health).run_forever(),
        HealthReporter(bus, "l1.feature_engine", probe=engine.health).run_forever(),
        # 합성기 자가 판정 (2026-08-05 장중 추가). 위 둘이 **신선도**("최근에 받았나")를
        # 재는 반면 이건 **"받은 것을 온전히 합쳤나"**를 잰다 — 그날 상위 Horizon 봉의
        # 3~17%가 사라지는 동안 위 두 축은 하루 종일 OK였다. 손상은 일어나는 중에 보여야
        # 하고, 장후 리포트에서 처음 아는 것은 이미 늦다.
        HealthReporter(bus, "l1.composer", probe=composer.health).run_forever(),
        # 시각 구동 1분봉 확정 (2026-08-05 고도화 1) — `minute_bar_close: timer`일 때만
        # 붙는다. 기본(`tick`)에서는 이 태스크가 아예 없으므로 동작이 종전과 완전히 같다.
        # 위상 0초인 이유: 유예는 `flush_due()`가 **거래소 시각**으로 직접 판정하므로
        # (`MINUTE_CLOSE_GRACE_SECONDS`) 스케줄러 위상으로 또 밀면 이중 계산이 된다.
        *(
            [FixedTickScheduler(tick_seconds=60).run_forever(collector.flush_due_minute)]
            if minute_bar_close == "timer"
            else []
        ),
        # 헤드리스 상태판 (2026-08-03 고도화 A) — UI가 하던 구독을 이 프로세스로 옮겨
        # `logs/status_snapshot.json`에 주기적으로 남긴다. 화면이 죽어도(07-30 32분,
        # 07-31 3시간) 관측은 계속되고, 15:40에 UI가 종료된 뒤의 장후 리뷰도 가능해진다.
        # UI 생사까지 같은 스냅샷에 기록한다 — 화면 없이 화면 상태를 안다.
        run_status_board_forever(
            bus, symbol=symbol, ui_probe=lambda: is_ui_already_running(ui_port)
        ),
        # 파생 장중 수급 (2026-08-04 결선). 폴러 자체는 2026-07-27부터 있었지만 **어디에도
        # 결선돼 있지 않았고** `raw.investor_flow.*` 구독자도 없어서, 이 프로젝트는 파생
        # 수급을 한 건도 갖고 있지 않다. 그리고 KIS 장중 엔드포인트는 당일 누적만 준다 —
        # 봉과 달리 **나중에 소급해 채울 방법이 없으므로**, 안 받은 날은 영원히 빈다.
        # 아카이버를 먼저 구독시키고 폴러를 돌린다(반대면 첫 폴링이 버스에서 증발한다).
        #
        # 옵션체인도 같은 날 같은 이유로 결선했다(2026-08-04) — `OptionChainPoller`는
        # 2026-07-28에 만들어졌지만 역시 어디에도 안 붙어 있었다. 옵션 시세 역시 과거 조회
        # 경로가 없어 안 받은 날은 영원히 빈다. 시리즈 3종이 **서로 다른 주기·위상**으로
        # 도는 이유는 `_option_chain_plan()` 위 주석 참고.
        *_rest_tasks(rest, bus),
    )


def _rest_tasks(rest: _RestCollection | None, bus: MessageBus) -> list:
    """REST 폴링 코루틴 목록 — **아카이버(구독)를 폴러(발행)보다 먼저** 넣는다.

    `asyncio.gather`는 인자 순서대로 태스크를 시작하므로, 구독이 먼저 걸려야 첫 폴링이
    버스에서 증발하지 않는다. 파생 수급에서 이 순서를 틀려 7개월을 날린 전례가 있다.
    """
    if rest is None:
        return []
    tasks: list = []
    if rest.price_tracker is not None:
        tasks.append(rest.price_tracker.run_forever(bus))
    if rest.chain_archiver is not None:
        tasks.append(rest.chain_archiver.run_forever(bus))
    if rest.flow_archiver is not None:
        tasks.append(rest.flow_archiver.run_forever(bus))
    if rest.flow_poller is not None:
        tasks.append(
            FixedTickScheduler(tick_seconds=_FLOW_POLL_SECONDS).run_forever(
                rest.flow_poller.poll_once
            )
        )
    for poller, period, phase in rest.chain_pollers:
        tasks.append(
            FixedTickScheduler(tick_seconds=period, phase_offset_seconds=phase).run_forever(
                poller.poll_once
            )
        )
    return tasks


def _write_integrity_report(today: date, symbol: str, instance_id: str) -> None:
    """장후 무결성 리포트 (고도화 2) — 그날의 봉 결손·재기동·NaN 비율·네이티브 크래시를
    집계해 `logs/daily_integrity_YYYYMMDD.json`에 남긴다.

    2026-07-30 점검에서 사람이 손으로 파야만 보였던 것들을 코드로 고정한 것이다(근거는
    `ops/integrity_report.py` 모듈 docstring). 봉 flush가 끝난 **뒤**에 불러야 그날 마지막
    봉까지 집계에 들어간다.

    실패해도 종료 절차를 막지 않는다 — 리포트는 관측 수단이지 운영 전제조건이 아니다.

    ## 이 리포트는 **예비본**이다 (2026-08-12 F-3)

    여기는 15:36이고 장후 산출물(`volume_check_*.json` 15:45 · `vol_scorecard_*.json`
    15:46)은 아직 없다 — 그건 설계대로다(REST를 종료 예산에 안 넣는다). 그래서
    `provisional=True`로 쓴다: 화면과 파일에는 남되 **등록부 채점은 하지 않는다.**
    그러지 않으면 `daily-axes-measured`가 매일 거짓 `재발`을 낸다(08-11·08-12 실측 —
    11분 뒤 `run_postmarket.py`의 재생성본에서는 같은 축이 깨끗했다).

    확정본은 `scripts/run_postmarket.py`의 5/5단계가 쓴다."""
    try:
        generate_and_write(day=today, symbol=symbol, instance_id=instance_id, provisional=True)
    except Exception as exc:  # noqa: BLE001
        mlog.log(
            "IntegrityThresholdBreached",
            f"무결성 리포트 산출 실패 — 수동 확인 필요: {exc}",
            date=today.isoformat(),
            symbol=symbol,
        )


def _compact_archive(archiver: ParquetArchiver, symbol: str, today: date) -> None:
    """장중 시간대 조각을 하루 1개 통합본으로 되돌린다 (고도화 3).

    조각화는 장중 쓰기 비용을 줄이는 내부 최적화일 뿐, 저장 포맷 변경이 아니다 — Digital
    Twin·백테스트 하니스가 보는 과거 데이터는 예전과 똑같이 `{date}.parquet` 하나여야 한다
    (`data/archiver.py`의 `compact_day()` docstring). 마지막 봉 flush **뒤**에 불러야 그날
    마지막 조각까지 통합에 들어간다.

    실패해도 종료 절차를 막지 않는다 — 통합이 안 돼도 `read_day()`가 조각을 그대로 읽으므로
    데이터가 사라지지는 않는다(다음 기동의 통합에서 자연히 정리된다)."""
    for horizon in Horizon:
        try:
            rows = archiver.compact_day(symbol, horizon, today)
        except Exception as exc:  # noqa: BLE001
            mlog.log(
                "ArchiveCompactionFailed",
                f"{horizon.value} 조각 통합 실패 — 조각은 그대로 남아 읽기는 가능: {exc}",
                symbol=symbol,
                horizon=horizon.value,
            )
            continue
        if rows:
            mlog.log(
                "ArchiveCompacted",
                f"{horizon.value} 조각 → 일자 통합본 {rows}행",
                symbol=symbol,
                horizon=horizon.value,
                rows=rows,
            )


def _recompose_today(archiver: ParquetArchiver, symbol: str, today: date) -> None:
    """장후 상위 Horizon 재합성 — 리포트를 쓰기 **전에** 아카이브를 정합 상태로 만든다
    (2026-08-06 P0-3b).

    ## 왜 종료 절차 안으로 들어왔나

    이건 원래 사람이 `scripts/run_recompose.py`로 돌리는 절차였다. 그런데 이틀 연속
    안 돌았다 — 2026-08-05에 한 번(그날 커밋 제목이 "그것을 쓰라던 절차는 조용히 안 돌았다"
    였다), 그 교훈을 적은 **다음 거래일인 2026-08-06에 또**. 절차를 문서에 적는 것으로는
    안 돈다는 것이 이틀치 실측으로 증명됐다.

    비용은 사실상 0이다: 로컬 Parquet 읽기·쓰기뿐이고 네트워크를 안 탄다(REST를 쓰는
    `verify_archive_volume.py`를 종료 예산에 안 넣기로 한 판단은 그대로 유효하다 —
    그쪽은 `scripts/run_postmarket.py`가 맡는다).

    겹⑤(`bar_composer.restore_open_buckets`)가 들어온 뒤로 이 함수가 고칠 것은 보통
    없다. 그래도 남기는 이유: 겹⑤는 **기동 시점**에만 돌아서 15:35 직전에 프로세스가
    비정상 종료하는 경우를 못 덮고, 무엇보다 리포트의 `horizon_findings`가 "정합하다"고
    말할 때 그게 실제로 참이어야 하기 때문이다.

    실패해도 종료 절차를 막지 않는다 — 실패하면 `horizon_findings`가 그 사실을 그대로
    드러내므로 조용한 실패가 아니다."""
    try:
        minute_bars = archiver.read_day_bars(symbol, Horizon.M1, today)
    except Exception as exc:  # noqa: BLE001
        mlog.log(
            "RecomposeFailed",
            f"1분봉을 못 읽어 재합성을 건너뛴다 — horizon_findings로 드러난다: {exc}",
            symbol=symbol,
            date=today.isoformat(),
        )
        return
    if not minute_bars:
        return

    written: dict[str, int] = {}
    for horizon in Horizon:
        if horizon is Horizon.M1:
            continue  # 원본 — 합성 대상이 아니다
        try:
            composites = compose_offline(symbol, horizon, minute_bars)
            written[horizon.value] = archiver.write_day(symbol, horizon, composites)
        except Exception as exc:  # noqa: BLE001 — 한 Horizon 실패가 나머지를 막지 않는다
            mlog.log(
                "RecomposeFailed",
                f"{horizon.value} 재합성 실패 — 그 Horizon은 수집 당시 상태로 남는다: {exc}",
                symbol=symbol,
                horizon=horizon.value,
                date=today.isoformat(),
            )
    if written:
        summary = " ".join(f"{h}={n}" for h, n in written.items())
        mlog.log(
            "Recomposed",
            f"장후 상위 Horizon 재합성 — 1분봉 {len(minute_bars)}개 기준 ({summary})",
            symbol=symbol,
            date=today.isoformat(),
            rows_by_horizon=written,
        )
        print(f"상위 Horizon 재합성: 1분봉 {len(minute_bars)}개 → {summary}", flush=True)


async def _daily_close(
    collector: TickCollector,
    composer: MultiHorizonBarComposer,
    bus: MessageBus,
    rest: _RestCollection | None = None,
    tick_archiver: TickArchiver | None = None,
    engine: FeatureEngine | None = None,
) -> None:
    # 마지막 1분봉은 **버스를 통해서만** 합성기에 도달한다(아키텍처 불변 원칙 2 — 직접
    # 함수 호출 금지). 발행과 구독자 콜백 사이에는 순서 보장이 없으므로, 곧바로
    # `flush_all_final()`을 부르면 그 봉이 상위 Horizon 전부에서 빠진다 — 2026-08-04에
    # 실제로 그랬다(15:34봉 137계약이 1분봉엔 있고 3/5/10/15/30분봉엔 없음). 경합을
    # 우회하지 않고 **관측 가능한 대기**로 바꾼다. 실패해도 종료는 계속하되 ERROR로 남긴다.
    final_bar = await collector.flush_final_bar()
    if final_bar is not None and not await composer.wait_for_bar(final_bar.bar_open_kst):
        # **버스로는 절대 도달하지 못한다 — 구독자가 이미 취소됐기 때문이다** (2026-08-05).
        #
        # `_run_regular_session()`의 `asyncio.gather`에 `composer.run_forever()`가 들어 있고,
        # 15:35에 `asyncio.wait_for(...)`가 그 gather를 통째로 취소한다. 그 뒤에 부르는
        # `flush_final_bar()`는 **아무도 안 듣는 버스**로 발행하는 셈이라, 이 대기는 매일
        # 반드시 실패하고 그날 마지막 1분봉이 상위 Horizon에서 빠진다(2026-08-05 15:35:06
        # 실측 — 수정 전에는 이 ERROR가 나고도 봉이 그냥 유실됐다).
        #
        # 그래서 여기서 **직접 넘긴다**. 아키텍처 불변 원칙 2("프로세스 간 통신은 Redis Bus
        # 로만")는 프로세스 **사이**의 규칙이고, 이 둘은 같은 프로세스 안에 있으며 지금은
        # 버스 자체가 내려간 종료 경로다. 조용히 하지 않는다 — 무엇을 왜 우회했는지 남긴다.
        mlog.log(
            "DailyCloseBarHandedOff",
            f"마지막 1분봉({final_bar.bar_open_kst:%H:%M})이 버스로 도달하지 않아 합성기에 "
            "직접 전달 — 종료 시퀀스에서 구독이 이미 취소된 상태다",
            symbol=final_bar.symbol,
            bar_open_kst=final_bar.bar_open_kst.isoformat(),
        )
        await composer.handle_one_minute_bar(final_bar)
        if not await composer.wait_for_bar(final_bar.bar_open_kst, timeout_seconds=0.05):
            mlog.log(
                "DailyCloseBarNotDrained",
                f"마지막 1분봉({final_bar.bar_open_kst:%H:%M})이 직접 전달로도 반영되지 않음 "
                "— 그 분은 상위 Horizon 합성봉에서 빠진다(1분봉 아카이브에는 남음)",
                symbol=final_bar.symbol,
                bar_open_kst=final_bar.bar_open_kst.isoformat(),
            )
    await composer.flush_all_final()
    # 세션 내내 죽어 있던 피처를 남긴다 (2026-08-05 고도화 3) — `nan_ratio`가 못 보는
    # 것을 본다. 퇴화 0건인 날도 남겨야 "검사했는데 0건"과 "검사를 안 함"이 갈린다.
    if engine is not None:
        engine.log_feature_health()
    # 회선 수신 지연 분포 (2026-08-05 고도화 1) — `minute_bar_close: timer` 승격의 근거
    # 데이터다. 유예를 몇 초로 둘지는 이 p99가 정하고, 그 값은 이 로그 이전엔 존재하지
    # 않았다. 여기(장 마감)에서 남기는 이유: 세션 전체 표본이 다 모인 시점이다.
    collector.log_delivery_latency()
    # 틱 아카이버도 버퍼링한다(하루 5~10만행이라 매 틱 재작성하면 O(n²)) — 남은 버퍼를
    # 확정하고, **그날 실제로 몇 행이 나갔는지 로그에 남긴다.** 결선만 하고 0행으로 하루가
    # 끝나는 것이 이 프로젝트의 반복 실패 모드였다(수급 폴러 7개월, 옵션체인 수개월).
    if tick_archiver is not None:
        tick_archiver.close()
        mlog.log(
            "TickArchiveSummary",
            f"체결틱 적재 {tick_archiver.written}행 → {_TICK_DIR}",
            symbol=tick_archiver.symbol,
            rows=tick_archiver.written,
        )
    # 옵션체인 아카이버는 사이클 단위로 버퍼링한다(하루 3,276행이라 스냅샷마다 전체 재작성
    # 하면 O(n²)) — 마지막 미완 사이클이 버퍼에 남아 있으므로 여기서 확정한다. 이 한 줄이
    # 없으면 매일 장 마감 직전 사이클이 조용히 사라진다.
    if rest is not None and rest.chain_archiver is not None:
        rest.chain_archiver.close()
    await bus.close()


async def main(cfg: InstanceConfig) -> None:
    # 네이티브 크래시 덤프 무장 (2026-08-03) — 이 프로세스도 polars로 Parquet을 읽고 쓴다
    # (`data/archiver.py`). UI에서 5거래일 연속 터진 access violation이 여기서 나면 지금까지는
    # 로그에 한 줄도 안 남고 수집이 통째로 사라졌을 것이다(`core/crash_forensics.py`).
    forensics_target = crash_forensics.enable(tag="l1_daily")
    mlog.setup(cfg.instance_id)
    # 무장 사실을 **구조화 로그로도** 남긴다 (2026-08-05) — stderr 마커 하나에만 의존하면
    # 호스트(PowerShell)가 그 줄에 접두사를 붙이는 것만으로 탐지가 깨진다. 실제로 08-04에
    # 그렇게 깨져 "수정이 안 들었다"는 ERROR 오탐이 났다(`ops/crash_dumps.py`).
    mlog.log(
        "CrashForensicsArmed",
        f"네이티브 크래시 덤프 무장 — 대상 {forensics_target}",
        process="l1_daily",
        target=forensics_target,
        armed=crash_forensics.is_armed(),
    )

    today = now_kst().date()
    if not EventCalendar.from_file().is_trading_day(today):
        print(
            f"{today.isoformat()}은 KRX 휴장일(Event Calendar) — 수집 생략, 즉시 종료",
            flush=True,
        )
        return

    # 기동 창 검사 (2026-08-06 P0-2) — Task Scheduler에 at-startup 트리거가 붙으면서
    # **아무 시각에나** 이 프로세스가 불릴 수 있게 됐다. 재부팅 복구(10:05 부팅 → 즉시
    # 재개)는 살리고, 새벽 재부팅에 하루 종일 빈 프로세스가 뜨는 것은 막는다.
    allowed, reason = session_guard.launch_window_verdict()
    if not allowed:
        print(f"[기동 창] {reason}", flush=True)
        # 구조화 로그로도 남긴다 (2026-08-07 P0-4) — 무결성 리포트가 이 `SessionStart`를
        # 기동으로 세지 않게 하는 유일한 근거다(`core/logging.py` 태그 주석).
        mlog.log("LaunchWindowRefused", reason, process="l1_daily")
        # 정시 트리거를 거부한 것이면 **종료 코드를 가른다** (2026-08-10 P0). 2026-08-10에
        # 이 경로가 조용히 0으로 끝나 스케줄러에 `LastTaskResult=0`(성공)으로 남았고, 그날
        # 오전이 통째로 사라지는 동안 모든 계기가 정상이라고 말했다. 부팅 트리거로 온 거부는
        # 종전대로 0이다 — 그건 실패가 아니다.
        if session_guard.refused_a_scheduled_launch():
            print(
                "[기동 창] 정시 트리거로 뜬 기동을 거부했다 — 오늘 수집이 통째로 없어진다. "
                "configs/scheduled_tasks.json의 시각과 실제 등록이 어긋났을 가능성이 크다"
                "(자가 점검 schedule_drift 항목 확인 → "
                "scripts/install_scheduled_tasks.ps1 재실행).",
                file=sys.stderr,
                flush=True,
            )
            raise SystemExit(session_guard.REFUSED_EXIT_CODE)
        return

    # **오늘 이미 잃은 것**의 첫 항목을 여기서 적는다 (2026-08-10 B-2). 기동 지연은 프로세스가
    # 뜨는 순간 이미 확정된 손실이고(그 구간의 틱·수급·옵션체인은 소급 경로가 없다), 대개
    # 그날 손실 중 가장 큰 덩어리다 — 08-10에 38분이었다. 이 한 줄이 없어서 그 사실이
    # 사람 눈에 닿은 것이 15:45 장후 리포트였다.
    lag = session_guard.minutes_since_scheduled_trigger()
    loss_ledger.record_start_lag(lag)
    if lag is not None and lag > _START_LAG_ALERT_MINUTES:
        print(
            f"[손실] 수집 기동이 정시 트리거보다 {lag:.0f}분 늦었다 — "
            "그 구간의 체결틱·수급·옵션체인은 영구 소실(소급 경로 없음)",
            flush=True,
        )

    launched_ui = _launch_ui(today.strftime("%Y%m%d"))

    creds = KISCredentials.from_broker_config(cfg.broker)
    symbol = await asyncio.to_thread(_resolve_front_month_symbol)
    tick_size = Decimal(cfg.futures_tick_size)
    print(f"근월물 심볼: {symbol} (tick_size={tick_size})", flush=True)
    # 1분봉 확정 방식을 기동 로그에 찍는다 — 설정 하나가 봉 생성 규칙을 바꾸므로, 그날
    # 아카이브가 어느 규칙의 산물인지 사후에 알 수 있어야 한다(`session_git_shas`와 같은 이유).
    print(
        f"1분봉 확정: {cfg.minute_bar_close}"
        + (
            f" (거래소 시각 경계+{normalizer.MINUTE_CLOSE_GRACE_SECONDS:.1f}초)"
            if cfg.minute_bar_close == "timer"
            else " (다음 분 첫 틱 도착 시 — 유예 뒤 도착 틱을 안 버리는 대신 발행이 늦다)"
        ),
        flush=True,
    )

    bus = MessageBus(cfg.redis_url, cfg.instance_id)
    await bus.connect()

    archiver = ParquetArchiver(_DATA_DIR)
    collector = TickCollector(
        creds=creds,
        symbol=symbol,
        tr_id=tr_codes.WS_TR_FUTURES_CONTRACT,
        parse_tick=parse_futures_ticks,
        tick_size=tick_size,
        archiver=archiver,
        bus=bus,
    )
    composer = MultiHorizonBarComposer(
        symbol=symbol,
        archiver=archiver,
        bus=bus,
        # 상위 Horizon 경계 판정을 로컬 시계가 아니라 **거래소 시각**으로 하기 위한 배선
        # (2026-08-05) — 수집기가 매 프레임 재는 값을 그대로 본다.
        clock_skew_seconds=collector.clock_skew_seconds,
    )
    # 사이드카는 **한 곳에서만** 만든다 (`features/sidecar.build()` docstring이 호출처 넷을
    # 이름으로 적어 뒀고 그중 하나가 여기다). 이 줄이 없던 동안 `feature_set`을
    # `v2026.08-ev`로 올리면 엔진이 "사이드카 ['calendar']가 주입되지 않았다"로 **기동을
    # 거부**했다 — EV 계산기도 피처셋 정의도 이미 다 있었는데, 정본을 안 부르는 소비자
    # 하나가 그 전환을 막고 있었다(2026-08-10 B-4).
    # **엔진을 만들기 전에** 무슨 모양으로 뜨려는지 찍는다 (2026-08-11 F-1) — 사이드카가
    # 빠져 엔진이 기동을 거부하면 이 줄이 마지막 단서가 되고, 그때 필요한 정보가 정확히
    # "어떤 피처셋이 무슨 사이드카를 요구했나"다.
    resolved_spec = feature_spec.resolve(cfg.feature_set)
    print(resolved_spec.describe(), flush=True)
    engine = FeatureEngine(
        symbol,
        bus,
        feature_set=cfg.feature_set,
        sidecars=sidecar.build(resolved_spec),
    )
    # 체결틱 원본 적재 (2026-08-04, F2). 지금까지 이 프로젝트는 틱을 한 번도 저장한 적이
    # 없다 — 받아서 분봉으로 집계하고 버렸다. 그래서 MS(마이크로구조) 30개가 통째로
    # 미착수였는데, 정작 호가는 매 틱 프레임(idx34~37)에 실려 오고 있었다.
    # 봉과 달리 **소급이 불가능**하므로 오늘 안 켜면 오늘치는 영원히 없다.
    tick_archiver = TickArchiver(_TICK_DIR, symbol)
    print(
        f"체결틱 원본 적재 결선 — {symbol} → {_TICK_DIR} (버퍼 {tick_archiver.buffered}행 시작)",
        flush=True,
    )

    # REST 폴링 3종 — 파생 장중 수급(1분 격자) + 옵션체인 시리즈별(주기·위상 분리).
    # 근거는 `_option_chain_plan()` 위 주석과 `_RestCollection` docstring. 클라이언트를
    # 하나만 만들어 공유하는 것이 핵심이다(페이서가 갈리면 실효 호출률이 배수로 뛴다).
    rest = _build_rest_collection(
        creds,
        bus,
        symbol,
        tick_size,
        archiver=archiver,
        today=today,
        # 그날 계약(2026-08-07 고도화 1)의 재료 — `universe:` 선언이 여기서 처음으로
        # "오늘 이 계열이 있어야 하는가"에 실제로 쓰인다.
        universe_tokens=list(cfg.universe),
    )

    # 첫 틱이 들어오기 전에 끝내야 한다 — 웜업 구간(09:00 이전)에 부르는 이유가 그것이다.
    await asyncio.to_thread(_load_warmup_artifacts, engine, archiver, symbol, today)
    await asyncio.to_thread(_restore_composer_buckets, composer, symbol, today)

    now = now_kst()
    session_stop = _today_at(now, *REGULAR_SESSION_STOP)
    hard_deadline = _today_at(now, *HARD_SHUTDOWN_DEADLINE)

    remaining = (session_stop - now_kst()).total_seconds()
    if remaining > 0:
        print(f"정규장 수집 시작 — {session_stop.isoformat()}까지 ({remaining:.0f}초)", flush=True)
        try:
            await asyncio.wait_for(
                _run_regular_session(
                    collector,
                    composer,
                    engine,
                    bus,
                    today.strftime("%Y%m%d"),
                    symbol,
                    rest,
                    tick_archiver,
                    cfg.minute_bar_close,
                    launched_ui.port,
                ),
                timeout=remaining,
            )
        except TimeoutError:
            print("수집 중단 신호 도달 — 장후 종료 절차 시작", flush=True)
    else:
        print(f"이미 {session_stop.isoformat()} 이후 — 수집 생략, 바로 종료 절차", flush=True)

    shutdown_budget = max((hard_deadline - now_kst()).total_seconds(), 30.0)
    try:
        await asyncio.wait_for(
            _daily_close(collector, composer, bus, rest, tick_archiver, engine),
            timeout=shutdown_budget,
        )
    except TimeoutError:
        mlog.log(
            "DailyCloseTimeout",
            f"daily_close()가 {shutdown_budget:.0f}초 내에 못 끝남 — 강제 종료",
        )
        raise SystemExit(1) from None

    # 통합 → **재합성** → 리포트 순서. 조각이 남아 있어도 `read_day()`가 읽으므로 통합과
    # 리포트의 선후는 결과를 안 바꾸지만, **재합성은 반드시 리포트보다 앞**이어야 한다 —
    # 그래야 `horizon_findings`가 "지금 아카이브가 정합한가"를 말한다(2026-08-06 P0-3b).
    _compact_archive(archiver, symbol, today)
    _recompose_today(archiver, symbol, today)
    _write_integrity_report(today, symbol, cfg.instance_id)
    # 정상 종료를 **구조화 로그로** 남긴다 (2026-08-07 P0-3). 이 한 줄이 없으면 리포트가
    # "정상 종료"와 "죽어서 사라짐"을 구분할 근거가 없다 — `ops/observation_gaps.py`가
    # 스스로 적어 둔 한계("마지막 기동 이후 조용히 사라진 경우는 안 센다")가 정확히 그것이고,
    # 2026-08-07에 그 한계 때문에 1시간 54분 유실이 `관측 공백: 없음 ✅`으로 지나갔다.
    mlog.log("SessionEnd", "정상 종료", process="l1_daily")
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
