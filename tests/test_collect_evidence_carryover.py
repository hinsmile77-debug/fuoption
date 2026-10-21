"""수집기가 **전일 계획 절의 ID가 오늘 보고서에서 사라졌는지** 대사한다 (2026-10-21 G-85).

F-132(P0)가 10-13 보고서 Fix 표에서 다음 보고서로 넘어가며 처분 기록 없이 사라져 일주일 넘게
아무도 몰랐다. 여기서 붙잡는 성질:
  ① 계획 절(Fix·고도화·수익률) 안의 ID만 뽑고, 같은 줄에 ✅가 붙은 ID는 뺀다.
  ② 하위 헤딩(###)은 절 안으로, 같은 레벨 헤딩은 절 끝으로 본다.
  ③ `G-84후속`처럼 한글이 바로 붙어도 ID를 잡고, `F-13`이 `F-132`에 부분일치하지 않는다.
  ④ 전일 보고서가 없으면 0이 아니라 **미측정**(L18). 휴장일 공백은 가장 가까운 이전 보고서로.
  ⑤ 장전은 목록만, 장중·장후는 빠진 ID를 §9 적신호로 올린다.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / ".claude"
    / "skills"
    / "messiah-daily-check"
    / "scripts"
    / "collect_evidence.py"
)

_DAY = date(2026, 10, 15)

_PREV = """# 보고서
## 제1부
F-999 는 계획 절 밖이라 대상이 아니다.
## Fix 작업 구현계획 — 장후
| 번호 | 제목 |
|---|---|
| **F-132** | 승인키 예외 |
| F-1·F-2 | 종목 마스터 |
| F-50 | ✅ 오늘 완료 |
### 적용 순서
1. G-84후속 확인
## 고도화 방안 — 장중
### G-85(신규)
## 제4부. 다음 거래일
G-777 은 다시 절 밖이다.
"""


@pytest.fixture(scope="module")
def ce():
    spec = importlib.util.spec_from_file_location("_collect_evidence_carryover", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _reports(root: Path, files: dict[str, str]) -> None:
    rdir = root / "logs" / "dailycheck"
    rdir.mkdir(parents=True)
    for name, text in files.items():
        (rdir / name).write_text(text, encoding="utf-8", newline="\n")


def test_carried_ids_scope_and_closure(ce):
    assert ce.carried_ids(_PREV) == ["F-132", "F-1", "F-2", "G-84", "G-85"]


def test_short_id_does_not_match_longer(ce):
    text = "## Fix 작업 구현계획\nF-132 만 있다\n"
    assert ce.carried_ids(text) == ["F-132"]
    assert "F-13" not in ce._CARRY_ID_RE.findall("F-132")


def test_missing_ids_against_today(ce, tmp_path):
    # 10-14 보고서가 없다(점검 공백) — 10-13이 전일이다.
    _reports(
        tmp_path,
        {
            "2026-10-13_report.md": _PREV,
            "2026-10-15_report.md": "F-1·F-2 지속 · G-84후속 지속 · G-85 지속\n",
            "2026-10-16_report.md": "미래 보고서 F-132\n",
        },
    )
    lines, missing = ce.carryover_check(tmp_path, _DAY)
    assert missing == ["F-132"]
    assert "2026-10-13_report.md" in lines[0]


def test_no_prev_report_is_unmeasured(ce, tmp_path):
    _reports(tmp_path, {"2026-10-15_report.md": "x\n"})
    lines, missing = ce.carryover_check(tmp_path, _DAY)
    assert missing == []
    assert "미측정" in lines[0]


def test_today_report_absent_lists_only(ce, tmp_path):
    _reports(tmp_path, {"2026-10-13_report.md": _PREV})
    lines, missing = ce.carryover_check(tmp_path, _DAY)
    assert missing == []
    assert any("전부" in ln for ln in lines)


@pytest.mark.parametrize("phase,flagged", [("pre", False), ("intra", True), ("post", True)])
def test_flag_only_after_premarket(ce, tmp_path, phase, flagged):
    _reports(
        tmp_path,
        {"2026-10-13_report.md": _PREV, "2026-10-15_report.md": "F-1 F-2 G-84 G-85\n"},
    )
    (tmp_path / "logs" / "dailycheck").mkdir(exist_ok=True)
    text = ce.build(tmp_path, _DAY, phase, ce.load_config(tmp_path))
    section9 = text.split("## 9. 자동 적신호", 1)[1].split("### 전일 이월 ID 대사", 1)[0]
    assert ("이월 미처분 ID: F-132" in section9) is flagged
    assert "F-132" in text.split("### 전일 이월 ID 대사", 1)[1]
