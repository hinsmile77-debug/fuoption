"""F-29 — 회선 지연 요약의 **모집단을 라벨로 가른다** (2026-08-24 이상점 1-17).

절단은 2026-08-20 F-H가 이미 자백하게 만들었다. 자백돼 있지 않던 것은 **「두 벌」** 이다:
같은 리포트의 위 분위수는 **링버퍼 끝 토막**이고 `by_hour`는 **전량**이다. 라벨이 없으면
둘을 나란히 놓은 사람이 「시간대별로는 괜찮은데 전체는 왜 나쁘지」를 영원히 못 푼다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from messiah.core.timeutil import KST
from messiah.ops.clock_skew import ClockSkewTracker

_BASE = datetime(2026, 8, 24, 9, 0, tzinfo=KST)


def _feed(tracker: ClockSkewTracker, *, hour: int, count: int, latency: float) -> None:
    """`observe()`가 만드는 지연은 `max(창) − 이번 표본`이다.

    전부 같은 간격으로 먹이면 지연이 전부 0이 되어 분위수가 뜻을 잃는다 — 표본마다
    조금씩 늦추어 분포를 만든다.
    """
    start = _BASE.replace(hour=hour)
    for i in range(count):
        ts = start + timedelta(seconds=i)
        received = ts - timedelta(seconds=latency) + timedelta(milliseconds=i % 7)
        tracker.observe(ts, received)


def test_the_two_blocks_declare_their_populations():
    tracker = ClockSkewTracker(latency_capacity=1_000)
    _feed(tracker, hour=9, count=100, latency=0.1)

    stats = tracker.delivery_latency_seconds()
    assert stats is not None
    assert stats["population"] == "tail_ringbuffer"

    by_hour = tracker.delivery_latency_by_hour()
    assert by_hour["09"]["population"] == "all"


def test_a_truncated_day_can_still_compare_like_with_like():
    """**절단된 날** — `by_hour`는 전량이고 `p90_tail`은 같은 링버퍼 구간만 잘라낸 값이다.

    이 칸이 있어야 위 요약과 아래 표를 직접 비교할 수 있다.
    """
    tracker = ClockSkewTracker(latency_capacity=50)
    _feed(tracker, hour=9, count=200, latency=0.5)  # 09시가 링버퍼에서 통째로 밀려난다
    _feed(tracker, hour=14, count=60, latency=0.5)

    stats = tracker.delivery_latency_seconds()
    assert stats is not None and stats["truncated"] is True

    by_hour = tracker.delivery_latency_by_hour()
    assert by_hour["09"]["samples"] == 200.0, "전량 표는 링버퍼에 안 덮인다"
    # 09시는 링버퍼에 한 건도 안 남았다 — **0이 아니라 None**이다(L18).
    assert by_hour["09"]["p90_tail"] is None
    assert by_hour["09"]["samples_tail"] == 0.0
    # 14시는 링버퍼 안에 있다 — 비교 가능한 숫자가 나온다.
    assert by_hour["14"]["p90_tail"] is not None
    assert by_hour["14"]["samples"] == 60.0
    assert by_hour["14"]["samples_tail"] == 50.0, "링버퍼 상한만큼만 남는다"


def test_the_ring_buffer_is_not_enlarged():
    """**링버퍼 용량을 늘리지 않는다** — 메모리 상한은 의도된 설계다.

    고친 것은 「무엇을 기억하는가」가 아니라 「무엇을 기억하고 있다고 말하는가」다.
    """
    tracker = ClockSkewTracker(latency_capacity=50)
    _feed(tracker, hour=9, count=200, latency=0.5)
    stats = tracker.delivery_latency_seconds()
    assert stats is not None
    assert stats["capacity"] == 50.0
    assert stats["samples"] == 50.0
    assert stats["observed_total"] == 200.0


def test_an_untruncated_day_matches_between_the_two_blocks():
    """절단이 없으면 두 모집단이 같은 것이어야 한다 — 라벨이 거짓말을 하지 않는지 본다."""
    tracker = ClockSkewTracker(latency_capacity=1_000)
    _feed(tracker, hour=10, count=120, latency=0.3)

    stats = tracker.delivery_latency_seconds()
    by_hour = tracker.delivery_latency_by_hour()
    assert stats is not None and stats["truncated"] is False
    assert by_hour["10"]["samples"] == by_hour["10"]["samples_tail"]
    assert by_hour["10"]["p90"] == by_hour["10"]["p90_tail"]
