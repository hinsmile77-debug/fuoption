"""헤드리스 상태판 (2026-08-03 고도화 A).

존재 이유는 하나 — **화면이 죽어도 관측은 계속된다**. 2026-07-30에 UI가 죽고 32분간
아무도 몰랐고, 07-31엔 3시간 무화면이었다. 검증도 "UI 없이 상태를 알 수 있는가"에 맞춘다.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from messiah.core.health import health_cache_key
from messiah.core.messages import CircuitBreakerStatus, Health, HealthLevel
from messiah.core.state_cache import StateCache
from messiah.core.timeutil import now_utc
from messiah.ops.status_board import (
    StatusBoard,
    format_snapshot,
    load_snapshot,
    run_status_board_forever,
)
from messiah.simulator.inprocess_bus import InProcessBus

_NOW = datetime(2026, 8, 3, 5, 0, tzinfo=timezone.utc)


def _board(cache: StateCache, *, ui_probe=None, now=_NOW) -> StatusBoard:
    return StatusBoard(
        cache,
        components=("l1.collector", "g2.pipeline"),
        ui_probe=ui_probe,
        now=lambda: now,
    )


def test_silence_shows_up_as_no_data_not_as_healthy():
    """한 번도 heartbeat를 안 보낸 컴포넌트가 스냅샷에서 **사라지면** 사고가 안 보인다 —
    자리를 고정으로 잡아두고 "데이터 없음"으로 남긴다(`core/health.py` "침묵도 상태다")."""
    snapshot = _board(StateCache()).snapshot()

    assert set(snapshot["components"]) == {"l1.collector", "g2.pipeline"}
    assert snapshot["components"]["l1.collector"]["state"] == "NO_DATA"
    assert snapshot["components"]["l1.collector"]["level"] is None


def test_stale_heartbeat_is_distinguished_from_a_fresh_one():
    """heartbeat가 끊긴 지 오래면 그 프로세스가 죽었거나 멈춘 것이다 — 마지막 값이 OK였다는
    이유로 정상으로 보이면 07-30 사고(죽은 뒤에도 화면은 멀쩡)를 반복한다."""
    cache = StateCache()
    cache.update(
        health_cache_key("l1.collector"),
        Health(component="l1.collector", level=HealthLevel.OK, detail="수신 중"),
    )
    # `StateCache.update()`가 찍는 시각은 실제 벽시계라 기준도 거기서 잡아야 한다 —
    # 고정 시각을 쓰면 나이가 음수가 되어 영원히 STALE이 안 된다(실측으로 확인).
    updated_at = now_utc()

    fresh = _board(cache, now=updated_at).snapshot()
    stale = _board(cache, now=updated_at + timedelta(seconds=120)).snapshot()

    assert fresh["components"]["l1.collector"]["state"] == "OK"
    assert stale["components"]["l1.collector"]["state"] == "STALE"
    assert stale["components"]["l1.collector"]["level"] == "OK"  # 마지막 값은 그대로 보존


def test_snapshot_records_whether_the_ui_itself_is_alive():
    """**화면 없이 화면의 생사를 안다** — 07-30의 32분·07-31의 3시간 무화면을 이 한 줄로
    사후에 알 수 있다."""
    up = _board(StateCache(), ui_probe=lambda: True).snapshot()
    down = _board(StateCache(), ui_probe=lambda: False).snapshot()

    assert up["command_center_ui"] == "UP"
    assert down["command_center_ui"] == "DOWN"


def test_circuit_breaker_state_is_captured():
    cache = StateCache()
    cache.update(
        "CircuitBreakerStatus",
        CircuitBreakerStatus(symbol="A05608", phase="confirmed", gateway_halted=True),
    )

    snapshot = _board(cache).snapshot()

    assert snapshot["circuit_breaker"]["phase"] == "confirmed"
    assert snapshot["circuit_breaker"]["gateway_halted"] is True


# ---------------------------------------------------------------- 파일 왕복


def test_write_is_atomic_and_leaves_no_temp_file(tmp_path: Path):
    """읽는 쪽이 쓰는 도중의 파일을 보면 안 된다 — `data/archiver.py`가 2026-07-30 UI 크래시
    대응으로 도입한 것과 같은 이유·같은 방식(임시 파일 + os.replace)."""
    path = tmp_path / "status_snapshot.json"

    _board(StateCache(), ui_probe=lambda: True).write(path)

    assert json.loads(path.read_text(encoding="utf-8"))["command_center_ui"] == "UP"
    assert list(tmp_path.glob("*.tmp")) == []


def test_missing_snapshot_says_so_instead_of_pretending(tmp_path: Path):
    """장중에 스냅샷이 없다는 건 수집이 안 돌고 있다는 뜻이라 그 자체가 신호다."""
    assert load_snapshot(tmp_path / "없음.json") is None
    assert "상태 스냅샷 없음" in format_snapshot(None)


def test_format_is_readable_without_the_ui(tmp_path: Path):
    cache = StateCache()
    cache.update(
        health_cache_key("l1.collector"),
        Health(component="l1.collector", level=HealthLevel.OK, detail="수신 중"),
    )
    path = tmp_path / "status_snapshot.json"
    _board(cache, ui_probe=lambda: False).write(path)

    text = format_snapshot(load_snapshot(path))

    assert "Command Center UI: 응답 없음" in text
    assert "l1.collector: 정상" in text
    assert "g2.pipeline: 데이터 없음" in text


# ---------------------------------------------------------------- 실제 버스 왕복


def test_subscribes_to_the_real_bus_and_writes_a_snapshot(tmp_path: Path):
    """UI가 하던 구독을 그대로 옮겨온 것이 이 모듈의 요점이라, 실제 버스로 한 바퀴 돈다."""
    path = tmp_path / "status_snapshot.json"

    async def scenario() -> None:
        bus = InProcessBus()
        published = asyncio.Event()
        wrote_once = asyncio.Event()

        async def _sleep(_seconds: float) -> None:
            """실시간 대기 없이 정확히 한 주기만 돌린다 — 발행 전에 쓰면 빈 스냅샷이 나오므로
            발행을 기다렸다 한 번 쓰고, 그 뒤로는 영원히 대기한다."""
            if wrote_once.is_set():
                await asyncio.Event().wait()
            await published.wait()
            wrote_once.set()

        task = asyncio.create_task(
            run_status_board_forever(
                bus,
                symbol="A05608",
                path=path,
                components=("l1.collector",),
                ui_probe=lambda: True,
                sleep=_sleep,
            )
        )
        for _ in range(5):  # 구독이 실제로 붙을 때까지 이벤트 루프를 몇 바퀴 돌린다
            await asyncio.sleep(0)
        await bus.publish(
            "sys.health",
            Health(component="l1.collector", level=HealthLevel.OK, detail="수신 중"),
        )
        published.set()
        for _ in range(50):
            if path.exists():
                break
            await asyncio.sleep(0.01)
        task.cancel()

    asyncio.run(scenario())

    snapshot = load_snapshot(path)
    assert snapshot is not None
    assert snapshot["components"]["l1.collector"]["level"] == "OK"
    assert snapshot["command_center_ui"] == "UP"
