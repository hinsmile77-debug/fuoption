"""VL(변동성) 기저 Feature — Master Plan Ver 1.4 §2.1 (Ver 2.0 §9 W20~21).

VL 카테고리는 원래 30개(Ver 1.4)지만 이번 스코프는 Regime AI의 HMM 입력으로 실제 필요한
`vl_vol_ratio` 1개만 구현한다 — 나머지 29개는 여전히 스코프 밖(MS·EV와 같은 처지,
capability_matrix.md 알려진 갭). px_core.py처럼 카테고리별 파일을 분리해 둔다.

`vl_vol_ratio`(Ver 1.4: "변동성 비율 — RV_fast/RV_slow, 레짐 전환 감지, 상태형")는 외부
window를 안 받는 "상태형"이라 fast/slow 윈도우를 내부 상수로 고정한다 — Ver 1.4가 구체
값을 명시하지 않아 판단으로 정함(px_core.W_STD=(5,20,60)의 앞 두 값 5/20을 재사용 —
60을 slow로 쓰면 30분봉 기준 워밍업에만 30시간이 필요해 국면 감지가 지나치게 굼떠지므로
제외, `_DEFAULT_FAST_WINDOW`/`_DEFAULT_SLOW_WINDOW` 참고).
"""

from __future__ import annotations

import math
import statistics
from typing import Sequence

from messiah.core.messages import BarClosed

Bars = Sequence[BarClosed]

_DEFAULT_FAST_WINDOW = 5  # px_core.W_STD 최솟값 재사용
_DEFAULT_SLOW_WINDOW = 20  # px_core.W_STD 최댓값 재사용


def vl_vol_ratio(
    bars: Bars,
    fast_window: int = _DEFAULT_FAST_WINDOW,
    slow_window: int = _DEFAULT_SLOW_WINDOW,
) -> float | None:
    """
    계산: RV_fast/RV_slow — 로그수익률 표준편차의 비율. 1보다 크게 벗어나면 최근
         변동성이 장기 대비 급변했다는 뜻(레짐 전환 신호, Regime AI HMM 입력).
    실패 조건: `slow_window`+1개 미만 봉이면 워밍업 부족으로 None. RV_slow가 0(구간
         전체 무변동)이면 나눗셈 불능으로 None. `fast_window > slow_window`는 설정
         자체가 잘못된 것이라 ValueError(워밍업 부족과 달리 조용히 넘길 문제가 아님).
    """
    if fast_window > slow_window:
        raise ValueError("fast_window는 slow_window 이하여야 한다")
    if len(bars) < slow_window + 1:
        return None

    closes = [float(b.c_ticks) for b in bars[-(slow_window + 1) :]]
    log_rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    if len(log_rets) < slow_window:
        return None

    rv_slow = statistics.pstdev(log_rets)
    if rv_slow == 0:
        return None
    rv_fast = statistics.pstdev(log_rets[-fast_window:])
    return rv_fast / rv_slow
