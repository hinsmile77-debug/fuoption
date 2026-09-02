"""매트릭스 정합 진단 — 0순위 도구 (2026-09-02 신설, 같은 날 O-4로 갱신).

`models/label_geometry.py` 계열의 자기판정 도구다. 여기서 지키는 것은 넷:
  ① 만들 수 있는 구조 목록을 **손으로 적지 않고 코드에서 유도**한다(적으면 갈라진다),
  ② 라이브 매트릭스에 **구멍 난 셀이 없다**(O-4 이후의 새 불변식),
  ③ 그런데도 **탐지 능력은 계속 검증한다** — 합성 구멍을 주입해서. 정합할 때만 돌리는
     검사는 자기가 죽은 것을 모른다(negative control, `test_zero_is_measured` 계열),
  ④ 의도된 빈 셀(관망)과 구멍 난 셀을 혼동하지 않고, 사유가 거짓말하지 않는다.
"""

from __future__ import annotations

import pytest

from messiah.core.messages import OptionsView
from messiah.simulator.inprocess_bus import InProcessBus
from messiah.strategy.options import matrix
from messiah.strategy.options.evaluator import buildable_structures
from messiah.strategy.options.matrix import Direction, IVState
from messiah.strategy.options.matrix_coverage import (
    check_matrix_coverage,
    unbuildable_reason,
)
from messiah.strategy.options.service import OptionsAIService
from messiah.strategy.options.surface import fit_smile
from messiah.strategy.options.vol_metrics import IVHistory
from tests.strategy.options.test_options_service import _futures_view  # 같은 픽스처 재사용

# 합성 매트릭스 — **탐지 기계를 검증하기 위한 것**이지 라이브 값이 아니다.
_HOLLOW_CELLS = {
    (Direction.NEUTRAL, IVState.LOW): ("MADE_UP_STRUCTURE",),  # 못 만드는 구조만 → 구멍
    (Direction.UP, IVState.LOW): ("LONG_CALL", "MADE_UP_STRUCTURE"),  # 일부 결손
    (Direction.UP, IVState.MID): (),  # 의도된 관망
    (Direction.DOWN, IVState.MID): ("LONG_PUT",),  # 정합
}
_BUILDABLE = frozenset({"LONG_CALL", "LONG_PUT"})


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
    assert "LONG_CALL" in buildable and "IRON_CONDOR" in buildable


# --------------------------------------------------------------- ② 라이브 불변식


def test_the_live_matrix_has_no_hollow_cells():
    """**O-4 이후의 불변식.** 이 줄이 깨지는 날은 누군가 평가기가 못 만드는 구조를 셀에
    넣은 날이다 — 2026-09-02 이전엔 (중립·저IV)가 정확히 그 상태였고, 관측의 75%가 거기
    떨어져 후보 0으로 조용히 지나갔다."""
    coverage = check_matrix_coverage()

    assert coverage.hollow_cells == ()
    assert coverage.partial_cells == ()
    assert coverage.is_healthy
    assert coverage.unbuildable == frozenset()


def test_the_watch_cells_are_empty_on_purpose():
    """(중립·저IV)는 O-4로, (중립·중IV)는 §4 「논지 없음」으로 — **둘 다 결함이 아니다.**"""
    cells = {cell.label: cell for cell in check_matrix_coverage().cells}

    for label in ("NEUTRAL·LOW", "NEUTRAL·MID"):
        assert cells[label].is_intentionally_empty
        assert not cells[label].is_hollow


# --------------------------------------------------------------- ③ 탐지 능력(negative control)


def test_a_synthetic_hollow_cell_is_still_detected():
    """라이브가 정합해진 뒤에도 **구멍을 잡아내는 능력**은 살아 있어야 한다."""
    coverage = check_matrix_coverage(cells_override=_HOLLOW_CELLS, buildable_override=_BUILDABLE)

    assert [cell.label for cell in coverage.hollow_cells] == ["NEUTRAL·LOW"]
    assert [cell.label for cell in coverage.partial_cells] == ["UP·LOW"]
    assert coverage.unbuildable == frozenset({"MADE_UP_STRUCTURE"})
    assert not coverage.is_healthy
    assert "NEUTRAL·LOW" in coverage.verdict and "비어 있다" in coverage.verdict


def test_hit_share_needs_samples_and_does_not_fake_zero():
    """표본이 없으면 **모른다**고 해야 한다 — 0%로 말하면 "구멍에 안 걸린다"로 읽힌다."""
    assert check_matrix_coverage(cells_override=_HOLLOW_CELLS).hollow_hit_share is None

    coverage = check_matrix_coverage(
        samples=[(0.0, 0.0), (0.0, 0.0), (0.0, 100.0)],  # 중립·저IV 두 번, 중립·고IV 한 번
        cells_override=_HOLLOW_CELLS,
        buildable_override=_BUILDABLE,
    )

    assert coverage.hollow_hit_share == pytest.approx(2 / 3)
    assert coverage.hits["NEUTRAL·LOW"] == 2


def test_unjudged_iv_rank_is_counted_separately():
    """IV 이력 부족은 셀 배정 실패가 아니라 **판정 이전**이다 — 구멍 비율에 섞지 않는다."""
    coverage = check_matrix_coverage(
        samples=[(0.0, None), (0.0, 0.0)],
        cells_override=_HOLLOW_CELLS,
        buildable_override=_BUILDABLE,
    )

    assert coverage.hits["IV Rank 미판정"] == 1
    assert coverage.hollow_hit_share == pytest.approx(0.5)


# --------------------------------------------------------------- ④ 사유가 거짓말하지 않는다


def test_unbuildable_reason_only_fires_when_nothing_is_buildable():
    buildable = frozenset({"LONG_CALL"})

    assert unbuildable_reason(["LONG_CALL"], buildable) is None
    assert unbuildable_reason(["LONG_CALL", "CALENDAR"], buildable) is None  # 일부는 만들었다
    reason = unbuildable_reason(["CALENDAR"], buildable)
    assert reason is not None and "불일치" in reason


@pytest.mark.asyncio
async def test_the_low_iv_cell_now_says_watch_not_a_false_rejection():
    """**㉢ 관망 확정의 관측 가능한 결과** (2026-09-02 O-4).

    이전 이 사이클은 「생성된 후보가 전부 안전규칙에서 기각됨」이라고 나갔다 — 안전규칙까지
    가지도 못했는데. 지금은 셀이 비어 있어 사유가 「관망」으로 정직해진다.
    """
    bus = InProcessBus()
    published: list[OptionsView] = []

    async def _capture(topic, message):  # noqa: ANN001 — 테스트 스텁
        published.append(message)

    bus.publish = _capture  # type: ignore[method-assign]
    history = IVHistory()
    # 현재 IV(0.20)의 백분위가 iv_rank_low(30) 아래가 되도록 높은 값들로 채운다 → (중립·저IV)
    for value in (0.50, 0.60, 0.70, 0.80):
        history.add(value)
    service = OptionsAIService("TEST", "KOSPI200", lambda: _smile(0.20), bus, iv_history=history)

    await service.handle_futures_view(_futures_view(0.0))

    assert published
    reason = published[-1].no_option_reason
    assert reason is not None
    assert "관망" in reason
    assert "안전규칙" not in reason
