"""레이블 기하 진단 — 레이블 정의가 **하류 판단 게이트와 양립 가능한가**를 스스로 판정한다.

`score_calibration.py`(|S|가 방향을 가르나) · `threshold_report.py`(임계값이 도달 가능한가)와
같은 계열의 자기판정 도구다. 저 둘은 **학습이 끝난 뒤**에야 돌릴 수 있는데, 이 도구가 보는
결함은 레이블을 만든 순간 이미 확정돼 있다 — 학습 한 번 돌리기 전에 잡을 수 있다.

## 왜 필요했나 (2026-08-04 실측)

"15m flat 64% / 30m flat 76%라서 모델이 flat으로 수렴한다"는 진단에서 출발했는데, 재보니
인과가 한 단계 더 있었다.

### |S|의 상한은 flat 비율이 **산술적으로** 정한다

판단 엔진이 보는 점수는 단일 Horizon일 때 S = p_up − p_down이고, 확률이므로 항상

    |S| = |p_up − p_down| <= p_up + p_down = 1 − p_flat

이다. 교정기(`calibration.ProbabilityCalibrator`)가 하는 일이 바로 예측 확률의 평균을 실제
기저확률에 맞추는 것이므로, **교정이 제대로 되면 평균 p_flat은 레이블의 flat 비율로 수렴하고
|S|의 천장도 `1 − flat_share`로 내려앉는다.** 이건 모델이 좋고 나쁘고와 무관한 항등식이다.

실측이 정확히 그 모양이었다 — isotonic 교정 후 |S|의 p99:

    15m: flat 64.3% → 천장 0.357,  실측 p99 0.273
    30m: flat 76.3% → 천장 0.237,  실측 p99 0.246   ← 천장에 붙어 있다

그런데 `meta_decision`의 우위 게이트는 **절대상수 0.20**이다. 30m은 천장 0.237이 게이트
0.20 바로 위라 사실상 도달 불가고, 실제로 교정 후 게이트 통과율이 33.4% → **3.6%**로
무너졌다. 즉 "모델에 우위가 없어서 거래가 안 된다"로 보이던 증상의 상당 부분은 **레이블
flat 비율과 게이트 상수가 서로를 모른 채 정해진 결합 결함**이다.

### 다만 천장을 올리는 것만으로는 안 된다 (같은 날 함께 측정)

flat을 33%로 되돌린 레이블(width_atr_mult 0.9)은 천장이 0.67로 올라갔지만 게이트 통과율은
6.5%에 그쳤고, 드리프트 차감 성과는 오히려 나빠졌다. 모델의 실제 변별력이 약해 천장 근처에
가지도 못하기 때문이다. 그래서 이 도구는 flat 비율을 "고쳐야 할 값"으로 판정하지 않는다 —
**게이트에 도달할 여지가 구조적으로 있는지**만 판정한다(필요조건이지 충분조건이 아니다).

## 시간배리어가 전 Horizon에서 3봉으로 붕괴해 있다

Ver 1.2 §3.2 표는 시간배리어를 분으로 줬는데(1m→3분, 3m→9분, … 30m→90분) 그 분 수가
**모든 Horizon에서 정확히 봉 크기의 3배**다. 봉 수로 환산하면 전부 3봉이다. 즉 Horizon
사다리는 '시간의 사다리'가 아니라 **'배리어 폭의 사다리'**(width_atr_mult 0.5→2.0)뿐이고,
터치 확률이 폭/(σ√H)의 함수인데 H가 고정이므로 flat 비율은 배수 하나가 단조로 결정한다:

    5m(×1.0) 35.6%   15m(×1.5) 64.3%   30m(×2.0) 76.3%

`check_horizon_ladder()`가 이 붕괴를 잡는다. 표를 그대로 옮긴 것이 원인이라 코드만 봐서는
안 보였다.

## 비용 강등 규칙은 죽어 있다

`triple_barrier_labels(cost_ticks=...)`의 강등 규칙(Ver 1.2 §3.2)은 2025-12-12~2026-08-03
전 구간·전 Horizon에서 **한 건도 발동하지 않았다**. 배리어 폭 중앙값이 왕복 비용의 90~550배
이기 때문이다(5m 150틱 vs 1.6틱). 규칙이 틀린 게 아니라 **배리어가 비용과 아무 관계 없는
크기**라서 검사 자체가 무의미하다. `cost_rule_is_live`가 이걸 드러낸다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Mapping, Sequence

from messiah.core.messages import HORIZON_SECONDS, Horizon
from messiah.models.labeling import BarrierParams, TripleBarrierLabel

# `strategy/decision/meta_decision.py`의 우위 게이트(|S| < 이 값이면 NO_TRADE)와 같은 값.
# 여기서 import하지 않고 복제한 이유: 이 모듈은 **판단 엔진과 레이블이 서로 모른 채 정해져
# 어긋난 것**을 잡는 도구라, 한쪽이 바뀌었을 때 조용히 따라가면 그 어긋남 자체가 안 보인다.
# 값이 갈라지면 `test_label_geometry.py`가 잡는다.
DEFAULT_SCORE_GATE = 0.20

# 천장이 게이트의 이 배수는 돼야 "여지가 있다"고 본다. 1.0이면 천장과 게이트가 같다는
# 뜻이고, 그건 확률분포의 최댓값 하나만 게이트에 닿는다는 말이라 실질적으로 무거래다.
MIN_CEILING_RATIO = 1.5

# 배리어 폭이 왕복 비용의 이 배수를 넘으면 비용 강등 규칙이 사실상 장식이다.
MAX_BARRIER_COST_RATIO = 10.0


@dataclass(frozen=True, slots=True)
class LabelGeometry:
    """한 Horizon 레이블 집합의 기하 — "이 레이블로 학습한 모델이 게이트에 닿을 수 있나".

    성과 지표가 아니다. 여기서 통과해도 모델에 우위가 있다는 뜻은 전혀 아니고(그 판정은
    `score_calibration.ScoreCalibration`의 몫), 여기서 막히면 **모델이 아무리 좋아도**
    거래가 안 된다는 뜻이다.
    """

    horizon: Horizon
    n: int
    n_down: int
    n_flat: int
    n_up: int
    n_cost_demoted: int
    barrier_width_median_ticks: float  # 터치된 레이블의 |배리어까지 이동폭| 중앙값
    flat_abs_ret_median_ticks: float  # flat 레이블 안에 남은 |수익| 중앙값
    flat_above_cost_share: float  # flat 중 |수익| > 비용인 비율(0으로 뭉갠 방향 정보)
    cost_ticks: float
    score_gate: float = DEFAULT_SCORE_GATE

    # ------------------------------------------------------------------ 비율

    @property
    def flat_share(self) -> float:
        return self.n_flat / self.n if self.n else 0.0

    @property
    def score_ceiling(self) -> float:
        """교정된 모델이 낼 수 있는 |S|의 상한 = 1 − flat_share.

        |p_up − p_down| <= p_up + p_down = 1 − p_flat 이라는 항등식에서 나온다(모듈
        docstring). 교정기가 평균 p_flat을 기저확률에 맞추므로 이 값이 실질 천장이다.
        """
        return 1.0 - self.flat_share

    @property
    def ceiling_ratio(self) -> float:
        return self.score_ceiling / self.score_gate if self.score_gate > 0 else float("inf")

    @property
    def barrier_cost_ratio(self) -> float:
        return (
            self.barrier_width_median_ticks / self.cost_ticks
            if self.cost_ticks > 0
            else float("inf")
        )

    # ------------------------------------------------------------------ 판정

    @property
    def gate_is_reachable(self) -> bool:
        """게이트에 닿을 **여지**가 있는가 — 필요조건이지 충분조건이 아니다(모듈 docstring
        "천장을 올리는 것만으로는 안 된다")."""
        return self.ceiling_ratio >= MIN_CEILING_RATIO

    @property
    def cost_rule_is_live(self) -> bool:
        """비용 강등 규칙이 실제로 작동하는가. 한 건도 강등이 없고 배리어가 비용의
        `MAX_BARRIER_COST_RATIO`배를 넘으면 규칙은 장식이다."""
        if self.n_cost_demoted > 0:
            return True
        return self.barrier_cost_ratio <= MAX_BARRIER_COST_RATIO

    @property
    def is_healthy(self) -> bool:
        return self.gate_is_reachable and self.cost_rule_is_live

    @property
    def verdict(self) -> str:
        if not self.n:
            return "레이블 0건 — 판정 불가"
        parts: list[str] = []
        if not self.gate_is_reachable:
            parts.append(
                f"**게이트 도달 불가** — flat {self.flat_share:.1%}라 교정 후 |S| 천장이 "
                f"{self.score_ceiling:.3f}인데 게이트는 {self.score_gate:.2f}다"
                f"(여유 {self.ceiling_ratio:.2f}배 < {MIN_CEILING_RATIO}). 모델 성능과 무관하게 "
                f"거래가 구조적으로 막힌다 — 배리어 폭을 좁히거나 게이트를 분위수 기준으로 "
                f"바꿀 것"
            )
        else:
            parts.append(
                f"게이트 도달 여지 있음 — 천장 {self.score_ceiling:.3f} / 게이트 "
                f"{self.score_gate:.2f} ({self.ceiling_ratio:.2f}배). 다만 여지일 뿐이고 "
                f"실제 우위 판정은 ScoreCalibration의 몫이다"
            )
        if not self.cost_rule_is_live:
            parts.append(
                f"**비용 강등 규칙이 죽어 있다** — 강등 0건, 배리어 폭 중앙값 "
                f"{self.barrier_width_median_ticks:.0f}틱이 왕복 비용 {self.cost_ticks:.2f}틱의 "
                f"{self.barrier_cost_ratio:.0f}배다. 레이블이 요구하는 이동이 실제 수익성 "
                f"기준과 무관하다"
            )
        if self.flat_above_cost_share > 0.5:
            parts.append(
                f"flat {self.flat_share:.1%} 중 {self.flat_above_cost_share:.1%}는 |수익| "
                f"{self.flat_abs_ret_median_ticks:.0f}틱(중앙값)으로 비용을 넘는데도 0으로 "
                f"뭉개졌다 — 방향 정보를 버리고 있다"
            )
        return " / ".join(parts)

    def format_lines(self) -> list[str]:
        return [
            f"[{self.horizon.value}] 레이블 {self.n}건 "
            f"— down {self.n_down / self.n:.1%} / flat {self.flat_share:.1%} "
            f"/ up {self.n_up / self.n:.1%}",
            f"  |S| 천장 {self.score_ceiling:.3f} (게이트 {self.score_gate:.2f}, "
            f"{self.ceiling_ratio:.2f}배)",
            f"  배리어 폭 중앙 {self.barrier_width_median_ticks:.0f}틱 "
            f"= 비용의 {self.barrier_cost_ratio:.0f}배 · 비용강등 {self.n_cost_demoted}건",
            f"  판정: {self.verdict}",
        ]

    # ------------------------------------------------------------------ 생성

    @classmethod
    def build(
        cls,
        labels: Sequence[TripleBarrierLabel],
        *,
        cost_ticks: float,
        score_gate: float = DEFAULT_SCORE_GATE,
    ) -> LabelGeometry:
        """
        입력: 단일 Horizon의 `TripleBarrierLabel` 시퀀스(`labeling.label_and_weight()` 출력).
             `cost_ticks`는 그 레이블을 만들 때 쓴 값 그대로 — 다른 값을 넣으면
             `cost_rule_is_live` 판정이 무의미해진다.
        실패 조건: 없다. 빈 입력은 n=0으로 반환하고 `verdict`가 "판정 불가"라고 말한다
             (조용히 0으로 채워 건강한 것처럼 보이게 하지 않는다).
        """
        if not labels:
            return cls(
                horizon=Horizon.M1,
                n=0,
                n_down=0,
                n_flat=0,
                n_up=0,
                n_cost_demoted=0,
                barrier_width_median_ticks=0.0,
                flat_abs_ret_median_ticks=0.0,
                flat_above_cost_share=0.0,
                cost_ticks=cost_ticks,
                score_gate=score_gate,
            )
        touched = [abs(x.ret_ticks) for x in labels if x.barrier != "time"]
        flat_rets = [abs(x.ret_ticks) for x in labels if x.label == 0]
        return cls(
            horizon=labels[0].horizon,
            n=len(labels),
            n_down=sum(1 for x in labels if x.label == -1),
            n_flat=sum(1 for x in labels if x.label == 0),
            n_up=sum(1 for x in labels if x.label == 1),
            n_cost_demoted=sum(1 for x in labels if x.cost_demoted),
            barrier_width_median_ticks=statistics.median(touched) if touched else 0.0,
            flat_abs_ret_median_ticks=statistics.median(flat_rets) if flat_rets else 0.0,
            flat_above_cost_share=(
                sum(1 for r in flat_rets if r > cost_ticks) / len(flat_rets) if flat_rets else 0.0
            ),
            cost_ticks=cost_ticks,
            score_gate=score_gate,
        )


@dataclass(frozen=True, slots=True)
class HorizonLadder:
    """Horizon별 시간배리어가 **봉 수 기준으로도** 실제로 다른가.

    분 단위로 보면 3분~90분으로 30배 차이라 사다리처럼 보이지만, 각 Horizon은 자기 봉으로
    세므로 의미 있는 단위는 봉 수다. 전부 같은 봉 수면 Horizon 축이 사실상 배리어 폭 축
    하나로 붕괴한다(모듈 docstring).
    """

    bars_by_horizon: dict[Horizon, int]
    mult_by_horizon: dict[Horizon, float]

    @property
    def is_collapsed(self) -> bool:
        return len(set(self.bars_by_horizon.values())) <= 1

    @property
    def verdict(self) -> str:
        if not self.is_collapsed:
            bars = ", ".join(f"{h.value}={b}봉" for h, b in self.bars_by_horizon.items())
            return f"시간배리어가 Horizon별로 다르다 — {bars}"
        n = next(iter(self.bars_by_horizon.values()), 0)
        mults = ", ".join(f"{h.value}=×{m}" for h, m in self.mult_by_horizon.items())
        return (
            f"**Horizon 사다리 붕괴** — 시간배리어가 전 Horizon에서 {n}봉으로 같다. "
            f"분 단위 표(3~90분)가 사다리처럼 보였을 뿐 봉 수로는 동일하다. 남은 축은 "
            f"배리어 폭뿐이라({mults}) flat 비율이 그 배수 하나로 단조 결정된다"
        )


def check_horizon_ladder(barrier_params: Mapping[Horizon, BarrierParams]) -> HorizonLadder:
    """`labeling.BARRIER_PARAMS`를 그대로 받아 Horizon 축이 살아 있는지 본다."""
    return HorizonLadder(
        bars_by_horizon={h: p.time_barrier_bars for h, p in barrier_params.items()},
        mult_by_horizon={h: p.width_atr_mult for h, p in barrier_params.items()},
    )


def time_barrier_minutes(horizon: Horizon, params: BarrierParams) -> int:
    """시간배리어를 분으로 환산 — Ver 1.2 §3.2 표와 대조할 때만 쓴다(코드의 정본은 봉 수)."""
    return params.time_barrier_bars * HORIZON_SECONDS[horizon] // 60
