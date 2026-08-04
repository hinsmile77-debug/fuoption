import pytest
from messiah.models.threshold_report import Distribution, ThresholdReport


def _report(threshold, selection, inference):
    return ThresholdReport.build(
        threshold=threshold,
        selection_probabilities=selection,
        inference_probabilities=inference,
    )


# ---------------------------------------------------------------- Distribution


def test_distribution_summarises_quantiles():
    dist = Distribution.of([0.1 * i for i in range(11)])  # 0.0 ~ 1.0

    assert dist.n == 11
    assert dist.minimum == pytest.approx(0.0)
    assert dist.p50 == pytest.approx(0.5)
    assert dist.maximum == pytest.approx(1.0)
    assert dist.p90 >= dist.p50
    assert dist.p99 >= dist.p90


def test_distribution_rejects_empty_sample():
    """빈 분포로 '과적합 아님'을 주장하지 않는다."""
    with pytest.raises(ValueError):
        Distribution.of([])


# ---------------------------------------------------------------- 판정


def test_reports_overfit_when_selection_reaches_but_inference_never_does():
    """2026-08-04 실측 재현 — 선택 시엔 넘었는데 추론에선 최대치가 임계에 못 미친다."""
    report = _report(0.60, selection=[0.2, 0.5, 0.7, 0.9], inference=[0.31, 0.42, 0.5422])

    assert report.is_unreachable is True
    assert report.inference_reach_rate == 0.0
    assert report.selection_reach_rate == pytest.approx(0.5)
    assert report.headroom == pytest.approx(0.5422 - 0.60)
    assert "과적합" in report.verdict


def test_reports_reachable_when_inference_clears_the_threshold():
    report = _report(0.50, selection=[0.4, 0.6], inference=[0.45, 0.55, 0.80])

    assert report.is_unreachable is False
    assert report.inference_reach_rate == pytest.approx(2 / 3)
    assert report.headroom > 0
    assert "도달 가능" in report.verdict


def test_reports_selector_fault_when_neither_side_reaches():
    """선택 시에도 아무도 못 넘었으면 과적합이 아니라 선택 로직 자체를 의심해야 한다."""
    report = _report(0.95, selection=[0.1, 0.2], inference=[0.3, 0.4])

    assert report.is_unreachable is True
    assert report.selection_reach_rate == 0.0
    assert "선택 로직" in report.verdict


def test_headroom_is_negative_exactly_when_unreachable():
    unreachable = _report(0.7, selection=[0.8], inference=[0.1, 0.69])
    reachable = _report(0.7, selection=[0.8], inference=[0.1, 0.71])

    assert unreachable.headroom < 0 and unreachable.is_unreachable
    assert reachable.headroom > 0 and not reachable.is_unreachable


def test_format_lines_puts_both_distributions_side_by_side():
    lines = _report(0.6, selection=[0.7, 0.9], inference=[0.2, 0.3]).format_lines()

    assert any("선택 시" in ln for ln in lines)
    assert any("추론 시" in ln for ln in lines)
    assert any("헤드룸" in ln for ln in lines)
    assert any("판정" in ln for ln in lines)


def test_build_rejects_empty_inputs():
    with pytest.raises(ValueError):
        _report(0.5, selection=[], inference=[0.1])
    with pytest.raises(ValueError):
        _report(0.5, selection=[0.1], inference=[])
