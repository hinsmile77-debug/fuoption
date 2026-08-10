"""dev_memory 갱신 경고 훅 (2026-08-10 C-2).

2026-08-10 13:51 커밋 `ce91b08`은 그날 오전을 잃은 사고의 원인 수정이었고, 코드·테스트·
등록부를 전부 갱신하면서 **`dev_memory/`는 손대지 않았다.** 그 사고가 커밋 메시지에는
있는데 다음 세션이 읽는 곳에는 없었다.

이 훅의 계약은 두 줄이다: **말은 하되 막지는 않는다.**
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_dev_memory_updated import main, missing_memory_note  # noqa: E402


def test_code_without_a_memory_note_is_called_out():
    assert missing_memory_note(["src/messiah/ops/series_coverage.py", "tests/ops/x.py"])
    assert missing_memory_note(["scripts/run_l1_daily.py"])
    assert missing_memory_note(["configs/pending_verifications.yaml"])


def test_code_with_a_memory_note_is_quiet():
    assert not missing_memory_note(
        ["src/messiah/ops/series_coverage.py", "dev_memory/DECISION_LOG.md"]
    )


def test_a_commit_without_code_is_quiet():
    """테스트·문서만 고친 커밋까지 매번 울면 아무도 안 읽는다."""
    assert not missing_memory_note(["tests/ops/test_series_coverage.py", "README.md"])
    assert not missing_memory_note([])


def test_windows_path_separators_are_normalised():
    """`git diff --cached`가 어떤 구분자를 주든 판정이 흔들리면 안 된다."""
    assert missing_memory_note(["src/messiah/ops/x.py"])
    assert not missing_memory_note(["dev_memory/NEXT_TODO.md", "src/messiah/ops/x.py"])


def test_the_hook_never_blocks_the_commit(monkeypatch, capsys):
    """**막으면 `--no-verify`가 습관이 되고, 그러면 ruff·비밀키 검사까지 함께 꺼진다.**"""
    monkeypatch.setattr("check_dev_memory_updated.staged_paths", lambda: ["src/messiah/ops/x.py"])

    assert main() == 0
    assert "dev_memory" in capsys.readouterr().out


def test_a_git_failure_does_not_break_the_commit(monkeypatch, capsys):
    """훅이 자기 실패로 커밋을 막으면 안 된다 — 관측 도구의 공통 규율."""
    monkeypatch.setattr("check_dev_memory_updated.staged_paths", lambda: [])

    assert main() == 0
    assert capsys.readouterr().out == ""
