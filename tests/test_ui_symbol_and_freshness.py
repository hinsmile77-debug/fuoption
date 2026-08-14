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
