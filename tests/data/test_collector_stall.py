"""조용한 스톨 탐지 검증 (2026-07-30 추가).

실측 근거는 `data/collector.py` 모듈 docstring 참고 — 2026-07-28·07-29 두 번 모두 소켓은
살아있는데 틱만 30분간 끊겼고, 예외가 안 나니 재연결 경로가 아예 안 탔다.

실시간 대기 없이 검증하려고 시계(monotonic)와 sleep을 전부 주입한다 — sleep이 호출될 때마다
가짜 시계를 그만큼 앞으로 돌리는 방식이라, 30초 격자 4번이면 120초 임계가 실제로 흐른 것과
동일하게 재현된다(`core/docker_bootstrap.py`류의 주입 원칙과 동일).
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from messiah.broker.kis import tr_codes
from messiah.broker.kis.credentials import KISCredentials
from messiah.broker.kis.ws_client import ApprovalKeyIssuer
from messiah.core.messages import HealthLevel
from messiah.data.archiver import ParquetArchiver
from messiah.data.collector import (
    _WS_DISCONNECT_ERRORS,
    TickCollector,
    TickStallError,
    _StallWatchdog,
)
from messiah.data.normalizer import parse_futures_tick

_TICK_SIZE = Decimal("0.02")
_SUBSCRIBE_ACK = json.dumps(
    {
        "header": {"tr_id": "H0IFCNT0", "tr_key": "A05608", "encrypt": "N"},
        "body": {"rt_cd": "0", "msg_cd": "OPSP0000", "msg1": "SUBSCRIBE SUCCESS"},
    }
)
_REAL_TICK = (
    "0|H0IFCNT0|001|A05608^152953^-0.54^5^-0.05^1080.30^1131.06^1146.44^1079.36^1^134935^"
    "7587962447^1082.72^-0.91^-0.22^0.00^0.00^5.22^76672^571^000000^5^-50.76^000000^5^-66.14^"
    "000000^2^0.94^0.49^94.95^-2.42^0^1.51^1080.30^1080.04^1^1^60234^56822^-3412^69150^65657^"
    "997^478^106.25^0^1091.10^1069.50^2"
)


class _StopLoop(Exception):
    """테스트에서 무한 감시 루프를 끊는 신호 — 프로덕션 코드는 이 예외를 모른다."""


class _FakeClock:
    """sleep 호출마다 요청된 만큼 시계를 앞으로 돌린다 — 실제 대기 없음."""

    def __init__(self, *, max_sleeps: int = 50) -> None:
        self.now = 1000.0
        self.sleeps = 0
        self._max_sleeps = max_sleeps
        self.on_sleep = None

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        if self.sleeps >= self._max_sleeps:
            raise _StopLoop
        self.sleeps += 1
        self.now += seconds
        if self.on_sleep is not None:
            self.on_sleep(self)


def _watchdog(clock: _FakeClock, *, timeout: float = 120.0, interval: float = 30.0):
    return _StallWatchdog(
        timeout_seconds=timeout,
        check_interval_seconds=interval,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


# ---------------------------------------------------------------- _StallWatchdog 단위


async def test_does_not_trip_before_the_first_tick_arrives():
    """콜드스타트 가드 — 08:35 기동 후 첫 틱(실측 08:45)까지 10분을 스톨로 오판하면 무한
    재연결 루프에 빠진다."""
    clock = _FakeClock(max_sleeps=40)  # 가짜 시계로 20분 경과
    watchdog = _watchdog(clock)

    with pytest.raises(_StopLoop):  # TickStallError가 아니라 루프 소진으로 끝나야 한다
        await watchdog.run_until_stalled(describe="A05608")


async def test_trips_once_timeout_elapses_after_a_tick():
    clock = _FakeClock()
    watchdog = _watchdog(clock, timeout=120.0, interval=30.0)
    watchdog.mark_tick()

    with pytest.raises(TickStallError):
        await watchdog.run_until_stalled(describe="A05608")

    assert clock.sleeps == 4  # 30초 격자 4번 = 120초 임계 도달


async def test_does_not_trip_while_ticks_keep_arriving():
    """정상 장중 — 매 점검 사이에 틱이 들어오면 영원히 안 터져야 한다."""
    clock = _FakeClock(max_sleeps=20)
    watchdog = _watchdog(clock)
    watchdog.mark_tick()
    clock.on_sleep = lambda _clock: watchdog.mark_tick()

    with pytest.raises(_StopLoop):
        await watchdog.run_until_stalled(describe="A05608")


async def test_does_not_trip_just_below_the_threshold():
    clock = _FakeClock(max_sleeps=3)  # 90초까지만 진행 — 임계 120초 미달
    watchdog = _watchdog(clock, timeout=120.0, interval=30.0)
    watchdog.mark_tick()

    with pytest.raises(_StopLoop):
        await watchdog.run_until_stalled(describe="A05608")


async def test_reset_clears_the_baseline():
    """재연결 시 이전 연결의 마지막 틱을 기준선으로 쓰면, 새 연결이 첫 틱을 받기도 전에
    즉시 스톨로 오판한다."""
    clock = _FakeClock(max_sleeps=10)
    watchdog = _watchdog(clock)
    watchdog.mark_tick()
    assert watchdog.seen_first_tick is True

    watchdog.reset()
    assert watchdog.seen_first_tick is False

    with pytest.raises(_StopLoop):
        await watchdog.run_until_stalled(describe="A05608")


def test_watchdog_is_disabled_when_timeout_is_zero():
    clock = _FakeClock()
    assert _watchdog(clock, timeout=0.0).enabled is False
    assert _watchdog(clock, timeout=120.0).enabled is True


def test_tick_stall_error_reuses_the_existing_reconnect_path():
    """`TickStallError`가 `_WS_DISCONNECT_ERRORS`에 안 걸리면 재연결이 아니라 프로세스
    사망으로 이어진다 — 상속 관계를 명시적으로 못박는다."""
    assert issubclass(TickStallError, ConnectionError)
    assert issubclass(TickStallError, OSError)
    assert isinstance(TickStallError("x"), _WS_DISCONNECT_ERRORS)


# ---------------------------------------------------------------- TickCollector 결선


class _HangingConnection:
    """구독 응답과 준비된 프레임을 흘린 뒤, 그 다음부터는 영원히 응답하지 않는다 —
    "소켓은 열려 있는데 틱만 안 오는" 실측 장애를 그대로 재현."""

    def __init__(self, incoming: list[str]) -> None:
        self.sent: list[str] = []
        self._incoming = list(incoming)
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        if self._incoming:
            return self._incoming.pop(0)
        await asyncio.Event().wait()  # 영원히 대기 — 끊기지도, 오지도 않음
        raise AssertionError("도달 불가")

    async def close(self) -> None:
        self.closed = True


class _FakeConnectCM:
    def __init__(self, conn: _HangingConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _HangingConnection:
        return self._conn

    async def __aexit__(self, *exc: object) -> None:
        await self._conn.close()


def _creds() -> KISCredentials:
    return KISCredentials(app_key="key", app_secret="secret", account_no="12345678", is_mock=True)


def _approval_issuer() -> ApprovalKeyIssuer:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"approval_key": "APV-TEST"})

    return ApprovalKeyIssuer(_creds(), client=httpx.Client(transport=httpx.MockTransport(handler)))


def _collector(tmp_path: Path, conn: _HangingConnection, clock: _FakeClock) -> TickCollector:
    return TickCollector(
        creds=_creds(),
        symbol="A05608",
        tr_id=tr_codes.WS_TR_FUTURES_CONTRACT,
        parse_tick=parse_futures_tick,
        tick_size=_TICK_SIZE,
        archiver=ParquetArchiver(tmp_path),
        approval_issuer=_approval_issuer(),
        ws_connect=lambda uri: _FakeConnectCM(conn),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


async def test_run_once_raises_tick_stall_when_socket_hangs(tmp_path: Path):
    """회귀 방지 — 예전 구현은 여기서 30분이고 한 시간이고 조용히 매달려 있었다."""
    conn = _HangingConnection([_SUBSCRIBE_ACK, _REAL_TICK])
    clock = _FakeClock()

    with pytest.raises(TickStallError):
        await _collector(tmp_path, conn, clock).run_once()

    assert conn.closed is True  # async with가 소켓을 닫아 재연결 준비를 마친다


async def test_run_once_does_not_stall_out_before_the_first_tick(tmp_path: Path):
    """장전 웜업 구간(기동 08:35, 첫 틱 08:45)이 스톨로 오인되면 안 된다."""
    conn = _HangingConnection([_SUBSCRIBE_ACK])  # 틱 없이 계속 대기
    clock = _FakeClock(max_sleeps=40)  # 20분 경과

    with pytest.raises(_StopLoop):  # TickStallError가 아니어야 한다
        await _collector(tmp_path, conn, clock).run_once()


async def test_stall_disabled_collector_keeps_hanging(tmp_path: Path):
    """감시를 끄면(임계 0) 예전 동작 그대로 — 옵트아웃 경로가 살아있는지 확인."""
    conn = _HangingConnection([_SUBSCRIBE_ACK, _REAL_TICK])
    collector = TickCollector(
        creds=_creds(),
        symbol="A05608",
        tr_id=tr_codes.WS_TR_FUTURES_CONTRACT,
        parse_tick=parse_futures_tick,
        tick_size=_TICK_SIZE,
        archiver=ParquetArchiver(tmp_path),
        approval_issuer=_approval_issuer(),
        ws_connect=lambda uri: _FakeConnectCM(conn),
        stall_timeout_seconds=0.0,
    )

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(collector.run_once(), timeout=0.2)


async def test_run_forever_reconnects_after_a_stall(tmp_path: Path):
    """스톨 → 기존 백오프 재연결 경로 재사용 확인 (새 복구 메커니즘을 만든 게 아니다)."""
    connections = [
        _HangingConnection([_SUBSCRIBE_ACK, _REAL_TICK]),  # 1회차: 틱 후 매달림 → 스톨
        _HangingConnection([]),  # 2회차: 재연결 성공 후 다시 매달림
    ]
    opened: list[_HangingConnection] = []
    clock = _FakeClock(max_sleeps=8)

    def _connect(uri: str):
        conn = connections[min(len(opened), len(connections) - 1)]
        opened.append(conn)
        return _FakeConnectCM(conn)

    collector = TickCollector(
        creds=_creds(),
        symbol="A05608",
        tr_id=tr_codes.WS_TR_FUTURES_CONTRACT,
        parse_tick=parse_futures_tick,
        tick_size=_TICK_SIZE,
        archiver=ParquetArchiver(tmp_path),
        approval_issuer=_approval_issuer(),
        ws_connect=_connect,
        reconnect_initial_backoff_seconds=0.0,
        reconnect_max_backoff_seconds=0.0,
        stall_timeout_seconds=120.0,
        stall_check_interval_seconds=30.0,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    with pytest.raises(_StopLoop):
        await collector.run_forever()

    assert len(opened) >= 2  # 스톨 후 실제로 다시 연결했다
    assert connections[0].closed is True


# ---------------------------------------------------------------- 자가 헬스 판정 (고도화 1/4)


def _health_collector(tmp_path: Path, clock: _FakeClock) -> TickCollector:
    return _collector(tmp_path, _HangingConnection([]), clock)


def test_health_is_ok_while_warming_up_before_the_first_tick(tmp_path: Path):
    """기준선이 없는 것과 끊긴 것은 다르다 — 장 개시 전 10분이 매일 CRITICAL로 뜨면 안 된다."""
    status = _health_collector(tmp_path, _FakeClock()).health()

    assert status.level is HealthLevel.OK
    assert "웜업" in status.detail


def test_health_walks_ok_warn_critical_as_ticks_go_silent(tmp_path: Path):
    clock = _FakeClock()
    collector = _health_collector(tmp_path, clock)
    collector._watchdog.mark_tick()

    assert collector.health().level is HealthLevel.OK
    clock.now += 60.0  # 임계(120초)의 절반 = WARN 시작점
    assert collector.health().level is HealthLevel.WARN
    clock.now += 60.0  # 총 120초 — 강제 재연결이 걸리는 바로 그 지점
    assert collector.health().level is HealthLevel.CRITICAL


def test_health_recovers_after_a_tick_arrives(tmp_path: Path):
    clock = _FakeClock()
    collector = _health_collector(tmp_path, clock)
    collector._watchdog.mark_tick()
    clock.now += 200.0
    assert collector.health().level is HealthLevel.CRITICAL

    collector._watchdog.mark_tick()

    assert collector.health().level is HealthLevel.OK


def test_seconds_since_last_tick_distinguishes_never_from_zero(tmp_path: Path):
    clock = _FakeClock()
    collector = _health_collector(tmp_path, clock)

    assert collector.seconds_since_last_tick() is None  # "아직 못 봤다"
    collector._watchdog.mark_tick()
    assert collector.seconds_since_last_tick() == 0.0  # "방금 받았다"
