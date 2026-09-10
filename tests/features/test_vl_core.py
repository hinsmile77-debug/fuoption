import math
import statistics
from datetime import datetime, timedelta

import pytest

from messiah.core.messages import BarClosed, Horizon
from messiah.core.timeutil import KST
from messiah.features import px_core
from messiah.features.px_core import W_STD
from messiah.features.px_core import atr as px_atr
from messiah.features.vl_core import (
    vl_atr,
    vl_atr_rel,
    vl_gk,
    vl_jump,
    vl_park,
    vl_range_exp,
    vl_rv,
    vl_semi_dn,
    vl_semi_ratio,
    vl_semi_up,
    vl_squeeze,
    vl_vol_ratio,
    vl_vov,
    vl_yz,
)

_SYMBOL = "TEST"
_START = datetime(2026, 7, 27, 9, 0, tzinfo=KST)


def _bars_from_closes(closes: list[float]) -> list[BarClosed]:
    return [
        BarClosed(
            symbol=_SYMBOL,
            horizon=Horizon.M30,
            bar_open_kst=_START + timedelta(minutes=30 * i),
            o_ticks=round(c),
            h_ticks=round(c) + 1,
            l_ticks=round(c) - 1,
            c_ticks=round(c),
            volume=10,
        )
        for i, c in enumerate(closes)
    ]


def _bars_from_ohlc(ohlc: list[tuple[float, float, float, float]]) -> list[BarClosed]:
    return [
        BarClosed(
            symbol=_SYMBOL,
            horizon=Horizon.M30,
            bar_open_kst=_START + timedelta(minutes=30 * i),
            o_ticks=round(o),
            h_ticks=round(h),
            l_ticks=round(low),
            c_ticks=round(c),
            volume=10,
        )
        for i, (o, h, low, c) in enumerate(ohlc)
    ]


def test_vl_vol_ratio_exactly_one_for_symmetric_alternating_series():
    # 100→105→100→...(11개 종가, 10개 로그수익률, 등폭 교대) — slow(10)/fast(4) 둘 다
    # 짝수라 교대 패턴 안에서 +/- 정확히 반반씩 걸려 평균이 0, 표준편차가 양쪽 다 동일해진다
    # (손으로 검증 가능한 유일하게 "깨끗한" 배치 — 모듈 docstring 참고).
    closes = [100.0]
    for i in range(10):
        closes.append(closes[-1] * 1.05 if i % 2 == 0 else closes[-1] / 1.05)
    bars = _bars_from_closes(closes)

    ratio = vl_vol_ratio(bars, fast_window=4, slow_window=10)

    assert ratio == pytest.approx(1.0, abs=1e-9)


def test_vl_vol_ratio_above_one_when_recent_volatility_spikes():
    # 정수 등락(100/101 교대)은 반올림해도 손실이 없어 "거의 무변동" 구간을 정확히 표현.
    quiet = [100.0 if i % 2 == 0 else 101.0 for i in range(17)]
    wild = [quiet[-1]]
    for i in range(5):
        wild.append(wild[-1] * 1.5 if i % 2 == 0 else wild[-1] / 1.5)  # 최근 급변동
    bars = _bars_from_closes(quiet + wild[1:])

    ratio = vl_vol_ratio(bars, fast_window=5, slow_window=20)

    assert ratio is not None
    assert ratio > 1.5


def test_vl_vol_ratio_below_one_when_recent_volatility_calms():
    wild = [100.0]
    for i in range(16):
        wild.append(wild[-1] * 1.3 if i % 2 == 0 else wild[-1] / 1.3)
    quiet = [wild[-1] if i % 2 == 0 else wild[-1] + 1.0 for i in range(5)]
    bars = _bars_from_closes(wild + quiet)

    ratio = vl_vol_ratio(bars, fast_window=5, slow_window=20)

    assert ratio is not None
    assert ratio < 0.5


def test_vl_vol_ratio_insufficient_bars_returns_none():
    bars = _bars_from_closes([100.0, 101.0, 100.0])
    assert vl_vol_ratio(bars, fast_window=1, slow_window=20) is None


def test_vl_vol_ratio_zero_slow_volatility_returns_none():
    bars = _bars_from_closes([100.0] * 25)  # 전 구간 무변동
    assert vl_vol_ratio(bars, fast_window=5, slow_window=20) is None


def test_vl_vol_ratio_rejects_fast_window_larger_than_slow_window():
    bars = _bars_from_closes([100.0] * 25)
    with pytest.raises(ValueError):
        vl_vol_ratio(bars, fast_window=21, slow_window=20)


def test_vl_vol_ratio_default_windows_match_px_core_w_std_first_two_values():
    # 기본값이 문서화된 대로인지(모듈 docstring: px_core.W_STD=(5,20,60) 앞 두 값 재사용,
    # 60은 30분봉 기준 워밍업이 30시간이라 제외) 회귀 확인.
    closes = [100.0]
    for i in range(20):
        closes.append(closes[-1] * 1.02 if i % 2 == 0 else closes[-1] / 1.02)
    bars = _bars_from_closes(closes)

    default_ratio = vl_vol_ratio(bars)
    explicit_ratio = vl_vol_ratio(bars, fast_window=W_STD[0], slow_window=W_STD[1])
    assert default_ratio == pytest.approx(explicit_ratio)


def test_vl_vol_ratio_pure_python_cross_check():
    """모듈 구현과 별개로 손으로 다시 계산해 대조 — 순수 사인파가 아닌 비대칭 배치에서도
    맞는지 확인."""
    closes = [100.0, 102.0, 99.0, 103.0, 98.0, 104.0, 97.0, 105.0, 96.0, 106.0, 95.0]
    bars = _bars_from_closes(closes)

    log_rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    expected_slow = statistics.pstdev(log_rets)
    expected_fast = statistics.pstdev(log_rets[-4:])
    expected_ratio = expected_fast / expected_slow

    ratio = vl_vol_ratio(bars, fast_window=4, slow_window=10)
    assert ratio == pytest.approx(expected_ratio)


# ---------------------------------------------------------------- vl_rv


def test_vl_rv_pure_python_cross_check():
    closes = [100.0, 102.0, 99.0, 103.0, 98.0, 104.0]
    bars = _bars_from_closes(closes)
    log_rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    expected = math.sqrt(sum(r * r for r in log_rets))
    assert vl_rv(bars, window=5) == pytest.approx(expected)


def test_vl_rv_insufficient_bars_returns_none():
    bars = _bars_from_closes([100.0, 101.0, 100.0])
    assert vl_rv(bars, window=5) is None


# ---------------------------------------------------------------- vl_park


def test_vl_park_pure_python_cross_check():
    ohlc = [(100, 105, 95, 101), (101, 104, 98, 100), (100, 110, 90, 103)]
    bars = _bars_from_ohlc(ohlc)
    terms = [math.log(h / low) ** 2 for _, h, low, _ in ohlc]
    expected = math.sqrt(statistics.fmean(terms) / (4 * math.log(2)))
    assert vl_park(bars, window=3) == pytest.approx(expected)


def test_vl_park_insufficient_bars_returns_none():
    bars = _bars_from_ohlc([(100, 105, 95, 101)])
    assert vl_park(bars, window=3) is None


# ---------------------------------------------------------------- vl_gk


def test_vl_gk_pure_python_cross_check():
    ohlc = [(100, 105, 95, 101), (101, 104, 98, 100), (100, 110, 90, 103)]
    bars = _bars_from_ohlc(ohlc)
    terms = [
        0.5 * math.log(h / low) ** 2 - (2 * math.log(2) - 1) * math.log(c / o) ** 2
        for o, h, low, c in ohlc
    ]
    expected = math.sqrt(statistics.fmean(terms))
    assert vl_gk(bars, window=3) == pytest.approx(expected)


def test_vl_gk_insufficient_bars_returns_none():
    bars = _bars_from_ohlc([(100, 105, 95, 101)])
    assert vl_gk(bars, window=3) is None


# ---------------------------------------------------------------- vl_yz


def test_vl_yz_pure_python_cross_check():
    # 4개 원본 봉 -> window=3 (오버나이트 3쌍)
    ohlc = [
        (100, 103, 98, 101),
        (102, 106, 99, 104),
        (103, 105, 100, 102),
        (101, 108, 97, 106),
    ]
    bars = _bars_from_ohlc(ohlc)
    window = 3

    overnight, oc, rs = [], [], []
    for (_, _, _, prev_c), (o, h, low, c) in zip(ohlc, ohlc[1:]):
        overnight.append(math.log(o / prev_c))
        oc.append(math.log(c / o))
        rs.append(math.log(h / o) * math.log(h / c) + math.log(low / o) * math.log(low / c))
    v_o = statistics.pvariance(overnight)
    v_c = statistics.pvariance(oc)
    v_rs = statistics.fmean(rs)
    k = 0.34 / (1.34 + (window + 1) / (window - 1))
    expected = math.sqrt(v_o + k * v_c + (1 - k) * v_rs)

    assert vl_yz(bars, window=window) == pytest.approx(expected)


def test_vl_yz_rejects_window_below_two():
    bars = _bars_from_ohlc([(100, 103, 98, 101)] * 5)
    assert vl_yz(bars, window=1) is None


def test_vl_yz_insufficient_bars_returns_none():
    bars = _bars_from_ohlc([(100, 103, 98, 101), (102, 106, 99, 104)])
    assert vl_yz(bars, window=3) is None


# ---------------------------------------------------------------- vl_atr / vl_atr_rel


def test_vl_atr_matches_px_core_atr():
    bars = _bars_from_closes([100.0, 102.0, 99.0, 103.0, 98.0, 104.0, 97.0])
    assert vl_atr(bars, window=5) == px_atr(bars, window=5)


def test_vl_atr_rel_is_atr_over_close():
    bars = _bars_from_closes([100.0, 102.0, 99.0, 103.0, 98.0, 104.0, 97.0])
    atr_value = px_atr(bars, window=5)
    expected = atr_value / bars[-1].c_ticks
    assert vl_atr_rel(bars, window=5) == pytest.approx(expected)


def test_vl_atr_rel_none_when_atr_none():
    bars = _bars_from_closes([100.0, 101.0])
    assert vl_atr_rel(bars, window=5) is None


# ---------------------------------------------------------------- vl_semi_dn / up / ratio


def test_vl_semi_dn_up_known_value():
    # 등락 교대(+5%/-5%가 아니라 로그수익률로 비대칭 설계): 상승 2회, 하락 3회
    closes = [100.0, 110.0, 90.0, 80.0, 88.0, 70.0]
    bars = _bars_from_closes(closes)
    log_rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    expected_dn = math.sqrt(sum(r * r for r in log_rets if r < 0))
    expected_up = math.sqrt(sum(r * r for r in log_rets if r > 0))

    assert vl_semi_dn(bars, window=5) == pytest.approx(expected_dn)
    assert vl_semi_up(bars, window=5) == pytest.approx(expected_up)
    assert vl_semi_ratio(bars, window=5) == pytest.approx(expected_dn / expected_up)


def test_vl_semi_dn_zero_when_no_downside():
    closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]  # 계속 상승만
    bars = _bars_from_closes(closes)
    assert vl_semi_dn(bars, window=5) == pytest.approx(0.0)


def test_vl_semi_ratio_none_when_no_upside():
    closes = [105.0, 104.0, 103.0, 102.0, 101.0, 100.0]  # 계속 하락만 -> 상방 준분산 0
    bars = _bars_from_closes(closes)
    assert vl_semi_ratio(bars, window=5) is None


def test_vl_semi_insufficient_bars_returns_none():
    bars = _bars_from_closes([100.0, 101.0])
    assert vl_semi_dn(bars, window=5) is None
    assert vl_semi_up(bars, window=5) is None
    assert vl_semi_ratio(bars, window=5) is None


# ---------------------------------------------------------------- vl_jump


def test_vl_jump_pure_python_cross_check():
    closes = [100.0, 102.0, 99.0, 103.0, 98.0, 104.0]
    bars = _bars_from_closes(closes)
    log_rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    window = 5
    rv_variance = sum(r * r for r in log_rets)
    bv = (
        (math.pi / 2)
        * (window / (window - 1))
        * sum(abs(a) * abs(b) for a, b in zip(log_rets, log_rets[1:]))
    )
    expected = max(rv_variance - bv, 0.0)
    assert vl_jump(bars, window=window) == pytest.approx(expected)


def test_vl_jump_rejects_window_below_two():
    bars = _bars_from_closes([100.0, 101.0, 102.0])
    assert vl_jump(bars, window=1) is None


def test_vl_jump_higher_with_injected_outlier_return():
    smooth = [100.0]
    for i in range(10):
        smooth.append(smooth[-1] * 1.01 if i % 2 == 0 else smooth[-1] / 1.01)
    with_outlier = smooth[:-1] + [smooth[-2] * 1.5]  # 마지막 수익률만 큰 점프로 교체

    jump_smooth = vl_jump(_bars_from_closes(smooth), window=10)
    jump_outlier = vl_jump(_bars_from_closes(with_outlier), window=10)
    assert jump_outlier > jump_smooth


def _m10_bars(closes: list[float]) -> list[BarClosed]:
    """10m 지평 실물 배치 — 한 거래일 39봉(09:00~15:20), 넘치면 다음 거래일로 넘어간다.

    `vl_jump_60`의 창(60봉)은 한 세션(39봉)보다 길어 **매일** 세션 경계를 넘는다. 그래서
    이 형태로 깔지 않으면 재현되지 않는다 (`px_core.same_session_pairs` 주석).
    """
    bars_per_session = 39
    out: list[BarClosed] = []
    for i, c in enumerate(closes):
        day, slot = divmod(i, bars_per_session)
        moment = _START + timedelta(days=day, minutes=10 * slot)
        out.append(
            BarClosed(
                symbol=_SYMBOL,
                horizon=Horizon.M10,
                bar_open_kst=moment,
                o_ticks=int(c),
                h_ticks=int(c) + 1,
                l_ticks=int(c) - 1,
                c_ticks=int(c),
                volume=10,
            )
        )
    return out


def test_vl_jump_60_latches_at_zero_across_a_10m_session():
    """`vl_jump_60`이 10m 지평에서 세션 내내 상수인 기전을 못 박는다 (2026-09-10 1-11 / F-97).

    ## 무엇이 관측됐나

    2026-09-10 15:34:59 `FeatureHealthDegenerate` — 10m 지평 40표본 전 구간에서 `vl_jump_60`이
    상수였다. 같은 시각 1m·3m·5m는 퇴화 0건이었다.

    ## 왜 10m의 60창에서만인가 — 희석이 아니라 **걸쇠**다

    장후 리포트의 가설은 *"창이 길어 세션 내 변동이 묻힌다"* 였다. 그 방향은 맞지만 기전은
    희석이 아니다. `vl_jump`는 `max(RV - BV, 0.0)`으로 **클립**하고, 점프가 없는 계열에서
    `RV - BV`가 음수가 될 확률은 창 길이와 거의 무관하다(모의: 창 5·20·60 모두 52~55%).

    가르는 것은 **창 겹침**이다. 한 표본에서 다음 표본으로 창이 한 봉 미끄러지므로 창 60은
    직전과 59/60(98.3%)이 같다 — 한 번 0.0으로 클립되면 그 상태가 세션 내내 풀리지 않는다.
    창 5는 4/5(80%)만 겹쳐 세션 안에서 풀린다. 10m은 세션 표본이 40개뿐이라 창이 풀릴 기회
    자체가 40번밖에 없고, 1m(≈390표본)은 그 열 배다 — 그래서 10m의 `_60`에서만 드러났다.

    ## 이 테스트는 값을 바꾸지 않는다

    창 길이 변경은 모델 입력 값 전환이라 재학습을 동반하고(`features/engine`의
    `session_boundary_inflation` 주석과 같은 규율), 화이트리스트 등재는 검출을 끄는 방향이다.
    둘 다 사람 결정 사안이다 — 여기서는 **지금 무슨 일이 벌어지는지**만 고정한다.
    """
    # 점프 없는 계열 — 창 60이 세션 경계를 넘으므로 네 세션치(160봉)를 깐다. 틱 규모는
    # 실물(KOSPI200 선물 ≈ 7,000틱)을 쓴다. 100틱 부근에서 만들면 봉당 변화가 반올림에
    # 먹혀 전 수익률이 0이 되고, 그러면 「상수」가 이 기전이 아니라 계열이 죽어서가 된다.
    steps = [-3, -2, -1, 1, 2, 3]
    closes = [7000.0]
    for i in range(159):
        closes.append(closes[-1] + steps[(i * 7 + i // 6) % len(steps)])
    bars = _m10_bars(closes)

    # 세션 내 40표본을 흉내 낸다 — 봉이 하나씩 늘어날 때마다 창이 한 칸 미끄러진다.
    w60 = [vl_jump(bars[: len(bars) - k], window=60) for k in reversed(range(40))]
    w5 = [vl_jump(bars[: len(bars) - k], window=5) for k in reversed(range(40))]

    assert all(v is not None for v in w60), "표본 부족이 아니다 — 값은 나온다"
    assert set(w60) == {0.0}, "창 60은 0.0에 걸쇠가 걸린 채 세션을 난다 (퇴화의 정체)"
    assert len(set(w5)) > 1, "같은 계열에서 창 5는 세션 안에서 풀린다 — 계열이 죽은 게 아니다"

    # **0.0이 클립의 결과임을 못 박는다.** `n_nan == n`이면 `FeatureStat.constant`가 False라
    # 퇴화로 세지도 않는다(`features/engine`) — 즉 이 피처는 값을 내면서 안 변한 것이고,
    # 그 값은 `RV - BV`가 음수라 0으로 잘린 것이다. 「데이터가 없어서」가 아니다.
    log_rets = px_core.same_session_log_returns(bars, 60)
    assert log_rets is not None and len(log_rets) == 60
    rv = sum(r * r for r in log_rets)
    bv = (math.pi / 2) * (60 / 59) * sum(abs(a) * abs(b) for a, b in zip(log_rets, log_rets[1:]))
    assert rv - bv < 0, "클립 전 원값이 음수다 — 0.0은 잘린 결과지 「점프 없음」의 측정값이 아니다"


def test_vl_jump_still_reports_a_real_jump_at_window_60():
    """걸쇠는 **점프가 없을 때**의 이야기다 — 진짜 점프는 창 60에서도 잡힌다.

    이것이 없으면 위 테스트가 "이 피처는 늘 0"으로 오독된다. 퇴화는 계산이 고장 난 것이
    아니라 입력에 점프가 없었다는 뜻이기도 하다.
    """
    steps = [-3, -2, -1, 1, 2, 3]
    closes = [7000.0]
    for i in range(159):
        closes.append(closes[-1] + steps[(i * 7 + i // 6) % len(steps)])
    with_jump = closes[:-1] + [closes[-2] + 350.0]  # 마지막 수익률만 큰 점프

    assert vl_jump(_m10_bars(closes), window=60) == 0.0
    assert vl_jump(_m10_bars(with_jump), window=60) > 0.0


# ---------------------------------------------------------------- vl_range_exp


def test_vl_range_exp_known_value():
    # 직전 5봉 레인지=10(H-L), 현재봉 레인지=30 -> 비율 3.0
    ohlc = [(100, 105, 95, 100) for _ in range(5)] + [(100, 130, 100, 115)]
    bars = _bars_from_ohlc(ohlc)
    assert vl_range_exp(bars, window=5) == pytest.approx(3.0)


def test_vl_range_exp_zero_baseline_returns_none():
    ohlc = [(100, 100, 100, 100) for _ in range(5)] + [(100, 110, 90, 100)]
    bars = _bars_from_ohlc(ohlc)
    assert vl_range_exp(bars, window=5) is None


def test_vl_range_exp_insufficient_bars_returns_none():
    bars = _bars_from_ohlc([(100, 105, 95, 100)] * 3)
    assert vl_range_exp(bars, window=5) is None


# ---------------------------------------------------------------- vl_vov


def test_vl_vov_near_zero_for_constant_volatility_regime():
    # 등폭 교대 등락이 계속 이어지면 하위윈도우 RV가 시점마다 거의 동일 -> vov가 작다
    closes = [100.0]
    for i in range(40):
        closes.append(closes[-1] * 1.02 if i % 2 == 0 else closes[-1] / 1.02)
    bars = _bars_from_closes(closes)
    assert vl_vov(bars, window=20) == pytest.approx(0.0, abs=1e-9)


def test_vl_vov_positive_when_volatility_regime_shifts():
    quiet = [100.0]
    for i in range(20):
        quiet.append(quiet[-1] * 1.001 if i % 2 == 0 else quiet[-1] / 1.001)
    wild = [quiet[-1]]
    for i in range(20):
        wild.append(wild[-1] * 1.1 if i % 2 == 0 else wild[-1] / 1.1)
    bars = _bars_from_closes(quiet + wild[1:])
    assert vl_vov(bars, window=20) > 0.0


def test_vl_vov_insufficient_bars_returns_none():
    bars = _bars_from_closes([100.0] * 5)
    assert vl_vov(bars, window=20) is None


# ---------------------------------------------------------------- vl_squeeze


def test_vl_squeeze_is_bounded_percentile():
    closes = [100.0]
    for i in range(40):
        closes.append(closes[-1] * 1.03 if i % 2 == 0 else closes[-1] / 1.03)
    bars = _bars_from_closes(closes)
    result = vl_squeeze(bars, window=20)
    assert result is not None
    assert 0.0 <= result <= 1.0


def test_vl_squeeze_low_percentile_when_compressing_after_expansion():
    # 창(window=30) 안에 확장(wild) 구간이 다수·압축(quiet) 구간이 소수로 섞이게 설계 —
    # window가 quiet 구간 길이만 겨우 넘으면 quiet 비중이 반반에 가까워져 판정이 애매해진다
    # (직접 겪은 실패: window=20으로 처음 설계했을 때 quiet 비중이 창의 85%를 차지해 오히려
    # 백분위가 높게 나왔음 — 이 값들로 재설계).
    wild = [100000.0]
    for i in range(40):
        wild.append(wild[-1] * 1.05 if i % 2 == 0 else wild[-1] / 1.05)
    quiet = [wild[-1]]
    for i in range(7):
        quiet.append(quiet[-1] * 1.002 if i % 2 == 0 else quiet[-1] / 1.002)
    bars = _bars_from_closes(wild + quiet[1:])
    result = vl_squeeze(bars, window=30)
    assert result is not None
    assert result < 0.5  # 최근(압축 구간)이 분포 하위권


def test_vl_squeeze_insufficient_bars_returns_none():
    bars = _bars_from_closes([100.0] * 10)
    assert vl_squeeze(bars, window=20) is None
