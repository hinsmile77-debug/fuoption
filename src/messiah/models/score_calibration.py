"""통합 점수 S가 **방향을 예측하는가**를 재는 진단 (2026-08-04 신설).

## 왜 이 모듈이 생겼나

2026-08-04, `SCORE_THRESHOLD = 0.20`(Ver 2.0 §3.1)이 이 상품에 맞는 값인지 재검토하다가
질문 자체가 틀렸다는 걸 알았다. 임계값을 논하려면 먼저 **|S|가 클수록 실제로 잘 맞는가**가
성립해야 하는데, 재보니 아니었다:

    방향 적중률 — 상위 20% |S|: 50.6%   하위 20% |S|: 51.1%   전체: 52.4%

확신이 큰 구간이 작은 구간보다 **나을 게 없다**. 이러면 임계값을 어디에 두든 동전던지기를
고르는 것이라, 0.20을 낮추면 비용만 더 내고 더 많이 진다. **임계값은 문제가 아니었다.**

## S는 평균이 아니라 합이다 (같이 확인된 사실)

`aggregator.compute()`의 S는 Horizon에 대한 **가중 합**이다(Ver 1.2 §7.2 원문:
`S = Σ_h [w × (P_h(+1) − P_h(−1)) × meta_h × (1−u_h) × f_h]`). `agg_p_up`/`uncertainty`가
`total_weight`로 나누는 것과 달리 S만 안 나눈다. 그래서 임계 0.20은 **여러 Horizon이 함께
기여하는 상태**를 전제한 값이다 — 가중치 합이 국면별 2.6~6.2이므로 6개를 다 결선하면
Horizon당 평균 기여가 0.03만 돼도 임계를 넘는다.

다만 실측으로는 1개 → 3개로 늘려도 `|S| >= 0.20` 비율이 2.6% → 3.0%로 거의 안 늘었다.
Horizon 수가 원인이 아니라는 뜻이며, 위의 "적중률이 |S|와 무관하다"와 같은 이야기다.

## 쓰는 법

임계값을 **바꾸기 전에** 이걸 먼저 돌린다. `is_informative`가 False인데 임계값을 조정하는 건
동전던지기의 개수를 조절하는 것이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True, slots=True)
class ScoreBin:
    """|S| 구간 하나의 실적."""

    lower: float
    upper: float
    n: int
    hit_rate: float

    @property
    def label(self) -> str:
        return f"[{self.lower:.3f}, {self.upper:.3f})"


@dataclass(frozen=True, slots=True)
class ScoreCalibration:
    """|S| 구간별 방향 적중률 — "임계값을 둘 만한 신호인가"의 유일한 근거.

    `bins`는 |S| 오름차순이다. 판정에 쓰는 것은 **상·하위 구간의 차이**이지 전체 적중률이
    아니다: 전체가 52%여도 그 우위가 |S|와 무관하게 흩어져 있으면 임계값으로 건질 수 없다.
    """

    bins: tuple[ScoreBin, ...]
    overall_hit_rate: float
    n: int

    # 판정 기준 세 가지 — **셋을 다 넘어야** 한다 (2026-08-04, 자기 오탐 대응).
    #
    # 처음엔 `MIN_EDGE_GAP` 하나만 봤는데, FL 피처 A/B에서 이 도구가 오탐을 냈다:
    # 격차 +4.2%p로 "방향을 가른다"고 판정했지만 구간당 표본이 165건이라 격차의 표준오차가
    # 5.5%p였다(0.76 SE — 순수 잡음). 게다가 **상위 구간 적중률이 49.7%로 50% 미만**이었다.
    # 격차가 있어도 상위 구간이 동전던지기보다 나쁘면 거래할 수 없다 — "덜 나쁜 쪽"은
    # 우위가 아니다.
    MIN_EDGE_GAP = 0.03
    MIN_EDGE_SIGMA = 2.0  # 격차가 자기 표준오차의 이 배수는 넘어야 한다
    MIN_TOP_HIT_RATE = 0.50  # 상위 구간이 동전던지기보다는 나아야 한다

    @property
    def top(self) -> ScoreBin | None:
        return self.bins[-1] if self.bins else None

    @property
    def bottom(self) -> ScoreBin | None:
        return self.bins[0] if self.bins else None

    @property
    def edge_gap(self) -> float:
        """상위 구간 적중률 − 하위 구간 적중률. 0 이하면 |S|가 방향을 전혀 못 가른다."""
        if not self.top or not self.bottom:
            return 0.0
        return self.top.hit_rate - self.bottom.hit_rate

    @property
    def edge_gap_stderr(self) -> float:
        """격차의 표준오차 — 구간이 작으면 큰 격차도 잡음이다(적중률은 이항비율)."""
        if not self.top or not self.bottom or self.top.n == 0 or self.bottom.n == 0:
            return float("inf")
        var = sum(b.hit_rate * (1.0 - b.hit_rate) / b.n for b in (self.top, self.bottom))
        return var**0.5 if var > 0 else 0.0

    @property
    def edge_sigma(self) -> float:
        se = self.edge_gap_stderr
        if se == 0.0:
            return float("inf") if self.edge_gap > 0 else 0.0
        return self.edge_gap / se

    @property
    def is_informative(self) -> bool:
        """셋을 **다** 넘어야 한다 — 크기·유의성·유용성(위 상수 주석의 오탐 사례)."""
        return (
            self.edge_gap >= self.MIN_EDGE_GAP
            and self.edge_sigma >= self.MIN_EDGE_SIGMA
            and bool(self.top)
            and self.top.hit_rate > self.MIN_TOP_HIT_RATE
        )

    @property
    def verdict(self) -> str:
        if not self.bins:
            return "표본 없음 — 판정 불가"
        if self.is_informative:
            return (
                f"|S|가 방향을 가른다 — 상위 구간 {self.top.hit_rate:.1%} vs 하위 "
                f"{self.bottom.hit_rate:.1%} (격차 {self.edge_gap:+.1%}, {self.edge_sigma:.1f}σ). "
                f"임계값 조정이 의미 있다"
            )
        if self.top and self.edge_gap >= self.MIN_EDGE_GAP:
            if self.edge_sigma < self.MIN_EDGE_SIGMA:
                return (
                    f"격차 {self.edge_gap:+.1%}는 잡음 범위 — 구간당 표본이 작아 표준오차가 "
                    f"{self.edge_gap_stderr:.1%}다({self.edge_sigma:.1f}σ). 표본을 늘리기 전엔 "
                    f"우위가 있다고 말할 수 없다"
                )
            if self.top.hit_rate <= self.MIN_TOP_HIT_RATE:
                return (
                    f"격차는 {self.edge_gap:+.1%}지만 **상위 구간이 {self.top.hit_rate:.1%}로 "
                    f"동전던지기 이하**다 — 하위가 더 나쁠 뿐이고 거래할 우위는 없다"
                )
        return (
            f"|S|가 방향을 못 가른다 — 상위 구간 {self.top.hit_rate:.1%} vs 하위 "
            f"{self.bottom.hit_rate:.1%} (격차 {self.edge_gap:+.1%}). **임계값을 어디에 두든 "
            f"동전던지기를 고르는 것**이므로, 낮추면 비용만 더 낸다"
        )

    @classmethod
    def build(
        cls, scores: Sequence[float], correct: Sequence[bool], *, n_bins: int = 5
    ) -> "ScoreCalibration":
        """
        입력: `scores`는 통합 점수 S(부호 포함), `correct`는 그 판단의 방향이 맞았는지.
             `score == 0`인 표본은 판단 자체가 없었다는 뜻이라 제외한다.
        계산: |S| 기준 동일 개수 분위 구간으로 나눠 구간별 적중률을 낸다(동일 **폭**이 아니라
             동일 **개수** — |S| 분포가 0 근처에 심하게 몰려 있어 등폭이면 상위 구간의 표본이
             한 자릿수가 된다).
        실패 조건: 길이가 다르면 ValueError.
        """
        if len(scores) != len(correct):
            raise ValueError("scores와 correct 길이가 다르다")
        paired = sorted(
            ((abs(s), c) for s, c in zip(scores, correct) if s != 0), key=lambda x: x[0]
        )
        if not paired:
            return cls(bins=(), overall_hit_rate=0.0, n=0)

        n_bins = max(1, min(n_bins, len(paired)))
        edges = [round(i * len(paired) / n_bins) for i in range(n_bins + 1)]
        bins = []
        for i in range(n_bins):
            lo, hi = edges[i], edges[i + 1]
            if lo >= hi:
                continue
            chunk = paired[lo:hi]
            bins.append(
                ScoreBin(
                    lower=chunk[0][0],
                    upper=chunk[-1][0],
                    n=len(chunk),
                    hit_rate=sum(1 for _, c in chunk if c) / len(chunk),
                )
            )
        return cls(
            bins=tuple(bins),
            overall_hit_rate=sum(1 for _, c in paired if c) / len(paired),
            n=len(paired),
        )

    def format_lines(self) -> list[str]:
        out = [f"|S| 구간별 방향 적중률 (표본 {self.n}건, 전체 {self.overall_hit_rate:.1%})"]
        out += [f"  {b.label:<20} n={b.n:<6} 적중 {b.hit_rate:.1%}" for b in self.bins]
        out.append(f"  판정: {self.verdict}")
        return out
