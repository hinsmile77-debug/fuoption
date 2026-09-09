"""완성봉 유실 사전 경보 (2026-09-10 G-57).

이 파일이 지키는 것 셋:

1. 경계 진입에서 **한 번만** 센다 — 5분 창에 남은 스파이크가 경보 다섯 건이 되면
   「사고 한 번」이 「경보 개수」로 부풀어 보인다(2026-08-19 F-4와 같은 형태).
2. 임계는 **상한에 연동**된다 — 상한이 바뀌면 문턱이 따라 움직여야 한다.
3. 실제로 유실이 났던 09-07의 지연 계열을 넣으면 **경보가 뜬다.** 원안의 80%로는
   그날 안 뜬다는 것까지 함께 못 박는다 — 그것이 임계를 70%로 내린 이유다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from messiah.core.timeutil import KST
from messiah.data.close_grace import MAX_CONSTITUENT_WAIT_SECONDS
from messiah.obs.delay_spike import (
    SPIKE_THRESHOLD_RATIO,
    DelaySpikeWatch,
    spike_threshold_seconds,
)

_T0 = datetime(2026, 9, 7, 14, 50, tzinfo=KST)


def _minutes(*delays: float, start: datetime = _T0):
    """1분 간격 표본 — 실제 1분봉 발행 격자와 같은 모양."""
    return [(start + timedelta(minutes=i), d) for i, d in enumerate(delays)]


def _run(watch: DelaySpikeWatch, samples) -> list:
    return [s for s in (watch.observe(t, v) for t, v in samples) if s is not None]


def test_the_threshold_tracks_the_bound():
    """상한이 바뀌면 문턱이 따라 움직인다 — 숫자가 아니라 **연동**이 원안의 요지였다."""
    assert spike_threshold_seconds() == MAX_CONSTITUENT_WAIT_SECONDS * SPIKE_THRESHOLD_RATIO
    # 지금 상한 10.0초 기준 7.0초.
    assert spike_threshold_seconds() == 7.0


def test_quiet_days_say_nothing():
    """정상 범위(p90 1.08초 대역)에서는 한 건도 안 뜬다."""
    watch = DelaySpikeWatch()

    assert _run(watch, _minutes(0.3, 0.9, 1.1, 0.4, 2.8, 0.6, 1.2)) == []


def test_one_spike_fires_once_not_once_per_sample():
    """**핵심 불변** — 창에 5분 남아 있어도 경보는 한 건이다."""
    watch = DelaySpikeWatch()

    fired = _run(watch, _minutes(0.3, 8.5, 0.4, 0.5, 0.6, 0.7))

    assert len(fired) == 1
    assert fired[0].window_max_seconds == 8.5


def test_it_rearms_after_the_window_clears():
    """창이 비면 다시 무장한다 — 두 번째 스파이크는 두 번째 경보다."""
    watch = DelaySpikeWatch()

    fired = _run(watch, _minutes(8.5, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 9.1))

    assert len(fired) == 2
    assert [round(f.window_max_seconds, 1) for f in fired] == [8.5, 9.1]


def test_the_alarm_says_how_much_room_is_left():
    """「아직 유실 아님」을 숫자로 말한다 — 여유가 0에 가까울수록 다음 버킷이 위험하다."""
    watch = DelaySpikeWatch()

    spike = _run(watch, _minutes(7.5))[0]

    assert spike.bound_seconds == MAX_CONSTITUENT_WAIT_SECONDS
    assert spike.headroom_seconds == round(MAX_CONSTITUENT_WAIT_SECONDS - 7.5, 3)
    assert 0.0 < spike.headroom_ratio < 1.0
    assert spike.worst_at == _T0


def test_the_window_folds_by_sample_time_not_by_count():
    """창은 표본 **시각**으로 접는다 — 발행이 멈춘 구간에서 창이 저절로 비면 안 된다."""
    watch = DelaySpikeWatch()
    # 8.5초 스파이크 뒤 6분을 건너뛴 표본 — 창에서 빠져야 한다.
    samples = [(_T0, 8.5), (_T0 + timedelta(minutes=6), 0.3)]

    fired = _run(watch, samples)

    assert len(fired) == 1
    assert watch.observe(_T0 + timedelta(minutes=7), 7.4) is not None, "창이 안 비었다"


# ---------------------------------------------------- 09-07 실계열 회귀 (임계 근거)
#
# 그날 5분봉 하나가 짧게 확정돼 거래량 162가 빠졌다. 하루 분위수는 전부 정상 범위였고
# (틱 지연 p99 1.030초) 1분봉 발행 지연의 그날 최대는 7.88초였다.

_SEP07_WORST = 7.88


def test_the_september_seventh_series_raises_the_alarm():
    """G-57을 만들게 한 그날에 실제로 뜨는가 — 이것이 이 축의 존재 이유다."""
    watch = DelaySpikeWatch()

    fired = _run(watch, _minutes(0.3, 0.5, 1.2, _SEP07_WORST, 0.4))

    assert len(fired) == 1
    assert fired[0].window_max_seconds == _SEP07_WORST
    # 상한 10초까지 2.12초 남은 상태 — 아직 유실이 아니라는 것이 요지다.
    assert fired[0].headroom_seconds > 0


def test_the_original_eighty_percent_would_have_missed_that_day():
    """원안(상한의 80% = 8.0초)으로는 09-07에 **안 뜬다.**

    이 테스트는 임계를 70%로 내린 근거를 코드에 못 박는다 — 누가 80%로 되돌리면
    여기서 그 대가가 드러난다.
    """
    eighty = DelaySpikeWatch(threshold_seconds=MAX_CONSTITUENT_WAIT_SECONDS * 0.80)
    seventy = DelaySpikeWatch()

    series = _minutes(0.3, 0.5, 1.2, _SEP07_WORST, 0.4)

    assert _run(eighty, series) == [], "8.0초 문턱이 09-07을 잡았다면 이 주석이 틀렸다"
    assert len(_run(seventy, series)) == 1
