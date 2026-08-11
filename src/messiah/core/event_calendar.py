"""Event Calendar — KRX 휴장일 인식 + 세션(정규장/오버나이트) 판정 (Ver 2.0 §9 L1 DATA
다이어그램의 "Event Calendar" 구성요소, 2026-07-27 신설).

여러 기존 갭이 전부 "지금이 장중인지 모른다"는 한 가지 문제로 수렴했다:
  - `scripts/run_l1_daily.py`가 KRX 휴장일에 실행되면 self_check는 통과하지만 하루 종일
    틱이 안 온다(capability_matrix.md 기존 갭).
  - `risk/risk_engine.py`의 R4(오버나이트 증거금 25%)·R6(오버나이트 자격→장마감 강제청산)이
    "세션 인식 컴포넌트가 없다"는 이유로 통째로 미구현이었다(risk_engine.py 모듈 docstring).
  - `strategy/regime/rules.py`의 규칙 1·2(경제이벤트·만기일)가 "Event Calendar Service가
    없다"는 이유로 항상 `None` 입력만 받아 죽어있었다.
이 모듈이 그 공통 원인의 "KRX 개장일" 부분을 해소한다.

**스코프 경계(중요)**: 여기서 다루는 건 "KRX가 문을 여는가"와 "지금이 장중인가"뿐이다.
Ver 1.4 §2.7 EV Feature 14개 중 `ev_econ_prox`/`ev_econ_grade`(FOMC·CPI 등 경제지표
캘린더)는 완전히 별개의 외부 데이터 소스(경제지표 발표 일정 피드)가 필요해 스코프 밖이다
— `strategy/regime/rules.py`의 `rule_economic_event`는 이 모듈이 생긴 뒤에도 여전히
미발동 상태로 남는다(별도 착수 필요, capability_matrix.md 참고). `is_expiry_day()`(옵션
만기일)는 KRX 개장일 여부와는 독립적인 계산(요일 규칙 기반 근사)이라 정확도 한계가
다르다 — 아래 docstring 참고.

휴장일 데이터는 `configs/krx_holidays.yaml`에서 로드한다 — 출처·정확도 한계는 그 파일
헤더 주석 참고("작성됨≠KRX 공식 확인됨").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import yaml

from messiah.core.timeutil import ensure_aware, to_kst

DEFAULT_HOLIDAYS_PATH = Path("configs") / "krx_holidays.yaml"


def load_holidays(path: Path | str = DEFAULT_HOLIDAYS_PATH) -> frozenset[date]:
    """`{연도: [ISO 날짜, ...]}` 형태의 YAML을 date 집합으로 평탄화."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"KRX 휴장일 설정 없음: {p} (configs/krx_holidays.yaml 참고, 형식은 그 파일 헤더 주석)"
        )
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return frozenset(date.fromisoformat(iso) for year_dates in raw.values() for iso in year_dates)


@dataclass(frozen=True)
class SessionHours:
    """정규장 개장/마감 시각(KST). 기본값은 `scripts/run_l1_daily.py`의
    `REGULAR_SESSION_STOP`(15:35 — 연속거래 종료, 그 뒤 15:45까지 종가단일가)과 동일 —
    두 곳이 따로 값을 들고 있다가 어긋나는 걸 막기 위해 run_l1_daily.py가 이 상수를
    가져다 쓰도록 리팩터했다(단일 소스, 아래 `DEFAULT_SESSION` 참고)."""

    open_time: time = time(9, 0)
    close_time: time = time(15, 35)
    # **틱이 실제로 들어오기 시작하는 시각** — 정규장 개시(09:00)와 다르다 (2026-08-11 F-3).
    #
    # `open_time`은 "정규장이 언제 시작하나"고, 이 값은 "언제부터 봉이 있어야 하나"다. 둘을
    # 같은 값으로 두면 08:45~09:00에 봉이 하나도 없는 것을 정상으로 오판한다 — 실측은 그
    # 15분에 봉이 **있다**: 2026-07-28·29·30 3거래일 연속 08:45:00 정각부터 틱이 들어왔고
    # (`scripts/run_l1_daily.py` docstring "장전 08:45~09:00 구간"), 2026-08-11도 첫 틱이
    # 08:44:58이었다. 그 구간 봉의 성격(예상체결/실체결)은 여전히 미해결이고 `BarSession.
    # PRE_OPEN`으로 표시해 보존 중이지만, **"봉이 있어야 하는가"의 답은 이미 확정**이다.
    #
    # 화면이 이 값을 쓴다: 이 시각 이전에 오늘 봉이 없는 것은 정상이고, 이후인데 없으면
    # 적재가 멈춘 것이다(`ui/app._live_date_notice()`).
    first_tick_time: time = time(8, 45)


DEFAULT_SESSION = SessionHours()

# Ver 1.2 §4.2 구현 순서 대응 위클리 요일 매핑(N/O=월요일, L/M=목요일) — 2026-07-22
# symbol_master 실측으로 재검증됨(dev_memory/NEXT_TODO.md 참고). 여기서는 "오늘이 그
# 요일인가"만 계산 — 실제 그 요일에 상장된 위클리 종목이 있는지는 symbol_master의 몫이다.
_WEEKLY_MONDAY_WEEKDAY = 0
_WEEKLY_THURSDAY_WEEKDAY = 3
_MONTHLY_EXPIRY_WEEKDAY = 3  # 정규월물(선물/먼스리옵션) 만기 = 해당월 두 번째 목요일(표준 관례)

# 동시만기(쿼드러플 위칭) 달 — 3·6·9·12월 정규월물 만기일.
_QUADRUPLE_WITCHING_MONTHS = frozenset({3, 6, 9, 12})


def monthly_expiry(year: int, month: int, calendar: "EventCalendar | None" = None) -> date:
    """그 달 정규월물 만기일 — 둘째 주 목요일, 휴장이면 직전 거래일.

    calendar를 주면 휴장일 보정을 하고, 생략하면 순수 요일 규칙만 쓴다(달력 없이도 계산이
    성립해야 테스트가 실제 휴장일 파일에 의존하지 않는다).

    **2026-08-04에 `data/backfill.py`에서 여기로 옮겼다.** 그 전에는 만기 규칙이 두 벌
    있었다 — backfill의 이 함수(휴장일 보정 있음, 2026-08-04에 A05601~A05607 7개 월물의
    실제 마지막 거래일과 전부 일치 확인)와 아래 `is_expiry_day()`의 인라인 판정
    (`8 <= d.day <= 14`, 휴장일 보정 없음). 같은 재료를 두 곳에서 다르게 손질하면 주방이
    오염된다(Ver 1.4 §0). EV Feature가 세 번째 사본을 만들기 전에 정본을 하나로 합쳤다.
    `backfill.monthly_expiry`는 이 함수를 재수출한다(기존 임포트 경로 유지).
    """
    first = date(year, month, 1)
    # 1일이 목요일이면 그날이 첫째 주 목요일 → 둘째는 +7일.
    offset = (_MONTHLY_EXPIRY_WEEKDAY - first.weekday()) % 7
    expiry = first + timedelta(days=offset + 7)
    if calendar is None:
        return expiry
    while not calendar.is_trading_day(expiry):
        expiry -= timedelta(days=1)
    return expiry


def weekly_expiry(
    year: int, month: int, week: int, weekday: int, calendar: "EventCalendar | None" = None
) -> date:
    """위클리 만기일 — 그 달의 `week`번째 `weekday`요일, 휴장이면 직전 거래일 (2026-08-07 P2).

    입력: `week`는 1부터. `weekday`는 `date.weekday()` 규약(월=0 … 목=3).
    계산: `monthly_expiry()`와 **같은 관례**로 휴장을 보정한다 — 위클리에 다른 관례를 새로
         만드는 것보다 KRX 실측으로 검증된 쪽(2026-08-04, A05601~A05607 7개 월물)에 맞추는
         편이 근거가 있다(`next_weekly_expiry()` docstring의 같은 판단).

    옵션체인 아카이브의 종목명(`2608W1` 등)을 실제 날짜로 되돌리는 데 쓴다. 그 전에는
    아카이브에 만기가 **한글종목명 원문**으로만 있어서, "이 행이 캘린더 예측과 같은
    만기인가"를 사후에 물을 수 없었다 — `OptionChainCalendarViolation`을 장후에 재확인할
    방법이 없다는 뜻이다.
    """
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    expiry = first + timedelta(days=offset + 7 * (week - 1))
    if expiry.month != month:
        raise ValueError(f"{year}-{month:02d}에 {week}번째 요일{weekday}이 없다")
    if calendar is None:
        return expiry
    while not calendar.is_trading_day(expiry):
        expiry -= timedelta(days=1)
    return expiry


class EventCalendar:
    """휴장일 인식 + 세션 판정. 순수 계산(주입된 휴장일 집합 + 세션 시각) — 네트워크
    호출도 전역 상태도 없다(`risk/cost_model.py`·`risk/risk_engine.py`와 동일한 "주입된
    상태 + 순수 계산" 설계 원칙)."""

    def __init__(
        self,
        holidays: frozenset[date],
        session: SessionHours = DEFAULT_SESSION,
        *,
        years: frozenset[int] | None = None,
    ) -> None:
        """`years`를 안 주면 `holidays`에 등장하는 연도만 "데이터 있음"으로 취급한다 —
        실제 휴장일이 0건인 연도(이론상 가능)를 "데이터 없음"과 구분 못 하는 함정을
        피하려면 그런 연도는 `years`로 명시할 것(테스트에서 실제로 이 경로를 씀,
        `tests/test_event_calendar.py` 참고)."""
        self._holidays = holidays
        self._session = session
        self._years = years if years is not None else frozenset(d.year for d in holidays)

    @classmethod
    def from_file(
        cls, path: Path | str = DEFAULT_HOLIDAYS_PATH, session: SessionHours = DEFAULT_SESSION
    ) -> EventCalendar:
        return cls(load_holidays(path), session)

    def is_trading_day(self, d: date) -> bool:
        """주말이거나 등록된 휴장일이면 False. 등록된 연도 밖의 날짜를 물으면(데이터
        갱신을 잊었다는 신호) 침묵하지 않고 예외를 던진다(SYSTEM.md L3 침묵 실패 금지)."""
        if d.year not in self._years:
            raise ValueError(
                f"{d.year}년 KRX 휴장일 데이터 없음 — configs/krx_holidays.yaml 갱신 필요"
            )
        return d.weekday() < 5 and d not in self._holidays

    def next_trading_day(self, d: date) -> date:
        cur = d + timedelta(days=1)
        while not self.is_trading_day(cur):
            cur += timedelta(days=1)
        return cur

    def previous_trading_day(self, d: date) -> date:
        cur = d - timedelta(days=1)
        while not self.is_trading_day(cur):
            cur -= timedelta(days=1)
        return cur

    def is_regular_session(self, dt: datetime) -> bool:
        """dt(aware, 임의 tz — 내부에서 KST로 변환)가 정규장(거래일 + 개장~마감) 안인지.
        `close_time` 자체는 미포함(그 시각에 정확히 종가단일가로 전환 — Ver 1.2 §2.2
        완성봉 규율과 동일하게 반개구간)."""
        kst = to_kst(ensure_aware(dt))
        in_hours = self._session.open_time <= kst.time() < self._session.close_time
        return self.is_trading_day(kst.date()) and in_hours

    def minutes_to_close(self, dt: datetime) -> float | None:
        """정규장 중이 아니면 None(R4/R6가 "정규장 중에만 의미 있는 질문"이라는 걸
        강제하는 계약). 정규장 중이면 마감까지 남은 분(음수 없음, 반개구간이라 0 미포함)."""
        kst = to_kst(ensure_aware(dt))
        if not self.is_regular_session(dt):
            return None
        close_dt = kst.replace(
            hour=self._session.close_time.hour,
            minute=self._session.close_time.minute,
            second=0,
            microsecond=0,
        )
        return (close_dt - kst).total_seconds() / 60.0

    def is_expiry_day(self, d: date) -> bool:
        """오늘이 위클리(N/O=월, L/M=목)·정규월물(2번째 목요일) 만기일 후보인지 — **요일
        규칙 기반 근사**(symbol_master 실측 아님). 위클리 요일 매핑은 2026-07-22 실측
        재검증된 값(dev_memory/NEXT_TODO.md)이지만, "그 요일이 마침 KRX 휴장일이면 실제
        만기가 어떻게 재조정되는지"는 symbol_master 없이는 알 수 없다 — 이 함수는 그
        경우를 다루지 않고 단순히 `is_trading_day(d)`로 걸러낸다(휴장일 자체는 False).
        정규월물 "두 번째 목요일" 규칙도 KRX 표준 관례로 알려진 값이며 이 세션에서
        symbol_master로 재검증하지 않았다(known gap, capability_matrix.md 참고)."""
        if not self.is_trading_day(d):
            return False
        if d.weekday() in (_WEEKLY_MONDAY_WEEKDAY, _WEEKLY_THURSDAY_WEEKDAY):
            return True
        return self.is_monthly_expiry(d)

    # -------------------------------------------------- 만기·거래일 계산 (2026-08-04, F1)
    #
    # EV Feature(`features/ev_core.py`)가 쓰는 질의들. 계산 자체는 순수하고 휴장일 집합에만
    # 의존하므로 여기가 정본이다 — 피처 모듈이 만기 규칙 사본을 들면 그게 세 번째 사본이
    # 되고, 셋 중 하나가 틀렸을 때 어느 것이 맞는지 알 방법이 없어진다.

    def monthly_expiry(self, year: int, month: int) -> date:
        """그 달 정규월물 만기일(휴장일 보정 포함) — 모듈 수준 `monthly_expiry()` 참고."""
        return monthly_expiry(year, month, self)

    def is_monthly_expiry(self, d: date) -> bool:
        """정규월물(미니선물·먼스리옵션 공통) 만기일인가.

        종전 `is_expiry_day()`는 `8 <= d.day <= 14`로 판정했는데, 그 범위는 **휴장 보정을
        모른다** — 둘째 목요일이 휴장이면 실제 만기는 그 앞 거래일(수요일 등)로 당겨지고
        그날은 이 범위 검사를 통과하지 못한다. 정본 규칙으로 판정한다.
        """
        return d == self.monthly_expiry(d.year, d.month)

    def is_quadruple_witching(self, d: date) -> bool:
        """동시만기(3·6·9·12월 정규월물 만기) — 선물·옵션이 한날 만기라 수급이 특히 크게 튄다."""
        return d.month in _QUADRUPLE_WITCHING_MONTHS and self.is_monthly_expiry(d)

    def next_monthly_expiry(self, d: date) -> date:
        """`d` **이상**인 첫 정규월물 만기일 — d가 만기일이면 d 자신."""
        this_month = self.monthly_expiry(d.year, d.month)
        if d <= this_month:
            return this_month
        year, month = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        return self.monthly_expiry(year, month)

    def has_thursday_weekly(self, d: date) -> bool:
        """그 주에 목위클리(L/M)가 상장되는가 — **먼슬리 만기 주에는 상장되지 않는다**.

        선행 프로젝트 마흐디의 **실측**이다(2026-07-10, `mahdi/data/symbol_master.py`
        `PRODUCT_TYPE_WEEKLY_THU_OPTION_CALL` 주석 + `dashboard/panels/
        expiry_liquidity_panel.py` `_is_monthly_expiry_week()`): KRX는 먼슬리 만기 주의
        목요일에 위클리(목)을 별도 상장하지 않고 먼슬리가 그 역할을 대신한다. 마흐디는 이
        사실을 몰라 한동안 대시보드의 위클리(목) 행이 비는 것을 데이터 누락으로 오인했다.

        판정은 마흐디 패널과 같은 기준(ISO 주 일치)을 쓴다. 먼슬리 만기가 휴장으로 당겨져
        수요일이 되는 경우에도 **그 주 전체**가 먼슬리 만기 주이므로 같은 판정이 맞는다.

        ## 인자는 **목요일**이어야 뜻이 맞는다 (2026-08-07 정정)

        이 함수가 답하는 것은 "`d`가 속한 주에 목위클리 만기가 있나"다. 유일한 호출처
        (`next_weekly_expiry`)가 **후보 목요일**을 넘기므로 지금까지 문제가 없었다.

        그런데 "**오늘 폴링하면 받을 목위클리 체인이 있나**"는 다른 질문이다 — 만기 다음날
        (금)에는 이번 주 물이 이미 소멸했고 다음 주 물이 상장되기 때문이다. 2026-08-07(금)에
        이 함수는 `True`(그 주=32주차에 8/6 만기가 있었다)를 답하지만 실제 마스터파일의
        목위클리는 0행이었다. 그 질문은 `thursday_weekly_listed()`가 답한다.
        """
        return d.isocalendar()[:2] != self.monthly_expiry(d.year, d.month).isocalendar()[:2]

    def thursday_weekly_listed(self, d: date) -> bool:
        """`d` 시점에 목위클리(L/M)가 **상장돼 있는가** — 수집 계획이 물어야 할 질문.

        `has_thursday_weekly()`와 다른 질문이다(그쪽 docstring "인자는 목요일이어야" 참고):
        그쪽은 "이 주에 목위클리 만기가 있나", 이쪽은 "오늘 폴링하면 받을 체인이 있나".

        ## 왜 "d 이후 첫 목요일"인가

        목위클리는 상장~결제가 최장 1주라 **만기 다음날 다음 주물 하나만** 신규 상장된다.
        따라서 어느 날 `d`에 상장돼 있는 목위클리는 `d` 이후(당일 포함) 첫 목요일이 만기다.
        그 목요일이 먼슬리 만기 주에 속하면 그 물은 애초에 상장되지 않으므로(위 실측),
        `d`에는 목위클리가 **하나도 없다**.

        ## 2026-08-07 실측 — 이 함수가 생긴 이유

        그날 `OptionChainPollEmpty`(weekly_thu)가 22회 찍혔고 `data/option_chain/weekly_thu/`에
        파일이 아예 안 생겼다. 처음엔 하루치 유실로 판정했으나 마스터파일 실측(당일 08:36
        자동 갱신본 + 12:2x 수동 재다운로드 둘 다 상품종류 L/M **0행**)으로 규정상 미상장이
        확인됐다. 8월 둘째 목요일 = 8/13 = 먼슬리(코스피200옵션 8월물) 최종거래일이라
        8/13 만기 목위클리는 상장되지 않고, 그 물이 상장될 차례이던 8/7(금)부터 8/13까지
        **5거래일간 목위클리가 존재하지 않는다**. 8/14(금)에 8/20 만기물로 재개된다.

        아카이브 3일치와 대조 검증: 08-05 True/파일있음 · 08-06 True/파일있음 ·
        08-07 False/파일없음 — 전부 일치.

        ## 이 규칙을 이미 알고 있었다는 사실

        `has_thursday_weekly()`는 2026-07-10 마흐디 실측을 이식해 **7월부터 있었고**
        `tests/features/test_ev_core.py`가 `2026-08-13 → False`를 못박아 두었다. 그런데
        옵션체인 수집 경로가 그 정본을 안 물어봐서 8/7 조사에서 오탐 22건 + 사고 오판이
        났다. 정본을 안 쓰는 소비자를 찾는 검사는 `ops/canonical_consumers.py`에 있다.
        """
        next_thursday = d + timedelta(days=(_WEEKLY_THURSDAY_WEEKDAY - d.weekday()) % 7)
        return self.has_thursday_weekly(next_thursday)

    def thursday_weekly_listing_resumes(self, d: date, *, max_scan_days: int = 28) -> date | None:
        """`d` 이후(당일 포함) 목위클리가 다시 상장되는 첫 **거래일** — 미상장 구간의 끝.

        운영 화면·로그에 "언제 돌아오는가"를 같이 찍기 위한 것이다. 부재를 알리면서 복귀
        시점을 안 알리면 사람이 매일 다시 확인해야 하고, 그러면 결국 아무도 안 본다.

        **이 값은 예측이지 관측이 아니다.** 그날 실제로 안 돌아오면 요일 규칙 쪽이 틀린
        것이고, 그건 `OptionChainSeriesMissing`(ERROR)으로 잡힌다 — 그게 이 예측의 채점이다.

        `max_scan_days` 안에 못 찾으면 None. 스캔이 **휴장일 데이터가 없는 연도로 넘어가도**
        None이다 — `is_trading_day()`는 그 경우 예외를 던지지만(L3 침묵 실패 금지), 여기서는
        그 예외를 삼킨다. 이 값은 로그에 붙는 부가 정보라, 연말에 이것 하나 때문에 무결성
        리포트 전체가 죽는 것이 훨씬 나쁘다. **부재 판정 자체는 이 함수에 의존하지 않는다.**
        """
        for offset in range(max_scan_days):
            cur = d + timedelta(days=offset)
            try:
                if self.is_trading_day(cur) and self.thursday_weekly_listed(cur):
                    return cur
            except ValueError:
                return None  # 휴장일 데이터 없는 연도 — 복귀 시점은 "모름"이다
        return None

    def next_weekly_expiry(self, d: date, *, max_scan_days: int = 28) -> date | None:
        """`d` 이상인 첫 위클리 만기 후보 — 월위클리(월)·목위클리(목) 중 **먼저 오는 쪽**.

        목요일 후보는 `has_thursday_weekly()`로 걸러낸다(먼슬리 만기 주 제외) — 마흐디
        2026-07-10 실측. 이걸 빼면 먼슬리 만기일에 "오늘 위클리도 만기"라고 주장하게 되고,
        `ev_dte_opt_w`가 그날 0을 낸다(2026-08-04 발견 — 마흐디 조사 중 확인된 실제 결함).

        **남은 근사 — 휴장 보정**: 만기 요일이 휴장이면 `monthly_expiry()`와 **같은 관례**로
        직전 거래일로 당긴다. 그 관례는 KRX 실측으로 검증된 유일한 것이고(2026-08-04,
        A05601~A05607 7개 월물의 실제 마지막 거래일과 전부 일치), 위클리에 다른 관례를
        새로 만드는 것보다 검증된 쪽에 맞추는 편이 근거가 있다. **다만 위클리 자체를 실측한
        것은 아니다.**

        권위 있는 출처는 따로 있다 — `get_quote()` 응답의 `futs_last_tr_date`(그 종목의 실제
        최종거래일)이고 마흐디가 그걸 쓴다(`mahdi/main.py`). MESSIAH도
        `OptionQuoteSnapshot.raw`에 그 필드를 보존하므로, 옵션체인이 쌓이면 **요일 규칙
        자체를 실측으로 대체**할 수 있다(NEXT_TODO).

        `max_scan_days` 안에 못 찾으면 None(휴장일 데이터가 없는 연도로 넘어갔거나 연속
        휴장이 비정상적으로 긴 경우) — 조용히 틀린 날짜를 내는 것보다 낫다. 창이 28일인
        이유는 먼슬리 만기 주를 통째로 건너뛰는 경우가 생겨서다.
        """
        for offset in range(max_scan_days):
            cur = d + timedelta(days=offset)
            if cur.weekday() == _WEEKLY_THURSDAY_WEEKDAY:
                if not self.has_thursday_weekly(cur):
                    continue  # 먼슬리 만기 주 — 목위클리 상장 없음(마흐디 2026-07-10 실측)
            elif cur.weekday() != _WEEKLY_MONDAY_WEEKDAY:
                continue

            expiry = cur
            while not self.is_trading_day(expiry):
                expiry -= timedelta(days=1)  # 먼슬리와 같은 관례 — 직전 거래일로 당긴다
            if expiry >= d:
                return expiry
            # 당겨진 만기가 이미 지났다 — 그 위클리는 끝났으므로 다음 후보로 넘어간다.
        return None

    def trading_days_until(self, start: date, end: date) -> int:
        """`start`(배타) ~ `end`(포함) 사이의 거래일 수 — D-day 계산의 정의.

        `end <= start`면 0이다. 즉 **만기 당일의 D-day는 0**이고, 만기 전날(거래일)이면 1이다.
        휴장일을 빼고 세므로 캘린더 일수와 다르다 — "잔여 거래일"이 Ver 1.4의 정의다.
        """
        if end <= start:
            return 0
        count = 0
        cur = start + timedelta(days=1)
        while cur <= end:
            if self.is_trading_day(cur):
                count += 1
            cur += timedelta(days=1)
        return count

    def calendar_gap_to_next_trading_day(self, d: date) -> int:
        """`d`부터 다음 거래일까지의 캘린더 간격(일). 평일 연속이면 1, 금→월이면 3."""
        return (self.next_trading_day(d) - d).days

    def calendar_gap_from_previous_trading_day(self, d: date) -> int:
        """직전 거래일부터 `d`까지의 캘린더 간격(일). 평일 연속이면 1, 금→월이면 3."""
        return (d - self.previous_trading_day(d)).days
