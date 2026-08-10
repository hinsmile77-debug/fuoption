"""진입점의 **종료 코드** — 로그가 아니라 OS가 기록한 그날의 결말 (2026-08-10 A-2).

## 왜 이 축이 생겼나 — 같은 채널이 하루에 두 번 실패했다

2026-08-10 아침, 08:20 정시 트리거가 기동 창 가드에 막혀 두 프로세스가 즉시 종료했다.
**종료 코드가 0이었다.** 스케줄러에는 `LastTaskResult=0`(성공)으로 남았고, 그 상태로
38분이 사라졌다. 그날 `ops/session_guard.py`가 정시 트리거 거절만 종료 코드 2로 가르는
수정을 받았다 — "사람이 아니라 스케줄러가 실패로 적게 한다".

그런데 같은 날 저녁, 정확히 반대편이 드러났다:

    15:35:00.6  {"tag": "SessionEnd", "msg": "정상 종료", "process": "g2_paper"}
    15:35:02    Task Scheduler ... "\\Messiah-G2" ... with return code 2147942655

`0x800700FF` = Win32 255. **로그는 정상 종료라 말하고 OS는 실패라 말한다.** 그리고 그
불일치를 읽는 축이 어디에도 없었다 — 2026-08-06·08-07 같은 자리는 0이었으므로 오늘 처음
생긴 상태인데도, 사람이 이벤트 로그를 손으로 열기 전까지 아무도 몰랐다.

같은 아침 `Messiah` 인스턴스는 15:35:40경 **한 번 더 떴다**(로그에 두 번째 `SessionStart`가
있고 기동 창이 정상적으로 거절했다). 작업 정의에는 `RestartOnFailure(3회/1분)`가 있으므로
그 경로가 유일한 후보지만 간격이 8초라 딱 맞지 않는다 — **원인을 확정할 수 없는 이유가
바로 종료 코드를 아무도 안 적었기 때문이다.**

## 왜 코드가 아니라 실측인가

`check_boot_recovery`·`check_schedule_drift`와 같은 계열이다. 종료 코드는 코드가 아니라
**OS 상태**라 테스트로는 못 잡는다. 매 거래일 장후에 이벤트 201을 실측해 리포트에 싣는다.

## 판정 둘을 나눈다

- **종료 코드 ≠ 0**: 그 자체로 조사 대상이다.
- **`SessionEnd`를 남겼는데 ≠ 0**: 더 위험하다. 로그와 OS가 서로 다른 말을 하는 상태이고,
  이때 "로그가 정상이라 했다"는 근거는 **더 이상 근거가 아니다**.

정상 종료로 끝난 뒤 거절된 재기동이 섞이면 마지막 값만 남는다(이벤트 201은 인스턴스가
끝날 때 한 번 찍힌다). 그래서 태스크별로 **그날 마지막 201**을 쓴다 — 그게 스케줄러가
`LastTaskResult`로 기억하는 값이고, 사람이 GUI에서 보는 값이다.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from messiah.ops import task_schedule

# 정본을 못 읽을 때만 쓰는 그물 — 같은 PC의 다른 프로젝트 작업을 끌어오지 않을 만큼만 넓다.
TASK_PREFIX = "Messiah"

# 작업 이름 → 구조화 로그의 `process` 이름. `SessionEnd` 대조(판정 ②)의 연결 고리다.
# 여기 없는 작업(Shutdown·Postmarket)은 로그를 안 내므로 판정 ①만 받는다.
TASK_TO_PROCESS: dict[str, str] = {
    "Messiah": "l1_daily",
    "Messiah-G2": "g2_paper",
}

_COMPLETED_EVENT_ID = 201
"""작업 인스턴스가 끝날 때의 이벤트. 반환 코드가 실린 유일한 이벤트다."""


def _watched_tasks() -> set[str] | None:
    """채점할 작업 이름 — 정본(`configs/scheduled_tasks.json`)에서 온다. 못 읽으면 None.

    이름 접두어로만 거르면 **일회성 작업이 섞인다.** 2026-08-10 09:06에 스케줄러 재등록이
    실행 중 인스턴스를 죽이는지 확인하려고 `Messiah-RegisterProbe`를 만들어 돌리고 지웠는데,
    그것이 종료 코드 1291로 끝나 이 축에 `❌`로 잡혔다(첫 실측에서 발견). 사람이 확인 목적
    으로 만든 임시 작업이 매일 우는 축을 만들면 그 축은 곧 안 읽힌다.

    정본을 못 읽으면 접두어로 되돌아간다 — 판정 불가를 이유로 축을 통째로 끄는 것보다
    넓게라도 보는 쪽이 낫다(그 경우 `TASK_PREFIX`가 그물이다).
    """
    try:
        tasks, _ = task_schedule.load_schedule()
    except task_schedule.ScheduleUnreadable:
        return None
    return {task.name for task in tasks}


@dataclass
class TaskExit:
    """작업 하나의 그날 마지막 결말."""

    task: str
    at_kst: str
    code: int
    # `0x800700FF`처럼 HRESULT로 감싸인 값의 하위 바이트 — 사람이 읽는 실제 종료 코드.
    # Win32 오류를 HRESULT로 감싸면 `0x8007####`가 되므로 그 껍질을 벗겨 둔다.
    win32_code: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def describe(self) -> str:
        raw = f"{self.code}" if self.code == self.win32_code else f"{self.code}(=0x{self.code:X})"
        return f"{self.task} {self.at_kst} 종료 코드 {raw} → Win32 {self.win32_code}"


@dataclass
class TaskExitReport:
    exits: list[TaskExit] = field(default_factory=list)
    available: bool = False
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "exits": [item.to_dict() for item in self.exits],
            "available": self.available,
            "detail": self.detail,
        }


def _win32_code(code: int) -> int:
    """HRESULT 껍질을 벗긴 실제 종료 코드.

    `0x8007####` = `FACILITY_WIN32`로 감싸인 Win32 오류다(2026-08-10 실측: G2가
    `2147942655` = `0x800700FF` = 255). 껍질째 두면 사람이 그 숫자를 못 읽고, 벗겨서만
    두면 어떤 값이 원본이었는지 잃는다 — 그래서 둘 다 남긴다.
    """
    if 0x80070000 <= code <= 0x8007FFFF:
        return code & 0xFFFF
    return code


def _query_script(start: datetime, end: datetime) -> str:
    """`ops/observation_gaps._query_script()`와 **같은 규율**의 PowerShell 스크립트.

    항상 `exit 0`으로 끝나고 첫 줄에 `OK <건수>` 또는 `ERR <예외형>`을 찍는다 — 종료 코드로
    판정하면 "이벤트 0건"과 "질의 실패"가 구분되지 않는다(2026-08-04에 그 혼동으로 등록부가
    영원히 판정 불가였다).

    `Message`(로캘 문장)는 안 쓰고 `Properties`만 읽는다. 이벤트 201의 실측 배치:
    `[0]` 작업 경로(`\\Messiah-G2`) · `[1]` 인스턴스 GUID · `[2]` 액션 · `[3]` 반환 코드.
    """
    return (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
        "$ErrorActionPreference='Stop'; $events=@(); "
        "try { $events=@(Get-WinEvent -FilterHashtable @{"
        "LogName='Microsoft-Windows-TaskScheduler/Operational';"
        f"Id={_COMPLETED_EVENT_ID};"
        f"StartTime='{start:%Y-%m-%d %H:%M:%S}';EndTime='{end:%Y-%m-%d %H:%M:%S}'"
        "} -ErrorAction Stop) } "
        "catch { if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { $events=@() } "
        "else { Write-Output ('ERR ' + $_.Exception.GetType().Name); exit 0 } } "
        "Write-Output ('OK ' + $events.Count); "
        "$events | Sort-Object TimeCreated | ForEach-Object { "
        "if ($_.Properties.Count -ge 4) { "
        "$name = ([string]$_.Properties[0].Value).TrimStart('\\'); "
        "$name + ' ' + $_.TimeCreated.ToString('HH:mm:ss') + ' ' "
        "+ [string]$_.Properties[3].Value } }; "
        "exit 0"
    )


def collect(day: date, *, runner=subprocess.run) -> TaskExitReport:
    """그날 `Messiah*` 작업의 마지막 종료 코드 — 못 읽으면 `available=False`.

    실패 조건: 없다. 관측 도구가 리포트를 죽이면 안 된다(`ops/observation_gaps.py`와 같은 규율).
    """
    if sys.platform != "win32":
        return TaskExitReport(available=False, detail="Windows 전용 집계 — 건너뜀")

    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)
    try:
        result = runner(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _query_script(start, end)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except Exception as exc:  # noqa: BLE001 — 못 읽는 것과 0건은 다르다
        return TaskExitReport(available=False, detail=f"조회 실패: {type(exc).__name__}")

    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    if result.returncode != 0 or not lines or not lines[0].startswith("OK "):
        reason = lines[0] if lines else f"출력 없음(exit={result.returncode})"
        return TaskExitReport(available=False, detail=f"Get-WinEvent 실패: {reason}")

    # 같은 작업이 하루에 여러 번 끝날 수 있다(수동 실행·재기동). 시간순으로 덮어써서
    # **마지막 것**만 남긴다 — 그게 스케줄러의 `LastTaskResult`이고 사람이 보는 값이다.
    watched = _watched_tasks()
    latest: dict[str, TaskExit] = {}
    for line in lines[1:]:
        parts = line.split()
        if len(parts) != 3:
            continue
        if not (parts[0] in watched if watched is not None else parts[0].startswith(TASK_PREFIX)):
            continue
        try:
            code = int(parts[2])
        except ValueError:
            continue
        latest[parts[0]] = TaskExit(
            task=parts[0], at_kst=parts[1], code=code, win32_code=_win32_code(code)
        )

    exits = [latest[name] for name in sorted(latest)]
    return TaskExitReport(exits=exits, available=True, detail=f"작업 {len(exits)}개")


def findings_for(report: TaskExitReport, *, session_ends: set[str] | None = None) -> list[str]:
    """사람이 읽는 판정 문장 — 빈 목록이면 그날 모든 작업이 0으로 끝났다.

    `session_ends`는 그날 `SessionEnd`를 남긴 프로세스 이름 집합이다. 주면 판정 ②
    (로그와 OS의 불일치)를 함께 본다 — 안 주면 판정 ①만 본다.
    """
    ends = session_ends or set()
    out: list[str] = []
    for item in report.exits:
        if item.win32_code == 0:
            continue
        process = TASK_TO_PROCESS.get(item.task)
        if process and process in ends:
            out.append(
                f"{item.describe()} — 로그는 `SessionEnd`(정상 종료)를 남겼다. "
                "둘 중 하나는 거짓이고, 그 사실을 아는 축이 이것뿐이다"
            )
        else:
            out.append(f"{item.describe()} — 0이 아닌 종료")
    return out


def summarize(report: TaskExitReport) -> list[str]:
    """리포트에 찍는 표 — **0으로 끝난 작업도 전부 찍는다.**

    정상까지 찍는 이유는 커버리지 표와 같다. "봤는데 0이었다"와 "이 축이 없다"가 구분돼야
    하고, 후자가 2026-08-10의 상태였다.
    """
    if not report.available:
        return [f"  작업 종료 코드: 판정 불가 — {report.detail}"]
    if not report.exits:
        return ["  작업 종료 코드: 그날 끝난 Messiah 작업이 없다"]
    parts = [
        f"{item.task}={item.win32_code}" + ("" if item.win32_code == 0 else " ❌")
        for item in report.exits
    ]
    return ["  작업 종료 코드: " + " · ".join(parts)]
