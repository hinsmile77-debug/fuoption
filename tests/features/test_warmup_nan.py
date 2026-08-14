"""워밍업 중 NaN 임계 초과를 **분류**한다 — 2026-08-14 F-9.

종전엔 `len(history) < _MAX_HISTORY`면 임계 초과를 아예 안 찍었다. 그 억제는 옳았지만
(30m은 창을 채우는 데만 며칠이 걸려 매 봉 WARNING이면 주간 경보가 잡음에 파묻힌다,
2026-07-24) **한 조건이 두 사건을 함께 덮고 있었다**: 평범한 워밍업과, 월물 롤로
아카이브가 통째로 빈 상태.

2026-08-14 첫 월물 롤에서 전 Horizon이 0봉에서 출발해 1m NaN 84.7%로 개장했고 30m은
종일 62% 아래로 안 내려갔는데 **로그에 한 줄도 안 남았다.** 화면과 자가점검이 정상을
말하는 동안 판단은 종일 불가였다.

그래서 억제를 분류로 바꿨다. 새 태그는 INFO이고 Horizon당 1회 + 30분 재고지다 —
WARNING으로 올리면 2026-07-24가 없앤 잡음이 그대로 돌아온다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from messiah.core.messages import BarClosed, FeatureVector, Horizon
from messiah.core.timeutil import KST
from messiah.features import engine as engine_mod
from messiah.features.engine import FeatureEngine


class FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, FeatureVector]] = []

    async def publish(self, topic: str, msg: FeatureVector) -> None:
        self.published.append((topic, msg))


def _bar(index: int, *, horizon: Horizon = Horizon.M5, close: int = 100) -> BarClosed:
    """`index`번째 봉 — Horizon 간격만큼 시간이 흐른다(봉 시각이 재고지 기준이다)."""
    minutes = {Horizon.M1: 1, Horizon.M5: 5, Horizon.M30: 30}[horizon]
    return BarClosed(
        symbol="A05609",
        horizon=horizon,
        bar_open_kst=datetime(2026, 8, 14, 9, 0, tzinfo=KST) + timedelta(minutes=minutes * index),
        o_ticks=close,
        h_ticks=close + 5,
        l_ticks=close - 5,
        c_ticks=close,
        volume=10,
        quality_ok=True,
    )


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    entries: list[tuple[str, dict]] = []
    monkeypatch.setattr(engine_mod.mlog, "log", lambda tag, msg, **f: entries.append((tag, f)))
    return entries


async def test_cold_start_now_says_it_is_starved(logged) -> None:
    """롤 당일의 침묵을 깬다 — 창이 비었는데 NaN이 높으면 그 사실이 남아야 한다."""
    engine = FeatureEngine("A05609", FakeBus(), feature_set="v-test", horizons=[Horizon.M5])

    await engine.handle_bar(_bar(0))

    tags = [tag for tag, _ in logged]
    assert "FeatureNanWarmupExceeded" in tags
    fields = next(f for tag, f in logged if tag == "FeatureNanWarmupExceeded")
    assert fields["horizon"] == "5m"
    assert fields["bars"] == 1
    assert fields["required"] == engine_mod._MAX_HISTORY
    assert fields["nan_ratio"] > engine_mod._NAN_RATIO_HALT_THRESHOLD


async def test_it_does_not_fire_on_every_bar(logged) -> None:
    """**핵심 설계 판단** — 매 봉 찍으면 2026-07-24가 없앤 잡음이 그대로 돌아온다."""
    engine = FeatureEngine("A05609", FakeBus(), feature_set="v-test", horizons=[Horizon.M5])

    for i in range(6):  # 5m × 6 = 25분 — 재고지 간격(30분) 안쪽이다
        await engine.handle_bar(_bar(i))

    warmup = [f for tag, f in logged if tag == "FeatureNanWarmupExceeded"]
    assert len(warmup) == 1


async def test_it_renotifies_after_the_interval(logged) -> None:
    """침묵이 영구가 되면 안 된다 — 창이 안 차는 채로 하루가 가는 날이 롤 당일이다."""
    engine = FeatureEngine("A05609", FakeBus(), feature_set="v-test", horizons=[Horizon.M5])

    for i in range(9):  # 5m × 9 = 40분 — 30분 경계를 넘는다
        await engine.handle_bar(_bar(i))

    warmup = [f for tag, f in logged if tag == "FeatureNanWarmupExceeded"]
    assert len(warmup) == 2
    assert warmup[1]["bars"] > warmup[0]["bars"]  # 창이 차고 있다는 것이 보여야 한다


async def test_each_horizon_is_tracked_separately(logged) -> None:
    """롤 당일엔 전 Horizon이 동시에 굶는다 — 하나가 다른 하나를 가리면 안 된다."""
    engine = FeatureEngine(
        "A05609", FakeBus(), feature_set="v-test", horizons=[Horizon.M5, Horizon.M30]
    )

    await engine.handle_bar(_bar(0, horizon=Horizon.M5))
    await engine.handle_bar(_bar(0, horizon=Horizon.M30))

    horizons = {f["horizon"] for tag, f in logged if tag == "FeatureNanWarmupExceeded"}
    assert horizons == {"5m", "30m"}


async def test_the_warmed_up_warning_path_is_untouched(logged, monkeypatch) -> None:
    """기존 WARNING 경로의 조건·문구는 불변이다 — 새 분기만 더했다.

    창이 찬 뒤에도 NaN이 높으면 그건 워밍업이 아니라 **사고**이고, 그 구분이 이 변경의
    목적이다. 워밍업 태그가 그 자리를 차지해 버리면 고친 것이 아니라 가린 것이 된다.
    """
    monkeypatch.setattr(engine_mod, "_MAX_HISTORY", 1)
    engine = FeatureEngine("A05609", FakeBus(), feature_set="v-test", horizons=[Horizon.M5])

    await engine.handle_bar(_bar(0))

    tags = [tag for tag, _ in logged]
    assert "FeatureNanWarmupExceeded" not in tags
    assert "FeatureNaN" in tags or "FeatureDegenerate" in tags
