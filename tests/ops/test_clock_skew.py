"""거래소 시각 대비 로컬 시계 스큐 계측 — 2026-08-05 일일점검 대응.

이 추정량의 핵심은 "중앙값이 아니라 **최댓값**"이다. KIS 체결 프레임의 영업시간 필드는
초 단위(HHMMSS)라 모든 표본이 참 스큐 이하로 깎여 나오기 때문이다
(`ops/clock_skew.py` 모듈 docstring의 유도 참고). 그 성질을 테스트로 고정한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from messiah.core.timeutil import KST
from messiah.ops.clock_skew import MIN_SAMPLES, WARN_THRESHOLD_SECONDS, ClockSkewTracker

_BASE = datetime(2026, 8, 4, 9, 0, tzinfo=KST)


def _fill(tracker: ClockSkewTracker, skew_seconds: float, *, n: int = MIN_SAMPLES) -> None:
    """참 스큐가 `skew_seconds`인 세계에서 n개의 표본을 만들어 넣는다.

    거래소 스탬프는 **초 단위로 절삭**되므로, 실제 프레임마다 `frac(t)`만큼 깎인 표본이
    나온다. 0.0~0.99초를 골고루 섞어 실제 수신 패턴을 흉내낸다.
    """
    for i in range(n):
        fraction = (i % 100) / 100.0
        true_moment = _BASE + timedelta(seconds=i, microseconds=int(fraction * 1_000_000))
        ts_exchange = true_moment.replace(microsecond=0)  # HHMMSS 절삭
        received = true_moment - timedelta(seconds=skew_seconds)
        tracker.observe(ts_exchange, received)


def test_estimate_is_none_until_enough_samples():
    """표본 1개는 [s−1, s] 구간 어디든이라 추정이 성립하지 않는다 — 0초라고 우기지 않는다."""
    tracker = ClockSkewTracker()
    _fill(tracker, 10.0, n=MIN_SAMPLES - 1)

    assert tracker.samples == MIN_SAMPLES - 1
    assert tracker.seconds is None
    assert tracker.exceeds_threshold is False


def test_max_sample_recovers_the_true_skew_from_below():
    """2026-08-04 실측 재현 — 거래소가 로컬보다 ~9.7초 앞서 있던 상태."""
    tracker = ClockSkewTracker()
    _fill(tracker, 9.7)

    estimate = tracker.seconds
    assert estimate is not None
    # 절삭 때문에 참값을 넘지 않고(하한), `frac(t)→0`인 표본이 있어 충분히 가깝다.
    assert estimate <= 9.7
    assert estimate == pytest.approx(9.7, abs=0.05)


def test_negative_skew_is_detected():
    """부호가 뒤집힌 쪽(로컬이 앞섬)이 실제 위험한 방향이다 — 상위 Horizon 합성봉이
    매 버킷 한 봉씩 잘린다(`data/bar_composer.py`)."""
    tracker = ClockSkewTracker()
    _fill(tracker, -3.0)

    estimate = tracker.seconds
    assert estimate is not None
    assert estimate == pytest.approx(-3.0, abs=0.05)
    assert tracker.exceeds_threshold is True


def test_synced_clock_is_within_threshold():
    """2026-08-05 w32time 복구 후 실측(오프셋 0.0006초)에 해당하는 상태."""
    tracker = ClockSkewTracker()
    _fill(tracker, 0.0)

    assert tracker.seconds == pytest.approx(0.0, abs=0.05)
    assert tracker.exceeds_threshold is False


def test_rolling_window_follows_a_clock_jump():
    """하루 중 Windows Time이 동기하면 스큐가 점프한다 — 창 밖으로 밀려난 옛 값이
    하루 종일 남아 "지금"을 잘못 말하면 안 된다."""
    tracker = ClockSkewTracker(window=MIN_SAMPLES)
    _fill(tracker, 14.4)  # 동기 전
    assert tracker.seconds == pytest.approx(14.4, abs=0.05)

    _fill(tracker, 0.0)  # 동기 후 — 창을 가득 채운다
    assert tracker.seconds == pytest.approx(0.0, abs=0.05)


def test_threshold_is_symmetric():
    for skew in (WARN_THRESHOLD_SECONDS + 1.0, -(WARN_THRESHOLD_SECONDS + 1.0)):
        tracker = ClockSkewTracker()
        _fill(tracker, skew)
        assert tracker.exceeds_threshold is True


def test_naive_datetime_is_rejected():
    """SYSTEM.md R3 — naive datetime은 만들지도 받지도 않는다(L21)."""
    tracker = ClockSkewTracker()
    with pytest.raises(ValueError):
        tracker.observe(datetime(2026, 8, 4, 9, 0), _BASE)  # noqa: DTZ001
