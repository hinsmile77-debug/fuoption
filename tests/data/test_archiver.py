from datetime import datetime
from pathlib import Path

import polars as pl
from messiah.core.messages import BarClosed, Horizon
from messiah.core.timeutil import KST
from messiah.data.archiver import ParquetArchiver


def _bar(
    minute: int,
    symbol: str = "A05608",
    o_ticks=100,
    h_ticks=105,
    l_ticks=95,
    c_ticks=102,
    volume=10,
    quality_ok=True,
):
    return BarClosed(
        symbol=symbol,
        horizon=Horizon.M1,
        bar_open_kst=datetime(2026, 7, 22, 15, minute, tzinfo=KST),
        o_ticks=o_ticks,
        h_ticks=h_ticks,
        l_ticks=l_ticks,
        c_ticks=c_ticks,
        volume=volume,
        quality_ok=quality_ok,
    )


def test_append_bar_creates_file_at_symbol_date_path(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)
    bar = _bar(minute=29)

    archiver.append_bar(bar)

    expected_path = tmp_path / "A05608" / "2026-07-22.parquet"
    assert expected_path.exists()


def test_append_bar_round_trips_values(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)
    bar = _bar(
        minute=29, o_ticks=100, h_ticks=110, l_ticks=90, c_ticks=105, volume=42, quality_ok=False
    )

    archiver.append_bar(bar)

    df = pl.read_parquet(tmp_path / "A05608" / "2026-07-22.parquet")
    row = df.row(0, named=True)
    assert row["symbol"] == "A05608"
    assert row["horizon"] == "1m"
    assert row["o_ticks"] == 100
    assert row["h_ticks"] == 110
    assert row["l_ticks"] == 90
    assert row["c_ticks"] == 105
    assert row["volume"] == 42
    assert row["quality_ok"] is False


def test_append_bar_accumulates_multiple_minutes(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar(minute=29))
    archiver.append_bar(_bar(minute=30))
    archiver.append_bar(_bar(minute=31))

    df = pl.read_parquet(tmp_path / "A05608" / "2026-07-22.parquet")
    assert df.height == 3
    assert df["bar_open_kst"].to_list() == sorted(df["bar_open_kst"].to_list())  # 정렬됨


def test_append_bar_same_minute_overwrites_not_duplicates(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar(minute=29, quality_ok=False))
    archiver.append_bar(_bar(minute=29, quality_ok=True))  # 같은 분 재처리(재시작 등)

    df = pl.read_parquet(tmp_path / "A05608" / "2026-07-22.parquet")
    assert df.height == 1
    assert df.row(0, named=True)["quality_ok"] is True  # 나중 값으로 갱신됨


def test_append_bar_separates_different_symbols(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar(minute=29, symbol="A05608"))
    archiver.append_bar(_bar(minute=29, symbol="OTHER"))

    assert (tmp_path / "A05608" / "2026-07-22.parquet").exists()
    assert (tmp_path / "OTHER" / "2026-07-22.parquet").exists()
    a = pl.read_parquet(tmp_path / "A05608" / "2026-07-22.parquet")
    other = pl.read_parquet(tmp_path / "OTHER" / "2026-07-22.parquet")
    assert a.height == 1
    assert other.height == 1


def test_append_bar_separates_different_dates(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar(minute=29))
    next_day_bar = BarClosed(
        symbol="A05608",
        horizon=Horizon.M1,
        bar_open_kst=datetime(2026, 7, 23, 9, 0, tzinfo=KST),
        o_ticks=1,
        h_ticks=1,
        l_ticks=1,
        c_ticks=1,
        volume=1,
        quality_ok=True,
    )
    archiver.append_bar(next_day_bar)

    assert (tmp_path / "A05608" / "2026-07-22.parquet").exists()
    assert (tmp_path / "A05608" / "2026-07-23.parquet").exists()
