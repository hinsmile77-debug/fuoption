from pathlib import Path

import numpy as np
import pytest

from messiah.core.messages import BundleStatus, Horizon
from messiah.models.registry import (
    ModelRegistry,
    RegistryError,
    load_conformal_state,
    load_expert,
    load_manifest,
    load_meta_labeler,
    pack_bundle,
    save_conformal_state,
)
from messiah.models.trainer import ExpertTrainingResult
from messiah.models.validator import GateResult, ValidationReport
from messiah.strategy.futures.expert import HorizonExpert
from messiah.strategy.futures.meta_labeler import META_FEATURE_NAMES, MetaLabeler

_FEATURE_SET = "v-registry-test"


def _tiny_training_result(model_version: str) -> ExpertTrainingResult:
    rows: list[list[float]] = []
    labels: list[int] = []
    for label, base in ((-1, -5.0), (0, 0.0), (1, 5.0)):
        for i in range(6):
            rows.append([base + i * 0.001, (i % 2) * 0.01])
            labels.append(label)
    x = np.array(rows, dtype=float)
    y = np.array(labels, dtype=int)
    weight = np.ones(len(labels))
    expert = HorizonExpert.train(
        horizon=Horizon.M5,
        feature_set=_FEATURE_SET,
        model_version=model_version,
        feature_names=["dominant", "weak"],
        x=x,
        y=y,
        sample_weight=weight,
    )
    meta_x = np.random.default_rng(0).normal(size=(12, len(META_FEATURE_NAMES)))
    meta_y = np.array([1, 0] * 6)
    meta_labeler = MetaLabeler.train(horizon=Horizon.M5, x=meta_x, y=meta_y, threshold=0.5)
    return ExpertTrainingResult(
        expert=expert,
        meta_labeler=meta_labeler,
        best_params={},
        n_oof_records=12,
        n_meta_signals=12,
    )


def _passing_validation_report() -> ValidationReport:
    """세 상태(통과·미달·미측정)가 전부 들어 있는 리포트 — 매니페스트가 셋을 다 적는지
    보려면 셋이 다 있어야 한다 (2026-08-21 F-14)."""
    return ValidationReport(
        gates=[
            GateResult("cost_adjusted_sharpe", True, 1.5, 1.0),
            GateResult("max_drawdown", True, 0.1, 0.3),
            GateResult("calibration_brier", False, 0.9, 0.5),
            GateResult("sharpe", False, None, None, "미측정", measured=False),
        ]
    )


def _all_gates_passed_report() -> ValidationReport:
    """전 관문 통과 — live 승격이 실제로 가능한 유일한 모양 (F-14)."""
    return ValidationReport(
        gates=[
            GateResult("cost_adjusted_sharpe", True, 1.5, 1.0),
            GateResult("max_drawdown", True, 0.1, 0.3),
            GateResult("calibration_brier", True, 0.2, 0.5),
        ]
    )


def _pack(
    tmp_path: Path,
    *,
    bundle_id: str,
    model_version: str,
    report: ValidationReport | None = None,
) -> tuple:
    result = _tiny_training_result(model_version)
    report = report or _passing_validation_report()
    manifest = pack_bundle(
        bundle_id=bundle_id,
        horizon=Horizon.M5,
        training_result=result,
        validation_report=report,
        trained_range=("2026-01-01", "2026-01-02"),
        feature_set=_FEATURE_SET,
        run_id="test-run",
        out_dir=tmp_path,
    )
    return manifest, result


# ---------------------------------------------------------------- pack_bundle / manifest


def test_pack_bundle_writes_manifest_and_artifacts(tmp_path):
    manifest, _ = _pack(tmp_path, bundle_id="5m_test_1", model_version="v1")
    bundle_dir = tmp_path / "5m_test_1"
    assert (bundle_dir / "manifest.yaml").exists()
    assert (bundle_dir / "expert.json").exists()
    assert (bundle_dir / "expert_e0").exists()  # HorizonExpert.save()의 stem 규칙 — 확장자 없음
    assert (bundle_dir / "meta_labeler.lgb").exists()
    assert (bundle_dir / "feature_set.yaml").exists()
    assert (bundle_dir / "thresholds.yaml").exists()
    assert (bundle_dir / "validation_report.json").exists()
    assert manifest.initial_status == BundleStatus.CANDIDATE


def test_manifest_records_passed_failed_and_unmeasured_gates(tmp_path):
    """**통과분만 담으면 매니페스트가 "전원 통과"라고 거짓말한다** (2026-08-21 F-14).

    종전 스키마(`gates_passed: dict`)는 통과 관문만 담았다. 그래서 미달도 미측정도
    매니페스트에서 사라졌고, 승격 관문이 그것을 읽어도 막을 재료가 없었다.
    """
    manifest, _ = _pack(tmp_path, bundle_id="5m_test_2", model_version="v2")
    by_name = {gate.name: gate for gate in manifest.gates}

    assert by_name["cost_adjusted_sharpe"].passed is True
    assert by_name["calibration_brier"].passed is False  # 미달도 남는다
    assert by_name["calibration_brier"].measured is True
    assert by_name["sharpe"].passed is False  # 미측정도 남는다
    assert by_name["sharpe"].measured is False
    assert by_name["sharpe"].value is None  # NaN이 아니다

    # 통과분만 추린 읽기 전용 뷰는 그대로 쓸 수 있다(요약용 — 판정용이 아니다).
    assert "cost_adjusted_sharpe" in manifest.gates_passed
    assert "calibration_brier" not in manifest.gates_passed


def test_validation_report_json_is_strict_json(tmp_path):
    """`NaN`을 쓰면 jq·브라우저가 거부한다 — 미측정은 `null`로 적는다 (F-14 검증 ㉢)."""
    import json

    _pack(tmp_path, bundle_id="5m_test_json", model_version="vj")
    text = (tmp_path / "5m_test_json" / "validation_report.json").read_text(encoding="utf-8")
    assert "NaN" not in text
    rows = json.loads(text, parse_constant=_reject_constant)
    unmeasured = [r for r in rows if not r["measured"]]
    assert unmeasured and all(r["value"] is None for r in unmeasured)


def _reject_constant(name: str):  # pragma: no cover - 실패 경로
    raise AssertionError(f"엄밀 JSON이 아니다: {name}")


def test_legacy_manifest_migrates_to_the_gates_list(tmp_path):
    """옛 번들 두 개를 못 읽게 만들면 안 된다 (F-14 회귀 위험 ㉡)."""
    import yaml

    bundle_dir = tmp_path / "legacy-30m"
    bundle_dir.mkdir()
    (bundle_dir / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "bundle_id": "legacy-30m",
                "horizon": "30m",
                "trained_range": ["2025-12-12", "2026-07-01"],
                "run_id": "legacy",
                "feature_set": "v2026.08-ev",
                "validation_report": "validation_report.json",
                "gates_passed": {"calibration_brier": 0.33},
                "status": "candidate",
            }
        ),
        encoding="utf-8",
    )

    manifest = load_manifest(bundle_dir)
    assert manifest.legacy_gates is True
    assert manifest.initial_status == BundleStatus.CANDIDATE
    assert [g.name for g in manifest.gates] == ["calibration_brier"]
    assert manifest.blocking_gates() == ()  # 담긴 것은 통과분뿐 — 막을 재료가 없다


def test_promote_to_live_is_rejected_when_a_gate_is_unmeasured(tmp_path):
    """**미측정은 통과가 아니다** (F-14 ①). 오늘 실전 전환 전 반드시 막아야 하는 것."""
    manifest, _ = _pack(tmp_path, bundle_id="5m_gate_block", model_version="vb")
    registry = _registry(tmp_path)
    registry.register(manifest, tmp_path / manifest.bundle_id)
    registry.promote_to_shadow(manifest.bundle_id)

    with pytest.raises(RegistryError) as exc:
        registry.promote_to_live(manifest.bundle_id, operator="tester")
    assert "sharpe" in str(exc.value)  # 미측정 관문 이름이 사유에 남는다
    assert registry.get_live(Horizon.M5) is None


def test_promote_to_live_grandfathers_a_legacy_manifest(tmp_path):
    """옛 스키마 번들은 판정할 재료가 없다 — 거부가 아니라 유예하되 조용하지 않게."""
    import yaml

    manifest, _ = _pack(
        tmp_path,
        bundle_id="5m_legacy_live",
        model_version="vl",
        report=_all_gates_passed_report(),
    )
    bundle_dir = tmp_path / manifest.bundle_id
    data = yaml.safe_load((bundle_dir / "manifest.yaml").read_text(encoding="utf-8"))
    data["gates_passed"] = {g["name"]: g["value"] for g in data.pop("gates")}
    data["status"] = data.pop("initial_status")
    (bundle_dir / "manifest.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")

    registry = _registry(tmp_path)
    registry.register(manifest, bundle_dir)
    registry.promote_to_shadow(manifest.bundle_id)
    registry.promote_to_live(manifest.bundle_id, operator="tester")

    assert registry.get_live(Horizon.M5).bundle_id == manifest.bundle_id


def test_load_manifest_round_trip(tmp_path):
    manifest, _ = _pack(tmp_path, bundle_id="5m_test_3", model_version="v3")
    reloaded = load_manifest(tmp_path / "5m_test_3")
    assert reloaded == manifest


def test_load_expert_and_meta_labeler_round_trip(tmp_path):
    manifest, result = _pack(tmp_path, bundle_id="5m_test_4", model_version="v4")
    bundle_dir = tmp_path / manifest.bundle_id
    expert = load_expert(bundle_dir)
    meta = load_meta_labeler(bundle_dir)
    assert expert.model_version == "v4"
    assert meta.threshold == result.meta_labeler.threshold


def test_conformal_state_round_trip_and_default_empty(tmp_path):
    manifest, _ = _pack(tmp_path, bundle_id="5m_test_5", model_version="v5")
    bundle_dir = tmp_path / manifest.bundle_id
    assert load_conformal_state(bundle_dir) == []  # 아직 갱신 안 됨 — 빈 리스트
    save_conformal_state(bundle_dir, [0.1, 0.2, 0.05])
    assert load_conformal_state(bundle_dir) == [0.1, 0.2, 0.05]


# ---------------------------------------------------------------- ModelRegistry state machine


def _registry(tmp_path: Path) -> ModelRegistry:
    return ModelRegistry(tmp_path / "registry.db")


def test_register_then_promote_to_shadow_then_live(tmp_path):
    manifest, _ = _pack(
        tmp_path, bundle_id="5m_reg_1", model_version="v1", report=_all_gates_passed_report()
    )
    registry = _registry(tmp_path)
    registry.register(manifest, tmp_path / manifest.bundle_id)

    assert registry.get_live(Horizon.M5) is None
    registry.promote_to_shadow(manifest.bundle_id)
    assert registry.get(manifest.bundle_id)
    registry.promote_to_live(manifest.bundle_id, operator="tester")

    live = registry.get_live(Horizon.M5)
    assert live is not None
    assert live.bundle_id == manifest.bundle_id


def test_promote_to_live_auto_retires_previous_live(tmp_path):
    registry = _registry(tmp_path)
    m1, _ = _pack(
        tmp_path, bundle_id="5m_reg_a", model_version="a", report=_all_gates_passed_report()
    )
    m2, _ = _pack(
        tmp_path, bundle_id="5m_reg_b", model_version="b", report=_all_gates_passed_report()
    )
    registry.register(m1, tmp_path / m1.bundle_id)
    registry.register(m2, tmp_path / m2.bundle_id)

    registry.promote_to_shadow(m1.bundle_id)
    registry.promote_to_live(m1.bundle_id, operator="tester")
    registry.promote_to_shadow(m2.bundle_id)
    registry.promote_to_live(m2.bundle_id, operator="tester")

    assert registry.get_live(Horizon.M5).bundle_id == m2.bundle_id
    from messiah.models.registry import BundleRecord  # noqa: F401 — 타입 확인용

    retired = registry.list_by_status(BundleStatus.RETIRED)
    assert any(r.bundle_id == m1.bundle_id for r in retired)


def test_promotion_without_shadow_days_is_logged_not_blocked(tmp_path, caplog):
    """R18 20거래일 섀도 요건 — 이번 범위는 **계측과 경보까지**다 (F-14 ④).

    강제 차단은 별도 결정이 필요하다. 다만 "몇 거래일 겨뤘는지 아무도 모른다"가 조용히
    넘어가면 안 된다 — 미측정도 한 줄 남는다.
    """
    import logging as pylogging

    manifest, _ = _pack(
        tmp_path, bundle_id="5m_shadow", model_version="vs", report=_all_gates_passed_report()
    )
    registry = _registry(tmp_path)
    registry.register(manifest, tmp_path / manifest.bundle_id)
    registry.promote_to_shadow(manifest.bundle_id)

    with caplog.at_level(pylogging.INFO, logger="messiah"):
        registry.promote_to_live(manifest.bundle_id, operator="tester", shadow_trading_days=3)

    warned = [r for r in caplog.records if getattr(r, "tag", None) == "BundlePromotedWithoutShadow"]
    assert warned and warned[0].levelno == pylogging.WARNING
    assert registry.get_live(Horizon.M5).bundle_id == manifest.bundle_id  # 막지는 않는다


def test_threshold_source_of_a_legacy_bundle_reads_as_unknown(tmp_path):
    """옛 번들 두 개에는 출처 키가 없다 — **"최적화였다"고 가정하지 않는다** (F-6 검증 ②).

    NEXT_TODO N-4 규율(저장 상태 전용 검증): 새 번들을 만들지 않고 파일을 읽어 확인한다.
    """
    import yaml

    from messiah.models.registry import load_threshold_selection

    bundle_dir = tmp_path / "legacy-thresholds"
    bundle_dir.mkdir()
    (bundle_dir / "thresholds.yaml").write_text(
        yaml.safe_dump({"meta_labeler_threshold": 0.0}), encoding="utf-8"
    )

    read = load_threshold_selection(bundle_dir)
    assert read["threshold"] == 0.0
    assert read["source"] == "unknown"  # `fallback`도 `optimized`도 아닌 세 번째 상태
    assert read["measured"] is False


def test_invalid_transition_raises(tmp_path):
    manifest, _ = _pack(tmp_path, bundle_id="5m_reg_c", model_version="c")
    registry = _registry(tmp_path)
    registry.register(manifest, tmp_path / manifest.bundle_id)
    with pytest.raises(RegistryError):
        # candidate -> live 직행 금지
        registry.promote_to_live(manifest.bundle_id, operator="tester")


def test_register_duplicate_bundle_id_raises(tmp_path):
    manifest, _ = _pack(tmp_path, bundle_id="5m_reg_d", model_version="d")
    registry = _registry(tmp_path)
    registry.register(manifest, tmp_path / manifest.bundle_id)
    with pytest.raises(RegistryError):
        registry.register(manifest, tmp_path / manifest.bundle_id)


def test_unregistered_bundle_id_raises(tmp_path):
    registry = _registry(tmp_path)
    with pytest.raises(RegistryError):
        registry.promote_to_shadow("does-not-exist")


def test_drain_events_returns_and_clears_pending(tmp_path):
    manifest, _ = _pack(tmp_path, bundle_id="5m_reg_e", model_version="e")
    registry = _registry(tmp_path)
    registry.register(manifest, tmp_path / manifest.bundle_id)
    registry.promote_to_shadow(manifest.bundle_id)

    events = registry.drain_events()
    assert [e.new_status for e in events] == [BundleStatus.CANDIDATE, BundleStatus.SHADOW]
    assert registry.drain_events() == []  # 이미 비워짐


def test_list_by_status_and_get(tmp_path):
    registry = _registry(tmp_path)
    m1, _ = _pack(tmp_path, bundle_id="5m_reg_f", model_version="f")
    registry.register(m1, tmp_path / m1.bundle_id)
    assert [r.bundle_id for r in registry.list_by_status(BundleStatus.CANDIDATE)] == [m1.bundle_id]
    assert registry.get(m1.bundle_id) is not None
    assert registry.get("nope") is None
