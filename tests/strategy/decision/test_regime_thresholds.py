"""국면별 우위 게이트 표 — 2026-09-02 신설.

표를 만든 이유는 값을 바꾸려는 게 아니라 **결합을 드러내려는** 것이다(모듈 상수 주석).
그래서 이 파일이 지키는 것은 두 가지다: ① 표를 도입해도 동작이 한 톨도 안 바뀐다,
② 나중에 누가 값을 갈라 놓으면 그 사실이 조용히 지나가지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from messiah.core.messages import FuturesView, Regime, Side
from messiah.strategy.decision.meta_decision import (
    SCORE_THRESHOLD,
    SCORE_THRESHOLD_BY_REGIME,
    MetaDecisionConfig,
    MetaDecisionEngine,
)

_TS = datetime(2026, 9, 2, 0, 0, tzinfo=timezone.utc)


def _view(score: float, regime: Regime = Regime.TREND_UP) -> FuturesView:
    return FuturesView(
        symbol="A05609",
        ts_utc=_TS,
        score=score,
        agg_p_up=0.6,
        agg_p_down=0.2,
        uncertainty=0.1,
        dispersion=0.0,
        regime=regime,
        n_experts=1,
        model_versions=["test"],
        top_features=[],
        valid_until=None,
    )


def test_every_regime_is_in_the_table():
    """새 국면이 생겼는데 표에 없으면 게이트가 폴백으로 흐른다 — 여기서 잡는다."""
    assert set(SCORE_THRESHOLD_BY_REGIME) == set(Regime)


def test_the_table_is_uniform_today():
    """**값을 바꾼 순간 이 테스트가 깨진다.** 깨지는 것이 목적이다 — 위험 성향을 바꾸는
    변경이므로 사람이 이 문장을 다시 읽고 고쳐 적어야 한다(DECISION_LOG 2026-08-24 규율)."""
    assert set(SCORE_THRESHOLD_BY_REGIME.values()) == {SCORE_THRESHOLD}


@pytest.mark.parametrize(
    "regime", [Regime.TREND_UP, Regime.TREND_DOWN, Regime.RANGE, Regime.HIGH_VOL]
)
def test_behaviour_is_unchanged_by_the_table(regime: Regime):
    """표 도입 전후로 판정이 같다 — 경계 바로 아래/위."""
    engine = MetaDecisionEngine()

    assert engine.decide(_view(0.199, regime), kill_active=False).side is Side.NO_TRADE
    assert engine.decide(_view(0.20, regime), kill_active=False).side is Side.LONG
    assert engine.decide(_view(-0.20, regime), kill_active=False).side is Side.SHORT


def test_a_single_threshold_override_still_means_what_it_says():
    """`MetaDecisionConfig(score_threshold=0.5)`가 조용히 0.20으로 돌아가면 스윕이 재려던
    것과 다른 것을 잰다(`__post_init__` docstring)."""
    engine = MetaDecisionEngine(MetaDecisionConfig(score_threshold=0.5))

    assert engine.decide(_view(0.40), kill_active=False).side is Side.NO_TRADE
    assert engine.decide(_view(0.55), kill_active=False).side is Side.LONG


def test_a_per_regime_table_is_honoured():
    """표를 명시하면 국면마다 다른 게이트가 실제로 적용된다(지금은 아무도 안 쓰지만,
    쓰이지 않는 경로는 배선된 적 없는 경로다)."""
    config = MetaDecisionConfig(
        score_threshold=0.20,
        score_threshold_by_regime={Regime.RANGE: 0.05, Regime.TREND_UP: 0.40},
    )
    engine = MetaDecisionEngine(config)

    assert engine.decide(_view(0.10, Regime.RANGE), kill_active=False).side is Side.LONG
    assert engine.decide(_view(0.30, Regime.TREND_UP), kill_active=False).side is Side.NO_TRADE
    # 표에 없는 국면은 폴백(0.20)으로 — 조용히 0이 되지 않는다
    assert engine.decide(_view(0.30, Regime.HIGH_VOL), kill_active=False).side is Side.LONG


def test_the_decision_says_which_regime_and_gate_it_used(capsys):
    """**판단은 자기 조건을 스스로 말한다** (2026-09-02).

    이 필드가 없어서 국면별 성적을 재려면 `RegimeClassified`와 시각으로 조인해야 했고,
    그 조인이 42건을 국면 미상으로 흘렸다. 구조화 필드로 나가는지를 보는 것이라 사람이
    읽는 메시지가 아니라 **JSON 줄**을 판다(`models/regime_direction.parse_decision()`이
    실제로 읽는 것이 이 줄이다).
    """
    import json

    from messiah.core import logging as mlog

    mlog.setup("test")
    engine = MetaDecisionEngine()
    engine.decide(_view(0.05, Regime.RANGE), kill_active=False)

    emitted = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("{") and '"DecisionEmitted"' in line
    ]
    assert emitted, "DecisionEmitted가 안 나갔다"
    assert emitted[-1]["regime"] == "RANGE"
    assert emitted[-1]["score_threshold"] == pytest.approx(0.20)
