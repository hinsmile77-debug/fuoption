"""F-19 — 기한이 산술적으로 닿는지를 **기한 전에** 묻는다 (2026-08-24 이상점 1-3 · 1-4).

종전에는 `기한 불가` 판정이 **기한이 지난 뒤에야** 났다 — 이미 늦은 뒤에 늦었다고 말한
셈이다. 2026-08-18에 세 항목이 그렇게 걸렸고 처방은 매번 "기한을 다시 잡아라"였다.
사람이 기한을 옮길 수 있는 시점은 아직 기한 안일 때뿐이다.
"""

from __future__ import annotations

from datetime import date

from messiah.ops.fix_verification import (
    PendingVerification,
    VerificationStatus,
    deadline_pressure,
    evaluate,
)


def _item(**over) -> PendingVerification:
    base = dict(
        id="x",
        summary="",
        registered=date(2026, 8, 17),
        metric="restarts",
        consecutive_days=3,
        max_value=0.0,
    )
    base.update(over)
    return PendingVerification(**base)


def _report(day: date, restarts: int = 0) -> dict:
    return {"date": day.isoformat(), "restarts": restarts}


def test_unreachable_is_seen_before_the_deadline_passes():
    """08-21(금)에 기한이 08-24(월)이고 3거래일이 필요하면 남은 거래일은 1일이다."""
    pressure = deadline_pressure(
        _item(deadline=date(2026, 8, 24)), clean_streak=0, today=date(2026, 8, 21), report_days=[]
    )
    assert pressure["days_needed"] == 3
    assert pressure["days_remaining"] == 1
    assert pressure["reachable"] is False


def test_reachable_when_the_window_still_fits():
    pressure = deadline_pressure(
        _item(deadline=date(2026, 9, 4)), clean_streak=1, today=date(2026, 8, 24), report_days=[]
    )
    assert pressure["days_needed"] == 2
    assert pressure["reachable"] is True


def test_already_satisfied_streak_is_always_reachable():
    pressure = deadline_pressure(
        _item(deadline=date(2026, 8, 24)), clean_streak=3, today=date(2026, 8, 24), report_days=[]
    )
    assert pressure["days_needed"] == 0
    assert pressure["reachable"] is True


def test_trading_day_deadline_not_yet_reached_is_not_judged():
    """G-14 항목은 기한 자체가 아직 안 왔다 — 「여유 없음」이 아니라 「모른다」다."""
    pressure = deadline_pressure(
        _item(deadline=None, deadline_trading_days=20),
        clean_streak=0,
        today=date(2026, 8, 24),
        report_days=[date(2026, 8, 25)],
    )
    assert pressure["deadline"] is None
    assert pressure["reachable"] is None


def test_verdict_says_unreachable_before_the_deadline():
    items = [_item(deadline=date(2026, 8, 24))]
    reports = {date(2026, 8, 20): _report(date(2026, 8, 20))}
    verdict = evaluate(items, reports, today=date(2026, 8, 21))[0]
    assert verdict.status == VerificationStatus.UNREACHABLE
    assert "지금" in verdict.detail, "처방은 「고쳐라」가 아니라 「지금 기한을 다시 잡아라」다"


def test_a_passed_deadline_still_gets_the_old_two_way_split():
    """기한이 **지난** 항목은 종전 판정(채점 가능일로 가르는 쪽)이 그대로 본다.

    그 구별(「못 고쳤다」 vs 「잴 날이 없었다」)은 2026-08-18에 얻은 것이라
    사전 경보가 가로채면 안 된다.
    """

    # 창은 세 날 다 있었는데 가운데 하루가 **판정 불가**라 연속이 안 찼다.
    def _crash(day: date, available: bool = True, count: int = 0) -> dict:
        return {
            "date": day.isoformat(),
            "native_crashes": {"available": available, "count": count, "details": []},
        }

    items = [_item(metric="native_crashes", deadline=date(2026, 8, 20), consecutive_days=3)]
    reports = {
        date(2026, 8, 18): _crash(date(2026, 8, 18)),
        date(2026, 8, 19): _crash(date(2026, 8, 19), available=False),  # 못 잼
        date(2026, 8, 20): _crash(date(2026, 8, 20)),
    }
    verdict = evaluate(items, reports, today=date(2026, 8, 21))[0]
    assert verdict.status == VerificationStatus.OVERDUE
    assert "경과" in verdict.detail


def test_self_check_line_reproduces_20260824():
    """실제 등록부·리포트로 2026-08-24 아침을 재현한다."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path("scripts").resolve()))
    import self_check

    result = self_check.check_pending_deadlines(today=date(2026, 8, 24))
    # `[OK ]`를 깨지 않는다 — 기한이 촉박한 것은 오늘 수집을 막을 이유가 아니다.
    assert result.ok is True
    assert "no-degenerate-features" in result.detail
    assert "기한 도달 불가 1건" in result.detail
