from datetime import date
from decimal import Decimal

from messiah.core.messages import BarClosed, BarSession, Horizon, Tick
from messiah.data.normalizer import (
    MinuteBarAggregator,
    parse_futures_ticks,
    parse_option_ticks,
)

# 2026-07-22 ws_client.py 실측 세션에서 실제로 캡처한 라이브 WS 프레임(미니선물 A05608,
# H0IFCNT0) — 최초 2건은 15:29:53/54(같은 분), 3번째는 인위적으로 15:30:10(다음 분)으로 바꿔
# 분 롤오버를 검증한다(원본 4건은 전부 같은 분 안에 들어와 실제 롤오버 경계 샘플이 없었음).
_REAL_TICK_1 = (
    "0|H0IFCNT0|001|A05608^152953^-0.54^5^-0.05^1080.30^1131.06^1146.44^1079.36^1^134935^"
    "7587962447^1082.72^-0.91^-0.22^0.00^0.00^5.22^76672^571^000000^5^-50.76^000000^5^-66.14^"
    "000000^2^0.94^0.49^94.95^-2.42^0^1.51^1080.30^1080.04^1^1^60234^56822^-3412^69150^65657^"
    "997^478^106.25^0^1091.10^1069.50^2"
)
_REAL_TICK_2 = (
    "0|H0IFCNT0|001|A05608^152954^-0.54^5^-0.05^1080.30^1131.06^1146.44^1079.36^1^134936^"
    "7588016462^1082.72^-0.91^-0.22^0.00^0.00^5.10^76672^571^000000^5^-50.76^000000^5^-66.14^"
    "000000^2^0.94^0.49^94.95^-2.42^0^1.51^1080.30^1080.26^1^1^60234^56823^-3411^69150^65658^"
    "943^484^106.25^0^1091.10^1069.50^2"
)
# 3번째 실캡처(원래 152955, count=002 — 여러 체결이 한 프레임에 묶인 경우)를 다음 분(153010)
# 으로 바꿔 롤오버 경계 테스트용으로 재사용.
_REAL_TICK_3_NEXT_MINUTE = (
    "0|H0IFCNT0|002|A05608^153010^-0.20^5^-0.02^1080.64^1131.06^1146.44^1079.36^1^134937^"
    "7588070494^1082.72^-0.57^-0.19^0.00^0.00^5.10^76672^571^000000^5^-50.42^000000^5^-65.80^"
    "000000^2^1.28^0.49^94.95^-2.08^0^1.51^1080.64^1080.50^1^1^60234^56824^-3410^69150^65659^"
    "927^486^106.25^0^1091.44^1069.84^2^A0560..."
)

_TICK_SIZE = Decimal("0.02")  # 2026-07-22 미니선물 A05608 실측(호가 5단계 간격)
_TODAY = date(2026, 7, 22)


def _first_futures_tick(raw, tick_size, **kw):
    """복수 계약(`parse_futures_ticks`)에서 첫 건만 꺼내는 테스트 헬퍼 — 아래 기존 단건
    검증들은 프레임에 체결이 1건인 샘플을 쓰므로 의미가 그대로다. 다중 레코드 동작 자체는
    `test_parse_futures_ticks_*`가 따로 검증한다."""
    ticks = parse_futures_ticks(raw, tick_size, **kw)
    return ticks[0] if ticks else None


def _first_option_tick(raw, tick_size, **kw):
    ticks = parse_option_ticks(raw, tick_size, **kw)
    return ticks[0] if ticks else None


def test_parse_futures_tick_matches_real_captured_sample():
    tick = _first_futures_tick(_REAL_TICK_1, _TICK_SIZE, today=_TODAY)

    assert tick is not None
    assert tick.symbol == "A05608"
    assert tick.ts_exchange.hour == 15
    assert tick.ts_exchange.minute == 29
    assert tick.ts_exchange.second == 53
    assert tick.ts_exchange.utcoffset().total_seconds() == 9 * 3600  # KST
    assert tick.price_ticks == 54015  # 1080.30 / 0.02
    assert tick.qty == 1
    assert tick.source == "kis"


# ---------------------------------------------- L1 호가·side_hint (2026-08-04, F2)


def test_parse_futures_tick_reads_the_l1_quote_that_was_being_discarded():
    """2026-08-04까지 이 파서는 50필드 중 4개만 읽고 46개를 버렸다 — 그중 idx34~37이
    매도호가1/매수호가1/잔량이고(2026-07-22 캡처로 교차검증된 위치), 그걸 안 읽는다는 이유로
    MS 카테고리 30개가 통째로 "데이터 없음"이었다. **데이터가 없던 게 아니라 스키마가
    좁았다.** 값은 실캡처 프레임에서 손으로 읽은 것이다."""
    tick = _first_futures_tick(_REAL_TICK_1, _TICK_SIZE, today=_TODAY)

    assert tick is not None
    assert tick.ask1_ticks == 54015  # 1080.30 / 0.02
    assert tick.bid1_ticks == 54002  # 1080.04 / 0.02
    assert tick.ask_qty1 == 1
    assert tick.bid_qty1 == 1
    assert tick.mid_ticks == 54008.5
    assert tick.ask1_ticks - tick.bid1_ticks == 13  # 스프레드 0.26pt = 13틱


def test_side_hint_is_buy_when_the_trade_prints_at_the_ask():
    """실캡처 프레임은 체결가(1080.30)가 정확히 최우선매도호가라 매수주도다 — 종전에는
    이 판정 근거(bid/ask)가 Tick에 없어 `side_hint`가 항상 0이었다."""
    tick = _first_futures_tick(_REAL_TICK_1, _TICK_SIZE, today=_TODAY)

    assert tick is not None
    assert tick.side_hint == 1


def test_side_hint_is_sell_when_the_trade_prints_at_the_bid():
    fields = _REAL_TICK_1.split("|", 3)[-1].split("^")
    fields[5] = fields[35]  # 체결가를 최우선매수호가로
    tick = _first_futures_tick(_bundle("^".join(fields)), _TICK_SIZE, today=_TODAY)

    assert tick is not None
    assert tick.side_hint == -1


def test_side_hint_is_unknown_at_the_mid():
    fields = _REAL_TICK_1.split("|", 3)[-1].split("^")
    fields[5] = "1080.16"  # (1080.30 + 1080.04)/2 = 1080.17 → 54008.5틱 미드, 54008틱 체결
    fields[34], fields[35] = "1080.20", "1080.12"  # 미드 = 54008틱 정확히
    tick = _first_futures_tick(_bundle("^".join(fields)), _TICK_SIZE, today=_TODAY)

    assert tick is not None
    assert tick.mid_ticks == 54008.0
    assert tick.price_ticks == 54008
    assert tick.side_hint == 0


def test_zero_quotes_are_read_as_missing_not_as_a_price_of_zero():
    """KIS는 호가가 없는 순간(장 시작 전, 일방 호가 소진)에 0을 채워 보낸다. 그걸 "0틱"으로
    읽으면 스프레드·미드가 통째로 망가진다 — 없는 것은 None이어야 한다."""
    fields = _REAL_TICK_1.split("|", 3)[-1].split("^")
    fields[34] = fields[35] = "0.00"
    tick = _first_futures_tick(_bundle("^".join(fields)), _TICK_SIZE, today=_TODAY)

    assert tick is not None
    assert tick.ask1_ticks is None
    assert tick.bid1_ticks is None
    assert tick.mid_ticks is None
    assert tick.side_hint == 0
    assert tick.price_ticks == 54015  # 체결 자체는 멀쩡하다 — 버리지 않는다


def test_a_short_frame_without_quotes_still_yields_the_trade():
    """최소 길이를 호가 위치까지 올리면 가격·수량이 멀쩡한 체결을 통째로 버리게 된다."""
    fields = _REAL_TICK_1.split("|", 3)[-1].split("^")[:20]  # idx34~37 없음
    tick = _first_futures_tick(_bundle("^".join(fields)), _TICK_SIZE, today=_TODAY)

    assert tick is not None
    assert tick.price_ticks == 54015
    assert tick.ask1_ticks is None
    assert tick.side_hint == 0


def test_raw_fields_preserve_the_whole_frame_verbatim():
    """필드 의미를 실측으로 확정하기 전에는 이름을 안 붙이되(R8/L16), **보존은 지금 한다** —
    틱은 봉과 달리 과거 조회 경로가 없어 안 받아둔 필드는 영원히 없다."""
    tick = _first_futures_tick(_REAL_TICK_1, _TICK_SIZE, today=_TODAY)

    assert tick is not None
    assert len(tick.raw_fields) == 50  # 2026-07-22 라이브 캡처 실측 폭
    assert tick.raw_fields[0] == "A05608"
    assert tick.raw_fields[18] == "76672"  # 미결제약정으로 보이나 미확정 — 문자열 원본 그대로


def test_parse_futures_ticks_truncated_bundled_frame_falls_back_to_first_record():
    # 실캡처 샘플은 2번째 레코드가 잘려 있어(`^A0560...`) 균등 분할이 불가능하다 —
    # 그 경우 종전대로 첫 레코드만 낸다(폴백 계약).
    ticks = parse_futures_ticks(_REAL_TICK_3_NEXT_MINUTE, _TICK_SIZE, today=_TODAY)

    assert len(ticks) == 1
    assert ticks[0].symbol == "A05608"
    assert ticks[0].price_ticks == 54032  # 1080.64 / 0.02


def _bundle(*records: str, tr_id: str = "H0IFCNT0") -> str:
    """count=N 프레임을 실캡처와 같은 규약으로 조립 — 레코드는 고정폭으로 연접된다."""
    return f"0|{tr_id}|{len(records):03d}|" + "^".join(records)


def _fut_record(hhmmss: str, price: str, qty: str) -> str:
    """실캡처 `_REAL_TICK_1`의 본문(50필드)에서 시각·가격·수량만 바꾼 레코드 1건."""
    fields = _REAL_TICK_1.split("|", 3)[-1].split("^")
    assert len(fields) == 50  # 2026-07-22 라이브 캡처 실측 폭
    fields[1], fields[5], fields[9] = hhmmss, price, qty
    return "^".join(fields)


def test_parse_futures_ticks_returns_every_record_in_bundled_frame():
    # 2026-08-04 이전에는 첫 건만 읽어 거래량의 절반을 버렸다(모듈 docstring 실측표).
    raw = _bundle(
        _fut_record("152953", "1080.30", "3"),
        _fut_record("152954", "1080.50", "5"),
        _fut_record("152955", "1080.10", "7"),
    )

    ticks = parse_futures_ticks(raw, _TICK_SIZE, today=_TODAY)

    assert [t.qty for t in ticks] == [3, 5, 7]
    assert [t.price_ticks for t in ticks] == [54015, 54025, 54005]
    assert [t.ts_exchange.second for t in ticks] == [53, 54, 55]


def test_parse_futures_ticks_bundled_frame_volume_is_the_sum_not_the_first():
    """이 버그의 실제 피해 형태 — 봉 거래량과 종가가 함께 틀렸다."""
    raw = _bundle(
        _fut_record("152953", "1080.30", "3"),
        _fut_record("152954", "1090.00", "5"),
    )
    agg = MinuteBarAggregator(symbol="A05608")
    for tick in parse_futures_ticks(raw, _TICK_SIZE, today=_TODAY):
        agg.add_tick(tick)
    bar = agg.flush_final()

    assert bar.volume == 8  # 첫 건만 읽던 시절엔 3
    assert bar.c_ticks == 54500  # 1090.00 — 버려지던 마지막 체결
    assert bar.h_ticks == 54500


def test_parse_futures_ticks_skips_broken_record_but_keeps_the_rest():
    raw = _bundle(
        _fut_record("152953", "1080.30", "3"),
        _fut_record("152954", "NOT_A_NUMBER", "5"),
        _fut_record("152955", "1080.10", "7"),
    )

    ticks = parse_futures_ticks(raw, _TICK_SIZE, today=_TODAY)

    assert [t.qty for t in ticks] == [3, 7]


def test_parse_option_ticks_returns_every_record_in_bundled_frame():
    # 옵션 레코드 폭은 실측 캡처가 없다 — 파서가 폭을 하드코딩하지 않고 count로 유도하므로
    # 임의 폭(10필드)으로도 정확히 나뉜다는 것이 이 테스트의 요지다.
    rec = lambda hhmmss, price: "^".join(["201W09", hhmmss, price] + ["0"] * 6 + ["2"])  # noqa: E731
    raw = _bundle(rec("101500", "1.50"), rec("101501", "1.60"), tr_id="H0IOCNT0")

    ticks = parse_option_ticks(raw, Decimal("0.01"), today=_TODAY)

    assert [t.price_ticks for t in ticks] == [150, 160]
    assert [t.ts_exchange.second for t in ticks] == [0, 1]


def test_parse_futures_ticks_uneven_split_logs_fallback(monkeypatch):
    """조용히 버리지 않는다 — 폴백은 반드시 흔적을 남긴다."""
    logged = []
    monkeypatch.setattr(
        "messiah.data.normalizer.mlog.log",
        lambda tag, msg, **kw: logged.append((tag, kw)),
    )
    # count=003인데 본문은 2레코드분(100필드) — 나눠떨어지지 않는다.
    raw = "0|H0IFCNT0|003|" + "^".join(
        [_fut_record("152953", "1080.30", "3"), _fut_record("152954", "1080.50", "5")]
    )

    ticks = parse_futures_ticks(raw, _TICK_SIZE, today=_TODAY)

    assert len(ticks) == 1
    assert logged and logged[0][0] == "TickFrameSplitFallback"
    assert logged[0][1]["record_count"] == 3
    assert logged[0][1]["field_count"] == 100


def test_parse_futures_tick_strips_ws_envelope_header():
    no_header = _REAL_TICK_1.split("|", 3)[-1]
    with_header = _REAL_TICK_1

    a = _first_futures_tick(no_header, _TICK_SIZE, today=_TODAY)
    b = _first_futures_tick(with_header, _TICK_SIZE, today=_TODAY)

    assert (a.symbol, a.ts_exchange, a.price_ticks, a.qty) == (
        b.symbol,
        b.ts_exchange,
        b.price_ticks,
        b.qty,
    )


def test_parse_futures_tick_invalid_format_returns_none():
    assert _first_futures_tick("garbage", _TICK_SIZE) is None
    assert _first_futures_tick("1^2^3", _TICK_SIZE) is None


def test_parse_futures_tick_bad_number_returns_none():
    broken = _REAL_TICK_1.replace("^1080.30^", "^NOT_A_NUMBER^", 1)
    assert _first_futures_tick(broken, _TICK_SIZE, today=_TODAY) is None


def test_parse_option_tick_valid_h0iocnt0_format():
    raw = "0|H0IOCNT0|001|" + "^".join(["201W09", "101500"] + ["0"] * 8)
    tick = _first_option_tick(raw, Decimal("0.01"), today=_TODAY)

    assert tick is not None
    assert tick.symbol == "201W09"
    assert tick.ts_exchange.hour == 10
    assert tick.ts_exchange.minute == 15


def test_parse_option_tick_invalid_format_returns_none():
    assert _first_option_tick("garbage", Decimal("0.01")) is None


# ---------------------------------------------------------------- MinuteBarAggregator


# --------------------------------------- 세션 구분 · 시세 정합성 (2026-07-31, P1-2)


def _tick(hhmmss: str, price: str) -> Tick:
    """실캡처 프레임의 시각·가격만 바꿔 재사용 — 필드 배치는 실측 그대로 유지한다."""
    raw = _REAL_TICK_1.replace("^152953^", f"^{hhmmss}^", 1).replace("^1080.30^", f"^{price}^", 1)
    tick = _first_futures_tick(raw, _TICK_SIZE, today=_TODAY)
    assert tick is not None
    return tick


def _bar_from(agg: MinuteBarAggregator, ticks: list[Tick]) -> BarClosed:
    for tick in ticks:
        agg.add_tick(tick)
    bar = agg.flush_final()
    assert bar is not None
    return bar


def test_pre_open_bars_are_labelled_as_such():
    """2026-07-31 실측 근거 — 그날 08:45~09:04의 20봉이 전부 `o=h=l=c=46633`으로 고정돼
    있다가 09:05에 6.1% 점프했다. 정규장 봉과 구분 없이 아카이브·웜스타트·차트에 섞여
    들어가던 것을 표시한다(버리지는 않는다)."""
    bar = _bar_from(MinuteBarAggregator(symbol="A05608"), [_tick("084500", "932.66")])

    assert bar.session is BarSession.PRE_OPEN


def test_regular_session_bars_keep_the_default_label():
    bar = _bar_from(MinuteBarAggregator(symbol="A05608"), [_tick("090000", "990.00")])

    assert bar.session is BarSession.REGULAR


def test_the_0900_boundary_belongs_to_the_regular_session():
    """반개구간 [09:00, …) — `EventCalendar.is_regular_session()`과 같은 경계 규약."""
    assert _bar_from(MinuteBarAggregator("A05608"), [_tick("085959", "990.00")]).session is (
        BarSession.PRE_OPEN
    )
    assert _bar_from(MinuteBarAggregator("A05608"), [_tick("090001", "990.00")]).session is (
        BarSession.REGULAR
    )


def test_price_jump_between_bars_marks_the_bar_low_quality():
    """2026-07-31 09:05봉 회귀 — `o=46633, h=49488`로 봉 하나가 6.1% 범위였는데
    `quality_ok=True`로 통과해 ATR·변동성 피처와 다음날 웜스타트로 그대로 흘러갔다."""
    agg = MinuteBarAggregator(symbol="A05608")
    for tick in [_tick("090400", "932.66")] * 3:  # 3틱 — 틱 수 조건은 충족
        agg.add_tick(tick)
    first = agg.add_tick(_tick("090500", "989.76"))  # +6.1% 점프한 다음 분

    assert first is not None and first.quality_ok is True  # 직전 봉 자체는 정상
    jumped = agg.flush_final()
    assert jumped is not None
    assert jumped.quality_ok is False  # 점프한 봉이 저품질로 표시된다


def test_normal_price_movement_keeps_quality_ok():
    agg = MinuteBarAggregator(symbol="A05608")
    for tick in [_tick("090400", "1000.00")] * 3:
        agg.add_tick(tick)
    agg.add_tick(_tick("090500", "1005.00"))  # +0.5% — 정상 범위
    for tick in [_tick("090501", "1005.00")] * 2:
        agg.add_tick(tick)

    bar = agg.flush_final()

    assert bar is not None and bar.quality_ok is True


def test_first_bar_has_no_baseline_so_it_is_never_a_jump():
    """직전 봉이 없으면 "튀었다"고 말할 근거가 없다 — 매일 첫 봉이 저품질로 찍히면 안 된다."""
    agg = MinuteBarAggregator(symbol="A05608")
    bar = _bar_from(agg, [_tick("090000", "5000.00")] * 3)

    assert bar.quality_ok is True


def test_minute_bar_aggregator_accumulates_within_same_minute_returns_none():
    agg = MinuteBarAggregator(symbol="A05608")
    t1 = _first_futures_tick(_REAL_TICK_1, _TICK_SIZE, today=_TODAY)
    t2 = _first_futures_tick(_REAL_TICK_2, _TICK_SIZE, today=_TODAY)

    assert agg.add_tick(t1) is None
    assert agg.add_tick(t2) is None  # 같은 분(15:29) — 아직 봉 미완성


def test_minute_bar_aggregator_flushes_completed_bar_on_minute_rollover():
    agg = MinuteBarAggregator(symbol="A05608")
    t1 = _first_futures_tick(_REAL_TICK_1, _TICK_SIZE, today=_TODAY)
    t2 = _first_futures_tick(_REAL_TICK_2, _TICK_SIZE, today=_TODAY)
    t3 = _first_futures_tick(_REAL_TICK_3_NEXT_MINUTE, _TICK_SIZE, today=_TODAY)

    agg.add_tick(t1)
    agg.add_tick(t2)
    bar = agg.add_tick(t3)  # 15:30분 틱 도착 — 15:29분 봉이 완성돼 flush됨

    assert bar is not None
    assert bar.symbol == "A05608"
    assert bar.horizon == Horizon.M1
    assert bar.bar_open_kst.minute == 29
    assert bar.o_ticks == t1.price_ticks
    assert bar.c_ticks == t2.price_ticks  # 그 분의 마지막 틱
    assert bar.volume == t1.qty + t2.qty
    assert bar.quality_ok is False  # 2틱 < MIN_TICKS_FOR_QUALITY_OK(3)


def test_minute_bar_aggregator_quality_ok_when_enough_ticks():
    agg = MinuteBarAggregator(symbol="A05608")
    agg.add_tick(_first_futures_tick(_REAL_TICK_1, _TICK_SIZE, today=_TODAY))
    agg.add_tick(_first_futures_tick(_REAL_TICK_2, _TICK_SIZE, today=_TODAY))
    agg.add_tick(_first_futures_tick(_REAL_TICK_1, _TICK_SIZE, today=_TODAY))  # 3번째(중복 재사용)

    bar = agg.flush_final()

    assert bar is not None
    assert bar.quality_ok is True


def test_minute_bar_aggregator_ohlc_reflects_price_extremes():
    agg = MinuteBarAggregator(symbol="A05608")
    low = _first_futures_tick(_REAL_TICK_1, _TICK_SIZE, today=_TODAY)  # 1080.30
    high_raw = _REAL_TICK_2.replace("^1080.30^", "^1090.00^", 1)
    high = _first_futures_tick(high_raw, _TICK_SIZE, today=_TODAY)  # 같은 분(15:29:54)

    agg.add_tick(low)
    assert agg.add_tick(high) is None  # 아직 같은 분 — 미완성
    bar = agg.flush_final()

    assert bar.h_ticks == high.price_ticks
    assert bar.l_ticks == low.price_ticks


def test_minute_bar_aggregator_flush_final_with_no_ticks_returns_none():
    agg = MinuteBarAggregator(symbol="A05608")
    assert agg.flush_final() is None


def test_minute_bar_aggregator_keeps_separate_symbols_independent():
    agg_a = MinuteBarAggregator(symbol="A05608")
    agg_b = MinuteBarAggregator(symbol="OTHER")

    t1 = _first_futures_tick(_REAL_TICK_1, _TICK_SIZE, today=_TODAY)
    agg_a.add_tick(t1)
    agg_b.add_tick(t1)

    bar_a = agg_a.flush_final()
    bar_b = agg_b.flush_final()

    assert bar_a.symbol == "A05608"
    assert bar_b.symbol == "OTHER"
