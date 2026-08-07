"""테스트가 **운영 버스에 닿지 못하게** 막는다 (2026-08-07 P1-2).

## 왜 있나 — 실사고

2026-08-07, Command Center의 Kill Switch에 `sys.kill` 발행 경로를 결선한 직후
`tests/ui/test_app_smoke.py`가 2단 확인 버튼을 클릭했다. 그 화면의 사이드바 Redis URL
기본값은 `load_instance().redis_url` = **`redis://localhost:6380/0`**, 즉 운영 버스다.

즉 `pytest tests/`를 돌릴 때마다 **구동 중인 시스템에 진짜 kill이 나가는** 구조였다.
그날은 두 가지 우연이 막았다: 테스트가 그 전 단언에서 먼저 깨졌고, 구동 중이던 G2는
수신 분기가 없는 구버전이었다. 우연에 기대지 않는다.

## 어디서 막나 — **길목에서** 막는다

`load_instance()`를 갈아끼우는 방법을 먼저 시도했다가 버렸다. 소비자들이
`from messiah.core.config import load_instance`로 **이름을 복사**해 가므로, 모듈 속성만
덮으면 복사본은 여전히 원본을 본다 — 모듈을 하나씩 찾아 덮는 목록은 반드시 새는 목록이
된다(그 목록에 없는 파일이 내일 생긴다).

그래서 **`MessageBus.connect()`** 에서 막는다. URL을 어디서 얻었든 Redis에 닿으려면
반드시 여기를 지나므로, 이 한 곳이 유일한 길목이다. 운영 주소로 연결을 시도하면
`ProductionBusBlocked`를 던져 **테스트가 시끄럽게 깨진다** — 조용히 다른 곳에 붙는 것보다
낫다(L18).

## 진짜 Redis가 필요한 테스트는?

지금은 없다(전 테스트가 `InProcessBus`나 가짜를 쓴다). 나중에 생기면 `allow_real_bus`
fixture를 받아 **명시적으로** 열 것 — 그리고 운영 DB(0번)가 아니라 테스트 전용 DB를
쓸 것. 섞이면 이 파일이 있으나 마나다.
"""

from __future__ import annotations

import pytest

# 포트 1은 예약 포트 — 어떤 서비스도 여기 붙지 않으므로 연결이 즉시 거부된다.
UNREACHABLE_REDIS_URL = "redis://127.0.0.1:1/0"

# 이 포트로 연결을 시도하면 운영 버스다(`configs/instance.yaml`의 `redis_url`).
PRODUCTION_REDIS_PORT = "6380"


class ProductionBusBlocked(RuntimeError):
    """테스트가 운영 Redis에 붙으려 했다 — 2026-08-07 실사고의 재발 방지.

    **테스트에서 이 클래스를 import해 잡지 말 것.** pytest는 conftest를 `conftest`로
    적재하고 테스트가 `tests.conftest`로 import하면 **다른 클래스 객체**가 되어
    `pytest.raises()`가 안 잡힌다(2026-08-07 실측). `RuntimeError`를 상속한 이유가
    그것이다 — `pytest.raises(RuntimeError, match="운영 버스")`로 잡는다.
    """


@pytest.fixture(autouse=True)
def _forbid_production_bus(monkeypatch: pytest.MonkeyPatch) -> None:
    """운영 버스로의 `MessageBus.connect()`를 차단한다.

    `autouse`인 것이 요점이다 — 테스트가 이 보호를 **기억해서 켜야 한다면** 언젠가
    잊는다. 2026-08-07이 정확히 그 형태였다(아무도 "이 클릭이 운영 버스를 건드리나"를
    묻지 않았다).
    """
    from messiah.core import bus as bus_module

    original = bus_module.MessageBus.connect

    async def _guarded(self, *args, **kwargs):
        if PRODUCTION_REDIS_PORT in getattr(self, "_url", ""):
            raise ProductionBusBlocked(
                f"테스트가 운영 버스({self._url})에 연결을 시도했다. "  # noqa: SLF001
                f"가짜 버스나 {UNREACHABLE_REDIS_URL}를 쓸 것 — tests/conftest.py 참고."
            )
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(bus_module.MessageBus, "connect", _guarded)


# 실제 버스가 필요한 통합 테스트가 생기면: 이 차단을 우회하는 fixture를 만들지 말고
# **테스트 전용 DB**(`redis://localhost:6380/15` 등)를 쓰도록 `PRODUCTION_REDIS_PORT`
# 대신 URL 전체를 비교하게 바꿀 것. 우회 스위치를 만들면 언젠가 기본값이 된다.
