from datetime import date, datetime
from pathlib import Path

from messiah.core.messages import BarClosed, Horizon
from messiah.core.timeutil import KST
from messiah.data.archiver import ParquetArchiver
from messiah.simulator.replay import ParquetBarReplaySource

_SYMBOL = "A05608"


def _m1(minute: int, day: int = 27) -> BarClosed:
    return BarClosed(
        symbol=_SYMBOL,
        horizon=Horizon.M1,
        bar_open_kst=datetime(2026, 7, day, 9, minute, tzinfo=KST),
        o_ticks=100,
        h_ticks=105,
        l_ticks=95,
        c_ticks=102,
        volume=10,
    )


def _m5(minute: int, day: int = 27) -> BarClosed:
    return BarClosed(
        symbol=_SYMBOL,
        horizon=Horizon.M5,
        bar_open_kst=datetime(2026, 7, day, 9, minute, tzinfo=KST),
        o_ticks=100,
        h_ticks=110,
        l_ticks=90,
        c_ticks=103,
        volume=50,
    )


def _archive(base: Path, *bars: BarClosed) -> None:
    archiver = ParquetArchiver(base)
    for bar in bars:
        archiver.append_bar(bar)


def test_sorts_by_confirm_time_shorter_horizon_first_on_tie(tmp_path: Path):
    # 5분봉(09:30 시작)은 09:35에 확정 — 그 순간 1분봉 09:34분(확정 09:35)과 동률.
    # 실제 운영에선 1분봉 4개가 전부 확정된 뒤에야 5분봉이 확정되므로 1분봉이 먼저 와야 한다.
    _archive(tmp_path, _m1(30), _m1(31), _m1(32), _m1(33), _m1(34), _m5(30))

    source = ParquetBarReplaySource(tmp_path, _SYMBOL)
    bars = source.load(date(2026, 7, 27), date(2026, 7, 27))

    assert [(b.horizon, b.bar_open_kst.minute) for b in bars] == [
        (Horizon.M1, 30),
        (Horizon.M1, 31),
        (Horizon.M1, 32),
        (Horizon.M1, 33),
        (Horizon.M1, 34),
        (Horizon.M5, 30),
    ]


def test_missing_dates_are_skipped_silently(tmp_path: Path):
    _archive(tmp_path, _m1(30, day=27))

    source = ParquetBarReplaySource(tmp_path, _SYMBOL)
    bars = source.load(date(2026, 7, 26), date(2026, 7, 28))

    assert len(bars) == 1
    assert bars[0].bar_open_kst.day == 27


def test_missing_symbol_directory_returns_empty(tmp_path: Path):
    source = ParquetBarReplaySource(tmp_path, "NOTHING_HERE")
    bars = source.load(date(2026, 7, 27), date(2026, 7, 27))
    assert bars == []


def test_spans_multiple_dates_in_order(tmp_path: Path):
    _archive(tmp_path, _m1(59, day=27), _m1(0, day=28))

    source = ParquetBarReplaySource(tmp_path, _SYMBOL, horizons=[Horizon.M1])
    bars = source.load(date(2026, 7, 27), date(2026, 7, 28))

    assert [(b.bar_open_kst.day, b.bar_open_kst.minute) for b in bars] == [(27, 59), (28, 0)]


def test_horizon_filter_restricts_loaded_horizons(tmp_path: Path):
    _archive(tmp_path, _m1(30), _m5(30))

    source = ParquetBarReplaySource(tmp_path, _SYMBOL, horizons=[Horizon.M1])
    bars = source.load(date(2026, 7, 27), date(2026, 7, 27))

    assert len(bars) == 1
    assert bars[0].horizon == Horizon.M1
