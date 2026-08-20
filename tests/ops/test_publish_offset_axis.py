"""하루 한 숫자가 「종일 나쁨」과 「갈수록 나빠짐」을 같은 값으로 접었다 — 2026-08-20 F-E · G-D.

2026-08-20 발행 오프셋 종일 p90은 1,083ms 하나였다. 그 숫자로는 두 해석이 구분되지 않는데
**처방이 정반대다** — 회선이 나쁜 날이면 완성봉 유예를 올려야 하고, 내부 적체면 프로파일링을
해야 한다. 시간대로 갈라 보니 1m p50이 09시 74.8ms → 15시 788.5ms(10.5배)였다.

그리고 종전 로그로는 **오프셋을 정확히 잴 수도 없었다**. `FeaturePublish`에 발행 wall clock만
남아서, 봉 확정 시각은 사람이 Horizon 격자로 역산해야 했다. 그 프록시엔 되감기 모호성이 있다 —
봉 확정은 거래소 시각으로 판정하는데 로그 `ts`는 로컬 시계라, 시계 스큐가 +0.156초인 날
「경계보다 0.15초 이르게」가 「59.85초 늦게」와 초 단위에서 구분되지 않는다. 실제로 이 저장소의
2026-08-20 replay가 그 함정에 빠졌다가 `ClockSkewMeasured`와 대조해서야 갈랐다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from messiah.core.messages import HORIZON_SECONDS, FeatureVector, Horizon
from messiah.core.timeutil import KST
from messiah.features.engine import _percentiles
from messiah.ops.integrity_report import INTRADAY_DRIFT_RATIO, hourly_trend

# ------------------------------------------------------------------ 백분위


def test_percentiles_work_with_a_single_sample() -> None:
    """15시대는 마감까지 한두 봉뿐인 Horizon이 있다 — 거기서 예외가 나면 축이 통째로 빈다."""
    stats = _percentiles([742.0])
    assert stats["p50"] == 742.0
    assert stats["max"] == 742.0
    assert stats["samples"] == 1.0


def test_percentiles_are_monotone() -> None:
    stats = _percentiles([float(x) for x in range(1, 101)])
    assert stats["p50"] <= stats["p90"] <= stats["p99"] <= stats["max"]


# ------------------------------------------------------------------ 일중 추세


def _hours(values: dict[str, tuple[float, int]]) -> dict[str, dict[str, float]]:
    return {h: {"p50": v, "samples": float(n)} for h, (v, n) in values.items()}


def test_partial_hour_buckets_are_dropped() -> None:
    """개장 전 웜업(08시)과 마감 잔여(15시)를 양 끝으로 쓰면 진폭이 절반으로 눌린다.

    2026-08-20 1m 실측이 그랬다 — 08시를 끝점으로 쓰면 2.8배, 빼면 10.5배다.
    표본이 중앙값의 절반에 못 미치는 버킷을 뺀다.
    """
    trend = hourly_trend(
        _hours(
            {
                "08": (280.3, 15),  # 웜업 — 정상 시간대의 1/4
                "09": (74.8, 59),
                "10": (140.4, 60),
                "11": (334.8, 60),
                "12": (664.1, 60),
                "13": (652.6, 60),
                "14": (884.8, 60),
                "15": (788.5, 35),
            }
        )
    )
    assert trend is not None
    assert trend["first_hour"] == "09", "08시(웜업)를 첫 점으로 쓰면 안 된다"
    assert trend["dropped"] == ["08"], "뺀 것은 조용히 버리지 않고 남긴다"
    assert trend["ratio"] == pytest.approx(10.54, abs=0.01)
    assert trend["drift"] is True


def test_slope_sees_what_the_ratio_cannot() -> None:
    """비율은 양 끝 두 점만 본다 — 첫 값이 작으면 폭발한다(1m 09시가 74.8ms다)."""
    trend = hourly_trend(
        _hours({"09": (74.8, 60), "10": (140.4, 60), "11": (334.8, 60), "12": (664.1, 60)})
    )
    assert trend is not None
    assert trend["slope"] is not None and trend["slope"] > 0, "우상향이면 기울기가 양수여야 한다"


def test_flat_day_is_not_drift() -> None:
    """종일 균일하게 나쁜 날은 **추세가 아니다** — 처방이 다르므로 섞으면 안 된다."""
    trend = hourly_trend(
        _hours({"09": (600.0, 60), "10": (610.0, 60), "11": (598.0, 60), "12": (605.0, 60)})
    )
    assert trend is not None
    assert trend["ratio"] < INTRADAY_DRIFT_RATIO
    assert trend["drift"] is False


def test_one_hour_is_unmeasured_not_flat() -> None:
    """재기동으로 잘린 날에 비율을 지어내면 그것이 곧 오탐이다 (L18)."""
    assert hourly_trend(_hours({"09": (600.0, 60)})) is None
    assert hourly_trend({}) is None


def test_all_partial_buckets_still_get_a_verdict() -> None:
    """전부 부분 버킷이어도 판정을 포기하진 않는다 — 걸러낼 기준이 없을 뿐이다."""
    trend = hourly_trend(_hours({"09": (100.0, 3), "10": (400.0, 3)}))
    assert trend is not None
    assert trend["dropped"] == []
    assert trend["ratio"] == 4.0


# ------------------------------------------------------ 엔진이 오프셋을 실제로 잰다


def _vector(horizon: Horizon, bar_open: datetime) -> FeatureVector:
    return FeatureVector(
        symbol="A05609",
        ts_utc=bar_open,
        horizon=horizon,
        feature_set="test",
        values={},
        nan_ratio=0.0,
        valid_until=bar_open + timedelta(seconds=HORIZON_SECONDS[horizon]),
    )


class _NullBus:
    """발행 자체는 이 테스트의 관심사가 아니다 — 오프셋 계측만 본다."""

    async def publish(self, topic, message) -> None:  # noqa: D102
        return None


def _engine(now_value: list[datetime]):
    from messiah.features.engine import FeatureEngine

    return FeatureEngine(
        "A05609",
        _NullBus(),
        feature_set="v-test",
        horizons=[Horizon.M1],
        now=lambda: now_value[0],
    )


def test_offset_is_measured_against_bar_confirm_not_wall_clock() -> None:
    """되감기 모호성을 없애는 것이 이 축의 요점이다 — 확정 시각과의 차이를 직접 잰다."""
    bar_open = datetime(2026, 8, 20, 10, 0, tzinfo=KST)
    vector = _vector(Horizon.M1, bar_open)
    now = [vector.valid_until + timedelta(milliseconds=140.4)]
    engine = _engine(now)
    assert engine._record_publish_offset(vector) == pytest.approx(140.4, abs=0.1)


def test_negative_offset_is_kept_as_negative() -> None:
    """경계보다 이르게 발행된 건은 **음수로 남긴다.**

    2026-08-20에 1m 24건이 최대 −152.1ms였고, 그것은 결함이 아니라 거래소−로컬 시계 스큐
    (+0.156초)의 그림자였다. 0으로 눌러 담으면 그 사실이 사라지고, 절댓값으로 접으면
    「늦음」과 「이름」이 같은 값이 된다 — 둘 다 사후 조사를 불가능하게 만든다.
    """
    bar_open = datetime(2026, 8, 20, 9, 2, tzinfo=KST)
    vector = _vector(Horizon.M1, bar_open)
    now = [vector.valid_until - timedelta(milliseconds=152.1)]
    engine = _engine(now)
    assert engine._record_publish_offset(vector) == pytest.approx(-152.1, abs=0.1)


def test_missing_valid_until_is_unmeasured_not_zero() -> None:
    bar_open = datetime(2026, 8, 20, 10, 0, tzinfo=KST)
    vector = _vector(Horizon.M1, bar_open).model_copy(update={"valid_until": None})
    engine = _engine([bar_open])
    assert engine._record_publish_offset(vector) is None


def test_summary_says_it_could_not_measure(monkeypatch) -> None:
    """발행이 0건인 세션을 조용히 넘기면 "지연이 없었다"와 구분되지 않는다 (L18)."""
    from messiah.core import logging as mlog

    records: list[dict] = []
    monkeypatch.setattr(mlog, "log", lambda tag, msg, **f: records.append({"tag": tag, **f}))
    engine = _engine([datetime(2026, 8, 20, 15, 40, tzinfo=KST)])
    assert engine.log_publish_offsets() is None
    assert records[-1]["tag"] == "FeaturePublishOffset"
    assert records[-1]["measured"] is False
    assert records[-1]["samples"] == 0


def test_summary_carries_by_hour(monkeypatch) -> None:
    """`by_hour`가 없으면 G-D가 잴 것이 없다 — 이 둘은 같은 커밋에 있어야 한다."""
    from messiah.core import logging as mlog

    records: list[dict] = []
    monkeypatch.setattr(mlog, "log", lambda tag, msg, **f: records.append({"tag": tag, **f}))
    now = [datetime(2026, 8, 20, 9, 1, tzinfo=KST)]
    engine = _engine(now)
    for hour, minutes in ((9, 30), (14, 30)):
        for minute in range(minutes):
            bar_open = datetime(2026, 8, 20, hour, minute, tzinfo=KST)
            vector = _vector(Horizon.M1, bar_open)
            delay = 75.0 if hour == 9 else 880.0
            now[0] = vector.valid_until + timedelta(milliseconds=delay)
            engine._record_publish_offset(vector)

    stats = engine.log_publish_offsets()
    assert stats is not None
    published = records[-1]
    assert published["measured"] is True
    # 버킷 키는 **발행 시각**의 시간대다(봉 시작이 아니라) — 09:00~09:29 봉은 09:01~09:30에
    # 확정되므로 전부 09시로 간다.
    assert set(published["by_hour"]) == {"09", "14"}
    trend = hourly_trend(published["by_hour"])
    assert trend is not None
    assert trend["ratio"] is not None and trend["ratio"] > INTRADAY_DRIFT_RATIO
