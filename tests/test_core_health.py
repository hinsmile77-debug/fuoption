"""컴포넌트 heartbeat 검증 — 고도화 1 (2026-07-30).

배경은 `core/health.py` 모듈 docstring 참고: 2026-07-30 점검의 이상점 4건이 전부 "죽어도
화면에 아무 표시가 없다"는 같은 형태였다. 여기서 못박는 핵심 계약은 세 가지다 —
① 정상일 때도 계속 발행한다(침묵 자체가 신호가 되려면) ② 발행 실패가 루프를 못 죽인다
③ "아직 못 받음"을 "끊김"으로 오판하지 않는다.
"""

from __future__ import annotations

import pytest

from messiah.core.health import (
    HealthReporter,
    HealthStatus,
    health_cache_key,
    staleness_status,
)
from messiah.core.messages import Health, HealthLevel


class _StopLoop(Exception):
    """무한 heartbeat 루프를 테스트에서 끊는 신호."""


class _FakeBus:
    def __init__(self, *, fail: bool = False) -> None:
        self.published: list[tuple[str, Health]] = []
        self._fail = fail

    async def publish(self, topic: str, message: Health) -> None:
        if self._fail:
            raise ConnectionError("redis down")
        self.published.append((topic, message))


class _Ticker:
    def __init__(self, max_calls: int) -> None:
        self.calls = 0
        self._max = max_calls

    async def __call__(self, _seconds: float) -> None:
        if self.calls >= self._max:
            raise _StopLoop
        self.calls += 1


# ---------------------------------------------------------------- 발행 계약


async def test_publishes_ok_heartbeat_without_a_probe():
    bus = _FakeBus()

    message = await HealthReporter(bus, "l1.collector", pid=4242).publish_once()

    assert message is not None
    topic, published = bus.published[0]
    assert topic == "sys.health"
    assert published.component == "l1.collector"
    assert published.level is HealthLevel.OK
    assert published.pid == 4242


async def test_probe_result_is_reported_verbatim():
    bus = _FakeBus()
    probe = lambda: HealthStatus(HealthLevel.WARN, "60초간 수신 없음")  # noqa: E731

    await HealthReporter(bus, "l1.collector", probe=probe).publish_once()

    _, published = bus.published[0]
    assert published.level is HealthLevel.WARN
    assert published.detail == "60초간 수신 없음"


async def test_probe_failure_is_reported_as_critical_not_swallowed():
    """상태를 못 재는 것도 하나의 상태다 — 조용히 OK로 넘어가면 안 된다."""
    bus = _FakeBus()

    def _broken_probe():
        raise RuntimeError("boom")

    await HealthReporter(bus, "l1.collector", probe=_broken_probe).publish_once()

    _, published = bus.published[0]
    assert published.level is HealthLevel.CRITICAL
    assert "boom" in published.detail


async def test_publish_failure_does_not_raise():
    """heartbeat 발행 실패가 수집 루프를 죽이면 부가 기능이 본 임무를 막는 것이다(L22)."""
    bus = _FakeBus(fail=True)

    assert await HealthReporter(bus, "l1.collector").publish_once() is None


async def test_run_forever_beats_even_while_healthy():
    """ "문제 있을 때만 알린다"면 프로세스가 통째로 죽었을 때 아무 신호도 안 난다 —
    2026-07-30 UI 크래시가 정확히 그 경우였다."""
    bus = _FakeBus()
    ticker = _Ticker(max_calls=3)

    with pytest.raises(_StopLoop):
        await HealthReporter(bus, "l1.collector", sleep=ticker).run_forever()

    assert len(bus.published) == 4  # 첫 발행 + 대기 3회마다 1회씩


async def test_run_forever_publishes_before_first_sleep():
    """기동 직후 한 주기 동안 화면이 "데이터 없음"으로 보이지 않아야 한다."""
    bus = _FakeBus()
    ticker = _Ticker(max_calls=0)

    with pytest.raises(_StopLoop):
        await HealthReporter(bus, "l1.collector", sleep=ticker).run_forever()

    assert len(bus.published) == 1


async def test_run_forever_survives_publish_failures():
    bus = _FakeBus(fail=True)
    ticker = _Ticker(max_calls=3)

    with pytest.raises(_StopLoop):  # 발행 예외가 아니라 테스트 신호로만 끝나야 한다
        await HealthReporter(bus, "l1.collector", sleep=ticker).run_forever()

    assert ticker.calls == 3


# ---------------------------------------------------------------- 신선도 판정


def test_never_received_is_not_treated_as_an_outage():
    """08:35 기동 후 첫 틱(실측 08:45)까지 10분을 장애로 표시하면 매일 아침 거짓 경보다."""
    status = staleness_status(None, warn_after=60.0, critical_after=120.0)

    assert status.level not in (HealthLevel.WARN, HealthLevel.CRITICAL)
    assert "웜업" in status.detail


def test_never_received_is_not_called_ok_either():
    """**"장애가 아니다"와 "정상이다"는 다르다** (2026-08-05 2차, 고도화 3).

    한 건도 못 받은 상태가 `OK`로 나가면 화면·상태판에서 초록으로 보이고, 더 나쁜 것은
    `TradingPipeline._collector_reports_healthy()`가 그걸 "한산하다"로 읽어 **서킷브레이커
    승격을 막는다는 점**이다 — 데이터가 0건인 것이 CB 억제 근거로 쓰였다(08:36~08:45의
    9분, 그리고 재연결 직후마다).
    """
    status = staleness_status(None, warn_after=60.0, critical_after=120.0)

    assert status.level is HealthLevel.UNKNOWN


@pytest.mark.parametrize(
    "age,expected",
    [
        (0.0, HealthLevel.OK),
        (59.0, HealthLevel.OK),
        (60.0, HealthLevel.WARN),
        (119.0, HealthLevel.WARN),
        (120.0, HealthLevel.CRITICAL),
        (3600.0, HealthLevel.CRITICAL),
    ],
)
def test_staleness_thresholds_are_inclusive_at_the_boundary(age, expected):
    assert staleness_status(age, warn_after=60.0, critical_after=120.0).level is expected


def test_health_cache_key_separates_components():
    """`sys.health`는 여러 컴포넌트가 같은 토픽에 발행한다 — 키가 안 갈리면 마지막
    발행자만 남아 나머지 컴포넌트가 화면에서 사라진다."""
    assert health_cache_key("l1.collector") != health_cache_key("g2.pipeline")
    assert health_cache_key("l1.collector") == "health:l1.collector"


# ---------------------------------------------------------------- 문구 (2026-08-05 3차, P1-1)


def test_staleness_wording_defaults_to_reception():
    """수집기 등 "받는" 축의 기존 문구는 그대로 — 기존 화면 표현이 안 바뀐다."""
    assert "최근 수신 3초 전" == staleness_status(3.0, warn_after=60.0, critical_after=120.0).detail


def test_staleness_wording_follows_what_is_actually_measured():
    """**재는 대상이 다르면 같은 단어를 쓰지 않는다.**

    `FeatureEngine.health()`는 `seconds_since_last_publish()`를 넘기는데 문구는 "최근 수신"으로
    하드코딩돼 있었다. 그래서 화면에 이렇게 떴다:

        수집기(WS) — OK · 최근 수신 0초 전     피처엔진 — OK · 최근 수신 54초 전

    읽는 사람은 54초짜리 정체로 읽는다. 실제로는 M1 봉 주기(60초) 안의 정상 발행 간격이었다.
    """
    status = staleness_status(54.0, warn_after=120.0, critical_after=240.0, subject="발행")

    assert status.level is HealthLevel.OK
    assert status.detail == "최근 발행 54초 전"
    assert "수신" not in status.detail


def test_staleness_wording_applies_to_warn_and_critical_too():
    kwargs = dict(warn_after=120.0, critical_after=240.0, subject="발행")

    assert staleness_status(130.0, **kwargs).detail == "130초간 발행 없음"
    assert staleness_status(300.0, **kwargs).detail == "300초간 발행 없음"


def test_feature_engine_reports_publishing_not_reception():
    """발행측 결선까지 못박는다 — `core/health.py`만 고치고 호출부를 안 바꾸면 무의미하다."""
    from messiah.features.engine import FeatureEngine

    engine = FeatureEngine.__new__(FeatureEngine)
    engine.seconds_since_last_publish = lambda: 54.0  # type: ignore[method-assign]
    engine._last_nan_ratio = {}  # type: ignore[attr-defined]

    assert "발행" in engine.health().detail
    assert "최근 수신" not in engine.health().detail


# ------------------------------------------------- heartbeat가 실어 나르는 코드 버전 (P0-1)


async def test_heartbeat_carries_the_code_version_it_was_sent_from():
    """2026-08-05엔 초록 신호등을 보낸 쪽이 3시간 전 코드였는데 알 방법이 없었다."""
    from messiah.core.version import PROCESS_GIT_SHA, PROCESS_STARTED_AT

    bus = _FakeBus()
    message = await HealthReporter(bus, "l1.collector").publish_once()

    assert message is not None
    assert message.git_sha == PROCESS_GIT_SHA
    assert message.started_at_utc == PROCESS_STARTED_AT


def test_health_defaults_to_an_empty_sha_so_old_processes_are_detectable():
    """기본값이 빈 문자열인 것이 핵심 — **미보고 자체가 구버전의 증거**가 된다."""
    assert Health(component="x", level=HealthLevel.OK).git_sha == ""
    assert Health(component="x", level=HealthLevel.OK).started_at_utc is None
