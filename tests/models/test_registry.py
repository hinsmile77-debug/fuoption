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
    return ValidationReport(
        gates=[
            GateResult("cost_adjusted_sharpe", True, 1.5, 1.0),
            GateResult("max_drawdown", True, 0.1, 0.3),
            GateResult("calibration_brier", False, 0.9, 0.5),
        ]
    )


def _pack(tmp_path: Path, *, bundle_id: str, model_version: str) -> tuple:
    result = _tiny_training_result(model_version)
    report = _passing_validation_report()
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
    assert manifest.status == BundleStatus.CANDIDATE


def test_pack_bundle_only_keeps_passed_gates_in_manifest(tmp_path):
    manifest, _ = _pack(tmp_path, bundle_id="5m_test_2", model_version="v2")
    assert "cost_adjusted_sharpe" in manifest.gates_passed
    assert "calibration_brier" not in manifest.gates_passed  # 미달 관문은 제외


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
    manifest, _ = _pack(tmp_path, bundle_id="5m_reg_1", model_version="v1")
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
    m1, _ = _pack(tmp_path, bundle_id="5m_reg_a", model_version="a")
    m2, _ = _pack(tmp_path, bundle_id="5m_reg_b", model_version="b")
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
