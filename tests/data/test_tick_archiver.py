"""체결틱 원본 적재 테스트 (2026-08-04, F2).

이 아카이버의 존재 이유는 **틱에 백필 경로가 없다**는 것이다 — 봉은 KIS 분봉 API로 소급이
되지만 체결 단위 과거 조회는 아예 없다. 그래서 "안 쓰였다"가 곧 "영원히 없다"이고,
테스트도 그 관점으로 짠다: 무엇이 유실되는가.
"""

from datetime import date, datetime, timedelta

from messiah.core.messages import Tick
from messiah.core.timeutil import KST
from messiah.data import tick_archiver as ta
from messiah.data.tick_archiver import TickArchiver

_DAY = date(2026, 8, 5)


def _tick(second: int, *, symbol: str = "A05608", price: int = 54015, hour: int = 9) -> Tick:
    return Tick(
        symbol=symbol,
        ts_exchange=datetime(2026, 8, 5, hour, 0, tzinfo=KST) + timedelta(seconds=second),
        price_ticks=price,
        qty=1,
        side_hint=1,
        bid1_ticks=54002,
        ask1_ticks=54015,
        bid_qty1=3,
        ask_qty1=7,
        raw_fields=("A05608", "090000", "-0.54", "5", "1080.30"),
    )


async def test_ticks_are_buffered_until_the_flush_threshold(tmp_path):
    """매 틱 조각을 다시 쓰면 하루 5~10만행에서 O(n²)가 된다 — 봉 아카이버가 조각화를
    도입해야 했던 그 문제를 틱 규모로 되살리는 셈."""
    archiver = TickArchiver(tmp_path, "A05608", flush_every=3)

    await archiver.handle_tick(_tick(0))
    await archiver.handle_tick(_tick(1))

    assert archiver.buffered == 2
    assert archiver.written == 0
    assert ta.read_day(tmp_path, "A05608", _DAY) is None

    await archiver.handle_tick(_tick(2))

    assert archiver.buffered == 0
    assert archiver.written == 3


async def test_flushed_rows_round_trip_with_every_field(tmp_path):
    archiver = TickArchiver(tmp_path, "A05608", flush_every=1)

    await archiver.handle_tick(_tick(0))

    frame = ta.read_day(tmp_path, "A05608", _DAY)
    assert frame is not None
    row = frame.row(0, named=True)
    assert row["price_ticks"] == 54015
    assert row["qty"] == 1
    assert row["side_hint"] == 1
    assert row["bid1_ticks"] == 54002
    assert row["ask1_ticks"] == 54015
    assert row["bid_qty1"] == 3
    assert row["ask_qty1"] == 7


async def test_raw_fields_are_stored_by_position_as_strings(tmp_path):
    """컬럼 이름을 의미가 아니라 위치로 붙인다 — 추정한 의미를 이름에 박으면 그 추정이
    틀렸을 때 컬럼 이름이 거짓말을 한다. 문자열 그대로 두는 것도 같은 이유다("000000"을
    0으로 바꾸면 원본과 달라진다)."""
    archiver = TickArchiver(tmp_path, "A05608", flush_every=1)

    await archiver.handle_tick(_tick(0))

    frame = ta.read_day(tmp_path, "A05608", _DAY)
    assert frame is not None
    row = frame.row(0, named=True)
    assert row["f00"] == "A05608"
    assert row["f04"] == "1080.30"  # 숫자로 변환하지 않았다
    assert "f05" not in frame.columns  # 프레임에 있던 만큼만


async def test_ts_kst_reads_back_as_kst_not_utc(tmp_path):
    """polars는 tz-aware datetime을 쓸 때 UTC로 정규화한다 — 그대로 읽으면 컬럼 이름은
    `ts_kst`인데 dtype이 UTC라 보는 사람이 9시간 틀리게 읽는다(`bar_open_kst`·
    `flow_archiver.ts_kst`와 같은 규율)."""
    archiver = TickArchiver(tmp_path, "A05608", flush_every=1)

    await archiver.handle_tick(_tick(0))

    frame = ta.read_day(tmp_path, "A05608", _DAY)
    assert frame is not None
    assert frame.row(0, named=True)["ts_kst"].hour == 9


async def test_a_buffer_spanning_two_hours_is_split_across_shards(tmp_path):
    """버퍼 전체를 첫 틱의 조각에 몰아넣으면 09:59~10:01 구간의 틱이 09시 조각에 섞인다."""
    archiver = TickArchiver(tmp_path, "A05608", flush_every=100)

    await archiver.handle_tick(_tick(0, hour=9))
    await archiver.handle_tick(_tick(0, hour=10))
    archiver.flush()

    day_dir = tmp_path / "A05608" / "2026-08-05"
    assert sorted(p.name for p in day_dir.glob("*.parquet")) == ["09.parquet", "10.parquet"]
    frame = ta.read_day(tmp_path, "A05608", _DAY)
    assert frame is not None and frame.height == 2


async def test_identical_trades_are_not_deduplicated(tmp_path):
    """같은 초·같은 가격·같은 수량의 체결이 두 번 나는 것은 실제로 일어난다 — 지우면
    거래량이 조용히 준다. 2026-08-04에 프레임당 레코드를 하나만 읽어 거래량 절반을 날린
    사고와 같은 방향의 실수(그때는 파서, 여기는 적재)."""
    archiver = TickArchiver(tmp_path, "A05608", flush_every=2)

    await archiver.handle_tick(_tick(0))
    await archiver.handle_tick(_tick(0))

    frame = ta.read_day(tmp_path, "A05608", _DAY)
    assert frame is not None and frame.height == 2


async def test_appending_to_an_existing_shard_keeps_earlier_rows(tmp_path):
    archiver = TickArchiver(tmp_path, "A05608", flush_every=1)

    await archiver.handle_tick(_tick(0))
    await archiver.handle_tick(_tick(5))

    frame = ta.read_day(tmp_path, "A05608", _DAY)
    assert frame is not None and frame.height == 2
    assert [r["ts_kst"].second for r in frame.iter_rows(named=True)] == [0, 5]


async def test_close_flushes_the_remaining_buffer(tmp_path):
    """안 부르면 마지막 몇 분이 사라진다 — 그리고 소급할 방법이 없다(L23)."""
    archiver = TickArchiver(tmp_path, "A05608", flush_every=1000)

    await archiver.handle_tick(_tick(0))
    assert archiver.written == 0

    assert archiver.close() == 1
    assert archiver.written == 1
    assert ta.read_day(tmp_path, "A05608", _DAY) is not None


async def test_ticks_for_other_symbols_are_ignored(tmp_path):
    archiver = TickArchiver(tmp_path, "A05608", flush_every=1)

    await archiver.handle_tick(_tick(0, symbol="A01609"))

    assert archiver.written == 0
    assert ta.available_days(tmp_path, "A05608") == []


async def test_a_write_failure_is_logged_and_does_not_raise(tmp_path, monkeypatch):
    """적재 실패가 수집 루프를 죽이면 안 된다(L22) — 다만 조용히는 안 된다. 이 버퍼는
    백필 경로가 없어 정말로 유실되기 때문이다."""
    logged: list[str] = []
    monkeypatch.setattr(
        "messiah.data.tick_archiver.mlog.log",
        lambda tag, msg, **kw: logged.append(tag),
    )
    archiver = TickArchiver(tmp_path, "A05608", flush_every=1)
    monkeypatch.setattr(
        TickArchiver, "_write_shard", lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
    )

    await archiver.handle_tick(_tick(0))

    assert logged == ["TickArchiveError"]
    assert archiver.buffered == 0  # 버퍼는 비운다 — 안 그러면 실패가 무한히 누적된다
    assert archiver.written == 0


def test_available_days_and_read_day_on_an_empty_archive(tmp_path):
    assert ta.available_days(tmp_path, "A05608") == []
    assert ta.read_day(tmp_path, "A05608", _DAY) is None


async def test_available_days_lists_collected_days(tmp_path):
    archiver = TickArchiver(tmp_path, "A05608", flush_every=1)

    await archiver.handle_tick(_tick(0))

    assert ta.available_days(tmp_path, "A05608") == [_DAY]
