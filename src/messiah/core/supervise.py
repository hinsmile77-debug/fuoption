"""부수적 태스크의 실패를 프로세스 생존과 떼어 놓는다 (2026-09-03 신설, F-89).

## 왜 생겼나 — 화면 표시 전용 기능 하나가 판단 계층 전체를 내렸다

2026-09-03 08:25:38, `ChainSmileProvider.run_forever()`가 버스 계약을 어긴 호출로
`TypeError`를 냈다. 그 제공자는 **주문 경로가 없는 화면 표시 전용**이었는데도, 그 예외가
`run_g2_paper_trading._run_regular_session()`의 `asyncio.gather()`를 타고 올라가 형제
태스크 전부를 취소시켰다:

    futures_service · pipeline(Meta→Risk→Sizer→OrderGateway) · watch_circuit_breaker
    · sim_feed · shadow_manager · regime_runtime · HealthReporter

즉 **격리는 주문 실행 경로에만 있었고 프로세스 생존에는 없었다.** 결과는 그날
09:00~15:35 판단 공백 하루이고, `HealthReporter`까지 같이 죽어 `g2.pipeline` 하트비트가
끊긴 채 33분간 아무도 몰랐다.

배선 오류 자체는 고쳤다(`strategy/options/chain_smile.py`). 이 모듈은 **다음 배선 오류**를
위한 것이다 — L22("항목 하나의 실패가 루프 전체를 죽이면 안 된다")를 태스크 수준으로
올린 자리다. `core/bus.py`의 구독 루프가 메시지 하나의 실패를 삼키는 것, `option_chain_
poller`가 다리 하나의 실패를 삼키는 것과 같은 규율이다.

## 조용해지지는 않는다 (R6·R10)

삼키는 것은 **예외의 전파**뿐이고 사실 자체는 삼키지 않는다. 크래시는 `IsolatedTaskCrashed`
(ERROR)로 트레이스백과 함께 남는다 — 금지계명 12번이 막는 "조용한 폴백"이 되지 않게.
반대로 **부수 태스크가 아닌 것에는 이 함수를 쓰면 안 된다.** 판단·주문·하트비트가 죽는
것은 프로세스가 죽어야 할 이유이고, 그것을 살려 두면 "살아 있는데 판단은 안 나가는"
2026-09-03 1-2형 거짓 정상이 된다.
"""

from __future__ import annotations

import asyncio
import traceback
from typing import Awaitable

from messiah.core import logging as mlog


async def run_isolated(name: str, coro: Awaitable[None]) -> None:
    """`coro`를 돌리되 예외를 형제 태스크에 전파하지 않는다 — 부수 태스크 전용.

    `asyncio.CancelledError`는 **삼키지 않는다.** 그것은 이 태스크의 실패가 아니라 바깥이
    내린 종료 지시라서, 삼키면 세션 종료·kill이 안 먹는다.
    """
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — 부수 태스크의 실패가 프로세스를 죽이면 안 됨(L22)
        mlog.log(
            "IsolatedTaskCrashed",
            f"부수 태스크 '{name}' 중단: {type(exc).__name__}: {exc} — 프로세스는 계속한다",
            task=name,
            error_type=type(exc).__name__,
            traceback=traceback.format_exc(),
        )
