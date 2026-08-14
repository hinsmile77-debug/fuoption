"""롤 경계를 넘는 웜스타트 — 2026-08-14 F-1 · F-9.

2026-08-14 첫 월물 롤(A05608 → A05609)에서 `load_recent_bars()`가 심볼 단일 색인이라
0봉을 돌려줬고, 그 **하나**가 세 소비처를 동시에 무너뜨렸다:

    피처 롤링 윈도    전 Horizon 0봉 → 1m NaN 84.7%로 개장, 30m은 종일 62% 아래로 안 감
    국면 이력         0봉 < 하한 22봉 → 종일 UNKNOWN, 판단 14/14가 NO_TRADE(gate=regime)
    옵션체인 기준가    장전 시드 없음 → 08:21~08:43 10사이클 스킵(소급 경로 없어 영구 결손)

셋 다 `ParquetArchiver.load_recent_bars()` 한 곳에 매달려 있었다. 그래서 고치는 자리도
한 곳이고, **이어 붙였다는 사실이 데이터에 남는지**가 이 파일의 주된 관심사다(R10).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import polars as pl
import pytest

from messiah.core.messages import BarClosed, BarSession, Horizon
from messiah.core.timeutil import KST
from messiah.data import backfill
from messiah.data.archiver import ParquetArchiver

_OLD = "A05608"
_NEW = "A05609"
_ROLL_DAY = date(2026, 8, 14)


def _seed(archiver: ParquetArchiver, symbol: str, day: date, count: int, base_tick: int) -> None:
    """그날 30m 봉 `count`개를 아카이브에 심는다."""
    bars = [
        BarClosed(
            symbol=symbol,
            horizon=Horizon.M30,
            bar_open_kst=datetime(day.year, day.month, day.day, 9, 0, tzinfo=KST)
            + timedelta(minutes=30 * i),
            o_ticks=base_tick,
            h_ticks=base_tick + 2,
            l_ticks=base_tick - 2,
            c_ticks=base_tick + 1,
            volume=100,
            trades=10,
            session=BarSession.REGULAR,
            quality_ok=True,
        )
        for i in range(count)
    ]
    archiver.write_day(symbol, Horizon.M30, bars)


# ------------------------------------------------------------- 선행 월물 산출


@pytest.mark.parametrize(
    ("day", "symbol", "expected"),
    [
        (_ROLL_DAY, _NEW, [_NEW, _OLD]),
        (date(2026, 8, 13), _OLD, [_OLD, "A05607"]),
        (date(2026, 9, 11), "A05610", ["A05610", _NEW]),
    ],
)
def test_chain_puts_today_first_then_the_contract_it_replaced(
    day: date, symbol: str, expected: list[str]
) -> None:
    """롤 당일에는 **같은 달 안에서** 근월이 바뀐다 — 곧바로 전달로 물러나면 직전 월물을
    통째로 건너뛴다(구현 중 실제로 한 번 그렇게 틀렸다)."""
    assert backfill.warmstart_symbol_chain(symbol, day) == expected


# ------------------------------------------------------------- 이어 읽기와 출처


def test_rollover_day_fills_the_window_from_the_previous_contract(tmp_path) -> None:
    """오늘의 사고 그 자체 — 신규 월물 아카이브가 비었을 때 창이 채워지는가."""
    archiver = ParquetArchiver(tmp_path)
    for i, day in enumerate([date(2026, 8, 11), date(2026, 8, 12), date(2026, 8, 13)]):
        _seed(archiver, _OLD, day, count=10, base_tick=1000 + i)

    bars, sources = archiver.load_recent_bars_by_source(
        [_NEW, _OLD], Horizon.M30, on_or_before=_ROLL_DAY, max_bars=25
    )

    assert len(bars) == 25
    # **조용히 잇지 않는다** — 어디서 왔는지가 남아야 한다.
    assert sources == {_NEW: 0, _OLD: 25}
    # 이어 붙인 봉은 **원래 월물의 코드를 그대로 단다**. 요청 심볼로 덮어쓰면 나중에
    # "이 구간이 어디서 왔나"를 아무도 알 수 없다.
    assert {b.symbol for b in bars} == {_OLD}
    assert bars == sorted(bars, key=lambda b: b.bar_open_kst)


def test_sources_report_what_was_used_not_what_was_read(tmp_path) -> None:
    """상한에 걸려 잘린 뒤의 **실제 구성**을 세야 한다 — 앞쪽(선행 월물)부터 잘린다."""
    archiver = ParquetArchiver(tmp_path)
    _seed(archiver, _OLD, date(2026, 8, 13), count=20, base_tick=1000)
    _seed(archiver, _NEW, _ROLL_DAY, count=5, base_tick=1100)

    bars, sources = archiver.load_recent_bars_by_source(
        [_NEW, _OLD], Horizon.M30, on_or_before=_ROLL_DAY, max_bars=8
    )

    assert len(bars) == 8
    assert sources == {_NEW: 5, _OLD: 3}  # 읽은 것은 25봉이지만 쓴 것은 8봉이다
    assert sum(sources.values()) == len(bars)


def test_single_symbol_call_is_unchanged(tmp_path) -> None:
    """기존 계약을 깨지 않는다 — `load_recent_bars()`는 그대로 봉 목록만 돌려준다."""
    archiver = ParquetArchiver(tmp_path)
    _seed(archiver, _NEW, _ROLL_DAY, count=4, base_tick=1100)

    bars = archiver.load_recent_bars(_NEW, Horizon.M30, on_or_before=_ROLL_DAY, max_bars=200)

    assert len(bars) == 4
    assert all(b.symbol == _NEW for b in bars)


def test_normal_day_does_not_reach_into_the_previous_contract(tmp_path) -> None:
    """평시엔 선행 월물을 안 건드린다 — 창이 오늘 심볼만으로 차기 때문이다.

    이게 성립해야 F-1이 "롤일에만 작동하는 변경"이 된다. 매일 두 월물을 섞으면 그건
    다른 설계(연속 계약)이고, 그 판단은 아직 안 내렸다.
    """
    archiver = ParquetArchiver(tmp_path)
    _seed(archiver, _OLD, date(2026, 8, 13), count=50, base_tick=1000)
    _seed(archiver, _NEW, _ROLL_DAY, count=30, base_tick=1100)

    _bars, sources = archiver.load_recent_bars_by_source(
        [_NEW, _OLD], Horizon.M30, on_or_before=_ROLL_DAY, max_bars=25
    )

    assert sources == {_NEW: 25}
    assert _OLD not in sources


def test_missing_predecessor_archive_is_not_an_error(tmp_path) -> None:
    """첫 거래일·아카이브 없음 — 웜스타트는 부가 기능이라 조용히 빈 결과다."""
    archiver = ParquetArchiver(tmp_path)
    bars, sources = archiver.load_recent_bars_by_source(
        [_NEW, _OLD], Horizon.M30, on_or_before=_ROLL_DAY, max_bars=25
    )
    assert bars == []
    assert sources == {_NEW: 0, _OLD: 0}


def test_broken_file_in_the_chain_does_not_block_the_rest(tmp_path) -> None:
    """깨진 파일 하나가 웜스타트 전체를 막지 않는다(기존 규율을 체인에서도 지킨다)."""
    archiver = ParquetArchiver(tmp_path)
    _seed(archiver, _OLD, date(2026, 8, 12), count=10, base_tick=1000)
    _seed(archiver, _OLD, date(2026, 8, 13), count=10, base_tick=1001)
    broken = tmp_path / _OLD / Horizon.M30.value / "2026-08-13.parquet"
    broken.write_bytes(b"not a parquet file")

    bars, sources = archiver.load_recent_bars_by_source(
        [_NEW, _OLD], Horizon.M30, on_or_before=_ROLL_DAY, max_bars=25
    )

    assert len(bars) == 10  # 08-12분은 살아남는다
    assert sources[_OLD] == 10


def test_written_frames_round_trip(tmp_path) -> None:
    """이 파일이 심는 봉이 실제 아카이브 형식과 같은지 — 테스트가 스스로를 속이지 않게."""
    archiver = ParquetArchiver(tmp_path)
    _seed(archiver, _NEW, _ROLL_DAY, count=3, base_tick=1100)
    frame = archiver.read_day(_NEW, Horizon.M30, _ROLL_DAY)
    assert isinstance(frame, pl.DataFrame)
    assert frame.height == 3
