from datetime import date
from decimal import Decimal

import pytest
from messiah.core.event_calendar import EventCalendar
from messiah.core.messages import BarSession, Horizon
from messiah.data import backfill

_TICK = Decimal("0.02")


def _calendar(holidays=()) -> EventCalendar:
    """휴장일 0건인 연도도 "데이터 있음"으로 취급하려면 years를 명시해야 한다
    (`EventCalendar.__init__` docstring)."""
    return EventCalendar(frozenset(holidays), years=frozenset({2025, 2026, 2027}))


# ---------------------------------------------------------------- 단축코드 · 만기


def test_contract_code_matches_measured_symbols():
    # 2026-08-04 실측: 마스터파일의 상장 월물 + 만기물 직접 조회로 확인된 코드들.
    assert backfill.contract_code(2026, 8) == "A05608"
    assert backfill.contract_code(2026, 12) == "A05612"
    assert backfill.contract_code(2027, 1) == "A05701"
    assert backfill.contract_code(2025, 12) == "A05512"


def test_contract_code_rejects_bad_month():
    with pytest.raises(ValueError):
        backfill.contract_code(2026, 13)


@pytest.mark.parametrize(
    ("year", "month", "expected"),
    [
        # 2026-08-04 실측: 각 월물의 실제 마지막 데이터 날짜와 일치했던 값들.
        (2026, 1, date(2026, 1, 8)),
        (2026, 2, date(2026, 2, 12)),
        (2026, 3, date(2026, 3, 12)),
        (2026, 6, date(2026, 6, 11)),
        (2026, 7, date(2026, 7, 9)),
        (2026, 8, date(2026, 8, 13)),
    ],
)
def test_monthly_expiry_is_second_thursday(year, month, expected):
    assert backfill.monthly_expiry(year, month) == expected


def test_monthly_expiry_backs_off_to_previous_trading_day_when_holiday():
    holiday = date(2026, 8, 13)  # 실제 8월물 만기일을 휴장으로 가정
    assert backfill.monthly_expiry(2026, 8, _calendar([holiday])) == date(2026, 8, 12)


# ---------------------------------------------------------------- 근월물 판정 · 구간


def test_front_month_code_includes_expiry_day_itself():
    cal = _calendar()
    assert backfill.front_month_code_for_day(date(2026, 7, 9), cal) == "A05607"  # 만기 당일
    assert backfill.front_month_code_for_day(date(2026, 7, 10), cal) == "A05608"  # 다음날 롤


def test_front_month_code_rolls_across_year_boundary():
    cal = _calendar()
    assert backfill.front_month_code_for_day(date(2026, 12, 10), cal) == "A05612"  # 만기 당일
    assert backfill.front_month_code_for_day(date(2026, 12, 11), cal) == "A05701"


def test_front_month_days_splits_at_expiry_and_skips_holidays():
    cal = _calendar([date(2026, 7, 6)])  # 월요일 하나를 휴장으로
    segments = backfill.front_month_days(date(2026, 7, 3), date(2026, 7, 14), cal)

    assert [s.symbol for s in segments] == ["A05607", "A05608"]
    assert date(2026, 7, 6) not in segments[0].days  # 휴장 제외
    assert date(2026, 7, 4) not in segments[0].days  # 토요일 제외
    assert segments[0].end == date(2026, 7, 9)  # 만기일까지
    assert segments[1].start == date(2026, 7, 10)


def test_front_month_days_without_calendar_keeps_all_weekdays():
    """달력 없이도 성립해야 한다 — 2025년 휴장일 데이터가 아예 없고, 틀린 목록으로 날짜를
    빼면 그 거래일은 조용히 안 채워진다(빈 응답으로 드러나는 쪽이 안전)."""
    segments = backfill.front_month_days(date(2026, 7, 3), date(2026, 7, 14))

    all_days = [d for s in segments for d in s.days]
    assert date(2026, 7, 6) in all_days  # 달력이 없으니 평일은 전부 포함
    assert date(2026, 7, 4) not in all_days  # 주말은 여전히 제외
    assert [s.symbol for s in segments] == ["A05607", "A05608"]


def test_front_month_days_rejects_reversed_range():
    with pytest.raises(ValueError):
        backfill.front_month_days(date(2026, 8, 4), date(2026, 8, 1), _calendar())


def test_continuous_days_is_time_ordered_across_contracts():
    segments = backfill.front_month_days(date(2026, 7, 8), date(2026, 7, 13), _calendar())
    pairs = backfill.continuous_days(segments)

    assert [d for _, d in pairs] == sorted(d for _, d in pairs)
    assert pairs[0][0] == "A05607"
    assert pairs[-1][0] == "A05608"


# ---------------------------------------------------------------- 행 → BarClosed


def _row(
    hour="090000",
    day="20260803",
    o="986.20",
    h="986.56",
    low="983.84",
    c="985.44",
    vol="582",
):
    return {
        "stck_bsop_date": day,
        "stck_cntg_hour": hour,
        "futs_oprc": o,
        "futs_hgpr": h,
        "futs_lwpr": low,
        "futs_prpr": c,
        "cntg_vol": vol,
    }


def test_chart_row_to_bar_maps_ohlcv_and_uses_bar_open_semantics():
    bar = backfill.chart_row_to_bar(_row(), "A05608", _TICK)

    assert bar.symbol == "A05608"
    assert bar.horizon == Horizon.M1
    assert bar.bar_open_kst.hour == 9 and bar.bar_open_kst.minute == 0
    assert bar.bar_open_kst.utcoffset().total_seconds() == 9 * 3600
    assert (bar.o_ticks, bar.h_ticks, bar.l_ticks, bar.c_ticks) == (49310, 49328, 49192, 49272)
    assert bar.volume == 582


def test_chart_row_to_bar_marks_pre_open_session():
    """08:45~09:00은 정상 거래 봉이며 정규장 전 웜업·스케일링에 쓴다 — 버리지 않고 표시만."""
    assert backfill.chart_row_to_bar(_row(hour="084500"), "A05608", _TICK).session is (
        BarSession.PRE_OPEN
    )
    assert backfill.chart_row_to_bar(_row(hour="090000"), "A05608", _TICK).session is (
        BarSession.REGULAR
    )


def test_chart_row_to_bar_quality_ok_is_always_true():
    # 거래소 공식 집계에는 "틱 수 부족" 판정 근거가 없다(모듈 docstring).
    assert backfill.chart_row_to_bar(_row(vol="1"), "A05608", _TICK).quality_ok is True


def test_chart_row_to_bar_returns_none_on_bad_row():
    assert backfill.chart_row_to_bar({"stck_bsop_date": "20260803"}, "A05608", _TICK) is None
    assert backfill.chart_row_to_bar(_row(c="NOPE"), "A05608", _TICK) is None


# ---------------------------------------------------------------- 하루치 페이징


class _FakeChart:
    """커서 규약(요청 시각부터 과거로 최대 N건)을 그대로 흉내내는 가짜 응답원."""

    def __init__(self, bars_by_day: dict[str, list[str]], page_size: int = 102):
        self.bars_by_day = bars_by_day  # {"20260803": ["084500", ...]} 오름차순
        self.page_size = page_size
        self.calls: list[tuple[str, str]] = []

    def __call__(self, symbol, *, date_yyyymmdd, hour_hhmmss, **kwargs):
        self.calls.append((date_yyyymmdd, hour_hhmmss))
        # 모든 날짜를 하나의 시간축으로 이어 붙인 뒤 커서보다 이른 것만 최신순으로 자른다.
        flat = [
            (day, hour)
            for day in sorted(self.bars_by_day)
            for hour in sorted(self.bars_by_day[day])
        ]
        upto = [x for x in flat if (x[0], x[1]) <= (date_yyyymmdd, hour_hhmmss)]
        page = list(reversed(upto))[: self.page_size]
        return {"output2": [_row(hour=h, day=d) for d, h in page]}


def _hours(start_min: int, count: int) -> list[str]:
    return [f"{(start_min + i) // 60:02d}{(start_min + i) % 60:02d}00" for i in range(count)]


def test_fetch_day_bars_pages_until_the_whole_day_is_collected():
    # 실제 API는 마지막 페이지에서 전 거래일까지 넘어간다 — 그 경계가 종료 신호이므로
    # 가짜 응답원도 전 거래일을 갖고 있어야 실제와 같은 호출 수가 나온다.
    chart = _FakeChart({"20260731": _hours(8 * 60 + 45, 380), "20260803": _hours(8 * 60 + 45, 410)})

    bars = backfill.fetch_day_bars(chart, "A05608", date(2026, 8, 3), _TICK)

    assert len(bars) == 410
    assert bars[0].bar_open_kst.strftime("%H%M") == "0845"
    assert bars[-1].bar_open_kst.strftime("%H%M") == "1534"
    assert len(chart.calls) == 5  # 410 / 102 = 5회 (2026-08-04 실측과 동일)


def test_fetch_day_bars_ignores_rows_from_other_days():
    chart = _FakeChart({"20260731": _hours(8 * 60 + 45, 380), "20260803": _hours(9 * 60, 30)})

    bars = backfill.fetch_day_bars(chart, "A05608", date(2026, 8, 3), _TICK)

    assert len(bars) == 30
    assert {b.bar_open_kst.date() for b in bars} == {date(2026, 8, 3)}


def test_fetch_day_bars_stops_as_soon_as_the_response_crosses_into_the_previous_day():
    """빈 응답을 한 번 더 받아 확인하지 않는다 — 160거래일이면 그 낭비가 160초다."""
    chart = _FakeChart({"20260731": _hours(15 * 60, 5), "20260803": _hours(9 * 60, 30)})

    backfill.fetch_day_bars(chart, "A05608", date(2026, 8, 3), _TICK)

    assert len(chart.calls) == 1  # 첫 응답이 이미 07-31까지 넘어갔다


def test_fetch_day_bars_returns_empty_for_a_day_with_no_data():
    chart = _FakeChart({"20260731": _hours(9 * 60, 10)})

    assert backfill.fetch_day_bars(chart, "A05608", date(2026, 8, 3), _TICK) == []


def test_fetch_day_bars_logs_when_paging_limit_is_hit(monkeypatch):
    logged = []
    monkeypatch.setattr(
        "messiah.data.backfill.mlog.log", lambda tag, msg, **kw: logged.append((tag, kw))
    )
    chart = _FakeChart({"20260803": _hours(8 * 60 + 45, 410)}, page_size=10)

    bars = backfill.fetch_day_bars(chart, "A05608", date(2026, 8, 3), _TICK, max_calls=3)

    assert len(bars) == 30  # 3회 × 10건
    assert logged and logged[0][0] == "BackfillPagingLimit"


# ---------------------------------------------------------------- 롤 조정


def _bar(symbol: str, day: date, minute: int, close: int) -> "object":
    from datetime import datetime

    from messiah.core.messages import BarClosed
    from messiah.core.timeutil import KST

    return BarClosed(
        symbol=symbol,
        horizon=Horizon.M1,
        bar_open_kst=datetime(day.year, day.month, day.day, 9, minute, tzinfo=KST),
        o_ticks=close,
        h_ticks=close + 2,
        l_ticks=close - 2,
        c_ticks=close,
        volume=10,
    )


def test_roll_overlap_targets_asks_for_the_incoming_contract_on_the_outgoing_last_day():
    segments = backfill.front_month_days(date(2026, 6, 12), date(2026, 8, 3))

    targets = backfill.roll_overlap_targets(segments)

    # A05607 → A05608 롤: 들어오는 A05608을 나가는 A05607의 마지막 날(만기 07-09)에 받는다.
    assert ("A05608", date(2026, 7, 9)) in targets
    assert len(targets) == len(segments) - 1


def test_roll_offset_is_incoming_minus_outgoing():
    assert backfill.roll_offset_ticks(outgoing_close=50000, incoming_close=50120) == 120


def test_back_adjust_removes_the_roll_gap_but_keeps_within_contract_moves():
    old = [_bar("A05607", date(2026, 7, 9), 0, 50000), _bar("A05607", date(2026, 7, 9), 1, 50030)]
    new = [_bar("A05608", date(2026, 7, 10), 0, 50150)]

    out = backfill.back_adjust(
        [("A05607", old), ("A05608", new)],
        offsets_by_symbol={"A05607": 120},  # 같은 날 A05608이 A05607보다 120틱 높았다
        symbol_out="A056FM",
    )

    assert [b.symbol for b in out] == ["A056FM"] * 3
    # 최근 월물은 손대지 않는다 — 백테스트 체결가가 실제 호가와 같아야 한다.
    assert out[-1].c_ticks == 50150
    # 과거 월물은 +120 — 롤 경계의 가짜 급등(50030 → 50150 = +120)이 사라진다.
    assert [b.c_ticks for b in out[:2]] == [50120, 50150]
    # 계약 **안에서의** 움직임(+30)은 그대로 보존된다.
    assert out[1].c_ticks - out[0].c_ticks == 30
    # 거래량은 조정 대상이 아니다.
    assert {b.volume for b in out} == {10}


def test_back_adjust_accumulates_offsets_across_multiple_rolls():
    a = [_bar("A05606", date(2026, 6, 11), 0, 40000)]
    b = [_bar("A05607", date(2026, 7, 9), 0, 45000)]
    c = [_bar("A05608", date(2026, 8, 3), 0, 50000)]

    out = backfill.back_adjust(
        [("A05606", a), ("A05607", b), ("A05608", c)],
        offsets_by_symbol={"A05606": 100, "A05607": 200},
        symbol_out="X",
    )

    assert [x.c_ticks for x in out] == [40300, 45200, 50000]  # 누적 300 / 200 / 0


def test_back_adjust_is_time_ordered():
    out = backfill.back_adjust(
        [
            (
                "A05607",
                [_bar("A05607", date(2026, 7, 9), 5, 1), _bar("A05607", date(2026, 7, 9), 1, 2)],
            ),
            ("A05608", [_bar("A05608", date(2026, 7, 10), 0, 3)]),
        ],
        offsets_by_symbol={},
        symbol_out="X",
    )

    assert [b.bar_open_kst for b in out] == sorted(b.bar_open_kst for b in out)


# ---------------------------------------------------------------- 만기 규칙 대조


def test_verify_expiry_against_chart_confirms_matching_last_trading_day():
    def daily(symbol, *, date_from, date_to, **kwargs):
        return {"output2": [{"stck_bsop_date": "20260709"}, {"stck_bsop_date": "20260708"}]}

    ok, actual = backfill.verify_expiry_against_chart(daily, "A05607", date(2026, 7, 9))

    assert ok is True and actual == "20260709"


def test_verify_expiry_against_chart_reports_mismatch_and_unknown_separately():
    def mismatched(symbol, **kwargs):
        return {"output2": [{"stck_bsop_date": "20260708"}]}

    def empty(symbol, **kwargs):
        return {"output2": []}

    assert backfill.verify_expiry_against_chart(mismatched, "A", date(2026, 7, 9)) == (
        False,
        "20260708",
    )
    assert backfill.verify_expiry_against_chart(empty, "A", date(2026, 7, 9)) == (False, None)
