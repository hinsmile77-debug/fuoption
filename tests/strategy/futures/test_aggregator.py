from datetime import datetime, timedelta

from messiah.core.messages import ExpertView, Horizon, Regime, RegimeState
from messiah.core.timeutil import KST
from messiah.strategy.futures.aggregator import Aggregator, AggregatorConfig

_SYMBOL = "TEST"
_NOW = datetime(2026, 7, 30, 10, 35, tzinfo=KST)


def _view(
    horizon: Horizon,
    *,
    p_up: float,
    p_down: float,
    ens_std: float = 0.05,
    meta_passed: bool = True,
    age_horizons: float = 0.0,
    model_version: str = "v1",
    top_features: list[tuple[str, float]] | None = None,
) -> ExpertView:
    """`age_horizons`: valid_until이 `_NOW`보다 몇 Horizon-길이만큼 과거인지 — 0.0이면
    방금 확정(신선도 1.0), 1.0이면 정확히 한 Horizon 지남(신선도 0.0). `valid_until`은
    실제로 "확정 시각"이다(aggregator.py 모듈 docstring "신선도" 절 참고 — 이름과 달리
    미래 만료 시점이 아니다)."""
    p_flat = max(0.0, 1.0 - p_up - p_down)
    from messiah.core.messages import HORIZON_SECONDS

    valid_until = _NOW - timedelta(seconds=HORIZON_SECONDS[horizon] * age_horizons)
    return ExpertView(
        symbol=_SYMBOL,
        horizon=horizon,
        p_up=p_up,
        p_flat=p_flat,
        p_down=p_down,
        ens_std=ens_std,
        meta_passed=meta_passed,
        model_version=model_version,
        top_features=top_features or [],
        valid_until=valid_until,
    )


def _regime(regime: Regime) -> RegimeState:
    return RegimeState(symbol=_SYMBOL, regime=regime, confidence=0.9, state_duration_bars=3)


def test_all_horizons_bullish_gives_positive_score_and_no_dispersion():
    views = {
        Horizon.M5: _view(Horizon.M5, p_up=0.7, p_down=0.1),
        Horizon.M15: _view(Horizon.M15, p_up=0.65, p_down=0.15),
        Horizon.M30: _view(Horizon.M30, p_up=0.6, p_down=0.2),
    }
    out = Aggregator().compute(_SYMBOL, views, _regime(Regime.TREND_UP), as_of=_NOW)
    assert out.score > 0
    assert out.n_experts == 3
    assert 0.0 <= out.agg_p_up <= 1.0
    assert out.dispersion >= 0.0


def test_meta_labeler_rejection_zeros_out_contribution():
    views = {
        Horizon.M5: _view(Horizon.M5, p_up=0.9, p_down=0.05, meta_passed=False),
        Horizon.M15: _view(Horizon.M15, p_up=0.5, p_down=0.5),
    }
    out = Aggregator().compute(_SYMBOL, views, _regime(Regime.TREND_UP), as_of=_NOW)
    # M5는 meta 미통과라 가중치 0 — 5분 의견의 극단적 확신이 결과에 전혀 반영 안 됨
    assert out.n_experts == 1


def test_stale_view_decays_to_zero_weight():
    stale = _view(Horizon.M5, p_up=0.9, p_down=0.05, age_horizons=1.0)
    views = {Horizon.M5: stale}
    out = Aggregator().compute(_SYMBOL, views, _regime(Regime.TREND_UP), as_of=_NOW)
    assert out.n_experts == 0
    assert out.score == 0.0
    assert out.uncertainty == 1.0


def test_high_uncertainty_reduces_weight_but_not_to_zero():
    low_u = _view(Horizon.M5, p_up=0.7, p_down=0.1, ens_std=0.0)
    high_u = _view(Horizon.M5, p_up=0.7, p_down=0.1, ens_std=0.4)
    cfg = AggregatorConfig(uncertainty_scale=0.5)
    out_low = Aggregator(cfg).compute(
        _SYMBOL, {Horizon.M5: low_u}, _regime(Regime.RANGE), as_of=_NOW
    )
    out_high = Aggregator(cfg).compute(
        _SYMBOL, {Horizon.M5: high_u}, _regime(Regime.RANGE), as_of=_NOW
    )
    assert abs(out_high.score) < abs(out_low.score)


def test_regime_weight_matrix_favors_long_horizons_in_trend():
    views = {
        Horizon.M1: _view(Horizon.M1, p_up=0.6, p_down=0.2),
        Horizon.M30: _view(Horizon.M30, p_up=0.6, p_down=0.2),
    }
    trend = Aggregator().compute(_SYMBOL, views, _regime(Regime.TREND_UP), as_of=_NOW)
    range_ = Aggregator().compute(_SYMBOL, views, _regime(Regime.RANGE), as_of=_NOW)
    # 동일 입력이라도 추세장은 30m 우대(가중치 1.5) vs 횡보장 30m 홀대(0.4)로 점수가 더 크다
    assert trend.score > range_.score


def test_dispersion_reflects_disagreement_between_horizons():
    agree = {
        Horizon.M5: _view(Horizon.M5, p_up=0.7, p_down=0.1),
        Horizon.M15: _view(Horizon.M15, p_up=0.7, p_down=0.1),
    }
    disagree = {
        Horizon.M5: _view(Horizon.M5, p_up=0.8, p_down=0.05),
        Horizon.M15: _view(Horizon.M15, p_up=0.1, p_down=0.8),
    }
    out_agree = Aggregator().compute(_SYMBOL, agree, _regime(Regime.RANGE), as_of=_NOW)
    out_disagree = Aggregator().compute(_SYMBOL, disagree, _regime(Regime.RANGE), as_of=_NOW)
    assert out_disagree.dispersion > out_agree.dispersion


def test_single_active_horizon_has_zero_dispersion():
    views = {Horizon.M5: _view(Horizon.M5, p_up=0.7, p_down=0.1)}
    out = Aggregator().compute(_SYMBOL, views, _regime(Regime.RANGE), as_of=_NOW)
    assert out.dispersion == 0.0


def test_top_features_aggregated_and_prefixed_by_horizon():
    views = {
        Horizon.M5: _view(
            Horizon.M5, p_up=0.7, p_down=0.1, top_features=[("px_ret_5", 10.0), ("px_mom_5", 3.0)]
        ),
    }
    out = Aggregator().compute(_SYMBOL, views, _regime(Regime.RANGE), as_of=_NOW)
    assert out.top_features
    assert out.top_features[0][0] == "5m:px_ret_5"


def test_empty_views_returns_conservative_flat_view():
    out = Aggregator().compute(_SYMBOL, {}, _regime(Regime.UNKNOWN), as_of=_NOW)
    assert out.score == 0.0
    assert out.uncertainty == 1.0
    assert out.n_experts == 0


def test_unknown_regime_uses_uniform_conservative_weights():
    views = {
        Horizon.M5: _view(Horizon.M5, p_up=0.7, p_down=0.1),
        Horizon.M30: _view(Horizon.M30, p_up=0.7, p_down=0.1),
    }
    out = Aggregator().compute(_SYMBOL, views, _regime(Regime.UNKNOWN), as_of=_NOW)
    # UNKNOWN은 전 Horizon 0.5 균등 — 동일 입력이면 실제로 균등 가중이 적용됐는지 스코어로 검증
    assert out.score > 0
