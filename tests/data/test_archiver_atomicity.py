"""원자적 쓰기·재시도·웜스타트 로더 검증 (2026-07-30 UI 크래시 사고 대응).

사고 요약은 `data/archiver.py` 모듈 docstring 참고 — 제자리 덮어쓰기의 truncate 중간 상태를
다른 프로세스(Command Center UI)가 읽다가 네이티브 크래시로 죽었다. 여기서는 실제 동시
프로세스를 띄우는 대신, `os.replace`를 주입해 "교체 직전에 대상 파일이 어떤 상태였는지"를
직접 관측한다 — 실제 파일 잠금 타이밍에 의존하지 않아 결정적으로 재현된다.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

import polars as pl
import pytest
from messiah.core.messages import BarClosed, Horizon
from messiah.core.timeutil import KST
from messiah.data.archiver import (
    ParquetArchiver,
    TornParquetError,
    day_signature,
    read_parquet_without_mmap,
)


def _bar(minute: int, *, day: int = 22, horizon: Horizon = Horizon.M1, c_ticks: int = 102):
    return BarClosed(
        symbol="A05608",
        horizon=horizon,
        bar_open_kst=datetime(2026, 7, day, 15, minute, tzinfo=KST),
        o_ticks=100,
        h_ticks=105,
        l_ticks=95,
        c_ticks=c_ticks,
        volume=10,
        quality_ok=True,
    )


def _target(tmp_path: Path, day: int = 22, horizon: Horizon = Horizon.M1) -> Path:
    """그날치를 담고 있는 **실제** 파일 — 장중이면 시간대 조각, 통합 후면 통합본이다.
    물리 배치를 테스트에 하드코딩하면 조각화 같은 내부 변경마다 테스트가 깨진다."""
    sources = ParquetArchiver(tmp_path).day_sources("A05608", horizon, date(2026, 7, day))
    assert len(sources) == 1, f"소스가 1개일 것으로 기대했으나 {sources}"
    return sources[0]


# ---------------------------------------------------------------- 원자적 교체


def test_destination_is_never_truncated_during_write(tmp_path: Path):
    """대상 파일은 교체 순간까지 '이전 완본'이어야 한다 — 예전 구현은 이 시점에 길이 0이
    관측됐고, 그걸 읽은 UI가 죽었다."""
    observed: list[int] = []

    def _watching_replace(src: Path, dst: Path) -> None:
        # 교체 직전의 대상 파일 상태를 기록 — 존재한다면 반드시 완전한 Parquet이어야 한다
        observed.append(read_parquet_without_mmap(Path(dst)).height if Path(dst).exists() else -1)
        os.replace(src, dst)

    archiver = ParquetArchiver(tmp_path, replace=_watching_replace)
    archiver.append_bar(_bar(29))
    archiver.append_bar(_bar(30))
    archiver.append_bar(_bar(31))

    # 1회차: 대상 없음(-1), 2회차: 이전 완본 1행, 3회차: 이전 완본 2행
    assert observed == [-1, 1, 2]


def test_no_temp_files_left_behind(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar(29))
    archiver.append_bar(_bar(30))

    leftovers = list((tmp_path / "A05608" / "1m").rglob("*.tmp"))
    assert leftovers == []


def test_temp_file_name_is_pid_scoped(tmp_path: Path):
    """여러 프로세스가 같은 경로에 append해도 서로의 임시 파일을 덮어쓰지 않아야 한다."""
    seen: list[str] = []

    def _capturing_replace(src: Path, dst: Path) -> None:
        seen.append(Path(src).name)
        os.replace(src, dst)

    ParquetArchiver(tmp_path, replace=_capturing_replace).append_bar(_bar(29))

    assert seen == [f"15.parquet.{os.getpid()}.tmp"]  # 15시대 조각의 임시 파일


# ---------------------------------------------------------------- 교체 재시도 (Windows 1224)


def test_replace_retries_until_it_succeeds(tmp_path: Path):
    """polars의 mmap이 살아있는 동안 rename은 OSError(1224)로 거부된다 — 곧 풀리므로
    짧은 백오프 재시도로 흡수해야 한다(예전엔 이 실패로 봉이 영구 소실됐다)."""
    attempts = {"n": 0}
    slept: list[float] = []

    def _flaky_replace(src: Path, dst: Path) -> None:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise OSError(1224, "ERROR_USER_MAPPED_FILE")
        os.replace(src, dst)

    archiver = ParquetArchiver(tmp_path, replace=_flaky_replace, sleep=slept.append)
    archiver.append_bar(_bar(29))

    assert attempts["n"] == 3
    assert len(slept) == 2  # 실패 2회분만 대기
    assert _target(tmp_path).exists()
    assert read_parquet_without_mmap(_target(tmp_path)).height == 1


def test_replace_raises_and_cleans_temp_after_exhausting_retries(tmp_path: Path):
    """끝까지 실패하면 조용히 삼키지 않고 올린다(L22) — 호출측이 로깅한다. 임시 파일은
    남기지 않는다."""

    def _always_failing_replace(src: Path, dst: Path) -> None:
        raise OSError(1224, "ERROR_USER_MAPPED_FILE")

    archiver = ParquetArchiver(
        tmp_path, replace=_always_failing_replace, sleep=lambda _: None, replace_attempts=3
    )

    with pytest.raises(OSError):
        archiver.append_bar(_bar(29))

    assert list((tmp_path / "A05608" / "1m").rglob("*.tmp")) == []
    assert ParquetArchiver(tmp_path).read_day("A05608", Horizon.M1, date(2026, 7, 22)) is None


def test_failed_replace_leaves_previous_content_intact(tmp_path: Path):
    """교체가 실패해도 이미 적재된 봉은 그대로 남아야 한다 — 실패가 기존 아카이브를 손상시키면
    안 된다."""
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar(29))

    def _always_failing_replace(src: Path, dst: Path) -> None:
        raise OSError(1224, "ERROR_USER_MAPPED_FILE")

    broken = ParquetArchiver(
        tmp_path, replace=_always_failing_replace, sleep=lambda _: None, replace_attempts=2
    )
    with pytest.raises(OSError):
        broken.append_bar(_bar(30))

    df = read_parquet_without_mmap(_target(tmp_path))
    assert df.height == 1
    assert df.row(0, named=True)["c_ticks"] == 102


# ---------------------------------------------------------------- mmap 없는 읽기


def test_read_parquet_without_mmap_round_trips(tmp_path: Path):
    ParquetArchiver(tmp_path).append_bar(_bar(29))

    df = read_parquet_without_mmap(_target(tmp_path))

    assert df.height == 1
    assert df.row(0, named=True)["symbol"] == "A05608"


def test_read_parquet_without_mmap_does_not_block_replace(tmp_path: Path):
    """읽어둔 프레임을 들고 있는 상태에서도 같은 파일을 교체할 수 있어야 한다 —
    `pl.read_parquet(path)`(mmap)였다면 Windows에서 1224로 막히던 지점."""
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar(29))

    held = read_parquet_without_mmap(_target(tmp_path))  # noqa: F841 — 참조를 일부러 유지
    archiver.append_bar(_bar(30))  # 예외 없이 성공해야 함

    assert read_parquet_without_mmap(_target(tmp_path)).height == 2


# ---------------------------------------------------------------- 웜스타트 로더


def test_load_recent_bars_returns_kst_wall_clock(tmp_path: Path):
    """Parquet 왕복은 UTC로 정규화한다 — 장전 08:45 KST 봉이 전일 23:45 UTC로 잡히면
    `SessionState`의 일자 롤오버가 어긋나므로 KST로 되돌려야 한다."""
    archiver = ParquetArchiver(tmp_path)
    pre_open = BarClosed(
        symbol="A05608",
        horizon=Horizon.M1,
        bar_open_kst=datetime(2026, 7, 30, 8, 45, tzinfo=KST),
        o_ticks=1,
        h_ticks=1,
        l_ticks=1,
        c_ticks=1,
        volume=1,
        quality_ok=True,
    )
    archiver.append_bar(pre_open)

    bars = archiver.load_recent_bars(
        "A05608", Horizon.M1, on_or_before=date(2026, 7, 30), max_bars=10
    )

    assert len(bars) == 1
    assert bars[0].bar_open_kst.hour == 8
    assert bars[0].bar_open_kst.date() == date(2026, 7, 30)


def test_load_recent_bars_includes_today(tmp_path: Path):
    """장중 재시작 시 오늘 오전에 이미 쌓인 봉을 되찾는 것이 주 용도다."""
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar(29, day=22))
    archiver.append_bar(_bar(30, day=22))

    bars = archiver.load_recent_bars(
        "A05608", Horizon.M1, on_or_before=date(2026, 7, 22), max_bars=10
    )

    assert len(bars) == 2


def test_load_recent_bars_spans_multiple_days_in_chronological_order(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar(29, day=22))
    archiver.append_bar(_bar(29, day=23))
    archiver.append_bar(_bar(30, day=23))

    bars = archiver.load_recent_bars(
        "A05608", Horizon.M1, on_or_before=date(2026, 7, 23), max_bars=10
    )

    assert [b.bar_open_kst.day for b in bars] == [22, 23, 23]
    assert bars == sorted(bars, key=lambda b: b.bar_open_kst)


def test_load_recent_bars_excludes_days_after_cutoff(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar(29, day=22))
    archiver.append_bar(_bar(29, day=23))

    bars = archiver.load_recent_bars(
        "A05608", Horizon.M1, on_or_before=date(2026, 7, 22), max_bars=10
    )

    assert [b.bar_open_kst.day for b in bars] == [22]


def test_load_recent_bars_caps_at_max_bars_keeping_newest(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)
    for minute in (29, 30, 31, 32):
        archiver.append_bar(_bar(minute, day=22))

    bars = archiver.load_recent_bars(
        "A05608", Horizon.M1, on_or_before=date(2026, 7, 22), max_bars=2
    )

    assert [b.bar_open_kst.minute for b in bars] == [31, 32]


def test_load_recent_bars_separates_horizons(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar(29, horizon=Horizon.M1, c_ticks=101))
    archiver.append_bar(_bar(29, horizon=Horizon.M5, c_ticks=205))

    m5 = archiver.load_recent_bars(
        "A05608", Horizon.M5, on_or_before=date(2026, 7, 22), max_bars=10
    )

    assert [b.c_ticks for b in m5] == [205]


def test_load_recent_bars_skips_unreadable_file(tmp_path: Path):
    """깨진 파일 하나가 웜스타트 전체를 막으면 안 된다 — 나머지 날짜는 살려야 한다."""
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar(29, day=22))
    (tmp_path / "A05608" / "1m" / "2026-07-23.parquet").write_bytes(b"not a parquet file")

    bars = archiver.load_recent_bars(
        "A05608", Horizon.M1, on_or_before=date(2026, 7, 23), max_bars=10
    )

    assert [b.bar_open_kst.day for b in bars] == [22]


def test_load_recent_bars_ignores_stray_temp_files(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar(29, day=22))
    (tmp_path / "A05608" / "1m" / "2026-07-23.parquet.9999.tmp").write_bytes(b"junk")

    bars = archiver.load_recent_bars(
        "A05608", Horizon.M1, on_or_before=date(2026, 7, 23), max_bars=10
    )

    assert len(bars) == 1


def test_load_recent_bars_empty_when_no_directory(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)

    assert (
        archiver.load_recent_bars("NOPE", Horizon.M1, on_or_before=date(2026, 7, 22), max_bars=10)
        == []
    )


def test_load_recent_bars_round_trips_all_fields(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar(29))

    bar = archiver.load_recent_bars(
        "A05608", Horizon.M1, on_or_before=date(2026, 7, 22), max_bars=1
    )[0]

    assert (bar.o_ticks, bar.h_ticks, bar.l_ticks, bar.c_ticks) == (100, 105, 95, 102)
    assert bar.volume == 10
    assert bar.quality_ok is True
    assert bar.horizon is Horizon.M1


def test_load_recent_bars_does_not_leak_polars_frame_types(tmp_path: Path):
    """반환 타입이 BarClosed여야 FeatureEngine이 그대로 소비할 수 있다."""
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar(29))

    bars = archiver.load_recent_bars(
        "A05608", Horizon.M1, on_or_before=date(2026, 7, 22), max_bars=1
    )

    assert all(isinstance(b, BarClosed) for b in bars)
    assert not isinstance(bars, pl.DataFrame)


# ---------------------------------------------------------------- 찢어진 파일 형태 검사


def test_torn_parquet_is_rejected_before_reaching_the_native_parser(tmp_path: Path):
    """2026-07-30 10:01 회귀 방지 — 바이트 복사만으로는 UI가 또 죽었다(같은 fault offset).
    잘린 바이트를 polars 디코더에 넘기면 파이썬 예외가 아니라 프로세스가 즉사한다."""
    ParquetArchiver(tmp_path).append_bar(_bar(29))
    path = _target(tmp_path)
    whole = path.read_bytes()

    path.write_bytes(whole[: len(whole) // 2])  # 쓰기 도중 상태 재현(꼬리 매직 없음)

    with pytest.raises(TornParquetError):
        read_parquet_without_mmap(path)


def test_empty_file_is_rejected_as_torn(tmp_path: Path):
    path = tmp_path / "empty.parquet"
    path.write_bytes(b"")

    with pytest.raises(TornParquetError):
        read_parquet_without_mmap(path)


def test_file_without_header_magic_is_rejected(tmp_path: Path):
    path = tmp_path / "junk.parquet"
    path.write_bytes(b"not a parquet at all but long enough PAR1")

    with pytest.raises(TornParquetError):
        read_parquet_without_mmap(path)


def test_torn_parquet_error_is_a_value_error(tmp_path: Path):
    """호출측의 일반 "읽기 실패" 처리에 자연히 걸려야 한다 — 새 except 절을 강요하지 않는다."""
    assert issubclass(TornParquetError, ValueError)


def test_complete_file_still_reads_normally(tmp_path: Path):
    ParquetArchiver(tmp_path).append_bar(_bar(29))

    assert read_parquet_without_mmap(_target(tmp_path)).height == 1


def test_load_recent_bars_skips_a_torn_file(tmp_path: Path):
    """장중 재시작 웜스타트가 쓰기 중인 오늘 파일에 걸려 통째로 실패하면 안 된다."""
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar(29, day=22))
    archiver.append_bar(_bar(29, day=23))
    torn = _target(tmp_path, day=23)
    torn.write_bytes(torn.read_bytes()[:20])

    bars = archiver.load_recent_bars(
        "A05608", Horizon.M1, on_or_before=date(2026, 7, 23), max_bars=10
    )

    assert [b.bar_open_kst.day for b in bars] == [22]


# ---------------------------------------------------------------- 조각 쓰기 · 장후 통합


def _shard_dir(tmp_path: Path, day: int = 22, horizon: str = "1m") -> Path:
    return tmp_path / "A05608" / horizon / f"2026-07-{day:02d}"


def _canonical(tmp_path: Path, day: int = 22, horizon: str = "1m") -> Path:
    return tmp_path / "A05608" / horizon / f"2026-07-{day:02d}.parquet"


def _bar_at(hour: int, minute: int, *, day: int = 22) -> BarClosed:
    return BarClosed(
        symbol="A05608",
        horizon=Horizon.M1,
        bar_open_kst=datetime(2026, 7, day, hour, minute, tzinfo=KST),
        o_ticks=100,
        h_ticks=105,
        l_ticks=95,
        c_ticks=100 + minute,
        volume=10,
        quality_ok=True,
    )


def test_intraday_writes_go_to_hour_shards(tmp_path: Path):
    """다시 쓰는 범위를 하루치에서 한 시간치로 줄이는 것이 조각화의 목적이다."""
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar_at(9, 0))
    archiver.append_bar(_bar_at(9, 30))
    archiver.append_bar(_bar_at(10, 0))

    shards = sorted(p.name for p in _shard_dir(tmp_path).glob("*.parquet"))

    assert shards == ["09.parquet", "10.parquet"]
    assert not _canonical(tmp_path).exists()  # 장중엔 통합본이 아직 없다


def test_read_day_merges_shards_transparently(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)
    for hour, minute in [(9, 0), (9, 30), (10, 0), (11, 15)]:
        archiver.append_bar(_bar_at(hour, minute))

    df = archiver.read_day("A05608", Horizon.M1, date(2026, 7, 22))

    assert df is not None and df.height == 4
    assert df["bar_open_kst"].to_list() == sorted(df["bar_open_kst"].to_list())


def test_compaction_produces_the_pre_sharding_layout(tmp_path: Path):
    """Digital Twin·백테스트가 보는 과거 데이터의 물리 배치는 조각화 이전과 같아야 한다."""
    archiver = ParquetArchiver(tmp_path)
    for hour, minute in [(9, 0), (9, 30), (10, 0)]:
        archiver.append_bar(_bar_at(hour, minute))

    rows = archiver.compact_day("A05608", Horizon.M1, date(2026, 7, 22))

    assert rows == 3
    assert _canonical(tmp_path).exists()
    assert not _shard_dir(tmp_path).exists()  # 조각 디렉터리까지 정리
    assert read_parquet_without_mmap(_canonical(tmp_path)).height == 3


def test_read_day_is_identical_before_and_after_compaction(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)
    for hour, minute in [(9, 0), (9, 30), (10, 0)]:
        archiver.append_bar(_bar_at(hour, minute))
    before = archiver.read_day("A05608", Horizon.M1, date(2026, 7, 22))

    archiver.compact_day("A05608", Horizon.M1, date(2026, 7, 22))
    after = archiver.read_day("A05608", Horizon.M1, date(2026, 7, 22))

    assert before is not None and after is not None
    assert before.equals(after)


def test_compaction_is_idempotent(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar_at(9, 0))
    archiver.compact_day("A05608", Horizon.M1, date(2026, 7, 22))

    assert archiver.compact_day("A05608", Horizon.M1, date(2026, 7, 22)) == 0  # 조각 없음
    assert read_parquet_without_mmap(_canonical(tmp_path)).height == 1


def test_shards_written_after_compaction_win_over_the_canonical_file(tmp_path: Path):
    """장중 재시작으로 통합본이 이미 있는 날에 조각이 새로 생기는 경우 — 조각이 최신이다."""
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar_at(9, 0))
    archiver.compact_day("A05608", Horizon.M1, date(2026, 7, 22))

    revised = _bar_at(9, 0).model_copy(update={"c_ticks": 999})
    archiver.append_bar(revised)

    df = archiver.read_day("A05608", Horizon.M1, date(2026, 7, 22))
    assert df is not None and df.height == 1
    assert df.row(0, named=True)["c_ticks"] == 999


def test_available_days_sees_both_layouts(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar_at(9, 0, day=22))
    archiver.compact_day("A05608", Horizon.M1, date(2026, 7, 22))  # 22일은 통합본
    archiver.append_bar(_bar_at(9, 0, day=23))  # 23일은 조각(장중)

    assert archiver.available_days("A05608", Horizon.M1) == [
        date(2026, 7, 22),
        date(2026, 7, 23),
    ]


def test_compaction_writes_canonical_before_deleting_shards(tmp_path: Path):
    """순서가 반대면 중간에 죽었을 때 데이터가 사라진다."""
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar_at(9, 0))
    observed: list[bool] = []

    real_replace = os.replace

    def _watching_replace(src, dst):
        real_replace(src, dst)
        observed.append(_shard_dir(tmp_path).is_dir())  # 교체 시점에 조각은 아직 살아있어야 함

    ParquetArchiver(tmp_path, replace=_watching_replace).compact_day(
        "A05608", Horizon.M1, date(2026, 7, 22)
    )

    assert observed == [True]


def test_compaction_of_a_day_without_data_is_a_noop(tmp_path: Path):
    assert ParquetArchiver(tmp_path).compact_day("A05608", Horizon.M1, date(2026, 7, 22)) == 0


def test_day_signature_changes_when_a_new_shard_appears(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar_at(9, 0))
    first = day_signature(archiver.day_sources("A05608", Horizon.M1, date(2026, 7, 22)))

    archiver.append_bar(_bar_at(10, 0))
    second = day_signature(archiver.day_sources("A05608", Horizon.M1, date(2026, 7, 22)))

    assert first != second  # 파일 하나만 보면 새 시간대 조각을 놓친다
