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

    # 상위 구간이 하위 구간보다 이만큼은 나아야 "임계값이 의미 있다"고 본다. 미검증
    # 초기값 — 표본 2,500건에서 적중률 표준오차가 약 1%p이므로 그 3배쯤을 요구한다.
    MIN_EDGE_GAP = 0.03

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
    def is_informative(self) -> bool:
        return self.edge_gap >= self.MIN_EDGE_GAP

    @property
    def verdict(self) -> str:
        if not self.bins:
            return "표본 없음 — 판정 불가"
        if self.is_informative:
            return (
                f"|S|가 방향을 가른다 — 상위 구간 {self.top.hit_rate:.1%} vs 하위 "
                f"{self.bottom.hit_rate:.1%} (격차 {self.edge_gap:+.1%}). 임계값 조정이 의미 있다"
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
