"""호스트 기초 위생 점검 — 고도화 5 (2026-08-05 신설).

2026-08-04에 로컬 시계가 14.41초 밀린 채 8거래일을 돌았는데 원인이 코드가 아니라
**Windows Time 서비스가 꺼져 있던 것**이었다. 자가 점검이 애플리케이션 안쪽만 보고 있었다
(`ops/host_health.py` 모듈 docstring).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from messiah.ops import host_health


class _FakeRun:
    """`subprocess.run` 대역 — 명령어의 첫 토큰으로 응답을 고른다."""

    def __init__(self, responses: dict[str, tuple[int, str]]) -> None:
        self.responses = responses

    def __call__(self, cmd, **_kwargs):  # noqa: ANN001
        key = "powershell" if cmd[0] == "powershell" else cmd[0]
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
