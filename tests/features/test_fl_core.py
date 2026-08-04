from datetime import date, datetime, timedelta

from messiah.core.messages import BarClosed, Horizon
from messiah.core.timeutil import KST
from messiah.data.investor_flow_history import FlowHistory, FlowRow
from messiah.features import fl_core

_FIELDS = ("frgn_ntby_qty", "frgn_ntby_tr_pbmn", "prsn_ntby_qty",
           "prsn_ntby_tr_pbmn", "orgn_ntby_qty", "orgn_ntby_tr_pbmn")


def _flow(series: list[tuple[str, int, int]]) -> FlowHistory:
    """(YYYYMMDD, 외국인, 기관) → FlowHistory. 개인은 둘의 반대로 채운다."""
    rows = []
    for day, frgn, orgn in series:
        d = datetime.strptime(day, "%Y%m%d").date()  # noqa: DTZ007
        vals = {
            "frgn_ntby_qty": float(frgn),
            "frgn_ntby_tr_pbmn": float(frgn * 10),
            "orgn_ntby_qty": float(orgn),
            "orgn_ntby_tr_pbmn": float(orgn * 10),
            "prsn_ntby_qty": float(-(frgn + orgn)),
            "prsn_ntby_tr_pbmn": float(-(frgn + orgn) * 10),
        }
        rows.append(FlowRow(day=d, values=vals))
    return FlowHistory(rows)


def _bars_on(day: date, n: int = 3) -> list[BarClosed]:
    return [
        BarClosed(
            symbol="X", horizon=Horizon.M5,
            bar_open_kst=datetime(day.year, day.month, day.day, 9, 0, tzinfo=KST)
            + timedelta(minutes=5 * i),
            o_ticks=100, h_ticks=105, l_ticks=95, c_ticks=100 + i, volume=10,
        )
        for i in range(n)
    ]


def _varied(n: int, base: int = 1000) -> list[tuple[str, int, int]]:
    """부호가 섞인 n일치 — 표준편차가 0이 아니게."""
    return [
        (f"202606{i + 1:02d}", base * (1 if i % 3 else -2), base * (-1 if i % 2 else 1))
        for i in range(n)
    ]


# ---------------------------------------------------------------- 미래 참조


def test_features_use_only_days_before_the_bar():
    """**가장 중요** — 그날 순매수는 장 마감 후에야 확정된다. 오늘 값이 오늘 봉의 피처에
    들어가면 미래 참조이고, 백테스트만 좋아진다."""
    flow = _flow(_varied(21) + [("20260701", 999_999, 999_999)])
    bars = _bars_on(date(2026, 7, 1))

    z_with_today = fl_core.fl_frgn_ntby_z(bars, flow)
    z_without = fl_core.fl_frgn_ntby_z(bars, _flow(_varied(21)))

    assert z_with_today == z_without, "당일 수급이 피처에 샜다"


# ---------------------------------------------------------------- z-score


def test_zscore_is_none_when_history_is_flat():
    """표준편차 0이면 정의 불가 — 0으로 채우면 '평균과 같다'는 없는 사실을 주장한다."""
    flat = _flow([(f"202606{i + 1:02d}", 500, 500) for i in range(10)])

    assert fl_core.fl_frgn_ntby_z(_bars_on(date(2026, 6, 20)), flat) is None


def test_zscore_is_none_without_enough_history():
    assert fl_core.fl_frgn_ntby_z(_bars_on(date(2026, 6, 2)), _flow(_varied(1))) is None


def test_zscore_positive_when_last_day_is_above_recent_mean():
    flow = _flow([(f"202606{i + 1:02d}", 100, 0) for i in range(10)]
                 + [("20260611", 5000, 0)])

    z = fl_core.fl_frgn_ntby_z(_bars_on(date(2026, 6, 12)), flow)

    assert z is not None and z > 1.0


# ---------------------------------------------------------------- 연속(streak)


def test_streak_counts_consecutive_same_sign_days_with_sign():
    flow = _flow([("20260601", -100, 0), ("20260602", 100, 0),
                  ("20260603", 200, 0), ("20260604", 300, 0)])

    assert fl_core.fl_frgn_streak(_bars_on(date(2026, 6, 5)), flow) == 3.0


def test_streak_is_negative_for_selling_runs():
    flow = _flow([("20260601", 100, 0), ("20260602", -50, 0), ("20260603", -70, 0)])

    assert fl_core.fl_frgn_streak(_bars_on(date(2026, 6, 4)), flow) == -2.0


def test_streak_breaks_on_zero_day():
    """0인 날은 흐름의 단절 — 연속을 이어주지 않는다."""
    flow = _flow([("20260601", 100, 0), ("20260602", 0, 0), ("20260603", 100, 0)])

    assert fl_core.fl_frgn_streak(_bars_on(date(2026, 6, 4)), flow) == 1.0


def test_streak_is_capped():
    flow = _flow([(f"202606{i + 1:02d}", 100, 0) for i in range(20)])

    assert abs(fl_core.fl_frgn_streak(_bars_on(date(2026, 6, 25)), flow)) <= fl_core.STREAK_CAP


# ---------------------------------------------------------------- 동조 여부


def test_agree_is_plus_one_when_both_buy_and_minus_one_when_split():
    both = _flow(_varied(5) + [("20260610", 500, 700)])
    split = _flow(_varied(5) + [("20260610", 500, -700)])
    bars = _bars_on(date(2026, 6, 11))

    assert fl_core.fl_frgn_orgn_agree(bars, both) == 1.0
    assert fl_core.fl_frgn_orgn_agree(bars, split) == -1.0


def test_agree_is_zero_when_either_side_is_flat():
    flow = _flow(_varied(5) + [("20260610", 0, 700)])

    assert fl_core.fl_frgn_orgn_agree(_bars_on(date(2026, 6, 11)), flow) == 0.0


# ---------------------------------------------------------------- 계약


def test_all_registered_features_produce_values_with_enough_history():
    flow = _flow(_varied(30))
    bars = _bars_on(date(2026, 7, 1))

    missing = [name for name, fn in fl_core.FLOW_FEATURES if fn(bars, flow) is None]

    assert not missing, f"충분한 이력에도 값이 안 나오는 FL 피처: {missing}"


def test_all_features_return_none_without_history():
    empty = FlowHistory([])
    bars = _bars_on(date(2026, 7, 1))

    assert all(fn(bars, empty) is None for _, fn in fl_core.FLOW_FEATURES)


def test_all_features_return_none_without_bars():
    flow = _flow(_varied(30))

    assert all(fn([], flow) is None for _, fn in fl_core.FLOW_FEATURES)
