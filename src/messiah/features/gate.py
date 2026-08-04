"""피처 품질 관문 — Ver 1.4 §3 (2026-08-04 신설, F0-3).

    후보 등록(candidate) → ① 단독 검정 → ② 중복 검정 → ③ 생존 검정 → active
                                                          미달 시 → retired
         active → 중요도 붕괴/NaN 급증 감지 → quarantined (자동 격리)

## 왜 피처를 늘리기 **전에** 만드나

이 관문은 Ver 1.4가 처음부터 요구했지만 코드로 존재한 적이 없었다. 그동안은 121개 전부가
검정 없이 모델에 들어갔고, 그 결과가 두 번 드러났다:

- `px_ema_cross_60`·`px_macd_h_60`이 **프로덕션에서 항상 NaN**이었다. 요구 봉 수(180/139)가
  히스토리 용량(130)을 넘어 계산 자체가 불가능했는데, 매일 `nan_ratio=0.0165`(=2/121)가
  찍히는데도 "정상 수준"으로 읽혔다(2026-08-04 발견).
- 탐색공간이 데이터 규모를 앞질러 `min_data_in_leaf=1285`가 뽑혔고, 75그루가 전부 2-leaf
  그루터기가 되어 **모델이 구조적으로 학습 불가**였다(2026-08-03).

둘 다 "쓸모를 사후에 확인하지 않았다"는 한 가지 원인이다. 121 → 250개로 늘리면서 관문이
없으면 과적합만 증폭된다. 그래서 F1(EV)보다 이것이 먼저다.

## 겹침 보정 — 이 파일에서 가장 틀리기 쉬운 부분

Triple Barrier 레이블은 시간배리어가 N봉이라 **연속 레이블끼리 구간이 겹친다**. 겹친 표본을
독립으로 세면 유효표본이 N배 부풀고, t값은 √N배 부풀어 **잡음이 전부 유의해 보인다**.

이 프로젝트는 이미 그 함정을 한 번 밟았다: 2026-08-04 레이블 A/B에서 3봉 겹침을 보정하지
않자 전 변형이 유의해 보였고, `√3` 보정 후에야 실상이 드러났다. 여기서는 `label_overlap_bars`
로 그 보정을 **강제한다** — 기본값 1(겹침 없음)을 쓰려면 호출측이 명시적으로 그렇게 정해야
한다.

같은 세션에 `ScoreCalibration`이 165표본짜리 +4.2pp 차이를 "유의미"라고 판정한 사고도 있었다.
그래서 이 관문은 크기(`min_samples`)·유의성(`min_abs_t`)·효과크기(`min_abs_ic`)를 **전부**
요구한다. 셋 중 하나만 보면 반드시 잡음을 통과시킨다.

## 순위상관(Spearman)을 쓴다

Ver 1.4는 "정보계수(IC)"라고만 적었다. 피어슨이 아니라 순위상관을 쓰는 이유는 이 데이터가
팻테일이고(`px_kurt_r`이 그걸 재는 피처로 등록돼 있을 정도다), 레이블이 {-1,0,1} 이산값이라
선형성 가정 자체가 성립하지 않기 때문이다. 순위상관은 단조 관계만 보므로 둘 다 무해하다.

## 이 관문이 하지 않는 것

- **자동 격리·자동 재학습을 트리거하지 않는다.** 판정만 낸다 — 실제로 어느 피처를 뺄지는
  사람이 정한다(R18 "게이트·차단 로직 신설은 섀도 계측 20거래일 후 승격"의 정신).
- **생존 검정은 창이 부족하면 판정하지 않는다**(`SKIPPED`). 지금 이 프로젝트는 G1 창이
  하나뿐이라 3창 요구를 채울 수 없다 — 그때 "전부 탈락"으로 처리하면 관문이 데이터 부족을
  피처 결함으로 오역한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

import numpy as np

# Ver 1.4 §3 관문표 초기값. 전부 "초기값"이라고 원문에 적혀 있고, 실제 분포를 보기 전에는
# 이 숫자들이 맞는지 알 수 없다 — `scripts/run_feature_gate.py`가 실측을 찍는 이유다.
DEFAULT_MIN_ABS_IC = 0.02
DEFAULT_MAX_ABS_CORR = 0.9
DEFAULT_SURVIVAL_TOP_FRACTION = 0.8
DEFAULT_SURVIVAL_MIN_WINDOWS = 3

# Ver 1.4에 없는 항목 — 이 프로젝트의 실패 이력에서 나온 추가 요구.
DEFAULT_MIN_ABS_T = 2.0  # 2σ. 165표본 +4.2pp(0.8σ)를 "유의미"로 읽은 2026-08-04 사고 대응.
DEFAULT_MIN_SAMPLES = 100  # 이보다 적으면 IC 자체를 신뢰하지 않는다.
DEFAULT_MAX_NAN_RATIO = 0.5  # 절반 넘게 비면 그 피처가 아니라 그 피처의 정의가 문제다.


class FeatureStatus(str, Enum):
    """Ver 1.4 §1.1 `status` 어휘 그대로."""

    ACTIVE = "active"
    QUARANTINED = "quarantined"
    RETIRED = "retired"
    SKIPPED = "skipped"  # 판정에 필요한 데이터가 없었다 — 통과도 탈락도 아니다


@dataclass(frozen=True, slots=True)
class FeatureVerdict:
    name: str
    status: FeatureStatus
    reason: str
    nan_ratio: float
    n_used: int
    # 판정에 쓰인 통계량. 기준선이 주어지면 **부분상관(증분)**, 아니면 주변상관이다.
    ic: float | None = None
    t_stat: float | None = None
    # 기준선이 있을 때만 채운다 — 통제 전 주변상관. 둘을 나란히 봐야 "IC 0.67 중 기준선을
    # 빼면 얼마 남는가"를 읽을 수 있다.
    marginal_ic: float | None = None
    redundant_with: str | None = None
    survived_windows: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status.value,
            "reason": self.reason,
            "nan_ratio": self.nan_ratio,
            "n_used": self.n_used,
            "ic": self.ic,
            "t_stat": self.t_stat,
            "marginal_ic": self.marginal_ic,
            "redundant_with": self.redundant_with,
            "survived_windows": self.survived_windows,
        }


@dataclass(frozen=True, slots=True)
class GateReport:
    verdicts: tuple[FeatureVerdict, ...]
    n_samples: int
    label_overlap_bars: int
    n_survival_windows: int

    def by_status(self, status: FeatureStatus) -> tuple[FeatureVerdict, ...]:
        return tuple(v for v in self.verdicts if v.status is status)

    @property
    def active_names(self) -> tuple[str, ...]:
        return tuple(v.name for v in self.by_status(FeatureStatus.ACTIVE))

    @property
    def dead_names(self) -> tuple[str, ...]:
        """계산 자체가 안 되는 피처 — `px_ema_cross_60` 부류. **가장 먼저 볼 목록이다.**"""
        return tuple(v.name for v in self.verdicts if v.nan_ratio >= 1.0)

    def summary(self) -> str:
        counts = {s: len(self.by_status(s)) for s in FeatureStatus}
        parts = [f"{s.value} {counts[s]}" for s in FeatureStatus if counts[s]]
        return (
            f"{len(self.verdicts)}개 판정 (표본 {self.n_samples}, 겹침 "
            f"{self.label_overlap_bars}봉 보정) — " + " · ".join(parts)
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "n_features": len(self.verdicts),
            "n_samples": self.n_samples,
            "label_overlap_bars": self.label_overlap_bars,
            "n_survival_windows": self.n_survival_windows,
            "summary": self.summary(),
            "dead": list(self.dead_names),
            "verdicts": [v.to_dict() for v in self.verdicts],
        }


# ---------------------------------------------------------------- 통계 기본기


def average_ranks(values: np.ndarray) -> np.ndarray:
    """동순위는 평균 순위로 — Spearman의 표준 처리. 1-based로 돌려준다.

    동순위를 무시하면(단순 argsort 순번) 레이블처럼 값이 {-1,0,1} 세 종류뿐인 벡터에서
    상관이 입력 순서에 따라 달라진다 — 재현되지 않는 IC는 IC가 아니다.
    """
    order = np.argsort(values, kind="mergesort")  # 안정 정렬 — 동순위 처리의 전제
    ranks = np.empty(len(values), dtype=float)
    sorted_values = values[order]
    i = 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and sorted_values[j + 1] == sorted_values[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def _pearson(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 2:
        return None
    a_dev, b_dev = a - a.mean(), b - b.mean()
    denom = math.sqrt(float(a_dev @ a_dev) * float(b_dev @ b_dev))
    if denom == 0.0:
        return None  # 한쪽이 상수 — 상관이 **정의되지 않는다**(0이 아니다)
    return float(a_dev @ b_dev) / denom


def spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    """순위상관. 표본 2개 미만이거나 한쪽이 상수면 None(정의 불가)."""
    if len(a) != len(b):
        raise ValueError(f"길이 불일치: {len(a)} vs {len(b)}")
    if len(a) < 2:
        return None
    return _pearson(average_ranks(a), average_ranks(b))


# 잔차가 "사실상 0"인지 판정하는 상대 허용오차 — 원래 순위 산포 대비 비율.
#
# **이 상수가 없으면 부분상관이 거짓말을 한다** (2026-08-04, 테스트가 발견): 피처가 기준선과
# 순위가 같으면 잔차가 수치오차만 남는데(lstsq가 정확히 0을 못 낸다), 그 잡음 벡터 둘을
# 상관내면 **1.0**이 나온다. 즉 "기준선의 복사본"이 "완벽한 증분"으로 보고된다 — 이 관문이
# 잡으려는 것과 정확히 반대되는 오답이다.
_RESIDUAL_DEGENERATE_RATIO = 1e-9


def _rank_residuals(values: np.ndarray, baseline_ranks: np.ndarray) -> np.ndarray | None:
    """`values`의 순위를 기준선 순위들로 회귀한 **잔차**. 절편 포함.

    기준선이 설명하는 부분을 걷어내고 남은 것만 돌려준다 — 부분상관의 핵심 연산이다.

    실패 조건(둘 다 None): 설계행렬 퇴화(기준선끼리 완전 공선성), 또는 **잔차가 사실상 0**
    (기준선이 이미 순위를 전부 설명 — `_RESIDUAL_DEGENERATE_RATIO` 주석 참고).
    """
    ranks = average_ranks(values)
    design = np.column_stack([np.ones(len(ranks)), baseline_ranks])
    try:
        coef, *_ = np.linalg.lstsq(design, ranks, rcond=None)
    except np.linalg.LinAlgError:
        return None
    residuals = ranks - design @ coef
    scale = float(np.std(ranks))
    if scale == 0.0 or float(np.std(residuals)) <= _RESIDUAL_DEGENERATE_RATIO * scale:
        return None  # 남은 게 수치오차뿐 — "증분 0"이 아니라 **잴 수 없다**
    return residuals


def partial_spearman(a: np.ndarray, b: np.ndarray, baselines: np.ndarray) -> float | None:
    """기준선을 통제한 순위 부분상관 — "`b`를 설명하는 데 `a`가 기준선 **너머로** 보태는가".

    ## 왜 필요한가 (2026-08-04)

    변동성 축 관문에서 137개 중 78개가 IC 0.4~0.67로 통과했다. 그런데 **변동성 군집**은
    금융 시계열에서 가장 강건한 정형화된 사실이라, 그 값이 피처의 정보인지 **지속성 그
    자체**인지 주변상관(marginal IC)만으로는 구분되지 않는다. `vl_atr_5`가 다음 3봉 RV를
    맞히는 것의 대부분이 "직전 3봉도 컸다"라면 그건 피처의 공이 아니다.

    통제변수(직전 RV의 HAR 구조 — 단기·중기·장기)를 넣고 **양쪽 다 잔차화**한 뒤 상관을
    재면, 남는 것이 기준선을 넘는 증분이다.

    ## 왜 모델을 적합하지 않는가

    HAR-RV 모델(`strategy/options/vol_forecast.py`)을 적합해 그 예측의 잔차를 쓸 수도 있다.
    안 쓴다 — 전 구간에 적합하면 **in-sample 과적합이 잔차에 그대로 섞이고**, 그러면 피처가
    "과적합이 못 맞힌 부분"을 맞히는지를 재게 된다. 순위 잔차화는 적합 없이 같은 통제를
    한다(파라미터를 추정하긴 하지만 순위 공간의 선형 사영이라 자유도가 기준선 수만큼으로
    고정된다).

    실패 조건: 표본 2개 미만, 잔차가 상수(기준선이 이미 완전히 설명), 설계행렬 퇴화 → None.
              **0이 아니라 None이다** — "증분이 0"과 "잴 수 없다"는 다른 사건이다.
    """
    if len(a) != len(b) or len(a) != len(baselines):
        raise ValueError(f"길이 불일치: a {len(a)} · b {len(b)} · baselines {len(baselines)}")
    if len(a) < 2:
        return None

    baseline_ranks = np.column_stack(
        [average_ranks(baselines[:, j]) for j in range(baselines.shape[1])]
    )
    res_a = _rank_residuals(a, baseline_ranks)
    res_b = _rank_residuals(b, baseline_ranks)
    if res_a is None or res_b is None:
        return None
    return _pearson(res_a, res_b)


def ic_t_stat(ic: float, n: int, *, overlap: int = 1) -> float | None:
    """IC의 t값 — **겹침 보정 포함**(모듈 docstring "겹침 보정").

    표준 상관 t검정 t = r·√((n−2)/(1−r²))에서 n을 유효표본 n/overlap으로 바꾼다.
    겹친 표본을 독립으로 세면 t가 √overlap배 부풀어 잡음이 유의해 보인다.
    """
    if overlap < 1:
        raise ValueError(f"overlap은 1 이상이어야 한다: {overlap}")
    effective_n = n / overlap
    if effective_n <= 2:
        return None
    if abs(ic) >= 1.0:
        return math.inf if ic > 0 else -math.inf
    return ic * math.sqrt((effective_n - 2.0) / (1.0 - ic * ic))


# ---------------------------------------------------------------- ① 단독 검정


def _finite_mask(column: np.ndarray) -> np.ndarray:
    return np.isfinite(column)


def screen_standalone(
    x: np.ndarray,
    y: np.ndarray,
    feature_names: Sequence[str],
    *,
    label_overlap_bars: int,
    baselines: np.ndarray | None = None,
    min_abs_ic: float = DEFAULT_MIN_ABS_IC,
    min_abs_t: float = DEFAULT_MIN_ABS_T,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    max_nan_ratio: float = DEFAULT_MAX_NAN_RATIO,
) -> list[FeatureVerdict]:
    """Ver 1.4 §3 ① 단독 예측력 — 피처별 IC를 레이블에 대해 잰다.

    입력: `x`는 (표본, 피처) 행렬로 결측은 NaN(엔진의 None이 그대로 온다), `y`는 레이블.
         `label_overlap_bars`는 시간배리어 길이(봉 수) — 겹침 보정에 쓴다.
    계산: 피처마다 그 피처가 유한한 행만 골라 Spearman IC와 겹침보정 t를 낸다.
    해석: NaN 비율이 먼저다 — 계산 자체가 안 되는 피처는 IC를 재봐야 의미가 없고, 그게
         `px_ema_cross_60` 부류다. 그 다음 크기·유의성·효과크기를 **전부** 요구한다.
    """
    if x.ndim != 2:
        raise ValueError(f"x는 2차원이어야 한다: {x.shape}")
    if x.shape[0] != len(y):
        raise ValueError(f"행 수 불일치: x {x.shape[0]} vs y {len(y)}")
    if x.shape[1] != len(feature_names):
        raise ValueError(f"열 수 불일치: x {x.shape[1]} vs 이름 {len(feature_names)}")

    y_arr = np.asarray(y, dtype=float)
    # 기준선이 있으면 그 열들이 전부 유한한 행만 쓴다 — 기준선 워밍업 구간에서는 "증분"이
    # 정의되지 않는다. `nan_ratio`는 여전히 **피처 자신의** 결측률이고(그게 피처의 성질이다),
    # `n_used`가 실제로 판정에 쓰인 행 수다. 둘이 다를 수 있다.
    base_arr = None if baselines is None else np.asarray(baselines, dtype=float)
    usable = np.isfinite(y_arr)
    if base_arr is not None:
        if base_arr.ndim != 2 or base_arr.shape[0] != len(y_arr):
            raise ValueError(
                f"baselines 모양 불일치: {base_arr.shape} (표본 {len(y_arr)}행이어야 한다)"
            )
        usable = usable & np.all(np.isfinite(base_arr), axis=1)

    verdicts: list[FeatureVerdict] = []

    for index, name in enumerate(feature_names):
        column = np.asarray(x[:, index], dtype=float)
        finite = _finite_mask(column)
        nan_ratio = 1.0 - (int(finite.sum()) / len(column)) if len(column) else 1.0
        mask = finite & usable
        n_used = int(mask.sum())

        if nan_ratio >= 1.0:
            verdicts.append(
                FeatureVerdict(
                    name,
                    FeatureStatus.QUARANTINED,
                    "전 구간 NaN — 계산 자체가 안 된다(윈도우 요구치가 히스토리 용량 초과 "
                    "또는 입력 결측). 모델에 넣으면 죽은 채로 학습된다",
                    nan_ratio,
                    n_used,
                )
            )
            continue
        if nan_ratio > max_nan_ratio:
            verdicts.append(
                FeatureVerdict(
                    name,
                    FeatureStatus.QUARANTINED,
                    f"NaN 비율 {nan_ratio:.1%} > 임계 {max_nan_ratio:.0%}",
                    nan_ratio,
                    n_used,
                )
            )
            continue
        if n_used < min_samples:
            verdicts.append(
                FeatureVerdict(
                    name,
                    FeatureStatus.SKIPPED,
                    f"유효표본 {n_used} < {min_samples} — 판정 불가(탈락이 아니다)",
                    nan_ratio,
                    n_used,
                )
            )
            continue

        marginal = spearman(column[mask], y_arr[mask])
        if base_arr is None:
            ic = marginal
            marginal_for_report = None
        else:
            ic = partial_spearman(column[mask], y_arr[mask], base_arr[mask])
            marginal_for_report = marginal
        if ic is None:
            verdicts.append(
                FeatureVerdict(
                    name,
                    FeatureStatus.QUARANTINED,
                    "IC 정의 불가 — 유효 구간에서 값이 상수이거나 기준선이 이미 전부 설명한다",
                    nan_ratio,
                    n_used,
                    marginal_ic=marginal_for_report,
                )
            )
            continue

        t_stat = ic_t_stat(ic, n_used, overlap=label_overlap_bars)
        if t_stat is None:
            verdicts.append(
                FeatureVerdict(
                    name,
                    FeatureStatus.SKIPPED,
                    f"겹침 보정 후 유효표본 {n_used / label_overlap_bars:.0f} — 판정 불가",
                    nan_ratio,
                    n_used,
                    ic=ic,
                    marginal_ic=marginal_for_report,
                )
            )
            continue

        if abs(ic) < min_abs_ic:
            reason = f"|IC| {abs(ic):.4f} < {min_abs_ic}"
            status = FeatureStatus.RETIRED
        elif abs(t_stat) < min_abs_t:
            reason = (
                f"|t| {abs(t_stat):.2f} < {min_abs_t} — 효과는 있으나 겹침 보정 후 잡음과 "
                f"구분 안 됨(유효표본 {n_used / label_overlap_bars:.0f})"
            )
            status = FeatureStatus.RETIRED
        else:
            reason = f"IC {ic:+.4f} · t {t_stat:+.2f}"
            status = FeatureStatus.ACTIVE

        verdicts.append(
            FeatureVerdict(
                name,
                status,
                reason,
                nan_ratio,
                n_used,
                ic=ic,
                t_stat=t_stat,
                marginal_ic=marginal_for_report,
            )
        )

    return verdicts


# ---------------------------------------------------------------- ② 중복 검정


def screen_redundancy(
    x: np.ndarray,
    feature_names: Sequence[str],
    verdicts: Sequence[FeatureVerdict],
    *,
    max_abs_corr: float = DEFAULT_MAX_ABS_CORR,
) -> list[FeatureVerdict]:
    """Ver 1.4 §3 ② 중복 제거 — |ρ| > 임계면 **예측력 낮은 쪽**이 탈락.

    ①을 통과한 피처끼리만 비교한다(원문 "기존 active Feature와"). 탈락한 피처와의 상관은
    볼 이유가 없고, 비교 쌍이 제곱으로 늘어나므로 비용도 그만큼 는다.

    비교는 두 피처가 **둘 다 유한한 행**에서 다시 순위를 매겨 계산한다 — 전역 순위를
    재활용하면 결측 패턴이 다른 두 피처 사이에서 값이 미묘하게 틀어진다.

    동률(|IC|가 같음)이면 이름 순으로 앞선 쪽을 남긴다 — 결정론적 타이브레이크가 없으면
    같은 데이터로 두 번 돌려 다른 피처셋이 나온다.
    """
    index_of = {name: i for i, name in enumerate(feature_names)}
    survivors = [v for v in verdicts if v.status is FeatureStatus.ACTIVE]
    # |IC| 큰 것부터 — 앞선 것이 남고 뒤엣것이 탈락하므로 이 순서가 곧 "예측력 낮은 쪽 탈락".
    survivors.sort(key=lambda v: (-abs(v.ic or 0.0), v.name))

    dropped: dict[str, str] = {}
    kept: list[FeatureVerdict] = []
    for candidate in survivors:
        col_c = np.asarray(x[:, index_of[candidate.name]], dtype=float)
        mask_c = _finite_mask(col_c)
        for keeper in kept:
            col_k = np.asarray(x[:, index_of[keeper.name]], dtype=float)
            both = mask_c & _finite_mask(col_k)
            if int(both.sum()) < 2:
                continue
            rho = spearman(col_c[both], col_k[both])
            if rho is not None and abs(rho) > max_abs_corr:
                dropped[candidate.name] = keeper.name
                break
        else:
            kept.append(candidate)

    out: list[FeatureVerdict] = []
    for verdict in verdicts:
        keeper_name = dropped.get(verdict.name)
        if keeper_name is None:
            out.append(verdict)
            continue
        out.append(
            FeatureVerdict(
                verdict.name,
                FeatureStatus.RETIRED,
                f"'{keeper_name}'와 |ρ| > {max_abs_corr} — 예측력 낮은 쪽 탈락",
                verdict.nan_ratio,
                verdict.n_used,
                ic=verdict.ic,
                t_stat=verdict.t_stat,
                marginal_ic=verdict.marginal_ic,
                redundant_with=keeper_name,
            )
        )
    return out


# ---------------------------------------------------------------- ③ 생존 검정


def screen_survival(
    verdicts: Sequence[FeatureVerdict],
    importances_by_window: Sequence[Mapping[str, float]],
    *,
    top_fraction: float = DEFAULT_SURVIVAL_TOP_FRACTION,
    min_windows: int = DEFAULT_SURVIVAL_MIN_WINDOWS,
) -> list[FeatureVerdict]:
    """Ver 1.4 §3 ③ 생존 검정 — Walk-Forward 여러 창에서 중요도 상위 `top_fraction`에
    `min_windows`회 이상 남았는가.

    **창이 부족하면 아무도 탈락시키지 않는다**(`SKIPPED`가 아니라 판정을 건너뛴다 — 기존
    판정을 그대로 돌려준다). 이 프로젝트는 지금 G1 창이 하나뿐이고, 그 상태에서 3창 요구를
    적용하면 관문이 **데이터 부족을 피처 결함으로 오역**해 전부 탈락시킨다.

    창별 중요도에 없는 이름은 그 창에서 상위권이 아니었던 것으로 본다(0으로 취급) — 학습이
    아예 안 쓴 피처가 "언급이 없으니 무해"로 통과하면 안 된다.
    """
    if len(importances_by_window) < min_windows:
        return list(verdicts)

    survived: dict[str, int] = {}
    for window in importances_by_window:
        if not window:
            continue
        ranked = sorted(window.items(), key=lambda kv: (-kv[1], kv[0]))
        cutoff = max(1, int(round(len(ranked) * top_fraction)))
        for name, _score in ranked[:cutoff]:
            survived[name] = survived.get(name, 0) + 1

    out: list[FeatureVerdict] = []
    for verdict in verdicts:
        if verdict.status is not FeatureStatus.ACTIVE:
            out.append(verdict)
            continue
        count = survived.get(verdict.name, 0)
        if count >= min_windows:
            out.append(
                FeatureVerdict(
                    verdict.name,
                    FeatureStatus.ACTIVE,
                    f"{verdict.reason} · 생존 {count}/{len(importances_by_window)}창",
                    verdict.nan_ratio,
                    verdict.n_used,
                    ic=verdict.ic,
                    t_stat=verdict.t_stat,
                    marginal_ic=verdict.marginal_ic,
                    survived_windows=count,
                )
            )
            continue
        out.append(
            FeatureVerdict(
                verdict.name,
                FeatureStatus.RETIRED,
                f"생존 {count}/{len(importances_by_window)}창 < {min_windows} — "
                f"한 구간에서만 반짝였다",
                verdict.nan_ratio,
                verdict.n_used,
                ic=verdict.ic,
                t_stat=verdict.t_stat,
                marginal_ic=verdict.marginal_ic,
                survived_windows=count,
            )
        )
    return out


# ---------------------------------------------------------------- 오케스트레이션


def run_gate(
    x: np.ndarray,
    y: np.ndarray,
    feature_names: Sequence[str],
    *,
    label_overlap_bars: int,
    baselines: np.ndarray | None = None,
    importances_by_window: Sequence[Mapping[str, float]] = (),
    min_abs_ic: float = DEFAULT_MIN_ABS_IC,
    min_abs_t: float = DEFAULT_MIN_ABS_T,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    max_nan_ratio: float = DEFAULT_MAX_NAN_RATIO,
    max_abs_corr: float = DEFAULT_MAX_ABS_CORR,
    survival_top_fraction: float = DEFAULT_SURVIVAL_TOP_FRACTION,
    survival_min_windows: int = DEFAULT_SURVIVAL_MIN_WINDOWS,
) -> GateReport:
    """①→②→③ 순서로 돌린다. 순서를 바꾸면 안 된다 — ②는 ① 생존자끼리만 비교하고,
    ③은 ②까지 살아남은 것만 본다(Ver 1.4 §3 흐름도 그대로).

    반환: 입력 `feature_names` 순서를 유지한 판정 목록. 호출측이 `active_names`를 그대로
         다음 학습의 피처 목록으로 쓸 수 있다.
    """
    verdicts = screen_standalone(
        x,
        y,
        feature_names,
        label_overlap_bars=label_overlap_bars,
        baselines=baselines,
        min_abs_ic=min_abs_ic,
        min_abs_t=min_abs_t,
        min_samples=min_samples,
        max_nan_ratio=max_nan_ratio,
    )
    verdicts = screen_redundancy(x, feature_names, verdicts, max_abs_corr=max_abs_corr)
    verdicts = screen_survival(
        verdicts,
        importances_by_window,
        top_fraction=survival_top_fraction,
        min_windows=survival_min_windows,
    )
    return GateReport(
        verdicts=tuple(verdicts),
        n_samples=int(x.shape[0]),
        label_overlap_bars=label_overlap_bars,
        n_survival_windows=len(importances_by_window),
    )
