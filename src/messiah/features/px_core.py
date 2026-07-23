"""PX(가격·추세·모멘텀) 기저 Feature 30개 — Master Plan Ver 2.0 §9 W6~8, Ver 1.4 §2.2.

이번 라운드는 PX만 구현한다(MS는 대부분 호가창 데이터가 필요한데 MESSIAH는 아직 호가 WS를
구독하지 않음 — capability_matrix.md "알려진 갭" 참고). PX 30개는 전부 완성봉의 OHLCV만으로
계산 가능해 지금 있는 데이터로 바로 구현할 수 있다.

모든 계산기는 `bars: Sequence[BarClosed]`(오래된 것 → 최신 순, 마지막 원소가 "지금 막
완성된 봉")를 입력으로 받는다. 윈도우형(26개)은 `window: int`(Ver 1.4 §1.3의 W-std={5,20,60}
또는 px_hurst만 W-slow={20,60,120})를 추가로 받고, 상태형(4개: px_gap_open/px_open_ret/
px_range_pos_d/px_round_dist)은 윈도우가 없다(px_round_dist만 예외적으로 ATR 계산에 고정
윈도우를 내부적으로 씀 — 세션 상태와는 무관).

워밍업 부족(윈도우를 채울 만큼 봉이 없음)이면 전부 None을 반환한다(NaN 마킹, Ver 1.4 §1.1
`nan_policy`) — 1차 구현은 전방채움(ffill) 없이 단순 None.

**알려진 근사·단순화** (Ver 1.4는 "무엇을 계산하는가"만 정의하고 정확한 파라미터화는 구현이
결정 — Ver 1.5 선정 절차에서 실제로 쓸모없으면 자연 탈락한다):
- ATR·RSI는 Wilder 지수평활이 아니라 단순 이동평균 기반(단순함 우선, 1차 구현)
- `px_vwap_dev`의 VWAP은 완성봉에 체결가중평균이 없어(BarClosed엔 O/H/L/C/volume만 있음)
  전형가(OHLC3=(H+L+C)/3)를 거래량 가중한 표준 근사를 쓴다
- `px_macd_h`는 고정 12/26/9 대신 window 파라미터로 일반화(단기=W, 장기=2W, 시그널=EMA(macd
  시계열, max(W//3,1)))
- `px_ema_cross`/`px_breakout`은 "경과 바 수"·"되돌림 정도" 서브 지표는 이번 스코프에서 뺌
  (부호/강도 스칼라 하나만) — 상태 누적이 필요해 순수 함수 범위를 벗어남
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

from messiah.core.messages import BarClosed

Bars = Sequence[BarClosed]

# Ver 1.4 §1.3 표준 윈도우 세트
W_STD = (5, 20, 60)
W_SLOW_HURST = (20, 60, 120)

_DEFAULT_ROUND_ATR_WINDOW = 20
_DEFAULT_ROUND_TICKS = 250  # 근사 라운드 레벨 간격(틱) — 상품별 실제 라운드넘버는 다름, 추정치


# ---------------------------------------------------------------- 공용 헬퍼


def _closes(bars: Bars) -> list[float]:
    return [float(b.c_ticks) for b in bars]


def _highs(bars: Bars) -> list[float]:
    return [float(b.h_ticks) for b in bars]


def _lows(bars: Bars) -> list[float]:
    return [float(b.l_ticks) for b in bars]


def _true_ranges(bars: Bars) -> list[float]:
    """bars[i]의 TR은 bars[i-1].close가 필요 — 반환 길이는 len(bars)-1."""
    out: list[float] = []
    for prev, cur in zip(bars, bars[1:]):
        h, lo, prev_c = float(cur.h_ticks), float(cur.l_ticks), float(prev.c_ticks)
        out.append(max(h - lo, abs(h - prev_c), abs(lo - prev_c)))
    return out


def _atr(bars: Bars, window: int) -> float | None:
    if len(bars) < window + 1:
        return None
    trs = _true_ranges(bars[-(window + 1) :])
    if not trs:
        return None
    atr = statistics.fmean(trs)
    return atr if atr > 0 else None


def _ema_series(values: Sequence[float], period: int) -> list[float]:
    """values[period-1:]에 대응하는 EMA 시계열 — 시드는 첫 period개의 단순평균."""
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    ema = statistics.fmean(values[:period])
    out = [ema]
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
        out.append(ema)
    return out


def _log_returns(closes: Sequence[float]) -> list[float]:
    return [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    try:
        return statistics.correlation(xs, ys)
    except statistics.StatisticsError:
        return None


def _skew(values: Sequence[float]) -> float | None:
    n = len(values)
    if n < 3:
        return None
    mean = statistics.fmean(values)
    std = statistics.pstdev(values)
    if std == 0:
        return None
    return (1 / n) * sum(((v - mean) / std) ** 3 for v in values)


def _kurtosis_excess(values: Sequence[float]) -> float | None:
    n = len(values)
    if n < 4:
        return None
    mean = statistics.fmean(values)
    std = statistics.pstdev(values)
    if std == 0:
        return None
    return (1 / n) * sum(((v - mean) / std) ** 4 for v in values) - 3.0


def _linreg_xy(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float] | None:
    """(slope, R²) of ys ~ xs 단순회귀 — 명시적 x쌍을 받는다(hand-rolled, numpy 불필요).
    px_hurst의 log(size) 축처럼 x가 등간격이 아닌 경우에 꼭 이 버전을 써야 한다 —
    등간격 인덱스로 대체하면 기울기가 왜곡된다(2026-07-23 실측으로 발견한 버그)."""
    n = len(xs)
    if n < 2 or n != len(ys):
        return None
    x_mean = statistics.fmean(xs)
    y_mean = statistics.fmean(ys)
    ss_xy = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    ss_xx = sum((x - x_mean) ** 2 for x in xs)
    if ss_xx == 0:
        return None
    slope = ss_xy / ss_xx
    y_pred = [y_mean + slope * (x - x_mean) for x in xs]
    ss_tot = sum((y - y_mean) ** 2 for y in ys)
    if ss_tot == 0:
        return 0.0, 1.0
    ss_res = sum((y - yp) ** 2 for y, yp in zip(ys, y_pred))
    r2 = 1 - ss_res / ss_tot
    return slope, r2


def _linreg_slope_r2(y: Sequence[float]) -> tuple[float, float] | None:
    """y ~ x(0..n-1) 등간격 단순회귀 — px_trend_slope/r2처럼 x가 봉 인덱스인 경우."""
    return _linreg_xy(list(range(len(y))), y)


# ---------------------------------------------------------------- 1. 모멘텀·수익률


def px_ret(bars: Bars, window: int) -> float | None:
    """로그 수익률 — Δlog(close) over window."""
    if len(bars) < window + 1:
        return None
    c0, c1 = bars[-1 - window].c_ticks, bars[-1].c_ticks
    if c0 <= 0 or c1 <= 0:
        return None
    return math.log(c1 / c0)


def px_mom(bars: Bars, window: int) -> float | None:
    """모멘텀 — close/close[-W] - 1."""
    if len(bars) < window + 1:
        return None
    c0 = bars[-1 - window].c_ticks
    if c0 == 0:
        return None
    return bars[-1].c_ticks / c0 - 1


def px_accel(bars: Bars, window: int) -> float | None:
    """가속도 — mom_W - mom_2W."""
    m1 = px_mom(bars, window)
    m2 = px_mom(bars, 2 * window)
    if m1 is None or m2 is None:
        return None
    return m1 - m2


def px_max_ret(bars: Bars, window: int) -> float | None:
    """최대 단봉 수익 — max(단순 수익률) in W."""
    if len(bars) < window + 1:
        return None
    closes = _closes(bars[-(window + 1) :])
    rets = [b / a - 1 for a, b in zip(closes, closes[1:]) if a != 0]
    return max(rets) if rets else None


# ---------------------------------------------------------------- 2. 이동평균·추세


def px_vwap_dev(bars: Bars, window: int) -> float | None:
    """VWAP 괴리 — (close-VWAP)/ATR. VWAP은 OHLC3 거래량가중 근사(모듈 docstring 참고)."""
    if len(bars) < window + 1:
        return None
    window_bars = bars[-window:]
    total_vol = sum(b.volume for b in window_bars)
    if total_vol == 0:
        return None
    vwap = sum(((b.h_ticks + b.l_ticks + b.c_ticks) / 3) * b.volume for b in window_bars) / (
        total_vol
    )
    atr = _atr(bars, window)
    if not atr:
        return None
    return (bars[-1].c_ticks - vwap) / atr


def px_ema_dev(bars: Bars, window: int) -> float | None:
    """EMA 괴리 — (close-EMA_W)/ATR."""
    closes = _closes(bars)
    ema = _ema_series(closes, window)
    atr = _atr(bars, window)
    if not ema or not atr:
        return None
    return (bars[-1].c_ticks - ema[-1]) / atr


def px_ema_cross(bars: Bars, window: int) -> float | None:
    """EMA 교차 상태 — sign(EMA_fast-EMA_slow). fast=W, slow=3W(경과 바 수는 스코프 밖,
    모듈 docstring 참고)."""
    closes = _closes(bars)
    fast = _ema_series(closes, window)
    slow = _ema_series(closes, 3 * window)
    if not fast or not slow:
        return None
    diff = fast[-1] - slow[-1]
    return float((diff > 0) - (diff < 0))


def px_trend_slope(bars: Bars, window: int) -> float | None:
    """추세 기울기 — 종가 회귀 기울기/ATR."""
    if len(bars) < window + 1:
        return None
    closes = _closes(bars[-window:])
    result = _linreg_slope_r2(closes)
    atr = _atr(bars, window)
    if result is None or not atr:
        return None
    slope, _ = result
    return slope / atr


def px_trend_r2(bars: Bars, window: int) -> float | None:
    """추세 결정계수 — 위 회귀의 R²."""
    if len(bars) < window:
        return None
    closes = _closes(bars[-window:])
    result = _linreg_slope_r2(closes)
    return result[1] if result else None


def px_hurst(bars: Bars, window: int) -> float | None:
    """허스트 지수 — R/S 근사(mahdi regime_features.hurst_exponent와 동일 로직, 이식).
    <20개 미만이거나 분산이 0이면 중립값 0.5."""
    if len(bars) < window:
        return None
    closes = _closes(bars[-window:])
    if len(closes) < 20:
        return 0.5
    log_rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    if len(log_rets) < 19 or statistics.pstdev(log_rets) == 0:
        return 0.5

    chunk_sizes = sorted({s for s in (8, 16, 32, len(log_rets) // 2) if 4 <= s <= len(log_rets)})
    if len(chunk_sizes) < 2:
        return 0.5

    log_sizes: list[float] = []
    log_rs: list[float] = []
    for size in chunk_sizes:
        rs_values: list[float] = []
        for start in range(0, len(log_rets) - size + 1, size):
            chunk = log_rets[start : start + size]
            mean = statistics.fmean(chunk)
            deviations = [v - mean for v in chunk]
            cumulative = [sum(deviations[: i + 1]) for i in range(len(deviations))]
            r = max(cumulative) - min(cumulative)
            s = statistics.pstdev(chunk)
            if s > 0:
                rs_values.append(r / s)
        if rs_values:
            log_sizes.append(math.log(size))
            log_rs.append(math.log(statistics.fmean(rs_values)))

    result = _linreg_xy(log_sizes, log_rs) if len(log_sizes) >= 2 else None
    if result is None:
        return 0.5
    slope, _ = result
    return max(0.0, min(1.0, slope))


def px_autocorr(bars: Bars, window: int) -> float | None:
    """수익률 자기상관 — corr(ret_t, ret_{t-1}) over W."""
    if len(bars) < window + 2:
        return None
    closes = _closes(bars[-(window + 1) :])
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    if len(rets) < 3:
        return None
    return _pearson(rets[:-1], rets[1:])


def px_skew_r(bars: Bars, window: int) -> float | None:
    """수익률 왜도 — rolling skew."""
    if len(bars) < window + 1:
        return None
    rets = _log_returns(_closes(bars[-(window + 1) :]))
    return _skew(rets)


def px_kurt_r(bars: Bars, window: int) -> float | None:
    """수익률 첨도 — rolling kurtosis(초과 첨도)."""
    if len(bars) < window + 1:
        return None
    rets = _log_returns(_closes(bars[-(window + 1) :]))
    return _kurtosis_excess(rets)


# ---------------------------------------------------------------- 3. 오실레이터·밴드


def px_rsi(bars: Bars, window: int) -> float | None:
    """RSI(W) — 단순평균 기반(Wilder 지수평활 아님, 모듈 docstring 참고)."""
    if len(bars) < window + 1:
        return None
    closes = _closes(bars[-(window + 1) :])
    deltas = [b - a for a, b in zip(closes, closes[1:])]
    gains = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]
    avg_gain = statistics.fmean(gains) if gains else 0.0
    avg_loss = statistics.fmean(losses) if losses else 0.0
    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def px_stoch(bars: Bars, window: int) -> float | None:
    """스토캐스틱 %K — (close-minW)/(maxW-minW)."""
    if len(bars) < window:
        return None
    window_bars = bars[-window:]
    lo, hi = min(_lows(window_bars)), max(_highs(window_bars))
    if hi == lo:
        return None
    return (bars[-1].c_ticks - lo) / (hi - lo)


def px_don_pos(bars: Bars, window: int) -> float | None:
    """돈치안 채널 위치 — (close-minW)/(maxW-minW). px_stoch와 기저식은 같으나 별개 등록
    항목(Ver 1.4 원문 그대로 — 실제 쓸모는 Ver 1.5 §5 상관 클러스터링에서 자연히 정리됨)."""
    return px_stoch(bars, window)


def px_bb_pos(bars: Bars, window: int) -> float | None:
    """볼린저 위치 — (close-MA)/2σ."""
    if len(bars) < window:
        return None
    closes = _closes(bars[-window:])
    std = statistics.pstdev(closes)
    if std == 0:
        return None
    return (bars[-1].c_ticks - statistics.fmean(closes)) / (2 * std)


def px_bb_width(bars: Bars, window: int) -> float | None:
    """볼린저 폭 — 4σ/MA (스퀴즈 감지)."""
    if len(bars) < window:
        return None
    closes = _closes(bars[-window:])
    mean = statistics.fmean(closes)
    if mean == 0:
        return None
    return 4 * statistics.pstdev(closes) / mean


def px_macd_h(bars: Bars, window: int) -> float | None:
    """MACD 히스토그램(ATR 정규화) — 단기=W, 장기=2W, 시그널=EMA(macd 시계열, max(W//3,1))
    (고정 12/26/9 대신 window로 일반화, 모듈 docstring 참고)."""
    closes = _closes(bars)
    fast = _ema_series(closes, window)
    slow = _ema_series(closes, 2 * window)
    if not fast or not slow:
        return None
    n = min(len(fast), len(slow))
    macd_series = [f - s for f, s in zip(fast[-n:], slow[-n:])]
    signal_period = max(window // 3, 1)
    signal = _ema_series(macd_series, signal_period)
    if not signal:
        return None
    atr = _atr(bars, window)
    if not atr:
        return None
    return (macd_series[-1] - signal[-1]) / atr


# ---------------------------------------------------------------- 4. 레인지·극값


def px_high_dist(bars: Bars, window: int) -> float | None:
    """고점 거리 — (maxW-close)/ATR."""
    if len(bars) < window:
        return None
    atr = _atr(bars, window)
    if not atr:
        return None
    return (max(_highs(bars[-window:])) - bars[-1].c_ticks) / atr


def px_low_dist(bars: Bars, window: int) -> float | None:
    """저점 거리 — (close-minW)/ATR."""
    if len(bars) < window:
        return None
    atr = _atr(bars, window)
    if not atr:
        return None
    return (bars[-1].c_ticks - min(_lows(bars[-window:]))) / atr


def px_dd(bars: Bars, window: int) -> float | None:
    """롤링 드로다운 — close/maxW(종가 기준) − 1."""
    if len(bars) < window:
        return None
    peak = max(_closes(bars[-window:]))
    if peak == 0:
        return None
    return bars[-1].c_ticks / peak - 1


def px_runup(bars: Bars, window: int) -> float | None:
    """롤링 런업 — close/minW(종가 기준) − 1."""
    if len(bars) < window:
        return None
    trough = min(_closes(bars[-window:]))
    if trough == 0:
        return None
    return bars[-1].c_ticks / trough - 1


def px_breakout(bars: Bars, window: int) -> float | None:
    """돌파 강도 — 직전 W개(현재봉 제외) 레인지를 갱신하면 그 초과폭/ATR, 아니면 0
    (되돌림 정도·경과 바 수는 스코프 밖, 모듈 docstring 참고)."""
    if len(bars) < window + 1:
        return None
    prior = bars[-1 - window : -1]
    atr = _atr(bars, window)
    if not atr:
        return None
    close = bars[-1].c_ticks
    prior_high, prior_low = max(_highs(prior)), min(_lows(prior))
    if close > prior_high:
        return (close - prior_high) / atr
    if close < prior_low:
        return (close - prior_low) / atr
    return 0.0


def px_zscore(bars: Bars, window: int) -> float | None:
    """가격 Z-score — (close-MA)/σ."""
    if len(bars) < window:
        return None
    closes = _closes(bars[-window:])
    std = statistics.pstdev(closes)
    if std == 0:
        return None
    return (bars[-1].c_ticks - statistics.fmean(closes)) / std


def px_adx(bars: Bars, window: int) -> float | None:
    """ADX(W) — Wilder 방향성 지표(단순평균 버전, mahdi regime_features.adx 이식 근사).
    데이터 부족 시 중립값 20.0."""
    if len(bars) < window + 1:
        return 20.0
    window_bars = bars[-(window + 1) :]
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    trs: list[float] = []
    for prev, cur in zip(window_bars, window_bars[1:]):
        up = cur.h_ticks - prev.h_ticks
        down = prev.l_ticks - cur.l_ticks
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
        trs.append(
            max(
                cur.h_ticks - cur.l_ticks,
                abs(cur.h_ticks - prev.c_ticks),
                abs(cur.l_ticks - prev.c_ticks),
            )
        )
    atr = statistics.fmean(trs) if trs else 0.0
    if atr == 0:
        return 20.0
    plus_di = 100 * statistics.fmean(plus_dm) / atr
    minus_di = 100 * statistics.fmean(minus_dm) / atr
    denom = plus_di + minus_di
    if denom == 0:
        return 0.0
    return 100 * abs(plus_di - minus_di) / denom


# ---------------------------------------------------------------- 5. 세션 상태형(4개)


@dataclass
class SessionState:
    """당일 세션 범위 상태 — px_gap_open/px_open_ret/px_range_pos_d가 필요로 하는, 단일
    윈도우로 표현 불가능한 "오늘 하루" 상태. 심볼당 1개, FeatureEngine이 M1 봉이 들어올
    때마다 `on_bar()`로 갱신한다(다른 Horizon 봉으로는 갱신하지 않음 — 분 단위가 "오늘의
    시가/고저"를 놓치지 않는 가장 촘촘한 단위이기 때문)."""

    current_day: date | None = None
    session_open_ticks: int | None = None
    session_high_ticks: int | None = None
    session_low_ticks: int | None = None
    prev_day_close_ticks: int | None = None
    _last_close_ticks: int | None = field(default=None, repr=False)

    def on_bar(self, bar: BarClosed) -> None:
        bar_day = bar.bar_open_kst.date()
        if self.current_day is None:
            self.current_day = bar_day
        elif bar_day != self.current_day:
            self.prev_day_close_ticks = self._last_close_ticks
            self.current_day = bar_day
            self.session_open_ticks = None
            self.session_high_ticks = None
            self.session_low_ticks = None

        if self.session_open_ticks is None:
            self.session_open_ticks = bar.o_ticks
            self.session_high_ticks = bar.h_ticks
            self.session_low_ticks = bar.l_ticks
        else:
            self.session_high_ticks = max(self.session_high_ticks, bar.h_ticks)  # type: ignore[type-var]
            self.session_low_ticks = min(self.session_low_ticks, bar.l_ticks)  # type: ignore[type-var]
        self._last_close_ticks = bar.c_ticks


def px_gap_open(bars: Bars, session: SessionState) -> float | None:
    """시가 갭 — log(세션 시가/전일 종가). 첫 거래일(전일 데이터 없음)은 None."""
    if session.session_open_ticks is None or session.prev_day_close_ticks is None:
        return None
    if session.prev_day_close_ticks <= 0 or session.session_open_ticks <= 0:
        return None
    return math.log(session.session_open_ticks / session.prev_day_close_ticks)


def px_open_ret(bars: Bars, session: SessionState) -> float | None:
    """당일 시가 대비 — log(close/세션 시가)."""
    if not bars or session.session_open_ticks is None or session.session_open_ticks <= 0:
        return None
    if bars[-1].c_ticks <= 0:
        return None
    return math.log(bars[-1].c_ticks / session.session_open_ticks)


def px_range_pos_d(bars: Bars, session: SessionState) -> float | None:
    """당일 레인지 위치 — (close-당일저가)/(당일고가-당일저가)."""
    if not bars or session.session_high_ticks is None or session.session_low_ticks is None:
        return None
    span = session.session_high_ticks - session.session_low_ticks
    if span == 0:
        return None
    return (bars[-1].c_ticks - session.session_low_ticks) / span


def px_round_dist(bars: Bars, session: SessionState) -> float | None:
    """라운드넘버 거리 — 가까운 라운드 레벨까지 ATR 거리(session은 시그니처 통일용, 미사용).
    라운드 간격은 _DEFAULT_ROUND_TICKS로 고정된 근사치(상품별 실제 값과 다를 수 있음)."""
    atr = _atr(bars, _DEFAULT_ROUND_ATR_WINDOW)
    if not bars or not atr:
        return None
    close = bars[-1].c_ticks
    nearest_round = round(close / _DEFAULT_ROUND_TICKS) * _DEFAULT_ROUND_TICKS
    return abs(close - nearest_round) / atr


# ---------------------------------------------------------------- 레지스트리

# 윈도우형 26개: (id, 계산기, 윈도우 세트)
WINDOWED_FEATURES: list[tuple[str, "callable[[Bars, int], float | None]", tuple[int, ...]]] = [
    ("px_ret", px_ret, W_STD),
    ("px_mom", px_mom, W_STD),
    ("px_accel", px_accel, W_STD),
    ("px_vwap_dev", px_vwap_dev, W_STD),
    ("px_ema_dev", px_ema_dev, W_STD),
    ("px_ema_cross", px_ema_cross, W_STD),
    ("px_rsi", px_rsi, W_STD),
    ("px_stoch", px_stoch, W_STD),
    ("px_macd_h", px_macd_h, W_STD),
    ("px_bb_pos", px_bb_pos, W_STD),
    ("px_bb_width", px_bb_width, W_STD),
    ("px_don_pos", px_don_pos, W_STD),
    ("px_high_dist", px_high_dist, W_STD),
    ("px_low_dist", px_low_dist, W_STD),
    ("px_breakout", px_breakout, W_STD),
    ("px_trend_slope", px_trend_slope, W_STD),
    ("px_trend_r2", px_trend_r2, W_STD),
    ("px_adx", px_adx, W_STD),
    ("px_zscore", px_zscore, W_STD),
    ("px_autocorr", px_autocorr, W_STD),
    ("px_hurst", px_hurst, W_SLOW_HURST),
    ("px_skew_r", px_skew_r, W_STD),
    ("px_kurt_r", px_kurt_r, W_STD),
    ("px_max_ret", px_max_ret, W_STD),
    ("px_dd", px_dd, W_STD),
    ("px_runup", px_runup, W_STD),
]

# 상태형 4개: (id, 계산기) — session 인자를 받음, 윈도우 없음
STATEFUL_FEATURES: list[tuple[str, "callable[[Bars, SessionState], float | None]"]] = [
    ("px_gap_open", px_gap_open),
    ("px_open_ret", px_open_ret),
    ("px_range_pos_d", px_range_pos_d),
    ("px_round_dist", px_round_dist),
]
