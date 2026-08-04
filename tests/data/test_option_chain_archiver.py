from datetime import date, datetime

import pytest

from messiah.core.messages import OptionQuoteSnapshot
from messiah.core.timeutil import KST
from messiah.data import option_chain_archiver
from messiah.data.option_chain_archiver import OptionChainArchiver

# 2026-08-04 실계좌 실측 응답의 축약형 — output1(다리) / output2(KOSPI 종합) /
# output3(KOSPI200 현물). 세 덩어리가 다 저장되는지가 이 파일의 핵심 관심사다.
_RAW = {
    "output1": {
        "hts_kor_isnm": "위클리M C 2608W2   867.5",
        "futs_prpr": "127.25",
        "acml_vol": "0",
        "hts_otst_stpl_qty": "12",
        "delta_val": "0.8614",
        "gama": "0.0018",
        "hts_ints_vltl": "0.1732",
        "futs_last_tr_date": "20260810",
    },
    "output2": {"bstp_cls_code": "0001", "bstp_nmix_prpr": "6358.95"},
    "output3": {"bstp_cls_code": "2001", "bstp_nmix_prpr": "1000.03"},
    "rt_cd": "0",
}


def _snap(hhmmss="090000", *, series="weekly_mon", strike=867.5, opt="C", day=4, raw=None):
    return OptionQuoteSnapshot(
        underlying="KOSPI200",
        series=series,
        option_type=opt,
        strike=strike,
        expiry="위클리M C 2608W2",
        symbol=f"BAFBRW{int(strike)}{opt}",
        ts_utc=datetime(
            2026, 8, day, int(hhmmss[:2]), int(hhmmss[2:4]), int(hhmmss[4:]), tzinfo=KST
        ),
        raw=_RAW if raw is None else raw,
    )


@pytest.mark.asyncio
async def test_writes_one_file_per_series_and_day(tmp_path):
    arch = OptionChainArchiver(tmp_path, flush_every=1)

    await arch.handle_snapshot(_snap("090000", series="regular", strike=100.0))
    await arch.handle_snapshot(_snap("090000", series="weekly_mon", strike=100.0))

    day = date(2026, 8, 4)
    assert option_chain_archiver.read_day(tmp_path, "regular", day).height == 1
    assert option_chain_archiver.read_day(tmp_path, "weekly_mon", day).height == 1
    assert option_chain_archiver.read_day(tmp_path, "weekly_thu", day) is None


@pytest.mark.asyncio
async def test_keeps_all_three_output_blocks(tmp_path):
    """output3(KOSPI200 현물)은 이 프로젝트가 아직 못 구한 소스다 — 지금 안 남기면
    소급이 불가능하다(옵션 시세는 과거 조회 경로가 없다)."""
    arch = OptionChainArchiver(tmp_path, flush_every=1)

    await arch.handle_snapshot(_snap())

    frame = option_chain_archiver.read_day(tmp_path, "weekly_mon", date(2026, 8, 4))
    assert frame["hts_ints_vltl"].to_list() == [0.1732]  # IV
    assert frame["hts_otst_stpl_qty"].to_list() == [12.0]  # 미결제약정 — 계산 불가, API가 유일 출처
    assert frame["gama"].to_list() == [0.0018]
    assert frame["idx3_bstp_nmix_prpr"].to_list() == [1000.03]  # KOSPI200 현물
    assert frame["idx2_bstp_nmix_prpr"].to_list() == [6358.95]  # KOSPI 종합


@pytest.mark.asyncio
async def test_non_numeric_fields_are_preserved_as_strings(tmp_path):
    arch = OptionChainArchiver(tmp_path, flush_every=1)

    await arch.handle_snapshot(_snap())

    frame = option_chain_archiver.read_day(tmp_path, "weekly_mon", date(2026, 8, 4))
    assert frame["hts_kor_isnm"].to_list() == ["위클리M C 2608W2   867.5"]


@pytest.mark.asyncio
async def test_identity_columns_come_from_the_message_not_the_raw_response(tmp_path):
    arch = OptionChainArchiver(tmp_path, flush_every=1)

    await arch.handle_snapshot(_snap(series="weekly_thu", strike=902.5, opt="P"))

    frame = option_chain_archiver.read_day(tmp_path, "weekly_thu", date(2026, 8, 4))
    assert frame["series"].to_list() == ["weekly_thu"]
    assert frame["strike"].to_list() == [902.5]
    assert frame["option_type"].to_list() == ["P"]


@pytest.mark.asyncio
async def test_same_symbol_and_time_is_overwritten_not_duplicated(tmp_path):
    """재시작 후 같은 사이클을 다시 받으면 행이 두 배가 되면 안 된다."""
    arch = OptionChainArchiver(tmp_path, flush_every=1)

    await arch.handle_snapshot(_snap("090000"))
    await arch.handle_snapshot(_snap("090000"))

    assert option_chain_archiver.read_day(tmp_path, "weekly_mon", date(2026, 8, 4)).height == 1


# --------------------------------------------------- 사이클 단위 flush


@pytest.mark.asyncio
async def test_does_not_write_on_every_snapshot(tmp_path):
    """수급 아카이버와 달리 스냅샷마다 전체 재작성하면 O(n^2)가 된다(하루 3,276행)."""
    arch = OptionChainArchiver(tmp_path, flush_every=4)

    for i in range(3):
        await arch.handle_snapshot(_snap(f"0900{i:02d}", strike=100.0 + i))

    assert option_chain_archiver.read_day(tmp_path, "weekly_mon", date(2026, 8, 4)) is None
    assert arch.row_count == 3


@pytest.mark.asyncio
async def test_flushes_once_the_cycle_worth_of_rows_arrives(tmp_path):
    arch = OptionChainArchiver(tmp_path, flush_every=4)

    for i in range(4):
        await arch.handle_snapshot(_snap(f"0900{i:02d}", strike=100.0 + i))

    assert option_chain_archiver.read_day(tmp_path, "weekly_mon", date(2026, 8, 4)).height == 4


@pytest.mark.asyncio
async def test_close_flushes_the_partial_cycle(tmp_path):
    arch = OptionChainArchiver(tmp_path, flush_every=100)

    await arch.handle_snapshot(_snap("090000"))
    arch.close()

    assert option_chain_archiver.read_day(tmp_path, "weekly_mon", date(2026, 8, 4)).height == 1


@pytest.mark.asyncio
async def test_day_rollover_flushes_previous_day_before_clearing(tmp_path):
    """날짜가 바뀔 때 버퍼를 그냥 비우면 전날 마지막 사이클이 통째로 사라진다."""
    arch = OptionChainArchiver(tmp_path, flush_every=100)

    await arch.handle_snapshot(_snap("150000", day=4))
    await arch.handle_snapshot(_snap("090000", day=5))
    arch.close()

    assert option_chain_archiver.read_day(tmp_path, "weekly_mon", date(2026, 8, 4)).height == 1
    assert option_chain_archiver.read_day(tmp_path, "weekly_mon", date(2026, 8, 5)).height == 1


# --------------------------------------------------- 필터 · 내성


@pytest.mark.asyncio
async def test_ignores_other_underlyings(tmp_path):
    arch = OptionChainArchiver(tmp_path, "KOSPI200", flush_every=1)
    snap = _snap().model_copy(update={"underlying": "KOSDAQ150"})

    await arch.handle_snapshot(snap)

    assert arch.row_count == 0


@pytest.mark.asyncio
async def test_archive_failure_is_logged_not_raised(tmp_path, monkeypatch):
    logged: list[str] = []
    monkeypatch.setattr(
        "messiah.data.option_chain_archiver.mlog.log", lambda tag, msg, **f: logged.append(tag)
    )
    monkeypatch.setattr(
        "messiah.data.option_chain_archiver.pl.DataFrame",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("디스크 가득")),
    )
    arch = OptionChainArchiver(tmp_path, flush_every=1)

    await arch.handle_snapshot(_snap())  # 예외 전파 없이

    assert "OptionChainArchiveError" in logged


def test_read_day_returns_kst_not_utc(tmp_path):
    """polars가 tz-aware를 UTC로 정규화해 저장하므로 되읽을 때 KST로 돌려놔야 한다."""
    import asyncio

    arch = OptionChainArchiver(tmp_path, flush_every=1)
    asyncio.run(arch.handle_snapshot(_snap("090000")))

    frame = option_chain_archiver.read_day(tmp_path, "weekly_mon", date(2026, 8, 4))

    assert frame["ts_kst"][0].hour == 9
    assert option_chain_archiver.available_days(tmp_path, "weekly_mon") == [date(2026, 8, 4)]
