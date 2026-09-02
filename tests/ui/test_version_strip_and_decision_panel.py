"""화면이 **자기 시스템에 대해 하는 말**이 사실인가 (2026-09-01 F-83 · F-84).

이 파일이 재는 것은 값이 아니라 **문장**이다. 2026-09-01 장전 점검이 찾은 다섯 이상점 중
셋이 「코드는 맞고 문장만 틀린」 형태였다 — 화면이 상태판과 같은 분에 반대말을 했고(1-1),
전일 판단을 지금 값처럼 그렸으며(1-3), 매일 돌고 있는 기능을 「미구현」이라 적었다(1-5).

렌더 함수는 Streamlit 런타임을 타므로 `app_module.st`를 기록기로 갈아 끼워 잰다 —
`AppTest`(`test_app_smoke.py`)는 스크립트 전체를 돌리느라 이 한 줄을 겨냥할 수 없다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from messiah.core.messages import DecisionIntent, Side
from messiah.core.version import VersionDrift
from messiah.ui import app as app_module
from messiah.ui.app import (
    _decision_staleness_warning,
    _version_stale_caption,
    _worktree_dirty_files,
)
from messiah.ui.data_source import DataSourceMode, FreshnessBadge, TopicSnapshot

_KST = timezone(timedelta(hours=9))


class _RecordingStreamlit:
    """`st.*` 호출을 그대로 받아 적는다 — 어떤 자리에 무엇이 찍혔는지가 곧 검증 대상이다."""

    def __init__(self) -> None:
        self.markdown: list[str] = []
        self.caption: list[str] = []
        self.warning: list[str] = []
        self.text: list[str] = []
        self.info: list[str] = []
        self.metric: list[tuple] = []
        self.subheader: list[str] = []

    def __getattr__(self, name):  # 기록하지 않는 나머지(divider 등)는 조용히 삼킨다
        return lambda *args, **kwargs: None


def _install(monkeypatch) -> _RecordingStreamlit:
    rec = _RecordingStreamlit()

    class _Shim:
        def markdown(self, value, **_kw):
            rec.markdown.append(value)

        def caption(self, value, **_kw):
            rec.caption.append(value)

        def warning(self, value, **_kw):
            rec.warning.append(value)

        def text(self, value, **_kw):
            rec.text.append(value)

        def info(self, value, **_kw):
            rec.info.append(value)

        def subheader(self, value, **_kw):
            rec.subheader.append(value)

        def metric(self, *args, **kwargs):
            rec.metric.append((args, kwargs))

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    monkeypatch.setattr(app_module, "st", _Shim())
    return rec


# ------------------------------------------------------------------ F-83


def test_uncommitted_sources_turn_the_strip_amber(monkeypatch) -> None:
    """**이것이 1-1의 재발 방지선이다.** 커밋은 같아도 미커밋 소스가 돌면 어긋남이다 —
    2026-08-31 F-76이 `code_version_axis()`에서 그렇게 정했고, 화면이 그 함수를 타야
    상태판과 같은 문장이 나온다."""
    rec = _install(monkeypatch)
    monkeypatch.setattr(app_module, "head_git_sha", lambda: "dfb835f")
    monkeypatch.setattr(app_module, "PROCESS_GIT_SHA", "dfb835f")
    monkeypatch.setattr(app_module, "_component_versions", lambda _s: {})
    monkeypatch.setattr(
        app_module, "load_snapshot", lambda: {"code_version": {"worktree_dirty_files": 5}}
    )

    app_module._render_version_strip(object())

    strip = "\n".join(rec.markdown)
    assert "#FFB020" in strip  # 앰버 — 회색(#8A8F98)이 아니다
    assert "저장소와 다름" in strip
    assert any("재기동해도 커밋 전엔 안 바뀐다" in c for c in rec.caption)
    # 틀린 처방을 **지우는 것**이 이 항목의 절반이다.
    assert not any("재기동해야 최신 코드가 적재된다" in c for c in rec.caption)


def test_a_clean_worktree_on_the_same_commit_stays_grey(monkeypatch) -> None:
    rec = _install(monkeypatch)
    monkeypatch.setattr(app_module, "head_git_sha", lambda: "dfb835f")
    monkeypatch.setattr(app_module, "PROCESS_GIT_SHA", "dfb835f")
    monkeypatch.setattr(app_module, "_component_versions", lambda _s: {})
    monkeypatch.setattr(
        app_module, "load_snapshot", lambda: {"code_version": {"worktree_dirty_files": 0}}
    )

    app_module._render_version_strip(object())

    assert "#8A8F98" in "\n".join(rec.markdown)
    assert rec.caption == []


def test_an_unreadable_snapshot_says_it_could_not_measure(monkeypatch) -> None:
    """못 잰 것을 0으로 접으면 화면이 "깨끗하다"고 거짓말한다(L18)."""
    rec = _install(monkeypatch)
    monkeypatch.setattr(app_module, "head_git_sha", lambda: "dfb835f")
    monkeypatch.setattr(app_module, "PROCESS_GIT_SHA", "dfb835f")
    monkeypatch.setattr(app_module, "_component_versions", lambda _s: {})
    monkeypatch.setattr(app_module, "load_snapshot", lambda: None)

    app_module._render_version_strip(object())

    assert "미커밋 미측정" in "\n".join(rec.markdown)


def test_dirty_file_count_comes_from_the_snapshot_not_git(monkeypatch) -> None:
    """화면은 git을 직접 부르지 않는다 (2026-08-31 F-78)."""
    monkeypatch.setattr(
        app_module, "load_snapshot", lambda: {"code_version": {"worktree_dirty_files": 3}}
    )
    assert _worktree_dirty_files() == 3

    monkeypatch.setattr(app_module, "load_snapshot", lambda: {"code_version": {}})
    assert _worktree_dirty_files() is None

    monkeypatch.setattr(app_module, "load_snapshot", lambda: {"code_version": "망가짐"})
    assert _worktree_dirty_files() is None


def test_each_stale_reason_gets_its_own_prescription() -> None:
    assert _version_stale_caption(None, 0) is None
    assert "재기동" in _version_stale_caption("sha_mismatch", 0)
    assert "5파일" in _version_stale_caption("worktree_dirty", 5)
    both = _version_stale_caption("both", 5)
    assert "재기동" in both and "5파일" in both


def test_the_strip_and_the_status_board_speak_the_same_sentence(monkeypatch) -> None:
    """1-1의 본질은 두 곳이 **같은 함수를 안 탔다**는 것이다 — 문장을 대조해 못 박는다."""
    from messiah.ops.status_board import code_version_axis

    rec = _install(monkeypatch)
    monkeypatch.setattr(app_module, "head_git_sha", lambda: "dfb835f")
    monkeypatch.setattr(app_module, "PROCESS_GIT_SHA", "dfb835f")
    monkeypatch.setattr(app_module, "_component_versions", lambda _s: {})
    monkeypatch.setattr(
        app_module, "load_snapshot", lambda: {"code_version": {"worktree_dirty_files": 5}}
    )

    app_module._render_version_strip(object())

    drift = VersionDrift(stale=False, summary="코드 dfb835f — 전 프로세스 동일")
    axis = code_version_axis(drift=drift, dirty_files=5, head_sha="dfb835f")
    assert axis["summary"] in "\n".join(rec.markdown)


# ------------------------------------------------------------------ F-84


def _intent() -> DecisionIntent:
    return DecisionIntent(
        symbol="A05609",
        side=Side.LONG,
        confidence=0.62,
        uncertainty=0.18,
        rationale="테스트",
    )


def _snapshot(badge: FreshnessBadge, age: float | None, cadence: float | None = 1800.0):
    return TopicSnapshot(message=_intent(), badge=badge, age_seconds=age, cadence_seconds=cadence)


def test_a_fresh_decision_carries_no_warning() -> None:
    """매번 뜨는 문구는 배경이 된다 — 신선하면 아무 말도 하지 않는다."""
    snap = _snapshot(FreshnessBadge.LIVE, 4.0)
    assert _decision_staleness_warning(snap, now=datetime(2026, 9, 1, 9, 0, tzinfo=_KST)) is None


def test_a_day_old_decision_says_when_it_was_made() -> None:
    """2026-09-01 08:20 화면 실측: 전일 15:29 판단이 좌상단 큰 칸에 그대로 있었다."""
    snap = _snapshot(FreshnessBadge.STALE, 1036 * 60.0)
    warning = _decision_staleness_warning(snap, now=datetime(2026, 9, 1, 8, 45, tzinfo=_KST))

    assert warning is not None
    assert "1036분 전" in warning
    assert "08-31 15:29" in warning
    assert "지금 시장의 판단이 아니다" in warning


def test_the_metric_delta_slot_no_longer_carries_a_level(monkeypatch) -> None:
    """**변화량이 아닌 값을 델타 칸에 넣지 않는다.** 확신도 62%가 초록 상승 화살표와 함께
    "62% 올랐다"로 읽혔다 — 값은 캡션으로 내리고 화살표를 없앤다."""
    rec = _install(monkeypatch)

    class _Source:
        mode = DataSourceMode.LIVE

        def snapshot(self, key):
            if key == "DecisionIntent":
                return _snapshot(FreshnessBadge.LIVE, 4.0)
            return TopicSnapshot(message=None, badge=FreshnessBadge.NO_DATA, age_seconds=None)

        def listening_seconds(self):
            return 10.0

    app_module.render_ai_decision_panel(_Source())

    assert rec.metric, "의도 metric이 사라지면 이 패널의 요점이 사라진다"
    args, kwargs = rec.metric[0]
    assert args[:2] == ("의도", "LONG")
    assert len(args) == 2 and "delta" not in kwargs  # 델타 인자를 넘기지 않는다
    assert any("확신도 62%" in c and "불확실성 0.18" in c for c in rec.caption)
    assert rec.warning == []


def test_a_stale_decision_warns_above_the_value(monkeypatch) -> None:
    rec = _install(monkeypatch)

    class _Source:
        mode = DataSourceMode.LIVE

        def snapshot(self, key):
            if key == "DecisionIntent":
                return _snapshot(FreshnessBadge.STALE, 1036 * 60.0)
            return TopicSnapshot(message=None, badge=FreshnessBadge.NO_DATA, age_seconds=None)

        def listening_seconds(self):
            return 10.0

    app_module.render_ai_decision_panel(_Source())

    assert rec.warning, "배지와 값이 같은 시선 안에 들어와야 한다"
    assert "지금 시장의 판단이 아니다" in rec.warning[0]


def test_no_data_keeps_the_absence_reason_path(monkeypatch) -> None:
    """`NO_DATA`일 때의 기존 경로(`_absence_reason`)는 손대지 않았다."""
    rec = _install(monkeypatch)

    class _Source:
        mode = DataSourceMode.LIVE

        def snapshot(self, key):
            return TopicSnapshot(message=None, badge=FreshnessBadge.NO_DATA, age_seconds=None)

        def listening_seconds(self):
            return 10.0

    app_module.render_ai_decision_panel(_Source())

    assert rec.info, "값이 없을 때는 info 한 줄로 사유를 말한다"
    assert rec.metric == []


@pytest.mark.parametrize("badge", [FreshnessBadge.LIVE, FreshnessBadge.REPLAY])
def test_a_missing_age_is_not_reported_as_stale(badge: FreshnessBadge) -> None:
    """나이를 모르면 「몇 분 전」을 지어내지 않는다."""
    snap = _snapshot(badge, None)
    assert _decision_staleness_warning(snap, now=datetime(2026, 9, 1, 9, 0, tzinfo=_KST)) is None
