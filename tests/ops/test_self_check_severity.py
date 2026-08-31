"""F-72 — 「합격」 도장이 그 안의 경고를 덮지 않게 한다 (2026-08-31 이상점 1-3).

2026-08-31 아침의 실제 출력은 이랬다::

    [OK ] git        [WARN] dirty 12건 중 **src/scripts 5파일 미커밋** — ...

    self-check: PASS — 기동 허용

사람의 눈도 기계의 집계도 줄머리에서 멈춘다. 그날 증거 다이제스트는 이 아침을
`비-OK 0행`으로 적었고, 미커밋 5파일이 **나흘간** 그 한 글자 뒤에 숨었다.
"""

from __future__ import annotations

import sys
from pathlib import Path

# `scripts/`는 패키지가 아니다 — 같은 파일을 보는 다른 테스트와 같은 방식으로 들여온다
# (`tests/ops/test_deadline_pressure.py`).
sys.path.insert(0, str(Path("scripts").resolve()))

import self_check  # noqa: E402
from self_check import CheckResult, render, severity  # noqa: E402


def test_a_warning_in_the_body_promotes_the_line_head():
    row = CheckResult("git", True, "[WARN] dirty 12건 중 **src/scripts 5파일 미커밋** — ...")
    assert severity(row) == "WARN"


def test_a_quiet_pass_stays_quiet():
    assert severity(CheckResult("git", True, "clean")) == "OK "


def test_a_failure_outranks_everything():
    assert severity(CheckResult("git", False, "미커밋 변경 5건 — 계명 10")) == "FAIL"


def test_a_mid_string_warning_counts_too():
    """`check_bundle`은 본문 **중간**에 경고를 단다 — 머리에만 있다고 보면 놓친다."""
    row = CheckResult("bundle", True, "live 1건; [WARN] 유예 번들 2건")
    assert severity(row) == "WARN"


def test_the_promoted_line_does_not_say_it_twice():
    lines = render([CheckResult("git", True, "[WARN] dirty 12건 — ...")])
    assert lines[0].startswith("[WARN] git")
    assert lines[0].count("[WARN]") == 1


def test_a_mid_string_token_is_left_where_it_is():
    """중간 토큰은 위치가 곧 뜻이다 — 떼면 어느 절이 경고인지 사라진다."""
    lines = render([CheckResult("bundle", True, "live 1건; [WARN] 유예 번들 2건")])
    assert lines[0] == "[WARN] bundle     live 1건; [WARN] 유예 번들 2건"


def test_the_pass_line_carries_its_conditions():
    """마지막 줄만 보는 사람과 기계가 있다 — 그들에게도 오늘이 무결이면 안 된다."""
    lines = render(
        [
            CheckResult("config", True, "instance=x mode=dev"),
            CheckResult("git", True, "[WARN] dirty 12건 — ..."),
            CheckResult("bundle", True, "live 1건; [WARN] 유예 번들 2건"),
        ]
    )
    assert lines[-1] == "self-check: PASS — 기동 허용 (경고 2건: git, bundle)"


def test_a_clean_morning_says_nothing_extra():
    """경고가 없는 날까지 괄호가 붙으면 괄호가 뜻을 잃는다."""
    lines = render([CheckResult("config", True, "instance=x mode=dev")])
    assert lines[-1] == "self-check: PASS — 기동 허용"


def test_promotion_does_not_change_the_verdict():
    """**판정 불변** — 표시만 바꾼다. dev의 dirty는 설계상 허용이고 기동을 막으면 안 된다.

    `main()`의 종료 코드는 여전히 `ok`만 본다: 경고 2건이 있어도 0이다.
    """
    warned = [
        CheckResult("config", True, "instance=x mode=dev"),
        CheckResult("git", True, "[WARN] dirty 12건 — ..."),
    ]
    assert all(r.ok for r in warned) is True
    assert render(warned)[-1].startswith("self-check: PASS — 기동 허용")

    failed = warned + [CheckResult("redis", False, "연결 실패")]
    assert render(failed)[-1] == "self-check: FAIL — 기동 거부 (Ver 1.1 §7.3)"


def test_the_collector_counts_a_promoted_line_as_non_ok():
    """F-72③ — 수집기의 `비-OK N행` 집계가 이 승격을 물려받는지 (오염의 하류).

    수집기는 `[OK ]`로 시작하지 않는 줄을 비-OK로 센다. 승격 전에는 문제의 줄이
    `[OK ] git …`이라 **0행**이었다.
    """
    lines = render(
        [
            CheckResult("config", True, "instance=x mode=dev"),
            CheckResult("git", True, "[WARN] dirty 12건 — ..."),
        ]
    )
    printed = [x for x in lines if x.strip()]
    bad = [x for x in printed if not x.startswith("[OK ]") and not x.startswith("self-check: PASS")]
    assert len(bad) == 1 and bad[0].startswith("[WARN] git")


def test_the_renderer_is_what_main_prints():
    """`main()`이 렌더러를 우회하면 위 단언들이 전부 무의미해진다."""
    from pathlib import Path

    source = Path(self_check.__file__).read_text(encoding="utf-8")
    body = source[source.index("def main() -> int:") :]
    assert "for line in render(results):" in body
