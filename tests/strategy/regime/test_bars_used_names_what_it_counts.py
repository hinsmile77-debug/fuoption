"""F-25 — `bars_used`가 판정에 실제 들어간 봉 수를 말한다 (2026-08-24 이상점 1-13).

2026-08-24 국면 로그는 *"200봉을 썼다"* 고 적었다. 실제로 필터에 들어간 것은 최근 **82봉**
이었고, 그 봉에서 만들어진 관측은 **61개**였다(워밍업 21봉 소진). 이름이 재는 것과
어긋나면 그 로그를 읽는 모든 판단이 함께 어긋난다 — F-12가 `publish_offset_axis`로
고친 것과 같은 처방이다.
"""

from __future__ import annotations

import pytest

from messiah.core.messages import Regime
from messiah.strategy.regime.service import RegimeAI
from tests.strategy.regime.test_runtime import _bars


@pytest.fixture(scope="module")
def regime_ai() -> RegimeAI:
    return RegimeAI.fit(_bars(120), n_states_candidates=(2, 3))


def test_filter_length_is_not_the_history_length(regime_ai: RegimeAI):
    bars = _bars(200)
    state = regime_ai.classify(bars)
    assert len(bars) == 200
    # 꼬리 = min_bars(22) + _FILTER_OBSERVATIONS(60) = 82
    assert state.bars_in_filter == 82, "이력 버퍼가 아무리 길어도 필터는 꼬리만 본다"
    assert state.observations_used == 61, "워밍업 21봉이 관측을 만들지 못한다"


def test_short_history_uses_everything_it_has(regime_ai: RegimeAI):
    bars = _bars(40)
    state = regime_ai.classify(bars)
    assert state.bars_in_filter == 40
    assert state.observations_used is not None and state.observations_used < 40


def test_below_minimum_reports_unmeasured_not_zero(regime_ai: RegimeAI):
    """하한 미달 경로는 **필터를 돌린 적이 없다.**

    0으로 적으면 「봉을 0개 썼다」가 되는데 사실은 「쓰지 않았다」다(L18).
    """
    state = regime_ai.classify(_bars(5))
    assert state.regime is Regime.UNKNOWN
    assert state.bars_in_filter is None
    assert state.observations_used is None
