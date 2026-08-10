"""진입점 종료 코드 축 (2026-08-10 A-2).

이 파일의 기준점은 2026-08-10 실측이다. 그날 아침 08:20 정시 트리거가 기동 창 가드에 막혀
종료했는데 **종료 코드가 0**이라 스케줄러에는 성공으로 남았고 38분이 사라졌다. 같은 날
저녁엔 정반대가 났다:

    15:35:00.6  {"tag": "SessionEnd", "msg": "정상 종료", "process": "g2_paper"}
    15:35:02    Task Scheduler ... "\\Messiah-G2" ... with return code 2147942655

`0x800700FF` = Win32 255. 로그와 OS가 서로 다른 말을 했고, 그 불일치를 읽는 축이 없었다.
"""

from __future__ import annotations

import subprocess
from datetime import date

from messiah.ops import task_exit_codes as tec

_DAY = date(2026, 8, 10)


def _runner(stdout: str, *, returncode: int = 0):
    """PowerShell 호출을 가로챈다 — 테스트가 이 PC의 이벤트 로그를 타면 안 된다."""

    def run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")

    return run


def test_reads_the_last_exit_code_per_task():
    """같은 작업이 하루에 여러 번 끝난다(수동 실행·자동 재기동) — 마지막 것이 정본이다.

    2026-08-10의 `Messiah`가 그랬다: 08:20 거절(0) → 08:50 사람이 중단 → 08:58 기동 →
    15:36 마지막. 스케줄러의 `LastTaskResult`도, 사람이 GUI에서 보는 값도 마지막 것이다.
    """
    report = tec.collect(
        _DAY,
        runner=_runner(
            "OK 3\nMessiah 08:20:27 0\nMessiah-G2 15:35:02 2147942655\nMessiah 15:36:12 0\n"
        ),
    )

    assert report.available
    assert [item.task for item in report.exits] == ["Messiah", "Messiah-G2"]
    assert {item.task: item.at_kst for item in report.exits}["Messiah"] == "15:36:12"


def test_hresult_wrapped_win32_code_is_unwrapped_but_the_original_is_kept():
    """`2147942655`를 그대로 두면 사람이 못 읽고, 벗기기만 하면 원본을 잃는다 — 둘 다 남긴다."""
    report = tec.collect(_DAY, runner=_runner("OK 1\nMessiah-G2 15:35:02 2147942655\n"))

    (item,) = report.exits
    assert item.code == 2147942655
    assert item.win32_code == 255
    assert "255" in item.describe()

    plain = tec.collect(_DAY, runner=_runner("OK 1\nMessiah 15:36:12 2\n")).exits[0]
    assert plain.code == plain.win32_code == 2, "HRESULT가 아닌 값은 건드리지 않는다"


def test_a_clean_day_produces_no_findings():
    """정상일에 조용해야 한다 — 매일 우는 축은 결국 안 읽힌다."""
    report = tec.collect(_DAY, runner=_runner("OK 2\nMessiah 15:36:12 0\nMessiah-G2 15:35:02 0\n"))

    assert tec.findings_for(report, session_ends={"l1_daily", "g2_paper"}) == []
    # 그래도 표에는 찍는다 — "봤는데 0"과 "이 축이 없다"가 갈려야 한다.
    assert "Messiah=0" in " ".join(tec.summarize(report))


def test_a_nonzero_exit_with_a_clean_session_end_is_called_out_as_a_contradiction():
    """2026-08-10 G2의 그 자리 — 이 문장이 없으면 다음에도 로그만 보고 정상이라 읽는다."""
    report = tec.collect(_DAY, runner=_runner("OK 1\nMessiah-G2 15:35:02 2147942655\n"))

    (finding,) = tec.findings_for(report, session_ends={"g2_paper"})

    assert "SessionEnd" in finding
    assert "255" in finding
    assert "둘 중 하나는 거짓" in finding


def test_a_nonzero_exit_without_a_session_end_is_still_a_finding():
    """마커가 없으면 `abnormal_exits`가 따로 울지만, 종료 코드도 제 몫을 말해야 한다."""
    report = tec.collect(_DAY, runner=_runner("OK 1\nMessiah 13:41:00 3221225786\n"))

    (finding,) = tec.findings_for(report, session_ends=set())

    assert "0이 아닌 종료" in finding


def test_only_tasks_in_the_canonical_schedule_are_scored():
    """접두어로만 거르면 **일회성 작업이 섞인다.**

    2026-08-10 09:06에 사람이 `Messiah-RegisterProbe`를 만들어 돌리고 지웠는데, 그것이
    종료 코드 1291로 끝나 이 축에 ❌로 잡혔다(첫 실측에서 발견). 확인 목적의 임시 작업이
    매일 우는 축을 만들면 그 축은 곧 안 읽힌다 — 채점 대상은 정본이 정한다.
    """
    report = tec.collect(
        _DAY,
        runner=_runner(
            "OK 3\n"
            "SomeOtherTask 10:00:00 1\n"
            "Messiah-RegisterProbe 09:06:33 2147943691\n"
            "Messiah 15:36:12 0\n"
        ),
    )

    assert [item.task for item in report.exits] == ["Messiah"]


def test_an_unreadable_schedule_falls_back_to_the_name_prefix(monkeypatch):
    """정본을 못 읽는다고 축을 통째로 끄면, 정본이 깨진 날 종료 코드까지 못 본다."""

    def unreadable(*_args, **_kwargs):
        raise tec.task_schedule.ScheduleUnreadable("없음")

    monkeypatch.setattr(tec.task_schedule, "load_schedule", unreadable)

    report = tec.collect(
        _DAY, runner=_runner("OK 2\nSomeOtherTask 10:00:00 1\nMessiah-RegisterProbe 09:06:33 5\n")
    )

    assert [item.task for item in report.exits] == ["Messiah-RegisterProbe"]


def test_a_failed_query_is_unmeasured_not_zero():
    """L18 — 못 읽은 것과 0건은 다르다. 0으로 접으면 "실패가 없었다"가 되어 거짓 통과다."""
    err = tec.collect(_DAY, runner=_runner("ERR UnauthorizedAccessException\n"))
    assert not err.available
    assert tec.findings_for(err) == []
    assert "판정 불가" in " ".join(tec.summarize(err))

    def boom(*_args, **_kwargs):
        raise OSError("powershell 없음")

    crashed = tec.collect(_DAY, runner=boom)
    assert not crashed.available
    assert "조회 실패" in crashed.detail


def test_no_events_is_measured_and_empty():
    """이벤트 0건은 **측정 성공**이다 — 그날 끝난 작업이 없었다는 사실이다."""
    report = tec.collect(_DAY, runner=_runner("OK 0\n"))

    assert report.available
    assert report.exits == []
    assert "없다" in " ".join(tec.summarize(report))


def test_malformed_lines_are_skipped_without_killing_the_axis():
    """관측 도구가 자기 파싱 실패로 리포트를 죽이면 안 된다."""
    report = tec.collect(
        _DAY, runner=_runner("OK 3\nMessiah 15:36:12 not-a-number\n쓰레기\nMessiah-G2 15:35:02 0\n")
    )

    assert [item.task for item in report.exits] == ["Messiah-G2"]
