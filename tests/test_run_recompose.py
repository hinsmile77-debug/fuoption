"""상위 Horizon 재합성 CLI — 오늘 포함 판단 (2026-08-05 실측 회귀).

이 스크립트는 **장후 일일 절차의 2단계**다. 그런데 기본값이 "오늘 제외"라 장 마감 뒤에
그대로 돌리면 `완료 — 0일 / 상위봉 0행`만 찍고 끝났다 — 그날 망가진 상위 Horizon을 고치라고
만든 절차가 아무것도 안 하고 성공처럼 보였다(2026-08-05에 실제로 그렇게 한 번 지나갔다).
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_recompose import _should_include_today  # noqa: E402

from messiah.core.timeutil import KST  # noqa: E402


def _at(hour: int, minute: int) -> datetime:
    return datetime(2026, 8, 5, hour, minute, tzinfo=KST)


def test_after_the_close_today_is_included_automatically():
    """장후 절차가 그냥 `run_recompose.py`로 돌아도 오늘이 고쳐져야 한다."""
    include, reason = _should_include_today(explicit=False, now=_at(16, 23))

    assert include is True
    assert "연속거래 종료" in reason


def test_during_the_session_today_is_excluded_and_says_why():
    """장중엔 제외가 맞다 — 라이브 수집이 조각을 쓰는 중이다. 다만 **이유를 말한다**."""
    include, reason = _should_include_today(explicit=False, now=_at(13, 0))

    assert include is False
    assert "조각을 쓰는 중" in reason
    assert "--include-today" in reason


def test_the_boundary_minute_counts_as_after():
    """15:35 정각은 연속거래 **종료** 시각이다 — 그 순간부터 포함."""
    assert _should_include_today(explicit=False, now=_at(15, 35))[0] is True
    assert _should_include_today(explicit=False, now=_at(15, 34))[0] is False


def test_explicit_flag_still_wins_during_the_session():
    include, reason = _should_include_today(explicit=True, now=_at(10, 0))

    assert include is True
    assert "--include-today" in reason
