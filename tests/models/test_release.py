import numpy as np

from messiah.core.messages import Horizon
from messiah.models.registry import ModelRegistry, pack_bundle
from messiah.models.release import load_release_manifest, pack_release, verify_release
from messiah.models.trainer import ExpertTrainingResult
from messiah.models.validator import GateResult, ValidationReport
from messiah.strategy.futures.expert import HorizonExpert
from messiah.strategy.futures.meta_labeler import META_FEATURE_NAMES, MetaLabeler

_FEATURE_SET = "v-release-test"


def _tiny_result(horizon: Horizon, model_version: str) -> ExpertTrainingResult:
    rows = [[i * 0.1, 0.0] for i in range(6)]
    labels = [-1, 0, 1, -1, 0, 1]
    x = np.array(rows, dtype=float)
    y = np.array(labels, dtype=int)
    weight = np.ones(len(labels))
    expert = HorizonExpert.train(
        horizon=horizon,
        feature_set=_FEATURE_SET,
        model_version=model_version,
        feature_names=["a", "b"],
        x=x,
        y=y,
        sample_weight=weight,
    )
    meta_x = np.random.default_rng(1).normal(size=(6, len(META_FEATURE_NAMES)))
    meta_y = np.array([1, 0, 1, 0, 1, 0])
    meta_labeler = MetaLabeler.train(horizon=horizon, x=meta_x, y=meta_y, threshold=0.5)
    return ExpertTrainingResult(expert, meta_labeler, {}, 6, 6)


def _report() -> ValidationReport:
    return ValidationReport(gates=[GateResult("cost_adjusted_sharpe", True, 1.2, 1.0)])


def _register_live(registry: ModelRegistry, tmp_path, horizon: Horizon, bundle_id: str) -> None:
    result = _tiny_result(horizon, bundle_id)
    manifest = pack_bundle(
        bundle_id=bundle_id,
        horizon=horizon,
        training_result=result,
        validation_report=_report(),
        trained_range=("2026-01-01", "2026-01-02"),
        feature_set=_FEATURE_SET,
        run_id="test",
        out_dir=tmp_path,
    )
    registry.register(manifest, tmp_path / bundle_id)
    registry.promote_to_shadow(bundle_id)
    registry.promote_to_live(bundle_id, operator="tester")


def test_pack_release_with_partial_horizons_reports_missing(tmp_path):
    registry = ModelRegistry(tmp_path / "registry.db")
    _register_live(registry, tmp_path, Horizon.M5, "5m_release_a")

    release = pack_release(registry, "release-test-1", out_dir=tmp_path)

    assert release.bundles == {"5m": "5m_release_a"}
    assert set(release.missing_horizons) == {"1m", "3m", "10m", "15m", "30m"}
    assert (tmp_path / "release-test-1" / "manifest.yaml").exists()


def test_pack_release_full_coverage_has_no_missing_horizons(tmp_path):
    registry = ModelRegistry(tmp_path / "registry.db")
    for horizon in (Horizon.M1, Horizon.M3, Horizon.M5, Horizon.M10, Horizon.M15, Horizon.M30):
        _register_live(registry, tmp_path, horizon, f"{horizon.value}_release_full")

    release = pack_release(registry, "release-test-2", out_dir=tmp_path)
    assert release.missing_horizons == []
    assert len(release.bundles) == 6


def test_load_release_manifest_round_trip(tmp_path):
    registry = ModelRegistry(tmp_path / "registry.db")
    _register_live(registry, tmp_path, Horizon.M5, "5m_release_b")
    release = pack_release(registry, "release-test-3", out_dir=tmp_path)

    reloaded = load_release_manifest(tmp_path / "release-test-3")
    assert reloaded == release


def test_verify_release_passes_when_bundle_still_live(tmp_path):
    registry = ModelRegistry(tmp_path / "registry.db")
    _register_live(registry, tmp_path, Horizon.M5, "5m_release_c")
    release = pack_release(registry, "release-test-4", out_dir=tmp_path)

    assert verify_release(registry, release) == []


def test_verify_release_flags_bundle_no_longer_live(tmp_path):
    registry = ModelRegistry(tmp_path / "registry.db")
    _register_live(registry, tmp_path, Horizon.M5, "5m_release_d")
    release = pack_release(registry, "release-test-5", out_dir=tmp_path)

    # 릴리스 발행 이후 다른 후보가 승격되어 이전 live가 강등된 상황을 재현.
    _register_live(registry, tmp_path, Horizon.M5, "5m_release_e")

    problems = verify_release(registry, release)
    assert len(problems) == 1
    assert "5m_release_d" in problems[0]
