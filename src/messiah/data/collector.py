"""L1 Collector — KIS WS 실시간체결가 구독 → 정규화 → 완성봉 적재/발행 오케스트레이션
(Master Plan Ver 2.0 §9 "L1 DATA: Collector").

`TickCollector`는 단일 연결·단일 심볼용 골격이다. 마흐디 mahdi/main.py가
run_observation_loop(단일 연결, 끊기면 예외 전파)과 run_observation_loop_forever(재연결
래퍼)를 분리한 것과 같은 설계로, run_once()가 전자에 해당하고 run_forever()가 후자에
해당한다(2026-07-23 추가 — 그 전까지는 run_once만 있었다, NEXT_TODO 참고).

`MultiSymbolTickCollector`(2026-07-27 신설)는 여러 심볼/TR을 **연결 하나**로 동시 수집한다
— 2026-07-23 실측으로 확인된 "같은 계좌로 WS 연결을 2개(선물+옵션) 열면 양쪽 다 몇 초
간격으로 반복 단절된다"는 문제(`ws_client.py`의 `KISWebSocketClient.subscribe()`가 처음부터
호출을 여러 번 지원하도록 설계돼 있었음에도, `TickCollector`가 인스턴스당 심볼 1개로 고정돼
있어 이 설계를 못 썼다)의 구조적 해법이다. ATM±N 옵션 체인 구독 롤링
(RollingSubscriptionManager 이식) 자체는 여전히 범위 밖 — 이 클래스는 생성 시 주어진
고정 심볼 목록만 구독한다(체인을 시세에 따라 동적으로 갈아끼우는 롤링은 별도 과제).

## 조용한 스톨 탐지 (2026-07-30 추가)

재연결은 "소켓이 끊기면"만 작동한다 — 그런데 실측된 장애는 그 형태가 아니었다: 2026-07-28
10:13~10:43, 2026-07-29 12:32~13:02 두 번 모두 **소켓은 살아 있는데 틱만 30분간 안 들어왔고**,
프로세스는 그 30분 동안 로그를 단 한 줄도 남기지 않았다(1분봉 아카이브에 그대로 구멍이 났고,
`FeaturePublish`도 12:31:38 → 13:02:11로 끊겼다). 예외가 안 나니 `run_forever()`의 재연결
경로가 아예 안 타는 것이다.

`_StallWatchdog`이 그 자리를 메운다 — 마지막 틱 이후 경과를 벽시계로 감시하다 임계를 넘으면
`TickStallError`(= `ConnectionError`, 즉 `OSError` 계열)를 던져 **기존 재연결 경로를 그대로
태운다**. 새 복구 메커니즘을 만드는 게 아니라, 이미 있는 것이 발동하지 못하던 구멍을 막는 것.

콜드스타트 가드는 `strategy/pipeline.py`의 CB 워치독과 같은 논리다 — 첫 틱을 받기 전에는
기준선이 없으므로 판정하지 않는다. 이게 없으면 08:35 기동 후 첫 틱(실측 08:45)까지의 10분을
스톨로 오판해 무한 재연결 루프에 빠진다.

**임계는 2026-07-31부터 고정값이 아니라 최근 관측된 한산함에서 파생된다** — 그날 상한가
고착 구간(분당 1~17계약)에서 고정 120초가 정상 상태를 6회 오판했고, 그 재연결들이 오히려
결손을 키웠다. 상세와 근거는 `_StallWatchdog` 클래스 docstring "적응 임계" 절 참고.

## 데이터 흐름의 1차 책임은 수집기에 있다 (고도화 4, 2026-07-30)

2026-07-30 점검에서 드러난 구조적 문제는 **탐지와 복구가 서로 다른 프로세스에 흩어져 있고
연결돼 있지 않다**는 것이었다: 데이터 단절을 감지하는 코드는 G2의 `CircuitBreakerMonitor`
(`strategy/pipeline.py`)에 있는데, 그걸 실제로 고칠 수 있는 유일한 곳 — WS 재연결 — 은 L1의
이 파일에 있었다. 그래서 07-28·07-29의 30분 공백은 "아무도 감지하지 못했고, 감지했더라도
아무도 고칠 수 없었던" 상태였다.

계층을 이렇게 나눈다:

- **L1 수집기(이 파일)** — 데이터 흐름의 **탐지 + 복구**: 스톨 판정, 강제 재연결,
  `sys.health`로 자기 상태 보고. 마지막 틱 시각을 아는 유일한 곳이라 여기가 맡는다.
- **G2 CB 모니터** — 그 위에서 **매매 판단**: 신규진입 차단·자동청산·재진입 관망. 포지션과
  게이트웨이를 아는 유일한 곳이라 거기가 맡는다.

즉 "데이터가 흐르는가"는 여기서 판정하고(`health()`), "그래서 거래를 어떻게 할 것인가"는
G2가 판정한다. 두 판정이 어긋나면(예: 여기가 CRITICAL인데 CB는 정상) 둘 중 하나가 틀린
것이므로, 일일 무결성 리포트가 그 불일치를 따로 잡아낸다(`ops/integrity_report.py`의
`analyze_data_flow_ownership()`).

**의도적으로 하지 않은 것**: CB의 판정 입력을 이 컴포넌트의 heartbeat로 바꾸지 않았다.
리스크 경로 변경이라 별도 합의가 필요하고, 지금은 두 판정이 독립적으로 서 있으면서 어긋날
때 드러나는 편이 안전하다(한쪽 버그가 조용히 다른 쪽을 오염시키지 않는다).
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Awaitable, Callable, Sequence

import websockets

from messiah.broker.kis import tr_codes
from messiah.broker.kis.credentials import KISCredentials
from messiah.broker.kis.ws_client import ApprovalKeyIssuer, KISWebSocketClient, Subscription
from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_BAR, TOPIC_TICK, MessageBus
from messiah.core.health import HealthStatus, staleness_status
from messiah.core.messages import BarClosed, Horizon, Tick
from messiah.core.timeutil import now_kst
from messiah.data.archiver import ParquetArchiver
from messiah.data.normalizer import MinuteBarAggregator

# websockets 라이브러리 자체 예외(ConnectionClosed 등)는 WebSocketException 계열, 소켓 단의
# 실패(연결 거부 등)는 OSError(ConnectionError는 그 서브클래스) 계열이다 — mahdi
# _WS_DISCONNECT_ERRORS와 동일 판단: 재연결 대상은 이 둘뿐이고, ValueError(구독 슬롯 한도 등)
# 같은 코드/설정 문제는 재시도로 해결되지 않으니 여기서 안 잡고 그대로 전파한다.
_WS_DISCONNECT_ERRORS = (OSError, websockets.WebSocketException)
_WS_RECONNECT_INITIAL_BACKOFF_SECONDS = 5.0
_WS_RECONNECT_MAX_BACKOFF_SECONDS = 60.0

# 스톨 임계의 **하한** — 정규장 미니선물은 가장 한산한 구간에도 분당 수십 틱이 들어온다는
# 2026-07-30 실측(최저 54틱/분)에서 나온 값이다. 30초 격자로 확인한다(CB 워치독과 같은 주기).
#
# 이 값을 **고정 임계로 쓰는 것은 2026-07-31에 틀린 것으로 판명됐다** — 아래 `_StallWatchdog`
# "적응 임계" 절 참고. 이제 하한으로만 쓰고, 실제 임계는 최근 관측된 한산함에서 파생된다.
_TICK_STALL_TIMEOUT_SECONDS = 120.0
_TICK_STALL_CHECK_INTERVAL_SECONDS = 30.0

# 적응 임계 파라미터 (2026-07-31 신설, 전부 미검증 초기값)
_STALL_QUIET_WINDOW_SECONDS = 1800.0  # 최근 30분의 틱 도착 이력만 본다
_STALL_QUIET_SLACK = 2.0  # 그 구간 최장 무틱 간격의 2배까지는 "정상적인 한산함"
_STALL_MAX_TIMEOUT_SECONDS = 600.0  # 아무리 한산해도 10분을 넘기면 진짜 이상이다
_STALL_TICK_HISTORY = 2048  # 메모리 상한(바쁜 구간에선 30분보다 짧은 창이 된다 — 무해)
_STALL_RELIEF_TICKS = 10  # 재연결 후 이만큼 틱이 다시 들어오면 "그 재연결은 효과가 있었다"
_STALL_MAX_PENALTY_DOUBLINGS = 3  # 연속 무효 재연결 페널티 상한(×8)


class TickStallError(ConnectionError):
    """소켓은 열려 있으나 틱이 끊긴 상태 — `ConnectionError`(→`OSError`)를 상속해
    `_WS_DISCONNECT_ERRORS`에 자연히 걸린다. 재연결 경로를 새로 만들지 않고 기존 것을
    재사용하기 위한 의도적 선택이다."""


class _StallWatchdog:
    """마지막 틱 이후 경과를 감시 — `TickCollector`/`MultiSymbolTickCollector` 공용.

    두 수집기가 같은 listen 루프 구조를 갖고 있어 각자 구현하면 갈라진다(한쪽만 고쳐지는
    사고가 실제로 이 파일에서 이미 한 번 있었다 — 모듈 docstring의 `run_forever` 이력 참고).

    ## 적응 임계 (2026-07-31 실측 대응)

    임계는 원래 고정 120초였다. 근거는 2026-07-30 실측 "정규장 최저 54틱/분"이었는데, **그
    관측이 시장 전체를 대표하지 않았다.** 2026-07-31 오후 A05608은 14:21부터 마감까지 가격이
    51814틱(= 그날 고가, 10:06에 처음 닿은 뒤 한 번도 안 넘김)에 완전히 고정된 채 **분당
    1~17계약**만 체결됐다 — 상한가/일방시장에 준하는 상태다. 그 구간에서 고정 임계 120초는
    "정상적인 한산함"을 장애로 오판했고, 워치독은 6회 강제 재연결을 걸었다.

    문제는 그 치료가 병보다 비쌌다는 것이다 — 재연결 자체는 매번 5~8초 만에 성공했지만
    **재연결 후 첫 틱까지가 오래 걸렸다**: 14:53:37 스톨 → 14:56:14 첫 틱(2분 37초),
    15:08:26 스톨 → 15:11:06 첫 틱(2분 40초). 그날 결손 30분·최장 공백 8분(15:04~15:13)의
    상당 부분이 이 워치독이 만든 것이다.

    그래서 임계를 **최근 관측된 한산함에서 파생**시킨다:

        임계 = clamp(최근 30분 최장 무틱 간격 × 2, 하한 120초, 상한 600초) × 2^(연속 무효 재연결)

    - **최장(max) 간격**을 쓰는 이유: 중앙값을 쓰면 "가끔 2분씩 조용한 시장"에서 임계가
      여전히 낮게 잡혀 같은 오탐이 난다. 최근에 실제로 일어난 최악의 정적을 정상 범위로
      인정해야 오탐이 사라진다.
    - **바쁜 구간에서는 자동으로 하한(120초)으로 수렴한다** — 틱이 초당 여러 개면 최장
      간격 자체가 1초 미만이라 곱해도 하한을 못 넘는다. 즉 진짜 30분 공백(2026-07-28·29)을
      2분 만에 잡던 성질은 그대로 유지된다.
    - **상한 600초**: 아무리 한산해도 10분 무틱은 시장 상태가 아니라 장애로 본다.
    - **연속 무효 재연결 페널티**: 재연결했는데 틱이 다시 안 들어오면(= `_STALL_RELIEF_TICKS`
      개를 못 채우면) 그 재연결은 효과가 없었다는 뜻이다. 임계를 배로 늘려 같은 실패를
      반복하지 않는다(2026-07-31의 6연속 무의미한 재연결이 이 경우).

    이력(`_tick_times`)은 `reset()`으로 안 지운다 — 그건 **연결의 상태가 아니라 시장의
    상태**라 재연결로 무효화되지 않는다. 다만 `_last_tick_at`은 지우므로 연결 경계를 넘는
    가짜 간격이 이력에 들어가지는 않는다.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float,
        check_interval_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        quiet_window_seconds: float = _STALL_QUIET_WINDOW_SECONDS,
        quiet_slack: float = _STALL_QUIET_SLACK,
        max_timeout_seconds: float = _STALL_MAX_TIMEOUT_SECONDS,
        relief_ticks: int = _STALL_RELIEF_TICKS,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._check_interval_seconds = check_interval_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._quiet_window_seconds = quiet_window_seconds
        self._quiet_slack = quiet_slack
        self._max_timeout_seconds = max_timeout_seconds
        self._relief_ticks = relief_ticks
        self._last_tick_at: float | None = None
        self._tick_times: deque[float] = deque(maxlen=_STALL_TICK_HISTORY)
        # (관측시각, 직전 틱과의 간격) — 간격을 **따로** 쌓는 이유는 재연결 경계 때문이다.
        # `_tick_times`의 연속 두 값에서 간격을 역산하면 단절 구간 자체가 "이 시장은 이만큼
        # 조용하다"는 증거로 둔갑해 임계를 스스로 밀어올린다(다음 장애 감지가 느려진다).
        # `mark_tick()`이 `_last_tick_at is None`(= reset 직후)일 때 간격을 기록하지 않으므로
        # 연결을 넘는 간격은 애초에 여기 안 들어온다.
        self._intervals: deque[tuple[float, float]] = deque(maxlen=_STALL_TICK_HISTORY)
        self._consecutive_stalls = 0
        self._ticks_since_stall = 0

    @property
    def enabled(self) -> bool:
        return self._timeout_seconds > 0

    def reset(self) -> None:
        """새 연결마다 호출 — 이전 연결의 마지막 틱 시각을 기준선으로 쓰면 안 된다.
        틱 도착 이력은 남긴다(위 docstring "이력은 reset으로 안 지운다")."""
        self._last_tick_at = None

    def mark_tick(self) -> None:
        now = self._monotonic()
        if self._last_tick_at is not None:  # reset 직후엔 간격을 안 남긴다(위 `_intervals` 주석)
            self._intervals.append((now, now - self._last_tick_at))
        self._last_tick_at = now
        self._tick_times.append(now)
        if self._consecutive_stalls:
            self._ticks_since_stall += 1
            if self._ticks_since_stall >= self._relief_ticks:
                # 재연결이 실제로 효과가 있었다 — 페널티 해제
                self._consecutive_stalls = 0
                self._ticks_since_stall = 0

    @property
    def seen_first_tick(self) -> bool:
        return self._last_tick_at is not None

    def seconds_since_last_tick(self) -> float | None:
        """None은 "이 연결에서 아직 틱을 못 봤다" — "0초 전에 받았다"와 구분된다."""
        if self._last_tick_at is None:
            return None
        return self._monotonic() - self._last_tick_at

    def recent_max_gap_seconds(self) -> float:
        """최근 창에서 실제로 관측된 최장 무틱 간격 — 표본이 없으면 0(= 임계는 하한 그대로)."""
        cutoff = self._monotonic() - self._quiet_window_seconds
        return max((gap for at, gap in self._intervals if at >= cutoff), default=0.0)

    def ticks_within(self, seconds: float) -> int:
        """최근 `seconds`초 안에 받은 틱 수 — 스톨 로그의 진단 재료.

        `_STALL_TICK_HISTORY` 상한 때문에 아주 바쁜 구간에서는 실제보다 작게 나올 수 있다.
        이 값의 용도는 "한산함의 정도"를 남기는 것이라 작은 쪽이 정확하면 충분하다.
        """
        cutoff = self._monotonic() - seconds
        return sum(1 for t in self._tick_times if t >= cutoff)

    def current_timeout_seconds(self) -> float:
        """지금 적용 중인 스톨 임계 — 감시·헬스 판정이 같은 값을 쓰도록 한 곳에서만 계산한다."""
        if not self.enabled:
            return self._timeout_seconds
        adaptive = max(self._timeout_seconds, self.recent_max_gap_seconds() * self._quiet_slack)
        penalty = 2 ** min(self._consecutive_stalls, _STALL_MAX_PENALTY_DOUBLINGS)
        return min(adaptive * penalty, self._max_timeout_seconds)

    async def run_until_stalled(self, *, describe: str) -> None:
        """스톨을 감지하면 `TickStallError`를 던진다 — 그 전까지는 영원히 돌아간다."""
        while True:
            await self._sleep(self._check_interval_seconds)
            if self._last_tick_at is None:
                continue  # 콜드스타트/워밍업 — 기준선 없음(모듈 docstring 근거)
            age = self._monotonic() - self._last_tick_at
            threshold = self.current_timeout_seconds()
            if age >= threshold:
                self._consecutive_stalls += 1
                self._ticks_since_stall = 0
                mlog.log(
                    "CollectorTickStall",
                    f"소켓은 열려 있으나 {age:.0f}초간 틱 없음 — 강제 재연결"
                    f"(임계 {threshold:.0f}초)",
                    stalled_seconds=age,
                    threshold_seconds=threshold,
                    recent_max_gap_seconds=self.recent_max_gap_seconds(),
                    ticks_last_60s=self.ticks_within(60.0),
                    ticks_last_300s=self.ticks_within(300.0),
                    consecutive_stalls=self._consecutive_stalls,
                    **({"symbol": describe} if describe else {}),
                )
                raise TickStallError(f"{age:.0f}초간 틱 수신 없음(소켓은 열려 있음)")


async def _listen_with_stall_watchdog(
    client: KISWebSocketClient,
    handler: Callable[[dict], Awaitable[None]],
    watchdog: _StallWatchdog,
    *,
    describe: str,
) -> None:
    """`client.listen()`과 스톨 감시를 동시에 돌리고, 먼저 끝난 쪽의 결과를 전파한다.

    감시가 먼저 끝나면(= 스톨) listen을 취소하고 `TickStallError`를 올린다 — 호출측
    `run_once()`의 `async with`가 소켓을 닫고, `run_forever()`가 백오프 후 재연결한다.
    """
    if not watchdog.enabled:
        await client.listen(handler)
        return

    listen_task = asyncio.create_task(client.listen(handler))
    watchdog_task = asyncio.create_task(watchdog.run_until_stalled(describe=describe))
    try:
        done, pending = await asyncio.wait(
            {listen_task, watchdog_task}, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        for task in (listen_task, watchdog_task):
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    for task in done:
        task.result()  # 예외가 있으면 여기서 전파(TickStallError 포함)


class TickCollector:
    def __init__(
        self,
        creds: KISCredentials,
        symbol: str,
        tr_id: str,
        parse_tick: Callable[..., list[Tick]],
        tick_size: Decimal,
        archiver: ParquetArchiver,
        bus: MessageBus | None = None,
        horizon: Horizon = Horizon.M1,
        approval_issuer: ApprovalKeyIssuer | None = None,
        ws_connect: Callable[[str], Any] = websockets.connect,
        reconnect_initial_backoff_seconds: float = _WS_RECONNECT_INITIAL_BACKOFF_SECONDS,
        reconnect_max_backoff_seconds: float = _WS_RECONNECT_MAX_BACKOFF_SECONDS,
        stall_timeout_seconds: float = _TICK_STALL_TIMEOUT_SECONDS,
        stall_check_interval_seconds: float = _TICK_STALL_CHECK_INTERVAL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """
        입력: tr_id/parse_tick은 짝을 맞춰 넘긴다 — 선물은
             (tr_codes.WS_TR_FUTURES_CONTRACT, normalizer.parse_futures_ticks), 옵션은
             (tr_codes.WS_TR_OPTION_CONTRACT, normalizer.parse_option_ticks). 파서는 프레임
             1건에서 **Tick 목록**을 낸다(한 프레임에 체결 여러 건). bus를 생략하면
             Redis 발행 없이 Parquet 적재만 한다(테스트·오프라인 실행에 유용). ws_connect는
             테스트에서 실제 네트워크 없이 가짜 연결을 주입하기 위한 것(기본값
             websockets.connect). reconnect_*_backoff_seconds는 run_forever() 전용 — 테스트가
             실제로 몇 초씩 기다리지 않도록 주입 가능하게 노출(기본값은 mahdi와 동일한 5~60초).
             stall_timeout_seconds=0이면 스톨 감시를 끈다(모듈 docstring "조용한 스톨 탐지").
             0이 아니면 그 값은 **임계의 하한**이고 실제 임계는 최근 한산함에서 파생된다
             (`_StallWatchdog` "적응 임계"); monotonic/sleep은 그 감시를 실시간 대기 없이
             테스트하기 위한 주입점.
        """
        self._creds = creds
        self._symbol = symbol
        self._tr_id = tr_id
        self._parse_tick = parse_tick
        self._tick_size = tick_size
        self._archiver = archiver
        self._bus = bus
        self._horizon = horizon
        self._approval_issuer = approval_issuer or ApprovalKeyIssuer(creds)
        self._ws_connect = ws_connect
        self._reconnect_initial_backoff_seconds = reconnect_initial_backoff_seconds
        self._reconnect_max_backoff_seconds = reconnect_max_backoff_seconds
        self._aggregator = MinuteBarAggregator(symbol, horizon)
        self._watchdog = _StallWatchdog(
            timeout_seconds=stall_timeout_seconds,
            check_interval_seconds=stall_check_interval_seconds,
            monotonic=monotonic,
            sleep=sleep,
        )

    async def run_once(self, on_connected: Callable[[], None] | None = None) -> None:
        """
        입력: on_connected는 구독까지 성공한 직후 호출되는 선택적 훅(run_forever()가 재연결
             성공을 감지해 백오프를 리셋하는 용도 — 일반 호출에서는 생략).
        계산: approval_key 발급(동기 httpx 호출이라 asyncio.to_thread로 감쌈) → WS 연결 →
             구독 → listen()의 무한 수신 루프.
        실패 조건: 연결이 끊기면(또는 다른 예외) 그대로 전파된다 — 이 메서드 자체는 재연결하지
                  않는다(run_forever()가 감싸서 처리, 모듈 docstring 참고).
        """
        approval_key = await asyncio.to_thread(self._approval_issuer.issue)
        async with self._ws_connect(tr_codes.MARKET_DATA_WS_DOMAIN) as ws:
            client = KISWebSocketClient(approval_key, ws)
            await client.subscribe(Subscription(self._tr_id, self._symbol))
            if on_connected is not None:
                on_connected()
            self._watchdog.reset()  # 이 연결의 첫 틱부터 새로 센다
            await _listen_with_stall_watchdog(
                client, self._handle_message, self._watchdog, describe=self._symbol
            )

    async def run_forever(self) -> None:
        """
        계산: run_once()를 감싸 WS 단절(_WS_DISCONNECT_ERRORS)마다 프로세스를 죽이는 대신
             지수 백오프(기본 5초→최대 60초) 후 재연결한다 — mahdi run_observation_loop_forever와
             동일 설계. approval_key도 재연결마다 새로 발급한다(run_once()를 통째로 재호출하므로
             — 장시간 연결 유지 중 만료됐을 가능성을 배제). 재연결(구독까지 성공)마다
             on_connected 훅으로 백오프를 초기값으로 리셋하고, 끊긴 상태에서 다시 이어졌을
             때만(연속 재시도 중 매번이 아니라) CollectorWSReconnected를 1회 로깅한다.
             재연결 시 이전 연결에서 누적 중이던 미완성 분봉은 버린다(aggregator 재생성) —
             그 분은 이미 관측 공백이 낀 구간이라 새 연결의 첫 틱과 섞이면 안 되고, 부분
             데이터로 quality_ok=True인 봉을 만드는 것도 부정확하다.
        실패 조건: OSError/websockets.WebSocketException 이외의 예외(ValueError 등 코드/설정
                  문제)는 재시도 없이 그대로 전파된다 — 재시도로 해결되지 않는 문제라 사람이
                  봐야 한다.
        """
        backoff = self._reconnect_initial_backoff_seconds
        was_disconnected = False

        def _on_connected() -> None:
            nonlocal backoff, was_disconnected
            if was_disconnected:
                mlog.log(
                    "CollectorWSReconnected", "WS 재연결 성공 — 수신 재개", symbol=self._symbol
                )
                was_disconnected = False
            backoff = self._reconnect_initial_backoff_seconds

        while True:
            try:
                await self.run_once(on_connected=_on_connected)
                return  # listen()은 정상 반환하지 않지만 방어적으로 그대로 종료
            except _WS_DISCONNECT_ERRORS as exc:
                if not was_disconnected:
                    mlog.log(
                        "CollectorWSDisconnected",
                        f"WS 연결 끊김 — {backoff:.0f}초 후 재연결 시도: {exc}",
                        symbol=self._symbol,
                    )
                    was_disconnected = True
                self._aggregator = MinuteBarAggregator(self._symbol, self._horizon)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._reconnect_max_backoff_seconds)

    async def flush_final_bar(self) -> None:
        """graceful shutdown 시 마지막 미완성 분봉을 강제 flush — 호출측(재연결 래퍼 등)이
        종료 시퀀스에서 부른다."""
        bar = self._aggregator.flush_final()
        if bar is not None:
            await self._archive_and_publish_bar(bar)

    def seconds_since_last_tick(self) -> float | None:
        """None은 "이 연결에서 아직 틱을 못 봤다"(웜업) — "0초 전에 받았다"와 구분된다."""
        return self._watchdog.seconds_since_last_tick()

    def health(self) -> HealthStatus:
        """`sys.health` heartbeat용 자가 판정 (고도화 4 — 데이터 흐름의 1차 책임은 수집기에
        있다). 임계는 스톨 워치독과 **같은 값**을 쓴다: WARN은 그 절반 지점이라 "곧 강제
        재연결이 일어날 것"을 화면이 미리 보여주고, CRITICAL이 뜨는 시점이 곧 재연결 시점이다.

        워치독이 적응 임계로 바뀐 뒤로는 그 값을 매번 물어본다(2026-07-31) — 고정 상수를
        그대로 쓰면 한산한 구간에 화면만 CRITICAL로 붉어지고 정작 재연결은 안 일어나는,
        "두 판정이 어긋나는" 상태가 된다."""
        threshold = self._watchdog.current_timeout_seconds()
        return staleness_status(
            self.seconds_since_last_tick(),
            warn_after=threshold / 2,
            critical_after=threshold,
            warming_up_detail="웜업 — 장 개시 전(첫 틱 대기)",
        )

    async def _handle_message(self, message: dict) -> None:
        raw = message.get("raw")
        if raw is None:
            return  # JSON 제어 메시지(구독응답/PINGPONG) — 정규화 대상 아님

        # 프레임 1건에 체결이 여러 개 묶여 올 수 있다 — 전부 처리한다(2026-08-04까지는 첫
        # 건만 처리해 거래량의 절반을 버렸다, `data/normalizer.py` 모듈 docstring).
        ticks = self._parse_tick(raw, self._tick_size)
        if not ticks:
            return  # 정규화 실패 — normalizer 계약과 동일하게 조용히 무시

        # 프레임 단위로 한 번만 — 스톨 워치독에 필요한 건 "언제 마지막으로 받았나"이지
        # 체결 건수가 아니다(같은 프레임의 N건은 같은 순간에 도착한 것).
        self._note_tick_received()

        for tick in ticks:
            if self._bus is not None:
                try:
                    await self._bus.publish(f"{TOPIC_TICK}.{tick.symbol}", tick)
                except Exception as exc:  # noqa: BLE001 — 발행 실패로 수신 루프가 죽으면 안 됨
                    mlog.log("CollectorProcessingError", f"틱 발행 실패: {exc}", symbol=tick.symbol)

            bar = self._aggregator.add_tick(tick)
            if bar is not None:
                await self._archive_and_publish_bar(bar)

    def _note_tick_received(self) -> None:
        """스톨 워치독 갱신 + 연결 후 첫 틱 시각 기록.

        첫 틱 로그는 진단용이다(2026-07-30 추가). 이 스크립트의 설계 문서는 "실제로 틱이 오기
        시작하는 건 장이 열려야 하므로 9시까지 대기 로직은 필요 없다"고 적어 뒀는데, 실측은
        3거래일 연속 **08:45:00 정각부터** 틱이 들어왔다(정규장 개시 09:00보다 15분 앞). 그
        구간 데이터가 예상체결인지 실체결인지에 따라 아카이브·피처 처리 방침이 달라지므로,
        우선 "언제부터 들어왔는지"를 매일 로그에 남겨 판단 근거를 쌓는다."""
        first = not self._watchdog.seen_first_tick
        self._watchdog.mark_tick()
        if first:
            mlog.log(
                "CollectorFirstTick",
                "연결 후 첫 틱 수신",
                symbol=self._symbol,
                received_kst=now_kst().isoformat(),
            )

    async def _archive_and_publish_bar(self, bar: BarClosed) -> None:
        """완성봉 적재/발행은 파싱과 달리 인프라 실패라 침묵하면 안 됨(L22) — 잡아서 로깅하고
        계속한다. 적재 실패와 발행 실패는 서로 독립(하나가 실패해도 다른 하나는 시도)."""
        try:
            self._archiver.append_bar(bar)
        except Exception as exc:  # noqa: BLE001
            mlog.log("CollectorProcessingError", f"완성봉 적재 실패: {exc}", symbol=bar.symbol)

        if self._bus is not None:
            try:
                await self._bus.publish(f"{TOPIC_BAR}.{bar.horizon.value}.{bar.symbol}", bar)
            except Exception as exc:  # noqa: BLE001
                mlog.log("CollectorProcessingError", f"완성봉 발행 실패: {exc}", symbol=bar.symbol)


@dataclass(frozen=True, slots=True)
class SymbolFeed:
    """`MultiSymbolTickCollector`에 등록할 심볼 1개 — TR·파서·틱 크기가 상품군마다
    다르므로(선물 vs 옵션) 심볼별로 함께 묶어 넘긴다."""

    symbol: str
    tr_id: str
    parse_tick: Callable[..., list[Tick]]
    tick_size: Decimal


def _extract_tr_id(raw: str) -> str | None:
    """ "암호화유무|TR_ID|데이터건수|실제데이터" 헤더에서 TR_ID(2번째 필드)만 뽑는다 —
    `normalizer._split_ws_records`와 같은 구분자 규약이지만 헤더 자체가 목적이라 별도
    구현(정규화 모듈에 파싱 대상 외 책임을 얹지 않는다)."""
    parts = raw.split("|", 3)
    return parts[1] if len(parts) >= 2 else None


class MultiSymbolTickCollector:
    """단일 WS 연결에 여러 (심볼, TR) 조합을 동시 구독 — 모듈 docstring 참고.

    **라이브 미검증 (검증 기한: 2026-08-14, Phase 1 파이프라인 완성 후 첫 금요일
    주간회의 — [[l1_gap_deferral_to_weekly_review]]와 동일 이관 사유)**: `run_l1_daily.py`가
    바로 이 계좌로 매일 라이브 수집 중이라, 이 클래스를 지금 실제 KIS WS로 검증하면 그
    라이브 세션과 리소스(WS 연결 슬롯·approval_key 재발급 빈도)를 다툴 위험이 있다 —
    비거래일·비거래시간 또는 별도 합의된 시점에 검증하기로 한다. 지금은 mock
    `WSConnection`으로 단위 테스트만 완료(구현됨≠검증됨, `broker/base.py` 원칙).
    """

    def __init__(
        self,
        creds: KISCredentials,
        feeds: Sequence[SymbolFeed],
        archiver: ParquetArchiver,
        bus: MessageBus | None = None,
        horizon: Horizon = Horizon.M1,
        approval_issuer: ApprovalKeyIssuer | None = None,
        ws_connect: Callable[[str], Any] = websockets.connect,
        reconnect_initial_backoff_seconds: float = _WS_RECONNECT_INITIAL_BACKOFF_SECONDS,
        reconnect_max_backoff_seconds: float = _WS_RECONNECT_MAX_BACKOFF_SECONDS,
        stall_timeout_seconds: float = _TICK_STALL_TIMEOUT_SECONDS,
        stall_check_interval_seconds: float = _TICK_STALL_CHECK_INTERVAL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not feeds:
            raise ValueError("feeds가 비어 있음 — 구독할 심볼이 최소 1개 필요")
        if len(feeds) > KISWebSocketClient.MAX_SUBSCRIPTIONS:
            raise ValueError(
                f"feeds {len(feeds)}건 > 세션당 구독 슬롯 한도 "
                f"{KISWebSocketClient.MAX_SUBSCRIPTIONS}건 — ATM±N 롤링(범위 밖) 없이는 불가"
            )
        self._creds = creds
        self._feeds = list(feeds)
        self._archiver = archiver
        self._bus = bus
        self._horizon = horizon
        self._approval_issuer = approval_issuer or ApprovalKeyIssuer(creds)
        self._ws_connect = ws_connect
        self._reconnect_initial_backoff_seconds = reconnect_initial_backoff_seconds
        self._reconnect_max_backoff_seconds = reconnect_max_backoff_seconds
        self._parsers_by_tr: dict[str, Callable[..., list[Tick]]] = {
            feed.tr_id: feed.parse_tick for feed in feeds
        }
        self._tick_sizes_by_symbol: dict[str, Decimal] = {
            feed.symbol: feed.tick_size for feed in feeds
        }
        self._aggregators = self._fresh_aggregators()
        # 연결이 하나뿐이라(모듈 docstring) 워치독도 하나로 충분하다 — 어느 심볼이든 틱이
        # 들어오면 그 연결은 살아있다는 뜻이고, 끊기면 전 심볼이 동시에 끊긴다.
        self._watchdog = _StallWatchdog(
            timeout_seconds=stall_timeout_seconds,
            check_interval_seconds=stall_check_interval_seconds,
            monotonic=monotonic,
            sleep=sleep,
        )

    def _fresh_aggregators(self) -> dict[str, MinuteBarAggregator]:
        return {
            feed.symbol: MinuteBarAggregator(feed.symbol, self._horizon) for feed in self._feeds
        }

    async def run_once(self, on_connected: Callable[[], None] | None = None) -> None:
        """`TickCollector.run_once()`와 같은 계약이나, 구독을 `feeds` 전부에 대해 반복한다
        (연결은 여전히 하나 — `KISWebSocketClient.subscribe()`를 여러 번 호출)."""
        approval_key = await asyncio.to_thread(self._approval_issuer.issue)
        async with self._ws_connect(tr_codes.MARKET_DATA_WS_DOMAIN) as ws:
            client = KISWebSocketClient(approval_key, ws)
            for feed in self._feeds:
                await client.subscribe(Subscription(feed.tr_id, feed.symbol))
            if on_connected is not None:
                on_connected()
            self._watchdog.reset()
            await _listen_with_stall_watchdog(
                client,
                self._handle_message,
                self._watchdog,
                describe=",".join(f.symbol for f in self._feeds),
            )

    async def run_forever(self) -> None:
        """`TickCollector.run_forever()`와 동일한 지수 백오프 재연결 — 재연결 시 등록된
        심볼 전부의 aggregator를 함께 재생성한다(부분 재생성은 없음, 연결이 하나라 끊기면
        전 심볼이 동시에 끊긴다)."""
        backoff = self._reconnect_initial_backoff_seconds
        was_disconnected = False

        def _on_connected() -> None:
            nonlocal backoff, was_disconnected
            if was_disconnected:
                mlog.log(
                    "CollectorWSReconnected",
                    "WS 재연결 성공 — 수신 재개",
                    symbols=[f.symbol for f in self._feeds],
                )
                was_disconnected = False
            backoff = self._reconnect_initial_backoff_seconds

        while True:
            try:
                await self.run_once(on_connected=_on_connected)
                return
            except _WS_DISCONNECT_ERRORS as exc:
                if not was_disconnected:
                    mlog.log(
                        "CollectorWSDisconnected",
                        f"WS 연결 끊김 — {backoff:.0f}초 후 재연결 시도: {exc}",
                        symbols=[f.symbol for f in self._feeds],
                    )
                    was_disconnected = True
                self._aggregators = self._fresh_aggregators()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._reconnect_max_backoff_seconds)

    async def flush_final_bar(self) -> None:
        """등록된 심볼 전부의 미완성 마지막 분봉을 강제 flush(graceful shutdown)."""
        for aggregator in self._aggregators.values():
            bar = aggregator.flush_final()
            if bar is not None:
                await self._archive_and_publish_bar(bar)

    async def _handle_message(self, message: dict) -> None:
        raw = message.get("raw")
        if raw is None:
            return  # JSON 제어 메시지(구독응답/PINGPONG) — 정규화 대상 아님

        tr_id = _extract_tr_id(raw)
        parser = self._parsers_by_tr.get(tr_id) if tr_id else None
        if parser is None:
            return  # 등록 안 된 TR — 조용히 무시(다른 세션 잔여 메시지 등 방어, mahdi와 동일)

        # tick_size는 심볼별로 다를 수 있어(선물 vs 옵션) 파싱 전엔 아직 어느 심볼인지 모른다
        # — 우선 파싱 없이 심볼만 뽑을 수는 없으므로, 등록된 심볼들의 tick_size가 전부
        # 같은 경우가 실무상 대부분이지만 다를 수 있어 일단 대표값(첫 feed)으로 파싱한 뒤
        # 실제 심볼로 올바른 tick_size였는지 확인 — 다르면 그 심볼의 tick_size로 재파싱한다.
        provisional = parser(raw, self._feeds[0].tick_size)
        if not provisional:
            return
        # 한 프레임의 레코드는 전부 같은 심볼이다(구독 단위가 심볼×TR) — 대표 1건으로
        # tick_size를 정하고, 틀렸으면 프레임 전체를 올바른 값으로 한 번만 재파싱한다.
        correct_tick_size = self._tick_sizes_by_symbol.get(provisional[0].symbol)
        if correct_tick_size is None:
            return  # 모르는 심볼(같은 TR의 다른 계좌 잔여 등) — 조용히 무시
        ticks = (
            provisional
            if correct_tick_size == self._feeds[0].tick_size
            else parser(raw, correct_tick_size)
        )
        if not ticks:
            return

        first = not self._watchdog.seen_first_tick
        self._watchdog.mark_tick()
        if first:
            mlog.log(
                "CollectorFirstTick",
                "연결 후 첫 틱 수신",
                symbol=ticks[0].symbol,
                received_kst=now_kst().isoformat(),
            )

        for tick in ticks:
            if self._bus is not None:
                try:
                    await self._bus.publish(f"{TOPIC_TICK}.{tick.symbol}", tick)
                except Exception as exc:  # noqa: BLE001
                    mlog.log("CollectorProcessingError", f"틱 발행 실패: {exc}", symbol=tick.symbol)

            aggregator = self._aggregators.get(tick.symbol)
            if aggregator is None:
                continue
            bar = aggregator.add_tick(tick)
            if bar is not None:
                await self._archive_and_publish_bar(bar)

    async def _archive_and_publish_bar(self, bar: BarClosed) -> None:
        try:
            self._archiver.append_bar(bar)
        except Exception as exc:  # noqa: BLE001
            mlog.log("CollectorProcessingError", f"완성봉 적재 실패: {exc}", symbol=bar.symbol)

        if self._bus is not None:
            try:
                await self._bus.publish(f"{TOPIC_BAR}.{bar.horizon.value}.{bar.symbol}", bar)
            except Exception as exc:  # noqa: BLE001
                mlog.log("CollectorProcessingError", f"완성봉 발행 실패: {exc}", symbol=bar.symbol)
