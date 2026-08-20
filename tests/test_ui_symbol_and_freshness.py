"""화면이 어제 계약을 오늘이라 불렀다 — 2026-08-14 F-3 · F-4.

첫 월물 롤에서 화면 상단은 `A05608`, 수집·매매는 `A05609`였다. 화면은 만기된 월물의
**어제 차트**를 그리면서 붉은 경보로 *"봉 적재 정지 의심, 수집기(l1.collector)를 먼저
확인할 것"* 을 띄웠다 — 같은 시각 수집기는 `age_seconds=0.4`로 건강했고 `A05609` 봉을
10:56:59까지 적재하고 있었다. **화면이 운영자를 정확히 틀린 방향으로 보냈다.**

그리고 상단 `intel.futures` 배지는 종일 앰버였다. 임계 10초 / 실제 발행 주기 1800초
(live 번들이 `30m` 한 종)라 **거래일의 99.4%가 STALE**이었다.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from messiah.core.messages import FuturesView, Regime
from messiah.core.timeutil import KST
from messiah.ui.app import _live_date_notice, _resolve_default_symbol
from messiah.ui.data_source import DataSourceMode, compute_badge, derived_stale_after

_ROLL_DAY = date(2026, 8, 14)


# ------------------------------------------------------------------ F-3: 심볼


def test_default_symbol_is_read_from_the_status_board(tmp_path: Path) -> None:
    """**해석이 아니라 조회다** — 해석 경로를 하나 더 만들면 갈릴 자리도 하나 더 생긴다."""
    snap = tmp_path / "status_snapshot.json"
    snap.write_text(json.dumps({"trading_symbol": "A05609"}), encoding="utf-8")

    symbol, origin = _resolve_default_symbol(snap, today=_ROLL_DAY)

    assert symbol == "A05609"
    assert "상태판" in origin


def test_falls_back_to_the_expiry_rule_when_there_is_no_snapshot(tmp_path: Path) -> None:
    """프로세스 미기동·장후에는 스냅샷이 없다 — 그때도 롤 당일 값이 나와야 한다."""
    symbol, origin = _resolve_default_symbol(tmp_path / "absent.json", today=_ROLL_DAY)
    assert symbol == "A05609"
    assert "만기 규칙" in origin


def test_a_broken_snapshot_does_not_kill_the_screen(tmp_path: Path) -> None:
    snap = tmp_path / "status_snapshot.json"
    snap.write_text("{ not json", encoding="utf-8")
    symbol, _origin = _resolve_default_symbol(snap, today=_ROLL_DAY)
    assert symbol == "A05609"  # 깨진 파일은 없는 파일과 같게 다룬다


def test_snapshot_without_the_field_falls_through(tmp_path: Path) -> None:
    """옛 스냅샷에는 `trading_symbol`이 없다 — 그 경우도 조용히 계산으로 넘어간다."""
    snap = tmp_path / "status_snapshot.json"
    snap.write_text(json.dumps({"components": {}}), encoding="utf-8")
    symbol, origin = _resolve_default_symbol(snap, today=_ROLL_DAY)
    assert symbol == "A05609"
    assert "만기 규칙" in origin


def test_the_alarm_names_the_symbol_and_offers_two_causes() -> None:
    """원인을 하나로 단정하면 롤 당일처럼 틀린 방향으로 보낸다."""
    severity, text = _live_date_notice(
        date(2026, 8, 13),
        now=datetime(2026, 8, 14, 10, 51, tzinfo=KST),
        calendar=None,
        symbol="A05608",
    )
    assert severity == "alert"
    assert "A05608" in text  # 어느 종목 이야기인지 문장이 말한다
    assert "월물 롤" in text
    assert text.index("월물 롤") < text.index("수집기")  # 롤을 먼저 의심하게 한다


# ------------------------------------------------------------------ F-4: 신선도


def _view(horizon_seconds: int | None) -> FuturesView:
    ts = datetime(2026, 8, 14, 10, 30, tzinfo=KST)
    return FuturesView(
        symbol="A05609",
        ts_utc=ts,
        score=0.0,
        agg_p_up=0.0,
        agg_p_down=0.0,
        uncertainty=1.0,
        dispersion=0.0,
        regime=Regime.UNKNOWN,
        n_experts=0,
        model_versions=[],
        top_features=[],
        valid_until=None if horizon_seconds is None else ts + timedelta(seconds=horizon_seconds),
    )


def test_threshold_is_derived_from_the_message_own_validity() -> None:
    """30분 주기면 임계는 45분이다 — 10초 상수로는 거래일의 99.4%가 STALE이었다."""
    threshold, cadence = derived_stale_after(_view(1800), fallback=10.0)
    assert cadence == 1800
    assert threshold == 2700  # 1800 × 1.5


def test_one_missed_publication_is_still_caught() -> None:
    """1.5배인 이유 — 1회 결손(2주기)은 반드시 걸리고 정상 간격은 안 걸린다."""
    threshold, _ = derived_stale_after(_view(1800), fallback=10.0)
    normal = compute_badge(DataSourceMode.LIVE, 1750, stale_after_seconds=threshold)
    missed = compute_badge(DataSourceMode.LIVE, 3600, stale_after_seconds=threshold)
    assert normal.value == "LIVE"
    assert missed.value == "STALE"


def test_without_validity_the_floor_is_kept() -> None:
    """`valid_until`이 없으면(기여 0명) 추측해서 늘리지 않는다 — 진짜 정지를 늦게 잡는다."""
    threshold, cadence = derived_stale_after(_view(None), fallback=10.0)
    assert cadence is None
    assert threshold == 10.0


def test_a_faster_horizon_never_loosens_below_the_floor() -> None:
    """1m 구동이면 유도값 90초가 하한 10초보다 크다 — max()가 양방향으로 안전하다."""
    threshold, cadence = derived_stale_after(_view(60), fallback=10.0)
    assert cadence == 60
    assert threshold == 90.0


# ------------------------------------------------------ G-0818I-4: 첫 렌더 신선도 로그
#
# 2026-08-18까지 세 국면 연속 NEXT_TODO P-9("연휴 뒤 첫 기동에서 3일 묵은 값을 「지금」으로
# 그리는가")가 판정 불가였다 — 화면이 무엇을 그렸는지가 어느 파일에도 없었다. 다음 자연
# 관측 기회는 추석(5주 뒤)이었고, 이 로그가 그 대기를 재생 1회로 바꾼다.


class _FakeSnapshot:
    def __init__(self, badge, age, cadence=None):
        from messiah.ui.data_source import FreshnessBadge

        self.badge = FreshnessBadge(badge)
        self.age_seconds = age
        self.cadence_seconds = cadence


class _FakeSource:
    mode = DataSourceMode.LIVE

    def __init__(self, snapshots):
        self._snapshots = snapshots

    def snapshot(self, key):
        return self._snapshots.get(key, _FakeSnapshot("NO_DATA", None))


def test_first_render_freshness_fields_capture_what_the_screen_drew(tmp_path: Path) -> None:
    from messiah.ui.app import _snapshot_freshness_fields

    # 연휴 형태 재현: 오늘은 08-18인데 가장 최근 봉은 08-14다.
    bar_day = tmp_path / "A05609" / "5m"
    bar_day.mkdir(parents=True)
    (bar_day / "2026-08-14.parquet").write_bytes(b"PAR1PAR1")

    fields = _snapshot_freshness_fields(
        _FakeSource({"FuturesView": _FakeSnapshot("STALE", 259200.0, 1800.0)}),
        "A05609",
        "5m",
        tmp_path,
        today=date(2026, 8, 18),
    )

    assert fields["mode"] == "LIVE"
    assert fields["threshold_basis"] == "elapsed_seconds", "P-9의 질문에 대한 명시적 답"
    assert fields["chart_date"] == "2026-08-14"
    assert fields["chart_lag_calendar_days"] == 4, "이것이 P-9가 재려던 값이다"
    assert fields["topics"]["FuturesView"]["badge"] == "STALE"
    assert fields["topics"]["FuturesView"]["age_seconds"] == 259200.0
    # 안 받은 토픽은 NO_DATA로 남는다 — 배지 판정을 로그가 재발명하지 않는다.
    assert fields["topics"]["DecisionIntent"]["badge"] == "NO_DATA"
    assert fields["topics"]["DecisionIntent"]["age_seconds"] is None


def test_freshness_fields_survive_an_empty_bar_dir(tmp_path: Path) -> None:
    """봉이 한 장도 없는 첫날 — 없는 날짜를 지어내지 않는다(L18)."""
    from messiah.ui.app import _snapshot_freshness_fields

    fields = _snapshot_freshness_fields(
        _FakeSource({}), "A05609", "5m", tmp_path, today=date(2026, 8, 18)
    )

    assert fields["chart_date"] is None
    assert fields["chart_lag_calendar_days"] is None


# --------------------------------------------- F-A′: 생산 형상으로 유도가 되는가 (2026-08-20)
#
# 위 `_view()`는 `valid_until = ts + horizon`(= 다음 갱신 시각)으로 짓는다. **생산 코드는
# 그런 값을 만들지 않는다.** `Aggregator`는 `ts_utc = as_of = 트리거 봉의 확정 시각`을 넣고
# `valid_until = min(기여 ExpertView.valid_until)`을 넣는데, 후자는 트리거 자신을 포함하므로
# **항상 `as_of` 이하**다. 즉 `valid_until − ts_utc ≤ 0`이라 유도가 매번 실패했고, 2026-08-14
# F-4가 "고쳤다"고 적은 경로는 라이브에서 **0회** 사용됐다.
#
# 위 테스트들이 그것을 못 잡은 이유가 정확히 픽스처다. 그래서 아래 두 테스트는 픽스처를 짓지
# 않고 **`Aggregator.compute()`가 실제로 낸 메시지**를 먹인다.


def _production_view(*, n_experts: int) -> FuturesView:
    """`Aggregator.compute()`가 실제로 내는 형상 그대로 — 픽스처를 짓지 않는다."""
    from messiah.core.messages import HORIZON_SECONDS, ExpertView, Horizon, RegimeState
    from messiah.strategy.futures.aggregator import Aggregator

    horizon = Horizon.M30
    bar_open = datetime(2026, 8, 20, 10, 0, tzinfo=KST)
    as_of = bar_open + timedelta(seconds=HORIZON_SECONDS[horizon])  # = 봉 확정 시각
    views = {}
    if n_experts:
        views[horizon] = ExpertView(
            symbol="A05609",
            ts_utc=as_of,
            horizon=horizon,
            p_up=0.6,
            p_flat=0.3,
            p_down=0.1,
            ens_std=0.01,
            meta_passed=True,
            model_version="test",
            valid_until=as_of,  # 생산: FeatureVector.valid_until 그대로
        )
    return Aggregator().compute(
        "A05609",
        views,
        RegimeState(symbol="A05609", regime=Regime.TREND_UP, confidence=0.9, state_duration_bars=3),
        as_of=as_of,
        cadence_seconds=float(HORIZON_SECONDS[horizon]),
    )


def test_production_shaped_view_derives_its_cadence() -> None:
    """기여 전문가가 있어도 `valid_until − ts_utc`는 0이다 — 그래서 `cadence_seconds`가 있다."""
    view = _production_view(n_experts=1)
    assert view.valid_until is not None
    assert (
        (view.valid_until - view.ts_utc).total_seconds() <= 0
    ), "생산 형상에서 이 차이가 양수가 되면 F-A′의 전제가 바뀐 것이다 — 유도식을 재검토하라"
    threshold, cadence = derived_stale_after(view, fallback=10.0)
    assert cadence == 1800
    assert threshold == 2700


def test_no_contribution_view_still_derives_its_cadence() -> None:
    """기여 0명이어도 **다음 완성봉에 또 돈다** — 그 사실이 10초 상수로 지워지면 안 된다."""
    view = _production_view(n_experts=0)
    assert view.n_experts == 0
    assert view.valid_until is None  # 기여가 없으니 봉 확정 시각은 못 정한다(종전과 동일)
    threshold, cadence = derived_stale_after(view, fallback=10.0)
    assert cadence == 1800
    assert threshold == 2700


def test_fallback_uses_are_counted() -> None:
    """「고쳤는데 새 경로가 한 번도 안 쓰였다」를 세는 축 (2026-08-20 G-A)."""
    from messiah.ui.data_source import (
        reset_threshold_derivation_stats,
        threshold_derivation_stats,
    )

    reset_threshold_derivation_stats()
    derived_stale_after(_production_view(n_experts=1), fallback=10.0)
    derived_stale_after(_view(None), fallback=10.0)  # 주기를 못 구하는 옛 형상
    stats = threshold_derivation_stats()
    assert stats == {"derived": 1, "fallback": 1}
    reset_threshold_derivation_stats()


def test_regime_state_cadence_is_the_driving_horizon() -> None:
    """`RegimeState`는 `valid_until − ts_utc`가 **음수**였다 — 15초 상수가 정본이었다."""
    from messiah.core.messages import HORIZON_SECONDS, Horizon, RegimeState

    state = RegimeState(
        symbol="A05609",
        regime=Regime.TREND_UP,
        confidence=0.8,
        state_duration_bars=2,
        valid_until=datetime(2026, 8, 20, 10, 30, tzinfo=KST),  # 봉 확정(과거)
        ts_utc=datetime(2026, 8, 20, 10, 30, 1, tzinfo=KST),  # 발행 wall clock
        cadence_seconds=float(HORIZON_SECONDS[Horizon.M30]),
    )
    assert (state.valid_until - state.ts_utc).total_seconds() < 0
    threshold, cadence = derived_stale_after(state, fallback=15.0)
    assert cadence == 1800
    assert threshold == 2700
