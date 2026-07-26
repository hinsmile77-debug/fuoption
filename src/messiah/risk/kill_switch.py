"""Kill Switch — 상주 감시자, Ver 1.1 §4-4, Ver 2.0 §5 (Ver 2.0 §9 W24~26).

"어떤 컴포넌트보다 단순하게 유지한다"(Ver 1.1 §4-4 원문 — "소화기는 복잡하면 안 된다") —
이 클래스는 4가지 트리거 신호(일일손실·데이터단절·수동·모델이상)만 받아 판정하고, 판정
이후는 이미 검증된 두 경로에 위임한다: `OrderGateway.halt()`(신규 주문 차단, Ver 2.0 §9
W24~26에서 추가)와 `sys.kill` 발행(전 컴포넌트 최우선 처리, `core/bus.py` 원칙).

트리거 = R2(일일손실 한도) + R11(데이터단절, **지속**) + 수동 버튼 + 모델 출력 이상
(Ver 2.0 §5 "Kill Switch 트리거 = R2 + R11(지속) + 수동 버튼 + 모델 출력 이상"). R11은
`RiskEngine`도 검사하지만(신규 진입 개별 차단), Kill Switch는 **지속된** 단절(더 긴 한도)만
전면 정지로 격상한다 — 순간적 단절로 전량 청산까지 가면 과잉 대응이라는 판단(설계 문서가
"(지속)"이라 괄호로 구분한 이유를 그대로 반영, `KillSwitchConfig.data_disconnect_limit_seconds`
기본값을 RiskEngine의 R11 한도보다 크게 잡는 건 호출자 재량).

## 발동 후 절차 (Ver 1.1 §4-4: "신규 주문 차단 → 전 포지션 청산 절차 → 사람 확인 전 재가동 금지")

1. `sys.kill` 발행 — 전 컴포넌트가 이미 이 토픽을 자동 구독한다(`core/bus.py` MessageBus.
   subscribe()가 `TOPIC_KILL`을 항상 포함).
2. `OrderGateway.halt()` — 신규 주문 차단.
3. `liquidate()`가 보유 포지션 전량을 반대매매 시장가 `OrderRequest`(kind=EMERGENCY)로
   변환해 반환 — **제출 자체는 호출자가 `OrderGateway.submit()`으로 한다**(Kill Switch도
   여전히 OrderGateway를 거친다, 계명 1 — 유일한 주문 경로 원칙에 예외는 없다).
4. 재가동은 `OrderGateway.resume(operator=...)`(사람 확인 후에만, 기존 구현 그대로) —
   이 클래스는 `reset()`으로 자신의 `triggered` 상태도 별도로 사람이 확인 후 풀어야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from messiah.broker.base import BrokerAccount, BrokerPosition
from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_KILL, BusLike
from messiah.core.messages import KillSignal, OrderKind, OrderRequest, Side


@dataclass(frozen=True)
class KillSwitchConfig:
    daily_loss_limit_pct: float = 2.0  # R2
    data_disconnect_limit_seconds: float = 30.0  # R11(지속) — Ver 2.0 §5 R11 원문 수치


class KillSwitch:
    def __init__(
        self,
        bus: BusLike,
        *,
        config: KillSwitchConfig | None = None,
        instance_id: str = "unset",
    ) -> None:
        self._bus = bus
        self._config = config or KillSwitchConfig()
        self._instance_id = instance_id
        self._triggered = False

    @property
    def triggered(self) -> bool:
        return self._triggered

    def reset(self, operator: str) -> None:
        """사람 확인 후에만 — `OrderGateway.resume()`과 짝을 이루는 재가동 절차(모듈
        docstring 4단계)."""
        mlog.log("KillSwitch", f"리셋(operator={operator})", triggered_by="reset")
        self._triggered = False

    async def evaluate(
        self,
        *,
        account: BrokerAccount,
        daily_start_equity: Decimal,
        data_age_seconds: float,
        manual: bool = False,
        model_anomaly: bool = False,
    ) -> bool:
        """4가지 트리거를 우선순위 없이(동시 위반 시 먼저 걸린 것 하나만 사유로 기록,
        모두 즉시 동일한 전면 정지로 이어지므로 우선순위 자체가 무의미) 검사한다. 이미
        발동 상태면 재평가 없이 즉시 True(중복 발행 방지)."""
        if self._triggered:
            return True

        reason: str | None = None
        trigger = ""
        if daily_start_equity > 0:
            loss_pct = float((daily_start_equity - account.total_equity) / daily_start_equity * 100)
            if loss_pct >= self._config.daily_loss_limit_pct:
                reason = f"R2 일일손실 {loss_pct:.2f}% ≥ {self._config.daily_loss_limit_pct}%"
                trigger = "R2"
        if reason is None and data_age_seconds >= self._config.data_disconnect_limit_seconds:
            reason = f"R11 데이터단절 {data_age_seconds:.0f}s 지속"
            trigger = "R11"
        if reason is None and manual:
            reason = "수동 정지 버튼"
            trigger = "manual"
        if reason is None and model_anomaly:
            reason = "모델 출력 이상"
            trigger = "model_anomaly"

        if reason is None:
            return False

        await self._trigger(reason, trigger)
        return True

    async def _trigger(self, reason: str, trigger: str) -> None:
        self._triggered = True
        mlog.log("KillSwitch", reason, triggered_by=trigger)
        await self._bus.publish(
            TOPIC_KILL,
            KillSignal(reason=reason, triggered_by=trigger, instance_id=self._instance_id),
        )

    def liquidate(self, positions: Sequence[BrokerPosition]) -> list[OrderRequest]:
        """보유 포지션 전량 반대매매 시장가 요청 목록(모듈 docstring — 제출은 호출자 책임)."""
        requests: list[OrderRequest] = []
        for position in positions:
            if position.qty == 0:
                continue
            side = Side.SHORT if position.qty > 0 else Side.LONG  # 반대매매로 플랫화
            requests.append(
                OrderRequest(
                    intent_id="kill-switch",
                    symbol=position.symbol,
                    kind=OrderKind.EMERGENCY,
                    side=side,
                    qty=abs(position.qty),
                    limit_price_ticks=None,  # 시장가 — "생존이 비용에 우선하는 유일한 순간"(§6)
                    ttl_ms=5_000,
                    risk_approved_by="kill-switch",
                )
            )
            mlog.log(
                "KillSwitchLiquidating",
                f"{position.symbol} {abs(position.qty)}계약 강제청산 주문 생성",
                symbol=position.symbol,
                qty=abs(position.qty),
            )
        return requests
