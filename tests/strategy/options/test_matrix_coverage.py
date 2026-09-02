"""매트릭스 정합 진단 — 0순위 (2026-09-02 신설).

`models/label_geometry.py` 계열의 자기판정 도구다. 여기서 지키는 것은 셋:
  ① 만들 수 있는 구조 목록을 **손으로 적지 않고 코드에서 유도**한다(적으면 갈라진다),
  ② 의도된 빈 셀(관망)과 구멍 난 셀(배정했는데 못 만듦)을 **혼동하지 않는다**,
  ③ 서비스가 그 구멍을 **안전규칙 탓으로 돌리지 않는다**.
"""

from __future__ import annotations

import pytest

from messiah.core.messages import OptionsView
from messiah.simulator.inprocess_bus import InProcessBus
from messiah.strategy.options import matrix
from messiah.strategy.options.evaluator import buildable_structures
from messiah.strategy.options.matrix_coverage import (
    check_matrix_coverage,
    unbuildable_reason,
)
from messiah.strategy.options.service import OptionsAIService
from messiah.strategy.options.surface import fit_smile
from messiah.strategy.options.vol_metrics import IVHistory
from tests.strategy.options.test_options_service import _futures_view  # 같은 픽스처 재사용


def _smile(iv: float = 0.20):
    fit = fit_smile(350.0, dte=20, strike_iv_points=[(k, iv) for k in (300.0, 350.0, 400.0)])
    assert fit is not None
    return fit


# --------------------------------------------------------------- ① 목록을 코드에서 유도


def test_buildable_structures_comes_from_the_code_not_a_list():
    """`_leg_templates()`에 직접 물어본 결과여야 한다 — 손으로 적은 목록은 갈라진다."""
    buildable = buildable_structures()

    assert buildable <= set(matrix.ALL_STRUCTURES)
    for structure in buildable:
        assert matrix.spec_for(structure) is not None
    # 2026-09-02 시점의 사실: CALENDAR 하나만 못 만든다. 이 줄이 깨지는 날은 둘 중 하나다 —
    # CALENDAR를 구현했거나, 새 구조를 매트릭스에 넣고 평가기를 안 고쳤거나. 어느 쪽이든
    # 사람이 이 문장을 다시 읽어야 한다.
    assert set(matrix.ALL_STRUCTURES) - buildable == {matrix.CALENDAR}


# --------------------------------------------------------------- ② 빈 셀과 구멍 난 셀


def test_an_intentionally_empty_cell_is_not_a_defect():
    """(중립·중IV)는 §4 「논지 없음」으로 **의도된 관망**이다 — 결함으로 세면 안 된다."""
    coverage = check_matrix_coverage()
    cells = {cell.label: cell for cell in coverage.cells}

    watch = cells["NEUTRAL·MID"]
    assert watch.is_intentionally_empty
    assert not watch.is_hollow
    assert watch not in coverage.hollow_cells


def test_the_hollow_cell_is_found_and_named():
    coverage = check_matrix_coverage()

    hollow = coverage.hollow_cells
    assert [cell.label for cell in hollow] == ["NEUTRAL·LOW"]
    assert hollow[0].structures == (matrix.CALENDAR,)
    assert not coverage.is_healthy
    assert "NEUTRAL·LOW" in coverage.verdict and "비어 있다" in coverage.verdict


def test_hit_share_needs_samples_and_does_not_fake_zero():
    """표본이 없으면 **모른다**고 해야 한다 — 0%로 말하면 "구멍에 안 걸린다"로 읽힌다."""
    assert check_matrix_coverage().hollow_hit_share is None

    # 중립(score 0) · 저IV(rank 0)로 두 번, 중립·고IV로 한 번
    samples = [(0.0, 0.0), (0.0, 0.0), (0.0, 100.0)]
    coverage = check_matrix_coverage(samples=samples)

    assert coverage.hollow_hit_share == pytest.approx(2 / 3)
    assert coverage.hits["NEUTRAL·LOW"] == 2


def test_unjudged_iv_rank_is_counted_separately():
    """IV 이력 부족은 셀 배정 실패가 아니라 **판정 이전**이다 — 구멍 비율에 섞지 않는다."""
    coverage = check_matrix_coverage(samples=[(0.0, None), (0.0, 0.0)])

    assert coverage.hits["IV Rank 미판정"] == 1
    assert coverage.hollow_hit_share == pytest.approx(0.5)


# --------------------------------------------------------------- ③ 사유가 거짓말하지 않는다


def test_unbuildable_reason_only_fires_when_nothing_is_buildable():
    buildable = frozenset({"LONG_CALL"})

    assert unbuildable_reason(["LONG_CALL"], buildable) is None
    assert unbuildable_reason(["LONG_CALL", "CALENDAR"], buildable) is None  # 일부는 만들었다
    reason = unbuildable_reason(["CALENDAR"], buildable)
    assert reason is not None and "불일치" in reason


@pytest.mark.asyncio
async def test_service_blames_the_matrix_not_the_safety_rules():
    """**2026-09-02 이전엔 이 사이클이 「안전규칙에서 기각됨」이라고 나갔다** — 안전규칙까지
    가지도 못했는데. 사유가 틀리면 사람이 엉뚱한 데를 고친다."""
    bus = InProcessBus()
    published: list[OptionsView] = []

    async def _capture(topic, message):  # noqa: ANN001 — 테스트 스텁
        published.append(message)

    bus.publish = _capture  # type: ignore[method-assign]
    history = IVHistory()
    # 현재 IV(0.20)의 백분위가 iv_rank_low(30) 아래가 되도록 높은 값들로 이력을 채운다 —
    # 그래야 (중립·저IV) 셀에 떨어져 CALENDAR가 배정된다.
    for value in (0.50, 0.60, 0.70, 0.80):
        history.add(value)
    service = OptionsAIService("TEST", "KOSPI200", lambda: _smile(0.20), bus, iv_history=history)

    await service.handle_futures_view(_futures_view(0.0))  # 중립 → (중립·저IV) 셀

    assert published
    reason = published[-1].no_option_reason
    assert reason is not None
    assert "CALENDAR" in reason and "불일치" in reason
    assert "안전규칙" not in reason
