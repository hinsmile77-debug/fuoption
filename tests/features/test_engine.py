from datetime import date, datetime, timedelta

from messiah.core.messages import BarClosed, FeatureVector, HealthLevel, Horizon
from messiah.core.timeutil import KST
from messiah.features.engine import FeatureEngine
from messiah.features.px_core import STATEFUL_FEATURES as PX_STATEFUL_FEATURES
from messiah.features.px_core import WINDOWED_FEATURES as PX_WINDOWED_FEATURES
from messiah.features.vl_core import STATEFUL_FEATURES as VL_STATEFUL_FEATURES
from messiah.features.vl_core import WINDOWED_FEATURES as VL_WINDOWED_FEATURES

_EXPECTED_KEY_COUNT = (
    sum(len(windows) for _, _, windows in PX_WINDOWED_FEATURES)
    + len(PX_STATEFUL_FEATURES)
    + sum(len(windows) for _, _, windows in VL_WINDOWED_FEATURES)
    + len(VL_STATEFUL_FEATURES)
)


class FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, FeatureVector]] = []

    async def publish(self, topic: str, msg: FeatureVector) -> None:
        self.published.append((topic, msg))


def _bar(minute: int, horizon: Horizon = Horizon.M5, c=100, symbol="A05608") -> BarClosed:
    return BarClosed(
        symbol=symbol,
        horizon=horizon,
        bar_open_kst=datetime(2026, 7, 23, 9, 30, tzinfo=KST) + timedelta(minutes=minute),
        o_ticks=c,
        h_ticks=c + 5,
        l_ticks=c - 5,
        c_ticks=c,
        volume=10,
        quality_ok=True,
    )


async def test_handle_bar_publishes_feature_vector(tmp_path):
    bus = FakeBus()
    engine = FeatureEngine("A05608", bus, feature_set="v-test", horizons=[Horizon.M5])

    await engine.handle_bar(_bar(0))

    assert len(bus.published) == 1
    topic, vector = bus.published[0]
    assert topic == "feat.5m.A05608"
    assert isinstance(vector, FeatureVector)
    assert vector.symbol == "A05608"
    assert vector.horizon == Horizon.M5
    assert vector.feature_set == "v-test"


async def test_feature_vector_has_full_key_count():
    bus = FakeBus()
    engine = FeatureEngine("A05608", bus, feature_set="v-test", horizons=[Horizon.M5])

    await engine.handle_bar(_bar(0))

    _, vector = bus.published[0]
    assert len(vector.values) == _EXPECTED_KEY_COUNT


async def test_nan_ratio_is_one_on_first_bar_and_decreases_with_more_history():
    bus = FakeBus()
    engine = FeatureEngine("A05608", bus, feature_set="v-test", horizons=[Horizon.M5])

    await engine.handle_bar(_bar(0))
    first_nan_ratio = bus.published[0][1].nan_ratio
    # px_adx는 데이터 부족 시 None이 아니라 중립값 20.0을 반환하는 계약(px_core.py 참고)이라
    # 정확히 1.0은 아니지만, 워밍업 전혀 안 된 시점이라 거의 전부 None이어야 함
    assert first_nan_ratio > 0.9

    for i in range(1, 10):
        await engine.handle_bar(_bar(i, c=100 + i))
    later_nan_ratio = bus.published[-1][1].nan_ratio
    assert later_nan_ratio < first_nan_ratio  # 히스토리가 쌓일수록 워밍업된 Feature가 늘어남


async def test_slice_based_calculators_produce_real_values_once_warmed(monkeypatch):
    """회귀 테스트(2026-07-26 버그) — `history`는 `collections.deque`인데 `px_vwap_dev`/
    `vl_atr` 등 다수의 계산기가 `bars[-window:]` 슬라이스를 쓴다. deque는 슬라이스를
    지원하지 않아(정수 인덱싱만 가능) `handle_bar()`가 deque를 그대로 넘기면 이 계산기들은
    워밍업 완료 여부와 무관하게 항상 TypeError→None이었다. `list(history)`로 변환한 뒤에는
    실제 값이 나와야 한다 — 변동하는 종가로 40봉을 흘려 슬라이스 기반 계산기 다수가 더 이상
    None이 아님을 확인(정수 인덱싱만 쓰는 px_ret 등과 달리, 이 계산기들은 리스트 변환 전엔
    이 테스트가 실패했다)."""
    bus = FakeBus()
    engine = FeatureEngine("A05608", bus, feature_set="v-test", horizons=[Horizon.M5])

    for i in range(40):
        await engine.handle_bar(_bar(i, c=100 + (i % 7)))

    vector = bus.published[-1][1]
    slice_based_keys = ["px_vwap_dev_5", "px_ema_dev_5", "px_high_dist_5", "vl_atr_5", "vl_rv_5"]
    for key in slice_based_keys:
        assert vector.values[key] is not None, f"{key}가 None — deque 슬라이스 버그 재발 의심"


async def test_valid_until_is_bar_open_plus_horizon_length():
    bus = FakeBus()
    engine = FeatureEngine("A05608", bus, feature_set="v-test", horizons=[Horizon.M5])
    bar = _bar(0)

    await engine.handle_bar(bar)

    vector = bus.published[0][1]
    assert vector.valid_until == bar.bar_open_kst + timedelta(minutes=5)


async def test_ignores_bars_for_other_symbols():
    bus = FakeBus()
    engine = FeatureEngine("A05608", bus, feature_set="v-test", horizons=[Horizon.M5])

    await engine.handle_bar(_bar(0, symbol="OTHER"))

    assert bus.published == []


async def test_ignores_bars_for_unsubscribed_horizons():
    bus = FakeBus()
    engine = FeatureEngine("A05608", bus, feature_set="v-test", horizons=[Horizon.M5])

    await engine.handle_bar(_bar(0, horizon=Horizon.M30))

    assert bus.published == []


async def test_separate_horizons_maintain_independent_rolling_history():
    bus = FakeBus()
    engine = FeatureEngine("A05608", bus, feature_set="v-test", horizons=[Horizon.M1, Horizon.M5])

    for i in range(3):
        await engine.handle_bar(_bar(i, horizon=Horizon.M1))
    await engine.handle_bar(_bar(0, horizon=Horizon.M5))

    m1_history_len = len(engine._history[Horizon.M1])
    m5_history_len = len(engine._history[Horizon.M5])
    assert m1_history_len == 3
    assert m5_history_len == 1


async def test_session_state_updates_only_from_m1_bars():
    bus = FakeBus()
    engine = FeatureEngine("A05608", bus, feature_set="v-test", horizons=[Horizon.M1, Horizon.M5])

    await engine.handle_bar(_bar(0, horizon=Horizon.M5, c=999))  # M5는 세션 상태를 안 건드림
    assert engine._session.session_open_ticks is None

    await engine.handle_bar(_bar(0, horizon=Horizon.M1, c=100))
    assert engine._session.session_open_ticks == 100


async def test_individual_feature_failure_does_not_crash_publish(monkeypatch):
    bus = FakeBus()
    engine = FeatureEngine("A05608", bus, feature_set="v-test", horizons=[Horizon.M5])

    def _boom(*args):
        raise RuntimeError("계산 실패")

    monkeypatch.setattr(
        "messiah.features.px_core.WINDOWED_FEATURES",
        [("px_ret", _boom, (5,))],
    )
    monkeypatch.setattr("messiah.features.px_core.STATEFUL_FEATURES", [])
    monkeypatch.setattr("messiah.features.vl_core.WINDOWED_FEATURES", [])
    monkeypatch.setattr("messiah.features.vl_core.STATEFUL_FEATURES", [])

    await engine.handle_bar(_bar(0))

    assert len(bus.published) == 1
    vector = bus.published[0][1]
    assert vector.values == {"px_ret_5": None}
    assert vector.nan_ratio == 1.0


async def test_publish_failure_is_logged_and_does_not_raise(monkeypatch):
    logged: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "messiah.features.engine.mlog.log", lambda tag, msg, **f: logged.append((tag, msg))
    )

    class FailingBus:
        async def publish(self, topic, msg):
            raise RuntimeError("발행 실패")

    engine = FeatureEngine("A05608", FailingBus(), feature_set="v-test", horizons=[Horizon.M5])

    await engine.handle_bar(_bar(0))  # 예외 없이 끝나야 함

    assert any(tag == "FeaturePublishError" for tag, _ in logged)


async def test_high_nan_ratio_during_warmup_does_not_log_feature_nan(monkeypatch):
    """워밍업 중(히스토리가 아직 _MAX_HISTORY 미만)엔 nan_ratio가 높은 게 정상이라 매 봉마다
    경고하면 안 된다 — 2026-07-24 실제 운영 로그 리뷰 중 발견한 잡음 문제의 회귀 테스트."""
    logged: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "messiah.features.engine.mlog.log", lambda tag, msg, **f: logged.append((tag, msg))
    )
    bus = FakeBus()
    engine = FeatureEngine("A05608", bus, feature_set="v-test", horizons=[Horizon.M5])

    await engine.handle_bar(_bar(0))  # 첫 봉 — nan_ratio=1.0 > 20%이지만 워밍업 중

    assert not any(tag == "FeatureNaN" for tag, _ in logged)


async def test_high_nan_ratio_after_warmup_logs_feature_nan_warning(monkeypatch):
    """워밍업이 끝났어야 할 시점(_MAX_HISTORY개 봉 이상)에도 nan_ratio가 여전히 높으면(=
    계산이 계속 실패 중) 그때는 진짜 문제이므로 경고해야 한다."""
    logged: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "messiah.features.engine.mlog.log", lambda tag, msg, **f: logged.append((tag, msg))
    )
    monkeypatch.setattr("messiah.features.engine._MAX_HISTORY", 3)  # 테스트를 짧게

    def _boom(*args):
        raise RuntimeError("계산 실패")

    monkeypatch.setattr("messiah.features.px_core.WINDOWED_FEATURES", [("px_ret", _boom, (5,))])
    monkeypatch.setattr("messiah.features.px_core.STATEFUL_FEATURES", [])
    monkeypatch.setattr("messiah.features.vl_core.WINDOWED_FEATURES", [])
    monkeypatch.setattr("messiah.features.vl_core.STATEFUL_FEATURES", [])

    bus = FakeBus()
    engine = FeatureEngine("A05608", bus, feature_set="v-test", horizons=[Horizon.M5])

    for i in range(2):  # _MAX_HISTORY(3) 미만 — 아직 경고 안 함
        await engine.handle_bar(_bar(i))
    assert not any(tag == "FeatureNaN" for tag, _ in logged)

    await engine.handle_bar(_bar(2))  # 3번째 봉 — 워밍업 완료 시점, 여전히 100% None이라 경고
    assert any(tag == "FeatureNaN" for tag, _ in logged)


# ------------------------------------------------- NaN 원인 구분: 결측 vs 퇴화 (2026-07-31)


def _break_all_features(monkeypatch) -> None:
    """모든 계산기를 실패시켜 nan_ratio를 1.0으로 만든다 — 이 테스트들이 보려는 건
    "NaN이 났을 때 원인을 어떻게 분류하는가"이지 계산기 정확도가 아니다."""

    def _boom(*args):
        raise RuntimeError("계산 실패")

    monkeypatch.setattr("messiah.features.px_core.WINDOWED_FEATURES", [("px_ret", _boom, (5,))])
    monkeypatch.setattr("messiah.features.px_core.STATEFUL_FEATURES", [])
    monkeypatch.setattr("messiah.features.vl_core.WINDOWED_FEATURES", [])
    monkeypatch.setattr("messiah.features.vl_core.STATEFUL_FEATURES", [])


def _capture_logs(monkeypatch) -> list[tuple[str, str, dict]]:
    logged: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        "messiah.features.engine.mlog.log",
        lambda tag, msg, **f: logged.append((tag, msg, f)),
    )
    return logged


async def test_flat_price_nan_is_reported_as_degenerate_not_missing(monkeypatch):
    """2026-07-31 실측 회귀 — 15:20 이후 1m NaN 33% 경고 15회는 전부 "가격이 14:21부터 마감까지
    51814틱에 고정돼 표준편차 계열이 정의 불가"인 경우였는데, 결측과 같은 문구로 찍혀 매번
    수집 장애를 먼저 의심하게 만들었다."""
    logged = _capture_logs(monkeypatch)
    monkeypatch.setattr("messiah.features.engine._MAX_HISTORY", 5)
    monkeypatch.setattr("messiah.features.engine._DEGENERATE_WINDOW", 3)
    _break_all_features(monkeypatch)
    engine = FeatureEngine("A05608", FakeBus(), feature_set="v-test", horizons=[Horizon.M5])

    for i in range(5):
        await engine.handle_bar(_bar(i, c=51814))  # 종가가 계속 동일

    tags = [tag for tag, _msg, _f in logged]
    assert "FeatureDegenerate" in tags
    assert "FeatureNaN" not in tags
    fields = next(f for tag, _m, f in logged if tag == "FeatureDegenerate")
    assert fields["cause"] == "degenerate"
    assert fields["flat_close_ticks"] == 51814


async def test_moving_price_nan_is_still_reported_as_missing(monkeypatch):
    """가격이 움직이는데도 NaN이면 그건 진짜 데이터 사고다 — 퇴화 분류가 그걸 가리면 안 된다."""
    logged = _capture_logs(monkeypatch)
    monkeypatch.setattr("messiah.features.engine._MAX_HISTORY", 5)
    monkeypatch.setattr("messiah.features.engine._DEGENERATE_WINDOW", 3)
    _break_all_features(monkeypatch)
    engine = FeatureEngine("A05608", FakeBus(), feature_set="v-test", horizons=[Horizon.M5])

    for i in range(5):
        await engine.handle_bar(_bar(i, c=100 + i))  # 매 봉 종가가 다름

    tags = [tag for tag, _msg, _f in logged]
    assert "FeatureNaN" in tags
    assert "FeatureDegenerate" not in tags
    fields = next(f for tag, _m, f in logged if tag == "FeatureNaN")
    assert fields["cause"] == "missing"


async def test_degenerate_classification_needs_a_full_window(monkeypatch):
    """표본이 창을 못 채웠으면 "고정됐다"고 단정하지 않는다 — 정상 시장에서도 두세 봉
    연속 동일가는 흔하다."""
    logged = _capture_logs(monkeypatch)
    monkeypatch.setattr("messiah.features.engine._MAX_HISTORY", 2)
    monkeypatch.setattr("messiah.features.engine._DEGENERATE_WINDOW", 10)
    _break_all_features(monkeypatch)
    engine = FeatureEngine("A05608", FakeBus(), feature_set="v-test", horizons=[Horizon.M5])

    for i in range(3):
        await engine.handle_bar(_bar(i, c=51814))

    tags = [tag for tag, _msg, _f in logged]
    assert "FeatureNaN" in tags
    assert "FeatureDegenerate" not in tags


# ---------------------------------------------------------------- 웜스타트 (2026-07-30)


def _bar_on(day: int, minute: int, horizon: Horizon = Horizon.M5, c=100) -> BarClosed:
    return BarClosed(
        symbol="A05608",
        horizon=horizon,
        bar_open_kst=datetime(2026, 7, day, 9, 0, tzinfo=KST) + timedelta(minutes=minute),
        o_ticks=c,
        h_ticks=c + 5,
        l_ticks=c - 5,
        c_ticks=c,
        volume=10,
        quality_ok=True,
    )


async def test_warm_start_fills_history_without_publishing():
    """웜스타트는 과거 봉으로 창을 채울 뿐, 실시간 토픽에 과거 피처를 흘리면 안 된다."""
    bus = FakeBus()
    engine = FeatureEngine("A05608", bus, feature_set="v-test", horizons=[Horizon.M5])

    loaded = engine.warm_start({Horizon.M5: [_bar_on(23, m) for m in range(10)]})

    assert loaded[Horizon.M5] == 10
    assert bus.published == []  # 발행 없음


async def test_warm_start_makes_the_first_live_bar_immediately_useful():
    """2026-07-30 핵심 회귀 — 예전엔 매 기동이 콜드스타트라 첫 봉의 nan_ratio가 0.96이었다."""
    bus = FakeBus()
    cold = FeatureEngine("A05608", bus, feature_set="v-test", horizons=[Horizon.M5])
    await cold.handle_bar(_bar_on(24, 0))
    cold_nan = bus.published[-1][1].nan_ratio

    warm_bus = FakeBus()
    warm = FeatureEngine("A05608", warm_bus, feature_set="v-test", horizons=[Horizon.M5])
    warm.warm_start({Horizon.M5: [_bar_on(23, m, c=100 + m) for m in range(130)]})
    await warm.handle_bar(_bar_on(24, 0))
    warm_nan = warm_bus.published[-1][1].nan_ratio

    assert cold_nan > 0.9  # 콜드스타트는 거의 전부 NaN
    assert warm_nan < 0.1  # 웜스타트 후 첫 봉부터 사실상 전부 계산됨


def test_warm_start_respects_history_capacity():
    engine = FeatureEngine("A05608", FakeBus(), feature_set="v-test", horizons=[Horizon.M5])

    loaded = engine.warm_start(
        {Horizon.M5: [_bar_on(23, m) for m in range(engine.history_capacity + 50)]}
    )

    assert loaded[Horizon.M5] == engine.history_capacity


def test_warm_start_sorts_out_of_order_input():
    engine = FeatureEngine("A05608", FakeBus(), feature_set="v-test", horizons=[Horizon.M5])
    shuffled = [_bar_on(23, 5), _bar_on(23, 1), _bar_on(23, 3)]

    engine.warm_start({Horizon.M5: shuffled})

    minutes = [b.bar_open_kst.minute for b in engine._history[Horizon.M5]]
    assert minutes == sorted(minutes)


def test_warm_start_drops_foreign_symbols_and_horizons():
    engine = FeatureEngine("A05608", FakeBus(), feature_set="v-test", horizons=[Horizon.M5])
    other_symbol = BarClosed(
        symbol="OTHER",
        horizon=Horizon.M5,
        bar_open_kst=datetime(2026, 7, 23, 9, 0, tzinfo=KST),
        o_ticks=1,
        h_ticks=1,
        l_ticks=1,
        c_ticks=1,
        volume=1,
        quality_ok=True,
    )

    loaded = engine.warm_start({Horizon.M5: [_bar_on(23, 0), other_symbol, _bar_on(23, 1)]})

    assert loaded[Horizon.M5] == 2


def test_warm_start_ignores_unsubscribed_horizons():
    engine = FeatureEngine("A05608", FakeBus(), feature_set="v-test", horizons=[Horizon.M5])

    loaded = engine.warm_start(
        {Horizon.M5: [_bar_on(23, 0)], Horizon.M30: [_bar_on(23, 0, horizon=Horizon.M30)]}
    )

    assert Horizon.M30 not in loaded
    assert loaded[Horizon.M5] == 1


async def test_warm_start_recovers_prev_day_close_for_gap_open():
    """px_gap_open은 전일 종가가 있어야 값이 나온다 — 콜드스타트에서는 볼 방법이 없어 항상
    None이었다. 전일 M1 봉을 시간순으로 흘리면 SessionState의 일자 롤오버가 이를 채운다."""
    engine = FeatureEngine("A05608", FakeBus(), feature_set="v-test", horizons=[Horizon.M1])

    engine.warm_start({Horizon.M1: [_bar_on(23, m, horizon=Horizon.M1, c=100) for m in range(30)]})

    assert engine._session.current_day == date(2026, 7, 23)
    assert engine._session._last_close_ticks == 100

    await engine.handle_bar(_bar_on(24, 0, horizon=Horizon.M1, c=110))

    assert engine._session.prev_day_close_ticks == 100  # 전일 종가가 살아났다
    assert engine._session.session_open_ticks == 110  # 새 세션 시가로 리셋


def test_warm_start_on_empty_input_is_harmless():
    engine = FeatureEngine("A05608", FakeBus(), feature_set="v-test", horizons=[Horizon.M5])

    loaded = engine.warm_start({})

    assert loaded == {Horizon.M5: 0}


# ---------------------------------------------------------------- 자가 헬스 판정 (고도화 1)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_health_is_ok_before_the_first_publish():
    clock = _Clock()
    engine = FeatureEngine(
        "A05608", FakeBus(), feature_set="v-test", horizons=[Horizon.M5], monotonic=clock
    )

    status = engine.health()

    assert status.level is HealthLevel.OK
    assert "웜업" in status.detail


async def test_health_goes_critical_when_publishing_stops():
    clock = _Clock()
    engine = FeatureEngine(
        "A05608", FakeBus(), feature_set="v-test", horizons=[Horizon.M5], monotonic=clock
    )
    engine.warm_start({Horizon.M5: [_bar_on(23, m, c=100 + m) for m in range(130)]})
    await engine.handle_bar(_bar_on(24, 0))

    assert engine.health().level is HealthLevel.OK
    clock.now += 130.0
    assert engine.health().level is HealthLevel.WARN
    clock.now += 130.0
    assert engine.health().level is HealthLevel.CRITICAL


async def test_health_warns_when_nan_ratio_exceeds_the_halt_threshold():
    """2026-07-30 실측: 15m/30m가 하루 종일 NaN 2/3였는데 화면 어디에도 안 드러났다."""
    clock = _Clock()
    engine = FeatureEngine(
        "A05608", FakeBus(), feature_set="v-test", horizons=[Horizon.M5], monotonic=clock
    )
    await engine.handle_bar(_bar_on(24, 0))  # 콜드스타트 — nan_ratio가 매우 높다

    status = engine.health()

    assert status.level is HealthLevel.WARN
    assert "5m" in status.detail


async def test_health_is_ok_once_warm_started_features_are_dense():
    clock = _Clock()
    engine = FeatureEngine(
        "A05608", FakeBus(), feature_set="v-test", horizons=[Horizon.M5], monotonic=clock
    )
    engine.warm_start({Horizon.M5: [_bar_on(23, m, c=100 + m) for m in range(130)]})
    await engine.handle_bar(_bar_on(24, 0))

    assert engine.health().level is HealthLevel.OK


# ------------------------------- 히스토리 용량 vs 피처 요구량 (2026-08-04)


def _rising_bars(n: int, horizon: Horizon = Horizon.M1) -> list[BarClosed]:
    """전 계산기가 값을 낼 수 있는 봉 — 세 가지를 일부러 만족시킨다.

    ① 변동이 있을 것: 완전 평탄하면 표준편차 계열이 **정의 불가**(퇴화)라 용량과 무관하게
       None이 된다.
    ② 어느 5봉 창에도 상승·하락이 **둘 다** 있을 것: 반음양 계열(`vl_semi_ratio_5`)은 한쪽
       변동만 있으면 분모가 0이다. 매 봉 부호를 뒤집어 보장한다.
    ③ **하루를 넘길 것**: `px_gap_open`은 전일 종가가 있어야 값이 나온다(`SessionState`의
       일자 롤오버). 라이브도 같은 조건에서 값을 낸다 — 웜스타트가 전일 꼬리를 채우고
       그날 첫 봉이 롤오버를 일으킨다(`FeatureEngine.warm_start` docstring). 용량 안에
       경계가 확실히 들어오도록 여기서는 하루를 짧게 잡는다(값의 성질은 무관).
    """
    import math

    out = []
    day_minutes = 100
    for i in range(n):
        c = round(10000 + 300 * math.sin(i / 7) + (i % 13) * 3 + (20 if i % 2 else -20))
        day = 2 + i // day_minutes
        minute = i % day_minutes
        out.append(
            BarClosed(
                symbol="A05608",
                horizon=horizon,
                bar_open_kst=datetime(2026, 3, day, 9, 0, tzinfo=KST) + timedelta(minutes=minute),
                o_ticks=c,
                h_ticks=c + 12,
                l_ticks=c - 12,
                c_ticks=c,
                volume=100 + i,
            )
        )
    return out


def test_every_registered_feature_is_computable_within_history_capacity():
    """**이 파일에서 가장 중요한 테스트** — 용량이 부족하면 그 피처는 프로덕션에서 영원히
    NaN이고, 아무도 모른다.

    2026-08-04 실측으로 두 개가 그 상태였다: `px_ema_cross_60`은 slow EMA가 3*W=180봉,
    `px_macd_h_60`은 2*W+시그널로 139봉을 요구하는데 용량은 130이었다. 매일 무결성
    리포트에 `nan_ratio=0.0165`(=2/121)가 찍히고 있었지만 "정상 수준"으로 읽혔다.

    2026-08-04(F0-4)에 **스펙 구동으로 바꿨다** — 종전에는 PX/VL 두 모듈을 손으로 나열해서,
    MS·OP·RG·EV가 붙으면 그 카테고리는 이 검사에서 통째로 빠진 채 같은 사고를 반복할
    수 있었다. 이제 `spec.CATEGORIES`에 등록된 **전 카테고리**가 자동으로 검사된다
    (봉 창만 검사한다 — 사이드카 계열은 봉 예산이 아니라 일별 이력 길이가 제약이라
    성격이 다르고, 그 카테고리의 자체 테스트가 본다).
    """
    from messiah.features import spec as feature_spec
    from messiah.features.engine import _MAX_HISTORY
    from messiah.features.px_core import SessionState

    bars = _rising_bars(_MAX_HISTORY)
    session = SessionState()
    for bar in bars:
        session.on_bar(bar)

    unusable: list[str] = []
    for category in feature_spec.CATEGORIES.values():
        for name, fn, windows in category.windowed:
            for window in windows:
                if fn(bars, window) is None:
                    unusable.append(f"{name}_{window}")
        for name, fn in category.stateful:
            if fn(bars, session) is None:
                unusable.append(name)

    assert not unusable, (
        f"용량 {_MAX_HISTORY}봉으로 계산 불가능한 피처: {unusable} — "
        f"_MAX_HISTORY를 올리거나 해당 피처의 윈도우를 줄일 것"
    )


def test_session_state_is_fed_when_m1_is_not_subscribed():
    """학습 경로(`build_feature_vectors`)는 학습 Horizon 하나만 구독한다 — 그때도 세션
    상태형 피처가 값을 내야 한다. 2026-08-04 이전에는 M1으로만 갱신해
    `px_gap_open`/`px_open_ret`/`px_range_pos_d`가 **학습에서만** 항상 NaN이었다."""
    engine = FeatureEngine("A05608", FakeBus(), "v2026.07", horizons=[Horizon.M15])

    engine.warm_start({Horizon.M15: _rising_bars(60, Horizon.M15)})

    assert engine._session.session_open_ticks is not None  # noqa: SLF001
    assert engine._session.session_high_ticks is not None  # noqa: SLF001


def test_session_state_still_prefers_m1_when_available():
    """라이브 동작 불변 — M1을 구독하면 여전히 M1으로만 갱신한다(장중 갱신이 가장 촘촘)."""
    engine = FeatureEngine("A05608", FakeBus(), "v2026.07")

    assert engine._session_horizon is Horizon.M1  # noqa: SLF001


# ------------------------------------------------ FL 결선 (2026-08-04)


def _flow_history(n: int = 30):
    from datetime import date as _date

    from messiah.data.investor_flow_history import FlowHistory, FlowRow

    rows = []
    for i in range(n):
        v = 1000 * (1 if i % 3 else -2)
        rows.append(
            FlowRow(
                day=_date(2026, 3, 1) + timedelta(days=i),
                values={
                    "frgn_ntby_qty": float(v),
                    "frgn_ntby_tr_pbmn": float(v * 10),
                    "prsn_ntby_qty": float(-v),
                    "prsn_ntby_tr_pbmn": float(-v * 10),
                    "orgn_ntby_qty": float(v // 2),
                    "orgn_ntby_tr_pbmn": float(v * 5),
                },
            )
        )
    return FlowHistory(rows)


def test_fl_features_are_absent_without_flow_history():
    """주입 안 하면 **자리조차 안 만든다** — NaN으로 채우면 `px_ema_cross_60`이 그랬듯
    죽은 채로 학습되는 피처가 또 생긴다(2026-08-04)."""
    from messiah.features.fl_core import FLOW_FEATURES

    engine = FeatureEngine("A05608", FakeBus(), "v2026.07", horizons=[Horizon.M5])
    vector = engine._build_feature_vector(  # noqa: SLF001
        _bar(0), _rising_bars(60, Horizon.M5)
    )

    assert not any(name in vector.values for name, _ in FLOW_FEATURES)


def test_fl_features_appear_and_have_values_when_flow_history_is_injected():
    from messiah.features.fl_core import FLOW_FEATURES

    engine = FeatureEngine(
        "A05608",
        FakeBus(),
        "v2026.08-fl",
        horizons=[Horizon.M5],
        sidecars={"flow": _flow_history()},
    )
    bars = _rising_bars(60, Horizon.M5)
    vector = engine._build_feature_vector(bars[-1], bars)  # noqa: SLF001

    present = [name for name, _ in FLOW_FEATURES if name in vector.values]
    assert len(present) == len(FLOW_FEATURES)
    assert any(vector.values[name] is not None for name in present)


# ---------------------------------------- 스펙·사이드카 정합 (2026-08-04, F0-1)


def test_engine_refuses_to_start_when_a_required_sidecar_is_missing():
    """2026-08-04 사고의 구조적 차단 — FL을 요구하는 feature_set인데 수급 이력을 안 넘기면
    종전에는 FL 9개가 조용히 사라진 벡터가 `v2026.08-fl` 이름을 달고 나갔다. 실제로
    `FeatureEngine` 생성처 7곳 전부가 그 상태였고, 아무도 몇 달간 몰랐다."""
    import pytest

    with pytest.raises(ValueError, match="flow"):
        FeatureEngine("A05608", FakeBus(), "v2026.08-fl", horizons=[Horizon.M5])


def test_engine_refuses_a_sidecar_the_feature_set_does_not_use():
    """반대 방향의 같은 사고 — 주입한 쪽은 FL이 나온다고 믿는데 스펙이 PX+VL이라 조용히
    무시된다. "붙였는데 왜 성능이 그대로지"로 몇 주를 쓰는 경로다."""
    import pytest

    with pytest.raises(ValueError, match="flow"):
        FeatureEngine(
            "A05608",
            FakeBus(),
            "v2026.07",
            horizons=[Horizon.M5],
            sidecars={"flow": _flow_history()},
        )


async def test_published_vector_keys_match_the_declared_spec_exactly():
    """`feature_set` 이름이 주장하는 모양과 실제 벡터가 같은가 — 학습 열 순서
    (`models/trainer.build_training_data`가 쓰는 `sorted(values)`)가 이 스펙과 어긋나면
    train/serve 불일치가 이름만 같은 채로 통과한다."""
    from messiah.features import spec as feature_spec

    bus = FakeBus()
    engine = FeatureEngine(
        "A05608",
        bus,
        "v2026.08-fl",
        horizons=[Horizon.M5],
        sidecars={"flow": _flow_history()},
    )

    await engine.handle_bar(_bar(0))

    _, vector = bus.published[0]
    assert sorted(vector.values) == list(feature_spec.resolve("v2026.08-fl").feature_names)


def test_feature_set_registry_is_internally_consistent():
    from messiah.features import spec as feature_spec

    assert feature_spec.validate_registry() == []


# ---------------------------------- 피처 건강도 (2026-08-05 고도화 3)
#
# `nan_ratio`가 못 보는 것을 본다. 2026-08-04 관문이 `px_macd_h_5`가 프로덕션에서 **항상
# 정확히 0**이라는 것을 처음 찾아냈는데, 값을 내므로 무결성 리포트에 아무 흔적이 없었다.


def _health_engine() -> FeatureEngine:
    return FeatureEngine("A05608", FakeBus(), feature_set="v-test", horizons=[Horizon.M5])


async def _feed(engine, n: int) -> int:
    """n개의 M5봉을 흘리고 그 수를 돌려준다.

    **주기적 톱니(`i % 13`)를 쓰면 안 된다** — 첫 시도에서 그렇게 했더니 `px_rsi_*`·
    `px_breakout_*`·`px_max_ret_*`·`vl_range_exp_*`가 전부 상수로 잡혔다. 검출기가
    맞았고 픽스처가 틀린 것이었다. 결정론적 의사난수 보행으로 바꾼다.
    """
    price, rng = 10_000, 424242
    for i in range(n):
        rng = (1103515245 * rng + 12345) % (2**31)
        price = max(100, price + (rng % 21) - 10)
        await engine.handle_bar(_bar(i, c=price))
    return n


async def test_constant_feature_is_detected_even_though_it_never_nans():
    """상수 피처는 `nan_ratio`에 흔적을 남기지 않는다 — 그게 이 검사가 필요한 이유다."""
    from messiah.features import engine as engine_module

    engine = _health_engine()
    n = await _feed(engine, engine_module._MIN_SAMPLES_FOR_HEALTH + 5)
    # 값은 내지만 절대 안 변하는 피처를 하나 주입한다(px_macd_h_5의 실제 형태).
    stats = engine._feature_stats[Horizon.M5]
    stats["dead_constant"] = engine_module._FeatureStat(n=n, n_nan=0, lo=0.0, hi=0.0)

    [health] = [h for h in engine.feature_health() if h.horizon == "5m"]

    assert "dead_constant" in health.constant
    assert "dead_constant" not in health.always_nan


async def test_always_nan_feature_is_reported_separately_from_constant():
    """항상 NaN과 상수는 원인이 다르다 — 전자는 계산 불가(용량·입력 부족), 후자는 정의 결함."""
    from messiah.features import engine as engine_module

    engine = _health_engine()
    n = await _feed(engine, engine_module._MIN_SAMPLES_FOR_HEALTH + 5)
    stats = engine._feature_stats[Horizon.M5]
    stats["never_computable"] = engine_module._FeatureStat(n=n, n_nan=n)

    [health] = [h for h in engine.feature_health() if h.horizon == "5m"]

    assert "never_computable" in health.always_nan
    assert "never_computable" not in health.constant


async def test_too_few_samples_is_not_judged():
    """장 초반 몇 봉이 우연히 같은 값인 것과 진짜 상수는 구분할 수 없다 — 판정하지 않는다."""
    from messiah.features import engine as engine_module

    engine = _health_engine()
    await _feed(engine, engine_module._MIN_SAMPLES_FOR_HEALTH - 1)

    for health in engine.feature_health():
        assert health.constant == []
        assert health.always_nan == []


async def test_log_feature_health_records_even_a_clean_day(monkeypatch):
    """퇴화 0건도 매일 남긴다 — 로그가 없는 날은 "검사했는데 0건"과 "검사를 안 함"이
    구분되지 않는다(L18). 그 구분이 무결성 리포트의 `unmeasured` 축의 전제다.

    **봉을 넉넉히 흘려야 한다**: 가장 긴 창이 180봉이라 그보다 적게 주면 그 피처들이
    정당하게 "항상 NaN"으로 잡힌다(첫 시도에서 35봉을 주고 Degenerate가 떠서 알았다).
    운영에서는 웜스타트가 200봉을 미리 채우므로 이 상황이 아니고, 반대로 **웜스타트가
    실패한 날엔 이 검사가 그 사실을 잡는다** — 의도한 동작이다.
    """
    logged: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "messiah.features.engine.mlog.log", lambda tag, msg, **f: logged.append((tag, f))
    )
    from messiah.features import engine as engine_module

    engine = _health_engine()
    # 통계를 **직접** 건강한 상태로 채운다. 합성 봉으로 "완전히 깨끗한 하루"를 만들려고
    # 하면 픽스처가 현실적이어야 하는데, 실제로 시도해 보니 고정 고저폭 때문에
    # `vl_range_exp_*`가, 단조 구간 때문에 `px_ema_cross_60`이 상수로 잡혔다 — **검출기가
    # 맞고 픽스처가 틀린 것**이었다. 여기서 검증할 계약은 "퇴화 0건도 로그를 남기는가"
    # 하나이므로 그 계약만 격리한다(퇴화 검출 자체는 위 두 테스트가 본다).
    engine._feature_stats[Horizon.M5] = {
        f"healthy_{i}": engine_module._FeatureStat(n=100, n_nan=0, lo=0.0, hi=float(i + 1))
        for i in range(5)
    }

    [health] = engine.log_feature_health()

    assert health.degenerate_count == 0
    tags = {tag for tag, _ in logged if tag.startswith("FeatureHealth")}
    assert tags == {"FeatureHealthSummary"}  # 퇴화 없음 → Summary(INFO)


async def test_a_failed_warm_start_shows_up_as_degenerate(monkeypatch):
    """반대 방향 — 롤링 창을 못 채운 채로 하루를 돌면 긴 창 피처가 통째로 죽는다.

    2026-07-29에 L1이 6번 재시작돼 피처 워밍업이 전량 소실된 전례가 있다. 그날 리포트에
    이 사실을 가리키는 지표가 없었다(`nan_ratio`는 올라갔지만 **어느 피처인지**는 안 나왔다).
    """
    logged: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "messiah.features.engine.mlog.log", lambda tag, msg, **f: logged.append((tag, f))
    )
    from messiah.features import engine as engine_module

    engine = _health_engine()
    await _feed(engine, engine_module._MIN_SAMPLES_FOR_HEALTH + 5)  # 창을 한참 못 채운 상태

    [health] = engine.log_feature_health()

    assert health.always_nan, "긴 창 피처가 죽어 있는데 아무것도 안 잡혔다"
    assert any(tag == "FeatureHealthDegenerate" for tag, _ in logged)
