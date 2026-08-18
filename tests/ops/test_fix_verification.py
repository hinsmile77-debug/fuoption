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
    scoreboard,
    scoreboard_line,
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
    """채점할 날은 충분히 있었는데 못 지킨 경우 — 이쪽이 진짜 `기한 초과`다."""
    registry = _registry(tmp_path)
    # 08-05 위반 뒤 08-06·08-07·08-10·08-11이 깨끗하지 않다(매일 크래시 1건).
    reports = {
        day: _report(day, native_crashes={"available": True, "count": 1, "details": []})
        for day in (date(2026, 8, 5), date(2026, 8, 6), date(2026, 8, 7), date(2026, 8, 10))
    }

    verdict = evaluate(registry, reports, today=date(2026, 8, 15))[0]

    assert verdict.status == VerificationStatus.RECURRED, "마지막으로 잰 날에 위반 중"

    # 위반이 아니라 "판정 불가"가 섞여 연속이 안 찬 경우 — 창은 세 날 다 있었다.
    window_existed = {
        date(2026, 8, 5): _report(date(2026, 8, 5)),
        date(2026, 8, 6): _report(
            date(2026, 8, 6), native_crashes={"available": False, "count": 0, "details": []}
        ),
        date(2026, 8, 7): _report(date(2026, 8, 7)),
    }
    verdict = evaluate(registry, window_existed, today=date(2026, 8, 15))[0]

    assert verdict.status == VerificationStatus.OVERDUE
    assert verdict.needs_attention


def test_a_deadline_with_no_days_to_score_is_not_the_same_as_a_missed_one(tmp_path: Path):
    """**"못 고쳤다"와 "잴 날이 없었다"는 다른 사건이다** (2026-08-18 F-0818P-4).

    08-18에 걸린 세 건이 전부 후자였다: 08-17 휴장으로 기한(08-19)까지 남은 거래일이
    1일인데 `consecutive_days: 3`이라 산술적으로 충족이 불가능했다. 그런데 판정은 다른
    항목과 똑같이 `기한 초과` — 읽는 사람에게는 "수정이 안 들었다"로 보인다. 처방이
    "고쳐라"가 아니라 "기한을 다시 잡아라"이므로 판정도 달라야 한다.
    """
    registry = _registry(tmp_path)  # deadline 08-14 · 3거래일 연속
    reports = {date(2026, 8, 13): _report(date(2026, 8, 13))}

    verdict = evaluate(registry, reports, today=date(2026, 8, 15))[0]

    assert verdict.status == VerificationStatus.UNREACHABLE
    assert "채점 가능일이 1일뿐" in verdict.detail
    assert verdict.needs_attention, "재조정도 사람이 해야 하는 일이다"


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
    """아직 못 잰 날이 연속 N일에 못 미치면 정체가 아니라 진행 중이다."""
    registry = _registry(tmp_path, metric="native_crashes", max=0, consecutive_days=3)
    reports = {
        date(2026, 8, 4): {"native_crashes": {"available": True, "count": 0}},
        date(2026, 8, 5): {"native_crashes": {"available": False, "count": 0}},
        date(2026, 8, 6): {"native_crashes": {"available": False, "count": 0}},
    }

    [verdict] = evaluate(registry, reports, today=date(2026, 8, 7))

    assert verdict.status == VerificationStatus.PENDING
    assert verdict.clean_days == 1


def test_stalled_counts_the_recent_run_not_the_whole_history(tmp_path: Path):
    """**정체는 "평생 한 번도 못 쟀나"가 아니라 "지금 못 재고 있나"다** (2026-08-18 F-0818P-1).

    종전 조건은 "통과 0 + 판정 불가 누적"이었다. 그래서 옛날에 한 번 통과한 축은 그 뒤로
    영영 못 재도 정체로 안 잡혔다 — 2026-08-18의 `exit-code-matches-log`가 정확히 그 자리다.
    08-12에 한 번 통과한 뒤 08-13·08-14·08-18을 `TimeoutExpired`로 연속 실패했는데도 판정은
    "회복 중 1/3"으로 조용했다. **축이 사흘째 눈을 감고 있다는 사실이 어디에도 안 나왔다.**
    """
    registry = _registry(tmp_path, metric="native_crashes", max=0, consecutive_days=3)
    reports = {
        date(2026, 8, 4): {"native_crashes": {"available": True, "count": 0}},
        date(2026, 8, 5): {"native_crashes": {"available": False, "count": 0}},
        date(2026, 8, 6): {"native_crashes": {"available": False, "count": 0}},
        date(2026, 8, 7): {"native_crashes": {"available": False, "count": 0}},
    }

    [verdict] = evaluate(registry, reports, today=date(2026, 8, 8))

    assert verdict.status == VerificationStatus.STALLED
    assert "최근 3거래일 연속 판정 불가" in verdict.detail


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


def test_schedule_drift_metric_reads_the_host_check():
    """2026-08-10 — 등록 정본과 실제 스케줄러 등록이 어긋난 날을 리포트가 말할 수 있는가.

    그날은 이 축 자체가 없어서, 트리거(08:20)와 기동 창(08:30)이 어긋난 채 오전이 사라지는
    동안 리포트에 그것을 적을 자리가 하나도 없었다.
    """
    extract = METRIC_EXTRACTORS["schedule_matches_registration"]

    matched = {
        "host_health": {"checks": [{"name": "schedule_drift", "available": True, "ok": True}]}
    }
    drifted = {
        "host_health": {"checks": [{"name": "schedule_drift", "available": True, "ok": False}]}
    }
    assert extract(matched) == 1.0
    assert extract(drifted) == 0.0


def test_schedule_drift_metric_separates_unmeasured_from_drifted():
    """못 잰 날을 "어긋남"으로 세면 늑대소년, "일치"로 세면 검사가 없느니만 못하다(L18)."""
    extract = METRIC_EXTRACTORS["schedule_matches_registration"]
    unmeasured = {
        "host_health": {"checks": [{"name": "schedule_drift", "available": False, "ok": True}]}
    }

    assert extract(unmeasured) is None
    assert extract({"host_health": {"checks": []}}) is None
    assert extract({}) is None, "축이 없던 2026-08-10 이전 리포트를 통과로 세면 안 된다"


def test_boot_recovery_metric_separates_unmeasured_from_unarmed():
    """못 잰 날을 "무장 안 됨"으로 세면 늑대소년이고, "무장됨"으로 세면 검사가 없느니만
    못하다 — 둘 다 아닌 **판정 불가**여야 한다(L18)."""
    extract = METRIC_EXTRACTORS["boot_recovery_armed"]
    unmeasured = {
        "host_health": {"checks": [{"name": "boot_recovery", "available": False, "ok": True}]}
    }

    assert extract(unmeasured) is None
    assert extract({"host_health": {"checks": []}}) is None


# ------------------------------------------------- 2026-08-10 A-1~A-3 신설 지표


def test_leg_shortfall_metric_counts_cycles_that_came_up_short():
    """2026-08-10 14:30 regular가 41/42였고 시간 축은 100%였다 — 그 자리를 세는 지표."""
    extract = METRIC_EXTRACTORS["series_leg_shortfall"]
    report = {
        "series_coverage": [
            {"name": "option_chain/regular", "short_cycles": [["14:30", 41]]},
            {"name": "flow_intraday/K2I", "short_cycles": [["10:46", 2], ["15:19", 2]]},
        ]
    }

    assert extract(report) == 3.0


def test_leg_shortfall_metric_is_unjudged_without_the_axis():
    """축이 없던 옛 리포트를 0으로 세면 "결손이 없었다"가 되어 거짓 통과다(L18)."""
    extract = METRIC_EXTRACTORS["series_leg_shortfall"]

    assert extract({}) is None
    assert extract({"series_coverage": [{"name": "ticks"}]}) is None
    assert extract({"series_coverage": [{"name": "ticks", "short_cycles": []}]}) == 0.0


def test_collection_start_lag_metric_folds_negative_values_to_zero():
    """트리거보다 이르게 뜬 날은 지연이 아니다 — max 기준 지표에 음수가 섞이면 통과가 쉬워진다."""
    extract = METRIC_EXTRACTORS["collection_start_lag_minutes"]

    assert extract({"collection_start_lag_minutes": 38.0}) == 38.0
    assert extract({"collection_start_lag_minutes": -12.0}) == 0.0
    assert extract({"collection_start_lag_minutes": None}) is None
    assert extract({}) is None


def test_nonzero_task_exit_metric_reads_the_os_not_the_log():
    """2026-08-10 G2가 255였다 — 로그는 정상 종료라 말했고 아무 지표도 그것을 몰랐다."""
    extract = METRIC_EXTRACTORS["nonzero_task_exits"]
    report = {
        "task_exit_codes": {
            "available": True,
            "exits": [
                {"task": "Messiah", "win32_code": 0},
                {"task": "Messiah-G2", "win32_code": 255},
            ],
        }
    }

    assert extract(report) == 1.0
    assert extract({"task_exit_codes": {"available": True, "exits": []}}) == 0.0


def test_nonzero_task_exit_metric_is_unjudged_when_the_query_failed():
    """이벤트 로그를 못 읽은 날을 "실패 0건"으로 세면 검사가 없느니만 못하다(L18)."""
    extract = METRIC_EXTRACTORS["nonzero_task_exits"]

    assert extract({"task_exit_codes": {"available": False, "exits": []}}) is None
    assert extract({}) is None


# ------------------------- B-3(2026-08-10): 재발의 나이


def _restarts_item(**overrides) -> PendingVerification:
    base = dict(
        id="x",
        summary="s",
        registered=date(2026, 8, 3),
        metric="restarts",
        consecutive_days=3,
        max_value=0.0,
    )
    base.update(overrides)
    return PendingVerification(**base)


def _days(*specs: tuple[int, int]) -> dict[date, dict]:
    return {date(2026, 8, d): _report(date(2026, 8, d), restarts=n) for d, n in specs}


def test_since_moves_the_scoring_start_without_erasing_when_it_was_fixed():
    """`registered`는 "언제 고쳤나"의 기록이라 덮어쓰지 않는다 — `since`가 따로 센다."""
    item = _restarts_item(since=date(2026, 8, 10))

    assert item.registered == date(2026, 8, 3)
    assert item.scored_after == date(2026, 8, 10)


def test_recovery_no_longer_needs_a_manual_since_reset():
    """2026-08-10의 그 자리 — 08-07 위반이 사흘째 매일 다시 보고되고 있었다.

    B-3의 처방은 `since:` **수동** 리셋이었고, 2026-08-18에 그 처방이 실패했다: 사람이
    밀지 않으면 회복이 영원히 안 보인다(그날 재발 11건 중 9건이 이미 회복 상태였다).
    이제 마지막 위반 이후 연속 통과가 `consecutive_days`에 닿으면 **손대지 않아도** 졸업한다.
    `since:`를 준 결과와 안 준 결과가 같다는 것이 그 증거다.
    """
    reports = _days((5, 0), (6, 0), (7, 3), (10, 0), (11, 0), (12, 0))

    before = evaluate([_restarts_item()], reports, today=date(2026, 8, 12))[0]
    after = evaluate([_restarts_item(since=date(2026, 8, 7))], reports, today=date(2026, 8, 12))[0]

    assert before.status == VerificationStatus.VERIFIED, "위반 뒤 3거래일이 깨끗하다"
    assert after.status == VerificationStatus.VERIFIED
    # 졸업했다고 위반이 없던 일이 되지는 않는다 — 언제 무너졌었는지는 문장에 남는다.
    assert "2026-08-07 위반에서 회복" in before.detail
    assert before.last_violation == date(2026, 8, 7)


def test_since_does_not_forgive_a_violation_after_it():
    """재설정은 면제가 아니다 — 그 뒤에 또 위반하면 그대로 재발이다."""
    reports = _days((7, 3), (10, 0), (11, 2), (12, 0))

    item = _restarts_item(since=date(2026, 8, 7))
    verdict = evaluate([item], reports, today=date(2026, 8, 12))[0]

    # 08-12 하루가 깨끗하니 `회복 중`이지만(3거래일 중 1일), **면제는 아니다** —
    # 위반일이 문장에 그대로 남고 연속 카운터는 그 위반에서 다시 시작한다.
    assert verdict.status == VerificationStatus.RECOVERING
    assert verdict.status != VerificationStatus.VERIFIED
    assert "2026-08-11" in verdict.detail
    assert verdict.clean_days == 1


def test_since_earlier_than_registered_cannot_widen_the_window():
    """`since`로 등록일 이전까지 채점을 넓히면 수정 이전의 세계를 채점하게 된다."""
    item = _restarts_item(since=date(2026, 7, 1))

    assert item.scored_after == date(2026, 8, 3)


def test_a_recurrence_says_how_old_it_is_in_trading_days():
    """`2026-08-07에 기준 위반`만으로는 오늘 난 것과 사흘 묵은 것이 같은 무게로 읽힌다."""
    # 08-07 위반 뒤 08-10 하루만 깨끗하다(3거래일 연속에 못 미쳐 아직 졸업 전).
    reports = _days((5, 0), (6, 0), (7, 3), (10, 0))

    verdict = evaluate([_restarts_item()], reports, today=date(2026, 8, 10))[0]

    assert verdict.status == VerificationStatus.RECOVERING
    assert "최초 2026-08-07" in verdict.detail
    assert "1거래일 전" in verdict.detail


def test_a_recurrence_that_happened_today_says_so():
    """오늘 난 재발은 **오늘**이라고 말해야 급한 정도가 바로 읽힌다."""
    reports = _days((10, 0), (11, 0), (12, 4))

    verdict = evaluate([_restarts_item()], reports, today=date(2026, 8, 12))[0]

    assert verdict.status == VerificationStatus.RECURRED
    assert verdict.violated_today is True
    # **오늘이 문장 맨 앞이다** (2026-08-18 F-0818P-1) — 종전엔 위반일이 먼저 오고 "(오늘)"이
    # 괄호로 뒤에 붙어, 사흘 묵은 줄 열 개 사이에서 오늘 것이 눈에 안 들어왔다.
    assert verdict.detail.startswith("오늘(2026-08-12) 기준 위반")


def test_unmeasured_count_does_not_punish_axes_that_are_still_accruing():
    """계측을 늘리는 일이 등록부에 벌점이 되면 아무도 새 축을 안 켠다 (2026-08-18 F-0818P-2)."""
    extract = METRIC_EXTRACTORS["unmeasured_count"]

    report = {
        "unmeasured": [
            "15m 피처 퇴화 판정(1거래일 누적 27 < 최소 30)",
            "30m 피처 퇴화 판정(1거래일 누적 14 < 최소 30)",
            "진입점 종료 코드(조회 실패: TimeoutExpired (2/2회 시도))",
        ],
        "unmeasured_kinds": {
            "accruing": [
                "15m 피처 퇴화 판정(1거래일 누적 27 < 최소 30)",
                "30m 피처 퇴화 판정(1거래일 누적 14 < 최소 30)",
            ],
            "failed": ["진입점 종료 코드(조회 실패: TimeoutExpired (2/2회 시도))"],
            "absent": [],
        },
    }

    assert extract(report) == 1.0, "고칠 것 하나 — 나머지 둘은 시간이 해결한다"


def test_unmeasured_count_counts_everything_in_reports_without_the_classification():
    """분류 이전에 쓰인 리포트의 과거 판정을 소급해서 뒤집지 않는다."""
    extract = METRIC_EXTRACTORS["unmeasured_count"]

    assert extract({"unmeasured": ["a", "b"]}) == 2.0


# ---------------- 등록부 스코어보드 (2026-08-18 G-0818P-1)
#
# 08-18 장후에 재발 11건 중 9건이 그날 기준을 충족했고 그중 `degenerate 57 → 0`은 08-16
# P0-1의 직접 성과였다. 그런데 그 사실을 말하는 산출물이 하나도 없어 사람이 추출기를 손으로
# 돌려서야 알았다. **측정하지 않으면 고쳤다는 사실도 없는 것과 같다.**


def test_the_scoreboard_carries_the_recovery_story_not_just_the_verdict():
    """`57.0 → 0.0`이 없으면 "회복했다"는 말에 크기가 없다."""
    # 08-05 위반(57) → 08-06 회복(0).
    reports = _days((4, 0), (5, 57), (6, 0))

    verdicts = evaluate([_restarts_item()], reports, today=date(2026, 8, 6))
    board = scoreboard(verdicts, today=date(2026, 8, 6))

    (entry,) = board["recovering"]
    assert entry["value"] == 0.0
    assert entry["prev_value"] == 57.0, "직전 **측정**값 — 회복의 크기가 여기 있다"
    assert entry["last_violation"] == "2026-08-05"
    assert board["recovered_today"] == ["x"], "연속 1일 = 오늘 처음 관측된 회복"


def test_a_second_clean_day_is_no_longer_todays_recovery():
    """어제도 깨끗했으면 오늘의 사건이 아니다 — 매일 같은 회복을 자랑하지 않는다."""
    reports = _days((4, 0), (5, 57), (6, 0), (7, 0))

    board = scoreboard(
        evaluate([_restarts_item()], reports, today=date(2026, 8, 7)), today=date(2026, 8, 7)
    )

    assert board["counts"]["recovering"] == 1
    assert board["recovered_today"] == []


def test_the_scoreboard_line_is_one_glance():
    """사람이 23줄을 훑지 않고 그날 형세를 읽는다 — 이 한 줄이 그 목적이다."""
    reports = _days((4, 0), (5, 57), (6, 0))

    board = scoreboard(
        evaluate([_restarts_item()], reports, today=date(2026, 8, 6)), today=date(2026, 8, 6)
    )
    line = scoreboard_line(board)

    assert "등록부 1건" in line
    assert "회복 중 1" in line
    assert "오늘 회복 x" in line


def test_today_violation_lands_in_its_own_bucket():
    """오늘 위반은 회복·졸업과 절대 같은 칸에 들어가면 안 된다(08-18에 그래서 묻혔다)."""
    reports = _days((4, 0), (5, 0), (6, 3))

    board = scoreboard(
        evaluate([_restarts_item()], reports, today=date(2026, 8, 6)), today=date(2026, 8, 6)
    )

    assert [e["id"] for e in board["today_violating"]] == ["x"]
    assert board["counts"]["recovering"] == 0
    assert board["recovered_today"] == []


# ---------------- 채점기가 끝까지 본다 (2026-08-18 F-0818P-1)
#
# 2026-08-18 장후 실측이 이 절의 출처다. `FixVerificationRecurred` 11건이 전부 옛 위반을
# 가리키는 동안 그중 9건은 그날 기준을 충족했고, 그날 유일하게 새로 위반한 항목은 사흘 묵은
# 문장 뒤에 숨었다. 원인은 채점 루프가 **최초 위반에서 멈춘** 것이었다.


def test_scoring_does_not_stop_at_the_first_violation():
    """멈추면 그 뒤에 무슨 일이 있었는지를 아무도 못 본다 — 회복도, 재악화도."""
    # 08-05 위반 → 08-06 회복 → 08-07 재위반 → 08-10 회복.
    reports = _days((4, 0), (5, 2), (6, 0), (7, 1), (10, 0))

    verdict = evaluate([_restarts_item()], reports, today=date(2026, 8, 10))[0]

    assert verdict.first_violation == date(2026, 8, 5)
    # **최근 위반이 따로 있다.** 종전엔 08-05에서 멈춰 08-07을 아예 못 봤다.
    assert verdict.last_violation == date(2026, 8, 7)
    assert verdict.violation_count == 2
    assert verdict.clean_days == 1, "마지막 위반 이후 연속 통과 — 누적이 아니다"
    assert verdict.status == VerificationStatus.RECOVERING


def test_a_recovering_item_is_not_an_error_but_is_not_silent_either():
    """회복 중은 ERROR가 아니다 — 그러나 조용히 지우지도 않는다(R10)."""
    reports = _days((4, 0), (5, 2), (6, 0))

    verdict = evaluate([_restarts_item()], reports, today=date(2026, 8, 6))[0]

    assert verdict.status == VerificationStatus.RECOVERING
    assert verdict.needs_attention is False, "매일 사람 앞에 올리면 오늘 위반이 묻힌다"
    assert "재발 이력 있음" in verdict.detail


def test_a_violation_with_no_clean_day_after_it_is_still_a_recurrence():
    """회복의 증거가 하나도 없으면 그건 회복이 아니라 재발 그대로다."""
    reports = _days((4, 0), (5, 2))

    verdict = evaluate([_restarts_item()], reports, today=date(2026, 8, 6))[0]

    assert verdict.status == VerificationStatus.RECURRED
    assert verdict.needs_attention is True


def test_the_age_counts_trading_days_not_calendar_days():
    """주말·휴장을 세면 "5일 전"이 실제로는 지난 거래일이 되어 급한 정도를 거꾸로 읽는다."""
    # 08-07(금) 위반 → 08-10(월)·08-11(화)만 리포트가 있다. 달력으로는 5일 전이다.
    reports = _days((7, 3), (10, 0), (11, 0))

    verdict = evaluate([_restarts_item()], reports, today=date(2026, 8, 12))[0]

    assert "2거래일 전" in verdict.detail


def test_registry_reads_since_from_yaml(tmp_path: Path):
    path = tmp_path / "reg.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "verifications": [
                    {
                        "id": "x",
                        "registered": "2026-08-03",
                        "since": "2026-08-10",
                        "metric": "restarts",
                        "max": 0,
                        "consecutive_days": 3,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    (item,) = load_registry(path)

    assert item.since == date(2026, 8, 10)
    assert item.scored_after == date(2026, 8, 10)


# ------------------------------------------- 예비 리포트 분리 (2026-08-12 F-3)


def test_provisional_reports_are_not_scored(tmp_path: Path):
    """15:36 예비본은 채점 대상이 아니다 — 그게 매일 거짓 재발을 만들던 자리다.

    08-11·08-12에 `daily-axes-measured`가 이틀 연속 「오늘 기준 위반 — 수정이 듣지 않았다」로
    ERROR를 냈는데, 11분 뒤 최종본의 `unmeasured`는 `[]`였다. **애초에 위반이 아니었다.**
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    bad = _report(date(2026, 8, 5), native_crashes={"available": True, "count": 9, "details": []})
    bad["provisional"] = True
    (log_dir / "daily_integrity_20260805.json").write_text(
        json.dumps(bad, ensure_ascii=False), encoding="utf-8"
    )
    (log_dir / "daily_integrity_20260806.json").write_text(
        json.dumps(_report(date(2026, 8, 6)), ensure_ascii=False), encoding="utf-8"
    )

    reports = load_daily_reports(log_dir)

    assert date(2026, 8, 5) not in reports, "예비본은 이력에 들어오지 않는다"
    assert date(2026, 8, 6) in reports, "확정본은 그대로 채점된다"


def test_final_report_is_scored_even_when_a_provisional_one_existed(tmp_path: Path):
    """같은 날짜의 확정본이 나중에 덮으면 그날은 정상 채점된다 — 침묵이 남으면 안 된다."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    final = _report(date(2026, 8, 5))
    final["provisional"] = False
    (log_dir / "daily_integrity_20260805.json").write_text(
        json.dumps(final, ensure_ascii=False), encoding="utf-8"
    )

    assert date(2026, 8, 5) in load_daily_reports(log_dir)


# ------------------------------------------- 국면 분포 지표 (2026-08-12 F-2)


def test_regime_unknown_ratio_reads_the_distribution():
    extract = METRIC_EXTRACTORS["regime_unknown_ratio"]

    # 2026-08-12 실측 — 14건 전부 UNKNOWN. 이 값이 1.0이라는 사실이 그날 어디에도 없었다.
    assert extract(_report(date(2026, 8, 12), regime_distribution={"UNKNOWN": 14})) == 1.0
    assert extract(
        _report(date(2026, 8, 13), regime_distribution={"UNKNOWN": 2, "TREND_UP": 8})
    ) == pytest.approx(0.2)


def test_regime_unknown_ratio_is_none_when_unmeasured():
    """옛 리포트와 **국면 미배선인 날**은 0.0이 아니라 판정 불가다 (L18).

    0으로 세면 국면이 죽어 있던 날이 "UNKNOWN 0% 통과"로 기록된다 — 이 지표가 잡으려는
    상태 그 자체가 통과로 둔갑한다.
    """
    extract = METRIC_EXTRACTORS["regime_unknown_ratio"]

    assert extract(_report(date(2026, 8, 12))) is None, "필드 없는 옛 리포트"
    assert extract(_report(date(2026, 8, 12), regime_distribution=None)) is None, "국면 미배선"
    assert extract(_report(date(2026, 8, 12), regime_distribution={})) is None, "빈 분포"
