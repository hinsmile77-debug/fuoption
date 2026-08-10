"""REST 폴러의 재시도 계층 — 정본 하나 (2026-08-10 A-4).

## 왜 공통으로 뽑았나

`OptionChainPoller`는 2026-08-05부터 다리 하나가 실패하면 같은 사이클 안에서 한 번 더
시도한다. 그 계층이 실제로 값을 한다는 것은 매일 실측된다 — 2026-08-10에 옵션체인은
**52건을 재시도로 살리고 1건만 잃었다**(실패율 1.05%).

같은 날 `InvestorFlowPoller`는 3건을 실패했고 **3건을 그대로 잃었다.** 그쪽엔 재시도가
없었기 때문이다. 두 폴러가 같은 KIS REST의 같은 500을 받는데 한쪽만 처방을 받고 있었고,
그 차이를 아무도 몰랐던 이유는 단순하다 — 재시도가 **옵션체인 폴러 안에 사유화된 메서드**
(`_fetch_with_retry`)로 있었다.

그래서 옮기면서 복사하지 않았다. 같은 코드를 두 곳에 두면 한쪽만 고쳐지고, 그게 이
저장소가 이미 네 번 겪은 형태다(`ops/canonical_consumers.py`가 존재하는 이유).

## 태그를 세 갈래로 가르는 규율은 그대로다 (2026-08-05)

- 첫 시도에 성공 → **아무 로그도 없다.** 정상은 조용해야 한다.
- 재시도로 살아남 → `{Prefix}Retried` (INFO). 서버 상태의 기록이지 **결손이 아니다**.
- 끝내 실패 → `{Prefix}Error` (WARNING). **이때만** 데이터가 빈다.

둘을 같은 태그로 남기면 무결성 리포트의 WARNING 수가 "잃은 것의 수"를 더 이상 뜻하지
않게 된다 — 재시도로 살아난 것까지 세기 때문이다. 그러면 다음 점검이 "42다리 중 몇 개가
실제로 비었나"를 로그에서 못 읽는다.

## 다음 사이클로 넘기지 않는다

격자 규율(`core/scheduler.FixedTickScheduler`)이 두 폴러 설계의 전부다. 재시도 큐를 다음
사이클에 얹으면 그 사이클의 호출 수가 가변이 되고, 마흐디가 2026-07-30에 총수요를
한계선에 붙여 25사이클을 통째로 잃은 것과 같은 형태의 위험이 된다.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, TypeVar

from messiah.core import logging as mlog

T = TypeVar("T")

RETRY_ATTEMPTS = 1
"""항목 하나가 실패했을 때 **같은 사이클 안에서** 다시 시도하는 횟수.

2026-08-05 실측(옵션체인): 실패 5건/약 800건 = 0.6%. 2026-08-10 실측: 옵션체인 53건/약
5,050건 = 1.05%, 수급 3건/1,188건 = 0.25%. 실패한 항목에만 한 번 더 쓰는 호출이므로
사이클당 추가 부하는 **사이클 크기가 아니라 실패 건수**이고, 유량 예산에 실질적 영향이
없다(2026-08-10 점유 26%, 백오프 내성 3.85배).
"""

RETRY_DELAY_SECONDS = 0.5
"""재시도 전 대기. 공유 `RateLimiter`가 이미 요청 간 최소 1.0초를 강제하므로
(`rest_client.DEFAULT_MIN_REQUEST_INTERVAL_SECONDS`) 실제 간격은 1.5초 이상이 된다 —
500은 즉시 재시도하면 대개 또 500이라 그 간격이 필요하다."""


async def fetch_with_retry(
    call: Callable[[], Awaitable[T]],
    *,
    retried_tag: str,
    error_tag: str,
    retry_attempts: int = RETRY_ATTEMPTS,
    retry_delay_seconds: float = RETRY_DELAY_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    **context: object,
) -> T | None:
    """항목 하나를 조회한다. 실패하면 같은 사이클 안에서 `retry_attempts`번 더 시도한다.

    입력: `call`은 **무인자 코루틴 팩토리**다 — 시도마다 새 어웨이터블이 필요하므로
         코루틴 객체가 아니라 그것을 만드는 콜러블을 받는다(`lambda: asyncio.to_thread(...)`).
         `context`는 로그에 그대로 실린다(계열·심볼·업종 등 호출자의 어휘).
    반환: 성공한 결과, 끝내 실패하면 None.
    실패 조건: 없다 — 예외를 밖으로 내보내지 않는다. 항목 하나의 실패가 폴링 루프를
         죽이면 안 된다(L22).
    """
    last_error: Exception | None = None
    for attempt in range(1 + retry_attempts):
        if attempt:
            await sleep(retry_delay_seconds)
        try:
            result = await call()
        except Exception as exc:  # noqa: BLE001 — REST 실패로 폴링 루프가 죽으면 안 됨
            last_error = exc
            continue
        if attempt:
            mlog.log(
                retried_tag,
                f"{attempt}회 재시도로 복구: {last_error}",
                attempts=attempt + 1,
                **context,
            )
        return result

    mlog.log(
        error_tag,
        f"조회 실패({1 + retry_attempts}회 시도): {last_error}",
        attempts=1 + retry_attempts,
        **context,
    )
    return None
