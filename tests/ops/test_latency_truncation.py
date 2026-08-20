"""하루 지연이라 부른 숫자가 끝 한 토막이었다 — 2026-08-20 F-H (C-6).

2026-08-20 `TickDeliveryLatency`의 표본 수가 정확히 상한값 `20000.0`이었다. 같은 세션의
`TickArchiveSummary`는 **137,977행**을 보고한다. 즉 그 p90 921ms는 하루가 아니라 **세션 끝
토막**의 값이었고, 그 사실을 로그도 리포트도 말하지 않았다 — `observe()`의 docstring이
*"세션 전체 목록에 쌓는다"* 라고 적혀 있었기 때문에 아무도 절단을 의심하지 않았다.

그날 그 값으로 세 가지가 동시에 무너졌다:

1. `J-10`/`L-7` 판정 — *"1,000ms 미만이니 완성봉 유예 상향 조건 미충족"*.
2. `C-6`(오프셋 악화가 회선인가 내부 적체인가) — **원자료가 링버퍼에 덮여 손으로도 못 푼다.**
3. `MinuteBarAggregator.flush_due()`의 유예 근거가 편향된 분포를 받는다.

표본 20,000은 「20,000건 봤다」가 아니라 **「20,000건까지만 기억한다」**다.
`references/phases.md` D절 *"건수 0은 두 가지다"* 와 같은 계열의 구분이다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from messiah.core.timeutil import KST
from messiah.ops.clock_skew import ClockSkewTracker
from messiah.ops.fix_verification import _latency


def _feed(tracker: ClockSkewTracker, n: int, *, start_hour: int = 9, step_ms: int = 100) -> None:
    base = datetime(2026, 8, 20, start_hour, 0, tzinfo=KST)
    for index in range(n):
        exchange = base + timedelta(milliseconds=index * step_ms)
        received = exchange - timedelta(milliseconds=200 + (index % 50))
        tracker.observe(exchange, received)


def test_untruncated_session_says_so() -> None:
    """상한에 안 닿은 날은 조용하다 — 매일 울면 아무도 안 읽는다."""
    tracker = ClockSkewTracker(latency_capacity=1000)
    _feed(tracker, 300)
    stats = tracker.delivery_latency_seconds()
    assert stats is not None
    assert stats["truncated"] is False
    assert stats["observed_total"] == 300.0
    assert stats["samples"] == 300.0


def test_truncated_session_is_named() -> None:
    """**2026-08-20의 재현.** 관측이 상한을 넘으면 그 사실이 값과 함께 나온다."""
    tracker = ClockSkewTracker(latency_capacity=100)
    _feed(tracker, 1000)
    stats = tracker.delivery_latency_seconds()
    assert stats is not None
    assert stats["truncated"] is True
    assert stats["observed_total"] == 1000.0
    assert stats["samples"] == 100.0, "링버퍼가 남긴 것은 끝 100건뿐이다"
    assert stats["capacity"] == 100.0


def test_registry_refuses_to_judge_on_a_truncated_day() -> None:
    """전제가 하루의 일부만 본 값이면 그 채점은 근거가 없다 — 통과로도 위반으로도 안 센다."""
    truncated = {"delivery_latency": {"p99": 0.9, "truncated": True}}
    assert _latency(truncated, "p99") is None

    full = {"delivery_latency": {"p99": 0.9, "truncated": False}}
    assert _latency(full, "p99") == 0.9


def test_old_reports_keep_their_meaning() -> None:
    """`truncated` 키가 없던 과거 리포트를 소급해 뒤집지 않는다."""
    legacy = {"delivery_latency": {"p99": 0.88}}
    assert _latency(legacy, "p99") == 0.88


def test_hourly_buckets_survive_the_ring_buffer() -> None:
    """**C-6의 답을 영구히 가능하게 하는 구조.**

    원자료는 덮여도 시간대별 모양은 남는다. 링버퍼 용량을 올릴 필요가 없는 이유이기도 하다 —
    문제는 용량이 아니라 침묵이었다.
    """
    tracker = ClockSkewTracker(latency_capacity=50)
    _feed(tracker, 400, start_hour=9)
    _feed(tracker, 400, start_hour=14)
    by_hour = tracker.delivery_latency_by_hour()
    assert set(by_hour) >= {"09", "14"}, "덮인 09시가 사라지면 하루의 모양을 못 본다"
    assert by_hour["09"]["samples"] == 400.0
    stats = tracker.delivery_latency_seconds()
    assert stats is not None and stats["samples"] == 50.0


def test_hourly_buckets_use_exchange_time() -> None:
    """로컬 시계는 **지금 재고 있는 대상**이다 — 그것으로 눈금을 나누면 안 된다."""
    tracker = ClockSkewTracker(latency_capacity=1000)
    exchange = datetime(2026, 8, 20, 14, 0, tzinfo=KST)
    for index in range(60):
        # 로컬 수신은 13시대인데 거래소 시각은 14시대다 — 버킷은 14시여야 한다.
        received = exchange.replace(hour=13) + timedelta(seconds=index)
        tracker.observe(exchange + timedelta(seconds=index), received)
    assert set(tracker.delivery_latency_by_hour()) == {"14"}
