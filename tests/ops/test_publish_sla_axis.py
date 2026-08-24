"""F-21 — 발행 예산은 **대기를 걷어낸 값**에 건다 (2026-08-24 사용자 조치 4 결정).

## 무엇이 틀렸었나

`SYSTEM.md` 불변원칙 3의 「유예 500ms」가 한 상수(`_BOUNDARY_GRACE_SECONDS`)로 구현돼
있었고, 그 상수가 **기다리는 시간이자 그 안에 끝내야 하는 마감**이었다. 합성 스케줄러가
경계 + 0.5초에 발사하므로 3m~30m의 발행 오프셋은 정의상 `500ms + 계산시간`이다 —
2거래일 598건 중 500ms 미만이 **0건**이고 최소가 532ms였다. 1분짜리만 「지킨」 이유는
빨라서가 아니라 그 경로만 이 위상을 안 타기 때문이다.

## 실측 (2026-08-21·08-24, 합성 계열 598건)

    순수 계산 시간 = 합성봉 발행 − max(위상 500ms, 마지막 1분봉 발행)
    p50 111ms · p90 289ms · p99 479ms · **최대 602ms** · 1초 이내 100%

예산 1,000ms는 그 최대 위로 66% 여유다.
"""

from __future__ import annotations

from messiah.ops.integrity_report import _publish_sla_axis, publish_sla_ms


def _session(**over) -> dict:
    base = {
        "sla_ms": 1000.0,
        "p50": 111.0,
        "p90": 289.0,
        "p99": 479.0,
        "max": 602.0,
        "samples": 598.0,
        "over_sla": 0.0,
        "by_horizon": {
            "3m": {"p50": 95.0, "p90": 679.0, "max": 5029.0, "samples": 272.0, "over_sla": 3.0},
            "30m": {"p50": 180.0, "p90": 1212.0, "max": 1841.0, "samples": 28.0, "over_sla": 2.0},
        },
    }
    base.update(over)
    return base


def test_the_budget_comes_from_the_engine_constant():
    """**숫자를 새로 쓰지 않는다** — 엔진이 경보하는 값과 리포트가 채점하는 값이 같아야."""
    assert publish_sla_ms() == 1000.0


def test_a_measured_session_is_scored():
    axis = _publish_sla_axis(_session())
    assert axis is not None
    assert axis["sla_ms"] == 1000.0
    assert axis["p99_ms"] == 479.0
    assert axis["max_ms"] == 602.0
    assert axis["over_sla"] == 0.0
    assert axis["over_sla_ratio"] == 0.0
    assert axis["horizons"]["3m"]["over_sla"] == 3.0
    # **판정하지 않는다**(R18) — 새 축이 며칠 돌아 본 뒤 사람이 승격한다.
    assert axis["verdict"] == "recorded_only"


def test_the_measured_distribution_sits_well_inside_the_budget():
    """실측이 예산 안에 있다는 사실 자체를 테스트가 붙들고 있는다.

    이 값들이 예산을 넘기 시작하면 그때가 예산을 다시 볼 때다 — 그 판단의 근거가
    코드 주석이 아니라 실행되는 검사여야 한다.
    """
    measured = _session()
    assert measured["max"] < publish_sla_ms(), "관측 최대가 예산 안쪽이어야 한다"
    assert measured["p99"] * 2 <= publish_sla_ms(), "p99의 2배 이상 여유"


def test_a_session_without_the_axis_is_unmeasured_not_clean():
    """F-21 이전 로그에는 이 블록이 없다 — **0건이 아니라 못 잼**이다(L18)."""
    assert _publish_sla_axis(None) is None
    assert _publish_sla_axis({}) is None


def test_zero_samples_is_not_a_perfect_score():
    """표본 0으로 「초과 0건」을 찍으면 발행이 없던 날이 만점이 된다."""
    assert _publish_sla_axis(_session(samples=0.0)) is None


def test_the_ratio_is_none_when_the_count_is_missing():
    axis = _publish_sla_axis(_session(over_sla=None))
    assert axis is not None
    assert axis["over_sla_ratio"] is None


def test_a_breaching_session_reports_the_ratio():
    axis = _publish_sla_axis(_session(over_sla=6.0))
    assert axis is not None
    assert axis["over_sla_ratio"] == round(6.0 / 598.0, 4)
