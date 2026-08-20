"""커밋과 실린 코드가 같다고 말하는 계기가 커밋을 안 봤다 — 2026-08-20 F-2 · G-C.

2026-08-19 저녁 구현 세션이 `dev_memory`에 "완료"라 적고 커밋 단계를 빠뜨렸다. 다음 날 아침
`status_snapshot.json`의 `code_version.stale`은 **false**였다 — 두 SHA(프로세스가 적재한 것,
작업트리 HEAD)가 같으니 그 자체로는 옳은 값이다. 그리고 그날 개장이 통째로 옛 코드로 갔다.

이 저장소는 워킹트리를 **직접 임포트**한다(배치가 `python scripts/run_l1_daily.py`를 부른다).
즉 실제로 실린 것은 커밋이 아니라 **기동 시점의 파일 내용**이고, 두 SHA는 그 축을 아예 안 본다.
직전 커밋 `50eff6c`가 같은 유형이라 **이틀 연속**이었는데, 그 사이 어느 계기도 이것을 말하지
않았다 — 워킹트리는 관측 대상이 아니었기 때문이다.

여기서 고정하는 계약 셋:

1. 미커밋 건수를 **못 잰 것**과 **0건**을 안 합친다 (L18).
2. 스냅샷이 그 값을 싣고, 사람이 읽는 요약 줄에 **같이** 나온다.
3. `SessionStart`가 소스 최신 수정 시각을 싣는다 — SHA로는 "08:20 l1은 실었고 08:25 g2는
   못 실었다"는 절반 상태를 말할 수 없다 (G-C).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from messiah.core import version as ver
from messiah.ops.status_board import format_snapshot


def test_unmeasured_dirty_count_is_not_zero(monkeypatch) -> None:
    """git 조회가 실패한 날을 "깨끗하다"로 적으면 그 계기는 영원히 조용해진다 (L18)."""
    ver.reset_dirty_cache()
    monkeypatch.setattr(ver, "_read_dirty_count", lambda: None)
    assert ver.worktree_dirty_files() is None
    ver.reset_dirty_cache()


def test_dirty_count_is_cached_within_ttl(monkeypatch) -> None:
    """렌더 루프가 5초 격자라 캐시가 없으면 분당 12번 `git status`를 띄운다."""
    ver.reset_dirty_cache()
    calls = {"n": 0}

    def _counted() -> int:
        calls["n"] += 1
        return 3

    monkeypatch.setattr(ver, "_read_dirty_count", _counted)
    now = datetime(2026, 8, 20, 15, 0, tzinfo=timezone.utc)
    assert ver.worktree_dirty_files(now=now) == 3
    assert ver.worktree_dirty_files(now=now + timedelta(seconds=30)) == 3
    assert calls["n"] == 1, "TTL 안에서는 다시 묻지 않는다"
    assert ver.worktree_dirty_files(now=now + timedelta(seconds=90)) == 3
    assert calls["n"] == 2, "TTL이 지나면 다시 잰다"
    ver.reset_dirty_cache()


def _snapshot(dirty_files: int | None) -> dict:
    return {
        "generated_at_kst": "2026-08-20T15:40:00+09:00",
        "code_version": {
            "process_git_sha": "abc1234",
            "head_git_sha": "abc1234",
            "stale": False,
            "summary": "코드 abc1234 — 전 프로세스 동일",
            "worktree_dirty_files": dirty_files,
            "worktree_dirty": None if dirty_files is None else dirty_files > 0,
        },
        "components": {},
    }


def test_status_board_says_the_uncommitted_count() -> None:
    """**같은 줄에** 붙는다 — "코드 버전"을 읽은 사람이 그 아래를 안 볼 수 있다."""
    text = format_snapshot(_snapshot(4))
    line = next(line for line in text.splitlines() if "코드 abc1234" in line)
    assert "미커밋 4파일" in line


def test_status_board_distinguishes_unmeasured_from_clean() -> None:
    assert "미커밋 미측정" in format_snapshot(_snapshot(None))
    clean = format_snapshot(_snapshot(0))
    assert "미커밋" not in clean, "0건이면 조용해야 한다 — 매일 울면 아무도 안 읽는다"


def test_session_start_carries_source_mtime(monkeypatch, tmp_path) -> None:
    """G-C — 커밋 SHA로는 「기동 뒤에 소스가 바뀌었다」를 말할 수 없다."""
    from messiah.core import logging as mlog

    log_file = tmp_path / "session.log"
    monkeypatch.setattr(mlog, "log_path_for", lambda *a, **k: log_file, raising=False)
    stamp = datetime(2026, 8, 20, 6, 49, 55, tzinfo=timezone.utc)
    monkeypatch.setattr(ver, "source_mtime_max", lambda root=".": stamp)

    records: list[dict] = []
    monkeypatch.setattr(mlog, "log", lambda tag, msg, **f: records.append({"tag": tag, **f}))
    mlog.session_start("test-instance")

    assert len(records) == 1
    assert records[0]["tag"] == "SessionStart"
    assert records[0]["source_mtime_max"] == stamp.isoformat()
    # 필드가 JSON 직렬화 가능해야 한다 — 로그는 JSONL이다.
    json.dumps(records[0])


def test_source_mtime_is_none_when_there_is_nothing_to_measure(tmp_path) -> None:
    """`src/`도 `scripts/`도 없는 디렉터리 — 0이 아니라 None이다 (L18)."""
    assert ver.source_mtime_max(tmp_path) is None


def test_source_mtime_picks_the_latest(tmp_path) -> None:
    import os

    src = tmp_path / "src"
    src.mkdir()
    older = src / "a.py"
    newer = src / "b.py"
    older.write_text("x", encoding="utf-8")
    newer.write_text("y", encoding="utf-8")
    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_700_000_500, 1_700_000_500))
    latest = ver.source_mtime_max(tmp_path)
    assert latest is not None
    assert latest.timestamp() == 1_700_000_500
