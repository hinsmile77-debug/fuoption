"""과거 1분봉 백필 — KIS 선물옵션 분봉조회로 월물 체인을 거슬러 아카이브를 채운다
(2026-08-04 신설).

## 왜 필요했나

2026-08-03까지 이 프로젝트의 시계열은 **매일 수집한 것뿐**이었다(2026-07-24 시작, 7거래일).
그래서 G1 walk-forward 관문(`models/cv.py` 프로덕션 기본값 train 180일 + embargo 1일 +
test 30일 = 211 캘린더일)을 통과할 수 있는 시점이 2027-02-20이었고, 그때까지 전략 계층은
`live 번들 결선: []`인 채로 매일 돌 예정이었다.

그 전제가 틀렸다. KIS에 국내 선물옵션 분봉 API가 있는데(`inquire-time-fuopchartprice`)
이 프로젝트에 결선돼 있지 않았을 뿐이다. 2026-08-04 실측으로 확인한 것:

- **만기된 월물의 분봉도 남아 있다.** A05607(7월물)·A05606(6월물)·A05603(3월물) 전부
  하루치 410봉을 결손 없이 돌려줬다. 마스터파일에는 현재 상장 월물만 있어 만기물 코드를
  못 얻지만, 단축코드 규칙(`_contract_code()`)으로 직접 조립하면 조회된다.
- **소급 한계는 2025-12-12**다. A05601(1월물)·A05602(2월물)까지는 응답이 오고 A05512
  이하는 빈 응답이다 — 즉 근월물 연속 시계열의 시작은 A05601이 근월이던 구간의 첫날이다.

2025-12-12 ~ 2026-08-04는 235 캘린더일이라 **G1 창 1개(211일)가 성립한다**.

## 무엇을 어느 심볼로 쓰나

**실제 월물 코드 그대로** 쓴다(`A05607`의 6~7월 봉은 `data/bars/A05607/1m/`로). 합성 연속
심볼을 만들지 않는 이유는 두 가지다: ① 아카이브가 "무엇을 받았는가"와 어긋나지 않는다,
② 라이브 수집이 쓰는 경로(`A05608`)와 같은 규칙이라 백필이 **기존 오염분을 그대로 덮어쓴다**.
연속 시계열이 필요한 소비자(학습·백테스트)는 `front_month_days()`가 돌려주는
(심볼, 날짜) 순서열로 이어 붙인다.

## 롤오버 경계

근월물은 그 달 만기일까지다. 만기는 **둘째 주 목요일**(휴장이면 직전 거래일)이고,
2026-08-04 실측으로 A05601~A05607 7개 월물의 마지막 거래일이 전부 이 규칙과 일치했다.
`monthly_expiry()`가 그 규칙이고, 어긋나면 조용히 넘어가지 않고 호출측이 알 수 있게
`verify_expiry_against_chart()`로 대조할 수 있게 해 뒀다(스크립트가 실행 시 1회 확인).

## 백필한 봉의 quality_ok / session

- `quality_ok=True` **고정**. 이 플래그의 원래 뜻은 "우리가 이 봉을 믿을 만하게 관측했는가"
  이고(틱 3개 미만이면 False), 거래소 공식 집계에는 그 판정 근거 자체가 없다 — 틱 수를
  모르기 때문이다. False로 두면 "저품질"이라는 없는 사실을 주장하게 되고, 틱 수 기준을
  거래량으로 바꿔 흉내내면 **원본과 다른 의미의 같은 컬럼**이 생긴다.
- `session`은 수집 경로와 **정확히 같은 규칙**(09:00 이전이면 `PRE_OPEN`)으로 붙인다.
  08:45~09:00 15분은 정상 거래 봉이며(2026-08-04 확정 — 거래소 공식 분봉도 이 구간을
  거래량과 함께 돌려준다), MESSIAH는 이 15분을 정규장 개시 전 지표 스케일링·웜업에 쓴다.
  그래서 **버리지 않고 표시만** 한다(`core/messages.py`의 `BarSession`과 같은 원칙).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Callable, Iterable, Mapping, Protocol, Sequence

from messiah.core import logging as mlog
from messiah.core.event_calendar import DEFAULT_SESSION, EventCalendar
from messiah.core.event_calendar import monthly_expiry as _monthly_expiry
from messiah.core.messages import BarClosed, BarSession, Horizon
from messiah.core.timeutil import KST, to_kst

# 단축코드 규칙 — "A05" + 연도 마지막 자리 + 월 2자리. 2026-08-04 실측으로 A05601~A05701
# 8개 월물 전부 확인(마스터파일의 상장 6개 + 만기물 직접 조회 2개 이상).
# 연도가 1자리라 10년마다 순환한다 — 백필 구간이 10년을 넘을 일이 없으므로 그대로 쓴다.
_CONTRACT_PREFIX = "A05"

# 분봉 커서를 되돌릴 때의 하한 — 08:45보다 이른 봉은 존재하지 않는다(실측). 08:00까지만
# 시도하고 멈춘다(무한 루프 방지).
_CURSOR_FLOOR_MINUTES = 8 * 60

# 하루치 페이징 호출 상한 — 410봉 / 102건 = 5회면 충분하고, 8이면 결손이 많은 날도 덮는다.
# 이 한도에 걸리면 그 자체가 이상 신호이므로 조용히 자르지 않고 로그를 남긴다.
_MAX_CALLS_PER_DAY = 8


class MinuteChartSource(Protocol):
    """`KISRestClient.get_futureoption_minute_chart`의 최소 계약 — 테스트가 실제 네트워크
    없이 가짜 응답을 주입할 수 있게 프로토콜로 받는다."""

    def __call__(self, symbol: str, *, date_yyyymmdd: str, hour_hhmmss: str, **kwargs) -> dict: ...


@dataclass(frozen=True, slots=True)
class FrontMonthSegment:
    """한 월물이 근월물이었던 구간 — (종목코드, 그 구간의 거래일들)."""

    symbol: str
    days: tuple[date, ...]

    @property
    def start(self) -> date:
        return self.days[0]

    @property
    def end(self) -> date:
        return self.days[-1]


def contract_code(year: int, month: int) -> str:
    """미니 KOSPI200 선물 월물의 단축코드 — 모듈 docstring "단축코드 규칙" 참고."""
    if not 1 <= month <= 12:
        raise ValueError(f"month는 1~12여야 한다: {month}")
    return f"{_CONTRACT_PREFIX}{year % 10}{month:02d}"


# 만기 규칙의 정본은 `core/event_calendar.py`다 — 2026-08-04에 옮겼다. 그 전에는 여기와
# `EventCalendar.is_expiry_day()`에 서로 다른 판정이 두 벌 있었고(이쪽만 휴장일 보정이
# 있었다), EV Feature가 세 번째 사본을 만들 참이었다. 기존 임포트 경로
# (`from messiah.data.backfill import monthly_expiry`)를 안 깨려고 여기서 재수출한다.
monthly_expiry = _monthly_expiry


def front_month_code_for_day(day: date, calendar: EventCalendar | None = None) -> str:
    """그날의 근월물 코드 — 만기일 당일까지는 그 달 월물이 근월이다.

    만기일 **당일**을 포함하는 이유: 그날도 정상 거래되고(최종거래일), 실측한 각 월물의
    마지막 데이터 날짜가 정확히 만기일이었다(A05607 → 20260709 등).
    """
    expiry = monthly_expiry(day.year, day.month, calendar)
    if day <= expiry:
        return contract_code(day.year, day.month)
    nxt = date(day.year + (day.month == 12), 1 if day.month == 12 else day.month + 1, 1)
    return contract_code(nxt.year, nxt.month)


def preceding_front_month_codes(
    day: date, count: int = 1, calendar: EventCalendar | None = None
) -> list[str]:
    """`day`의 근월물 **직전**에 근월이었던 월물들 — 가까운 것부터 (2026-08-14 F-1).

    롤 당일에는 새 월물의 아카이브가 비어 있다. 웜스타트가 200봉을 채우려면 그 앞 구간을
    직전 월물에서 이어 읽어야 하고, 그때 "직전이 누구인가"를 답하는 것이 이 함수다.

    계산: `day`가 속한 달의 1일부터 한 달씩 뒤로 물러나며 근월물 코드를 묻고, 현재 코드와
         다른 것을 순서대로 모은다. 만기 규칙 자체는 `front_month_code_for_day()` 하나만
         쓴다 — 월물 경계를 두 번째로 구현하지 않는다.
    산출: 실측 — 2026-08-14(A05609) → ["A05608"] · 2026-08-13(A05608) → ["A05607"].
    실패 조건: 없다. 과거로 못 가면(달력 예외 등) 모은 만큼만 돌려준다 — 웜스타트는 부가
              기능이라 여기서 던지면 기동을 막는다.
    """
    if count <= 0:
        return []
    current = front_month_code_for_day(day, calendar)
    found: list[str] = []
    # **그 달 1일부터 본다.** 롤 당일(만기 다음 날)에는 같은 달 안에서 근월이 바뀌어 있다 —
    # 2026-08-14의 근월은 A05609지만 2026-08-01의 근월은 A05608이다. 곧바로 전달로 물러나면
    # 바로 그 직전 월물을 건너뛴다.
    probe = date(day.year, day.month, 1)
    # 한 계약이 한 달이므로 count달이면 충분하지만, 만기 보정으로 한 달이 통째로 건너뛰는
    # 경우를 대비해 여유를 둔다. 상한이 없으면 달력 이상 시 무한 루프가 된다.
    for _ in range(count + 12):
        if len(found) >= count:
            break
        try:
            code = front_month_code_for_day(probe, calendar)
        except Exception:  # noqa: BLE001 — 못 가면 모은 만큼만
            break
        if code != current and code not in found:
            found.append(code)
        year, month = probe.year, probe.month
        probe = date(year - 1, 12, 1) if month == 1 else date(year, month - 1, 1)
    return found


def warmstart_symbol_chain(symbol: str, day: date, depth: int = 1) -> list[str]:
    """웜스타트가 아카이브를 읽을 순서 — `[오늘 심볼, 직전 월물, ...]` (2026-08-14 F-1).

    **소비처가 셋이라 여기 둔다**(`run_l1_daily`의 피처 웜스타트·옵션체인 기준가 시드,
    `run_g2_paper_trading`의 국면 웜스타트). 스크립트마다 한 벌씩 두면 이 저장소가 이미
    다섯 번 겪은 "정본 아닌 소비자"가 여섯 번째로 생긴다(`ops/canonical_consumers.py`가
    존재하는 이유).

    달력을 못 읽으면 만기 보정 없이 계산하고, 산출 자체가 실패하면 **오늘 심볼 하나만**
    돌려준다 — 체인 산출 실패가 웜스타트를 막으면 안 된다. 다만 조용히 넘어가지 않는다
    (`SymbolChainFallback`, WARNING).
    """
    try:
        calendar = EventCalendar.from_file()
    except Exception:  # noqa: BLE001 — 달력은 부가 정보다
        calendar = None
    try:
        preceding = preceding_front_month_codes(day, depth, calendar)
    except Exception as exc:  # noqa: BLE001
        mlog.log(
            "SymbolChainFallback",
            f"선행 월물 산출 실패 — 오늘 심볼만 읽는다: {exc}",
            symbol=symbol,
            date=day.isoformat(),
        )
        return [symbol]
    return [symbol, *(code for code in preceding if code != symbol)]


def audit_warm_start_drop(
    *,
    offered_by_source: Mapping[str, int],
    loaded_total: int,
    consumer: str,
    symbol: str,
) -> int:
    """로더가 건넨 봉 수와 실제 적재된 봉 수를 대조한다 — 차이가 있으면 운다 (2026-08-16 P0).

    **왜 이 대조가 필요한가.** 2026-08-14 저녁 F-1은 `warmstart_symbol_chain()`과
    `ParquetArchiver.load_recent_bars_by_source()`까지만 고쳤다. 로더는 직전 월물 봉을
    **그 월물 코드 그대로** 돌려주는데(설계다 — 출처가 데이터에 남아야 한다), 정작 그걸
    받는 `FeatureEngine.warm_start()`/`RegimeRuntime.warm_start()`의 필터는
    `b.symbol == self._symbol` 하나였다. 그래서 이어 읽은 봉이 **적재 단계에서 전량
    버려졌다.**

    두 함수 모두 "적재된 봉 수"를 정직하게 돌려주고 있었으므로 거짓말한 코드는 없다.
    문제는 **아무도 두 수를 나란히 놓지 않았다**는 것이다 — 기동 로그의 `bars_by_source`는
    로더의 답이고 `bars_by_horizon`은 적재의 답인데, 같은 줄에 있으면서 다른 질문의
    답이라는 사실이 어디에도 적혀 있지 않았다. 2026-08-16 리허설이 손으로 대조해서야
    30m에서 200 vs 15가 드러났다.

    이 함수는 그 대조를 **매 기동 자동으로** 한다. 정상 경로에서는 절대 울지 않아야 한다.

    입력: `offered_by_source`는 로더가 돌려준 심볼별 봉 수(`bars_by_source`),
         `loaded_total`은 소비처가 실제로 적재한 봉 수. `consumer`는 어느 웜스타트인지
         (`feature` / `regime`) — 태그 하나를 두 소비처가 공유하므로 구분이 필요하다.
    반환: 버려진 봉 수(0이면 정상).
    """
    offered = sum(offered_by_source.values())
    dropped = offered - loaded_total
    if dropped <= 0:
        return 0
    mlog.log(
        "WarmStartBarsDropped",
        f"{consumer} 웜스타트 — 로더가 {offered}봉을 건넸는데 {loaded_total}봉만 적재됐다"
        f"(버려진 {dropped}봉). 체인으로 이어 읽은 선행 월물이 적재 필터에 걸리는지 확인할 것",
        symbol=symbol,
        consumer=consumer,
        offered=offered,
        loaded=loaded_total,
        dropped=dropped,
        offered_by_source=dict(offered_by_source),
    )
    return dropped


def front_month_days(
    start: date, end: date, calendar: EventCalendar | None = None
) -> list[FrontMonthSegment]:
    """[start, end]를 근월물 구간으로 묶어 시간순으로 — 연속 시계열의 설계도.

    입력: calendar를 주면 휴장일을 빼고 만기 보정도 한다. **생략하면 주말만 빼고 평일을
         전부 담는다** — 백필의 기본 경로가 이쪽이다. 이유는 `configs/krx_holidays.yaml`이
         2026년만 갖고 있고(2025년은 데이터 자체가 없다) 그 파일 스스로 "작성됨≠KRX 공식
         확인됨"이라고 밝히고 있기 때문이다. 틀린 휴장일 목록으로 날짜를 **빼면** 그 거래일은
         조용히 영영 안 채워지지만, 넣어두면 그날 응답이 빈 것으로 휴장이 드러난다 — 즉
         달력을 안 믿는 쪽이 실패해도 관측 가능한 방향으로 실패한다.
    산출: 구간은 만기일 경계에서만 갈린다.
    실패 조건: start > end면 ValueError(호출 실수를 조용히 빈 결과로 만들지 않는다).
    """
    if start > end:
        raise ValueError(f"start({start})가 end({end})보다 늦다")
    segments: list[FrontMonthSegment] = []
    current_symbol: str | None = None
    bucket: list[date] = []
    day = start
    while day <= end:
        included = calendar.is_trading_day(day) if calendar is not None else day.weekday() < 5
        if included:
            symbol = front_month_code_for_day(day, calendar)
            if symbol != current_symbol:
                if bucket:
                    segments.append(FrontMonthSegment(current_symbol, tuple(bucket)))
                current_symbol, bucket = symbol, []
            bucket.append(day)
        day += timedelta(days=1)
    if bucket:
        segments.append(FrontMonthSegment(current_symbol, tuple(bucket)))
    return segments


def _price_to_ticks(raw_price: str, tick_size: Decimal) -> int:
    return int((Decimal(raw_price) / tick_size).to_integral_value(rounding=ROUND_HALF_UP))


def chart_row_to_bar(
    row: dict, symbol: str, tick_size: Decimal, *, horizon: Horizon = Horizon.M1
) -> BarClosed | None:
    """분봉 응답 1행 → BarClosed. 모듈 docstring "quality_ok / session" 참고.

    `stck_cntg_hour`는 **봉 시작 시각**이다(2026-08-04 대조 실측: 2026-08-03 응답의 최초/
    최종이 084500/153400으로 우리 수집 아카이브의 `bar_open_kst`와 정확히 일치).
    실패 조건: 필수 필드 누락·숫자 변환 실패면 None(그 행만 버리고 나머지는 계속).
    """
    try:
        day_raw, hour_raw = row["stck_bsop_date"], row["stck_cntg_hour"]
        bar_open = datetime(
            int(day_raw[0:4]),
            int(day_raw[4:6]),
            int(day_raw[6:8]),
            int(hour_raw[0:2]),
            int(hour_raw[2:4]),
            int(hour_raw[4:6]),
            tzinfo=KST,
        )
        return BarClosed(
            symbol=symbol,
            horizon=horizon,
            bar_open_kst=bar_open,
            o_ticks=_price_to_ticks(row["futs_oprc"], tick_size),
            h_ticks=_price_to_ticks(row["futs_hgpr"], tick_size),
            l_ticks=_price_to_ticks(row["futs_lwpr"], tick_size),
            c_ticks=_price_to_ticks(row["futs_prpr"], tick_size),
            volume=int(Decimal(row["cntg_vol"])),
            quality_ok=True,
            session=(
                BarSession.PRE_OPEN
                if bar_open.time() < DEFAULT_SESSION.open_time
                else BarSession.REGULAR
            ),
        )
    except (KeyError, ValueError, IndexError, InvalidOperation, ArithmeticError):
        return None


def fetch_day_bars(
    fetch: MinuteChartSource,
    symbol: str,
    day: date,
    tick_size: Decimal,
    *,
    max_calls: int = _MAX_CALLS_PER_DAY,
) -> list[BarClosed]:
    """하루치 1분봉 전부 — 커서를 뒤로 옮겨가며 페이징한다(1회 최대 102건).

    계산: 15:35(장 마감 이후)에서 시작해 응답의 **그날치 중 가장 이른 봉 1분 전**으로
         커서를 옮긴다. 응답은 날짜 경계를 넘어 전 거래일까지 이어지므로 그날치만 걸러낸다
         (`FID_PW_DATA_INCU_YN="Y"`의 성질 — `rest_client` docstring).
    종료 조건: ① 응답이 **전 거래일까지 넘어갔으면** 그날의 첫 봉을 이미 받았다는 뜻이라
             즉시 멈춘다(정확한 종료 신호 — 이게 없으면 하루마다 빈 응답을 받는 호출이
             1회씩 더 들고, 160거래일이면 160초를 그냥 버린다), ② 그날치 행이 하나도 없거나
             ③ 새로 받은 행이 없거나(커서가 더 못 감) ④ 커서가 08:00 이전이면 멈춘다.
    산출: `bar_open_kst` 오름차순 목록. 빈 목록이면 그날 데이터가 없다는 뜻이다(휴장 등).
    실패 조건: 없다 — 개별 행 파싱 실패는 그 행만 버린다. 네트워크 예외는 그대로 전파해
              호출측(스크립트)이 재시도·중단을 정한다.
    """
    day_key = day.strftime("%Y%m%d")
    collected: dict[str, BarClosed] = {}
    cursor = "153500"

    for _ in range(max_calls):
        body = fetch(symbol, date_yyyymmdd=day_key, hour_hhmmss=cursor)
        all_rows = body.get("output2") or []
        rows = [r for r in all_rows if r.get("stck_bsop_date") == day_key]
        if not rows:
            break

        new_rows = 0
        for row in rows:
            hour = row.get("stck_cntg_hour")
            if not hour or hour in collected:
                continue
            bar = chart_row_to_bar(row, symbol, tick_size)
            if bar is not None:
                collected[hour] = bar
                new_rows += 1
        if new_rows == 0:
            break
        if len(rows) < len(all_rows):
            break  # 응답이 전 거래일까지 넘어감 = 그날 첫 봉을 이미 받았다

        oldest = min(r["stck_cntg_hour"] for r in rows)
        minutes = int(oldest[0:2]) * 60 + int(oldest[2:4]) - 1
        if minutes < _CURSOR_FLOOR_MINUTES:
            break
        cursor = f"{minutes // 60:02d}{minutes % 60:02d}00"
    else:
        mlog.log(
            "BackfillPagingLimit",
            f"{symbol} {day.isoformat()} — 호출 상한 {max_calls}회에 도달, 더 이른 봉이 남아 "
            f"있을 수 있음(현재 {len(collected)}봉)",
            symbol=symbol,
            day=day.isoformat(),
            rows=len(collected),
        )

    return sorted(collected.values(), key=lambda b: b.bar_open_kst)


def verify_expiry_against_chart(
    daily_chart: Callable[..., dict], symbol: str, expected_expiry: date
) -> tuple[bool, str | None]:
    """월물의 실제 마지막 거래일이 `monthly_expiry()` 계산과 맞는지 1회 확인.

    만기 규칙은 KRX 관례에서 온 **가정**이라(`core/event_calendar.py` 주석) 백필처럼 여러
    달을 이어 붙이는 작업에서는 한 번 틀리면 구간 경계가 통째로 어긋난다. 조용히 믿지 않고
    거래소 데이터로 대조한다.

    반환: (일치 여부, 실제 마지막 거래일 문자열 또는 None). 응답이 비면 (False, None) —
         "확인 불가"와 "불일치"를 호출측이 구분하려면 두 번째 값을 보면 된다.
    """
    year, month = expected_expiry.year, expected_expiry.month
    body = daily_chart(
        symbol,
        date_from=date(year, month, 1).strftime("%Y%m%d"),
        date_to=(expected_expiry + timedelta(days=20)).strftime("%Y%m%d"),
    )
    rows = body.get("output2") or []
    if not rows:
        return False, None
    actual = max(r["stck_bsop_date"] for r in rows)
    return actual == expected_expiry.strftime("%Y%m%d"), actual


def roll_overlap_targets(
    segments: Sequence[FrontMonthSegment],
) -> list[tuple[str, date]]:
    """롤 조정에 필요한 **겹치는 하루** — (들어오는 월물, 나가는 월물의 마지막 거래일).

    근월물이 바뀌는 날 두 계약의 가격 차(basis)를 알아야 연속 시계열을 이어붙일 수 있는데,
    각 월물의 근월 구간만 받으면 두 계약이 같은 날 관측된 적이 한 번도 없다. 다행히
    **들어오는 월물은 근월이 되기 전에도 거래된다**(2026-08-04 실측: A05608은 2026-02-13부터
    데이터가 있다) — 나가는 월물의 마지막 날 하루만 더 받으면 진짜 겹침이 생긴다.

    롤 1회당 1일이므로 8개 월물이면 7일치 추가 호출(약 35회)이면 끝난다.
    """
    return [(segments[i].symbol, segments[i - 1].end) for i in range(1, len(segments))]


def roll_offset_ticks(outgoing_close: int, incoming_close: int) -> int:
    """롤 시점 basis — 같은 날 두 계약의 종가 차이(틱). 들어오는 쪽 기준."""
    return incoming_close - outgoing_close


def back_adjust(
    series: Sequence[tuple[str, Sequence[BarClosed]]],
    *,
    offsets_by_symbol: dict[str, int],
    symbol_out: str,
) -> list[BarClosed]:
    """월물별 봉 묶음 → 하나의 **후방조정(back-adjusted) 연속 시계열**.

    ## 왜 조정이 필요한가

    이어붙이기만 하면 롤 경계마다 두 계약의 가격차(basis)가 **하루 만의 급등락**으로 나타난다
    — 실제로는 일어나지 않은 움직임이다. 그 봉의 수익률이 Triple Barrier 레이블을 뒤집고
    ATR·변동성 피처를 오염시키며, walk-forward 창이 그 지점을 지날 때마다 성과가 왜곡된다.

    ## 방식 (Panama / 후방 가산조정)

    가장 최근 월물을 **손대지 않고**, 그보다 이른 월물들의 가격에 누적 offset을 더한다.
    그래서 최근 구간의 가격은 실제 호가와 같고(백테스트 체결가가 현실과 맞는다), 과거로
    갈수록 실제 그날의 절대가와 달라진다. **차분(수익률)은 전 구간에서 보존된다** — 이
    프로젝트의 피처·레이블이 전부 차분 기반이라 그게 지켜야 할 성질이다.

    거래량은 조정하지 않는다(계약이 달라도 계약 수는 계약 수다). `symbol`만 `symbol_out`으로
    통일하는데, 학습 경로(`models/trainer.build_feature_vectors`)가 단일 심볼 시계열을
    요구하기 때문이다 — 이 심볼은 **거래 가능한 종목코드가 아니라 합성 연속물의 이름**이다.

    입력: `series`는 (월물코드, 그 월물의 봉들) 목록이며 **시간 오름차순**이어야 한다.
         `offsets_by_symbol`은 각 월물에서 **다음 월물로 넘어갈 때의** basis(틱).
    """
    adjusted: list[BarClosed] = []
    # 뒤에서 앞으로 누적 — 마지막 월물의 조정량이 0이고, 하나 앞설 때마다 그 롤의 basis가 더해진다.
    cumulative = 0
    shift_by_index: list[int] = [0] * len(series)
    for i in range(len(series) - 2, -1, -1):
        cumulative += offsets_by_symbol.get(series[i][0], 0)
        shift_by_index[i] = cumulative

    for index, (_symbol, bars) in enumerate(series):
        shift = shift_by_index[index]
        for bar in bars:
            adjusted.append(
                bar.model_copy(
                    update={
                        "symbol": symbol_out,
                        "o_ticks": bar.o_ticks + shift,
                        "h_ticks": bar.h_ticks + shift,
                        "l_ticks": bar.l_ticks + shift,
                        "c_ticks": bar.c_ticks + shift,
                    }
                )
            )
    adjusted.sort(key=lambda b: b.bar_open_kst)
    return adjusted


@dataclass(frozen=True, slots=True)
class RollInfo:
    """롤 1회의 관측 결과 — 리포트용(조정이 조용히 일어나지 않게)."""

    outgoing: str
    incoming: str
    day: date
    offset_ticks: int
    matched_minute: str | None  # basis를 잰 봉의 시각. None이면 겹침 데이터가 없어 0으로 둠


def compute_roll_offsets(
    archiver, segments: Sequence[FrontMonthSegment], horizon: Horizon = Horizon.M1
) -> tuple[dict[str, int], list[RollInfo]]:
    """롤 경계마다 두 계약을 **같은 날 같은 분**에 비교해 basis를 잰다.

    같은 분을 고르는 이유: 하루 종가끼리 비교해도 되지만, 두 계약의 마지막 체결 시각이
    다르면(만기일의 나가는 월물은 대개 일찍 한산해진다) 그 시차만큼 시장 변동이 basis에
    섞인다. 두 계약이 **모두 봉을 가진 가장 늦은 분**을 쓰면 그 오염이 최소가 된다.

    겹침 데이터가 없는 롤은 offset 0으로 두고 `matched_minute=None`으로 표시한다 — 조용히
    0으로 처리하면 그 경계의 가짜 급등이 조정된 줄 알고 넘어가게 된다.
    """
    offsets: dict[str, int] = {}
    infos: list[RollInfo] = []
    for i in range(len(segments) - 1):
        outgoing, incoming = segments[i], segments[i + 1]
        day = outgoing.end
        old_frame = archiver.read_day(outgoing.symbol, horizon, day)
        new_frame = archiver.read_day(incoming.symbol, horizon, day)
        offset, minute = 0, None
        if old_frame is not None and new_frame is not None:
            old_by_min = {
                to_kst(r["bar_open_kst"]).strftime("%H%M"): r["c_ticks"]
                for r in old_frame.iter_rows(named=True)
            }
            new_by_min = {
                to_kst(r["bar_open_kst"]).strftime("%H%M"): r["c_ticks"]
                for r in new_frame.iter_rows(named=True)
            }
            common = sorted(set(old_by_min) & set(new_by_min))
            if common:
                minute = common[-1]
                offset = roll_offset_ticks(old_by_min[minute], new_by_min[minute])
        offsets[outgoing.symbol] = offset
        if minute is None:
            # **표시만으로는 부족했다** (2026-08-14 G-1). 이 함수는 원래부터 `matched_minute=
            # None`으로 사실을 남겼지만 소비처가 아무도 안 읽었고, 2026-08-14 롤이 그 상태로
            # 학습 시계열에 들어갈 뻔했다. 측정된 basis 중앙값이 116틱(= 1분봉 봉간 변동
            # 중앙값의 3배)이라 조정 없이 잇는 것은 가짜 사건을 하나 심는 일이다.
            mlog.log(
                "RollBasisUnmeasured",
                f"{outgoing.symbol}→{incoming.symbol} ({day}) basis 측정 불가 — 겹침 하루가 "
                f"없어 offset 0으로 잇는다(scripts/run_roll_overlap.py --date {day})",
                outgoing=outgoing.symbol,
                incoming=incoming.symbol,
                date=day.isoformat(),
            )
        infos.append(RollInfo(outgoing.symbol, incoming.symbol, day, offset, minute))
    return offsets, infos


def load_continuous_series(
    archiver,
    segments: Sequence[FrontMonthSegment],
    *,
    symbol_out: str,
    horizon: Horizon = Horizon.M1,
) -> tuple[list[BarClosed], list[RollInfo]]:
    """아카이브 → 후방조정된 근월물 연속 시계열 (학습·백테스트 입력).

    각 구간은 **그 월물이 근월이던 날들만** 읽는다 — 롤 겹침으로 받아둔 하루(나가는 월물의
    마지막 날에 받은 들어오는 월물)는 basis 측정에만 쓰고 시계열에는 안 넣는다. 넣으면 그
    날짜의 봉이 두 번 들어간다.
    """
    per_segment: list[tuple[str, list[BarClosed]]] = []
    for segment in segments:
        bars: list[BarClosed] = []
        for day in segment.days:
            bars.extend(archiver.read_day_bars(segment.symbol, horizon, day))
        if bars:
            per_segment.append((segment.symbol, sorted(bars, key=lambda b: b.bar_open_kst)))

    offsets, infos = compute_roll_offsets(archiver, segments, horizon)
    return back_adjust(per_segment, offsets_by_symbol=offsets, symbol_out=symbol_out), infos


def iter_backfill_targets(
    segments: Iterable[FrontMonthSegment],
) -> Iterable[tuple[str, date]]:
    """(심볼, 날짜)를 시간순으로 펼친다 — 스크립트의 진행률 표시·재개 지점 계산용."""
    for segment in segments:
        for day in segment.days:
            yield segment.symbol, day


def continuous_days(segments: Sequence[FrontMonthSegment]) -> list[tuple[str, date]]:
    """연속 시계열을 읽을 순서 — `iter_backfill_targets`의 리스트판(학습·백테스트가 쓴다)."""
    return list(iter_backfill_targets(segments))
