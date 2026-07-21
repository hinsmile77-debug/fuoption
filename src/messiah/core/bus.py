"""Message Bus — Redis pub/sub + Streams 래퍼 (Ver 1.1 §4).

원칙:
- 모든 프로세스 간 통신은 이 모듈을 통해서만 (SYSTEM.md §4-2)
- 페이로드는 core/messages.py의 Pydantic 모델만 — encode/decode에 타입 레지스트리 사용
- 이력이 필요한 토픽(decision.*, capital.*, exec.*)은 pub/sub이 아니라 Streams(XADD, 재생 가능)
- sys.kill은 최우선: 구독자는 반드시 sys.kill을 함께 구독한다

코덱(encode/decode)은 Redis 없이도 테스트 가능하도록 분리되어 있다.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Type

from messiah.core import messages as m
from messiah.core.messages import BusMessage

# ---------------------------------------------------------------- 토픽 정의 (Ver 1.1 §4.2)

TOPIC_RAW = "raw"  # raw.{source}
TOPIC_TICK = "md.tick"  # md.tick.{symbol}
TOPIC_BAR = "bar"  # bar.{horizon}.{symbol} — 완성봉 확정
TOPIC_FEAT = "feat"  # feat.{horizon}
TOPIC_REGIME = "intel.regime"
TOPIC_FUTURES = "intel.futures"
TOPIC_OPTIONS = "intel.options"
TOPIC_INTENT = "decision.intent"  # Streams
TOPIC_ORDER_REQ = "capital.order_request"  # Streams
TOPIC_EXEC_ORDER = "exec.order"  # Streams
TOPIC_EXEC_FILL = "exec.fill"  # Streams
TOPIC_HEALTH = "sys.health"
TOPIC_KILL = "sys.kill"  # 최우선

STREAM_TOPICS: frozenset[str] = frozenset(
    {TOPIC_INTENT, TOPIC_ORDER_REQ, TOPIC_EXEC_ORDER, TOPIC_EXEC_FILL}
)

# ---------------------------------------------------------- 코덱 (서버 불필요 — 단위테스트 대상)

# 타입 레지스트리: 클래스명 -> 모델. 신규 메시지는 messages.py에 정의하면 자동 등록된다.
_TYPE_REGISTRY: dict[str, Type[BusMessage]] = {
    cls.__name__: cls
    for cls in vars(m).values()
    if isinstance(cls, type) and issubclass(cls, BusMessage) and cls is not BusMessage
}


def encode(msg: BusMessage) -> bytes:
    """BusMessage -> JSON bytes. 타입명을 봉투에 포함해 수신측이 복원 가능."""
    envelope = {"_type": type(msg).__name__, "payload": msg.model_dump(mode="json")}
    return json.dumps(envelope, ensure_ascii=False).encode("utf-8")


def decode(raw: bytes | str) -> BusMessage:
    """JSON bytes -> BusMessage 서브클래스. 미등록 타입·스키마 위반은 즉시 예외 (침묵 금지)."""
    envelope: dict[str, Any] = json.loads(raw)
    type_name = envelope.get("_type", "")
    cls = _TYPE_REGISTRY.get(type_name)
    if cls is None:
        raise ValueError(f"미등록 메시지 타입 '{type_name}' — core/messages.py에 정의할 것")
    return cls.model_validate(envelope["payload"])


def registered_types() -> frozenset[str]:
    return frozenset(_TYPE_REGISTRY)


# ---------------------------------------------------------------- Redis 버스

Handler = Callable[[BusMessage], Awaitable[None]]


class MessageBus:
    """Redis 기반 버스. redis 패키지는 지연 import — 코덱 테스트에 서버 불필요."""

    def __init__(self, redis_url: str, instance_id: str) -> None:
        self._url = redis_url
        self._instance_id = instance_id
        self._redis: Any = None

    async def connect(self) -> None:
        import redis.asyncio as aioredis  # 지연 import

        self._redis = aioredis.from_url(self._url, decode_responses=False)
        await self._redis.ping()

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    # ---- 발행 ----------------------------------------------------------
    async def publish(self, topic: str, msg: BusMessage) -> None:
        """스트림 토픽은 XADD(이력 보존), 나머지는 pub/sub."""
        if msg.instance_id == "unset":
            msg = msg.model_copy(update={"instance_id": self._instance_id})
        data = encode(msg)
        base = topic.split(".")[0] + "." + topic.split(".")[1] if "." in topic else topic
        if topic in STREAM_TOPICS or base in STREAM_TOPICS:
            await self._redis.xadd(topic, {"data": data}, maxlen=100_000, approximate=True)
        else:
            await self._redis.publish(topic, data)

    # ---- 구독 (pub/sub) -------------------------------------------------
    async def subscribe(self, patterns: list[str], handler: Handler) -> None:
        """패턴 구독 루프. sys.kill은 자동 포함 — 어떤 구독자도 kill을 놓치지 않는다."""
        pubsub = self._redis.pubsub()
        want = set(patterns) | {TOPIC_KILL}
        await pubsub.psubscribe(*want)
        async for item in pubsub.listen():
            if item.get("type") not in ("pmessage", "message"):
                continue
            await handler(decode(item["data"]))

    # ---- 스트림 소비 ----------------------------------------------------
    async def read_stream(
        self, topic: str, last_id: str = "$", block_ms: int = 1000
    ) -> list[tuple[str, BusMessage]]:
        """Streams 소비 — 재시작 시 last_id부터 재생 가능 (무상태 복원, R12)."""
        result = await self._redis.xread({topic: last_id}, block=block_ms, count=100)
        out: list[tuple[str, BusMessage]] = []
        for _stream, entries in result or []:
            for entry_id, fields in entries:
                eid = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
                out.append((eid, decode(fields[b"data"])))
        return out
