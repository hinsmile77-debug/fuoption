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


def _engine(now_value: list[datetime], *, skew_seconds=None, horizons=None):
    from messiah.features.engine import FeatureEngine

    return FeatureEngine(
        "A05609",
        _NullBus(),
        feature_set="v-test",
        horizons=horizons or [Horizon.M1],
        now=lambda: now_value[0],
        clock_skew_seconds=None if skew_seconds is None else (lambda: skew_seconds),
    )


def test_offset_is_measured_against_bar_confirm_not_wall_clock() -> None:
    """되감기 모호성을 없애는 것이 이 축의 요점이다 — 확정 시각과의 차이를 직접 잰다."""
    bar_open = datetime(2026, 8, 20, 10, 0, tzinfo=KST)
    vector = _vector(Horizon.M1, bar_open)
    now = [vector.valid_until + timedelta(milliseconds=140.4)]
    engine = _engine(now)
    offset, axis, skew_ms = engine._record_publish_offset(vector)
    assert offset == pytest.approx(140.4, abs=0.1)
    assert axis == "exchange_vs_local"  # 1m은 두 축을 섞는다 (2026-08-21 F-12)
    assert skew_ms is None  # 스큐 원천이 없으면 보정하지 않는다 — 0으로 때우지 않는다


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
    offset, _axis, _skew = engine._record_publish_offset(vector)
    assert offset == pytest.approx(-152.1, abs=0.1)


def test_missing_valid_until_is_unmeasured_not_zero() -> None:
    bar_open = datetime(2026, 8, 20, 10, 0, tzinfo=KST)
    vector = _vector(Horizon.M1, bar_open).model_copy(update={"valid_until": None})
    engine = _engine([bar_open])
    offset, axis, skew_ms = engine._record_publish_offset(vector)
    assert offset is None
    assert axis == "exchange_vs_local"  # 못 잰 것과 축이 무엇인지는 별개 사실이다
    assert skew_ms is None


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


# --------------------------------------- 계단 감지 (2026-08-20 장후 F-E 승격분)
#
# 그날 발행 오프셋은 **선형 악화가 아니라 11시 계단 + 고원**이었다. 09→15시 회귀직선은
# 그 계단을 완만한 상승으로 뭉갠다 — 기울기만 보면 「종일 조금씩 나빠졌다」로 읽히고,
# 그건 처방이 다른 이야기다(점진 악화면 자원, 계단이면 **그 시각에 무슨 일이 있었나**).


def test_step_is_detected_where_the_level_jumps() -> None:
    trend = hourly_trend(
        _hours(
            {
                "09": (74.8, 60),
                "10": (140.4, 60),
                "11": (334.8, 60),
                "12": (664.1, 60),
                "13": (652.6, 60),
                "14": (884.8, 60),
            }
        )
    )
    assert trend is not None
    assert trend["step_detected"] is not None, "계단이 있는데 못 잡으면 기울기와 다를 게 없다"


def test_a_flat_day_has_no_step() -> None:
    trend = hourly_trend(
        _hours({"09": (600.0, 60), "10": (610.0, 60), "11": (598.0, 60), "12": (605.0, 60)})
    )
    assert trend is not None
    assert trend["step_detected"] is None


def test_a_single_noisy_hour_is_not_a_step() -> None:
    """인접 두 점만 보면 잡음 한 점이 계단이 된다 — 앞뒤 **구간 중앙값**을 본다."""
    trend = hourly_trend(
        _hours(
            {
                "09": (600.0, 60),
                "10": (605.0, 60),
                "11": (2400.0, 60),  # 한 시간만 튐
                "12": (598.0, 60),
                "13": (602.0, 60),
            }
        )
    )
    assert trend is not None
    assert trend["step_detected"] is None


def test_too_few_hours_is_not_judged() -> None:
    trend = hourly_trend(_hours({"09": (100.0, 60), "10": (500.0, 60)}))
    assert trend is not None
    assert trend["step_detected"] is None, "두 점으로는 계단과 추세를 못 가른다"


# ------------------------------------------------- F-12 · 1m 전용 롤링 스큐 보정


def test_one_minute_offset_is_corrected_by_the_rolling_skew() -> None:
    """**1m만 두 축을 섞는다** (2026-08-21 F-12).

    봉 확정은 거래소 시각 경계, 발행 시각은 로컬 시계다. 스큐가 +800ms면 봉이 실제로
    닫힌 순간의 로컬 시계는 확정 시각보다 800ms **이르다** — 그래서 경과는 원시값에
    스큐를 **더한** 값이다. 리포트 문안의 뺄셈 부호는 그 검증 예시(+300ms → +1,100ms)와
    어긋났고, 실측 대조(1m 중앙값 −3.5ms + 스큐 798ms ≈ 795ms로 3m 586ms·5m 596ms와
    같은 대역)도 더하기를 지지한다.
    """
    bar_open = datetime(2026, 8, 21, 10, 0, tzinfo=KST)
    vector = _vector(Horizon.M1, bar_open)
    now = [vector.valid_until + timedelta(milliseconds=300)]
    engine = _engine(now, skew_seconds=0.8)

    offset, axis, skew_ms = engine._record_publish_offset(vector)
    assert offset == pytest.approx(1100.0, abs=0.1)
    assert axis == "exchange_vs_local"
    assert skew_ms == pytest.approx(800.0, abs=0.1)


def test_three_minute_offset_is_never_touched_by_the_skew() -> None:
    """**3m 이상에 보정을 흘리면 이 fix가 원래 문제보다 나쁘다** (F-12 회귀 위험 ㉠).

    3m+ 는 1m 봉이 **도착한 시점**을 기점으로 합성·발행하므로 양쪽 다 로컬 시계다 —
    축이 하나다. 2026-08-21 실측 이동폭이 3m +29ms · 5m −1ms · 10m +72ms로 사실상
    미동이 없었고 음수는 0건이었다. 여기에 800ms를 주입하면 없던 계통오차가 생긴다.
    """
    bar_open = datetime(2026, 8, 21, 10, 0, tzinfo=KST)
    for horizon in (Horizon.M3, Horizon.M5, Horizon.M10, Horizon.M15, Horizon.M30):
        vector = _vector(horizon, bar_open)
        now = [vector.valid_until + timedelta(milliseconds=300)]
        engine = _engine(now, skew_seconds=0.8, horizons=[horizon])

        offset, axis, skew_ms = engine._record_publish_offset(vector)
        assert offset == pytest.approx(300.0, abs=0.1), horizon
        assert axis == "local_only", horizon
        assert skew_ms is None, horizon


def test_unmeasured_skew_leaves_the_offset_uncorrected() -> None:
    """스큐를 못 재면 **보정하지 않는다.** 0으로 때우면 "보정했다"와 구분되지 않는다(L18)."""
    bar_open = datetime(2026, 8, 21, 10, 0, tzinfo=KST)
    vector = _vector(Horizon.M1, bar_open)
    now = [vector.valid_until + timedelta(milliseconds=300)]
    engine = _engine(now, skew_seconds=None)
    engine._clock_skew_seconds = lambda: None  # 표본 부족 — 콜러블은 있는데 값이 없다

    offset, axis, skew_ms = engine._record_publish_offset(vector)
    assert offset == pytest.approx(300.0, abs=0.1)
    assert axis == "exchange_vs_local"
    assert skew_ms is None


def test_summary_reports_drift_per_horizon(monkeypatch) -> None:
    """하루 한 표는 "1m만 848ms 미끄러진다"를 볼 수 없다 — Horizon 축이 있어야 한다."""
    from messiah.core import logging as mlog

    records: list[dict] = []
    monkeypatch.setattr(mlog, "log", lambda tag, msg, **f: records.append({"tag": tag, **f}))
    now = [datetime(2026, 8, 21, 9, 0, tzinfo=KST)]
    engine = _engine(now, horizons=[Horizon.M1, Horizon.M3])

    for hour, delay_1m in ((9, -495.0), (15, 353.0)):
        for minute in (0, 1, 2):
            v1 = _vector(Horizon.M1, datetime(2026, 8, 21, hour, minute, tzinfo=KST))
            now[0] = v1.valid_until + timedelta(milliseconds=delay_1m)
            engine._record_publish_offset(v1)
            v3 = _vector(Horizon.M3, datetime(2026, 8, 21, hour, minute * 3, tzinfo=KST))
            now[0] = v3.valid_until + timedelta(milliseconds=586.0)
            engine._record_publish_offset(v3)

    engine.log_publish_offsets()
    by_horizon = records[-1]["by_horizon"]
    assert by_horizon["1m"]["axis"] == "exchange_vs_local"
    assert by_horizon["1m"]["day_drift_ms"] == pytest.approx(848.0, abs=0.1)
    assert by_horizon["1m"]["negative"] == 3.0
    assert by_horizon["3m"]["axis"] == "local_only"
    assert by_horizon["3m"]["day_drift_ms"] == pytest.approx(0.0, abs=0.1)


# ------------------------------- F-15 ③ · F-8 · 유예 초과와 루프 정체를 가른다


def _grace_alert_ms() -> float:
    from messiah.features.engine import _PUBLISH_GRACE_ALERT_MULTIPLE, _boundary_grace_ms

    return _boundary_grace_ms() * _PUBLISH_GRACE_ALERT_MULTIPLE


def test_a_normal_publish_does_not_cry(monkeypatch) -> None:
    """유예를 조금 넘는 것은 매일 있는 일이다 — 2026-08-21 실측 중앙값이 3m 586ms ·
    30m 775ms였다. 그걸 전부 경보로 올리면 **경보가 닳는다**(1-15와 같은 형태)."""
    from messiah.core import logging as mlog

    records: list[dict] = []
    monkeypatch.setattr(mlog, "log", lambda tag, msg, **f: records.append({"tag": tag, **f}))
    bar_open = datetime(2026, 8, 21, 10, 0, tzinfo=KST)
    vector = _vector(Horizon.M3, bar_open)
    engine = _engine([vector.valid_until + timedelta(milliseconds=775)], horizons=[Horizon.M3])
    records.clear()  # 엔진 생성이 남기는 `FeatureSetUnregistered`는 이 테스트의 관심사가 아니다
    engine._note_publish_grace(vector, 775.0)

    assert [r["tag"] for r in records] == []


def test_one_late_horizon_is_a_grace_breach_not_a_stall(monkeypatch) -> None:
    """한 Horizon이 늦은 것과 루프가 멈춘 것은 **다른 사건**이고 처방이 다르다."""
    from messiah.core import logging as mlog

    records: list[dict] = []
    monkeypatch.setattr(mlog, "log", lambda tag, msg, **f: records.append({"tag": tag, **f}))
    bar_open = datetime(2026, 8, 21, 10, 0, tzinfo=KST)
    vector = _vector(Horizon.M3, bar_open)
    engine = _engine([bar_open], horizons=[Horizon.M3])
    records.clear()
    engine._note_publish_grace(vector, _grace_alert_ms() + 1.0)
    engine._flush_publish_stall()

    assert [r["tag"] for r in records] == ["PublishGraceExceeded"]
    assert records[0]["horizon"] == "3m"


def test_several_horizons_at_the_same_instant_are_one_stall(monkeypatch) -> None:
    """**같은 순간에 여러 Horizon이 함께 늦으면 루프가 멈춘 것이다** (2026-08-21 F-8).

    실측 14군집 중 5군집이 이 형태였다. **한 줄이어야 한다** — 즉시 남기면 3개짜리
    군집이 세 줄이 되어 「묶는다」는 목적 자체가 사라진다. 그래서 순간이 바뀌거나
    세션이 끝날 때 flush한다.
    """
    from messiah.core import logging as mlog

    records: list[dict] = []
    monkeypatch.setattr(mlog, "log", lambda tag, msg, **f: records.append({"tag": tag, **f}))
    # 09:00:00 — 1m·3m·5m 경계가 한 순간에 겹친다(실측에서 관측된 형태).
    confirm = datetime(2026, 8, 21, 9, 0, tzinfo=KST)
    engine = _engine([confirm], horizons=[Horizon.M1, Horizon.M3, Horizon.M5])
    records.clear()
    for horizon in (Horizon.M1, Horizon.M3, Horizon.M5):
        seconds = HORIZON_SECONDS[horizon]
        vector = _vector(horizon, confirm - timedelta(seconds=seconds))
        engine._note_publish_grace(vector, _grace_alert_ms() + 3_000.0)
    assert records == [], "순간이 안 끝났으면 아직 남기지 않는다"

    engine._flush_publish_stall()

    assert [r["tag"] for r in records] == ["PublishLoopStalled"], "세 건이 한 줄로 묶인다"
    assert records[-1]["horizons"] == ["1m", "3m", "5m"]
    assert records[-1]["bar_confirm_kst"] == confirm.isoformat()


def test_a_new_instant_flushes_the_previous_cluster(monkeypatch) -> None:
    """다음 순간이 오면 직전 군집을 내보낸다 — 세션 끝까지 쌓아 두면 아무것도 안 남는다."""
    from messiah.core import logging as mlog

    records: list[dict] = []
    monkeypatch.setattr(mlog, "log", lambda tag, msg, **f: records.append({"tag": tag, **f}))
    engine = _engine([datetime(2026, 8, 21, 9, 0, tzinfo=KST)], horizons=[Horizon.M1])
    records.clear()
    for minute in (0, 5):
        vector = _vector(Horizon.M1, datetime(2026, 8, 21, 9, minute, tzinfo=KST))
        engine._note_publish_grace(vector, _grace_alert_ms() + 2_000.0)

    assert [r["tag"] for r in records] == ["PublishGraceExceeded"]
    assert records[0]["bar_confirm_kst"].endswith("09:01:00+09:00")


def test_the_last_cluster_of_the_day_is_flushed_by_the_summary(monkeypatch) -> None:
    """그날 마지막 군집을 안 내보내면 그 한 줄이 영영 안 남는다."""
    from messiah.core import logging as mlog

    records: list[dict] = []
    monkeypatch.setattr(mlog, "log", lambda tag, msg, **f: records.append({"tag": tag, **f}))
    engine = _engine([datetime(2026, 8, 21, 15, 30, tzinfo=KST)], horizons=[Horizon.M1])
    records.clear()
    vector = _vector(Horizon.M1, datetime(2026, 8, 21, 15, 30, tzinfo=KST))
    engine._note_publish_grace(vector, _grace_alert_ms() + 2_000.0)
    assert records == []

    engine.log_publish_offsets()

    assert "PublishGraceExceeded" in [r["tag"] for r in records]


def test_grace_threshold_reads_the_composer_constant() -> None:
    """**숫자를 새로 쓰지 않는다** (F-15 ②) — 자가점검이 인용하는 값과 채점에 쓰는 값이
    갈리면, 화면은 500ms를 말하는데 채점은 다른 숫자로 하는 상태가 된다."""
    from messiah.data.bar_composer import _BOUNDARY_GRACE_SECONDS
    from messiah.features.engine import _boundary_grace_ms
    from messiah.ops.integrity_report import boundary_grace_seconds

    assert _boundary_grace_ms() == _BOUNDARY_GRACE_SECONDS * 1000.0
    assert boundary_grace_seconds() == _BOUNDARY_GRACE_SECONDS


def test_publish_grace_axis_scores_each_horizon_without_judging() -> None:
    """**1단계는 기록만 한다**(R18). 지금 판정하면 3m~30m 전 계열이 매일 breach를 낸다 —
    그것이 사실이지만, 임계 확정은 F-12 실측이 며칠 쌓인 뒤 사람이 한다."""
    from messiah.ops.integrity_report import _publish_grace_axis

    axis = _publish_grace_axis(
        {
            "by_horizon": {
                "1m": {"axis": "exchange_vs_local", "p50": 795.0, "p90": 1200.0, "samples": 409.0},
                "3m": {"axis": "local_only", "p50": 586.0, "p90": 900.0, "samples": 136.0},
                "30m": {"axis": "local_only", "p50": 320.0, "p90": 700.0, "samples": 14.0},
            }
        }
    )
    assert axis is not None
    assert axis["verdict"] == "recorded_only"
    assert axis["grace_ms"] == 500.0
    assert axis["exceeded"] == ["1m", "3m"]  # 30m 320ms는 유예 안쪽
    assert axis["horizons"]["3m"]["over_grace_ms"] == 86.0
    assert axis["horizons"]["30m"]["exceeds_grace"] is False


def test_publish_grace_axis_is_none_without_the_horizon_axis() -> None:
    """못 잰 것과 유예를 지킨 것은 다르다 — 재료가 없으면 축 자체를 만들지 않는다(L18)."""
    from messiah.ops.integrity_report import _publish_grace_axis

    assert _publish_grace_axis(None) is None
    assert _publish_grace_axis({"p50": 500.0}) is None


def test_a_replayed_bar_is_not_a_publish_delay(monkeypatch) -> None:
    """**학습·리플레이를 「유예 초과」라고 부르면 안 된다** (2026-08-21 구현 중 발견).

    `models/trainer.build_feature_vectors()`는 같은 엔진으로 과거 봉을 흘린다. 그때
    `now()`는 벽시계고 `valid_until`은 몇 달 전이라 오프셋이 수십억 ms가 된다 — 첫
    구현에서 학습 테스트가 `발행 유예 초과 — 5m 2377834874ms`를 봉마다 찍었다.
    그것은 「늦게 발행했다」가 아니라 「지금 재생 중이다」이고, 경보로 올리면 진짜 신호가
    그 아래 묻힌다.

    다만 **값 자체는 버리지 않는다** — 세션 요약에는 그대로 실린다.
    """
    from messiah.core import logging as mlog
    from messiah.features.engine import _PUBLISH_OFFSET_LIVE_CEILING_MS

    records: list[dict] = []
    monkeypatch.setattr(mlog, "log", lambda tag, msg, **f: records.append({"tag": tag, **f}))
    bar_open = datetime(2026, 3, 2, 10, 0, tzinfo=KST)
    vector = _vector(Horizon.M5, bar_open)
    engine = _engine([datetime(2026, 8, 21, 10, 0, tzinfo=KST)], horizons=[Horizon.M5])
    records.clear()

    engine._note_publish_grace(vector, _PUBLISH_OFFSET_LIVE_CEILING_MS + 1.0)
    engine._flush_publish_stall()

    assert records == []


def test_a_real_stall_just_under_the_ceiling_still_cries(monkeypatch) -> None:
    """상한이 진짜 정체를 삼키면 안 된다 — 실측 최대는 5.5초였다."""
    from messiah.core import logging as mlog

    records: list[dict] = []
    monkeypatch.setattr(mlog, "log", lambda tag, msg, **f: records.append({"tag": tag, **f}))
    bar_open = datetime(2026, 8, 21, 10, 0, tzinfo=KST)
    vector = _vector(Horizon.M5, bar_open)
    engine = _engine([bar_open], horizons=[Horizon.M5])
    records.clear()

    engine._note_publish_grace(vector, 5_528.8)
    engine._flush_publish_stall()

    assert [r["tag"] for r in records] == ["PublishGraceExceeded"]


# ------------------------------- F-24 · 정체 경보가 다음 정체를 기다리지 않는다


def test_a_stale_cluster_is_flushed_by_time_not_by_the_next_stall(monkeypatch) -> None:
    """**08:56의 정체가 11:25에 기록됐다** (2026-08-24 이상점 1-12).

    군집을 닫는 계기가 「다음 정체의 도착」 하나뿐이었다. 그래서 정체가 드문 날일수록
    경보가 더 늦게 왔다 — 있어야 할 성질의 정반대다. 그날 7군집 중 6건이 3~149분 밀렸고
    중앙 지연이 10분이었다.
    """
    from messiah.core import logging as mlog
    from messiah.features import engine as engine_module

    records: list[dict] = []
    monkeypatch.setattr(mlog, "log", lambda tag, msg, **f: records.append({"tag": tag, **f}))

    confirm = datetime(2026, 8, 24, 8, 56, tzinfo=KST)
    now = [confirm + timedelta(milliseconds=900)]
    engine = _engine(now, horizons=[Horizon.M1, Horizon.M3])
    records.clear()

    clock = [1_000.0]
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock[0])

    stalled = _vector(Horizon.M3, confirm - timedelta(seconds=HORIZON_SECONDS[Horizon.M3]))
    engine._note_publish_grace(stalled, _grace_alert_ms() + 3_000.0)
    assert records == [], "군집이 방금 열렸다 — 아직 닫지 않는다"

    # 창의 2배가 지난 뒤 **정상** 발행 하나가 들어온다. 종전에는 이 줄이 아무것도 안 했다.
    clock[0] += (engine_module._PUBLISH_STALL_FLUSH_MS / 1000.0) + 0.001
    now[0] = confirm + timedelta(minutes=1)
    healthy = _vector(Horizon.M1, confirm + timedelta(seconds=30))
    engine._note_publish_grace(healthy, 120.0)

    assert [r["tag"] for r in records] == ["PublishGraceExceeded"]
    assert records[0]["bar_confirm_kst"] == confirm.isoformat()
    # **경보가 얼마나 늦게 왔는지를 경보 자신이 말한다.**
    assert records[0]["detection_lag_ms"] == pytest.approx(60_000.0, abs=1.0)


def test_a_fresh_cluster_is_not_split_by_a_late_arrival(monkeypatch) -> None:
    """창 안에 도착한 것은 한 줄, 창 밖은 두 줄 — 시간 flush가 군집을 쪼개지 않는다.

    같은 정체가 두 줄로 쪼개지는 것이 애초에 군집화가 막으려던 것이므로, flush 창을
    군집 창의 2배로 두어 여유를 준다.
    """
    from messiah.core import logging as mlog
    from messiah.features import engine as engine_module

    records: list[dict] = []
    monkeypatch.setattr(mlog, "log", lambda tag, msg, **f: records.append({"tag": tag, **f}))

    confirm = datetime(2026, 8, 24, 9, 0, tzinfo=KST)
    engine = _engine([confirm], horizons=[Horizon.M1, Horizon.M3, Horizon.M5])
    records.clear()
    clock = [500.0]
    monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock[0])

    for index, horizon in enumerate((Horizon.M1, Horizon.M3, Horizon.M5)):
        # 창 2배 **안쪽**에서 늦게 도착한다 — 같은 군집이어야 한다.
        clock[0] += (engine_module._PUBLISH_STALL_FLUSH_MS / 1000.0) / 4.0
        vector = _vector(horizon, confirm - timedelta(seconds=HORIZON_SECONDS[horizon]))
        engine._note_publish_grace(vector, _grace_alert_ms() + 3_000.0 + index)

    engine._flush_publish_stall()
    assert [r["tag"] for r in records] == ["PublishLoopStalled"], "한 줄이어야 한다"
    assert records[-1]["horizons"] == ["1m", "3m", "5m"]
