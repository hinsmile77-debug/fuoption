"""장후 배치의 「오늘인데 아직 마감 전」 가드 (2026-10-06 F-130).

2026-10-06 07:24에 `Messiah-Postmarket` 캐치업이 개장 전에 떠서 그날(벽시계 날짜)을 대상으로
돌았고, 1분봉이 없는 것을 오조회로 읽어 exit 3으로 끝났다. 그 종료 코드가 수정검증
`exit-code-matches-log`를 「재발」로 뒤집었다(`ops/session_guard.market_not_yet_closed_reason`).
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from messiah.core.timeutil import KST
from messiah.ops import session_guard
from messiah.ops.session_guard import market_not_yet_closed_reason

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
_DAY = date(2026, 10, 6)


def _at(hour: int, minute: int, second: int = 0, *, day: date = _DAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=KST)


def test_today_before_close_is_skipped() -> None:
    """(a) 그날 07:24 캐치업 — 건너뛴다."""
    reason = market_not_yet_closed_reason(_DAY, now=_at(7, 24, 6))
    assert reason is not None
    assert "15:35" in reason and "07:24:06" in reason


@pytest.mark.parametrize("moment", [_at(15, 35), _at(15, 45), _at(23, 59)])
def test_today_after_close_proceeds(moment: datetime) -> None:
    """(b) 15:45 정시 실행 — 지금 운영 중인 유일한 정상 경로는 반드시 통과한다.

    15:35 정각도 통과다 — `EventCalendar.is_regular_session`이 `close_time`을 미포함으로 보는
    것과 같은 경계.
    """
    assert market_not_yet_closed_reason(_DAY, now=moment) is None


@pytest.mark.parametrize("target", [date(2026, 10, 2), date(2026, 10, 5)])
def test_past_date_is_not_judged(target: date) -> None:
    """(c) `--date` 소급 재처리 — 개장 전에 돌려도 가드에 안 걸린다(밀린 날 복구 경로)."""
    assert market_not_yet_closed_reason(target, now=_at(7, 24)) is None


def test_close_time_is_the_session_close_not_a_new_constant() -> None:
    """마감 기준은 기동 창 끝과 같은 값 하나다 — 둘이 어긋나면 그 사이 시각이 양쪽에서 샌다."""
    assert session_guard.LAUNCH_WINDOW_END == session_guard.DEFAULT_SESSION.close_time
    one_minute_before = session_guard.LAUNCH_WINDOW_END.replace(
        minute=session_guard.LAUNCH_WINDOW_END.minute - 1
    )
    moment = datetime.combine(_DAY, one_minute_before, tzinfo=KST)
    assert market_not_yet_closed_reason(_DAY, now=moment) is not None


def test_postmarket_gates_before_symbol_resolution_and_exits_zero() -> None:
    """진입점은 이 가드를 **심볼 해석보다 앞**에서 부르고, 걸리면 종료 코드 0으로 끝낸다.

    0이 요점이다 — 0이 아니면 Windows 스케줄러 기록에 남아 `nonzero_task_exits`가 오른다
    (2026-10-06 수정검증 재발의 직접 원인). `SessionEnd`도 남긴다 — 없으면
    `_abnormal_exits`가 이 기동을 「죽었다」로 센다.
    """
    text = (_SCRIPTS / "run_postmarket.py").read_text(encoding="utf-8")
    body = text.split("def main() -> int:", 1)[1]
    guard_at = body.index("market_not_yet_closed_reason")
    assert body.index("non_trading_day_reason") < guard_at < body.index("_resolve_symbol(")
    block = body[guard_at : body.index("_resolve_symbol(")]
    assert "return 0" in block
    assert '"SessionEnd"' in block and "MARKET_NOT_CLOSED_REASON" in block


def test_reason_value_is_registered_with_session_end() -> None:
    """`SessionEnd`의 `reason` 허용값은 `core/logging.py`에 등재돼 있어야 한다(R6)."""
    text = (
        Path(__file__).resolve().parent.parent.parent / "src" / "messiah" / "core" / "logging.py"
    ).read_text(encoding="utf-8")
    assert session_guard.MARKET_NOT_CLOSED_REASON in text
