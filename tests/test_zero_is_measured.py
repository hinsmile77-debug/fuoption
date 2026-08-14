"""**0이 "없었다"인지 "안 셌다"인지 가른다** — 2026-08-14 F-5 · F-6.

두 축이 같은 병을 앓고 있었다. 어떤 사실의 건수가 0인데, 그 0이

    ① 진짜 그 일이 안 일어났거나
    ② 그 일을 세는 계측이 아예 없거나

둘 중 어느 쪽인지 로그로 구분할 수 없었다(점검 스킬 체크리스트 D — *"건수 0은 두
가지다"*). 그 결과:

    F-5  `n_experts=0`의 사유를 못 밝혀 `NEXT_TODO` W-2가 **3거래일째 미확정**
    F-6  옵션체인 "성공"에 로그가 없어 폴러 생사를 파일시스템으로 확인해야 했다
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from messiah.core.messages import ExpertView, Horizon, Regime, RegimeState
from messiah.core.timeutil import KST
from messiah.strategy.futures import aggregator as agg_mod
from messiah.strategy.futures.aggregator import Aggregator, AggregatorConfig

_NOW = datetime(2026, 8, 14, 10, 30, tzinfo=KST)


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    entries: list[tuple[str, dict]] = []
    monkeypatch.setattr(agg_mod.mlog, "log", lambda tag, msg, **f: entries.append((tag, f)))
    return entries


def _view(
    *,
    meta_passed: bool = True,
    ens_std: float = 0.0,
    valid_for: timedelta | None = timedelta(minutes=30),
) -> ExpertView:
    return ExpertView(
        symbol="A05609",
        horizon=Horizon.M30,
        p_up=0.6,
        p_flat=0.0,
        p_down=0.4,
        ens_std=ens_std,
        meta_passed=meta_passed,
        model_version="v-test",
        top_features=[],
        valid_until=None if valid_for is None else _NOW + valid_for,
    )


def _regime(value: Regime = Regime.UNKNOWN) -> RegimeState:
    return RegimeState(
        symbol="A05609",
        regime=value,
        confidence=0.0,
        state_duration_bars=1,
    )


def _compute(views, regime=Regime.UNKNOWN):
    return Aggregator(AggregatorConfig(uncertainty_scale=0.5)).compute(
        "A05609", views, _regime(regime), as_of=_NOW
    )


# ------------------------------------------------------------------ F-5


def test_no_views_at_all_is_named(logged) -> None:
    """전문가가 아예 안 붙은 날 — 2026-07-31에 live 번들 0개로 하루를 보낸 형태."""
    result = _compute({})

    assert result.n_experts == 0
    tag, fields = next(t for t in logged if t[0] == "AggregatorNoContribution")
    assert tag == "AggregatorNoContribution"
    assert fields["views_received"] == 0


def test_meta_rejection_is_named(logged) -> None:
    _compute({Horizon.M30: _view(meta_passed=False)})
    fields = next(f for t, f in logged if t == "AggregatorNoContribution")
    assert fields["blocked_by_meta"] == ["30m"]
    assert fields["blocked_by_uncertainty"] == []
    assert fields["blocked_by_freshness"] == []


def test_uncertainty_saturation_is_named(logged) -> None:
    """`ens_std >= uncertainty_scale`면 `u_h=1`이라 가중치가 0이 된다.

    2026-08-14에 30m `nan_ratio`가 종일 84.7%였으니 이 갈래가 유력했지만 — **유력한 것과
    확정한 것은 다르다.** 이제 로그가 답한다.
    """
    _compute({Horizon.M30: _view(ens_std=0.9)})
    fields = next(f for t, f in logged if t == "AggregatorNoContribution")
    assert fields["blocked_by_uncertainty"] == ["30m"]
    assert fields["blocked_by_meta"] == []


def test_staleness_is_named(logged) -> None:
    _compute({Horizon.M30: _view(valid_for=timedelta(minutes=-60))})
    fields = next(f for t, f in logged if t == "AggregatorNoContribution")
    assert fields["blocked_by_freshness"] == ["30m"]


def test_every_applicable_cause_is_recorded_not_just_the_first(logged) -> None:
    """**하나만 남기면 "먼저 검사한 것"이 원인처럼 보인다.**

    한 Horizon이 여러 갈래에 동시에 걸릴 수 있고, 그때 무엇을 고쳐야 하는지는 전부를
    봐야 정해진다.
    """
    _compute({Horizon.M30: _view(meta_passed=False, ens_std=0.9)})
    fields = next(f for t, f in logged if t == "AggregatorNoContribution")
    assert fields["blocked_by_meta"] == ["30m"]
    assert fields["blocked_by_uncertainty"] == ["30m"]


def test_a_contributing_view_stays_quiet(logged) -> None:
    """기여가 있으면 안 찍는다 — 매 사이클 울면 그게 곧 잡음이다."""
    result = _compute({Horizon.M30: _view()})
    assert result.n_experts == 1
    assert not [t for t, _ in logged if t == "AggregatorNoContribution"]


def test_the_message_carries_the_regime(logged) -> None:
    """국면이 UNKNOWN이면 이건 정상 동작이다 — 사유를 읽는 사람이 그것부터 알아야 한다."""
    _compute({Horizon.M30: _view(meta_passed=False)}, regime=Regime.TREND_UP)
    fields = next(f for t, f in logged if t == "AggregatorNoContribution")
    assert fields["regime"] == "TREND_UP"
    assert fields["horizons_received"] == ["30m"]


# ------------------------------------------------------------------ F-6


class _Bus:
    def __init__(self) -> None:
        self.published: list[object] = []

    async def publish(self, topic: str, msg: object) -> None:
        self.published.append(msg)


def test_poll_one_reports_whether_it_published() -> None:
    """사이클 요약이 "몇 다리를 **실제로** 내보냈나"를 말하려면 이 반환값이 필요하다.

    창 크기만 적으면 절반이 조용히 실패한 사이클과 온전한 사이클이 같은 줄로 나간다 —
    그건 이 태그를 만든 이유와 정반대다.
    """
    import inspect

    from messiah.data.option_chain_poller import OptionChainPoller

    signature = inspect.signature(OptionChainPoller._poll_one)
    assert signature.return_annotation == "bool"


def test_polled_tag_is_debug_not_warning() -> None:
    """`OptionChainPollEmpty`가 2026-08-07에 WARNING이라 22번 울고 강등된 전례를 따른다.

    성공 로그가 WARNING이면 하루 550건이 경보가 되어 진짜 신호를 덮는다.
    """
    import logging

    from messiah.core.logging import TAG_LEVELS

    assert TAG_LEVELS["OptionChainPolled"] == logging.DEBUG
    # F-5는 반대로 INFO — 국면이 죽은 날엔 정상 동작이라 WARNING이면 30분마다 운다.
    assert TAG_LEVELS["AggregatorNoContribution"] == logging.INFO
