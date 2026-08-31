"""F-78 — 점검이 저장소에 잠금을 남기지 않는다 (2026-08-31 이상점 1-8).

2026-08-26 F-60은 **수집기의** git 을 화이트리스트로 묶었고 08-27에 합격 판정을 받았다.
그런데 점검 세션 자신이 리포트 근거를 만들려고 부르는 git(`diff --stat`,
`ls-files --others`)은 그 밖이었다 — 두 경로가 있는데 하나만 막았고, 그 사실이
「F-60 통과」 한 줄에 가려졌다. 08-31 아침 08:51:49의 0바이트 락이 **7시간 6분**
방치돼 그날 밤 예정된 여덟 항목이 전부 막혔다.

여기서 붙잡는 성질은 넷이다:
  ① 세션이 필요로 하던 두 출력(`diff --stat` · `ls-files --others`)을 **수집기가** 낸다.
  ② 그래도 인덱스에 닿는 계열은 여전히 거절된다 — 화이트리스트가 넓어진 것이지
     열린 것이 아니다.
  ③ 수집기 실행 전후로 `.git/index.lock` 이 늘지 않는다.
  ④ 락이 생기면 **즉시 회수**하고 §9 적신호로 올린다 — 발견에서 그치면 그날 밤이 막힌다.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

KST = timezone(timedelta(hours=9))
_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / ".claude" / "skills" / "messiah-daily-check" / "scripts" / "collect_evidence.py"


@pytest.fixture(scope="module")
def ce():
    spec = importlib.util.spec_from_file_location("_collect_evidence_f78", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fake_repo(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


# ---------- ① 세션이 부르던 두 출력을 수집기가 낸다


def test_the_two_subcommands_the_session_used_are_now_allowed(ce):
    """`ls-files` 가 없으면 세션은 다시 자기 손으로 git 을 부른다 — 금지만으로는 안 된다."""
    assert "ls-files" in ce._GIT_READONLY_SUBCOMMANDS
    assert "diff" in ce._GIT_READONLY_SUBCOMMANDS


def test_index_touching_subcommands_are_still_refused(ce, tmp_path):
    """화이트리스트가 **넓어진 것이지 열린 것이 아니다.** `add -n` 한 번이면 재발한다."""
    root = _fake_repo(tmp_path)
    for args in (["add", "-n", "."], ["commit", "-m", "x"], ["stash"], ["checkout", "master"]):
        out = ce.run_git(root, args)
        assert "화이트리스트 밖" in out, args
    assert not (root / ".git" / "index.lock").exists()


# ---------- ③④ 락 자경


def test_the_collector_leaves_no_lock_behind(ce):
    """라이브 검증의 단위 테스트판 — 실제 저장소에 읽기 전용 호출을 걸고 전후를 본다."""
    lock = _ROOT / ".git" / "index.lock"
    before = lock.exists()
    ce.run_git(_ROOT, ["diff", "--ignore-all-space", "--stat", "HEAD", "--", "src"])
    ce.run_git(_ROOT, ["ls-files", "--others", "--exclude-standard"])
    assert lock.exists() is before, "읽기 전용 호출이 락을 남겼다 — 화이트리스트를 좁혀야 한다"


def test_a_lock_this_call_created_is_reclaimed_at_once(ce, tmp_path, monkeypatch):
    """발견에서 그치면 그날 밤이 통째로 막힌다 — 만든 쪽이 치운다."""
    root = _fake_repo(tmp_path)
    lock = root / ".git" / "index.lock"

    def _fake_run(*a, **k):
        lock.write_bytes(b"")  # 호출 도중에 0바이트 락이 생긴 상황을 만든다

        class _P:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return _P()

    monkeypatch.setattr(ce.subprocess, "run", _fake_run)
    ce.GIT_SELF_LOCK_EVENTS.clear()
    out = ce.run_git(root, ["status", "--porcelain"])

    assert not lock.exists(), "스스로 만든 0바이트 락은 즉시 회수한다"
    assert "F-78 자경" in out
    assert len(ce.GIT_SELF_LOCK_EVENTS) == 1
    assert "즉시 회수 완료" in ce.GIT_SELF_LOCK_EVENTS[0]
    ce.GIT_SELF_LOCK_EVENTS.clear()


def test_a_non_empty_lock_is_left_alone(ce, tmp_path, monkeypatch):
    """0바이트가 아니면 인덱스 쓰기가 실제로 진행된 것이다 — **다른 프로세스**일 수 있다."""
    root = _fake_repo(tmp_path)
    lock = root / ".git" / "index.lock"

    def _fake_run(*a, **k):
        lock.write_bytes(b"something")

        class _P:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return _P()

    monkeypatch.setattr(ce.subprocess, "run", _fake_run)
    ce.GIT_SELF_LOCK_EVENTS.clear()
    ce.run_git(root, ["status", "--porcelain"])

    assert lock.exists(), "남의 락을 지우면 진행 중인 커밋을 깬다"
    assert "회수 보류" in ce.GIT_SELF_LOCK_EVENTS[0]
    ce.GIT_SELF_LOCK_EVENTS.clear()


# ---------- §9 「점검 자신이 범인인가」


def test_a_lock_born_inside_a_check_window_names_the_check(ce):
    """08-31의 회귀 픽스처 — 수집 08:51:10 / 락 08:51:49, **39초 간격**이었다."""
    now = datetime(2026, 8, 31, 15, 58, 0, tzinfo=KST)
    born = datetime(2026, 8, 31, 8, 51, 49, tzinfo=KST)
    lk = {"age_sec": (now - born).total_seconds()}

    note = ce.lock_blame(lk, now)
    assert "08:51:49" in note
    assert "점검 실행 창" in note and "08:50" in note
    # **단정하지 않는다** — 창 안이라는 것은 정황이지 증거가 아니다.
    assert "가능성이 높다" in note


def test_a_lock_born_outside_the_windows_says_so(ce):
    """창 밖이면 그 사실도 적는다 — 「점검이 아닌 무언가」가 곧 다음 질문이다."""
    now = datetime(2026, 8, 31, 15, 58, 0, tzinfo=KST)
    born = datetime(2026, 8, 31, 11, 20, 0, tzinfo=KST)
    note = ce.lock_blame({"age_sec": (now - born).total_seconds()}, now)
    assert "창 밖" in note and "점검이 아닌 무언가" in note


def test_an_unmeasured_lock_age_is_not_guessed(ce):
    """못 잰 것을 「창 밖」으로 적으면 점검이 무죄가 된다 (L18)."""
    assert "미측정" in ce.lock_blame({"age_sec": None}, datetime.now(KST))


# ---------- 규칙이 문서에 있는가 (08-31 장전은 규칙이 없어서 어겼다)


def test_the_rule_is_written_down_not_just_implemented():
    """`SKILL.md` 에 금지가 없으면 다음 세션이 또 부른다 — 그날 세션은 규칙을 어긴 게 아니었다."""
    skill = (_ROOT / ".claude" / "skills" / "messiah-daily-check" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "점검 세션은 `git` 을 직접 실행하지 않는다" in skill
    assert "ls-files" in skill


def test_the_self_check_carries_the_lock_facts():
    """스테일 락에서 `git status` 는 rc=0 이다 — 자가점검이 안 실으면 볼 창구가 없다."""
    sys.path.insert(0, str((_ROOT / "scripts").resolve()))
    import self_check

    note = self_check._index_lock_note()
    assert note.startswith(";")
    assert "인덱스락" in note
