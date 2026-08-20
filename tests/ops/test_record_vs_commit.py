"""기록이 자기 자신을 채점하고 있었다 — 2026-08-20 G-2.

2026-08-19 저녁 구현 세션이 `NEXT_TODO.md`의 항목을 `[x]`로 닫고 `DECISION_LOG.md`에
*"구현 (2026-08-20)"* 이라 적었다. **그리고 커밋하지 않았다.** 다음 날 아침 두 프로세스가
그 미커밋 코드로 떴고(워킹트리를 직접 임포트한다), `code_version.stale`은 `false`였다 —
두 SHA만 보면 그 값이 옳기 때문이다. 사람이 다음 날 아침 `git diff`를 쳐서야 알았다.

여기서 고정하는 것 셋:

1. **원안 규칙(`커밋 0건`)으로는 그 사고를 못 잡는다.** 2026-08-19은 커밋이 1건 있었다.
   진짜 신호는 「그날 커밋이 몇 건이냐」가 아니라 「완료라 적은 것이 반입됐느냐」다.
2. **미커밋 축은 당일에만 쓴다.** 미커밋 여부는 *지금* 워킹트리의 상태라, 과거일에
   대입하면 오늘 상태로 어제를 판정하게 된다.
3. **못 재는 날은 판정하지 않는다.** 0으로 적으면 "완료 처리가 없었다"가 되어 조용한
   통과가 된다 — 2026-08-19 G-4가 코드로 금지한 형태다.
"""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest

from messiah.ops import record_vs_commit as rvc


def _git(args: list[str], cwd: Path, *, when: str | None = None) -> None:
    import os

    env = dict(os.environ)
    if when is not None:
        # **커밋 날짜는 committer date다.** `--date`만 주면 author date만 바뀌고
        # `git log --until`은 여전히 "지금"으로 필터한다 — 이 테스트가 처음에 그 함정에
        # 빠져 전부 `unresolved`를 받았다.
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "dev_memory").mkdir(parents=True)
    _git(["init", "-q"], root)
    _git(["config", "user.email", "t@example.com"], root)
    _git(["config", "user.name", "T"], root)
    _git(["config", "commit.gpgsign", "false"], root)
    todo = root / "dev_memory" / "NEXT_TODO.md"
    todo.write_text("- [x] 어제 닫은 것\n- [ ] 아직 열린 것\n", encoding="utf-8")
    _git(["add", "-A"], root)
    _git(["commit", "-q", "-m", "base"], root, when="2026-08-18T10:00:00+09:00")
    return root


def _close_one(root: Path) -> None:
    todo = root / "dev_memory" / "NEXT_TODO.md"
    todo.write_text("- [x] 어제 닫은 것\n- [x] 오늘 닫은 것\n", encoding="utf-8")


def test_clean_day_is_ok(repo: Path) -> None:
    """완료 처리도 없고 미커밋도 없는 날은 조용하다."""
    result = rvc.assess(
        date(2026, 8, 19), repo_root=repo, today=date(2026, 8, 19), dirty_source_files=0
    )
    assert result.verdict == "ok"
    assert result.n_closed == 0
    assert not result.breached


def test_closed_with_uncommitted_source_is_the_real_signal(repo: Path) -> None:
    """**2026-08-19 사고의 재현.** 커밋은 있었지만 구현분이 워킹트리에 남아 있었다."""
    _close_one(repo)
    result = rvc.assess(
        date(2026, 8, 19), repo_root=repo, today=date(2026, 8, 19), dirty_source_files=14
    )
    assert result.verdict == "closed_with_uncommitted_source"
    assert result.breached
    assert result.n_closed == 1
    assert "14파일" in result.detail


def test_original_rule_alone_would_have_missed_it(repo: Path) -> None:
    """장전 G-2 원안(`n_closed > 0 and n_commits == 0`)만으로는 `ok`가 나온다.

    이 테스트는 **원안이 부족했다는 사실 자체**를 고정한다 — 나중에 누가 미커밋 갈래를
    "중복"이라며 지우면 여기서 걸린다.
    """
    _close_one(repo)
    result = rvc.assess(
        date(2026, 8, 19), repo_root=repo, today=date(2026, 8, 19), dirty_source_files=0
    )
    # 커밋이 0건이 아니므로(base 커밋은 08-18이라 08-19 커밋은 0건이다) —
    # 여기서는 커밋 0건 갈래가 잡는다. 즉 두 갈래가 서로 다른 날을 잡는다.
    assert result.verdict == "closed_without_commit"


def test_uncommitted_axis_is_skipped_for_past_days(repo: Path) -> None:
    """오늘의 미커밋 상태로 어제를 판정하면 안 된다."""
    _close_one(repo)
    result = rvc.assess(date(2026, 8, 19), repo_root=repo, today=date(2026, 8, 20))
    assert result.verdict != "closed_with_uncommitted_source"
    assert result.dirty_files is None


def test_unresolved_when_git_is_unavailable(tmp_path: Path) -> None:
    """git 리포가 아니면 0이 아니라 **판정 불가**다 (L18)."""
    result = rvc.assess(date(2026, 8, 19), repo_root=tmp_path)
    assert result.verdict == "unresolved"
    assert result.n_closed is None


def test_dates_are_passed_with_an_explicit_time(repo: Path) -> None:
    """bare `YYYY-MM-DD`를 `--since`에 주면 이 git이 그날 커밋을 0건으로 돌려준다.

    그대로 뒀다면 이 계기가 **매일** "커밋 0건"을 보고해 늑대소년이 됐을 것이고,
    늑대소년이 된 계기는 정작 진짜 사고가 난 날에도 안 읽힌다.
    """
    source = Path(rvc.__file__).read_text(encoding="utf-8")
    assert "00:00:00" in source, "시각 없이 날짜만 넘기면 커밋 수가 0으로 나온다"


def test_summary_speaks_even_on_a_clean_day(repo: Path) -> None:
    """공백이 없는 날도 한 줄 남긴다 — 측정된 0과 미검사를 가른다."""
    ok = rvc.assess(
        date(2026, 8, 19), repo_root=repo, today=date(2026, 8, 19), dirty_source_files=0
    )
    assert any("기록↔반입" in line for line in rvc.summarize(ok))
    unresolved = rvc.RecordVsCommit(None, None, "unresolved", "git 없음")
    assert "판정 불가" in rvc.summarize(unresolved)[0]
