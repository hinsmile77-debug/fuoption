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
edge            = <호출측이 계산해 넘긴다>       # 정본: pipeline._directional_edge()
kelly_scaled    = edge × fractional_kelly                       # 예: edge×0.25 = "1/4 Kelly"
qty             = floor(vol_target_qty × kelly_scaled × (1 − uncertainty))
```

## R1은 여기서 사이징 상한으로 강제한다

`risk/risk_engine.py` 모듈 docstring 참고 — `risk_pct`를 `vol_target_pct`와
`max_position_loss_pct`(R1, 계좌 2%) 중 **작은 값**으로 고정해, Vol Target 설정을 아무리
느슨하게 잡아도 단일 포지션 최대손실이 R1 한도를 구조적으로 못 넘게 한다.

## `edge`는 사이저가 계산하지 않는다 (2026-08-24 · F-22)

`edge`는 **호출측이 계산해 넘기는 필수 인자**다. 정본은 단 하나 —
`strategy/pipeline._directional_edge()`(`edge = p_favorable − p_adverse`).

**왜 사이저에서 뺐나.** 종전에는 사이저가 자기 몫으로 `edge = clip(2×confidence−1, 0, 1)`을
따로 계산했다. 2026-08-23에 파이프라인 쪽 산식만 3-클래스에 맞게 고쳐졌고(`d468402`),
사이저의 사본은 그대로 남아 **같은 개념이 두 곳에서 다른 값을 냈다** — 주문 직전 계층이
하필 옛 산식 쪽이었다(2026-08-24 이상점 1-10). 두 벌을 유지한 채 양쪽을 고치는 선택지는
기각한다. 사본을 **없애는 것**이 이 모듈의 처방이다.

기본값을 주지 않는 것도 같은 이유다 — 기본값이 있으면 `edge`를 안 넘긴 호출부가 조용히
옛 동작을 한다. 인자를 빠뜨리면 **호출 시점에 즉시 실패**해야 한다.

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
        edge: float,
        edge_source: str,
    ) -> int:
        """반환: 계약수(0 이상 정수). 0이면 호출자가 주문을 만들지 않아야 한다(모듈
        docstring — Risk Engine 승인 후에도 사이징 결과 0계약은 정상 동작).

        `edge`·`edge_source`는 **기본값 없는 필수 인자**다(F-22). 사이저는 우위를 스스로
        계산하지 않는다 — 정본은 `strategy/pipeline._directional_edge()` 하나뿐이고,
        `edge_source`는 그 값이 어느 산식에서 왔는지를 로그가 말하게 하는 라벨이다.
        다음에 또 두 벌로 갈라지면 로그가 먼저 말한다.
        """
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
        edge = max(0.0, min(1.0, edge))
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
                edge_source=edge_source,
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
