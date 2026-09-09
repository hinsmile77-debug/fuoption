"""「완료라 적었지만 아직 반입 안 된 것」을 `NEXT_TODO.md` 상단에 모은다 (2026-09-09 고도화).

## 왜 만들었나

2026-09-09 이상점 1-6: 장후 채점기의 「기록↔반입 대조」 경보가 사흘 연속 커졌다 —
09-07 구현 1건 · 09-08 6건 · 09-09 15건을 완료로 적었는데 그날 커밋이 0건이다.
무엇이 「완료했지만 아직 안 보낸 것」인지 확인하려면 **매번 장후 배치 로그를 뒤져야**
했고, 그래서 다음 커밋 계획을 세울 때 항목이 흩어졌다.

`ops/record_vs_commit`이 이미 그 목록을 계산한다. 이 도구는 그 결과를 `NEXT_TODO.md`
최상단의 한 절에 **덮어쓰기로** 붙여, 사람이 파일을 열면 바로 보이게 한다.

## 체크박스를 그대로 옮기지 않는다 (중요)

`record_vs_commit._closed_lines()`는 `- [x]`로 시작하는 줄을 센다. 그 형태를 이 절에
그대로 복사하면 **같은 항목이 두 번 세어져** 「완료 처리 N건」이 부풀고, 이 도구가
자기가 재려던 수치를 오염시킨다 — 이 저장소가 반복해서 맞은 형태다(2026-08-20
`60b6d95` "기록이 자기 자신을 채점하고 있었다" · 2026-08-21 F-16). 그래서 체크박스를
떼고 평문으로 적는다. `tests/ops/test_pending_intake.py`가 그 불변을 못 박는다.

## 왜 판정하지 않나

이 도구는 목록을 **보이게만** 한다. 무엇을 먼저 커밋할지는 사람이 정한다(09-09 리포트가
그 우선순위를 사람 결정으로 넘겼다).
"""

from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path

from messiah.core.console import ensure_utf8_console
from messiah.ops.record_vs_commit import assess

_BEGIN = "<!-- 반입대기: 시작 (자동 생성 — scripts/pending_intake.py) -->"
_END = "<!-- 반입대기: 끝 -->"

#: 절을 끼울 자리 — 에이징 규칙 인용구 바로 다음. 파일 맨 앞의 제목·규칙은 건드리지 않는다.
_ANCHOR = re.compile(r"^> 에이징 규칙:.*$", re.MULTILINE)

#: `- [x] **F-91** 어쩌고` → `**F-91** 어쩌고`
_CHECKBOX = re.compile(r"^\s*[-*]\s*\[[xX]\]\s*")


def _plain(line: str) -> str:
    """체크박스를 떼고 한 줄로. 떼는 이유는 모듈 docstring에 있다."""
    return _CHECKBOX.sub("", line).strip()


def render(items: list[str], *, day: date, verdict: str) -> str:
    body = [
        _BEGIN,
        "",
        f"## 반입 대기(커밋 전) — {day.isoformat()} 자동 갱신",
        "",
    ]
    if not items:
        body += [
            "오늘 완료로 적은 구현 항목 중 미반입인 것은 **없다**.",
            "",
            f"판정: `{verdict}`",
        ]
    else:
        body += [
            f"장후 채점기가 오늘 **구현 종결**로 센 항목 {len(items)}건 — 그날 커밋에 아직 없다.",
            "",
            "> 이 목록은 채점기(`ops/record_vs_commit`)가 센 것을 그대로 옮긴 것이고, "
            "판정하지 않는다. `F-`/`G-` 머리표가 붙은 **상태 갱신 줄**도 함께 잡히므로 "
            "(예: 「대기 지속, 새 정보 없음」) 전부가 반입할 코드라는 뜻은 아니다. "
            "무엇을 먼저 보낼지는 사람이 정한다.",
            "",
        ]
        body += [f"{i}. {_plain(line)}" for i, line in enumerate(items, 1)]
        body += ["", f"판정: `{verdict}`"]
    body += ["", _END]
    return "\n".join(body)


def apply_to(text: str, section: str) -> str:
    """절을 덮어쓰거나(있으면) 앵커 뒤에 끼운다(없으면). **나머지는 한 글자도 안 건드린다.**"""
    if _BEGIN in text and _END in text:
        head, rest = text.split(_BEGIN, 1)
        _, tail = rest.split(_END, 1)
        return head + section + tail
    match = _ANCHOR.search(text)
    if match is None:  # 앵커가 없으면 맨 앞에 붙인다 — 조용히 넘기지 않는다
        return section + "\n\n" + text
    cut = match.end()
    return text[:cut] + "\n\n" + section + text[cut:]


def main() -> int:
    # 출력 단계에서 넘어지지 않는다 (F-81) — 한글 Windows 콘솔은 cp949다.
    ensure_utf8_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", help="저장소 루트 (기본: 현재 디렉터리)")
    parser.add_argument("--day", default=None, help="대상 날짜 YYYY-MM-DD (기본: 오늘)")
    parser.add_argument(
        "--dry-run", action="store_true", help="파일을 고치지 않고 만들 절만 표준출력에 낸다"
    )
    args = parser.parse_args()

    root = Path(args.repo_root)
    day = date.fromisoformat(args.day) if args.day else None
    if day is None:
        from messiah.core.timeutil import now_kst

        day = now_kst().date()

    result = assess(day, repo_root=root)
    items = [line for line in result.closed_items if _is_implementation(line)]
    section = render(items, day=day, verdict=result.verdict)

    if args.dry_run:
        print(section)
        return 0

    path = root / "dev_memory" / "NEXT_TODO.md"
    text = path.read_text(encoding="utf-8")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(apply_to(text, section))
    print(f"반입 대기 {len(items)}건 · {path} 갱신 · 판정 {result.verdict}")
    return 0


def _is_implementation(line: str) -> bool:
    from messiah.ops.record_vs_commit import classify_closed

    return classify_closed(line) == "implementation"


if __name__ == "__main__":
    raise SystemExit(main())
