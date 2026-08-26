"""감시기가 **중앙값만** 봐서 꼬리 15배 악화를 「이상 없음」으로 내보냈다 — 2026-08-25 F-43 · 1-13.

2026-08-25 하루가 이 파일의 픽스처다. 그날 같은 하루를 두 축으로 재면 이렇게 갈린다:

| 축 | 09시 | 15시 | 배율 | 종전 감시기 |
|---|---|---|---|---|
| 발행 오프셋 p50 | 348.9ms | 579.1ms | 1.66배 | `drift: false` (임계 3.0 미달) |
| 발행 오프셋 p90 | 745.2ms | 1,417.4ms | **1.90배** | **안 봄** |
| 1분봉 1,000ms 초과율 | 1.7% | 25.7% | **15.1배** | **안 봄** |

이 계기의 설계 근거(2026-08-20 F-E)는 스스로 *"계단은 기울기로 안 잡힌다"* 고 적어 두었다.
그 교훈을 **기울기 → 계단**에는 적용했고 **중앙값 → 꼬리**에는 적용하지 않은 것이 1-13이다.

여기 쓰인 숫자는 전부 `logs/l1_daily_20260825.log`의 실측이다 — 지어낸 값이 아니다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from messiah.core.messages import HORIZON_SECONDS, FeatureVector, Horizon
from messiah.core.timeutil import KST
from messiah.ops.integrity_report import (
    INTRADAY_OVER_RATIO_FLOOR,
    _intraday_trends,
    hourly_trend,
)

# 2026-08-25 실측 `FeaturePublishOffset.by_hour` (전 Horizon 합산).
AUG25_BY_HOUR: dict[str, dict[str, float]] = {
    "08": {"p50": 570.0, "p90": 678.1, "samples": 21.0},
    "09": {"p50": 348.9, "p90": 745.2, "samples": 104.0},
    "10": {"p50": 568.1, "p90": 1006.5, "samples": 104.0},
    "11": {"p50": 582.8, "p90": 1297.5, "samples": 104.0},
    "12": {"p50": 581.5, "p90": 1199.2, "samples": 104.0},
    "13": {"p50": 586.5, "p90": 1051.0, "samples": 104.0},
    "14": {"p50": 601.8, "p90": 1479.5, "samples": 104.0},
    "15": {"p50": 579.1, "p90": 1417.4, "samples": 63.0},
}

# 같은 하루의 1분봉 1,000ms 초과율 (보고서 1-13 표).
AUG25_1M_OVER_RATIO: dict[str, float] = {
    "09": 0.017,
    "10": 0.083,
    "11": 0.133,
    "12": 0.175,
    "13": 0.150,
    "14": 0.221,
    "15": 0.257,
}


def _with_over_ratio(ratios: dict[str, float], samples: float = 104.0) -> dict[str, dict]:
    return {
        hour: {"p50": 300.0, "p90": 900.0, "samples": samples, "over_1000_ratio": value}
        for hour, value in ratios.items()
    }


# ------------------------------------------------- 그날의 판정을 그대로 재현한다


def test_the_median_axis_still_says_no_drift_on_that_day() -> None:
    """**종전 판정을 고정한다.** 이 값이 바뀌면 p50 축을 건드린 것이고, 그건 별개 결정이다."""
    trend = hourly_trend(AUG25_BY_HOUR, key="p50")
    assert trend is not None
    assert trend["first_hour"] == "09" and trend["last_hour"] == "15"
    assert trend["ratio"] == 1.66
    assert trend["drift"] is False  # 임계 3.0 미달 — 그날 리포트가 낸 값 그대로


def test_the_p90_axis_moves_more_than_the_median_but_still_misses_that_day() -> None:
    """**이 테스트는 F-43의 한계를 고정한다** — p90 축은 2026-08-25를 못 잡는다.

    같은 `by_hour`에서 p50은 1.66배, p90은 1.90배다. 꼬리가 더 움직인 것은 맞지만
    임계 2.0에는 못 미친다. 3거래일 실측이 그 임계를 정당화한다 — 08-21이 2.61배,
    08-24가 2.20배로 **둘 다 걸리고**, 08-25가 셋 중 가장 얌전한 날이었다.

    관측 하나에 맞춰 1.8로 낮추면 그날은 걸리지만 그건 임계를 지어낸 것이다.
    08-25를 이름으로 잡는 축은 아래 `over_1000_ratio`이고, 그 축은 상류 카운터가
    필요해 F-43 적용 다음 거래일부터 존재한다.
    """
    trend = hourly_trend(AUG25_BY_HOUR, key="p90")
    assert trend is not None
    assert trend["ratio"] == 1.9
    assert trend["key"] == "p90"
    assert trend["drift"] is False  # ← 한계. 이 값이 True로 바뀌면 임계를 건드린 것이다
    # 그래도 중앙값 축보다는 크게 움직인다 — 축을 늘린 것 자체는 값을 한다.
    assert trend["ratio"] > hourly_trend(AUG25_BY_HOUR, key="p50")["ratio"]


def test_the_p90_axis_catches_the_two_days_that_were_actually_worse() -> None:
    """임계 2.0의 근거 — 지어낸 값이 아니라 3거래일 실측에서 두 날을 가른다."""
    for last_p90, expected_ratio in ((1666.0, 2.61), (1918.0, 2.2)):
        by_hour = {
            "09": {
                "p50": 400.0,
                "p90": 638.0 if expected_ratio == 2.61 else 873.0,
                "samples": 104.0,
            },
            "12": {"p50": 500.0, "p90": 1200.0, "samples": 104.0},
            "15": {"p50": 500.0, "p90": last_p90, "samples": 104.0},
        }
        trend = hourly_trend(by_hour, key="p90")
        assert trend is not None
        assert trend["ratio"] == expected_ratio
        assert trend["drift"] is True


def test_the_over_budget_ratio_is_the_axis_that_moved_fifteen_fold() -> None:
    trend = hourly_trend(_with_over_ratio(AUG25_1M_OVER_RATIO), key="over_1000_ratio")
    assert trend is not None
    # 15.1배 — 사람이 손으로 센 값과 자릿수까지 같아야 한다.
    assert trend["ratio"] == 15.12
    assert trend["drift"] is True


def test_a_ratio_axis_is_not_rounded_into_nothing() -> None:
    """`round(0.017, 1)`은 0.0이다 — 그러면 배율의 분모가 사라져 「못 잼」으로 빠져나간다."""
    trend = hourly_trend(_with_over_ratio(AUG25_1M_OVER_RATIO), key="over_1000_ratio")
    assert trend is not None
    assert trend["first"] == 0.017
    assert trend["last"] == 0.257


# ------------------------------------------------- ⚠는 세 축의 OR다


def test_the_old_log_alone_still_reports_that_day_clean() -> None:
    """**F-43이 소급해서 고쳐 주지 않는다는 사실을 고정한다.**

    2026-08-25 로그에는 상류 카운터가 없다(그날 코드가 안 실었다). p50·p90 두 축만으로는
    그날이 여전히 조용하다. 이 사실을 테스트로 못 박지 않으면 "F-43을 넣었으니 그날도
    잡힌다"는 말이 검증 없이 굳는다 — 다음 사람이 replay를 돌려 보고서야 알게 된다.
    """
    trends = _intraday_trends({"publish_offset": {"by_hour": AUG25_BY_HOUR}})
    axis = trends["publish_offset"]
    assert axis["drift"] is False
    assert axis["drift_axes"] == []
    # 최상위 필드는 종전대로 p50 축이다(과거 리포트를 읽는 눈과 기존 소비처가 이 이름을 안다).
    assert axis["ratio"] == 1.66


def test_the_same_day_with_the_upstream_counters_does_raise_the_flag() -> None:
    """1-13의 회귀 테스트 그 자체 — **내일부터의 로그**가 어떻게 다른가.

    같은 하루, 같은 p50/p90에 상류 초과율만 얹는다. 중앙값 축은 여전히 `false`이고
    (그날 중앙값은 실제로 안 움직였다) 꼬리 축이 그 하루를 이름으로 지목한다.
    """
    by_hour = {
        hour: {**stats, "over_1000_ratio": AUG25_1M_OVER_RATIO.get(hour, 0.0)}
        for hour, stats in AUG25_BY_HOUR.items()
    }
    axis = _intraday_trends({"publish_offset": {"by_hour": by_hour}})["publish_offset"]
    assert axis["drift"] is True
    assert axis["drift_axes"] == ["over_1000_ratio"]
    # 중앙값 축 단독 판정은 **버리지 않는다** — 「전체가 밀렸다」와 「꼬리만 나빠졌다」는
    # 처방이 다르고, 두 값이 나란히 있어야 그 구분이 리포트에서 읽힌다.
    assert axis["drift_p50_only"] is False


def test_a_genuinely_flat_day_is_still_flat() -> None:
    """오탐 축 — 축을 늘렸다고 조용한 날이 시끄러워지면 그게 늑대소년이다."""
    flat = {
        f"{hour:02d}": {"p50": 300.0, "p90": 700.0, "samples": 100.0, "over_1000_ratio": 0.01}
        for hour in range(9, 16)
    }
    trends = _intraday_trends({"publish_offset": {"by_hour": flat}})
    axis = trends["publish_offset"]
    assert axis["drift"] is False
    assert axis["drift_axes"] == []


# ------------------------------------------------- 0에서 출발하는 하루


def test_a_ratio_starting_at_zero_is_the_worst_day_not_an_unmeasured_one() -> None:
    """배율만 보면 `first == 0`이 `ratio: None`이 되어 **최악의 하루가 조용히 빠져나간다.**

    0 → 0.25는 「못 쟀다」가 아니라 「아침엔 한 건도 안 넘었는데 오후엔 넷 중 하나가
    넘었다」이다. 그래서 이 축만 절대 바닥을 함께 본다.
    """
    ratios = {"09": 0.0, "10": 0.02, "11": 0.05, "12": 0.09, "13": 0.15, "14": 0.20, "15": 0.25}
    trend = hourly_trend(_with_over_ratio(ratios), key="over_1000_ratio")
    assert trend is not None
    assert trend["ratio"] is None  # 배율은 여전히 성립하지 않는다 — 지어내지 않는다
    assert trend["last"] >= INTRADAY_OVER_RATIO_FLOOR
    assert trend["drift"] is True  # 바닥이 잡는다


# ------------------------------------------------- 없는 축을 0으로 채우지 않는다


def test_an_old_log_without_the_tail_counter_drops_the_axis_rather_than_zeroing_it() -> None:
    """F-43 이전 로그에는 `over_1000_ratio`가 없다. **없는 것과 0은 다르다**(L18)."""
    trends = _intraday_trends({"publish_offset": {"by_hour": AUG25_BY_HOUR}})
    axes = trends["publish_offset"]["axes"]
    assert "p50" in axes and "p90" in axes
    assert "over_1000_ratio" not in axes  # 0.0으로 채워 「초과 0건」이라 말하지 않는다


def test_delivery_latency_keeps_the_single_axis_it_has_counters_for() -> None:
    """회선 지연에는 예산 경계가 정의된 적이 없다 — 소비처 없는 축을 미리 만들지 않는다."""
    by_hour = {
        hour: {"p50": value, "p90": value * 2, "samples": 100.0}
        for hour, value in (("09", 0.5), ("15", 0.9))
    }
    trends = _intraday_trends({"delivery_latency": {"by_hour": by_hour}})
    assert set(trends["delivery_latency"]["axes"]) == {"p50"}


# ------------------------------------------------- 상류가 꼬리를 세는가


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
    async def publish(self, topic, message) -> None:  # noqa: D102
        return None


def _engine(now_value: list[datetime], horizons=None):
    from messiah.features.engine import FeatureEngine

    return FeatureEngine(
        "A05609",
        _NullBus(),
        feature_set="v-test",
        horizons=horizons or [Horizon.M1],
        now=lambda: now_value[0],
        mode="live",
    )


def test_grace_is_read_from_the_upstream_constants_not_retyped() -> None:
    """두 곳에 숫자를 적으면 두 곳이 갈라진다 — 이 테스트가 그것을 막는다."""
    from messiah.data.bar_composer import _MAX_CONSTITUENT_WAIT_SECONDS
    from messiah.data.normalizer import MINUTE_CLOSE_GRACE_SECONDS
    from messiah.features.engine import _grace_ms

    assert _grace_ms(Horizon.M1) == MINUTE_CLOSE_GRACE_SECONDS * 1000.0
    for horizon in (Horizon.M3, Horizon.M5, Horizon.M30):
        assert _grace_ms(horizon) == _MAX_CONSTITUENT_WAIT_SECONDS * 1000.0


def test_the_engine_counts_the_tail_per_hour(monkeypatch) -> None:
    """하류가 꼬리를 보려면 상류가 세야 한다 — p50/p90만 넘기면 복원할 방법이 없다."""
    from messiah.core import logging as mlog

    records: list[dict] = []
    monkeypatch.setattr(mlog, "log", lambda tag, msg, **f: records.append({"tag": tag, **f}))
    now = [datetime(2026, 8, 25, 9, 1, tzinfo=KST)]
    engine = _engine(now)
    # 09시 20건 중 1건만 예산 초과 · 15시 20건 중 10건 초과 — 중앙값은 둘 다 300ms다.
    for hour, over in ((9, 1), (15, 10)):
        for minute in range(20):
            vector = _vector(Horizon.M1, datetime(2026, 8, 25, hour, minute, tzinfo=KST))
            delay = 1500.0 if minute < over else 300.0
            now[0] = vector.valid_until + timedelta(milliseconds=delay)
            engine._record_publish_offset(vector)

    assert engine.log_publish_offsets() is not None
    by_hour = records[-1]["by_hour"]
    assert by_hour["09"]["over_1000"] == 1.0
    assert by_hour["09"]["over_1000_ratio"] == 0.05
    assert by_hour["15"]["over_1000_ratio"] == 0.5
    # p50은 두 시간대가 같다 — **그래서 중앙값 축만으로는 이 하루를 못 본다.**
    assert by_hour["09"]["p50"] == by_hour["15"]["p50"]
    assert hourly_trend(by_hour, key="p50")["drift"] is False
    assert hourly_trend(by_hour, key="over_1000_ratio")["drift"] is True


def test_the_grace_breach_is_counted_against_each_horizon_own_boundary(monkeypatch) -> None:
    """1분봉 2,000ms와 상위 5,000ms를 한 임계로 세면 둘 중 하나가 반드시 거짓말이 된다."""
    from messiah.core import logging as mlog

    records: list[dict] = []
    monkeypatch.setattr(mlog, "log", lambda tag, msg, **f: records.append({"tag": tag, **f}))
    now = [datetime(2026, 8, 25, 10, 0, tzinfo=KST)]
    engine = _engine(now, horizons=[Horizon.M1, Horizon.M3])
    # 3,000ms — 1분봉에겐 유예 초과, 3분봉에겐 아직 여유 안이다.
    for horizon in (Horizon.M1, Horizon.M3):
        vector = _vector(horizon, datetime(2026, 8, 25, 10, 0, tzinfo=KST))
        now[0] = vector.valid_until + timedelta(milliseconds=3000.0)
        engine._record_publish_offset(vector)

    assert engine.log_publish_offsets() is not None
    # **태그로 고른다** (2026-08-26 F-66). 종전엔 `records[-1]`이었는데, 유예 여유가 음수인
    # 세션에서는 요약 뒤에 `PublishGraceBreached` 한 줄이 더 붙으므로 마지막이 요약이 아니다.
    # 위치가 아니라 성질로 단언한다(1-18 규율).
    published = next(r for r in records if r["tag"] == "FeaturePublishOffset")
    assert published["by_hour"]["10"]["over_grace"] == 1.0  # 1분봉 한 건만


def test_the_headroom_answers_how_much_is_left_not_how_late_it_was(monkeypatch) -> None:
    """2026-08-25에 이 뺄셈(5,000 − 3,119.7 = 1,880.3)을 사람이 했다. 계기가 하게 한다."""
    from messiah.core import logging as mlog

    records: list[dict] = []
    monkeypatch.setattr(mlog, "log", lambda tag, msg, **f: records.append({"tag": tag, **f}))
    now = [datetime(2026, 8, 25, 10, 0, tzinfo=KST)]
    engine = _engine(now, horizons=[Horizon.M1, Horizon.M3])
    for horizon, delay in ((Horizon.M1, 1200.0), (Horizon.M3, 3119.7)):
        vector = _vector(horizon, datetime(2026, 8, 25, 10, 0, tzinfo=KST))
        now[0] = vector.valid_until + timedelta(milliseconds=delay)
        engine._record_publish_offset(vector)

    assert engine.log_publish_offsets() is not None
    headroom = records[-1]["grace_headroom"]
    assert headroom["by_horizon"]["3m"]["headroom_ms"] == 1880.3
    # **최악은 여유가 가장 적은 계열이다** — 지연이 가장 큰 계열이 아니다. 1분봉은
    # 1,200ms로 더 빨랐지만 경계가 2,000ms라 남은 여유는 800ms로 더 아슬아슬하다.
    assert headroom["worst_horizon"] == "1m"
    assert headroom["worst_headroom_ms"] == 800.0
