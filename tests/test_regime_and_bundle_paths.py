"""G2 손익 측정 사슬의 두 마디 — 국면 학습(④-c)과 번들 생산(④-b) (2026-08-11).

11거래일간 `registry.db`의 `bundles`가 0행이었고 `intel.regime`은 **한 번도 발행된 적이
없었다.** 둘 다 "코드가 없어서"가 아니라 **그 코드를 부르는 경로가 없어서**였다:
`pack_bundle`을 부르는 곳은 토이 스모크 하나, `RegimeRuntime`은 어떤 운영 루프에도
안 붙어 있었다.

이 파일이 고정하는 것은 그 두 경로의 **판정 규칙**이다 — 실제 학습(수 분)은 장후에 돌지만,
"어떤 결과를 결선 가능으로 볼 것인가"는 데이터 없이도 지금 못박을 수 있다.
"""

from __future__ import annotations

import math
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from build_bundles import (  # noqa: E402
    _deferred_performance_gates,
    _promote,
    build_one,
    holdout_calibration,
    model_gates_passed,
)
from train_regime_ai import (  # noqa: E402
    MAX_UNKNOWN_RATIO,
    assess,
    holdout_regime_distribution,
)

from messiah.core.messages import BarClosed, BundleStatus, Horizon, Regime  # noqa: E402
from messiah.core.timeutil import KST  # noqa: E402
from messiah.models.registry import BundleManifest, ModelRegistry  # noqa: E402
from messiah.models.validator import GateResult, ValidationReport  # noqa: E402
from messiah.strategy.regime.service import RegimeAI  # noqa: E402

_SYMBOL = "TEST"
_START = datetime(2026, 7, 27, 9, 0, tzinfo=KST)


def _bars(n: int, horizon: Horizon = Horizon.M30) -> list[BarClosed]:
    """`tests/strategy/regime/test_service.py`와 같은 모양의 합성 시계열 — HMM이 상태를
    나눌 만큼의 구조(사인파 + 결정적 잡음)는 있고 재현 가능하다."""
    out = []
    price = 100.0
    step = {Horizon.M30: 30, Horizon.M5: 5, Horizon.M1: 1}[horizon]
    for i in range(n):
        price += math.sin(i / 4) * 2 + ((i * 53) % 7 - 3) * 0.2
        price = max(price, 10.0)
        out.append(
            BarClosed(
                symbol=_SYMBOL,
                horizon=horizon,
                bar_open_kst=_START + timedelta(minutes=step * i),
                o_ticks=round(price),
                h_ticks=round(price) + 2,
                l_ticks=round(price) - 2,
                c_ticks=round(price),
                volume=10 + i,
            )
        )
    return out


# ---------------------------------------------------------------- ④-c 국면 학습


def test_holdout_is_classified_one_bar_at_a_time():
    """**미래 참조 금지가 이 함수의 존재 이유다.**

    한 번에 전 구간을 주면 HMM의 Viterbi가 미래 관측까지 보고 상태를 매겨, 실시간 경로에서
    절대 못 얻는 분포가 나온다. 이 테스트는 판정 건수가 홀드아웃 봉 수와 정확히 같은지를
    본다 — 봉마다 한 번씩 불렸다는 뜻이다(`RegimeRuntime.handle_bar`와 같은 리듬).
    """
    bars = _bars(120)
    split = 90
    regime_ai = RegimeAI.fit(bars[:split], n_states_candidates=(2, 3))

    counts = holdout_regime_distribution(regime_ai, bars, split)

    assert sum(counts.values()) == len(bars) - split


def test_a_mostly_unknown_holdout_is_not_wireable():
    """붙여도 판단이 0건이면 붙일 이유가 없다 — `MetaDecisionEngine` 규칙 ②가 UNKNOWN을
    무조건 NO_TRADE로 보낸다. 그 사실을 **결선 전에** 알아야 한다."""
    counts = Counter({Regime.UNKNOWN.value: 80, Regime.RANGE.value: 20})

    ok, verdict = assess(counts)

    assert ok is False
    assert "UNKNOWN" in verdict


def test_a_constant_regime_is_not_wireable():
    """**첫 실학습이 이 관문을 요구했다** (2026-08-11). 홀드아웃 437봉이 전부 `TREND_DOWN`
    하나로 나왔는데 UNKNOWN이 0%라 종전 판정을 통과했다.

    상수 국면은 정보가 0인데 UNKNOWN보다 **나쁘다** — UNKNOWN은 "모른다"고 정직하게 말하고
    하위 AI를 보수 모드로 보내지만, 상수 `TREND_DOWN`은 재지 않은 사실을 단언하고 가중치
    매트릭스가 그것을 믿는다. 피처의 `no-degenerate-features`와 같은 잣대다.
    """
    counts = Counter({Regime.TREND_DOWN.value: 437})

    ok, verdict = assess(counts)

    assert ok is False
    assert "상수" in verdict


def test_a_dominant_but_not_constant_regime_is_still_wireable():
    """실제 시장에 한 국면이 오래 이어지는 구간은 있다 — 상한을 낮게 잡으면 정상 모델을
    막는다. 잡으려는 것은 "많다"가 아니라 첫 실행에서 관측된 **100%**다."""
    counts = Counter({Regime.RANGE.value: 70, Regime.HIGH_VOL.value: 30})

    ok, _verdict = assess(counts)

    assert ok is True


def test_a_mostly_known_holdout_is_wireable():
    counts = Counter({Regime.UNKNOWN.value: 10, Regime.TREND_UP.value: 50, Regime.RANGE.value: 40})

    ok, verdict = assess(counts)

    assert ok is True
    assert "결선 가능" in verdict


def test_an_empty_holdout_is_not_silently_wireable():
    """판정이 0건인 것과 "UNKNOWN이 0%"인 것은 다르다 — 후자로 읽으면 데이터가 없는
    모델이 만점을 받는다."""
    ok, _verdict = assess(Counter())

    assert ok is False


def test_classification_uses_the_transition_matrix_not_just_the_prior():
    """**2026-08-11 실측 회귀.** `classify()`가 관측 **하나**만 `predict_proba`에 넘기던
    동안 길이-1 사후분포 = `startprob × emission`이라 전이행렬도 이력도 안 쓰였다.
    학습된 `startprob_`이 원-핫이면(단일 시퀀스 적합에서 흔하다) 다른 상태의 확률이 항상
    0이 되어 **모든 봉이 같은 국면**으로 나온다 — 실데이터에서 437봉 전부 그랬다.

    같은 관측을 전 구간 Viterbi로 풀면 다섯 상태가 골고루 나왔다: 모델이 아니라 추론이
    틀렸다. 이 테스트는 startprob을 원-핫으로 **강제로** 만들어 그 상황을 재현한다.
    """
    import numpy as np

    bars = _bars(300)
    regime_ai = RegimeAI.fit(bars[:200], n_states_candidates=(3, 4))
    model = regime_ai.hmm_model._model
    forced = np.zeros_like(model.startprob_)
    forced[0] = 1.0
    model.startprob_ = forced  # 첫 관측이 상태 0이었던 학습 결과를 극단으로 재현

    seen = {regime_ai.classify(bars[: i + 1]).regime for i in range(200, 300)}

    assert (
        len(seen) > 1
    ), "원-핫 startprob 아래서도 국면이 갈려야 한다 — 하나면 길이-1 사후분포로 되돌아간 것이다"


def test_the_unknown_threshold_is_a_declared_constant():
    """임계값이 코드 어딘가에 숨어 있으면 다음 사람이 그 값을 못 찾는다 — 미검증
    초기값이라는 사실 자체가 모듈에 적혀 있어야 한다."""
    assert 0.0 < MAX_UNKNOWN_RATIO <= 1.0


# ---------------------------------------------------------------- ④-b 번들 생산


def _gate(name: str, passed: bool) -> GateResult:
    return GateResult(name=name, passed=passed, value=0.1, threshold=0.5)


def test_performance_gates_are_recorded_as_unmeasured_not_omitted():
    """**빼면 "넷을 다 통과했다"처럼 읽힌다.** 실제로는 일곱 중 넷이고 셋은 아무도 안 쟀다 —
    없는 것과 통과한 것을 같은 모양으로 두지 않는다(마흐디 L18)."""
    gates = _deferred_performance_gates()

    assert {g.name for g in gates} == {"sharpe", "max_drawdown", "negative_window_ratio"}
    assert all(not g.passed for g in gates)
    assert all("미측정" in g.detail for g in gates)


def test_model_gates_ignore_the_deferred_performance_gates():
    """`ValidationReport.passed`를 그대로 쓰면 성과 셋이 `passed=False`라 **항상 거짓**이
    되어 어떤 번들도 영원히 등록되지 않는다."""
    report = ValidationReport(
        gates=[
            *_deferred_performance_gates(),
            _gate("calibration_brier", True),
            _gate("feature_dependency", True),
            _gate("inference_latency_ms", True),
            _gate("serialization_round_trip", True),
        ]
    )

    assert report.passed is False  # 성과 셋 때문에
    assert model_gates_passed(report) is True  # 모델 넷만 보면 통과


def test_a_failed_model_gate_blocks_registration():
    report = ValidationReport(
        gates=[
            *_deferred_performance_gates(),
            _gate("calibration_brier", False),
            _gate("feature_dependency", True),
            _gate("inference_latency_ms", True),
            _gate("serialization_round_trip", True),
        ]
    )

    assert model_gates_passed(report) is False


@pytest.mark.asyncio
async def test_holdout_calibration_matches_labels_by_confirm_time(monkeypatch):
    """**키를 틀리면 표본 0건으로 조용히 떨어진다.** `TripleBarrierLabel.t_start`는 진입봉
    **확정시각**이라 `bar_open_kst`로 맞추면 전건이 매칭 실패하는데, 그 실패는 예외가
    아니라 "교정을 못 잰다"는 정상 분기로 보인다 — 그래서 테스트가 필요하다."""

    class _FakeView:
        p_down, p_flat, p_up = 0.2, 0.5, 0.3

    class _FakeExpert:
        def predict(self, _vector):
            return _FakeView()

    bars = _bars(80, Horizon.M5)

    async def _fake_vectors(bars_in, **_kwargs):
        return [object() for _ in bars_in]

    monkeypatch.setattr("build_bundles.build_feature_vectors", _fake_vectors)

    probs, true_idx = await holdout_calibration(
        _FakeExpert(),
        bars,
        feature_set="v2026.07",
        sidecars=None,
        atr_window=14,
        cost_ticks=0.0,
    )

    assert probs, "확정시각으로 매칭되면 표본이 나온다 — 0건이면 키가 틀린 것이다"
    assert len(probs) == len(true_idx)
    assert all(len(row) == 3 for row in probs)
    assert set(true_idx) <= {0, 1, 2}


@pytest.mark.asyncio
async def test_build_one_produces_a_loadable_bundle(tmp_path):
    """**이 테스트가 ④-b의 요점이다** — 학습→홀드아웃 관문→패킹까지 한 바퀴가 실제로 돈다.

    11거래일간 막혀 있던 것은 이 한 바퀴였고, 막힌 이유는 코드가 틀려서가 아니라 이 순서로
    부르는 곳이 없어서였다. 합성 데이터라 관문 통과 여부는 의미가 없지만(그건 장후 실데이터
    실행의 몫), **번들이 만들어지고 다시 읽히는가**는 지금 못박을 수 있다.
    """
    built = await build_one(
        horizon=Horizon.M5,
        bars=_bars(140, Horizon.M5),
        holdout_fraction=0.25,
        feature_set="v2026.07",
        sidecars=None,
        run_id="test-build",
        out_dir=tmp_path,
        atr_window=3,
        train_kwargs={
            "n_splits": 3,
            "n_search_trials": 2,
            "search_num_boost_round": 10,
            "final_num_boost_round": 10,
            "n_members": 2,
            "meta_num_boost_round": 10,
        },
    )

    assert built is not None, "합성 데이터로도 한 바퀴는 돌아야 한다"
    bundle_id, report, bundle_dir, trained_range = built

    # 관문 일곱이 전부 기록된다 — 성과 셋은 "미측정"으로.
    assert len(report.gates) == 7
    # 아티팩트가 실제로 다시 읽힌다(매니페스트만 그럴듯한 번들은 롤백 때 드러난다).
    from messiah.models.registry import load_expert, load_manifest, load_meta_labeler

    manifest = load_manifest(bundle_dir)
    assert manifest.bundle_id == bundle_id
    assert manifest.feature_set == "v2026.07"
    assert manifest.trained_range == trained_range
    assert load_expert(bundle_dir) is not None
    assert load_meta_labeler(bundle_dir) is not None
    # 통과한 관문만 매니페스트에 담긴다 — 미측정 성과 관문이 통과로 실리면 안 된다.
    assert "sharpe" not in manifest.gates_passed


# ---------------------------------------------------------------- 부트스트랩 승격


class _Args:
    def __init__(self, promote: str, operator: str | None = None):
        self.promote = promote
        self.operator = operator


def _register(registry: ModelRegistry, bundle_id: str, tmp_path: Path) -> None:
    manifest = BundleManifest(
        bundle_id=bundle_id,
        horizon=Horizon.M30,
        trained_range=("2026-01-02", "2026-08-10"),
        run_id="test",
        feature_set="v2026.07",
        validation_report="validation_report.json",
        gates_passed={"calibration_brier": 0.1},
    )
    registry.register(manifest, tmp_path / bundle_id)


def test_the_first_bundle_may_become_the_champion(tmp_path):
    """챔피언이 없으면 shadow에 넣어도 겨룰 상대가 없고 `get_live()`가 계속 None이라
    `intel.futures`는 안 흐른다 — 부트스트랩은 명시적으로 허용된 예외다."""
    registry = ModelRegistry(tmp_path / "registry.db")
    _register(registry, "first", tmp_path)

    status = _promote(registry, "first", Horizon.M30, _Args("live", operator="MW0601"))

    assert status == BundleStatus.LIVE.value
    assert registry.get_live(Horizon.M30).bundle_id == "first"
    registry.close()


def test_a_second_bundle_may_not_seize_the_crown(tmp_path):
    """챔피언 교체는 shadow에서 20거래일 겨룬 뒤 **성적으로** 하는 일이다 — 이 스크립트가
    조용히 갈아치우면 `evaluate_promotion`이 있으나 마나다(Ver 1.1 §6-4)."""
    registry = ModelRegistry(tmp_path / "registry.db")
    _register(registry, "champion", tmp_path)
    _promote(registry, "champion", Horizon.M30, _Args("live", operator="MW0601"))
    _register(registry, "challenger", tmp_path)

    with pytest.raises(SystemExit, match="이미 챔피언이 있다"):
        _promote(registry, "challenger", Horizon.M30, _Args("live", operator="MW0601"))

    assert registry.get_live(Horizon.M30).bundle_id == "champion"
    registry.close()


def test_shadow_is_the_default_path(tmp_path):
    registry = ModelRegistry(tmp_path / "registry.db")
    _register(registry, "challenger", tmp_path)

    status = _promote(registry, "challenger", Horizon.M30, _Args("shadow"))

    assert status == BundleStatus.SHADOW.value
    assert registry.get_live(Horizon.M30) is None
    registry.close()
