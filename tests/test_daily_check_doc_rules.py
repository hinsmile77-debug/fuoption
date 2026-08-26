"""점검 스킬 문서에 **규칙이 실제로 적혀 있는지** (2026-08-26 F-58 · F-70).

두 규칙 다 「사람이 다음에 같은 실수를 하지 않게」 문서에 남기는 것이 처방의 전부다.
그런 처방은 조용히 지워져도 아무 데도 안 걸린다 — 2026-08-25 F-34 재발방지 3건이 정확히
그렇게 하루 만에 사라졌다(이상점 1-9). 그래서 문장의 **존재**를 테스트가 붙잡는다.
"""

from __future__ import annotations

from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "messiah-daily-check"


def _read(*parts: str) -> str:
    return (_SKILL_DIR.joinpath(*parts)).read_text(encoding="utf-8")


def test_split_id_rule_is_in_the_report_template() -> None:
    """F-58 — 즉시 조치와 재발방지에 **각각 번호를 준다.**

    한 ID 가 둘을 겸하면 즉시 조치를 닫는 순간 재발방지가 ID 없는 하위 문장이 되어
    ID 기반 절차(이월 처분 · NEXT_TODO · 18:10 자동조치) 전부에서 동시에 사라진다.
    """
    text = _read("references", "report_template.md")

    assert "번호를 나눈다" in text
    assert "재발방지 번호는 열려 있어야 한다" in text


def test_split_id_rule_is_also_in_the_record_duty() -> None:
    """계획 쪽에만 적으면 NEXT_TODO 로 옮겨 적는 단계에서 다시 합쳐진다."""
    text = _read("SKILL.md")

    assert "번호를 나눠 적는다" in text
    assert "자동 부여 금지" in text


def test_intraday_must_not_demand_a_commit() -> None:
    """F-70 — 장중 국면은 커밋을 요구하지 않는다. **판정이 하루 늦는 편이 낫다.**

    2026-08-26 15:22판 지시가 마감 7분·5분 전 커밋을 유발했다. 결과는 무해했지만 그
    무해의 근거가 「프로세스가 파일을 다시 안 읽는다」는 우연한 구현 성질이었다.
    """
    for text in (_read("references", "phases.md"), _read("references", "report_template.md")):
        assert "장중 국면은 커밋을 요구하지 않는다" in text
        assert "판정이 하루 늦는 편이 낫다" in text
