"""적재 계열 시간 커버리지 — "오늘 이 계열이 **언제부터 언제까지** 쌓였나" (2026-08-06 고도화 2).

## 왜 이 모듈이 생겼나

2026-08-06에 옵션체인 약 1,500다리와 장중 수급 264행이 영구 소실됐다. 소실 자체는
아카이버 결함이었고 그날 고쳤다(`data/flow_archiver.py` "죽어도 남는다는 재기동에서
거짓이었다"). 그런데 **무결성 리포트는 그 얘기를 한 줄도 안 했다.**

이유는 단순하다 — 리포트가 보는 계열이 봉과 틱뿐이었다. 옵션체인·수급은 하루 종일 0행이어도
리포트가 초록이다. 이 프로젝트가 같은 형태로 이미 세 번 당했다: `InvestorFlowPoller`(7개월),
`OptionChainPoller`(수개월), FL 피처(모델 미도달). 셋 다 "만들었으니 되고 있겠지"였다.

## 행수가 아니라 **커버리지**를 재는 이유

2026-08-06의 `option_chain/regular`는 1,302행이었다. 행수만 보면 많아 보인다. 그런데 첫
사이클이 **10:30**이었다 — 08:35~10:29가 통째로 비어 있었고 그게 그날 소실의 전부다.
"몇 행인가"로는 절대 안 보이고 "언제부터인가"로만 보인다.

## 카덴스를 어떻게 아는가 — 안 안다, 데이터에서 뽑는다

계열마다 폴링 주기가 다르다(수급 60초 · 먼쓰리 300초 · 위클리 600/300초). 그 상수는
`scripts/run_l1_daily.py`의 `_option_chain_plan()`과 `_FLOW_POLL_SECONDS`에 있는데,
여기에 복사하면 **두 곳이 조용히 어긋나는 순간** 이 검사가 거짓말을 시작한다.

그래서 안 가져온다. 그날 데이터의 **사이클 간격 중앙값**을 그 계열의 정상 카덴스로 보고,
그 3배를 넘는 구멍만 구멍으로 센다. 자기 기준으로 자기를 재는 셈이라 상수가 필요 없고,
주기를 바꿔도 검사가 따라온다.

### "사이클"로 묶는 이유 (첫 구현에서 틀린 자리)

처음에는 분(分) 간격의 중앙값을 그대로 카덴스로 썼다. 옵션체인 한 사이클은 42다리를
초당 1건으로 받아 **2~3분에 걸쳐** 들어오므로, 그 안의 1분 간격들이 중앙값을 1분으로
끌어내렸다. 임계가 5분이 되어 정상적인 10분 주기 사이의 8분 간격이 전부 구멍으로 잡혔고,
2026-08-06 실데이터에서 **한 계열에만 30건**의 헛경고가 났다.

그래서 연속된 분들을 먼저 한 사이클로 묶고, **사이클 시작 시각 사이의** 간격으로 카덴스를
잰다. 그러면 regular는 10분, weekly_thu는 5분으로 제대로 나온다.

사이클이 `_MIN_CYCLES_FOR_CADENCE`개 미만이면 카덴스를 추정하지 않고 1분으로 본다.
표본 2개로 중앙값을 내면 **그 간격 자신이 기준이 되어 어떤 구멍도 못 잡는다** — 체결틱이
정확히 그 경우다(2026-08-06에 사이클이 08:45~10:03과 10:25~15:34 둘뿐이었고, 그 사이
22분 구멍이 바로 재부팅 공백이었다).

**이 방식이 못 잡는 것**(정직하게): 하루 종일 **고르게** 절반이 빠지면 중앙값이 2배가 되어
아무것도 안 걸린다. 이 모듈이 겨냥하는 것은 그게 아니라 **연속된 구멍**이다 —
2026-08-05(5시간 35분)과 2026-08-06(1시간 55분)이 전부 그 모양이었고, 재기동·크래시·
결선 끊김은 원래 그 모양으로 난다.

## 장전 구간에 대한 예외 하나

계열마다 데이터가 **시작되는 시각이 다르다**: 체결틱은 08:45 정각부터(3거래일 실측,
`scripts/run_l1_daily.py` 모듈 docstring), 옵션체인은 첫 폴링 사이클(08:38~08:40),
수급은 첫 60초 격자(08:36). 기준선을 프로세스 기동(08:35)으로 잡으면 이 편차가 전부
"머리 구멍"으로 잡힌다.

그래서 머리 구멍은 `_HEAD_GAP_FLOOR_MINUTES` 미만이면 안 센다. 20분은 위 편차(최대 10분)를
넉넉히 덮으면서, 실제 소실(2026-08-06 기준 111~115분)과는 5배 이상 떨어져 있다.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence

import polars as pl

from messiah.core.event_calendar import DEFAULT_SESSION
from messiah.core.timeutil import KST
from messiah.ops.series_expectation import Expectation

# 정상 카덴스의 몇 배를 넘어야 "구멍"인가. 3배면 폴링 두 번을 연달아 놓친 상태다 —
# 재시도(`OptionChainPollRetried`)가 사는 범위를 넘어선다.
_GAP_CADENCE_MULTIPLE = 3.0

# 구멍의 최소 크기. 카덴스가 1분인 계열(수급·틱)에서 3배는 3분인데, 그 정도는 KIS 500
# 한 번으로도 난다(2026-08-06에 `InvestorFlowPollError` 4건이 그랬고 결손은 3행이었다).
_MIN_GAP_MINUTES = 5.0

# 카덴스를 추정하려면 사이클이 최소 이만큼 필요하다 — 모듈 docstring "사이클로 묶는 이유".
# 미만이면 1분(연속 계열)로 보고, 그러면 임계는 `_MIN_GAP_MINUTES`가 된다.
_MIN_CYCLES_FOR_CADENCE = 5

# 사이클 길이가 사이클 간격의 이 비율을 넘으면 "폴링 사이클"이 아니라 "끊긴 연속 계열"로
# 본다 — `_estimate_cadence()` docstring "긴 블록은 사이클이 아니다".
_CYCLE_DURATION_SHARE = 0.5

# 한 계열에서 사람에게 보여줄 구멍 판정의 최대 개수. 넘으면 "외 N건"으로 접는다 —
# 30줄짜리 breach 목록은 그 자체가 아무도 안 읽는 상태를 만든다(늑대소년).
_MAX_GAP_FINDINGS = 3

# 머리 구멍의 하한 — 모듈 docstring "장전 구간에 대한 예외 하나".
_HEAD_GAP_FLOOR_MINUTES = 20.0

# 소급 불가 계열의 접두어 (2026-08-07 고도화 4).
#
# `findings_for()`는 이미 문장에 "소급 불가"를 적고 있었지만, **그게 다른 판정과 같은
# 무게로 섞여 있었다.** 봉은 KIS 분봉 API로 되메울 수 있고 재합성이 자동이라 하루 뒤에
# 고쳐진다. 옵션체인·수급·체결틱은 과거 조회 경로가 자체가 없어 **지금 없으면 영원히
# 없다** — 급이 다른 사건인데 문구가 같으면 사람이 같은 무게로 읽는다.
#
# **순서가 중요하다**: 이 등급은 캘린더 계약(`expected`) **뒤에** 붙는다. 앞에 두면
# 2026-08-07처럼 규정상 미상장인 날에 가장 크게 우는 오탐이 된다 — 묻고, 기대를 세우고,
# 그 다음에 등급을 매긴다.
_IRRECOVERABLE_PREFIXES = ("option_chain/", "flow_intraday/", "ticks")

# 꼬리 구멍의 하한. 마지막 사이클과 15:35 사이는 카덴스만큼 비는 것이 정상이라
# (먼쓰리 10분 격자면 15:30이 마지막) 머리보다 관대할 이유가 없다 — 같은 값을 쓴다.
_TAIL_GAP_FLOOR_MINUTES = 20.0


# 세션 커버리지가 이 값을 밑돌면 **잘렸다**고 본다 (2026-08-07 고도화 1).
#
# 95%: 판정 창 7시간(08:35~15:35 = 420분) 기준 21분이다. `_HEAD_GAP_FLOOR_MINUTES`·
# `_TAIL_GAP_FLOOR_MINUTES`·`bar_tail_gap_minutes`(20분)와 같은 크기를 비율로 옮긴 값 —
# 네 곳이 같은 질문에 답하므로 임계도 같은 크기여야 한다.
_COVERAGE_FLOOR_PCT = 95.0


def _is_irrecoverable(name: str) -> bool:
    """이 계열의 공백이 **영구**인가 — 과거 조회 경로가 없는 계열인지."""
    return name.startswith(_IRRECOVERABLE_PREFIXES)


def _coverage_pct(
    minutes: Sequence[datetime], cadence: float, window: tuple[datetime, datetime]
) -> float:
    """판정 창 중 실제로 **관측된 비율**(0~100) — 고도화 1.

    ## 분 수를 그대로 나누면 안 된다

    간헐 폴러(옵션체인 10분 격자)는 창의 10%에만 행이 있다. 그걸 "커버리지 10%"로 읽으면
    정상인 계열이 매일 빨갛게 된다. 그래서 **관측된 분이 아니라 관측된 구간**을 센다:
    각 사이클이 자기 카덴스만큼의 시간을 대표한다고 보고, 그 합을 창 길이로 나눈다.

    즉 10분 격자로 42사이클을 돈 계열은 420분을 덮은 것이고 커버리지 100%다. 13:40에
    끊겼으면 그 뒤 카덴스만큼도 못 덮으므로 그만큼 떨어진다.

    상한을 100으로 접는다 — 마지막 사이클이 창 끝을 넘어 대표할 수 있어서다.
    """
    start, end = window
    span = (end - start).total_seconds() / 60.0
    if span <= 0:
        return 100.0
    covered = 0.0
    for begin, finish in _group_into_cycles(list(minutes)):
        # 사이클 자체의 길이 + 그 사이클이 대표하는 카덴스 한 칸.
        covered += (finish - begin).total_seconds() / 60.0 + max(cadence, 1.0)
    # 머리·꼬리 구멍은 자동으로 빠진다 — 사이클이 없는 구간은 아무것도 안 더하기 때문이다.
    return round(min(100.0, 100.0 * covered / span), 1)


@dataclass
class SeriesCoverage:
    """계열 하나의 그날 시간 커버리지.

    `measured=False`는 **판정 불가**다(파일을 못 읽음) — 0행과 다르다. 0행은 "쌓기로 되어
    있는데 하나도 안 쌓였다"는 판정이고, 그게 이 모듈이 가장 잡고 싶은 상태다(L18).
    """

    name: str
    rows: int
    measured: bool
    # 그날 이 계열이 **있어야 하는가** (2026-08-07 P0-3). `measured`/`rows`와 직교하는
    # 세 번째 축이다: `measured=False`는 "못 읽었다", `rows=0`은 "없다", `expected=False`는
    # **"없는 것이 정답이다"**. 셋을 한 축으로 접으면 규정상 미상장인 날마다 오탐이 난다
    # (2026-08-07 목위클리 — 그대로 뒀으면 8/13까지 5거래일 연속 ERROR였다).
    expected: bool = True
    # 판정 창 대비 **관측된 분의 비율**(0~100) — 고도화 1 (2026-08-07).
    #
    # 이 저장소의 계측은 "구멍"은 보는데 "끊김"은 못 봤다. 봉 연속성·거래량 대조·관측 공백
    # 세 축이 전부 *관측된 구간 안에서 무엇이 빠졌나*를 묻고, *관측이 언제 멈췄나*는 계열
    # 커버리지 하나만 물었다(그리고 그 축은 봉을 안 본다). 2026-08-07에 네 축이 초록인 채로
    # 1시간 54분이 날아간 이유가 그것이다.
    #
    # 이 한 값이 모든 계열에 같은 질문을 던진다: **"세션 중 몇 %를 실제로 봤나."**
    # 그날 답은 봉 71% · 틱 71% · 옵션체인 71% · 수급 72%였는데, 세 축이 100%라고 답했다.
    coverage_pct: float = 100.0
    # `expected=False`인 이유 + 복귀 예정일. `ops/series_expectation.Expectation.note` 원문
    # (계열 이름은 빼고 사유만 — 이 표는 이미 이름을 찍으므로 붙이면 두 번 나온다).
    expectation_note: str = ""
    first_kst: str | None = None
    last_kst: str | None = None
    # 그날 데이터에서 뽑은 정상 간격(분) — 상수가 아니라 관측값이다(모듈 docstring).
    cadence_minutes: float | None = None
    head_gap_minutes: float = 0.0
    tail_gap_minutes: float = 0.0
    longest_gap_minutes: float = 0.0
    # 구멍으로 판정된 구간 [시작, 끝, 분] — 사람이 로그에서 그 시각을 바로 찾을 수 있게.
    gaps: list[tuple[str, str, int]] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def session_window(day: date, *, start: datetime | None = None) -> tuple[datetime, datetime]:
    """그날의 판정 창 — (시작, 끝).

    시작은 **첫 프로세스 기동 시각**이다(호출측이 `_first_session_start()`로 넘긴다).
    08:35 고정값을 쓰지 않는 이유: 재기동으로 늦게 뜬 날 그 공백까지 이 검사가 다시
    세면, 이미 `restarts`가 재고 있는 사건을 두 번 세는 것이 된다.

    `start`가 naive면 KST로 읽는다 — 유일한 생산자인 `_first_session_start()`가
    **의도적으로 naive**를 돌려주기 때문이다(그쪽 docstring: 소비처가 `Get-WinEvent`의
    로컬 시각 문자열이라 tz를 붙이면 오프셋 변환이 끼어든다). 그 값은 KST 벽시계다.
    """
    close = datetime.combine(day, DEFAULT_SESSION.close_time, tzinfo=KST)
    if start is None:
        start = datetime.combine(day, DEFAULT_SESSION.open_time, tzinfo=KST) - timedelta(
            minutes=25
        )  # 08:35 — 정시 기동 시각. 기동 로그가 없는 날의 최선 추정치다.
    elif start.tzinfo is None:
        start = start.replace(tzinfo=KST)
    return min(start, close), close


def _group_into_cycles(minutes: Sequence[datetime]) -> list[tuple[datetime, datetime]]:
    """연속된 분들을 한 사이클로 묶는다 → [(시작, 끝), ...].

    옵션체인 한 사이클은 42다리가 초당 1건으로 와서 2~3분에 걸친다. 그 안쪽 1분 간격을
    카덴스로 오인하지 않으려면 먼저 묶어야 한다(모듈 docstring).
    """
    cycles: list[tuple[datetime, datetime]] = []
    begin = previous = minutes[0]
    for moment in minutes[1:]:
        if (moment - previous).total_seconds() / 60.0 <= 1.0:
            previous = moment
            continue
        cycles.append((begin, previous))
        begin = previous = moment
    cycles.append((begin, previous))
    return cycles


def _estimate_cadence(cycles: Sequence[tuple[datetime, datetime]]) -> float:
    """사이클 시작 간격의 중앙값(분). 추정할 근거가 약하면 1분(연속 계열)로 본다.

    ## 표본이 모자라면 추정하지 않는다

    사이클이 둘뿐이면 중앙값이 **그 둘 사이의 간격 자신**이라 임계가 그 3배가 되고, 어떤
    구멍도 못 잡는 검사가 된다. 체결틱의 재부팅 공백이 정확히 그 모양이다(모듈 docstring).

    ## 긴 블록은 사이클이 아니다

    "연속 계열이 구멍 몇 개로 쪼개진 것"과 "간헐 폴러가 규칙적으로 도는 것"은 둘 다
    사이클 여러 개로 보인다. 그런데 전자에서 블록 간격을 카덴스로 삼으면 **그 구멍들이
    정상 주기가 되어** 아무것도 안 걸린다.

    둘을 가르는 것은 **사이클의 길이**다. 옵션체인 한 사이클은 42다리가 2~3분이고 주기는
    10분이라 길이가 간격의 20~30%다. 반면 40분짜리 블록이 60분마다 있는 것은 폴링 주기가
    아니라 끊긴 연속 계열이다. 길이가 간격의 절반을 넘으면 후자로 본다.
    """
    if len(cycles) < _MIN_CYCLES_FOR_CADENCE:
        return 1.0
    starts = [begin for begin, _ in cycles]
    deltas = [
        (later - earlier).total_seconds() / 60.0 for earlier, later in zip(starts, starts[1:])
    ]
    if not deltas:
        return 1.0
    cadence = statistics.median(deltas)
    durations = [(end - begin).total_seconds() / 60.0 for begin, end in cycles]
    if statistics.median(durations) > cadence * _CYCLE_DURATION_SHARE:
        return 1.0  # 폴링 사이클이 아니라 끊긴 연속 계열이다
    return cadence


def measure(
    name: str,
    timestamps: Iterable[datetime],
    *,
    window: tuple[datetime, datetime],
    expectation: "Expectation | None" = None,
) -> SeriesCoverage:
    """타임스탬프 목록 → 커버리지 판정 (순수 함수).

    입력: `timestamps`는 tz-aware. 창 밖의 것은 버린다 — 장 시작 전 워밍업 폴링이
         머리 구멍 계산을 어지럽히지 않게 한다.
    계산: 분(分) 격자로 내림해 중복을 없앤 뒤 간격을 본다. 옵션체인처럼 한 사이클이 42행
         1초 간격으로 오는 계열도, 분으로 뭉치면 카덴스가 사이클 주기로 드러난다.

    `expectation`을 주면 그날 계약(`ops/series_expectation.py`)을 판정에 실어 보낸다.
    안 주면 **필수**로 본다 — 계약을 모르는 호출자가 조용히 면제받는 일이 없어야 한다.
    """
    expected = expectation.required if expectation is not None else True
    note = "" if expectation is None or expectation.required else expectation.note

    start, end = window
    minutes = sorted(
        {ts.replace(second=0, microsecond=0) for ts in timestamps if start <= ts <= end}
    )
    if not minutes:
        span = max((end - start).total_seconds() / 60.0, 0.0)
        return SeriesCoverage(
            name=name,
            rows=0,
            measured=True,
            expected=expected,
            expectation_note=note,
            # 미상장 계열의 머리 구멍은 0으로 둔다 — 창 전체가 구멍으로 잡히면
            # `fix_verification`의 `series_head_gap_minutes_max` 지표가 그 값을 집어
            # 등록부 항목이 통째로 `재발`로 뒤집힌다(2026-08-07 발견).
            head_gap_minutes=round(span, 1) if expected else 0.0,
            coverage_pct=0.0 if expected else 100.0,
            detail=(
                "그날 한 행도 없다 — 결선이 끊겼거나 적재가 실패했다"
                if expected
                else f"미상장일이라 0행이 정상 — {note}"
            ),
        )

    cycles = _group_into_cycles(minutes)
    cadence = _estimate_cadence(cycles)
    threshold = max(_GAP_CADENCE_MULTIPLE * cadence, _MIN_GAP_MINUTES)

    gaps: list[tuple[str, str, int]] = []
    longest = 0.0
    for (_, ends), (begins, _) in zip(cycles, cycles[1:]):
        span = (begins - ends).total_seconds() / 60.0
        longest = max(longest, span)
        if span > threshold:
            gaps.append((f"{ends:%H:%M}", f"{begins:%H:%M}", int(round(span))))

    head = (minutes[0] - start).total_seconds() / 60.0
    tail = (end - minutes[-1]).total_seconds() / 60.0
    return SeriesCoverage(
        name=name,
        rows=len(minutes),
        measured=True,
        expected=expected,
        expectation_note=note,
        first_kst=f"{minutes[0]:%H:%M}",
        last_kst=f"{minutes[-1]:%H:%M}",
        cadence_minutes=round(cadence, 1),
        head_gap_minutes=round(head, 1),
        tail_gap_minutes=round(tail, 1),
        longest_gap_minutes=round(longest, 1),
        gaps=gaps,
        coverage_pct=_coverage_pct(minutes, cadence, window),
        detail=f"{minutes[0]:%H:%M}~{minutes[-1]:%H:%M} · 카덴스 {cadence:.0f}분",
    )


def findings_for(coverage: SeriesCoverage) -> list[str]:
    """사람이 읽는 판정 문장 — 빈 목록이면 그 계열은 정상이다.

    문장에 **소급 가능성**을 같이 적는다. 옵션체인·수급·틱은 과거 조회 경로가 없어서
    "지금 없으면 영원히 없다" — 봉의 결손과는 급이 다른 사건인데, 문구가 같으면 사람이
    같은 무게로 읽는다.
    """
    if not coverage.measured:
        return []

    # ---- 캘린더 계약 (2026-08-07 P0-3 + 고도화 3) ----
    #
    # **양방향으로 단언한다.** 종전 검사는 "없으면 운다" 한 방향뿐이었고, 그래서 규정상
    # 미상장인 날에 오탐을 내면서 동시에 **"있으면 안 되는데 있는 경우"는 못 봤다.**
    # 후자가 더 위험하다 — 빈 파일은 눈에 띄지만 조용히 섞인 만기 하루짜리 체인은
    # 그대로 모델에 들어간다(그릭스·IV 성질이 전혀 다르다).
    if not coverage.expected:
        if coverage.rows == 0:
            return []  # 없는 것이 정답 — 조용한 것이 옳다
        return [
            f"{coverage.name}: 미상장으로 판정했는데 {coverage.rows}분치가 쌓였다 — "
            f"규정 이해가 틀렸거나 계열 매핑이 어긋났다({coverage.expectation_note})"
        ]

    # ---- 소급 가능성 등급 (2026-08-07 고도화 4) ----
    #
    # 하드코딩된 "소급 불가"를 계열별 판정으로 바꾼다. 지금은 `collect()`가 발견하는 계열이
    # 전부 소급 불가라 결과가 같지만, 봉처럼 되메울 수 있는 계열이 이 축에 붙는 순간
    # 종전 문구는 **거짓말을 시작한다**. 그때 고치면 늦다.
    permanent = _is_irrecoverable(coverage.name)
    scope = "그 구간은 영구 소실(소급 경로 없음)" if permanent else "소급 가능 — 재수집 대상"

    out: list[str] = []
    # **세션 커버리지가 첫 줄이다** (고도화 1). 아래 머리/꼬리/구멍 판정은 "어디가"를
    # 말하고 이 줄은 "얼마나"를 말한다. 사람이 먼저 알아야 하는 건 후자다 —
    # 2026-08-07에 세 축이 100%라고 답하는 동안 실제는 71%였다.
    if coverage.rows and coverage.coverage_pct < _COVERAGE_FLOOR_PCT:
        out.append(
            f"{coverage.name}: 세션 커버리지 {coverage.coverage_pct:.0f}% "
            f"(< {_COVERAGE_FLOOR_PCT:.0f}%) — {scope}"
        )
    if coverage.rows == 0:
        detail = "영구 소실 — 소급 경로 없음" if permanent else "재수집 가능"
        return [f"{coverage.name}: 그날 한 행도 없다 — 결선 확인 필요({detail})"]
    if coverage.head_gap_minutes > _HEAD_GAP_FLOOR_MINUTES:
        out.append(
            f"{coverage.name}: 세션 시작 후 {coverage.head_gap_minutes:.0f}분간 적재 없음"
            f"(첫 행 {coverage.first_kst}) — {scope}"
        )
    if coverage.tail_gap_minutes > _TAIL_GAP_FLOOR_MINUTES:
        out.append(
            f"{coverage.name}: 마지막 행({coverage.last_kst}) 이후 "
            f"{coverage.tail_gap_minutes:.0f}분간 적재 없음 — {scope}"
        )
    for begin, finish, span in coverage.gaps[:_MAX_GAP_FINDINGS]:
        out.append(f"{coverage.name}: {begin}~{finish} {span}분 구멍 — {scope}")
    if len(coverage.gaps) > _MAX_GAP_FINDINGS:
        hidden = len(coverage.gaps) - _MAX_GAP_FINDINGS
        total = sum(span for _, _, span in coverage.gaps)
        out.append(
            f"{coverage.name}: 구멍 {len(coverage.gaps)}건(합 {total}분) — "
            f"위 {_MAX_GAP_FINDINGS}건 외 {hidden}건은 리포트 JSON의 series_coverage 참조"
        )
    return out


# ---------------------------------------------------------------- 계열 발견

DEFAULT_FLOW_DIR = Path("data") / "flow_intraday"
DEFAULT_OPTION_CHAIN_DIR = Path("data") / "option_chain"
DEFAULT_TICK_DIR = Path("data") / "ticks"


def _timestamps_from(frame: pl.DataFrame | None, column: str) -> list[datetime]:
    if frame is None or frame.height == 0 or column not in frame.columns:
        return []
    return [ts for ts in frame[column].to_list() if isinstance(ts, datetime)]


def _read_parquet_day(directory: Path, day: date) -> pl.DataFrame | None:
    """`{directory}/{날짜}.parquet` — 읽기 실패는 None(판정 불가)으로 올린다."""
    path = directory / f"{day.isoformat()}.parquet"
    if not path.exists():
        return None
    try:
        frame = pl.read_parquet(path)
    except Exception:  # noqa: BLE001 — 한 계열이 못 읽혀도 나머지는 잰다
        return None
    if "ts_kst" in frame.columns:
        frame = frame.with_columns(pl.col("ts_kst").dt.convert_time_zone("Asia/Seoul"))
    return frame


def collect(
    day: date,
    symbol: str,
    *,
    window: tuple[datetime, datetime],
    flow_dir: Path = DEFAULT_FLOW_DIR,
    option_chain_dir: Path = DEFAULT_OPTION_CHAIN_DIR,
    tick_dir: Path = DEFAULT_TICK_DIR,
    expectations: "dict[str, Expectation] | None" = None,
) -> list[SeriesCoverage]:
    """그날 적재된 **모든** 계열의 커버리지.

    계열 목록은 디렉터리에서 **발견**한다 — 하드코딩하면 새 계열이 붙을 때 리포트가
    조용히 그것만 안 본다(이 프로젝트가 반복한 실패 형태 그 자체다). 디렉터리가 있다는
    것은 언젠가 그 계열을 모았다는 뜻이고, 오늘 파일이 없으면 그게 곧 판정 대상이다.

    `expectations`(`ops/series_expectation.for_day()`)를 주면 그날 계약을 실어 보낸다.
    **발견은 그대로 하고 판정만 계약에 따른다** — 계약에 없는 계열도 계속 발견되고
    필수로 판정된다. 계약을 발견의 필터로 쓰면 계약에서 빠뜨린 계열이 조용히 시야 밖으로
    나가고, 그게 이 모듈이 애초에 막으려던 상태다.
    """
    contract = expectations or {}
    out: list[SeriesCoverage] = []

    for base, label in ((flow_dir, "flow_intraday"), (option_chain_dir, "option_chain")):
        if not base.is_dir():
            continue
        for child in sorted(p for p in base.iterdir() if p.is_dir()):
            frame = _read_parquet_day(child, day)
            name = f"{label}/{child.name}"
            out.append(
                measure(
                    name,
                    _timestamps_from(frame, "ts_kst"),
                    window=window,
                    expectation=contract.get(name),
                )
            )

    # 틱은 시간대 조각이라 경로 조립이 다르다 — 전용 리더를 쓴다(`data/tick_archiver.py`).
    from messiah.data import tick_archiver

    try:
        ticks = tick_archiver.read_day(tick_dir, symbol, day)
    except Exception:  # noqa: BLE001
        ticks = None
    if ticks is not None or (Path(tick_dir) / symbol).is_dir():
        out.append(
            measure(
                "ticks",
                _timestamps_from(ticks, "ts_kst"),
                window=window,
                expectation=contract.get("ticks"),
            )
        )

    return out


def summarize(coverages: Sequence[SeriesCoverage]) -> list[str]:
    """사람이 읽는 표 — 정상인 계열도 한 줄씩 남긴다.

    정상까지 찍는 이유: "검사했는데 이상 없다"와 "그 계열을 아예 안 본다"가 구분돼야
    한다. 후자가 2026-08-06의 상태였고, 리포트는 그날 완벽하게 조용했다.
    """
    lines: list[str] = []
    for item in sorted(coverages, key=lambda c: c.name):
        if not item.measured:
            lines.append(f"    {item.name}: 판정 불가 — {item.detail}")
            continue
        # 미상장일은 ⊘ — ❌(사고)와도 ✅(정상 적재)와도 다르게 찍는다. 같은 기호를 쓰면
        # "오늘 안 모으는 것을 알고 안 모았다"와 "모으려 했는데 못 모았다"가 섞인다.
        if not item.expected:
            mark = "⊘" if item.rows == 0 else "❗"
            suffix = "" if item.rows == 0 else f" — 미상장인데 {item.rows}분치 수신"
            lines.append(f"    {item.name}: {item.expectation_note} {mark}{suffix}")
            continue
        if item.rows == 0:
            lines.append(f"    {item.name}: 0분 ❌ {item.detail}")
            continue
        mark = "✅" if not findings_for(item) else "⚠"
        lines.append(
            f"    {item.name}: 커버리지 {item.coverage_pct:.0f}% · "
            f"{item.rows}분 {item.first_kst}~{item.last_kst} "
            f"(카덴스 {item.cadence_minutes:.0f}분 · 머리 {item.head_gap_minutes:.0f}분 · "
            f"최장 구멍 {item.longest_gap_minutes:.0f}분) {mark}"
        )
    return lines
