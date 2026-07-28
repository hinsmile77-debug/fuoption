"""Command Center 데이터 소스 — LIVE/REPLAY + 신선도 배지 (신규, Ver 2.0 §9 W32~34)."""

from __future__ import annotations

from datetime import timedelta

from messiah.core.messages import DecisionIntent, Side
from messiah.core.timeutil import now_utc
from messiah.ui.data_source import (
    DataSourceMode,
    FreshnessBadge,
    LiveDataSource,
    ReplayDataSource,
    compute_badge,
)
from messiah.ui.state_cache import StateCache

_SYMBOL = "TEST"


def _intent() -> DecisionIntent:
    return DecisionIntent(symbol=_SYMBOL, side=Side.LONG, confidence=0.7, uncertainty=0.1)


# ---------------------------------------------------------------- compute_badge


def test_compute_badge_replay_mode_always_replay_regardless_of_age():
    replay = DataSourceMode.REPLAY
    assert compute_badge(replay, None, stale_after_seconds=5.0) == FreshnessBadge.REPLAY
    assert compute_badge(replay, 999.0, stale_after_seconds=5.0) == FreshnessBadge.REPLAY


def test_compute_badge_live_mode_no_data_yet():
    badge = compute_badge(DataSourceMode.LIVE, None, stale_after_seconds=5.0)
    assert badge == FreshnessBadge.NO_DATA


def test_compute_badge_live_mode_fresh_vs_stale():
    live = DataSourceMode.LIVE
    assert compute_badge(live, 3.0, stale_after_seconds=5.0) == FreshnessBadge.LIVE
    assert compute_badge(live, 5.0, stale_after_seconds=5.0) == FreshnessBadge.LIVE  # 경계 포함
    assert compute_badge(live, 5.1, stale_after_seconds=5.0) == FreshnessBadge.STALE


# ---------------------------------------------------------------- LiveDataSource


def test_live_data_source_no_data_before_any_cache_update():
    source = LiveDataSource(StateCache())
    snap = source.snapshot("DecisionIntent")
    assert snap.message is None
    assert snap.badge == FreshnessBadge.NO_DATA
    assert snap.age_seconds is None


def test_live_data_source_fresh_after_update():
    cache = StateCache()
    cache.update("DecisionIntent", _intent())
    source = LiveDataSource(cache, default_stale_after_seconds=30.0)
    snap = source.snapshot("DecisionIntent")
    assert snap.message is not None
    assert snap.badge == FreshnessBadge.LIVE


def test_live_data_source_per_key_stale_threshold_overrides_default():
    cache = StateCache()
    cache.update("intel.futures", _intent())
    # 캐시 갱신 시각을 인위적으로 과거로 되돌릴 수 없으니 임계값을 매우 짧게 잡아 STALE 유도.
    source = LiveDataSource(
        cache, stale_after_seconds={"intel.futures": -1.0}, default_stale_after_seconds=30.0
    )
    snap = source.snapshot("intel.futures")
    assert snap.badge == FreshnessBadge.STALE

    other = LiveDataSource(cache, default_stale_after_seconds=30.0)  # 기본 임계(30초) 적용
    assert other.snapshot("intel.futures").badge == FreshnessBadge.LIVE


def test_live_data_source_never_returns_replay_badge():
    cache = StateCache()
    cache.update("DecisionIntent", _intent())
    source = LiveDataSource(cache)
    assert source.snapshot("DecisionIntent").badge != FreshnessBadge.REPLAY
    assert source.mode == DataSourceMode.LIVE


# ---------------------------------------------------------------- ReplayDataSource


def test_replay_data_source_always_replay_badge_when_data_present():
    source = ReplayDataSource({"DecisionIntent": _intent()})
    snap = source.snapshot("DecisionIntent")
    assert snap.badge == FreshnessBadge.REPLAY
    assert snap.age_seconds is None
    assert source.mode == DataSourceMode.REPLAY


def test_replay_data_source_no_data_when_key_missing():
    source = ReplayDataSource()
    snap = source.snapshot("DecisionIntent")
    assert snap.message is None
    assert snap.badge == FreshnessBadge.NO_DATA


def test_replay_data_source_set_adds_snapshot():
    source = ReplayDataSource()
    source.set("DecisionIntent", _intent())
    assert source.snapshot("DecisionIntent").badge == FreshnessBadge.REPLAY


def test_replay_never_reports_live_badge_even_with_recent_synthetic_timestamp():
    # L18 핵심 방어: REPLAY 데이터의 내부 타임스탬프가 아무리 "방금"처럼 보여도(메시지 자체의
    # ts_utc는 now_utc() 기본값이라 항상 최신) 배지는 절대 LIVE가 아니다 — 모드가 결정한다.
    msg = _intent()
    assert (now_utc() - msg.ts_utc) < timedelta(seconds=1)
    source = ReplayDataSource({"DecisionIntent": msg})
    assert source.snapshot("DecisionIntent").badge == FreshnessBadge.REPLAY
