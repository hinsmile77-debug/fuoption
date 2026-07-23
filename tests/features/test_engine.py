from datetime import datetime, timedelta

from messiah.core.messages import BarClosed, FeatureVector, Horizon
from messiah.core.timeutil import KST
from messiah.features.engine import FeatureEngine
from messiah.features.px_core import STATEFUL_FEATURES, WINDOWED_FEATURES

_EXPECTED_KEY_COUNT = sum(len(windows) for _, _, windows in WINDOWED_FEATURES) + len(
    STATEFUL_FEATURES
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


async def test_high_nan_ratio_logs_feature_nan_warning(monkeypatch):
    logged: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "messiah.features.engine.mlog.log", lambda tag, msg, **f: logged.append((tag, msg))
    )
    bus = FakeBus()
    engine = FeatureEngine("A05608", bus, feature_set="v-test", horizons=[Horizon.M5])

    await engine.handle_bar(_bar(0))  # 첫 봉 — nan_ratio=1.0 > 20%

    assert any(tag == "FeatureNaN" for tag, _ in logged)
