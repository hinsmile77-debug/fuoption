"""F-30 — 틱 하나가 몇 ms 일찍 발사됐다고 「영구 소실」이 되지 않는다 (2026-08-24).

2026-08-24 리포트는 이상점 1-14를 *"09:31 사이클 2/3다리 — 영구 소실 · 이번 달 세 번째"* ·
`P1`로 올렸다. **잃은 자료는 없었다.** 실측:

    아카이브 1,302행 = 434사이클 × 3다리 (정확히 나누어떨어진다)
    다리 수가 3이 아닌 분 버킷은 09:30·09:31 둘뿐이고 합이 6이다
    09:30:59.994 에 발사된 틱 하나의 첫 다리가 앞 분 버킷에 떨어졌다

`_leg_completeness()`가 연속 계열을 **벽시계 분**으로 묶은 탓이다. 한 사이클 안에서 같은
다리 키는 정확히 한 번 오므로, 키가 되풀이되는 순간을 다음 사이클의 시작으로 본다 —
발사 시각의 흔들림과 무관한 규칙이다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from messiah.core.timeutil import KST
from messiah.ops.series_coverage import measure, session_window

_DAY = datetime(2026, 8, 24, tzinfo=KST).date()
_SECTORS = ("F001", "OC01", "OP01")


def _cycle(base: datetime, offsets=(0.0, 1.0, 2.0)):
    """한 사이클 3다리 — 초 단위 간격은 실측(1초 페이싱)."""
    return [(base + timedelta(seconds=off), key) for off, key in zip(offsets, _SECTORS)]


def _measure(pairs):
    pairs = sorted(pairs)
    return measure(
        "flow_intraday/K2I",
        [ts for ts, _ in pairs],
        window=session_window(_DAY),
        leg_keys=[key for _, key in pairs],
    )


def _straight_day(n_cycles: int = 30, *, skip: dict[int, tuple[str, ...]] | None = None):
    """09:00부터 1분 격자로 연속한 하루 — 분이 끊기면 `_group_into_cycles()`가 블록을
    쪼개 「판정하지 않음」 구간으로 빠지므로, 픽스처는 반드시 연속이어야 한다."""
    skip = skip or {}
    base = datetime(2026, 8, 24, 9, 0, tzinfo=KST)
    out = []
    for i in range(n_cycles):
        legs = _cycle(base + timedelta(minutes=i))
        missing = skip.get(i, ())
        out += [(ts, key) for ts, key in legs if key not in missing]
    return out


def test_clean_day_has_no_short_cycles():
    coverage = _measure(_straight_day())
    assert coverage.expected_legs == 3
    assert coverage.short_cycles == []


def test_a_tick_firing_six_milliseconds_early_is_not_a_loss():
    """2026-08-24 09:30:59.994의 재현 — 앞 분으로 5.8ms 넘어간 첫 다리."""
    pairs = _straight_day()
    # 09:15 사이클이 6ms 일찍 발사돼 첫 다리가 09:14 버킷으로 넘어간 형태.
    pairs = [p for p in pairs if not (p[0].hour == 9 and p[0].minute == 15)]
    early_base = datetime(2026, 8, 24, 9, 15, tzinfo=KST) - timedelta(milliseconds=6)
    pairs += _cycle(early_base, offsets=(0.0, 1.36, 2.70))

    coverage = _measure(pairs)
    assert coverage.expected_legs == 3
    assert coverage.short_cycles == [], "다리는 셋 다 왔다 — 분 경계만 넘었을 뿐이다"


def test_a_real_missing_leg_still_rings():
    """인공물을 지우면서 진짜 결손까지 못 보게 되면 아무것도 고친 게 아니다."""
    coverage = _measure(_straight_day(skip={10: ("OP01",)}))
    assert coverage.expected_legs == 3
    assert coverage.short_cycles == [("09:10", 2)]


def test_a_slow_retry_stays_in_its_own_cycle():
    """재시도 예산은 40초다(`data/poll_retry.RETRY_BUDGET_SECONDS`).

    시간 간격으로 자르는 규칙이었다면 이 다리가 다음 사이클로 튕겨 나가 사이클 둘이
    동시에 결손으로 보였을 것이다.
    """
    pairs = _straight_day(skip={15: ("OP01",)})
    slow = datetime(2026, 8, 24, 9, 15, tzinfo=KST)
    pairs.append((slow + timedelta(seconds=38), "OP01"))  # 재시도로 늦었지만 같은 사이클이다

    coverage = _measure(pairs)
    assert coverage.short_cycles == []


def test_consecutive_cycles_missing_the_same_leg_are_still_split():
    """같은 다리가 연속으로 빠져도 사이클 경계는 첫 키의 되풀이가 잡는다."""
    coverage = _measure(_straight_day(skip={12: ("OP01",), 13: ("OP01",)}))
    assert coverage.short_cycles == [("09:12", 2), ("09:13", 2)]
