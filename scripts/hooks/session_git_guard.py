"""점검 세션의 `git` 직접 실행을 **셸에 닿기 전에** 막는다 (2026-09-17 F-112 / F-78).

## 왜 규칙만으로는 안 됐나

F-78(2026-08-31)은 *"점검 세션은 `git` 을 직접 실행하지 않는다 — 어떤 하위명령도 예외가
아니다"*를 `.claude/skills/messiah-daily-check/SKILL.md` §1에 **산문으로** 적었다. 그 뒤
09-16·09-17 두 날 모두 장중 세션이 `git diff --stat`을 직접 불렀고, 일일점검은 그것을
「점검 세션 자신의 절차 위반」으로 다시 보고했다. 산문 규칙은 읽는 쪽이 매번 기억해야
성립하고, 안 지켜졌다는 사실은 **어긴 뒤에야** 드러난다.

사고 자체는 규칙 위반보다 훨씬 비쌌다 — Git Bash/MSYS 마운트에서 실행된 git이
`.git/index.lock`을 남기고 마운트가 unlink를 거부해 저장소가 커밋 불가로 굳었다
(08-24 3시간 21분, 08-25·08-26·08-31 재발, 08-31에는 아침 08:51에 생긴 0바이트 락이
**7시간 6분** 방치돼 그날 밤 예정된 여덟 항목이 전부 막혔다).

## 왜 「셸 래퍼」도 「수집기 시작 가드」도 아닌가

리포트가 적어 둔 두 후보는 각각 이렇게 어긋난다.

- **셸 래퍼**(PATH 앞에 가짜 `git`을 둔다): PATH는 자식 프로세스가 통째로 물려받는다 —
  `collect_evidence.py`의 `run_git()`(F-60에서 화이트리스트·`--no-optional-locks`로
  이미 안전하게 만들어 08-27 합격 판정을 받은 경로)까지 같이 막힌다. 막아야 할 것과
  남겨야 할 것을 PATH는 구분하지 못한다.
- **`collect_evidence.py` 시작 가드**: 그 스크립트는 **이미 안전한 쪽**이다. 문제가 난
  경로는 세션이 직접 부르는 `git`이고, 수집기에 가드를 걸어도 그 경로는 그대로 열려 있다.
  F-78이 *"두 경로가 있는데 하나만 막았다"*고 적은 것이 정확히 이 지점이다.

이 훅은 **호출자를 기준으로** 가른다: 세션의 셸 도구를 지나는 `git`만 본다. 수집기가
`subprocess`로 부르는 `git`은 이 훅을 지나지 않으므로 종전대로 돈다.

**셸 도구가 둘이라는 것이 함정이다.** 이 세션에는 `Bash`와 `PowerShell`이 둘 다 있고,
이 저장소의 커밋 규약은 오히려 *"쓰기는 전부 네이티브 PowerShell 도구"* 다(2026-09-17
장후 자동조치 기록). `Bash`만 막으면 F-78이 지적한 바로 그 형태 — **두 경로가 있는데
하나만 막았고 그 사실이 「차단했다」는 한 줄에 가린다** — 가 이 훅 자신에게서 재발한다.
그래서 두 도구를 같은 기준으로 본다.

## 무엇을 막고 무엇을 남기나

**조회형을 막고 기록형을 남긴다.** 거꾸로 들리지만 이 저장소에서는 그게 맞다:

- 막는 것 — `status`·`diff`·`log`·`show`·`ls-files` 등. 이것들이 F-78이 겨냥한 「리포트
  근거를 만들려고 부르는 git」이고, `git status`는 읽기처럼 보이지만 stat 캐시를 갱신하며
  **인덱스를 다시 쓴다**(= 락을 잡는다). 이 값들은 전부 `collect_evidence.py` §1
  「코드·커밋 상태」에 이미 들어 있다 — 세션은 그것을 인용하면 된다.
- 남기는 것 — `add`·`commit`·`push`·`mv` 등. 장후 자동조치가 실제로 커밋하는 경로이고
  (2026-09-17 제6부의 `b7288d2`·`082b33b`가 그것), 사람이 지시한 의도적 쓰기다. 여기서
  막으면 이 훅이 자동조치 자체를 못 하게 만든다.
- **목록에 없는 하위명령은 막는다**(기본 거절). F-60이 *"플래그를 믿지 않는다"*로 세운
  규율과 같다 — 새 하위명령이 조용히 새어 들어오는 길을 열어 두지 않는다.

## 거절은 사유와 대안을 같이 준다

거절 문자열이 「무엇을 대신 보라」를 말하지 않으면 세션은 다른 우회로를 찾는다. 그래서
수집기 §1을 명시적으로 가리킨다.

입력: Claude Code PreToolUse 훅 규약 — stdin JSON `{tool_name, tool_input:{command}}`.
     `tool_name`은 `Bash` 또는 `PowerShell`.
출력: 거절이면 `hookSpecificOutput.permissionDecision="deny"`, 아니면 아무것도 안 낸다
     (빈 출력 = 판단 없음 = 평소대로 진행).
"""

from __future__ import annotations

import json
import re
import shlex
import sys

#: 세션이 직접 부르면 안 되는 하위명령. 인덱스에 닿는지 여부가 아니라 **「근거를 만들려고
#: 부르는가」** 가 기준이다 — `git status`는 인덱스를 쓰고(락), `git log`는 안 쓰지만 둘 다
#: 수집기 §1이 이미 답하는 질문이다.
BLOCKED = frozenset(
    {
        "status",
        "diff",
        "log",
        "show",
        "ls-files",
        "ls-tree",
        "rev-parse",
        "rev-list",
        "describe",
        "blame",
        "shortlog",
        "reflog",
        "cat-file",
        "for-each-ref",
        "branch",
        "remote",
        "stash",
        "whatchanged",
        "grep",
        "diff-tree",
        "diff-index",
        "name-rev",
        "count-objects",
        "fsck",
    }
)

#: 장후 자동조치가 실제로 쓰는 기록형. **사람이 지시한 의도적 쓰기**라 남긴다.
ALLOWED = frozenset({"add", "commit", "push", "mv", "rm", "tag", "checkout", "switch", "restore"})

_HINT = (
    "저장소 상태가 필요하면 `python scripts/collect_evidence.py --phase <국면>` 의 "
    "§1 「코드·커밋 상태」를 인용하라 — HEAD·브랜치·미커밋 목록·변경 규모 표"
    "(`--ignore-all-space --stat`)·추적 안 되는 파일 목록·인덱스락 3상태가 이미 다 있다. "
    "없는 값이 필요하면 **수집기를 고쳐서** 내게 하라."
)

#: 이 가드가 보는 셸 도구. 하나만 보면 다른 하나가 그대로 우회로가 된다(모듈 docstring
#: "셸 도구가 둘이라는 것이 함정이다").
_SHELL_TOOLS = frozenset({"Bash", "PowerShell"})

#: 셸 연결자. 파이프·논리연산자·세미콜론·개행으로 끊어 각 조각의 첫 명령을 본다 —
#: 2026-09-17 위반이 `git -c core.pager=cat diff ...` 형태였으므로 `git`이 문장 맨 앞에
#: 있다는 가정만으로는 못 잡는다(`cd x && git status`도 마찬가지).
_SEPARATORS = re.compile(r"\|\||&&|[;|\n]")


def _git_subcommand(segment: str) -> str | None:
    """이 조각이 `git`을 부르면 그 하위명령을, 아니면 `None`을 돌려준다.

    `git -c a=b diff` 처럼 전역 플래그가 앞에 붙는 형태를 위해 **첫 비-플래그 토큰**을
    찾는다(`collect_evidence.run_git()`이 같은 방식으로 화이트리스트를 검사한다).
    `-c key=value` 는 값이 별도 토큰이므로 한 칸 더 건너뛴다.
    """
    try:
        tokens = shlex.split(segment)
    except ValueError:  # 따옴표가 안 닫힌 조각 — 판단하지 않는다(빈 출력 = 평소대로)
        return None
    if not tokens:
        return None
    head = tokens[0].strip('"').strip("'").replace("\\", "/").rsplit("/", 1)[-1]
    if head.lower() not in ("git", "git.exe"):
        return None
    rest = tokens[1:]
    i = 0
    while i < len(rest):
        token = rest[i]
        if token == "-c":
            i += 2  # `-c key=value` — 값 토큰까지 건너뛴다
            continue
        if token.startswith("-"):
            i += 1
            continue
        return token
    return ""  # `git` 단독 — 하위명령 없음


def _decide(command: str) -> str | None:
    """거절 사유 문자열, 또는 `None`(통과)."""
    for segment in _SEPARATORS.split(command):
        sub = _git_subcommand(segment)
        if sub is None:
            continue
        if sub in ALLOWED:
            continue
        if sub in BLOCKED:
            return (
                f"F-112/F-78 — 점검 세션은 `git {sub}` 을 직접 실행하지 않는다. "
                f"2026-08-24부터 08-31까지 네 차례, MSYS 마운트에서 실행된 git이 남긴 "
                f".git/index.lock 으로 저장소가 커밋 불가 상태로 굳었다(최장 7시간 6분). " + _HINT
            )
        return (
            f"F-112/F-78 — 허용 목록 밖의 `git {sub or '(하위명령 없음)'}` 이다. "
            f"플래그를 믿지 않고 **목록에 없으면 막는다**(F-60과 같은 규율). "
            f"기록형(add·commit·push·mv 등)만 열려 있다. " + _HINT
        )
    return None


def main() -> int:
    # **한글 사유가 콘솔 코드페이지에서 깨지면 훅이 죽는다** — 첫 실측에서 cp949
    # `UnicodeEncodeError`로 거절 JSON이 중간에 잘렸다. `run_postmarket.py`가 2026-09-17
    # F-115에서 고친 것과 같은 계열의 결함이다(파이썬은 표준스트림 인코딩을 로캘에서 받는다).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001 — 재설정 불가 환경에서도 훅은 살아야 한다
            pass
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 — 훅이 세션을 죽이면 본말전도다(L22와 같은 규율)
        return 0
    if payload.get("tool_name") not in _SHELL_TOOLS:
        return 0
    command = (payload.get("tool_input") or {}).get("command")
    if not isinstance(command, str):
        return 0
    reason = _decide(command)
    if reason is None:
        return 0
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
        ensure_ascii=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
