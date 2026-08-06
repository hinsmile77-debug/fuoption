"""관측 공백과 그 원인 (2026-08-06 P1-1·P1-2).

기준점은 그날의 실제 사건이다: 10:03:49 재부팅 개시 → 10:04:31 OS 종료 → 10:05:03 부팅 →
10:25:31 수동 재기동. 21분간 아무것도 관측하지 못했는데 리포트는 "재기동 1회"만 말했고,
UI는 같이 죽었는데 `ui_restarts`는 0이었다.
"""

from __future__ import annotations

import subprocess
from datetime import date

from messiah.ops import observation_gaps as og

_DAY = date(2026, 8, 6)


def _reboot_events() -> list[og.HostEvent]:
    """2026-08-06 실측 이벤트 순서 그대로."""
    return [
        og.HostEvent(
            1074,
            "10:03:49",
            "shutdown",
            "RuntimeBroker.exe / 다시 시작 / 기타(계획되지 않음)",
        ),
        og.HostEvent(6006, "10:04:25", "shutdown"),
        og.HostEvent(13, "10:04:31", "shutdown"),
        og.HostEvent(12, "10:05:03", "boot"),
        og.HostEvent(6005, "10:05:12", "boot"),
    ]


# ---------------------------------------------------------------- 공백 계산


def test_the_reboot_gap_is_measured_from_the_host_event():
    """**이 파일의 핵심.** 프로세스는 죽을 때 아무것도 안 남긴다 — 호스트 종료 이벤트가
    공백의 시작을 **정확히** 짚어 준다."""
    gaps = og.find_gaps(
        _DAY,
        starts_by_process={"l1_daily": ["08:35:23", "10:25:31"]},
        activity_by_process={"l1_daily": ["08:36:01", "10:04:00"]},
        events=_reboot_events(),
    )

    [gap] = gaps
    assert gap.from_kst == "10:04:31"  # 이벤트 13 — 마지막 활동(10:04:00)보다 뒤다
    assert gap.to_kst == "10:25:31"
    assert gap.minutes == 21.0
    assert gap.exact is True


def test_the_cause_quotes_the_initiating_process_and_reason():
    """ "왜 끊겼나"가 리포트에 없어서 사람이 이벤트로그를 두 번 뒤졌다."""
    [gap] = og.find_gaps(
        _DAY,
        starts_by_process={"l1_daily": ["08:35:23", "10:25:31"]},
        activity_by_process={"l1_daily": ["10:04:00"]},
        events=_reboot_events(),
    )

    assert "RuntimeBroker.exe" in gap.cause
    assert "계획되지 않음" in gap.cause


def test_a_silent_process_still_gets_an_exact_gap():
    """`g2_paper`는 번들 결선 전이라 장중에 아무것도 안 찍는다. 활동 로그만 보면 공백이
    110분으로 과대평가되는데, 호스트 이벤트가 있으면 21분으로 정확해진다."""
    [gap] = og.find_gaps(
        _DAY,
        starts_by_process={"g2_paper": ["08:36:16", "10:26:05"]},
        activity_by_process={"g2_paper": ["08:36:16"]},  # 기동 직후가 마지막 활동
        events=_reboot_events(),
    )

    assert gap.exact is True
    assert gap.minutes == 21.6


def test_without_host_events_the_gap_is_an_upper_bound():
    """이벤트를 못 읽으면 마지막 활동으로 추정한다 — 실제 공백은 **이보다 짧다**.
    모르는 것을 아는 척하지 않는다(L18)."""
    [gap] = og.find_gaps(
        _DAY,
        starts_by_process={"g2_paper": ["08:36:16", "10:26:05"]},
        activity_by_process={"g2_paper": ["08:36:16"]},
        events=[],
    )

    assert gap.exact is False
    assert gap.minutes > 100  # 과대평가 — 그래서 exact=False로 함께 말한다
    assert "원인 불명" in gap.cause


def test_the_ui_gap_is_visible_even_though_ui_restarts_is_zero():
    """`ui_restarts`는 인프로세스 워치독의 자동 재기동만 센다 — 밖에서 죽는 경로는 구조적으로
    시야 밖이라 2026-08-06에 21분 공백 위에서 0이었다."""
    [gap] = og.find_gaps(
        _DAY,
        starts_by_process={"ui": ["08:35:29", "10:25:36"]},
        activity_by_process={},  # UI는 활동 로그가 없다
        events=_reboot_events(),
    )

    assert gap.process == "ui"
    assert gap.minutes == 21.1
    assert gap.exact is True


def test_no_restart_means_no_gap():
    assert (
        og.find_gaps(
            _DAY,
            starts_by_process={"l1_daily": ["08:35:23"]},
            activity_by_process={"l1_daily": ["15:34:00"]},
            events=[],
        )
        == []
    )


def test_a_shutdown_event_before_the_last_activity_is_not_the_cause():
    """종료 이벤트가 마지막 활동보다 **앞**이면 이 공백의 원인이 아니다 — 다른 재기동의
    것이거나 순서가 안 맞는다. 엉뚱한 원인을 붙이느니 모른다고 한다."""
    [gap] = og.find_gaps(
        _DAY,
        starts_by_process={"l1_daily": ["08:35:23", "10:25:31"]},
        activity_by_process={"l1_daily": ["10:20:00"]},  # 종료 이벤트(10:04:31)보다 뒤
        events=_reboot_events(),
    )

    assert gap.exact is False
    assert "시각이 안 맞음" in gap.cause


# ---------------------------------------------------------------- UI 로그 파싱


def test_ui_starts_are_parsed_from_the_streamlit_log():
    """UI 로그는 구조화 로그가 아니라 `analyze_logs()`의 시야 밖이다."""
    text = (
        "2026-08-06 08:35:29.599 Uvicorn server started on :::8511\n"
        "\n  You can now view your Streamlit app in your browser.\n"
        "[crash_forensics] armed tag=ui target=stderr\n"
        "2026-08-06 10:25:36.343 Uvicorn server started on :::8511\n"
    )

    assert og.parse_ui_starts(text) == ["08:35:29", "10:25:36"]


def test_ui_log_without_starts_yields_nothing():
    assert og.parse_ui_starts("아무 내용 없음\n") == []


# ---------------------------------------------------------------- 이벤트 조회


class _FakeRun:
    def __init__(self, code: int, out: str) -> None:
        self.code, self.out = code, out

    def __call__(self, cmd, **_kwargs):  # noqa: ANN001
        return subprocess.CompletedProcess(cmd, self.code, stdout=self.out, stderr="")


def test_events_are_parsed_with_the_ok_sentinel(monkeypatch):
    monkeypatch.setattr(og.sys, "platform", "win32")
    runner = _FakeRun(0, "OK 2\n1074 10:03:49 RuntimeBroker.exe / 다시 시작 / 기타\n13 10:04:31 \n")

    events, available, detail = og.collect_host_events(_DAY, runner=runner)

    assert available is True
    assert [e.event_id for e in events] == [1074, 13]
    assert events[0].kind == "shutdown"
    assert "RuntimeBroker.exe" in events[0].detail
    assert "2건" in detail


def test_zero_events_is_measured_not_failed(monkeypatch):
    """ "이벤트 0건"과 "질의 실패"를 구분 못 해서 2026-08-04에 등록부가 영원히 판정
    불가였다 — 종료 코드가 아니라 센티널 첫 줄로 가른다."""
    monkeypatch.setattr(og.sys, "platform", "win32")

    events, available, _ = og.collect_host_events(_DAY, runner=_FakeRun(0, "OK 0\n"))

    assert available is True
    assert events == []


def test_a_failed_query_is_unmeasured(monkeypatch):
    monkeypatch.setattr(og.sys, "platform", "win32")

    _, available, detail = og.collect_host_events(_DAY, runner=_FakeRun(0, "ERR SomeException\n"))

    assert available is False
    assert "ERR" in detail


def test_non_windows_is_skipped(monkeypatch):
    monkeypatch.setattr(og.sys, "platform", "linux")

    _, available, detail = og.collect_host_events(_DAY, runner=_FakeRun(0, ""))

    assert available is False
    assert "Windows 전용" in detail


# ---------------------------------------------------------------- 요약 출력


def test_summary_says_so_even_when_there_is_no_gap():
    """공백 0건도 남긴다 — "측정된 0"과 "그 축이 없음"이 갈려야 한다(L18)."""
    lines = og.summarize(og.ObservationReport(events_available=True))

    assert lines == ["  관측 공백: 없음 ✅"]


def test_summary_flags_an_unmeasurable_day():
    lines = og.summarize(og.ObservationReport(events_available=False, events_detail="조회 실패"))

    assert any("판정 불가" in line for line in lines)
