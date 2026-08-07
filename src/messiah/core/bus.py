"""Message Bus — Redis pub/sub + Streams 래퍼 (Ver 1.1 §4).

원칙:
- 모든 프로세스 간 통신은 이 모듈을 통해서만 (SYSTEM.md §4-2)
- 페이로드는 core/messages.py의 Pydantic 모델만 — encode/decode에 타입 레지스트리 사용
- 이력이 필요한 토픽(decision.*, capital.*, exec.*)은 pub/sub이 아니라 Streams(XADD, 재생 가능)
- sys.kill은 최우선이되 **원한 구독자에게만** 간다(`subscribe(..., on_kill=...)`).
  종전엔 모든 구독자에게 자동 배달했는데, 2026-08-07에 그 자동 배달이 수집 프로세스를
  죽였다 — 상세는 `MessageBus.subscribe()` docstring.

코덱(encode/decode)은 Redis 없이도 테스트 가능하도록 분리되어 있다.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Protocol, Type

from messiah.core import messages as m
from messiah.core.messages import BusMessage

# ---------------------------------------------------------------- 토픽 정의 (Ver 1.1 §4.2)

TOPIC_RAW = "raw"  # raw.{source}
TOPIC_TICK = "md.tick"  # md.tick.{symbol}
TOPIC_BAR = "bar"  # bar.{horizon}.{symbol} — 완성봉 확정
TOPIC_FEAT = "feat"  # feat.{horizon}.{symbol}
TOPIC_REGIME = "intel.regime"
TOPIC_FUTURES = "intel.futures"
TOPIC_OPTIONS = "intel.options"
TOPIC_INTENT = "decision.intent"  # Streams
TOPIC_ORDER_REQ = "capital.order_request"  # Streams
TOPIC_EXEC_ORDER = "exec.order"  # Streams
TOPIC_EXEC_FILL = "exec.fill"  # Streams
TOPIC_HEALTH = "sys.health"
TOPIC_KILL = "sys.kill"  # 최우선
TOPIC_CIRCUIT_BREAKER = "sys.circuit_breaker"  # CircuitBreakerStatus heartbeat (Command Center UI)

# L6 Learning / Self Evolution (Ver 2.0 §9 W35~36, Phase 5) — 전부 감사 이력이 필요해
# Streams(재생 가능)로 분류. decision.intent와 같은 이유(사람이 나중에 리뷰).
TOPIC_REGISTRY = "sys.registry"
TOPIC_SHADOW_FILL = "sys.shadow_fill"
TOPIC_PROMOTION = "sys.promotion_proposal"
TOPIC_SELF_EVAL = "sys.self_eval"

STREAM_TOPICS: frozenset[str] = frozenset(
    {
        TOPIC_INTENT,
        TOPIC_ORDER_REQ,
        TOPIC_EXEC_ORDER,
        TOPIC_EXEC_FILL,
        TOPIC_REGISTRY,
        TOPIC_SHADOW_FILL,
        TOPIC_PROMOTION,
        TOPIC_SELF_EVAL,
    }
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


class BusLike(Protocol):
    """`publish`/`subscribe`만 있으면 되는 최소 계약 — Ver 1.0.1 §2.1 "동일 인터페이스"를
    타입 수준에서도 명시한다. `MessageBus`(Redis)·`simulator.InProcessBus`(재생)·테스트용
    FakeBus가 전부 이 구조를 구조적으로 만족한다. `connect`/`close`/`read_stream` 등
    `MessageBus`의 나머지 메서드는 `bus.*`만 쓰는 소비자(FeatureEngine 등)에겐 불필요해
    포함하지 않는다 — 그 메서드가 필요한 소비자(collector 등)는 여전히 구체 클래스를 받는다."""

    async def publish(self, topic: str, msg: BusMessage) -> None: ...
    async def subscribe(
        self, patterns: list[str], handler: Handler, *, on_kill: Handler | None = None
    ) -> None: ...


def _log_subscriber_failure(failures: int, kind: str, exc: Exception, patterns: list[str]) -> int:
    """구독 루프의 처리 실패를 남기고 누적 건수를 돌려준다 (2026-08-07 P0-1).

    **로그를 조절한다**: 매 메시지마다 실패하는 상태(핸들러가 통째로 깨진 경우)에서 건당
    한 줄씩 찍으면 초당 수십 줄이 되어 로그가 못 쓰게 된다. 1·10·100·1000…번째만 찍되
    누적 건수를 항상 실어 크기를 잃지 않는다 — `series_coverage`가 구멍 목록을 접는 것과
    같은 규율(늑대소년 방지)이다.

    첫 건은 반드시 찍는다. 조용히 넘어가면 2026-08-07이 그대로 반복된다(그날은 루프가
    죽어서 흔적이라도 남았지, 이제는 살아남으므로 **로그가 유일한 증거**다).
    """
    failures += 1
    if failures == 1 or (failures % 10 == 0 and failures <= 100) or failures % 100 == 0:
        from messiah.core.logging import log  # 순환 import 방지 — 실패 시에만 필요

        log(
            "SubscriberHandlerFailed",
            f"구독 처리 실패({kind}) — 이 메시지만 버리고 루프는 계속: {exc}",
            patterns=sorted(patterns),
            kind=kind,
            error=f"{type(exc).__name__}: {exc}",
            failures=failures,
        )
    return failures


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
    async def subscribe(
        self, patterns: list[str], handler: Handler, *, on_kill: Handler | None = None
    ) -> None:
        """패턴 구독 루프.

        ## `sys.kill`은 **원한 구독자에게만** 간다 (2026-08-07 P0-1, 실사고 후 변경)

        종전엔 `want = set(patterns) | {TOPIC_KILL}`로 **모든** 구독자에게 kill을 배달하며
        *"어떤 구독자도 kill을 놓치지 않는다"*고 적혀 있었다. 그런데 그렇게 배달된
        `KillSignal`을 **핸들러가 견뎌야 한다는 계약은 어디에도 없었다.**

        2026-08-07 13:41에 `sys.kill`이 이 시스템 역사상 처음 흘렀고, 그 즉시
        `FeatureEngine.handle_bar`가 `bar.symbol`에서 `AttributeError`로 죽으면서 구독
        루프째 무너져 **수집 프로세스가 종료됐다**(1시간 54분 유실, 소급 불가 계열 3종 영구
        소실). 즉 "kill을 놓치지 않게" 만든 장치가 **kill이 오는 순간 수집을 죽이는 장치**로
        작동했다. R2(일일손실 한도) 자동 발동으로도 똑같이 났을 일이다.

        이제 kill은 **의사를 밝힌 구독자에게만** 간다:
          - `on_kill`을 주면 → `TOPIC_KILL`을 구독하고 `KillSignal`을 그쪽으로 보낸다.
          - `patterns`에 `TOPIC_KILL`을 직접 넣으면 → `handler`가 받는다(전용 리스너).
          - 둘 다 아니면 → 구독조차 안 한다. 봉만 보겠다는 구독자는 봉만 받는다.

        ## 핸들러 예외가 루프를 죽이지 않는다

        메시지 하나의 실패로 그 구독자의 **나머지 전부**가 멈추면 안 된다 — `option_chain_poller`
        가 *"다리 하나의 실패가 나머지를 막지 않는다(L22)"*로 지키는 그 규율이 정작 버스
        루프엔 없었다. 이 `try/except`가 있었다면 2026-08-07 손실은 **0**이었다.
        """
        pubsub = self._redis.pubsub()
        want = set(patterns)
        if on_kill is not None:
            want.add(TOPIC_KILL)
        await pubsub.psubscribe(*want)
        wants_kill_on_handler = TOPIC_KILL in set(patterns)
        failures = 0
        async for item in pubsub.listen():
            if item.get("type") not in ("pmessage", "message"):
                continue
            try:
                message = decode(item["data"])
            except Exception as exc:  # noqa: BLE001 — 깨진 페이로드가 루프를 죽이면 안 된다
                failures = _log_subscriber_failure(failures, "decode", exc, patterns)
                continue
            target = handler
            if isinstance(message, m.KillSignal):
                if on_kill is not None:
                    target = on_kill
                elif not wants_kill_on_handler:
                    continue  # 원하지 않은 구독자에게는 배달하지 않는다
            try:
                await target(message)
            except Exception as exc:  # noqa: BLE001 — L22, 위 docstring 참고
                failures = _log_subscriber_failure(failures, type(message).__name__, exc, patterns)

    # ---- 스트림 소비 ----------------------------------------------------
    async def read_stream(
        self, topic: str, last_id: str = "$", block_ms: int = 1000
    ) -> list[tuple[str, BusMessage]]:
        """Streams 소비 — 재시작 시 last_id부터 재생 가능 (무상태 복원, R12).

        여러 토픽을 **끊김 없이** 따라가야 하면 이걸 토픽 수만큼 부르지 말고
        `read_streams()`를 쓴다 — 그 이유는 그쪽 docstring에 있다.
        """
        entries = await self.read_streams({topic: last_id}, block_ms=block_ms)
        return [(entry_id, message) for _topic, entry_id, message in entries]

    async def read_streams(
        self, last_ids: dict[str, str], block_ms: int = 1000
    ) -> list[tuple[str, str, BusMessage]]:
        """여러 스트림을 **한 번의 XREAD**로 읽는다 — 반환은 (토픽, 엔트리ID, 메시지).

        ## 왜 단일 호출이어야 하나 (2026-08-05 3차, P0-2)

        `ui/app.py`가 `decision.intent`와 `exec.fill`을 순차 루프로 각각 1초씩 블록하며
        읽고 있었다. 그런데 `"$"`는 고정된 위치가 아니라 **호출 시점의 마지막 ID로 매번 다시
        해석**되는 값이다 — 엔트리가 없어 `last_id`가 `"$"`인 채로 남으면, `exec.fill`을
        블록하는 그 1초 동안 도착한 `decision.intent`는 다음 읽기에서 `$`가 그 **뒤로**
        재해석되며 영영 전달되지 않는다. 토픽당 약 50%의 사각지대였다.

        블록 한 번이 모든 토픽을 함께 덮으면 그 창 자체가 사라진다. 남은 조건은 `last_id`가
        구체 ID여야 한다는 것 — 기동 시 한 번만 `stream_last_id()`로 해석해 두면 그 뒤로는
        읽은 만큼 전진하므로 `$`가 다시 등장하지 않는다.
        """
        result = await self._redis.xread(dict(last_ids), block=block_ms, count=100)
        out: list[tuple[str, str, BusMessage]] = []
        for stream, entries in result or []:
            topic = stream.decode() if isinstance(stream, bytes) else stream
            for entry_id, fields in entries:
                eid = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
                out.append((topic, eid, decode(fields[b"data"])))
        return out

    async def stream_last_id(self, topic: str) -> str:
        """ "지금 이 스트림의 마지막 엔트리 ID" — `"$"`를 **구체 ID로 한 번만** 고정하는 용도.

        비어 있으면 `"0-0"`을 준다. 그 값으로 읽으면 "처음부터 전부"라 과거 재생처럼 보이지만,
        비어 있으니 실제로 재생될 것이 없고 이후 추가분은 하나도 안 놓친다 — `$`가 남기는
        사각지대(위 `read_streams` docstring)를 여는 것보다 이쪽이 안전하다.
        """
        entries = await self._redis.xrevrange(topic, count=1)
        if not entries:
            return "0-0"
        entry_id = entries[0][0]
        return entry_id.decode() if isinstance(entry_id, bytes) else entry_id
