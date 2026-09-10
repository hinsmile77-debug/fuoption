"""반복 접기 회귀 테스트 (2026-08-13 G-2 · 2026-09-10 반입)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from messiah.core.log_dedup import REPEAT_FOLDED_TAGS, RepeatFolder
from messiah.core.timeutil import KST

_TAG = "OptionChainCalendarViolation"
_T0 = datetime(2026, 9, 10, 9, 0, tzinfo=KST)


def _folder() -> RepeatFolder:
    return RepeatFolder(interval_seconds=900.0)


def test_the_first_line_always_goes_out_at_its_own_level() -> None:
    """접기는 **첫 건을 건드리지 않는다** — 사고가 조용히 시작되면 안 된다."""
    emit, folded = _folder().admit(_TAG, {"series": "W1"}, _T0)
    assert emit is True
    assert folded is None


def test_repeats_inside_the_window_are_swallowed_and_then_counted() -> None:
    """삼킨 건수는 반드시 다음 요약에 실린다 (금지계명 12 — 조용한 폴백 금지).

    5분 폴링 · 15분 창 = 창 하나에 3건. 첫 건이 나가고 2건이 삼켜진 뒤, 창을 넘긴
    4번째 건이 「3회 반복」을 싣고 나온다(삼킨 2 + 자기 1).
    """
    folder = _folder()
    fields = {"series": "W1", "nearest": "2026-09-11"}

    assert folder.admit(_TAG, fields, _T0)[0] is True
    for minute in (5, 10):
        emit, folded = folder.admit(_TAG, fields, _T0 + timedelta(minutes=minute))
        assert emit is False, "창 안의 반복은 삼킨다"
        assert folded is None

    emit, folded = folder.admit(_TAG, fields, _T0 + timedelta(minutes=15))
    assert emit is True
    assert folded is not None
    assert folded.repeat_count == 3, "삼킨 2건 + 자기 1건 — 사라진 건수가 없다"
    assert folded.first_at == _T0, "최초 발생 시각을 잃지 않는다"


def test_a_changed_payload_returns_to_the_original_level_at_once() -> None:
    """페이로드가 바뀌면 새 사실이다 — 창이 남았어도 즉시 원래 레벨로 복귀한다."""
    folder = _folder()

    assert folder.admit(_TAG, {"nearest": "2026-09-11"}, _T0)[0] is True
    assert folder.admit(_TAG, {"nearest": "2026-09-11"}, _T0 + timedelta(minutes=5))[0] is False

    emit, folded = folder.admit(_TAG, {"nearest": "2026-09-18"}, _T0 + timedelta(minutes=10))
    assert emit is True, "라벨이 바뀌었으면 접지 않는다"
    assert folded is None


@pytest.mark.parametrize("tag", ["ComposerLateBarDropped", "BarPublishDelaySpike", "RiskReject"])
def test_tags_outside_the_allowlist_are_never_folded(tag: str) -> None:
    """**판정 불변** — 허용 목록 밖의 태그는 한 건도 접히지 않는다.

    접기는 조용해지는 방향의 변경이라 전 태그에 자동 적용하면 어느 계기가 언제 조용해졌는지
    아무도 모른다. 등록부·리포트가 세는 태그들이 종전과 똑같이 나오는지 여기서 못 박는다.
    """
    assert tag not in REPEAT_FOLDED_TAGS
    folder = _folder()
    for minute in range(0, 60, 5):
        emit, folded = folder.admit(tag, {"same": "payload"}, _T0 + timedelta(minutes=minute))
        assert emit is True
        assert folded is None


def test_reset_clears_the_run_so_a_new_session_starts_loud() -> None:
    """세션 경계에서 비운다 — 어제의 반복이 오늘 첫 건을 삼키면 안 된다."""
    folder = _folder()
    fields = {"series": "W1"}
    assert folder.admit(_TAG, fields, _T0)[0] is True
    assert folder.admit(_TAG, fields, _T0 + timedelta(minutes=5))[0] is False

    folder.reset()
    assert folder.admit(_TAG, fields, _T0 + timedelta(minutes=10))[0] is True


def test_an_unserialisable_payload_does_not_break_emission() -> None:
    """계기 하나가 발행을 막으면 본말전도다 — 직렬화 불가한 값이 섞여도 줄은 나간다."""
    folder = _folder()
    emit, folded = folder.admit(_TAG, {"obj": object()}, _T0)
    assert emit is True
    assert folded is None
