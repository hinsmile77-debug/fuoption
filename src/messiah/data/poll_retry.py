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

## 재시도는 먹었고 두 번이 모자랐다 (2026-08-12 F-4)

08-10 3건 → 08-11 **0건** → 08-12 1건. 등록부가 스스로 정한 판정 기준
(`configs/pending_verifications.yaml` `leg-completeness-measured`: *"며칠 뒤에도 3건이
계속 나면 재시도가 안 먹은 것이다"*)에 비추면 **재시도는 먹었다.** 남은 1건은 2회 시도로도
못 넘긴 KIS 5xx다. 그래서 처방은 "다시 고친다"가 아니라 **예산을 늘린다**이고, 늘리는
방식에 세 가지 제약을 건다:

1. **시간 상한이 횟수보다 우선한다.** `flow_intraday`의 카덴스가 1분이라(그날 실측
   `cadence_minutes: 1.0`) 재시도가 60초를 넘으면 다음 사이클을 밀어내 **결손 1건이 2건이
   된다.** 다리 하나를 살리려다 사이클 하나를 잃는 것은 손해다.
2. **5xx·타임아웃만 다시 쏜다.** 4xx는 잘못된 요청이라 세 번 보내도 같은 답이고, 그건
   낭비이자 레이트리밋 위험이다(`_is_kis_rate_limit_error`가 이미 아는 형태).
3. **지수 백오프.** 500은 즉시 재시도하면 대개 또 500이다.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, TypeVar

import httpx

from messiah.core import logging as mlog
from messiah.ops import loss_ledger

T = TypeVar("T")

RETRY_ATTEMPTS = 2
"""항목 하나가 실패했을 때 **같은 사이클 안에서** 다시 시도하는 횟수(총 시도 = 이 값 + 1).

2026-08-05 실측(옵션체인): 실패 5건/약 800건 = 0.6%. 2026-08-10 실측: 옵션체인 53건/약
5,050건 = 1.05%, 수급 3건/1,188건 = 0.25%. 실패한 항목에만 한 번 더 쓰는 호출이므로
사이클당 추가 부하는 **사이클 크기가 아니라 실패 건수**이고, 유량 예산에 실질적 영향이
없다(2026-08-10 점유 26%, 백오프 내성 3.85배).

**1 → 2 (2026-08-12 F-4)**: 08-12 11:05 수급 사이클이 총 2회 시도로 500을 두 번 받고
포기해 3다리 중 2다리만 남았다(소급 경로 없음 · 영구 소실 5분). 한 번 더 준다 —
다만 아래 `RETRY_BUDGET_SECONDS`가 이 횟수보다 우선한다.
"""

RETRY_DELAY_SECONDS = 0.5
"""**첫** 재시도 전 대기 — 이후는 지수적으로 늘어난다(`RETRY_BACKOFF_FACTOR`).

공유 `RateLimiter`가 이미 요청 간 최소 1.0초를 강제하므로
(`rest_client.DEFAULT_MIN_REQUEST_INTERVAL_SECONDS`) 실제 간격은 1.5초 이상이 된다 —
500은 즉시 재시도하면 대개 또 500이라 그 간격이 필요하다."""

RETRY_BACKOFF_FACTOR = 2.0
"""재시도마다 대기를 몇 배로 늘리나 (2026-08-12 F-4). 대기는 0.5초 → 1.0초가 되고,
레이트리미터의 1.0초를 얹으면 실제 간격은 1.5초 → 2.0초다. 5xx가 몇 초 이어지는
구간을 넘기려면 같은 간격으로 두드리는 것보다 물러서는 편이 낫다."""

RETRY_BUDGET_SECONDS = 40.0
"""한 항목의 재시도에 쓸 수 있는 **총 시간**. 이 예산을 넘으면 남은 횟수가 있어도 포기한다.

**횟수보다 이쪽이 우선이다** (2026-08-12 F-4). `flow_intraday`의 카덴스가 1분이라 재시도가
60초를 넘으면 다음 사이클을 밀어내고, 그러면 다리 하나를 살리려다 **사이클 하나를 통째로
잃는다** — 결손 1건이 2건이 되는 거래다. 40초는 그 60초 아래로 여유를 둔 값이다(호출 자체의
소요 시간과 레이트리미터 대기까지 이 예산 안에서 센다).

옵션체인 폴러도 같은 정본을 공유하는데 그쪽 카덴스는 5·10분이라 이 상한이 먼저 걸릴 일이
없다 — 상한이 더 촘촘한 계열에 맞춰져 있으면 느슨한 계열은 저절로 안전하다."""

# **다시 쏠 가치가 있는 실패**만 재시도한다 (2026-08-12 F-4).
#
# `TransportError`는 연결/타임아웃 계열(`ConnectTimeout`·`ReadTimeout`·`RemoteProtocolError`
# 등)의 공통 조상이다 — `scripts/run_backfill.py`가 이미 같은 짝(`TransportError`,
# `HTTPStatusError`)으로 재시도하고 있고, 여기서 다른 목록을 쓰면 같은 KIS를 두 잣대로 보게 된다.
# `HTTPStatusError`는 상태 코드를 보고 **5xx만** 통과시킨다(아래 `_worth_retrying`).
_RETRYABLE = (httpx.TransportError, httpx.HTTPStatusError)


def _worth_retrying(exc: Exception) -> bool:
    """이 실패를 다시 쏠 것인가 — 5xx·타임아웃은 예, 4xx는 아니오.

    4xx를 세 번 보내는 것은 낭비이자 레이트리밋 위험이다(같은 요청에 같은 거절이 온다).
    분류할 수 없는 예외(`_RETRYABLE` 밖)는 **재시도한다** — 종전 동작이 "모든 예외를
    재시도"였으므로, 모르는 실패를 조용히 포기 쪽으로 바꾸면 이 변경이 데이터를 덜 지키는
    쪽으로 작동할 수 있다. 좁히는 것은 근거가 있는 4xx 하나뿐이다.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return True


async def fetch_with_retry(
    call: Callable[[], Awaitable[T]],
    *,
    retried_tag: str,
    error_tag: str,
    retry_attempts: int = RETRY_ATTEMPTS,
    retry_delay_seconds: float = RETRY_DELAY_SECONDS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    loss_series: str | None = None,
    backoff_factor: float = RETRY_BACKOFF_FACTOR,
    budget_seconds: float = RETRY_BUDGET_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    **context: object,
) -> T | None:
    """항목 하나를 조회한다. 실패하면 같은 사이클 안에서 `retry_attempts`번 더 시도한다.

    입력: `call`은 **무인자 코루틴 팩토리**다 — 시도마다 새 어웨이터블이 필요하므로
         코루틴 객체가 아니라 그것을 만드는 콜러블을 받는다(`lambda: asyncio.to_thread(...)`).
         `context`는 로그에 그대로 실린다(계열·심볼·업종 등 호출자의 어휘).
         `loss_series`를 주면 **끝내 실패했을 때만** 그 이름으로 손실 장부에 적는다
         (`ops/loss_ledger.py`) — 소급 경로가 없는 계열의 호출자만 준다.
         `clock`은 예산 계산용 단조 시계 — 테스트가 실제로 40초를 기다리지 않도록 주입받는다
         (`sleep`을 주입받는 것과 같은 이유).
    반환: 성공한 결과, 끝내 실패하면 None.
    실패 조건: 없다 — 예외를 밖으로 내보내지 않는다. 항목 하나의 실패가 폴링 루프를
         죽이면 안 된다(L22).

    **포기하는 이유가 셋이다** (2026-08-12 F-4): 횟수 소진 · 시간 예산 초과 · 다시 쏠
    가치가 없는 실패(4xx). 로그 문구가 그 셋을 구분한다 — "2회 시도 후 실패"와 "4xx라
    한 번만 쏘고 접었다"가 같은 문장으로 남으면 다음 점검이 재시도 계층을 또 의심한다.
    """
    started = clock()
    last_error: Exception | None = None
    attempts = 0
    give_up = ""
    delay = retry_delay_seconds

    for attempt in range(1 + retry_attempts):
        if attempt:
            await sleep(delay)
            delay *= backoff_factor
        attempts = attempt + 1
        try:
            result = await call()
        except Exception as exc:  # noqa: BLE001 — REST 실패로 폴링 루프가 죽으면 안 됨
            last_error = exc
            if not _worth_retrying(exc):
                # 4xx — 같은 요청에 같은 거절이 온다. 더 쏘는 것은 낭비이자 레이트리밋 위험.
                give_up = "4xx(요청 자체가 거절됨 — 재시도 무의미)"
                break
            if clock() - started >= budget_seconds:
                # **다음 사이클을 밀어내지 않는다** — 카덴스 1분짜리 계열에서 이 선을 넘으면
                # 결손 1건이 2건이 된다(`RETRY_BUDGET_SECONDS`).
                give_up = f"재시도 예산 {budget_seconds:.0f}초 소진"
                break
            continue
        if attempt:
            mlog.log(
                retried_tag,
                f"{attempt}회 재시도로 복구: {last_error}",
                attempts=attempts,
                **context,
            )
        return result

    reason = f", {give_up}" if give_up else ""
    mlog.log(
        error_tag,
        f"조회 실패({attempts}회 시도{reason}): {last_error}",
        attempts=attempts,
        gave_up=give_up or "재시도 횟수 소진",
        **context,
    )
    # **여기서만** 장부에 적는다 (2026-08-10 B-2). 위 `Retried` 경로는 살아난 것이라
    # 손실이 아니다 — 둘을 같이 세면 이 숫자가 "오늘 잃은 것"을 더 이상 뜻하지 않는다.
    if loss_series is not None:
        loss_ledger.record_lost(loss_series)
    return None
