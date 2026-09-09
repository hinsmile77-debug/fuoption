"""「반입 대기」 절이 **자기가 재려던 수치를 오염시키지 않는가** (2026-09-09 고도화).

`ops/record_vs_commit`은 `- [x]`로 시작하는 줄을 세어 「완료라 적었는데 반입 안 됨」을
잡는다. 그 목록을 `NEXT_TODO.md` 상단에 **체크박스 형태로** 복사하면 같은 항목이 두 번
세어져 그 수치가 부푼다 — 이 저장소가 반복해서 맞은 형태다(2026-08-20 `60b6d95`
"기록이 자기 자신을 채점하고 있었다" · 2026-08-21 F-16 "측정 도구가 자기를 돌리는
세션의 산물을 결함으로 셌다").

이 파일의 첫 테스트가 그 불변이고, 나머지는 절이 파일의 다른 부분을 건드리지 않는지다.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from pending_intake import apply_to, render  # noqa: E402

from messiah.ops.record_vs_commit import _closed_lines, classify_closed  # noqa: E402

_ITEMS = [
    "- [x] **F-91** 등록부 정리 — 블록 삭제",
    "  - [X] G-54 구조적 장치 미착수",
]


def test_the_section_adds_no_new_closed_checkbox_lines():
    """**핵심 불변** — 절 안에 채점 대상 줄이 하나도 없어야 한다."""
    section = render(_ITEMS, day=date(2026, 9, 9), verdict="closed_with_uncommitted_source")

    assert _closed_lines(section) == [], "절이 체크박스를 되심어 항목이 두 번 세어진다"


def test_the_section_still_names_the_items():
    """오염을 피하려고 내용을 버리면 안 된다 — 항목 이름은 남아야 한다."""
    section = render(_ITEMS, day=date(2026, 9, 9), verdict="closed_with_uncommitted_source")

    assert "**F-91**" in section
    assert "G-54" in section


def test_round_trip_through_a_todo_file_does_not_change_the_count():
    """절을 붙인 뒤에도 파일의 「닫힌 줄」 수가 그대로인가 — 실제 파일 모양으로."""
    todo = "\n".join(
        [
            "# NEXT_TODO — MESSIAH",
            "",
            "> 에이징 규칙: 30일 초과 시 주간회의 최상단 강제 배치",
            "",
            "## 오늘",
            "",
            *_ITEMS,
            "",
        ]
    )
    before = len(_closed_lines(todo))
    section = render(_ITEMS, day=date(2026, 9, 9), verdict="closed_with_uncommitted_source")

    after = len(_closed_lines(apply_to(todo, section)))

    assert before == after == 2


def test_applying_twice_replaces_instead_of_stacking():
    todo = "> 에이징 규칙: 30일\n\n## 오늘\n\n- [x] **F-1** 무엇\n"
    first = apply_to(todo, render(_ITEMS, day=date(2026, 9, 9), verdict="ok"))
    second = apply_to(first, render([], day=date(2026, 9, 10), verdict="ok"))

    assert second.count("## 반입 대기(커밋 전)") == 1
    assert "2026-09-10" in second
    assert "2026-09-09" not in second
    assert "- [x] **F-1** 무엇" in second, "본문을 건드렸다"


def test_the_section_lands_after_the_aging_rule_not_before_the_title():
    todo = "# NEXT_TODO\n\n> 에이징 규칙: 30일\n\n## 오늘\n"
    out = apply_to(todo, render([], day=date(2026, 9, 9), verdict="ok"))

    assert out.startswith("# NEXT_TODO\n\n> 에이징 규칙: 30일")
    assert out.index("반입 대기") < out.index("## 오늘")


def test_no_anchor_still_gets_the_section():
    """앵커가 없다고 조용히 넘어가지 않는다 — 맨 앞에 붙인다."""
    out = apply_to(
        "## 오늘\n\n- [x] **F-1** 무엇\n", render([], day=date(2026, 9, 9), verdict="ok")
    )

    assert "반입 대기" in out
    assert "- [x] **F-1** 무엇" in out


def test_empty_list_says_so():
    section = render([], day=date(2026, 9, 9), verdict="ok")

    assert "없다" in section
    assert _closed_lines(section) == []


def test_status_lines_are_what_the_scorer_calls_implementation():
    """오늘 목록 16건의 정체 — 채점기는 `F-`/`G-` 머리표만 보고 구현으로 센다.

    「대기 지속, 새 정보 없음」 같은 **상태 갱신 줄**도 구현으로 분류된다. 이것이
    2026-09-09 이상점 1-6의 「추세」를 만든 실제 기전이고, 그래서 절 문구가
    "전부가 반입할 코드라는 뜻은 아니다"를 명시한다. 분류 규칙 자체를 고치는 것은
    판정 변경이라 사람 결정으로 남긴다(NEXT_TODO 등록).
    """
    status_line = "- [x] **G-57/3-1** 변경 없음, 대기 지속."

    assert classify_closed(status_line) == "implementation"
