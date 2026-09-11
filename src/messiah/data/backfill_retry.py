"""배치가 `fetch_day_bars()`를 부를 때 쓰는 **동기** 재시도 — 정본 하나 (2026-09-11 F-100).

## 왜 이 모듈이 생겼나

2026-09-11 15:45, 장후 배치 3/7단계(`verify_archive_volume.py`)가 KIS의 일시적 500 하나를
맞고 통째로 죽었다. `logs/volume_check_20260911.json`이 안 만들어졌고, 그 한 건의 미측정이
`daily-axes-measured` 게이트를 뒤집어 `FixVerificationRecurred`(P0)까지 갔다.

같은 날 실시간 폴러는 같은 KIS의 같은 500을 **11번** 받고 11번 다 살아남았다
(`data/poll_retry.py`). 차이는 재시도 하나뿐이었다.

그런데 그 재시도가 없었던 게 아니다 — `scripts/run_backfill.py`가 2026-08-04 사고 이후
똑같은 것을 **자기 안에** 갖고 있었고(`_fetch_with_retry`), 옆에 선 도구는 그것을 못 봤다.
`poll_retry.py`의 모듈 docstring이 미리 적어 둔 형태 그대로다: *"같은 코드를 두 곳에 두면
한쪽만 고쳐지고, 그게 이 저장소가 이미 네 번 겪은 형태다."* 그래서 복사하지 않고 옮겼다.

## 왜 `backfill.fetch_day_bars()` 안이 아닌가

`fetch_day_bars()`의 계약은 *"네트워크 예외는 그대로 전파해 호출측(스크립트)이 재시도·중단을
정한다"*이다(그 함수 docstring). 그 계약을 뒤집으면 재시도를 원치 않는 호출자
(예: 대화형 확인)가 몰래 40초를 기다리게 된다. 계약은 그대로 두고, **재시도를 원하는
호출자가 고르는 바깥층**을 여기에 둔다.

## 왜 `poll_retry.fetch_with_retry()`를 안 쓰나

그쪽은 **코루틴**이고 예산(`RETRY_BUDGET_SECONDS=40초`)이 *다음 사이클을 밀어내지 않는다*는
격자 규율에서 나온 값이다. 장후 배치엔 밀어낼 다음 사이클이 없고, 여기서 중요한 것은
"한 번 더 기다려서라도 그날 측정을 남기는 것"이다. 실패 시 동작도 반대다 — 폴러는 `None`을
돌려 루프를 계속하지만, 배치는 **예외를 올려** 호출측이 그 종목·일자를 "대조 불가"로
기록하게 해야 한다(조용히 비면 R10 위반이다).
"""

from __future__ import annotations

import time
from datetime import date
from decimal import Decimal
from typing import Callable

import httpx

from messiah.core import logging as mlog
from messiah.core.messages import BarClosed
from messiah.data import backfill
from messiah.data.backfill import MinuteChartSource

# 다시 쏠 가치가 있는 실패 — `poll_retry._RETRYABLE`과 같은 짝이다. 같은 KIS를 두 잣대로
# 보면 어느 경로는 살고 어느 경로는 죽는 날이 생긴다(오늘이 정확히 그 날이었다).
RETRY_ERRORS = (httpx.TransportError, httpx.HTTPStatusError)

RETRY_ATTEMPTS = 4
"""총 시도 횟수(첫 시도 포함). 2026-08-04 `run_backfill.py`가 정한 값을 그대로 가져왔다 —
835회 호출을 한 번에 도는 작업이 13일째에 `RemoteProtocolError`로 멈춘 뒤의 값이다."""

RETRY_BACKOFF_SECONDS = 3.0
"""대기 = 이 값 × 시도 회차 (3초 → 6초 → 9초). 500은 즉시 다시 쏘면 대개 또 500이다."""


def fetch_day_bars_with_retry(
    fetch: MinuteChartSource,
    symbol: str,
    day: date,
    tick_size: Decimal,
    *,
    attempts: int = RETRY_ATTEMPTS,
    backoff_seconds: float = RETRY_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    notify: Callable[[str], None] | None = None,
) -> list[BarClosed]:
    """하루치 1분봉을 받되, 다시 쏠 가치가 있는 실패면 `attempts`번까지 다시 시도한다.

    입력: `sleep`은 테스트가 실제로 18초를 기다리지 않도록 주입받는다(`poll_retry`와 같은
         이유). `notify`를 주면 재시도할 때마다 사람이 읽을 한 줄을 그쪽으로도 보낸다 —
         콘솔로 진행을 보여 주는 배치(`run_backfill.py`)가 쓴다.
    반환: `fetch_day_bars()`가 돌려준 그대로.
    실패 조건: 마지막 시도까지 실패하면 `RuntimeError`(원인 예외를 `__cause__`로 달아서).
         **삼키지 않는다** — 그날 측정이 빈 것을 호출측이 알아야 "대조 불가"로 남길 수 있다.
    """
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return backfill.fetch_day_bars(fetch, symbol, day, tick_size)
        except RETRY_ERRORS as exc:
            last = exc
            if attempt == attempts:
                break
            wait = backoff_seconds * attempt
            mlog.log(
                "BackfillFetchRetried",
                f"{symbol} {day.isoformat()} — {exc.__class__.__name__}: {exc} "
                f"({wait:.0f}초 후 재시도 {attempt}/{attempts - 1})",
                symbol=symbol,
                day=day.isoformat(),
                attempt=attempt,
                error=f"{type(exc).__name__}: {exc}",
            )
            if notify is not None:
                notify(
                    f"      재시도 {attempt}/{attempts - 1} — {exc.__class__.__name__}: "
                    f"{exc} ({wait:.0f}초 대기)"
                )
            sleep(wait)
    raise RuntimeError(f"{symbol} {day}: {attempts}회 재시도 후에도 실패") from last
