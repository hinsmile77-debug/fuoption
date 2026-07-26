from messiah.core.messages import Health, HealthLevel
from messiah.simulator.inprocess_bus import InProcessBus


def _health(instance_id: str = "unset") -> Health:
    return Health(component="test", level=HealthLevel.OK, instance_id=instance_id)


async def test_publish_dispatches_to_matching_topic_handler():
    bus = InProcessBus()
    received: list[Health] = []

    async def handler(msg: Health) -> None:
        received.append(msg)

    await bus.subscribe(["sys.health"], handler)
    await bus.publish("sys.health", _health())

    assert len(received) == 1


async def test_publish_ignores_unmatched_topic():
    bus = InProcessBus()
    received: list[Health] = []

    async def handler(msg: Health) -> None:
        received.append(msg)

    await bus.subscribe(["sys.health"], handler)
    await bus.publish("bar.1m.A05608", _health())

    assert received == []


async def test_publish_fills_unset_instance_id():
    bus = InProcessBus(instance_id="messiah-replay-test")
    received: list[Health] = []

    async def handler(msg: Health) -> None:
        received.append(msg)

    await bus.subscribe(["sys.health"], handler)
    await bus.publish("sys.health", _health(instance_id="unset"))

    assert received[0].instance_id == "messiah-replay-test"


async def test_multiple_subscribers_to_same_topic_all_receive():
    bus = InProcessBus()
    counts = {"a": 0, "b": 0}

    async def handler_a(msg: Health) -> None:
        counts["a"] += 1

    async def handler_b(msg: Health) -> None:
        counts["b"] += 1

    await bus.subscribe(["sys.health"], handler_a)
    await bus.subscribe(["sys.health"], handler_b)
    await bus.publish("sys.health", _health())

    assert counts == {"a": 1, "b": 1}
