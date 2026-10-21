"""승인키 재발급의 네트워크 실패가 L1을 죽이지 않고 재연결 경로를 탄다 (2026-10-22 F-132).

2026-10-08 08:27:32: WS 끊김 → 재연결 중 `ApprovalKeyIssuer.issue()`가 DNS 실패
(`httpx.ConnectError`, `getaddrinfo failed`) → httpx 예외는 `OSError` 계열이 아니라
`_WS_DISCONNECT_ERRORS`를 빠져나가 프로세스가 통째로 죽었다(6시간 35분 공백).
여기서 붙잡는 성질:
  ① `TickCollector`·`MultiSymbolTickCollector` **두 곳 모두** `httpx.TransportError`를
     `CollectorWSDisconnected` + 백오프 재시도로 처리한다.
  ② `httpx.HTTPStatusError`(4xx/5xx — 키·권한 문제)는 재시도 없이 그대로 전파된다.
  ③ 새 예외는 `ConnectionError`라 기존 재연결 튜플을 넓히지 않고도 잡힌다.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from messiah.broker.kis import tr_codes
from messiah.broker.kis.credentials import KISCredentials
from messiah.broker.kis.ws_client import ApprovalKeyIssuer
from messiah.data import collector as collector_mod
from messiah.data.archiver import ParquetArchiver
from messiah.data.collector import (
    ApprovalKeyIssueError,
    MultiSymbolTickCollector,
    SymbolFeed,
    TickCollector,
)
from messiah.data.normalizer import parse_futures_ticks

_TICK_SIZE = Decimal("0.02")
_SUBSCRIBE_ACK = json.dumps(
    {
        "header": {"tr_id": "H0IFCNT0", "tr_key": "A05608", "encrypt": "N"},
        "body": {"rt_cd": "0", "msg_cd": "OPSP0000", "msg1": "SUBSCRIBE SUCCESS"},
    }
)


class _StopConnection:
    """구독은 받고 첫 recv에서 ValueError — run_forever 루프를 끝내는 용도(재시도 대상 아님)."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        raise ValueError("테스트 종료")

    async def close(self) -> None:
        pass


class _CM:
    def __init__(self) -> None:
        self.conn = _StopConnection()

    async def __aenter__(self) -> _StopConnection:
        return self.conn

    async def __aexit__(self, *exc: object) -> None:
        await self.conn.close()


def _creds() -> KISCredentials:
    return KISCredentials(app_key="key", app_secret="secret", account_no="12345678", is_mock=True)


def _issuer(responses: list) -> tuple[ApprovalKeyIssuer, list[int]]:
    """responses 원소: Exception이면 던지고, int면 그 상태코드로 응답, 소진되면 200."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        item = responses.pop(0) if responses else 200
        if isinstance(item, Exception):
            raise item
        return httpx.Response(item, json={"approval_key": "APV-TEST"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ApprovalKeyIssuer(_creds(), client=client), calls


def _single(issuer: ApprovalKeyIssuer, tmp_path: Path) -> TickCollector:
    return TickCollector(
        creds=_creds(),
        symbol="A05608",
        tr_id=tr_codes.WS_TR_FUTURES_CONTRACT,
        parse_tick=parse_futures_ticks,
        tick_size=_TICK_SIZE,
        archiver=ParquetArchiver(tmp_path),
        approval_issuer=issuer,
        ws_connect=lambda uri: _CM(),
        reconnect_initial_backoff_seconds=5.0,
        reconnect_max_backoff_seconds=60.0,
    )


def _multi(issuer: ApprovalKeyIssuer, tmp_path: Path) -> MultiSymbolTickCollector:
    return MultiSymbolTickCollector(
        creds=_creds(),
        feeds=[
            SymbolFeed("A05608", tr_codes.WS_TR_FUTURES_CONTRACT, parse_futures_ticks, _TICK_SIZE)
        ],
        archiver=ParquetArchiver(tmp_path),
        approval_issuer=issuer,
        ws_connect=lambda uri: _CM(),
        reconnect_initial_backoff_seconds=5.0,
        reconnect_max_backoff_seconds=60.0,
    )


@pytest.fixture
def recorded(monkeypatch):
    logged: list[tuple[str, str]] = []
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(
        "messiah.data.collector.mlog.log", lambda tag, msg, **f: logged.append((tag, msg))
    )
    monkeypatch.setattr("messiah.data.collector.asyncio.sleep", fake_sleep)
    return logged, slept


def test_issue_error_rides_the_existing_reconnect_tuple():
    assert issubclass(ApprovalKeyIssueError, collector_mod._WS_DISCONNECT_ERRORS)
    assert not issubclass(httpx.HTTPStatusError, httpx.TransportError)


@pytest.mark.parametrize("make", [_single, _multi], ids=["TickCollector", "MultiSymbol"])
async def test_dns_failure_on_reissue_reconnects_instead_of_dying(tmp_path, recorded, make):
    logged, slept = recorded
    issuer, calls = _issuer(
        [
            httpx.ConnectError("[Errno 11001] getaddrinfo failed"),  # 10-08 실측 메시지
            httpx.ReadTimeout("timed out"),
        ]
    )
    collector = make(issuer, tmp_path)

    with pytest.raises(ValueError, match="테스트 종료"):  # 세 번째 발급 성공 → 연결 → 정상 종료
        await collector.run_forever()

    assert len(calls) == 3
    assert slept == [5.0, 10.0]  # 연속 실패는 백오프 배증
    disconnects = [msg for tag, msg in logged if tag == "CollectorWSDisconnected"]
    assert len(disconnects) == 1  # 연속 실패는 한 번만 적는다(기존 규칙)
    assert "승인키 재발급 네트워크 실패" in disconnects[0]
    assert "getaddrinfo failed" in disconnects[0]
    assert [tag for tag, _ in logged].count("CollectorWSReconnected") == 1


@pytest.mark.parametrize("make", [_single, _multi], ids=["TickCollector", "MultiSymbol"])
@pytest.mark.parametrize("status", [401, 500])
async def test_http_status_error_still_propagates(tmp_path, recorded, make, status):
    logged, slept = recorded
    issuer, calls = _issuer([status])
    collector = make(issuer, tmp_path)

    with pytest.raises(httpx.HTTPStatusError):
        await collector.run_forever()

    assert len(calls) == 1
    assert slept == []
    assert not any(tag == "CollectorWSDisconnected" for tag, _ in logged)
