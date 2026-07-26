import math
import statistics
from datetime import datetime, timedelta

import pytest
from messiah.core.messages import BarClosed, Horizon
from messiah.core.timeutil import KST
from messiah.features.px_core import W_STD
from messiah.features.vl_core import vl_vol_ratio

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
