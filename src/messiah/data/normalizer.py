"""KIS WS 실시간체결가 원시 메시지 -> core.messages.Tick/BarClosed 정규화.

마흐디(선행 옵션 프로젝트) mahdi/main.py의 _parse_tick()/_parse_futures_tick()
(main.py:355-447)과 mahdi/data/collector.py의 MinuteBarAggregator(102-183)에서 필드 인덱스·
집계 로직을 가져왔다. 필드 인덱스 출처: docs/efriend 한국투자증권 오픈API 공식 문서
(API ID 실시간-010 "지수선물 실시간체결가"/실시간-014 "지수옵션 실시간체결가"), 마흐디
2026-07-06 실측. 선물(H0IFCNT0) 인덱스는 2026-07-22 messiah 자체 라이브 WS 캡처(ws_client.py
실측 세션, A05608)로 symbol(idx0)·시각(idx1)·가격(idx5)·거래량(idx9)·매도호가/매수호가/잔량
(idx34~37)까지 실제 데이터와 교차검증했다 — 옵션(H0IOCNT0) 인덱스도 2026-07-23
TickCollector.run_forever() 실측 세션에서 처음으로 messiah 자체 라이브 캡처로 재검증했다
(위클리 목요일물 근월 풋 C09F7WA45, 만기 당일이라 거래량이 두터워 70건 이상 실틱 확보 —
symbol(idx0)·시각(idx1, HHMMSS가 수신 당시 KST 벽시계와 일치)·가격(idx2)·거래량(idx9) 전부
파싱 결과와 원시 프레임을 직접 대조해 확인). 정규월물은 이 세션 시점 거래량이 너무 얇아
(당일 누적 0~23건) 별도로 시도했으나 틱을 못 잡았음 — 위 재검증은 위클리 상품 기준이며 필드
배치 자체는 정규/위클리가 동일 TR(H0IOCNT0)이라 공통이다.

messiah의 Tick(core.messages)은 마흐디 Tick과 달리 bid/ask를 안 들고 있다 — 그래서
side_hint(틱룰 보조)는 항상 0(불명)으로 채운다. OFI/VWAP/microprice/스프레드처럼 마흐디의
MinuteBar가 갖고 있던 필드들은 messiah의 BarClosed에 아예 없다 — 그런 계산은 L1이 아니라
L2 Feature Engine의 몫이라는 messiah 스키마 설계(core/messages.py)를 따른다.

## 다중 레코드 프레임 (2026-08-04 수정 — 그 전까지 체결의 절반을 버리고 있었다)

데이터건수(헤더 3번째 필드)가 1보다 크면 여러 체결이 한 WS 프레임에 묶여 온다. 2026-08-04
이전에는 **첫 레코드만 파싱하고 이후 레코드를 조용히 버렸다**(마흐디 원본 main.py의
_parse_tick/_parse_futures_tick이 가진 한계를 그대로 이식했고, "별도 개선 대상"으로만
적어 뒀다). 그 영향 규모는 측정된 적이 없었는데, 이날 KIS 공식 분봉
(`inquire-time-fuopchartprice`)과 우리 아카이브를 대조해 처음 측정했다 — A05608 3거래일:

| 날짜 | 공식 총거래량 | 우리 아카이브 | 종가 불일치 | 고가/저가 불일치 |
|---|---|---|---|---|
| 2026-08-03 | 152,618 | 78,080 (51%) | 104/410 | 129 / 126 |
| 2026-07-31 | 112,521 | 58,942 (52%) | 77/380 | 90 / 93 |
| 2026-07-29 | 212,238 | 103,700 (49%) | 109/375 | 103 / 115 |

거래량이 절반이라는 건 프레임당 평균 2건이 묶여 온다는 뜻이고, 종가·고저가가 20~29% 어긋난
것은 그 버려진 레코드가 그 분의 마지막/최고/최저 체결이었던 경우다. 즉 **거래량 계열 피처
전부와 종가 기반 레이블의 1/4이 틀린 값 위에 있었다**.

`_split_ws_records()`가 헤더의 데이터건수만큼 본문을 균등 분할한다. 레코드 폭을 TR별로
하드코딩하지 않고 `len(fields) // count`로 유도하는 이유: 폭은 TR마다 다른데(선물 50개 실측)
옵션(H0IOCNT0)은 자체 실측 캡처가 없어 상수로 박으면 그 자체가 미검증 가정이 된다. KIS
프레임은 레코드가 고정폭으로 연접되므로 나눠떨어짐 자체가 검증이고, 안 나눠떨어지면(잘린
프레임) 전체를 1레코드로 보는 종전 동작으로 폴백하면서 `TickFrameSplitFallback`을 남긴다 —
조용히 넘어가면 이 버그의 재발을 또 못 본다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from datetime import time as dtime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from messiah.core import logging as mlog
from messiah.core.event_calendar import DEFAULT_SESSION
from messiah.core.messages import BarClosed, BarSession, Horizon, Tick
from messiah.core.timeutil import KST, ensure_aware, now_kst, to_kst

# 시각 구동 1분봉 확정(`MinuteBarAggregator.flush_due`)의 경계 이후 유예 (고도화 1).
#
# **실측으로 확정한 값이다** (2026-08-11 G-4). 2026-08-05에 수신 지연 계측
# (`ops/clock_skew.delivery_latency_seconds`)을 붙이고 3거래일치 분포를 모았다:
#
#     날짜       p50     p90     p99     최대     표본
#     08-05    0.5065  0.9208  1.0239  1.2973    9,115
#     08-06    0.5121  0.9312  1.0322  1.1297   20,000
#     08-10    0.5262  0.9314  1.0353  1.3964   20,000
#
# 종전 값 1.0초는 **관측된 최대(1.3964초)보다 작았다** — 그 상태로 `timer`에 승격했다면
# 유예 뒤 도착한 틱을 매일 버렸을 것이다. 유실을 고치려던 변경이 다른 유실을 들여오는,
# 이 축이 애초에 경계하던 형태 그대로다.
#
# 2.0초를 고른 근거: 관측 최대 위로 43% 여유. 그리고 이 계측은 `frac(t)`만큼 **과대평가된
# 상한**이라(`TickCollector.log_delivery_latency()`) 실제 여유는 더 크다. 다음 분 경계
# (60초)와는 비교도 안 되게 안쪽이라 경계 침범 위험은 없다.
#
# **3거래일이다.** `dev_memory/NEXT_TODO.md`가 "4거래일치 확보"라고 적었는데 사실이 아니다 —
# 08-07은 13:41에 프로세스가 죽어 세션 요약(`TickDeliveryLatency`)이 안 찍혔고,
# `daily_integrity_20260807.json`의 `delivery_latency`는 `null`이다. 08-11이 4일째 표본이
# 되며, 그 값이 2.0초를 넘으면 이 상수를 다시 올려야 한다(넘을 것 같지는 않지만 그건 예측이다).
MINUTE_CLOSE_GRACE_SECONDS = 2.0

# H0IFCNT0(지수선물 실시간체결가) 필드 인덱스 — "^" 구분, 0-based. 실제 메시지는 50개 필드다
# (2026-07-22 라이브 캡처로 확인).
#
# 2026-08-04(F2)부터 호가 4개(idx34~37)도 읽는다. 그 전까지는 symbol/시각/가격/거래량 4개만
# 읽고 **나머지 46필드를 버렸는데**, 그 결과 MS(마이크로구조) 카테고리 30개가 "호가 데이터가
# 없다"는 이유로 통째로 미착수였다 — 데이터가 없던 게 아니라 파서가 안 읽었을 뿐이다.
# idx34~37은 2026-07-22 캡처에서 실제 데이터와 교차검증된 위치다(모듈 docstring).
#
# **여기 이름 붙인 것 외의 필드는 해석하지 않는다.** 미결제약정·이론가·총잔량·체결강도로
# 보이는 필드가 더 있지만 위치를 실측으로 확정한 적이 없고, 추정으로 스키마를 정하는 것이
# 정확히 마흐디 L16 사고(단위 미확인 스키마로 5일치 유실)의 형태다. 대신 프레임 전체를
# `Tick.raw_fields`에 실어 `data/tick_archiver.py`가 통째로 보존한다 — 실측이 끝나면 그때
# 소급해서 쓸 수 있다(틱은 봉과 달리 과거 조회 경로가 없어 안 받아두면 영원히 없다).
_FUT_IDX_SYMBOL = 0
_FUT_IDX_BSOP_HOUR = 1
_FUT_IDX_PRICE = 5
_FUT_IDX_QTY = 9
_FUT_IDX_ASKP1 = 34
_FUT_IDX_BIDP1 = 35
_FUT_IDX_ASKP_RSQN1 = 36
_FUT_IDX_BIDP_RSQN1 = 37
# 최소 길이는 여전히 idx9까지다 — 호가는 **있으면 읽고 없으면 넘어간다**. idx37까지로 올리면
# 호가가 없는 짧은 프레임에서 가격·수량이 멀쩡한데도 체결 자체를 통째로 버리게 된다.
_FUT_MIN_FIELDS = _FUT_IDX_QTY + 1

# H0IOCNT0(지수옵션 실시간체결가) 필드 인덱스 — 선물과 필드 순서가 다르다(가격이 idx2).
_OPT_IDX_SYMBOL = 0
_OPT_IDX_BSOP_HOUR = 1
_OPT_IDX_PRICE = 2
_OPT_IDX_QTY = 9
_OPT_MIN_FIELDS = _OPT_IDX_QTY + 1


def _split_ws_records(raw: str) -> list[list[str]]:
    """ "암호화유무|TR_ID|데이터건수|실제데이터" 헤더를 읽어 본문을 **레코드별** 필드로 나눈다.

    계산: 헤더의 데이터건수(count)만큼 "^" 필드를 균등 분할한다. 레코드 폭은 상수가 아니라
         `len(fields) // count`로 유도한다(모듈 docstring "다중 레코드 프레임" 참고).
    해석: 헤더가 없는 입력(순수 "^" 데이터만)은 `split("|", 3)`이 1조각을 주므로 count=1로
         읽혀 원본이 그대로 1레코드가 된다(mahdi 2026-07-06 실측 근거 — main.py:372-375).
    실패 조건: 없다 — count가 숫자가 아니거나 본문이 count로 안 나눠떨어지면(잘린 프레임)
              전체를 1레코드로 반환한다. 파싱은 앞쪽 인덱스만 읽으므로 이 폴백은 2026-08-04
              이전과 정확히 같은 결과(첫 레코드)를 낸다 — 다만 조용히 넘어가지 않고
              `TickFrameSplitFallback`을 남긴다.
    """
    parts = raw.split("|", 3)
    fields = parts[-1].split("^")
    if len(parts) < 4:
        return [fields]
    try:
        count = int(parts[2])
    except ValueError:
        count = 1
    if count <= 1:
        return [fields]
    if len(fields) % count != 0:
        mlog.log(
            "TickFrameSplitFallback",
            f"데이터건수 {count}건인데 필드 {len(fields)}개가 균등 분할 안 됨 — "
            f"첫 레코드만 파싱(나머지 유실)",
            tr_id=parts[1],
            record_count=count,
            field_count=len(fields),
        )
        return [fields]
    width = len(fields) // count
    return [fields[i * width : (i + 1) * width] for i in range(count)]


def _price_to_ticks(raw_price: str, tick_size: Decimal) -> int:
    return int((Decimal(raw_price) / tick_size).to_integral_value(rounding=ROUND_HALF_UP))


def _combine_kst(hhmmss: str, today: date) -> datetime:
    tick_time = dtime(int(hhmmss[0:2]), int(hhmmss[2:4]), int(hhmmss[4:6]))
    return datetime.combine(today, tick_time, tzinfo=KST)


def _optional_ticks(fields: list[str], index: int, tick_size: Decimal) -> int | None:
    """호가 1개를 정수 틱으로 — 못 읽으면 None(그 필드만 없는 것이지 레코드가 깨진 게 아니다).

    0은 **None으로 본다**. KIS는 호가가 없는 순간(장 시작 전, 일방 호가 소진)에 0을 채워
    보내는데, 그걸 "가격이 0틱"으로 읽으면 스프레드·미드가 통째로 망가진다.
    """
    try:
        value = _price_to_ticks(fields[index], tick_size)
    except (ValueError, IndexError, InvalidOperation, ArithmeticError):
        return None
    return value if value > 0 else None


def _optional_int(fields: list[str], index: int) -> int | None:
    """잔량 1개 — 못 읽으면 None. 여기서는 0을 **그대로 0으로 둔다**(가격과 달리 잔량 0은
    "최우선호가에 물량이 없다"는 실제 상태이고, 흔하지는 않지만 의미가 있다)."""
    try:
        return int(Decimal(fields[index]))
    except (ValueError, IndexError, InvalidOperation, ArithmeticError):
        return None


def _quote_rule_side(price_ticks: int, bid: int | None, ask: int | None) -> int:
    """체결의 주도 방향 — quote rule(체결가 vs 미드): 미드 위면 매수주도(+1), 아래면 -1.

    **알려진 근사**: Lee-Ready(1991)는 체결 **직전**의 호가와 비교하고 5초 지연을 권한다.
    여기 쓸 수 있는 것은 같은 프레임에 실려 온 **동시 스냅샷**이라 체결 후 갱신된 호가일 수
    있다 — 그러면 방향이 반대로 잡히는 체결이 일부 생긴다.

    그래도 0(불명)으로 두는 것보다 낫다는 판단이다: 부호가 섞여도 집계량(`ms_vol_delta`·
    `ms_tick_rule`)은 편향이 아니라 잡음으로 나타나고, 편향 방향이 한쪽으로 쏠린다면 그건
    실측으로 재는 대상이지 지금 추측할 것이 아니다. 이 한계는 `Docs/capability_matrix.md`의
    알려진 갭에 기록한다 — MS 피처를 실제로 학습에 넣기 전(F6)에 재검토한다.

    미드와 정확히 같으면 0 — 틱룰(직전 체결가 대비) 폴백은 여기서 안 한다. 그건 레코드 하나가
    아니라 시계열이 필요해 파서(무상태)의 범위를 벗어나고, MS 계산기가 봉 단위로 하면 된다.
    """
    if bid is None or ask is None or ask <= bid:
        return 0  # 호가가 없거나 역전(크로스) — 판정 근거가 없다
    mid = (bid + ask) / 2.0
    if price_ticks > mid:
        return 1
    if price_ticks < mid:
        return -1
    return 0


def _record_to_tick(
    fields: list[str],
    tick_size: Decimal,
    *,
    min_fields: int,
    idx_symbol: int,
    idx_hour: int,
    idx_price: int,
    idx_qty: int,
    source: str,
    today: date,
    quote_indices: tuple[int, int, int, int] | None = None,
) -> Tick | None:
    """레코드 1건 → Tick. 실패 시 None(해당 레코드만 버리고 같은 프레임의 나머지는 계속).

    `quote_indices`는 (매도호가1, 매수호가1, 매도잔량1, 매수잔량1)의 필드 위치 — 주면 호가를
    함께 읽고 `side_hint`를 quote rule로 채운다. 호가 필드가 짧거나 못 읽히면 호가만 None으로
    두고 체결 자체는 그대로 낸다(가격·수량은 멀쩡하다).
    """
    if len(fields) < min_fields:
        return None
    try:
        price_ticks = _price_to_ticks(fields[idx_price], tick_size)
        ask1 = bid1 = ask_qty1 = bid_qty1 = None
        if quote_indices is not None and len(fields) > max(quote_indices):
            idx_ask, idx_bid, idx_ask_qty, idx_bid_qty = quote_indices
            ask1 = _optional_ticks(fields, idx_ask, tick_size)
            bid1 = _optional_ticks(fields, idx_bid, tick_size)
            ask_qty1 = _optional_int(fields, idx_ask_qty)
            bid_qty1 = _optional_int(fields, idx_bid_qty)
        return Tick(
            symbol=fields[idx_symbol],
            ts_exchange=_combine_kst(fields[idx_hour], today),
            price_ticks=price_ticks,
            qty=int(Decimal(fields[idx_qty])),
            side_hint=_quote_rule_side(price_ticks, bid1, ask1),
            source=source,
            bid1_ticks=bid1,
            ask1_ticks=ask1,
            bid_qty1=bid_qty1,
            ask_qty1=ask_qty1,
            # 프레임 전체를 그대로 — 해석은 나중에, 보존은 지금(모듈 상단 인덱스 주석).
            raw_fields=tuple(fields),
        )
    except (ValueError, IndexError, InvalidOperation, ArithmeticError):
        return None


def parse_futures_ticks(
    raw: str, tick_size: Decimal, *, source: str = "kis", today: date | None = None
) -> list[Tick]:
    """
    입력: WS로 수신한 H0IFCNT0 원시 프레임 1건(체결 1건 이상이 묶여 있을 수 있다), 종목 1틱의
         실제 가격 크기(KISBrokerAdapter와 동일한 하드코딩 금지 원칙 — 호출측이 실측값을 주입).
    계산: 헤더의 데이터건수만큼 레코드 분할(`_split_ws_records`) → 각 레코드의 영업시간
         (HHMMSS)을 today(기본 오늘 KST 날짜)와 결합해 tz-aware ts_exchange 생성 → 가격을
         tick_size로 나눠 정수 price_ticks로 변환(SYSTEM.md R2, float 금지).
    산출: 프레임에 실린 순서(= 체결 시각 순) 그대로의 Tick 목록. 한 건도 못 읽으면 빈 목록.
    해석: 2026-08-04(F2)부터 L1 호가(idx34~37)도 함께 읽고 `side_hint`를 quote rule로 채운다
         (`_quote_rule_side()`의 알려진 근사 참고). 프레임 전체는 `raw_fields`에 보존한다.
    실패 조건: 없다 — 필드 수 부족·숫자 변환 실패한 레코드는 건너뛰고 나머지는 그대로 낸다
              (레코드 1건의 파손이 같은 프레임의 성한 체결까지 버리게 두지 않는다).
    """
    day = today or now_kst().date()
    ticks = []
    for fields in _split_ws_records(raw):
        tick = _record_to_tick(
            fields,
            tick_size,
            min_fields=_FUT_MIN_FIELDS,
            idx_symbol=_FUT_IDX_SYMBOL,
            idx_hour=_FUT_IDX_BSOP_HOUR,
            idx_price=_FUT_IDX_PRICE,
            idx_qty=_FUT_IDX_QTY,
            source=source,
            today=day,
            quote_indices=(
                _FUT_IDX_ASKP1,
                _FUT_IDX_BIDP1,
                _FUT_IDX_ASKP_RSQN1,
                _FUT_IDX_BIDP_RSQN1,
            ),
        )
        if tick is not None:
            ticks.append(tick)
    return ticks


def parse_option_ticks(
    raw: str, tick_size: Decimal, *, source: str = "kis", today: date | None = None
) -> list[Tick]:
    """H0IOCNT0(지수옵션 실시간체결가) 버전 — 필드 배치만 다르고 나머지는 parse_futures_ticks와
    동일 계약. 옵션 인덱스는 마흐디 인용만 반영, messiah 자체 라이브 재검증은 안 됨(모듈
    docstring 참고)."""
    day = today or now_kst().date()
    ticks = []
    for fields in _split_ws_records(raw):
        tick = _record_to_tick(
            fields,
            tick_size,
            min_fields=_OPT_MIN_FIELDS,
            idx_symbol=_OPT_IDX_SYMBOL,
            idx_hour=_OPT_IDX_BSOP_HOUR,
            idx_price=_OPT_IDX_PRICE,
            idx_qty=_OPT_IDX_QTY,
            source=source,
            today=day,
        )
        if tick is not None:
            ticks.append(tick)
    return ticks


class MinuteBarAggregator:
    """symbol 1개에 대해 틱을 누적하고, 분이 바뀌면 완성된 BarClosed(1분봉)를 flush한다.

    마흐디 mahdi/data/collector.py MinuteBarAggregator(102-183) 이식 — OFI/VWAP/microprice/
    스프레드는 messiah BarClosed에 없는 필드라 제외(L2 Feature Engine 몫).

    ## 세션 구분과 시세 정합성 (2026-07-31 신설)

    두 가지를 봉에 표시한다. 둘 다 **버리지 않고 표시만** 한다 — 파기는 되돌릴 수 없고,
    "이 데이터를 쓸 것인가"는 소비자(웜스타트·Trainer·차트)별로 나중에 정할 수 있다.

    - **`session`**: `bar_open_kst`가 정규장 개시(09:00) 전이면 `PRE_OPEN`
      (`core/messages.py`의 `BarSession` docstring에 2026-07-31 실측 근거).
    - **`quality_ok=False` + `TickPriceJump` 로그**: 직전 완성봉 종가 대비 시가 변화율이
      `PRICE_JUMP_RATIO`를 넘으면. 2026-07-31 09:05봉이 정확히 이 경우였다 —
      `o=46633, h=49488`로 봉 하나가 6.1% 범위였는데 `quality_ok=True`로 통과했고, 그 값이
      ATR·변동성 피처와 다음날 웜스타트로 그대로 흘러갔다.

    직전 종가는 **이 인스턴스가 만든 봉**만 기준으로 삼는다. 재연결 시 `collector.py`가
    aggregator를 새로 만드므로 기준선도 함께 리셋되는데, 그게 맞다 — 단절 구간을 사이에 둔
    두 봉의 변화율은 "가격이 튀었다"가 아니라 "그 사이를 못 봤다"이기 때문이다.

    ## 봉을 언제 닫는가 — 두 경로 (2026-08-05 2차, 고도화 1)

    원래 이 클래스는 **다음 분의 첫 틱이 도착해야** 이전 분의 봉을 내놓았다(`add_tick`).
    그 결과 1분봉의 발행 시각을 시계가 아니라 **틱 도착률**이 정했고, 2026-08-05 실측으로
    발행 지연 중앙값이 0.655초·p90 1.62초·최대 7.96초였다. 상위 Horizon 합성기가 경계+0.5초에
    확정하므로 69%가 그 뒤에 도착했고, 상위 봉이 매 버킷 한 분씩 잘렸다
    (`data/bar_composer.py` "스큐를 고쳤더니 드러난 것").

    합성기 쪽은 겹④(마지막 구성봉 대기)로 막았다. 그건 **정확하지만 느리다** — 매 상위 봉이
    1분봉을 기다린 만큼 늦게 나간다. 근본 처방은 1분봉 자체를 시각으로 닫는 것이다:

    - **`add_tick`(틱 구동, 기본)** — 종전 그대로. 틱이 흐르는 동안은 즉시·정확하다.
    - **`flush_due`(시각 구동, 선택)** — 거래소 시각이 경계+유예를 지나면 닫는다.

    둘은 **배타가 아니다.** 먼저 오는 쪽이 닫고, 늦은 쪽은 `_last_flushed_minute` 가드에
    걸려 아무 일도 안 한다. 그래서 시각 구동을 켜도 정상 구간의 동작은 안 바뀌고,
    **틱이 늦는 구간에서만** 봉이 제때 나간다.

    ## 왜 시각 구동이 기본이 아닌가

    시각으로 닫으면 그 분에 속하지만 유예 뒤에 도착한 틱은 **버려진다**. 즉 유실을 고치려고
    다른 유실을 들여올 수 있다. 그 크기를 정하는 것은 회선의 수신 지연 분포인데,
    2026-08-05 시점에 이 프로젝트엔 그걸 잰 데이터가 하나도 없었다 — 틱 아카이브는 거래소
    시각만 남기고 수신 시각을 안 남긴다.

    그래서 같은 날 `ClockSkewTracker.delivery_latency_seconds()`로 **측정부터 붙였다.**
    임계를 실측 없이 정하지 않는다는 것이 이 프로젝트의 반복된 교훈이다.

    2026-08-11에 3거래일치 분포로 `MINUTE_CLOSE_GRACE_SECONDS`를 1.0 → 2.0초로 확정했다
    (근거는 그 상수 위 주석). **승격 자체는 아직이다** — 운영 설정
    (`configs/instance.yaml`의 `minute_bar_close`)은 여전히 `tick`이고, 4일째 표본이
    08-11 15:35에 나온 뒤에 바꾼다. 상수를 먼저 고친 이유는 그것이 **선결 조건**이기
    때문이다: 종전 1.0초는 관측 최대(1.3964초)보다 작아서, 그 상태로 승격하면 유예 뒤
    도착한 틱을 매일 버렸다.

    ## 늦은 틱은 조용히 버리지 않는다

    종전에도 `minute < _current_minute`인 틱은 버렸는데 **로그가 없었다**(L18 위반). 시각
    구동에서는 그 경로가 훨씬 자주 열리므로, 분(分)마다 한 줄씩 남기고 건수를 센다 —
    매 틱 로그하면 하루 수만 줄이 되어 아무도 안 본다(`FeaturePublish`가 그랬다).
    """

    MIN_TICKS_FOR_QUALITY_OK = 3  # mahdi MinuteBarAggregator.MIN_TICKS_FOR_NORMAL_QUALITY와 동일

    # 직전 봉 종가 대비 이 비율을 넘게 시가가 벌어지면 저품질로 표시한다. KOSPI200 미니선물
    # 1분봉이 정상적으로 3%를 움직이는 일은 없다(2026-07-29~31 실측 최대 1분 변동폭 1.4%) —
    # 미검증 초기값이며, 실측이 쌓이면 재조정 대상이다.
    PRICE_JUMP_RATIO = 0.03

    def __init__(self, symbol: str, horizon: Horizon = Horizon.M1) -> None:
        self._symbol = symbol
        self._horizon = horizon
        self._current_minute: datetime | None = None
        self._ticks: list[Tick] = []
        self._prev_close_ticks: int | None = None
        # 이미 닫아서 내보낸 마지막 분 — 그 분으로 오는 틱이 새 버킷을 열지 못하게 막는다
        # (`MultiHorizonBarComposer._last_flushed_start`와 같은 역할·같은 이유).
        self._last_flushed_minute: datetime | None = None
        self._late_tick_drops = 0
        self._last_late_log_minute: datetime | None = None

    @property
    def late_tick_drops(self) -> int:
        """이미 닫힌 분으로 도착해 버린 틱 수 — 그만큼 1분봉 거래량에서 빠졌다."""
        return self._late_tick_drops

    def add_tick(self, tick: Tick) -> BarClosed | None:
        """
        계산: tick의 분(ts_exchange를 초 단위로 절삭)이 누적 중인 분과 다르면 기존 버킷을
             BarClosed로 flush하고 새 버킷을 시작한다. 같은 분이면 누적만 하고 None.
        실패 조건: 이미 닫은 분으로 오는 틱(지연 도착)은 버린다 — 조용히는 안 버린다(L18).
        """
        minute = tick.ts_exchange.replace(second=0, microsecond=0)

        if self._last_flushed_minute is not None and minute <= self._last_flushed_minute:
            self._note_late_tick(minute)
            return None

        if self._current_minute is None:
            self._current_minute = minute

        if minute < self._current_minute:
            self._note_late_tick(minute)
            return None

        if minute > self._current_minute:
            completed = self._build_bar()
            self._last_flushed_minute = self._current_minute
            self._current_minute = minute
            self._ticks = [tick]
            return completed

        self._ticks.append(tick)
        return None

    def flush_due(
        self, exchange_now: datetime, *, grace_seconds: float = MINUTE_CLOSE_GRACE_SECONDS
    ) -> BarClosed | None:
        """거래소 시각이 **경계 + 유예**를 지났으면 누적 중인 분을 닫는다 (고도화 1).

        입력: `exchange_now`는 로컬 시각이 아니라 **거래소 시각**이어야 한다 — 호출측
             (`TickCollector.flush_due_minute`)이 측정된 스큐를 더해서 넘긴다. 로컬 시계로
             판단하면 2026-08-05에 상위 Horizon을 잘라먹은 것과 똑같은 실패를 1분봉에서
             반복한다.
        반환: 닫은 봉, 아직 때가 아니거나 누적 틱이 없으면 None.

        `add_tick`의 롤오버와 **경합하지 않는다**: 먼저 닫는 쪽이 `_last_flushed_minute`를
        올리고, 늦은 쪽은 그 가드에 걸려 아무 일도 안 한다.
        """
        ensure_aware(exchange_now)
        if self._current_minute is None or not self._ticks:
            return None
        deadline = self._current_minute + timedelta(minutes=1, seconds=grace_seconds)
        if exchange_now < deadline:
            return None
        completed = self._build_bar()
        self._last_flushed_minute = self._current_minute
        self._current_minute = None
        self._ticks = []
        return completed

    def flush_final(self) -> BarClosed | None:
        """세션 종료(WS 연결 종료 등) 시 마지막 누적 버킷을 강제로 flush한다."""
        completed = self._build_bar()
        if self._current_minute is not None:
            self._last_flushed_minute = self._current_minute
        self._ticks = []
        return completed

    def _note_late_tick(self, minute: datetime) -> None:
        """건수는 매번 세고, 로그는 **분마다 한 줄**만 남긴다.

        매 틱 남기면 하루 수만 줄이 되어 아무도 안 본다 — `FeaturePublish`가 8거래일 내내
        `nan_ratio=0.0165`를 찍고도 아무도 안 물어본 실패 형태다(2026-08-04). 분당 한 줄이면
        최악이어도 정규장 405줄이고, 각 줄이 곧 조치 대상이다.
        """
        self._late_tick_drops += 1
        if self._last_late_log_minute == minute:
            return
        self._last_late_log_minute = minute
        mlog.log(
            "AggregatorLateTickDropped",
            f"이미 닫은 {minute:%H:%M} 분봉으로 체결틱이 늦게 도착 — 그 분의 거래량에서 빠진다",
            symbol=self._symbol,
            horizon=self._horizon.value,
            bar_open_kst=minute.isoformat(),
        )

    def _build_bar(self) -> BarClosed | None:
        if not self._ticks or self._current_minute is None:
            return None
        prices = [t.price_ticks for t in self._ticks]
        # `open_time`은 naive `time`이라 KST 벽시계로 맞춰 비교한다(aware time과 직접
        # 비교하면 TypeError). `_combine_kst()`가 항상 KST를 붙이지만, 다른 경로로 들어온
        # 틱도 안전하도록 명시적으로 변환한다.
        session = (
            BarSession.PRE_OPEN
            if to_kst(self._current_minute).time() < DEFAULT_SESSION.open_time
            else BarSession.REGULAR
        )
        jumped = self._is_price_jump(prices[0])
        bar = BarClosed(
            symbol=self._symbol,
            horizon=self._horizon,
            bar_open_kst=self._current_minute,
            o_ticks=prices[0],
            h_ticks=max(prices),
            l_ticks=min(prices),
            c_ticks=prices[-1],
            volume=sum(t.qty for t in self._ticks),
            quality_ok=len(self._ticks) >= self.MIN_TICKS_FOR_QUALITY_OK and not jumped,
            session=session,
        )
        self._prev_close_ticks = bar.c_ticks
        return bar

    def _is_price_jump(self, open_ticks: int) -> bool:
        """직전 완성봉 종가 대비 시가가 `PRICE_JUMP_RATIO`를 넘게 벌어졌는가.

        조용히 플래그만 내리지 않고 로그도 남긴다(L18) — `quality_ok=False`는 여러 원인이
        공유하는 값이라(틱 수 부족 등) 그것만으로는 사후에 "왜 저품질인지"를 복원할 수 없다.
        """
        previous = self._prev_close_ticks
        if previous is None or previous <= 0:
            return False
        ratio = abs(open_ticks - previous) / previous
        if ratio <= self.PRICE_JUMP_RATIO:
            return False
        mlog.log(
            "TickPriceJump",
            f"직전 종가 {previous}틱 → 시가 {open_ticks}틱 ({ratio:.1%} 점프) — "
            f"임계 {self.PRICE_JUMP_RATIO:.0%} 초과, quality_ok=False로 표시",
            symbol=self._symbol,
            horizon=self._horizon.value,
            prev_close_ticks=previous,
            open_ticks=open_ticks,
            jump_ratio=ratio,
        )
        return True
