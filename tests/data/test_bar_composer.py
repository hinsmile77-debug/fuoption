from datetime import date, datetime
from pathlib import Path

from messiah.core.messages import BarClosed, Horizon
from messiah.core.timeutil import KST
from messiah.data.archiver import ParquetArchiver
from messiah.data.bar_composer import MultiHorizonBarComposer, floor_to_horizon


def _m1(minute: int, o=100, h=105, lo=95, c=102, volume=10, quality_ok=True) -> BarClosed:
    return BarClosed(
        symbol="A05608",
        horizon=Horizon.M1,
        bar_open_kst=datetime(2026, 7, 23, 9, minute, tzinfo=KST),
        o_ticks=o,
        h_ticks=h,
        l_ticks=lo,
        c_ticks=c,
        volume=volume,
        quality_ok=quality_ok,
    )


class FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, BarClosed]] = []

    async def publish(self, topic: str, msg: BarClosed) -> None:
        self.published.append((topic, msg))


def _composer(tmp_path: Path, bus: FakeBus, horizons=None) -> MultiHorizonBarComposer:
    targets = {Horizon.M5: 300} if horizons is None else horizons
    return MultiHorizonBarComposer(
        symbol="A05608", archiver=ParquetArchiver(tmp_path), bus=bus, target_horizons=targets
    )


def test_floor_to_horizon_aligns_to_horizon_grid():
    dt = datetime(2026, 7, 23, 9, 32, 47, tzinfo=KST)
    assert floor_to_horizon(dt, 300) == datetime(2026, 7, 23, 9, 30, tzinfo=KST)
    assert floor_to_horizon(dt, 900) == datetime(2026, 7, 23, 9, 30, tzinfo=KST)
    assert floor_to_horizon(dt, 1800) == datetime(2026, 7, 23, 9, 30, tzinfo=KST)


async def test_composes_ohlcv_from_constituent_one_minute_bars(tmp_path: Path):
    bus = FakeBus()
    composer = _composer(tmp_path, bus)

    for minute, o, h, lo, c in [
        (30, 100, 108, 99, 103),
        (31, 103, 106, 101, 104),
        (32, 104, 110, 103, 109),
        (33, 109, 109, 100, 101),
        (34, 101, 105, 98, 102),
    ]:
        await composer.handle_one_minute_bar(_m1(minute, o=o, h=h, lo=lo, c=c))
    await composer.flush_due_horizon(Horizon.M5)

    assert len(bus.published) == 1
    topic, bar = bus.published[0]
    assert topic == "bar.5m.A05608"
    assert bar.bar_open_kst == datetime(2026, 7, 23, 9, 30, tzinfo=KST)
    assert bar.o_ticks == 100  # 첫 1분봉의 open
    assert bar.h_ticks == 110  # 구성봉 전체 최댓값
    assert bar.l_ticks == 98  # 구성봉 전체 최솟값
    assert bar.c_ticks == 102  # 마지막 1분봉의 close
    assert bar.volume == 50  # 10 x 5
    assert bar.quality_ok is True  # 5개 전부 있고 전부 quality_ok


async def test_quality_ok_false_when_minute_missing(tmp_path: Path):
    bus = FakeBus()
    composer = _composer(tmp_path, bus)

    for minute in (30, 31, 33, 34):  # 32분 결측
        await composer.handle_one_minute_bar(_m1(minute))
    await composer.flush_due_horizon(Horizon.M5)

    bar = bus.published[0][1]
    assert bar.quality_ok is False


async def test_quality_ok_false_when_a_constituent_is_low_quality(tmp_path: Path):
    bus = FakeBus()
    composer = _composer(tmp_path, bus)

    for minute in (30, 31, 32, 33):
        await composer.handle_one_minute_bar(_m1(minute))
    await composer.handle_one_minute_bar(_m1(34, quality_ok=False))
    await composer.flush_due_horizon(Horizon.M5)

    assert bus.published[0][1].quality_ok is False


async def test_empty_bucket_does_not_publish(tmp_path: Path):
    bus = FakeBus()
    composer = _composer(tmp_path, bus)

    await composer.flush_due_horizon(Horizon.M5)  # 구성봉 없음(조용한 구간)

    assert bus.published == []


async def test_second_bucket_starts_fresh_after_flush(tmp_path: Path):
    bus = FakeBus()
    composer = _composer(tmp_path, bus)

    for minute in range(30, 35):
        await composer.handle_one_minute_bar(_m1(minute))
    await composer.flush_due_horizon(Horizon.M5)

    for minute in range(35, 40):
        await composer.handle_one_minute_bar(_m1(minute, o=200, c=210))
    await composer.flush_due_horizon(Horizon.M5)

    assert len(bus.published) == 2
    second_bar = bus.published[1][1]
    assert second_bar.bar_open_kst == datetime(2026, 7, 23, 9, 35, tzinfo=KST)
    assert second_bar.o_ticks == 200


async def test_ignores_bars_for_other_symbols(tmp_path: Path):
    bus = FakeBus()
    composer = _composer(tmp_path, bus)
    other_symbol_bar = _m1(30).model_copy(update={"symbol": "OTHER"})

    await composer.handle_one_minute_bar(other_symbol_bar)
    await composer.flush_due_horizon(Horizon.M5)

    assert bus.published == []


async def test_multiple_horizons_accumulate_independently(tmp_path: Path):
    bus = FakeBus()
    composer = _composer(tmp_path, bus, horizons={Horizon.M3: 180, Horizon.M5: 300})

    for minute in range(30, 35):
        await composer.handle_one_minute_bar(_m1(minute))
    await composer.flush_due_horizon(Horizon.M3)  # 09:30-09:33 구간(3개: 30,31,32) 확정

    m3_published = [b for t, b in bus.published if t.startswith("bar.3m.")]
    assert len(m3_published) == 1
    assert m3_published[0].bar_open_kst == datetime(2026, 7, 23, 9, 30, tzinfo=KST)

    await composer.flush_due_horizon(Horizon.M5)  # M5는 아직 안 건드림 — 09:30~09:34(5개) 전부
    m5_published = [b for t, b in bus.published if t.startswith("bar.5m.")]
    assert len(m5_published) == 1
    assert m5_published[0].volume == 50


async def test_archives_composite_bar(tmp_path: Path):
    bus = FakeBus()
    composer = _composer(tmp_path, bus)

    for minute in range(30, 35):
        await composer.handle_one_minute_bar(_m1(minute))
    await composer.flush_due_horizon(Horizon.M5)

    # 물리 배치(장중 조각 vs 통합본)는 `read_day()` 뒤에 숨는다 — 경로가 아니라 "그날치를
    # 다시 읽을 수 있는가"를 본다(`data/archiver.py` "조각 쓰기").
    df = ParquetArchiver(tmp_path).read_day("A05608", Horizon.M5, date(2026, 7, 23))
    assert df is not None
    assert df.height == 1


async def test_flush_all_final_flushes_every_horizon(tmp_path: Path):
    bus = FakeBus()
    composer = _composer(tmp_path, bus, horizons={Horizon.M3: 180, Horizon.M5: 300})

    for minute in range(30, 33):  # 3개 -> M3만 완전, M5는 미완성이지만 강제 flush
        await composer.handle_one_minute_bar(_m1(minute))
    await composer.flush_all_final()

    topics = {t for t, _ in bus.published}
    assert topics == {"bar.3m.A05608", "bar.5m.A05608"}


async def test_archive_failure_is_logged_and_does_not_raise(tmp_path: Path, monkeypatch):
    logged: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "messiah.data.bar_composer.mlog.log", lambda tag, msg, **f: logged.append((tag, msg))
    )
    bus = FakeBus()
    composer = _composer(tmp_path, bus)
    broken_dir = tmp_path / "A05608"
    broken_dir.mkdir(parents=True)
    (broken_dir / "5m").write_text("not a directory")  # append_bar의 mkdir을 실패시킴

    for minute in range(30, 35):
        await composer.handle_one_minute_bar(_m1(minute))
    await composer.flush_due_horizon(Horizon.M5)  # 예외 없이 끝나야 함

    assert any(tag == "CollectorProcessingError" for tag, _ in logged)
    assert len(bus.published) == 1  # 적재 실패해도 발행은 별도로 시도됨


async def test_publish_failure_is_logged_and_does_not_raise(tmp_path: Path, monkeypatch):
    logged: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "messiah.data.bar_composer.mlog.log", lambda tag, msg, **f: logged.append((tag, msg))
    )

    class FailingBus:
        async def publish(self, topic: str, msg: BarClosed) -> None:
            raise RuntimeError("발행 실패")

    composer = _composer(tmp_path, FailingBus())
    for minute in range(30, 35):
        await composer.handle_one_minute_bar(_m1(minute))
    await composer.flush_due_horizon(Horizon.M5)  # 예외 없이 끝나야 함

    assert any(tag == "CollectorProcessingError" for tag, _ in logged)
    assert (
        ParquetArchiver(tmp_path).read_day("A05608", Horizon.M5, date(2026, 7, 23)) is not None
    )  # 적재는 성공


# ---------------------------------------------------------------- 오프라인 재합성 (백필용)


def test_compose_offline_buckets_by_horizon_boundary():
    from messiah.data.bar_composer import compose_offline

    bars = [_m1(m, c=100 + m, volume=1) for m in range(10)]  # 09:00~09:09

    out = compose_offline("A05608", Horizon.M5, bars)

    assert [b.bar_open_kst.minute for b in out] == [0, 5]
    assert [b.volume for b in out] == [5, 5]
    assert out[0].o_ticks == bars[0].o_ticks
    assert out[0].c_ticks == bars[4].c_ticks
    assert out[1].c_ticks == bars[9].c_ticks


def test_compose_offline_matches_the_live_path_exactly():
    """실시간 경로와 오프라인 재합성이 같은 봉을 만들어야 한다 — 아카이브 안에서 같은
    Horizon이 두 규칙으로 만들어지면 안 된다(그래서 규칙을 한 함수로 모았다)."""
    import asyncio
    import tempfile

    from messiah.data.bar_composer import compose_offline

    bars = [_m1(m, c=100 + m, h=110 + m, lo=90 - m, volume=m + 1) for m in range(5)]

    offline = compose_offline("A05608", Horizon.M5, bars)

    async def _live():
        with tempfile.TemporaryDirectory() as tmp:
            published: list[BarClosed] = []

            class _Bus:
                async def publish(self, topic, msg):
                    published.append(msg)

                async def subscribe(self, topics, handler):
                    return None

            composer = MultiHorizonBarComposer(
                "A05608", ParquetArchiver(Path(tmp)), _Bus(), {Horizon.M5: 300}
            )
            for bar in bars:
                await composer.handle_one_minute_bar(bar)
            await composer.flush_due_horizon(Horizon.M5)
            return published

    live = asyncio.run(_live())

    assert len(offline) == len(live) == 1
    for attr in (
        "bar_open_kst",
        "o_ticks",
        "h_ticks",
        "l_ticks",
        "c_ticks",
        "volume",
        "quality_ok",
        "session",
    ):
        assert getattr(offline[0], attr) == getattr(live[0], attr), attr


def test_compose_offline_marks_incomplete_bucket_as_low_quality():
    from messiah.data.bar_composer import compose_offline

    partial = [_m1(0), _m1(1), _m1(2)]  # 5분봉인데 3분치뿐

    out = compose_offline("A05608", Horizon.M5, partial)

    assert len(out) == 1
    assert out[0].quality_ok is False


def test_compose_offline_rejects_non_m1_input():
    import pytest
    from messiah.data.bar_composer import compose_offline

    five = BarClosed(
        symbol="A05608",
        horizon=Horizon.M5,
        bar_open_kst=datetime(2026, 7, 23, 9, 0, tzinfo=KST),
        o_ticks=1,
        h_ticks=1,
        l_ticks=1,
        c_ticks=1,
        volume=1,
    )
    with pytest.raises(ValueError):
        compose_offline("A05608", Horizon.M15, [five])
