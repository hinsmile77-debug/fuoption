"""피처 품질 관문 테스트 — 전부 손으로 계산한 known-value 기준 (SYSTEM.md R16).

이진 피처 × 이진 레이블의 Spearman은 **정확히 phi 계수**다(동순위 평균 순위 변환이 0/1
변수의 아핀 변환이고, 피어슨은 아핀 변환에 불변). 균형 주변합에서 phi = 2p − 1이므로
(p = 일치 비율) 원하는 IC를 소수점까지 정확히 만들 수 있다 — 난수 시드에 기대지 않는다.
"""

import math

import numpy as np
import pytest

from messiah.features.gate import (
    FeatureStatus,
    average_ranks,
    ic_t_stat,
    partial_spearman,
    run_gate,
    screen_redundancy,
    screen_standalone,
    screen_survival,
    spearman,
)

# ---------------------------------------------------------------- 통계 기본기


def test_average_ranks_splits_ties_evenly():
    ranks = average_ranks(np.array([10.0, 20.0, 20.0, 40.0]))

    # 20이 2·3위를 나눠 가지므로 둘 다 2.5 — 동순위를 무시하면 입력 순서가 IC를 바꾼다.
    assert list(ranks) == [1.0, 2.5, 2.5, 4.0]


def test_average_ranks_handles_all_ties():
    assert list(average_ranks(np.array([7.0, 7.0, 7.0]))) == [2.0, 2.0, 2.0]


def test_spearman_is_one_for_a_monotone_pair_regardless_of_scale():
    a = np.array([1.0, 2.0, 3.0, 4.0])
    b = np.array([10.0, 200.0, 3000.0, 40000.0])  # 단조지만 전혀 선형이 아니다

    assert spearman(a, b) == pytest.approx(1.0)
    assert spearman(a, -b) == pytest.approx(-1.0)


def test_spearman_is_undefined_not_zero_when_one_side_is_constant():
    """상수 피처의 상관은 0이 아니라 **정의되지 않는다** — 0으로 내면 "무관하다"는 없는
    사실을 주장하게 되고, 관문이 그걸 "약한 피처"로 분류해 조용히 통과시킨다."""
    assert spearman(np.array([1.0, 2.0, 3.0]), np.array([5.0, 5.0, 5.0])) is None


def test_ic_t_stat_matches_the_hand_computed_value():
    # t = 0.5 * sqrt((102-2)/(1-0.25)) = 0.5 * sqrt(133.3333) = 5.7735
    assert ic_t_stat(0.5, 102, overlap=1) == pytest.approx(5.7735, abs=1e-4)


def test_ic_t_stat_shrinks_by_the_overlap_correction():
    """3봉 겹침을 무시하면 t가 √3배 부푼다 — 2026-08-04에 전 레이블 변형이 유의해 보였던
    바로 그 오류다."""
    plain = ic_t_stat(0.3, 300, overlap=1)
    corrected = ic_t_stat(0.3, 300, overlap=3)

    assert plain is not None and corrected is not None
    # 유효표본 300 → 100. t 비율은 sqrt((300-2)/(100-2)) = sqrt(3.0408) = 1.744
    assert plain / corrected == pytest.approx(math.sqrt(298 / 98), abs=1e-6)


def test_ic_t_stat_is_none_when_the_effective_sample_collapses():
    assert ic_t_stat(0.5, 10, overlap=9) is None


def test_ic_t_stat_rejects_a_nonsensical_overlap():
    with pytest.raises(ValueError, match="overlap"):
        ic_t_stat(0.5, 100, overlap=0)


# ---------------------------------------------------------------- 표본 만들기


def _binary_pair(n: int, agreement: float) -> tuple[np.ndarray, np.ndarray]:
    """균형 주변합 이진 (피처, 레이블) — Spearman IC가 정확히 `2*agreement - 1`이 된다."""
    half = n // 2
    n_agree = int(round(half * agreement))
    y = np.array([1.0] * half + [-1.0] * half)
    feature = np.array(
        [1.0] * n_agree
        + [0.0] * (half - n_agree)  # y=+1 구간
        + [0.0] * n_agree
        + [1.0] * (half - n_agree)  # y=-1 구간
    )
    return feature, y


def test_binary_pair_helper_produces_the_intended_exact_ic():
    feature, y = _binary_pair(1000, 0.55)

    assert spearman(feature, y) == pytest.approx(0.10, abs=1e-12)


# ---------------------------------------------------------------- ① 단독 검정


def test_a_feature_that_is_never_computable_is_quarantined_as_dead():
    """`px_ema_cross_60` 부류 — 요구 봉 수가 히스토리 용량을 넘어 **프로덕션에서 항상 NaN**
    이었는데 nan_ratio 0.0165가 '정상 수준'으로 읽혔다(2026-08-04). 관문의 존재 이유 1번."""
    _feature, y = _binary_pair(1000, 0.55)
    x = np.full((1000, 1), np.nan)

    (verdict,) = screen_standalone(x, y, ["px_dead"], label_overlap_bars=1)

    assert verdict.status is FeatureStatus.QUARANTINED
    assert verdict.nan_ratio == 1.0
    assert "계산 자체가 안 된다" in verdict.reason


def test_a_mostly_missing_feature_is_quarantined_before_its_ic_is_trusted():
    feature, y = _binary_pair(1000, 0.55)
    feature = feature.copy()
    feature[:600] = np.nan  # 60% 결측 > 기본 임계 50%

    (verdict,) = screen_standalone(feature.reshape(-1, 1), y, ["px_sparse"], label_overlap_bars=1)

    assert verdict.status is FeatureStatus.QUARANTINED
    assert verdict.nan_ratio == pytest.approx(0.6)


def test_a_constant_feature_is_quarantined_not_merely_retired():
    _feature, y = _binary_pair(1000, 0.55)
    x = np.zeros((1000, 1))

    (verdict,) = screen_standalone(x, y, ["px_flat"], label_overlap_bars=1)

    assert verdict.status is FeatureStatus.QUARANTINED
    assert "정의 불가" in verdict.reason


def test_a_feature_with_no_relationship_is_retired():
    """IC가 정확히 0인 표본 — 일치·불일치가 완전 균형이라 시드에 기대지 않는다."""
    feature, y = _binary_pair(1000, 0.5)

    (verdict,) = screen_standalone(feature.reshape(-1, 1), y, ["px_noise"], label_overlap_bars=1)

    assert verdict.ic == pytest.approx(0.0)
    assert verdict.status is FeatureStatus.RETIRED
    assert "|IC|" in verdict.reason


def test_a_strong_feature_passes():
    feature, y = _binary_pair(1000, 0.55)

    (verdict,) = screen_standalone(feature.reshape(-1, 1), y, ["px_good"], label_overlap_bars=1)

    assert verdict.status is FeatureStatus.ACTIVE
    assert verdict.ic == pytest.approx(0.10)
    # t = 0.1 * sqrt(998/0.99) = 3.175
    assert verdict.t_stat == pytest.approx(3.1751, abs=1e-3)


def test_the_overlap_correction_actually_flips_a_verdict():
    """**이 파일에서 가장 중요한 테스트.** 같은 데이터, 같은 IC(0.10)인데 9봉 겹침을
    보정하면 유효표본이 1000 → 111로 줄어 t가 3.18 → 1.05가 된다. 보정을 빼먹으면 이
    피처는 '유의미'로 통과하고, 그게 2026-08-04에 실제로 일어난 일이다."""
    feature, y = _binary_pair(1000, 0.55)

    (naive,) = screen_standalone(feature.reshape(-1, 1), y, ["px_x"], label_overlap_bars=1)
    (corrected,) = screen_standalone(feature.reshape(-1, 1), y, ["px_x"], label_overlap_bars=9)

    assert naive.status is FeatureStatus.ACTIVE
    assert corrected.status is FeatureStatus.RETIRED
    assert corrected.ic == naive.ic  # 효과크기는 그대로 — 바뀐 건 신뢰도뿐
    assert corrected.t_stat == pytest.approx(1.0498, abs=1e-3)


def test_too_few_samples_is_skipped_not_retired():
    """판정 불가와 탈락은 다른 사건이다 — 섞으면 데이터 부족이 피처 결함으로 기록된다."""
    feature, y = _binary_pair(40, 0.55)

    (verdict,) = screen_standalone(feature.reshape(-1, 1), y, ["px_thin"], label_overlap_bars=1)

    assert verdict.status is FeatureStatus.SKIPPED
    assert "판정 불가" in verdict.reason


def test_screen_standalone_rejects_shape_mismatches():
    _feature, y = _binary_pair(100, 0.5)

    with pytest.raises(ValueError, match="열 수 불일치"):
        screen_standalone(np.zeros((100, 2)), y, ["only_one"], label_overlap_bars=1)
    with pytest.raises(ValueError, match="행 수 불일치"):
        screen_standalone(np.zeros((99, 1)), y, ["a"], label_overlap_bars=1)


# ---------------------------------------------------------------- ② 중복 검정


def test_redundant_pair_drops_the_weaker_predictor():
    strong, y = _binary_pair(1000, 0.60)  # IC 0.20
    weak, _ = _binary_pair(1000, 0.55)  # IC 0.10
    # weak을 strong과 거의 같게 만든다(순위상관 1.0) — 중복 판정 대상.
    weak = strong.copy()
    x = np.column_stack([strong, weak])

    verdicts = screen_standalone(x, y, ["px_strong", "px_weak"], label_overlap_bars=1)
    verdicts = screen_redundancy(x, ["px_strong", "px_weak"], verdicts)

    by_name = {v.name: v for v in verdicts}
    assert by_name["px_strong"].status is FeatureStatus.ACTIVE
    assert by_name["px_weak"].status is FeatureStatus.RETIRED
    assert by_name["px_weak"].redundant_with == "px_strong"


def test_redundancy_tie_is_broken_by_name_so_reruns_agree():
    """동률에 결정론적 타이브레이크가 없으면 같은 데이터로 두 번 돌려 다른 피처셋이 나온다."""
    feature, y = _binary_pair(1000, 0.55)
    x = np.column_stack([feature, feature.copy()])

    verdicts = screen_standalone(x, y, ["px_bbb", "px_aaa"], label_overlap_bars=1)
    verdicts = screen_redundancy(x, ["px_bbb", "px_aaa"], verdicts)

    by_name = {v.name: v.status for v in verdicts}
    assert by_name["px_aaa"] is FeatureStatus.ACTIVE  # 이름 순으로 앞선 쪽이 남는다
    assert by_name["px_bbb"] is FeatureStatus.RETIRED


def test_uncorrelated_survivors_both_stay():
    strong, y = _binary_pair(1000, 0.60)
    other = np.tile([0.0, 1.0], 500)  # strong과 상관 없음
    x = np.column_stack([strong, other])

    verdicts = screen_standalone(x, y, ["px_a", "px_b"], label_overlap_bars=1)
    kept = {
        v.name
        for v in screen_redundancy(x, ["px_a", "px_b"], verdicts)
        if v.status is FeatureStatus.ACTIVE
    }

    assert "px_a" in kept  # px_b는 IC가 0이라 ①에서 이미 탈락 — 중복 판정 대상이 아니다


def test_redundancy_only_compares_features_that_passed_the_first_screen():
    """①에서 탈락한 피처와의 상관은 볼 이유가 없다 — 비교하면 살아남은 쪽이 죽은 쪽 때문에
    탈락하는 뒤집힌 결과가 난다."""
    strong, y = _binary_pair(1000, 0.60)
    dead = np.full(1000, np.nan)
    x = np.column_stack([strong, dead])

    verdicts = screen_standalone(x, y, ["px_a", "px_dead"], label_overlap_bars=1)
    out = {v.name: v for v in screen_redundancy(x, ["px_a", "px_dead"], verdicts)}

    assert out["px_a"].status is FeatureStatus.ACTIVE
    assert out["px_dead"].status is FeatureStatus.QUARANTINED  # 그대로 유지


# ---------------------------------------------------------------- ③ 생존 검정


def _active(name: str) -> object:
    from messiah.features.gate import FeatureVerdict

    return FeatureVerdict(name, FeatureStatus.ACTIVE, "ok", 0.0, 1000, ic=0.1, t_stat=3.0)


def test_survival_does_not_run_when_there_are_too_few_windows():
    """G1 창이 하나뿐인 지금 상태에서 3창 요구를 적용하면 관문이 데이터 부족을 피처 결함으로
    오역해 **전부 탈락시킨다**."""
    verdicts = [_active("px_a"), _active("px_b")]

    out = screen_survival(verdicts, [{"px_a": 1.0}], min_windows=3)

    assert [v.status for v in out] == [FeatureStatus.ACTIVE, FeatureStatus.ACTIVE]


def test_survival_keeps_a_feature_that_stays_in_the_top_band():
    windows = [{"px_a": 10.0, "px_b": 1.0}] * 3

    out = {v.name: v for v in screen_survival([_active("px_a"), _active("px_b")], windows)}

    # top_fraction 0.8 × 2개 = 상위 1.6 → 반올림 2 → 둘 다 상위권
    assert out["px_a"].status is FeatureStatus.ACTIVE
    assert out["px_a"].survived_windows == 3


def test_survival_retires_a_feature_that_only_shone_in_one_window():
    windows = [
        {"px_a": 10.0, "px_b": 9.0, "px_c": 8.0, "px_d": 7.0, "px_e": 0.0},
        {"px_a": 10.0, "px_b": 9.0, "px_c": 8.0, "px_d": 7.0, "px_e": 0.0},
        {"px_e": 10.0, "px_a": 9.0, "px_b": 8.0, "px_c": 7.0, "px_d": 0.0},
    ]

    out = {v.name: v for v in screen_survival([_active("px_a"), _active("px_e")], windows)}

    assert out["px_a"].status is FeatureStatus.ACTIVE  # 3창 전부 상위 4/5
    assert out["px_e"].status is FeatureStatus.RETIRED
    assert out["px_e"].survived_windows == 1


def test_survival_treats_an_unmentioned_feature_as_absent_not_harmless():
    windows = [{"px_a": 1.0}] * 3

    out = {v.name: v for v in screen_survival([_active("px_a"), _active("px_ghost")], windows)}

    assert out["px_ghost"].status is FeatureStatus.RETIRED
    assert out["px_ghost"].survived_windows == 0


# ---------------------------------------------------------------- 오케스트레이션


def test_run_gate_preserves_input_order_and_reports_the_dead_list():
    strong, y = _binary_pair(1000, 0.60)  # IC 0.20
    dupe = strong.copy()
    dupe[:40] = 1.0 - dupe[:40]  # ρ(strong,dupe)=0.923 > 0.9 · IC 0.120 — 약한 쪽이 탈락해야
    dead = np.full(1000, np.nan)
    noise, _ = _binary_pair(1000, 0.5)  # IC 0.0
    names = ["px_strong", "px_dupe", "px_dead", "px_noise"]
    x = np.column_stack([strong, dupe, dead, noise])

    report = run_gate(x, y, names, label_overlap_bars=1)

    assert [v.name for v in report.verdicts] == names  # 입력 순서 보존
    assert report.active_names == ("px_strong",)
    assert report.dead_names == ("px_dead",)
    by_name = {v.name: v for v in report.verdicts}
    assert by_name["px_dupe"].redundant_with == "px_strong"
    assert by_name["px_noise"].status is FeatureStatus.RETIRED
    assert report.n_samples == 1000
    assert "표본 1000" in report.summary()


def test_run_gate_report_serialises_to_plain_json_types():
    strong, y = _binary_pair(1000, 0.60)

    payload = run_gate(strong.reshape(-1, 1), y, ["px_strong"], label_overlap_bars=3).to_dict()

    import json

    assert json.loads(json.dumps(payload))["n_features"] == 1
    assert payload["label_overlap_bars"] == 3


# ------------------------------------- 기준선 대비 증분 (2026-08-04, ①)


def test_partial_spearman_matches_the_textbook_formula_for_one_baseline():
    """기준선 1개일 때 부분상관은 닫힌 식이 있다:

        ρ_xy·b = (ρ_xy − ρ_xb·ρ_yb) / sqrt((1−ρ_xb²)(1−ρ_yb²))

    잔차화 구현이 그 값과 일치해야 한다 — 안 맞으면 우리가 재는 게 부분상관이 아니다."""
    rng = np.random.default_rng(7)
    b = rng.normal(size=500)
    x = 0.8 * b + rng.normal(size=500)
    y = 0.5 * b + 0.3 * x + rng.normal(size=500)

    r_xy, r_xb, r_yb = spearman(x, y), spearman(x, b), spearman(y, b)
    expected = (r_xy - r_xb * r_yb) / math.sqrt((1 - r_xb**2) * (1 - r_yb**2))

    assert partial_spearman(x, y, b.reshape(-1, 1)) == pytest.approx(expected, abs=1e-9)


def test_a_feature_that_is_exactly_the_baseline_is_unmeasurable_not_zero():
    """**이 파일에서 ①의 핵심 테스트.** 기준선 자신을 피처로 넣으면 잔차가 수치오차만
    남는다 — 그 잡음 둘을 상관내면 **1.0**이 나와 "완벽한 증분"으로 보고된다(2026-08-04에
    이 테스트가 실제로 그 버그를 잡았다). "증분 0"과 "잴 수 없다"는 다른 사건이므로 None."""
    rng = np.random.default_rng(11)
    b = rng.normal(size=400)
    y = b + 0.5 * rng.normal(size=400)  # 기준선이 y를 강하게 설명

    assert abs(spearman(b, y)) > 0.7  # 주변상관은 크다
    assert partial_spearman(b, y, b.reshape(-1, 1)) is None


def test_a_near_duplicate_of_the_baseline_keeps_only_a_small_increment():
    """현실적인 경우 — 기준선과 **거의** 같은 피처. 주변상관은 여전히 크지만 증분은 작다.
    변동성 축의 `vl_atr_5` 부류가 이 형태일 것으로 예상되는 자리다."""
    rng = np.random.default_rng(23)
    b = rng.normal(size=1000)
    y = b + 0.5 * rng.normal(size=1000)
    x = b + 0.05 * rng.normal(size=1000)  # 기준선 + 아주 작은 잡음

    marginal = abs(spearman(x, y))
    partial = abs(partial_spearman(x, y, b.reshape(-1, 1)))

    assert marginal > 0.7  # 통제 전에는 강해 보인다
    assert partial < 0.15  # 통제 후 대부분 사라진다


def test_a_feature_independent_of_the_baseline_keeps_its_ic():
    """기준선과 무관한 정보를 담은 피처는 통제 후에도 살아남아야 한다 — 부분상관이
    무조건 값을 깎는 연산이 아니라는 확인."""
    rng = np.random.default_rng(13)
    b = rng.normal(size=800)
    x = rng.normal(size=800)  # 기준선과 독립
    y = b + x  # 둘 다 기여

    marginal = abs(spearman(x, y))
    partial = abs(partial_spearman(x, y, b.reshape(-1, 1)))

    assert partial > marginal  # 기준선 잡음을 걷어내면 오히려 또렷해진다


def test_partial_spearman_supports_multiple_baselines():
    """HAR 구조(단기·중기·장기 3개)를 통제하려면 다변량이어야 한다 — 기준선과 무관한
    정보를 담은 피처는 통제 후에도 남아야 한다."""
    rng = np.random.default_rng(17)
    b = rng.normal(size=(600, 3))
    independent = rng.normal(size=600)
    y = b @ np.array([1.0, 0.5, 0.25]) + independent

    assert abs(partial_spearman(independent, y, b)) > 0.5


def test_rank_partialling_only_removes_the_rank_linear_part_of_the_baselines():
    """**알려진 한계를 코드로 고정한다** (2026-08-04, 테스트가 발견).

    순위 부분상관은 기준선의 **순위-선형** 성분만 제거한다. 기준선이 하나면 단조 관계 전체가
    순위로 보존되므로 정확하지만, **여럿이면** 값 공간의 선형결합이 순위 공간에서 선형이
    아니라 잔여 구조가 남는다 — 아래가 그 실증이다(x는 기준선들의 선형결합인데 증분이 0.5로
    남는다).

    방향이 중요하다: 이 누수는 부분 IC를 **크게** 만들므로 피처에 유리한 쪽이다(비보수적).
    그래서 `run_feature_gate.py`의 기본 기준선은 단변량(`rv`)이고, HAR 3성분은 보조다.
    """
    rng = np.random.default_rng(17)
    b = rng.normal(size=(600, 3))
    y = b @ np.array([1.0, 0.5, 0.25]) + 0.3 * rng.normal(size=600)
    x = b @ np.array([0.9, 0.4, 0.2]) + 0.02 * rng.normal(size=600)  # 새 정보 없음

    leaked = abs(partial_spearman(x, y, b))

    assert leaked > 0.3, "누수가 사라졌다면 구현이 바뀐 것 — 이 주석과 기본 기준선을 재검토할 것"
    # 같은 관계를 단변량으로 통제하면(합성 기준선 1개) 훨씬 깨끗하게 걷힌다.
    single = b @ np.array([0.9, 0.4, 0.2])
    assert abs(partial_spearman(x, y, single.reshape(-1, 1))) < leaked


def test_partial_spearman_is_none_when_the_baseline_explains_everything():
    """ "증분이 0"과 "잴 수 없다"는 다른 사건이다 — 잔차가 상수면 None."""
    b = np.arange(50, dtype=float)
    y = b * 3.0  # 순위가 기준선과 완전히 같다 → 잔차가 수치오차만

    assert partial_spearman(b, y, b.reshape(-1, 1)) is None


def test_partial_spearman_rejects_length_mismatch():
    with pytest.raises(ValueError, match="길이 불일치"):
        partial_spearman(np.zeros(10), np.zeros(10), np.zeros((9, 1)))


def test_screen_uses_the_incremental_ic_for_pass_fail_and_keeps_the_marginal():
    """기준선을 주면 **판정은 증분으로** 하되 주변상관도 보존해야 한다 — 둘을 나란히 봐야
    "IC 0.67 중 기준선을 빼면 얼마 남는가"를 읽을 수 있다."""
    rng = np.random.default_rng(19)
    b = rng.normal(size=1000)
    y = b + 0.3 * rng.normal(size=1000)
    x = np.column_stack([b, rng.normal(size=1000)])  # 0번=기준선 복사본, 1번=무관

    verdicts = screen_standalone(
        x, y, ["px_copy", "px_noise"], label_overlap_bars=1, baselines=b.reshape(-1, 1)
    )
    by_name = {v.name: v for v in verdicts}

    assert abs(by_name["px_copy"].marginal_ic) > 0.7  # 통제 전에는 강해 보인다
    assert by_name["px_copy"].status is FeatureStatus.QUARANTINED  # 증분을 잴 수 없다
    assert "기준선이 이미" in by_name["px_copy"].reason
    assert by_name["px_noise"].status is FeatureStatus.RETIRED


def test_baseline_warmup_rows_are_excluded_without_inflating_nan_ratio():
    """기준선이 NaN인 워밍업 구간은 판정에서 빠지되, 그건 **피처의** 결측이 아니다 —
    `nan_ratio`는 피처 자신의 성질이고 `n_used`가 실제 판정 행 수다."""
    feature, y = _binary_pair(1000, 0.60)
    base = np.full(1000, np.nan)
    base[200:] = np.arange(800, dtype=float)

    (verdict,) = screen_standalone(
        feature.reshape(-1, 1), y, ["px_a"], label_overlap_bars=1, baselines=base.reshape(-1, 1)
    )

    assert verdict.nan_ratio == 0.0  # 피처 자체는 결측이 없다
    assert verdict.n_used == 800  # 기준선 워밍업 200행은 판정에서 빠졌다
