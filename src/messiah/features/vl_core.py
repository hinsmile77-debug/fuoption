"""VL(변동성) 기저 Feature — Master Plan Ver 1.4 §2.3 (Ver 2.0 §9 W20~21 프로토타입 →
W22~23 확장 + FeatureEngine 결선).

VL 카테고리는 원래 16개(Ver 1.4)다. W20~21은 Regime AI의 HMM 입력으로 실제 필요한
`vl_vol_ratio` 1개만 구현했고, FeatureEngine에는 아예 연결돼 있지 않았다(Regime AI가
`build_observations()`로 직접 호출하는 별도 경로였음). 이번 라운드(W22~23, Ver 1.5 §3.5~3.6
15m/30m Expert의 VL 15% 배정 대응)에서 OHLCV만으로 계산 가능한 나머지 13개를 채우고,
`WINDOWED_FEATURES`/`STATEFUL_FEATURES` 레지스트리를 `px_core.py`와 같은 형태로 노출해
`features/engine.py`가 자동으로 계산·발행하도록 결선한다.

여전히 스코프 밖(2개, "상태형"): `vl_har_pred`(일/주/월 RV 회귀 예측)와 `vl_intraday_shape`
(현재 RV/시간대별 평균 RV)는 여러 거래일에 걸친 시간대별 통계가 필요한데, 이 프로젝트엔 아직
그 통계를 쌓는 인프라(세션별 시계열 저장소)가 없다 — Event Calendar 미구현으로 Regime AI의
이벤트 근접 규칙을 미룬 것과 같은 이유(capability_matrix.md 알려진 갭).

## 윈도우 세트 (Ver 1.4 §1.3, VL은 대부분 W-std)

```
W_FAST = (5, 20)        # vl_range_exp
W_STD  = (5, 20, 60)    # vl_rv/park/gk/yz/atr/atr_rel/semi_dn/semi_up/semi_ratio/jump
W_SLOW = (20, 60, 120)  # vl_vov, vl_squeeze
```

## 하위윈도우 판단(_INNER_SUBWINDOW=5)

`vl_vov`(변동성의 변동성)와 `vl_squeeze`(BB폭 백분위)는 정의상 "윈도우 안에서 하위 지표를
여러 시점에 굴려 그 분포를 본다" — 이중 윈도우 구조다. 표준 관례라면 하위윈도우를 20(BB
표준)으로 잡고 싶지만, 외부 윈도우가 W_SLOW 최댓값(120)일 때 `outer + inner`가
`features/engine.py`의 `_MAX_HISTORY`(130, px_hurst/px_accel 요구치 기준으로 이미 고정된
예산)를 넘어버린다(120+20=140>130) — 그러면 이 두 Feature의 120-윈도우 변형은 히스토리가
아무리 쌓여도 영원히 워밍업이 안 끝나는 죽은 칸이 된다. `_MAX_HISTORY`를 올리는 대신
하위윈도우를 5로 낮춰(120+5=125<130) 기존 예산 안에 맞춘다 — Ver 1.4가 하위윈도우 값을
못박지 않아 내린 판단(px_core.py의 ATR/RSI 단순화 판단과 같은 종류의 트레이드오프).

## 근사·단순화

- 분산 성분은 전부 모집단 분산(`statistics.pstdev`/`pvariance`) — `vl_vol_ratio`·px_core
  전반의 기존 관례를 그대로 따름(표준 문헌의 표본분산 n-1 대신).
- `vl_rv`는 Ver 1.4 원문 "√Σret²(연율화)"에서 연율화 계수를 적용하지 않는다 — Horizon마다
  하루 봉 수가 달라 통일된 연율화 상수가 없고(1m 405개 vs 30m 13개), 부스팅 트리는 단조
  스케일 변환에 불변이라 실익이 없다는 판단.
"""

from __future__ import annotations

import math
import statistics
from typing import Sequence

from messiah.core.messages import BarClosed
from messiah.features import px_core
from messiah.features.px_core import atr as _px_atr
from messiah.features.px_core import px_bb_width as _px_bb_width

Bars = Sequence[BarClosed]

W_FAST = (5, 20)
W_STD = (5, 20, 60)
W_SLOW = (20, 60, 120)

_DEFAULT_FAST_WINDOW = 5  # px_core.W_STD 최솟값 재사용 (vl_vol_ratio 전용)
_DEFAULT_SLOW_WINDOW = 20  # px_core.W_STD 최댓값 재사용 (vl_vol_ratio 전용)
_INNER_SUBWINDOW = 5  # vl_vov/vl_squeeze 하위윈도우 (모듈 docstring 참고)


# ---------------------------------------------------------------- 공용 헬퍼


def _windowed_log_rets(bars: Bars, window: int) -> list[float] | None:
    """최근 `window`개 **장중** 로그수익률 — 워밍업 부족·로그 불능이면 None.

    2026-08-20 F-G — 세션 경계 쌍을 세지 않는다. 이 헬퍼가 `vl_rv`·`vl_semi_*`·`vl_jump`·
    `vl_vov` 등 변동성 계열 전부의 입구이고, 야간 갭 하나가 그 전부를 위로 밀어 올린다.
    실현변동성이 실제보다 크면 포지션 사이징이 그만큼 작아진다 — 조용히 손해 보는 쪽이다.
    """
    return px_core.same_session_log_returns(bars, window)


# ---------------------------------------------------------------- W-std 계열


def vl_rv(bars: Bars, window: int) -> float | None:
    """실현변동성 — √Σ(로그수익률²)(연율화 미적용, 모듈 docstring 참고)."""
    log_rets = _windowed_log_rets(bars, window)
    if log_rets is None:
        return None
    return math.sqrt(sum(r * r for r in log_rets))


def vl_park(bars: Bars, window: int) -> float | None:
    """파킨슨(1980) 변동성 — 고저 레인지만 사용(갭 미반영). `window`개 봉이면 충분(직전봉
    불필요, vl_yz와 달리 오버나이트 항이 없음)."""
    if window < 1 or len(bars) < window:
        return None
    window_bars = bars[-window:]
    terms: list[float] = []
    for bar in window_bars:
        h, low = float(bar.h_ticks), float(bar.l_ticks)
        if h <= 0 or low <= 0:
            return None
        terms.append(math.log(h / low) ** 2)
    mean_sq = statistics.fmean(terms)
    return math.sqrt(mean_sq / (4 * math.log(2)))


def vl_gk(bars: Bars, window: int) -> float | None:
    """가먼-클래스(1980) 변동성 — 고저 레인지 + 시가/종가."""
    if window < 1 or len(bars) < window:
        return None
    window_bars = bars[-window:]
    terms: list[float] = []
    for bar in window_bars:
        o, h, low, c = (
            float(bar.o_ticks),
            float(bar.h_ticks),
            float(bar.l_ticks),
            float(bar.c_ticks),
        )
        if o <= 0 or h <= 0 or low <= 0 or c <= 0:
            return None
        terms.append(0.5 * math.log(h / low) ** 2 - (2 * math.log(2) - 1) * math.log(c / o) ** 2)
    mean_term = statistics.fmean(terms)
    if mean_term < 0:
        return None
    return math.sqrt(mean_term)


def vl_yz(bars: Bars, window: int) -> float | None:
    """양-장(Yang-Zhang, 2000) 변동성 — 오버나이트 갭 + 시가-종가 + Rogers-Satchell 성분의
    가중합(Ver 1.4: "갭 포함 OHLC 기반"). 오버나이트 항이 직전 봉 종가를 참조하므로
    `window`+1개 원본 봉이 필요."""
    if window < 2 or len(bars) < window + 1:
        return None
    window_bars = bars[-(window + 1) :]
    overnight: list[float] = []
    oc: list[float] = []
    rs: list[float] = []
    for prev, cur in zip(window_bars, window_bars[1:]):
        o, h, low, c, prev_c = (
            float(cur.o_ticks),
            float(cur.h_ticks),
            float(cur.l_ticks),
            float(cur.c_ticks),
            float(prev.c_ticks),
        )
        if o <= 0 or h <= 0 or low <= 0 or c <= 0 or prev_c <= 0:
            return None
        overnight.append(math.log(o / prev_c))
        oc.append(math.log(c / o))
        rs.append(math.log(h / o) * math.log(h / c) + math.log(low / o) * math.log(low / c))
    v_o = statistics.pvariance(overnight)
    v_c = statistics.pvariance(oc)
    v_rs = statistics.fmean(rs)
    k = 0.34 / (1.34 + (window + 1) / (window - 1))
    variance = v_o + k * v_c + (1 - k) * v_rs
    if variance < 0:
        return None
    return math.sqrt(variance)


def vl_atr(bars: Bars, window: int) -> float | None:
    """ATR — `px_core.atr()` 그대로 재사용(로직 중복 방지, models/labeling.py와 같은 원칙).
    px_core에는 `px_atr`이라는 이름의 Feature로 노출돼 있지 않아(내부 헬퍼로만 쓰임) 여기서
    처음 실제 Feature 컬럼이 된다."""
    return _px_atr(bars, window)


def vl_atr_rel(bars: Bars, window: int) -> float | None:
    """상대 ATR — ATR/종가."""
    atr_value = _px_atr(bars, window)
    if atr_value is None or not bars or bars[-1].c_ticks <= 0:
        return None
    return atr_value / float(bars[-1].c_ticks)


def vl_semi_dn(bars: Bars, window: int) -> float | None:
    """하방 준분산 — 음(-) 로그수익률만으로 계산한 실현변동성. 하락이 전혀 없던 구간이면
    0.0(결측이 아니라 유효한 값)."""
    log_rets = _windowed_log_rets(bars, window)
    if log_rets is None:
        return None
    return math.sqrt(sum(r * r for r in log_rets if r < 0))


def vl_semi_up(bars: Bars, window: int) -> float | None:
    """상방 준분산 — 양(+) 로그수익률만으로 계산한 실현변동성."""
    log_rets = _windowed_log_rets(bars, window)
    if log_rets is None:
        return None
    return math.sqrt(sum(r * r for r in log_rets if r > 0))


def vl_semi_ratio(bars: Bars, window: int) -> float | None:
    """준분산 비 — 하방/상방(하방 쏠림 정도). 상방 준분산이 0이면(구간 전체 비상승) 나눗셈
    불능으로 None(vl_vol_ratio와 동일 원칙 — 조용히 0/inf를 만들지 않는다)."""
    log_rets = _windowed_log_rets(bars, window)
    if log_rets is None:
        return None
    up = math.sqrt(sum(r * r for r in log_rets if r > 0))
    if up == 0:
        return None
    dn = math.sqrt(sum(r * r for r in log_rets if r < 0))
    return dn / up


def vl_jump(bars: Bars, window: int) -> float | None:
    """점프 성분 — 실현분산(Σret², vl_rv와 달리 제곱합 그대로, 단위 통일 위해 sqrt 안 함) −
    Bipower Variation. 이론상 비음수이나 소표본에서 수치적으로 음수가 나올 수 있어 0으로
    클립(표준 관행). Bipower Variation은 연속 수익률 쌍을 쓰므로 `window`>=2 필요."""
    if window < 2:
        return None
    log_rets = _windowed_log_rets(bars, window)
    if log_rets is None:
        return None
    rv_variance = sum(r * r for r in log_rets)
    abs_products = [abs(a) * abs(b) for a, b in zip(log_rets, log_rets[1:])]
    bv = (math.pi / 2) * (window / (window - 1)) * sum(abs_products)
    return max(rv_variance - bv, 0.0)


# ---------------------------------------------------------------- W-fast 계열


def vl_range_exp(bars: Bars, window: int) -> float | None:
    """레인지 확장 — 현재봉 레인지/직전 `window`개 봉(현재봉 제외) 평균 레인지. 기준선에
    현재봉을 포함하면 자기 자신과 비교하는 셈이라 제외한다."""
    if window < 1 or len(bars) < window + 1:
        return None
    current = bars[-1]
    current_range = float(current.h_ticks - current.l_ticks)
    baseline_bars = bars[-(window + 1) : -1]
    avg_range = statistics.fmean(float(b.h_ticks - b.l_ticks) for b in baseline_bars)
    if avg_range <= 0:
        return None
    return current_range / avg_range


# ---------------------------------------------------------------- W-slow 계열 (이중 윈도우)


def vl_vov(bars: Bars, window: int) -> float | None:
    """변동성의 변동성 — 하위윈도우(`_INNER_SUBWINDOW`) 실현변동성을 `window`개 시점에서
    굴려 그 표준편차를 낸다(모듈 docstring "하위윈도우 판단" 참고)."""
    inner = _INNER_SUBWINDOW
    if len(bars) < window + inner + 1:
        return None
    rv_series: list[float] = []
    for end in range(len(bars) - window, len(bars)):
        # **앞부분을 잘라서 넘기지 않는다** (2026-08-20 F-G 2단계).
        #
        # 종전엔 `bars[end - inner : end + 1]`로 딱 `inner + 1`봉만 넘겼다. 세션 경계를
        # 인식하게 된 뒤로는 그 조각 안에 경계가 하나만 있어도 인접쌍이 `inner - 1`개로
        # 줄어 `vl_rv`가 `None`을 내고, 그러면 `vl_vov` 전체가 `None`이 된다.
        # 30분봉 기준 하루 13봉이라 **거의 모든 조각이 경계를 문다.**
        #
        # 접두 전체를 넘기면 `vl_rv`가 끝에서부터 경계를 건너뛰며 `inner`개를 채운다 —
        # 걷는 비용은 `inner + 경계 수`라 잘라서 넘기던 때와 사실상 같다.
        rv = vl_rv(bars[: end + 1], inner)
        if rv is None:
            return None
        rv_series.append(rv)
    if len(rv_series) < 2:
        return None
    return statistics.pstdev(rv_series)


def vl_squeeze(bars: Bars, window: int) -> float | None:
    """압축 지표 — 현재 BB폭(`px_core.px_bb_width`, 하위윈도우 `_INNER_SUBWINDOW`)이 최근
    `window`개 시점의 BB폭 분포에서 차지하는 백분위(0~1). 낮을수록 압축(폭발 전조)."""
    inner = _INNER_SUBWINDOW
    if len(bars) < window + inner:
        return None
    series: list[float] = []
    for end in range(len(bars) - window, len(bars)):
        sub = bars[end - inner + 1 : end + 1]
        width = _px_bb_width(sub, inner)
        if width is None:
            return None
        series.append(width)
    current = series[-1]
    return sum(1 for v in series if v <= current) / len(series)


# ---------------------------------------------------------------- 상태형


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

    # 세션 경계 쌍 제외 (2026-08-20 F-G) — 위 `_windowed_log_rets`와 같은 규율.
    log_rets = px_core.same_session_log_returns(bars, slow_window)
    if log_rets is None:
        return None

    rv_slow = statistics.pstdev(log_rets)
    if rv_slow == 0:
        return None
    rv_fast = statistics.pstdev(log_rets[-fast_window:])
    return rv_fast / rv_slow


def _vol_ratio_for_engine(bars: Bars, _session: object) -> float | None:
    """`vl_vol_ratio`는 FeatureEngine의 다른 stateful 계산기와 달리 세션 상태가 필요 없다 —
    레지스트리 시그니처(`(bars, session) -> float | None`)만 맞추는 얇은 어댑터."""
    return vl_vol_ratio(bars)


# ---------------------------------------------------------------- FeatureEngine 레지스트리

WINDOWED_FEATURES: list[tuple[str, "callable[[Bars, int], float | None]", tuple[int, ...]]] = [
    ("vl_rv", vl_rv, W_STD),
    ("vl_park", vl_park, W_STD),
    ("vl_gk", vl_gk, W_STD),
    ("vl_yz", vl_yz, W_STD),
    ("vl_atr", vl_atr, W_STD),
    ("vl_atr_rel", vl_atr_rel, W_STD),
    ("vl_semi_dn", vl_semi_dn, W_STD),
    ("vl_semi_up", vl_semi_up, W_STD),
    ("vl_semi_ratio", vl_semi_ratio, W_STD),
    ("vl_jump", vl_jump, W_STD),
    ("vl_range_exp", vl_range_exp, W_FAST),
    ("vl_vov", vl_vov, W_SLOW),
    ("vl_squeeze", vl_squeeze, W_SLOW),
]

STATEFUL_FEATURES: list[tuple[str, "callable[[Bars, object], float | None]"]] = [
    ("vl_vol_ratio", _vol_ratio_for_engine),
]
