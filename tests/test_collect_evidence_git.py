"""점검 수집기가 **마운트에서 git을 쓰지 않는다** (2026-08-26 F-60 · F-61 · 이상점 1-6·1-9).

2026-08-24·25·26 사흘 연속으로 점검 세션의 git 호출이 `.git/index.lock` 을 남겼고, 마운트
파일시스템이 unlink 를 거부해 **저장소가 커밋 불가 상태**가 됐다(08-24 는 3시간 21분).
원인은 명령이 아니라 실행 위치이며, 플래그로는 닫히지 않는다 — `add -n` 한 번이면 재발한다.

여기서 붙잡는 성질은 셋이다:
  ① HEAD sha·브랜치·커밋 제목이 **git 없이** 나온다.
  ② 인덱스에 닿는 하위명령은 실행 자체가 거절된다.
  ③ 다이제스트 §1 이 「마운트 관측이라 네이티브와 다를 수 있다」를 **스스로** 말한다.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / ".claude"
    / "skills"
    / "messiah-daily-check"
    / "scripts"
    / "collect_evidence.py"
)


@pytest.fixture(scope="module")
def ce():
    spec = importlib.util.spec_from_file_location("_collect_evidence_under_test", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fake_repo(tmp_path: Path) -> Path:
    """`.git` 디렉터리만 손으로 만든다 — git 을 **한 번도 안 띄우고** 읽히는지 보려는 것이다."""
    gitdir = tmp_path / ".git"
    (gitdir / "refs" / "heads").mkdir(parents=True)
    (gitdir / "logs").mkdir(parents=True)
    (gitdir / "HEAD").write_text("ref: refs/heads/master\n", encoding="utf-8", newline="\n")
    (gitdir / "refs" / "heads" / "master").write_text(
        "ffe9d277c19e66683233a9ffe4dfbf228ab5c699\n", encoding="utf-8", newline="\n"
    )
    return tmp_path


def test_head_sha_and_branch_come_from_files(tmp_path: Path, ce) -> None:
    facts = ce.git_head_facts(_fake_repo(tmp_path))

    assert facts["branch"] == "master"
    assert facts["short"] == "ffe9d27"
    assert facts["note"] == ""


def test_packed_ref_is_read_when_the_loose_ref_is_absent(tmp_path: Path, ce) -> None:
    """느슨한 ref 가 없는 저장소(gc 직후)에서도 「미측정」으로 주저앉지 않는다."""
    root = _fake_repo(tmp_path)
    (root / ".git" / "refs" / "heads" / "master").unlink()
    (root / ".git" / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted\n"
        "abc1234567890abc1234567890abc1234567890a refs/heads/master\n",
        encoding="utf-8",
        newline="\n",
    )

    assert ce.git_head_facts(root)["short"] == "abc1234"


def test_a_broken_repo_is_unmeasured_not_guessed(tmp_path: Path, ce) -> None:
    """못 읽으면 **미측정**이다 — 0 도 빈 문자열도 추정치도 아니다(계측 4원칙 ②)."""
    facts = ce.git_head_facts(tmp_path)

    assert facts["sha"] is None
    assert "미측정" in facts["note"]


def test_commit_titles_come_from_the_reflog(tmp_path: Path, ce) -> None:
    root = _fake_repo(tmp_path)
    old = "0" * 40
    new = "1" * 40
    (root / ".git" / "logs" / "HEAD").write_text(
        f"{old} {new} 사람 <a@b.c> 1787725723 +0900\tcommit (amend): [MW0601] 고침\n"
        f"{new} {'2' * 40} 사람 <a@b.c> 1787732291 +0900\tcommit: [MW0601] 나중 것\n"
        f"{'2' * 40} {'3' * 40} 사람 <a@b.c> 1787732300 +0900\tcheckout: moving from x to y\n",
        encoding="utf-8",
        newline="\n",
    )

    rows = ce.git_reflog_commits(root)

    # 최신이 먼저다. `checkout` 은 커밋이 아니므로 빠진다.
    assert [r["subject"] for r in rows] == ["[MW0601] 나중 것", "[MW0601] 고침"]
    assert rows[1]["amend"] is True
    assert rows[0]["at"].strftime("%Y-%m-%d %H:%M") == "2026-08-26 17:18"


def test_missing_reflog_yields_nothing_rather_than_raising(tmp_path: Path, ce) -> None:
    assert ce.git_reflog_commits(_fake_repo(tmp_path)) == []


@pytest.mark.parametrize("args", [["add", "-n", "."], ["commit", "-m", "x"], ["push"], ["fetch"]])
def test_index_touching_subcommands_are_refused(tmp_path: Path, ce, args) -> None:
    """**플래그를 믿지 않는다.** `add -n` 은 dry-run 이어도 락을 만든다 — 2026-08-26 15:02."""
    out = ce.run_git(_fake_repo(tmp_path), args)

    assert "거절" in out
    assert "F-60" in out


def test_readonly_subcommands_are_still_allowed(tmp_path: Path, ce) -> None:
    """읽기 계열까지 막으면 미커밋 목록을 낼 수 없다 — 목적은 **인덱스 보호**이지 금욕이 아니다."""
    out = ce.run_git(_fake_repo(tmp_path), ["status", "--porcelain"])

    assert "거절" not in out
