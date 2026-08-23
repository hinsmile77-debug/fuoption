"""기대우위가 FLAT 확률을 불리한 쪽으로 세면 안 된다 — 2026-08-23.

## 무슨 일이 있었나

2026-08-05부터 2026-08-21까지 **17거래일 연속 주문 0건**이었다. 리스크 엔진까지 도달한
사이클은 그 사이 딱 2건(08-18 · 08-21)이었고, **둘 다 소수점까지 같은 사유로 거절됐다**:

    20260818 RiskReject Net ER -1.62틱 <= 0 (Ver 1.1 §4-2)
    20260821 RiskReject Net ER -1.62틱 <= 0 (Ver 1.1 §4-2)

날짜가 다르고 신호가 다르고 ATR이 다른데 사유가 동일한 것이 지문이었다. `edge`가 0이면
`net_er = edge × ATR − 비용`이 `−비용`으로 붕괴하고, 비용은 거의 상수다. **신호가 0에
곱해져서 비용만 살아남은 것이다.**

원인은 `strategy/pipeline.py`의 주석 없는 한 줄이었다:

    edge = max(0.0, min(1.0, 2.0 * intent.confidence - 1.0))

`2p − 1`은 이항 승부의 배당 공식인데 `confidence`는 3-클래스 모델의 방향 확률 하나다.
FLAT 몫이 통째로 불리한 쪽으로 들어갔고, 삼중장벽 30m의 FLAT은 실측 76.3%다
(`models/labeling.py`). `p > 0.5`가 구조적으로 어려우니 클램프가 매번 정확히 0을 냈다.
"""

from __future__ import annotations

from messiah.core.messages import Side
from messiah.strategy.pipeline import _directional_edge


class _View:
    """`_directional_edge`가 보는 두 필드만 갖춘 최소 대역."""

    def __init__(self, p_up: float, p_down: float) -> None:
        self.agg_p_up = p_up
        self.agg_p_down = p_down


def _legacy_edge(confidence: float) -> float:
    """종전 산식 — 회귀 대조용으로 여기 남긴다."""
    return max(0.0, min(1.0, 2.0 * confidence - 1.0))


def test_the_real_2026_08_21_cycle_is_no_longer_erased():
    """**이 테스트가 요점이다.** 2026-08-21 15:30 실제 사이클의 값 그대로다.

    p_down 0.4873 · p_up 0.1473 · p_flat 0.3654 · ATR 48.93틱 · 왕복 비용 1.62틱.
    """
    view = _View(p_up=0.1472727272727273, p_down=0.4872727272727273)
    atr_ticks, cost_ticks = 48.92857142857143, 1.6231951290229052

    legacy = _legacy_edge(0.4872727272727273)
    assert legacy == 0.0, "종전 산식은 이 신호를 0으로 지웠다"
    assert (
        abs((legacy * atr_ticks - cost_ticks) - (-1.6231951290229052)) < 1e-9
    ), "그 결과가 로그에 남은 -1.62틱이다"

    edge = _directional_edge(view, Side.SHORT)
    assert abs(edge - 0.34) < 1e-9  # 0.4873 - 0.1473
    assert edge * atr_ticks - cost_ticks > 15.0, "같은 신호가 +15틱으로 살아난다"


def test_flat_mass_is_not_charged_as_adverse():
    """**FLAT은 불리한 결과가 아니다.** 시간 배리어는 진입가 근처 청산이라 0에 가깝고,
    `labeling._resolve_barrier()`가 시간 만료 시 `last.c_ticks`로 청산한다.

    아래는 30m 실측 FLAT 비율(76.3%)에 가까운 모양이다 — 방향 확률이 유리한 쪽으로
    두 배 넘게 기울었는데도 종전 산식은 0을 냈다.
    """
    view = _View(p_up=0.20, p_down=0.05)  # FLAT 0.75
    assert _legacy_edge(0.20) == 0.0, "종전: 유리한 쪽이 4배인데도 0"
    assert abs(_directional_edge(view, Side.LONG) - 0.15) < 1e-12


def test_an_adverse_signal_keeps_its_magnitude():
    """**음수를 0으로 누르지 않는다.** 종전 클램프는 「약간 불리」와 「크게 불리」를 같은
    `−비용`으로 접었고, 그래서 서로 다른 두 날의 거절 사유가 소수점까지 같았다.
    얼마나 나빴는지는 남아야 한다(L18)."""
    mild = _directional_edge(_View(p_up=0.30, p_down=0.35), Side.LONG)
    severe = _directional_edge(_View(p_up=0.05, p_down=0.85), Side.LONG)

    assert abs(mild - (-0.05)) < 1e-12
    assert abs(severe - (-0.80)) < 1e-12
    assert severe < mild, "두 사이클이 같은 숫자로 접히면 안 된다"


def test_the_side_decides_which_probability_is_favorable():
    view = _View(p_up=0.15, p_down=0.49)

    assert abs(_directional_edge(view, Side.SHORT) - 0.34) < 1e-12
    assert abs(_directional_edge(view, Side.LONG) - (-0.34)) < 1e-12


def test_edge_stays_within_minus_one_and_one():
    """확률에서 유도되므로 벗어날 수 없지만, 상류가 정규화를 깨뜨려도 하류 산술이
    폭주하지 않게 못 박는다."""
    assert _directional_edge(_View(p_up=1.0, p_down=0.0), Side.LONG) == 1.0
    assert _directional_edge(_View(p_up=0.0, p_down=1.0), Side.LONG) == -1.0
    assert _directional_edge(_View(p_up=5.0, p_down=0.0), Side.LONG) == 1.0
