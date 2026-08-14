"""검사 안 한 0을 "0건"이라 말하지 않는다 — 2026-08-14 F-C.

`degenerate_count == 0`에는 두 뜻이 있다: **검사했는데 없었거나, 검사를 못 했거나.**
2026-08-14 리포트는 *"30m 피처 퇴화 0건(14표본)"* 이라고 말했다. 그런데 30m은 하루 15봉이
물리적 상한이라 판정 하한 30을 **어떤 날에도 못 넘는다** — 가장 위험한 Horizon에 대한
가장 안심되는 문장이 매일 나오고 있었다.

임계를 낮추는 것은 답이 아니다(오탐이 는다). 답은 다일 누적 판정이고 그건 별건이다(G-9).
여기서 하는 것은 **세 번째 상태를 어휘에 넣는 것**뿐이다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from messiah.core.messages import BarClosed, FeatureVector, Horizon
from messiah.core.timeutil import KST
from messiah.features import engine as engine_mod
from messiah.features.engine import FeatureEngine
from messiah.ops.fix_verification import METRIC_EXTRACTORS


class FakeBus:
    async def publish(self, topic: str, msg: FeatureVector) -> None:  # noqa: D102
        return None


def _bar(index: int, *, close: int) -> BarClosed:
    return BarClosed(
        symbol="A05609",
        horizon=Horizon.M5,
        bar_open_kst=datetime(2026, 8, 14, 9, 0, tzinfo=KST) + timedelta(minutes=5 * index),
        o_ticks=close,
        h_ticks=close + 5,
        l_ticks=close - 5,
        c_ticks=close,
        volume=10,
        quality_ok=True,
    )


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, dict]]:
    entries: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(engine_mod.mlog, "log", lambda tag, msg, **f: entries.append((tag, msg, f)))
    return entries


async def _run(engine: FeatureEngine, bars: int) -> None:
    for i in range(bars):
        await engine.handle_bar(_bar(i, close=100 + i))


# ------------------------------------------------------------------ 엔진 어휘


async def test_too_few_samples_is_reported_as_not_judged(logged) -> None:
    """오늘의 30m이 정확히 이 형태였다 — 14표본으로 "퇴화 0건"이 나갔다."""
    engine = FeatureEngine("A05609", FakeBus(), feature_set="v-test", horizons=[Horizon.M5])
    await _run(engine, 14)

    engine.log_feature_health()

    tag, msg, fields = next(t for t in logged if t[0].startswith("FeatureHealth"))
    assert tag == "FeatureHealthNotJudged"
    assert fields["judged"] is False
    assert fields["samples"] == 14
    assert "판정 보류" in msg
    assert "0건이 아니라" in msg  # 문장 자체가 두 뜻을 가른다


async def test_enough_samples_still_says_zero_when_clean(logged) -> None:
    """판정된 0은 그대로 0이다 — 이 변경이 정상 경로를 흐리지 않는지 지킨다."""
    engine = FeatureEngine("A05609", FakeBus(), feature_set="v-test", horizons=[Horizon.M5])
    await _run(engine, 40)

    healths = engine.log_feature_health()

    assert healths[0].judged is True
    tag, msg, fields = next(t for t in logged if t[0].startswith("FeatureHealth"))
    assert tag in ("FeatureHealthSummary", "FeatureHealthDegenerate")
    assert fields["judged"] is True
    if tag == "FeatureHealthSummary":
        assert "판정됨" in msg


async def test_judged_flag_tracks_the_sample_floor(logged) -> None:
    engine = FeatureEngine("A05609", FakeBus(), feature_set="v-test", horizons=[Horizon.M5])
    await _run(engine, engine_mod._MIN_SAMPLES_FOR_HEALTH - 1)
    assert engine.feature_health()[0].judged is False

    await _run(engine, engine_mod._MIN_SAMPLES_FOR_HEALTH + 1)
    assert engine.feature_health()[0].judged is True


# ------------------------------------------------------------------ 채점기


def test_unjudged_horizons_leave_the_denominator() -> None:
    """판정 못 한 Horizon은 분모에서 빠진다 — 0으로 합산하면 거짓 통과가 매일 쌓인다."""
    extractor = METRIC_EXTRACTORS["degenerate_feature_count"]
    report = {
        "degenerate_features": {
            "5m": {"always_nan": ["a"], "constant": [], "judged": True, "samples": 80},
            "30m": {"always_nan": [], "constant": [], "judged": False, "samples": 14},
        }
    }
    assert extractor(report) == 1.0


def test_a_day_with_nothing_judged_is_unjudged_not_clean() -> None:
    """한 Horizon도 판정 못 했으면 `None`이다 — `evaluate()`가 그날을 건너뛴다(L18)."""
    extractor = METRIC_EXTRACTORS["degenerate_feature_count"]
    report = {
        "degenerate_features": {
            "30m": {"always_nan": [], "constant": [], "judged": False, "samples": 14},
        }
    }
    assert extractor(report) is None


def test_old_reports_without_the_flag_keep_their_meaning() -> None:
    """옛 리포트에는 `judged`가 없다 — 과거 판정을 소급해 뒤집지 않는다."""
    extractor = METRIC_EXTRACTORS["degenerate_feature_count"]
    report = {"degenerate_features": {"5m": {"always_nan": ["a", "b"], "constant": ["c"]}}}
    assert extractor(report) == 3.0
