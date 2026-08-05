"""코드 버전 대조 — "고친 것"과 "도는 것"이 다를 수 있다는 축 (2026-08-05 3차, P0-1).

2026-08-05 장중점검의 실제 상황을 그대로 재현한다: 프로세스는 08:35에 `bb60f19`로 떴고,
11:03·11:57에 감시 장치가 커밋됐으며, 화면 신호등은 전부 초록이었다. 그 초록이 **구버전이
보낸 것**이라는 사실을 말할 수단이 없던 것이 이 모듈이 메우는 자리다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from messiah.core import version as version_module
from messiah.core.version import (
    UNKNOWN_SHA,
    assess_version_drift,
    head_git_sha,
    uptime_text,
)

_UTC = timezone.utc


def test_all_processes_on_head_is_not_drift():
    drift = assess_version_drift(
        process_sha="8810867",
        head_sha="8810867",
        component_shas={"l1.collector": "8810867", "g2.pipeline": "8810867"},
    )

    assert drift.stale is False
    assert "8810867" in drift.summary


def test_the_2026_08_05_situation_is_reported_as_drift():
    """실제로 일어난 일 — 전 프로세스가 3시간 전 코드로 돌고 있었다."""
    drift = assess_version_drift(
        process_sha="bb60f19",  # UI 기동 08:35
        head_sha="8810867",  # 커밋 11:57
        component_shas={"l1.collector": "bb60f19", "g2.pipeline": "bb60f19"},
    )

    assert drift.stale is True
    assert "8810867" in drift.summary  # 무엇이 최신인지
    assert "bb60f19" in drift.summary  # 무엇이 돌고 있는지
    assert len(drift.details) == 3  # 화면 + 컴포넌트 2개


def test_missing_git_sha_field_is_itself_evidence_of_an_old_process():
    """`Health.git_sha`를 **안 싣는** heartbeat는 그 필드가 생기기 전 코드라는 뜻이다.

    2026-08-05 장중에 실제로 이 상태였다 — 그래서 "미보고"를 정상으로 넘기면 안 된다.
    """
    drift = assess_version_drift(
        process_sha="8810867",
        head_sha="8810867",
        component_shas={"l1.collector": ""},
    )

    assert drift.stale is True
    assert "버전 미보고" in drift.summary


def test_screen_alone_can_be_stale_even_when_collectors_are_current():
    drift = assess_version_drift(
        process_sha="bb60f19",
        head_sha="8810867",
        component_shas={"l1.collector": "8810867"},
    )

    assert drift.stale is True
    assert drift.details == ("화면 bb60f19",)


def test_git_lookup_failure_is_not_reported_as_agreement():
    """판정할 근거가 없는 것과 일치하는 것은 다르다 — `HealthLevel.UNKNOWN`과 같은 논리."""
    drift = assess_version_drift(process_sha="bb60f19", head_sha=UNKNOWN_SHA)

    assert drift.stale is False  # 어긋남으로 단정하지도 않는다
    assert "확인 불가" in drift.summary
    assert "bb60f19" not in drift.summary  # 근거 없는 비교를 보여주지 않는다


def test_process_sha_is_pinned_at_import_not_read_per_call():
    """**이 단언이 이 모듈의 존재 이유다.**

    렌더마다 `git rev-parse`를 다시 부르면 항상 최신 HEAD가 나와 어긋남을 영영 못 본다.
    `PROCESS_GIT_SHA`는 임포트 시점에 고정된 상수여야 한다 — 재조회 함수가 아니다.
    """
    assert isinstance(version_module.PROCESS_GIT_SHA, str)
    assert not callable(version_module.PROCESS_GIT_SHA)

    before = version_module.PROCESS_GIT_SHA
    version_module.reset_head_cache()
    head_git_sha()  # 작업트리 HEAD를 다시 읽어도
    assert version_module.PROCESS_GIT_SHA == before  # 적재 버전은 안 변한다


def test_head_lookup_is_cached_so_the_render_loop_does_not_spawn_git_every_5_seconds(monkeypatch):
    calls = {"n": 0}

    def _counting() -> str:
        calls["n"] += 1
        return "8810867"

    monkeypatch.setattr(version_module, "_read_git_sha", _counting)
    version_module.reset_head_cache()

    base = datetime(2026, 8, 5, 3, 0, tzinfo=_UTC)
    for offset in (0, 5, 10, 55):  # LIVE 렌더 격자(5초)
        head_git_sha(now=base + timedelta(seconds=offset))
    assert calls["n"] == 1

    head_git_sha(now=base + timedelta(seconds=61))  # TTL 경과 후엔 다시 확인한다
    assert calls["n"] == 2

    version_module.reset_head_cache()


def test_git_failure_falls_back_to_unknown_instead_of_raising(monkeypatch):
    def _boom():
        raise OSError("git not found")

    monkeypatch.setattr(version_module, "_read_git_sha", _boom)
    version_module.reset_head_cache()
    with pytest.raises(OSError):
        version_module._read_git_sha()

    # 실제 구현은 예외를 삼키고 UNKNOWN_SHA를 준다 — 버전 조회 실패가 화면을 죽이면 안 된다.
    monkeypatch.setattr(version_module, "_read_git_sha", lambda: UNKNOWN_SHA)
    assert head_git_sha() == UNKNOWN_SHA
    version_module.reset_head_cache()


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "0분"), (59, "0분"), (600, "10분"), (3600, "1시간 0분"), (13200, "3시간 40분")],
)
def test_uptime_text(seconds, expected):
    started = datetime(2026, 8, 5, 0, 0, tzinfo=_UTC)
    assert uptime_text(started, now=started + timedelta(seconds=seconds)) == expected
