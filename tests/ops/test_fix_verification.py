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
    METRIC_EXTRACTORS,
    PendingVerification,
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


# ------------------------------------ 좁은 지표 (2026-08-05 — 넓은 그물이 낸 오탐 두 건)


def test_crash_forensics_unarmed_counts_only_unarmed_processes():
    """`breaches`로 채점하던 것을 좁힌 이유 — 무장과 무관한 사고가 이 수정을 "재발"로 만들었다.

    2026-08-04에 두 번 그랬다: ① PowerShell이 마커 줄에 접두사를 붙여 탐지가 깨졌고(ERROR),
    ② 그걸 고친 뒤엔 체결틱 0행(그날 결선 전이라 정상)이 breaches를 채워 또 ERROR가 났다.
    두 번 다 무장 자체는 정상이었다.
    """
    extract = METRIC_EXTRACTORS["crash_forensics_unarmed"]

    all_armed = {"crash_forensics": {"armed": {"ui": True, "l1_daily": True, "g2_paper": True}}}
    assert extract(all_armed) == 0.0

    one_missing = {"crash_forensics": {"armed": {"ui": True, "l1_daily": False}}}
    assert extract(one_missing) == 1.0

    # 무장은 멀쩡한데 다른 사고가 있는 날 — 이제 이 지표는 반응하지 않는다.
    noisy = {**all_armed, "breaches": ["체결틱 적재 0행", "1분봉 결손 9분"]}
    assert extract(noisy) == 0.0


def test_native_crashes_measurable_separates_zero_from_uncountable():
    """2026-08-04 회귀 — **크래시가 0건인 날에만** 집계가 실패했다.

    그 상태로는 `native_crashes ≤ 0`을 보는 등록부가 매일 "판정 불가"라 영원히 안 끝난다.
    이 지표는 "셀 수 있었는가" 자체를 min 기준으로 재서 그 상태를 직접 잡는다.
    """
    extract = METRIC_EXTRACTORS["native_crashes_measurable"]

    counted = {"native_crashes": {"supported": True, "available": True, "count": 0}}
    assert extract(counted) == 1.0

    failed = {"native_crashes": {"supported": True, "available": False, "count": 0}}
    assert extract(failed) == 0.0

    # 비Windows는 원래 못 센다 — 판정 대상이 아니다(매일 울리면 늑대소년).
    unsupported = {"native_crashes": {"supported": False, "available": False, "count": 0}}
    assert extract(unsupported) is None

    # `supported` 키가 없는 옛 리포트(08-04 이전 산출분)도 판정하지 않는다.
    legacy = {"native_crashes": {"available": True, "count": 2}}
    assert extract(legacy) is None


def test_clock_skew_metric_is_absolute_and_none_when_unmeasured():
    """부호가 아니라 크기가 판정 대상이다 — 어느 쪽으로 벌어져도 완성봉 경계가 깨진다."""
    extract = METRIC_EXTRACTORS["clock_skew_abs_seconds"]

    assert extract({"clock_skew_seconds": 9.72}) == 9.72
    assert extract({"clock_skew_seconds": -3.0}) == 3.0
    assert extract({"clock_skew_seconds": None}) is None
    assert extract({}) is None


# -------------------------------- 판정 불가 정체 (2026-08-05 고도화 2)


def test_repeated_unjudged_days_become_stalled_not_pending(tmp_path: Path):
    """2026-08-04 회귀 — 못 잰 날을 그냥 건너뛰면 **영원히 "검증 대기"로 조용히 남는다**.

    그날 `Get-WinEvent`가 크래시 0건인 날에만 실패해서 `ui-crash-isolation`은 며칠이
    지나도 0/3이었고, 아무도 그게 "진행 중"이 아니라 **"계측 고장"**이라는 걸 몰랐다.

    통과로도 위반으로도 안 세는 것 자체는 여전히 맞다(L18). 다만 그 상태가 쌓이는 것은
    그 자체로 사람이 봐야 할 사건이다.
    """
    registry = _registry(tmp_path, metric="native_crashes", max=0, consecutive_days=3)
    # 사흘 연속 집계 불가 — `_native_crashes`가 None을 돌려준다.
    reports = {
        date(2026, 8, 4 + i): {"native_crashes": {"available": False, "count": 0}} for i in range(3)
    }

    [verdict] = evaluate(registry, reports, today=date(2026, 8, 7))

    assert verdict.status == VerificationStatus.STALLED
    assert verdict.needs_attention is True
    assert "계측이 고장" in verdict.detail


def test_one_unjudged_day_is_still_just_pending(tmp_path: Path):
    """하루 못 잰 것은 정상이다 — 오탐을 만들면 이 신호도 무시당한다."""
    registry = _registry(tmp_path, metric="native_crashes", max=0, consecutive_days=3)
    reports = {date(2026, 8, 4): {"native_crashes": {"available": False, "count": 0}}}

    [verdict] = evaluate(registry, reports, today=date(2026, 8, 5))

    assert verdict.status == VerificationStatus.PENDING


def test_progress_beats_stalled(tmp_path: Path):
    """한 번이라도 실제로 통과한 적이 있으면 정체가 아니라 진행 중이다."""
    registry = _registry(tmp_path, metric="native_crashes", max=0, consecutive_days=3)
    reports = {
        date(2026, 8, 4): {"native_crashes": {"available": True, "count": 0}},
        date(2026, 8, 5): {"native_crashes": {"available": False, "count": 0}},
        date(2026, 8, 6): {"native_crashes": {"available": False, "count": 0}},
        date(2026, 8, 7): {"native_crashes": {"available": False, "count": 0}},
    }

    [verdict] = evaluate(registry, reports, today=date(2026, 8, 8))

    assert verdict.status == VerificationStatus.PENDING
    assert verdict.clean_days == 1


# ------------------------- 전제 채점 (2026-08-05 2차, 고도화 4)
#
# 2026-08-05에 일어난 일: 전날 넣은 P0-1(시계 동기)이 P0-2(합성기 방어)의 **전제를 깼다**.
# 등록부는 각 수정을 자기 지표로만 채점하므로 그걸 볼 수 없었다 — 08-04 리포트에서
# `horizon_findings`는 빈 배열이었고 그 항목은 깨끗하게 통과 중이었다.


def _premise_item(**overrides) -> PendingVerification:
    base = dict(
        id="composer-bucket-completeness",
        summary="합성기 겹④",
        registered=date(2026, 8, 5),
        metric="late_bar_drops",
        consecutive_days=3,
        max_value=0.0,
        premise_metric="delivery_latency_p99_seconds",
        premise_max=3.0,
        premise_summary="겹④ 상한 5초의 60%",
    )
    base.update(overrides)
    return PendingVerification(**base)


def _report_with(day: date, *, late_bar_drops: int = 0, latency_p99: float | None = None) -> dict:
    report: dict = {"date": day.isoformat(), "late_bar_drops": late_bar_drops}
    if latency_p99 is not None:
        report["delivery_latency"] = {"p99": latency_p99, "max": latency_p99, "samples": 9000}
    return report


def test_a_clean_metric_with_a_broken_premise_is_flagged():
    """**고도화 4의 핵심.** 결과가 아직 깨끗해도 딛고 선 전제가 무너지면 사람이 봐야 한다 —
    그게 곧 다음 사고의 예고다."""
    days = [date(2026, 8, 6), date(2026, 8, 7)]
    reports = {d: _report_with(d, late_bar_drops=0, latency_p99=4.2) for d in days}

    [verdict] = evaluate([_premise_item()], reports, today=date(2026, 8, 7))

    assert verdict.status == VerificationStatus.PREMISE_BROKEN
    assert verdict.needs_attention is True
    assert "4.2" in verdict.detail
    assert "겹④ 상한" in verdict.detail


def test_a_holding_premise_does_not_disturb_the_normal_verdict():
    """전제가 성립하면 종전과 완전히 같은 판정이어야 한다 —
    이 기능이 정상일을 어지럽히면 안 된다."""
    days = [date(2026, 8, 6), date(2026, 8, 7), date(2026, 8, 10)]
    reports = {d: _report_with(d, late_bar_drops=0, latency_p99=0.9) for d in days}

    [verdict] = evaluate([_premise_item()], reports, today=date(2026, 8, 10))

    assert verdict.status == VerificationStatus.VERIFIED


def test_recurrence_outranks_a_broken_premise():
    """결과가 이미 나빠졌으면 그게 더 급한 사실이다 — 전제 얘기로 덮으면 안 된다."""
    days = [date(2026, 8, 6), date(2026, 8, 7)]
    reports = {d: _report_with(d, late_bar_drops=12, latency_p99=4.2) for d in days}

    [verdict] = evaluate([_premise_item()], reports, today=date(2026, 8, 7))

    assert verdict.status == VerificationStatus.RECURRED


def test_an_unmeasured_premise_never_fabricates_a_verdict():
    """전제를 못 잰 날을 "전제 성립"으로도 "붕괴"로도 읽지 않는다(L18)."""
    days = [date(2026, 8, 6), date(2026, 8, 7), date(2026, 8, 10)]
    reports = {d: _report_with(d, late_bar_drops=0) for d in days}  # delivery_latency 없음

    [verdict] = evaluate([_premise_item()], reports, today=date(2026, 8, 10))

    assert verdict.status == VerificationStatus.VERIFIED


def test_a_typo_in_the_premise_metric_is_rejected_at_load(tmp_path: Path):
    """전제도 결과와 **같은 엄격도**로 검증한다 — 조용히 건너뛰면 "전제를 감시 중"이라고
    믿는 항목이 실제로는 아무것도 안 본다."""
    path = tmp_path / "registry.yaml"
    path.write_text(
        "verifications:\n"
        "  - id: x\n"
        "    registered: 2026-08-05\n"
        "    metric: late_bar_drops\n"
        "    max: 0\n"
        "    premise:\n"
        "      metric: delivery_latency_p99_secondz\n"
        "      max: 3.0\n",
        encoding="utf-8",
    )

    with pytest.raises(RegistryError, match="알 수 없는 전제 지표"):
        load_registry(path)


def test_a_premise_without_bounds_is_rejected_at_load(tmp_path: Path):
    path = tmp_path / "registry.yaml"
    path.write_text(
        "verifications:\n"
        "  - id: x\n"
        "    registered: 2026-08-05\n"
        "    metric: late_bar_drops\n"
        "    max: 0\n"
        "    premise:\n"
        "      metric: delivery_latency_p99_seconds\n",
        encoding="utf-8",
    )

    with pytest.raises(RegistryError, match="전제에 max/min"):
        load_registry(path)


def test_the_shipped_registry_premises_are_loadable():
    """실제 `configs/pending_verifications.yaml`이 로드되는지 — 오타는 기동이 아니라
    여기서 잡혀야 한다."""
    registry = load_registry()

    with_premise = [item for item in registry if item.premise_metric]
    assert with_premise, "전제가 붙은 항목이 하나도 없다"
    for item in with_premise:
        assert item.premise_metric in METRIC_EXTRACTORS


# ------------------------- EV 승격 자기검증 (2026-08-05 2차, 고도화 5)
#
# `ev_tod_cos`·`ev_close_remain`은 2026-08-04 관문이 상위로 지목했는데 프로덕션
# feature_set(v2026.07)에 없어 **측정조차 안 된다**. 승격이 조용히 안 먹는 것을 잡는다.


def _vol_report(day: date, *, absent: list[str] | None, with_field: bool = True) -> dict:
    entry: dict = {"samples": 900, "baseline_ic": 0.4, "beats_baseline": [], "measurable": True}
    if with_field:
        entry["absent_features"] = absent or []
    return {"date": day.isoformat(), "vol_axis": {"horizons": {"5m": entry}}}


def _ev_item(registered: date = date(2026, 8, 5)) -> PendingVerification:
    return PendingVerification(
        id="ev-features-measured",
        summary="EV 승격",
        registered=registered,
        metric="absent_watchlist_features",
        consecutive_days=3,
        max_value=0.0,
    )


def test_absent_ev_features_are_counted_as_a_violation():
    days = [date(2026, 8, 6), date(2026, 8, 7)]
    reports = {d: _vol_report(d, absent=["ev_tod_cos", "ev_close_remain"]) for d in days}

    [verdict] = evaluate([_ev_item()], reports, today=date(2026, 8, 7))

    assert verdict.status == VerificationStatus.RECURRED


def test_promotion_shows_up_as_zero_absent_features():
    days = [date(2026, 8, 6), date(2026, 8, 7), date(2026, 8, 10)]
    reports = {d: _vol_report(d, absent=[]) for d in days}

    [verdict] = evaluate([_ev_item()], reports, today=date(2026, 8, 10))

    assert verdict.status == VerificationStatus.VERIFIED


def test_a_day_without_the_scorecard_is_unjudged_not_clean():
    """채점을 안 돌린 날을 "0개 미탑재"로 읽으면 승격 안 했는데 통과해 버린다(L18)."""
    days = [date(2026, 8, 6), date(2026, 8, 7), date(2026, 8, 10)]
    reports = {d: {"date": d.isoformat()} for d in days}  # vol_axis 없음

    [verdict] = evaluate([_ev_item()], reports, today=date(2026, 8, 10))

    assert verdict.status == VerificationStatus.STALLED


def test_old_scorecards_without_the_field_are_unjudged():
    """`absent_features` 이전에 쓰인 산출물은 판정 근거가 없다 — 통과로 세면 안 된다."""
    days = [date(2026, 8, 6), date(2026, 8, 7), date(2026, 8, 10)]
    reports = {d: _vol_report(d, absent=None, with_field=False) for d in days}

    [verdict] = evaluate([_ev_item()], reports, today=date(2026, 8, 10))

    assert verdict.status == VerificationStatus.STALLED


def test_a_future_registration_date_stays_quiet_until_then():
    """승격 전 며칠을 매일 "재발"로 울리면 늑대소년이 된다 — 등록일이 미래면 조용해야 한다."""
    days = [date(2026, 8, 6), date(2026, 8, 7)]
    reports = {d: _vol_report(d, absent=["ev_tod_cos"]) for d in days}

    [verdict] = evaluate([_ev_item(registered=date(2026, 8, 12))], reports, today=date(2026, 8, 7))

    assert verdict.status == VerificationStatus.PENDING
    assert verdict.needs_attention is False


# ------------------- 2026-08-06 P0 신설 지표 (커버리지 축·부팅 무장)


def test_series_gap_findings_metric_reads_the_coverage_axis():
    extract = METRIC_EXTRACTORS["series_gap_findings"]

    assert extract({"series_findings": ["a", "b"]}) == 2.0
    assert extract({"series_findings": []}) == 0.0


def test_series_head_gap_metric_takes_the_worst_series():
    """2026-08-06에 4개 계열이 111~116분이었다 — 최악값이 그날의 크기다."""
    extract = METRIC_EXTRACTORS["series_head_gap_minutes_max"]
    report = {
        "series_coverage": [
            {"name": "ticks", "head_gap_minutes": 10.0},
            {"name": "option_chain/regular", "head_gap_minutes": 115.0},
        ]
    }

    assert extract(report) == 115.0


def test_series_head_gap_metric_is_unjudged_without_the_axis():
    """축이 없던 옛 리포트(2026-08-06 이전)를 0으로 세면 통과해 버린다(L18)."""
    extract = METRIC_EXTRACTORS["series_head_gap_minutes_max"]

    assert extract({}) is None


def test_boot_recovery_metric_reads_the_host_check():
    extract = METRIC_EXTRACTORS["boot_recovery_armed"]

    armed = {"host_health": {"checks": [{"name": "boot_recovery", "available": True, "ok": True}]}}
    unarmed = {
        "host_health": {"checks": [{"name": "boot_recovery", "available": True, "ok": False}]}
    }
    assert extract(armed) == 1.0
    assert extract(unarmed) == 0.0


def test_boot_recovery_metric_separates_unmeasured_from_unarmed():
    """못 잰 날을 "무장 안 됨"으로 세면 늑대소년이고, "무장됨"으로 세면 검사가 없느니만
    못하다 — 둘 다 아닌 **판정 불가**여야 한다(L18)."""
    extract = METRIC_EXTRACTORS["boot_recovery_armed"]
    unmeasured = {
        "host_health": {"checks": [{"name": "boot_recovery", "available": False, "ok": True}]}
    }

    assert extract(unmeasured) is None
    assert extract({"host_health": {"checks": []}}) is None
