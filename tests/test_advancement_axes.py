"""고도화 축의 회귀 방지선 — 2026-08-14 G-3~G-10.

각 고도화가 **다시 무너지는 형태**를 하나씩 잡는다. 개별 동작은 각 모듈 테스트가 보고,
여기서는 "그 축이 존재하는가"와 "상수로 되돌아가지 않았는가"를 지킨다.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from messiah.core.messages import Horizon
from messiah.features import engine as feature_engine
from messiah.ops import feature_health_rolling, verdict
from messiah.ui import data_source as ds

_REPO_ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ G-5


def test_required_bars_is_measured_not_assumed() -> None:
    """윈도 최댓값으로 갈음하면 60이라 답한다 — 실제로는 180이다.

    `px_ema_cross_60`은 slow EMA가 `3×W`를 요구하고 `px_macd_h_60`은 `2×W + W//3`이다.
    두 피처는 용량이 130이던 시절 **프로덕션에서 영원히 NaN이었고**, 8거래일간 아무도
    못 잡았다. 가정으로 갈음하면 그 결함을 그대로 재생산한다.
    """
    per_feature = feature_engine.required_bars_by_feature("v2026.08-ev")
    assert per_feature["px_ema_cross_60"] == 180
    assert per_feature["px_macd_h_60"] == 139
    assert feature_engine.required_bars("v2026.08-ev") == 180


def test_required_bars_fits_in_history_capacity() -> None:
    """요구가 용량을 넘으면 그 피처는 영원히 NaN이다 — 새 피처가 그러면 여기서 깨진다."""
    assert feature_engine.required_bars("v2026.08-ev") <= feature_engine._MAX_HISTORY


@pytest.mark.parametrize(
    ("horizon", "expect_days"),
    [(Horizon.M1, "1거래일"), (Horizon.M30, "12거래일")],
)
def test_recovery_forecast_answers_in_trading_days(horizon: Horizon, expect_days: str) -> None:
    """ "180봉 부족"보다 "12거래일"이 운영 판단에 쓰이는 단위다.

    2026-08-14엔 이 값이 없어 사람이 12:30까지 세 번 손으로 계산했고 한 번 틀렸다.
    """
    line = feature_engine.recovery_forecast(0, "v2026.08-ev", horizon)
    assert expect_days in line
    assert "부족" in line


def test_recovery_forecast_is_quiet_when_satisfied() -> None:
    line = feature_engine.recovery_forecast(999, "v2026.08-ev", Horizon.M30)
    assert "충족" in line
    assert "부족" not in line


# ------------------------------------------------------------------ G-9


def _health(horizon: str, samples: int, constant: list[str]):
    return feature_engine.FeatureHealth(
        horizon=horizon, samples=samples, always_nan=[], constant=constant
    )


def test_three_days_reach_the_floor_that_one_day_cannot(tmp_path: Path) -> None:
    """30m는 하루 15봉이 상한이라 하한 30을 **어떤 날에도** 못 넘는다 — 3일이면 45봉이다."""
    path = tmp_path / "rolling.json"
    for i, day in enumerate((date(2026, 8, 11), date(2026, 8, 12), date(2026, 8, 13))):
        feature_health_rolling.record_day(
            [_health("30m", 15, ["px_dead"])], symbol="A05608", day=day, path=path
        )
    verdicts = feature_health_rolling.judge(
        day=date(2026, 8, 13), path=path, window_days=3, min_samples=30
    )
    assert len(verdicts) == 1
    assert verdicts[0].samples == 45
    assert verdicts[0].judged is True


def test_degeneracy_needs_every_day_in_the_window(tmp_path: Path) -> None:
    """**교집합이다.** 하루만 상수였던 피처는 조용한 장의 흔적일 수 있다 —
    합집합으로 세면 창을 넓힐수록 퇴화가 늘어나는 이상한 축이 된다."""
    path = tmp_path / "rolling.json"
    feature_health_rolling.record_day(
        [_health("30m", 15, ["always", "sometimes"])],
        symbol="A05608",
        day=date(2026, 8, 11),
        path=path,
    )
    feature_health_rolling.record_day(
        [_health("30m", 15, ["always"])], symbol="A05608", day=date(2026, 8, 12), path=path
    )
    feature_health_rolling.record_day(
        [_health("30m", 15, ["always"])], symbol="A05608", day=date(2026, 8, 13), path=path
    )
    v = feature_health_rolling.judge(
        day=date(2026, 8, 13), path=path, window_days=3, min_samples=30
    )[0]
    assert v.constant == ("always",)


def test_a_rollover_inside_the_window_is_declared(tmp_path: Path) -> None:
    """심볼이 바뀌면 피처의 성질도 바뀔 수 있다 — 판정은 하되 조용히 섞지 않는다(R10)."""
    path = tmp_path / "rolling.json"
    feature_health_rolling.record_day(
        [_health("30m", 15, [])], symbol="A05608", day=date(2026, 8, 13), path=path
    )
    feature_health_rolling.record_day(
        [_health("30m", 15, [])], symbol="A05609", day=date(2026, 8, 14), path=path
    )
    v = feature_health_rolling.judge(
        day=date(2026, 8, 14), path=path, window_days=3, min_samples=30
    )[0]
    assert v.spans_rollover is True
    assert set(v.symbols) == {"A05608", "A05609"}


def test_same_day_recorded_twice_keeps_the_last(tmp_path: Path) -> None:
    """장중 재기동이 실제로 그 경우다 — 같은 날이 두 번 세어지면 판정이 부풀려진다."""
    path = tmp_path / "rolling.json"
    for samples in (15, 15):
        feature_health_rolling.record_day(
            [_health("30m", samples, [])], symbol="A05608", day=date(2026, 8, 13), path=path
        )
    v = feature_health_rolling.judge(
        day=date(2026, 8, 13), path=path, window_days=3, min_samples=1
    )[0]
    assert v.samples == 15


# ------------------------------------------------------------------ G-3 · G-6 · G-8


def test_verdict_summarises_in_one_line() -> None:
    """화면은 목록을 다 안 읽는다 — 첫 줄만 읽어도 알 수 있어야 한다."""
    v = verdict.build(
        [
            verdict.Reason(code=verdict.REASON_REGIME_UNKNOWN, detail="국면 UNKNOWN"),
            verdict.Reason(code=verdict.REASON_NAN_RATIO_EXCEEDED, detail="NaN 임계 초과"),
        ]
    )
    assert v.ok is False
    assert v.to_dict()["summary"] == "regime_unknown · feature_nan_ratio_exceeded"


def test_clean_day_says_so() -> None:
    v = verdict.build([None, None])
    assert v.ok is True
    assert v.to_dict()["summary"] == "판단 가용"


def test_a_fact_on_one_surface_only_is_an_observation_gap() -> None:
    """한 표면에만 나타나면 다른 표면을 보는 사람은 그 사실을 영영 못 본다 (G-6)."""
    v = verdict.build(
        [
            verdict.Reason(
                code=verdict.REASON_NAN_RATIO_EXCEEDED,
                detail="NaN 임계 초과",
                sources=("status_snapshot",),
                missing_from=("l1_daily.log",),
            )
        ]
    )
    assert len(v.observation_gaps) == 1
    assert v.to_dict()["observation_gap_count"] == 1


def test_axes_agreeing_produce_no_conflict() -> None:
    """전원 일치면 조용하다 — 넓은 그물은 늑대소년을 만든다."""
    assert verdict.arbitrate_axes({"a": (True, "p1"), "b": (True, "p2")}) is None
    assert verdict.arbitrate_axes({"a": (False, "p1"), "b": (False, "p2")}) is None


def test_conflicting_axes_promote_differing_paths_to_a_cause() -> None:
    """**경로가 다르면 그 자체가 원인 후보다** — 2026-08-14가 정확히 그 형태였다."""
    reason = verdict.arbitrate_axes(
        {
            "계열 머리 구멍": (True, "data/bars/A05608"),
            "기동 지연": (False, "logs(SessionStart)"),
            "거래량 아침 미수집": (False, "logs/volume_check.json"),
        },
        evidence={"data/bars/A05608": False},
    )
    assert reason is not None
    assert reason.code == verdict.REASON_AXIS_CONFLICT
    assert "경로가 다르다" in reason.detail
    assert "데이터 없음" in reason.detail  # ③ 소수파 경로를 되물은 답
    assert "다수파" in reason.detail


def test_single_axis_cannot_conflict() -> None:
    assert verdict.arbitrate_axes({"only": (True, "p")}) is None


# ------------------------------------------------------------------ G-4


def test_dead_is_distinguished_from_stale() -> None:
    """ "느려졌다"와 "죽었다"는 처방이 다르다 — 전자는 지켜보고 후자는 프로세스를 확인한다."""
    slow = ds.TopicSnapshot(None, ds.FreshnessBadge.STALE, age_seconds=2000, cadence_seconds=1800)
    dead = ds.TopicSnapshot(None, ds.FreshnessBadge.STALE, age_seconds=6000, cadence_seconds=1800)
    assert slow.dead is False
    assert dead.dead is True


def test_unknown_cadence_is_never_called_dead() -> None:
    """모르는 것을 "죽었다"로 부르지 않는다(L18)."""
    unknown = ds.TopicSnapshot(None, ds.FreshnessBadge.STALE, age_seconds=99999)
    assert unknown.dead is False


def test_no_new_constant_threshold_sneaks_into_the_ui() -> None:
    """**상수 임계가 다시 생기면 여기서 깨진다** (2026-08-14 G-4).

    `CircuitBreakerStatus`만 이 함정을 알고 40초로 잡아 뒀고 `FuturesView`는 10초 상수로
    남아 거래일의 99.4%를 STALE로 보냈다 — 한 곳에서만 피한 것은 설계가 아니라 우연이다.
    판단 계열(`valid_until`을 싣는 메시지)은 주기 유도가 정본이고, 여기 남은 상수는
    유도 실패 시의 **하한**이라는 뜻이 문서로 고정돼 있어야 한다.
    """
    source = (_REPO_ROOT / "src" / "messiah" / "ui" / "app.py").read_text(encoding="utf-8")
    block = source[source.index("_STALE_AFTER: dict[str, float]") :]
    block = block[: block.index("}")]
    for key in ("FuturesView", "RegimeState", "OptionsView"):
        assert key in block, f"{key} 하한이 사라졌다 — 유도 실패 시 쓸 값이 없어진다"
    preamble = source[: source.index("_STALE_AFTER: dict[str, float]")]
    assert (
        "발행 주기에서 유도한다" in preamble
    ), "판단 계열 임계가 주기 유도라는 사실이 문서에서 사라졌다 — 다음 사람이 상수로 되돌린다"


def test_stale_multiple_catches_exactly_one_missed_publication() -> None:
    """1.5배인 이유 — 1회 결손(2주기)은 반드시 걸리고 정상 간격은 안 걸린다."""
    assert ds._CADENCE_STALE_MULTIPLE < 2.0
    assert ds._CADENCE_STALE_MULTIPLE > 1.0
    assert ds._CADENCE_DEAD_MULTIPLE > ds._CADENCE_STALE_MULTIPLE


# ------------------------------------------------------------------ G-7 회귀


def test_symbol_resolution_is_the_only_expiring_constant_home() -> None:
    """만기가 있는 값이 `src/` 어디에도 기본값으로 박히지 않는다 (G-7·G-10 공동)."""
    offenders: list[str] = []
    pattern = re.compile(r'^\s*(?!#)\w*(DEFAULT|FALLBACK)\w*SYMBOL\w*\s*[:=].*[\'"]A05\d{2}[\'"]')
    for path in (_REPO_ROOT / "src").rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}")
    assert not offenders, "만기 심볼 상수:\n" + "\n".join(offenders)


def test_trading_symbol_record_roundtrips(tmp_path: Path) -> None:
    from messiah.core import symbol_resolution

    day = date(2026, 8, 14)
    path = symbol_resolution.record(day, "A05609", log_dir=tmp_path)
    assert path is not None
    assert json.loads(path.read_text(encoding="utf-8"))["symbol"] == "A05609"
    assert symbol_resolution.recorded(day, log_dir=tmp_path) == "A05609"
