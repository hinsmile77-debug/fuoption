from datetime import date, datetime, timedelta

import polars as pl
import pytest

from messiah.core.messages import InvestorFlowSnapshot
from messiah.core.timeutil import KST
from messiah.data import flow_archiver
from messiah.data.flow_archiver import InvestorFlowArchiver

_MARKET = "K2I"


def _snap(hhmm: str, sector: str = "F001", frgn: int = 100, day: int = 4, **extra):
    hour, minute = int(hhmm[:2]), int(hhmm[2:])
    return InvestorFlowSnapshot(
        market_code=_MARKET,
        sector_code=sector,
        ts_utc=datetime(2026, 8, day, hour, minute, tzinfo=KST),
        raw={"output": [{"frgn_ntby_qty": str(frgn), "prsn_ntby_qty": "-50", **extra}]},
    )


@pytest.mark.asyncio
async def test_writes_one_file_per_day_with_parsed_numeric_columns(tmp_path):
    arch = InvestorFlowArchiver(tmp_path, _MARKET)

    await arch.handle_snapshot(_snap("0900", frgn=100))
    await arch.handle_snapshot(_snap("0901", frgn=250))

    frame = flow_archiver.read_day(tmp_path, _MARKET, date(2026, 8, 4))
    assert frame.height == 2
    assert frame["frgn_ntby_qty"].to_list() == [100.0, 250.0]
    assert frame["sector_code"].to_list() == ["F001", "F001"]


@pytest.mark.asyncio
async def test_keeps_all_response_fields_not_just_the_ones_we_use(tmp_path):
    """장중 수급은 과거 조회가 없다 — 지금 안 받은 필드는 영원히 못 받는다."""
    arch = InvestorFlowArchiver(tmp_path, _MARKET)

    await arch.handle_snapshot(_snap("0900", bank_ntby_qty="7", etc_corp_ntby_vol="3"))

    frame = flow_archiver.read_day(tmp_path, _MARKET, date(2026, 8, 4))
    assert frame["bank_ntby_qty"].to_list() == [7.0]
    assert frame["etc_corp_ntby_vol"].to_list() == [3.0]


@pytest.mark.asyncio
async def test_non_numeric_fields_are_kept_as_text_not_dropped(tmp_path):
    """모르는 형식이라고 조용히 버리지 않는다."""
    arch = InvestorFlowArchiver(tmp_path, _MARKET)

    await arch.handle_snapshot(_snap("0900", hts_kor_isnm="코스피200"))

    frame = flow_archiver.read_day(tmp_path, _MARKET, date(2026, 8, 4))
    assert frame["hts_kor_isnm"].to_list() == ["코스피200"]


@pytest.mark.asyncio
async def test_same_minute_and_sector_is_replaced_not_duplicated(tmp_path):
    """재시작 후 같은 분을 다시 받으면 행이 두 배가 되고 누적 계열이 틀어진다."""
    arch = InvestorFlowArchiver(tmp_path, _MARKET)

    await arch.handle_snapshot(_snap("0900", frgn=100))
    await arch.handle_snapshot(_snap("0900", frgn=999))

    frame = flow_archiver.read_day(tmp_path, _MARKET, date(2026, 8, 4))
    assert frame.height == 1
    assert frame["frgn_ntby_qty"].to_list() == [999.0]


@pytest.mark.asyncio
async def test_different_sectors_in_the_same_minute_are_separate_rows(tmp_path):
    arch = InvestorFlowArchiver(tmp_path, _MARKET)

    await arch.handle_snapshot(_snap("0900", sector="F001"))
    await arch.handle_snapshot(_snap("0900", sector="OC01"))

    frame = flow_archiver.read_day(tmp_path, _MARKET, date(2026, 8, 4))
    assert sorted(frame["sector_code"].to_list()) == ["F001", "OC01"]


@pytest.mark.asyncio
async def test_day_rollover_starts_a_new_file_without_carrying_rows(tmp_path):
    arch = InvestorFlowArchiver(tmp_path, _MARKET)

    await arch.handle_snapshot(_snap("1530", day=4))
    await arch.handle_snapshot(_snap("0900", day=5))

    assert flow_archiver.read_day(tmp_path, _MARKET, date(2026, 8, 4)).height == 1
    day5 = flow_archiver.read_day(tmp_path, _MARKET, date(2026, 8, 5))
    assert day5.height == 1  # 전날 행이 안 섞였다


@pytest.mark.asyncio
async def test_ignores_snapshots_from_other_markets(tmp_path):
    arch = InvestorFlowArchiver(tmp_path, _MARKET)
    other = InvestorFlowSnapshot(
        market_code="OTHER",
        sector_code="F001",
        ts_utc=datetime(2026, 8, 4, 9, 0, tzinfo=KST),
        raw={"output": [{"a": "1"}]},
    )

    await arch.handle_snapshot(other)

    assert arch.row_count == 0
    assert flow_archiver.read_day(tmp_path, _MARKET, date(2026, 8, 4)) is None


@pytest.mark.asyncio
async def test_archive_failure_is_logged_but_does_not_raise(monkeypatch, tmp_path):
    """적재 실패가 수집 루프를 죽이면 안 된다(L22) — 그날 수급을 통째로 잃는다."""
    logged = []
    monkeypatch.setattr(
        "messiah.data.flow_archiver.mlog.log", lambda tag, msg, **kw: logged.append(tag)
    )
    monkeypatch.setattr(
        "messiah.data.flow_archiver.pl.DataFrame",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    arch = InvestorFlowArchiver(tmp_path, _MARKET)

    await arch.handle_snapshot(_snap("0900"))

    assert logged == ["InvestorFlowArchiveError"]


@pytest.mark.asyncio
async def test_empty_output_produces_a_row_with_only_the_key_columns(tmp_path):
    """응답이 비어도 '그 시각에 폴링했다'는 사실은 남는다."""
    arch = InvestorFlowArchiver(tmp_path, _MARKET)
    empty = InvestorFlowSnapshot(
        market_code=_MARKET,
        sector_code="F001",
        ts_utc=datetime(2026, 8, 4, 9, 0, tzinfo=KST),
        raw={},
    )

    await arch.handle_snapshot(empty)

    frame = flow_archiver.read_day(tmp_path, _MARKET, date(2026, 8, 4))
    assert frame.height == 1
    assert set(frame.columns) == {"ts_kst", "sector_code"}


def test_available_days_lists_written_days(tmp_path):
    (tmp_path / _MARKET).mkdir(parents=True)
    for day in ("2026-08-03", "2026-08-04"):
        pl.DataFrame({"a": [1]}).write_parquet(tmp_path / _MARKET / f"{day}.parquet")
    (tmp_path / _MARKET / "not-a-date.parquet").write_bytes(b"x")

    assert flow_archiver.available_days(tmp_path, _MARKET) == [
        date(2026, 8, 3),
        date(2026, 8, 4),
    ]


def test_read_day_missing_returns_none(tmp_path):
    assert flow_archiver.read_day(tmp_path, _MARKET, date(2026, 8, 4)) is None


@pytest.mark.asyncio
async def test_subscribes_to_the_market_topic(tmp_path):
    seen = []

    class _Bus:
        async def subscribe(self, topics, handler):
            seen.extend(topics)

        async def publish(self, topic, msg):
            pass

    await InvestorFlowArchiver(tmp_path, _MARKET).run_forever(_Bus())

    assert seen == ["raw.investor_flow.K2I"]


@pytest.mark.asyncio
async def test_rows_accumulate_across_the_session(tmp_path):
    arch = InvestorFlowArchiver(tmp_path, _MARKET)
    start = datetime(2026, 8, 4, 9, 0, tzinfo=KST)

    for i in range(5):
        moment = start + timedelta(minutes=i)
        await arch.handle_snapshot(_snap(f"{moment.hour:02d}{moment.minute:02d}", frgn=i))

    assert flow_archiver.read_day(tmp_path, _MARKET, date(2026, 8, 4)).height == 5


# ---------------------------------------------- 폴러 → 버스 → 아카이버 (결선 회귀)


@pytest.mark.asyncio
async def test_poller_to_archiver_round_trip_persists_the_snapshot(tmp_path):
    """폴러가 발행한 것이 실제로 **파일에 남는가** — 2026-08-04 이전에는 폴러가 어디에도
    결선돼 있지 않았고 `raw.investor_flow.*` 구독자도 없어 버스에서 증발했다."""
    from messiah.broker.kis import tr_codes
    from messiah.data.investor_flow_poller import InvestorFlowPoller
    from messiah.simulator.inprocess_bus import InProcessBus

    class _Rest:
        def get_investor_flow(self, market_code, sector_code):
            return {"output": [{"frgn_ntby_qty": "-164", "orgn_ntby_qty": "897"}]}

    bus = InProcessBus()
    archiver = InvestorFlowArchiver(tmp_path, tr_codes.FID_MRKT_DIV_DERIVATIVES)
    await archiver.run_forever(bus)

    poller = InvestorFlowPoller(
        rest_client=_Rest(),
        market_code=tr_codes.FID_MRKT_DIV_DERIVATIVES,
        sector_codes=[
            tr_codes.FID_INVESTOR_FLOW_FUTURES,
            tr_codes.FID_INVESTOR_FLOW_CALL_OPTION,
        ],
        bus=bus,
    )
    await poller.poll_once()

    days = flow_archiver.available_days(tmp_path, tr_codes.FID_MRKT_DIV_DERIVATIVES)
    assert len(days) == 1
    frame = flow_archiver.read_day(tmp_path, tr_codes.FID_MRKT_DIV_DERIVATIVES, days[0])
    assert sorted(frame["sector_code"].to_list()) == ["F001", "OC01"]
    assert frame["frgn_ntby_qty"].to_list() == [-164.0, -164.0]


@pytest.mark.asyncio
async def test_read_day_returns_kst_not_utc(tmp_path):
    """컬럼 이름이 `ts_kst`인데 UTC로 읽히면 보는 사람이 9시간 틀리게 읽는다."""
    arch = InvestorFlowArchiver(tmp_path, _MARKET)
    await arch.handle_snapshot(_snap("0900"))

    frame = flow_archiver.read_day(tmp_path, _MARKET, date(2026, 8, 4))

    assert frame["ts_kst"][0].hour == 9
    assert frame["ts_kst"][0].utcoffset().total_seconds() == 9 * 3600


# ------------------------------------ 재기동 복원 (2026-08-06 호스트 재부팅 대응, P0-1)


@pytest.mark.asyncio
async def test_restart_keeps_rows_written_before_the_restart(tmp_path):
    """**이 파일에서 가장 중요한 테스트다.**

    2026-08-06: 10:03:49 호스트 재부팅 → 10:25 재기동. 그날 파일의 첫 행이 10:26이었고
    08:36~10:03의 88분 × 3업종이 사라졌다. 원인은 `_flush()`가 메모리에 있는 것만 쓰면서
    `os.replace()`로 파일을 통째로 교체한 것 — 재기동하면 메모리가 비어 있다.
    장중 수급은 과거 조회가 없어 **소급이 불가능**하다.
    """
    morning = InvestorFlowArchiver(tmp_path, _MARKET)
    await morning.handle_snapshot(_snap("0900", frgn=1))
    await morning.handle_snapshot(_snap("0901", frgn=2))
    del morning  # 재부팅 — close() 없이 사라진다

    afternoon = InvestorFlowArchiver(tmp_path, _MARKET)  # 새 프로세스, 빈 메모리
    await afternoon.handle_snapshot(_snap("1030", frgn=3))

    frame = flow_archiver.read_day(tmp_path, _MARKET, date(2026, 8, 4))
    assert frame.height == 3, "재기동 전 2행이 지워졌다 — 소급 불가능한 소실"
    assert frame["frgn_ntby_qty"].to_list() == [1.0, 2.0, 3.0]


@pytest.mark.asyncio
async def test_restored_rows_keep_kst_and_all_columns(tmp_path):
    """복원이 시각을 9시간 틀리게 되돌리거나 필드를 떨구면 복원이 아니라 손상이다."""
    morning = InvestorFlowArchiver(tmp_path, _MARKET)
    await morning.handle_snapshot(_snap("0900", bank_ntby_qty="7", hts_kor_isnm="코스피200"))

    afternoon = InvestorFlowArchiver(tmp_path, _MARKET)
    await afternoon.handle_snapshot(_snap("1030", bank_ntby_qty="9", hts_kor_isnm="코스피200"))

    frame = flow_archiver.read_day(tmp_path, _MARKET, date(2026, 8, 4))
    assert frame["ts_kst"].to_list()[0].hour == 9
    assert frame["ts_kst"].to_list()[0].utcoffset().total_seconds() == 9 * 3600
    assert frame["bank_ntby_qty"].to_list() == [7.0, 9.0]
    assert frame["hts_kor_isnm"].to_list() == ["코스피200", "코스피200"]


@pytest.mark.asyncio
async def test_same_minute_after_restart_is_replaced_not_duplicated(tmp_path):
    """복원이 중복을 만들면 누적 계열이 두 배로 틀어진다 — 키가 병합을 정의해야 한다."""
    morning = InvestorFlowArchiver(tmp_path, _MARKET)
    await morning.handle_snapshot(_snap("0900", frgn=100))

    afternoon = InvestorFlowArchiver(tmp_path, _MARKET)
    await afternoon.handle_snapshot(_snap("0900", frgn=999))

    frame = flow_archiver.read_day(tmp_path, _MARKET, date(2026, 8, 4))
    assert frame.height == 1
    assert frame["frgn_ntby_qty"].to_list() == [999.0]


@pytest.mark.asyncio
async def test_shrinking_write_is_refused_when_restore_fails(monkeypatch, tmp_path):
    """겹②: 복원이 깨져도 **줄어드는 쓰기**는 안 일어난다(옛 행은 복구 불가, 새 행은 가능)."""
    morning = InvestorFlowArchiver(tmp_path, _MARKET)
    for i in range(3):
        await morning.handle_snapshot(_snap(f"09{i:02d}", frgn=i))

    logged: list[str] = []
    monkeypatch.setattr(
        "messiah.data.flow_archiver.mlog.log", lambda tag, msg, **kw: logged.append(tag)
    )
    monkeypatch.setattr(
        "messiah.data.flow_archiver.read_day",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("복원 불가")),
    )
    afternoon = InvestorFlowArchiver(tmp_path, _MARKET)
    await afternoon.handle_snapshot(_snap("1030", frgn=9))

    # `read_day`가 monkeypatch돼 있으므로 파일을 직접 읽어 확인한다.
    on_disk = pl.read_parquet(tmp_path / _MARKET / "2026-08-04.parquet")
    assert on_disk.height == 3, "복원이 깨진 상태에서 파일이 줄어들었다"
    assert "InvestorFlowArchiveShrinkRefused" in logged


@pytest.mark.asyncio
async def test_day_rollover_does_not_absorb_the_previous_day(tmp_path):
    """복원은 **그 날짜 파일**만 본다 — 날짜가 바뀌면 전날 행이 따라오면 안 된다."""
    arch = InvestorFlowArchiver(tmp_path, _MARKET)
    await arch.handle_snapshot(_snap("1530", day=4))
    await arch.handle_snapshot(_snap("0900", day=5))

    assert flow_archiver.read_day(tmp_path, _MARKET, date(2026, 8, 5)).height == 1
