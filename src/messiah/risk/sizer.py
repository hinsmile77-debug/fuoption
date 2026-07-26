"""Position Sizer — Ver 1.1 §4-3, Ver 2.0 §2 워크스루 (Ver 2.0 §9 W24~26).

책임: 승인된 의도의 크기 결정 — **Volatility Targeting × Fractional Kelly × 불확실성
페널티**(Ver 1.1 §4-3 원문). Ver 2.0 §2 예시("Vol Target × 1/4 Kelly × (1−0.11) → 미니
3계약")를 그대로 세 개의 곱으로 구현한다.

## 공식

```
risk_pct        = min(vol_target_pct, max_position_loss_pct)   # R1은 여기서 상한으로 강제(아래)
risk_budget_krw = equity × risk_pct
loss_per_contract_krw = stop_distance_ticks × tick_size × point_value_krw
vol_target_qty  = risk_budget_krw / loss_per_contract_krw
edge            = clip(2×confidence − 1, 0, 1)                  # 이진 배팅 근사 Kelly 엣지
kelly_scaled    = edge × fractional_kelly                       # 예: edge×0.25 = "1/4 Kelly"
qty             = floor(vol_target_qty × kelly_scaled × (1 − uncertainty))
```

## R1은 여기서 사이징 상한으로 강제한다

`risk/risk_engine.py` 모듈 docstring 참고 — `risk_pct`를 `vol_target_pct`와
`max_position_loss_pct`(R1, 계좌 2%) 중 **작은 값**으로 고정해, Vol Target 설정을 아무리
느슨하게 잡아도 단일 포지션 최대손실이 R1 한도를 구조적으로 못 넘게 한다.

## `edge` 근사 (알려진 갭)

정식 Kelly는 `f* = (p·b − q)/b`(b=평균이익/평균손실 비율)를 쓰지만, 실현 거래 통계가
아직 없어(Shadow/G2 이전이라 트랙레코드 부재) b를 추정할 수 없다 — 대칭 페이오프(b=1)를
가정한 `edge = 2p−1`로 단순화했다(p=confidence). 실제 페이오프 비율이 쌓이면(Self
Evaluation, Phase 5) 이 근사를 교체할 자리.

## `point_value_krw` (알려진 갭)

KOSPI200 미니선물의 지수 1pt당 금액은 공개적으로 50,000원(정규선물 250,000원의 1/5,
Holding Policy §1.1 "미니는 승수 1/5")로 알려져 있으나, 이 프로젝트가 KIS API로 직접
실측(계약승수 필드)한 값은 아니다(symbol_master.py가 이 필드를 아직 안 읽음) —
`instance.yaml`의 `futures_tick_size`와 같은 성격의 "실측 전 placeholder"(그 필드 docstring
참고).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from messiah.core import logging as mlog
from messiah.core.messages import DecisionIntent, OrderKind, OrderRequest, Side

DEFAULT_POINT_VALUE_KRW = Decimal("50000")  # 모듈 docstring "알려진 갭" 참고


@dataclass(frozen=True)
class SizerConfig:
    vol_target_pct: float = 2.0  # Volatility Targeting 목표 리스크(계좌 대비 %)
    max_position_loss_pct: float = 2.0  # R1 — Holding Policy §3 "단일 포지션 최대손실 2%"
    fractional_kelly: float = 0.25  # Ver 2.0 §2 예시 "1/4 Kelly" 기본값(Ver 1.1 범위 1/4~1/2)
    point_value_krw: Decimal = DEFAULT_POINT_VALUE_KRW
    min_qty: int = 1


class PositionSizer:
    def __init__(self, config: SizerConfig | None = None) -> None:
        self._config = config or SizerConfig()

    def size(
        self,
        *,
        intent: DecisionIntent,
        equity: Decimal,
        tick_size: Decimal,
        stop_distance_ticks: float,
    ) -> int:
        """반환: 계약수(0 이상 정수). 0이면 호출자가 주문을 만들지 않아야 한다(모듈
        docstring — Risk Engine 승인 후에도 사이징 결과 0계약은 정상 동작)."""
        if stop_distance_ticks <= 0:
            raise ValueError("stop_distance_ticks는 0보다 커야 함")
        if equity <= 0:
            return 0

        cfg = self._config
        risk_pct = min(cfg.vol_target_pct, cfg.max_position_loss_pct) / 100.0
        risk_budget_krw = equity * Decimal(str(risk_pct))
        loss_per_contract_krw = Decimal(str(stop_distance_ticks)) * tick_size * cfg.point_value_krw
        if loss_per_contract_krw <= 0:
            return 0

        vol_target_qty = float(risk_budget_krw / loss_per_contract_krw)
        edge = max(0.0, min(1.0, 2.0 * intent.confidence - 1.0))
        kelly_scaled = edge * cfg.fractional_kelly
        uncertainty_penalty = max(0.0, 1.0 - intent.uncertainty)
        raw_qty = vol_target_qty * kelly_scaled * uncertainty_penalty

        qty = math.floor(raw_qty)
        if qty < cfg.min_qty:
            mlog.log(
                "SizerZeroQty",
                f"사이징 결과 {qty}계약(raw={raw_qty:.3f}) — 주문 생성 안 함",
                symbol=intent.symbol,
                raw_qty=raw_qty,
                edge=edge,
                uncertainty_penalty=uncertainty_penalty,
            )
            return 0
        return qty

    def build_order_request(
        self,
        *,
        intent: DecisionIntent,
        qty: int,
        net_expected_return: Decimal,
        kind: OrderKind = OrderKind.ENTRY,
        limit_price_ticks: int | None = None,
        ttl_ms: int = 30_000,
        risk_approved_by: str = "risk_engine-v1",
    ) -> OrderRequest:
        if intent.side not in (Side.LONG, Side.SHORT):
            raise ValueError(f"방향 의도(LONG/SHORT)만 주문으로 변환 가능: side={intent.side}")
        return OrderRequest(
            intent_id=intent.msg_id,
            symbol=intent.symbol,
            kind=kind,
            side=intent.side,
            qty=qty,
            limit_price_ticks=limit_price_ticks,
            ttl_ms=ttl_ms,
            net_expected_return=net_expected_return,
            risk_approved_by=risk_approved_by,
        )
