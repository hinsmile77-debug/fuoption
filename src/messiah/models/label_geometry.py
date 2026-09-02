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

### 그 천장에 국면가중이 한 번 더 곱해진다 (2026-09-02에 추가)

위 항등식은 **가중치 1**을 가정한 것이다. 실제 S는 `aggregator.REGIME_WEIGHTS`가 곱해진
가중합이고, 라이브에서 기여 전문가는 30m 하나뿐이라 실효 천장은 `w(국면,30m) × (1−flat)`다:

    추세 1.5 × 0.236 = 0.355   고변동 0.8 × 0.236 = 0.190   횡보 0.4 × 0.236 = 0.095

게이트 0.20에 대해 추세는 열려 있고 **횡보·고변동은 닫혀 있다.** 즉 "30m은 여유 1.18배라
사실상 도달 불가"라던 종전 판정은 국면을 안 봐서 추세엔 비관적, 횡보엔 낙관적으로 **동시에
두 방향으로 틀려 있었다.** `RegimeReachability`가 이 절을 담당한다. 2026-08-21~09-02 라이브
123 사이클이 정확히 이 모양이다 — 게이트 통과 6건 전부 추세, 횡보 60건·고변동 34건 0건.

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

from messiah.core.messages import HORIZON_SECONDS, Horizon, Regime
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
class RegimeReachability:
    """**국면가중까지 곱한** |S| 천장 — 그 국면에서 게이트에 닿을 여지가 있는가.

    ## 왜 `LabelGeometry.score_ceiling`만으로는 절반이었나 (2026-09-02)

    `score_ceiling`은 `1 − flat_share`다. 그건 **가중치 1을 가정한** 천장이다. 그런데 판단
    엔진이 보는 S는 가중합이다(`aggregator.compute()`):

        S = Σ_h w(국면,h) × (P_h(+1) − P_h(−1)) × meta_h × (1 − u_h) × f_h

    라이브에서 기여 전문가는 사실상 30m 하나뿐이라(`n_experts=1`, 2026-08-21~09-02 전
    사이클) 실제 천장은 `w(국면, 30m) × (1 − flat_share)`다. 30m flat 76.4%면

        추세 1.5 × 0.236 = 0.355   고변동 0.8 × 0.236 = 0.190   횡보 0.4 × 0.236 = 0.095

    이고 게이트는 0.20이다 — **횡보·고변동은 모델 성능과 무관하게 닫혀 있다.** 종전 진단은
    가중치를 안 봤으므로 30m을 "천장 0.236 대 게이트 0.20, 여유 1.18배"라고만 말했다.
    그 문장은 추세 국면에 대해서는 지나치게 비관적이고(실제 0.355 = 1.78배, 도달 가능),
    횡보에 대해서는 지나치게 낙관적이다(실제 0.095 = 0.48배, 절반도 못 닿는다). **하나의
    숫자가 두 방향으로 동시에 틀렸다.**

    ## 두 천장을 함께 낸다

    - `solo` — 그 Horizon 하나만 기여할 때(= 지금의 라이브 실태).
    - `combined` — 가중치표의 전 Horizon이 동시에 기여할 때의 합(= 설계상 최대).

    둘을 함께 두는 이유는 "지금 닫혀 있다"와 "구조적으로 닫혀 있다"가 다른 사건이기
    때문이다. 전자는 번들을 더 띄우면 열리고, 후자는 폭이나 가중치를 바꿔야 열린다.
    """

    regime: Regime
    score_gate: float
    solo: dict[Horizon, float]  # Horizon별 w × (1 − flat_share)
    combined: float  # Σ_h w_h × (1 − flat_share_h)

    @property
    def best_solo(self) -> float:
        return max(self.solo.values(), default=0.0)

    @property
    def best_solo_horizon(self) -> Horizon | None:
        if not self.solo:
            return None
        return max(self.solo.items(), key=lambda item: item[1])[0]

    @property
    def solo_ratio(self) -> float:
        return self.best_solo / self.score_gate if self.score_gate > 0 else float("inf")

    @property
    def combined_ratio(self) -> float:
        return self.combined / self.score_gate if self.score_gate > 0 else float("inf")

    @property
    def gate_is_reachable_solo(self) -> bool:
        """전문가 한 종만 살아 있을 때 — **지금의 실태**."""
        return self.solo_ratio >= MIN_CEILING_RATIO

    @property
    def gate_is_reachable_combined(self) -> bool:
        return self.combined_ratio >= MIN_CEILING_RATIO

    @property
    def is_closed(self) -> bool:
        """천장이 게이트에 **아예 못 닿는다** — 여유 부족이 아니라 산술적 불가."""
        return self.best_solo < self.score_gate

    @property
    def verdict(self) -> str:
        horizon = self.best_solo_horizon
        where = f" ({horizon.value} 단독)" if horizon else ""
        if self.is_closed:
            return (
                f"**닫힘** — 실효 천장{where} {self.best_solo:.3f} < 게이트 "
                f"{self.score_gate:.2f}. 이 국면에서는 모델이 무엇을 예측하든 판단이 안 나간다"
                + (
                    f" (전 Horizon이 동시에 기여하면 {self.combined:.3f}까지 오르지만, 지금 "
                    "live 번들은 한 종뿐이다)"
                    if self.gate_is_reachable_combined
                    else ""
                )
            )
        if not self.gate_is_reachable_solo:
            return (
                f"여유 부족 — 실효 천장{where} {self.best_solo:.3f} / 게이트 "
                f"{self.score_gate:.2f} ({self.solo_ratio:.2f}배 < {MIN_CEILING_RATIO}). "
                "분포의 꼭짓점만 게이트에 닿는다"
            )
        return (
            f"도달 여지 있음 — 실효 천장{where} {self.best_solo:.3f} / 게이트 "
            f"{self.score_gate:.2f} ({self.solo_ratio:.2f}배)"
        )

    def format_lines(self) -> list[str]:
        solo = " · ".join(f"{h.value} {v:.3f}" for h, v in sorted(self.solo.items(), key=_h_key))
        return [
            f"[{self.regime.value}] 실효 천장 단독 {self.best_solo:.3f} "
            f"/ 전 Horizon 합 {self.combined:.3f} (게이트 {self.score_gate:.2f})",
            f"  Horizon별: {solo}" if solo else "  Horizon별: 가중치표에 항목 없음",
            f"  판정: {self.verdict}",
        ]


def _h_key(item: tuple[Horizon, float]) -> int:
    return HORIZON_SECONDS[item[0]]


def regime_reachability(
    geometries: Sequence[LabelGeometry],
    weights: Mapping[Regime, Mapping[Horizon, float]],
    *,
    score_gate: float = DEFAULT_SCORE_GATE,
) -> list[RegimeReachability]:
    """국면별 실효 천장 — `LabelGeometry`(레이블이 정한 천장) × 국면가중표.

    입력: `geometries`는 Horizon별 `LabelGeometry`(같은 Horizon이 둘이면 뒤엣것이 이긴다),
         `weights`는 `strategy/futures/aggregator.REGIME_WEIGHTS`를 그대로 넘긴다.
         **여기서 import하지 않는 이유는 `DEFAULT_SCORE_GATE` 주석과 같다** — 이 모듈은 두
         정의가 어긋난 것을 잡는 도구라, 한쪽을 직접 끌어다 쓰면 어긋남이 안 보인다. 대신
         호출부(`scripts/run_label_geometry.py`)가 둘을 마주 놓고, 배선은
         `tests/models/test_label_geometry.py`가 검사한다.
    실패 조건: 없다. 레이블이 0건인 Horizon은 천장 0으로 들어가고 판정이 "닫힘"이 된다
         (조용히 건너뛰면 그 Horizon이 건강한 것처럼 보인다).
    """
    ceiling_by_horizon = {g.horizon: g.score_ceiling for g in geometries}
    cards: list[RegimeReachability] = []
    for regime, table in weights.items():
        solo = {
            horizon: weight * ceiling_by_horizon[horizon]
            for horizon, weight in table.items()
            if horizon in ceiling_by_horizon
        }
        cards.append(
            RegimeReachability(
                regime=regime,
                score_gate=score_gate,
                solo=solo,
                combined=sum(solo.values()),
            )
        )
    return cards


def summarise_regime_reachability(cards: Sequence[RegimeReachability]) -> dict[str, object]:
    """화면·리포트가 읽을 요약 — `models/vol_scorecard.summarise()`와 같은 자리."""
    return {
        card.regime.value: {
            "score_gate": card.score_gate,
            "ceiling_solo": round(card.best_solo, 4),
            "ceiling_combined": round(card.combined, 4),
            "best_horizon": card.best_solo_horizon.value if card.best_solo_horizon else None,
            "ratio_solo": round(card.solo_ratio, 3),
            "closed": card.is_closed,
            "reachable_solo": card.gate_is_reachable_solo,
            "verdict": card.verdict,
        }
        for card in cards
    }


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
