"""Digital Twin 오케스트레이터 — 재생봉 → 버스 전파 → 체결 판정을 한 루프로 묶는다
(Master Plan Ver 2.0 §9 W9~11, "이후 모든 것의 시험장").

`ParquetBarReplaySource`가 만든 시간순 봉 시퀀스를 하나씩:
  1) `bus.publish("bar.{h}.{symbol}", bar)` — FeatureEngine 등 구독자에게 실제 운영과
     동일한 경로로 전파.
  2) `broker.on_bar(bar)` — 이 봉으로 pending 지정가 주문 체결/TTL 만료를 판정.
  3) 새로 발생한 Fill을 `OrderGateway.on_fill()`로 전달 — 실전과 동일하게 pending
     매칭·미매칭 CRITICAL 정지 경로를 그대로 통과한다.

Expert·Meta Decision 등 상위 전략 레이어는 아직 없다(Phase 3) — 이 엔진 자체는 전략을
포함하지 않고, 재생 중 임의 시점에 `gateway.submit()`을 호출하는 외부 콜러(테스트·향후
전략 코드)를 위한 배선만 제공한다.
"""

from __future__ import annotations

from typing import Sequence

from messiah.broker.simulator.adapter import SimBroker
from messiah.core.bus import TOPIC_BAR
from messiah.core.messages import BarClosed
from messiah.execution.order_gateway import OrderGateway
from messiah.simulator.inprocess_bus import InProcessBus


class DigitalTwinEngine:
    def __init__(
        self,
        symbol: str,
        broker: SimBroker,
        gateway: OrderGateway,
        bus: InProcessBus,
    ) -> None:
        self._symbol = symbol
        self._broker = broker
        self._gateway = gateway
        self._bus = bus

    async def run(self, bars: Sequence[BarClosed]) -> None:
        """
        입력: `ParquetBarReplaySource.load()`가 만든 확정시각 순 봉 시퀀스.
        계산: 이 심볼의 봉만 처리(다른 심볼이 섞여 들어와도 무시). 봉마다 버스 발행 →
             체결 판정 → 체결 결과를 게이트웨이로 전달, 순서를 고정한다(발행이 먼저인
             이유: FeatureEngine이 이 봉을 본 뒤에 그 봉으로 인한 체결도 반영돼야
             인과관계가 실제 운영과 같다).
        """
        for bar in bars:
            if bar.symbol != self._symbol:
                continue
            await self._bus.publish(f"{TOPIC_BAR}.{bar.horizon.value}.{bar.symbol}", bar)
            for fill in self._broker.on_bar(bar):
                await self._gateway.on_fill(fill)
