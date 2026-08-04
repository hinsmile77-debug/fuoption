"""Triple Barrier 레이블링 + 샘플 고유도(uniqueness) — Master Plan Ver 1.2 §3, Ver 2.0 §9 W12~13.

Trainer 파이프라인(Ver 1.6 §7.1) 2단계 "레이블 생성"에 해당한다 — Ver 1.6이 이 파일명을
`labeling.py`로 명시했다.

## Triple Barrier (Ver 1.2 §3.1~3.2)

진입 시점(=진입봉 확정시각, 완성봉 규율 Ver 1.2 §2.2)마다 3개의 벽을 세우고 먼저 닿는 벽이
레이블이 된다:
- 상단 배리어(진입가 + width_atr_mult×ATR) 터치 → +1
- 하단 배리어(진입가 − width_atr_mult×ATR) 터치 → −1
- 시간 배리어(H봉 경과, `BARRIER_PARAMS`가 Horizon별 표를 그대로 인코딩) 도달 → 0

**동일 봉에서 상/하단이 동시에 터치되면 상단을 우선한다** — 완성봉(OHLC)만으로는 봉 내부에서
어느 벽에 먼저 닿았는지 알 수 없어 결정론적 타이브레이크가 필요하다. 실제로는 드문 경우이고
(배리어 폭이 ATR 기반이라 한 봉 안에서 양쪽을 다 뚫는 건 급변동뿐), 표본 통계에 미치는 영향은
작다고 보고 단순한 규칙을 택했다.

**비용 반영 강등**(Ver 1.2 §3.2): 터치가 발생해도 배리어까지의 이동폭(ret_ticks)이
`cost_ticks`(왕복 비용 추정) 이하면 레이블을 0으로 강등한다. `cost_ticks`는 이제(W14~16)
`risk/cost_model.py`의 `CostModel.estimate_round_trip_from_bars(...).total_ticks`가 정식
출처다(`models/trainer.py`가 실제 연결부) — 이 함수 자체는 여전히 숫자만 받고 CostModel을
직접 import하지 않는다(레이블링과 비용 추정의 관심사 분리). `barrier`/`ret_ticks` 필드는
원래 터치 정보를 그대로 보존하고 `cost_demoted=True`로만 표시한다 — 실제 시간배리어 도달과
구분해 진단 가능.

**워밍업·꼬리 트림**: ATR 계산에 필요한 만큼의 과거봉이 없는 진입, 또는 시간배리어까지의
미래봉이 시계열 끝에서 부족한 진입은 레이블을 만들지 않고 건너뛴다(결과를 끝까지 확정할 수
없는 표본은 애초에 만들지 않는다 — 결측치로 채우지 않음).

**이 레이블은 하류 판단 게이트와 결합돼 있다**(2026-08-04): flat 비율이 `meta_decision`의
우위 게이트(|S| >= 0.20) 도달 가능성을 산술적으로 정한다 — 교정된 모델의 |S|는
`1 − flat_share`를 넘을 수 없기 때문이다. 30m은 flat 76.3%라 천장이 0.237로 게이트 바로
위다. 배리어 폭(`_WIDTH_ATR_MULT`)을 만질 때는 반드시 `models/label_geometry.py`를 함께
돌릴 것 — 그 결합을 판정하는 도구다.

## 샘플 고유도 (Ver 1.2 §3.3)

겹치는 레이블(동시에 열려 있는 여러 진입)은 정보가 중복된다. Lopez de Prado(2018) 평균
고유도를 그대로 따른다: 각 이벤트 구간 [t_start, t_end] 위의 매 격자점에서 그 순간 동시에
열려 있는 이벤트 수(동시성)의 역수를 취해 구간 전체로 평균한다. 격자점은 전체 레이블의
t_start·t_end를 합친 시각 집합(동시성이 바뀔 수 있는 지점은 어떤 구간의 경계뿐이므로 이
합집합이 정확한 격자다 — `compute_uniqueness()` 함수 docstring 참고). 부스팅 학습의
`sample_weight`로 그대로 전달할 값이다(Ver 1.6 §2.1).
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, Mapping, Sequence

from messiah.core.messages import BarClosed, Horizon, bar_confirm_time
from messiah.features.px_core import atr as compute_atr

BarrierSide = Literal["upper", "lower", "time"]

DEFAULT_ATR_WINDOW = 14  # Wilder 관례 기본값 — Ver 1.2 §3.2는 창 크기를 못 박지 않아 명시 선택


@dataclass(frozen=True)
class BarrierParams:
    time_barrier_bars: int  # 진입봉 기준 몇 봉 뒤에 시간배리어가 확정되는가
    width_atr_mult: float  # 상/하단 폭 = 이 값 × ATR


# Ver 1.2 §3.2 표는 시간배리어를 **분**으로 준다: 1m→3, 3m→9, 5m→15, 10m→30, 15m→45,
# 30m→90. 예전엔 그 표를 그대로 상수로 두고 여기서 봉 수로 나눴는데, 그러면 코드만 봐서는
# 안 보이는 사실이 하나 숨는다 — **그 분 수가 전부 봉 크기의 정확히 3배라, 봉 수로 환산하면
# 전 Horizon이 3봉으로 같아진다.** 그래서 봉 수를 정본으로 적는다(분 환산이 필요하면
# `label_geometry.time_barrier_minutes()`가 되돌려 준다).
#
# 왜 이게 중요한가(2026-08-04 실측): 터치 확률은 대략 폭/(σ√H)의 함수인데 H가 3봉으로
# 고정이면 Horizon 축은 **배리어 폭 축 하나로 붕괴**한다. 즉 flat 비율을 width_atr_mult
# 하나가 단조로 결정한다 — 5m(×1.0) 35.6% / 15m(×1.5) 64.3% / 30m(×2.0) 76.3%.
# "긴 Horizon일수록 flat이 많다"는 관측은 시장 성질이 아니라 이 표의 모양이었다.
#
# 값 자체는 바꾸지 않았다. 2026-08-04 A/B(피처·모델·분할 고정, 레이블만 교체, 드리프트
# 차감·겹침 보정)에서 폭을 낮춰 flat을 33%로 되돌린 변형이 30m에서 오히려 **나빠졌고**
# (초과손익 t 2.38 → 1.38), 15m에서는 어느 변형도 유의성에 못 미쳤다. 8개월 단일 상승
# 국면 표본으로 새 표를 확정할 근거가 없어, 구조만 드러내고 숫자는 보존한다.
# 진단은 `models/label_geometry.py`(`check_horizon_ladder()`)가 상시로 잡는다.
_TIME_BARRIER_BARS: dict[Horizon, int] = {
    Horizon.M1: 3,
    Horizon.M3: 3,
    Horizon.M5: 3,
    Horizon.M10: 3,
    Horizon.M15: 3,
    Horizon.M30: 3,
}
_WIDTH_ATR_MULT: dict[Horizon, float] = {
    Horizon.M1: 0.5,
    Horizon.M3: 0.8,
    Horizon.M5: 1.0,
    Horizon.M10: 1.2,
    Horizon.M15: 1.5,
    Horizon.M30: 2.0,
}

BARRIER_PARAMS: dict[Horizon, BarrierParams] = {
    h: BarrierParams(
        time_barrier_bars=_TIME_BARRIER_BARS[h],
        width_atr_mult=_WIDTH_ATR_MULT[h],
    )
    for h in Horizon
}


@dataclass(frozen=True)
class TripleBarrierLabel:
    symbol: str
    horizon: Horizon
    t_start: datetime  # 진입시각(진입봉 확정시각)
    t_end: datetime  # 판정시각(터치 또는 시간배리어 확정시각)
    entry_price_ticks: int
    label: int  # -1 / 0 / +1 (비용 강등 반영 후 최종값)
    barrier: BarrierSide  # 실제로 먼저 닿은 벽 — 강등돼도 원래 벽 정보를 보존
    ret_ticks: int  # 판정가 - 진입가 (부호 있음)
    cost_demoted: bool = False
    weight: float = 1.0  # compute_uniqueness()가 채우기 전엔 1.0(= 미가중)


def triple_barrier_labels(
    bars: Sequence[BarClosed],
    *,
    atr_window: int = DEFAULT_ATR_WINDOW,
    cost_ticks: float = 0.0,
    barrier_params: Mapping[Horizon, BarrierParams] | None = None,
) -> list[TripleBarrierLabel]:
    """
    입력: 단일 심볼·단일 Horizon의 완성봉 시퀀스(오래된 것 → 최신 순, FeatureEngine의 `bars`
         관례와 동일). 다른 심볼/Horizon이 섞여 있으면 ValueError.
    계산: 매 봉을 진입 후보로 훑으며 진입가(종가)·ATR 기반 배리어를 세우고, 그 Horizon의
         시간배리어 봉 수만큼 앞을 스캔해 먼저 닿는 벽으로 레이블을 확정한다. `weight`는
         전부 1.0(미가중) — 실제 가중치는 `compute_uniqueness()`를 따로 호출해 채운다(전체
         레이블 집합에 대한 전역 계산이라 이 함수 내부에서는 알 수 없음).
    """
    if not bars:
        return []
    symbol, horizon = bars[0].symbol, bars[0].horizon
    for bar in bars:
        if bar.symbol != symbol or bar.horizon != horizon:
            raise ValueError("triple_barrier_labels는 단일 심볼·단일 Horizon 시퀀스만 받는다")

    params = (barrier_params or BARRIER_PARAMS)[horizon]
    h_bars = params.time_barrier_bars

    labels: list[TripleBarrierLabel] = []
    for i, entry in enumerate(bars):
        window_atr = compute_atr(bars[: i + 1], atr_window)
        if window_atr is None:
            continue  # ATR 워밍업 부족

        forward = bars[i + 1 : i + 1 + h_bars]
        if len(forward) < h_bars:
            continue  # 시간배리어까지 판정할 미래봉 부족(꼬리 트림)

        entry_price = entry.c_ticks
        width = round(params.width_atr_mult * window_atr)
        upper, lower = entry_price + width, entry_price - width

        label, barrier, end_bar, exit_price = _resolve_barrier(forward, upper, lower)
        ret_ticks = exit_price - entry_price
        cost_demoted = label != 0 and abs(ret_ticks) <= cost_ticks
        if cost_demoted:
            label = 0

        labels.append(
            TripleBarrierLabel(
                symbol=symbol,
                horizon=horizon,
                t_start=bar_confirm_time(entry),
                t_end=bar_confirm_time(end_bar),
                entry_price_ticks=entry_price,
                label=label,
                barrier=barrier,
                ret_ticks=ret_ticks,
                cost_demoted=cost_demoted,
            )
        )
    return labels


def _resolve_barrier(
    forward: Sequence[BarClosed], upper: int, lower: int
) -> tuple[int, BarrierSide, BarClosed, int]:
    """먼저 닿는 벽을 찾는다 — 동일 봉에서 상/하단이 동시에 터치되면 상단 우선(모듈
    docstring 참고)."""
    for bar in forward:
        if bar.h_ticks >= upper:
            return 1, "upper", bar, upper
        if bar.l_ticks <= lower:
            return -1, "lower", bar, lower
    last = forward[-1]
    return 0, "time", last, last.c_ticks


def compute_uniqueness(labels: Sequence[TripleBarrierLabel]) -> list[float]:
    """
    계산: Ver 1.2 §3.3 평균 고유도. 격자점은 전체 레이블의 t_start·t_end를 합친 시각
         집합이다(t_start만 쓰면 어떤 레이블의 t_end가 다른 레이블의 t_start와 우연히도
         맞아떨어지지 않는 구간 — 예: 시계열 꼬리라 그 봉 자체는 진입 후보가 못 된 경우 —
         에서 동시성을 과소평가한다). 동시성이 구간 경계에서만 바뀌는 계단함수라는 성질상
         이 두 집합의 합집합이 정확한 격자다. 각 격자점의 동시성(그 시각을 포함하는 이벤트
         수)을 구한 뒤, 이벤트마다 자신이 덮는 격자점들의 1/동시성 평균을 가중치로 낸다.
    반환: `labels`와 같은 순서의 가중치 리스트(레이블 자체는 변경하지 않음 — 호출자가
         `dataclasses.replace(label, weight=w)`로 반영).
    """
    if not labels:
        return []
    grid = sorted({label.t_start for label in labels} | {label.t_end for label in labels})
    grid_index = {t: idx for idx, t in enumerate(grid)}

    concurrency = [0] * len(grid)
    spans: list[tuple[int, int]] = []
    for label in labels:
        start_idx, end_idx = grid_index[label.t_start], grid_index[label.t_end]
        spans.append((start_idx, end_idx))
        for idx in range(start_idx, end_idx + 1):
            concurrency[idx] += 1

    return [
        statistics.fmean(1.0 / concurrency[idx] for idx in range(start_idx, end_idx + 1))
        for start_idx, end_idx in spans
    ]


def label_and_weight(
    bars: Sequence[BarClosed],
    *,
    atr_window: int = DEFAULT_ATR_WINDOW,
    cost_ticks: float = 0.0,
    barrier_params: Mapping[Horizon, BarrierParams] | None = None,
) -> list[TripleBarrierLabel]:
    """Trainer 파이프라인 2단계 전체(Ver 1.6 §7.1) — 레이블 생성 + 고유도 가중치 반영까지
    한 번에. `triple_barrier_labels()` + `compute_uniqueness()`를 그대로 합성한 편의 함수.
    `cost_ticks`는 이제(W14~16) `risk/cost_model.py`의 `CostModel.estimate_round_trip_from_bars(
    bars, qty=...).total_ticks`를 그대로 넘기는 것이 정식 경로다 — 호출자가 직접 숫자를
    주는 이전 방식은 CostModel이 없던 W12~13 임시 상태였다(models/trainer.py가 실제
    연결부)."""
    labels = triple_barrier_labels(
        bars, atr_window=atr_window, cost_ticks=cost_ticks, barrier_params=barrier_params
    )
    weights = compute_uniqueness(labels)
    return [replace(label, weight=w) for label, w in zip(labels, weights)]


# ---------------------------------------------------------------- 변동성 타깃 (2026-08-04)


def forward_realized_volatility(
    bars: Sequence[BarClosed], *, horizon_bars: int
) -> list[float | None]:
    """bars[i] 시점에서 본 **다음 `horizon_bars`봉의 실현변동성** — 방향이 아닌 예측 대상.

    ## 왜 이 함수가 생겼나

    2026-08-04 피처 관문 실측: 137개 피처 중 **부호 있는 방향 정보를 담은 것은 단 하나도
    통과하지 못했다**(방향성 39개 × 3 Horizon = 117 판정 중 0). 통과한 것은 전부 무부호
    크기(`vl_atr`·`vl_rv`·`px_bb_width` …)나 시각이었고, |IC| 중앙값 격차가 3배였다.

    즉 지금 입력이 담고 있는 정보는 "어디로 가는가"가 아니라 **"얼마나 움직이는가"**다.
    이 함수는 그 관측을 검정 가능한 형태로 만든다 — 같은 피처를 변동성 타깃에 태워
    IC가 실제로 커지는지 재기 위한 것이다(Ver 1.3 §9의 변동성 예측 축과 같은 방향).

    ## 계산

        RV_i = sqrt( Σ_{k=1..N} r_{i+k}² ),   r_t = log(c_t / c_{t-1})

    창의 첫 수익률 `r_{i+1} = log(c_{i+1}/c_i)`는 bars[i] **이후**의 움직임이다 — 예측
    대상이므로 그게 맞다. 피처는 bars[i]까지만 보므로 미래 참조가 아니다.

    **연율화하지 않는다.** 관문이 쓰는 것은 순위상관(Spearman)이고 연율화는 단조 변환이라
    순위를 바꾸지 않는다 — 상수를 곱해 봐야 IC가 그대로다. 학습 타깃으로 쓸 때 스케일이
    필요하면 그때 호출측이 곱한다.

    반환: `bars`와 같은 길이. 창을 못 채우는 **마지막 `horizon_bars`개는 None**(꼬리 트림),
         종가가 0 이하라 로그를 못 취하는 구간도 None.
    """
    if horizon_bars < 1:
        raise ValueError(f"horizon_bars는 1 이상이어야 한다: {horizon_bars}")

    out: list[float | None] = []
    for i in range(len(bars)):
        end = i + horizon_bars
        if end >= len(bars):
            out.append(None)  # 창을 못 채운다 — 0이 아니라 "모른다"
            continue
        total = 0.0
        ok = True
        for k in range(i, end):
            prev_close, next_close = bars[k].c_ticks, bars[k + 1].c_ticks
            if prev_close <= 0 or next_close <= 0:
                ok = False
                break
            total += math.log(next_close / prev_close) ** 2
        out.append(math.sqrt(total) if ok else None)
    return out


def trailing_realized_volatility(
    bars: Sequence[BarClosed], *, horizon_bars: int
) -> list[float | None]:
    """bars[i] 시점에 **이미 알고 있는** 직전 `horizon_bars`봉의 실현변동성.

    `forward_realized_volatility()`의 거울상이다 — 그건 예측 **대상**이고 이건 예측의
    **기준선**이다. 변동성 군집(volatility clustering)이 금융 시계열의 가장 강건한 정형화된
    사실이라, "다음 N봉 변동성"의 상당 부분은 "직전 N봉 변동성" 하나로 설명된다.

    피처 관문이 이걸 필요로 하는 이유(2026-08-04): 변동성 축에서 137개 중 78개가 IC 0.4~0.67로
    통과했는데, 그 값이 **피처의 정보인지 지속성 그 자체인지** 구분이 안 된다. 이 값을
    통제변수로 넣고 부분상관을 재야 "기준선을 넘는 증분"이 보인다.

    계산: `sqrt( Σ_{k=i-N+1..i} r_k² )`, `r_k = log(c_k / c_{k-1})`.
         창의 마지막 수익률이 `log(c_i / c_{i-1})`이라 **bars[i]까지의 정보만** 쓴다 —
         미래 참조가 아니다.

    반환: `bars`와 같은 길이. 창을 못 채우는 **처음 `horizon_bars`개는 None**.
    """
    if horizon_bars < 1:
        raise ValueError(f"horizon_bars는 1 이상이어야 한다: {horizon_bars}")

    out: list[float | None] = []
    for i in range(len(bars)):
        start = i - horizon_bars
        if start < 0:
            out.append(None)
            continue
        total = 0.0
        ok = True
        for k in range(start, i):
            prev_close, next_close = bars[k].c_ticks, bars[k + 1].c_ticks
            if prev_close <= 0 or next_close <= 0:
                ok = False
                break
            total += math.log(next_close / prev_close) ** 2
        out.append(math.sqrt(total) if ok else None)
    return out
