"""Meta-Labeler 임계값이 **도달 가능한 값인가**를 재는 진단 (2026-08-04 신설).

## 왜 필요했나

2026-08-04 백필 후 처음으로 실제 8개월 데이터로 전략 계층을 돌렸더니, 판단은 매번
나오는데(`intel.futures` 1366건) **전부 NO_TRADE**였다. 집계 가중치
(`weight = 가중치표 × meta_h × (1−u_h) × f_h`)의 항을 하나씩 재보니 `meta_h`만 0이었다:

    Meta-Labeler 통과   : 0건 / 1006건 (0.0%)
    통과확률 최대치     : 0.5422
    선택된 임계값       : 0.6000

**검증 구간에서 임계값에 닿는 표본이 하나도 없다.** 그런데 `select_threshold()`는
"어떤 신호도 안 남는 임계값은 후보에서 제외"하므로, 그 임계값은 **선택 당시엔 도달
가능했다**. 두 사실이 함께 성립하는 유일한 설명은 선택에 쓴 확률과 추론에서 나오는
확률의 분포가 다르다는 것이다.

## 기전 — 임계값을 in-sample 확률로 고른다

`models/trainer.py`는 Meta-Labeler를 `meta_x`로 학습한 **직후 같은 `meta_x`로 예측**해
그 확률로 임계값을 고른다. Expert 쪽은 out-of-fold라 look-ahead가 없지만(`PurgedKFold`),
**메타 모델 자신의 예측은 자기 학습 행에 대한 것**이다. LightGBM은 학습 행을 잘 맞히므로
확률이 0/1 쪽으로 밀리고, 그 위에서 고른 임계값은 새 데이터에서 아무도 못 넘는 높이가 된다.

이 모듈은 그 격차를 **숫자로** 만든다 — "모델에 우위가 없다"와 "임계값이 과적합됐다"는
증상이 똑같이 무거래라서, 구분하려면 두 분포를 나란히 놓고 보는 수밖에 없다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Sequence

# 퇴화 판정 — **통과율 하나로만** 본다. 임계값의 명목 크기는 상관없다: 0.100이든 0.000이든
# 검증 표본이 전부 통과하면 그 게이트는 아무것도 거르지 않는다(2026-08-04 실측: 15m에서
# 임계 0.100인데 도달률 100%였고, 처음엔 "임계값이 0에 가까울 때만 퇴화"로 좁게 잡아
# 이걸 놓쳤다). 미검증 초기값.
_DEGENERATE_REACH_RATE = 0.99


@dataclass(frozen=True, slots=True)
class Distribution:
    """통과확률 분포 요약 — 분위수까지 두는 이유는 "최대치가 임계에 얼마나 못 미치나"가
    핵심 질문이라 평균만으로는 답이 안 나오기 때문이다."""

    n: int
    minimum: float
    p50: float
    p90: float
    p99: float
    maximum: float

    @classmethod
    def of(cls, values: Sequence[float]) -> "Distribution":
        if not values:
            raise ValueError("빈 표본으로는 분포를 만들 수 없다")
        ordered = sorted(values)

        def q(fraction: float) -> float:
            idx = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
            return ordered[idx]

        return cls(
            n=len(ordered),
            minimum=ordered[0],
            p50=statistics.median(ordered),
            p90=q(0.90),
            p99=q(0.99),
            maximum=ordered[-1],
        )


@dataclass(frozen=True, slots=True)
class ThresholdReport:
    """임계값 도달 가능성 진단.

    `selection_*`은 임계값을 고를 때 본 확률(현재 구현에선 in-sample),
    `inference_*`은 실제 추론에서 나오는 확률(검증 구간)이다.
    """

    threshold: float
    selection: Distribution
    inference: Distribution
    selection_reach_rate: float
    inference_reach_rate: float

    @property
    def headroom(self) -> float:
        """추론 최대치 − 임계값. 음수면 **어떤 표본도 임계값에 못 닿는다**(= 항상 무거래)."""
        return self.inference.maximum - self.threshold

    @property
    def is_unreachable(self) -> bool:
        return self.inference_reach_rate <= 0.0

    @property
    def is_degenerate(self) -> bool:
        """게이트가 **사실상 꺼진** 상태 — 임계값이 0에 붙고 거의 전부가 통과한다.

        무거래의 반대쪽 실패 모드다. `select_threshold()`는 비용차감 기대수익을 최대화하는데,
        **거르는 것이 아무 도움이 안 되면 전부 통과시키는 후보가 최댓값을 낸다** — 즉 임계값
        0.0은 "Meta-Labeler에 변별력이 없다"는 정직한 출력이지 좋은 결과가 아니다.

        2026-08-04 스윕에서 30m/oof가 정확히 이 모양이었다(임계 0.000, 도달률 100%, 신호
        421/421). 신호 수만 보면 최다였지만 그건 **게이트를 끈 것**이라, 신호 수를 기준으로
        설정을 고르면 가장 변별력 없는 설정을 고르게 된다.
        """
        return self.inference_reach_rate >= _DEGENERATE_REACH_RATE

    @property
    def verdict(self) -> str:
        """사람이 읽는 한 줄 판정 — 리포트가 스스로 결론을 말하게 한다(숫자만 뱉으면
        결국 매번 사람이 해석해야 한다, `ops/integrity_report.py`와 같은 원칙)."""
        if self.is_degenerate:
            return (
                f"게이트 무력화 — 임계 {self.threshold:.3f}에 추론 표본의 "
                f"{self.inference_reach_rate:.1%}가 통과한다. 임계값의 크기와 무관하게 "
                f"아무것도 거르지 않는 상태이며, 신호 수가 많은 것은 성과가 아니다"
            )
        if not self.is_unreachable:
            return (
                f"도달 가능 — 추론 표본의 {self.inference_reach_rate:.1%}가 임계 "
                f"{self.threshold:.3f}를 넘는다"
            )
        gap = -self.headroom
        if self.selection_reach_rate > 0.0:
            return (
                f"임계값 과적합 의심 — 선택 시엔 {self.selection_reach_rate:.1%}가 임계 "
                f"{self.threshold:.3f}를 넘었는데 추론에선 0%다(최대치가 {gap:.3f} 부족). "
                f"선택에 쓴 확률이 in-sample이면 이 격차가 그 증거다"
            )
        return (
            f"임계값 자체가 도달 불가 — 선택 시에도 추론에서도 0%다(최대치가 {gap:.3f} 부족). "
            f"임계값 선택 로직을 의심할 것"
        )

    @classmethod
    def build(
        cls,
        *,
        threshold: float,
        selection_probabilities: Sequence[float],
        inference_probabilities: Sequence[float],
    ) -> "ThresholdReport":
        """
        입력: `selection_probabilities`는 `select_threshold()`에 넘긴 바로 그 확률,
             `inference_probabilities`는 검증(미학습) 구간에서 나온 확률.
        실패 조건: 둘 중 하나라도 비면 ValueError — 빈 분포로 "과적합 아님"을 주장하지 않는다.
        """
        selection = Distribution.of(selection_probabilities)
        inference = Distribution.of(inference_probabilities)
        return cls(
            threshold=threshold,
            selection=selection,
            inference=inference,
            selection_reach_rate=_reach_rate(selection_probabilities, threshold),
            inference_reach_rate=_reach_rate(inference_probabilities, threshold),
        )

    def format_lines(self) -> list[str]:
        """콘솔 출력용 — 두 분포를 **같은 줄에 나란히** 둔다(격차가 한눈에 보여야 한다)."""

        def row(name: str, dist: Distribution, reach: float) -> str:
            return (
                f"  {name:<8} n={dist.n:<6} min={dist.minimum:.4f} p50={dist.p50:.4f} "
                f"p90={dist.p90:.4f} p99={dist.p99:.4f} max={dist.maximum:.4f} "
                f"임계도달={reach:.1%}"
            )

        return [
            f"Meta-Labeler 임계값 = {self.threshold:.4f}",
            row("선택 시", self.selection, self.selection_reach_rate),
            row("추론 시", self.inference, self.inference_reach_rate),
            f"  헤드룸(추론 최대 − 임계) = {self.headroom:+.4f}",
            f"  판정: {self.verdict}",
        ]


def _reach_rate(values: Sequence[float], threshold: float) -> float:
    if not values:
        return 0.0
    return sum(1 for v in values if v >= threshold) / len(values)
