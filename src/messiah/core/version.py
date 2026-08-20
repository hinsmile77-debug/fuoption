"""프로세스가 적재한 코드 버전 — "지금 이 화면은 어느 코드로 도는가" (2026-08-05 3차, P0-1).

## 왜 만들었나

2026-08-05 장중점검에서 화면의 신호등은 전부 초록이었고, **실제로도 초록이 맞았다**. 그런데
그날 오전에 커밋한 감시 장치가 하나도 안 돌고 있었다:

    08:35:01  run_l1_daily.py 기동        (HEAD = bb60f19)
    08:35:14  Command Center UI 기동      (HEAD = bb60f19)
    11:03:35  커밋 8354e98 — `l1.composer` 신호등 신설
    11:57:32  커밋 8810867 — 웜업 구간 UNKNOWN 판정

"상위 Horizon 봉의 3~17%가 사라지는 동안 세 축이 전부 OK였다"를 고치려고 만든 합성기
신호등이, 정작 그 유실이 일어나는 날 장중에 **적재되지도 않은 채** 화면에는 신호등 칸이
아예 없었다. 그리고 그 사실을 알 방법이 화면에 없었다.

이건 개별 버그가 아니라 축이 하나 없는 것이다 — `core/health.py`가 "침묵도 상태다"로 메운
자리("프로세스가 죽으면 아무 신호도 안 나온다")와 같은 형태의 구멍이다. 여기서는 **"고친
것"과 "도는 것"이 다를 수 있다**는 축을 세운다.

## 두 개의 SHA를 구분하는 것이 전부다

- `PROCESS_GIT_SHA` — **이 프로세스가 임포트 시점에 본 HEAD** = 적재한 코드의 버전.
  모듈 임포트는 프로세스당 한 번뿐이라(`sys.modules` 캐시), Streamlit이 스크립트를 5초마다
  다시 실행해도 이 값은 기동 시점에 고정된다. 그게 정확히 알고 싶은 값이다 — 재실행 때마다
  `git rev-parse`를 다시 부르면 항상 최신 HEAD가 나와서 어긋남을 영영 못 본다.
- `head_git_sha()` — **지금 작업트리의 HEAD**. 이건 계속 변하므로 매번 조회하되, 렌더 루프가
  5초마다 subprocess를 띄우는 낭비를 막으려 TTL 캐시를 둔다.

둘이 다르면 "커밋한 코드가 아직 안 돌고 있다"가 **확정**된다. 추정이 아니다.

## 다른 프로세스의 버전은 heartbeat가 실어 나른다

`Health.git_sha`(`core/messages.py`)에 각 컴포넌트가 자기 `PROCESS_GIT_SHA`를 싣는다 —
화면은 자기 버전만이 아니라 수집기·G2의 버전까지 한자리에서 대조할 수 있다. 이 필드를
**아예 안 싣는 heartbeat는 그 자체가 구버전이라는 증거다**(빈 문자열 → "버전 미보고").
"""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from messiah.core.timeutil import now_utc

# git 조회 자체가 실패했을 때의 값 — `core/logging.py`가 예전부터 쓰던 문자열을 그대로 쓴다
# (로그와 화면이 같은 말을 하게).
UNKNOWN_SHA = "nogit"

# 작업트리 HEAD 캐시 수명. LIVE 렌더는 5초 격자라 그대로 두면 분당 12번 subprocess를 띄운다 —
# 커밋은 그보다 훨씬 드물게 일어나므로 60초면 충분하고, 어긋남 발견이 최대 1분 늦어질 뿐이다.
HEAD_CACHE_TTL_SECONDS = 60.0


def _read_git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:  # noqa: BLE001 — git이 없거나 리포 밖이어도 본 기능을 막지 않는다
        return UNKNOWN_SHA


# **임포트 시점에 한 번만** 잰다 — 이 값이 곧 "이 프로세스가 적재한 코드"다(모듈 docstring).
PROCESS_GIT_SHA: str = _read_git_sha()
PROCESS_STARTED_AT: datetime = now_utc()

_head_lock = threading.Lock()
_head_cache: tuple[float, str] | None = None  # (조회 시각 epoch, sha)


def head_git_sha(
    *, now: datetime | None = None, ttl_seconds: float = HEAD_CACHE_TTL_SECONDS
) -> str:
    """지금 작업트리의 HEAD — TTL 캐시. 렌더 루프에서 불려도 안전하다."""
    global _head_cache
    stamp = (now or now_utc()).timestamp()
    with _head_lock:
        cached = _head_cache
        if cached is not None and stamp - cached[0] < ttl_seconds:
            return cached[1]
    sha = _read_git_sha()
    with _head_lock:
        _head_cache = (stamp, sha)
    return sha


def reset_head_cache() -> None:
    """테스트 전용 — TTL 캐시를 비운다."""
    global _head_cache
    with _head_lock:
        _head_cache = None


# ## 커밋이 아니라 **워킹트리**가 실행 코드다 (2026-08-20 F-2 · G-C)
#
# 위 두 SHA는 "커밋한 코드가 도는가"에 답한다. 그런데 이 저장소는 워킹트리를 **직접
# 임포트**한다 — 배치가 부르는 것은 `python scripts/run_l1_daily.py`이지 설치된
# 패키지가 아니다. 즉 실제로 실린 것은 커밋이 아니라 **기동 시점의 파일 내용**이다.
# 두 SHA가 같아도 워킹트리에 미커밋 변경이
# 있으면 프로세스는 그 미커밋 코드로 돈다.
#
# 2026-08-19 저녁 구현 세션이 dev_memory에 "완료"라 적고 커밋을 빠뜨렸다. 다음 날 아침
# `code_version.stale`은 false였다(커밋한 것과 HEAD가 같으니 옳다) — 그리고 그날 개장이
# 통째로 갔다. 직전 커밋 `50eff6c`가 같은 유형이라 **이틀 연속**이었고, 그 사이 어느
# 계기도 이것을 말하지 않았다. 워킹트리는 애초에 관측 대상이 아니었기 때문이다.
#
# `git status --porcelain -- src scripts` 로 경로를 **좁힌다**. 이 저장소의 `data/`는
# 수 GB라 전체 스캔이 기동 경로에 들어가면 안 된다.
_DIRTY_CACHE_TTL_SECONDS = 60.0
_dirty_lock = threading.Lock()
_dirty_cache: tuple[float, int | None] | None = None  # (조회 시각 epoch, 파일 수)

# 「실행 코드의 정본」으로 보는 경로 — 커밋 여부와 무관하게 임포트되는 것들.
SOURCE_PATHS: tuple[str, ...] = ("src", "scripts")


def _read_dirty_count() -> int | None:
    """`src/`·`scripts/`의 미커밋 실변경 파일 수. 못 재면 `None`(0이 아니다 — L18)."""
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain", "--", *SOURCE_PATHS],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except Exception:  # noqa: BLE001 — git이 없거나 리포 밖이어도 기동을 막지 않는다
        return None
    return len([line for line in out.splitlines() if line.strip()])


def worktree_dirty_files(
    *, now: datetime | None = None, ttl_seconds: float = _DIRTY_CACHE_TTL_SECONDS
) -> int | None:
    """미커밋 실변경 파일 수 — TTL 캐시. `None`은 **미측정**이지 0이 아니다."""
    global _dirty_cache
    stamp = (now or now_utc()).timestamp()
    with _dirty_lock:
        cached = _dirty_cache
        if cached is not None and stamp - cached[0] < ttl_seconds:
            return cached[1]
    count = _read_dirty_count()
    with _dirty_lock:
        _dirty_cache = (stamp, count)
    return count


def reset_dirty_cache() -> None:
    """테스트 전용."""
    global _dirty_cache
    with _dirty_lock:
        _dirty_cache = None


def source_mtime_max(root: Path | str = ".") -> datetime | None:
    """`src/`·`scripts/`의 **가장 최근 수정 시각** (2026-08-20 G-C).

    ## 왜 SHA로는 부족한가

    2026-08-20 아침 실측: 08:20에 뜬 l1은 F-2b를 실었고, 08:25에 뜬 g2는 F-5를 못 실었다.
    같은 커밋 상태에서 **5분 사이에 파일이 바뀌었기 때문**이다. `code_version.stale`은
    커밋 기준이라 이 절반 상태를 말할 수 없었고, 그날 점검은 그것을 「커밋 누락」이라
    잘못 부르고 나서야 「편집 시각 vs 기동 시각」임을 알았다(commit `e4894d5`).

    `SessionStart`가 이 값을 실으면 장후 리포트가 **`기동 시각 < 소스 최신 mtime`** 을
    판정할 수 있다 — "기동한 뒤에 소스가 바뀌었다". 판정이 아니라 **기록으로 시작**한다
    (dev에서 편집이 잦아 오탐이 잦을 것이므로 — R18).
    """
    base = Path(root)
    latest: float | None = None
    for name in SOURCE_PATHS:
        directory = base / name
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.py"):
            try:
                stamp = path.stat().st_mtime
            except OSError:  # noqa: PERF203 — 파일 하나가 사라져도 나머지는 잰다
                continue
            if latest is None or stamp > latest:
                latest = stamp
    if latest is None:
        return None
    return datetime.fromtimestamp(latest, tz=timezone.utc)


def uptime_text(started_at: datetime, *, now: datetime | None = None) -> str:
    """ "기동 후 얼마나 지났나" — 어긋남을 봤을 때 "언제부터 이랬나"가 바로 붙어야 한다."""
    seconds = int(max(((now or now_utc()) - started_at).total_seconds(), 0))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours}시간 {minutes}분" if hours else f"{minutes}분"


@dataclass(frozen=True)
class VersionDrift:
    """코드 버전 대조 결과 — `stale`이 참이면 커밋한 코드가 안 돌고 있다는 뜻."""

    stale: bool
    summary: str
    details: tuple[str, ...] = ()


def _display_sha(sha: str) -> str:
    # 빈 문자열은 "이 필드를 만들기 전 코드"라는 뜻이다 — 현재 코드로 도는 프로세스는 반드시
    # 자기 SHA를 싣기 때문에(`core/health.py`), 미보고 자체가 구버전의 증거다.
    if not sha:
        return "버전 미보고(구버전)"
    return sha


def assess_version_drift(
    *,
    process_sha: str = PROCESS_GIT_SHA,
    head_sha: str,
    component_shas: Mapping[str, str] | None = None,
) -> VersionDrift:
    """화면 자신과 heartbeat를 보내온 컴포넌트들의 버전을 작업트리 HEAD와 대조한다.

    `head_sha`를 못 구한 경우(git 없음/리포 밖)를 **정상으로 보고하지 않는다** — 판정할
    근거가 없는 것과 일치하는 것은 다르다(`core/health.py`의 `UNKNOWN` 도입과 같은 논리).
    다만 어긋남으로 단정하지도 않으므로 `stale`은 거짓이고, 문구가 근거 없음을 밝힌다.
    """
    if not head_sha or head_sha == UNKNOWN_SHA:
        return VersionDrift(False, "코드 버전 확인 불가 — git 조회 실패")

    mismatched: list[str] = []
    if process_sha != head_sha:
        mismatched.append(f"화면 {_display_sha(process_sha)}")
    for component, sha in sorted((component_shas or {}).items()):
        if sha != head_sha:
            mismatched.append(f"{component} {_display_sha(sha)}")

    if not mismatched:
        return VersionDrift(False, f"코드 {head_sha} — 전 프로세스 동일")
    return VersionDrift(
        True,
        f"코드 불일치 — HEAD {head_sha} / " + " · ".join(mismatched),
        tuple(mismatched),
    )
