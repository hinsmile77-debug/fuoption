from datetime import datetime, timedelta

import pytest

from messiah.core.messages import BarClosed, Horizon
from messiah.core.timeutil import KST
from messiah.models import labeling
from messiah.models.labeling import (
    TripleBarrierLabel,
    compute_uniqueness,
    label_and_weight,
    triple_barrier_labels,
)

_SYMBOL = "TEST"
_START = datetime(2026, 7, 27, 9, 0, tzinfo=KST)


def _bar(idx: int, h: int, lo: int, c: int, horizon: Horizon = Horizon.M1) -> BarClosed:
    return BarClosed(
        symbol=_SYMBOL,
        horizon=horizon,
        bar_open_kst=_START + timedelta(minutes=idx),
        o_ticks=c,
        h_ticks=h,
        l_ticks=lo,
        c_ticks=c,
        volume=1,
    )


_QUIET = dict(h=101, lo=99, c=100)  # TR=2 against prev close 100 → ATR(window=2)=2.0 → width=1


def _warmup(n: int = 3) -> list[BarClosed]:
    """진입까지의 워밍업 3봉 — atr_window=2로 호출하면 entry(i=2)에서 atr=2.0, width=1,
    upper=101, lower=99가 나온다(모든 테스트가 공유하는 전제)."""
    return [_bar(i, **_QUIET) for i in range(n)]


def _filler(start_idx: int, n: int) -> list[BarClosed]:
    """터치 이후 남은 forward 슬롯을 채우는 무관 값(범위 안쪽이라 터치되지 않음)."""
    return [_bar(start_idx + k, h=100, lo=100, c=100) for k in range(n)]


def test_upper_barrier_touch_on_second_forward_bar():
    bars = [
        *_warmup(),
        _bar(3, h=100, lo=100, c=100),  # forward1: 터치 없음
        _bar(4, h=102, lo=99, c=101),  # forward2: 고가 102 >= upper(101) → 터치
        *_filler(5, 1),
    ]
    labels = triple_barrier_labels(bars, atr_window=2)
    entry_label = _entry_label(labels, bars)

    assert entry_label.label == 1
    assert entry_label.barrier == "upper"
    assert entry_label.entry_price_ticks == 100
    assert entry_label.ret_ticks == 1  # upper(101) - entry(100)
    assert entry_label.cost_demoted is False


def test_lower_barrier_touch_on_first_forward_bar():
    bars = [
        *_warmup(),
        _bar(3, h=100, lo=98, c=99),  # 저가 98 <= lower(99) → 즉시 터치
        *_filler(4, 2),
    ]
    labels = triple_barrier_labels(bars, atr_window=2)
    entry_label = _entry_label(labels, bars)

    assert entry_label.label == -1
    assert entry_label.barrier == "lower"
    assert entry_label.ret_ticks == -1  # lower(99) - entry(100)


def test_time_barrier_reached_labels_zero():
    bars = [
        *_warmup(),
        *_filler(3, 3),  # 3개 forward 봉 전부 [99,101] 범위 안쪽 — 터치 없음
    ]
    labels = triple_barrier_labels(bars, atr_window=2)
    entry_label = _entry_label(labels, bars)

    assert entry_label.label == 0
    assert entry_label.barrier == "time"
    assert entry_label.cost_demoted is False
    # 시간배리어 판정시각은 forward 마지막 봉(idx=5)의 확정시각
    assert entry_label.t_end == bars[5].bar_open_kst + timedelta(minutes=1)


def test_same_bar_touching_both_barriers_prefers_upper():
    bars = [
        *_warmup(),
        _bar(3, h=110, lo=90, c=100),  # 상/하단 동시 터치 — 상단 우선
        *_filler(4, 2),
    ]
    labels = triple_barrier_labels(bars, atr_window=2)
    entry_label = _entry_label(labels, bars)

    assert entry_label.label == 1
    assert entry_label.barrier == "upper"


def test_cost_demotion_downgrades_touch_to_zero():
    bars = [
        *_warmup(),
        _bar(3, h=100, lo=100, c=100),
        _bar(4, h=102, lo=99, c=101),  # 위와 동일한 upper 터치, ret_ticks=+1
        *_filler(5, 1),
    ]
    labels = triple_barrier_labels(bars, atr_window=2, cost_ticks=1)  # 왕복비용 1틱 >= ret_ticks
    entry_label = _entry_label(labels, bars)

    assert entry_label.label == 0  # 강등됨
    assert entry_label.barrier == "upper"  # 원래 터치 정보는 보존
    assert entry_label.cost_demoted is True
    assert entry_label.ret_ticks == 1


def test_insufficient_forward_bars_are_skipped():
    # entry(i=2)는 ATR 워밍업은 충족하지만 forward 3봉 중 1개(idx=3)만 존재 — 레이블 생성 안 함
    bars = [*_warmup(), _bar(3, **_QUIET)]
    labels = triple_barrier_labels(bars, atr_window=2)
    assert labels == []


def test_insufficient_atr_warmup_is_skipped():
    bars = [_bar(0, **_QUIET), _bar(1, **_QUIET)]  # atr_window=2는 3봉 필요
    labels = triple_barrier_labels(bars, atr_window=2)
    assert labels == []


def test_rejects_mixed_symbol_or_horizon():
    bars = [_bar(0, **_QUIET), _bar(1, **_QUIET).model_copy(update={"symbol": "OTHER"})]
    try:
        triple_barrier_labels(bars, atr_window=2)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def _entry_label(labels: list[TripleBarrierLabel], bars: list[BarClosed]) -> TripleBarrierLabel:
    entry_confirm = bars[2].bar_open_kst + timedelta(minutes=1)
    return next(lbl for lbl in labels if lbl.t_start == entry_confirm)


# ---------------------------------------------------------------- compute_uniqueness


def _label(t_start: datetime, t_end: datetime) -> TripleBarrierLabel:
    return TripleBarrierLabel(
        symbol=_SYMBOL,
        horizon=Horizon.M1,
        t_start=t_start,
        t_end=t_end,
        entry_price_ticks=100,
        label=0,
        barrier="time",
        ret_ticks=0,
    )


def test_compute_uniqueness_hand_verified_overlap():
    """A=[t0,t1], B=[t1,t2], C=[t3,t3] — 동시성: t0=1,t1=2,t2=1,t3=1.
    uniqueness(A)=mean(1/1,1/2)=0.75, uniqueness(B)=mean(1/2,1/1)=0.75, uniqueness(C)=1.0."""
    t = [_START + timedelta(minutes=i) for i in range(5)]
    labels = [_label(t[0], t[1]), _label(t[1], t[2]), _label(t[3], t[3])]

    weights = compute_uniqueness(labels)

    assert weights == [0.75, 0.75, 1.0]


def test_compute_uniqueness_non_overlapping_events_get_weight_one():
    t = [_START + timedelta(minutes=i) for i in range(6)]
    labels = [_label(t[0], t[1]), _label(t[2], t[3]), _label(t[4], t[5])]

    weights = compute_uniqueness(labels)

    assert weights == [1.0, 1.0, 1.0]


def test_compute_uniqueness_empty_input():
    assert compute_uniqueness([]) == []


def test_label_and_weight_fills_uniqueness_onto_real_generated_labels():
    """triple_barrier_labels()가 만든 실제 레이블(서로 겹치는 진입들 — 매 봉이 진입 후보라
    시간배리어 구간 3봉이 계속 겹친다)에 compute_uniqueness()가 크래시 없이 붙고, 겹치는
    구간이라 가중치가 1.0 미만으로 깎였는지 확인."""
    bars = [*_warmup(), *_filler(3, 3), *_filler(6, 3)]  # 워밍업 3 + 시간배리어까지 6봉 여유

    labeled = label_and_weight(bars, atr_window=2)

    assert len(labeled) > 1
    assert all(0.0 < lbl.weight <= 1.0 for lbl in labeled)
    assert any(lbl.weight < 1.0 for lbl in labeled)  # 겹치는 진입이 있으니 전부 1.0일 수 없음


# ------------------------------------------ 변동성 타깃 (2026-08-04, 예측 대상 전환 실측용)


def _closes(values: list[float]) -> list[BarClosed]:
    return [
        BarClosed(
            symbol="A05608",
            horizon=Horizon.M5,
            bar_open_kst=datetime(2026, 8, 5, 9, 0, tzinfo=KST) + timedelta(minutes=5 * i),
            o_ticks=int(c),
            h_ticks=int(c),
            l_ticks=int(c),
            c_ticks=int(c),
            volume=10,
        )
        for i, c in enumerate(values)
    ]


def test_forward_realized_volatility_matches_the_hand_computed_value():
    """N=2, 종가 100→110→121이면 두 로그수익률이 각각 log(1.1)로 같다.
    RV_0 = sqrt(2 * log(1.1)^2) = log(1.1) * sqrt(2)."""
    import math

    bars = _closes([100, 110, 121, 121])

    out = labeling.forward_realized_volatility(bars, horizon_bars=2)

    assert out[0] == pytest.approx(math.log(1.1) * math.sqrt(2))


def test_forward_realized_volatility_looks_forward_only():
    """bars[i]의 값은 i **이후**의 움직임만 담아야 한다 — 과거 변동은 안 섞인다."""
    bars = _closes([100, 200, 300, 300, 300])  # 앞은 격렬, 뒤는 정지

    out = labeling.forward_realized_volatility(bars, horizon_bars=2)

    assert out[0] > 0  # 100→200→300을 본다
    assert out[2] == pytest.approx(0.0)  # 300→300→300 — 앞의 격변이 안 섞였다


def test_forward_realized_volatility_trims_the_tail_as_none_not_zero():
    """창을 못 채우는 꼬리는 0이 아니라 None이다 — 0으로 채우면 "변동이 없었다"는 없는
    사실을 주장하게 되고, 관문이 그걸 실제 관측으로 센다."""
    bars = _closes([100, 101, 102, 103, 104])

    out = labeling.forward_realized_volatility(bars, horizon_bars=3)

    assert len(out) == 5
    assert out[:2] == [pytest.approx(out[0]), pytest.approx(out[1])]
    assert out[2:] == [None, None, None]


def test_forward_realized_volatility_window_matches_the_direction_barrier():
    """방향 레이블과 **같은 봉 수**를 봐야 두 축의 IC를 견줄 수 있다 — 관문 스크립트가
    `BARRIER_PARAMS[horizon].time_barrier_bars`를 그대로 넘기는 이유."""
    bars = _closes([100 + i for i in range(20)])
    n = labeling.BARRIER_PARAMS[Horizon.M5].time_barrier_bars

    out = labeling.forward_realized_volatility(bars, horizon_bars=n)

    assert sum(1 for v in out if v is None) == n  # 꼬리 트림이 정확히 N봉


def test_forward_realized_volatility_rejects_a_nonsensical_window():
    with pytest.raises(ValueError, match="horizon_bars"):
        labeling.forward_realized_volatility(_closes([100, 101]), horizon_bars=0)
