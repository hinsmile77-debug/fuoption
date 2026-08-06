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
from typing import Sequence

# faulthandler가 Windows에서 치명적 예외를 만났을 때 첫 줄로 찍는 머리말.
# (`Windows fatal exception: access violation` / `... : code 0x...` 등)
_FATAL_HEADER = re.compile(r"^Windows fatal exception: (?P<kind>.+)$")

# 스레드 블록 머리말 — `Thread 0x...` / `Current thread 0x...` 양쪽에 공통으로 붙는다.
# 이 문자열의 등장 횟수가 곧 **덤프에 찍힌 스레드 수**이고, 그 수가 07-31 이후의 핵심 질문
# ("polars 네이티브 호출에 동시에 들어간 스레드가 몇 개였나")의 직접 답이다.
_THREAD_HEADER = re.compile(r"^(?P<which>Current thread|Thread) 0x[0-9a-fA-F]+ \(most recent")

_FRAME = re.compile(r'^\s+File "(?P<file>.+)", line (?P<line>\d+) in (?P<func>.+)$')

# `core/crash_forensics.py`가 무장 직후 남기는 ASCII 마커.
#
# **줄 처음에 고정(`^`)하지 않는다** (2026-08-05). 종전엔 `^...$` 앵커 매치였는데,
# `.bat`가 stderr를 PowerShell 파이프라인(`2>&1 | ForEach-Object`)에 태우면 PS 5.1이
# 네이티브 exe의 stderr **첫 줄**을 NativeCommandError로 감싸 `python.exe : ` 접두사를
# 붙인다. 마커는 프로세스가 내는 첫 stderr 줄이라 정확히 그 자리에 걸렸다:
#
#     python.exe : [crash_forensics] armed tag=l1_daily target=stderr
#
# 그래서 2026-08-04에 l1_daily·g2_paper 둘 다 "무장 마커 없음"으로 잡혔고,
# `crash-forensics-armed` 등록부가 **`재발`(ERROR)** 로 오탐을 냈다 — 무장은 되어 있었다.
# `.bat` 쪽도 고쳤지만(호스트가 stderr를 안 건드리게), 탐지기가 호스트 접두사 하나에
# 깨지는 것 자체가 결함이라 여기도 함께 느슨하게 만든다.
_ARMED_MARKER = re.compile(r"\[crash_forensics\] armed tag=(?P<tag>\S+) target=(?P<target>.+)$")

# 무장 사실의 **두 번째 출처** — 구조화 JSON 로그(`core/logging.py`). stderr 마커가 호스트
# 포맷팅에 오염돼도 이건 stdout의 JSON 한 줄이라 안 깨진다. 검출 수단이 하나뿐이면 그
# 수단이 못 보는 결함은 안 보인다(2026-08-04 피처 관문이 `px_macd_h_5`에서 배운 것과 동일).
_ARMED_JSON = re.compile(r'"tag":\s*"CrashForensicsArmed"')

# 프로세스가 **다시 떴다**는 표지. JSON 로그를 내는 프로세스는 `SessionStart`, Streamlit UI는
# 자기 기동 줄이다(UI 로그는 구조화 로그가 아니다 — 모듈 docstring "왜 UI 로그를 따로 읽나").
_RESTART_MARKER = re.compile(r'"tag":\s*"SessionStart"|Uvicorn server started')

# 로그 줄에서 시각을 뽑는다 — JSON은 `"ts": "2026-08-06T08:36:01.155113+09:00"`,
# UI는 줄머리의 `2026-08-06 10:25:36.343`. 덤프 자체에는 시각이 없어서 **주변 줄로 가둔다**.
_TS_JSON = re.compile(r'"ts":\s*"(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})')
_TS_PLAIN = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

# 덤프 블록의 잔재 — `Thread ...` / `  File "..."` 는 활동으로 세지 않는다.
_DUMP_NOISE = re.compile(r'^(Thread|Current thread) 0x|^\s+File "|^Windows fatal exception')

# **재기동 프로세스의 기동 배너.** `SessionStart`보다 먼저 찍히므로, 이걸 활동으로 세면
# "죽고 다시 뜬 것"이 "살아서 계속 돈 것"으로 뒤집힌다 — 2026-08-06 g2_paper가 실제로 그렇게
# 잘못 판정됐다(`[OK ] config ...`가 이전 프로세스의 활동으로 읽혔다).
_STARTUP_BANNER = re.compile(r"^\[(OK|FAIL|WARN)\s*\]|^self-check:")


def _timestamp_of(line: str) -> str | None:
    """그 줄이 말하는 시각(`HH:MM:SS`) — 못 읽으면 None."""
    for pattern in (_TS_JSON, _TS_PLAIN):
        found = pattern.search(line)
        if found:
            return found.group("ts")[-8:]
    return None


def _survival_after(lines: Sequence[str], index: int) -> bool | None:
    """덤프 블록 뒤를 훑어 **그 프로세스가 계속 돌았는지** 본다.

    True  — 재기동 표지보다 먼저 평범한 활동 줄이 나왔다 = 살아서 계속 로깅했다.
    False — 활동 없이 재기동 표지가 먼저 나왔다.
    None  — 로그가 거기서 끝났다.

    ## False가 "치명"이 아닌 이유

    **로그가 없다는 것은 죽었다는 뜻이 아니다.** `g2_paper`는 번들이 결선되기 전이라 장중에
    아무것도 안 찍고, Streamlit UI도 기동 뒤로는 조용하다. 그 프로세스들은 멀쩡히 돌아도
    덤프와 재기동 사이가 비어 있다.

    그래서 False는 **"덤프 뒤 활동 없이 재기동이 뒤따랐다"는 관측**이지 사인(死因) 판정이
    아니다. 치명 여부는 이벤트로그(`native_crashes`)와 대조해야 갈린다 —
    `collect_crash_forensics()`가 그 대조를 한다.
    """
    for line in lines[index:]:
        text = line.strip()
        if not text or _DUMP_NOISE.match(line) or _STARTUP_BANNER.match(text):
            continue
        if _RESTART_MARKER.search(text):
            return False
        if _ARMED_MARKER.search(text):
            continue  # 재기동 직전의 무장 마커 — 활동이 아니다
        return True
    return None


@dataclass
class FaulthandlerDump:
    """로그에 남은 덤프 1건 — 네이티브 크래시의 **파이썬 레벨 증거**.

    ## 덤프가 났다고 프로세스가 죽은 것은 아니다 (2026-08-06 실측)

    그날 세 프로세스가 전부 `access violation` 덤프를 하나씩 남겼는데, 리포트는 같은 화면에
    `네이티브 크래시: 0건`을 나란히 찍었다. 사람이 그 두 줄을 보고 할 수 있는 판단이 없다.

    조사해 보니 **셋 다 프로세스가 안 죽었다**. l1_daily는 08:36에 덤프를 찍고 10:04까지
    88분을 계속 로깅했다(그 사이 `SessionStart`는 없다). Windows 이벤트로그에도 우리
    프로세스의 Application Error가 없었다. 즉 faulthandler가 **first-chance 예외**를
    잡아 찍은 것이고, 그 예외는 어딘가에서 처리돼 실행이 계속됐다.

    그래서 세 가지를 함께 기록한다:

    - `survived` — 덤프 뒤에 **같은 프로세스의 로그 활동이 이어졌는가**.
      True(살았다) / False(활동 없이 재기동이 뒤따랐다) / None(로그 끝이라 판정 불가).
      False는 **관측**이지 사인 판정이 아니다 — 조용한 프로세스는 살아 있어도 비어 있다
      (`_survival_after` docstring).
    - `after_kst` — 덤프 **직전** JSON 로그 줄의 시각. faulthandler 출력에는 시각이 없어서
      정확한 시점은 알 수 없다 — "이 시각 이후"라는 하한이다.
    - `crashing_frames` — `Current thread` 블록의 프레임. **비어 있는 것 자체가 정보다**:
      faulthandler는 폴트 난 스레드를 `Current thread`로 찍는데 그 블록이 아예 없다는 것은
      **파이썬 상태가 없는 스레드**(주입된 네이티브 DLL 등)에서 났다는 뜻이다.
    """

    process: str
    kind: str  # "access violation" 등
    thread_count: int
    crashing_frames: list[str] = field(default_factory=list)
    survived: bool | None = None
    after_kst: str | None = None

    @property
    def verdict(self) -> str:
        """사람이 읽는 한 줄 — **본 것만 말한다**(사인 판정은 이벤트로그와 대조해야 나온다)."""
        if self.survived is True:
            return "first-chance(덤프 뒤에도 계속 로깅함)"
        if self.survived is False:
            return "생존 미확인(덤프 뒤 활동 없이 재기동)"
        return "생존 미확인(로그 끝)"

    @property
    def frame_summary(self) -> str:
        if self.crashing_frames:
            return self.crashing_frames[0]
        return "파이썬 스레드 아님 — Current thread 블록 없음(네이티브 스레드에서 폴트)"


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
    last_seen_ts: str | None = None
    while index < len(lines):
        header = _FATAL_HEADER.match(lines[index].strip())
        if header is None:
            # 덤프 밖의 줄에서 시각을 계속 갱신한다 — 덤프에는 시각이 없어서 **직전 줄**이
            # 그 덤프의 하한이 된다("이 시각 이후에 났다").
            stamp = _timestamp_of(lines[index])
            if stamp:
                last_seen_ts = stamp
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
                survived=_survival_after(lines, index),
                after_kst=last_seen_ts,
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
        # 두 출처 중 하나만 있어도 무장으로 본다 — 둘 다 없어야 진짜 "증거를 안 남기는 세션"이다.
        armed[process] = bool(_ARMED_JSON.search(text)) or any(
            _ARMED_MARKER.search(line.strip()) for line in text.splitlines()
        )
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

    # **덤프와 이벤트로그의 반대 방향 불일치** (2026-08-06). 덤프 뒤에 활동 없이 재기동이
    # 뒤따랐는데 이벤트로그에는 크래시가 0건이면, 그 프로세스는 **자기가 죽은 게 아니라
    # 밖에서 끝난 것**일 수 있다(호스트 종료·워치독·수동 kill). 원인이 다르면 처방도 다르다.
    restarted = [d for d in dumps if d.survived is False]
    if restarted and native_crashes_available and native_crash_count == 0:
        listed = ", ".join(sorted({d.process for d in restarted}))
        findings.append(
            f"덤프 뒤 재기동이 뒤따른 프로세스({listed})가 있는데 이벤트로그 크래시는 0건 — "
            "스스로 죽은 것이 아니라 밖에서 종료됐을 수 있다(호스트 종료·워치독·수동 kill). "
            "관측 공백 원인과 대조할 것"
        )

    return CrashForensics(armed=armed, dumps=dumps, findings=findings)


def format_dump_lines(forensics: CrashForensics) -> list[str]:
    """사람이 읽는 요약 — **덤프 하나로 판단이 끝나야 한다.**

    2026-08-06까지는 `크래시 덤프(ui): access violation · 스레드 10개 · 프레임 없음`이
    전부였고, 같은 화면의 `네이티브 크래시: 0건`과 모순돼 보였다. 그 두 줄로는 아무 판단도
    할 수 없어서 사람이 매번 로그를 직접 열었다.

    이제 한 줄에 넷을 담는다 — **언제**(하한) · **무엇이**(예외 종류) · **죽었나**(생존 판정) ·
    **어디서**(프레임 또는 "파이썬 스레드 아님").
    """
    lines: list[str] = []
    for dump in forensics.dumps:
        when = f"{dump.after_kst} 이후" if dump.after_kst else "시각 불명"
        lines.append(
            f"  크래시 덤프({dump.process}): {dump.kind} · {when} · {dump.verdict} · "
            f"스레드 {dump.thread_count}개 · {dump.frame_summary}"
        )
    return lines
