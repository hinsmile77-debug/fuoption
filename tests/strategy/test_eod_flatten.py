"""장마감 강제청산 판정 (2026-09-15 F-104, 대응 이상점 1-3).

이 파일이 지키는 것 셋:
  ① 창(마감 N분 전) 안에서만 쏜다 — 그 밖에서는 절대 안 쏜다
  ② 하루 한 번만 쏜다 — 고정 틱이 30초마다 도는데 창 안 매 틱이 「대상」으로 판정된다
  ③ "안 쐈다"의 이유가 로그로 갈린다 — 창 밖인가, 창 안인데 들 것이 없었나
"""

from __future__ import annotations

from datetime import date

from messiah.broker.base import BrokerPosition
from messiah.strategy.eod_flatten import decide

TODAY = date(2026, 9, 15)


def _pos(symbol: str = "A05610", qty: int = 1) -> BrokerPosition:
    return BrokerPosition(symbol=symbol, qty=qty, avg_price_ticks=41_000)


# --- ① 창 판정 ---------------------------------------------------------------


def test_outside_the_window_never_fires():
    """마감 10.1분 전은 아직 창 밖이다 — 반개구간을 왼쪽으로 새지 않게 한다."""
    plan = decide(
        minutes_to_close=10.1,
        positions=[_pos()],
        today=TODAY,
        already_done_for=None,
        lead_minutes=10.0,
    )

    assert plan.should_flatten is False
    assert plan.in_window is False
    assert plan.positions == ()


def test_exactly_at_the_lead_minute_fires():
    """정확히 10.0분 전은 **창 안**이다 — R6이 `<=`로 진입을 막는 그 경계와 같아야 한다."""
    plan = decide(
        minutes_to_close=10.0,
        positions=[_pos()],
        today=TODAY,
        already_done_for=None,
        lead_minutes=10.0,
    )

    assert plan.should_flatten is True
    assert plan.in_window is True


def test_outside_regular_session_does_nothing():
    """`minutes_to_close`가 None이면 정규장이 아니다(EventCalendar 계약) — 장 밖에서
    포지션을 건드리면 거래소가 거부할 뿐이고, 재생·스모크 경로가 여기로 샌다."""
    plan = decide(
        minutes_to_close=None,
        positions=[_pos()],
        today=TODAY,
        already_done_for=None,
    )

    assert plan.should_flatten is False
    assert plan.in_window is False


# --- ② 하루 한 번 ------------------------------------------------------------


def test_second_tick_on_the_same_day_does_not_refire():
    """창 안에서 30초마다 도는 틱이 매번 쏘면 반대 포지션이 쌓인다.

    중복 청산은 미청산보다 고치기 어렵다 — 없던 방향의 포지션이 새로 생기기 때문이다.
    """
    plan = decide(
        minutes_to_close=5.0,
        positions=[_pos()],
        today=TODAY,
        already_done_for=TODAY,
        lead_minutes=10.0,
    )

    assert plan.should_flatten is False
    assert plan.in_window is True, "창 안이라는 사실 자체는 유지돼야 한다"


def test_previous_day_lock_does_not_block_today():
    """어제 쐈다는 사실이 오늘을 막으면 안 된다 — 프로세스가 밤새 살아 있는 구성이 있다."""
    plan = decide(
        minutes_to_close=5.0,
        positions=[_pos()],
        today=TODAY,
        already_done_for=date(2026, 9, 14),
        lead_minutes=10.0,
    )

    assert plan.should_flatten is True


# --- ③ 침묵과 무포지션을 가른다 ----------------------------------------------


def test_in_window_with_no_position_is_not_silence():
    """창 안인데 들 것이 없는 것과 로직이 아예 안 돈 것은 다른 사실이다.

    09-14 이상점 1-4가 정확히 그 혼동이었다 — 아무 로그도 없어서 "실패인지 의도된
    스킵인지조차 구분이 안 된다"가 그날의 결론이었다.
    """
    plan = decide(
        minutes_to_close=5.0,
        positions=[],
        today=TODAY,
        already_done_for=None,
        lead_minutes=10.0,
    )

    assert plan.should_flatten is False
    assert plan.in_window is True, "이 플래그가 있어야 호출자가 그 한 줄을 남길 수 있다"
    assert "보유 포지션 0" in plan.reason


def test_zero_qty_rows_are_not_positions():
    """수량 0인 줄은 브로커가 흔히 남기는 잔재다 — 그걸로 청산 주문을 만들면 안 된다."""
    plan = decide(
        minutes_to_close=5.0,
        positions=[_pos(qty=0)],
        today=TODAY,
        already_done_for=None,
        lead_minutes=10.0,
    )

    assert plan.should_flatten is False
    assert plan.in_window is True


def test_every_path_explains_itself():
    """**왜 안 했는지가 왜 했는지만큼 중요하다** — 어느 갈래도 빈 사유를 내지 않는다."""
    cases = [
        dict(minutes_to_close=None, positions=[_pos()], already_done_for=None),
        dict(minutes_to_close=30.0, positions=[_pos()], already_done_for=None),
        dict(minutes_to_close=5.0, positions=[_pos()], already_done_for=TODAY),
        dict(minutes_to_close=5.0, positions=[], already_done_for=None),
        dict(minutes_to_close=5.0, positions=[_pos()], already_done_for=None),
    ]
    for case in cases:
        plan = decide(today=TODAY, **case)
        assert plan.reason.strip(), f"사유가 비었다: {case}"


# --- 대상 선별 ---------------------------------------------------------------


def test_both_directions_are_flattened():
    """롱도 숏도 대상이다 — 반대매매 방향 결정은 `KillSwitch.liquidate()`의 몫이고,
    이 모듈은 **무엇을** 넘길지만 정한다."""
    plan = decide(
        minutes_to_close=5.0,
        positions=[_pos("A05610", 2), _pos("A05609", -3), _pos("A05608", 0)],
        today=TODAY,
        already_done_for=None,
        lead_minutes=10.0,
    )

    assert plan.should_flatten is True
    assert {p.symbol for p in plan.positions} == {"A05610", "A05609"}


def test_lead_minutes_is_honoured_not_hardcoded():
    """창 폭은 R6에서 건네받는다 — 여기 상수를 박아두면 두 창이 조용히 어긋난다."""
    wide = decide(
        minutes_to_close=20.0,
        positions=[_pos()],
        today=TODAY,
        already_done_for=None,
        lead_minutes=30.0,
    )
    narrow = decide(
        minutes_to_close=20.0,
        positions=[_pos()],
        today=TODAY,
        already_done_for=None,
        lead_minutes=10.0,
    )

    assert wide.should_flatten is True
    assert narrow.should_flatten is False


# --- R6과 같은 창을 보는가 (결선) ---------------------------------------------


def test_entry_block_and_flatten_share_one_number():
    """R6(진입 차단)과 이 모듈(청산)이 **같은 설정값**을 봐야 한다.

    어긋나면 둘 사이 구간이 생긴다 — 청산 창이 더 넓으면 아직 진입이 열려 있는데 청산이
    먼저 돌고, 진입 창이 더 넓으면 청산 뒤에 새로 들어간 포지션이 밤을 넘긴다.
    `RiskEngine.overnight_flatten_lead_minutes`가 그 유일한 출처다.
    """
    from messiah.risk.risk_engine import RiskEngine, RiskEngineConfig

    engine = RiskEngine(RiskEngineConfig(overnight_flatten_lead_minutes=7.0))
    lead = engine.overnight_flatten_lead_minutes

    assert lead == 7.0
    # R6이 거부하기 시작하는 바로 그 순간 청산도 개시된다.
    assert (
        decide(
            minutes_to_close=lead,
            positions=[_pos()],
            today=TODAY,
            already_done_for=None,
            lead_minutes=lead,
        ).should_flatten
        is True
    )
    # R6이 아직 진입을 허용하는 순간에는 청산도 안 돈다.
    assert (
        decide(
            minutes_to_close=lead + 0.1,
            positions=[_pos()],
            today=TODAY,
            already_done_for=None,
            lead_minutes=lead,
        ).should_flatten
        is False
    )
