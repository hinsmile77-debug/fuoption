"""인메모리 버스 — Digital Twin용 `core.bus.MessageBus` 대역 (Ver 1.0.1 §2.1 "동일 인터페이스").

`publish()`/`subscribe()` 시그니처만 MessageBus와 맞추고 Redis 없이 프로세스 내부에서
즉시 디스패치한다. FeatureEngine 등 하위 소비자는 자신이 구독한 버스가 진짜 Redis인지
재생용 인메모리 버스인지 몰라도 코드 변경 없이 그대로 동작한다.

`subscribe()`는 (Redis 버전과 달리) 블로킹 루프가 아니라 핸들러를 등록하고 즉시 반환한다
— 재생은 실시간 대기가 필요 없으므로, `await engine.run_forever()`처럼 구독만 등록하고
끝나는 컴포넌트를 백그라운드 태스크로 따로 띄울 필요가 없다(publish 시점에 동기 디스패치).
"""

from __future__ import annotations

from typing import Awaitable, Callable

from messiah.core.messages import BusMessage

Handler = Callable[[BusMessage], Awaitable[None]]


class InProcessBus:
    def __init__(self, instance_id: str = "messiah-replay") -> None:
        self._instance_id = instance_id
        self._handlers: list[tuple[str, Handler]] = []

    async def publish(self, topic: str, msg: BusMessage) -> None:
        if msg.instance_id == "unset":
            msg = msg.model_copy(update={"instance_id": self._instance_id})
        for pattern, handler in self._handlers:
            if pattern == topic:
                await handler(msg)

    async def subscribe(self, patterns: list[str], handler: Handler) -> None:
        for pattern in patterns:
            self._handlers.append((pattern, handler))
