"""점검 세션의 `git` 직접 실행 구조적 차단 (2026-09-17 F-112 / F-78).

F-78은 이 규칙을 **산문으로만** 세웠고, 그 뒤 09-16·09-17 두 날 모두 장중 세션이 그것을
어겼다. 이 파일은 그 규칙이 이제 **코드로** 성립하는지를 지킨다 — 특히 09-17에 실제로
불린 그 명령줄이 막히는지를 실측 형태 그대로 고정한다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_GUARD = Path(__file__).resolve().parent.parent / "scripts" / "hooks" / "session_git_guard.py"

sys.path.insert(0, str(_GUARD.parent))

from session_git_guard import _decide  # noqa: E402,I001


# ---------------------------------------------------------------- 실측 재현


def test_the_exact_command_that_violated_the_rule_on_2026_09_17_is_blocked():
    """리포트 1-5에 기록된 그 명령줄이다 — 전역 플래그(`-c core.pager=cat`)가 하위명령
    앞에 붙어 있어 "맨 앞 토큰이 git이면 막는다" 식 구현으로는 하위명령을 못 읽는다."""
    reason = _decide("git -c core.pager=cat diff --stat --ignore-all-space -- src scripts")

    assert reason is not None
    assert "git diff" in reason
    assert "collect_evidence.py" in reason, "거절은 **대안**을 같이 줘야 우회로를 안 찾는다"


def test_a_git_call_hidden_behind_a_shell_connector_is_still_seen():
    """`cd x && git status`·`... | git log`처럼 조각 뒤에 숨은 호출도 본다."""
    assert _decide("cd /c/foo && git status --short") is not None
    assert _decide("echo x; git log --oneline -3") is not None
    assert _decide("cat f | git hash-object --stdin") is not None


# ---------------------------------------------------------------- 남겨야 하는 것


@pytest.mark.parametrize(
    "command",
    [
        'git commit -m "[MW0601] 장후 자동조치"',
        "git add src/messiah/execution/position_reconciler.py",
        "git push origin master",
        "git mv a b",
    ],
)
def test_the_write_path_the_postmarket_automation_uses_stays_open(command: str):
    """장후 자동조치는 실제로 커밋한다(2026-09-17 제6부의 `b7288d2`·`082b33b`). 여기서
    막으면 이 훅이 자동조치 자체를 못 하게 만든다 — 막으려는 것은 **근거를 만들려고 부르는
    조회형**이지 사람이 지시한 쓰기가 아니다."""
    assert _decide(command) is None


def test_the_collector_is_not_what_this_blocks():
    """수집기의 `run_git()`은 F-60에서 화이트리스트·`--no-optional-locks`로 이미 안전하게
    만들어 08-27에 합격 판정을 받은 경로다. 그것을 부르는 명령줄은 막히면 안 된다 —
    막으면 세션이 **인용할 근거 자체**를 못 만든다."""
    assert _decide("python scripts/collect_evidence.py --phase post") is None
    assert _decide(".venv/Scripts/python.exe scripts/collect_evidence.py --phase pre") is None


def test_reading_a_git_file_directly_is_not_running_git():
    """수집기 자신이 *"필요하면 `.git` 파일을 직접 읽어라"*로 권하는 경로다 — 이 훅은
    `git` **실행**을 막지 파일 읽기를 막지 않는다. 둘을 섞으면 대안이 사라진다."""
    assert _decide("tail -3 .git/logs/HEAD") is None
    assert _decide("cat .git/HEAD") is None


def test_ordinary_evidence_commands_are_untouched():
    """SKILL.md §2가 권하는 그 형태들 — 훅이 이것들에 걸리면 점검 자체가 멈춘다."""
    assert _decide('grep -c \'"level": "ERROR"\' logs/l1_daily_20260917.log') is None
    assert _decide("sed -n '/08:4[0-9]:/p' logs/x.log | head -40") is None
    assert _decide("ls -t logs/*.log") is None


# ---------------------------------------------------------------- 기본 거절


def test_an_unlisted_subcommand_is_refused_rather_than_waved_through():
    """F-60이 *"플래그를 믿지 않는다"*로 세운 규율 — 새 하위명령이 조용히 새어 들어오는
    길을 열어 두지 않는다."""
    reason = _decide("git bisect start")

    assert reason is not None
    assert "허용 목록 밖" in reason


def test_bare_git_is_refused_too():
    assert _decide("git") is not None


# ---------------------------------------------------------------- 훅 규약


def _run_hook(payload: dict) -> str:
    result = subprocess.run(
        [sys.executable, str(_GUARD)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_the_hook_emits_the_deny_shape_claude_code_expects():
    out = _run_hook({"tool_name": "Bash", "tool_input": {"command": "git status"}})

    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "deny"
    assert "F-112" in decision["permissionDecisionReason"]


def test_a_korean_reason_survives_a_non_utf8_console():
    """첫 실측에서 cp949 `UnicodeEncodeError`로 거절 JSON이 **중간에 잘렸다** — 잘린 JSON은
    파싱 실패라 사실상 통과와 같다. 훅이 스스로 표준출력을 UTF-8로 세운다."""
    out = _run_hook({"tool_name": "Bash", "tool_input": {"command": "git diff"}})

    json.loads(out)  # 잘렸으면 여기서 깨진다
    assert "점검 세션" in out


def test_a_non_shell_tool_and_a_clean_command_produce_no_decision():
    """빈 출력 = 판단 없음 = 평소대로. 훅이 무엇이든 말하면 그만큼 사람이 읽을 것이 는다."""
    assert _run_hook({"tool_name": "Write", "tool_input": {"file_path": "x"}}) == ""
    assert _run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}) == ""


def test_the_powershell_tool_is_not_a_way_around_this():
    """**셸 도구가 둘이라는 것이 함정이다.** 이 저장소의 커밋 규약은 오히려 "쓰기는 네이티브
    PowerShell"이라(2026-09-17 장후 자동조치 기록) 그쪽이 더 자연스러운 우회로다. `Bash`만
    막으면 F-78이 지적한 그 형태 — 두 경로가 있는데 하나만 막고 그 사실이 「차단했다」는 한
    줄에 가리는 것 — 가 이 훅 자신에게서 재발한다."""
    out = _run_hook({"tool_name": "PowerShell", "tool_input": {"command": "git status"}})

    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    # 쓰기 경로는 PowerShell에서도 열려 있어야 한다 — 이 저장소가 실제로 커밋하는 길이다.
    assert _run_hook({"tool_name": "PowerShell", "tool_input": {"command": "git add ."}}) == ""


def test_a_malformed_payload_never_breaks_the_session():
    """훅이 세션을 죽이면 본말전도다(L22와 같은 규율) — 못 읽으면 조용히 통과시킨다."""
    result = subprocess.run(
        [sys.executable, str(_GUARD)], input="not json", capture_output=True, text=True
    )

    assert result.returncode == 0
    assert result.stdout == ""


# ---------------------------------------------------------------- 설정 결선


def test_the_hook_is_actually_wired_in_project_settings():
    """스크립트가 있는 것과 **돌게 돼 있는 것**은 다르다 — F-78이 산문으로만 존재하다
    두 번 어겨진 것과 같은 형태의 실패를 여기서 막는다."""
    settings = json.loads(
        (_GUARD.parent.parent.parent / ".claude" / "settings.json").read_text(encoding="utf-8")
    )

    entries = [
        entry
        for entry in settings["hooks"]["PreToolUse"]
        if any(
            "session_git_guard.py" in hook.get("command", "")
            for hook in entry["hooks"]
            if hook.get("type") == "command"
        )
    ]
    assert entries, "훅이 설정에 없다 — 스크립트가 있는 것과 **돌게 돼 있는 것**은 다르다"
    matched = {part for entry in entries for part in entry.get("matcher", "").split("|")}
    assert {"Bash", "PowerShell"} <= matched, "셸 도구 하나만 걸면 다른 하나가 우회로다"
