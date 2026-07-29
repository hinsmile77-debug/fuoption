from datetime import datetime, timedelta, timezone

from messiah.risk.circuit_breaker_monitor import (
    CircuitBreakerMonitor,
    CircuitBreakerMonitorConfig,
    CircuitBreakerPhase,
)

_START = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)
_CFG = CircuitBreakerMonitorConfig(
    warning_seconds=90.0,
    suspected_seconds=150.0,
    confirmed_seconds=240.0,
    reentry_cooldown_minutes=10.0,
)


def test_stays_normal_below_warning_threshold():
    monitor = CircuitBreakerMonitor(config=_CFG)
    event = monitor.observe(60.0, _START)
    assert event.phase == CircuitBreakerPhase.NORMAL
    assert event.just_confirmed is False


def test_escalates_through_warning_and_suspected():
    monitor = CircuitBreakerMonitor(config=_CFG)
    warning_event = monitor.observe(95.0, _START)
    assert warning_event.phase == CircuitBreakerPhase.WARNING

    suspected_event = monitor.observe(160.0, _START + timedelta(seconds=65))
    assert suspected_event.phase == CircuitBreakerPhase.SUSPECTED
    assert suspected_event.just_confirmed is False


def test_confirms_at_threshold_exactly_once():
    monitor = CircuitBreakerMonitor(config=_CFG)
    monitor.observe(95.0, _START)
    monitor.observe(160.0, _START + timedelta(seconds=65))
    confirmed_event = monitor.observe(240.0, _START + timedelta(seconds=145))
    assert confirmed_event.phase == CircuitBreakerPhase.CONFIRMED
    assert confirmed_event.just_confirmed is True

    # 같은 CONFIRMED 상태가 이어지는 다음 호출은 just_confirmed가 다시 True이면 안 됨
    still_confirmed = monitor.observe(270.0, _START + timedelta(seconds=175))
    assert still_confirmed.phase == CircuitBreakerPhase.CONFIRMED
    assert still_confirmed.just_confirmed is False


def test_resume_transition_sets_just_resumed_and_cooldown():
    monitor = CircuitBreakerMonitor(config=_CFG)
    monitor.observe(300.0, _START)
    resume_at = _START + timedelta(seconds=310)
    resumed_event = monitor.observe(0.5, resume_at)

    assert resumed_event.phase == CircuitBreakerPhase.NORMAL
    assert resumed_event.just_resumed is True
    assert resumed_event.reentry_cooldown_until == resume_at + timedelta(minutes=10.0)


def test_single_jump_from_normal_to_confirmed_is_still_just_confirmed():
    """이벤트 구동 파이프라인에서는 정지 중 호출 자체가 없어 NORMAL에서 곧바로 CONFIRMED로
    한 번에 건너뛸 수 있다 — 그래도 just_confirmed는 정확히 감지돼야 한다."""
    monitor = CircuitBreakerMonitor(config=_CFG)
    event = monitor.observe(1400.0, _START)
    assert event.just_confirmed is True
    assert event.phase == CircuitBreakerPhase.CONFIRMED


def test_blocks_entry_during_confirmed_phase():
    monitor = CircuitBreakerMonitor(config=_CFG)
    monitor.observe(300.0, _START)
    assert monitor.blocks_entry(_START) is True


def test_blocks_entry_during_reentry_cooldown_then_releases():
    monitor = CircuitBreakerMonitor(config=_CFG)
    monitor.observe(300.0, _START)
    resume_at = _START + timedelta(seconds=310)
    monitor.observe(0.5, resume_at)

    assert monitor.blocks_entry(resume_at + timedelta(minutes=5)) is True
    assert monitor.blocks_entry(resume_at + timedelta(minutes=10, seconds=1)) is False


def test_blocks_entry_false_when_never_triggered():
    monitor = CircuitBreakerMonitor(config=_CFG)
    assert monitor.blocks_entry(_START) is False
