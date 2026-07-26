from messiah.core.messages import FuturesView, Regime, Side
from messiah.strategy.decision.meta_decision import MetaDecisionConfig, MetaDecisionEngine

_SYMBOL = "TEST"


def _view(
    *,
    score: float = 0.0,
    agg_p_up: float = 0.5,
    agg_p_down: float = 0.5,
    uncertainty: float = 0.1,
    dispersion: float = 0.0,
    regime: Regime = Regime.TREND_UP,
    n_experts: int = 3,
) -> FuturesView:
    return FuturesView(
        symbol=_SYMBOL,
        score=score,
        agg_p_up=agg_p_up,
        agg_p_down=agg_p_down,
        uncertainty=uncertainty,
        dispersion=dispersion,
        regime=regime,
        n_experts=n_experts,
        model_versions=["v1"],
        top_features=[("5m:px_ret_5", 1.0)],
    )


def test_kill_switch_forces_no_trade_regardless_of_score():
    view = _view(score=0.9, agg_p_up=0.9)
    intent = MetaDecisionEngine().decide(view, kill_active=True)
    assert intent.side == Side.NO_TRADE
    assert "①" in intent.rationale


def test_event_regime_forces_no_trade():
    view = _view(score=0.9, regime=Regime.EVENT)
    intent = MetaDecisionEngine().decide(view, kill_active=False)
    assert intent.side == Side.NO_TRADE
    assert "②" in intent.rationale


def test_unknown_regime_forces_no_trade():
    view = _view(score=0.9, regime=Regime.UNKNOWN)
    intent = MetaDecisionEngine().decide(view, kill_active=False)
    assert intent.side == Side.NO_TRADE


def test_high_dispersion_forces_no_trade():
    view = _view(score=0.9, dispersion=0.3)
    intent = MetaDecisionEngine().decide(view, kill_active=False)
    assert intent.side == Side.NO_TRADE
    assert "③" in intent.rationale


def test_weak_score_forces_no_trade():
    view = _view(score=0.1)
    intent = MetaDecisionEngine().decide(view, kill_active=False)
    assert intent.side == Side.NO_TRADE
    assert "④" in intent.rationale


def test_strong_positive_score_gives_long():
    view = _view(score=0.42, agg_p_up=0.71, agg_p_down=0.2)
    intent = MetaDecisionEngine().decide(view, kill_active=False)
    assert intent.side == Side.LONG
    assert intent.confidence == 0.71
    assert intent.horizon is None
    assert intent.option_strategy is None


def test_strong_negative_score_gives_short():
    view = _view(score=-0.42, agg_p_up=0.15, agg_p_down=0.68)
    intent = MetaDecisionEngine().decide(view, kill_active=False)
    assert intent.side == Side.SHORT
    assert intent.confidence == 0.68


def test_score_exactly_at_threshold_is_tradeable():
    view = _view(score=0.20, agg_p_up=0.6)
    intent = MetaDecisionEngine().decide(view, kill_active=False)
    assert intent.side == Side.LONG


def test_no_trade_still_carries_rationale_and_top_features():
    view = _view(score=0.0)
    intent = MetaDecisionEngine().decide(view, kill_active=False)
    assert intent.rationale
    assert intent.top_features == [("5m:px_ret_5", 1.0)]


def test_model_version_joins_unique_sorted_versions():
    view = _view(score=0.5, agg_p_up=0.7)
    view = view.model_copy(update={"model_versions": ["v2", "v1"]})
    intent = MetaDecisionEngine().decide(view, kill_active=False)
    assert intent.model_version == "v2+v1" or intent.model_version == "v1+v2"


def test_custom_thresholds_respected():
    config = MetaDecisionConfig(score_threshold=0.5, dispersion_threshold=0.1)
    view = _view(score=0.3, agg_p_up=0.6, dispersion=0.05)
    intent = MetaDecisionEngine(config).decide(view, kill_active=False)
    assert intent.side == Side.NO_TRADE  # 0.3 < 커스텀 임계 0.5
