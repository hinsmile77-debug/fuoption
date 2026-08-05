"""컴포넌트 heartbeat 발행 — `sys.health` (고도화 1, 2026-07-30).

## 왜 만들었나

2026-07-30 일일 점검에서 나온 이상점 4건이 전부 같은 형태였다 — **조용한 실패**:

- Command Center UI가 08:57에 네이티브 크래시로 죽었는데 32분간 아무도 몰랐다
- WS는 07-28·07-29 두 번 다 소켓이 살아있는 채로 30분씩 틱만 끊겼고 로그가 한 줄도 없었다
- FeatureEngine은 재시작마다 워밍업이 리셋됐는데 nan_ratio 로그를 사람이 직접 파싱해야만
  보였다
- 아카이버는 봉 적재에 실패해도 로깅만 하고 넘어갔다

개별 원인은 각자 고쳤지만(F1~F4), 공통 구조 — "죽어도 화면에 아무 표시가 없다" — 는 그대로
였다. 이 모듈이 그 자리를 메운다: 각 컴포넌트가 자기 상태를 주기적으로 `sys.health`에 알리고,
Command Center 상단의 신호등이 그걸 그대로 보여준다.

## 침묵도 상태다

`HealthReporter`는 정상일 때도 계속 발행한다 — "문제 있을 때만 알린다"는 방식은 프로세스가
통째로 죽었을 때 **아무 신호도 안 나온다**는 치명적 약점이 있다(위 UI 크래시가 정확히 그
경우다). 주기적 heartbeat라야 "안 오는 것" 자체가 신호가 된다. 화면 쪽도 컴포넌트 목록을
고정해 두고 안 들어오는 항목을 "데이터 없음"으로 드러낸다(`ui/app.py`) — 안 보이면 정상인
줄 아는 착각을 막는다(마흐디 L18과 같은 원칙).

## 상태는 컴포넌트가 스스로 판정한다

`probe`는 "지금 내 상태가 어떤지"를 그 컴포넌트가 직접 계산해 돌려주는 콜러블이다. 예를 들어
`TickCollector`는 마지막 틱 이후 경과로 OK/WARN/CRITICAL을 스스로 정한다 — 데이터 흐름의
1차 책임이 수집기에 있다는 계층 분리(고도화 4)를 그대로 반영한 것이다. `probe`를 안 주면
"프로세스는 살아있다" 이상은 주장하지 않는 단순 heartbeat(OK)가 된다.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Awaitable, Callable

from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_HEALTH, BusLike
from messiah.core.messages import Health, HealthLevel

# heartbeat 주기 — `Health` docstring이 "5초 주기, 15초 미수신 = 사망 판정"을 적어뒀지만,
# 그건 원안이고 실제 결선은 10초/30초로 잡는다: Redis pub/sub 부하를 반으로 줄이면서도
# 사람이 화면을 보고 이상을 느끼기 전에 배지가 먼저 바뀌는 수준이다(`ui/app.py`의
# `_STALE_AFTER`가 같은 근거로 30초를 쓴다 — CB 배지 40초 선택과 동일한 논리).
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 10.0
HEALTH_STALE_AFTER_SECONDS = 30.0

# UI 캐시 키 접두사 — `sys.health`는 여러 컴포넌트가 같은 토픽에 발행하므로, 메시지 타입
# 이름(`"Health"`)을 그대로 키로 쓰면 서로 덮어써서 마지막 하나만 남는다.
HEALTH_KEY_PREFIX = "health:"

# 수집기 컴포넌트 이름 — 발행측(`scripts/run_l1_daily.py`)과 구독측(`strategy/pipeline.py`의
# CB 판정)이 같은 문자열을 봐야 한다. 양쪽에 따로 하드코딩하면 한쪽 오타로 **조용히** 결선이
# 끊긴다(CB가 영영 "수집기 상태를 모름"으로 남아 오탐 억제가 안 걸린다).
COLLECTOR_COMPONENT = "l1.collector"


def health_cache_key(component: str) -> str:
    return f"{HEALTH_KEY_PREFIX}{component}"


@dataclass(frozen=True)
class HealthStatus:
    level: HealthLevel
    detail: str = ""


_OK = HealthStatus(HealthLevel.OK)


class HealthReporter:
    """단일 컴포넌트의 heartbeat 발행기.

    실패해도 본 임무를 막지 않는다 — 발행 예외는 잡아서 로깅하고 다음 주기에 다시 시도한다
    (L22: 부가 기능의 실패가 처리 루프를 죽이면 안 된다). `probe` 자체가 던지는 예외도
    같은 이유로 잡아서 CRITICAL로 보고한다 — 상태를 못 재는 것도 하나의 상태다.
    """

    def __init__(
        self,
        bus: BusLike,
        component: str,
        *,
        probe: Callable[[], HealthStatus] | None = None,
        interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        pid: int | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._bus = bus
        self._component = component
        self._probe = probe
        self._interval_seconds = interval_seconds
        self._pid = os.getpid() if pid is None else pid
        self._sleep = sleep

    def _current_status(self) -> HealthStatus:
        if self._probe is None:
            return _OK
        try:
            return self._probe()
        except Exception as exc:  # noqa: BLE001 — 상태를 못 재는 것도 상태다
            return HealthStatus(HealthLevel.CRITICAL, f"probe 실패: {exc}")

    async def publish_once(self) -> Health | None:
        """반환은 실제로 발행한 메시지 — 발행에 실패하면 None(테스트/호출측 확인용)."""
        status = self._current_status()
        message = Health(
            component=self._component,
            level=status.level,
            detail=status.detail,
            pid=self._pid,
        )
        try:
            await self._bus.publish(TOPIC_HEALTH, message)
        except Exception as exc:  # noqa: BLE001 — heartbeat 실패가 루프를 죽이면 안 됨(L22)
            mlog.log(
                "HealthPublishError",
                f"heartbeat 발행 실패: {exc}",
                component=self._component,
            )
            return None
        return message

    async def run_forever(self) -> None:
        """첫 heartbeat를 먼저 보내고 그 다음부터 주기 대기 — 기동 직후 화면이 한 주기
        동안 "데이터 없음"으로 보이지 않게 한다."""
        while True:
            await self.publish_once()
            await self._sleep(self._interval_seconds)


def staleness_status(
    age_seconds: float | None,
    *,
    warn_after: float,
    critical_after: float,
    warming_up_detail: str = "웜업 — 아직 첫 수신 전",
    unit: str = "초",
) -> HealthStatus:
    """ "마지막 수신 이후 경과"류 probe의 공통 판정 — 수집기·피처엔진이 같이 쓴다.

    `age_seconds is None`(아직 한 번도 못 받음)을 CRITICAL로 보지 않는 것이 핵심이다.
    기준선이 없는 것과 끊긴 것은 다르다 — 08:35 기동 후 첫 틱(실측 08:45)까지 10분을
    장애로 표시하면 매일 아침 10분씩 거짓 경보가 뜬다(`strategy/pipeline.py`의 CB 콜드스타트
    가드, `data/collector.py`의 스톨 워치독과 같은 논리).

    ## 그런데 그것을 `OK`라고 부른 것이 틀렸다 (2026-08-05 2차, 고도화 3)

    "장애가 아니다"와 "정상이다"는 다르다. 웜업 구간을 `OK`로 내보내면 **한 건도 못 받은
    상태가 화면과 상태판에서 초록으로 보인다** — 2026-08-05 장중점검에서 관측 축 전체가
    "OK인데 실제로는 손상 중"이었던 것과 같은 형태의 실패다.

    실제 영향이 있었다: `TradingPipeline._collector_reports_healthy()`가 `OK`를 "한산하다"로
    읽어 **서킷브레이커 승격을 막는다.** 즉 08:36~08:45의 9분 동안, 수집기가 데이터를 한 건도
    못 받은 상태가 CB 억제 근거로 쓰였다. 재연결 직후(워치독이 리셋된다)에도 같은 창이 열린다.

    `UNKNOWN`은 그 소비처의 `None`(모름) 갈래로 매핑되어, 억제하지 않고 원래 규칙대로 간다 —
    안전한 방향이다.
    """
    if age_seconds is None:
        return HealthStatus(HealthLevel.UNKNOWN, warming_up_detail)
    if age_seconds >= critical_after:
        return HealthStatus(HealthLevel.CRITICAL, f"{age_seconds:.0f}{unit}간 수신 없음")
    if age_seconds >= warn_after:
        return HealthStatus(HealthLevel.WARN, f"{age_seconds:.0f}{unit}간 수신 없음")
    return HealthStatus(HealthLevel.OK, f"최근 수신 {age_seconds:.0f}{unit} 전")
