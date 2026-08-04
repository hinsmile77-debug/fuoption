"""FL(수급) Feature — Ver 1.5 §3.5 (2026-08-04 신설).

## 입력이 다른 카테고리와 다르다

PX/VL은 봉만 보면 계산된다. FL은 **봉 밖의 상태**(일별 투자자 순매수)가 필요하다 —
`data/investor_flow_history.FlowHistory`가 그 상태를 들고 있고, 계산기는
`(bars, flow, as_of)`를 받는다. `SessionState`를 받는 상태형 PX와 같은 모양이다.

## 일 단위라는 성질을 계산기가 숨기지 않는다

원천이 일별이라 하루 안에서는 값이 고정된다(5분봉 78개가 전부 같은 값). 그래서 여기 있는
피처들은 전부 **일간 축**의 양이다 — 누적·연속·표준화. 일중 타이밍을 만들어내는 척하는
계산은 넣지 않았다. 원천에 없는 정보를 피처가 만들어낼 수는 없다.

## 미래 참조

`FlowHistory.as_of()`/`recent()`가 **요청일보다 엄격히 이전**만 준다 —
`features/sidecar.DailySidecar` 계약이다. 이 파일의 계산기는 그 계약을 그대로 믿고 쓰며,
직접 날짜를 자르지 않는다 — 자르는 곳이 둘이면 한쪽만 고쳐지는 사고가 난다.

## 정규화

순매수는 계약 수/거래대금이라 절대 크기가 시기마다 다르다(2025년과 2026년의 시장 규모가
다르다). 그대로 쓰면 모델이 "최근인가"를 학습한다 — 전부 최근 N일 표준편차로 나눈
z-score나 부호 기반 양으로 낸다.
"""

from __future__ import annotations

import statistics
from datetime import date
from typing import Sequence

from messiah.core.messages import BarClosed
from messiah.data.investor_flow_history import FlowHistory, FlowRow

Bars = Sequence[BarClosed]

# 표준화 창 — 20거래일(약 1개월). 미검증 초기값이며, 더 짧으면 추세 자체가 정규화로
# 지워지고 더 길면 시장 규모 변화를 못 따라간다는 판단.
Z_WINDOW = 20

# 누적 창 — 5일(주간)·20일(월간). Ver 1.5 §3.5의 `fl_frgn_cum`이 이 축이다.
CUM_WINDOWS = (5, 20)

# 연속 부호를 세는 상한 — 이보다 길어도 값을 키우지 않는다(꼬리가 모델을 지배하지 않게).
STREAK_CAP = 10


def _as_of_day(bars: Bars) -> date | None:
    return bars[-1].bar_open_kst.date() if bars else None


def _zscore(values: Sequence[float]) -> float | None:
    """마지막 값의 z-score. 표본이 2개 미만이거나 표준편차가 0이면 **정의 불가**(None) —
    0으로 채우면 "평균과 같다"는 없는 사실을 주장하게 된다."""
    if len(values) < 2:
        return None
    sd = statistics.pstdev(values)
    if sd == 0:
        return None
    return (values[-1] - statistics.fmean(values)) / sd


def _series(rows: Sequence[FlowRow], field: str) -> list[float]:
    return [r.values[field] for r in rows if field in r.values]


def _z_of(bars: Bars, flow: FlowHistory, field: str) -> float | None:
    day = _as_of_day(bars)
    if day is None:
        return None
    return _zscore(_series(flow.recent(day, Z_WINDOW), field))


def _cum_z(bars: Bars, flow: FlowHistory, field: str, window: int) -> float | None:
    """최근 `window`일 누적 순매수를 `Z_WINDOW` 기준으로 표준화.

    누적값 자체는 단위가 계약 수라 시기별 비교가 안 된다 — 같은 창의 일별 표준편차로
    나눠 "평소 변동 대비 몇 배 쌓였나"로 만든다.
    """
    day = _as_of_day(bars)
    if day is None:
        return None
    recent = _series(flow.recent(day, Z_WINDOW), field)
    if len(recent) < window or len(recent) < 2:
        return None
    sd = statistics.pstdev(recent)
    if sd == 0:
        return None
    return sum(recent[-window:]) / (sd * window**0.5)


def _streak(bars: Bars, flow: FlowHistory, field: str) -> float | None:
    """같은 부호가 며칠 연속인가 — 부호 있는 값(매수 연속은 +, 매도 연속은 −).

    0인 날은 연속을 끊는다(중립도 흐름의 단절이다). `STREAK_CAP`에서 자른다.
    """
    day = _as_of_day(bars)
    if day is None:
        return None
    values = _series(flow.recent(day, STREAK_CAP), field)
    if not values:
        return None
    last = values[-1]
    if last == 0:
        return 0.0
    sign = 1.0 if last > 0 else -1.0
    count = 0
    for v in reversed(values):
        if (v > 0) == (last > 0) and v != 0:
            count += 1
        else:
            break
    return sign * min(count, STREAK_CAP)


def fl_frgn_ntby_z(bars: Bars, flow: FlowHistory) -> float | None:
    """외국인 순매수 수량의 z-score (최근 20일 기준)."""
    return _z_of(bars, flow, "frgn_ntby_qty")


def fl_orgn_ntby_z(bars: Bars, flow: FlowHistory) -> float | None:
    """기관계 순매수 수량의 z-score."""
    return _z_of(bars, flow, "orgn_ntby_qty")


def fl_prsn_ntby_z(bars: Bars, flow: FlowHistory) -> float | None:
    """개인 순매수 수량의 z-score — 외국인/기관과 반대로 움직이는 경향의 반대편 축."""
    return _z_of(bars, flow, "prsn_ntby_qty")


def fl_frgn_cum_5(bars: Bars, flow: FlowHistory) -> float | None:
    return _cum_z(bars, flow, "frgn_ntby_qty", 5)


def fl_frgn_cum_20(bars: Bars, flow: FlowHistory) -> float | None:
    return _cum_z(bars, flow, "frgn_ntby_qty", 20)


def fl_orgn_cum_5(bars: Bars, flow: FlowHistory) -> float | None:
    return _cum_z(bars, flow, "orgn_ntby_qty", 5)


def fl_frgn_streak(bars: Bars, flow: FlowHistory) -> float | None:
    return _streak(bars, flow, "frgn_ntby_qty")


def fl_orgn_streak(bars: Bars, flow: FlowHistory) -> float | None:
    return _streak(bars, flow, "orgn_ntby_qty")


def fl_frgn_orgn_agree(bars: Bars, flow: FlowHistory) -> float | None:
    """외국인·기관이 같은 방향인가 — +1(둘 다 매수) / −1(둘 다 매도) / 0(엇갈림).

    둘이 갈리는 날은 방향이 애매하다는 것이 시장 통념이고, 그 통념이 맞는지를 모델이
    직접 판단할 수 있게 별도 축으로 준다(두 z-score만으로는 트리가 교호작용을 배워야 한다).
    """
    day = _as_of_day(bars)
    if day is None:
        return None
    row = flow.as_of(day)
    if row is None:
        return None
    frgn, orgn = row.values.get("frgn_ntby_qty"), row.values.get("orgn_ntby_qty")
    if frgn is None or orgn is None or frgn == 0 or orgn == 0:
        return 0.0
    return 1.0 if (frgn > 0) == (orgn > 0) else -1.0


# `px_core.STATEFUL_FEATURES`와 같은 모양 — 이름과 계산기의 쌍. 엔진이 이 목록만 보고
# 전부 계산한다(신규 FL 피처는 여기 한 줄만 추가하면 된다).
FLOW_FEATURES: list[tuple[str, "callable[[Bars, FlowHistory], float | None]"]] = [
    ("fl_frgn_ntby_z", fl_frgn_ntby_z),
    ("fl_orgn_ntby_z", fl_orgn_ntby_z),
    ("fl_prsn_ntby_z", fl_prsn_ntby_z),
    ("fl_frgn_cum_5", fl_frgn_cum_5),
    ("fl_frgn_cum_20", fl_frgn_cum_20),
    ("fl_orgn_cum_5", fl_orgn_cum_5),
    ("fl_frgn_streak", fl_frgn_streak),
    ("fl_orgn_streak", fl_orgn_streak),
    ("fl_frgn_orgn_agree", fl_frgn_orgn_agree),
]
