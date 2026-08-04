from datetime import date, datetime
from pathlib import Path

from messiah.core.messages import BarClosed, Horizon
from messiah.core.timeutil import KST
from messiah.data.archiver import ParquetArchiver


def _bar(
    minute: int,
    symbol: str = "A05608",
    horizon: Horizon = Horizon.M1,
    o_ticks=100,
    h_ticks=105,
    l_ticks=95,
    c_ticks=102,
    volume=10,
    quality_ok=True,
):
    return BarClosed(
        symbol=symbol,
        horizon=horizon,
        bar_open_kst=datetime(2026, 7, 22, 15, minute, tzinfo=KST),
        o_ticks=o_ticks,
        h_ticks=h_ticks,
        l_ticks=l_ticks,
        c_ticks=c_ticks,
        volume=volume,
        quality_ok=quality_ok,
    )


def test_append_bar_is_readable_back_for_that_symbol_horizon_date(tmp_path: Path):
    """물리 배치(장중 조각 vs 장후 통합본)는 `read_day()` 뒤에 숨는다 — 테스트도 경로가
    아니라 "그날치를 다시 읽을 수 있는가"를 본다(`data/archiver.py` "조각 쓰기")."""
    archiver = ParquetArchiver(tmp_path)

    archiver.append_bar(_bar(minute=29))

    df = archiver.read_day("A05608", Horizon.M1, date(2026, 7, 22))
    assert df is not None and df.height == 1


def test_append_bar_round_trips_values(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)
    bar = _bar(
        minute=29, o_ticks=100, h_ticks=110, l_ticks=90, c_ticks=105, volume=42, quality_ok=False
    )

    archiver.append_bar(bar)

    df = ParquetArchiver(tmp_path).read_day("A05608", Horizon.M1, date(2026, 7, 22))
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

    df = ParquetArchiver(tmp_path).read_day("A05608", Horizon.M1, date(2026, 7, 22))
    assert df.height == 3
    assert df["bar_open_kst"].to_list() == sorted(df["bar_open_kst"].to_list())  # 정렬됨


def test_append_bar_same_minute_overwrites_not_duplicates(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar(minute=29, quality_ok=False))
    archiver.append_bar(_bar(minute=29, quality_ok=True))  # 같은 분 재처리(재시작 등)

    df = ParquetArchiver(tmp_path).read_day("A05608", Horizon.M1, date(2026, 7, 22))
    assert df.height == 1
    assert df.row(0, named=True)["quality_ok"] is True  # 나중 값으로 갱신됨


def test_append_bar_separates_different_symbols(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar(minute=29, symbol="A05608"))
    archiver.append_bar(_bar(minute=29, symbol="OTHER"))

    a = archiver.read_day("A05608", Horizon.M1, date(2026, 7, 22))
    other = archiver.read_day("OTHER", Horizon.M1, date(2026, 7, 22))
    assert a is not None and a.height == 1
    assert other is not None and other.height == 1


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

    assert archiver.read_day("A05608", Horizon.M1, date(2026, 7, 22)) is not None
    assert archiver.read_day("A05608", Horizon.M1, date(2026, 7, 23)) is not None


def test_append_bar_separates_different_horizons(tmp_path: Path):
    """2026-07-23 발견: 서로 다른 Horizon의 봉이 같은 bar_open_kst를 가질 수 있어(예: 5m봉과
    1m봉이 둘 다 09:30:00에 시작) horizon을 경로·dedup 키에 안 넣으면 서로를 지워버린다."""
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar(minute=29, horizon=Horizon.M1, c_ticks=101))
    archiver.append_bar(_bar(minute=29, horizon=Horizon.M5, c_ticks=205))

    m1 = archiver.read_day("A05608", Horizon.M1, date(2026, 7, 22))
    m5 = archiver.read_day("A05608", Horizon.M5, date(2026, 7, 22))
    assert m1 is not None and m1.row(0, named=True)["c_ticks"] == 101
    assert m5 is not None and m5.row(0, named=True)["c_ticks"] == 205


# ---------------------------------------------------------------- write_day (백필 전용)


def test_write_day_replaces_the_whole_day_instead_of_merging(tmp_path: Path):
    """백필의 목적이 '수집값을 거래소 공식값으로 갈아끼우기'라 병합이면 안 된다."""
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar(1, volume=50))  # 오염된 수집분
    archiver.append_bar(_bar(2, volume=50))

    written = archiver.write_day("A05608", Horizon.M1, [_bar(1, volume=100)])

    assert written == 1
    frame = archiver.read_day("A05608", Horizon.M1, date(2026, 7, 22))
    assert frame.height == 1  # 2분봉은 남지 않는다 — 하루를 통째로 교체했다
    assert frame["volume"].to_list() == [100]


def test_write_day_removes_shards_so_stale_rows_cannot_win(tmp_path: Path):
    """조각을 안 지우면 `read_day()`가 keep="last"로 조각을 이긴 것으로 취급해
    옛 오염값이 되살아난다(조각이 통합본보다 나중 소스)."""
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar(1, volume=50))
    day = date(2026, 7, 22)
    assert (tmp_path / "A05608" / "1m" / day.isoformat()).is_dir()

    archiver.write_day("A05608", Horizon.M1, [_bar(1, volume=100)])

    assert not (tmp_path / "A05608" / "1m" / day.isoformat()).exists()
    assert archiver.read_day("A05608", Horizon.M1, day)["volume"].to_list() == [100]


def test_write_day_with_no_bars_does_nothing(tmp_path: Path):
    """백필 실패와 '그날 휴장'을 구분할 수 없게 만들면 안 된다 — 빈 파일로 덮지 않는다."""
    archiver = ParquetArchiver(tmp_path)
    archiver.append_bar(_bar(1, volume=50))

    assert archiver.write_day("A05608", Horizon.M1, []) == 0
    assert archiver.read_day("A05608", Horizon.M1, date(2026, 7, 22)).height == 1


def test_read_day_bars_round_trips_through_write_day(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)
    archiver.write_day("A05608", Horizon.M1, [_bar(1, c_ticks=111), _bar(2, c_ticks=222)])

    bars = archiver.read_day_bars("A05608", Horizon.M1, date(2026, 7, 22))

    assert [b.c_ticks for b in bars] == [111, 222]
    assert all(b.symbol == "A05608" and b.horizon is Horizon.M1 for b in bars)


def test_read_day_bars_returns_empty_for_missing_day(tmp_path: Path):
    archiver = ParquetArchiver(tmp_path)
    assert archiver.read_day_bars("A05608", Horizon.M1, date(2026, 7, 22)) == []
