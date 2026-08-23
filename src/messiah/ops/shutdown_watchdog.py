"""강제 종료를 정상 종료로 센다 — 2026-08-21 F-16 ②③.

## 무슨 오탐이었나 (이상점 1-16)

증거 수집기 §9가 매일 이렇게 올렸다:

    `ui`: SessionStart 있고 SessionEnd 없음 — 비정상 종료 의심

그런데 UI에는 **정상 종료 경로가 없다.** 15:40에 `scripts/stop_l1_daily.bat`의 워치독이
명령줄 패턴으로 찾아 `Stop-Process -Force`로 죽인다 — 그것이 설계다(그 프로세스를
정리하는 다른 경로가 없어서 매치 집합에 들어 있다). 즉 `SessionEnd`의 부재는 **설계된
결과**이고, 매일 적신호 한 줄을 만들면서 진짜 신호를 목록 아래로 밀어냈다.

## 왜 목록을 코드에 박지 않는가

박으면 2026-08-21 이상점 1-12와 같은 형태가 된다 — **정적 선언이 코드보다 낡고, 그
낡음은 아무 계기에도 안 잡힌다.** UI가 나중에 정상 종료를 배우거나 워치독 매치 집합이
바뀌면 이 파일만 거짓이 된다.

그래서 **그날 워치독이 실제로 무엇을 죽였는지**(`logs/shutdown_watchdog.log`의 그날
`command-line match, stopping:` 기록)에서 파생시킨다. 관측이 선언을 이긴다.

## 두 곳이 같은 답을 내야 한다 (F-16 ③)

종전엔 같은 사실에 두 곳이 다른 답을 냈다 — `ops/integrity_report._abnormal_exits()`는
"마커를 한 번도 안 낸 프로세스는 판정 대상이 아니다"라며 **조용히 건너뛰었고**, 증거
수집기는 **적신호로 올렸다**. 둘 다 부분적으로 옳았지만 어느 쪽도 사유를 대지 못했다.
이 모듈의 `session_end_verdict()`가 그 판정의 단일 출처다.

stdlib만 쓴다 — 증거 수집기(`.claude/skills/.../collect_evidence.py`)가 외부 의존성
없이 임포트해야 한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DEFAULT_LOG_PATH = Path("logs/shutdown_watchdog.log")

#: `[2026-08-21 15:40:01.15] ===== MESSIAH shutdown watchdog start =====`
_SESSION_HEADER = re.compile(r"^\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})[.\d]*\]")

#: `command-line match, stopping: PID 10732 - C:\...\streamlit.exe run ...app.py --server.port 8511`
_KILL = re.compile(r"command-line match, stopping:\s*PID\s+(\d+)\s*-\s*(.+?)\s*$")

#: 명령줄 조각 → 이 저장소가 프로세스를 부르는 이름.
#:
#: 워치독 자신의 매치 집합(`scripts/stop_l1_daily.bat`)과 **같은 세 조각**이다. 여기서
#: 새로 정의하는 것이 아니라 그쪽이 이미 쓰는 표식을 읽는 것뿐이다.
_COMMAND_MARKERS: tuple[tuple[str, str], ...] = (
    ("run_l1_daily.py", "l1_daily"),
    ("run_g2_paper_trading.py", "g2_daily"),
    ("messiah\\ui\\app.py", "ui"),
    ("messiah/ui/app.py", "ui"),
)


@dataclass(frozen=True)
class ForcedKill:
    """워치독이 그날 실제로 죽인 프로세스 한 건."""

    process: str  # 이 저장소가 쓰는 이름("ui"/"l1_daily"/"g2_daily") 또는 "unknown"
    pid: int
    at_kst: str  # HH:MM:SS — 그 워치독 세션의 시작 시각
    command: str

    def describe(self) -> str:
        return f"{self.process} PID {self.pid} ({self.at_kst})"


def _process_of(command: str) -> str:
    lowered = command.lower()
    for marker, name in _COMMAND_MARKERS:
        if marker.lower() in lowered:
            return name
    return "unknown"


def forced_kills(day: date, *, log_path: Path | str = DEFAULT_LOG_PATH) -> list[ForcedKill]:
    """그날 워치독이 **실제로 강제 종료한** 프로세스 목록 — 못 읽으면 빈 목록.

    빈 목록은 "그날 강제 종료가 없었다"와 "로그를 못 읽었다"를 합친다. 그 구분이 필요한
    호출부는 `watchdog_log_readable()`을 함께 본다 — 여기서 예외를 던지면 그날 리포트가
    통째로 멈추고, 그건 관측 하나 때문에 관측 전체를 잃는 것이다.
    """
    path = Path(log_path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    out: list[ForcedKill] = []
    current_day: str | None = None
    current_time = "??:??:??"
    for line in text.splitlines():
        header = _SESSION_HEADER.match(line)
        if header:
            current_day, current_time = header.group(1), header.group(2)
            continue
        if current_day != day.isoformat():
            continue
        kill = _KILL.search(line)
        if kill is None:
            continue
        out.append(
            ForcedKill(
                process=_process_of(kill.group(2)),
                pid=int(kill.group(1)),
                at_kst=current_time,
                command=kill.group(2),
            )
        )
    return out


def watchdog_log_readable(*, log_path: Path | str = DEFAULT_LOG_PATH) -> bool:
    """워치독 로그를 읽을 수 있었는가 — 못 읽은 것과 강제 종료 0건은 다른 사실이다(L18)."""
    return Path(log_path).exists()


def forced_processes(
    day: date, *, log_path: Path | str = DEFAULT_LOG_PATH
) -> dict[str, ForcedKill]:
    """프로세스 이름 → 그날 마지막 강제 종료 기록."""
    latest: dict[str, ForcedKill] = {}
    for kill in forced_kills(day, log_path=log_path):
        if kill.process != "unknown":
            latest[kill.process] = kill
    return latest


@dataclass(frozen=True)
class SessionEndVerdict:
    """`SessionEnd` 유무에 대한 **단일 판정** — 증거 수집기와 무결성 리포트가 함께 쓴다."""

    #: "clean" | "forced_by_design" | "never_marks" | "abnormal"
    verdict: str
    reason: str

    @property
    def is_finding(self) -> bool:
        """사람이 봐야 하는 사건인가 — 설계된 강제 종료는 아니다."""
        return self.verdict == "abnormal"


def session_end_verdict(
    process: str,
    *,
    has_start: bool,
    has_end: bool,
    forced: ForcedKill | None,
    ever_marks_end: bool = True,
) -> SessionEndVerdict:
    """그 프로세스의 종료를 어떻게 읽을 것인가 (2026-08-21 F-16 ③ — 판정의 단일 출처).

    입력:
        `forced`는 그날 워치독이 이 프로세스를 강제 종료한 기록(`forced_processes()`).
        `ever_marks_end`는 그 프로세스가 `SessionEnd` 마커를 낼 줄 아는가 — 2026-08-07
        이전 로그에는 마커 자체가 없었으므로 옛 이력을 소급해 빨갛게 칠하지 않는다.
    """
    if has_end:
        return SessionEndVerdict("clean", "SessionEnd 있음")
    if not has_start:
        return SessionEndVerdict("clean", "그날 기동 자체가 없음")
    if forced is not None:
        return SessionEndVerdict(
            "forced_by_design",
            f"watchdog 강제 종료(설계) — {forced.at_kst} PID {forced.pid}. "
            f"{process}에는 정상 종료 경로가 없어 scripts/stop_l1_daily.bat이 정리한다",
        )
    if not ever_marks_end:
        return SessionEndVerdict(
            "never_marks",
            f"{process}는 SessionEnd 마커를 낸 적이 없다 — 이 축으로 판정된 적 없는 프로세스",
        )
    return SessionEndVerdict("abnormal", "SessionStart 있고 SessionEnd 없음 — 비정상 종료 의심")
