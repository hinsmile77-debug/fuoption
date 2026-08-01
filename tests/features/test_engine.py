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
