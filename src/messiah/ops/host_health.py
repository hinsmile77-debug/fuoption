"""운영 PC의 기초 위생 점검 — 고도화 5 (2026-08-05).

## 왜 이 모듈이 생겼나

2026-08-04 점검에서 로컬 시계가 실제보다 **14.41초 느린 채로 8거래일**을 돌았다는 것이
드러났다. 원인은 코드가 아니라 **Windows Time 서비스가 꺼져 있었던 것**이다. 그때까지
`self_check`는 애플리케이션 안쪽만 봤다 — 설정·스키마·시크릿·번들·Redis. 프로세스 바깥의
호스트 상태는 점검 범위에 아예 없었다.

복제 배포(SYSTEM.md §4-6)에서 이건 특히 중요하다. 인스턴스 차이는 `configs/instance.yaml`
하나뿐이라는 것이 원칙인데, **호스트 상태는 그 파일에 안 적힌다** — PC마다 다를 수 있고
아무도 안 본다. 시계가 그랬던 것처럼.

## 무엇을 보나 — 전부 "조용히 운영을 망가뜨린 적이 있거나, 망가뜨릴 수 있는 것"

| 항목 | 왜 |
|---|---|
| 시간 동기 | 2026-08-04 실측 — 14.41초 밀림. 완성봉 경계·마감 전 청산이 그만큼 어긋난다 |
| 디스크 여유 | Parquet 아카이브·틱 원본이 매일 쌓인다. 꽉 차면 그날 수집이 통째로 없어진다 |
| 전원 관리 | 절전은 **무인 운영의 조용한 사망**이다. 깨어나도 그 사이 데이터는 없다 |
| Docker | Redis(메시지 버스)가 여기 있다. 기동은 자동인데 상태가 리포트에 안 남았다 |

## 판정 원칙 — 못 재는 것과 정상은 다르다

이 프로젝트에서 가장 자주 재발한 실패 형태다(L18). 항목마다 `available`을 따로 두고,
못 잰 항목은 `None`으로 남긴다 — "확인했는데 문제없음"과 "확인 못 함"을 절대 합치지 않는다.

## 왜 기동을 막지 않나

시간 동기만 예외다(`scripts/self_check.py`의 `check_clock` — 5초 초과 시 거래 거부).
나머지는 **경고만** 한다. 디스크가 좀 부족하다고 그날 수집을 통째로 포기하는 것은
"부가 정보 실패가 본 기능을 막지 않는다" 원칙(`core/docker_bootstrap.py`)에 어긋나고,
무엇보다 이 임계들이 전부 미검증 초기값이다.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# 아카이브가 쌓이는 속도 실측(2026-08-04): 봉 Parquet 하루 ~60KB, 체결틱 하루 ~0.3MB,
# 옵션체인 하루 ~3,276행. 합쳐도 하루 1MB 남짓이라 5GB면 수년치다 — 이 임계는
# "곧 찬다"가 아니라 **"이미 이상하다"**를 잡는 값이다(로그 폭주·덤프 누적 등).
MIN_FREE_GB = 5.0

# 절전/최대절전 진입까지의 시간이 0이면 "안 함"이다(Windows 규약). 무인 운영 PC는 이 값이
# 반드시 0이어야 한다 — 장중에 잠들면 그 구간 데이터는 백필 없이는 영원히 빈다.
#
# **출력 문장을 읽지 않는다.** `powercfg /query`의 라벨은 로케일 언어라(한글 Windows에서는
# "현재 AC 전원 설정 색인") `Select-String 'Current AC Power Setting'`이 안 걸린다 —
# 2026-08-05에 실제로 그렇게 실패했다. 레지스트리 직접 조회도 시도했으나, 설정이 기본값이면
# 키 자체가 없어(실측) 그 경로도 못 쓴다.
#
# 그래서 **16진 토큰의 위치**만 쓴다. powercfg는 로케일과 무관하게 항상 같은 순서로 찍는다:
#
#     가능한 최소 설정: 0x00000000
#     가능한 최대 설정: 0xffffffff
#     가능한 설정 증가: 0x00000001
#     현재 AC 전원 설정 색인: 0x00000000   ← 뒤에서 두 번째
#     현재 DC 전원 설정 색인: 0x00000000   ← 마지막
#
# 토큰이 2개 미만이면 형식이 바뀐 것이므로 **판정하지 않는다**(미측정으로 남긴다).
_POWER_QUERY = (
    "$g=(powercfg /getactivescheme); "
    "if ($g -match '([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})') "
    "{ $guid=$Matches[1] } else { exit 3 }; "
    "powercfg /query $guid SUB_SLEEP STANDBYIDLE; exit 0"
)

# powercfg 출력에서 뽑을 16진 토큰.
_HEX_TOKEN = re.compile(r"0x([0-9a-fA-F]+)")


@dataclass
class HostCheck:
    """호스트 항목 1개. `available=False`면 `ok`는 판정이 아니라 **미판정**이다."""

    name: str
    available: bool
    ok: bool
    detail: str = ""


@dataclass
class HostHealth:
    checks: list[HostCheck] = field(default_factory=list)

    @property
    def degraded(self) -> list[str]:
        """잰 항목 중 기준을 못 넘긴 것 — 못 잰 항목은 여기 안 들어간다."""
        return [f"{c.name}: {c.detail}" for c in self.checks if c.available and not c.ok]

    @property
    def unmeasured(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if not c.available]

    def to_dict(self) -> dict[str, object]:
        return {
            "checks": [asdict(c) for c in self.checks],
            "degraded": self.degraded,
            "unmeasured": self.unmeasured,
        }


def check_disk_free(path: Path | str = ".", *, min_free_gb: float = MIN_FREE_GB) -> HostCheck:
    """아카이브가 쌓이는 볼륨의 여유 공간. 꽉 차면 그날 수집이 통째로 없어진다."""
    try:
        usage = shutil.disk_usage(str(path))
    except OSError as exc:
        return HostCheck("disk", available=False, ok=True, detail=f"측정 실패({exc.strerror})")
    free_gb = usage.free / (1024**3)
    return HostCheck(
        "disk",
        available=True,
        ok=free_gb >= min_free_gb,
        detail=f"여유 {free_gb:.1f}GB (최소 {min_free_gb:.0f}GB)",
    )


def check_power_plan(*, runner=subprocess.run) -> HostCheck:
    """AC 전원에서 절전 진입까지의 분(分). 0이 "절전 안 함"이고, 무인 운영엔 그게 요구값이다.

    `powercfg`의 출력 문장은 로케일 언어라 **16진수 값 토큰만** 뽑는다 — `self_check`가
    `w32tm` 출력에서, `ops/integrity_report.py`가 이벤트로그에서 쓰는 것과 같은 회피법이다.
    """
    if sys.platform != "win32":
        return HostCheck("power", available=False, ok=True, detail="Windows 전용 — 건너뜀")
    try:
        result = runner(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _POWER_QUERY],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001 — 못 재는 것과 정상은 다르다
        return HostCheck(
            "power", available=False, ok=True, detail=f"측정 실패({type(exc).__name__})"
        )

    tokens = _HEX_TOKEN.findall(result.stdout or "")
    if result.returncode != 0 or len(tokens) < 2:
        return HostCheck(
            "power", available=False, ok=True, detail="측정 실패(powercfg 출력 형식 불일치)"
        )
    minutes = int(tokens[-2], 16)  # 뒤에서 두 번째가 AC (위 `_POWER_QUERY` 주석)
    return HostCheck(
        "power",
        available=True,
        ok=minutes == 0,
        detail=(
            "AC 절전 없음"
            if minutes == 0
            else f"AC {minutes}분 후 절전 — 무인 운영 중 잠들면 그 구간은 백필 없이 영원히 빈다"
        ),
    )


def check_docker(*, runner=subprocess.run) -> HostCheck:
    """Docker daemon 응답 — Redis(메시지 버스)가 그 위에 있다.

    `core/docker_bootstrap.py`가 기동 시 자동으로 띄우지만, **그 사실이 리포트에 안 남았다**.
    "오늘 Docker가 몇 번 죽었나"를 사후에 물을 수단이 없었다.
    """
    try:
        result = runner(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        return HostCheck(
            "docker", available=False, ok=True, detail=f"측정 실패({type(exc).__name__})"
        )
    if result.returncode != 0:
        return HostCheck("docker", available=True, ok=False, detail="daemon 무응답")
    return HostCheck("docker", available=True, ok=True, detail=f"v{(result.stdout or '').strip()}")


def collect(
    *,
    path: Path | str = ".",
    min_free_gb: float = MIN_FREE_GB,
    runner=subprocess.run,
) -> HostHealth:
    """전 항목 점검. 어느 하나가 실패해도 나머지는 계속한다(L22 — 항목별 격리)."""
    return HostHealth(
        checks=[
            check_disk_free(path, min_free_gb=min_free_gb),
            check_power_plan(runner=runner),
            check_docker(runner=runner),
        ]
    )
