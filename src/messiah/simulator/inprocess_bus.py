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

from messiah.core.bus import TOPIC_KILL
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

    async def subscribe(
        self, patterns: list[str], handler: Handler, *, on_kill: Handler | None = None
    ) -> None:
        """`on_kill`은 `MessageBus`와 **같은 계약**이다 (2026-08-07 P0-1).

        종전에 이 대역은 `on_kill`을 몰랐고 `TOPIC_KILL` 자동 구독도 안 했다 — 즉 재생·
        스모크에서는 kill 경로가 아예 안 돌았고, 그래서 2026-08-07 사고가 테스트에서
        재현되지 않았다. 두 버스가 다른 계약을 가지면 "재생에서 됐으니 라이브에서도
        되겠지"가 거짓이 된다(Ver 1.0.1 §2.1 "동일 인터페이스"의 요점).
        """
        for pattern in patterns:
            self._handlers.append((pattern, handler))
        if on_kill is not None:
            self._handlers.append((TOPIC_KILL, on_kill))
