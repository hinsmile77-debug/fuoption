"""faulthandler 덤프 수집 — 크래시 포렌식 상시화 (2026-08-03 고도화 D).

## 왜 이 모듈이 생겼나

2026-08-03 일일점검에서 UI 크래시 2건의 정체를 밝히려고 **사람이 손으로** 했던 일이 있다:
Windows 이벤트로그를 뒤지고(ID 1000/1001), WER `Report.wer` 원본을 열고, fault offset을
과거 4일치와 대조했다. 그 조사로 얻은 결론은 결국 "5거래일 연속 같은 주소"였는데, 그건
**매일 자동으로 나왔어야 하는 사실**이다.

`ops/integrity_report.py`의 `_collect_native_crashes()`가 이벤트로그 쪽 절반은 이미 자동화해
뒀다(시각·모듈·예외코드·오프셋). 이 모듈은 나머지 절반 — 2026-08-03에 새로 무장한
`core/crash_forensics.py`가 로그에 남기는 **파이썬 레벨 덤프** — 를 맡는다.

## 이 모듈이 만드는 가장 중요한 한 줄

    "네이티브 크래시 2건인데 faulthandler 덤프 0건 — 그 세션은 무장되지 않았다"

5거래일 동안 세 번이나 잘못된 가설로 고친 근본 이유가 정확히 이 상태였다(증거 없음). 그런데
그때는 **증거가 없다는 사실 자체도 리포트에 안 나왔다** — 사람이 로그를 열어보고서야 알았다.
무장 마커(`[crash_forensics] armed ...`)와 크래시 건수를 대조하면 그 공백이 매일 자동으로
드러난다.

## 왜 UI 로그를 따로 읽나

`log_paths_for()`는 JSON 로그를 내는 프로세스(l1_daily·g2_paper)만 돌려준다 — Streamlit이
찍는 `ui_{date}.log`는 구조화 로그가 아니라 거기 안 들어 있다. 그런데 **5거래일 크래시가 전부
그 UI 프로세스**에서 났으므로, 포렌식만큼은 UI 로그를 반드시 봐야 한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# faulthandler가 Windows에서 치명적 예외를 만났을 때 첫 줄로 찍는 머리말.
# (`Windows fatal exception: access violation` / `... : code 0x...` 등)
_FATAL_HEADER = re.compile(r"^Windows fatal exception: (?P<kind>.+)$")

# 스레드 블록 머리말 — `Thread 0x...` / `Current thread 0x...` 양쪽에 공통으로 붙는다.
# 이 문자열의 등장 횟수가 곧 **덤프에 찍힌 스레드 수**이고, 그 수가 07-31 이후의 핵심 질문
# ("polars 네이티브 호출에 동시에 들어간 스레드가 몇 개였나")의 직접 답이다.
_THREAD_HEADER = re.compile(r"^(?P<which>Current thread|Thread) 0x[0-9a-fA-F]+ \(most recent")

_FRAME = re.compile(r'^\s+File "(?P<file>.+)", line (?P<line>\d+) in (?P<func>.+)$')

# `core/crash_forensics.py`가 무장 직후 남기는 ASCII 마커.
_ARMED_MARKER = re.compile(r"^\[crash_forensics\] armed tag=(?P<tag>\S+) target=(?P<target>.+)$")


@dataclass
class FaulthandlerDump:
    """로그에 남은 덤프 1건 — 네이티브 크래시의 **파이썬 레벨 증거**."""

    process: str
    kind: str  # "access violation" 등
    thread_count: int
    crashing_frames: list[str] = field(default_factory=list)


@dataclass
class CrashForensics:
    armed: dict[str, bool]  # 프로세스 → 무장 마커가 로그에 있었나
    dumps: list[FaulthandlerDump] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)


def forensics_log_paths(day: date, log_dir: Path) -> dict[str, Path]:
    """포렌식 대상 로그 — **UI를 포함한다**(모듈 docstring "왜 UI 로그를 따로 읽나")."""
    stamp = day.strftime("%Y%m%d")
    return {
        "ui": log_dir / f"ui_{stamp}.log",
        "l1_daily": log_dir / f"l1_daily_{stamp}.log",
        "g2_paper": log_dir / f"g2_daily_{stamp}.log",
    }


def parse_dumps(process: str, text: str) -> list[FaulthandlerDump]:
    """한 로그 본문에서 faulthandler 덤프를 전부 뽑는다.

    덤프 형태(실측):

        Windows fatal exception: access violation
        <빈 줄>
        Thread 0x00006598 (most recent call first):
          File "...", line 5 in <lambda>
          ...
        <빈 줄>
        Current thread 0x00000f30 (most recent call first):
          File "...", line 525 in string_at

    `Current thread` 블록이 **죽은 스레드**다 — 거기 프레임만 `crashing_frames`로 남긴다.
    나머지 스레드는 개수만 센다(전문은 원본 로그에 있고, 리포트에 통째로 실으면 못 읽는다).
    """
    lines = text.splitlines()
    dumps: list[FaulthandlerDump] = []
    index = 0
    while index < len(lines):
        header = _FATAL_HEADER.match(lines[index].strip())
        if header is None:
            index += 1
            continue

        thread_count = 0
        frames: list[str] = []
        in_crashing_thread = False
        index += 1
        while index < len(lines):
            line = lines[index]
            thread = _THREAD_HEADER.match(line.strip())
            if thread is not None:
                thread_count += 1
                in_crashing_thread = thread.group("which") == "Current thread"
                index += 1
                continue
            frame = _FRAME.match(line)
            if frame is not None:
                if in_crashing_thread:
                    frames.append(
                        f"{Path(frame.group('file')).name}:{frame.group('line')} "
                        f"{frame.group('func')}"
                    )
                index += 1
                continue
            if not line.strip():
                index += 1
                continue
            # 덤프 블록이 끝났다 — 다음 크래시를 찾으러 바깥 루프로 돌아간다.
            break

        dumps.append(
            FaulthandlerDump(
                process=process,
                kind=header.group("kind").strip(),
                thread_count=thread_count,
                crashing_frames=frames,
            )
        )
    return dumps


def collect_crash_forensics(
    day: date,
    *,
    log_dir: Path,
    native_crash_count: int,
    native_crashes_available: bool,
) -> CrashForensics:
    """그날 로그에서 무장 여부와 덤프를 모으고, 둘과 이벤트로그 집계를 **대조**한다.

    대조가 이 함수의 핵심이다 — 덤프를 모으는 것보다, "크래시는 났는데 덤프가 없다"를
    말하는 것이 5거래일간 없었던 신호다.
    """
    armed: dict[str, bool] = {}
    dumps: list[FaulthandlerDump] = []
    findings: list[str] = []

    for process, path in forensics_log_paths(day, log_dir).items():
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        armed[process] = any(_ARMED_MARKER.match(line.strip()) for line in text.splitlines())
        dumps.extend(parse_dumps(process, text))

    for process, is_armed in sorted(armed.items()):
        if not is_armed:
            findings.append(
                f"{process} 로그에 crash_forensics 무장 마커 없음 — "
                f"그 세션은 네이티브 크래시가 나도 증거를 안 남긴다"
            )

    if native_crashes_available and native_crash_count > 0 and not dumps:
        # 2026-07-29~08-03을 다섯 번 반복하게 만든 바로 그 상태.
        findings.append(
            f"네이티브 크래시 {native_crash_count}건인데 faulthandler 덤프 0건 — "
            f"원인 규명 불가 상태(무장 확인 필요)"
        )

    return CrashForensics(armed=armed, dumps=dumps, findings=findings)


def format_dump_lines(forensics: CrashForensics) -> list[str]:
    """사람이 읽는 요약 — 덤프가 있으면 **죽은 지점의 파이썬 프레임**을 바로 보여준다.
    이 두세 줄이 5거래일간 없어서 매번 정황 추론을 해야 했다."""
    lines: list[str] = []
    for dump in forensics.dumps:
        top = dump.crashing_frames[0] if dump.crashing_frames else "프레임 없음"
        lines.append(
            f"  크래시 덤프({dump.process}): {dump.kind} · 스레드 {dump.thread_count}개 · {top}"
        )
    return lines
