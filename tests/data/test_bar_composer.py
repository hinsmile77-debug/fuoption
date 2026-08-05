import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from messiah.core.messages import BarClosed, HealthLevel, Horizon
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


async def _yield_only(_seconds: float) -> None:
    """주입용 sleep — 실제로 자지 않고 이벤트 루프에만 양보한다.

    겹④(`_await_last_constituent`)가 마지막 1분봉을 최대 5초 기다리므로, 미완 버킷을
    확정하는 테스트가 실제로 5초씩 자면 스위트가 못 쓰게 느려진다. `wait_for_bar()`는
    시간이 아니라 **횟수**로 세도록 만들어져 있어(그쪽 docstring) 대기 로직 자체는 그대로
    검증된다. 양보는 유지해야 한다 — 대기 중에 봉이 도착하는 시나리오가 성립해야 한다.
    """
    await asyncio.sleep(0)


def _composer(
    tmp_path: Path, bus: FakeBus, horizons=None, *, sleep=_yield_only
) -> MultiHorizonBarComposer:
    targets = {Horizon.M5: 300} if horizons is None else horizons
    return MultiHorizonBarComposer(
        symbol="A05608",
        archiver=ParquetArchiver(tmp_path),
        bus=bus,
        target_horizons=targets,
        sleep=sleep,
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
    """Horizon마다 버킷 경계가 따로 간다 — M3는 09:33에서 갈리고 M5는 안 갈린다.

    2026-08-05 이후 M3의 첫 버킷은 **스케줄러가 아니라 09:33봉의 도착**이 확정한다
    (`handle_one_minute_bar`의 봉 도착 기반 롤오버). 그래서 09:34까지 넣고 나면 M3는
    이미 [09:30,09:33)을 내보냈고, 뒤이은 flush가 [09:33,09:36)을 마저 내보낸다.
    """
    bus = FakeBus()
    composer = _composer(tmp_path, bus, horizons={Horizon.M3: 180, Horizon.M5: 300})

    for minute in range(30, 35):
        await composer.handle_one_minute_bar(_m1(minute))

    # 09:33봉이 들어온 순간 [09:30,09:33)이 자동 확정됐다 — 시계를 전혀 안 보고.
    m3_published = [b for t, b in bus.published if t.startswith("bar.3m.")]
    assert [b.bar_open_kst for b in m3_published] == [datetime(2026, 7, 23, 9, 30, tzinfo=KST)]
    assert m3_published[0].volume == 30  # 30·31·32분봉 3개

    await composer.flush_due_horizon(Horizon.M3)  # 남은 [09:33,09:36) — 33·34분봉 2개
    m3_published = [b for t, b in bus.published if t.startswith("bar.3m.")]
    assert [b.bar_open_kst for b in m3_published] == [
        datetime(2026, 7, 23, 9, 30, tzinfo=KST),
        datetime(2026, 7, 23, 9, 33, tzinfo=KST),
    ]

    await composer.flush_due_horizon(Horizon.M5)  # M5는 경계를 안 넘었다 — 09:30~09:34 전부
    m5_published = [b for t, b in bus.published if t.startswith("bar.5m.")]
    assert len(m5_published) == 1
    assert m5_published[0].volume == 50

    # 어느 Horizon으로 집계해도 1분봉 거래량 총합은 보존된다 — 무결성 리포트의
    # `analyze_horizon_consistency()`가 매일 확인하는 바로 그 항등식.
    assert sum(b.volume for b in m3_published) == 50 == sum(b.volume for b in m5_published)


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


# ------------------------------------------------- 시계 스큐 내성 (2026-08-05 일일점검 대응)
#
# 2026-08-04 실측: 1분봉 아카이브 거래량 합 84,346 vs 3/5/10/15/30분봉 전부 84,209.
# 차이 137은 정확히 15:34봉 하나였다 — 상위 Horizon 확정이 **로컬 시계**로만 이루어졌고,
# 그 시계가 거래소와 어긋나 있었기 때문이다. 아래 셋이 그 경로를 각각 막는다.


def _at(minute: int, second: int = 0) -> datetime:
    return datetime(2026, 7, 23, 9, minute, second, tzinfo=KST)


class _FakeClock:
    """주입용 시계 — `sleep()`이 실제로 자지 않고 그만큼 시각을 앞으로 민다."""

    def __init__(self, start: datetime) -> None:
        self.now = start
        self.slept: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += timedelta(seconds=seconds)


async def test_scheduler_flush_waits_when_local_clock_runs_ahead_of_the_exchange(tmp_path: Path):
    """**이 프로젝트가 아직 겪지 않은 방향**의 사고를 미리 막는다.

    2026-08-04엔 거래소가 로컬보다 앞서 있어(+9.7초) 우연히 안전했다. 부호가 뒤집히면
    스케줄러가 경계+0.5초에 쐈을 때 그 버킷의 마지막 1분봉이 **아직 안 왔고**, 버킷은 한 봉
    모자란 채 확정된다. 스큐를 알려주면 그만큼 더 기다려야 한다.
    """
    bus = FakeBus()
    clock = _FakeClock(_at(35, 0))  # 로컬 09:35:00 — 스케줄러가 09:35 경계에 쏜 순간
    composer = MultiHorizonBarComposer(
        symbol="A05608",
        archiver=ParquetArchiver(tmp_path),
        bus=bus,
        target_horizons={Horizon.M5: 300},
        clock_skew_seconds=lambda: -3.0,  # 거래소가 로컬보다 3초 뒤 = 로컬이 앞선다
        now=clock,
        sleep=clock.sleep,
    )

    for minute in range(30, 34):  # 09:34봉은 아직 도착 전
        await composer.handle_one_minute_bar(_m1(minute))

    await composer.flush_due_horizon(Horizon.M5)

    # 거래소 시각으로는 아직 09:34:57이었으므로 기다렸다.
    assert clock.slept, "로컬이 앞선 상태인데 기다리지 않았다 — 버킷이 잘린다"
    assert sum(clock.slept) >= 3.0


async def test_no_wait_when_the_exchange_runs_ahead(tmp_path: Path):
    """2026-08-04 실측 방향(거래소가 앞섬)에서는 종전과 완전히 같은 동작이어야 한다 —
    이 수정이 정상일의 지연을 늘리지 않는다는 확인."""
    bus = FakeBus()
    clock = _FakeClock(_at(35, 0))
    composer = MultiHorizonBarComposer(
        symbol="A05608",
        archiver=ParquetArchiver(tmp_path),
        bus=bus,
        target_horizons={Horizon.M5: 300},
        clock_skew_seconds=lambda: 9.7,
        now=clock,
        sleep=clock.sleep,
    )

    for minute in range(30, 35):
        await composer.handle_one_minute_bar(_m1(minute))
    await composer.flush_due_horizon(Horizon.M5)

    assert clock.slept == []  # 한 번도 안 잤다
    assert len(bus.published) == 1
    assert bus.published[0][1].volume == 50


async def test_late_bar_never_creates_a_duplicate_bucket(tmp_path: Path, monkeypatch):
    """세 번째 겹 — 앞의 둘이 다 뚫려도 **아카이브에 같은 시각의 봉이 두 줄** 생기면 안 된다.

    늦게 온 1분봉을 그냥 받으면 `floor_to_horizon`이 같은 버킷 시작 시각을 돌려주므로,
    그 봉이 두 번째 버킷을 열고 다음 flush에서 같은 시각의 합성봉이 또 나간다. 버리되
    조용히는 안 한다(L18) — 유실이 로그에 남아야 다음 점검이 그 크기를 안다.
    """
    logged: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "messiah.data.bar_composer.mlog.log", lambda tag, msg, **f: logged.append((tag, msg))
    )
    bus = FakeBus()
    composer = _composer(tmp_path, bus)  # M5만

    for minute in range(30, 34):
        await composer.handle_one_minute_bar(_m1(minute))
    await composer.flush_due_horizon(Horizon.M5)  # [09:30,09:35)를 4봉으로 확정(스큐 없음)

    await composer.handle_one_minute_bar(_m1(34))  # 뒤늦게 도착
    await composer.flush_all_final()  # 수정 전이라면 여기서 09:30이 한 번 더 나갔다

    starts = [b.bar_open_kst for _, b in bus.published]
    assert starts == [_at(30)], f"같은 시각의 합성봉이 두 번 나갔다: {starts}"
    assert any(tag == "ComposerLateBarDropped" for tag, _ in logged)


async def test_bucket_rollover_is_driven_by_bar_arrival_not_the_clock(tmp_path: Path):
    """첫 번째 겹 — 스케줄러를 한 번도 안 불러도 경계는 정확히 갈린다.

    이 경로가 시계를 전혀 안 보기 때문에, 시계가 얼마나 어긋나 있든 **1분봉 거래량 총합은
    상위 Horizon에 보존된다**(무결성 리포트 `analyze_horizon_consistency()`의 항등식).
    """
    bus = FakeBus()
    composer = _composer(tmp_path, bus)  # M5

    for minute in range(30, 40):  # 09:30~09:39 — 5분 버킷 두 개 분량
        await composer.handle_one_minute_bar(_m1(minute))

    published = [b for _, b in bus.published]
    assert [b.bar_open_kst for b in published] == [_at(30)]  # 두 번째는 아직 진행 중
    await composer.flush_all_final()

    published = [b for _, b in bus.published]
    assert [b.bar_open_kst for b in published] == [_at(30), _at(35)]
    assert sum(b.volume for b in published) == 100  # 1분봉 10개 × 10 — 총합 보존


# ------------------------------------------------- 종료 시퀀스 경합 (2026-08-04 실측 사고)


async def test_wait_for_bar_returns_true_once_the_bar_arrives(tmp_path: Path):
    bus = FakeBus()
    composer = _composer(tmp_path, bus)

    assert await composer.wait_for_bar(_at(34), timeout_seconds=0.01, poll_seconds=0.005) is False

    await composer.handle_one_minute_bar(_m1(34))

    assert await composer.wait_for_bar(_at(34), timeout_seconds=0.01, poll_seconds=0.005) is True
    assert composer.last_seen_bar_open == _at(34)


async def test_wait_for_bar_gives_the_final_minute_time_to_land(tmp_path: Path):
    """2026-08-04 사고의 직접 회귀.

    `TickCollector.flush_final_bar()`는 버스에 발행만 하고 돌아온다. 구독자 콜백이 그 사이에
    실행된다는 보장이 없어, 곧바로 `flush_all_final()`을 부르면 그날 마지막 1분봉이 상위
    Horizon 전부에서 빠졌다(15:34봉 137계약). 여기서는 그 봉이 **대기 중에** 도착한다.
    """
    # 이 테스트만 **진짜** sleep을 쓴다 — 검증 대상이 "실제 시간 경과 중에 봉이 도착한다"는
    # 경합 그 자체라, 양보만 하는 가짜 sleep으로는 재현되지 않는다.
    bus = FakeBus()
    composer = _composer(tmp_path, bus, sleep=asyncio.sleep)
    for minute in range(30, 34):
        await composer.handle_one_minute_bar(_m1(minute))

    async def _deliver_late() -> None:
        await asyncio.sleep(0.02)  # 발행 → 구독자 도달 사이의 지연을 흉내
        await composer.handle_one_minute_bar(_m1(34))

    delivery = asyncio.create_task(_deliver_late())
    arrived = await composer.wait_for_bar(_at(34), timeout_seconds=2.0, poll_seconds=0.005)
    await delivery
    await composer.flush_all_final()

    assert arrived is True
    assert len(bus.published) == 1
    assert bus.published[0][1].volume == 50  # 5봉 전부 — 137계약이 빠지던 자리


# ------------------------------- 겹④ 마지막 구성봉 대기 (2026-08-05 장중 실측 사고)
#
# 시계를 맞추자(P0-1) 그날 바로 26건의 `ComposerLateBarDropped`가 났다. 3분봉 18개 중 12개가
# 2분짜리, 30분봉 2개 다 29분짜리. 원인은 스큐가 아니라 **1분봉이 시각이 아니라 다음 분의 첫
# 틱으로 확정된다**는 것이었다(발행 지연 중앙값 0.655초 > 스케줄러 위상 0.5초).


async def test_scheduler_waits_for_the_last_minute_bar_to_arrive(tmp_path: Path):
    """2026-08-05 사고의 직접 회귀 — 스케줄러가 먼저 쐈어도 버킷이 잘리면 안 된다.

    스큐는 0이다(겹②가 한 번도 안 잔다). 그런데도 마지막 1분봉이 아직 안 왔다 — 시계가
    아니라 틱 도착이 늦은 것이라, 시계를 보는 어떤 방어로도 이 상황은 못 막는다.
    """
    bus = FakeBus()
    composer = _composer(tmp_path, bus, sleep=asyncio.sleep)  # M5
    for minute in range(30, 34):  # 09:34봉이 아직 안 왔다
        await composer.handle_one_minute_bar(_m1(minute))

    async def _deliver_last_minute() -> None:
        await asyncio.sleep(0.02)
        await composer.handle_one_minute_bar(_m1(34))

    delivery = asyncio.create_task(_deliver_last_minute())
    await composer.flush_due_horizon(Horizon.M5)
    await delivery

    published = [b for _, b in bus.published]
    assert len(published) == 1
    assert published[0].volume == 50, "마지막 1분봉을 안 기다리고 4봉으로 확정했다"
    assert published[0].quality_ok is True
    assert composer.late_bar_drops == 0
    assert composer.incomplete_flushes == 0


async def test_no_wait_when_every_constituent_has_already_arrived(tmp_path: Path):
    """정상 경로(구성봉이 다 와 있음)에서는 겹④가 **한 번도 안 잔다** — 이 수정이 매 버킷에
    지연을 얹지 않는다는 확인."""
    slept: list[float] = []

    async def _record(seconds: float) -> None:
        slept.append(seconds)

    bus = FakeBus()
    composer = _composer(tmp_path, bus, sleep=_record)
    for minute in range(30, 35):
        await composer.handle_one_minute_bar(_m1(minute))
    await composer.flush_due_horizon(Horizon.M5)

    assert slept == []
    assert [b.volume for _, b in bus.published] == [50]


async def test_incomplete_flush_is_logged_when_the_last_minute_never_trades(
    tmp_path: Path, monkeypatch
):
    """거래가 없는 분은 봉 자체가 안 나온다 — 무한 대기는 유실보다 나쁘므로 확정하되,
    짧은 봉이 나갔다는 사실은 조용하면 안 된다(L18)."""
    logged: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "messiah.data.bar_composer.mlog.log",
        lambda tag, msg, **f: logged.append((tag, f)),
    )
    bus = FakeBus()
    composer = _composer(tmp_path, bus)  # 양보만 하는 sleep — 상한을 즉시 소진한다
    for minute in range(30, 34):
        await composer.handle_one_minute_bar(_m1(minute))

    await composer.flush_due_horizon(Horizon.M5)

    assert [b.volume for _, b in bus.published] == [40]  # 4봉으로 확정됐다
    assert bus.published[0][1].quality_ok is False
    incomplete = [f for tag, f in logged if tag == "ComposerFlushedIncomplete"]
    assert len(incomplete) == 1
    assert incomplete[0]["awaited_bar_open_kst"] == _at(34).isoformat()
    assert composer.incomplete_flushes == 1


async def test_waiting_scheduler_never_flushes_a_bucket_that_rolled_over(tmp_path: Path):
    """겹②·④의 대기 중에 다음 버킷이 열리면, 깨어난 스케줄러는 **아무것도 안 해야** 한다.

    안 그러면 갓 열린 버킷을 1봉짜리로 확정하고 `_last_flushed_start`가 그 시각으로 올라가,
    뒤이어 올 그 버킷의 나머지 봉이 전부 늦은 봉으로 버려진다 — 원래 결함보다 나쁘다.
    """
    bus = FakeBus()
    composer = _composer(tmp_path, bus, sleep=asyncio.sleep)  # M5
    for minute in range(30, 34):
        await composer.handle_one_minute_bar(_m1(minute))

    async def _deliver_next_bucket() -> None:
        await asyncio.sleep(0.02)
        await composer.handle_one_minute_bar(_m1(34))  # [09:30,09:35) 완성
        await composer.handle_one_minute_bar(_m1(35))  # 겹① — 여기서 09:30이 확정된다

    delivery = asyncio.create_task(_deliver_next_bucket())
    await composer.flush_due_horizon(Horizon.M5)
    await delivery

    # 겹①이 낸 09:30 하나뿐이어야 한다 — 09:35가 1봉짜리로 따라 나가면 안 된다.
    assert [b.bar_open_kst for _, b in bus.published] == [_at(30)]
    assert bus.published[0][1].volume == 50

    # 그리고 09:35 버킷은 아직 살아 있어야 한다 — 나머지 봉을 정상으로 받는다.
    for minute in range(36, 40):
        await composer.handle_one_minute_bar(_m1(minute))
    await composer.flush_all_final()

    assert [b.bar_open_kst for _, b in bus.published] == [_at(30), _at(35)]
    assert sum(b.volume for _, b in bus.published) == 100  # 1분봉 10개 — 총합 보존
    assert composer.late_bar_drops == 0


async def test_health_reports_bucket_losses(tmp_path: Path):
    """26건이 나는 동안 heartbeat가 계속 OK였던 자리 — 손상은 일어나는 중에 보여야 한다."""
    bus = FakeBus()
    composer = _composer(tmp_path, bus)

    # 아직 아무 봉도 확정 안 했으면 OK가 아니라 **판정 불가**다(고도화 3).
    assert composer.health().level is HealthLevel.UNKNOWN

    for minute in range(30, 34):
        await composer.handle_one_minute_bar(_m1(minute))
    await composer.flush_due_horizon(Horizon.M5)  # 09:34가 안 와 짧게 확정
    await composer.handle_one_minute_bar(_m1(34))  # 뒤늦게 도착 → 버려진다

    status = composer.health()
    assert status.level is HealthLevel.WARN
    assert "버킷 손실 2건" in status.detail
    assert composer.late_bar_drops == 1
    assert composer.incomplete_flushes == 1


# ------------------- 장중 거래량 항등식 (2026-08-05 2차, 고도화 2)
#
# `ops/integrity_report.analyze_horizon_consistency`가 보는 항등식과 **같은 것**을 메모리에서
# 즉시 본다. 2026-08-05엔 첫 증거가 08:48에 있었는데 사람이 알아챈 건 한 시간 뒤였다.


async def test_volume_identity_holds_on_a_clean_session(tmp_path: Path):
    bus = FakeBus()
    composer = _composer(tmp_path, bus)  # M5
    for minute in range(30, 40):
        await composer.handle_one_minute_bar(_m1(minute))
    await composer.flush_all_final()

    identity = composer.volume_identity()["5m"]

    assert identity["composed_volume"] == 100  # 1분봉 10개 × 10
    assert identity["lost_volume"] == 0
    assert identity["lost_ratio"] == 0.0
    assert identity["bars"] == 2


async def test_volume_identity_counts_the_lost_contracts_not_just_the_bars(tmp_path: Path):
    """건수가 아니라 **거래량**이 항등식의 단위다 — "3봉이 늦었다"는 크기를 말해주지 않는다."""
    bus = FakeBus()
    composer = _composer(tmp_path, bus)
    for minute in range(30, 34):
        await composer.handle_one_minute_bar(_m1(minute))
    await composer.flush_due_horizon(Horizon.M5)  # 09:34가 안 와 4봉으로 확정
    await composer.handle_one_minute_bar(_m1(34, volume=37))  # 뒤늦게 도착 → 버려진다

    identity = composer.volume_identity()["5m"]

    assert identity["composed_volume"] == 40
    assert identity["lost_volume"] == 37
    assert identity["lost_ratio"] == pytest.approx(37 / 77)
    assert "37" in composer.health().detail


async def test_health_says_what_it_is_ok_on_the_basis_of(tmp_path: Path):
    """정상일 때도 근거를 말한다(고도화 3) — 근거 없는 OK와 구분되어야 한다."""
    bus = FakeBus()
    composer = _composer(tmp_path, bus)
    for minute in range(30, 40):
        await composer.handle_one_minute_bar(_m1(minute))

    status = composer.health()

    assert status.level is HealthLevel.OK
    assert "합성봉 1개" in status.detail
    assert "항등식 일치" in status.detail
