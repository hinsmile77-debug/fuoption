"""커밋에 `src/` 변경이 있는데 `dev_memory/` 변경이 없으면 경고한다 (2026-08-10 C-2).

## 왜 만들었나

2026-08-10 13:51 커밋 `ce91b08`은 그날 아침 사고(정시 트리거가 기동 창에 막혀 38분 유실)의
원인 수정이었다. 코드·테스트·검증 등록부를 전부 갱신했고, **`dev_memory/`는 손대지 않았다.**
`DECISION_LOG.md`와 `NEXT_TODO.md`의 마지막 수정은 08-07 17:08에 멈춰 있었다.

즉 그날 오전을 잃은 사고가 커밋 메시지에는 있는데 **dev_memory에는 없었다.** 이 저장소의
규율은 "커밋 메시지는 그 변경을, dev_memory는 그 판단을 남긴다"이고, 후자가 다음 세션이
읽는 것이다. 사람의 기억에 맡긴 규율은 바쁜 날 가장 먼저 빠진다 — 그리고 바쁜 날이 정확히
기록이 가장 필요한 날이다.

## 왜 실패가 아니라 경고인가

**막으면 우회하게 된다.** 오타 수정·포맷 정리처럼 남길 판단이 없는 커밋이 실제로 있고,
그때마다 `--no-verify`를 쓰는 습관이 들면 이 훅뿐 아니라 ruff·비밀키 검사까지 함께
꺼진다. 그 대가가 훨씬 크다.

그래서 이 훅은 **항상 0으로 끝난다.** 하는 일은 커밋하려는 사람 눈앞에 한 번 띄우는 것뿐이고,
그 한 번이 `ce91b08` 때 없었던 것이다.

## 왜 pre-commit `always_run`인가

파일 목록 인자로는 "없는 것"을 못 본다 — `dev_memory/`가 **안 바뀐** 상태가 판정 대상이라
스테이징 전체를 봐야 한다. 그래서 `pass_filenames: false`로 두고 여기서 직접 읽는다.
"""

from __future__ import annotations

import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

# 판단이 남을 만한 변경으로 보는 경로. 테스트·문서만 고친 커밋은 부르지 않는다 —
# 매번 울면 아무도 안 읽는다(이 저장소가 반복해서 배운 것).
CODE_PREFIXES = ("src/", "scripts/", "configs/")
MEMORY_PREFIX = "dev_memory/"


def staged_paths() -> list[str]:
    """스테이징된 파일 목록 — git이 없거나 실패하면 빈 목록(훅이 커밋을 막지 않는다)."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except Exception:  # noqa: BLE001 — 훅이 자기 실패로 커밋을 막으면 안 된다
        return []
    if result.returncode != 0:
        return []
    return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]


def missing_memory_note(paths: list[str]) -> bool:
    """코드 변경은 있는데 dev_memory 변경이 없는가."""
    code = any(path.startswith(CODE_PREFIXES) for path in paths)
    memory = any(path.startswith(MEMORY_PREFIX) for path in paths)
    return code and not memory


def main() -> int:
    paths = staged_paths()
    if missing_memory_note(paths):
        print(
            "\n[dev_memory] 이 커밋에 src/·scripts/·configs/ 변경이 있는데 "
            "dev_memory/ 변경이 없다.\n"
            "  남길 판단이 있으면 DECISION_LOG.md(증상→원인→결정→Why→How→검증)와\n"
            "  NEXT_TODO.md(다음에 볼 것)에 적고 다시 커밋할 것.\n"
            "  2026-08-10 ce91b08이 그날 오전을 잃은 사고를 고치고도 이 둘을 안 남겼다.\n"
            "  (경고만 한다 — 남길 판단이 없는 커밋도 있다)\n"
        )
    # **항상 0이다.** 막으면 `--no-verify`가 습관이 되고, 그러면 ruff·비밀키 검사까지 꺼진다.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
