"""호스트 기초 위생 점검 — 고도화 5 (2026-08-05 신설).

2026-08-04에 로컬 시계가 14.41초 밀린 채 8거래일을 돌았는데 원인이 코드가 아니라
**Windows Time 서비스가 꺼져 있던 것**이었다. 자가 점검이 애플리케이션 안쪽만 보고 있었다
(`ops/host_health.py` 모듈 docstring).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from messiah.ops import host_health


class _FakeRun:
    """`subprocess.run` 대역 — 명령어의 첫 토큰으로 응답을 고른다.

    PowerShell을 쓰는 검사가 둘(`power`·`cpu`)이라 첫 토큰만으로는 안 갈린다 — 스크립트
    본문에 든 표지로 한 번 더 나눈다(`"cpu"` 키를 주면 CPU 조회에만 간다).
    """

    def __init__(self, responses: dict[str, tuple[int, str]]) -> None:
        self.responses = responses

    def __call__(self, cmd, **_kwargs):  # noqa: ANN001
        key = cmd[0]
        if key == "powershell":
            if "Win32_Process" in cmd[-1]:
                key = "cpu"
            elif "StartBoundary" in cmd[-1]:
                # 2026-08-10 신설 — 트리거 **시각**을 읽는 네 번째 검사. `Get-ScheduledTask`를
                # 쓴다는 점이 부팅 트리거 검사와 같아서, 시각을 뽑는 표지로 한 번 더 나눈다.
                key = "schedule"
            elif "Get-ScheduledTask" in cmd[-1]:
                key = "boot"  # 2026-08-06 신설 — PowerShell을 쓰는 세 번째 검사
            else:
                key = "powershell"
        code, out = self.responses.get(key, (1, ""))
        return subprocess.CompletedProcess(cmd, code, stdout=out, stderr="")


# 한글 Windows의 실제 `powercfg /query ... SUB_SLEEP STANDBYIDLE` 출력(2026-08-05 채집).
# 라벨이 전부 한국어라 영문 문자열 매칭은 안 통한다 — 그래서 16진 토큰 위치로 읽는다.
_POWERCFG_KO_NO_SLEEP = """
      가능한 최소 설정: 0x00000000
      가능한 최대 설정: 0xffffffff
      가능한 설정 증가: 0x00000001
    현재 AC 전원 설정 색인: 0x00000000
    현재 DC 전원 설정 색인: 0x00000384
"""
_POWERCFG_KO_SLEEPS = _POWERCFG_KO_NO_SLEEP.replace(
    "현재 AC 전원 설정 색인: 0x00000000", "현재 AC 전원 설정 색인: 0x0000000f"
)


def test_disk_check_passes_with_room(tmp_path: Path):
    check = host_health.check_disk_free(tmp_path, min_free_gb=0.0)

    assert check.available is True
    assert check.ok is True
    assert "여유" in check.detail


def test_disk_check_fails_when_below_threshold(tmp_path: Path):
    """꽉 차면 그날 수집이 통째로 없어진다 — 아카이브가 매일 쌓이므로 조용히 오면 안 된다."""
    check = host_health.check_disk_free(tmp_path, min_free_gb=10**9)

    assert check.available is True
    assert check.ok is False


def test_power_plan_parses_localized_output(monkeypatch):
    """**로케일 문자열을 안 읽는다는 것**이 이 테스트의 요점이다.

    2026-08-05 첫 구현은 `Select-String 'Current AC Power Setting'`으로 읽었는데, 한글
    Windows에서는 그 줄이 "현재 AC 전원 설정 색인"이라 매칭이 통째로 실패했다. 레지스트리
    직접 조회도 시도했으나 설정이 기본값이면 키가 아예 없어(실측) 그 경로도 못 썼다.
    """
    monkeypatch.setattr(host_health.sys, "platform", "win32")
    runner = _FakeRun({"powershell": (0, _POWERCFG_KO_NO_SLEEP)})

    check = host_health.check_power_plan(runner=runner)

    assert check.available is True
    assert check.ok is True
    assert "절전 없음" in check.detail


def test_power_plan_flags_a_machine_that_sleeps(monkeypatch):
    """무인 운영 중 잠들면 그 구간 데이터는 백필 없이는 영원히 빈다."""
    monkeypatch.setattr(host_health.sys, "platform", "win32")
    runner = _FakeRun({"powershell": (0, _POWERCFG_KO_SLEEPS)})

    check = host_health.check_power_plan(runner=runner)

    assert check.available is True
    assert check.ok is False
    assert "15분 후 절전" in check.detail


def test_power_plan_is_unmeasured_when_the_format_changes(monkeypatch):
    """형식이 바뀌면 **판정하지 않는다** — 못 잰 것을 "정상"으로 우기지 않는다(L18)."""
    monkeypatch.setattr(host_health.sys, "platform", "win32")
    runner = _FakeRun({"powershell": (0, "설정을 찾을 수 없습니다")})

    check = host_health.check_power_plan(runner=runner)

    assert check.available is False
    assert "형식 불일치" in check.detail


def test_docker_down_is_a_measured_failure_not_an_unknown():
    """Redis(메시지 버스)가 Docker 위에 있다 — 무응답은 "못 쟀다"가 아니라 "죽었다"다."""
    check = host_health.check_docker(runner=_FakeRun({"docker": (1, "")}))

    assert check.available is True
    assert check.ok is False


def test_degraded_and_unmeasured_are_separate_lists(tmp_path: Path, monkeypatch):
    """이 분리가 고도화 2의 전제다 — 못 잰 것이 "정상"에 섞이면 안 된다."""
    monkeypatch.setattr(host_health.sys, "platform", "win32")
    runner = _FakeRun({"powershell": (0, "해독 불가"), "docker": (1, "")})

    health = host_health.HostHealth(
        checks=[
            host_health.check_disk_free(tmp_path, min_free_gb=0.0),
            host_health.check_power_plan(runner=runner),
            host_health.check_docker(runner=runner),
        ]
    )

    assert [d.split(":")[0] for d in health.degraded] == ["docker"]
    assert [u.split(":")[0] for u in health.unmeasured] == ["power"]
    assert set(health.to_dict()) == {"checks", "degraded", "unmeasured"}


# ------------------------------------------- CPU 경합 (2026-08-05 장중 점검 P2-2)
#
# 그날 1분봉 발행 지연의 꼬리가 최대 7.96초(중앙값의 12배)였는데, 이벤트 루프 지연을
# 뒷받침하거나 기각할 측정이 프로젝트에 하나도 없었다. 같은 시각 이 PC에서는 MESSIAH 말고도
# 파이썬 워크로드 4개가 돌고 있었다.

_CPU_OUTPUT = "\n".join(
    [
        "37",
        f"260{host_health._CPU_FIELD_SEP}C:\\proj\\futures\\main.py",
        f"250{host_health._CPU_FIELD_SEP}python.exe -m mahdi.main",
        # MESSIAH 자신 — 루트가 명령줄에 안 나오고 **스크립트 이름만** 나온다(실측 형태).
        f"39{host_health._CPU_FIELD_SEP}python.exe -u scripts\\run_l1_daily.py",
        f"4{host_health._CPU_FIELD_SEP}.venv\\Scripts\\python.exe -u "
        "scripts\\run_g2_paper_trading.py",
    ]
)


def test_cpu_contention_counts_only_workloads_outside_messiah(monkeypatch, tmp_path: Path):
    """루트 경로 매칭만으로는 MESSIAH 자신을 못 거른다 — 수집·페이퍼 프로세스는 명령줄에
    **상대 경로**로 뜨기 때문이다(2026-08-05 `Win32_Process` 실측)."""
    monkeypatch.setattr(host_health.sys, "platform", "win32")
    (tmp_path / "scripts").mkdir()
    for name in ("run_l1_daily.py", "run_g2_paper_trading.py"):
        (tmp_path / "scripts" / name).touch()

    check = host_health.check_cpu_contention(
        runner=_FakeRun({"cpu": (0, _CPU_OUTPUT)}), project_root=tmp_path
    )

    assert check.available is True
    assert check.ok is True  # 판정은 안 한다 — 임계를 정할 근거가 아직 없다
    assert "사용률 37%" in check.detail
    assert "외부 파이썬 2개" in check.detail
    assert "510초" in check.detail  # 260 + 250 — MESSIAH의 39·4는 안 세어야 한다


def test_cpu_contention_is_unmeasured_when_the_query_output_changes(monkeypatch):
    """못 잰 것과 "경합 없음"을 절대 합치지 않는다(L18) — `ok=True`가 항상이므로 이 구분이
    이 항목에서 유일하게 의미 있는 실패 신호다."""
    monkeypatch.setattr(host_health.sys, "platform", "win32")

    check = host_health.check_cpu_contention(runner=_FakeRun({"cpu": (0, "해독 불가")}))

    assert check.available is False
    assert "측정 실패" in check.detail


def test_cpu_contention_skips_outside_windows(monkeypatch):
    monkeypatch.setattr(host_health.sys, "platform", "linux")

    check = host_health.check_cpu_contention(runner=_FakeRun({}))

    assert check.available is False


def test_markers_follow_the_scripts_directory(tmp_path: Path):
    """진입점이 늘면 표지도 자동으로 따라와야 한다 — 상수로 박으면 새 스크립트가 조용히
    "외부 프로세스"로 세어진다."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "run_brand_new_thing.py").touch()

    markers = host_health.messiah_command_markers(tmp_path)

    assert "run_brand_new_thing.py" in markers
    assert "messiah" in markers


def test_collect_includes_the_cpu_axis(tmp_path: Path, monkeypatch):
    """`collect()`에 안 들어가면 리포트에도 안 실린다 — 검사를 만들고 안 부르는 것이
    이 프로젝트가 반복한 실패 형태다(`OptionChainPoller`가 2026-07-28~08-04 그랬다)."""
    monkeypatch.setattr(host_health.sys, "platform", "win32")
    runner = _FakeRun({"powershell": (0, _POWERCFG_KO_NO_SLEEP), "cpu": (0, _CPU_OUTPUT)})

    health = host_health.collect(path=tmp_path, min_free_gb=0.0, runner=runner)

    assert [c.name for c in health.checks] == [
        "disk",
        "power",
        "docker",
        "cpu",
        "boot_recovery",
        "schedule_drift",
        # 2026-08-20 F-6 — Windows Update 활성 시간. 이 목록이 곧 리포트에 실리는 축이라
        # 새 축을 넣고 여기를 안 고치면 그 축은 만들어만 두고 아무도 안 부르는 상태가 된다.
        "active_hours",
    ]


# ------------------------- 부팅 복구 무장 (2026-08-06 P0-2 검증용)


def _boot_run(output: str, code: int = 0):
    return _FakeRun({"boot": (code, output)})


def test_boot_trigger_on_both_tasks_is_armed(monkeypatch):
    monkeypatch.setattr(host_health.sys, "platform", "win32")

    check = host_health.check_boot_recovery(runner=_boot_run("Messiah=boot\nMessiah-G2=boot\n"))

    assert check.available and check.ok
    assert "무장 2개" in check.detail


def test_a_task_without_a_boot_trigger_is_a_finding(monkeypatch):
    """2026-08-06 이전 상태 — 평일 08:35 트리거만 있고 부팅 트리거가 없었다.

    그 상태로 10:03:49에 재부팅이 나서 21분간 관측이 죽었다. 설정은 코드가 아니라 OS
    상태라 테스트로는 못 잡는다 — 매일 실측하는 이 항목만이 잡는다.
    """
    monkeypatch.setattr(host_health.sys, "platform", "win32")

    check = host_health.check_boot_recovery(runner=_boot_run("Messiah=boot\nMessiah-G2=none\n"))

    assert check.available and not check.ok
    assert "Messiah-G2" in check.detail


def test_a_missing_task_is_a_finding_not_silence(monkeypatch):
    """작업 자체가 사라진 것이 트리거가 없는 것보다 나쁘다 — 둘 다 판정 대상이다."""
    monkeypatch.setattr(host_health.sys, "platform", "win32")

    check = host_health.check_boot_recovery(runner=_boot_run("Messiah=missing\nMessiah-G2=boot\n"))

    assert check.available and not check.ok
    assert "Messiah" in check.detail


def test_unreadable_task_list_is_unmeasured_not_ok(monkeypatch):
    """못 잰 것을 "무장됨"으로 세면 이 검사가 있으나 마나다(L18)."""
    monkeypatch.setattr(host_health.sys, "platform", "win32")

    check = host_health.check_boot_recovery(runner=_boot_run("", code=1))

    assert not check.available


def test_non_windows_is_skipped_not_failed(monkeypatch):
    monkeypatch.setattr(host_health.sys, "platform", "linux")

    check = host_health.check_boot_recovery(runner=_boot_run(""))

    assert not check.available
    assert "Windows 전용" in check.detail


# ------------------------- 등록 시각 드리프트 (2026-08-10 P0 검증용)
#
# 그날 아침 08:20/08:25 트리거가 정시에 떴고, self-check도 PASS였고, 두 프로세스 모두 그 자리에서
# 종료했다 — 기동 창이 08:30에 하드코딩돼 있었기 때문이다. **종료 코드가 0이라 스케줄러에는
# 성공으로 남았다.** 등록 시각은 OS 상태라 테스트로는 못 잡는다. 매일 실측하는 이 항목만이 잡는다.


def _schedule_file(tmp_path, entries, margin=5):
    path = tmp_path / "scheduled_tasks.json"
    path.write_text(
        json.dumps(
            {
                "launch_window_margin_minutes": margin,
                "tasks": [
                    {
                        "name": name,
                        "bat": f"scripts\\{name}.bat",
                        "weekly": weekly,
                        "at_boot": True,
                        "restart": True,
                        "collection": True,
                    }
                    for name, weekly in entries
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _sched_run(output: str, code: int = 0):
    return _FakeRun({"schedule": (code, output)})


def test_registered_time_matching_the_source_of_truth_is_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(host_health.sys, "platform", "win32")
    path = _schedule_file(tmp_path, [("Messiah", "08:20"), ("Messiah-G2", "08:25")])

    check = host_health.check_schedule_drift(
        runner=_sched_run("Messiah=08:20\nMessiah-G2=08:25\n"), schedule_path=path
    )

    assert check.available and check.ok
    assert "08:15" in check.detail, "판정에 쓴 기동 창을 안 보여주면 사람이 재확인할 수 없다"


def test_a_trigger_earlier_than_the_window_is_a_finding(monkeypatch, tmp_path):
    """2026-08-10 그 자체 — 정본은 08:35인데 등록은 08:20이라 창(08:30) 밖으로 나갔다.

    이 조합이 그날 오전을 통째로 날렸고, 모든 계기는 정상이라고 말하고 있었다.
    """
    monkeypatch.setattr(host_health.sys, "platform", "win32")
    path = _schedule_file(tmp_path, [("Messiah", "08:35"), ("Messiah-G2", "08:36")])

    check = host_health.check_schedule_drift(
        runner=_sched_run("Messiah=08:20\nMessiah-G2=08:25\n"), schedule_path=path
    )

    assert check.available and not check.ok
    assert "Messiah" in check.detail
    assert "기동 창" in check.detail
    assert "install_scheduled_tasks.ps1" in check.detail, "고치는 법을 안 알려주면 사람이 헤맨다"


def test_drift_inside_the_window_is_still_a_finding(monkeypatch, tmp_path):
    """오늘은 돌지만 정본이 실제와 다르다 — 다음에 재등록하면 조용히 되돌아간다."""
    monkeypatch.setattr(host_health.sys, "platform", "win32")
    path = _schedule_file(tmp_path, [("Messiah", "08:20")], margin=60)

    check = host_health.check_schedule_drift(
        runner=_sched_run("Messiah=08:25\n"), schedule_path=path
    )

    assert check.available and not check.ok
    assert "정본" in check.detail


def test_a_task_with_no_weekly_trigger_is_a_finding(monkeypatch, tmp_path):
    """부팅 트리거만 남고 정시 트리거가 지워지면 아침에 아무것도 안 뜬다."""
    monkeypatch.setattr(host_health.sys, "platform", "win32")
    path = _schedule_file(tmp_path, [("Messiah", "08:20")])

    check = host_health.check_schedule_drift(
        runner=_sched_run("Messiah=none\n"), schedule_path=path
    )

    assert check.available and not check.ok
    assert "트리거 없음" in check.detail


def test_a_missing_task_is_a_finding_for_drift_too(monkeypatch, tmp_path):
    monkeypatch.setattr(host_health.sys, "platform", "win32")
    path = _schedule_file(tmp_path, [("Messiah", "08:20")])

    check = host_health.check_schedule_drift(
        runner=_sched_run("Messiah=missing\n"), schedule_path=path
    )

    assert check.available and not check.ok
    assert "작업 자체가 없음" in check.detail


def test_unreadable_scheduler_is_unmeasured_not_ok(monkeypatch, tmp_path):
    """못 잰 것을 "일치"로 세면 이 검사가 있으나 마나다(L18)."""
    monkeypatch.setattr(host_health.sys, "platform", "win32")
    path = _schedule_file(tmp_path, [("Messiah", "08:20")])

    check = host_health.check_schedule_drift(runner=_sched_run("", code=1), schedule_path=path)

    assert not check.available


def test_unreadable_source_of_truth_is_unmeasured(monkeypatch, tmp_path):
    """정본을 못 읽으면 기동 창도 폴백으로 돌고 있다 — 그 사실 자체가 알려야 할 상태다."""
    monkeypatch.setattr(host_health.sys, "platform", "win32")

    check = host_health.check_schedule_drift(
        runner=_sched_run("Messiah=08:20\n"), schedule_path=tmp_path / "없다.json"
    )

    assert not check.available
    assert "정본" in check.detail


def test_drift_check_is_skipped_off_windows(monkeypatch):
    monkeypatch.setattr(host_health.sys, "platform", "linux")

    check = host_health.check_schedule_drift(runner=_sched_run(""))

    assert not check.available
    assert "Windows 전용" in check.detail
