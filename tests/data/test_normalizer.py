from datetime import date
from decimal import Decimal

from messiah.core.messages import Horizon
from messiah.data.normalizer import MinuteBarAggregator, parse_futures_tick, parse_option_tick

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


def test_parse_futures_tick_matches_real_captured_sample():
    tick = parse_futures_tick(_REAL_TICK_1, _TICK_SIZE, today=_TODAY)

    assert tick is not None
    assert tick.symbol == "A05608"
    assert tick.ts_exchange.hour == 15
    assert tick.ts_exchange.minute == 29
    assert tick.ts_exchange.second == 53
    assert tick.ts_exchange.utcoffset().total_seconds() == 9 * 3600  # KST
    assert tick.price_ticks == 54015  # 1080.30 / 0.02
    assert tick.qty == 1
    assert tick.side_hint == 0
    assert tick.source == "kis"


def test_parse_futures_tick_handles_bundled_frame_by_reading_first_record():
    # count=002(여러 체결 묶임)여도 첫 레코드는 정상 파싱된다 — mahdi도 동일 한계
    # (main.py _parse_futures_tick)를 그대로 가져와 두 번째 이후 레코드는 파싱하지 않는다.
    tick = parse_futures_tick(_REAL_TICK_3_NEXT_MINUTE, _TICK_SIZE, today=_TODAY)

    assert tick is not None
    assert tick.symbol == "A05608"
    assert tick.price_ticks == 54032  # 1080.64 / 0.02


def test_parse_futures_tick_strips_ws_envelope_header():
    no_header = _REAL_TICK_1.split("|", 3)[-1]
    with_header = _REAL_TICK_1

    a = parse_futures_tick(no_header, _TICK_SIZE, today=_TODAY)
    b = parse_futures_tick(with_header, _TICK_SIZE, today=_TODAY)

    assert (a.symbol, a.ts_exchange, a.price_ticks, a.qty) == (
        b.symbol,
        b.ts_exchange,
        b.price_ticks,
        b.qty,
    )


def test_parse_futures_tick_invalid_format_returns_none():
    assert parse_futures_tick("garbage", _TICK_SIZE) is None
    assert parse_futures_tick("1^2^3", _TICK_SIZE) is None


def test_parse_futures_tick_bad_number_returns_none():
    broken = _REAL_TICK_1.replace("^1080.30^", "^NOT_A_NUMBER^", 1)
    assert parse_futures_tick(broken, _TICK_SIZE, today=_TODAY) is None


def test_parse_option_tick_valid_h0iocnt0_format():
    raw = "0|H0IOCNT0|001|" + "^".join(["201W09", "101500"] + ["0"] * 8)
    tick = parse_option_tick(raw, Decimal("0.01"), today=_TODAY)

    assert tick is not None
    assert tick.symbol == "201W09"
    assert tick.ts_exchange.hour == 10
    assert tick.ts_exchange.minute == 15


def test_parse_option_tick_invalid_format_returns_none():
    assert parse_option_tick("garbage", Decimal("0.01")) is None


# ---------------------------------------------------------------- MinuteBarAggregator


def test_minute_bar_aggregator_accumulates_within_same_minute_returns_none():
    agg = MinuteBarAggregator(symbol="A05608")
    t1 = parse_futures_tick(_REAL_TICK_1, _TICK_SIZE, today=_TODAY)
    t2 = parse_futures_tick(_REAL_TICK_2, _TICK_SIZE, today=_TODAY)

    assert agg.add_tick(t1) is None
    assert agg.add_tick(t2) is None  # 같은 분(15:29) — 아직 봉 미완성


def test_minute_bar_aggregator_flushes_completed_bar_on_minute_rollover():
    agg = MinuteBarAggregator(symbol="A05608")
    t1 = parse_futures_tick(_REAL_TICK_1, _TICK_SIZE, today=_TODAY)
    t2 = parse_futures_tick(_REAL_TICK_2, _TICK_SIZE, today=_TODAY)
    t3 = parse_futures_tick(_REAL_TICK_3_NEXT_MINUTE, _TICK_SIZE, today=_TODAY)

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
    agg.add_tick(parse_futures_tick(_REAL_TICK_1, _TICK_SIZE, today=_TODAY))
    agg.add_tick(parse_futures_tick(_REAL_TICK_2, _TICK_SIZE, today=_TODAY))
    agg.add_tick(parse_futures_tick(_REAL_TICK_1, _TICK_SIZE, today=_TODAY))  # 3번째(중복 재사용)

    bar = agg.flush_final()

    assert bar is not None
    assert bar.quality_ok is True


def test_minute_bar_aggregator_ohlc_reflects_price_extremes():
    agg = MinuteBarAggregator(symbol="A05608")
    low = parse_futures_tick(_REAL_TICK_1, _TICK_SIZE, today=_TODAY)  # 1080.30
    high_raw = _REAL_TICK_2.replace("^1080.30^", "^1090.00^", 1)
    high = parse_futures_tick(high_raw, _TICK_SIZE, today=_TODAY)  # 같은 분(15:29:54)

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

    t1 = parse_futures_tick(_REAL_TICK_1, _TICK_SIZE, today=_TODAY)
    agg_a.add_tick(t1)
    agg_b.add_tick(t1)

    bar_a = agg_a.flush_final()
    bar_b = agg_b.flush_final()

    assert bar_a.symbol == "A05608"
    assert bar_b.symbol == "OTHER"
