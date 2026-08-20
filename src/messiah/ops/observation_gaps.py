"""관측 공백 — "오늘 우리는 **언제 눈을 감고 있었나**, 그리고 왜" (2026-08-06 P1-1·P1-2).

## 왜 이 모듈이 생겼나

2026-08-06 10:03:49에 호스트가 재부팅됐다. MESSIAH 세 프로세스가 전부 죽었고 10:25까지
21분간 아무것도 관측하지 못했다. 그날 리포트가 그 사건에 대해 말한 것은 두 줄뿐이었다:

    l1_daily 재기동 1회 · g2_paper 재기동 1회

**왜 재기동했는지도, 그 사이 몇 분을 잃었는지도, UI가 같이 죽었다는 것도 말하지 않았다.**

세 가지가 각각 빠져 있었다:

1. **공백의 크기** — `restarts`는 횟수만 센다. 2분 재기동과 21분 정지가 같은 "1회"다.
2. **공백의 원인** — `_collect_native_crashes()`가 이미 이벤트로그를 여는데, 호스트 생명주기
   이벤트(1074/6006/13/12/6005/41)는 안 봤다. 그 여섯 개면 "10:03:49 재부팅, 사유 기타
   (계획되지 않음)"가 자동으로 나왔을 자리다. 실제로는 사람이 조사에서 손으로 캤다.
3. **UI의 공백** — `ui_restarts`는 인프로세스 워치독의 **자동 재기동만** 센다. 밖에서
   죽는 경로는 이 지표의 시야 밖이라, UI가 10:04~10:25 사라졌는데 값은 **0**이었다.
   그 위에서 등록부 `ui-restart-observability`는 "검증 완료 3거래일"을 찍고 있었다 —
   필드 주석이 *"관측 공백의 직접 지표"* 라고 적힌 지표가, 21분짜리 관측 공백을 못 봤다.

## 공백의 시작 시각을 어떻게 아는가

프로세스는 죽을 때 아무것도 안 남긴다. 그래서 두 출처를 순서대로 쓴다:

    1순위  호스트 종료 이벤트   — 있으면 **정확한 시각**이다(`exact=True`).
                                 2026-08-06이면 10:04:31(이벤트 13).
    2순위  그 프로세스의 마지막 로그 활동 — 상한이다. 실제 죽음은 그 이후 어딘가다.

2순위가 특히 헐거운 프로세스가 있다: `g2_paper`는 번들 결선 전이라 장중에 아무것도 안 찍고,
Streamlit UI는 기동 뒤로 조용하다. 그래서 **공백을 과대평가**할 수 있고, 그 사실을
`exact=False`로 함께 남긴다 — 모르는 것을 아는 척하지 않는다(L18).

## 왜 `restarts`를 대체하지 않고 더하나

`restarts`는 **횟수**를, 이쪽은 **시간과 원인**을 센다. 재기동 0회인데 공백이 있을 수 있고
(프로세스가 죽은 채 안 돌아온 날), 재기동 2회인데 공백이 1분일 수도 있다. 둘은 다른 질문이다.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Mapping, Sequence

# 공백의 원인으로 인용할 Windows 시스템 이벤트.
#
#   1074  사용자/프로세스가 종료·재시작을 개시 — **사유 문자열이 붙는다**(가장 값어치 있다).
#         2026-08-06: "RuntimeBroker.exe ... 기타(계획되지 않음)".
#   6006  이벤트 로그 서비스 정지 = 정상 종료의 마지막 지점
#   13    OS 종료 시작
#   12    OS 기동
#   6005  이벤트 로그 서비스 시작 = 부팅 완료
#   41    Kernel-Power — **정상 종료 없이** 꺼졌다(전원 차단·블루스크린). 1074가 없는 정지의 답.
_EVENT_IDS = (1074, 6006, 13, 12, 6005, 41)

# 종료로 읽는 이벤트 — 공백의 **시작** 후보.
_SHUTDOWN_IDS = frozenset({1074, 6006, 13, 41})

# 공백 시작 후보로 인정할 최대 소급 거리. 공백보다 훨씬 이전의 종료 이벤트를 끌어다 쓰면
# 엉뚱한 원인이 붙는다 — 공백 구간 안에 있는 것만 쓴다는 뜻이라 넉넉히 잡아도 안전하다.
_CAUSE_LOOKBACK_MINUTES = 90.0


@dataclass
class HostEvent:
    """호스트 생명주기 이벤트 1건."""

    event_id: int
    at_kst: str  # HH:MM:SS
    kind: str  # "shutdown" | "boot"
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class ObservationGap:
    """한 프로세스가 **아무것도 관측하지 못한** 구간."""

    process: str
    from_kst: str
    to_kst: str
    minutes: float
    # 시작 시각을 정확히 아는가 — 호스트 이벤트로 특정했으면 True, 마지막 로그 활동으로
    # 추정했으면 False(그 경우 실제 공백은 이보다 **짧다**).
    exact: bool
    cause: str = "원인 불명"
    # 이 원인을 **누가** 말했나 (2026-08-19 F-6).
    #
    #   auto        호스트 이벤트로 기계가 특정했다 — 또는 특정 못 했다("원인 불명")
    #   unresolved  자동 판정이 원인을 못 찾았고 사람이 적어 둔 것도 없다
    #   human       `configs/incident_causes.yaml`에 사람이 근거와 함께 적었다
    #
    # 2026-08-19에 사람은 12:14에 원인을 확정(Windows Update)했는데 산출물은 15:45까지
    # "원인 불명"으로 봉인했다. 두 앎이 만나는 자리가 없었다.
    cause_source: str = "auto"
    # 사람이 적은 근거 문서 — `cause_source == "human"`일 때만 채워진다.
    evidence: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def model_replace(self, **changes: object) -> "ObservationGap":
        """일부 필드만 바꾼 새 값 — 이 dataclass는 변경 가능하지만 교체로 다룬다."""
        return replace(self, **changes)  # type: ignore[arg-type]

    def describe(self) -> str:
        bound = "" if self.exact else " 이하"
        # 사람이 적은 원인은 **근거와 함께** 읽혀야 한다 — 출처 없는 단정은 자동 판정보다
        # 신뢰도가 낮다(`configs/incident_causes.yaml` 상단 주석).
        tail = f" (사람 확정 · {self.evidence})" if self.cause_source == "human" else ""
        return (
            f"{self.process}: {self.from_kst}~{self.to_kst} "
            f"{self.minutes:.0f}분{bound} 관측 공백 — {self.cause}{tail}"
        )


@dataclass
class ObservationReport:
    events: list[HostEvent] = field(default_factory=list)
    gaps: list[ObservationGap] = field(default_factory=list)
    # 이벤트로그를 **읽을 수 있었나** — 못 읽은 것과 "이벤트 0건"은 다르다(L18).
    events_available: bool = False
    events_detail: str = ""

    @property
    def worst_minutes(self) -> float:
        return max((g.minutes for g in self.gaps), default=0.0)


# ---------------------------------------------------------------- 호스트 이벤트


def _query_script(start: datetime, end: datetime) -> str:
    """`_collect_native_crashes()`와 **같은 규율**의 PowerShell 스크립트.

    - 항상 `exit 0`으로 끝나고 첫 줄에 `OK <건수>` 또는 `ERR <예외형>`을 찍는다. 종료 코드로
      판정하면 "이벤트 0건"과 "질의 실패"가 구분되지 않는다(2026-08-04에 그 혼동으로
      등록부가 영원히 판정 불가였다).
    - "이벤트 없음"은 로케일 메시지가 아니라 번역되지 않는 `FullyQualifiedErrorId`로 본다.
    - `Message`(로캘 문장)는 안 쓰고 `Properties`의 개별 필드만 읽는다.

    ## 로캘 문자열을 여기서는 **포기하지 않는다**

    `_collect_native_crashes()`는 CP949 깨짐을 피하려고 문자열을 통째로 안 읽는 길을 택했다.
    그쪽은 모듈명·예외코드가 전부 ASCII라 그래도 됐지만, 여기서 가장 값어치 있는 정보는
    1074의 **사유 문장**이다("기타(계획되지 않음)" — 계획된 업데이트 재부팅과 갈리는 지점).

    그래서 스크립트 첫머리에서 콘솔 출력 인코딩을 UTF-8로 고정한다. 그러면 파이썬이
    `encoding="utf-8"`로 그대로 받는다(2026-08-06 실측: 고정 전 `기타(계획되지 않음)`가
    `�ٽ� ����`로 깨졌다).

    1074의 `Properties` 배열 실측 배치:
    `[0]` 개시 프로세스 · `[2]` 사유 · `[4]` 종료 유형("다시 시작"/"시스템 종료").
    """
    ids = ",".join(str(i) for i in _EVENT_IDS)
    return (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
        "$ErrorActionPreference='Stop'; $events=@(); "
        "try { $events=@(Get-WinEvent -FilterHashtable @{LogName='System';"
        f"Id={ids};"
        f"StartTime='{start:%Y-%m-%d %H:%M:%S}';EndTime='{end:%Y-%m-%d %H:%M:%S}'"
        "} -ErrorAction Stop) } "
        "catch { if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { $events=@() } "
        "else { Write-Output ('ERR ' + $_.Exception.GetType().Name); exit 0 } } "
        "Write-Output ('OK ' + $events.Count); "
        "$events | Sort-Object TimeCreated | ForEach-Object { "
        "$reason=''; "
        "if ($_.Id -eq 1074 -and $_.Properties.Count -ge 5) { "
        "$who = Split-Path -Leaf ([string]$_.Properties[0].Value -replace ' \\(.*$',''); "
        "$reason = $who + ' / ' + [string]$_.Properties[4].Value + ' / ' "
        "+ [string]$_.Properties[2].Value }; "
        "[string]$_.Id + ' ' + $_.TimeCreated.ToString('HH:mm:ss') + ' ' + $reason }; "
        "exit 0"
    )


def collect_host_events(day: date, *, runner=subprocess.run) -> tuple[list[HostEvent], bool, str]:
    """그날의 호스트 생명주기 이벤트 — (목록, 읽을 수 있었나, 사유).

    실패 조건: 없다 — 못 읽으면 빈 목록 + `available=False`. 관측 도구가 리포트를 죽이면 안 된다.
    """
    if sys.platform != "win32":
        return [], False, "Windows 전용 집계 — 건너뜀"

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
        return [], False, f"조회 실패: {type(exc).__name__}"

    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    if result.returncode != 0 or not lines or not lines[0].startswith("OK "):
        reason = lines[0] if lines else f"출력 없음(exit={result.returncode})"
        return [], False, f"Get-WinEvent 실패: {reason}"

    events: list[HostEvent] = []
    for line in lines[1:]:
        parts = line.split(" ", 2)
        if len(parts) < 2 or not parts[0].isdigit() or len(parts[1]) != 8:
            continue
        event_id = int(parts[0])
        events.append(
            HostEvent(
                event_id=event_id,
                at_kst=parts[1],
                kind="shutdown" if event_id in _SHUTDOWN_IDS else "boot",
                detail=(parts[2].strip() if len(parts) > 2 else ""),
            )
        )
    return events, True, f"이벤트 {len(events)}건"


def describe_event(event: HostEvent) -> str:
    """사람이 읽는 한 줄 — 이벤트 번호를 외우지 않아도 되게 한다."""
    label = {
        1074: "종료·재시작 개시",
        6006: "이벤트 로그 정지(정상 종료)",
        13: "OS 종료",
        12: "OS 기동",
        6005: "이벤트 로그 시작(부팅 완료)",
        41: "비정상 전원 차단(Kernel-Power)",
    }.get(event.event_id, f"이벤트 {event.event_id}")
    return f"{event.at_kst} {label}" + (f" — {event.detail}" if event.detail else "")


# ---------------------------------------------------------------- 공백 계산


def _to_dt(day: date, clock: str) -> datetime | None:
    try:
        parsed = datetime.strptime(clock, "%H:%M:%S").time()  # noqa: DTZ007 — KST 벽시계 문자열
    except ValueError:
        return None
    return datetime.combine(day, parsed)


def _cause_for(
    day: date, gap_end: datetime, events: Sequence[HostEvent]
) -> tuple[datetime | None, str]:
    """공백 구간에 걸치는 **가장 늦은 종료 이벤트** — (시각, 사유 문장).

    가장 늦은 것을 쓰는 이유: 1074(개시) → 13(종료) 순으로 찍히는데, 관측이 실제로 끊긴
    시점은 뒤쪽이다. 사유 문자열은 1074에만 있으므로 그건 따로 붙여 준다.
    """
    window_start = gap_end - timedelta(minutes=_CAUSE_LOOKBACK_MINUTES)
    candidates = []
    for event in events:
        if event.kind != "shutdown":
            continue
        moment = _to_dt(day, event.at_kst)
        if moment is None or not (window_start <= moment <= gap_end):
            continue
        candidates.append((moment, event))
    if not candidates:
        return None, "원인 불명 — 호스트 종료 이벤트 없음"

    moment, event = max(candidates, key=lambda pair: pair[0])
    # 사유는 1074에만 붙는다 — 같은 창의 1074가 있으면 그 문장을 인용한다.
    initiated = [e for _, e in candidates if e.event_id == 1074 and e.detail]
    reason = f" ({initiated[-1].detail})" if initiated else ""
    return moment, f"호스트 {describe_event(event).split(' ', 1)[1]}{reason}"


def find_gaps(
    day: date,
    *,
    starts_by_process: Mapping[str, Sequence[str]],
    activity_by_process: Mapping[str, Sequence[str]],
    events: Sequence[HostEvent] = (),
) -> list[ObservationGap]:
    """프로세스별 **재기동 사이의** 공백을 계산한다.

    입력: `starts_by_process`는 `HH:MM:SS` 기동 시각들, `activity_by_process`는 그 프로세스가
         뭔가를 찍은 시각들(둘 다 `analyze_logs`가 주는 형태). UI처럼 활동 로그가 없는
         프로세스는 빈 목록을 넘기면 되고, 그때는 호스트 이벤트가 유일한 근거가 된다.
    계산: 기동 i+1 직전의 공백 = [죽은 시각, 기동 i+1]. 죽은 시각은 호스트 종료 이벤트(정확)
         또는 마지막 활동(상한) 중 **더 늦은 쪽**을 쓴다 — 둘 다 있으면 호스트 이벤트가
         활동보다 뒤일 때만 그쪽이 맞다.

    **세션 창을 안 받는다.** 마지막 기동 이후 조용히 사라진 경우는 여기서 안 센다 —
    정상 종료(15:35)와 구분할 근거가 이 모듈엔 없고, 그 구분은 봉 연속성
    (`analyze_bar_continuity`)이 이미 한다. 창을 받아 두면 안 쓰는 인자가 남고,
    호출측의 tz-aware 시각과 이 모듈의 naive 벽시계가 섞이는 자리가 된다.
    """
    gaps: list[ObservationGap] = []

    for process, starts in sorted(starts_by_process.items()):
        moments = sorted(m for m in (_to_dt(day, s) for s in starts) if m is not None)
        if len(moments) < 2:
            continue  # 재기동이 없으면 프로세스 사이 공백도 없다
        activity = sorted(
            m for m in (_to_dt(day, s) for s in activity_by_process.get(process, ())) if m
        )

        for previous, restart in zip(moments, moments[1:]):
            last_activity = max((m for m in activity if previous <= m < restart), default=previous)
            cause_at, cause = _cause_for(day, restart, events)
            if cause_at is not None and cause_at >= last_activity:
                died_at, exact = cause_at, True
            else:
                died_at, exact = last_activity, False
                if cause_at is not None:
                    # 호스트 이벤트가 마지막 활동보다 **앞**이다 — 그 종료는 이 공백의
                    # 원인이 아니다(다른 재기동의 것이거나 순서가 안 맞는다).
                    cause = "원인 불명 — 호스트 종료 이벤트와 시각이 안 맞음"
            minutes = (restart - died_at).total_seconds() / 60.0
            if minutes <= 0:
                continue
            gaps.append(
                ObservationGap(
                    process=process,
                    from_kst=f"{died_at:%H:%M:%S}",
                    to_kst=f"{restart:%H:%M:%S}",
                    minutes=round(minutes, 1),
                    exact=exact,
                    cause=cause,
                )
            )
    return gaps


DEFAULT_CAUSES_PATH = Path("configs") / "incident_causes.yaml"

# 사람이 적은 시각과 리포트가 잰 시각이 이만큼 안쪽이면 같은 공백으로 본다.
# 로그가 갱신되면 추정 시각이 몇 분 움직일 수 있는데, 그때마다 짝이 끊기면 사람이 매번
# 파일을 고쳐야 한다 — 그러면 아무도 안 고친다.
_CAUSE_MATCH_TOLERANCE_MINUTES = 5.0


def apply_known_causes(
    day: date, gaps: Sequence[ObservationGap], *, path: Path | None = None
) -> list[ObservationGap]:
    """사람이 확정한 원인을 공백에 붙인다 (2026-08-19 F-6, `configs/incident_causes.yaml`).

    ## 왜 필요한가

    2026-08-19에 두 프로세스가 세 시간 가까이 사라졌고, 호스트 이벤트 로그엔 그 시각
    부근에 아무것도 없었다(05:52 부팅 3건뿐). 그래서 리포트는 두 건 다 *"원인 불명 —
    호스트 종료 이벤트 없음"* 으로 봉인했다. 그런데 그날 12:14에 사람이 조사를 끝내
    Windows Update로 원인을 확정해 딥다이브 문서에 적어 두었다 — **두 앎이 만나는 자리가
    없었다.**

    ## 자동 판정이 이긴다

    `_cause_for()`가 호스트 이벤트로 원인을 특정했으면 그걸 덮지 않는다. 기계가 본 증거가
    사람의 기억보다 강하고, 무엇보다 이 파일이 자동 판정을 가리기 시작하면 자동 판정이
    틀렸을 때 아무도 모른다. 이 통로는 **자동이 침묵한 자리**만 채운다.

    실패 조건: 없다. 파일이 없거나 못 읽으면 원본을 그대로 돌려준다 — 보조 입력이 리포트
              산출을 막으면 본말전도다(`ops/session_guard`류와 같은 원칙).
    """
    entries = _load_causes(path or DEFAULT_CAUSES_PATH, day)
    if not entries:
        return [g.model_replace(cause_source="unresolved") if _unresolved(g) else g for g in gaps]

    out: list[ObservationGap] = []
    for gap in gaps:
        if not _unresolved(gap):
            out.append(gap)  # 기계가 이미 원인을 특정했다 — 덮지 않는다(위 docstring)
            continue
        match = _match(day, gap, entries)
        if match is None:
            out.append(gap.model_replace(cause_source="unresolved"))
            continue
        out.append(
            gap.model_replace(
                cause=str(match.get("cause", "")),
                cause_source="human",
                evidence=str(match.get("evidence", "")),
            )
        )
    return out


def _unresolved(gap: ObservationGap) -> bool:
    """자동 판정이 원인을 못 찾았는가 — `_cause_for()`가 내는 두 문장으로 판정한다."""
    return gap.cause.startswith("원인 불명")


def _load_causes(path: Path, day: date) -> list[dict]:
    try:
        import yaml

        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, ImportError):
        return []
    entries = payload.get("incidents")
    if not isinstance(entries, list):
        return []
    stamp = day.isoformat()
    return [e for e in entries if isinstance(e, dict) and str(e.get("date", "")).strip() == stamp]


def _match(day: date, gap: ObservationGap, entries: Sequence[dict]) -> dict | None:
    """같은 프로세스 · 시각이 허용 오차 안 — 가장 가까운 항목."""
    target = _to_dt(day, gap.from_kst)
    if target is None:
        return None
    best: tuple[float, dict] | None = None
    for entry in entries:
        if str(entry.get("process", "")) != gap.process:
            continue
        moment = _to_dt(day, str(entry.get("from_kst", "")))
        if moment is None:
            continue
        distance = abs((moment - target).total_seconds()) / 60.0
        if distance > _CAUSE_MATCH_TOLERANCE_MINUTES:
            continue
        if best is None or distance < best[0]:
            best = (distance, entry)
    return None if best is None else best[1]


def parse_ui_starts(log_text: str) -> list[str]:
    """Streamlit UI 로그에서 기동 시각을 뽑는다 — `2026-08-06 10:25:36.343 Uvicorn ...`.

    UI 로그는 구조화 로그가 아니라 `analyze_logs()`가 못 본다. 그런데 **관측 공백을 재려면
    UI야말로 봐야 하는 프로세스다** — 사람이 장중에 보는 화면이 그것이고, 2026-08-06에
    21분간 사라졌는데 `ui_restarts`는 0이었다.
    """
    starts: list[str] = []
    for line in log_text.splitlines():
        if "Uvicorn server started" not in line:
            continue
        head = line.strip()[:19]
        if len(head) == 19 and head[10] == " ":
            starts.append(head[11:19])
    return starts


def summarize(report: ObservationReport) -> list[str]:
    """사람이 읽는 요약 — **공백이 없는 날도 한 줄 남긴다**(측정된 0과 미검사를 가른다)."""
    lines: list[str] = []
    if not report.events_available:
        lines.append(f"  관측 공백: 호스트 이벤트 판정 불가 — {report.events_detail}")
    if not report.gaps:
        lines.append("  관측 공백: 없음 ✅")
        return lines
    lines.append(f"  관측 공백 {len(report.gaps)}건 (최장 {report.worst_minutes:.0f}분):")
    lines.extend(f"    {gap.describe()}" for gap in report.gaps)
    return lines
