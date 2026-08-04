import math
import random
from datetime import datetime, timedelta

import pytest

from messiah.core.messages import BarClosed, Horizon
from messiah.core.timeutil import KST
from messiah.features import px_core as px


def _bars(rows: list[tuple[float, float, float, float, int]]) -> list[BarClosed]:
    """rows: (open, high, low, close, volume) — 순차 1분봉으로 변환."""
    return [
        BarClosed(
            symbol="TEST",
            horizon=Horizon.M1,
            bar_open_kst=datetime(2026, 7, 23, 9, 30, tzinfo=KST) + timedelta(minutes=i),
            o_ticks=int(o),
            h_ticks=int(h),
            l_ticks=int(lo),
            c_ticks=int(c),
            volume=v,
            quality_ok=True,
        )
        for i, (o, h, lo, c, v) in enumerate(rows)
    ]


def _flat_bars(closes: list[float]) -> list[BarClosed]:
    """OHLC를 전부 close로 통일한 단순 봉 시퀀스(모멘텀·회귀 계열 테스트용)."""
    return _bars([(c, c, c, c, 10) for c in closes])


# ---------------------------------------------------------------- 손으로 검산한 값들


def test_px_ret_log_return_over_window():
    bars = _flat_bars([100, 100, 100, 100, 100, 110])
    assert px.px_ret(bars, 5) == pytest.approx(0.09531017980432493)


def test_px_mom_simple_return_over_window():
    bars = _flat_bars([100, 100, 100, 100, 100, 110])
    assert px.px_mom(bars, 5) == pytest.approx(0.10000000000000009)


def test_px_accel_momentum_change():
    bars = _flat_bars([100, 105, 110, 120, 140])
    assert px.px_accel(bars, 2) == pytest.approx(-0.1272727272727272)


def test_px_zscore():
    bars = _flat_bars([10, 12, 11, 13, 12])
    assert px.px_zscore(bars, 5) == pytest.approx(0.39223227027636837)


def test_px_bb_pos_and_width():
    bars = _flat_bars([10, 12, 11, 13, 12])
    assert px.px_bb_pos(bars, 5) == pytest.approx(0.19611613513818418)
    assert px.px_bb_width(bars, 5) == pytest.approx(0.35165651817881277)


def test_px_stoch_and_don_pos_match():
    bars = _bars(
        [
            (10, 15, 9, 10, 10),
            (10, 16, 10, 12, 10),
            (10, 14, 9, 11, 10),
            (10, 17, 11, 13, 10),
            (10, 15, 10, 12, 10),
        ]
    )
    assert px.px_stoch(bars, 5) == pytest.approx(0.375)
    assert px.px_don_pos(bars, 5) == pytest.approx(0.375)


def test_px_high_dist_and_low_dist():
    # window=4 → ATR은 5개 봉(4개 TR) 사용, 고저는 마지막 4개 봉만 사용(1번째 봉 제외) —
    # 이 데이터셋은 우연히 최댓값/최솟값이 뒤 4개 안에도 있어 window=5로 계산한 손 검산과 일치
    bars = _bars(
        [
            (10, 15, 9, 10, 10),
            (10, 16, 10, 12, 10),
            (10, 14, 9, 11, 10),
            (10, 17, 11, 13, 10),
            (10, 15, 10, 12, 10),
        ]
    )
    assert px.px_high_dist(bars, 4) == pytest.approx(0.9090909090909091)
    assert px.px_low_dist(bars, 4) == pytest.approx(0.5454545454545454)


def test_px_dd_and_runup():
    bars = _flat_bars([10, 12, 11, 13, 9])
    assert px.px_dd(bars, 5) == pytest.approx(-0.3076923076923077)
    assert px.px_runup(bars, 5) == pytest.approx(0.0)


def test_px_max_ret():
    bars = _flat_bars([10, 12, 11, 13, 9])
    assert px.px_max_ret(bars, 4) == pytest.approx(0.19999999999999996)


# ---------------------------------------------------------------- 방향성/정합성 검증


def test_px_rsi_strongly_uptrending_is_high():
    bars = _flat_bars([100 + i for i in range(21)])  # 단조 증가
    assert px.px_rsi(bars, 20) == pytest.approx(100.0)


def test_px_rsi_strongly_downtrending_is_low():
    bars = _flat_bars([100 - i for i in range(21)])
    assert px.px_rsi(bars, 20) == pytest.approx(0.0)


def test_px_rsi_flat_is_neutral():
    bars = _flat_bars([100] * 21)
    assert px.px_rsi(bars, 20) == pytest.approx(50.0)


def test_px_trend_slope_positive_and_r2_near_one_for_clean_uptrend():
    bars = _flat_bars([100 + 2 * i for i in range(21)])
    assert px.px_trend_slope(bars, 20) > 0
    assert px.px_trend_r2(bars, 20) == pytest.approx(1.0, abs=1e-6)


def test_px_adx_higher_for_trending_than_sideways():
    trending = _bars([(c, c + 1, c - 1, c, 10) for c in (100 + 3 * i for i in range(21))])
    sideways = _bars([(100, 101, 99, 100, 10) for _ in range(21)])
    trend_adx = px.px_adx(trending, 20)
    side_adx = px.px_adx(sideways, 20)
    assert trend_adx > side_adx


def test_px_hurst_neutral_when_insufficient_history():
    bars = _flat_bars([100 + i for i in range(10)])
    assert px.px_hurst(bars, 10) == 0.5


def test_px_hurst_trending_higher_than_mean_reverting():
    # 소표본 R/S 추정치는 절대 0.5 기준보다 편향되기 쉬움(짧은 시계열의 알려진 한계) —
    # 추세 vs 평균회귀의 상대 비교로 검증(mahdi hurst_exponent 테스트와 동일 방식).
    rng = random.Random(42)
    trending = [100 + 0.5 * i + rng.uniform(-0.3, 0.3) for i in range(65)]
    rng2 = random.Random(7)
    mean_reverting = [100 + 3 * ((-1) ** i) + rng2.uniform(-0.5, 0.5) for i in range(65)]

    h_trend = px.px_hurst(_flat_bars(trending), 60)
    h_mean_revert = px.px_hurst(_flat_bars(mean_reverting), 60)
    assert h_trend > h_mean_revert


def test_px_ema_dev_positive_when_price_above_average():
    bars = _flat_bars([100] * 20 + [200])  # 마지막 봉이 평균보다 훨씬 위(ATR 계산에 21개 필요)
    assert px.px_ema_dev(bars, 20) > 0


def test_px_ema_cross_sign_matches_trend_direction():
    up = _flat_bars([100 + i for i in range(61)])
    assert px.px_ema_cross(up, 20) == 1.0


def test_px_macd_h_positive_for_accelerating_uptrend():
    bars = _flat_bars([100 + i**1.3 for i in range(50)])
    assert px.px_macd_h(bars, 12) > 0


def test_px_macd_h_is_not_identically_zero_at_the_smallest_registered_window():
    """2026-08-04 회귀(F0-3 관문 첫 실행에서 발견) — `window=5`는 `5//3=1`이고
    `_ema_series(x, 1)`은 k=1이라 EMA가 항등이 된다. 그래서 히스토그램
    `macd[-1] - signal[-1]`이 **모든 봉에서 정확히 0**이었고, 값이 나오므로 `nan_ratio`에
    아무 흔적도 안 남아 무결성 리포트로는 영원히 안 보였다.

    `px_ema_cross_60`(NaN이라 흔적이 남았던 사고)과 같은 종류인데 검출 수단이 달랐다 —
    그래서 관문이 필요했다. W_STD의 최솟값 5는 실제 등록된 윈도우다.
    """
    bars = _flat_bars([100 + i**1.3 for i in range(60)])

    values = [px.px_macd_h(bars[: i + 1], 5) for i in range(20, 60)]
    computed = [v for v in values if v is not None]

    assert computed, "window=5에서 값이 하나도 안 나온다"
    assert any(v != 0.0 for v in computed), "px_macd_h_5가 여전히 항상 0이다"


def test_px_breakout_detects_new_high():
    bars = _flat_bars([100, 101, 99, 100, 101] + [150])
    assert px.px_breakout(bars, 5) > 0


def test_px_breakout_zero_within_range():
    bars = _flat_bars([100, 101, 99, 102, 98, 100])
    assert px.px_breakout(bars, 5) == 0.0


def test_px_autocorr_returns_none_with_too_little_data():
    bars = _flat_bars([100, 101])
    assert px.px_autocorr(bars, 5) is None


def test_px_skew_and_kurt_return_float_for_enough_data():
    bars = _flat_bars([100 + (i % 3) * (i**0.5) for i in range(25)])
    assert isinstance(px.px_skew_r(bars, 20), float)
    assert isinstance(px.px_kurt_r(bars, 20), float)


def test_px_vwap_dev_uses_volume_weighted_typical_price():
    # 마지막 봉의 종가가 window 내 typical price 평균보다 위 → 양수
    bars = _bars([(100, 102, 98, 100, 10) for _ in range(20)] + [(150, 152, 148, 150, 10)])
    assert px.px_vwap_dev(bars, 20) > 0


# ---------------------------------------------------------------- 세션 상태형 4개


def test_session_state_tracks_open_high_low_within_day():
    session = px.SessionState()
    bars = _bars(
        [
            (100, 105, 98, 102, 10),
            (102, 110, 101, 108, 10),
            (108, 109, 95, 100, 10),
        ]
    )
    for b in bars:
        session.on_bar(b)
    assert session.session_open_ticks == 100
    assert session.session_high_ticks == 110
    assert session.session_low_ticks == 95
    assert px.px_range_pos_d(bars, session) == pytest.approx((100 - 95) / (110 - 95))
    assert px.px_open_ret(bars, session) == pytest.approx(math.log(100 / 100))


def test_session_state_rolls_over_to_new_day_and_tracks_prev_close():
    session = px.SessionState()
    day1 = [
        BarClosed(
            symbol="TEST",
            horizon=Horizon.M1,
            bar_open_kst=datetime(2026, 7, 22, 9, 30, tzinfo=KST),
            o_ticks=100,
            h_ticks=105,
            l_ticks=98,
            c_ticks=103,
            volume=10,
            quality_ok=True,
        )
    ]
    day2 = [
        BarClosed(
            symbol="TEST",
            horizon=Horizon.M1,
            bar_open_kst=datetime(2026, 7, 23, 9, 0, tzinfo=KST),
            o_ticks=110,
            h_ticks=112,
            l_ticks=108,
            c_ticks=111,
            volume=10,
            quality_ok=True,
        )
    ]
    session.on_bar(day1[0])
    session.on_bar(day2[0])

    assert session.prev_day_close_ticks == 103
    assert session.session_open_ticks == 110  # 새 세션으로 리셋됨
    assert px.px_gap_open(day2, session) == pytest.approx(math.log(110 / 103))


def test_px_gap_open_none_on_first_session():
    session = px.SessionState()
    bars = _bars([(100, 105, 98, 102, 10)])
    session.on_bar(bars[0])
    assert px.px_gap_open(bars, session) is None  # 전일 종가 없음


def test_px_round_dist_uses_fixed_round_ticks():
    # 250틱 간격 근사 라운드 레벨 — close가 정확히 라운드 레벨이면 거리 0
    closes = [1000 + (i % 3) for i in range(21)]  # 워밍업용(ATR 계산에 변동 필요)
    closes[-1] = 250 * 4  # 정확히 라운드 레벨
    bars = _flat_bars(closes)
    session = px.SessionState()
    assert px.px_round_dist(bars, session) == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------- 워밍업 부족 → None


@pytest.mark.parametrize(
    "name,fn",
    [(n, f) for n, f, _ in px.WINDOWED_FEATURES if n != "px_adx"],  # px_adx는 중립값 반환이 계약
)
def test_windowed_feature_returns_none_when_warmup_insufficient(name, fn):
    bars = _flat_bars([100, 101])  # 어떤 W_STD/W_SLOW 윈도우에도 못 미침
    assert fn(bars, 60) is None, f"{name} should return None on insufficient warmup"
