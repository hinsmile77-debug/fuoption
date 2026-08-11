"""EV(이벤트·시간·만기) Feature — Ver 1.4 §2.7 (2026-08-04 신설, F1).

## 왜 이게 먼저인가

지금 모델이 보는 121개는 전부 OHLCV 파생이다. 그래서 **"장 마감 10분 전"과 "개장 직후"를
구분할 수단이 하나도 없다** — 같은 가격 패턴이면 같은 판단을 낸다. 유동성·변동성·강제청산
압력이 전혀 다른 두 순간인데도 그렇다.

그리고 EV는 **이력 전체에 소급 계산이 가능한 유일한 카테고리**다. 전부 시각·달력 함수라
과거 봉의 타임스탬프만 있으면 값이 나온다 — MS/OP처럼 "오늘부터 모아서 3개월 뒤"가 아니라
지금 있는 163거래일에 그대로 붙는다. 비용 대비 효과가 가장 큰 자리라 F1이 됐다.

## 언제의 시각인가 — 봉 시작이 아니라 **확정 시각**

`bars[-1].bar_open_kst`가 아니라 `bar_confirm_time(bars[-1])`(= 봉 시작 + Horizon 길이)을
쓴다. 이 벡터가 실제로 존재하게 되는 순간이 봉 **종료** 시점이고(완성봉 규율, Ver 1.2 §2.2),
판단도 그때 내려진다. 30분봉이면 둘이 30분 차이라 `ev_close_remain`·`ev_lunch_flag` 같은
경계 피처에서 값이 실제로 갈린다.

## 캘린더는 사이드카지만 FL과 성질이 다르다

`FlowHistory`(FL)는 **관측 데이터**라 "요청일보다 엄격히 이전만" 계약이 필수다 — 그날 수급을
그날 아침에 알 수 없기 때문이다. `EventCalendar`는 **참조 데이터**다. 내일이 휴장일이라는 걸
오늘 아는 것은 미래 참조가 아니라 몇 달 전부터 공표된 사실이다. 그래서 이 카테고리의
계산기는 미래 날짜를 자유롭게 조회한다(`features/sidecar.py`의 두 종류 구분 참고).

## 스코프에서 제외한 3개 — NaN으로 채우지 않는다

Ver 1.4 §2.7의 14행 중 `ev_econ_prox`·`ev_econ_grade`·`ev_overnight_gap_risk`는 **경제지표
발표 일정 피드**라는 별개의 외부 소스가 필요하다(FOMC·CPI·고용지표). 그 소스가 이 프로젝트에
없다.

자리만 만들고 NaN을 채우는 선택지가 있지만 안 한다 — `px_ema_cross_60`이 정확히 그렇게
**죽은 채로 학습되다** 몇 달 뒤에 발견됐다(2026-08-04). 없는 것은 없는 것으로 둔다.

## 알려진 중복 2쌍 — 일부러 남겼다

- `ev_open_elapsed`와 `ev_close_remain`은 세션 길이가 고정이라 **정확히 상보적**이다
  (합이 1). 즉 순위상관 |ρ| = 1.
- `ev_dte_fut`와 `ev_dte_opt_m`은 KRX 규칙상 **같은 날 만기**(둘 다 둘째 목요일)라 항상
  같은 값이다.

둘 다 Ver 1.4가 별개 항목으로 적은 것이고, 여기서 내 판단으로 하나를 지우는 대신 **관문
(`features/gate.py` ② 중복 검정)이 측정해서 떨어뜨리게** 둔다. 그게 이번 주에 관문을 먼저
만든 이유이기도 하다 — "내가 보기에 중복"과 "측정된 중복"은 다른 근거이고, KRX가 규칙을
바꾸면(미니선물 만기 주기 변경 등) 전자만 조용히 틀린다.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time
from typing import Sequence

from messiah.core.event_calendar import DEFAULT_SESSION, EventCalendar
from messiah.core.messages import BarClosed, bar_confirm_time
from messiah.core.timeutil import to_kst

Bars = Sequence[BarClosed]

# 하루를 한 주기로 하는 시각 인코딩. **세션 길이가 아니라 24시간**을 주기로 쓴다 — 세션을
# 한 바퀴로 잡으면 개장(위상 0)과 마감(위상 2π)이 같은 점으로 겹쳐 둘을 구분할 수 없게 된다.
_SECONDS_PER_DAY = 24 * 60 * 60

# 점심 유동성 저하 구간 — **실측값이다**(2026-08-04, 연속물 63,508봉 = 163거래일).
# 정규장 분당 평균 거래량 345계약 대비 10분 버킷 비율:
#
#     11:00 1.01x · 11:20 0.85x · 11:50 0.71x · 12:20 0.65x(최저) · 13:30 0.81x
#     13:50 0.78x · 14:00 0.95x · 14:30 1.00x
#
# 11:30에 0.84x로 내려가 14:00에 0.95x로 복귀한다. 창을 이 구간으로 잡는다 — Ver 1.4는
# "유동성 저하 시간대"라고만 적었고 시각을 못박지 않아 구현이 정하는데, 추측 대신 우리
# 아카이브로 쟀다. 15:30 이후에도 0.48x로 낮지만 그건 종가단일가 전환이라 성격이 다르다.
_LUNCH_START = time(11, 30)
_LUNCH_END = time(14, 0)

# 롤오버 활성 구간 — **미실측 초기값**. 실측을 시도했으나 불가능했다(2026-08-04): 백필이
# 날짜마다 **근월물만** 저장해서 두 월물이 겹치는 날이 만기일 하루뿐이다. 그 하루의 실측은
# 차월물 거래량 비중 36~64%(7개 월물)로, 만기일에는 이미 이전이 끝나 있다는 것만 알려준다.
# 언제 시작되는지는 안 보인다.
#
# 5거래일(1주)로 둔다. **측정 가능한 갭이다** — KIS 분봉 API는 만기물도 조회되므로
# (`data/backfill.py`), 각 만기 직전 10거래일치 차월물을 추가 백필하면 이전 곡선을 그릴 수
# 있다. NEXT_TODO에 절차를 적어 뒀다.
ROLLOVER_TRADING_DAYS = 5

_WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri")


def _now(bars: Bars) -> datetime | None:
    """이 벡터가 존재하게 되는 시각 — 봉 **확정** 시각(모듈 docstring)."""
    if not bars:
        return None
    return to_kst(bar_confirm_time(bars[-1]))


def _session_minutes() -> float:
    open_dt = datetime.combine(date(2000, 1, 1), DEFAULT_SESSION.open_time)
    close_dt = datetime.combine(date(2000, 1, 1), DEFAULT_SESSION.close_time)
    return (close_dt - open_dt).total_seconds() / 60.0


def _minutes_since_open(now: datetime) -> float:
    open_dt = now.replace(
        hour=DEFAULT_SESSION.open_time.hour,
        minute=DEFAULT_SESSION.open_time.minute,
        second=0,
        microsecond=0,
    )
    return (now - open_dt).total_seconds() / 60.0


# ---------------------------------------------------------------- 시각 (calendar 불필요)


def ev_tod_sin(bars: Bars, calendar: EventCalendar) -> float | None:
    """장중 시각의 사인 — 자정 기준 24시간 주기(모듈 상단 `_SECONDS_PER_DAY` 주석)."""
    now = _now(bars)
    if now is None:
        return None
    seconds = now.hour * 3600 + now.minute * 60 + now.second
    return math.sin(2 * math.pi * seconds / _SECONDS_PER_DAY)


def ev_tod_cos(bars: Bars, calendar: EventCalendar) -> float | None:
    now = _now(bars)
    if now is None:
        return None
    seconds = now.hour * 3600 + now.minute * 60 + now.second
    return math.cos(2 * math.pi * seconds / _SECONDS_PER_DAY)


def ev_open_elapsed(bars: Bars, calendar: EventCalendar) -> float | None:
    """개장 후 경과 — 세션 길이로 정규화. 장전(08:45~09:00) 봉은 **음수**가 나온다.

    0으로 자르지 않는다: 장전은 실제로 다른 국면이고(2026-07-31에 08:45~09:04 20봉이 전부
    동일가로 고정됐다가 09:05에 6.1% 튄 실측이 있다) 그 사실을 모델에서 지우면 `BarSession.
    PRE_OPEN`을 만든 이유가 없어진다.
    """
    now = _now(bars)
    if now is None:
        return None
    return _minutes_since_open(now) / _session_minutes()


def ev_close_remain(bars: Bars, calendar: EventCalendar) -> float | None:
    """마감까지 잔여 — 세션 길이로 정규화(강제청산 인지).

    현재 구현에서는 `1 - ev_open_elapsed`와 **정확히 같다**(세션 길이가 상수라서). 그래도
    남기는 이유는 모듈 docstring "알려진 중복" 참고 — 관문이 측정해서 떨어뜨리게 둔다.
    """
    now = _now(bars)
    if now is None:
        return None
    return 1.0 - _minutes_since_open(now) / _session_minutes()


def ev_lunch_flag(bars: Bars, calendar: EventCalendar) -> float | None:
    """점심 유동성 저하 구간(11:30~14:00, 실측 — 모듈 상단 `_LUNCH_START` 주석)."""
    now = _now(bars)
    if now is None:
        return None
    return 1.0 if _LUNCH_START <= now.time() < _LUNCH_END else 0.0


def _dow_feature(index: int):
    def _fn(bars: Bars, calendar: EventCalendar) -> float | None:
        now = _now(bars)
        if now is None:
            return None
        return 1.0 if now.weekday() == index else 0.0

    _fn.__name__ = f"ev_dow_{_WEEKDAY_NAMES[index]}"
    _fn.__doc__ = f"요일 one-hot — {_WEEKDAY_NAMES[index]}인가 (Ver 1.4 §2.7 `ev_dow`)."
    return _fn


# Ver 1.4는 `ev_dow`를 "one-hot"으로 적었다. 서수 인코딩(0~4)이 열 하나로 끝나 싸지만, 트리는
# 임계값으로만 가르므로 {월, 금} 같은 비연속 집합을 분리하려면 서수로는 3번 이상 갈라야 한다
# — 원문대로 one-hot으로 간다. 5개 중 몇 개가 실제로 쓸모 있는지는 관문이 판정한다.
_DOW_FEATURES = [(f"ev_dow_{name}", _dow_feature(i)) for i, name in enumerate(_WEEKDAY_NAMES)]


# ---------------------------------------------------------------- 만기 (calendar 필요)


def _confirm_day(bars: Bars) -> date | None:
    now = _now(bars)
    return now.date() if now is not None else None


def ev_dte_fut(bars: Bars, calendar: EventCalendar) -> float | None:
    """미니선물 만기까지 잔여 **거래일**. 만기 당일은 0.

    휴장일 데이터가 없는 연도로 넘어가면(`configs/krx_holidays.yaml` 미갱신) `is_trading_day`
    가 예외를 던지고 엔진의 `_safe_call`이 None으로 마킹한다 — 조용히 틀린 D-day를 내는
    것보다 낫다. 연말에 EV 계열이 통째로 NaN이 되면 그건 달력을 갱신하라는 신호다.
    """
    day = _confirm_day(bars)
    if day is None:
        return None
    return float(calendar.trading_days_until(day, calendar.next_monthly_expiry(day)))


def ev_dte_opt_m(bars: Bars, calendar: EventCalendar) -> float | None:
    """먼스리 옵션 만기까지 잔여 거래일.

    KRX 규칙상 미니선물과 **같은 날**(둘째 목요일) 만기라 현재는 `ev_dte_fut`와 항상 같은
    값이다(모듈 docstring "알려진 중복"). 그래도 별도 계산기로 두는 이유는, 상품별 만기
    주기는 거래소가 바꿀 수 있는 규칙이고 그때 "같을 것"이라는 가정이 조용히 틀리기 때문이다.
    """
    day = _confirm_day(bars)
    if day is None:
        return None
    return float(calendar.trading_days_until(day, calendar.next_monthly_expiry(day)))


def ev_dte_opt_w(bars: Bars, calendar: EventCalendar) -> float | None:
    """위클리 옵션 만기까지 잔여 거래일 — 월위클리·목위클리 중 **먼저 오는 쪽**.

    확정 유니버스가 두 위클리를 다 거래하므로(`core/universe.py`) "다음 위클리 만기"는 둘 중
    가까운 쪽이 맞다. 근사 한계는 `EventCalendar.next_weekly_expiry()` docstring 참고.
    """
    day = _confirm_day(bars)
    if day is None:
        return None
    expiry = calendar.next_weekly_expiry(day)
    if expiry is None:
        return None
    return float(calendar.trading_days_until(day, expiry))


def ev_expiry_flag(bars: Bars, calendar: EventCalendar) -> float | None:
    """당일 만기 여부 — 0 없음 / 1 위클리 / 2 먼스리 / **3 동시만기**.

    Ver 1.4의 "당일 만기 여부(동시만기 별도 가중)"를 등급 스칼라로 인코딩했다. 이진 플래그로
    두면 동시만기(3·6·9·12월, 선물·옵션이 한날 만기라 수급이 특히 크게 튄다)를 평범한 위클리
    만기와 같은 값으로 뭉개게 된다. 순서가 "만기 압력의 크기"와 같은 방향이라 트리가 임계값
    하나로 "먼스리 이상"을 가를 수 있다.
    """
    day = _confirm_day(bars)
    if day is None:
        return None
    if not calendar.is_trading_day(day):
        return 0.0
    if calendar.is_quadruple_witching(day):
        return 3.0
    if calendar.is_monthly_expiry(day):
        return 2.0
    return 1.0 if calendar.is_expiry_day(day) else 0.0


def ev_rollover_win(bars: Bars, calendar: EventCalendar) -> float | None:
    """롤오버 활성 구간인가 — 선물 만기 `ROLLOVER_TRADING_DAYS`거래일 전부터 만기일까지.

    창 크기의 근거(와 그것이 왜 아직 미실측인지)는 모듈 상단 `ROLLOVER_TRADING_DAYS` 주석.
    """
    dte = ev_dte_fut(bars, calendar)
    if dte is None:
        return None
    return 1.0 if dte <= ROLLOVER_TRADING_DAYS else 0.0


def ev_holiday_adj(bars: Bars, calendar: EventCalendar) -> float | None:
    """연휴 인접 — **부호가 방향, 크기가 길이**. 연휴 직전 +, 직후 −, 평상시 0.

    값은 `캘린더 간격 − 1`이다: 평일 연속이면 0, 금요일(다음 거래일이 월요일)이면 +2,
    월요일이면 −2, 추석 직전이면 +5 같은 식.

    부호를 나눈 이유는 두 상황의 성격이 다르기 때문이다 — 연휴 **직전**은 포지션을 들고 갈지
    정하는 시점(오버나이트 리스크가 며칠치로 커진다)이고, **직후**는 그 갭이 이미 실현된
    시점이다. 하나의 플래그로 뭉치면 정반대 국면이 같은 값을 받는다.

    양쪽 다 해당하면(연휴 사이에 낀 하루) **직전을 우선**한다 — 앞으로 질 리스크가 이미
    지나간 것보다 판단에 더 관계있다는 판단이며, 자주 나오는 경우는 아니다.
    """
    day = _confirm_day(bars)
    if day is None:
        return None
    if not calendar.is_trading_day(day):
        return 0.0
    ahead = calendar.calendar_gap_to_next_trading_day(day) - 1
    behind = calendar.calendar_gap_from_previous_trading_day(day) - 1
    if ahead > 0:
        return float(ahead)
    return float(-behind)


# `px_core.STATEFUL_FEATURES`와 같은 모양 — 이름과 계산기의 쌍. 엔진은 이 목록만 보고 전부
# 계산한다(`features/spec.py`의 EV 카테고리가 이 속성을 참조).
CALENDAR_FEATURES: list[tuple[str, "callable[[Bars, EventCalendar], float | None]"]] = [
    ("ev_tod_sin", ev_tod_sin),
    ("ev_tod_cos", ev_tod_cos),
    ("ev_open_elapsed", ev_open_elapsed),
    ("ev_close_remain", ev_close_remain),
    *_DOW_FEATURES,
    ("ev_dte_fut", ev_dte_fut),
    ("ev_dte_opt_m", ev_dte_opt_m),
    ("ev_dte_opt_w", ev_dte_opt_w),
    ("ev_expiry_flag", ev_expiry_flag),
    ("ev_rollover_win", ev_rollover_win),
    ("ev_holiday_adj", ev_holiday_adj),
    ("ev_lunch_flag", ev_lunch_flag),
]

# 세션 내내 안 변해도 **결함이 아닌** 피처 (2026-08-11).
#
# `px_core.INTRADAY_CONSTANT_OK`와 같은 뜻·같은 규약이고, 여기 두는 이유는 **정의 옆에
# 선언한다**는 것뿐이다. 2026-08-10에 운영 피처셋을 `v2026.08-ev`로 올렸을 때 이 선언이
# 없어서, 다음 날 리포트가 4개 Horizon 전부에 "피처 11개가 세션 내내 죽어 있었다"를 찍었다
# — 등록부 `no-degenerate-features`(임계 0)가 **구조적으로 통과 불가**가 된 것이다.
# 목록이 px_core 한 곳에만 있으면 새 카테고리를 붙일 때마다 같은 일이 반복된다.
#
# 아래 11개는 전부 **`_confirm_day()` 또는 `now.weekday()`만 보는** 계산이다 — 하루 안에서
# 변할 수 있는 입력이 하나도 없다. 상수인 것이 사고가 아니라 **정의**다.
# (`ev_tod_sin`/`ev_tod_cos`/`ev_open_elapsed`/`ev_close_remain`/`ev_lunch_flag`는 시각을
# 보므로 여기 없다 — 실제로 2026-08-11 리포트도 그 다섯은 안 걸었다. 검출기는 잘 돌고 있다.)
#
# ## 검출력을 잃지 않는다
#
# ① **항상 NaN이면 여전히 잡힌다** — 캘린더 사이드카가 아예 안 붙은 날의 진짜 사고다.
# ② **요일 원-핫의 날짜 간 동결**은 리포트가 따로 잡는다(`ops/integrity_report.py`의
#    `_calendar_freeze_finding`). `ev_dow_*`는 매 거래일 반드시 달라져야 하므로 —
#    어제와 오늘의 요일이 같을 수 없다 — 전일과 동일하면 그게 곧 사이드카 동결의 증거다.
#    화이트리스트가 검출을 끄는 것이 아니라 **하루 단위 축에서 날짜 단위 축으로 옮긴다.**
INTRADAY_CONSTANT_OK: frozenset[str] = frozenset(
    {
        *(f"ev_dow_{name}" for name in _WEEKDAY_NAMES),
        "ev_dte_fut",
        "ev_dte_opt_m",
        "ev_dte_opt_w",
        "ev_expiry_flag",
        "ev_rollover_win",
        "ev_holiday_adj",
    }
)

# 위 목록 중 **매 거래일 반드시 달라지는** 부분집합 — 동결 검사의 대상(위 ② 참고).
# `ev_dte_*`나 `ev_expiry_flag`는 이틀 연속 같은 값이 정상일 수 있어(만기가 멀면 dte는
# 하루에 1씩만 줄고, 만기가 아닌 날은 flag가 계속 0) 여기 넣지 않는다. 요일 원-핫만이
# **오탐 0으로** 동결을 말할 수 있다.
DAILY_VARYING_FEATURES: tuple[str, ...] = tuple(f"ev_dow_{name}" for name in _WEEKDAY_NAMES)

# Ver 1.4 §2.7의 14행 중 여기 없는 3개 — 경제지표 캘린더 피드가 없어서다(모듈 docstring).
# 상수로 남겨 두면 "빠뜨린 것"과 "일부러 뺀 것"이 구분되고, 소스가 생겼을 때 무엇을 채워야
# 하는지가 코드에 남는다.
EXCLUDED_FEATURES: tuple[str, ...] = (
    "ev_econ_prox",  # 다음 주요 지표까지 시간(등급 가중)
    "ev_econ_grade",  # 이벤트 등급 — FOMC=3, CPI=3, 고용=2 …
    "ev_overnight_gap_risk",  # 오늘 밤 주요 이벤트 유무
)
