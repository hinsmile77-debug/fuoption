"""적재 계열의 **그날 기대치** — "오늘 이 계열이 있어야 하는가" (2026-08-07 고도화 1).

## 왜 생겼나

`configs/instance.yaml`의 `universe:`에는 2026-08-04부터 `K200_OPT_WEEKLY_THU`가 적혀
있었다. 그런데 그 선언을 **"오늘 실제로 쌓이고 있는가"와 대조하는 자리가 어디에도
없었다.** `core/universe.py`는 토큰을 폴러 인자로 번역할 뿐이고, `ops/series_coverage.py`는
디스크에서 계열을 발견할 뿐이다. 둘 사이에 "있어야 하는데 없다"를 판정하는 축이 없다.

2026-08-07에 그 빈자리가 두 방향으로 동시에 드러났다:

1. 목위클리가 하루 종일 안 쌓였는데 **장중 어느 화면에도 안 떴다**(3시간 40분).
2. 그런데 그 부재는 **규정상 정상**이었고(먼슬리 만기 주 미상장), 장후 리포트는
   `option_chain/weekly_thu: 그날 한 행도 없다 — 결선 확인 필요`라는 **오탐 ERROR**를
   찍을 참이었다. 그것도 8/7~8/13 **5거래일 연속**으로.

즉 필요한 것은 "선언 목록"이 아니라 **캘린더 조건부 계약**이다. 평면 목록으로 계약을
세우면 미상장 구간마다 매일 늑대소년이 된다 —
`configs/pending_verifications.yaml`이 가장 경계하는 실패 형태 그대로다.

## 규칙을 여기서 만들지 않는다

목위클리 미상장 규칙의 정본은 `core/event_calendar.py`다(`thursday_weekly_listed()`,
2026-07-10 마흐디 실측 이식). 이 모듈은 **묻기만 한다.** 2026-08-04에 만기 규칙 사본
두 벌을 하나로 합친 것과 같은 규율이다 — 여기에 요일 계산을 한 줄이라도 적으면 그게
세 번째 사본이 되고, 셋이 어긋났을 때 어느 것이 맞는지 알 방법이 없어진다.

## 수급은 유니버스 토큰이 아니다

`flow_intraday/K2I`(투자자별 수급)는 `core/universe.py`의 어휘에 없다 — 거래 대상이
아니라 보조 데이터라 토큰이 없다. 그래도 **매 거래일 필수**이므로 여기서 기준선으로
직접 선언한다. 유니버스에 없다는 이유로 계약에서 빠지면, 그건 이 모듈이 막으려는
"아무도 안 보는 계열"을 새로 하나 만드는 것이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time

from messiah.core import universe
from messiah.core.event_calendar import EventCalendar

# 옵션 시리즈 이름(`symbol_master`/`OptionChainPoller`) → 적재 계열 이름
# (`ops/series_coverage.py`가 디렉터리에서 발견하는 이름). 두 어휘가 다른 이유는
# 아카이브가 `data/option_chain/{series}/`로 한 겹 더 들어가기 때문이다.
ARCHIVE_PREFIX_OPTION_CHAIN = "option_chain"

# 유니버스 토큰이 아니지만 매 거래일 필수인 계열 — 모듈 docstring "수급은 토큰이 아니다".
BASELINE_SERIES: tuple[str, ...] = ("flow_intraday/K2I", "ticks")

# 계열별 **데이터가 존재할 수 있는 가장 이른 시각** (2026-08-10 A-1). None이면 판정 창
# 시작(= 등록된 정시 트리거)이 곧 기준선이다 — 그 계열은 프로세스가 뜨는 즉시 자기 격자로
# 폴링을 시작하므로 "언제부터 볼 수 있었나"가 곧 "언제 떴나"다.
#
# ## 왜 이 축이 필요한가 — 계열마다 "볼 수 있었던 시작"이 다르다
#
# 2026-08-07(정상 기동 08:35:34) 실측: 수급 첫 행 **08:36**(1분 뒤) · 옵션 regular
# **08:40** · weekly_mon **08:41** — 셋 다 기동 시각에 따라온다. 그런데 체결틱은 그날도
# **08:45**였고, 기동을 10분 앞당긴 날도 08:45였다. 시장이 그 시각부터 틱을 내기 때문이다
# (3거래일 실측, `scripts/run_l1_daily.py` 모듈 docstring).
#
# 판정 창을 정본(08:20)으로 옮기면서 이 차이를 안 실으면 **틱은 매일 25분짜리 머리 구멍을
# 갖게 된다** — 아무도 못 고치는 오탐이고, 그런 축은 한 달 안에 안 읽히게 된다.
FIRST_DATA_KST: dict[str, time] = {
    # 체결틱은 시장 사정이라 기동 시각과 무관하다.
    "ticks": time(8, 45),
}


@dataclass(frozen=True)
class Expectation:
    """계열 하나의 그날 계약.

    `required=False`는 **"오늘은 없는 것이 정답"**이다 — 판정 불가(`measured=False`)와도
    0행 사고와도 다른 세 번째 상태다. `ops/series_coverage.py`가 이 셋을 각각 다르게 찍는다.
    """

    series: str
    required: bool
    reason: str = ""
    # 미상장 계열이 다시 돌아오는 첫 거래일 — 부재만 알리고 복귀 시점을 안 알리면
    # 사람이 매일 다시 확인해야 하고, 그러면 결국 아무도 안 본다.
    resumes_on: date | None = None
    # 이 계열의 데이터가 존재할 수 있는 가장 이른 시각 — `FIRST_DATA_KST` 참고.
    # None이면 판정 창 시작이 그대로 기준선이다.
    first_data_kst: time | None = None

    @property
    def note(self) -> str:
        """계열 **이름 없이** 사유만 — 이미 이름을 찍은 줄에 덧붙일 때 쓴다.

        `label`과 나눠 둔 이유는 순전히 중복 때문이다. 커버리지 표는 `"{계열}: ..."`로
        시작하므로 거기에 `label`을 붙이면 이름이 두 번 나온다(2026-08-07 실측).
        """
        if self.required:
            return "필수"
        resume = f" · {self.resumes_on:%m-%d} 재개 예정" if self.resumes_on else ""
        return f"미상장({self.reason}){resume}"

    @property
    def label(self) -> str:
        """사람이 읽는 한 줄 — 계열 이름을 포함한다. 리포트·기동 로그가 공유한다."""
        return f"{self.series}: {self.note}"


def option_series_expectation(
    series: str, day: date, calendar: EventCalendar
) -> tuple[bool, str, date | None]:
    """옵션 시리즈가 `day`에 상장돼 있는가 → (상장 여부, 사유, 재개일).

    상장돼 있으면 사유는 빈 문자열이고 재개일은 None이다 — "정상인데 사유를 붙이면"
    로그가 매일 같은 말을 하게 되고 그게 곧 안 읽히는 상태다.

    `weekly_thu` 외의 시리즈에 캘린더 조건이 없는 것은 **아직 아는 것이 없어서**이지
    조건이 없다고 확인해서가 아니다. 반대 방향 단언(`OptionChainCalendarViolation`)이
    그 무지를 매일 채점한다 — 있어야 할 조건을 몰랐다면 언젠가 그쪽으로 걸린다.
    """
    if series != "weekly_thu":
        return True, "", None
    if calendar.thursday_weekly_listed(day):
        return True, "", None
    expiry = calendar.monthly_expiry(day.year, day.month)
    return (
        False,
        f"먼슬리 만기 주({expiry:%m-%d} 만기) — KRX 미상장",
        calendar.thursday_weekly_listing_resumes(day),
    )


def for_day(day: date, tokens: list[str], calendar: EventCalendar) -> dict[str, Expectation]:
    """그날 적재 계열별 계약 — `{계열 이름: Expectation}`.

    입력: `tokens`는 `configs/instance.yaml`의 `universe` 목록(검증 전이어도 된다 —
         모르는 토큰은 소비자가 없으므로 계약도 없다. 토큰 검증은 `universe.validate()`의
         몫이고 기동 시점에 이미 끝나 있다).
    계산: 옵션 토큰은 시리즈별로 캘린더에 묻고, 선물 토큰은 `ticks`로 접힌다
         (미니선물 체결틱이 곧 그 토큰의 적재 증거다). 기준선 계열은 무조건 필수.
    """
    out: dict[str, Expectation] = {
        name: Expectation(series=name, required=True, first_data_kst=FIRST_DATA_KST.get(name))
        for name in BASELINE_SERIES
    }
    for series in universe.option_series(tokens):
        name = f"{ARCHIVE_PREFIX_OPTION_CHAIN}/{series}"
        listed, reason, resumes = option_series_expectation(series, day, calendar)
        out[name] = Expectation(
            series=name,
            required=listed,
            reason=reason,
            resumes_on=resumes,
            first_data_kst=FIRST_DATA_KST.get(name),
        )
    return out


def summarize(expectations: dict[str, Expectation]) -> list[str]:
    """기동 로그·리포트에 찍을 계약 요약 — **미상장 계열만** 한 줄씩.

    필수 계열까지 매일 찍으면 그게 소음이 된다. 필수인데 안 쌓인 것은 커버리지 축이
    따로 운다(`ops/series_coverage.py`). 여기서 볼 것은 **"오늘 일부러 안 모으는 것"**뿐이고,
    그 줄이 없는 날이 정상이다.
    """
    ordered = sorted(expectations.values(), key=lambda e: e.series)
    return [item.label for item in ordered if not item.required]
