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
import glob
import threading
from datetime import date
from pathlib import Path

import plotly.graph_objects as go
import polars as pl
import streamlit as st

from messiah.core.bus import MessageBus
from messiah.core.config import load_instance
from messiah.core.messages import (
    BusMessage,
    CircuitBreakerStatus,
    DecisionIntent,
    Fill,
    FuturesView,
    Health,
    OptionsView,
    RegimeState,
)
from messiah.core.timeutil import now_utc
from messiah.ui.data_source import (
    DataSourceMode,
    FreshnessBadge,
    LiveDataSource,
    ReplayDataSource,
)
from messiah.ui.state_cache import CacheSubscriber, StateCache

DEFAULT_BAR_DIR = Path("data") / "bars"
DEFAULT_SYMBOL = "A05608"
DEFAULT_TICK_SIZE = 0.02  # 미니선물(A05608) 2026-07-22 실측값(core/config.py 동일 placeholder)

_STALE_AFTER: dict[str, float] = {
    "FuturesView": 10.0,
    "RegimeState": 15.0,
    "OptionsView": 15.0,
    # CircuitBreakerStatus는 워치독이 30초 격자로 heartbeat한다(`strategy/pipeline.py`
    # `watch_circuit_breaker_forever`) — 그 주기보다 여유 있게 잡아 두 heartbeat 사이에
    # 배지가 헛되이 STALE로 안 보이게 한다(`_LIVE_REFRESH_SECONDS` 선택과 같은 논리).
    "CircuitBreakerStatus": 40.0,
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
}
_CB_PHASE_LABEL: dict[str, str] = {
    "normal": "정상",
    "warning": "주의(데이터 지연)",
    "suspected": "CB 의심",
    "confirmed": "CB 정지 추정",
}


def _render_circuit_breaker_badge(source) -> None:
    """Top Bar용 CB 상태 배지 — `strategy/pipeline.py`가 `sys.circuit_breaker`에 발행하는
    `CircuitBreakerStatus` heartbeat를 그대로 보여준다. `circuit_breaker_monitor`를 안 쓰는
    구성(스모크/재생 등)에서는 이 토픽 자체가 발행되지 않아 항상 NO_DATA — 그 경우 "미사용"
    문구로 명시해 "정상"과 혼동되지 않게 한다(마흐디 L18과 같은 원칙 — 값이 없는 것과 정상인
    것을 구분)."""
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

    if status.reentry_cooldown_until is not None:
        remaining = (status.reentry_cooldown_until - now_utc()).total_seconds()
        if remaining > 0:
            st.caption(f"재진입 관망 {remaining / 60:.1f}분 남음")


# ---------------------------------------------------------------- LIVE 배선 (백그라운드 스레드)


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
        pubsub_subscriber = CacheSubscriber(bus, patterns, cache)
        await asyncio.gather(
            pubsub_subscriber.run_forever(),
            _poll_streams_forever(bus, cache),
        )

    try:
        asyncio.run(_main())
    except Exception as exc:  # noqa: BLE001 — UI 스레드 예외가 프로세스를 죽이면 안 됨
        cache.update("LiveConnectionError", _error_message(str(exc)))


async def _poll_streams_forever(bus: MessageBus, cache: StateCache, *, poll_ms: int = 1000) -> None:
    last_ids = {"decision.intent": "$", "exec.fill": "$"}
    while True:
        for topic in list(last_ids):
            entries = await bus.read_stream(topic, last_id=last_ids[topic], block_ms=poll_ms)
            for entry_id, message in entries:
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


# ---------------------------------------------------------------- Parquet 캔들 (LIVE/REPLAY 공용)


def _available_dates(symbol: str, horizon: str, bar_dir: Path) -> list[date]:
    pattern = str(bar_dir / symbol / horizon / "*.parquet")
    dates = []
    for path in sorted(glob.glob(pattern)):
        stem = Path(path).stem
        try:
            dates.append(date.fromisoformat(stem))
        except ValueError:
            continue
    return dates


def _load_bars(symbol: str, horizon: str, day: date, bar_dir: Path) -> pl.DataFrame | None:
    """`bar_open_kst`는 만들어질 때(`data/normalizer.py`의 `_combine_kst()`) 진짜 KST
    (+09:00)이지만, Polars가 tz-aware datetime을 Parquet에 쓸 때 항상 `time_zone='UTC'`로
    정규화해서 저장한다(그래서 이 값을 그대로 읽으면 물리적으로는 UTC 벽시계 값 — 이름과
    실제 표시가 어긋난다, 2026-07-29 Command Center 스크린샷 점검 중 실측 발견: 09:00 KST
    개장 봉이 화면엔 00:00으로 찍힘). `dt.convert_time_zone("Asia/Seoul")`로 KST 벽시계로
    되돌린다 — `pyproject.toml`이 이미 이 Windows tzdata 왕복 문제 때문에
    `tzdata>=2026.1; sys_platform == 'win32'`를 명시적으로 넣어뒀으므로(2026-07-22) 새 의존성
    추가가 아니다."""
    path = bar_dir / symbol / horizon / f"{day.isoformat()}.parquet"
    if not path.exists():
        return None
    bars = pl.read_parquet(path)
    return bars.with_columns(pl.col("bar_open_kst").dt.convert_time_zone("Asia/Seoul"))


def _candlestick_figure(bars: pl.DataFrame, tick_size: float) -> go.Figure:
    fig = go.Figure(
        data=[
            go.Candlestick(
                x=bars["bar_open_kst"],
                open=bars["o_ticks"] * tick_size,
                high=bars["h_ticks"] * tick_size,
                low=bars["l_ticks"] * tick_size,
                close=bars["c_ticks"] * tick_size,
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


def _badge_caption(label: str, snapshot) -> None:
    color = _BADGE_COLOR[snapshot.badge]
    st.markdown(
        f"**{label}** &nbsp; " f"<span style='color:{color}'>● {snapshot.badge.value}</span>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------- 존 렌더링


def render_top_bar(source, symbol: str) -> None:
    st.markdown(f"### MESSIAH Command Center — `{symbol}`")
    cols = st.columns([2, 2, 2, 2, 1])
    with cols[0]:
        st.metric("모드", source.mode.value)
    with cols[1]:
        futures_snap = source.snapshot("FuturesView")
        _badge_caption("intel.futures", futures_snap)
    with cols[2]:
        decision_snap = source.snapshot("DecisionIntent")
        _badge_caption("decision.intent", decision_snap)
    with cols[3]:
        _render_circuit_breaker_badge(source)
    with cols[4]:
        confirmed = st.button("🛑 KILL SWITCH", type="primary")
        if confirmed:
            st.session_state["kill_confirm_pending"] = True
        if st.session_state.get("kill_confirm_pending"):
            st.warning("정말 전량 청산·주문 차단하시겠습니까? (2단 확인)")
            if st.button("확인 — 즉시 발동"):
                st.session_state["kill_confirm_pending"] = False
                st.session_state["kill_requested"] = True
                st.error(
                    "Kill Switch 발동 요청 — LIVE 모드에서 sys.kill 발행 배선은 알려진 갭"
                    "(모듈 docstring, 아직 실제 KillSwitch 프로세스에 연결되지 않음)"
                )

    # `_run_live_subscriber`가 예외를 삼키고 "LiveConnectionError" 키에 Health(CRITICAL)를
    # 남긴다(모듈 docstring) — 지금까지는 그 키를 읽는 render_* 함수가 하나도 없어서, 연결
    # 실패가 모든 배지를 그냥 조용히 영원한 NO_DATA로만 보여줬다(2026-07-29 실측 발견: 잘못된
    # 기본 Redis URL로 "연결은 성공했지만 엉뚱한 서버"인 경우도 똑같이 조용했다). REPLAY 모드의
    # `snapshot()`은 이 키를 절대 채우지 않으므로 무조건 호출해도 안전하다.
    conn_error = source.snapshot("LiveConnectionError")
    if isinstance(conn_error.message, Health):
        st.error(f"LIVE 연결 실패: {conn_error.message.detail}")

    st.divider()


def render_ai_decision_panel(source) -> None:
    st.subheader("① AI Decision")
    intent_snap = source.snapshot("DecisionIntent")
    if isinstance(intent_snap.message, DecisionIntent):
        intent = intent_snap.message
        st.metric("의도", intent.side.value, f"확신도 {intent.confidence:.0%}")
        st.caption(f"불확실성 {intent.uncertainty:.2f}")
        st.text(intent.rationale)
    else:
        st.info("decision.intent 데이터 없음")

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
        st.info("intel.options 데이터 없음")


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
        st.caption(f"오늘({chosen.isoformat()}) 봉 — LIVE는 항상 최신 날짜")

    bars = _load_bars(symbol, horizon, chosen, bar_dir)
    if bars is None or bars.is_empty():
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


def render_bottom_zone(source) -> None:
    st.subheader("④ 실행 로그 · 이벤트 캘린더 · Self-Eval")
    fill_snap = source.snapshot("Fill")
    if isinstance(fill_snap.message, Fill):
        fill = fill_snap.message
        st.caption(f"최근 체결: {fill.symbol} {fill.qty}계약 @ {fill.price_ticks}")
    else:
        st.caption("exec.fill 데이터 없음")
    st.caption("이벤트 캘린더 D-day: 알려진 갭 — EventCalendar 연동 미배선")
    st.caption("Self-Evaluation 미니보드: Phase 5 미구현 — 자리만")


# ---------------------------------------------------------------- 진입점

# LIVE 모드 자동 새로고침 주기(초) — 2026-07-29 추가. 이전엔 자동 재실행 트리거가 전혀
# 없어(`st.rerun()`/autorefresh 전무, 실측 확인) 봉·배지가 사람이 위젯을 조작하거나
# 브라우저를 새로고침해야만 갱신됐다. `_STALE_AFTER`의 가장 빡빡한 임계값(FuturesView 10초)
# 보다 넉넉히 짧게 잡아, 두 번의 자동 새로고침 사이에 배지가 헛되이 STALE로 안 보이게 한다.
_LIVE_REFRESH_SECONDS = 5


def _render_dashboard_body(
    source, *, symbol: str, horizon: str, bar_dir: Path, tick_size: float
) -> None:
    """`main()`에서 분리 — LIVE 모드에서만 `st.fragment(run_every=...)`로 감싸 이 부분만
    주기적으로 다시 그린다(전체 페이지 rerun은 사이드바 위젯 상태를 흔들 필요가 없어 과함)."""
    render_top_bar(source, symbol)
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


def main() -> None:
    st.set_page_config(page_title="MESSIAH Command Center", layout="wide")

    st.sidebar.header("데이터 소스")
    # 기본값 LIVE(2026-07-29 사용자 요청 — 모듈 docstring "LIVE/STALE/REPLAY는 항상
    # 명시적이다" 참고, REPLAY로 명시 전환도 여전히 가능).
    mode_label = st.sidebar.radio("모드", ["REPLAY", "LIVE"], index=1)
    symbol = st.sidebar.text_input("종목", DEFAULT_SYMBOL)
    horizon = st.sidebar.selectbox("차트 Horizon", ["1m", "3m", "5m", "10m", "15m", "30m"], index=2)
    tick_size = st.sidebar.number_input("틱 크기", value=DEFAULT_TICK_SIZE, format="%.4f")

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
    body(source, symbol=symbol, horizon=horizon, bar_dir=DEFAULT_BAR_DIR, tick_size=tick_size)


if __name__ == "__main__":
    main()
