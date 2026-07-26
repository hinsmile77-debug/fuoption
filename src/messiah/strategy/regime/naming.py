"""명명층 — HMM 상태 → Regime 의미 부여 (Ver 1.6 §3.1, Ver 2.0 §9 W20~21).

"상태별 사후 통계(평균 수익률·변동성)로 자동 라벨링 + 사람 검수"(Ver 1.6 §3.1)를 구현한다.
자동 라벨링만 여기서 하고, "사람 검수"는 `describe_labels()`가 만드는 사람이 읽을 수 있는
요약(상태별 통계+배정된 Regime)을 개발자가 로그/스모크 출력으로 확인하는 것으로 대신한다 —
UI·Registry를 통한 정식 검수 절차는 아직 없다(Command Center UI는 Phase 4, capability_matrix.md
알려진 갭).

**EVENT는 이 계층이 배정하지 않는다** — Ver 1.6 §3.1 명명층 목록도 "추세상승/추세하락/횡보/
고변동성" 4개만 언급한다. 이벤트(지표발표·만기)는 통계로 발견할 수 있는 패턴이 아니라 달력
기반이라 규칙층(`rules.py`)의 전유물이다.

## 라벨링 규칙 (사후 통계 → 4개 원형)

1. **고변동성**: 평균 `vol_ratio`가 전체 평균의 `HIGH_VOL_MULTIPLIER`배 이상인 상태 —
   가장 먼저 배정한다(규칙 원문 "vl_vol_ratio > 극단 임계 → 즉시 고변동성"과 같은 정신,
   통계층에서도 우선순위를 동일하게 둠).
2. **추세상승/추세하락**: 남은 상태 중 평균 수익률이 가장 큰(양수일 때만) 상태를 상승,
   가장 작은(음수일 때만) 상태를 하락으로.
3. **횡보**: 나머지 전부.

임계값(`HIGH_VOL_MULTIPLIER`)은 Ver 1.4/1.6이 구체 수치를 안 줘 판단으로 정함 — 사람 검수
대상 1순위.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Sequence

import numpy as np

from messiah.core.messages import BarClosed, Regime

HIGH_VOL_MULTIPLIER = 1.5  # 평균 대비 이 배수 이상이면 "뚜렷이 높다"고 판단(임계값, 사람 검수 대상)


def label_states(
    observations: np.ndarray,
    indices: Sequence[int],
    bars: Sequence[BarClosed],
    states: np.ndarray,
) -> dict[int, Regime]:
    """
    입력: `observations`/`indices`는 `hmm_model.build_observations()`의 출력, `states`는
         그 관측치들에 대한 `RegimeHMM.predict_states()` 결과(같은 순서·길이).
    계산: 상태별로 (그 상태에 배정된 관측치들의) 평균 봉대비 로그수익률과 평균 `vol_ratio`
         (observations의 3번째 열)를 낸 뒤 모듈 docstring의 3단계 규칙으로 라벨링한다.
    반환: 실제로 관측된 상태 전부에 대해 정확히 하나씩 배정 — 관측이 없는 상태(HMM은
         상태를 뒀지만 이 데이터로는 한 번도 방문 안 한 경우)는 결과에 없다.
    """
    returns_by_state: dict[int, list[float]] = defaultdict(list)
    vol_by_state: dict[int, list[float]] = defaultdict(list)
    for obs_pos, bar_idx in enumerate(indices):
        if bar_idx == 0:
            continue
        prev_close = bars[bar_idx - 1].c_ticks
        cur_close = bars[bar_idx].c_ticks
        if prev_close <= 0 or cur_close <= 0:
            continue
        state = int(states[obs_pos])
        returns_by_state[state].append(math.log(cur_close / prev_close))
        vol_by_state[state].append(float(observations[obs_pos][2]))

    stats = {
        state: (statistics.fmean(returns_by_state[state]), statistics.fmean(vol_by_state[state]))
        for state in returns_by_state
        if returns_by_state[state]
    }
    if not stats:
        return {}

    remaining = dict(stats)
    labels: dict[int, Regime] = {}

    overall_vol = statistics.fmean(vol for _, vol in stats.values())
    if len(remaining) > 1:
        high_vol_state = max(remaining, key=lambda s: remaining[s][1])
        if remaining[high_vol_state][1] >= overall_vol * HIGH_VOL_MULTIPLIER:
            labels[high_vol_state] = Regime.HIGH_VOL
            del remaining[high_vol_state]

    if remaining:
        up_state = max(remaining, key=lambda s: remaining[s][0])
        if remaining[up_state][0] > 0:
            labels[up_state] = Regime.TREND_UP
            del remaining[up_state]

    if remaining:
        down_state = min(remaining, key=lambda s: remaining[s][0])
        if remaining[down_state][0] < 0:
            labels[down_state] = Regime.TREND_DOWN
            del remaining[down_state]

    for state in remaining:
        labels[state] = Regime.RANGE

    return labels


def describe_labels(
    labels: dict[int, Regime],
    observations: np.ndarray,
    indices: Sequence[int],
    states: np.ndarray,
) -> str:
    """사람 검수용 요약 문자열 — 상태별 배정 Regime과 관측 횟수·평균 vol_ratio를 한 줄씩.
    모듈 docstring "자동 라벨링 + 사람 검수"의 검수 부분을 위한 최소 도구(UI 없음)."""
    counts: dict[int, int] = defaultdict(int)
    vol_sums: dict[int, float] = defaultdict(float)
    for obs_pos in range(len(indices)):
        state = int(states[obs_pos])
        counts[state] += 1
        vol_sums[state] += float(observations[obs_pos][2])

    lines = []
    for state in sorted(labels):
        n = counts[state]
        avg_vol = vol_sums[state] / n if n else float("nan")
        lines.append(
            f"state {state}: {labels[state].value} (관측 {n}건, 평균 vol_ratio {avg_vol:.3f})"
        )
    return "\n".join(lines)
