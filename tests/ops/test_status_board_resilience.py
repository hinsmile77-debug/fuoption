"""상태판이 한 번 미끄러진 것과 영영 죽은 것 — 2026-08-14 F-10 · F-13.

`StatusSnapshotWriteFailed` 하나가 두 사건을 겸하고 있었다: "이번 주기를 놓쳤다"(파일 경합,
회복 가능)와 "상태판이 그날 내내 죽었다"(프로세스 중단). 같은 이름·같은 심각도로 나가면
사람이 둘을 못 가른다(R6: 태그 1개 = 심각도 1개).

그리고 경합 자체는 재시도로 대부분 사라진다 — 2026-08-14에 15초 주기 하루치 중 2회 났고,
전부 Windows `WinError 5`(다른 프로세스가 그 파일을 연 순간)였다.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from messiah.core import logging as mlog
from messiah.core.state_cache import StateCache
from messiah.ops import status_board
from messiah.ops.status_board import StatusBoard, run_status_board_forever


def _board() -> StatusBoard:
    """진짜 `StateCache`를 쓴다 — 가짜를 얇게 만들면 이 테스트가 스냅샷 구성 변화를 놓친다."""
    return StatusBoard(StateCache(), components=())


# ------------------------------------------------------------------ 재시도


def test_replace_is_retried_and_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """경합은 대개 수십 ms 안에 풀린다 — 한 번 튕겼다고 주기를 통째로 버리지 않는다."""
    target = tmp_path / "status_snapshot.json"
    calls = {"n": 0}
    real_replace = status_board.os.replace

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(5, "액세스가 거부되었습니다")
        return real_replace(src, dst)

    monkeypatch.setattr(status_board.os, "replace", flaky)
    _board().write(target, sleep=lambda _s: None)

    assert calls["n"] == 3
    assert json.loads(target.read_text(encoding="utf-8")) is not None


def test_last_attempt_raises_and_cleans_up_the_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """끝까지 실패하면 삼키지 않는다 — 삼키면 호출부가 "썼다"고 믿는다.

    임시 파일은 반드시 지운다. 남으면 매 주기 쓰레기가 쌓여 디렉터리가 그 자체로 사고가 된다.
    """
    target = tmp_path / "status_snapshot.json"

    def always_denied(src, dst):
        raise PermissionError(5, "액세스가 거부되었습니다")

    monkeypatch.setattr(status_board.os, "replace", always_denied)

    with pytest.raises(PermissionError):
        _board().write(target, sleep=lambda _s: None)

    assert not target.exists()
    assert list(tmp_path.glob("*.tmp")) == []


# ------------------------------------------------------------------ 태그 분리


async def _run_until(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failures: int, ticks: int):
    """`_write_forever`를 `ticks`회 돌리고 남은 로그를 돌려준다."""
    logged: list[tuple[str, dict]] = []
    monkeypatch.setattr(mlog, "log", lambda tag, msg, **f: logged.append((tag, f)))

    state = {"n": 0}

    # 클래스에 붙이므로 `self`를 받는다 — 안 받으면 TypeError가 나고, 그건 바깥 `except
    # Exception`에 먹혀 테스트가 조용히 통과 아닌 통과를 한다(구현 중 실제로 그랬다).
    def write(_self, _path, **_kwargs):
        state["n"] += 1
        if state["n"] <= failures:
            raise OSError(5, "액세스가 거부되었습니다")

    monkeypatch.setattr(StatusBoard, "write", write)

    async def sleeper(_seconds):
        if state["n"] >= ticks:
            raise asyncio.CancelledError
        return None

    class _Bus:
        async def subscribe(self, *_a, **_k):
            return None

    class _Subscriber:
        async def run_forever(self):
            await asyncio.Event().wait()  # 구독은 이 테스트의 관심사가 아니다

    monkeypatch.setattr(status_board, "CacheSubscriber", lambda *a, **k: _Subscriber())
    with pytest.raises(asyncio.CancelledError):
        await run_status_board_forever(
            _Bus(), symbol="A05609", path=tmp_path / "s.json", sleep=sleeper
        )
    return logged


async def test_one_slip_is_not_a_stall(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    logged = await _run_until(monkeypatch, tmp_path, failures=1, ticks=4)
    tags = [t for t, _ in logged]
    assert "StatusSnapshotWriteFailed" in tags
    assert "StatusSnapshotStalled" not in tags  # 한 번 미끄러진 것은 멈춘 것이 아니다


async def test_consecutive_failures_are_announced_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logged = await _run_until(monkeypatch, tmp_path, failures=8, ticks=8)
    tags = [t for t, _ in logged]
    # 계속 울면 그 자체가 잡음이 된다 — 한 번만 운다.
    assert tags.count("StatusSnapshotStalled") == 1


async def test_recovery_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """멈췄다고만 하면 언제 풀렸는지를 모른다."""
    logged = await _run_until(monkeypatch, tmp_path, failures=5, ticks=8)
    tags = [t for t, _ in logged]
    assert "StatusSnapshotStalled" in tags
    assert "StatusSnapshotResumed" in tags


# ------------------------------------------------------------------ 중첩 세션


def test_nested_child_does_not_claim_a_session_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """배치가 부른 자식이 `SessionStart`를 찍으면 분석 도구가 재기동으로 읽는다 (F-13)."""
    logged: list[str] = []
    monkeypatch.setattr(mlog, "log", lambda tag, msg, **f: logged.append(tag))

    monkeypatch.delenv(mlog.NESTED_SESSION_ENV, raising=False)
    mlog.session_start("messiah-dev-01")
    assert logged == ["SessionStart"]

    logged.clear()
    monkeypatch.setenv(mlog.NESTED_SESSION_ENV, "1")
    mlog.session_start("messiah-dev-01")
    assert logged == ["NestedSessionStart"]
