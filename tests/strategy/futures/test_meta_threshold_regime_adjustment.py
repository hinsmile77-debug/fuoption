"""설계표의 「Meta 임계 보정」이 넉 달간 정의만 되어 있었다 — 2026-08-25 F-41 · F-44 · 1-12 · 1-14.

`META_THRESHOLD_ADJUSTMENT`는 `aggregator.py`에 있었고 임계를 쓰는 곳은 `service.py`였다.
**정의한 파일과 써야 할 파일이 달랐고, "호출자 재량"이라는 주석이 배선 책임을 아무에게도
지우지 않았다.** 그 결과 차단 계층 하나가 넉 달간 설계 강도의 0%로 돌았다.

## 이 파일의 픽스처는 2026-08-25 실측 14사이클이다

그날 `MetaGateEvaluated` 14건은 전부 `threshold: 0.0`으로 통과했다. 같은 확률에 설계표
보정을 얹으면 **11건이 차단되고 3건이 통과한다** — 아래 표가 그 계산이고, 숫자는
`logs/g2_daily_20260825.log`에서 그대로 가져왔다.

## 배선은 했으나 판정은 아직 안 바꾼다 (㉠섀도)

R18이 이유이고, 실질적 이유는 따로 있다: 켜는 순간 30m가 전량 차단으로 돈다.
「전량 통과」와 「전량 차단」은 **둘 다 관문이 판단을 안 하는 상태**다.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from messiah.core.messages import FeatureVector, Horizon, Regime, RegimeState
from messiah.core.timeutil import KST
from messiah.simulator.inprocess_bus import InProcessBus
from messiah.strategy.futures.aggregator import (
    META_THRESHOLD_ADJUSTMENT,
    META_THRESHOLD_ADJUSTMENT_SOURCE,
)
from messiah.strategy.futures.expert import HorizonExpert
from messiah.strategy.futures.meta_labeler import META_FEATURE_NAMES, MetaLabeler
from messiah.strategy.futures.service import FuturesAIService

_SYMBOL = "TEST"
_FEATURE_SET = "v-test"
_FEATURE_NAMES = ["px_ret_5", "px_mom_5", "px_rsi_5"]
_NOW = datetime(2026, 8, 25, 10, 0, tzinfo=KST)

# 2026-08-25 실측 14사이클 — (시각, 국면, meta 확률).
AUG25_CYCLES: list[tuple[str, Regime, float]] = [
    ("09:00", Regime.RANGE, 0.04764898211330633),
    ("09:30", Regime.RANGE, 0.027435011740691812),
    ("10:00", Regime.RANGE, 0.029264161991575558),
    ("10:30", Regime.RANGE, 0.03129372311596493),
    ("11:00", Regime.RANGE, 0.028192099069640073),
    ("11:30", Regime.RANGE, 0.028118739384592917),
    ("12:00", Regime.HIGH_VOL, 0.03275313664702633),
    ("12:30", Regime.HIGH_VOL, 0.027637725357622513),
    ("13:00", Regime.HIGH_VOL, 0.02503226064854292),
    ("13:30", Regime.HIGH_VOL, 0.08701950706463064),
    ("14:00", Regime.HIGH_VOL, 0.029251986625481615),
    ("14:30", Regime.RANGE, 0.35658369227917924),
    ("15:00", Regime.TREND_UP, 0.15936197490949056),
    # **오늘 유일하게 ④점수 관문이 열린 사이클**이다(`decision_funnel.pass: 1`).
    # 실제로 막은 것은 R6(장 마감 10분 내 신규 진입 금지)였지 Meta가 아니었다.
    ("15:30", Regime.TREND_UP, 0.08725010757697647),
]


# ------------------------------------------------- 설계표 자체


def test_the_table_matches_the_design_document_including_the_trend_rows() -> None:
    """**§7.1의 「기본」은 0.0이 맞다** (2026-08-25 F-44 선행 조사).

    설계표는 추세 상승·하락 칸에 「기본」이라고 적었고 나머지 칸은 전부 `+0.05` 형태다.
    즉 이 열은 절대 임계가 아니라 **기본 임계에 더할 값**이고, `TREND_UP: 0.0`은 설계를
    어긴 값이 아니라 정확히 옮긴 값이다.

    1-14(*"추세장에서는 관문이 항상 열린다"*)의 뿌리는 이 표가 아니라 **기본 임계 자체가
    0**이라는 것(1-5)이다. 표에 없는 값을 지어 넣으면 설계표와 코드가 갈라진다.
    """
    assert META_THRESHOLD_ADJUSTMENT[Regime.TREND_UP] == 0.0
    assert META_THRESHOLD_ADJUSTMENT[Regime.TREND_DOWN] == 0.0
    assert META_THRESHOLD_ADJUSTMENT[Regime.RANGE] == 0.05
    assert META_THRESHOLD_ADJUSTMENT[Regime.HIGH_VOL] == 0.10
    assert META_THRESHOLD_ADJUSTMENT[Regime.EVENT] == 0.15
    assert META_THRESHOLD_ADJUSTMENT[Regime.UNKNOWN] == 0.10


def test_every_value_says_where_it_came_from() -> None:
    """2026-08-24 F-18의 규율 — 「없다」와 「없다고 적혀 있다」를 가른다."""
    assert set(META_THRESHOLD_ADJUSTMENT_SOURCE) == set(META_THRESHOLD_ADJUSTMENT)
    for regime, source in META_THRESHOLD_ADJUSTMENT_SOURCE.items():
        assert "§7.1" in source, regime


# ------------------------------------------------- 그날 14사이클의 섀도 판정


def _shadow(regime: Regime, probability: float, base: float = 0.0) -> bool:
    return probability >= base + META_THRESHOLD_ADJUSTMENT[regime]


def test_the_design_table_would_have_blocked_eleven_of_that_days_fourteen() -> None:
    """보고서 1-12가 손으로 센 값(11/14 차단 · 3건 통과)과 자릿수까지 일치해야 한다."""
    verdicts = [_shadow(regime, probability) for _ts, regime, probability in AUG25_CYCLES]
    assert verdicts.count(False) == 11
    assert verdicts.count(True) == 3


def test_the_only_cycle_that_actually_opened_the_gate_would_have_passed_anyway() -> None:
    """**F-41만으로는 부족하다** — 그것이 1-14를 새 이상점으로 올린 이유다.

    15:30은 그날 유일하게 점수 관문이 열린 사이클이고 국면이 `TREND_UP`이었다.
    설계표를 배선했어도 보정이 0이라 그 판단은 그대로 통과했을 것이다. 실제로 막은 것은
    R6(시각 조건)였고, 그건 방어의 성공이 아니라 **시각이 우연히 맞은 것**이다.
    """
    ts, regime, probability = AUG25_CYCLES[-1]
    assert ts == "15:30" and regime is Regime.TREND_UP
    assert _shadow(regime, probability) is True


def test_the_range_cycle_that_missed_by_a_hair_is_still_blocked() -> None:
    """09:00은 0.0476으로 임계 0.05에 0.0024 모자랐다 — 경계가 실제로 판정을 가른다."""
    _ts, regime, probability = AUG25_CYCLES[0]
    assert regime is Regime.RANGE
    assert _shadow(regime, probability) is False


# ------------------------------------------------- 서비스가 실제로 그렇게 찍는가


def _train_expert(horizon: Horizon) -> HorizonExpert:
    rows, labels = [], []
    for label, base in ((-1, -5.0), (0, 0.0), (1, 5.0)):
        for index in range(5):
            rows.append([base + index * 0.01, base * 2, 50 + base])
            labels.append(label)
    return HorizonExpert.train(
        horizon=horizon,
        feature_set=_FEATURE_SET,
        model_version="test-v1",
        feature_names=_FEATURE_NAMES,
        x=np.array(rows, dtype=float),
        y=np.array(labels, dtype=int),
        sample_weight=np.ones(len(labels), dtype=float),
    )


def _train_meta(horizon: Horizon) -> MetaLabeler:
    """**임계 0의 번들** — 2026-08-25 현역 `real-20260820-2053-30m`이 그 상태다(1-5)."""
    n = 20
    x = np.random.default_rng(0).normal(size=(n, len(META_FEATURE_NAMES)))
    y = np.ones(n, dtype=int)
    return MetaLabeler.train(horizon=horizon, x=x, y=y, threshold=0.0)


def _feature_vector(horizon: Horizon) -> FeatureVector:
    return FeatureVector(
        symbol=_SYMBOL,
        ts_utc=_NOW,
        horizon=horizon,
        feature_set=_FEATURE_SET,
        values={name: 1.0 for name in _FEATURE_NAMES},
        nan_ratio=0.0,
        valid_until=_NOW,
    )


async def _run_once(monkeypatch, regime: Regime | None) -> dict:
    from messiah.strategy.futures import service as service_mod

    entries: list[tuple[str, dict]] = []
    monkeypatch.setattr(service_mod.mlog, "log", lambda tag, msg, **f: entries.append((tag, f)))
    service = FuturesAIService(
        _SYMBOL,
        {Horizon.M5: _train_expert(Horizon.M5)},
        InProcessBus(),
        meta_labelers={Horizon.M5: _train_meta(Horizon.M5)},
    )
    if regime is not None:
        await service.handle_regime(
            RegimeState(symbol=_SYMBOL, regime=regime, confidence=0.8, state_duration_bars=3)
        )
    await service.handle_feature(_feature_vector(Horizon.M5))
    return next(f for tag, f in entries if tag == "MetaGateEvaluated")


@pytest.mark.asyncio
async def test_the_shadow_threshold_is_logged_without_changing_the_verdict(monkeypatch) -> None:
    """㉠섀도의 정의 그 자체 — 값은 계산하고 로그에 남기되 `passed`는 안 건드린다."""
    fields = await _run_once(monkeypatch, Regime.HIGH_VOL)
    assert fields["threshold_base"] == 0.0
    assert fields["threshold_regime_adj"] == 0.10
    assert fields["threshold_shadow"] == 0.10
    # 실판정은 여전히 기본 임계로 한다 — `p >= 0.0`은 언제나 참이다.
    assert fields["threshold"] == 0.0
    assert fields["passed"] is True
    # 섀도는 그 확률을 막았을 것이다(테스트 번들의 확률은 0.10 미만이다).
    assert fields["passed_shadow"] == (fields["probability"] >= 0.10)
    # 로그가 말한 섀도 판정과 섀도 임계가 서로 어긋나면 이 계측은 소설이다.
    assert fields["passed_shadow"] == (fields["probability"] >= fields["threshold_shadow"])


@pytest.mark.asyncio
async def test_an_unseen_regime_gets_no_adjustment_at_all(monkeypatch) -> None:
    """**「안 온 것」과 「UNKNOWN으로 판정된 것」을 다시 한 몸으로 만들지 않는다.**

    2026-08-19 F-5가 정확히 그 둘을 갈랐다. 국면 미수신 상태에 「보수적으로 UNKNOWN
    +0.10」을 먹이면 그 구분이 도로 사라진다 — 로그만 봐서는 첫 사이클이 보수 모드였는지
    국면이 안 왔는지 알 수 없게 된다.
    """
    fields = await _run_once(monkeypatch, None)
    assert fields["regime_received"] is False
    assert fields["threshold_regime_adj"] == 0.0
    assert fields["threshold_shadow"] == fields["threshold_base"]
    assert "미수신" in fields["threshold_adj_source"]


@pytest.mark.asyncio
async def test_the_trend_regime_keeps_crying_after_the_wiring(monkeypatch) -> None:
    """**1-14를 매일 재는 방식** (F-44).

    배선 뒤에도 추세 국면은 보정이 0이라 유효 임계가 0으로 남는다. 표에 없는 값을 지어
    넣어 조용하게 만드는 대신, 그 사실이 매일 WARNING으로 나오게 둔다.
    """
    import logging

    trend = await _run_once(monkeypatch, Regime.TREND_UP)
    assert trend["threshold_shadow"] == 0.0
    assert trend["level"] == logging.WARNING  # 게이트 무력 — 계속 운다
    assert "§7.1" in trend["threshold_adj_source"]  # 그런데 그 0에는 출처가 있다

    # 다른 국면은 배선이 승격되면 조용해질 자리다 — 지금도 유효 임계가 0이 아니다.
    ranged = await _run_once(monkeypatch, Regime.RANGE)
    assert ranged["threshold_shadow"] == 0.05
    assert ranged.get("level") is None


@pytest.mark.asyncio
async def test_the_regime_that_produced_the_adjustment_is_named_in_the_log(monkeypatch) -> None:
    """값만 남기면 20거래일 뒤에 "이 0.0이 설계값인가 배선 누락인가"를 다시 조사하게 된다."""
    fields = await _run_once(monkeypatch, Regime.EVENT)
    assert fields["regime"] == Regime.EVENT.value
    assert fields["threshold_regime_adj"] == 0.15
    assert "이벤트" in fields["threshold_adj_source"]
