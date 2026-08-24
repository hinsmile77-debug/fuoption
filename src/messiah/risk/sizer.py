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

## 0계약은 「정상 동작」이 아니라 **거리**다 (2026-08-24 · F-23)

`qty < min_qty`로 접힌 사이클은 종전에 `SizerZeroQty` 한 줄로 "정상 동작"이라 적히고
끝났다. 그 문구가 **18거래일 연속 주문 0건**을 눈멀게 했다 — 0이 몇 번 났는지는 셌지만
**1계약까지 얼마나 모자랐는지**를 아무도 재지 않아서, 문턱이 손에 닿을 거리인지 몇 배
떨어져 있는지를 판단할 근거가 없었다.

이제 매 0계약마다 두 값을 남긴다:

    # 1.0에 얼마나 가까웠나
    shortfall_ratio         = raw_qty / min_qty
    # 지금 조건에서 1계약이 되려면 edge가 얼마여야 하나
    edge_needed_for_min_qty = min_qty / (vol_target_qty × fractional_kelly × (1−uncertainty))

**문턱을 바꾸지 않는다.** 문턱 변경은 위험 성향을 바꾸는 변경이라 R18의 섀도 계측
20거래일이 선행이다. 이 축은 **그 20거래일을 시작시키는 계측**이다
(`configs/pending_verifications.yaml` — `order-path-live`).

세션이 끝나면 `log_session_summary()`가 건수와 최댓값을 `SizerZeroQtyStreak`(WARNING)
한 줄로 낸다 — 사이클마다 우는 대신 하루에 한 번 운다.

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
from typing import Any

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
        # 세션 누계 (F-23) — 사이클마다 우는 대신 세션 끝에 한 줄 낸다.
        self._zero_qty_count = 0
        self._shortfall_ratio_max: float | None = None
        self._shortfall_ratios: list[float] = []
        self._sized_calls = 0

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
        self._sized_calls += 1
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
            # **1계약까지 얼마나 모자랐나** (F-23). 건수만으로는 "손에 닿는 거리"와
            # "몇 배 떨어져 있음"이 구별되지 않는다 — 그 구별이 18거래일을 눈멀게 했다.
            shortfall_ratio = raw_qty / cfg.min_qty if cfg.min_qty > 0 else None
            denom = vol_target_qty * cfg.fractional_kelly * uncertainty_penalty
            edge_needed = (cfg.min_qty / denom) if denom > 0 else None
            self._zero_qty_count += 1
            if shortfall_ratio is not None:
                self._shortfall_ratios.append(shortfall_ratio)
                if self._shortfall_ratio_max is None or shortfall_ratio > self._shortfall_ratio_max:
                    self._shortfall_ratio_max = shortfall_ratio
            mlog.log(
                "SizerZeroQty",
                f"사이징 결과 {qty}계약(raw={raw_qty:.3f}) — 주문 생성 안 함",
                symbol=intent.symbol,
                raw_qty=raw_qty,
                edge=edge,
                edge_source=edge_source,
                uncertainty_penalty=uncertainty_penalty,
                vol_target_qty=vol_target_qty,
                kelly_scaled=kelly_scaled,
                shortfall_ratio=shortfall_ratio,
                edge_needed_for_min_qty=edge_needed,
                min_qty=cfg.min_qty,
            )
            return 0
        return qty

    def log_session_summary(self) -> dict[str, Any] | None:
        """세션 누계를 한 줄로 낸다 (F-23) — 0계약이 한 건도 없었으면 아무것도 안 낸다.

        **왜 세션 끝인가.** `SizerZeroQty`는 사이클마다 뜨므로 INFO다. 그런데 "오늘 하루
        내내 한 번도 1계약에 못 닿았다"는 사실은 INFO 서른 줄이 아니라 WARNING 한 줄로
        보여야 한다 — 18거래일 동안 전자만 있었고 후자가 없었다.

        반환: 낸 내용(리포트가 그대로 쓸 수 있게) 또는 None.
        """
        if self._zero_qty_count == 0:
            return None
        ratios = sorted(self._shortfall_ratios)
        p50 = ratios[(len(ratios) - 1) // 2] if ratios else None
        payload: dict[str, Any] = {
            "zero_qty_cycles": self._zero_qty_count,
            "sized_calls": self._sized_calls,
            "shortfall_ratio_max": self._shortfall_ratio_max,
            "shortfall_ratio_p50": p50,
        }
        best = self._shortfall_ratio_max
        mlog.log(
            "SizerZeroQtyStreak",
            f"세션 내 0계약 {self._zero_qty_count}건 — 1계약까지 최대 도달률 "
            + (f"{best:.3f}" if best is not None else "미측정"),
            **payload,
        )
        return payload

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
