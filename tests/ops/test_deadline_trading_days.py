"""G-14 선행 적용 — 기한을 「날짜」가 아니라 「채점 가능한 거래일 수」로 적는다.

달력 날짜로 적으면 휴장·주말이 기한을 조용히 먹는다. 2026-08-18에 세 항목이 정확히
그렇게 `기한 불가`가 됐고, 처방은 매번 "기한을 다시 잡아라"였다 — 반복되는 연장은
곧 기한이 없는 것과 같다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from messiah.ops.fix_verification import (
    METRIC_EXTRACTORS,
    PendingVerification,
    RegistryError,
    effective_deadline,
    load_registry,
)


def _item(**over) -> PendingVerification:
    base = dict(
        id="x",
        summary="",
        registered=date(2026, 8, 24),
        metric="restarts",
        consecutive_days=1,
    )
    base.update(over)
    return PendingVerification(**base)


def test_calendar_deadline_still_wins():
    item = _item(deadline=date(2026, 9, 5))
    assert effective_deadline(item, [date(2026, 8, 25)]) == date(2026, 9, 5)


def test_no_deadline_at_all_is_none():
    assert effective_deadline(_item(), [date(2026, 8, 25)]) is None


def test_trading_day_deadline_is_the_nth_report_day():
    item = _item(deadline_trading_days=3)
    days = [date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26), date(2026, 8, 27)]
    # 등록일(08-24)은 채점 시작점 이전이라 안 센다 — 수정은 그날 장 마감 후에 들어간다.
    assert effective_deadline(item, days) == date(2026, 8, 27)


def test_deadline_not_yet_reached_is_none_not_overdue():
    """**기한이 없는 것과 아직 안 온 것을 섞으면 등록 당일에 기한 초과가 뜬다.**"""
    item = _item(deadline_trading_days=20)
    assert effective_deadline(item, [date(2026, 8, 25), date(2026, 8, 26)]) is None


def test_holidays_do_not_eat_the_window():
    """08-25만 리포트가 있고 나머지는 휴장 — 3일째 기한은 달력으로 한참 뒤가 된다."""
    item = _item(deadline_trading_days=3)
    days = [date(2026, 8, 25), date(2026, 9, 1), date(2026, 9, 10)]
    assert effective_deadline(item, days) == date(2026, 9, 10)


def test_registry_rejects_two_deadlines(tmp_path: Path):
    path = tmp_path / "p.yaml"
    path.write_text(
        "verifications:\n"
        "  - id: both\n"
        "    summary: x\n"
        "    registered: 2026-08-24\n"
        "    metric: restarts\n"
        "    max: 0\n"
        "    deadline: 2026-09-05\n"
        "    deadline_trading_days: 20\n",
        encoding="utf-8",
    )
    with pytest.raises(RegistryError):
        load_registry(path)


def test_orders_submitted_metric_is_registered():
    assert "orders_submitted" in METRIC_EXTRACTORS
    extract = METRIC_EXTRACTORS["orders_submitted"]
    # 축이 없던 옛 리포트는 판정 불가지 0건이 아니다(L18).
    assert extract({}) is None
    assert extract({"sizer_funnel": {"submitted": 0}}) == 0.0
    assert extract({"sizer_funnel": {"submitted": 1}}) == 1.0


def test_live_registry_has_order_path_item():
    items = load_registry(Path("configs/pending_verifications.yaml"))
    item = next(i for i in items if i.id == "order-path-live")
    assert item.metric == "orders_submitted"
    assert item.min_value == 1
    assert item.deadline is None
    assert item.deadline_trading_days == 20
