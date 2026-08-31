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


# ------------------------------- 합성기 축 (2026-08-05 장중 점검 P0-2)


def test_default_components_include_the_bar_composer():
    """2026-08-05 장중, 상위 Horizon 봉의 3~17%가 사라지는 동안 상태판 세 축은 전부 OK였다.

    나머지 축이 **신선도**("최근에 받았나")를 재는 반면 합성 손상은 "받은 것을 온전히
    합쳤나"라서, 볼 축이 아예 없었다. 화면(`ui/app.py`의 `_HEALTH_COMPONENTS`)과 목록이
    갈리면 조용히 한쪽에서만 사라지므로 함께 확인한다.
    """
    from messiah.ops.status_board import DEFAULT_COMPONENTS
    from messiah.ui.app import _HEALTH_COMPONENTS

    assert "l1.composer" in DEFAULT_COMPONENTS
    assert set(DEFAULT_COMPONENTS) == {name for name, _label in _HEALTH_COMPONENTS}


# ------------------------------------------------- 코드 버전 축 (2026-08-05 3차, P0-1)


def test_snapshot_records_which_code_reported_the_state():
    """화면이 죽으면 이 파일이 유일한 관측 수단인데(모듈 docstring), 버전 축이 없으면
    "구버전이 보낸 초록"과 "최신 코드가 보낸 초록"이 파일에서도 똑같이 보인다."""
    cache = StateCache()
    cache.update(
        health_cache_key("l1.collector"),
        Health(component="l1.collector", level=HealthLevel.OK, git_sha="bb60f19"),
    )

    snapshot = _board(cache).snapshot()

    assert snapshot["components"]["l1.collector"]["git_sha"] == "bb60f19"
    assert "code_version" in snapshot
    assert snapshot["code_version"]["process_git_sha"]


def test_snapshot_flags_a_component_running_older_code(monkeypatch):
    monkeypatch.setattr("messiah.ops.status_board.head_git_sha", lambda: "8810867")
    monkeypatch.setattr("messiah.ops.status_board.PROCESS_GIT_SHA", "8810867")
    cache = StateCache()
    cache.update(
        health_cache_key("l1.collector"),
        Health(component="l1.collector", level=HealthLevel.OK, git_sha="bb60f19"),
    )

    snapshot = _board(cache).snapshot()

    assert snapshot["code_version"]["stale"] is True
    assert "bb60f19" in snapshot["code_version"]["summary"]


def test_terminal_output_surfaces_version_drift(monkeypatch):
    """터미널이 화면 없을 때의 마지막 수단이다 — 여기서도 어긋남이 눈에 띄어야 한다."""
    monkeypatch.setattr("messiah.ops.status_board.head_git_sha", lambda: "8810867")
    monkeypatch.setattr("messiah.ops.status_board.PROCESS_GIT_SHA", "bb60f19")

    text = format_snapshot(_board(StateCache()).snapshot())

    assert "⚠" in text
    assert "코드 불일치" in text


def test_terminal_output_survives_a_snapshot_without_the_version_block():
    """이 필드가 생기기 전에 쓰인 스냅샷 파일도 계속 읽혀야 한다(장후 리뷰가 과거 파일을 본다)."""
    text = format_snapshot({"generated_at_kst": "2026-08-03T15:00:00+09:00", "components": {}})

    assert "MESSIAH 상태판" in text


# --------- 「저장소 상태와 다르다」로 뜻을 넓힌다 (2026-08-31 F-76 · 이상점 1-2)
#
# 미커밋 소스 5파일이 닷새째 돌던 아침, 이 축은 `stale: false` · "전 프로세스 동일"을 냈다.
# 두 SHA만 보면 참이고 실제로 도는 바이트를 물으면 거짓인 문장이다.


def _axis(*, sha_stale: bool, dirty_files):
    from messiah.core.version import VersionDrift
    from messiah.ops.status_board import code_version_axis

    drift = (
        VersionDrift(True, "코드 불일치 — HEAD 5755804 / 화면 bb60f19")
        if sha_stale
        else VersionDrift(False, "코드 5755804 — 전 프로세스 동일")
    )
    return code_version_axis(drift=drift, dirty_files=dirty_files, head_sha="5755804")


def test_a_clean_worktree_on_the_committed_sha_is_not_stale():
    axis = _axis(sha_stale=False, dirty_files=0)
    assert axis["stale"] is False
    assert axis["stale_reason"] is None
    assert "미커밋" not in axis["summary"], "0건이면 조용해야 한다 — 매일 울면 아무도 안 읽는다"


def test_uncommitted_source_is_stale_even_when_the_sha_matches():
    """2026-08-31 아침의 실제 상태 — 이 한 줄이 그날 거짓을 말했다."""
    axis = _axis(sha_stale=False, dirty_files=5)
    assert axis["stale"] is True
    assert axis["stale_reason"] == "worktree_dirty"
    assert axis["summary"] == "코드 5755804 + 미커밋 5파일 — 저장소와 다름"
    # SHA 축 단독 판정은 버리지 않는다 — 옛 뜻으로 읽던 쪽이 사후에 가를 수 있어야 한다.
    assert axis["sha_stale"] is False


def test_both_axes_drifting_are_named_separately():
    axis = _axis(sha_stale=True, dirty_files=5)
    assert axis["stale"] is True
    assert axis["stale_reason"] == "both"
    # SHA 쪽 문장을 **덮지 않는다** — 두 어긋남은 원인이 다르다.
    assert "코드 불일치" in axis["summary"]
    assert "미커밋 5파일" in axis["summary"]


def test_unmeasured_dirt_is_not_folded_into_clean_or_dirty():
    """미측정을 0으로도 1로도 접지 않는다 (L18) — SHA 축의 판정만 남는다."""
    axis = _axis(sha_stale=False, dirty_files=None)
    assert axis["stale"] is False
    assert axis["stale_reason"] is None
    assert axis["worktree_dirty"] is None
    assert "미커밋 미측정" in axis["summary"]

    dirty_and_mismatched = _axis(sha_stale=True, dirty_files=None)
    assert dirty_and_mismatched["stale"] is True
    assert dirty_and_mismatched["stale_reason"] == "sha_mismatch"


def test_the_terminal_line_says_it_once_not_twice():
    """요약이 이미 담은 사실을 렌더러가 또 붙이면 사람은 한 번도 안 읽는다."""
    from messiah.ops.status_board import format_snapshot

    line = format_snapshot({"code_version": _axis(sha_stale=False, dirty_files=5)})
    assert line.count("미커밋") == 1
