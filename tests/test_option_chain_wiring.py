"""옵션체인 결선 — 폴링 계획(주기·위상·만기일 교대)과 REST 유량 예산 (2026-08-04).

여기서 지키려는 것은 마흐디(선행 프로젝트)가 두 번 잃고 배운 두 가지다:
- 총수요가 용량에 붙으면 호출 시각을 재배치해도 못 푼다(2026-07-30, 25사이클 유실)
- 총수요가 같아도 **한 틱에 몰리면** 다음 틱이 통째로 스킵된다(2026-08-03, 39분 결손)
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import run_l1_daily as rl1  # noqa: E402

from messiah.broker.kis.rest_client import (  # noqa: E402
    DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
)
from messiah.core import universe  # noqa: E402
from messiah.data.option_chain_poller import OptionChainPoller  # noqa: E402

# 마흐디 2026-07-30 실측 최대 백오프 배율 — 수요 0.663건/초·내성 1.51배에서 이 값에 부딪혀
# 옵션체인 25사이클(5.1%)이 통째로 유실됐다. MESSIAH의 내성은 이보다 커야 한다.
_MAHDI_OBSERVED_MAX_BACKOFF = 2.61

_TUESDAY = date(2026, 8, 4)  # 만기 아님
_MONDAY = date(2026, 8, 10)  # 월위클리 만기
_THURSDAY = date(2026, 8, 6)  # 목위클리 만기


class _FakeMaster:
    def nearest_expiry_chain(self, underlying, *, series="regular"):  # pragma: no cover
        return []


def _collection(plan):
    pollers = tuple(
        (
            OptionChainPoller(
                None,
                _FakeMaster(),
                None,
                series=series,
                reference_price=lambda: 1000.0,
                strike_window=rl1._OPTION_STRIKE_WINDOW,
            ),
            period,
            phase,
        )
        for series, period, phase in plan
    )
    return rl1._RestCollection(flow_poller=object(), chain_pollers=pollers)


# ------------------------------------------------------------ 폴링 계획


def test_plan_covers_every_confirmed_option_series():
    plan = rl1._option_chain_plan(_TUESDAY)

    assert [series for series, _, _ in plan] == universe.option_series(
        list(universe.DEFAULT_UNIVERSE)
    )


def test_monthly_is_fast_and_weeklies_are_slow_on_an_ordinary_day():
    """먼슬리는 GEX/감마플립의 주 입력, 위클리는 핀 리스크 전용 — 요구 해상도가 다르다."""
    plan = dict((series, period) for series, period, _ in rl1._option_chain_plan(_TUESDAY))

    assert plan["regular"] == rl1._OPTION_FAST_SECONDS
    assert plan["weekly_mon"] == rl1._OPTION_SLOW_SECONDS
    assert plan["weekly_thu"] == rl1._OPTION_SLOW_SECONDS


def test_every_series_gets_a_distinct_phase():
    """2026-08-03 마흐디 사고의 핵심 — 같은 위상에 몰리면 총량이 같아도 밀림이 한쪽에 쏠린다."""
    phases = [phase for _, _, phase in rl1._option_chain_plan(_TUESDAY)]

    assert len(set(phases)) == len(phases)


def test_phases_are_spread_within_the_fast_grid():
    """위상이 빠른 격자 안에 고루 퍼져야 한 틱에 두 폴러가 겹치지 않는다."""
    phases = sorted(phase for _, _, phase in rl1._option_chain_plan(_TUESDAY))

    assert all(0 <= p < rl1._OPTION_FAST_SECONDS for p in phases)
    assert min(b - a for a, b in zip(phases, phases[1:])) >= 60.0


# ------------------------------------------------------------ 만기일 교대


def test_expiring_weekly_takes_over_the_fast_cadence_on_its_expiry_day():
    """만기 당일 북은 0DTE라 BS 감마가 정의되지 않는 대신 **핀 리스크가 거기서만** 나온다.
    MESSIAH는 옵션 WS가 없어 그대로 두면 그날 핀 리스크를 10분 해상도로 보게 된다."""
    for day, expiring in ((_MONDAY, "weekly_mon"), (_THURSDAY, "weekly_thu")):
        plan = dict((series, period) for series, period, _ in rl1._option_chain_plan(day))

        assert plan[expiring] == rl1._OPTION_FAST_SECONDS, day
        assert plan["regular"] == rl1._OPTION_SLOW_SECONDS, day


def test_expiry_day_swap_does_not_add_any_demand():
    """ "총수요는 1건도 안 늘고 해상도만 옮긴다"가 이 교대의 전제다."""
    ordinary = _collection(rl1._option_chain_plan(_TUESDAY)).requests_per_second
    expiry_day = _collection(rl1._option_chain_plan(_MONDAY)).requests_per_second

    assert ordinary == expiry_day


def test_phases_are_stable_across_the_expiry_swap():
    """주기만 바꾸고 위상은 그대로 — 위상까지 흔들면 겹침 회피가 깨진다."""
    ordinary = {s: ph for s, _, ph in rl1._option_chain_plan(_TUESDAY)}
    expiry_day = {s: ph for s, _, ph in rl1._option_chain_plan(_MONDAY)}

    assert ordinary == expiry_day


def test_exactly_one_series_is_fast_on_any_day():
    for day in (_TUESDAY, _MONDAY, _THURSDAY):
        periods = [period for _, period, _ in rl1._option_chain_plan(day)]

        assert periods.count(rl1._OPTION_FAST_SECONDS) == 1, day


# ------------------------------------------------------------ 유량 예산


def test_demand_stays_far_enough_below_capacity_to_survive_mahdi_s_worst_backoff():
    collection = _collection(rl1._option_chain_plan(_TUESDAY))

    assert collection.backoff_headroom > _MAHDI_OBSERVED_MAX_BACKOFF


def test_budget_matches_the_hand_computed_plan():
    """먼쓰리 42/300 + 위클리 42/600 x2 + 수급 3/60 = 0.330건/초."""
    collection = _collection(rl1._option_chain_plan(_TUESDAY))

    assert collection.requests_per_second == pytest.approx(0.330, rel=1e-3)


def test_equal_cadence_would_not_have_survived():
    """3종을 균등하게 빠른 격자로 돌면 내성이 마흐디 실측 백오프 밑으로 내려간다 — 이
    테스트가 깨지는 방향으로 계획을 바꾸면 그날 사고를 그대로 재현하는 것이다."""
    equal = [
        (series, rl1._OPTION_FAST_SECONDS, 0.0)
        for series in ("regular", "weekly_mon", "weekly_thu")
    ]

    assert _collection(equal).backoff_headroom < _MAHDI_OBSERVED_MAX_BACKOFF


def test_capacity_reference_is_the_shared_pacer_not_a_per_poller_one():
    """폴러마다 페이서를 두면 실효 호출률이 배수로 뛴다 — 마흐디 2026-07-08, 203분 유실."""
    collection = _collection(rl1._option_chain_plan(_TUESDAY))
    capacity = 1.0 / DEFAULT_MIN_REQUEST_INTERVAL_SECONDS

    assert collection.backoff_headroom == pytest.approx(capacity / collection.requests_per_second)


def test_empty_collection_reports_no_demand():
    empty = rl1._RestCollection()

    assert empty.requests_per_second == 0.0
    assert empty.backoff_headroom == float("inf")
