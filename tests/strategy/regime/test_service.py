import math
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from messiah.core.messages import BarClosed, Horizon, Regime
from messiah.core.timeutil import KST
from messiah.strategy.regime.rules import RuleContext
from messiah.strategy.regime.service import RegimeAI

_SYMBOL = "TEST"
_START = datetime(2026, 7, 27, 9, 0, tzinfo=KST)


def _bars(n: int) -> list[BarClosed]:
    out = []
    price = 100.0
    for i in range(n):
        price += math.sin(i / 4) * 2 + ((i * 53) % 7 - 3) * 0.2
        price = max(price, 10.0)
        out.append(
            BarClosed(
                symbol=_SYMBOL,
                horizon=Horizon.M30,
                bar_open_kst=_START + timedelta(minutes=30 * i),
                o_ticks=round(price),
                h_ticks=round(price) + 2,
                l_ticks=round(price) - 2,
                c_ticks=round(price),
                volume=10 + i,
            )
        )
    return out


def test_fit_and_classify_produces_valid_regime_state():
    bars = _bars(100)
    regime_ai = RegimeAI.fit(bars, n_states_candidates=(2, 3, 4))

    state = regime_ai.classify(bars)

    assert isinstance(state.regime, Regime)
    assert 0.0 <= state.confidence <= 1.0
    assert state.state_duration_bars == 1
    assert state.symbol == _SYMBOL
    assert state.valid_until is not None


def test_fit_raises_when_insufficient_data():
    with pytest.raises(ValueError):
        RegimeAI.fit(_bars(5))


def test_classify_with_short_history_returns_unknown():
    regime_ai = RegimeAI.fit(_bars(100), n_states_candidates=(2, 3))
    short_bars = _bars(5)

    state = regime_ai.classify(short_bars)

    assert state.regime == Regime.UNKNOWN
    assert state.confidence == 0.0


def test_classify_empty_bars_returns_unknown():
    regime_ai = RegimeAI.fit(_bars(100), n_states_candidates=(2, 3))
    state = regime_ai.classify([])
    assert state.regime == Regime.UNKNOWN
    assert state.symbol == "UNKNOWN"


def test_rule_override_forces_confidence_one_and_reason():
    regime_ai = RegimeAI.fit(_bars(100), n_states_candidates=(2, 3))
    bars = _bars(100)

    state = regime_ai.classify(bars, rule_context=RuleContext(vol_ratio=1000.0))

    assert state.regime == Regime.HIGH_VOL
    assert state.confidence == 1.0
    assert state.rule_override == "rule_volatility_extreme"


def test_state_duration_increments_on_same_regime_and_resets_on_change():
    regime_ai = RegimeAI.fit(_bars(100), n_states_candidates=(2, 3))
    bars = _bars(100)

    first = regime_ai.classify(bars, rule_context=RuleContext(econ_grade=3, econ_prox_days=0))
    second = regime_ai.classify(bars, rule_context=RuleContext(vol_ratio=1000.0))
    third = regime_ai.classify(bars, rule_context=RuleContext(vol_ratio=1000.0))

    assert first.regime == Regime.EVENT
    assert first.state_duration_bars == 1

    assert second.regime == Regime.HIGH_VOL
    assert second.state_duration_bars == 1  # 국면이 바뀌어 리셋

    assert third.regime == Regime.HIGH_VOL
    assert third.state_duration_bars == 2  # 같은 국면 유지 → 누적


def test_transition_prob_values_are_valid_probabilities():
    regime_ai = RegimeAI.fit(_bars(100), n_states_candidates=(2, 3))
    state = regime_ai.classify(_bars(100))

    assert all(0.0 <= p <= 1.0 for p in state.transition_prob.values())
    assert sum(state.transition_prob.values()) <= 1.0 + 1e-6


def test_save_load_round_trip_preserves_classification(tmp_path: Path):
    bars = _bars(100)
    regime_ai = RegimeAI.fit(bars, n_states_candidates=(2, 3))
    before = regime_ai.classify(bars)

    path = tmp_path / "regime_ai_v1"
    regime_ai.save(path)
    reloaded = RegimeAI.load(path)
    after = reloaded.classify(bars)

    assert after.regime == before.regime
    assert after.confidence == pytest.approx(before.confidence)
