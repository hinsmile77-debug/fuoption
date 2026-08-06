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
| CPU 경합 | 2026-08-05 실측 — 이 PC에서 MESSIAH 말고도 파이썬 워크로드 4개가 동시에 돌고 있었다 |

## CPU 경합을 왜 재나 (2026-08-05 추가)

그날 상위 Horizon 합성봉이 매 버킷 마지막 1분봉을 잃었다(`data/bar_composer.py`). 주 원인은
따로 있었지만, 그 판단의 근거가 된 1분봉 발행 지연 분포는 **최대 7.96초**까지 벌어져 있었다
— 중앙값 0.655초의 12배다. 그런 꼬리는 틱 도착만으로는 설명되지 않고 이벤트 루프 지연을
의심하게 하는데, **그걸 뒷받침하거나 기각할 측정이 이 프로젝트에 하나도 없었다.**

같은 시각 이 PC의 실제 상태(`Win32_Process` 실측):

    futures/main.py (py37_32)          CPU 260.6초   ← 08:57 기동
    mahdi.main ×2 + mahdi 대시보드 ×2   CPU 250 / 58.9초
    MESSIAH l1_daily / g2_paper / UI    CPU 39 / 4.6 / 27초

**판정은 안 한다.** 임계를 정할 근거가 아직 없다 — 며칠 실측해서 "정상인 날의 경합"이
얼마인지 본 뒤에 정한다(디스크·전원 임계를 미검증 초기값으로 둔 것과 같은 태도). 지금
필요한 것은 다음에 같은 꼬리를 봤을 때 **참조할 숫자가 리포트에 남아 있는 것**이다.

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

# CPU 경합 조회 — 전체 사용률 1표본 + 파이썬 프로세스의 (CPU초, 명령줄).
#
# 프로세스를 파이썬으로만 좁히는 이유: 이 PC에서 실제로 경합을 만든 것이 전부 파이썬
# 워크로드였고(mahdi 2 + 대시보드 2 + futures 1), 전체 프로세스를 세면 브라우저·IDE까지 섞여
# 매일 다른 숫자가 나와 아무도 안 보게 된다.
#
# **로케일 의존을 피한다** — `Get-Counter`는 카운터 이름이 번역되지만
# `Win32_PerfFormattedData_PerfOS_Processor`의 `_Total`은 숫자 필드라 언어와 무관하다
# (`check_power_plan`이 `powercfg` 문장을 안 읽고 16진 토큰만 쓰는 것과 같은 회피법).
#
# **MESSIAH 자신을 거르는 일은 파이썬에서 한다.** PowerShell 문자열 안에 경로를 끼워 넣으면
# 따옴표·백슬래시 이스케이프가 층층이 쌓여 조용히 안 걸리기 쉽다 — 그 판정을 여기 두면
# 테스트로 확인할 수도 없다.
#
# 출력 형식: 첫 줄 = CPU 사용률(정수), 이후 각 줄 = "<CPU초>{sep}<명령줄>".
_CPU_FIELD_SEP = " ## "
_CPU_QUERY = (
    "$t = (Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor "
    "| Where-Object { $_.Name -eq '_Total' }).PercentProcessorTime; "
    "Write-Output ([int]$t); "
    "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | ForEach-Object { "
    "Write-Output ([string][int](($_.KernelModeTime + $_.UserModeTime) / 10000000) "
    f"+ '{_CPU_FIELD_SEP}' + [string]$_.CommandLine) }}"
)


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


def messiah_command_markers(project_root: Path | str = ".") -> set[str]:
    """명령줄이 **MESSIAH 자신의 프로세스**인지 가르는 소문자 표지들.

    프로젝트 루트 경로만으로는 안 된다 — 2026-08-05 실측으로 이 PC의 수집·페이퍼 프로세스는
    루트가 명령줄에 아예 안 나온다:

        "C:\\...\\anaconda3\\python.exe" -u scripts\\run_l1_daily.py       ← 상대 경로
        .venv\\Scripts\\python.exe  -u scripts\\run_g2_paper_trading.py    ← uv 트램폴린
        "C:\\...\\fuoption\\.venv\\Scripts\\streamlit.exe" run C:\\...     ← 이것만 절대 경로

    (`.venv\\Scripts\\python.exe`가 부모, 실제 작업은 기저 인터프리터 자식 프로세스가 한다 —
    uv가 만든 venv의 트램폴린이라 프로세스가 짝으로 보이는 것은 정상이다.)

    그래서 `scripts/`의 실제 파일 이름을 표지로 쓴다 — **진입점이 늘면 자동으로 따라온다.**
    목록을 상수로 박아 두면 새 스크립트가 조용히 "외부 프로세스"로 세어진다.

    한계: 다른 프로젝트가 우연히 같은 이름의 스크립트를 돌리면 그것도 MESSIAH로 센다.
    이 항목은 판정을 안 하고 참고 숫자만 남기므로(모듈 docstring) 그 오차를 감수한다.
    """
    root = Path(project_root).resolve()
    markers = {str(root).lower(), "messiah"}
    scripts_dir = root / "scripts"
    if scripts_dir.is_dir():
        markers.update(path.name.lower() for path in scripts_dir.glob("*.py"))
    return markers


def check_cpu_contention(*, runner=subprocess.run, project_root: Path | str = ".") -> HostCheck:
    """CPU 사용률과 **MESSIAH 밖의** 파이썬 워크로드 — 기록만 하고 판정은 안 한다.

    `ok`가 항상 True인 유일한 항목이다. 임계를 정할 근거가 아직 없기 때문이다(모듈
    docstring "CPU 경합을 왜 재나"). `available=False`(못 쟀다)와는 여전히 구분되므로,
    측정이 조용히 사라지면 `unmeasured`에 뜬다 — 그게 이 항목에서 진짜 경계할 일이다.

    `project_root` 아래 경로를 명령줄에 가진 프로세스는 MESSIAH 자신이라 세지 않는다.
    """
    if sys.platform != "win32":
        return HostCheck("cpu", available=False, ok=True, detail="Windows 전용 — 건너뜀")
    try:
        result = runner(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _CPU_QUERY],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001 — 못 재는 것과 정상은 다르다
        return HostCheck("cpu", available=False, ok=True, detail=f"측정 실패({type(exc).__name__})")

    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    if result.returncode != 0 or not lines or not lines[0].lstrip("-").isdigit():
        return HostCheck("cpu", available=False, ok=True, detail="측정 실패(조회 출력 형식 불일치)")

    percent = int(lines[0])
    markers = messiah_command_markers(project_root)
    outside_cpu_seconds = 0
    outside = 0
    for line in lines[1:]:
        cpu_text, _, command = line.partition(_CPU_FIELD_SEP)
        lowered = command.lower()
        if any(marker in lowered for marker in markers):
            continue  # MESSIAH 자신 — 경합이 아니라 본 임무다
        if not cpu_text.strip().lstrip("-").isdigit():
            continue  # 명령줄에 구분자와 같은 문자열이 섞인 줄 — 세지 않는다
        outside += 1
        outside_cpu_seconds += int(cpu_text)

    return HostCheck(
        "cpu",
        available=True,
        ok=True,  # 판정 안 함 — 위 docstring
        detail=(
            f"사용률 {percent}% · 외부 파이썬 {outside}개"
            + (f"(누적 CPU {outside_cpu_seconds}초)" if outside else "")
        ),
    )


# 부팅 복구 무장 여부를 확인할 작업 — `scripts/install_scheduled_tasks.ps1`이 등록하는 것과
# 같은 이름이다. 장후 정리(Messiah-Shutdown)·장후 절차(Messiah-Postmarket)는 부팅 트리거가
# 필요 없으므로 대상이 아니다(그쪽은 뜰 이유가 재부팅과 무관하다).
BOOT_RECOVERY_TASKS = ("Messiah", "Messiah-G2")

_BOOT_TRIGGER_QUERY = (
    "$ErrorActionPreference='SilentlyContinue';"
    "foreach ($n in @('Messiah','Messiah-G2')) {"
    "  $t = Get-ScheduledTask -TaskName $n;"
    '  if (-not $t) { "$n=missing"; continue }'
    "  $b = @($t.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskBootTrigger' });"
    '  if ($b.Count -gt 0) { "$n=boot" } else { "$n=none" }'
    "}"
)


def check_boot_recovery(*, runner=subprocess.run) -> HostCheck:
    """수집 작업에 **부팅 트리거가 걸려 있는가** (2026-08-06 신설).

    ## 왜 호스트 위생 항목인가

    2026-08-06 10:03:49에 PC가 재부팅됐다. OS는 10:05:03에 올라왔는데 MESSIAH는 안 올라왔다 —
    Task Scheduler에 평일 08:35 트리거 하나뿐이었기 때문이다. 21분간 관측이 죽었고 1분봉
    21개가 영구 소실됐다.

    그날 트리거를 붙였다(`scripts/install_scheduled_tasks.ps1`). 그런데 **그 설정이 살아
    있는지 매일 확인하는 것이 없으면** 08-06 이전과 똑같은 상태로 조용히 돌아갈 수 있다 —
    이 프로젝트가 반복한 실패 형태가 정확히 그것이다("결선했다고 믿는데 안 붙어 있음").
    설정은 코드가 아니라 OS 상태라 테스트로 못 잡는다. 그래서 매일 실측한다.

    **이 항목은 재부팅이 나기 전에도 답을 준다.** 부팅 복구가 실제로 동작하는지는 다음
    재부팅까지 알 수 없지만, 무장 여부는 오늘 알 수 있다 — 그 둘을 구분해 두는 것이
    "고쳤다"를 기억이 아니라 실측이 판정하게 하는 방법이다.
    """
    if sys.platform != "win32":
        return HostCheck("boot_recovery", available=False, ok=True, detail="Windows 전용 — 건너뜀")
    try:
        result = runner(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _BOOT_TRIGGER_QUERY],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001 — 못 재는 것과 정상은 다르다
        return HostCheck(
            "boot_recovery", available=False, ok=True, detail=f"측정 실패({type(exc).__name__})"
        )

    states: dict[str, str] = {}
    for line in (result.stdout or "").splitlines():
        name, _, state = line.strip().partition("=")
        if name and state:
            states[name] = state
    if result.returncode != 0 or set(states) != set(BOOT_RECOVERY_TASKS):
        return HostCheck(
            "boot_recovery", available=False, ok=True, detail="측정 실패(작업 조회 출력 불일치)"
        )

    unarmed = sorted(name for name, state in states.items() if state != "boot")
    if unarmed:
        return HostCheck(
            "boot_recovery",
            available=True,
            ok=False,
            detail=(
                f"{', '.join(unarmed)}에 부팅 트리거 없음 — 장중 재부팅이 나면 사람이 "
                "손으로 띄울 때까지 관측이 죽는다(2026-08-06에 21분)"
            ),
        )
    return HostCheck(
        "boot_recovery",
        available=True,
        ok=True,
        detail=f"부팅 트리거 무장 {len(states)}개({', '.join(sorted(states))})",
    )


def collect(
    *,
    path: Path | str = ".",
    min_free_gb: float = MIN_FREE_GB,
    runner=subprocess.run,
    project_root: Path | str = ".",
) -> HostHealth:
    """전 항목 점검. 어느 하나가 실패해도 나머지는 계속한다(L22 — 항목별 격리)."""
    return HostHealth(
        checks=[
            check_disk_free(path, min_free_gb=min_free_gb),
            check_power_plan(runner=runner),
            check_docker(runner=runner),
            check_cpu_contention(runner=runner, project_root=project_root),
            check_boot_recovery(runner=runner),
        ]
    )
