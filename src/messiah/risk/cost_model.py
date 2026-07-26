"""Cost Model v1 — Ver 1.1 §4-1, Ver 1.0.1 §1.1, Ver 2.0 §9 W14~16.

책임(Ver 1.1 §4-1): 의도별 예상 비용 = 수수료 + 세금 + 슬리피지(스프레드 기반) + 시장충격
(주문크기/유동성 비율) — Meta 의도의 기대수익에서 이 값을 차감해 Net Expected Return을 낸다
(Ver 2.0 §2 워크스루: "왕복 예상비용 0.8틱 → Net ER = +2.1틱 > 0").

**v1의 근본 한계**: 슬리피지는 원래 "스프레드·호가잔량 기반"이어야 하지만 MESSIAH는 아직
호가(orderbook) WS를 구독하지 않는다(원시 틱 미적재와 동일 원인, capability_matrix.md 기존
갭). 실측 스프레드 대신 `expected_spread_ticks`(설정값)로 근사한다 — Ver 2.0 §6 "체결 품질
기록: 의도가격 대비 슬리피지를 전 주문에 기록 → Cost Model이 매주 자기 보정" 루프가 실제
체결 데이터로 이 값을 교정할 자리다. 시장충격은 완성봉의 실제 volume 필드로 계산 가능해
(호가 데이터 불필요) 근사가 아니라 구조적으로 정확하다. `CostModelConfig`의 다른 수치도
전부 실측 전 placeholder — capability_matrix.md "알려진 갭"에 기록.

이 모델의 출력(`estimate_round_trip().total_ticks`)은 두 자리에서 쓰인다:
- `models/labeling.py`의 `triple_barrier_labels(cost_ticks=...)` — 비용을 못 넘는 터치는
  0으로 강등(Ver 1.2 §3.2)
- `models/validator.py`의 비용차감 성과 지표(Ver 1.2 §8.3)
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Sequence

from messiah.core.messages import BarClosed


@dataclass(frozen=True)
class CostEstimate:
    commission_ticks: float
    tax_ticks: float
    slippage_ticks: float
    market_impact_ticks: float

    @property
    def total_ticks(self) -> float:
        return (
            self.commission_ticks + self.tax_ticks + self.slippage_ticks + self.market_impact_ticks
        )

    def __add__(self, other: CostEstimate) -> CostEstimate:
        return CostEstimate(
            commission_ticks=self.commission_ticks + other.commission_ticks,
            tax_ticks=self.tax_ticks + other.tax_ticks,
            slippage_ticks=self.slippage_ticks + other.slippage_ticks,
            market_impact_ticks=self.market_impact_ticks + other.market_impact_ticks,
        )


@dataclass(frozen=True)
class CostModelConfig:
    """전부 실측 전 placeholder(모듈 docstring 참고) — 편도(주문 1회) 기준, round-trip은 2배."""

    commission_ticks_per_contract: float = 0.3
    # K200 미니선물 거래세 실측 안 됨(파생상품은 비과세 구조가 흔하나 확인 전) — 확인 전까지 0
    tax_ticks_per_contract: float = 0.0
    expected_spread_ticks: float = 1.0  # 최우선 매수/매도 스프레드 근사(호가 WS 미구독)
    impact_coefficient: float = 2.0  # (qty/avg_volume) × 이 값 = 시장충격(틱)


class CostModel:
    def __init__(self, config: CostModelConfig | None = None) -> None:
        self._config = config or CostModelConfig()

    def estimate(self, *, qty: int, avg_volume: float) -> CostEstimate:
        """편도(진입 또는 청산 1회) 비용."""
        if qty <= 0:
            raise ValueError("qty는 1 이상이어야 한다")
        cfg = self._config
        return CostEstimate(
            commission_ticks=cfg.commission_ticks_per_contract * qty,
            tax_ticks=cfg.tax_ticks_per_contract * qty,
            slippage_ticks=cfg.expected_spread_ticks / 2,  # half-spread 관례 — 가격 속성, 수량 무관
            market_impact_ticks=self._market_impact(qty, avg_volume),
        )

    def estimate_round_trip(self, *, qty: int, avg_volume: float) -> CostEstimate:
        """왕복(진입+청산) 비용 — Triple Barrier 비용반영 강등(Ver 1.2 §3.2)·Net ER 계산 입력."""
        leg = self.estimate(qty=qty, avg_volume=avg_volume)
        return leg + leg

    def estimate_round_trip_from_bars(
        self, bars: Sequence[BarClosed], *, qty: int, volume_window: int = 20
    ) -> CostEstimate:
        """
        입력: `bars`는 시장충격 산정 시점까지의 완성봉(오래된 것 → 최신 순, px_core.py
             관례와 동일). 마지막 volume_window개의 평균 거래량으로 avg_volume을 근사한다.
        """
        recent = bars[-volume_window:]
        avg_volume = statistics.fmean(b.volume for b in recent) if recent else 0.0
        return self.estimate_round_trip(qty=qty, avg_volume=avg_volume)

    def _market_impact(self, qty: int, avg_volume: float) -> float:
        if avg_volume <= 0:
            # 유동성 데이터 없음(거래량 0 또는 이력 없음) — 침묵하지 않고 최대충격(비율 1.0
            # 가정)으로 보수적 처리(L18: 폴백은 조용하지 않게, 여기선 예외 대신 안전측 값).
            return self._config.impact_coefficient
        return self._config.impact_coefficient * (qty / avg_volume)
