"""L1 데이터 파이프라인 일일 운영 — 장전 웜업 → 장중 수집 → 장후 종료.

Master Plan Ver 2.0 §9 W6~8까지의 산출물(TickCollector·MultiHorizonBarComposer·
FeatureEngine)을 실제 매매일 하루 동안 무인으로 돌리기 위한 진입점. 지금까지는 전부
스크래치 스크립트로 손으로 실행해 검증했을 뿐, "장전에 대기하다 자동으로 켜지는" 운영
흐름 자체는 없었다 — 이 스크립트가 그 자리를 채운다.

시간대(KST, 전부 하드코딩 아님 — 아래 상수만 바꾸면 됨):
- 08:35(작업 스케줄러 "Messiah" 실제 트리거 시각) ~ 09:00: 웜업 — **Docker Desktop 응답
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
from dataclasses import dataclass
from datetime import date, datetime
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
    is_ui_already_running,
    launch_command_center,
    watch_command_center_forever,
)
from messiah.data.archiver import ParquetArchiver  # noqa: E402
from messiah.data.bar_composer import MultiHorizonBarComposer  # noqa: E402
from messiah.data.collector import TickCollector  # noqa: E402
from messiah.data.flow_archiver import InvestorFlowArchiver  # noqa: E402
from messiah.data.investor_flow_poller import InvestorFlowPoller  # noqa: E402
from messiah.data.last_price import LastPriceTracker  # noqa: E402
from messiah.data.normalizer import parse_futures_ticks  # noqa: E402
from messiah.data.option_chain_archiver import OptionChainArchiver  # noqa: E402
from messiah.data.option_chain_poller import OptionChainPoller  # noqa: E402
from messiah.features.engine import FeatureEngine  # noqa: E402
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
# 수급 폴링 격자 — 1분봉과 같은 주기. 3업종 순차 조회라 유량(모의투자 1건/초)에
# 여유가 크고, 이보다 촘촘히 받아도 원천이 "당일 누적"이라 정보가 늘지 않는다.
_FLOW_POLL_SECONDS = 60.0

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
        loaded = engine.warm_start(history)
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


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _ui_log_path(today_str: str) -> Path:
    return Path("logs") / f"ui_{today_str}.log"


def _launch_ui(today_str: str) -> subprocess.Popen | None:
    """`core/ui_launcher.py`의 얇은 래퍼 — 이 프로세스(데이터 수집)와 화면은 서로
    독립적이다. 중복 기동 방지(포트 응답 확인)는 공용 모듈이 담당한다(2026-07-30 추가,
    `run_g2_paper_trading.py`와 UI를 동시에 켰을 때의 실측 발견 대응)."""
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

    @property
    def requests_per_second(self) -> float:
        """정상 상태의 평균 REST 수요 — 기동 로그가 찍는 값."""
        rps = 0.0
        if self.flow_poller is not None:
            rps += 3 / _FLOW_POLL_SECONDS  # 3업종
        for poller, period, _ in self.chain_pollers:
            rps += poller.legs_per_cycle / period
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


def _build_rest_collection(
    creds: KISCredentials, bus: MessageBus, symbol: str, tick_size: Decimal
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
        plan = _option_chain_plan(now_kst().date())
        chain_pollers = tuple(
            (
                OptionChainPoller(
                    client,
                    master,
                    bus,
                    series=series,
                    reference_price=lambda: tracker.price_points(),
                    strike_window=_OPTION_STRIKE_WINDOW,
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
    )
    print(
        f"수급 수집 결선 — {tr_codes.FID_MRKT_DIV_DERIVATIVES} "
        f"3업종 / {_FLOW_POLL_SECONDS:.0f}초 격자 → {_FLOW_DIR}",
        flush=True,
    )
    for poller, period, phase in chain_pollers:
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

    async def _report_ui_gave_up() -> None:
        await HealthReporter(
            bus,
            "l1.command_center_ui",
            probe=lambda: HealthStatus(
                HealthLevel.CRITICAL, "자동 재기동 한도 소진 — 화면 없음, 수동 확인 필요"
            ),
        ).publish_once()

    await asyncio.gather(
        collector.run_forever(),
        composer.run_forever(),
        engine.run_forever(),
        watch_command_center_forever(
            caller_tag="run_l1_daily",
            project_root=_PROJECT_ROOT,
            log_path=_ui_log_path(today_str),
            on_gave_up=_report_ui_gave_up,
        ),
        # 컴포넌트 이름은 상수로 — G2의 `TradingPipeline`이 이 heartbeat를 구독해 CB 오탐을
        # 억제한다(`strategy/pipeline.py` "한산과 단절"). 문자열이 갈리면 조용히 결선이 끊긴다.
        HealthReporter(bus, COLLECTOR_COMPONENT, probe=collector.health).run_forever(),
        HealthReporter(bus, "l1.feature_engine", probe=engine.health).run_forever(),
        # 헤드리스 상태판 (2026-08-03 고도화 A) — UI가 하던 구독을 이 프로세스로 옮겨
        # `logs/status_snapshot.json`에 주기적으로 남긴다. 화면이 죽어도(07-30 32분,
        # 07-31 3시간) 관측은 계속되고, 15:40에 UI가 종료된 뒤의 장후 리뷰도 가능해진다.
        # UI 생사까지 같은 스냅샷에 기록한다 — 화면 없이 화면 상태를 안다.
        run_status_board_forever(bus, symbol=symbol, ui_probe=is_ui_already_running),
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

    실패해도 종료 절차를 막지 않는다 — 리포트는 관측 수단이지 운영 전제조건이 아니다."""
    try:
        generate_and_write(day=today, symbol=symbol, instance_id=instance_id)
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


async def _daily_close(
    collector: TickCollector,
    composer: MultiHorizonBarComposer,
    bus: MessageBus,
    rest: _RestCollection | None = None,
) -> None:
    await collector.flush_final_bar()
    await composer.flush_all_final()
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
    crash_forensics.enable(tag="l1_daily")
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
    composer = MultiHorizonBarComposer(symbol=symbol, archiver=archiver, bus=bus)
    engine = FeatureEngine(symbol, bus, feature_set=cfg.feature_set)

    # REST 폴링 3종 — 파생 장중 수급(1분 격자) + 옵션체인 시리즈별(주기·위상 분리).
    # 근거는 `_option_chain_plan()` 위 주석과 `_RestCollection` docstring. 클라이언트를
    # 하나만 만들어 공유하는 것이 핵심이다(페이서가 갈리면 실효 호출률이 배수로 뛴다).
    rest = _build_rest_collection(creds, bus, symbol, tick_size)

    # 첫 틱이 들어오기 전에 끝내야 한다 — 웜업 구간(09:00 이전)에 부르는 이유가 그것이다.
    await asyncio.to_thread(_load_warmup_artifacts, engine, archiver, symbol, today)

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
            _daily_close(collector, composer, bus, rest), timeout=shutdown_budget
        )
    except TimeoutError:
        mlog.log(
            "DailyCloseTimeout",
            f"daily_close()가 {shutdown_budget:.0f}초 내에 못 끝남 — 강제 종료",
        )
        raise SystemExit(1) from None

    # 통합 → 리포트 순서 (조각이 남아 있어도 `read_day()`가 읽으므로 순서가 결과를 바꾸지는
    # 않지만, 리포트가 최종 물리 배치를 보고 산출되는 편이 사후 조사와 일치한다)
    _compact_archive(archiver, symbol, today)
    _write_integrity_report(today, symbol, cfg.instance_id)
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
