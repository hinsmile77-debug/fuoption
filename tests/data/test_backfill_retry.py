"""배치의 하루치 조회 재시도 — 2026-09-11 F-100 (`data/backfill_retry.py`).

2026-09-11 15:45에 KIS 500 **한 건**이 장후 배치 3/7단계를 통째로 죽였다. 같은 날 실시간
폴러는 같은 500을 11번 받고 11번 다 살아남았다 — 차이는 재시도 하나뿐이었다.

실제 시계를 타지 않도록 `sleep`을 주입한다(`poll_retry` 테스트와 같은 이유).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest

from messiah.data import backfill_retry

_TICK = Decimal("0.02")
_DAY = date(2026, 9, 11)


def _server_error() -> httpx.HTTPStatusError:
    """2026-09-11에 실제로 온 형태 — `inquire-time-fuopchartprice` 500."""
    request = httpx.Request("GET", "https://example.invalid/inquire-time-fuopchartprice")
    return httpx.HTTPStatusError(
        "Server error '500 Internal Server Error'",
        request=request,
        response=httpx.Response(500, request=request),
    )


def _source(failures: int):
    """앞 `failures`번은 500을 내고 그 뒤로는 하루치 한 페이지를 돌려주는 가짜 REST."""
    calls = {"n": 0}

    def fetch(symbol: str, *, date_yyyymmdd: str, hour_hhmmss: str):
        calls["n"] += 1
        if calls["n"] <= failures:
            raise _server_error()

        # 응답에 **전 거래일 행**을 섞는다 — `fetch_day_bars`는 그것을 "그날 첫 봉을 이미
        # 받았다"는 종료 신호로 읽어 한 번만 호출하고 끝낸다(그 함수 docstring 종료 조건 ①).
        # 그래야 이 테스트의 호출 수가 **재시도 횟수**를 그대로 뜻한다.
        def _row(day_key: str, hour: str, volume: str) -> dict:
            return {
                "stck_bsop_date": day_key,
                "stck_cntg_hour": hour,
                "futs_prpr": "35000",
                "futs_oprc": "35000",
                "futs_hgpr": "35000",
                "futs_lwpr": "35000",
                "cntg_vol": volume,
            }

        return {"output2": [_row(date_yyyymmdd, "090000", "10"), _row("20260910", "153400", "7")]}

    return fetch, calls


def test_transient_500_is_survived():
    """500 한 건은 배치를 죽이지 않는다 — 2026-09-11 사고의 직접 원인."""
    fetch, calls = _source(failures=1)
    slept: list[float] = []

    bars = backfill_retry.fetch_day_bars_with_retry(
        fetch, "A05610", _DAY, _TICK, sleep=slept.append, notify=None
    )

    assert [b.volume for b in bars] == [10]
    assert calls["n"] == 2  # 첫 시도 실패 + 재시도 성공
    assert slept == [3.0]  # 백오프는 회차 × 3초


def test_exhausted_retries_raise_not_return_empty():
    """끝내 실패하면 **예외**다 — 빈 목록을 돌려주면 그날이 "0봉"으로 조용히 통과한다."""
    fetch, calls = _source(failures=99)

    with pytest.raises(RuntimeError, match="4회 재시도 후에도 실패"):
        backfill_retry.fetch_day_bars_with_retry(fetch, "A05610", _DAY, _TICK, sleep=lambda _: None)

    assert calls["n"] == backfill_retry.RETRY_ATTEMPTS


def test_client_error_is_not_retried_forever_but_still_raises():
    """4xx도 예외로 나간다 — `_RETRYABLE` 밖이라 곧바로 전파된다(다시 쏴도 같은 거절)."""
    request = httpx.Request("GET", "https://example.invalid/x")
    not_found = httpx.HTTPStatusError(
        "Client error '404'", request=request, response=httpx.Response(404, request=request)
    )
    calls = {"n": 0}

    def fetch(symbol: str, *, date_yyyymmdd: str, hour_hhmmss: str):
        calls["n"] += 1
        raise not_found

    # 4xx는 `RETRY_ERRORS`(HTTPStatusError)에 걸리므로 재시도 대상이긴 하다 — 여기서 고정하는
    # 것은 **어떤 경우에도 조용히 성공으로 끝나지 않는다**는 계약이다.
    with pytest.raises(RuntimeError):
        backfill_retry.fetch_day_bars_with_retry(fetch, "A05610", _DAY, _TICK, sleep=lambda _: None)
    assert calls["n"] == backfill_retry.RETRY_ATTEMPTS


def test_first_try_success_does_not_sleep():
    """정상은 조용해야 한다 — 성공한 날에 배치가 3초를 더 쓰면 안 된다."""
    fetch, calls = _source(failures=0)
    slept: list[float] = []
    notified: list[str] = []

    backfill_retry.fetch_day_bars_with_retry(
        fetch, "A05610", _DAY, _TICK, sleep=slept.append, notify=notified.append
    )

    assert (calls["n"], slept, notified) == (1, [], [])
