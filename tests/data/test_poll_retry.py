"""재시도 계층의 정본 (`data/poll_retry.py`) — 2026-08-12 F-4로 예산·백오프·4xx 분기 추가.

이 스위트가 지키는 것은 하나다: **포기하는 이유가 셋이고 셋이 서로 구분된다**는 것.
2026-08-12 점검이 "재시도가 안 먹었다"와 "두 번이 모자랐다"를 로그만으로 구분 못 해
등록부 문구가 과장됐던 것이 이 테스트들이 생긴 이유다.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from messiah.data import poll_retry


async def _no_sleep(_seconds: float) -> None:
    return None


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://openapi.koreainvestment.com:9443/x")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError(f"{code}", request=request, response=response)


class _Boom:
    """`fails`번 실패한 뒤 성공하는 무인자 코루틴 팩토리."""

    def __init__(self, fails: int, exc_factory=lambda: _status_error(500)) -> None:
        self.fails = fails
        self.calls = 0
        self._exc_factory = exc_factory

    async def __call__(self):
        self.calls += 1
        if self.calls <= self.fails:
            raise self._exc_factory()
        return {"ok": True}


@pytest.fixture(autouse=True)
def _silence_logs(monkeypatch):
    records: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        poll_retry.mlog, "log", lambda tag, msg, **fields: records.append((tag, fields))
    )
    return records


def test_three_total_attempts_by_default():
    """08-12 11:05은 총 2회 시도로 접혔다 — 이제 3회다 (RETRY_ATTEMPTS 1 → 2)."""
    assert poll_retry.RETRY_ATTEMPTS == 2

    call = _Boom(fails=2)
    result = asyncio.run(
        poll_retry.fetch_with_retry(call, retried_tag="R", error_tag="E", sleep=_no_sleep)
    )

    assert result == {"ok": True}
    assert call.calls == 3, "5xx 두 번을 넘기고 세 번째에 살아나야 한다"


def test_backoff_grows_between_attempts():
    """대기가 지수적으로 늘어난다 — 같은 간격으로 두드리면 500은 대개 또 500이다."""
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    asyncio.run(
        poll_retry.fetch_with_retry(
            _Boom(fails=99),
            retried_tag="R",
            error_tag="E",
            sleep=record,
            retry_attempts=3,
            retry_delay_seconds=0.5,
        )
    )

    assert slept == [0.5, 1.0, 2.0]


def test_4xx_is_not_retried(_silence_logs):
    """잘못된 요청을 세 번 보내는 것은 낭비이자 레이트리밋 위험이다."""
    call = _Boom(fails=99, exc_factory=lambda: _status_error(400))
    result = asyncio.run(
        poll_retry.fetch_with_retry(call, retried_tag="R", error_tag="E", sleep=_no_sleep)
    )

    assert result is None
    assert call.calls == 1, "4xx는 즉시 포기한다"
    tag, fields = _silence_logs[-1]
    assert tag == "E"
    assert "4xx" in fields["gave_up"], "포기 사유가 로그에서 구분돼야 한다"


def test_5xx_is_retried(_silence_logs):
    """서버 쪽 실패는 다시 쏠 가치가 있다 — 08-12에 잃은 다리가 정확히 이 경우였다."""
    call = _Boom(fails=99, exc_factory=lambda: _status_error(500))
    asyncio.run(poll_retry.fetch_with_retry(call, retried_tag="R", error_tag="E", sleep=_no_sleep))
    assert call.calls == 1 + poll_retry.RETRY_ATTEMPTS


def test_timeout_is_retried():
    """타임아웃도 5xx와 같은 취급 — `run_backfill.py`가 쓰는 짝과 같다."""
    call = _Boom(fails=1, exc_factory=lambda: httpx.ReadTimeout("slow"))
    result = asyncio.run(
        poll_retry.fetch_with_retry(call, retried_tag="R", error_tag="E", sleep=_no_sleep)
    )
    assert result == {"ok": True}
    assert call.calls == 2


def test_time_budget_wins_over_attempt_count(_silence_logs):
    """**시간 상한이 횟수보다 우선한다** — 카덴스 1분짜리 계열에서 다음 사이클을 밀어내면
    다리 하나를 살리려다 사이클 하나를 잃는다."""
    ticks = iter([0.0, 100.0, 200.0, 300.0, 400.0])  # 첫 시도만에 예산을 넘긴다

    call = _Boom(fails=99)
    result = asyncio.run(
        poll_retry.fetch_with_retry(
            call,
            retried_tag="R",
            error_tag="E",
            sleep=_no_sleep,
            retry_attempts=10,
            budget_seconds=40.0,
            clock=lambda: next(ticks),
        )
    )

    assert result is None
    assert call.calls == 1, "예산을 넘겼으면 남은 횟수가 있어도 포기한다"
    tag, fields = _silence_logs[-1]
    assert tag == "E"
    assert "예산" in fields["gave_up"]


def test_budget_does_not_cut_a_fast_recovery():
    """예산은 상한이지 지연이 아니다 — 빨리 살아나면 그대로 살아난다."""
    ticks = iter([0.0, 0.1, 0.2, 0.3])
    call = _Boom(fails=1)
    result = asyncio.run(
        poll_retry.fetch_with_retry(
            call,
            retried_tag="R",
            error_tag="E",
            sleep=_no_sleep,
            clock=lambda: next(ticks),
        )
    )
    assert result == {"ok": True}


def test_recovered_item_is_not_counted_as_loss(_silence_logs):
    """살아난 것은 손실이 아니다 — 태그 세 갈래 규율(모듈 docstring)은 그대로다."""
    asyncio.run(
        poll_retry.fetch_with_retry(_Boom(fails=1), retried_tag="R", error_tag="E", sleep=_no_sleep)
    )
    assert [tag for tag, _ in _silence_logs] == ["R"], "복구는 Retried 하나만 남긴다"


def test_first_try_success_is_silent(_silence_logs):
    """정상은 조용해야 한다."""
    asyncio.run(
        poll_retry.fetch_with_retry(_Boom(fails=0), retried_tag="R", error_tag="E", sleep=_no_sleep)
    )
    assert _silence_logs == []
