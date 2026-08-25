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
    """2026-08-24 아침의 그 문장을 **픽스처로** 재현한다.

    ## 왜 실제 등록부를 안 읽는가 (2026-08-25)

    종전 이 테스트는 `configs/pending_verifications.yaml`을 그대로 읽어 `no-degenerate-features`
    가 「기한 도달 불가」로 뜨는지 봤다. 그 항목은 2026-08-25에 **검증을 마치고 등록부에서
    나갔고**(streak 3/3), 그러자 이 테스트가 깨졌다.

    깨진 것이 옳다 — 등록부는 **매일 바뀌는 운영 파일**이고, 항목이 통과해서 사라지는 것이
    그 정상 수명이다. 역사적 사실을 가변 파일에 고정하면, 사실이 변한 것이 아니라 파일이
    변했을 뿐인 날에도 빨간불이 켜진다. 그 빨간불은 아무 처방으로도 이어지지 않는다.

    그래서 재현할 값(그날의 등록부 모양)은 여기 픽스처로 박고, 실제 파일에 대해서는
    **모양이 아니라 계기가 도는지**만 아래 테스트가 본다.
    """
    # 2026-08-24 아침의 그 항목: 대응 수정이 08-20 저녁에 들어가 첫 채점이 08-21,
    # 3거래일 연속이 필요한데 기한은 08-24 — 그날 남은 거래일은 0일이었다.
    item = _item(
        id="no-degenerate-features",
        metric="degenerate_feature_count",
        deadline=date(2026, 8, 24),
        consecutive_days=3,
        registered=date(2026, 8, 6),
    )
    pressure = deadline_pressure(
        item, clean_streak=2, today=date(2026, 8, 24), report_days=[date(2026, 8, 21)]
    )
    assert pressure["reachable"] is False
    assert pressure["days_remaining"] == 0
    assert pressure["days_needed"] == 1


def test_the_self_check_line_still_renders_against_the_live_registry():
    """계기가 실제 등록부에서 **돌기는 하는가** — 내용이 아니라 동작을 본다.

    `[OK ]`를 깨지 않는다는 것이 이 줄의 설계다(기한이 촉박한 것은 오늘 수집을 막을 이유가
    아니다). 그 성질은 등록부에 무엇이 들어 있든 참이어야 한다.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path("scripts").resolve()))
    import self_check

    result = self_check.check_pending_deadlines(today=date(2026, 8, 25))
    assert result.ok is True
    assert "등록부" in result.detail
    # **「기한 도달 불가」가 0건이라고 말할 수 있어야 한다** — 문장 자체가 빠지면
    # 「없다」와 「안 셌다」가 구분되지 않는다(L18).
    assert "기한 도달 불가" in result.detail
