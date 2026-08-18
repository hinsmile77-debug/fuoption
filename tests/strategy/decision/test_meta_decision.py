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


# ---------------- 입력 부재 갈래 (2026-08-18 F-0818I-1, 원안 2026-08-13 장중 F-1)
#
# 2026-08-18 실측: 9사이클 전부 기여 전문가 0명인데 판단 로그는 "④ |S|=0.000 — 우위 부족"
# 이라고 말했다. 폴백의 `score=0.0 · dispersion=0.0`이 ③을 무사통과해 ④에서 접히므로
# **입력 부재가 우위 부족으로 위장**됐고, Go/No-Go ④ 채점이 배선 문제로 오염됐다.


def test_zero_experts_is_input_absence_not_weak_edge(monkeypatch):
    from messiah.strategy.decision import meta_decision as md_mod

    entries: list[tuple[str, dict]] = []
    monkeypatch.setattr(md_mod.mlog, "log", lambda tag, msg, **f: entries.append((tag, f)))

    view = _view(score=0.0, dispersion=0.0, n_experts=0)
    intent = MetaDecisionEngine().decide(view, kill_active=False)

    assert intent.side == Side.NO_TRADE
    assert "입력 부재" in intent.rationale
    assert entries[0][1]["gate"] == "no_expert", "우위 부족(score)이 아니라 입력 부재다"


def test_zero_experts_wins_over_unknown_regime(monkeypatch):
    """②(regime) 앞에 두는 이유 — 국면 전파가 어긋난 사이클(2026-08-18 장중 1-1)에서
    regime 갈래가 이 갈래를 가리면 입력 부재가 국면 문제로 또 한 번 위장된다."""
    from messiah.strategy.decision import meta_decision as md_mod

    entries: list[tuple[str, dict]] = []
    monkeypatch.setattr(md_mod.mlog, "log", lambda tag, msg, **f: entries.append((tag, f)))

    view = _view(n_experts=0, regime=Regime.UNKNOWN)
    MetaDecisionEngine().decide(view, kill_active=False)

    assert entries[0][1]["gate"] == "no_expert"


def test_kill_still_wins_over_zero_experts(monkeypatch):
    """①(kill)은 무조건 최우선이다 — 새 갈래가 그 위로 올라가면 안 된다."""
    from messiah.strategy.decision import meta_decision as md_mod

    entries: list[tuple[str, dict]] = []
    monkeypatch.setattr(md_mod.mlog, "log", lambda tag, msg, **f: entries.append((tag, f)))

    view = _view(n_experts=0)
    MetaDecisionEngine().decide(view, kill_active=True)

    assert entries[0][1]["gate"] == "kill"


def test_every_decision_log_carries_the_judged_values(monkeypatch):
    """판단 값 계측 (2026-08-18 F-0818I-1) — 통과·차단 경로의 관측 스키마가 같아야 한다.

    종전엔 차단 경로가 값을 안 실어, `gate=score` 9건의 |S|가 0.000인지 0.19인지를
    rationale 문자열을 파싱해야만 알 수 있었다.
    """
    from messiah.strategy.decision import meta_decision as md_mod

    entries: list[tuple[str, dict]] = []
    monkeypatch.setattr(md_mod.mlog, "log", lambda tag, msg, **f: entries.append((tag, f)))

    engine = MetaDecisionEngine()
    engine.decide(_view(score=0.1), kill_active=False)  # 차단(④)
    engine.decide(_view(score=0.9, agg_p_up=0.9), kill_active=False)  # 통과(⑤)

    for _tag, fields in entries:
        for key in ("n_experts", "score", "dispersion", "uncertainty", "gate"):
            assert key in fields, f"{fields.get('gate')} 경로에 {key}가 없다"
