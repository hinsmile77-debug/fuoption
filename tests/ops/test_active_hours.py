"""Windows Update 활성 시간을 호스트 위생 축으로 올린다 — 2026-08-20 F-6.

2026-08-10에 오전 38분이 사라진 뒤로 이 저장소는 「관측 공백의 원인」을 계속 쫓았다. 원인
후보 중 사람이 **설정 하나로 막을 수 있는** 것이 이것이다 — 활성 시간이 거래 구간을 덮지
않으면 OS가 장중에 재부팅을 밀어붙일 수 있고, 그 구간의 틱·수급·옵션체인은 소급 경로가 없다.

`check_power_plan`(절전)과 같은 계열이다: `configs/instance.yaml`에 안 적히는 호스트 상태라
복제 배포(SYSTEM.md §4-6)의 사각지대다.

**기동은 막지 않는다.** 관측 항목 하나가 그날 수집을 통째로 포기시키면 본말전도다.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from messiah.ops import host_health


def _runner(stdout: str, returncode: int = 0):
    def _run(*args, **kwargs):
        return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)

    return _run


@pytest.mark.skipif(
    host_health.sys.platform != "win32", reason="Windows 전용 축 — 다른 OS는 미판정"
)
def test_covering_window_is_ok() -> None:
    check = host_health.check_active_hours(runner=_runner("8-16\n"))
    assert check.available and check.ok
    assert check.detail == "08:00~16:00"


@pytest.mark.skipif(
    host_health.sys.platform != "win32", reason="Windows 전용 축 — 다른 OS는 미판정"
)
def test_narrow_window_says_what_it_leaves_uncovered() -> None:
    """09~15시는 기동 창(08:15)과 장후 배치(15:45)를 **못 덮는다** — 그 사실을 문장으로."""
    check = host_health.check_active_hours(runner=_runner("9-15\n"))
    assert check.available and not check.ok
    assert "09:00~15:00" in check.detail
    assert "Windows Update 재부팅" in check.detail


@pytest.mark.skipif(
    host_health.sys.platform != "win32", reason="Windows 전용 축 — 다른 OS는 미판정"
)
def test_query_failure_is_unmeasured_not_ok() -> None:
    """못 잰 것을 「정상」으로 적으면 그 축은 영원히 조용해진다 (L18).

    다만 `ok=True`는 유지한다 — 이 항목은 기동을 막지 않는다(`check_host` 원칙).
    `degraded`에 안 들어가고 `unmeasured`로 간다는 것이 판정의 실체다.
    """
    check = host_health.check_active_hours(runner=_runner("", returncode=1))
    assert not check.available
    assert check.ok is True
    health = host_health.HostHealth(checks=[check])
    assert health.degraded == []
    assert any("active_hours" in item for item in health.unmeasured)


@pytest.mark.skipif(
    host_health.sys.platform != "win32", reason="Windows 전용 축 — 다른 OS는 미판정"
)
def test_exception_does_not_escape() -> None:
    """레지스트리 접근이 던져도 기동은 계속된다 (L22 — 항목별 격리)."""

    def _boom(*args, **kwargs):
        raise OSError("registry denied")

    check = host_health.check_active_hours(runner=_boom)
    assert not check.available and check.ok
    assert "조회 실패" in check.detail


def test_collect_includes_the_axis(monkeypatch) -> None:
    """`collect()`에 실제로 실려야 `self_check`의 host 줄에 나온다."""
    monkeypatch.setattr(
        host_health,
        "check_active_hours",
        lambda **kwargs: host_health.HostCheck("active_hours", True, True, "08:00~16:00"),
    )
    monkeypatch.setattr(subprocess, "run", _runner(""))
    health = host_health.collect()
    assert any(c.name == "active_hours" for c in health.checks)
