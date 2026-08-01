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

알려진 한계(마흐디 원본과 동일): 데이터건수(헤더 3번째 필드)가 1보다 크면(여러 체결이 한 WS
프레임에 묶여 옴) 첫 레코드만 파싱하고 이후 레코드는 조용히 버린다 — mahdi의 _parse_tick/
_parse_futures_tick도 프레임을 레코드 단위로 분리하지 않는 동일한 한계를 그대로 갖고 있었다
(2026-07-22 라이브 캡처에서 count=002 프레임 실제 관측, 테스트로 이 동작을 못박아둠).
프레임을 레코드 단위로 온전히 분리하는 건 별도 개선 대상.
"""

from __future__ import annotations

from datetime import date, datetime
from datetime import time as dtime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from messiah.core import logging as mlog
from messiah.core.event_calendar import DEFAULT_SESSION
from messiah.core.messages import BarClosed, BarSession, Horizon, Tick
from messiah.core.timeutil import KST, now_kst, to_kst

# H0IFCNT0(지수선물 실시간체결가) 필드 인덱스 — "^" 구분, 0-based. 실제 메시지는 50개 필드를
# 갖지만(2026-07-22 라이브 캡처로 확인 — 매도/매수호가 등 idx34~37까지 포함) 이 파서는 symbol/
# 시각/가격/거래량만 읽으므로(messiah Tick에 bid/ask 필드 자체가 없음), 필요한 만큼만
# (idx9까지) 최소 길이로 검증한다.
_FUT_IDX_SYMBOL = 0
_FUT_IDX_BSOP_HOUR = 1
_FUT_IDX_PRICE = 5
_FUT_IDX_QTY = 9
_FUT_MIN_FIELDS = _FUT_IDX_QTY + 1

# H0IOCNT0(지수옵션 실시간체결가) 필드 인덱스 — 선물과 필드 순서가 다르다(가격이 idx2).
_OPT_IDX_SYMBOL = 0
_OPT_IDX_BSOP_HOUR = 1
_OPT_IDX_PRICE = 2
_OPT_IDX_QTY = 9
_OPT_MIN_FIELDS = _OPT_IDX_QTY + 1


def _strip_ws_envelope(raw: str) -> list[str]:
    """ "암호화유무|TR_ID|데이터건수|실제데이터" 헤더를 제거하고 "^" 구분 필드로 나눈다.

    헤더가 없는 입력(순수 "^" 데이터만)도 raw.split("|", 3)[-1]이 원본을 그대로 통과시키므로
    안전하다(mahdi 2026-07-06 실측 근거 — main.py:372-375).
    """
    body = raw.split("|", 3)[-1]
    return body.split("^")


def _price_to_ticks(raw_price: str, tick_size: Decimal) -> int:
    return int((Decimal(raw_price) / tick_size).to_integral_value(rounding=ROUND_HALF_UP))


def _combine_kst(hhmmss: str, today: date) -> datetime:
    tick_time = dtime(int(hhmmss[0:2]), int(hhmmss[2:4]), int(hhmmss[4:6]))
    return datetime.combine(today, tick_time, tzinfo=KST)


def parse_futures_tick(
    raw: str, tick_size: Decimal, *, source: str = "kis", today: date | None = None
) -> Tick | None:
    """
    입력: WS로 수신한 H0IFCNT0 원시 문자열 1건, 종목 1틱의 실제 가격 크기(KISBrokerAdapter와
         동일한 하드코딩 금지 원칙 — 호출측이 실측값을 주입).
    계산: 헤더 제거 → "^" 분리 → 영업시간(HHMMSS)을 today(기본 오늘 KST 날짜)와 결합해
         tz-aware ts_exchange 생성 → 가격을 tick_size로 나눠 정수 price_ticks로 변환
         (SYSTEM.md R2, float 금지) → Tick 반환.
    해석: side_hint는 항상 0 — messiah Tick이 bid/ask를 안 들고 있어 틱룰 계산이 불가능.
    실패 조건: 필드 수 부족·숫자 변환 실패 시 None(해당 틱 무시 — mahdi와 동일 계약, 실시간
              틱 1건 유실은 로깅 없이 조용히 넘어간다).
    """
    fields = _strip_ws_envelope(raw)
    if len(fields) < _FUT_MIN_FIELDS:
        return None
    try:
        ts_exchange = _combine_kst(fields[_FUT_IDX_BSOP_HOUR], today or now_kst().date())
        return Tick(
            symbol=fields[_FUT_IDX_SYMBOL],
            ts_exchange=ts_exchange,
            price_ticks=_price_to_ticks(fields[_FUT_IDX_PRICE], tick_size),
            qty=int(Decimal(fields[_FUT_IDX_QTY])),
            side_hint=0,
            source=source,
        )
    except (ValueError, IndexError, InvalidOperation, ArithmeticError):
        return None


def parse_option_tick(
    raw: str, tick_size: Decimal, *, source: str = "kis", today: date | None = None
) -> Tick | None:
    """H0IOCNT0(지수옵션 실시간체결가) 버전 — 필드 배치만 다르고 나머지는 parse_futures_tick과
    동일 계약. 옵션 인덱스는 마흐디 인용만 반영, messiah 자체 라이브 재검증은 안 됨(모듈
    docstring 참고)."""
    fields = _strip_ws_envelope(raw)
    if len(fields) < _OPT_MIN_FIELDS:
        return None
    try:
        ts_exchange = _combine_kst(fields[_OPT_IDX_BSOP_HOUR], today or now_kst().date())
        return Tick(
            symbol=fields[_OPT_IDX_SYMBOL],
            ts_exchange=ts_exchange,
            price_ticks=_price_to_ticks(fields[_OPT_IDX_PRICE], tick_size),
            qty=int(Decimal(fields[_OPT_IDX_QTY])),
            side_hint=0,
            source=source,
        )
    except (ValueError, IndexError, InvalidOperation, ArithmeticError):
        return None


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

    def add_tick(self, tick: Tick) -> BarClosed | None:
        """
        계산: tick의 분(ts_exchange를 초 단위로 절삭)이 누적 중인 분과 다르면 기존 버킷을
             BarClosed로 flush하고 새 버킷을 시작한다. 같은 분이면 누적만 하고 None.
        실패 조건: tick이 현재 버킷보다 과거 분(지연 도착)이면 무시하고 None.
        """
        minute = tick.ts_exchange.replace(second=0, microsecond=0)

        if self._current_minute is None:
            self._current_minute = minute

        if minute < self._current_minute:
            return None

        if minute > self._current_minute:
            completed = self._build_bar()
            self._current_minute = minute
            self._ticks = [tick]
            return completed

        self._ticks.append(tick)
        return None

    def flush_final(self) -> BarClosed | None:
        """세션 종료(WS 연결 종료 등) 시 마지막 누적 버킷을 강제로 flush한다."""
        completed = self._build_bar()
        self._ticks = []
        return completed

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
