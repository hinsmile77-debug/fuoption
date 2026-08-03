"""수정 유효성 자동 검증 (2026-08-03 고도화 B).

이 모듈이 존재하는 이유는 **재발을 자동으로 잡는 것** 하나다 — 2026-07-29~08-03에 같은 UI
크래시를 세 번 "고쳤다"고 판정하고 세 번 재발시켰다. 검증도 그 시나리오를 그대로 재현하는
데 무게를 둔다.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import yaml
from messiah.ops.fix_verification import (
    RegistryError,
    VerificationStatus,
    evaluate,
    load_daily_reports,
    load_registry,
    run,
)

_REGISTERED = date(2026, 8, 3)


def _report(day: date, **overrides) -> dict:
    base = {
        "date": day.isoformat(),
        "symbol": "A05608",
        "restarts": 0,
        "ui_restarts": 0,
        "breaches": [],
        "log_level_counts": {},
        "native_crashes": {"available": True, "count": 0, "details": []},
        "crash_forensics": {"armed": {}, "dumps": [], "findings": []},
        "bar_continuity": [
            {"horizon": "1m", "missing_minutes": 0, "longest_gap_minutes": 0},
        ],
    }
    base.update(overrides)
    return base


def _registry(tmp_path: Path, **overrides) -> list:
    entry = {
        "id": "ui-crash-isolation",
        "summary": "UI 크래시 격리",
        "registered": _REGISTERED.isoformat(),
        "metric": "native_crashes",
        "max": 0,
        "consecutive_days": 3,
        "deadline": "2026-08-14",
    }
    entry.update(overrides)
    path = tmp_path / "pending.yaml"
    path.write_text(yaml.safe_dump({"verifications": [entry]}), encoding="utf-8")
    return load_registry(path)


# ---------------------------------------------------------------- 핵심: 재발 판정


def test_a_crash_after_the_fix_is_reported_as_recurred(tmp_path: Path):
    """이 테스트가 고도화 B 전체의 요점이다.

    07-31에 "고쳤다"고 판정한 뒤 08-03에 같은 오프셋으로 재발한 상황을 그대로 재현한다.
    그때는 재발을 **새로운 사고**로 취급하며 또 새 가설을 세웠다. 이제는 시스템이
    "그 수정은 듣지 않았다"고 먼저 말한다.
    """
    registry = _registry(tmp_path)
    reports = {
        date(2026, 8, 4): _report(date(2026, 8, 4)),
        date(2026, 8, 5): _report(
            date(2026, 8, 5), native_crashes={"available": True, "count": 2, "details": []}
        ),
    }

    verdict = evaluate(registry, reports, today=date(2026, 8, 6))[0]

    assert verdict.status == VerificationStatus.RECURRED
    assert "2026-08-05" in verdict.detail
    assert verdict.needs_attention


def test_registration_day_itself_is_not_judged(tmp_path: Path):
    """수정은 그날 장 마감 후에 들어간다 — 등록일 리포트는 **수정 이전의 세계**다.
    그날 크래시를 재발로 세면 모든 수정이 등록 즉시 실패로 뜬다."""
    registry = _registry(tmp_path)
    reports = {
        _REGISTERED: _report(
            _REGISTERED, native_crashes={"available": True, "count": 2, "details": []}
        )
    }

    verdict = evaluate(registry, reports, today=date(2026, 8, 4))[0]

    assert verdict.status == VerificationStatus.PENDING
    assert verdict.clean_days == 0


# ---------------------------------------------------------------- 통과·대기·기한


def test_consecutive_clean_days_verify_the_fix(tmp_path: Path):
    registry = _registry(tmp_path)
    reports = {date(2026, 8, d): _report(date(2026, 8, d)) for d in (4, 5, 6)}

    verdict = evaluate(registry, reports, today=date(2026, 8, 7))[0]

    assert verdict.status == VerificationStatus.VERIFIED
    assert verdict.clean_days == 3
    assert not verdict.needs_attention


def test_not_enough_samples_stays_pending(tmp_path: Path):
    registry = _registry(tmp_path)
    reports = {date(2026, 8, 4): _report(date(2026, 8, 4))}

    verdict = evaluate(registry, reports, today=date(2026, 8, 5))[0]

    assert verdict.status == VerificationStatus.PENDING
    assert "1/3" in verdict.detail


def test_deadline_passed_without_verification_is_overdue(tmp_path: Path):
    registry = _registry(tmp_path)
    reports = {date(2026, 8, 4): _report(date(2026, 8, 4))}

    verdict = evaluate(registry, reports, today=date(2026, 8, 15))[0]

    assert verdict.status == VerificationStatus.OVERDUE
    assert verdict.needs_attention


# ---------------------------------------------------------------- 못 잰 날 (L18)


def test_uncountable_metric_days_count_neither_way(tmp_path: Path):
    """네이티브 크래시 집계가 불가능한 날(비 Windows 등)을 "0건"으로 세면 검증이 그냥
    통과해 버린다 — 못 센 것과 0건은 다르다. 통과로도 위반으로도 세지 않는다."""
    registry = _registry(tmp_path)
    reports = {
        date(2026, 8, 4): _report(
            date(2026, 8, 4), native_crashes={"available": False, "count": 0, "details": []}
        ),
        date(2026, 8, 5): _report(date(2026, 8, 5)),
    }

    verdict = evaluate(registry, reports, today=date(2026, 8, 6))[0]

    assert verdict.status == VerificationStatus.PENDING
    assert verdict.clean_days == 1  # 08-04는 안 셌다
    assert "판정 불가 1일" in verdict.detail


# ---------------------------------------------------------------- 등록부 자체의 무결성


def test_unknown_metric_is_loud_not_silent(tmp_path: Path):
    """오타 난 항목을 조용히 건너뛰면 "검증 중"이라고 믿는 항목이 실제로는 아무것도 안 본다
    — 이 모듈이 막으려는 실패 그 자체라 반드시 시끄러워야 한다."""
    path = tmp_path / "pending.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "verifications": [
                    {
                        "id": "오타",
                        "registered": "2026-08-03",
                        "metric": "native_crashs",  # 오타
                        "max": 0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RegistryError, match="알 수 없는 지표"):
        load_registry(path)


def test_entry_without_any_criterion_is_rejected(tmp_path: Path):
    path = tmp_path / "pending.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "verifications": [
                    {"id": "기준없음", "registered": "2026-08-03", "metric": "ui_restarts"}
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RegistryError, match="max/min"):
        load_registry(path)


def test_missing_registry_is_an_empty_list_not_an_error(tmp_path: Path):
    assert load_registry(tmp_path / "없는파일.yaml") == []


# ---------------------------------------------------------------- 실제 파일 왕복


def test_reads_real_daily_integrity_files(tmp_path: Path):
    for day in (date(2026, 8, 4), date(2026, 8, 5)):
        (tmp_path / f"daily_integrity_{day:%Y%m%d}.json").write_text(
            json.dumps(_report(day), ensure_ascii=False), encoding="utf-8"
        )
    (tmp_path / "daily_integrity_broken.json").write_text("{깨진 파일", encoding="utf-8")

    reports = load_daily_reports(tmp_path)

    # 깨진 파일 하나가 나머지 채점을 막지 않는다
    assert sorted(reports) == [date(2026, 8, 4), date(2026, 8, 5)]


def test_run_wires_registry_and_reports_together(tmp_path: Path):
    registry_path = tmp_path / "pending.yaml"
    registry_path.write_text(
        yaml.safe_dump(
            {
                "verifications": [
                    {
                        "id": "ui-restart-observability",
                        "summary": "UI 재기동 관측",
                        "registered": _REGISTERED.isoformat(),
                        "metric": "ui_restarts",
                        "max": 0,
                        "consecutive_days": 1,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    day = date(2026, 8, 4)
    (tmp_path / f"daily_integrity_{day:%Y%m%d}.json").write_text(
        json.dumps(_report(day, ui_restarts=2), ensure_ascii=False), encoding="utf-8"
    )

    verdicts = run(today=date(2026, 8, 5), registry_path=registry_path, log_dir=tmp_path)

    assert verdicts[0].status == VerificationStatus.RECURRED
