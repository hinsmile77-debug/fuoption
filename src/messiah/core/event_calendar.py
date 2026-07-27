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


DEFAULT_SESSION = SessionHours()

# Ver 1.2 §4.2 구현 순서 대응 위클리 요일 매핑(N/O=월요일, L/M=목요일) — 2026-07-22
# symbol_master 실측으로 재검증됨(dev_memory/NEXT_TODO.md 참고). 여기서는 "오늘이 그
# 요일인가"만 계산 — 실제 그 요일에 상장된 위클리 종목이 있는지는 symbol_master의 몫이다.
_WEEKLY_MONDAY_WEEKDAY = 0
_WEEKLY_THURSDAY_WEEKDAY = 3
_MONTHLY_EXPIRY_WEEKDAY = 3  # 정규월물(선물/먼스리옵션) 만기 = 해당월 두 번째 목요일(표준 관례)


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
        return d.weekday() == _MONTHLY_EXPIRY_WEEKDAY and 8 <= d.day <= 14
