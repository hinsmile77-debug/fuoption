import math
from datetime import datetime, timedelta

import pytest
from messiah.core.messages import BarClosed, Horizon
from messiah.core.timeutil import KST
from messiah.models.labeling import label_and_weight
from messiah.models.trainer import (
    ExpertTrainingResult,
    _class_balance_weights,
    build_feature_vectors,
    build_training_data,
    generate_out_of_fold_predictions,
    train_formal_expert,
    train_prototype_expert,
)
from messiah.risk.cost_model import CostModel
from messiah.strategy.futures.expert import HorizonExpert
from messiah.strategy.futures.meta_labeler import MetaLabeler

_SYMBOL = "TEST"
_FEATURE_SET = "v-trainer-test"
_START = datetime(2026, 7, 27, 9, 0, tzinfo=KST)


def _bars(n: int, horizon: Horizon = Horizon.M5) -> list[BarClosed]:
    """가격이 오르내리며 ATR>0을 보장하는 합성 5분봉 시퀀스(사인파 + 정수 반올림)."""
    minutes = 5 if horizon == Horizon.M5 else 1
    out = []
    for i in range(n):
        close = round(100 + 10 * math.sin(i / 3))
        out.append(
            BarClosed(
                symbol=_SYMBOL,
                horizon=horizon,
                bar_open_kst=_START + timedelta(minutes=minutes * i),
                o_ticks=close,
                h_ticks=close + 3,
                l_ticks=close - 3,
                c_ticks=close,
                volume=100 + i,
            )
        )
    return out


# ---------------------------------------------------------------- build_feature_vectors


async def test_build_feature_vectors_is_one_to_one_with_bars():
    bars = _bars(10)
    vectors = await build_feature_vectors(bars, feature_set=_FEATURE_SET)

    assert len(vectors) == len(bars)
    for bar, vector in zip(bars, vectors):
        assert vector.symbol == bar.symbol
        assert vector.horizon == bar.horizon
        assert vector.feature_set == _FEATURE_SET


async def test_build_feature_vectors_empty_input():
    assert await build_feature_vectors([], feature_set=_FEATURE_SET) == []


async def test_build_feature_vectors_rejects_mixed_symbol():
    bars = _bars(3)
    bars[1] = bars[1].model_copy(update={"symbol": "OTHER"})
    with pytest.raises(ValueError):
        await build_feature_vectors(bars, feature_set=_FEATURE_SET)


# ---------------------------------------------------------------- build_training_data


async def test_build_training_data_produces_aligned_matrix():
    bars = _bars(30)
    vectors = await build_feature_vectors(bars, feature_set=_FEATURE_SET)

    feature_names, x, y, sample_weight = build_training_data(
        bars, vectors, cost_model=CostModel(), atr_window=14
    )

    assert len(feature_names) > 0
    assert x.shape == (len(y), len(feature_names))
    assert len(y) == len(sample_weight)
    assert len(y) > 0  # 30봉이면 최소 몇 개는 레이블이 나와야 함(14 워밍업 + 3 꼬리)
    assert set(int(label) for label in y) <= {-1, 0, 1}
    assert all(w > 0 for w in sample_weight)


def test_build_training_data_rejects_length_mismatch():
    bars = _bars(5)
    with pytest.raises(ValueError):
        build_training_data(bars, [], cost_model=CostModel())


async def test_build_training_data_empty_bars_returns_empty_matrix():
    feature_names, x, y, sample_weight = build_training_data([], [], cost_model=CostModel())
    assert feature_names == []
    assert x.shape == (0, 0)
    assert len(y) == 0
    assert len(sample_weight) == 0


# ---------------------------------------------------------------- _class_balance_weights


def test_class_balance_weights_hand_computed():
    labels = [-1] * 4 + [0] * 2 + [1] * 6  # n=12, num_classes=3
    weights = _class_balance_weights(labels)

    assert weights[-1] == pytest.approx(12 / (3 * 4))  # 1.0
    assert weights[0] == pytest.approx(12 / (3 * 2))  # 2.0
    assert weights[1] == pytest.approx(12 / (3 * 6))  # 0.6667


def test_class_balance_weights_single_class_is_one():
    weights = _class_balance_weights([0, 0, 0])
    assert weights == {0: pytest.approx(1.0)}


# ---------------------------------------------------------------- train_prototype_expert


async def test_train_prototype_expert_end_to_end_produces_usable_expert():
    bars = _bars(30)

    expert = await train_prototype_expert(
        bars, feature_set=_FEATURE_SET, model_version="trainer-smoke-v1"
    )

    assert isinstance(expert, HorizonExpert)
    assert expert.horizon == Horizon.M5
    assert expert.feature_set == _FEATURE_SET
    assert expert.model_version == "trainer-smoke-v1"

    vectors = await build_feature_vectors(bars, feature_set=_FEATURE_SET)
    view = expert.predict(vectors[-1])
    assert view.p_up + view.p_flat + view.p_down == pytest.approx(1.0, abs=1e-6)


async def test_train_prototype_expert_rejects_empty_bars():
    with pytest.raises(ValueError):
        await train_prototype_expert([], feature_set=_FEATURE_SET, model_version="trainer-smoke-v1")


async def test_train_prototype_expert_raises_when_data_too_short_for_any_label():
    bars = _bars(5)  # atr_window=14 기본값 대비 턱없이 부족 — 레이블 0건
    with pytest.raises(ValueError, match="레이블"):
        await train_prototype_expert(
            bars, feature_set=_FEATURE_SET, model_version="trainer-smoke-v1"
        )


async def test_train_prototype_expert_custom_atr_window_for_small_datasets():
    bars = _bars(10)  # atr_window=2로 낮추면 10봉으로도 레이블 생성 가능
    expert = await train_prototype_expert(
        bars,
        feature_set=_FEATURE_SET,
        model_version="trainer-smoke-v1",
        atr_window=2,
    )
    assert isinstance(expert, HorizonExpert)


# ---------------------------------------------------------------- generate_out_of_fold_predictions

_SMALL_SEARCH_SPACE: dict[str, tuple[str, float, float]] = {
    "num_leaves": ("int", 3, 7),
    "min_data_in_leaf": ("int", 1, 3),
}


async def test_generate_out_of_fold_predictions_produces_valid_records():
    bars = _bars(60)
    feature_vectors = await build_feature_vectors(bars, feature_set=_FEATURE_SET)
    cost_ticks = CostModel().estimate_round_trip_from_bars(bars, qty=1).total_ticks
    labels = label_and_weight(bars, atr_window=3, cost_ticks=cost_ticks)
    feature_names = sorted(feature_vectors[0].values.keys())

    records = await generate_out_of_fold_predictions(
        bars,
        feature_vectors,
        labels,
        feature_set=_FEATURE_SET,
        feature_names=feature_names,
        n_splits=3,
        n_members=1,
        num_boost_round=10,
    )

    assert len(records) > 0
    for record in records:
        assert record.probs.shape == (3,)
        assert record.probs.sum() == pytest.approx(1.0, abs=1e-6)
        assert record.ens_std >= 0.0


async def test_generate_out_of_fold_predictions_rejects_length_mismatch():
    bars = _bars(10)
    with pytest.raises(ValueError):
        await generate_out_of_fold_predictions(
            bars, [], [], feature_set=_FEATURE_SET, feature_names=["a"]
        )


async def test_generate_out_of_fold_predictions_empty_when_no_labels():
    bars = _bars(3)  # 너무 짧아 정렬 매칭될 레이블이 없음
    feature_vectors = await build_feature_vectors(bars, feature_set=_FEATURE_SET)
    records = await generate_out_of_fold_predictions(
        bars, feature_vectors, [], feature_set=_FEATURE_SET, feature_names=["a"]
    )
    assert records == []


# ---------------------------------------------------------------- train_formal_expert


async def test_train_formal_expert_end_to_end_produces_full_result():
    bars = _bars(80)

    result = await train_formal_expert(
        bars,
        feature_set=_FEATURE_SET,
        model_version="formal-v1",
        atr_window=3,
        n_splits=3,
        n_search_trials=2,
        search_space=_SMALL_SEARCH_SPACE,
        search_num_boost_round=10,
        final_num_boost_round=10,
        n_members=2,
        meta_num_boost_round=10,
    )

    assert isinstance(result, ExpertTrainingResult)
    assert isinstance(result.expert, HorizonExpert)
    assert isinstance(result.meta_labeler, MetaLabeler)
    assert result.expert.n_members == 2
    assert set(result.best_params.keys()) == set(_SMALL_SEARCH_SPACE.keys())
    assert result.n_oof_records > 0
    assert 0.0 <= result.meta_labeler.threshold <= 1.0


async def test_train_formal_expert_attaches_calibrator_when_oof_available():
    bars = _bars(80)
    result = await train_formal_expert(
        bars,
        feature_set=_FEATURE_SET,
        model_version="formal-v1",
        atr_window=3,
        n_splits=3,
        n_search_trials=2,
        search_space=_SMALL_SEARCH_SPACE,
        search_num_boost_round=10,
        final_num_boost_round=10,
        n_members=2,
        meta_num_boost_round=10,
    )
    assert result.expert.calibrator is not None


async def test_train_formal_expert_rejects_empty_bars():
    with pytest.raises(ValueError):
        await train_formal_expert([], feature_set=_FEATURE_SET, model_version="formal-v1")


async def test_train_formal_expert_raises_when_data_too_short_for_any_label():
    bars = _bars(5)
    with pytest.raises(ValueError, match="레이블"):
        await train_formal_expert(bars, feature_set=_FEATURE_SET, model_version="formal-v1")
