"""EV(이벤트·시간·만기) Feature 테스트 — 전부 손으로 검산한 known-value (SYSTEM.md R16).

날짜는 실제 2026년 달력에서 고른다: 2026-08-13(목)은 8월 둘째 목요일 = 정규월물 만기,
2026-09-10(목)은 9월 만기 = **동시만기**, 2026-08-14(금)/17(월)은 평범한 주말 경계다.
합성 날짜를 쓰면 "둘째 목요일" 같은 규칙을 테스트가 다시 계산하게 되고, 그러면 구현과
같은 실수를 공유한다.
"""

from datetime import date, datetime, timedelta

import pytest

from messiah.core.event_calendar import EventCalendar, SessionHours
from messiah.core.messages import BarClosed, Horizon
from messiah.core.timeutil import KST
from messiah.features import ev_core as ev


def _calendar(holidays: list[date] | None = None, years: set[int] | None = None) -> EventCalendar:
    return EventCalendar(
        frozenset(holidays or []),
        SessionHours(),
        years=frozenset(years or {2025, 2026, 2027}),
    )


def _bar(when: datetime, horizon: Horizon = Horizon.M5) -> BarClosed:
    """`when`이 봉 **확정** 시각이 되도록 시작 시각을 역산한다 — EV는 확정 시각을 본다."""
    seconds = {Horizon.M1: 60, Horizon.M5: 300, Horizon.M30: 1800}[horizon]
    return BarClosed(
        symbol="A05608",
        horizon=horizon,
        bar_open_kst=when - timedelta(seconds=seconds),
        o_ticks=100,
        h_ticks=105,
        l_ticks=95,
        c_ticks=100,
        volume=10,
    )


def _at(y: int, m: int, d: int, hh: int = 10, mm: int = 0) -> list[BarClosed]:
    return [_bar(datetime(y, m, d, hh, mm, tzinfo=KST))]


# ------------------------------------------------ 확정 시각 규율 (봉 시작이 아니다)


def test_features_use_the_bar_confirm_time_not_the_open_time():
    """30분봉이면 시작과 확정이 30분 차이다 — 판단은 봉이 닫힐 때 내려지므로 확정 시각이
    맞다. 시작 시각을 쓰면 `ev_lunch_flag` 같은 경계 피처가 한 봉씩 밀린다."""
    cal = _calendar()
    # 확정 11:30 → 점심 구간 시작. 봉 시작은 11:00이라 시작 시각을 봤다면 0이 나온다.
    bars = [_bar(datetime(2026, 8, 5, 11, 30, tzinfo=KST), Horizon.M30)]

    assert bars[0].bar_open_kst.hour == 11
    assert bars[0].bar_open_kst.minute == 0
    assert ev.ev_lunch_flag(bars, cal) == 1.0


def test_every_feature_returns_none_on_empty_bars():
    cal = _calendar()
    for _name, fn in ev.CALENDAR_FEATURES:
        assert fn([], cal) is None


# ---------------------------------------------------------------- 시각 인코딩


def test_tod_encoding_uses_a_24h_period_so_open_and_close_are_distinct():
    """세션을 한 주기로 잡으면 개장(위상 0)과 마감(위상 2π)이 같은 점으로 겹쳐 둘을 구분할
    수 없게 된다 — 24시간 주기라야 장중 시각이 서로 다른 점에 놓인다."""
    cal = _calendar()
    at_open = _at(2026, 8, 5, 9, 0)
    at_close = _at(2026, 8, 5, 15, 35)

    assert (ev.ev_tod_sin(at_open, cal), ev.ev_tod_cos(at_open, cal)) != (
        ev.ev_tod_sin(at_close, cal),
        ev.ev_tod_cos(at_close, cal),
    )


def test_tod_sin_cos_known_values_at_06_and_12():
    cal = _calendar()
    # 06:00 = 하루의 1/4 → sin 1, cos 0. 12:00 = 1/2 → sin 0, cos -1.
    six = _at(2026, 8, 5, 6, 0)
    noon = _at(2026, 8, 5, 12, 0)

    assert ev.ev_tod_sin(six, cal) == pytest.approx(1.0)
    assert ev.ev_tod_cos(six, cal) == pytest.approx(0.0, abs=1e-12)
    assert ev.ev_tod_sin(noon, cal) == pytest.approx(0.0, abs=1e-12)
    assert ev.ev_tod_cos(noon, cal) == pytest.approx(-1.0)


def test_open_elapsed_is_zero_at_open_and_one_at_close():
    cal = _calendar()

    assert ev.ev_open_elapsed(_at(2026, 8, 5, 9, 0), cal) == pytest.approx(0.0)
    assert ev.ev_open_elapsed(_at(2026, 8, 5, 15, 35), cal) == pytest.approx(1.0)


def test_open_elapsed_is_negative_before_the_open_and_is_not_clamped():
    """장전(08:45~09:00)은 실제로 다른 국면이다 — 0으로 자르면 `BarSession.PRE_OPEN`을
    만든 이유가 없어진다(2026-07-31 08:45~09:04 20봉 고정가 → 09:05 6.1% 점프 실측)."""
    cal = _calendar()

    assert ev.ev_open_elapsed(_at(2026, 8, 5, 8, 45), cal) < 0


def test_close_remain_is_exactly_complementary_to_open_elapsed():
    """세션 길이가 상수라 둘은 정확히 상보적이다 — 이 사실을 테스트로 고정해 둔다.
    관문 ②가 |ρ|=1로 하나를 떨어뜨릴 것이고, 그게 의도한 동작이다(ev_core 모듈 docstring)."""
    cal = _calendar()
    for hh, mm in ((9, 30), (11, 0), (14, 15)):
        bars = _at(2026, 8, 5, hh, mm)
        assert ev.ev_open_elapsed(bars, cal) + ev.ev_close_remain(bars, cal) == pytest.approx(1.0)


# ---------------------------------------------------------------- 점심 구간 (실측 창)


@pytest.mark.parametrize(
    ("hh", "mm", "expected"),
    [
        (11, 29, 0.0),  # 창 직전
        (11, 30, 1.0),  # 창 시작 — 실측 0.84x
        (12, 20, 1.0),  # 실측 최저 0.65x
        (13, 59, 1.0),
        (14, 0, 0.0),  # 실측 0.95x로 복귀 — 반개구간이라 미포함
        (15, 30, 0.0),  # 거래량은 낮지만(0.48x) 종가단일가 전환이라 성격이 다르다
    ],
)
def test_lunch_flag_matches_the_measured_window(hh, mm, expected):
    assert ev.ev_lunch_flag(_at(2026, 8, 5, hh, mm), _calendar()) == expected


# ---------------------------------------------------------------- 요일 one-hot


def test_dow_one_hot_has_exactly_one_active_column():
    cal = _calendar()
    dow_fns = [(n, f) for n, f in ev.CALENDAR_FEATURES if n.startswith("ev_dow_")]

    assert len(dow_fns) == 5
    # 2026-08-05는 수요일
    values = {n: f(_at(2026, 8, 5), cal) for n, f in dow_fns}
    assert values["ev_dow_wed"] == 1.0
    assert sum(values.values()) == 1.0


def test_dow_one_hot_tracks_the_day():
    cal = _calendar()
    by_name = dict(ev.CALENDAR_FEATURES)

    assert by_name["ev_dow_mon"](_at(2026, 8, 3), cal) == 1.0  # 월
    assert by_name["ev_dow_fri"](_at(2026, 8, 7), cal) == 1.0  # 금
    assert by_name["ev_dow_fri"](_at(2026, 8, 3), cal) == 0.0


# ---------------------------------------------------------------- 만기 D-day


def test_dte_fut_counts_trading_days_to_the_second_thursday():
    """2026-08-13(목)이 8월 둘째 목요일 = 정규월물 만기. 08-05(수)에서 남은 거래일은
    6·7·10·11·12·13 = **6거래일**(주말 8·9 제외)."""
    cal = _calendar()

    assert ev.ev_dte_fut(_at(2026, 8, 5), cal) == 6.0


def test_dte_fut_is_zero_on_the_expiry_day_itself():
    assert ev.ev_dte_fut(_at(2026, 8, 13), _calendar()) == 0.0


def test_dte_fut_rolls_to_the_next_month_after_expiry():
    """만기 다음날(08-14 금)에는 9월 만기(09-10 목)를 본다 — 8월 잔여 거래일
    17·18·19·20·21·24·25·26·27·28·31 + 9월 1~4·7·8·9·10 = 19거래일."""
    cal = _calendar()

    assert ev.ev_dte_fut(_at(2026, 8, 14), cal) == 19.0


def test_dte_fut_skips_holidays_when_counting():
    """휴장일은 세지 않는다 — "잔여 거래일"이 Ver 1.4의 정의다."""
    plain = _calendar()
    with_holiday = _calendar([date(2026, 8, 10), date(2026, 8, 11)])

    assert ev.ev_dte_fut(_at(2026, 8, 5), plain) == 6.0
    assert ev.ev_dte_fut(_at(2026, 8, 5), with_holiday) == 4.0


def test_dte_opt_m_equals_dte_fut_under_the_current_krx_rule():
    """둘 다 둘째 목요일 만기라 현재는 항상 같다 — 관문 ②가 측정해서 하나를 떨어뜨릴
    것이고, 그게 의도한 동작이다(ev_core 모듈 docstring "알려진 중복")."""
    cal = _calendar()
    for day in (5, 12, 13, 14, 20):
        bars = _at(2026, 8, day)
        assert ev.ev_dte_opt_m(bars, cal) == ev.ev_dte_fut(bars, cal)


def test_dte_opt_w_takes_whichever_weekly_comes_first():
    """월위클리(월)·목위클리(목) 중 가까운 쪽. 2026-08-05(수)에서 다음 위클리는 08-06(목)
    → 1거래일. 08-07(금)에서는 08-10(월) → 1거래일."""
    cal = _calendar()

    assert ev.ev_dte_opt_w(_at(2026, 8, 5), cal) == 1.0
    assert ev.ev_dte_opt_w(_at(2026, 8, 7), cal) == 1.0
    assert ev.ev_dte_opt_w(_at(2026, 8, 6), cal) == 0.0  # 목요일 당일


def test_dte_opt_w_pulls_a_holiday_expiry_back_to_the_previous_trading_day():
    """휴장 보정은 `monthly_expiry()`와 **같은 관례**(직전 거래일)를 쓴다 — 그게 KRX 실측으로
    검증된 유일한 관례이기 때문이다(2026-08-04, 7개 월물). 위클리에 다른 관례를 새로 만드는
    것보다 검증된 쪽에 맞추는 편이 근거가 있다.

    08-06(목) 휴장 → 그 위클리는 08-05(수)로 당겨진다. 즉 08-05 당일이 만기라 0."""
    cal = _calendar([date(2026, 8, 6)])

    assert ev.ev_dte_opt_w(_at(2026, 8, 5), cal) == 0.0


def test_no_thursday_weekly_is_listed_in_the_monthly_expiry_week():
    """**마흐디 2026-07-10 실측** — KRX는 먼슬리 만기 주의 목요일에 위클리(목)을 별도 상장하지
    않고 먼슬리가 그 역할을 대신한다(`mahdi/data/symbol_master.py` L/M 주석,
    `dashboard/panels/expiry_liquidity_panel.py::_is_monthly_expiry_week`).

    이걸 모르면 먼슬리 만기일(2026-08-13)에 `ev_dte_opt_w`가 0을 내며 "오늘 위클리도 만기"라고
    주장한다 — 2026-08-04 마흐디 조사 중 실제로 발견한 결함이다. 합성 달력에서 다음 위클리는
    08-17(월)이고 잔여 거래일은 14·17 = 2다."""
    cal = _calendar()

    assert cal.monthly_expiry(2026, 8) == date(2026, 8, 13)
    assert cal.has_thursday_weekly(date(2026, 8, 13)) is False
    assert cal.has_thursday_weekly(date(2026, 8, 6)) is True  # 만기 주 아님 — 상장된다
    assert ev.ev_dte_opt_w(_at(2026, 8, 13), cal) == 2.0


def test_the_monthly_expiry_week_exclusion_covers_the_whole_week_not_just_thursday():
    """먼슬리 만기가 휴장으로 수요일로 당겨져도 **그 주 전체**가 먼슬리 만기 주다 —
    마흐디 패널과 같은 ISO 주 기준."""
    cal = _calendar([date(2026, 8, 13)])  # 둘째 목요일 휴장 → 만기가 08-12(수)로 당겨진다

    assert cal.monthly_expiry(2026, 8) == date(2026, 8, 12)
    assert cal.has_thursday_weekly(date(2026, 8, 13)) is False  # 같은 ISO 주


def test_dte_features_are_none_when_the_holiday_file_lacks_the_year():
    """달력 미갱신은 **조용히 틀린 D-day**보다 NaN이 낫다 — 연말에 EV가 통째로 NaN이 되면
    그건 `configs/krx_holidays.yaml`을 갱신하라는 신호다."""
    cal = _calendar(years={2026})
    bars = _at(2026, 12, 30)

    with pytest.raises(ValueError, match="휴장일 데이터 없음"):
        ev.ev_dte_fut(bars, cal)  # 엔진의 `_safe_call`이 이걸 None으로 마킹한다


# ---------------------------------------------------------------- 만기 플래그·롤오버


def test_expiry_flag_grades_weekly_monthly_and_quadruple_witching():
    """이진 플래그로 두면 동시만기를 평범한 위클리와 같은 값으로 뭉갠다."""
    cal = _calendar()

    assert ev.ev_expiry_flag(_at(2026, 8, 5), cal) == 0.0  # 수요일 — 만기 아님
    assert ev.ev_expiry_flag(_at(2026, 8, 6), cal) == 1.0  # 목위클리
    assert ev.ev_expiry_flag(_at(2026, 8, 13), cal) == 2.0  # 8월 먼스리
    assert ev.ev_expiry_flag(_at(2026, 9, 10), cal) == 3.0  # 9월 = 동시만기


def test_expiry_flag_is_zero_on_a_holiday():
    cal = _calendar([date(2026, 8, 6)])

    assert ev.ev_expiry_flag(_at(2026, 8, 6), cal) == 0.0


def test_rollover_window_opens_five_trading_days_before_expiry():
    cal = _calendar()

    assert ev.ev_dte_fut(_at(2026, 8, 5), cal) == 6.0
    assert ev.ev_rollover_win(_at(2026, 8, 5), cal) == 0.0  # D-6 — 아직
    assert ev.ev_rollover_win(_at(2026, 8, 6), cal) == 1.0  # D-5 — 개시
    assert ev.ev_rollover_win(_at(2026, 8, 13), cal) == 1.0  # 만기 당일도 활성


# ---------------------------------------------------------------- 연휴 인접


def test_holiday_adj_is_zero_midweek():
    assert ev.ev_holiday_adj(_at(2026, 8, 5), _calendar()) == 0.0  # 수요일


def test_holiday_adj_is_signed_by_direction_and_sized_by_length():
    """연휴 **직전**은 포지션을 들고 갈지 정하는 시점이고 **직후**는 갭이 이미 실현된
    시점이다 — 하나의 플래그로 뭉치면 정반대 국면이 같은 값을 받는다."""
    cal = _calendar()

    assert ev.ev_holiday_adj(_at(2026, 8, 7), cal) == 2.0  # 금 → 다음 거래일 월(+3일)
    assert ev.ev_holiday_adj(_at(2026, 8, 10), cal) == -2.0  # 월 → 직전 거래일 금


def test_holiday_adj_grows_with_a_longer_break():
    cal = _calendar([date(2026, 8, 10), date(2026, 8, 11)])  # 월·화 휴장

    assert ev.ev_holiday_adj(_at(2026, 8, 7), cal) == 4.0  # 금 → 수(+5일)
    assert ev.ev_holiday_adj(_at(2026, 8, 12), cal) == -4.0  # 수 → 직전 금


def test_holiday_adj_prefers_the_upcoming_break_when_a_day_sits_between_two():
    """앞으로 질 리스크가 이미 지나간 것보다 판단에 더 관계있다(ev_core docstring)."""
    cal = _calendar([date(2026, 8, 6), date(2026, 8, 10), date(2026, 8, 11)])

    assert ev.ev_holiday_adj(_at(2026, 8, 7), cal) == 4.0  # 직전(목 휴장)이 아니라 직후를 우선


# ---------------------------------------------------------------- 레지스트리


def test_registry_shape_and_excluded_features_are_declared():
    """스코프 밖 3개를 상수로 남겨야 "빠뜨린 것"과 "일부러 뺀 것"이 구분된다."""
    names = [n for n, _ in ev.CALENDAR_FEATURES]

    assert len(names) == 16  # 기저 12개, 요일 one-hot이 5컬럼
    assert len(set(names)) == 16
    assert ev.EXCLUDED_FEATURES == (
        "ev_econ_prox",
        "ev_econ_grade",
        "ev_overnight_gap_risk",
    )
    assert not set(names) & set(ev.EXCLUDED_FEATURES)


def test_every_registered_feature_produces_a_value_on_a_normal_trading_day():
    """하나라도 None이면 그 피처는 프로덕션에서 죽은 채로 학습된다(`px_ema_cross_60` 교훈)."""
    cal = _calendar()
    bars = _at(2026, 8, 5, 10, 30)

    dead = [name for name, fn in ev.CALENDAR_FEATURES if fn(bars, cal) is None]

    assert not dead, f"정상 거래일에 값을 못 내는 EV 피처: {dead}"
