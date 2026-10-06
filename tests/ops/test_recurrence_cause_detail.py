"""재발 로그에 원인 문구를 싣는다 — 판정은 바꾸지 않는다 (2026-10-06 G-70).

2026-10-06 `exit-code-matches-log`가 「재발」로 떴는데 원인(장후 캐치업 exit 3)은 2026-08-11
최초 위반(G2 크래시)과 달랐다. 등록부 로그는 "위반 2회째"만 말해, 사람이 그날
`daily_integrity_*.json`을 따로 열어야 원인이 갈렸다.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import yaml

from messiah.ops import fix_verification as fv
from messiah.ops import integrity_report

_DAYS = [date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 2)]
_TODAY = date(2026, 10, 6)


def _exits(postmarket_code: int) -> dict:
    win32 = postmarket_code & 0xFFFF if postmarket_code > 0xFFFF else postmarket_code
    return {
        "available": True,
        "exits": [
            {"task": "Messiah", "at_kst": "15:36:50", "code": 0, "win32_code": 0},
            {
                "task": "Messiah-Postmarket",
                "at_kst": "07:24:06",
                "code": postmarket_code,
                "win32_code": win32,
            },
        ],
    }


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    registry = tmp_path / "pending.yaml"
    registry.write_text(
        yaml.safe_dump(
            {
                "verifications": [
                    {
                        "id": "exit-code-matches-log",
                        "summary": "진입점 종료 코드",
                        "registered": "2026-09-28",
                        "metric": "nonzero_task_exits",
                        "max": 0,
                        "consecutive_days": 3,
                        "deadline": "2026-10-30",
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    for day in _DAYS + [_TODAY]:
        code = 2147942403 if day == _TODAY else 0
        report = {"date": day.isoformat(), "breaches": [], "task_exit_codes": _exits(code)}
        (log_dir / f"daily_integrity_{day:%Y%m%d}.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
    return registry, log_dir


def test_recurred_item_gets_the_exit_code_cause(tmp_path: Path) -> None:
    registry, log_dir = _setup(tmp_path)
    verdicts = fv.run(today=_TODAY, registry_path=registry, log_dir=log_dir)
    assert verdicts[0].status == fv.VerificationStatus.RECURRED
    causes = fv.recurrence_causes(verdicts, today=_TODAY, registry_path=registry, log_dir=log_dir)
    assert causes == {
        "exit-code-matches-log": (
            "Messiah-Postmarket 07:24:06 종료 코드 2147942403(=0x80070003) → Win32 3"
        )
    }


def test_verdicts_are_unchanged_by_cause_lookup(tmp_path: Path) -> None:
    """판정 불변 — 원인 조회는 읽기만 한다. 전후 판정이 한 글자도 같아야 한다."""
    registry, log_dir = _setup(tmp_path)
    before = fv.run(today=_TODAY, registry_path=registry, log_dir=log_dir)
    fv.recurrence_causes(before, today=_TODAY, registry_path=registry, log_dir=log_dir)
    after = fv.run(today=_TODAY, registry_path=registry, log_dir=log_dir)
    assert before == after


def test_no_cause_when_not_recurred_or_unreadable(tmp_path: Path) -> None:
    registry, log_dir = _setup(tmp_path)
    clean = fv.run(today=_DAYS[-1], registry_path=registry, log_dir=log_dir)
    assert (
        fv.recurrence_causes(clean, today=_DAYS[-1], registry_path=registry, log_dir=log_dir) == {}
    )
    recurred = fv.run(today=_TODAY, registry_path=registry, log_dir=log_dir)
    assert (
        fv.recurrence_causes(
            recurred, today=_TODAY, registry_path=tmp_path / "missing.yaml", log_dir=log_dir
        )
        == {}
    )


def test_recurred_log_line_carries_cause_detail(tmp_path: Path, monkeypatch) -> None:
    """장후 리포트가 `FixVerificationRecurred` 한 줄에 `cause_detail`을 싣는다."""
    registry, log_dir = _setup(tmp_path)
    real_run, real_causes = fv.run, fv.recurrence_causes
    monkeypatch.setattr(fv, "run", lambda **kw: real_run(registry_path=registry, **kw))
    monkeypatch.setattr(
        fv,
        "recurrence_causes",
        lambda verdicts, **kw: real_causes(verdicts, registry_path=registry, **kw),
    )
    logged: list[tuple[str, str, dict]] = []
    from messiah.core import logging as mlog

    monkeypatch.setattr(mlog, "log", lambda tag, msg, *a, **f: logged.append((tag, msg, f)))
    integrity_report._report_fix_verifications(_TODAY, log_dir)

    recurred = [entry for entry in logged if entry[0] == "FixVerificationRecurred"]
    assert len(recurred) == 1
    _, msg, fields = recurred[0]
    assert "Win32 3" in fields["cause_detail"]
    assert "원인: Messiah-Postmarket" in msg
    others = [entry for entry in logged if entry[0] != "FixVerificationRecurred"]
    assert all("cause_detail" not in f for _, _, f in others)
