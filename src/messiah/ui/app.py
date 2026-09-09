"""Command Center — MESSIAH Trading UI 1단계 프로토타입 (Ver 1.0.1 §3, Ver 2.0 §9 W32~34).

Streamlit로 화면 구성을 빠르게 검증하는 1단계(Ver 1.0.1 §3.4 "1단계 프로토타입은
Streamlit/Dash로") — 확정판이 아니라 React 이관(Ver 2.2) 전 레이아웃·정보 밀도를 검증하는
자리다. 고정 상단 바 + 핵심 4존(Ver 2.0 §9 "핵심 4존"):

    ┌─────────────────────────────────────────────────────────┐
    │ Top Bar: 총자산 · 일일 PnL · 리스크 게이지 · KILL SWITCH   │
    ├───────────────┬──────────────────────┬───────────────────┤
    │ ① AI Decision │ ② Market View        │ ③ Position & Risk │
    ├───────────────┴──────────────────────┴───────────────────┤
    │ ④ Bottom: 실행 로그 · 이벤트 캘린더 · Self-Eval 요약        │
    └─────────────────────────────────────────────────────────┘

## LIVE/STALE/REPLAY는 항상 명시적이다 (마흐디 L18)

사이드바에서 사용자가 LIVE 또는 REPLAY를 직접 고른다 — 어느 한쪽이 실패했다고 조용히
다른 쪽으로 전환하지 않는다(`ui/data_source.py` 모듈 docstring). 기본값은 LIVE다(2026-07-29
사용자 요청으로 REPLAY→LIVE 변경 — 평소 장중 모니터링 용도가 대부분이라 매번 수동 전환하는
게 번거로웠음). "착각의 여지 없음" 방어 자체는 그대로 유지된다 — LIVE 배지는 항상 신선도를
드러내고(`_STALE_AFTER`), Redis 연결 실패도 `LiveConnectionError`로 화면에 노출되므로
(`render_top_bar` 참고), 기본값이 LIVE라고 해서 실패가 조용히 숨겨지지는 않는다.

## Streams(decision.intent·exec.*)는 pub/sub 구독으로 못 받는다

`core/bus.py`의 `MessageBus.publish()`는 `STREAM_TOPICS`를 `XADD`로 쓰는데, `subscribe()`는
`psubscribe`(pub/sub)만 구독한다 — 그래서 `intel.*`/`bar.*`류는 `state_cache.
CacheSubscriber`로 충분하지만, `decision.intent`/`exec.fill` 같은 Stream은 별도로
`MessageBus.read_stream()`을 주기적으로 폴링해야 한다(`_poll_streams_forever` 참고).

## 캔들 차트는 항상 Parquet에서 읽는다

LIVE든 REPLAY든 완성봉은 `ParquetArchiver`가 이미 적재해 둔 파일이 유일한 진실원천이다
(`data/archiver.py`) — 그래서 이 화면은 봉 데이터를 버스가 아니라 항상 Parquet에서 읽는다.
LIVE/REPLAY의 차이는 "오늘"과 "과거 날짜 선택"뿐이다.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

# 무거운 임포트보다 **먼저** 네이티브 크래시 덤프를 무장한다(2026-08-03). 이 프로세스는
# 5거래일 연속 같은 access violation으로 죽었는데 로그에 아무 흔적이 없어 매번 정황으로만
# 원인을 추정해야 했다 — 임포트 도중의 크래시까지 덮으려면 이 줄이 맨 위여야 한다
# (`core/crash_forensics.py` 모듈 docstring). 멱등이라 Streamlit의 5초 재실행에 안전하다.
from messiah.core import crash_forensics
from messiah.core import logging as mlog

crash_forensics.enable(tag="ui")

# numpy를 **직접** 임포트한다 — 이 모듈은 numpy를 직접 쓰지 않지만, plotly가 numpy를
# **지연 임포트**한다(`_plotly_utils.optional_imports.get_module`). Streamlit LIVE 모드는
# `st.fragment(run_every=5)`로 렌더 경로를 별도 ScriptRunner 스레드에서 돌리는데, 그 스레드
# 둘이 동시에 numpy의 **최초** 임포트를 밟으면 `AttributeError: partially initialized module
# 'numpy' has no attribute 'ndarray' (most likely due to a circular import)`가 난다 —
# 2026-07-31 UI 로그에 실제로 3건 기록됐다(12:21:20·12:21:20·12:31:55, 전부 재기동 직후
# 2초 이내 = 콜드스타트 레이스). 여기서 단일 스레드(모듈 임포트 시점)에 미리 완료시키면
# 그 경합 자체가 성립하지 않는다.
#
# 2026-08-03: 원래 이 주석은 polars도 같은 이유로 지목했는데, 이제 **이 프로세스엔 polars가
# 아예 없다**(`ui/bar_reader.py` — 봉 파싱을 자식 프로세스로 분리). numpy 선임포트는 plotly
# 때문에 그대로 필요하다.
import numpy  # noqa: F401
import plotly.graph_objects as go
import streamlit as st

from messiah.core import symbol_resolution
from messiah.core.bus import TOPIC_KILL, TOPIC_RESUME, MessageBus
from messiah.core.config import load_instance
from messiah.core.event_calendar import DEFAULT_SESSION, EventCalendar
from messiah.core.health import HEALTH_STALE_AFTER_SECONDS, health_cache_key
from messiah.core.messages import (
    CIRCUIT_BREAKER_PHASE_WARMUP,
    HORIZON_SECONDS,
    BusMessage,
    CircuitBreakerStatus,
    DecisionIntent,
    Fill,
    FuturesView,
    Health,
    Horizon,
    KillSignal,
    OptionsView,
    RegimeState,
    ResumeSignal,
)
from messiah.core.state_cache import CacheSubscriber, StateCache
from messiah.core.timeutil import now_kst, now_utc
from messiah.core.version import (
    PROCESS_GIT_SHA,
    PROCESS_STARTED_AT,
    assess_version_drift,
    head_git_sha,
    uptime_text,
)
from messiah.data import bar_paths
from messiah.data.close_grace import close_grace_ms
from messiah.ops.status_board import (
    DEAD_AFTER_MULTIPLE,
    code_version_axis,
    load_snapshot,
)
from messiah.ui.bar_reader import BarExportError, read_day_series
from messiah.ui.bar_series import BarSeries
from messiah.ui.data_source import (
    DEFAULT_STALE_AFTER_SECONDS,
    DataSourceMode,
    FreshnessBadge,
    LiveDataSource,
    ReplayDataSource,
)

DEFAULT_BAR_DIR = Path("data") / "bars"
DEFAULT_SNAPSHOT_PATH = Path("logs") / "status_snapshot.json"
DEFAULT_TICK_SIZE = 0.02  # 미니선물(A05608) 2026-07-22 실측값(core/config.py 동일 placeholder)

# 화면에 항상 자리를 잡아둘 컴포넌트 — **고정 목록인 것이 핵심이다**. 동적으로 "수신된 것만"
# 보여주면 프로세스가 통째로 죽었을 때 그 줄이 화면에서 사라져 버려, 사고가 오히려 안 보이게
# 된다(2026-07-30 UI 크래시가 32분간 안 보였던 것과 같은 실패 형태). 안 들어오는 항목을
# "데이터 없음"으로 남겨 둬야 침묵이 신호가 된다(`core/health.py` "침묵도 상태다").
_HEALTH_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("l1.collector", "수집기(WS)"),
    ("l1.feature_engine", "피처엔진"),
    # 2026-08-05 추가 — 위 둘은 "최근에 받았나"(신선도)를 재고, 이건 "받은 것을 온전히
    # 합쳤나"를 잰다. 그날 상위 Horizon 봉의 3~17%가 사라지는 동안 화면은 전부 초록이었다.
    ("l1.composer", "봉 합성기"),
    ("g2.pipeline", "G2 파이프라인"),
)

# ## 판단 계열 임계는 **발행 주기에서 유도한다** (2026-08-14 F-4)
#
# 아래 세 값은 종전에 10~15초 상수였다. 그런데 판단은 **구동 Horizon 격자**로만 나간다 —
# 2026-08-14 기준 live 번들이 `30m` 한 종이라 `intel.futures`는 30분에 한 번 발행됐다.
# 임계 10초 / 주기 1800초면 **거래일의 99.4%가 STALE**이다. 그날 화면 상단은 종일 앰버였고,
# 그 앰버의 뜻("그 프로세스가 죽었거나 멈췄다", `_render_health_strip` docstring)은 틀렸다.
#
# `CircuitBreakerStatus`는 이 함정을 이미 알고 40초(주기 30초 대비)로 잡아 뒀다. **한 곳에서만
# 피한 것은 설계가 아니라 우연이다.** 그래서 상수 대신 메시지가 스스로 말한 유효기간
# (`valid_until - ts_utc` = 그 Horizon 길이)에서 계산한다(`_derived_stale_after`).
#
# 여기 남은 값은 **유도가 불가능할 때의 하한**이다.
#
# 2026-08-20 F-A′ — 종전 이 주석은 *"`valid_until`이 None인 경우(기여 전문가 0명)에만 쓰인다"*
# 라고 적었다. **틀렸다.** 유도식이 `valid_until − ts_utc`였는데 생산 경로에서 두 값이 같은 봉을
# 가리켜 차이가 늘 0 이하였고, 그래서 아래 상수들이 **모든 사이클에서** 정본이었다 —
# 기여 전문가 수와 무관하게. 고친 기록만 남고 새 경로는 엿새 동안 0회 사용됐다.
# 지금은 메시지가 `cadence_seconds`(구동 Horizon 길이)를 직접 싣는다
# (`ui/data_source.derived_stale_after` docstring "한 이름에 두 의미" 절).
# 그러므로 아래 값은 **이제 정말 예외 경로**다 — 옛 메시지가 캐시에 남아 있거나 구동 주기를
# 못 구한 경우뿐이고, 그 횟수는 `data_source.threshold_derivation_stats()`가 센다(G-A).
#
# 상수 자체를 30분으로 올리는 안은 **기각**한다 — 그러면 진짜 정지도 30분간 초록이다.
_STALE_AFTER: dict[str, float] = {
    "FuturesView": 10.0,
    "RegimeState": 15.0,
    "OptionsView": 15.0,
    # CircuitBreakerStatus는 워치독이 30초 격자로 heartbeat한다(`strategy/pipeline.py`
    # `watch_circuit_breaker_forever`) — 그 주기보다 여유 있게 잡아 두 heartbeat 사이에
    # 배지가 헛되이 STALE로 안 보이게 한다(`_LIVE_REFRESH_SECONDS` 선택과 같은 논리).
    "CircuitBreakerStatus": 40.0,
    # 컴포넌트 heartbeat는 10초 주기(`core/health.py`) — 그 3배를 넘게 안 오면 그 프로세스가
    # 죽었거나 멈춘 것으로 본다(CB 배지 40초 선택과 같은 논리).
    **{
        health_cache_key(component): HEALTH_STALE_AFTER_SECONDS
        for component, _label in _HEALTH_COMPONENTS
    },
}

_BADGE_COLOR = {
    FreshnessBadge.LIVE: "#00C9A7",  # Ver 1.0.1 §3.1 청록 — LONG/상승과 같은 계열(정상)
    FreshnessBadge.STALE: "#FFB020",  # 앰버 — 경고
    FreshnessBadge.REPLAY: "#8A8F98",  # 그레이 — 중립
    FreshnessBadge.NO_DATA: "#8A8F98",
}

# 거래소 서킷브레이커(CB) phase별 배지 — `CircuitBreakerPhase.value` 문자열을 그대로 키로 쓴다
# (`core/messages.py`의 `CircuitBreakerStatus.phase` 참고). 위험도가 올라갈수록
# 정상(청록)→경고(앰버)→위험 심화(주황)→확정(적색)으로 톤을 올린다 — `_BADGE_COLOR`와
# 같은 팔레트를 재사용하되 SUSPECTED만 그 사이 단계를 표현할 새 색이 필요해 추가했다.
_CB_PHASE_COLOR: dict[str, str] = {
    "normal": "#00C9A7",
    "warning": "#FFB020",
    "suspected": "#FF8C42",
    "confirmed": "#FF5C7A",
    # 웜업은 **판정 전**이라 초록이 아니다 — `_HEALTH_LEVEL_COLOR["UNKNOWN"]`과 같은 회색,
    # 같은 뜻이다("판정할 근거가 없는 상태를 정상색으로 칠하지 않는다", 2026-08-05 고도화 3).
    CIRCUIT_BREAKER_PHASE_WARMUP: "#8A8F98",
}
_CB_PHASE_LABEL: dict[str, str] = {
    "normal": "정상",
    "warning": "주의(데이터 지연)",
    "suspected": "CB 의심",
    "confirmed": "CB 정지 추정",
    CIRCUIT_BREAKER_PHASE_WARMUP: "웜업 — 첫 봉 대기(판정 전)",
}


def _render_circuit_breaker_badge(source) -> None:
    """Top Bar용 CB 상태 배지 — `strategy/pipeline.py`가 `sys.circuit_breaker`에 발행하는
    `CircuitBreakerStatus` heartbeat를 그대로 보여준다. `circuit_breaker_monitor`를 안 쓰는
    구성(스모크/재생 등)에서는 이 토픽 자체가 발행되지 않아 항상 NO_DATA — 그 경우 "미사용"
    문구로 명시해 "정상"과 혼동되지 않게 한다(마흐디 L18과 같은 원칙 — 값이 없는 것과 정상인
    것을 구분).

    ## "미사용"이 두 가지를 덮고 있었다 (2026-08-11 F-2)

    2026-08-11 08:43 화면이 `미사용/데이터 없음`이었는데 그 시각 CB 모니터는 **정상 주입돼
    있었다** — 첫 봉이 확정되기 전이라 워치독이 판정을 건너뛰며 아무것도 발행하지 않았을
    뿐이다(`strategy/pipeline.observe_circuit_breaker_tick()`의 콜드스타트 분기). 그 침묵이
    "이 구성에선 CB를 안 쓴다"로 보였다. 이제 그 구간은 `warmup` phase heartbeat로 오고,
    토픽이 정말 없을 때만 "미사용"이다 — `_ABSENCE_REASON`이 NO_DATA를 ①끊김 ②미배선
    ③대기로 가른 것과 같은 수술이며, 여기서 걸린 것이 ③이다."""
    snap = source.snapshot("CircuitBreakerStatus")
    if not isinstance(snap.message, CircuitBreakerStatus):
        st.markdown(
            "**서킷브레이커** &nbsp; <span style='color:#8A8F98'>● 미사용/데이터 없음</span>",
            unsafe_allow_html=True,
        )
        return

    status = snap.message
    phase = status.phase
    color = _CB_PHASE_COLOR.get(phase, "#8A8F98")
    label = _CB_PHASE_LABEL.get(phase, phase)
    st.markdown(
        f"**서킷브레이커** &nbsp; <span style='color:{color}'>● {label}</span>",
        unsafe_allow_html=True,
    )
    if snap.badge == FreshnessBadge.STALE:
        st.caption(f"⚠ 상태 갱신 지연({snap.age_seconds:.0f}초 전)")

    # 웜업은 **정상 경로**다 — 무엇을 기다리는지 적어야 사람이 "고장인가"를 다시 안 묻는다.
    # 아래 `gateway_halted` 줄은 건너뛰지 않는다: 판정을 못 하는 것과 게이트가 닫혀 있는 것은
    # 별개 사실이고, 후자는 웜업 중에도 이미 아는 값이다.
    if phase == CIRCUIT_BREAKER_PHASE_WARMUP:
        st.caption("첫 봉이 확정되면 판정을 시작한다 — 기준선이 없는 동안은 CB 판정 대상이 아니다")

    # **거래소가 멈춘 것인가, 우리가 죽은 것인가** (2026-08-07 고도화 2).
    #
    # 2026-08-07 13:41에 수집 프로세스가 죽자 이 배지는 13:45부터 "CB 정지 추정"을 띄웠다.
    # 그 문구는 거래소 얘기로 읽힌다 — 사람이 봤어야 할 문장은 "수집기가 죽었다"였고,
    # G2는 그 사실을(`collector_healthy`) 이미 알고 있었다. 알고도 안 보여준 것이다.
    if phase in ("suspected", "confirmed") and status.collector_healthy is not True:
        st.caption(
            "🛑 **수집기 heartbeat 없음 — 거래소 CB가 아니라 우리 쪽 사망 의심.** "
            "l1_daily 프로세스를 먼저 확인할 것"
        )

    # 추정 phase와 **실제 게이트 상태**는 별개 사실이다 — 2026-07-31엔 phase가 정상으로
    # 돌아온 뒤에도 게이트가 6시간 42분간 halted로 남았는데 화면엔 아무 흔적이 없었다
    # (`core/messages.py`의 `CircuitBreakerStatus.gateway_halted` docstring 참고).
    if status.gateway_halted:
        st.caption("🛑 주문 게이트 정지 중 — 신규 주문 차단 상태")

    if status.reentry_cooldown_until is not None:
        remaining = (status.reentry_cooldown_until - now_utc()).total_seconds()
        if remaining > 0:
            st.caption(f"재진입 관망 {remaining / 60:.1f}분 남음")


# ---------------------------------------------------------------- 컴포넌트 헬스 신호등

_HEALTH_LEVEL_COLOR: dict[str, str] = {
    "OK": "#00C9A7",  # `_BADGE_COLOR`/`_CB_PHASE_COLOR`와 같은 팔레트 재사용
    "WARN": "#FFB020",
    "CRITICAL": "#FF5C7A",
    # UNKNOWN은 **초록이 아니다**(2026-08-05 2차, 고도화 3). 판정할 근거가 없는 상태를
    # 정상색으로 칠하면 "한 건도 못 받았다"가 화면에서 "잘 돌고 있다"로 보인다 — `NO_DATA`
    # 배지와 같은 그레이를 쓴다(`_BADGE_COLOR[FreshnessBadge.NO_DATA]`와 같은 값·같은 뜻).
    "UNKNOWN": "#8A8F98",
}


def _render_health_strip(source) -> None:
    """컴포넌트별 신호등 (고도화 1).

    `CircuitBreakerStatus` 배지와 같은 구조를 컴포넌트 수만큼 늘린 것 — 그 배지가 이미
    "heartbeat를 그대로 보여준다"는 패턴으로 검증됐으므로 새 방식을 만들지 않았다.

    STALE(= heartbeat가 임계보다 오래 안 옴)은 "그 프로세스가 죽었거나 멈췄다"는 뜻이라
    레벨과 무관하게 경고색으로 덮어쓴다 — 마지막으로 받은 값이 OK였다는 이유로 초록으로
    남아 있으면 정확히 이번 사고(죽은 뒤에도 화면은 멀쩡해 보였다)를 반복한다.
    """
    st.caption("컴포넌트 상태")
    cols = st.columns(len(_HEALTH_COMPONENTS))
    for col, (component, label) in zip(cols, _HEALTH_COMPONENTS):
        snap = source.snapshot(health_cache_key(component))
        message = snap.message
        with col:
            if not isinstance(message, Health):
                st.markdown(
                    f"<span style='color:#8A8F98'>● {label} — 데이터 없음</span>",
                    unsafe_allow_html=True,
                )
                continue
            if snap.badge == FreshnessBadge.STALE:
                # **지연과 사망을 가른다** (2026-08-07 고도화 2). 종전엔 둘 다 "응답 없음
                # (N초)"였고, 2026-08-07에 수집기가 죽은 뒤 그 숫자만 커지는 것을 사람이
                # 세고 있어야 했다. 처방이 다르면 문장도 달라야 한다.
                dead = (snap.age_seconds or 0) > HEALTH_STALE_AFTER_SECONDS * DEAD_AFTER_MULTIPLE
                text = (
                    f"● {label} — **죽음**({snap.age_seconds / 60:.0f}분) · 프로세스 확인"
                    if dead
                    else f"● {label} — 응답 없음({snap.age_seconds:.0f}초)"
                )
                st.markdown(f"<span style='color:#FF5C7A'>{text}</span>", unsafe_allow_html=True)
                continue
            color = _HEALTH_LEVEL_COLOR.get(message.level.value, "#8A8F98")
            detail = f" · {message.detail}" if message.detail else ""
            st.markdown(
                f"<span style='color:{color}'>● {label} — {message.level.value}{detail}</span>",
                unsafe_allow_html=True,
            )


# ---------------------------------------------------------------- LIVE 배선 (백그라운드 스레드)


def _cache_key_for(message: BusMessage) -> str:
    """`sys.health`는 여러 컴포넌트가 **같은 토픽**에 발행한다 — `CacheSubscriber`의 기본
    키(메시지 타입 이름)를 그대로 쓰면 전부 `"Health"` 하나로 뭉쳐 마지막 발행자만 남는다.
    Health만 컴포넌트별로 쪼개고 나머지는 기존 규약(타입 이름)을 그대로 유지한다."""
    if isinstance(message, Health):
        return health_cache_key(message.component)
    return type(message).__name__


def _run_live_subscriber(redis_url: str, symbol: str, cache: StateCache) -> None:
    """백그라운드 스레드 진입점 — 자기만의 이벤트 루프에서 pub/sub 구독 + Stream 폴링을
    동시에 돌린다(모듈 docstring "Streams는 pub/sub 구독으로 못 받는다"). 예외가 스레드를
    죽이면 캐시가 조용히 멈추므로, 실패 원인을 캐시에 남겨 화면에서 보이게 한다."""

    async def _main() -> None:
        bus = MessageBus(redis_url, instance_id="command-center-ui")
        await bus.connect()
        patterns = [
            "intel.regime",
            "intel.futures",
            "intel.options",
            f"bar.5m.{symbol}",
            "sys.health",
            "sys.circuit_breaker",
        ]
        pubsub_subscriber = CacheSubscriber(bus, patterns, cache, topic_key_fn=_cache_key_for)
        await asyncio.gather(
            pubsub_subscriber.run_forever(),
            _poll_streams_forever(bus, cache),
        )

    try:
        asyncio.run(_main())
    except Exception as exc:  # noqa: BLE001 — UI 스레드 예외가 프로세스를 죽이면 안 됨
        cache.update("LiveConnectionError", _error_message(str(exc)))


# 화면이 따라가는 Stream 토픽 — pub/sub으로는 못 받는 것들(모듈 docstring).
_WATCHED_STREAM_TOPICS: tuple[str, ...] = ("decision.intent", "exec.fill")


async def _poll_streams_forever(
    bus: MessageBus,
    cache: StateCache,
    *,
    poll_ms: int = 1000,
    topics: tuple[str, ...] = _WATCHED_STREAM_TOPICS,
) -> None:
    """Stream 폴링 — **`$`를 두 번 쓰지 않는다** (2026-08-05 3차, P0-2).

    종전엔 토픽별로 순차 블록하며 `last_id`를 `"$"`로 남겨뒀는데, `$`는 위치가 아니라
    "호출 시점의 마지막 ID"라 그 사이에 도착한 메시지가 영영 안 왔다(`core/bus.py`의
    `read_streams()` docstring에 재현 경로). 판단이 하루 몇 건뿐인 토픽에서 절반을 놓치는
    구조였고, **놓쳤다는 사실조차 화면에 안 남는다** — 첫 판단이 나오는 순간이 가장 놓치기
    쉬운 순간이었다.

    여기서는 기동 시 한 번만 구체 ID로 고정하고(그게 "지금부터"의 정확한 표현이다), 그
    뒤로는 읽은 만큼만 전진한다. 블록도 토픽별이 아니라 한 번에 걸어 창 자체를 없앤다.
    """
    ## 창을 새로 열면 **직전 판단부터 보여준다** (2026-08-21 F-9)
    #
    # 종전엔 `stream_last_id()`로 "지금부터"를 잡고 시작했다. 그건 정확한 표현이지만,
    # 화면을 새로 여는 사람에게는 다음 발행이 올 때까지 빈 칸이다. 2026-08-21에 13:03에
    # 창을 열고 13:30까지 **27분**을 그렇게 기다렸다 — `decision.intent`가 30분 주기라
    # 그 사이엔 아무것도 안 온다.
    #
    # 소급분과 이후 전진은 **같은 ID 축**을 쓴다: `last_ids`를 `stream_last_id()`가
    # 아니라 여기서 읽은 마지막 ID로 고정한다. 그러지 않으면 둘 사이에 창이 다시 생긴다
    # (2026-08-05 P0-2와 같은 형태).
    #
    # 소급된 값이 「방금 온 값」으로 보이면 더 나쁘다 — 신선도 배지는 메시지의 **원래
    # 발행 시각**으로 계산하므로(`_FRESHNESS_LIMITS`), 오래된 값이면 배지가 회색·붉은색
    # 으로 뜬다. 화면이 거짓말을 하지 않는다.
    last_ids: dict[str, str] = {}
    for topic in topics:
        backfilled = await bus.stream_tail(topic, count=1)
        for entry_id, message in backfilled:
            last_ids[topic] = entry_id
            # **원래 발행 시각으로 넣는다.** 지금 시각을 찍으면 어제 판단이 화면에서
            # 「0초 전」으로 보인다 — 빈 칸을 채우려다 화면이 거짓말을 하게 만드는 것이라
            # 원래 결함보다 나쁘다(F-9 회귀 위험 ②).
            cache.update(
                type(message).__name__, message, received_at=getattr(message, "ts_utc", None)
            )
        if topic not in last_ids:
            last_ids[topic] = await bus.stream_last_id(topic)
    while True:
        for topic, entry_id, message in await bus.read_streams(last_ids, block_ms=poll_ms):
            last_ids[topic] = entry_id
            cache.update(type(message).__name__, message)


def _error_message(text: str) -> BusMessage:
    # 캐시는 BusMessage만 받는다 — 에러도 그 계약을 지키려 DecisionIntent 재사용은 오해를
    # 부르니, rationale 필드가 있는 아무 메시지나 빌리는 대신 그냥 원문을 로그로만 남긴다.
    from messiah.core.messages import HealthLevel

    return Health(component="command-center-ui", level=HealthLevel.CRITICAL, detail=text)


def _get_live_cache(redis_url: str, symbol: str) -> StateCache:
    if "live_cache" not in st.session_state:
        st.session_state["live_cache"] = StateCache()
        st.session_state["live_thread_started"] = False
    cache: StateCache = st.session_state["live_cache"]
    if not st.session_state["live_thread_started"]:
        thread = threading.Thread(
            target=_run_live_subscriber, args=(redis_url, symbol, cache), daemon=True
        )
        thread.start()
        st.session_state["live_thread_started"] = True
    return cache


def _resolve_default_symbol(
    snapshot_path: Path = DEFAULT_SNAPSHOT_PATH, today: date | None = None
) -> tuple[str, str]:
    """오늘 화면이 기본으로 볼 종목과 그 **출처** (2026-08-14 F-3).

    ## 왜 상수를 지웠나

    종전엔 `DEFAULT_SYMBOL = "A05608"`이 소스에 박혀 있었다(R4 위반). 2026-08-14 첫 월물
    롤에서 수집·매매는 `A05609`로 옮겨갔는데 화면만 만기된 월물에 남아, **어제 차트를
    그리면서 붉은 경보로 *"봉 적재 정지 의심, 수집기(l1.collector)를 먼저 확인할 것"* 을
    띄웠다.** 같은 시각 수집기는 `age_seconds=0.4`로 완벽히 건강했고 `A05609` 봉을
    10:56:59까지 적재하고 있었다. 화면이 운영자를 정확히 틀린 방향으로 보냈다.

    ## 왜 다시 해석하지 않고 읽나

    여기서 마스터파일을 따로 읽으면 **해석 경로가 하나 더 생긴다** — 그러면 갈릴 자리도
    하나 더 생긴다. 상태판이 이미 `trading_symbol`로 "오늘 이 시스템이 실제로 보고 있는
    종목"을 쓴다. 화면은 그것을 **조회**한다. 해석이 아니라 조회가 되면 갈라질 수 없다.

    스냅샷이 없거나(프로세스 미기동·장후) 오래됐으면 만기 규칙으로 계산한다 — 그것도
    안 되면 빈 문자열이고, 사이드바 입력은 그대로 살아 있어 사람이 직접 넣을 수 있다.
    **부가 정보 하나 때문에 화면 전체가 죽는 것이 훨씬 나쁘다.**
    """
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        symbol = payload.get("trading_symbol")
        if isinstance(symbol, str) and symbol:
            return symbol, "상태판(수집 프로세스가 기록)"
    except (OSError, ValueError):
        pass
    try:
        # 상태판이 없으면(프로세스 미기동·장후) 정본 해석기로 간다 — 그쪽도 런타임 기록을
        # 먼저 조회하고 없을 때만 계산한다(2026-08-14 G-7).
        return symbol_resolution.resolve_for_tools(today or now_kst().date())
    except Exception:  # noqa: BLE001 — 화면을 죽이지 않는다
        return "", "⚠ 자동 해석 실패 — 직접 입력할 것"


def _default_redis_url() -> str:
    """LIVE 모드 사이드바의 Redis URL 기본값 — `configs/instance.yaml`의 실제 값을 읽는다
    (2026-07-29 수정). 예전엔 `"redis://localhost:6379/0"`을 그냥 하드코딩해뒀는데, MESSIAH의
    실제 Redis(`messiah-redis`)는 6380에서 돈다(`configs/instance.yaml`) — 이 PC엔 공교롭게
    6379에도 다른(무관한) Redis가 떠 있어서 `bus.connect()`가 에러 없이 "성공"해버리고, 화면은
    엉뚱한 Redis에 붙은 채 모든 배지가 영원히 NO_DATA로 남는 조용한 오연결이 실측으로 확인됐다
    (SYSTEM.md R4 하드코딩 금지 원칙과도 어긋났던 값). `load_instance()`는 시크릿을 해석하지
    않아(`core/config.py`) UI가 KIS 자격증명 없이도 안전하게 호출 가능 — 그래도 설정 파일 자체가
    없거나 깨졌을 가능성에 대비해 실패하면 프로젝트가 실제로 쓰는 6380 기본값으로 폴백한다(화면
    전체를 죽이는 것보다 낫다, `core/docker_bootstrap.py`류의 "부가 정보 실패가 본 기능을 막지
    않는다" 원칙과 동일)."""
    try:
        return load_instance().redis_url
    except Exception:
        return "redis://localhost:6380/0"


# ---------------------------------------------------------------- KRX 달력 (F-3·F-5 공용)


@st.cache_resource(show_spinner=False)
def _logging_ready() -> bool:
    """이 프로세스의 구조화 로깅을 **한 번만** 켠다 (2026-08-20 F-B · D-2 마감).

    ## 왜 UI에 로그가 없었나

    `_log_first_render_freshness()`(2026-08-18 G-0818I-4)는 `mlog.log("UISnapshotFreshness", ...)`
    를 부르고 있었다. 그런데 이 프로세스는 `mlog.setup()`을 **한 번도 부른 적이 없어서**
    `_logger`에 핸들러가 없었고, 그 줄들은 전부 허공으로 갔다. 계기를 만들어 두고 배선을
    안 한 것이다 — 2026-08-18부터 사흘간 UI 관련 이상점을 화면 캡처로만 쫓아야 했던 이유다.

    ## 왜 가드가 필요한가

    Streamlit은 **매 상호작용·매 5초 fragment마다 스크립트를 통째로 재실행**한다
    (`crash_forensics.enable` 옆 주석이 같은 사실을 다룬다). `setup()`은 멱등이 아니다 —
    `_logger.handlers.clear()`를 돌리고 `SessionStart`를 찍는다. 가드 없이 부르면 그 줄이
    렌더 수만큼 쌓인다.

    `st.cache_resource`가 그 가드다. 모듈 전역 플래그로는 안 된다 — Streamlit은 스크립트를
    새 네임스페이스에서 실행하므로 전역이 매번 초기화된다.

    ## 재기동 계수를 오염시키지 않는다 (확인함)

    2026-08-20 장중 계획은 이 변경이 `starts_by_process`를 오염시킬 수 있다며
    `NESTED_SESSION_ENV` 배선을 선행 조건으로 걸었다. **그 경로는 성립하지 않는다:**

    · `ops/integrity_report.log_paths_for()`는 `l1_daily`·`g2_paper`만 돌려준다 —
      `analyze_logs()`는 UI 로그를 **아예 읽지 않는다**.
    · UI 기동 수는 `observation_gaps.parse_ui_starts()`가 세는데, 그것은 Streamlit이 찍는
      `"Uvicorn server started"` 줄을 본다. `SessionStart`가 아니다.

    그래서 이 프로세스는 `NestedSessionStart`가 아니라 **`SessionStart`가 맞다.** UI는 배치
    단계가 아니라 독립 장기 프로세스이고, 그 줄이 싣는 `git_sha`·`source_mtime_max`가
    "화면이 어느 코드로 도는가"(2026-08-05 P0-1이 만든 축)의 유일한 1차 증거다.

    실패해도 화면은 뜬다 — 관측 도구가 화면을 죽이면 본말전도다(R10).
    """
    try:
        mlog.setup("command-center-ui")
    except Exception:  # noqa: BLE001
        return False
    return True


@st.cache_resource(show_spinner=False)
def _event_calendar_or_none() -> EventCalendar | None:
    """휴장일 달력 — 못 읽으면 `None`이고, **화면은 그래도 뜬다** (2026-08-11 F-3/F-5).

    `configs/krx_holidays.yaml`은 운영 프로세스가 이미 쓰는 파일이라 정상 배포에선 항상
    있다. 그런데 화면은 부가 정보를 얹는 자리고, 그 파일 하나가 없다고 차트·헬스 신호등까지
    같이 죽으면 정작 사고를 볼 수단이 사라진다 — UI 크래시가 32분간 안 보였던 2026-07-30이
    그 형태다. 실패는 `None`으로 돌려 호출측이 그 항목만 접게 한다.

    `cache_resource`인 이유: 달력은 프로세스 수명 동안 불변인데 Streamlit은 5초마다 스크립트
    전체를 다시 돌린다(`_LIVE_REFRESH_SECONDS`) — 캐시가 없으면 YAML을 하루 6천 번 읽는다.
    """
    try:
        return EventCalendar.from_file()
    except (OSError, ValueError) as exc:
        mlog.log(
            "UIEventCalendarUnavailable",
            f"휴장일 달력을 못 읽어 화면의 캘린더 항목을 접는다 — {exc}",
            error=str(exc),
        )
        return None


# ---------------------------------------------------------------- Parquet 캔들 (LIVE/REPLAY 공용)


def _available_dates(symbol: str, horizon: str, bar_dir: Path) -> list[date]:
    """경로 계층에 묻는다 — 장중에는 조각 디렉터리, 장후에는 통합본 파일로 배치가 다르므로
    여기서 glob 패턴을 직접 쓰면 당일이 목록에서 빠진다(`data/bar_paths.py` 배치 규칙).

    `data/archiver.py`가 아니라 `data/bar_paths.py`를 쓰는 게 중요하다 — 전자를 임포트하면
    polars가 이 프로세스에 딸려 올라와 자식 프로세스로 파싱을 미룬 의미가 사라진다."""
    return bar_paths.available_days(bar_dir, symbol, Horizon(horizon))


class _BarFileCache:
    """봉 읽기 캐시 겸 방어층 — UI 크래시 대응의 누적 결과물 (2026-07-30 ~ 08-03).

    ## 이 클래스가 지금 하는 일

    **① 재읽기 억제**: LIVE 모드는 `st.fragment(run_every=5)`로 5초마다 이 경로를 탄다 —
    파일이 안 바뀌었으면(mtime·크기 동일, `data/bar_paths.py`의 `day_signature()`) 읽기를
    통째로 건너뛴다. 봉은 Horizon당 봉 주기에 한 번만 바뀌므로 대부분의 재실행은 캐시
    히트다. 자식 프로세스를 띄우는 비용(약 0.8초)이 감당 가능한 이유가 바로 이것이다.

    **② 실패를 화면 죽이지 않고 흡수**: 읽기가 실패하면 **직전 성공본을 그대로 쓰고 화면에
    그 사실을 표시**한다 — 실패를 조용히 삼키지도(L18), 화면 전체를 죽이지도 않는다.

    **③ 스냅샷만 내보낸다**: 캐시가 들고 있는 것도 내보내는 것도 불변 `BarSeries`
    (`ui/bar_series.py`)다 — 전부 파이썬 기본형이라 소비자가 뭘 하든 네이티브 경로가 없다.

    ## 어떻게 여기까지 왔나 (같은 크래시, 네 번의 대응)

    `_polars_runtime.pyd` +0x083973c7, 0xc0000005 — **5거래일 연속 동일한 fault offset**
    (07-29 2건·07-30 6건·07-31 6건·08-03 2건). 매번 원인을 추정해 고쳤고 매번 재발했다.

        07-30  mmap 무효화가 원인이라 보고 → 원자적 쓰기 + `read_parquet_without_mmap()`
        07-30  잘린 바이트가 원인이라 보고 → Parquet 꼬리 매직(PAR1) 검증
        07-31  프로세스 내 동시성이라 보고 → numpy 선임포트 + 프로세스 전역 락 + list 변환
        08-03  ↑ 락의 **적용 범위**가 절반뿐이었음을 확인 → 스냅샷화(위 ③)

    08-03의 발견이 중요하다: 07-31의 락은 파싱만 감쌌는데 `load()`가 캐시된 `pl.DataFrame`
    **객체 자체**를 돌려줘서, 실제 소비(`is_empty()`, `to_list()` ×5)는 전부 락 밖에서 그
    **공유 객체**를 만지고 있었다. "직렬화했다"고 믿은 구간이 실제로는 절반이었다.

    ## 그리고 추측을 그만뒀다 (2026-08-03 P0-1(b))

    네 번째 가설을 세우는 대신, 크래시가 나도 **화면이 안 죽는 구조**로 바꿨다. 봉 파싱은
    이제 자식 프로세스에서 일어난다(`ui/bar_reader.py` → `data/bar_export.py`). 자식이
    access violation으로 죽으면 부모에겐 `BarExportError`일 뿐이고, 그건 이미 있는 위 ②
    경로로 흡수된다. **이 프로세스엔 polars가 아예 로드되지 않는다.**

    락은 그대로 둔다 — 이제 직렬화할 polars 호출은 없지만, 자식 프로세스를 동시에 여러 개
    띄우는 걸 막는 값어치가 있다(같은 파일에 대해 스레드 수만큼 0.8초짜리 프로세스가 동시에
    뜨는 건 낭비다).

    주의: 크래시의 **근본 원인은 여전히 미확정**이다. 위 구조는 원인을 밝힌 게 아니라 영향을
    가둔 것이다. 원인 규명은 `core/crash_forensics.py`(faulthandler 상시화)가 다음 크래시
    한 번으로 판정한다 — 그 전까지 "고쳤다"고 말하면 안 된다. 07-30·07-31에 세 번 그렇게
    판단했다가 같은 오프셋으로 재발했다.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str, str, str], tuple[tuple, BarSeries]] = {}
        self._lock = threading.Lock()

    def load(
        self, bar_dir: Path, symbol: str, horizon: Horizon, day: date
    ) -> tuple[BarSeries | None, str | None]:
        """반환: (스냅샷, 경고문). 경고문이 None이 아니면 스냅샷은 최신이 아닐 수 있다.

        하루치가 파일 하나가 아니라 여러 조각일 수 있으므로(`data/bar_paths.py` 배치 규칙)
        캐시 지문도 파일 집합 전체로 잡는다 — 하나만 보면 새 시간대 조각이 생겼을 때 갱신을
        놓친다.
        """
        with self._lock:
            return self._load_locked(bar_dir, symbol, horizon, day)

    def _load_locked(
        self, bar_dir: Path, symbol: str, horizon: Horizon, day: date
    ) -> tuple[BarSeries | None, str | None]:
        key = (str(bar_dir), symbol, horizon.value, day.isoformat())
        sources = bar_paths.day_sources(bar_dir, symbol, horizon, day)
        if not sources:
            return None, None  # 그날 데이터 없음 — 호출측이 "데이터 없음"으로 다룬다(경고 아님)

        cached = self._entries.get(key)
        signature = bar_paths.day_signature(sources)
        if cached is not None and cached[0] == signature:
            return cached[1], None

        try:
            series = self._read(bar_dir, symbol, horizon, day)
        except BarExportError as exc:
            # 자식이 죽었거나 멈췄다 — 크래시 종류까지 문구에 담는다(`ui/bar_reader.py`).
            return self._degraded(cached, str(exc))
        except Exception as exc:  # noqa: BLE001 — 어떤 실패든 화면이 죽으면 안 됨
            return self._degraded(cached, exc.__class__.__name__)
        if series is None:
            return self._degraded(cached, "읽기 실패")

        self._entries[key] = (signature, series)
        return series, None

    @staticmethod
    def _read(bar_dir: Path, symbol: str, horizon: Horizon, day: date) -> BarSeries | None:
        """자식 프로세스에 넘기는 지점 — 여기서만 봉 파일의 **내용**을 다룬다."""
        return read_day_series(bar_dir, symbol, horizon, day)

    @staticmethod
    def _degraded(cached, reason: str) -> tuple[BarSeries | None, str | None]:
        if cached is None:
            return None, f"봉 파일을 읽지 못했습니다({reason}) — 다음 갱신에 재시도"
        return (
            cached[1],
            f"봉 파일이 갱신 중이라 직전 값을 표시합니다({reason}) — 다음 갱신에 재시도",
        )


_BAR_CACHE = _BarFileCache()


def _load_bars_with_status(
    symbol: str, horizon: str, day: date, bar_dir: Path
) -> tuple[BarSeries | None, str | None]:
    """차트용 봉 스냅샷 + 신선도 경고 — 실제 읽기는 `_BarFileCache`가 자식 프로세스로 한다."""
    return _BAR_CACHE.load(bar_dir, symbol, Horizon(horizon), day)


def _load_bars(symbol: str, horizon: str, day: date, bar_dir: Path) -> BarSeries | None:
    """스냅샷만 필요한 호출자용 얇은 래퍼 — 신선도 경고까지 보려면
    `_load_bars_with_status()`를 쓴다."""
    return _load_bars_with_status(symbol, horizon, day, bar_dir)[0]


def _candlestick_figure(bars: BarSeries, tick_size: float) -> go.Figure:
    """polars 객체를 plotly에 **직접 넘기지 않는다**(2026-07-31).

    2026-07-31 크래시 6건의 파이썬 레벨 흔적은 전부 이 함수 안이었다
    (`app.py:_candlestick_figure` → `go.Candlestick` → `_plotly_utils.basevalidators.
    is_homogeneous_array` → `np.ndarray` 접근). plotly의 값 검증기는 넘어온 객체가 무엇인지
    모른 채 numpy/배열 프로토콜을 더듬는데, 그 대상이 polars Series면 네이티브 변환 경로를
    타게 된다.

    2026-08-03부터는 이 함수가 애초에 polars 객체를 **받을 수 없다** — 입력이 `BarSeries`
    (전부 파이썬 기본형인 불변 스냅샷)이기 때문이다. 예전엔 여기서 매 렌더마다 `to_list()`로
    변환했는데, 그 변환이 락 밖에서 공유 프레임을 만지는 바로 그 경로였다
    (`_BarFileCache` docstring ④). 이제 변환은 캐시 채울 때 락 안에서 한 번만 일어난다.

    여기 남은 일은 **틱 → 가격 환산**뿐이다 — `tick_size`가 사이드바 입력값이라 캐시에
    구울 수 없어서다(`ui/bar_series.py` "왜 틱을 그대로 담나").
    """
    opens, highs, lows, closes = (
        [v * tick_size for v in column]
        for column in (bars.o_ticks, bars.h_ticks, bars.l_ticks, bars.c_ticks)
    )
    fig = go.Figure(
        data=[
            go.Candlestick(
                # 이미 naive KST 벽시계다(`ui/bar_series.py`) — tz-aware로 넘기면 plotly가
                # 자기 기준으로 다시 해석할 여지가 생기고, 그 버그는 2026-07-29에 이미 한 번
                # 겪었다(09:00 개장봉이 00:00으로 찍힘).
                x=list(bars.x_kst),
                open=opens,
                high=highs,
                low=lows,
                close=closes,
                increasing_line_color="#00C9A7",
                decreasing_line_color="#FF5C7A",
            )
        ]
    )
    fig.update_layout(
        template="plotly_dark",
        height=400,
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis_rangeslider_visible=False,
    )
    return fig


# ---------------------------------------------------------------- 배지 렌더링 helper

# NO_DATA 한 색이 **서로 다른 세 가지 사실**을 덮고 있었다 (2026-08-05 3차, P1-2):
#
#   ① 끊김    — 발행자가 있었는데 지금 안 온다 (진짜 사고)
#   ② 미배선  — 이 구성엔 발행자가 아예 없다 (구조적으로 영원히 안 온다)
#   ③ 대기    — 발행자는 살아 있는데 아직 발행 조건이 안 됐다
#
# 셋이 같은 회색 NO_DATA로 보이면 Redis가 끊긴 것과 "원래 그런 것"이 구분되지 않는다 —
# 마흐디 L18(값 없음과 정상을 혼동하지 않는다)이 정확히 막으려던 형태다. CB 배지가 이미
# "미사용/데이터 없음"으로 쓰던 패턴을 나머지 토픽으로 넓힌 것이다.
#
# **①은 관측으로 판정한다** — 발행자의 heartbeat가 살아 있으면 ②/③, 죽었으면 ①이다.
#
# ## ②도 관측으로 판정한다 (2026-08-21 F-11 · 1-12)
#
# 종전엔 ②가 **선언**이었다. 아래 표에 "미배선 — Registry에 live 번들이 0개"라고 적혀
# 있었고, 그 문장은 **2026-08-11에 번들이 승격된 뒤로 열흘째 거짓**이었다. 2026-08-21
# 13:03에 창을 열었을 때 화면은 「미배선」이라고 말했고, 27분 뒤 13:30에 값이 정상으로
# 들어왔다 — 배선은 처음부터 멀쩡했고 **주기가 아직 안 돌았을 뿐**이었다.
#
# 정적 선언은 코드보다 낡는다. 그리고 그 낡음은 아무 계기에도 안 잡힌다 — 문자열이기
# 때문이다. 그래서 **그 문장에 도달하는 경로를 관측에 묶는다**: 「한 번도 안 왔다」에
# 시간 조건을 붙여, 관측 창을 채우기 전까지는 「대기」라고만 말한다.
#
# 고정 문자열을 지우는 것이 목적이 아니다 — `OptionsView`처럼 **정말로 미배선인 것**은
# 관측 창을 넘기고 나면 그대로 「미배선」이 나와야 하고, 아래 갈래가 그것을 자연히 만든다.
_ABSENCE_REASON: dict[str, str] = {
    "FuturesView": "미배선 또는 끊김 — 관측 창 동안 intel.futures가 한 번도 안 왔다",
    "OptionsView": "미배선 또는 끊김 — 관측 창 동안 intel.options가 한 번도 안 왔다",
    "RegimeState": "미배선 또는 끊김 — 관측 창 동안 intel.regime이 한 번도 안 왔다",
    "DecisionIntent": "미배선 또는 끊김 — 관측 창 동안 decision.intent가 한 번도 안 왔다",
    "Fill": "미배선 또는 끊김 — 관측 창 동안 exec.fill이 한 번도 안 왔다",
}

#: 「미배선」이라고 말하기 전에 **얼마나 들어봐야 하는가** (2026-08-21 F-11 ㉡).
#:
#: 이 화면이 보는 토픽 중 가장 느린 것이 30분 주기다(구동 Horizon이 30m인 live 번들).
#: 그 2배를 기다린다 — 1회 결손까지 흡수하고도 안 오면 그때는 주기 문제가 아니다.
#: **이 시간 조건이 설계의 핵심이다.** 없으면 새 창이 매번 「미배선」을 잠깐 보여준다
#: (`ever_seen`은 세션별이라 창을 새로 열면 리셋된다).
#:
#: 숫자를 새로 쓰지 않는다 — 가장 굵은 Horizon의 길이에서 파생시킨다(F-2가
#: 합성기 위상 상수에 세운 규율과 같다. 그 상수는 2026-08-24 F-21에서
#: `_COMPOSE_SCHEDULER_PHASE_SECONDS`로 개명됐다 — 파생 규율만 인용한다).
_ABSENCE_OBSERVE_SECONDS = HORIZON_SECONDS[Horizon.M30] * 2

# 위 토픽들의 발행 프로세스 — 전부 G2 러너 한 프로세스다(`scripts/run_g2_paper_trading.py`).
# 그 heartbeat가 ①과 ②/③을 가르는 유일한 관측 근거다.
_PUBLISHER_OF: dict[str, str] = dict.fromkeys(_ABSENCE_REASON, "g2.pipeline")


def _absence_reason(source, key: str) -> str | None:
    """NO_DATA 배지에 붙일 사유 — 없으면 None(사유를 못 대면 아무 말도 안 한다).

    REPLAY에서는 판정하지 않는다. 그 모드의 NO_DATA는 "이 재생 스냅샷에 그 값이 없다"는
    뜻이지 발행자의 생사와 무관하다 — LIVE의 사유를 그대로 갖다 붙이면 재생 화면이 있지도
    않은 사고를 보고하게 된다.
    """
    if source.mode != DataSourceMode.LIVE:
        return None

    # 연결 자체가 실패했으면 모든 토픽이 같은 이유로 비어 있다 — 개별 사유보다 이게 먼저다.
    if isinstance(source.snapshot("LiveConnectionError").message, Health):
        return "끊김 — LIVE 연결 실패"

    # **①「끊김」 판정은 손대지 않는다** (F-11 ㉠). 진짜 사고를 「대기」로 덮으면 이
    # 변경이 원래 결함보다 나쁘다.
    publisher = _PUBLISHER_OF.get(key)
    if publisher is not None:
        snap = source.snapshot(health_cache_key(publisher))
        if not isinstance(snap.message, Health):
            return f"끊김 — {publisher} heartbeat 없음(프로세스 미기동)"
        if snap.badge == FreshnessBadge.STALE:
            return f"끊김 — {publisher} 응답 없음({snap.age_seconds:.0f}초)"

    # 발행자는 살아 있다. 남은 질문은 「배선이 없다」인가 「아직 주기가 안 돌았다」인가고,
    # 그것을 가르는 것은 **얼마나 들어봤는가**다 (2026-08-21 F-11).
    listening = None
    getter = getattr(source, "listening_seconds", None)
    if callable(getter):
        listening = getter()
    if listening is None:
        # 못 재면 판정하지 않는다 — 모르는 것을 「미배선」이라 부르지 않는다(L18).
        return "대기 — 아직 수신 없음(관측 시간을 못 재 미배선 여부는 판정 불가)"
    if listening < _ABSENCE_OBSERVE_SECONDS:
        # **판정 근거를 화면에 함께 적는다.** 사람이 "왜 대기라고 하지"를 안 물어도 된다.
        return (
            f"대기 — 아직 수신 없음 (듣기 시작 후 {listening / 60:.0f}분 · "
            f"{_ABSENCE_OBSERVE_SECONDS / 60:.0f}분까지 대기)"
        )
    return _ABSENCE_REASON.get(key, "미배선 또는 끊김 — 관측 창 동안 수신 없음")


def _off_session_caption(snapshot, *, now: datetime) -> str | None:
    """세션 밖의 오래된 값은 **초 단위로 세지 않는다** (2026-09-01 F-85).

    `죽음(1036분 침묵)`을 `62160초 전 수신`으로 바꿔 적는 것은 문구만 순화한 것이지 사람
    에게 도움이 되지 않는다. 세션 밖이라면 알아야 할 것은 경과가 아니라 **언제 것인가**다.
    세션 안이거나 값이 아직 신선하면 아무것도 바꾸지 않는다(None).
    """
    if snapshot.in_session is not False:
        return None
    age = snapshot.age_seconds
    if age is None or age <= DEFAULT_STALE_AFTER_SECONDS:
        return None
    seen_at = now - timedelta(seconds=age)
    phase = "장 개시 전" if now.time() < DEFAULT_SESSION.first_tick_time else "장 마감 후"
    return f"{phase} — 최근 수신은 {seen_at.strftime('%m-%d %H:%M')}"


def _badge_caption(label: str, snapshot, *, reason: str | None = None) -> None:
    color = _BADGE_COLOR[snapshot.badge]
    st.markdown(
        f"**{label}** &nbsp; <span style='color:{color}'>● {snapshot.badge.value}</span>",
        unsafe_allow_html=True,
    )
    if snapshot.badge == FreshnessBadge.NO_DATA and reason:
        st.caption(reason)
        return
    # **마지막 수신 시각을 항상 병기한다** (2026-08-14 F-4·G-4). 배지 한 글자로는 "느려졌다"와
    # "죽었다"를 못 가르고, 숫자를 보고 사람이 주기를 역산하게 두면 그것이 곧 오늘 아침에
    # 사람이 세 화면을 15분간 대조한 이유다.
    if snapshot.age_seconds is not None:
        cadence = snapshot.cadence_seconds
        note = f" · 주기 {cadence / 60:.0f}분" if cadence and cadence >= 60 else ""
        if snapshot.dead:
            # 주기의 3배를 넘었다 — "느려졌다"가 아니라 "확인하라"다.
            st.caption(f"**죽음**({snapshot.age_seconds / 60:.0f}분 침묵){note} · 프로세스 확인")
        else:
            off_session = _off_session_caption(snapshot, now=now_kst())
            st.caption(off_session or f"{snapshot.age_seconds:.0f}초 전 수신{note}")


# ---------------------------------------------------------------- Kill Switch

# `sys.kill` 발행 경로 결선 완료 (2026-08-07 고도화 6). LIVE 모드에서만 누를 수 있다 —
# REPLAY 화면의 버튼이 실계좌를 청산하면 그건 이 화면이 만들 수 있는 최악의 사고다.
_KILL_SWITCH_WIRED = True


def _publish_kill(redis_url: str, reason: str, *, bus_factory=MessageBus) -> None:
    """`sys.kill`에 `KillSignal`을 발행한다 (2026-08-07 고도화 6).

    `bus_factory`는 테스트 주입점이다. **이 인자가 없으면 단위 테스트가 운영 Redis
    (`redis://localhost:6380/0`)에 진짜 kill을 쏜다** — 2026-08-07 구현 중 UI 스모크
    테스트가 정확히 그 경로를 밟을 뻔했다(구동 중이던 G2가 수신 분기 없는 구버전이라
    우연히 무해했을 뿐이다). `reference_price`·`listed`와 같은 주입 패턴.

    Streamlit 콜백은 동기라 자기 이벤트 루프를 열고 닫는다 — 구독용 백그라운드 스레드의
    버스를 재사용하지 않는 이유는 그쪽이 `pubsub.listen()`에 블록돼 있어서 같은 커넥션으로
    발행을 끼워 넣을 수 없기 때문이다. 발동은 하루 0~1회라 커넥션 비용은 문제가 아니다.

    **누가 받는가**: `core/bus.py`의 `subscribe()`가 `TOPIC_KILL`을 항상 패턴에 넣으므로
    (그쪽 docstring "어떤 구독자도 kill을 놓치지 않는다") G2 러너의 `TradingPipeline`이
    이미 이 메시지를 **받고 있었다** — 종전엔 `_dispatch`에 처리 분기가 없어 조용히
    버려졌을 뿐이다. 그 분기가 이번에 붙었다(`strategy/pipeline.py handle_kill()`).
    """

    async def _send() -> None:
        bus = bus_factory(redis_url, instance_id="command-center-ui")
        await bus.connect()
        try:
            await bus.publish(TOPIC_KILL, KillSignal(reason=reason, triggered_by="manual"))
        finally:
            await bus.close()

    asyncio.run(_send())


def _render_kill_switch(source, redis_url: str | None) -> None:
    """2단 확인 뒤 `sys.kill` 발행 (2026-08-07 결선, 종전 2026-08-05 3차 P2의 후속).

    ## 못 하는 일은 못 하게 보인다 (2026-08-05의 규율, 그대로 유지)

    종전엔 화면에서 가장 강한 요소(적색 primary 버튼)가 멀쩡히 눌렸고, 2단 확인까지 통과하면
    "알려진 갭"이라는 에러가 떴다. 그런데 그 에러는 `st.session_state`에 남지 않아 **5초 뒤
    fragment 재실행에 사라졌다** — 비상시에 누르고, 사라진 문구를 못 본 채 "발동됐다"고 믿는
    것이 안 눌리는 것보다 훨씬 위험하다.

    이제 발행 경로가 생겼으므로 그 규율이 겨냥하는 대상만 바뀐다: **REPLAY에서는 여전히
    누를 수 없다.** 재생 화면의 버튼이 살아 있는 계좌를 청산하는 것이 이 화면이 만들 수
    있는 최악의 사고이고, 그건 "미배선"보다 나쁘다.

    ## 발행 실패도 화면에 남는다

    Redis가 끊겨 발행이 실패하면 그 사실이 세션 상태에 남는다. 비상시에 가장 나쁜 것은
    "눌렀는데 아무 일도 안 일어났고 그것을 모르는 것"이다 — 그 경우 브로커 화면으로
    가라는 문구가 그대로 필요하다.
    """
    live = source.mode == DataSourceMode.LIVE and bool(redis_url)
    clicked = st.button("🛑 KILL SWITCH", type="primary", disabled=not live)
    if not live:
        st.caption("REPLAY 모드 — 발동 불가(LIVE에서만 sys.kill을 발행한다)")

    if clicked:
        st.session_state["kill_confirm_pending"] = True
    if st.session_state.get("kill_confirm_pending"):
        st.warning("정말 전량 청산·주문 차단하시겠습니까? (2단 확인)")
        if st.button("확인 — 즉시 발동"):
            st.session_state["kill_confirm_pending"] = False
            try:
                _publish_kill(redis_url or "", "Command Center 수동 발동")
                st.session_state["kill_requested"] = "발행됨"
            except Exception as exc:  # noqa: BLE001 — 실패도 화면에 남아야 한다
                st.session_state["kill_requested"] = f"발행 실패: {exc}"
    state = st.session_state.get("kill_requested")
    if state == "발행됨":
        # 한 번 뜨고 사라지지 않는다 — 발동은 세션이 끝날 때까지 화면에 남는 사실이다.
        st.error(
            "Kill Switch 발행됨(sys.kill) — 게이트 정지·청산 주문은 G2 러너가 수행한다. "
            "**체결 완료 여부는 브로커 화면에서 직접 확인할 것**"
        )
    elif state:
        st.error(f"Kill Switch {state} — 즉시 브로커 화면에서 직접 청산할 것")

    _render_resume(source, redis_url)


def _publish_resume(redis_url: str, operator: str, reason: str, *, bus_factory=MessageBus) -> None:
    """`sys.resume` 발행 — `_publish_kill()`과 같은 구조·같은 주입점(그쪽 docstring 참고)."""

    async def _send() -> None:
        bus = bus_factory(redis_url, instance_id="command-center-ui")
        await bus.connect()
        try:
            await bus.publish(TOPIC_RESUME, ResumeSignal(operator=operator, reason=reason))
        finally:
            await bus.close()

    asyncio.run(_send())


def _render_resume(source, redis_url: str | None) -> None:
    """Kill의 반대편 — 닫힌 게이트를 사람이 다시 연다 (2026-08-11).

    ## 왜 빨간 버튼만 있었나

    `sys.kill`은 2026-08-07에 결선됐는데 되돌리는 경로가 같이 안 붙었다. `KillSwitch`가
    한 번 발동하면 `handle_kill()`이 재진입 가드에 걸려 게이트만 다시 닫으므로, 푸는 방법이
    **프로세스 재기동뿐**이었다 — 2026-08-11 09:27에 점검용 kill 한 번으로 운영 G2의 게이트가
    닫혔고 실제로 재기동해야 했다.

    ## 게이트가 열려 있으면 버튼을 안 그린다

    "지금 할 일이 없는 버튼"을 상시 노출하면 비상시에 눈이 그것부터 찾는다. 게이트 상태는
    이미 CB 배지가 `gateway_halted`로 말하고 있으므로(`_render_circuit_breaker_badge`),
    그 사실이 참일 때만 이 버튼이 존재한다.

    ## 여기서 판단하지 않는다

    누른다고 열리는 것이 아니다 — `TradingPipeline.handle_resume()`이 서킷브레이커가
    의심/확정 중이면 **거부한다**. 화면은 "요청했다"까지만 말하고, 실제로 열렸는지는 다음
    heartbeat의 `gateway_halted`가 답한다. 그 둘을 화면이 합쳐 말하면 안 된다(L18).
    """
    snap = source.snapshot("CircuitBreakerStatus")
    status = snap.message
    if not isinstance(status, CircuitBreakerStatus) or not status.gateway_halted:
        return

    live = source.mode == DataSourceMode.LIVE and bool(redis_url)
    if not live:
        st.caption("주문 게이트 정지 중 — REPLAY에서는 재가동할 수 없다")
        return

    operator = st.text_input("재가동 승인자(operator)", key="resume_operator", max_chars=32)
    if st.button("주문 게이트 재가동", disabled=not operator.strip()):
        st.session_state["resume_confirm_pending"] = True
    if st.session_state.get("resume_confirm_pending"):
        st.warning("주문 차단을 해제하고 KillSwitch를 리셋합니다. (2단 확인)")
        if st.button("확인 — 재가동 요청"):
            st.session_state["resume_confirm_pending"] = False
            try:
                _publish_resume(redis_url or "", operator.strip(), "Command Center 수동 재가동")
                st.session_state["resume_requested"] = "요청 발행됨"
            except Exception as exc:  # noqa: BLE001 — 실패도 화면에 남아야 한다
                st.session_state["resume_requested"] = f"발행 실패: {exc}"
    requested = st.session_state.get("resume_requested")
    if requested == "요청 발행됨":
        st.info(
            "재가동 요청 발행됨(sys.resume) — **열렸는지는 이 배지의 "
            "`주문 게이트 정지 중` 문구가 사라지는 것으로 확인할 것**. "
            "서킷브레이커가 의심/확정 중이면 G2가 거부한다(로그 `ResumeRefused`)"
        )
    elif requested:
        st.error(f"재가동 {requested}")


# ---------------------------------------------------------------- 코드 버전 스트립


def _component_versions(source) -> dict[str, str]:
    """heartbeat를 실제로 보내온 컴포넌트의 적재 코드 SHA — 안 보낸 건 담지 않는다.

    안 보낸 컴포넌트는 이미 신호등이 "데이터 없음"으로 드러내고 있다. 여기까지 끌고 오면
    같은 사실을 두 곳에서 다른 말로 보고하게 된다(버전 불일치 vs 프로세스 사망).
    """
    versions: dict[str, str] = {}
    for component, _label in _HEALTH_COMPONENTS:
        message = source.snapshot(health_cache_key(component)).message
        if isinstance(message, Health):
            versions[component] = message.git_sha
    return versions


# `stale_reason` → 사람이 **할 수 있는 일** (2026-09-01 F-83).
#
# 어긋남의 종류마다 처방이 다르다. 하나로 뭉뚱그리면 미커밋 때문에 뜬 앰버를 보고 재기동을
# 반복하게 된다 — 그러면 안 고쳐지고, 고쳐지지 않는 경보는 곧 무시된다.
_RESTART_CAPTION = "재기동해야 최신 코드가 적재된다 — 지금 화면의 판정은 옛 규칙의 결과다"


def _version_stale_caption(reason: str | None, dirty_files: int | None) -> str | None:
    """어긋남의 종류에 맞는 처방 한 줄 — 어긋남이 없으면 None (2026-09-01 F-83)."""
    if reason is None:
        return None
    dirty = f"저장 안 된 소스 {dirty_files}파일이 섞여 돈다 — 재기동해도 커밋 전엔 안 바뀐다"
    if reason == "sha_mismatch":
        return _RESTART_CAPTION
    if reason == "worktree_dirty":
        return dirty
    if reason == "both":
        return f"{_RESTART_CAPTION} · {dirty}"
    return None


def _worktree_dirty_files() -> int | None:
    """미커밋 소스 파일 수 — 못 읽으면 None (2026-09-01 F-83).

    **화면은 git을 직접 부르지 않는다**(2026-08-31 F-78). 이 값은 수집 프로세스가
    `logs/status_snapshot.json`에 이미 적어 둔 것을 그대로 읽는다. 파일이 없거나 필드가
    없으면 `None` — 「0파일」과 「못 쟀다」는 다른 사실이고, 후자를 전자로 접으면 화면이
    "깨끗하다"고 거짓말한다(L18).
    """
    snapshot = load_snapshot()
    if not isinstance(snapshot, dict):
        return None
    version = snapshot.get("code_version")
    if not isinstance(version, dict):
        return None
    dirty = version.get("worktree_dirty_files")
    return dirty if isinstance(dirty, int) else None


def _render_version_strip(source) -> None:
    """ "고친 코드가 지금 돌고 있는가" (2026-08-05 3차, P0-1).

    2026-08-05엔 11:03·11:57에 커밋한 감시 장치가 장중 내내 안 돌았다 — 프로세스들이 08:35에
    떴기 때문이다. 신호등은 전부 초록이었고 그 초록은 **구버전이 보낸 것**이었는데, 화면
    어디에도 그 사실이 없었다. 이 한 줄이 그 축이다(`core/version.py` 모듈 docstring).

    어긋남을 **초록으로 칠하지 않는다** — 앰버는 `_BADGE_COLOR[STALE]`과 같은 값·같은 뜻
    ("이 화면이 지금 사실을 말하고 있는지 의심하라")이다.

    ## 화면과 상태판이 **같은 분에 반대말을 했다** (2026-09-01 F-83 · F-76 잔여)

    2026-08-31 F-76이 `ops.status_board.code_version_axis()`의 `stale`을 「커밋과 다르다」
    에서 「저장소 상태와 다르다」로 넓혔다 — 미커밋 소스가 돌고 있으면 그것도 어긋남이다.
    그런데 **화면은 그 함수를 안 탔다.** 두 SHA만 보는 `assess_version_drift()`를 직접
    읽었으므로, 08:47:52 `status_snapshot`이 `stale: true · 미커밋 5파일`이라 적은 그
    시각에 화면은 회색으로 "전 프로세스 동일"이라 적었다. 값이 아니라 **문장이 갈렸다.**

    처방도 갈라 준다. 종전 캡션 `재기동해야 최신 코드가 적재된다`는 `sha_mismatch`에만
    맞는 말이다 — 미커밋 때문에 어긋난 것이라면 **재기동해도 안 바뀐다.** 틀린 처방을
    지우는 것이 이 항목의 절반이다.

    미커밋 건수는 **화면이 git을 부르지 않고** `status_snapshot.json`에서 읽는다
    (2026-08-31 F-78 규율 — 점검·화면 프로세스가 저장소에 쓰기 락을 만들지 않는다).
    못 읽으면 `None`을 넘기고, 그때 축은 「미커밋 미측정」이라고 말한다(0으로 접지 않는다).
    """
    head_sha = head_git_sha()
    drift = assess_version_drift(
        process_sha=PROCESS_GIT_SHA,
        head_sha=head_sha,
        component_shas=_component_versions(source),
    )
    dirty_files = _worktree_dirty_files()
    axis = code_version_axis(drift=drift, dirty_files=dirty_files, head_sha=head_sha)
    color = "#FFB020" if axis["stale"] else "#8A8F98"
    uptime = uptime_text(PROCESS_STARTED_AT)
    st.markdown(
        f"<span style='color:{color}'>● {axis['summary']} · 화면 기동 후 {uptime}</span>",
        unsafe_allow_html=True,
    )
    caption = _version_stale_caption(axis["stale_reason"], dirty_files)
    if caption:
        st.caption(caption)


def _render_irrecoverable_loss_strip() -> None:
    """**오늘 이미 잃은 것** (2026-08-10 B-2).

    바로 위 컴포넌트 신호등은 *"지금 살아 있나"*에 답한다. 2026-08-10에 그 넷이 종일
    초록이었고, 그날 아침 38분의 체결틱·수급·옵션체인은 이미 영원히 사라진 뒤였다 —
    화면이 틀린 게 아니라 **아무 자리도 그 질문을 안 했다.**

    출처는 수집 프로세스가 쓰는 `logs/status_snapshot.json`이다(`ops/loss_ledger.py`).
    버스가 아니라 파일을 읽는 이유: 이 값은 수집 프로세스 안에서만 셀 수 있고(폴러의 최종
    실패는 버스에 안 실린다), 그 파일은 화면이 죽어도 계속 쓰인다.

    **못 읽으면 조용히 넘어가지 않는다.** 손실 축이 비어 있는 것과 손실이 없는 것은 다르고,
    전자를 초록으로 칠하는 것이 이 화면이 2026-08-10에 한 일이다.
    """
    snapshot = load_snapshot()
    if snapshot is None:
        st.markdown(
            "<span style='color:#8A8F98'>● 오늘 손실 — 상태 스냅샷 없음(수집 프로세스 확인)</span>",
            unsafe_allow_html=True,
        )
        return
    loss = snapshot.get("irrecoverable_loss") or {}
    summary = loss.get("summary")
    if not summary:
        st.markdown(
            "<span style='color:#8A8F98'>● 오늘 손실 — 이 축이 없는 구버전 스냅샷</span>",
            unsafe_allow_html=True,
        )
        return
    clean = bool(loss.get("clean"))
    color = "#22C55E" if clean else "#FF5C7A"
    st.markdown(f"<span style='color:{color}'>● {summary}</span>", unsafe_allow_html=True)
    if not clean:
        st.caption("소급 경로가 없는 계열이다 — 지금 없는 것은 나중에도 없다")


# ---------------------------------------------------------------- 존 렌더링


def render_top_bar(source, symbol: str, redis_url: str | None = None) -> None:
    """`redis_url`은 Kill Switch 발행에만 쓴다 (2026-08-07 고도화 6) — 없으면 버튼이
    비활성이다. 기본값 None인 이유는 REPLAY 경로와 테스트가 이 인자를 안 주기 때문이고,
    그 경우 **못 누르는 쪽으로** 실패하는 것이 맞다."""
    st.markdown(f"### MESSIAH Command Center — `{symbol}`")
    cols = st.columns([2, 2, 2, 2, 1])
    with cols[0]:
        st.metric("모드", source.mode.value)
    with cols[1]:
        futures_snap = source.snapshot("FuturesView")
        _badge_caption("intel.futures", futures_snap, reason=_absence_reason(source, "FuturesView"))
    with cols[2]:
        decision_snap = source.snapshot("DecisionIntent")
        _badge_caption(
            "decision.intent", decision_snap, reason=_absence_reason(source, "DecisionIntent")
        )
    with cols[3]:
        _render_circuit_breaker_badge(source)
    with cols[4]:
        _render_kill_switch(source, redis_url)

    # `_run_live_subscriber`가 예외를 삼키고 "LiveConnectionError" 키에 Health(CRITICAL)를
    # 남긴다(모듈 docstring) — 지금까지는 그 키를 읽는 render_* 함수가 하나도 없어서, 연결
    # 실패가 모든 배지를 그냥 조용히 영원한 NO_DATA로만 보여줬다(2026-07-29 실측 발견: 잘못된
    # 기본 Redis URL로 "연결은 성공했지만 엉뚱한 서버"인 경우도 똑같이 조용했다). REPLAY 모드의
    # `snapshot()`은 이 키를 절대 채우지 않으므로 무조건 호출해도 안전하다.
    conn_error = source.snapshot("LiveConnectionError")
    if isinstance(conn_error.message, Health):
        st.error(f"LIVE 연결 실패: {conn_error.message.detail}")

    _render_health_strip(source)
    _render_irrecoverable_loss_strip()
    _render_version_strip(source)
    st.divider()


def _decision_staleness_warning(snapshot, *, now: datetime) -> str | None:
    """이 판단이 **지금 시장의 판단이 아닐 때** 그 사실을 한 문장으로 (2026-09-01 F-84).

    배지는 패널 밖 다른 줄에 있고, 값은 좌상단 첫 칸에 크게 있다. 2026-09-01 아침 화면은
    전일 15:29의 `LONG`을 그 큰 칸에 그대로 그렸다 — 배지와 값이 **같은 시선 안에** 없으면
    사람은 값만 읽는다. 신선할 때는 아무 말도 하지 않는다(매번 뜨는 문구는 배경이 된다).
    """
    if snapshot.badge not in (FreshnessBadge.STALE,) and not snapshot.dead:
        return None
    age = snapshot.age_seconds
    if age is None:
        return None
    seen_at = now - timedelta(seconds=age)
    return (
        f"이 판단은 {age / 60:.0f}분 전({seen_at.strftime('%m-%d %H:%M')}) 것이다 — "
        "지금 시장의 판단이 아니다"
    )


def render_ai_decision_panel(source) -> None:
    st.subheader("① AI Decision")
    intent_snap = source.snapshot("DecisionIntent")
    if isinstance(intent_snap.message, DecisionIntent):
        intent = intent_snap.message
        # **신선도 경고가 값보다 위에 온다** (2026-09-01 F-84) — 아래 큰 칸을 읽기 전에
        # "이건 지금 값이 아니다"가 눈에 들어와야 순서가 맞는다.
        warning = _decision_staleness_warning(intent_snap, now=now_kst())
        if warning:
            st.warning(warning)
        # **델타 칸에 변화량이 아닌 값을 넣지 않는다** (2026-09-01 F-84). 종전엔 확신도를
        # `st.metric`의 세 번째 인자로 넘겼는데, 그 자리는 Streamlit이 **증감**으로 읽어
        # 초록 상승 화살표를 붙인다 — 확신도 62%가 "62% 올랐다"로 보였다. 값은 캡션으로
        # 내리고 화살표를 없앤다.
        st.metric("의도", intent.side.value)
        st.caption(f"확신도 {intent.confidence:.0%} · 불확실성 {intent.uncertainty:.2f}")
        st.text(intent.rationale)
    else:
        st.info(_absence_reason(source, "DecisionIntent") or "decision.intent 데이터 없음")

    futures_snap = source.snapshot("FuturesView")
    if isinstance(futures_snap.message, FuturesView):
        view = futures_snap.message
        st.caption(
            f"통합점수 S={view.score:+.3f} · 분산={view.dispersion:.3f} · n={view.n_experts}"
        )

    st.markdown("**옵션 후보**")
    options_snap = source.snapshot("OptionsView")
    if isinstance(options_snap.message, OptionsView):
        view = options_snap.message
        if view.no_option_reason:
            st.caption(f"NO_OPTION — {view.no_option_reason}")
        else:
            for candidate in view.candidates:
                st.caption(
                    f"{candidate.structure}: NetER={candidate.net_expected_return:.2f}pt "
                    f"POP={candidate.pop:.0%}"
                )
    else:
        st.info(_absence_reason(source, "OptionsView") or "intel.options 데이터 없음")


def _first_bar_expected_at(first_tick: dtime, horizon: str | None) -> dtime | None:
    """그 Horizon의 **첫 봉이 있어야 하는 시각** — 못 정하면 None (2026-09-01 F-82).

    `first_tick`은 「틱이 들어오기 시작하는 시각」이고, 첫 봉은 그로부터 한 Horizon이
    닫히고 유예까지 지나야 나온다. 알 수 없는 Horizon 문자열에 추정치를 지어내지 않는다 —
    None을 돌려주면 호출부가 옛 임계(첫 틱)를 그대로 쓴다(L18: 모르는 것은 모른다고 한다).
    """
    if not horizon:
        return None
    try:
        bar = Horizon(horizon)
    except ValueError:
        return None
    seconds = HORIZON_SECONDS[bar] + close_grace_ms(bar) / 1000.0
    base = datetime.combine(date.min, first_tick)
    return (base + timedelta(seconds=seconds)).time()


def _live_date_notice(
    chosen: date,
    *,
    now: datetime,
    calendar: EventCalendar | None = None,
    symbol: str = "",
    horizon: str | None = None,
) -> tuple[str, str]:
    """LIVE 차트 위에 붙일 날짜 문구 — 반환은 (severity, 문구).

    ## **"오늘"이라고 쓰기 전에 오늘인지 확인한다** (2026-08-05 3차, P1-3)

    종전엔 가장 최근 날짜를 무조건 `f"오늘({chosen}) 봉"`으로 캡션했다. 장 개시 전이거나
    봉 적재가 멈추면 그 최근 날짜는 **전일**이고, 그때 화면은 어제 차트에 "오늘" 라벨을
    붙인다. 차트는 화면에서 가장 큰 요소라 그 한 단어가 곧 "지금 시장이 이렇게 움직이고
    있다"로 읽힌다 — 값은 진짜인데 문구가 틀린, L18이 정확히 막으려던 형태다.

    ## 그런데 그 문구가 **정상과 사고를 한 문장에 담고 있었다** (2026-08-11 F-3)

    고친 문구는 `장 개시 전이거나 봉 적재가 멈춘 상태`였다. 앞쪽은 매일 아침 반복되는
    정상이고 뒤쪽은 P0인데, 화면은 둘을 같은 노란 박스로 말했다 — 그러면 사람은 그 박스를
    아침마다 보다가 무시하는 법을 배우고, 정작 적재가 멈춘 날에도 똑같이 넘긴다.

    **시스템은 둘을 가를 근거를 이미 갖고 있었다**: 오늘이 거래일인가(`EventCalendar`),
    지금이 첫 틱 시각(08:45, `SessionHours.first_tick_time`)을 지났는가. 그 둘만 보면
    "아직 올 때가 아니다"와 "올 때가 지났는데 없다"가 갈린다.

    달력을 못 읽으면(파일 부재·미등록 연도) 휴장 판정만 포기하고 시각 판정은 그대로
    한다 — 부가 정보 하나 때문에 화면 전체가 죽는 것이 훨씬 나쁘다
    (`EventCalendar.thursday_weekly_listing_resumes()`가 예외를 삼키는 것과 같은 판단).

    ## 그런데 **첫 틱은 첫 봉이 아니다** (2026-09-01 F-82)

    F-3이 세운 임계는 `first_tick_time`(08:45)이었다. 틱이 들어오는 시각과 그 틱들로 만든
    **봉이 디스크에 내려앉는 시각**은 다르다 — 2026-09-01 실측으로 1m 08:46 · 3m 08:48 ·
    5m 08:50이었다. 그래서 08:45~08:50 사이에는 화면이 매 거래일 **확정적으로** 적색
    `🛑 봉이 없다`를 띄웠고, 그 5분은 아무 사고도 아니었다. 매일 뜨는 경보는 경보가 아니라
    배경이고, 사람은 그것을 무시하는 법을 배운다 — F-3이 막으려던 바로 그 병이 임계 하나
    때문에 되살아나 있었다.

    임계를 **그 Horizon의 첫 봉 완성 시각**으로 옮긴다:

        첫 봉 예정 = 첫 틱(08:45) + Horizon 길이 + 완성봉 유예

    유예는 새 숫자를 적지 않고 `data/close_grace.close_grace_ms()` 정본에서 가져온다
    (1분봉 2,000ms · 상위는 **합성 대기 상한 + 오버헤드 여유** = 11,500ms, 2026-09-10 F-94로
    5,000ms에서 올랐다). `horizon`을 안 주면 **판정을 넓히지 않는다** — 옛 임계(첫 틱)
    그대로다.

    F-94로 유예가 6.5초 늘었으므로 이 임계도 그만큼 뒤로 밀린다. 2026-09-01 실측
    (1m 08:46 · 3m 08:48 · 5m 08:50)과는 **여전히 같은 분**에 떨어진다 — 3m 08:48:11.5 ·
    5m 08:50:11.5. 아래 ⚠의 교환 조건이 6.5초만큼 커지는 것이고, 그 감시는 그대로 두 축이 맡는다.

    ⚠ **이 변경은 진짜 적재 정지의 발견을 최대 5분 늦춘다.** 의도된 교환이고, 그 5분은
    `l1.collector` 배지와 `irrecoverable_loss` 스트립이 이미 독립적으로 감시한다 — 두 축이
    이 문구보다 빠르므로 실질 손실이 없다. 첫 봉 예정 시각이 지나도 없으면 문구는 F-3이
    쓴 `alert` **그대로**다(월물 롤 / 수집기 두 후보 유지 — 2026-08-14 F-3을 훼손하지 않는다).
    """
    today = now.date()
    if chosen == today:
        return "ok", f"오늘({chosen.isoformat()}) 봉 — LIVE는 항상 최신 날짜"

    tail = f"{chosen.isoformat()} 차트를 표시 중"

    if calendar is not None:
        try:
            if not calendar.is_trading_day(today):
                return "expected", f"ℹ {today.isoformat()}은 KRX 휴장 — 최근 거래일 {tail}"
        except ValueError:
            pass  # 휴장일 데이터 없는 연도 — 아래 시각 판정만으로 간다

    first_tick = DEFAULT_SESSION.first_tick_time
    if now.time() < first_tick:
        return "expected", (
            f"ℹ 장 개시 전({first_tick.strftime('%H:%M')} 첫 틱 예정) — 전일 {tail}"
        )

    first_bar = _first_bar_expected_at(first_tick, horizon)
    if first_bar is not None and now.time() < first_bar:
        return "expected", (
            f"ℹ 오늘 첫 {horizon}봉은 {first_bar.strftime('%H:%M')} 예정 — 그때까지 전일 {tail}"
        )

    # **원인 후보를 하나로 단정하지 않는다** (2026-08-14 F-3). 종전 문구는 "수집기를 먼저
    # 확인할 것"이라 단정했는데, 롤 당일엔 수집기가 멀쩡하고 화면이 만기된 월물을 보고
    # 있었다 — 운영자를 정확히 틀린 방향으로 보냈다. 심볼을 문장에 넣고 후보를 둘로 연다.
    return "alert", (
        f"🛑 {first_tick.strftime('%H:%M')}이 지났는데 `{symbol}`의 오늘({today.isoformat()}) "
        f"봉이 없다 — ① 이 종목이 오늘의 근월물이 맞는지(월물 롤) ② 수집기(l1.collector) "
        f"순으로 확인할 것. {tail}"
    )


# severity → 렌더러. `expected`는 정상이라 캡션이고, `alert`는 P0라 경고색이 아니라 에러색이다
# (종전엔 셋 다 `st.warning` 하나였다 — 2026-08-11 F-3).
_NOTICE_RENDERER = {"ok": st.caption, "expected": st.caption, "alert": st.error}


def render_market_view(
    source, *, symbol: str, horizon: str, bar_dir: Path, tick_size: float
) -> None:
    st.subheader("② Market View")
    is_replay = source.mode == DataSourceMode.REPLAY
    available = _available_dates(symbol, horizon, bar_dir)
    if not available:
        st.warning(f"{bar_dir}/{symbol}/{horizon}에 표시할 봉 데이터가 없음")
        return

    if is_replay:
        chosen = st.selectbox("날짜(REPLAY)", options=list(reversed(available)), key="replay_date")
    else:
        chosen = available[-1]
        severity, notice = _live_date_notice(
            chosen,
            now=now_kst(),
            calendar=_event_calendar_or_none(),
            symbol=symbol,
            horizon=horizon,
        )
        _NOTICE_RENDERER[severity](notice)

    bars, stale_reason = _load_bars_with_status(symbol, horizon, chosen, bar_dir)
    if stale_reason is not None:
        st.warning(stale_reason)  # 조용히 넘어가지 않는다(L18) — 화면이 최신이 아닐 수 있음
    if bars is None or bars.is_empty:
        st.warning("선택한 날짜의 봉 데이터가 비어 있음")
        return
    st.plotly_chart(_candlestick_figure(bars, tick_size), width="stretch")

    regime_snap = source.snapshot("RegimeState")
    if isinstance(regime_snap.message, RegimeState):
        st.caption(f"Regime: {regime_snap.message.regime.value}")


def render_position_risk_panel(source) -> None:
    st.subheader("③ Position & Risk")
    st.info(
        "broker.positions() 실시간 연동은 알려진 갭 — "
        "Command Center는 아직 브로커 계좌를 직접 조회하지 않는다"
    )
    st.markdown("**포트폴리오 Greeks**")
    st.caption("옵션 실행 경로가 없어(known gap) 실제 보유 옵션 Greeks 합산은 항상 비어 있음")


def _event_calendar_lines(today: date, calendar: EventCalendar | None) -> list[str]:
    """화면 ④의 이벤트 캘린더 D-day (2026-08-11 F-5).

    ## 없던 기능이 아니라 **안 붙인 기능이었다**

    `core/event_calendar.py`는 2026-07-27부터 있고 `ev_core`·`sidecar`·`session_guard`·
    `option_chain_poller`가 이미 정본으로 쓴다. 그런데 화면은 `알려진 갭 — EventCalendar
    연동 미배선`이라고만 적혀 있었다. 2026-08-11이 8/13 먼슬리 만기 D-2였고 그 사실이 그날
    `weekly_thu 미상장`의 **원인**인데, 화면은 두 사실 중 어느 쪽도 말하지 않았다 —
    기동 로그에만 있었고 로그는 아침에 한 번 흘러가면 끝이다.

    D-day는 **거래일 거리**로 센다(`trading_days_until`). 달력 날짜로 세면 금요일에 보는
    "D-3"이 실제로는 하루 뒤라 급한 정도가 거꾸로 읽힌다 — 등록부 재발 문구가 거래일
    거리를 쓰기로 한 것과 같은 근거(2026-08-10 B-3).

    달력이 없으면 **한 줄로 그 사실만** 말한다. 조용히 빈칸으로 두면 "오늘 아무 이벤트도
    없다"로 읽히는데, 그건 이 함수가 답할 수 없는 상태에서 답한 척하는 것이다.
    """
    if calendar is None:
        return ["이벤트 캘린더: 휴장일 달력을 못 읽어 판정 불가 — configs/krx_holidays.yaml 확인"]

    lines: list[str] = []
    try:
        monthly = calendar.next_monthly_expiry(today)
        d_day = calendar.trading_days_until(today, monthly)
        witching = " · 동시만기(쿼드러플 위칭)" if calendar.is_quadruple_witching(monthly) else ""
        when = "오늘" if d_day == 0 else f"D-{d_day}(거래일)"
        lines.append(f"먼슬리 만기: {monthly.isoformat()} — {when}{witching}")

        weekly = calendar.next_weekly_expiry(today)
        if weekly is not None:
            w_day = calendar.trading_days_until(today, weekly)
            w_when = "오늘" if w_day == 0 else f"D-{w_day}(거래일)"
            lines.append(f"위클리 만기: {weekly.isoformat()} — {w_when}")

        # 목위클리 미상장 구간은 **부재를 사고로 오인한 전례**가 있다(2026-08-07 오탐 22건).
        # 화면이 그 사실과 복귀 예정일을 같이 말하면 사람이 매일 다시 확인하지 않는다.
        if not calendar.thursday_weekly_listed(today):
            resumes = calendar.thursday_weekly_listing_resumes(today)
            resume_text = resumes.isoformat() if resumes is not None else "모름"
            lines.append(
                f"목위클리(weekly_thu): 오늘 미상장 — 먼슬리 만기 주라 KRX 미상장(정상) · "
                f"{resume_text} 재개 예정"
            )
    except ValueError as exc:
        # 휴장일 데이터가 없는 연도로 계산이 넘어간 경우 — 지금까지 만든 줄은 살리고 사유를 붙인다.
        lines.append(f"이벤트 캘린더: 일부 판정 불가 — {exc}")

    return lines


#: 자기평가 산출물이 놓이는 자리 — 쓰는 쪽(`ops/self_eval.py`)과 같은 규칙이다.
#: 절대경로를 적지 않는다(R4): 프로세스의 작업 디렉터리가 저장소 루트라는 것이 전제이고,
#: 그 전제는 `ops/status_board.DEFAULT_SNAPSHOT_PATH`가 이미 같은 꼴로 쓰고 있다.
_SELF_EVAL_DIR = Path("logs")

#: 화면에 낼 세 줄이 읽는 필드 — (라벨, 키). 옛 스키마엔 없는 키가 있을 수 있으므로
#: **있는 것만** 낸다(없는 칸을 0이나 빈칸으로 지어내지 않는다, L18).
_SELF_EVAL_FIELDS = (
    ("배선 단계", "wiring_stage"),
    ("수익률 표본", "n_return_samples"),
    ("손익 측정 가능", "pnl_measurable"),
)


class _SelfEvalCache:
    """`logs/self_eval_<날짜>.json` 한 파일의 파싱 결과 — 지문이 같으면 다시 안 읽는다.

    LIVE 화면은 5초마다 다시 그린다(`_LIVE_REFRESH_SECONDS`). 그 주기로 파일을 열면
    렌더 경로에 매번 I/O가 붙는데, 이 파일은 하루 한 번(15:34) 쓰인다 — 지문(수정시각·
    크기)이 같으면 읽을 것이 없다. `_BarFileCache`와 같은 판단이다.
    """

    def __init__(self) -> None:
        self._entry: tuple[str, tuple[int, int], list[str]] | None = None
        self._lock = threading.Lock()

    def lines(self, path: Path) -> list[str]:
        with self._lock:
            try:
                stat = path.stat()
            except OSError:
                self._entry = None
                return []
            signature = (stat.st_mtime_ns, stat.st_size)
            cached = self._entry
            if cached is not None and cached[0] == str(path) and cached[1] == signature:
                return list(cached[2])
            lines = _read_self_eval(path)
            self._entry = (str(path), signature, lines)
            return list(lines)


_SELF_EVAL_CACHE = _SelfEvalCache()


def _read_self_eval(path: Path) -> list[str]:
    """파일 하나를 세 줄로 — 못 읽으면 **사유를 한 줄로** 낸다 (2026-09-01 F-86)."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [f"Self-Eval: {path.name}을 못 읽었다 — JSON이 깨졌다({exc.__class__.__name__})"]
    except OSError as exc:  # 권한·잠금 등 — 조용히 빈칸으로 두지 않는다
        return [f"Self-Eval: {path.name}을 못 읽었다 — {exc.__class__.__name__}"]
    if not isinstance(payload, dict):
        return [f"Self-Eval: {path.name}의 형식이 예상과 다르다 — 최상위가 객체가 아니다"]

    parts = [f"{label} {payload[key]}" for label, key in _SELF_EVAL_FIELDS if key in payload]
    if not parts:
        return [f"Self-Eval: {path.name}에 읽을 칸이 없다 — 옛 스키마이거나 빈 산출물이다"]
    return [f"Self-Eval: {' · '.join(parts)}"]


def _self_eval_lines(today: date) -> list[str]:
    """화면 ④의 Self-Eval 미니보드 (2026-09-01 F-86 · 2026-08-11 F-5 잔여 줄).

    ## 코드는 맞고 **문장만 틀려 있었다**

    이 자리엔 `Self-Evaluation 미니보드: Phase 5 미구현 — 자리만`이 **조건 없이** 찍혔다.
    그런데 자기평가는 매 거래일 15:34에 실제로 돌아 `logs/self_eval_<날짜>.json`을 남긴다
    (2026-09-01에도 1.1KB가 쌓였다). 화면이 자기 시스템에 대해 사실이 아닌 말을 하고 있었고,
    그건 F-5가 **바로 위 줄**에서 고친 것과 똑같은 형태다(같은 함수, 같은 병).

    산출 전이면 그 사실을 말한다 — 빈칸으로 두면 "오늘 자기평가가 없다"로 읽히는데,
    그건 이 함수가 답할 수 없는 상태에서 답한 척하는 것이다(`_event_calendar_lines()`의
    달력 부재 처리와 같은 판단).
    """
    path = _SELF_EVAL_DIR / f"self_eval_{today.isoformat()}.json"
    lines = _SELF_EVAL_CACHE.lines(path)
    if lines:
        return lines
    return [f"Self-Eval: 오늘({today.isoformat()}) 자기평가 산출 전 — 15:34 예정"]


def render_bottom_zone(source) -> None:
    st.subheader("④ 실행 로그 · 이벤트 캘린더 · Self-Eval")
    fill_snap = source.snapshot("Fill")
    if isinstance(fill_snap.message, Fill):
        fill = fill_snap.message
        st.caption(f"최근 체결: {fill.symbol} {fill.qty}계약 @ {fill.price_ticks}")
    else:
        st.caption(_absence_reason(source, "Fill") or "exec.fill 데이터 없음")
    for line in _event_calendar_lines(now_kst().date(), _event_calendar_or_none()):
        st.caption(line)
    for line in _self_eval_lines(now_kst().date()):
        st.caption(line)


# ---------------------------------------------------------------- ⑤ 국면 실태

#: 국면 채점 산출물이 놓이는 자리 — 쓰는 쪽(`scripts/run_regime_direction_scorecard.py`)과
#: 같은 규칙. 상대경로인 이유는 `_SELF_EVAL_DIR` 주석과 같다.
_REGIME_BOARD_DIR = Path("logs")

#: 며칠 전 산출물까지 거슬러 볼 것인가. 이 채점은 **장후에** 돌므로 장중 화면이 보는 것은
#: 늘 어제 것이다 — 오늘 파일만 찾으면 거래일 내내 "미측정"이라 적힌다. 다만 얼마나 오래된
#: 것인지는 화면에 그대로 적는다(오래된 값을 최신인 척 보여주지 않는다, L18).
_REGIME_BOARD_LOOKBACK_DAYS = 7

#: 국면 이름 → 화면 표기. 영문 enum 값을 그대로 쓰면 옆의 한글과 눈이 갈린다.
_REGIME_LABELS = {
    "TREND_UP": "상승추세",
    "TREND_DOWN": "하락추세",
    "RANGE": "횡보",
    "HIGH_VOL": "고변동",
    "EVENT": "이벤트",
    "UNKNOWN": "미판정",
}


class _RegimeBoardCache:
    """`logs/regime_direction_<날짜>.json` 한 파일의 파싱 결과 — `_SelfEvalCache`와 같은 판단
    (LIVE는 5초마다 다시 그리는데 이 파일은 하루 한 번 쓰인다)."""

    def __init__(self) -> None:
        self._entry: tuple[str, tuple[int, int], list[str]] | None = None
        self._lock = threading.Lock()

    def lines(self, path: Path) -> list[str]:
        with self._lock:
            try:
                stat = path.stat()
            except OSError:
                self._entry = None
                return []
            signature = (stat.st_mtime_ns, stat.st_size)
            cached = self._entry
            if cached is not None and cached[0] == str(path) and cached[1] == signature:
                return list(cached[2])
            lines = _read_regime_board(path)
            self._entry = (str(path), signature, lines)
            return list(lines)


_REGIME_BOARD_CACHE = _RegimeBoardCache()


def _latest_regime_board_path(today: date) -> Path | None:
    for back in range(_REGIME_BOARD_LOOKBACK_DAYS + 1):
        day = today - timedelta(days=back)
        path = _REGIME_BOARD_DIR / f"regime_direction_{day.strftime('%Y%m%d')}.json"
        if path.exists():
            return path
    return None


def _regime_board_row(name: str, direction: dict, reachability: dict) -> str:
    """국면 한 줄 — **게이트 도달성과 방향 적중률을 한 줄에 붙인다.**

    둘을 따로 보여주면 사람이 둘을 이어 읽지 않는다. 2026-09-02에 드러난 사실이 정확히 그
    이음매에 있다: 고변동 국면은 방향 적중률이 26%인데 손실이 안 났고, 그 이유는 판단력이
    아니라 **그 국면의 실효 천장(0.188)이 게이트(0.20)에 산술적으로 못 닿기 때문**이었다.
    한 줄에 붙여야 "지금 안전한 이유가 우연"이라는 것이 보인다.
    """
    label = _REGIME_LABELS.get(name, name)
    parts = [label]

    gate = reachability.get(name) if isinstance(reachability, dict) else None
    if isinstance(gate, dict):
        ceiling, threshold = gate.get("ceiling_solo"), gate.get("score_gate")
        if gate.get("closed"):
            parts.append(f"게이트 **닫힘**(천장 {ceiling:.3f} < {threshold:g})")
        elif gate.get("reachable_solo") is False:
            parts.append(f"게이트 여유부족(천장 {ceiling:.3f} / {threshold:g})")
        else:
            parts.append(f"게이트 열림(천장 {ceiling:.3f} / {threshold:g})")

    if isinstance(direction, dict) and direction.get("n"):
        n, hit = direction["n"], direction.get("hit_rate", 0.0)
        net = direction.get("net_ticks", 0.0)
        parts.append(f"방향 {hit:.0%} ({direction.get('n_hit', 0)}/{n})")
        parts.append(f"반사실 {net:+,.0f}틱")
        parts.append(f"통과 {direction.get('n_gate_passed', 0)}건")
        if direction.get("flagged"):
            parts.append("⚠ 동전과 유의하게 다름")
        elif direction.get("status") != "측정":
            parts.append("표본 부족")
    else:
        parts.append("사이클 없음")
    return " · ".join(parts)


def _read_regime_board(path: Path) -> list[str]:
    """파일 하나를 사람이 읽는 줄들로 — 못 읽으면 **사유를 한 줄로**(`_read_self_eval`과 같다)."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [f"국면 실태: {path.name}을 못 읽었다 — JSON이 깨졌다({exc.__class__.__name__})"]
    except OSError as exc:
        return [f"국면 실태: {path.name}을 못 읽었다 — {exc.__class__.__name__}"]
    if not isinstance(payload, dict):
        return [f"국면 실태: {path.name}의 형식이 예상과 다르다 — 최상위가 객체가 아니다"]

    direction = payload.get("direction") or {}
    reachability = payload.get("reachability") or {}
    regimes = direction.get("regimes") or {}
    if not isinstance(regimes, dict):
        return [f"국면 실태: {path.name}에 읽을 칸이 없다 — 옛 스키마이거나 빈 산출물이다"]

    days = direction.get("trading_days", 0)
    need = direction.get("min_trading_days", 20)
    header = (
        f"국면 실태 ({payload.get('date', '?')} 기준 · {days}거래일 창 · "
        f"판단 {direction.get('n_scored', 0)}건)"
    )
    if not direction.get("meets_shadow_window"):
        header += f" — 섀도 계측 {need}거래일까지 {max(0, need - days)}일 남음(승격 판단 전)"
    lines = [header]

    # **천장은 닫혔는데 적중률이 나쁜 국면부터** — 순서가 곧 읽는 순서다.
    order = sorted(
        set(regimes) | {k for k in reachability if isinstance(reachability, dict)},
        key=lambda name: regimes.get(name, {}).get("net_ticks", 0.0),
    )
    for name in order:
        lines.append(_regime_board_row(name, regimes.get(name, {}), reachability))

    sources = direction.get("regime_sources") or {}
    if isinstance(sources, dict) and sources.get("joined"):
        lines.append(
            f"※ 국면 {sources['joined']}건은 옛 로그에서 이어 붙인 값이다"
            f"(판단이 스스로 말한 것 {sources.get('self', 0)}건) — 2026-09-02 이전 로그"
        )
    if not reachability:
        lines.append("※ 실효 천장 미계산 — `--no-reachability`로 돌렸거나 연속 시계열이 없다")
    return lines


def _regime_board_lines(today: date) -> list[str]:
    """화면 ⑤. 산출 전이면 **그 사실과 돌리는 법**을 말한다(빈칸으로 두지 않는다)."""
    path = _latest_regime_board_path(today)
    if path is None:
        return [
            "국면 실태: 미측정 — `python scripts/run_regime_direction_scorecard.py`를 "
            f"장후에 돌리면 최근 {_REGIME_BOARD_LOOKBACK_DAYS}일 안의 산출물을 여기 띄운다"
        ]
    return _REGIME_BOARD_CACHE.lines(path)


def render_regime_board() -> None:
    st.subheader("⑤ 국면 실태 — 게이트 도달성 · 방향 적중률")
    lines = _regime_board_lines(now_kst().date())
    st.caption(lines[0])
    for line in lines[1:]:
        if "⚠" in line:
            st.warning(line)
        else:
            st.caption(line)


# ---------------------------------------------------------------- 진입점

# LIVE 모드 자동 새로고침 주기(초) — 2026-07-29 추가. 이전엔 자동 재실행 트리거가 전혀
# 없어(`st.rerun()`/autorefresh 전무, 실측 확인) 봉·배지가 사람이 위젯을 조작하거나
# 브라우저를 새로고침해야만 갱신됐다. `_STALE_AFTER`의 가장 빡빡한 임계값(FuturesView 10초)
# 보다 넉넉히 짧게 잡아, 두 번의 자동 새로고침 사이에 배지가 헛되이 STALE로 안 보이게 한다.
_LIVE_REFRESH_SECONDS = 5


def _snapshot_freshness_fields(
    source, symbol: str, horizon: str, bar_dir: Path, *, today: date
) -> dict:
    """첫 렌더가 실제로 그리는 것들의 신선도 — 로그용 구조화 필드 (2026-08-18 G-0818I-4).

    배지 계산은 렌더가 이미 쓰는 `source.snapshot()`을 그대로 재사용한다 — 여기서 다른
    판정을 만들면 로그와 화면이 다른 말을 하는 표면이 하나 더 생긴다(F-0818P-5의 병).
    차트 날짜는 **가장 최근 가용일**(화면 기본 선택값)이다 — 사용자가 과거 날짜를 골라
    보는 것은 이 축의 관심사가 아니다. `threshold_basis`는 P-9의 질문("거래일 기준인가
    경과 시간 기준인가")에 대한 명시적 답이다: 배지 임계는 전부 경과 초 기준이다.
    """
    topics = {}
    for key in ("FuturesView", "DecisionIntent", "RegimeState", "CircuitBreakerStatus"):
        snap = source.snapshot(key)
        topics[key] = {
            "badge": snap.badge.value,
            "age_seconds": None if snap.age_seconds is None else round(snap.age_seconds, 1),
            "cadence_seconds": snap.cadence_seconds,
        }
    dates = _available_dates(symbol, horizon, bar_dir)
    return {
        "mode": source.mode.value,
        "threshold_basis": "elapsed_seconds",
        "topics": topics,
        "chart_date": dates[-1].isoformat() if dates else None,
        # 달력일 기준 — "3일 묵은 값"(P-9)이 정확히 이 필드다. 연휴 뒤 첫 기동이면 3이 정상
        # 이고, 화면은 그 사실을 STALE/NO_DATA 배지로 함께 말해야 한다(위 topics가 그 증거).
        "chart_lag_calendar_days": (today - dates[-1]).days if dates else None,
        "today": today.isoformat(),
    }


def _log_snapshot_freshness_once(source, symbol: str, horizon: str, bar_dir: Path) -> None:
    """세션당 1회, 첫 렌더가 그린 것을 로그로 남긴다 (2026-08-18 G-0818I-4 · NEXT_TODO P-9).

    ## 왜 「기동 직후」가 아니라 「첫 렌더」인가

    Streamlit 스크립트는 브라우저가 붙어야 돈다 — 서버 기동만으로는 이 함수까지 오지
    않는다. 따라서 이 로그의 **부재**는 "UI가 죽었다"가 아니라 **"떠 있었지만 아무도 안
    봤다"**다(죽음은 상태판 프로브 `command_center_ui`가 따로 말한다). 2026-08-18까지 세
    국면 연속 P-9가 판정 불가였던 이유가 정확히 이 로그의 부재였다 — 화면이 무엇을
    그렸는지가 어느 파일에도 없어, 다음 자연 관측 기회(추석, 5주 뒤)를 기다려야 했다.

    ## 「한 번만」 가드를 **성공 뒤로** 옮겼다 (2026-08-21 F-1 ③)

    종전엔 가드를 세운 **다음** 로그를 시도했다. 2026-08-21에 그 로그가
    `UnicodeEncodeError: 'cp949'`로 통째로 유실됐는데, 가드는 이미 소모된 뒤라
    **그 세션에서 다시 시도하지 않았다** — 남은 것이 아무것도 없었다. 성공/실패 어느
    쪽이든 한 번씩만 남기되, **성공 전에 소모되지는 않게** 한다.

    가드는 여전히 세션별이다(창을 새로 열면 리셋). 그건 의도다 — 새 창은 새 첫 렌더고,
    그 렌더가 무엇을 그렸는지는 별개의 사실이다.
    """
    if st.session_state.get("snapshot_freshness_logged"):
        return
    try:
        fields = _snapshot_freshness_fields(
            source, symbol, horizon, bar_dir, today=now_kst().date()
        )
        badges = " · ".join(f"{k} {v['badge']}" for k, v in fields["topics"].items())
        mlog.log(
            "UISnapshotFreshness",
            f"첫 렌더({fields['mode']}) — {badges} · 차트 {fields['chart_date']}"
            f"(지연 {fields['chart_lag_calendar_days']}일)",
            **fields,
        )
        st.session_state["snapshot_freshness_logged"] = True
    except Exception as exc:  # noqa: BLE001 — 관측 도구가 화면을 죽이면 본말전도(R10은 지킨다)
        mlog.log(
            "UISnapshotFreshnessFailed",
            f"신선도 로그 실패 — 화면은 계속 그린다: {exc}",
            error=str(exc),
        )
        # 실패도 한 번만 알린다 — 스트림릿 5초 재실행마다 같은 실패가 쌓이면 그것이
        # 새 잡음이 된다. 소모 시점만 옮긴 것이지 재시도를 무한히 여는 것이 아니다.
        st.session_state["snapshot_freshness_logged"] = True


def _render_dashboard_body(
    source,
    *,
    symbol: str,
    horizon: str,
    bar_dir: Path,
    tick_size: float,
    redis_url: str | None = None,
) -> None:
    """`main()`에서 분리 — LIVE 모드에서만 `st.fragment(run_every=...)`로 감싸 이 부분만
    주기적으로 다시 그린다(전체 페이지 rerun은 사이드바 위젯 상태를 흔들 필요가 없어 과함)."""
    _log_snapshot_freshness_once(source, symbol, horizon, bar_dir)
    render_top_bar(source, symbol, redis_url)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col1:
        render_ai_decision_panel(source)
    with col2:
        render_market_view(
            source,
            symbol=symbol,
            horizon=horizon,
            bar_dir=bar_dir,
            tick_size=tick_size,
        )
    with col3:
        render_position_risk_panel(source)
    st.divider()
    render_bottom_zone(source)
    st.divider()
    render_regime_board()


def main() -> None:
    st.set_page_config(page_title="MESSIAH Command Center", layout="wide")
    # 첫 렌더보다 **먼저** 로깅을 켠다 (2026-08-20 F-B) — `_log_first_render_freshness()`가
    # 그 뒤에 불리므로, 여기가 아니면 정작 첫 렌더 줄이 또 허공으로 간다.
    _logging_ready()

    st.sidebar.header("데이터 소스")
    # 기본값 LIVE(2026-07-29 사용자 요청 — 모듈 docstring "LIVE/STALE/REPLAY는 항상
    # 명시적이다" 참고, REPLAY로 명시 전환도 여전히 가능).
    mode_label = st.sidebar.radio("모드", ["REPLAY", "LIVE"], index=1)
    default_symbol, symbol_origin = _resolve_default_symbol()
    symbol = st.sidebar.text_input("종목", default_symbol)
    st.sidebar.caption(f"기본값 출처: {symbol_origin}")
    horizon = st.sidebar.selectbox("차트 Horizon", ["1m", "3m", "5m", "10m", "15m", "30m"], index=2)
    tick_size = st.sidebar.number_input("틱 크기", value=DEFAULT_TICK_SIZE, format="%.4f")

    redis_url: str | None = None
    if mode_label == "LIVE":
        redis_url = st.sidebar.text_input("Redis URL", _default_redis_url())
        cache = _get_live_cache(redis_url, symbol)
        source = LiveDataSource(cache, stale_after_seconds=_STALE_AFTER)
        run_every: int | None = _LIVE_REFRESH_SECONDS
    else:
        # REPLAY는 선택한 날짜가 바뀔 때만 다시 그리면 된다 — 데이터 자체가 시간이 지난다고
        # 저절로 안 바뀌므로 타이머로 다시 그려봐야 낭비(불필요한 Parquet 재읽기)만 늘어난다.
        source = ReplayDataSource()
        run_every = None

    body = st.fragment(run_every=run_every)(_render_dashboard_body)
    body(
        source,
        symbol=symbol,
        horizon=horizon,
        bar_dir=DEFAULT_BAR_DIR,
        tick_size=tick_size,
        redis_url=redis_url,
    )


if __name__ == "__main__":
    main()
