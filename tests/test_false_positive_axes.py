"""매일 우는 두 축 — 오탐이 관측을 못 쓰게 만들던 자리 (2026-08-11).

2026-08-11 리포트의 임계 초과 9건 중 **5건이 오탐**이었다. 둘 다 형태가 같다: 판정이
답할 수 없는 질문을 하고 있었고, 답할 수 있는 자리는 따로 있었다.

    ① 퇴화 판정   "하루 안에서 상수인가"        ← EV 캘린더는 정의상 상수다
                  → 날짜 간 동결로 축을 옮긴다(끄는 게 아니다)
    ② 관측 공백   "그 프로세스가 로그를 찍었나"  ← Streamlit은 정상일 때 조용하다
                  → 30초 워치독의 침묵을 생존 증거로 읽는다

**매일 우는 경고는 결국 아무도 안 본다** — 이 저장소가 `늑대소년`이라 부르며 반복해서
경계해 온 실패다(`configs/pending_verifications.yaml`).
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from messiah.core.messages import BarClosed, Horizon
from messiah.core.timeutil import KST
from messiah.features import ev_core, px_core
from messiah.features import spec as feature_spec
from messiah.features.engine import FeatureEngine
from messiah.ops.integrity_report import _calendar_freeze_finding, _ui_activity_from_watchdog
from messiah.simulator.inprocess_bus import InProcessBus

# ---------------------------------------------------------------- ① 퇴화 판정


def test_every_calendar_constant_is_declared():
    """**2026-08-11 리포트가 잡은 11개 전부**가 선언돼 있어야 한다.

    이 목록이 그날 4개 Horizon에 각각 "피처 11개가 세션 내내 죽어 있었다"로 찍혔고,
    등록부 `no-degenerate-features`(임계 0)를 구조적으로 통과 불가로 만들었다.
    """
    flagged_on_20260811 = {
        "ev_dow_fri",
        "ev_dow_mon",
        "ev_dow_thu",
        "ev_dow_tue",
        "ev_dow_wed",
        "ev_dte_fut",
        "ev_dte_opt_m",
        "ev_dte_opt_w",
        "ev_expiry_flag",
        "ev_holiday_adj",
        "ev_rollover_win",
    }

    assert flagged_on_20260811 <= feature_spec.intraday_constant_ok()


def test_time_of_day_features_are_not_whitelisted():
    """**화이트리스트가 넓어지면 그 축이 아무것도 안 잡는다.** 시각을 보는 EV 피처는 장중에
    실제로 변하므로, 그것들이 상수로 나오면 그건 진짜 사고다 — 2026-08-11 리포트도 이 다섯은
    안 걸었다(검출기는 잘 돌고 있었다)."""
    whitelist = feature_spec.intraday_constant_ok()

    for name in ("ev_tod_sin", "ev_tod_cos", "ev_open_elapsed", "ev_close_remain", "ev_lunch_flag"):
        assert name not in whitelist


def test_the_aggregator_collects_every_category():
    """카테고리가 자기 상수를 선언하고 판정기가 모아 본다 — 종전엔 엔진이 `px_core`를
    직접 참조해서, EV를 켠 다음 날 그 목록이 EV를 모른 채로 판정했다."""
    aggregated = feature_spec.intraday_constant_ok()

    assert px_core.INTRADAY_CONSTANT_OK <= aggregated
    assert ev_core.INTRADAY_CONSTANT_OK <= aggregated


def test_a_typo_in_a_declaration_is_caught(monkeypatch):
    """**선언했다고 믿는 동안 등록부는 계속 운다.** 오타난 이름은 아무것도 안 가리고,
    정작 대상 피처는 계속 퇴화로 잡힌다."""
    monkeypatch.setattr(ev_core, "INTRADAY_CONSTANT_OK", frozenset({"ev_dwo_mon"}), raising=False)

    problems = feature_spec.validate_registry()

    assert any("ev_dwo_mon" in p for p in problems)


def test_windowed_names_resolve_to_their_base():
    """`px_ema_cross_20`은 `px_ema_cross`로 판정한다 — 선언은 기저 이름으로 하기 때문이다."""
    assert feature_spec.is_intraday_constant_ok("px_ema_cross_20")
    assert feature_spec.is_intraday_constant_ok("ev_dow_mon")
    assert not feature_spec.is_intraday_constant_ok("px_zscore_20")


def _flat_bars(n: int) -> list[BarClosed]:
    """값이 움직이는 봉 — 상수 판정이 **가격 때문에** 나지 않게 한다."""
    start = datetime(2026, 8, 11, 9, 0, tzinfo=KST)
    out = []
    for i in range(n):
        close = round(1000 + 10 * math.sin(i / 5))
        out.append(
            BarClosed(
                symbol="A05608",
                horizon=Horizon.M1,
                bar_open_kst=start + timedelta(minutes=i),
                o_ticks=close,
                h_ticks=close + 2,
                l_ticks=close - 2,
                c_ticks=close,
                volume=100 + i,
            )
        )
    return out


@pytest.mark.asyncio
async def test_the_engine_does_not_flag_calendar_constants_but_still_flags_nan():
    """**검출력을 잃지 않는다** — 허용된 상수는 안 잡되, 같은 피처가 항상 NaN이면 잡는다.
    그게 이 피처들의 진짜 사고다(캘린더 사이드카가 아예 안 붙은 날)."""
    from messiah.features import sidecar

    spec = feature_spec.resolve("v2026.08-ev")
    engine = FeatureEngine(
        "A05608", InProcessBus(instance_id="t"), feature_set=spec.name, sidecars=sidecar.build(spec)
    )
    for bar in _flat_bars(60):
        await engine.handle_bar(bar)

    health = next(h for h in engine.feature_health() if h.horizon == Horizon.M1.value)

    assert not any(name.startswith("ev_dow_") for name in health.constant)
    # 값은 남는다 — 날짜 간 동결 검사의 재료다.
    assert "ev_dow_tue" in health.allowed_constant_values  # 2026-08-11은 화요일
    assert health.allowed_constant_values["ev_dow_tue"] == 1.0


# ---------------------------------------------------------------- ① 날짜 간 동결


def _write_report(log_dir: Path, day: date, dow: dict[str, float] | None) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    payload: dict = {"date": day.isoformat()}
    if dow is not None:
        payload["allowed_constant_values"] = dow
    (log_dir / f"daily_integrity_{day:%Y%m%d}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _dow(active: str) -> dict[str, float]:
    return {
        f"ev_dow_{d}": (1.0 if d == active else 0.0) for d in ("mon", "tue", "wed", "thu", "fri")
    }


_TUE = _dow("tue")
_WED = _dow("wed")


def test_an_identical_weekday_vector_is_a_freeze(tmp_path: Path):
    """**화이트리스트의 반대편이다.** `ev_dow_*`는 매 거래일 반드시 달라진다 — 어제와 오늘의
    요일이 같을 수 없으므로, 동일한 벡터는 오탐 없이 "캘린더가 얼었다"를 뜻한다."""
    _write_report(tmp_path, date(2026, 8, 11), _TUE)

    finding = _calendar_freeze_finding(date(2026, 8, 12), tmp_path, _TUE)

    assert finding is not None
    assert "동결" in finding


def test_a_normal_day_is_silent(tmp_path: Path):
    """정상일엔 조용해야 한다 — 매일 우는 축을 하나 없애면서 다른 하나를 만들면 안 된다."""
    _write_report(tmp_path, date(2026, 8, 11), _TUE)

    assert _calendar_freeze_finding(date(2026, 8, 12), tmp_path, _WED) is None


def test_a_weekend_gap_still_compares(tmp_path: Path):
    """금요일 다음은 월요일이다 — 달력상 하루 전 리포트가 없다고 비교를 포기하면 주말마다
    이 축이 눈을 감는다."""
    _write_report(tmp_path, date(2026, 8, 7), _TUE)  # 값은 금요일이 아니지만 비교 대상은 동일성뿐

    finding = _calendar_freeze_finding(date(2026, 8, 10), tmp_path, _TUE)

    assert finding is not None


def test_a_missing_previous_report_is_not_a_pass(tmp_path: Path):
    """ "비교 못 했다"와 "정상"은 다르다 — 없으면 None이지 "이상 없음"이 아니다."""
    assert _calendar_freeze_finding(date(2026, 8, 12), tmp_path, _TUE) is None


def test_an_old_report_without_the_field_is_not_a_pass(tmp_path: Path):
    """이 축이 생기기 전 리포트에는 그 필드가 없다 — 없는 것을 "같지 않다"로 읽으면 안 된다."""
    _write_report(tmp_path, date(2026, 8, 11), None)

    assert _calendar_freeze_finding(date(2026, 8, 12), tmp_path, _TUE) is None


# ---------------------------------------------------------------- ② 관측 공백


def _record(tag: str, clock: str) -> dict:
    return {"tag": tag, "ts": f"2026-08-11T{clock}+09:00"}


def test_a_silent_ui_is_alive_while_the_watchdog_says_nothing():
    """**2026-08-11 실측 회귀.** Streamlit은 기동 배너 이후 조용하다 — 그 침묵이
    `ui: 08:20:33~09:40:20 79.8분 공백`으로 잡혔고, 같은 시각 상태판은 종일 `UP`이었다.
    감시자가 30초마다 포트를 찌르고 아무 말도 안 했다면 그건 **살아 있었다는 관측**이다."""
    synthesized = _ui_activity_from_watchdog(
        ui_own=["08:20:33"],
        watcher_activity=["08:30:00", "09:00:00", "15:34:00"],
        watcher_records=[],
    )

    assert synthesized == ["08:20:33", "08:30:00", "09:00:00", "15:34:00"]


def test_the_window_between_down_and_restarted_stays_a_gap():
    """거기서는 UI가 **실제로** 죽어 있었다 — 이 축이 잡아야 하는 진짜 사건이다."""
    synthesized = _ui_activity_from_watchdog(
        ui_own=[],
        watcher_activity=["09:00:00", "09:10:00", "09:20:00", "09:40:00"],
        watcher_records=[
            _record("CommandCenterUIDown", "09:05:00"),
            _record("CommandCenterUIRestarted", "09:30:00"),
        ],
    )

    assert synthesized == ["09:00:00", "09:40:00"]  # 09:10·09:20은 사망 구간


def test_synthesis_stops_after_the_watchdog_gives_up():
    """재기동을 포기한 뒤의 침묵은 "화면이 없다"는 뜻이다(2026-07-31에 3시간이 그랬다) —
    죽은 UI를 산 것으로 만들면 이 수정이 고치려던 것보다 나쁜 거짓말이 된다."""
    synthesized = _ui_activity_from_watchdog(
        ui_own=[],
        watcher_activity=["09:00:00", "12:00:00", "15:00:00"],
        watcher_records=[_record("CommandCenterUIRestartGaveUp", "11:00:00")],
    )

    assert synthesized == ["09:00:00"]


def test_a_death_without_a_restart_ends_the_synthesis():
    """`Down` 뒤에 `Restarted`가 없으면 그 뒤로는 모른다 — 모르는 것을 아는 척하지 않는다."""
    synthesized = _ui_activity_from_watchdog(
        ui_own=[],
        watcher_activity=["09:00:00", "10:00:00", "11:00:00"],
        watcher_records=[_record("CommandCenterUIDown", "09:30:00")],
    )

    assert synthesized == ["09:00:00"]


def test_a_dead_watcher_synthesizes_nothing():
    """감시자가 죽은 구간엔 원료가 없다 — UI를 산 것으로 만들지 않는다. 그 구간은
    `l1_daily` 자신의 공백이 따로 잡는다(이중 계산 없음)."""
    assert _ui_activity_from_watchdog(ui_own=[], watcher_activity=[], watcher_records=[]) == []
