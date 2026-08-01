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


def test_resume_into_warning_band_still_counts_as_resumed():
    """2026-07-31 실측 회귀 — 봉은 "다음 분의 첫 틱"이 와야 확정되므로, 한산한 구간에서
    데이터가 돌아와도 첫 재평가의 data_age가 WARNING 대역(90~150초)에 떨어지는 일이 흔하다.
    예전 조건(`new_phase == NORMAL`)은 이 경우를 놓쳐 해제가 조용히 건너뛰어졌다."""
    monitor = CircuitBreakerMonitor(config=_CFG)
    monitor.observe(300.0, _START)
    resume_at = _START + timedelta(seconds=310)
    resumed_event = monitor.observe(100.0, resume_at)  # WARNING 대역 — NORMAL까지 안 내려옴

    assert resumed_event.phase == CircuitBreakerPhase.WARNING
    assert resumed_event.just_resumed is True
    assert resumed_event.reentry_cooldown_until == resume_at + timedelta(minutes=10.0)


def test_resume_into_suspected_band_still_counts_as_resumed():
    monitor = CircuitBreakerMonitor(config=_CFG)
    monitor.observe(300.0, _START)
    resume_at = _START + timedelta(seconds=310)
    resumed_event = monitor.observe(160.0, resume_at)

    assert resumed_event.phase == CircuitBreakerPhase.SUSPECTED
    assert resumed_event.just_resumed is True


def test_re_escalation_after_partial_resume_confirms_again():
    """WARNING까지만 내려왔다가 다시 나빠지면 정지가 **다시** 걸려야 한다 — 해제 조건을
    넓힌 대가로 조기 재개가 방치되지 않는다는 것의 근거."""
    monitor = CircuitBreakerMonitor(config=_CFG)
    monitor.observe(300.0, _START)
    monitor.observe(100.0, _START + timedelta(seconds=310))  # 부분 해제(WARNING)

    re_confirmed = monitor.observe(250.0, _START + timedelta(seconds=460))
    assert re_confirmed.phase == CircuitBreakerPhase.CONFIRMED
    assert re_confirmed.just_confirmed is True


def test_partial_resume_still_blocks_entry_via_cooldown():
    """WARNING으로만 내려온 해제도 재진입 관망이 그대로 걸린다 — "완전 정상"이 아닌 상태로
    해제를 인정해도 안전한 이유."""
    monitor = CircuitBreakerMonitor(config=_CFG)
    monitor.observe(300.0, _START)
    resume_at = _START + timedelta(seconds=310)
    monitor.observe(100.0, resume_at)

    assert monitor.blocks_entry(resume_at + timedelta(minutes=5)) is True
    assert monitor.blocks_entry(resume_at + timedelta(minutes=10, seconds=1)) is False


def test_single_jump_from_normal_to_confirmed_is_still_just_confirmed():
    """이벤트 구동 파이프라인에서는 정지 중 호출 자체가 없어 NORMAL에서 곧바로 CONFIRMED로
    한 번에 건너뛸 수 있다 — 그래도 just_confirmed는 정확히 감지돼야 한다."""
    monitor = CircuitBreakerMonitor(config=_CFG)
    event = monitor.observe(1400.0, _START)
    assert event.just_confirmed is True
    assert event.phase == CircuitBreakerPhase.CONFIRMED


# ------------------------------------------------- 한산 vs 단절 (2026-07-31, 수집기 heartbeat)


def test_healthy_collector_caps_escalation_at_suspected():
    """2026-07-31 실측 회귀 — 상한가 고착으로 분당 1~17계약만 체결되던 구간에서 봉이 안
    만들어지자 CB가 하루 5회 확정을 냈다(실제 거래소 CB는 없었음). 수집기가 "내 연결은
    멀쩡하다"고 말하는 동안엔 정지까지 가지 않는다."""
    monitor = CircuitBreakerMonitor(config=_CFG)

    event = monitor.observe(600.0, _START, collector_healthy=True)

    assert event.phase == CircuitBreakerPhase.SUSPECTED  # 의심까지는 올라간다(침묵 아님)
    assert event.just_confirmed is False
    assert monitor.blocks_entry(_START) is False  # 거래를 막지는 않는다


def test_unhealthy_collector_still_confirms():
    """수집기 스스로 흐름이 이상하다고 말하면 정지가 걸려야 한다 — 억제는 OK일 때만."""
    monitor = CircuitBreakerMonitor(config=_CFG)

    event = monitor.observe(600.0, _START, collector_healthy=False)

    assert event.phase == CircuitBreakerPhase.CONFIRMED
    assert event.just_confirmed is True


def test_unknown_collector_state_does_not_suppress_confirmation():
    """heartbeat를 못 받은 상태(수집기 프로세스가 죽었을 수도 있다)를 정상으로 오해하면
    안 된다 — 모르면 기존대로 확정한다."""
    monitor = CircuitBreakerMonitor(config=_CFG)

    assert monitor.observe(600.0, _START, collector_healthy=None).just_confirmed is True


def test_collector_going_unhealthy_confirms_after_earlier_suppression():
    """한산으로 억제되던 중에 수집기가 실제로 나빠지면 그때 정지가 걸린다."""
    monitor = CircuitBreakerMonitor(config=_CFG)
    monitor.observe(600.0, _START, collector_healthy=True)

    event = monitor.observe(660.0, _START + timedelta(seconds=60), collector_healthy=False)

    assert event.phase == CircuitBreakerPhase.CONFIRMED
    assert event.just_confirmed is True


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
