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


def _engine(now_value: list[datetime], *, skew_seconds=None, horizons=None, mode="live"):
    from messiah.features.engine import FeatureEngine

    return FeatureEngine(
        "A05609",
        _NullBus(),
        feature_set="v-test",
        horizons=horizons or [Horizon.M1],
        now=lambda: now_value[0],
        clock_skew_seconds=None if skew_seconds is None else (lambda: skew_seconds),
        # 이 파일의 관심사는 **라이브 경보 축**이다 — 기본값 `replay`는 그 축을 통째로
        # 끄므로(2026-08-24 F-28) 여기서는 명시적으로 라이브라고 말한다.
        mode=mode,
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


# ------------------------------- F-15 ③ · F-8 · 예산 초과와 루프 정체를 가른다
#
# **재는 값이 바뀌었다** (2026-08-24 F-21). 종전 입력은 `publish_offset_ms`(대기 포함)에
# 합성 스케줄러 위상의 4배(2,000ms)를 대조했고, 이제는 `bar_to_publish_ms`(봉 도착 →
# 발행, monotonic)에 실측에서 온 절대 예산 `_PUBLISH_SLA_MS`를 대조한다.


def _sla_ms() -> float:
    from messiah.features.engine import _PUBLISH_SLA_MS

    return _PUBLISH_SLA_MS


def test_a_normal_publish_does_not_cry(monkeypatch) -> None:
    """**실측 분포 전체가 조용해야 한다** (2026-08-24 F-21).

    2거래일 598건의 순수 계산 시간은 p50 111ms · p90 289ms · p99 479ms · 최대 602ms였다.
    예산 1,000ms는 그 최대 위로 66% 여유다 — 정상적인 하루는 한 줄도 안 나온다.
    """
    from messiah.core import logging as mlog

    records: list[dict] = []
    monkeypatch.setattr(mlog, "log", lambda tag, msg, **f: records.append({"tag": tag, **f}))
    bar_open = datetime(2026, 8, 21, 10, 0, tzinfo=KST)
    vector = _vector(Horizon.M3, bar_open)
    engine = _engine([vector.valid_until + timedelta(milliseconds=775)], horizons=[Horizon.M3])
    records.clear()  # 엔진 생성이 남기는 `FeatureSetUnregistered`는 이 테스트의 관심사가 아니다
    for elapsed in (111.0, 289.0, 479.0, 602.0):
        engine._note_publish_sla(vector, elapsed)

    assert [r["tag"] for r in records] == []


def test_the_old_offset_would_have_cried_every_cycle() -> None:
    """종전 기준이 왜 못 쓰는 값이었는지를 테스트가 기억한다.

    3m~30m의 발행 오프셋에는 합성 스케줄러 위상 0.5초가 **구조적으로** 들어 있어
    500ms 아래로 내려갈 수 없다 — 2거래일 598건 중 500ms 미만이 **0건**이고 최소가
    532ms였다. 값이 자기 자신을 더한 수와 비교되고 있었다.
    """
    from messiah.data.bar_composer import _COMPOSE_SCHEDULER_PHASE_SECONDS

    phase_ms = _COMPOSE_SCHEDULER_PHASE_SECONDS * 1000.0
    measured_minimums = {"3m": 543.0, "5m": 556.0, "10m": 534.0, "15m": 532.0, "30m": 561.0}
    for horizon, floor in measured_minimums.items():
        assert floor > phase_ms, f"{horizon}는 위상보다 작아질 수 없다"


def test_one_late_horizon_is_a_grace_breach_not_a_stall(monkeypatch) -> None:
    """한 Horizon이 늦은 것과 루프가 멈춘 것은 **다른 사건**이고 처방이 다르다."""
    from messiah.core import logging as mlog

    records: list[dict] = []
    monkeypatch.setattr(mlog, "log", lambda tag, msg, **f: records.append({"tag": tag, **f}))
    bar_open = datetime(2026, 8, 21, 10, 0, tzinfo=KST)
    vector = _vector(Horizon.M3, bar_open)
    engine = _engine([bar_open], horizons=[Horizon.M3])
    records.clear()
    engine._note_publish_sla(vector, _sla_ms() + 1.0)
    engine._flush_publish_stall()

    assert [r["tag"] for r in records] == ["PublishGraceExceeded"]
    assert records[0]["horizon"] == "3m"
    assert records[0]["sla_ms"] == _sla_ms()
    assert records[0]["bar_to_publish_ms"] == _sla_ms() + 1.0


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
        engine._note_publish_sla(vector, _sla_ms() + 3_000.0)
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
        engine._note_publish_sla(vector, _sla_ms() + 2_000.0)

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
    engine._note_publish_sla(vector, _sla_ms() + 2_000.0)
    assert records == []

    engine.log_publish_offsets()

    assert "PublishGraceExceeded" in [r["tag"] for r in records]


def test_sla_threshold_reads_the_engine_constant() -> None:
    """**숫자를 새로 쓰지 않는다** (F-15 ②) — 리포트가 채점하는 값과 엔진이 경보하는
    값이 갈리면, 화면은 한 숫자를 말하고 채점은 다른 숫자로 하는 상태가 된다."""
    from messiah.features.engine import _PUBLISH_SLA_MS
    from messiah.ops.integrity_report import publish_sla_ms

    assert publish_sla_ms() == _PUBLISH_SLA_MS


def test_the_composer_phase_is_no_longer_a_deadline() -> None:
    """합성 스케줄러 위상을 **판정에 쓰는 곳이 하나도 없어야 한다** (2026-08-24 F-21).

    이름이 `_BOUNDARY_GRACE_SECONDS`이던 시절 세 소비처가 그것을 마감 시한으로 읽었고,
    그 오독이 6거래일 연속 경고의 원인이었다.
    """
    from pathlib import Path

    for path in (
        Path("src/messiah/features/engine.py"),
        Path("src/messiah/ops/integrity_report.py"),
        Path("scripts/self_check.py"),
    ):
        source = path.read_text(encoding="utf-8")
        assert "_COMPOSE_SCHEDULER_PHASE_SECONDS" not in source, f"{path}가 위상을 읽는다"
        assert "_BOUNDARY_GRACE_SECONDS" not in source, f"{path}에 옛 이름이 남아 있다"


def test_publish_grace_axis_keeps_the_shape_and_drops_the_verdict() -> None:
    """**유예 대조를 걷어냈다** (2026-08-24 F-21). 남는 것은 종단 지연의 모양뿐이다."""
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
    assert "grace_ms" not in axis and "exceeded" not in axis
    assert "exceeds_grace" not in axis["horizons"]["3m"]
    assert axis["horizons"]["3m"]["p50_ms"] == 586.0
    assert axis["horizons"]["1m"]["axis"] == "exchange_vs_local"


def test_publish_grace_axis_is_none_without_the_horizon_axis() -> None:
    """못 잰 것과 유예를 지킨 것은 다르다 — 재료가 없으면 축 자체를 만들지 않는다(L18)."""
    from messiah.ops.integrity_report import _publish_grace_axis

    assert _publish_grace_axis(None) is None
    assert _publish_grace_axis({"p50": 500.0}) is None


def test_a_suspended_process_is_not_a_publish_delay(monkeypatch) -> None:
    """**상한을 넘는 경과는 「느린 것」이 아니다** (2026-08-21 → 2026-08-24 F-21 개정).

    종전에 이 상한이 막던 것은 리플레이의 벽시계 오프셋(수십억 ms)이었다. 재는 값이
    monotonic 경과로 바뀌면서 그 경로는 구조적으로 사라졌지만(리플레이도 계산은 빠르다),
    상한 자체는 그대로 옳다 — 한 시간짜리 「계산」은 프로세스가 멈췄다 깨어난 것이고,
    그것을 발행 예산 경보로 올리면 진짜 신호가 묻힌다.

    다만 **값 자체는 버리지 않는다** — 세션 요약에는 그대로 실린다.
    """
    from messiah.core import logging as mlog
    from messiah.features.engine import _PUBLISH_OFFSET_LIVE_CEILING_MS

    records: list[dict] = []
    monkeypatch.setattr(mlog, "log", lambda tag, msg, **f: records.append({"tag": tag, **f}))
    bar_open = datetime(2026, 8, 21, 10, 0, tzinfo=KST)
    vector = _vector(Horizon.M5, bar_open)
    engine = _engine([datetime(2026, 8, 21, 10, 5, tzinfo=KST)], horizons=[Horizon.M5])
    records.clear()

    engine._note_publish_sla(vector, _PUBLISH_OFFSET_LIVE_CEILING_MS + 1.0)
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

    engine._note_publish_sla(vector, 5_528.8)
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
    engine._note_publish_sla(stalled, _sla_ms() + 3_000.0)
    assert records == [], "군집이 방금 열렸다 — 아직 닫지 않는다"

    # 창의 2배가 지난 뒤 **정상** 발행 하나가 들어온다. 종전에는 이 줄이 아무것도 안 했다.
    clock[0] += (engine_module._PUBLISH_STALL_FLUSH_MS / 1000.0) + 0.001
    now[0] = confirm + timedelta(minutes=1)
    healthy = _vector(Horizon.M1, confirm + timedelta(seconds=30))
    engine._note_publish_sla(healthy, 120.0)

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
        engine._note_publish_sla(vector, _sla_ms() + 3_000.0 + index)

    engine._flush_publish_stall()
    assert [r["tag"] for r in records] == ["PublishLoopStalled"], "한 줄이어야 한다"
    assert records[-1]["horizons"] == ["1m", "3m", "5m"]


# ------------------------------- F-28 · 장후 배치가 라이브 경보 문구를 찍지 않는다


def test_replay_mode_does_not_cry_at_all(monkeypatch) -> None:
    """**2026-08-24 장후 배치가 라이브와 글자 하나 다르지 않은 경보를 11줄 찍었다**
    (이상점 1-16).

    값의 크기로 거르던 상한(1시간)이 못 잡은 이유는 **당일 재합성**이다 — 그날 봉을
    다시 흘리면 오프셋이 몇 분 단위라 상한 안쪽에 들어온다. 판별의 근거를
    「값이 얼마나 큰가」가 아니라 「지금이 라이브 세션인가」로 바꾼다.
    """
    from messiah.core import logging as mlog

    records: list[dict] = []
    monkeypatch.setattr(mlog, "log", lambda tag, msg, **f: records.append({"tag": tag, **f}))
    confirm = datetime(2026, 8, 24, 10, 0, tzinfo=KST)
    engine = _engine([confirm], horizons=[Horizon.M3], mode="replay")
    records.clear()

    vector = _vector(Horizon.M3, confirm - timedelta(seconds=HORIZON_SECONDS[Horizon.M3]))
    engine._note_publish_sla(vector, _sla_ms() + 5_000.0)
    engine._flush_publish_stall()

    assert [r["tag"] for r in records] == [], "리플레이는 경보 축을 아예 안 탄다"


def test_replay_still_keeps_the_offsets_for_the_session_summary() -> None:
    """**값은 버리지 않는다** — 경보 축에서만 뺀다. 세션 요약은 그대로 나온다."""
    bar_open = datetime(2026, 8, 24, 10, 0, tzinfo=KST)
    vector = _vector(Horizon.M1, bar_open)
    engine = _engine([vector.valid_until + timedelta(milliseconds=4_200)], mode="replay")
    offset, _axis, _skew = engine._record_publish_offset(vector)
    assert offset == pytest.approx(4_200.0, abs=0.1)
    assert engine._publish_offsets, "리플레이에서도 분포는 쌓인다"


def test_the_safe_default_is_replay() -> None:
    """기본을 `live`로 두면 새 호출부가 조용히 라이브로 취급된다.

    2026-08-24의 사고가 정확히 그 형태였다 — 아무도 "이건 리플레이다"라고 말할
    수단이 없었다.
    """
    from messiah.features.engine import FeatureEngine

    engine = FeatureEngine("A05609", _NullBus(), feature_set="v-test", horizons=[Horizon.M1])
    assert engine._live is False


def test_an_unknown_mode_is_rejected_not_guessed() -> None:
    from messiah.features.engine import FeatureEngine

    with pytest.raises(ValueError, match="mode"):
        FeatureEngine("A05609", _NullBus(), feature_set="v-test", mode="배치")


def test_the_live_entrypoint_declares_itself() -> None:
    """라이브 경로가 **명시적으로** 선언하는지는 소스로 확인한다 —
    `run_l1_daily.py`의 `__main__`은 Docker와 KIS를 건드리므로 import하지 않는다
    (`tests/test_non_trading_day_gate.py`와 같은 규율)."""
    from pathlib import Path

    source = Path("scripts/run_l1_daily.py").read_text(encoding="utf-8")
    assert 'mode="live"' in source


# --------- 유예 여유가 음수면 운다 (2026-08-26 F-66 · 이상점 1-11)
#
# 2026-08-26이 `grace_headroom` 계기의 첫날이었고 첫 값이 곧바로 음수였다 — 1분봉 최악
# −3,596ms · 유예 초과 14건. 그런데 그날 경고는 **0건**이다. `PublishGraceExceeded`는
# 2026-08-24 F-21 이후 **예산**(대기 제외)을 재므로 유예를 넘긴 회차가 그 축에 안 걸린다.


def _run_session(monkeypatch, delay_ms: float, *, count: int = 3):
    from messiah.core import logging as mlog

    records: list[dict] = []
    monkeypatch.setattr(
        mlog, "log", lambda tag, msg, **f: records.append({"tag": tag, "msg": msg, **f})
    )
    now = [datetime(2026, 8, 26, 13, 0, tzinfo=KST)]
    engine = _engine(now)
    for minute in range(count):
        bar_open = datetime(2026, 8, 26, 13, minute, tzinfo=KST)
        vector = _vector(Horizon.M1, bar_open)
        now[0] = vector.valid_until + timedelta(milliseconds=delay_ms)
        engine._record_publish_offset(vector)
    stats = engine.log_publish_offsets()
    return engine, records, stats


def test_negative_grace_headroom_raises_a_warning(monkeypatch) -> None:
    """1분봉 유예 2,000ms를 넘겨 발행한 세션 — 여유가 음수다."""
    _engine_, records, _stats = _run_session(monkeypatch, 5596.3)

    breached = [r for r in records if r["tag"] == "PublishGraceBreached"]
    assert len(breached) == 1, "세션당 한 줄이다 — 회차마다 울면 아무도 안 본다"
    assert breached[0]["worst_horizon"] == "1m"
    assert breached[0]["headroom_ms"] < 0
    # 「몇 시에 몰렸나」가 원인 추적의 첫 질문이므로 시간대 분포를 동봉한다.
    assert breached[0]["over_grace_by_hour"] == {"13": 3.0}


def test_positive_grace_headroom_stays_silent(monkeypatch) -> None:
    """여유가 남은 날에 울면 매일 뜨는 경고가 되어 진짜 음수인 날을 가린다."""
    _engine_, records, _stats = _run_session(monkeypatch, 880.0)

    assert not [r for r in records if r["tag"] == "PublishGraceBreached"]


def test_grace_warning_does_not_change_the_offset_summary(monkeypatch) -> None:
    """**판정 불변** — 경보를 붙였다고 발행도 통계도 달라지지 않는다.

    이것은 게이트가 아니라 경보다(R18 대상 아님). 같은 입력에서 `FeaturePublishOffset`이
    내는 값이 경보 유무와 무관하게 같아야 한다.
    """
    _e1, loud, stats_loud = _run_session(monkeypatch, 5596.3)
    _e2, quiet, stats_quiet = _run_session(monkeypatch, 880.0)

    for records, stats in ((loud, stats_loud), (quiet, stats_quiet)):
        offsets = [r for r in records if r["tag"] == "FeaturePublishOffset"]
        assert len(offsets) == 1
        assert offsets[0]["measured"] is True
        assert stats is not None and stats["samples"] == 3
    # 경보가 붙은 쪽에서도 요약은 **먼저** 나간다 — 순서가 바뀌면 요약이 유실될 수 있다.
    tags = [r["tag"] for r in loud]
    assert tags.index("FeaturePublishOffset") < tags.index("PublishGraceBreached")


def test_grace_breach_tag_has_exactly_one_severity() -> None:
    """R6 — 태그 1개 = 심각도 1개. 등록 안 된 태그는 로거가 거절한다."""
    import logging as _logging

    from messiah.core.logging import TAG_LEVELS

    assert TAG_LEVELS["PublishGraceBreached"] == _logging.WARNING


def test_replay_does_not_ride_the_grace_alert_axis(monkeypatch) -> None:
    """옛 하루를 재생할 때마다 이미 아는 사실로 울면 경보의 값이 떨어진다 (2026-08-24 F-28).

    요약 `FeaturePublishOffset`은 **기록**이므로 리플레이에서도 그대로 나간다 — 사라지는
    것은 경보뿐이고, 사실은 하나도 안 사라진다.
    """
    from messiah.core import logging as mlog

    records: list[dict] = []
    monkeypatch.setattr(
        mlog, "log", lambda tag, msg, **f: records.append({"tag": tag, "msg": msg, **f})
    )
    now = [datetime(2026, 8, 26, 13, 0, tzinfo=KST)]
    engine = _engine(now, mode="replay")
    for minute in range(3):
        bar_open = datetime(2026, 8, 26, 13, minute, tzinfo=KST)
        vector = _vector(Horizon.M1, bar_open)
        now[0] = vector.valid_until + timedelta(milliseconds=5596.3)
        engine._record_publish_offset(vector)
    assert engine.log_publish_offsets() is not None

    tags = [r["tag"] for r in records]
    assert "FeaturePublishOffset" in tags
    assert "PublishGraceBreached" not in tags


# --------- 번진 범위를 목록으로 남긴다 (2026-08-31 F-80 · 이상점 1-10)
#
# 2026-08-31에 처음으로 1m 밖(3m)이 음수로 넘어갔다. 최악은 여전히 1m이라 요약 한 줄은
# 전날과 같은 모양이었고, 「한 계열의 사건」이 「두 계열의 사건」이 된 것을 아무도 못 봤다.


def _run_two_horizon_session(monkeypatch, delays_ms: dict):
    """Horizon마다 다른 지연으로 한 세션을 돌린다 — 어떤 계열이 넘겼는지가 관심사다."""
    from messiah.core import logging as mlog

    records: list[dict] = []
    monkeypatch.setattr(
        mlog, "log", lambda tag, msg, **f: records.append({"tag": tag, "msg": msg, **f})
    )
    now = [datetime(2026, 8, 31, 13, 0, tzinfo=KST)]
    engine = _engine(now, horizons=[Horizon.M1, Horizon.M3])
    for horizon, delay_ms in delays_ms.items():
        for minute in range(3):
            bar_open = datetime(2026, 8, 31, 13, minute * 3, tzinfo=KST)
            vector = _vector(horizon, bar_open)
            now[0] = vector.valid_until + timedelta(milliseconds=delay_ms)
            engine._record_publish_offset(vector)
    engine.log_publish_offsets()
    return records


def test_every_breached_horizon_is_listed_not_just_the_worst(monkeypatch) -> None:
    """1m −2,926ms · 3m −160ms인 날 — 목록에 **둘 다** 있어야 한다."""
    records = _run_two_horizon_session(monkeypatch, {Horizon.M1: 4926.0, Horizon.M3: 5160.0})

    breached = [r for r in records if r["tag"] == "PublishGraceBreached"]
    assert len(breached) == 1
    assert sorted(breached[0]["breached_horizons"]) == ["1m", "3m"]
    # 사람이 첫 줄만 읽어도 몇 종류인지 알아야 한다 — 페이로드를 열게 만들면 안 된다.
    assert "음수 Horizon 2개" in breached[0]["msg"]
    # 최악 한 건은 **그대로 남는다** — 이 값을 읽는 쪽(무결성 채점)이 있다.
    assert breached[0]["worst_horizon"] == "1m"


def test_only_the_breaching_horizon_is_listed(monkeypatch) -> None:
    """3m이 여유 안에 있으면 목록에 안 들어간다 — 목록이 「전 Horizon」이 되면 뜻이 없다."""
    records = _run_two_horizon_session(monkeypatch, {Horizon.M1: 4926.0, Horizon.M3: 1200.0})

    breached = [r for r in records if r["tag"] == "PublishGraceBreached"]
    assert breached[0]["breached_horizons"] == ["1m"]
    assert "음수 Horizon 1개" in breached[0]["msg"]


def test_breached_horizons_does_not_change_the_verdict(monkeypatch) -> None:
    """**판정 불변** — 목록을 붙였다고 우는 조건도 요약 통계도 달라지지 않는다.

    우는 조건은 여전히 `worst_headroom_ms < 0` 하나이며, 여유가 남은 날은 조용하다.
    """
    quiet = _run_two_horizon_session(monkeypatch, {Horizon.M1: 880.0, Horizon.M3: 1200.0})
    assert not [r for r in quiet if r["tag"] == "PublishGraceBreached"]
    summary = [r for r in quiet if r["tag"] == "FeaturePublishOffset"]
    assert len(summary) == 1 and summary[0]["measured"] is True
    # 기존 필드가 하나도 사라지지 않았는지 — 읽는 쪽이 있는 값들이다.
    loud = [
        r
        for r in _run_two_horizon_session(monkeypatch, {Horizon.M1: 4926.0, Horizon.M3: 5160.0})
        if r["tag"] == "PublishGraceBreached"
    ]
    for key in ("worst_horizon", "headroom_ms", "by_horizon", "over_grace_by_hour"):
        assert key in loud[0], key
