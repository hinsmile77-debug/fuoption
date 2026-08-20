"""기록이 자기 자신을 채점하고 있었다 — 2026-08-20 G-2.

## 무슨 일이 있었나

2026-08-19 저녁 구현 세션이 F-1~F-6·G-3/G-4를 끝내고 `NEXT_TODO.md`의 해당 항목을 `[x]`로
닫았다. `DECISION_LOG.md`에는 *"구현 (2026-08-20)"* 이라 적었다. **그리고 커밋하지 않았다.**

다음 날 아침 08:20/08:25에 두 프로세스가 떴고, 그 프로세스들은 그 구현분을 실었다(워킹트리를
직접 임포트하므로) — 그런데 `git`은 그것을 모르고, `code_version.stale`은 `false`였다.
그 어긋남을 **아무 계기도 재고 있지 않았다.** 사람이 다음 날 아침 점검에서 `git diff`를 쳐서야
알았고, 그 사이 개장 한 번이 통째로 지나갔다.

직전 커밋 `50eff6c`의 제목이 같은 사고다 — *"장중에 재기동해야 하는데 실릴 코드가 커밋에
없었다"*. 즉 **이틀 연속**이고, 개인 규율의 문제가 아니라 절차에 계기가 없는 것이 원인이다.

## 이 모듈이 세는 것

    n_closed   그날 `NEXT_TODO.md`에서 새로 `[x]`가 된 항목 수
    n_commits  그날의 커밋 수

`n_closed > 0 and n_commits == 0` 이면 **`ClosedWithoutCommit`**. "완료라 적었는데 반입되지
않은 날"이 그날 저녁에 이름을 얻는다.

## 왜 워킹트리와 비교하나

원안(장전 G-2)은 `git diff HEAD~1 -- dev_memory/NEXT_TODO.md`를 제안했다. 그러면 **정작
잡아야 할 날을 놓친다** — dev_memory 자체가 커밋 안 된 날엔 diff에 아무것도 안 나오고,
2026-08-19가 정확히 그런 날이었다.

그래서 **그날 시작 시점의 커밋본**과 **지금 워킹트리**를 비교한다. 커밋 여부와 무관하게
"오늘 무엇을 완료로 적었나"가 잡힌다.

## 못 재는 날은 판정하지 않는다

git이 없거나 조회가 실패하면 `verdict="unresolved"`다. 0으로 적으면 "완료 처리가 없었다"가
되어 조용한 통과가 된다 — 2026-08-19 G-4가 코드로 금지한 바로 그 형태(계기가 눈이 멀었는데
0이 정상으로 보이는 것)다.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from messiah.core.version import worktree_dirty_files

NEXT_TODO = "dev_memory/NEXT_TODO.md"

# "인자를 안 줬다"와 "None을 줬다(= 미측정으로 판정하라)"를 가른다 — 이 저장소가 L18로
# 반복해서 배운 구분이고, 테스트가 미측정 경로를 명시적으로 밟을 수 있어야 한다.
_SENTINEL: int | None = -1


def _today() -> date:
    from messiah.core.timeutil import now_kst

    return now_kst().date()


# `- [x] ...` / `  - [X] ...` — 들여쓰기와 대소문자를 모두 받는다.
_CLOSED = re.compile(r"^\s*[-*]\s*\[[xX]\]")


@dataclass
class RecordVsCommit:
    """그날의 「완료 처리」와 「반입」 대조 결과."""

    n_closed: int | None
    n_commits: int | None
    # "ok" | "closed_without_commit" | "closed_with_uncommitted_source" | "unresolved"
    verdict: str
    detail: str = ""
    closed_items: list[str] = field(default_factory=list)
    # 하루 끝의 `src/`·`scripts/` 미커밋 파일 수 (2026-08-20 F-2). `None`은 미측정이다.
    dirty_files: int | None = None

    @property
    def breached(self) -> bool:
        return self.verdict in ("closed_without_commit", "closed_with_uncommitted_source")

    def to_dict(self) -> dict[str, object]:
        return {
            "n_closed": self.n_closed,
            "n_commits": self.n_commits,
            "verdict": self.verdict,
            "detail": self.detail,
            "dirty_files": self.dirty_files,
            # 항목 문구를 담되 상한을 둔다 — `NEXT_TODO.md`가 480KB라 전량이면 리포트가
            # 그 파일의 사본이 된다. 사람이 "무엇을 닫았나"를 떠올리기엔 몇 줄이면 충분하다.
            "closed_items": self.closed_items[:5],
        }


def _run(args: list[str], *, cwd: Path) -> str | None:
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except Exception:  # noqa: BLE001 — 관측이 장후 절차를 막으면 본말전도다
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _closed_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if _CLOSED.match(line)]


def assess(
    day: date,
    *,
    repo_root: Path | str = ".",
    today: date | None = None,
    dirty_source_files: int | None = _SENTINEL,
) -> RecordVsCommit:
    """그날 `[x]`로 닫힌 항목 수와 커밋 수를 대조한다.

    `dirty_source_files`는 **당일 판정에만** 쓴다 — 미커밋 여부는 *지금* 워킹트리의 상태라
    과거일에 대입하면 오늘의 상태로 어제를 판정하게 된다. 과거일은 커밋 수 갈래만 본다.
    """
    root = Path(repo_root)
    # **시각을 붙여야 한다.** bare `2026-08-20`을 `--since`/`--until`에 주면 이 git이
    # 그날 커밋을 0건으로 돌려준다(실측: bare 0건 vs `"2026-08-20 00:00:00"` 17건).
    # 그대로 뒀다면 이 계기가 **매일** "커밋 0건"을 보고해 늑대소년이 됐을 것이다 —
    # 그리고 늑대소년이 된 계기는 정작 진짜 사고가 난 날에도 안 읽힌다.
    start = f"{day.isoformat()} 00:00:00"
    end = f"{(day + timedelta(days=1)).isoformat()} 00:00:00"

    commits = _run(
        ["git", "log", "--since", start, "--until", end, "--format=%h", "--no-merges"],
        cwd=root,
    )
    if commits is None:
        return RecordVsCommit(None, None, "unresolved", "git 이력 조회 실패")
    n_commits = len([line for line in commits.splitlines() if line.strip()])

    # 그날 **시작 시점**의 `NEXT_TODO.md` — 그날 첫 커밋보다 앞선 마지막 커밋본이다.
    base = _run(
        ["git", "log", "--until", start, "-1", "--format=%H", "--", NEXT_TODO],
        cwd=root,
    )
    if base is None or not base.strip():
        return RecordVsCommit(
            None, n_commits, "unresolved", f"{NEXT_TODO}의 전일 커밋본을 못 찾았다"
        )
    before = _run(["git", "show", f"{base.strip()}:{NEXT_TODO}"], cwd=root)
    if before is None:
        return RecordVsCommit(None, n_commits, "unresolved", f"{NEXT_TODO} 전일본 조회 실패")

    try:
        # **워킹트리**를 읽는다 — 커밋 여부와 무관하게 "오늘 무엇을 완료로 적었나"를 본다.
        # 커밋본만 보면 정작 잡아야 할 날(dev_memory도 미커밋인 날)을 놓친다.
        now_text = (root / NEXT_TODO).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return RecordVsCommit(None, n_commits, "unresolved", f"{NEXT_TODO} 읽기 실패: {exc}")

    was = set(_closed_lines(before))
    now = _closed_lines(now_text)
    newly = [line for line in now if line not in was]
    n_closed = len(newly)

    # **원안 규칙만으로는 정작 그 사고를 못 잡는다** (실측으로 확인).
    #
    # 장전 G-2는 `n_closed > 0 and n_commits == 0`을 조건으로 제안했다. 그런데 2026-08-19은
    # 커밋이 1건 있었다(그날 장후 점검 기록). 즉 그 규칙으로는 **`ok`가 나온다** — 정작
    # 이 축을 만들게 한 사고를 놓치는 것이다.
    #
    # 진짜 신호는 「그날 커밋이 몇 건이냐」가 아니라 **「완료라 적은 것이 실제로 반입됐느냐」**다.
    # 후자는 하루 끝의 미커밋 소스로 잰다(2026-08-20 F-2, `core/version.worktree_dirty_files`).
    # 그래서 두 갈래를 모두 본다.
    # 미커밋 축은 **오늘**만 잰다(위 docstring). 과거일은 이 갈래를 건너뛴다.
    is_today = (today or _today()) == day
    if dirty_source_files is _SENTINEL:
        dirty = worktree_dirty_files() if is_today else None
    else:
        dirty = dirty_source_files
    if n_closed > 0 and dirty:
        return RecordVsCommit(
            n_closed,
            n_commits,
            "closed_with_uncommitted_source",
            f"{n_closed}건을 완료로 적었는데 src/scripts에 미커밋 {dirty}파일이 남아 있다 — "
            "다음 기동은 커밋에 없는 코드로 돈다",
            newly,
            dirty,
        )
    if n_closed > 0 and n_commits == 0:
        return RecordVsCommit(
            n_closed,
            n_commits,
            "closed_without_commit",
            f"{n_closed}건을 완료로 적었는데 그날 커밋이 0건이다 — "
            "구현분이 반입되지 않은 채 다음 기동을 맞는다",
            newly,
            dirty,
        )
    return RecordVsCommit(
        n_closed,
        n_commits,
        "ok",
        f"완료 처리 {n_closed}건 · 커밋 {n_commits}건"
        + ("" if dirty is None else f" · 미커밋 {dirty}파일"),
        newly,
        dirty,
    )


def summarize(result: RecordVsCommit) -> list[str]:
    """사람이 읽는 한 줄 — **어긋남이 없는 날도 남긴다**(측정된 0과 미검사를 가른다)."""
    if result.verdict == "unresolved":
        return [f"  기록↔반입 대조: 판정 불가 — {result.detail}"]
    mark = "⚠" if result.breached else "✅"
    return [f"  {mark} 기록↔반입 대조: {result.detail}"]
