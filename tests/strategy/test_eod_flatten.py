"""장마감 강제청산 판정 (2026-09-15 F-104, 대응 이상점 1-3).

이 파일이 지키는 것 넷:
  ① 창(마감 N분 전) 안에서만 쏜다 — 그 밖에서는 절대 안 쏜다
  ② **분할**한다 — 한 틱에 전량을 던지지 않는다
  ③ 그리고 **끝을 정한다** — 마감 직전에는 남은 전량을 쓸어담는다
  ④ "안 쐈다"의 이유가 갈린다 — 창 밖인가, 창 안인데 들 것이 없었나
"""

from __future__ import annotations

from messiah.broker.base import BrokerPosition
from messiah.strategy.eod_flatten import decide


def _pos(symbol: str = "A05610", qty: int = 1) -> BrokerPosition:
    return BrokerPosition(symbol=symbol, qty=qty, avg_price_ticks=41_000)


def _total(plan) -> int:
    return sum(s.qty for s in plan.slices)


# --- ① 창 판정 ---------------------------------------------------------------


def test_outside_the_window_never_fires():
    """마감 10.1분 전은 아직 창 밖이다 — 반개구간을 왼쪽으로 새지 않게 한다."""
    plan = decide(minutes_to_close=10.1, positions=[_pos()], lead_minutes=10.0)

    assert plan.should_flatten is False
    assert plan.in_window is False
    assert plan.slices == ()


def test_exactly_at_the_lead_minute_fires():
    """정확히 10.0분 전은 **창 안**이다 — R6이 `<=`로 진입을 막는 그 경계와 같아야 한다."""
    plan = decide(minutes_to_close=10.0, positions=[_pos()], lead_minutes=10.0)

    assert plan.should_flatten is True
    assert plan.in_window is True


def test_outside_regular_session_does_nothing():
    """`minutes_to_close`가 None이면 정규장이 아니다(EventCalendar 계약) — 장 밖에서
    포지션을 건드리면 거래소가 거부할 뿐이고, 재생·스모크 경로가 여기로 샌다."""
    plan = decide(minutes_to_close=None, positions=[_pos()])

    assert plan.should_flatten is False
    assert plan.in_window is False


def test_lead_minutes_is_honoured_not_hardcoded():
    """창 폭은 R6에서 건네받는다 — 여기 상수를 박아두면 두 창이 조용히 어긋난다."""
    assert decide(minutes_to_close=20.0, positions=[_pos()], lead_minutes=30.0).should_flatten
    assert not decide(minutes_to_close=20.0, positions=[_pos()], lead_minutes=10.0).should_flatten


# --- ② 분할 -----------------------------------------------------------------


def test_a_large_position_goes_out_in_slices_not_all_at_once():
    """**핵심** — 10계약을 한 장에 시장가로 던지지 않는다. Master Plan "분할 시장가"."""
    plan = decide(
        minutes_to_close=9.0,
        positions=[_pos(qty=10)],
        lead_minutes=10.0,
        slice_contracts=3,
        final_sweep_minutes=2.0,
    )

    assert _total(plan) == 3, "이번 틱에는 한 조각만 나가야 한다"
    assert plan.final_sweep is False
    assert "분할" in plan.reason


def test_in_flight_shrinks_the_remainder():
    """체결이 아직 안 잡혀 포지션이 그대로여도, 접수된 만큼은 다시 안 보낸다.

    이게 없으면 체결 지연 구간에서 같은 수량을 매 틱 중복 발행한다.
    """
    plan = decide(
        minutes_to_close=9.0,
        positions=[_pos(qty=10)],
        in_flight={"A05610": 9},
        lead_minutes=10.0,
        slice_contracts=3,
    )

    assert _total(plan) == 1, "남은 1계약만 나가야 한다"


def test_symbol_with_everything_in_flight_is_skipped():
    """이미 전량 보낸 심볼은 건너뛴다 — 체결 대기 중 포지션이 남아 있어도."""
    plan = decide(
        minutes_to_close=9.0,
        positions=[_pos(qty=2)],
        in_flight={"A05610": 2},
        lead_minutes=10.0,
    )

    assert plan.should_flatten is False
    assert plan.in_window is True


def test_partial_fill_making_remainder_negative_is_normal():
    """부분 체결로 포지션이 줄면 `남은`이 음수가 된다 — 이미 충분히 보낸 것이고 정상이다."""
    plan = decide(
        minutes_to_close=9.0,
        positions=[_pos(qty=1)],
        in_flight={"A05610": 2},
        lead_minutes=10.0,
    )

    assert plan.should_flatten is False


def test_slice_never_collapses_to_zero():
    """`slice_contracts`가 0이나 음수여도 최소 1은 나간다 — 설정 실수가 미청산이 되면 안 된다."""
    for bad in (0, -5):
        plan = decide(
            minutes_to_close=9.0,
            positions=[_pos(qty=4)],
            lead_minutes=10.0,
            slice_contracts=bad,
        )
        assert _total(plan) == 1, f"slice_contracts={bad}에서 아무것도 안 나갔다"


def test_slicing_drains_the_position_over_ticks():
    """틱을 거듭하면 실제로 전량이 빠져나간다 — 분할이 영원히 안 끝나면 안 된다."""
    qty = 7
    sent: dict[str, int] = {}
    for _ in range(50):
        plan = decide(
            minutes_to_close=9.0,
            positions=[_pos(qty=qty)],
            in_flight=sent,
            lead_minutes=10.0,
            slice_contracts=2,
            final_sweep_minutes=2.0,
        )
        if not plan.should_flatten:
            break
        for piece in plan.slices:
            sent[piece.position.symbol] = sent.get(piece.position.symbol, 0) + piece.qty

    assert sent["A05610"] == qty, "분할을 반복하면 정확히 원 수량만큼만 나가야 한다"


# --- ③ 마무리 쓸어담기 --------------------------------------------------------


def test_final_sweep_sends_everything_left():
    """마감 2분 전부터는 분할을 그만두고 남은 전량을 내보낸다.

    분할만 하고 끝을 안 정하면 마감까지 다 못 빠져나가는 날이 생기고, 그건 이 기능이
    고치려던 바로 그 문제로 되돌아가는 것이다.
    """
    plan = decide(
        minutes_to_close=1.5,
        positions=[_pos(qty=10)],
        lead_minutes=10.0,
        slice_contracts=1,
        final_sweep_minutes=2.0,
    )

    assert _total(plan) == 10
    assert plan.final_sweep is True
    assert "쓸어담기" in plan.reason


def test_final_sweep_still_respects_in_flight():
    """쓸어담기도 이미 보낸 것은 빼고 보낸다 — 여기서 중복이 나면 반대 포지션이 생긴다."""
    plan = decide(
        minutes_to_close=1.0,
        positions=[_pos(qty=10)],
        in_flight={"A05610": 6},
        lead_minutes=10.0,
        final_sweep_minutes=2.0,
    )

    assert _total(plan) == 4


# --- ④ 대상 선별과 사유 -------------------------------------------------------


def test_zero_qty_rows_are_not_positions():
    """수량 0인 줄은 브로커가 흔히 남기는 잔재다 — 그걸로 청산 주문을 만들면 안 된다."""
    plan = decide(minutes_to_close=5.0, positions=[_pos(qty=0)], lead_minutes=10.0)

    assert plan.should_flatten is False
    assert plan.in_window is True


def test_in_window_with_no_position_is_not_silence():
    """창 안인데 들 것이 없는 것과 로직이 아예 안 돈 것은 다른 사실이다.

    09-14 이상점 1-4가 정확히 그 혼동이었다 — 아무 로그도 없어서 "실패인지 의도된
    스킵인지조차 구분이 안 된다"가 그날의 결론이었다.
    """
    plan = decide(minutes_to_close=5.0, positions=[], lead_minutes=10.0)

    assert plan.should_flatten is False
    assert plan.in_window is True, "이 플래그가 있어야 호출자가 그 한 줄을 남길 수 있다"
    assert "내보낼 수량 0" in plan.reason


def test_both_directions_are_flattened():
    """롱도 숏도 대상이다 — 반대매매 방향은 `KillSwitch.liquidate()`가 부호에서 정하고,
    이 모듈은 **무엇을 얼마나** 넘길지만 정한다."""
    plan = decide(
        minutes_to_close=5.0,
        positions=[_pos("A05610", 2), _pos("A05609", -3), _pos("A05608", 0)],
        lead_minutes=10.0,
        slice_contracts=5,
    )

    assert {s.position.symbol for s in plan.slices} == {"A05610", "A05609"}
    # 부호는 원 포지션이 그대로 들고 있다 — 슬라이스 수량은 언제나 양수다.
    assert all(s.qty > 0 for s in plan.slices)
    assert {s.position.symbol: s.position.qty for s in plan.slices} == {
        "A05610": 2,
        "A05609": -3,
    }


def test_every_path_explains_itself():
    """**왜 안 했는지가 왜 했는지만큼 중요하다** — 어느 갈래도 빈 사유를 내지 않는다."""
    cases = [
        dict(minutes_to_close=None, positions=[_pos()]),
        dict(minutes_to_close=30.0, positions=[_pos()]),
        dict(minutes_to_close=5.0, positions=[]),
        dict(minutes_to_close=5.0, positions=[_pos()], in_flight={"A05610": 1}),
        dict(minutes_to_close=5.0, positions=[_pos()]),
        dict(minutes_to_close=1.0, positions=[_pos(qty=4)]),
    ]
    for case in cases:
        assert decide(**case).reason.strip(), f"사유가 비었다: {case}"


# --- R6과 같은 창을 보는가 (결선) ---------------------------------------------


def test_entry_block_and_flatten_share_one_number():
    """R6(진입 차단)과 이 모듈(청산)이 **같은 설정값**을 봐야 한다.

    어긋나면 둘 사이 구간이 생긴다 — 청산 창이 더 넓으면 아직 진입이 열려 있는데 청산이
    먼저 돌고, 진입 창이 더 넓으면 청산 뒤에 새로 들어간 포지션이 밤을 넘긴다.
    `RiskEngine.overnight_flatten_lead_minutes`가 그 유일한 출처다.
    """
    from messiah.risk.risk_engine import RiskEngine, RiskEngineConfig

    lead = RiskEngine(
        RiskEngineConfig(overnight_flatten_lead_minutes=7.0)
    ).overnight_flatten_lead_minutes

    assert lead == 7.0
    assert decide(minutes_to_close=lead, positions=[_pos()], lead_minutes=lead).should_flatten
    assert not decide(
        minutes_to_close=lead + 0.1, positions=[_pos()], lead_minutes=lead
    ).should_flatten
