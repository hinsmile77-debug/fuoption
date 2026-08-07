"""conftest의 운영 버스 차단이 실제로 걸리는지 — 2026-08-07 P1-2.

이 파일이 없으면 `conftest.py`가 조용히 안 먹어도 아무도 모른다. 보호 장치는
**자기가 작동한다는 것을 증명**해야 한다(이 저장소가 반복해 배운 것).

`ProductionBusBlocked`를 import해 잡지 않는 이유는 그 클래스 docstring 참고 —
pytest의 conftest 적재 경로 때문에 다른 클래스 객체가 된다.
"""

from __future__ import annotations

import pytest

from messiah.core.bus import MessageBus
from messiah.core.config import load_instance

pytestmark = pytest.mark.asyncio

_PRODUCTION_PORT = "6380"


async def test_connecting_to_the_production_bus_is_blocked():
    """2026-08-07엔 이 경로가 열려 있었고, 그날 진짜 kill이 나가 수집기를 죽였다."""
    bus = MessageBus(f"redis://localhost:{_PRODUCTION_PORT}/0", instance_id="test")
    with pytest.raises(RuntimeError, match="운영 버스"):
        await bus.connect()


async def test_the_blocked_port_is_the_one_operations_actually_uses():
    """설정이 바뀌어 포트가 달라지면 이 차단이 헛돈다 — 그 사실을 여기서 잡는다."""
    assert (
        _PRODUCTION_PORT in load_instance("configs").redis_url
    ), "운영 redis_url의 포트가 바뀌었다 — tests/conftest.py의 PRODUCTION_REDIS_PORT도 갱신할 것"
