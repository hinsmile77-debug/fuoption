"""거래 대상 확정(2026-08-04) — 미니선물 + 먼쓰리/월위클리/목위클리."""

import pytest

from messiah.broker.kis import symbol_master
from messiah.broker.kis.adapter import _PROBE_PRODUCT_TYPES
from messiah.core import universe
from messiah.core.config import InstanceConfig


def test_confirmed_universe_is_mini_futures_plus_three_option_series():
    assert universe.DEFAULT_UNIVERSE == (
        "K200_MINI_FUT",
        "K200_OPT_MONTHLY",
        "K200_OPT_WEEKLY_MON",
        "K200_OPT_WEEKLY_THU",
    )


def test_option_series_names_match_symbol_master_vocabulary():
    """`universe.py`는 의존 방향 때문에 series 이름을 문자열로 복제한다(모듈 docstring) —
    어긋나면 조용히 빈 체인을 도는 대신 여기서 깨져야 한다."""
    known = set(symbol_master._SERIES_PRODUCT_TYPES)

    assert set(universe.OPTION_SERIES_BY_TOKEN.values()) <= known


def test_all_three_option_series_are_distinct_and_present():
    series = universe.option_series(list(universe.DEFAULT_UNIVERSE))

    assert series == ["regular", "weekly_mon", "weekly_thu"]


def test_mini_options_are_not_in_the_universe():
    """미니옵션(D/E)은 2026-07-22 실측에서 상장 0/0이라 뺐다 — symbol_master는 여전히
    series로 알고 있으므로, 유니버스에 없다는 것이 이 테스트의 주장이다."""
    assert "mini" in symbol_master._SERIES_PRODUCT_TYPES
    assert "mini" not in universe.OPTION_SERIES_BY_TOKEN.values()


def test_futures_token_is_mini_only():
    """정규선물(K200_FUT)은 어댑터가 조회는 하지만 거래 대상이 아니다."""
    assert universe.futures_tokens(list(universe.DEFAULT_UNIVERSE)) == ["K200_MINI_FUT"]
    assert "K200_FUT" not in universe.KNOWN_TOKENS
    assert "K200_FUT" in _PROBE_PRODUCT_TYPES  # 교차검증 경로로는 남아 있음


def test_every_futures_token_is_resolvable_by_the_adapter():
    """유니버스의 선물 토큰은 probe_front_month()가 실제로 받을 수 있어야 한다 — 이게
    깨지면 '설정엔 있는데 조회는 ValueError'인 옛 K200_OPT 상태가 재발한 것이다."""
    for token in universe.futures_tokens(list(universe.DEFAULT_UNIVERSE)):
        assert token in _PROBE_PRODUCT_TYPES


# ------------------------------------------------------------ 죽은 토큰 차단


def test_retired_k200_opt_token_is_rejected():
    """종전 단일 옵션 토큰 — 소비자가 없어 죽어 있었다. 조용히 무시하지 않는다."""
    with pytest.raises(universe.UnknownUniverseTokenError) as exc:
        universe.validate(["K200_MINI_FUT", "K200_OPT"])

    assert "K200_OPT_MONTHLY" in str(exc.value)  # 무엇으로 바뀌었는지 알려줘야 한다


def test_config_rejects_unknown_universe_token_at_load_time():
    with pytest.raises(ValueError):
        InstanceConfig(universe=["K200_MINI_FUT", "K200_NOT_A_THING"])


def test_config_default_is_the_confirmed_universe():
    assert InstanceConfig().universe == list(universe.DEFAULT_UNIVERSE)


def test_validate_passes_the_confirmed_universe_through():
    tokens = list(universe.DEFAULT_UNIVERSE)

    assert universe.validate(tokens) == tokens
