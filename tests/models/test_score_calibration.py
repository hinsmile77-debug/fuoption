import pytest

from messiah.models.score_calibration import ScoreCalibration


def _calib(pairs, n_bins=5):
    return ScoreCalibration.build([s for s, _ in pairs], [c for _, c in pairs], n_bins=n_bins)


def test_detects_informative_score_when_high_confidence_wins_more():
    # |S|가 클수록 잘 맞는 이상적인 경우.
    low = [(0.01, i % 2 == 0) for i in range(100)]  # 50%
    high = [(0.50, i % 10 != 0) for i in range(100)]  # 90%

    calib = _calib(low + high, n_bins=2)

    assert calib.is_informative is True
    assert calib.edge_gap == pytest.approx(0.40, abs=0.02)
    assert "방향을 가른다" in calib.verdict


def test_detects_uninformative_score_when_confidence_does_not_help():
    """2026-08-04 실측 재현 — 상위 20% 50.6%, 하위 20% 51.1%. 임계값을 어디에 둬도
    동전던지기라 낮추면 비용만 더 낸다."""
    low = [(0.01, i % 2 == 0) for i in range(100)]  # 50%
    high = [(0.50, i % 2 == 0) for i in range(100)]  # 50%

    calib = _calib(low + high, n_bins=2)

    assert calib.is_informative is False
    assert "동전던지기" in calib.verdict


def test_inverted_edge_is_not_informative():
    """확신이 클수록 **틀리는** 경우도 임계값으로는 못 건진다(방향을 뒤집는 건 별개 결정)."""
    low = [(0.01, True) for _ in range(50)]
    high = [(0.50, False) for _ in range(50)]

    calib = _calib(low + high, n_bins=2)

    assert calib.edge_gap < 0
    assert calib.is_informative is False


def test_zero_scores_are_excluded_as_no_decision():
    """S=0은 판단이 없었다는 뜻 — 적중/실패로 세면 안 된다."""
    calib = _calib([(0.0, False)] * 10 + [(0.4, True)] * 10, n_bins=1)

    assert calib.n == 10
    assert calib.overall_hit_rate == 1.0


def test_bins_use_equal_counts_not_equal_width():
    """|S|가 0 근처에 몰려 있어 등폭이면 상위 구간 표본이 한 자릿수가 된다."""
    pairs = [(0.001 * i, True) for i in range(1, 100)] + [(5.0, True)]

    calib = _calib(pairs, n_bins=4)

    assert len({b.n for b in calib.bins}) <= 2  # 개수가 균등(나머지만 ±1)
    assert all(b.n >= 20 for b in calib.bins)


def test_empty_input_reports_unjudgeable_instead_of_claiming_no_edge():
    calib = _calib([])

    assert calib.n == 0
    assert calib.bins == ()
    assert "판정 불가" in calib.verdict


def test_rejects_length_mismatch():
    with pytest.raises(ValueError):
        ScoreCalibration.build([0.1, 0.2], [True])


def test_format_lines_include_bins_and_verdict():
    lines = _calib([(0.1, True), (0.2, False), (0.3, True), (0.4, False)], n_bins=2).format_lines()

    assert any("적중" in ln for ln in lines)
    assert any("판정" in ln for ln in lines)
