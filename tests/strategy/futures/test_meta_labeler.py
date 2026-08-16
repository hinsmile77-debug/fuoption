from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from messiah.core.messages import BarClosed, FeatureVector, Horizon
from messiah.core.timeutil import KST, UTC
from messiah.models.labeling import TripleBarrierLabel
from messiah.strategy.futures.meta_labeler import (
    META_FEATURE_NAMES,
    MetaLabeler,
    OutOfFoldRecord,
    build_meta_features,
    build_meta_training_data,
    compute_net_return,
    select_threshold,
)

_SYMBOL = "TEST"
_START = datetime(2026, 7, 27, 9, 35, tzinfo=KST)


def _bar(minute: int = 35) -> BarClosed:
    return BarClosed(
        symbol=_SYMBOL,
        horizon=Horizon.M5,
        bar_open_kst=datetime(2026, 7, 27, 9, minute, tzinfo=KST),
        o_ticks=100,
        h_ticks=101,
        l_ticks=99,
        c_ticks=100,
        volume=10,
    )


def _feature_vector(realized_vol: float = 0.03) -> FeatureVector:
    return FeatureVector(
        symbol=_SYMBOL,
        horizon=Horizon.M5,
        feature_set="v-test",
        values={"px_bb_width_20": realized_vol},
    )


def _label(label: int, ret_ticks: int) -> TripleBarrierLabel:
    return TripleBarrierLabel(
        symbol=_SYMBOL,
        horizon=Horizon.M5,
        t_start=_START,
        t_end=_START,
        entry_price_ticks=100,
        label=label,
        barrier="time",
        ret_ticks=ret_ticks,
    )


# ---------------------------------------------------------------- build_meta_features


def test_build_meta_features_hand_computed():
    probs = np.array([0.2, 0.3, 0.5])  # down,flat,up
    features = build_meta_features(
        probs, ens_std=0.05, feature_vector=_feature_vector(0.03), bar=_bar(35)
    )

    assert features == {
        "meta_p_primary": pytest.approx(0.5),
        "meta_margin": pytest.approx(0.2),  # 0.5 - 0.3
        "meta_ens_std": pytest.approx(0.05),
        "meta_realized_vol": pytest.approx(0.03),
        "meta_minutes_since_open": pytest.approx(35.0),
    }


def test_meta_minutes_since_open_is_timezone_independent():
    """같은 순간이면 tzinfo가 달라도 같은 값이어야 한다 (2026-08-16 P0 회귀).

    이 저장소에는 같은 봉을 다른 tzinfo로 돌려주는 로더가 둘 있다 —
    `ParquetArchiver`(KST) vs `ParquetBarReplaySource`(UTC). `.hour`를 그대로 읽던
    종전 구현은 재생 경로에서 이 Feature를 **540분 어긋나게** 만들었고, 학습이 본
    범위(0~390)와 추론이 본 범위(-540~-150)가 겹치지도 않았다. 값이 NaN이 아니라
    그럴듯한 숫자라 8거래일간 아무 흔적도 없었다(금지계명 6).
    """
    probs = np.array([0.2, 0.3, 0.5])
    kst_bar = _bar(35)  # 2026-07-27 09:35 KST
    utc_bar = kst_bar.model_copy(
        update={"bar_open_kst": kst_bar.bar_open_kst.astimezone(UTC)}  # 00:35 UTC, 같은 순간
    )

    kst_features = build_meta_features(probs, 0.05, _feature_vector(0.03), bar=kst_bar)
    utc_features = build_meta_features(probs, 0.05, _feature_vector(0.03), bar=utc_bar)

    assert kst_features["meta_minutes_since_open"] == pytest.approx(35.0)
    assert utc_features["meta_minutes_since_open"] == pytest.approx(35.0)


def test_build_meta_features_missing_realized_vol_defaults_to_zero():
    probs = np.array([0.2, 0.3, 0.5])
    fv = FeatureVector(symbol=_SYMBOL, horizon=Horizon.M5, feature_set="v-test", values={})
    features = build_meta_features(probs, ens_std=0.0, feature_vector=fv, bar=_bar(35))
    assert features["meta_realized_vol"] == 0.0


# ---------------------------------------------------------------- compute_net_return


def test_compute_net_return_up_signal():
    assert compute_net_return(1, _label(1, ret_ticks=10), cost_ticks=2) == 8


def test_compute_net_return_down_signal():
    assert compute_net_return(-1, _label(-1, ret_ticks=-10), cost_ticks=2) == 8


def test_compute_net_return_flat_signal_is_zero():
    assert compute_net_return(0, _label(0, ret_ticks=999), cost_ticks=2) == 0.0


# ---------------------------------------------------------------- build_meta_training_data


def test_build_meta_training_data_filters_flat_and_computes_y_and_returns():
    records = [
        OutOfFoldRecord(
            bar=_bar(),
            feature_vector=_feature_vector(),
            label=_label(label=1, ret_ticks=5),  # 실제로도 up → 신호 적중
            probs=np.array([0.1, 0.1, 0.8]),  # argmax=up
            ens_std=0.01,
        ),
        OutOfFoldRecord(
            bar=_bar(),
            feature_vector=_feature_vector(),
            label=_label(label=0, ret_ticks=-3),  # 예측은 down인데 실제는 flat → 신호 오적중
            probs=np.array([0.7, 0.1, 0.2]),  # argmax=down
            ens_std=0.02,
        ),
        OutOfFoldRecord(
            bar=_bar(),
            feature_vector=_feature_vector(),
            label=_label(label=1, ret_ticks=1),
            probs=np.array([0.2, 0.6, 0.2]),  # argmax=flat → 신호 아님, 제외돼야 함
            ens_std=0.0,
        ),
    ]

    x, y, net_returns = build_meta_training_data(records, cost_ticks=1.0)

    assert x.shape == (2, len(META_FEATURE_NAMES))
    assert list(y) == [1, 0]
    assert list(net_returns) == pytest.approx([4.0, 2.0])  # (5-1), (-(-3)-1)


def test_build_meta_training_data_empty_input():
    x, y, net_returns = build_meta_training_data([], cost_ticks=1.0)
    assert x.shape == (0, len(META_FEATURE_NAMES))
    assert len(y) == 0
    assert len(net_returns) == 0


# ---------------------------------------------------------------- select_threshold


def test_select_threshold_hand_computed():
    probs = [0.1, 0.3, 0.5, 0.7, 0.9]
    returns = [-5, -2, 3, 4, 1]
    candidates = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]

    best = select_threshold(probs, returns, candidates=candidates)

    assert best == pytest.approx(0.4)  # 평균 8/3 ≈ 2.667로 최대


def test_select_threshold_rejects_length_mismatch():
    with pytest.raises(ValueError):
        select_threshold([0.1, 0.2], [1.0])


def test_select_threshold_rejects_empty_input():
    with pytest.raises(ValueError):
        select_threshold([], [])


def test_select_threshold_ties_prefer_higher_threshold():
    # 두 임계값 모두 동일 평균(1.0)을 내면 더 큰(보수적인) 쪽을 선택해야 함.
    probs = [0.5, 0.9]
    returns = [1.0, 1.0]
    best = select_threshold(probs, returns, candidates=[0.0, 0.5, 0.9])
    assert best == pytest.approx(0.9)


# ---------------------------------------------------------------- MetaLabeler


def _synthetic_meta_data(n: int = 20) -> tuple[np.ndarray, np.ndarray]:
    rows = []
    labels = []
    for i in range(n):
        passed = i % 2 == 0
        rows.append([0.9 if passed else 0.3, 0.2, 0.01, 0.02, float(i)])
        labels.append(1 if passed else 0)
    return np.array(rows, dtype=float), np.array(labels, dtype=int)


def test_meta_labeler_train_predict_and_threshold():
    x, y = _synthetic_meta_data()
    labeler = MetaLabeler.train(horizon=Horizon.M5, x=x, y=y, threshold=0.5)

    features = dict(zip(META_FEATURE_NAMES, x[0]))
    prob = labeler.predict_pass_probability(features)
    assert 0.0 <= prob <= 1.0
    assert labeler.passes(features) == (prob >= 0.5)
    assert labeler.horizon == Horizon.M5
    assert labeler.feature_names == list(META_FEATURE_NAMES)


def test_meta_labeler_with_threshold_does_not_retrain():
    x, y = _synthetic_meta_data()
    labeler = MetaLabeler.train(horizon=Horizon.M5, x=x, y=y, threshold=0.5)
    stricter = labeler.with_threshold(0.9)

    features = dict(zip(META_FEATURE_NAMES, x[0]))
    assert stricter.threshold == 0.9
    assert labeler.threshold == 0.5  # 원본은 불변
    assert stricter.predict_pass_probability(features) == pytest.approx(
        labeler.predict_pass_probability(features)
    )


def test_meta_labeler_save_load_round_trip(tmp_path: Path):
    x, y = _synthetic_meta_data()
    labeler = MetaLabeler.train(horizon=Horizon.M5, x=x, y=y, threshold=0.42)
    features = dict(zip(META_FEATURE_NAMES, x[0]))
    before = labeler.predict_pass_probability(features)

    path = tmp_path / "meta_5m.lgb"
    labeler.save(path)
    reloaded = MetaLabeler.load(path)

    assert reloaded.threshold == pytest.approx(0.42)
    assert reloaded.horizon == Horizon.M5
    assert reloaded.feature_names == labeler.feature_names
    assert reloaded.predict_pass_probability(features) == pytest.approx(before, abs=1e-9)


# -------------------------------------------- 임계값 지지도 하한 (2026-08-04)


def test_select_threshold_rejects_candidates_with_too_little_support():
    """표본 몇 개의 우연을 '기대수익'이라 부르지 않는다.

    0.95 이상은 1건뿐인데 그게 가장 수익이 크다 — 지지도 하한이 없으면 그 극단값이
    선택되고, 새 데이터에서는 아무도 그 높이에 못 닿는다(2026-08-04 실측: 선택 임계에
    도달하는 학습 표본 0.5%, 검증 0%)."""
    probs = [i / 100 for i in range(100)]
    returns = [1.0] * 99 + [500.0]  # 마지막(0.99) 하나만 압도적

    lax = select_threshold(probs, returns, min_support_fraction=0.0)
    strict = select_threshold(probs, returns, min_support_fraction=0.20)

    assert lax > strict  # 하한이 없으면 극단값을 고른다
    reached = sum(1 for p in probs if p >= strict)
    assert reached >= 20  # 하한을 두면 최소 20%가 남는다


def test_select_threshold_support_floor_scales_with_sample_size():
    probs = [i / 100 for i in range(100)]
    returns = [1.0] * 100

    chosen = select_threshold(probs, returns, min_support_fraction=0.30)

    assert sum(1 for p in probs if p >= chosen) >= 30


def test_select_threshold_falls_back_when_no_candidate_meets_support():
    """어떤 후보도 하한을 못 채우면 선택 불가로 죽지 않고 가장 많이 남기는 쪽으로 폴백한다."""
    probs = [0.1, 0.95]
    returns = [1.0, 2.0]

    # 후보가 둘 다 1건씩만 남기는데 하한은 2건 — 전부 탈락 → 폴백
    chosen = select_threshold(probs, returns, candidates=[0.9, 0.95], min_support_fraction=1.0)

    assert chosen == pytest.approx(0.9)
