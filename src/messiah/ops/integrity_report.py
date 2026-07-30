"""일일 무결성 리포트 — 고도화 2 (2026-07-30).

## 왜 만들었나

2026-07-30 일일 점검에서 나온 이상점은 전부 **사람이 손으로 파야만 보이는 것**이었다:
1분봉 Parquet을 직접 읽어 분 단위 연속성을 검사해야 30분 공백 2건이 나왔고, `FeaturePublish`
DEBUG 라인을 Horizon별로 집계해야 15m/30m가 하루 종일 NaN 2/3라는 게 보였고, `SessionStart`
개수를 세야 그날 6번 재시작됐다는 걸 알았고, Windows 이벤트 로그를 뒤져야 UI 크래시 3건이
나왔다. 그 조사를 다음날 다시 하려면 처음부터 똑같이 손으로 해야 한다.

이 모듈은 **그 조사 자체를 코드로 고정한 것**이다. `Docs/dailycheck_prompt.txt`의 반복
점검이 매번 수작업 포렌식이 되지 않게 하는 것이 목적이다.

## 지표 선정 근거 — 전부 "그날 실제로 놓쳤던 것"

| 지표 | 이 지표가 있었다면 잡았을 사고 |
|---|---|
| 봉 결손 분 수 / 최장 공백 | 07-28 10:13~10:43, 07-29 12:32~13:02 (각 29분 무성 단절) |
| 프로세스 재기동 횟수 | 07-29 L1 6회 재시작 → 피처 워밍업 전량 소실 |
| Horizon별 nan_ratio | 15m 0.678 / 30m 0.694 — 하루 종일 피처 2/3가 NaN |
| 네이티브 크래시 건수 | 07-29 2건 + 07-30 1건 UI 즉사(로그에 한 줄도 안 남음) |
| WARN/ERROR/CRITICAL 태그 집계 | 스톨·UI 사망·적재 실패가 새 태그로 이제 남는다 |

## 판정은 임계와 함께 한다

숫자만 뱉으면 결국 사람이 매일 읽고 판단해야 한다 — `DEFAULT_THRESHOLDS`를 넘긴 항목만
`breaches`로 따로 모아 준다. 임계값 자체는 지금까지의 실측(정상일이었던 07-27은 결손 0분,
재기동 1회)을 기준으로 잡은 **초기값이며 미검증**이다.

## 크래시 집계는 Windows 전용이다

`_collect_native_crashes()`는 `Get-WinEvent`(PowerShell)에 의존한다 — 다른 OS에서는 조용히
0건이 아니라 `available=False`로 "못 셌다"를 명시한다(값이 없는 것과 0인 것을 구분하는
마흐디 L18 원칙). 이 사고의 유일한 흔적이 그 로그였으므로 못 셌다는 사실 자체가 중요하다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

import polars as pl

from messiah.core.messages import Horizon
from messiah.data.archiver import ParquetArchiver

_KST_ZONE_NAME = "Asia/Seoul"

# 정상 운영일의 실측(2026-07-27: 결손 0분·재기동 1회)을 기준으로 한 초기 임계 — 미검증.
DEFAULT_THRESHOLDS: dict[str, float] = {
    "missing_minutes": 5.0,  # 결손 5분 초과면 조사 대상
    "longest_gap_minutes": 3.0,  # 연속 3분 넘게 비면 스톨 의심(워치독 임계 120초와 정합)
    "restarts": 1.0,  # 예정된 08:35 기동 1회를 넘으면 그날 무슨 일이 있었다는 뜻
    "native_crashes": 0.0,  # 네이티브 크래시는 1건도 정상이 아니다
    "critical_log_lines": 0.0,
}


@dataclass
class BarContinuity:
    horizon: str
    rows: int
    first_bar_kst: str | None
    last_bar_kst: str | None
    missing_minutes: int
    longest_gap_minutes: int
    gaps: list[tuple[str, str, int]] = field(default_factory=list)


@dataclass
class NativeCrashes:
    available: bool
    count: int
    details: list[str] = field(default_factory=list)


@dataclass
class IntegrityReport:
    date: str
    symbol: str
    instance_id: str
    bar_continuity: list[BarContinuity]
    restarts: int  # 프로세스별 최댓값 — 임계 판정용 스칼라
    restarts_by_process: dict[str, int]
    session_starts_kst: dict[str, list[str]]
    nan_ratio_by_horizon: dict[str, dict[str, float]]
    log_level_counts: dict[str, int]
    tag_counts: dict[str, int]
    circuit_breaker_events: dict[str, int]
    data_flow_findings: list[str]
    native_crashes: NativeCrashes
    breaches: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------- 봉 연속성


def _load_day_frame(bar_dir: Path, symbol: str, horizon: Horizon, day: date) -> pl.DataFrame | None:
    """`ParquetArchiver.read_day()`를 쓴다 — 장중(조각)인지 장후(통합본)인지에 따라 물리
    배치가 다르므로(`data/archiver.py` "조각 쓰기"), 경로를 여기서 다시 조립하면 장중에
    리포트를 돌렸을 때 데이터가 없다고 나온다."""
    frame = ParquetArchiver(bar_dir).read_day(symbol, horizon, day)
    if frame is None:
        return None
    return frame.with_columns(pl.col("bar_open_kst").dt.convert_time_zone(_KST_ZONE_NAME)).sort(
        "bar_open_kst"
    )


def analyze_bar_continuity(
    bar_dir: Path, symbol: str, day: date, *, horizons: Sequence[Horizon] = (Horizon.M1,)
) -> list[BarContinuity]:
    """봉 사이 간격이 Horizon 길이보다 크면 그만큼을 결손으로 센다.

    M1만 기본으로 보는 이유: 굵은 Horizon은 M1이 비면 따라서 비므로 같은 사고를 중복 계상할
    뿐이고, 분 단위가 공백의 시작·끝을 가장 정확히 짚는다(실측 조사도 M1으로 했다).
    """
    out: list[BarContinuity] = []
    for horizon in horizons:
        step = _horizon_minutes(horizon)
        frame = _load_day_frame(bar_dir, symbol, horizon, day)
        if frame is None or frame.height == 0:
            out.append(BarContinuity(horizon.value, 0, None, None, 0, 0))
            continue

        stamps = frame["bar_open_kst"].to_list()
        gaps: list[tuple[str, str, int]] = []
        for earlier, later in zip(stamps, stamps[1:]):
            missing = int((later - earlier).total_seconds() // 60) - step
            if missing > 0:
                gaps.append((earlier.strftime("%H:%M"), later.strftime("%H:%M"), missing))

        out.append(
            BarContinuity(
                horizon=horizon.value,
                rows=len(stamps),
                first_bar_kst=stamps[0].strftime("%H:%M"),
                last_bar_kst=stamps[-1].strftime("%H:%M"),
                missing_minutes=sum(g[2] for g in gaps),
                longest_gap_minutes=max((g[2] for g in gaps), default=0),
                gaps=gaps,
            )
        )
    return out


def _horizon_minutes(horizon: Horizon) -> int:
    return int(horizon.value.rstrip("m"))


# ---------------------------------------------------------------- 로그 집계


def _iter_json_lines(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    for path in paths:
        if not path.exists():
            continue
        # utf-8-sig: PowerShell tee가 BOM을 붙인다(`run_l1_daily.bat`) — 이걸 안 벗기면
        # 첫 줄이 JSON으로 안 읽힌다(실측).
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue  # self_check의 사람용 출력 등 — JSON 라인만 본다
            try:
                yield json.loads(line)
            except ValueError:
                continue


def analyze_logs(log_paths: Sequence[Path]) -> dict[str, Any]:
    level_counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}
    session_starts: list[str] = []
    nan_by_horizon: dict[str, list[float]] = {}
    cb_events: dict[str, int] = {}

    for record in _iter_json_lines(log_paths):
        level = str(record.get("level", "-"))
        tag = str(record.get("tag", "-"))
        level_counts[level] = level_counts.get(level, 0) + 1
        tag_counts[tag] = tag_counts.get(tag, 0) + 1

        if tag == "SessionStart":
            session_starts.append(str(record.get("ts", ""))[11:19])
        elif tag == "FeaturePublish":
            horizon = str(record.get("horizon", "?"))
            ratio = record.get("nan_ratio")
            if isinstance(ratio, (int, float)):
                nan_by_horizon.setdefault(horizon, []).append(float(ratio))
        elif tag.startswith("CircuitBreaker"):
            cb_events[tag] = cb_events.get(tag, 0) + 1

    nan_summary = {
        horizon: {
            "median": round(median(values), 4),
            "min": round(min(values), 4),
            "last": round(values[-1], 4),
            "samples": len(values),
        }
        for horizon, values in sorted(nan_by_horizon.items())
    }
    return {
        "level_counts": level_counts,
        "tag_counts": tag_counts,
        "session_starts": session_starts,
        "nan_ratio_by_horizon": nan_summary,
        "circuit_breaker_events": cb_events,
    }


# ---------------------------------------------------------------- 탐지·복구 소유권 (고도화 4)


def analyze_data_flow_ownership(tag_counts: Mapping[str, int]) -> list[str]:
    """L1(탐지·복구)과 G2(매매 판단)의 판정이 어긋난 흔적을 찾는다.

    계층 분리 자체는 `data/collector.py` 모듈 docstring "데이터 흐름의 1차 책임" 참고. 두
    판정을 런타임에 묶지 않기로 한 이상(한쪽 버그가 조용히 다른 쪽을 오염시키지 않게), 어긋난
    사실은 사후에라도 반드시 드러나야 한다 — 그게 이 함수다.

    잡아내는 두 가지:

    1. **L1이 감지했는데 복구를 못 했다** — `CollectorTickStall`은 났는데
       `CollectorWSReconnected`가 없다. 강제 재연결이 걸렸으면 재연결 로그가 따라야 한다.
    2. **G2만 알고 L1은 몰랐다** — `CircuitBreakerConfirmed`는 났는데 L1 쪽에 단절 흔적
       (`CollectorTickStall`/`CollectorWSDisconnected`)이 하나도 없다. 이건 2026-07-28·29의
       30분 공백과 정확히 같은 구조다: 거래는 멈췄는데 데이터 흐름은 아무도 안 고쳤다는 뜻.
    """
    stalls = tag_counts.get("CollectorTickStall", 0)
    disconnects = tag_counts.get("CollectorWSDisconnected", 0)
    reconnects = tag_counts.get("CollectorWSReconnected", 0)
    cb_confirmed = tag_counts.get("CircuitBreakerConfirmed", 0)

    findings: list[str] = []
    if stalls > 0 and reconnects == 0:
        findings.append(f"L1 스톨 감지 {stalls}회인데 재연결 성공 0회 — 탐지는 됐으나 복구가 안 됨")
    if cb_confirmed > 0 and (stalls + disconnects) == 0:
        findings.append(
            f"G2 CB 확정 {cb_confirmed}회인데 L1 단절 흔적 0건 — "
            "거래는 멈췄으나 데이터 흐름은 아무도 손대지 않음"
        )
    return findings


# ---------------------------------------------------------------- 네이티브 크래시


def _collect_native_crashes(day: date, *, runner=subprocess.run) -> NativeCrashes:
    """`Application Error`(이벤트 ID 1000) 중 해당 날짜의 python.exe 크래시만 센다.

    2026-07-30 사고의 **유일한** 흔적이 여기였다 — 네이티브 크래시는 프로세스를 즉사시켜
    애플리케이션 로그에 traceback은커녕 한 줄도 안 남긴다.
    """
    if sys.platform != "win32":
        return NativeCrashes(available=False, count=0, details=["Windows 전용 집계 — 건너뜀"])

    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)
    # 출력은 **ASCII만** 뽑는다(시각 + 오류 모듈 파일명). 이벤트 로그의 `Message`는 시스템
    # 로캘 언어라(한글 Windows면 한국어) 그대로 받으면 PowerShell이 CP949로 내보내는데,
    # 파이썬이 utf-8로 디코딩하다 UnicodeDecodeError가 난다(2026-07-30 실측). 로캘 문자열을
    # 아예 안 건드리는 게 근본 해법 — 필요한 정보(언제, 어느 모듈)는 정규식으로만 뽑는다.
    script = (
        "Get-WinEvent -FilterHashtable @{LogName='Application';"
        "ProviderName='Application Error';"
        f"StartTime='{start:%Y-%m-%d %H:%M:%S}';EndTime='{end:%Y-%m-%d %H:%M:%S}'"
        "} -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Message -like '*python.exe*' } | "
        "ForEach-Object { $m = [regex]::Match($_.Message, '[\\w.]+\\.(pyd|dll)'); "
        "$_.TimeCreated.ToString('HH:mm:ss') + ' ' + "
        "$(if ($m.Success) { $m.Value } else { 'unknown' }) }"
    )
    try:
        result = runner(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",  # 그래도 로캘 바이트가 섞여 오면 리포트를 죽이지 말고 흘린다
            timeout=60,
        )
    except Exception as exc:  # noqa: BLE001 — 못 세는 것과 0건은 다르다
        return NativeCrashes(available=False, count=0, details=[f"집계 실패: {exc}"])

    if result.returncode != 0:
        return NativeCrashes(available=False, count=0, details=["Get-WinEvent 실패"])

    details = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    return NativeCrashes(available=True, count=len(details), details=details)


# ---------------------------------------------------------------- 조립


def build_report(
    *,
    day: date,
    symbol: str,
    instance_id: str,
    bar_dir: Path,
    log_paths: Mapping[str, Sequence[Path]],
    thresholds: dict[str, float] | None = None,
    crash_collector=_collect_native_crashes,
) -> IntegrityReport:
    """`log_paths`는 프로세스 이름 → 로그 파일 목록이다.

    프로세스별로 나눠 받는 이유: 재기동 횟수를 통째로 합치면 "L1이 6번 + G2가 5번"이
    "11번"으로 뭉뚱그려져 **어느 프로세스가 불안정한지**가 사라진다(2026-07-29가 정확히 그
    형태였다 — 원인은 워치독이 L1을 죽인 것이었고 G2 재시작은 그 여파였다).
    """
    limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    continuity = analyze_bar_continuity(bar_dir, symbol, day)
    crashes = crash_collector(day)

    per_process = {name: analyze_logs(paths) for name, paths in log_paths.items()}
    logs = analyze_logs([path for paths in log_paths.values() for path in paths])
    restarts_by_process = {
        name: len(result["session_starts"]) for name, result in per_process.items()
    }
    session_starts = {name: result["session_starts"] for name, result in per_process.items()}

    m1 = next((c for c in continuity if c.horizon == Horizon.M1.value), None)
    restarts = max(restarts_by_process.values(), default=0)
    critical_lines = logs["level_counts"].get("CRITICAL", 0)

    breaches: list[str] = []
    if m1 is not None and m1.missing_minutes > limits["missing_minutes"]:
        breaches.append(
            f"1분봉 결손 {m1.missing_minutes}분 > 임계 {limits['missing_minutes']:.0f}분"
        )
    if m1 is not None and m1.longest_gap_minutes > limits["longest_gap_minutes"]:
        breaches.append(
            f"최장 공백 {m1.longest_gap_minutes}분 > 임계 {limits['longest_gap_minutes']:.0f}분"
        )
    for name, count in sorted(restarts_by_process.items()):
        if count > limits["restarts"]:
            breaches.append(f"{name} 재기동 {count}회 > 임계 {limits['restarts']:.0f}회")
    if crashes.available and crashes.count > limits["native_crashes"]:
        breaches.append(f"네이티브 크래시 {crashes.count}건")
    if critical_lines > limits["critical_log_lines"]:
        breaches.append(f"CRITICAL 로그 {critical_lines}건")

    data_flow_findings = analyze_data_flow_ownership(logs["tag_counts"])
    breaches.extend(data_flow_findings)

    return IntegrityReport(
        date=day.isoformat(),
        symbol=symbol,
        instance_id=instance_id,
        bar_continuity=continuity,
        restarts=restarts,
        restarts_by_process=restarts_by_process,
        session_starts_kst=session_starts,
        nan_ratio_by_horizon=logs["nan_ratio_by_horizon"],
        log_level_counts=logs["level_counts"],
        tag_counts=logs["tag_counts"],
        circuit_breaker_events=logs["circuit_breaker_events"],
        data_flow_findings=data_flow_findings,
        native_crashes=crashes,
        breaches=breaches,
    )


def format_summary(report: IntegrityReport) -> str:
    """사람이 장 마감 후 30초 안에 훑을 수 있는 요약 — 상세는 JSON에 있다."""
    lines = [f"=== 일일 무결성 리포트 {report.date} ({report.symbol}) ==="]
    for continuity in report.bar_continuity:
        span = (
            f"{continuity.first_bar_kst}~{continuity.last_bar_kst}"
            if continuity.first_bar_kst
            else "데이터 없음"
        )
        lines.append(
            f"  봉 {continuity.horizon}: {continuity.rows}개 {span} · "
            f"결손 {continuity.missing_minutes}분(최장 {continuity.longest_gap_minutes}분)"
        )
    for name, count in sorted(report.restarts_by_process.items()):
        lines.append(f"  {name} 재기동: {count}회 {report.session_starts_kst.get(name, [])}")

    if report.nan_ratio_by_horizon:
        parts = [
            f"{horizon} 중앙 {stat['median']:.2f}/최종 {stat['last']:.2f}"
            for horizon, stat in report.nan_ratio_by_horizon.items()
        ]
        lines.append("  피처 NaN 비율: " + " · ".join(parts))

    crashes = report.native_crashes
    lines.append(
        f"  네이티브 크래시: {crashes.count}건"
        if crashes.available
        else "  네이티브 크래시: 집계 불가(Windows 전용)"
    )

    levels = report.log_level_counts
    lines.append(
        f"  로그: CRITICAL {levels.get('CRITICAL', 0)} · ERROR {levels.get('ERROR', 0)} · "
        f"WARNING {levels.get('WARNING', 0)}"
    )
    if report.circuit_breaker_events:
        lines.append(f"  CB 이벤트: {report.circuit_breaker_events}")
    for finding in report.data_flow_findings:
        lines.append(f"  ⚠ 탐지·복구 불일치: {finding}")

    if report.breaches:
        lines.append("  ⚠ 임계 초과:")
        lines.extend(f"    - {breach}" for breach in report.breaches)
    else:
        lines.append("  ✅ 임계 초과 없음")
    return "\n".join(lines)


# ---------------------------------------------------------------- 산출 (CLI·장후 절차 공용)

DEFAULT_LOG_DIR = Path("logs")
DEFAULT_BAR_DIR = Path("data") / "bars"


def log_paths_for(day: date, log_dir: Path = DEFAULT_LOG_DIR) -> dict[str, list[Path]]:
    """프로세스 이름 → 그날의 로그 파일. 실제 로그는 `.bat`가 stdout을 날짜별 파일로 tee한
    것뿐이다(`scripts/agenda.py`가 2026-07-27에 정정한 것과 같은 사실)."""
    stamp = day.strftime("%Y%m%d")
    return {
        "l1_daily": [log_dir / f"l1_daily_{stamp}.log"],
        "g2_paper": [log_dir / f"g2_daily_{stamp}.log"],
    }


def generate_and_write(
    *,
    day: date,
    symbol: str,
    instance_id: str,
    bar_dir: Path = DEFAULT_BAR_DIR,
    log_dir: Path = DEFAULT_LOG_DIR,
) -> IntegrityReport:
    """리포트를 만들어 `logs/daily_integrity_YYYYMMDD.json`에 쓰고 로그에 남긴다.

    CLI(`scripts/daily_integrity_report.py`)와 장후 종료 절차(`run_l1_daily.py`)가 같은
    함수를 쓴다 — 두 경로가 갈리면 "손으로 돌린 리포트와 자동 리포트가 다르다"는 최악의
    형태가 된다.
    """
    from messiah.core import logging as mlog  # 순환 방지용 지역 임포트 아님 — 로깅만 필요

    report = build_report(
        day=day,
        symbol=symbol,
        instance_id=instance_id,
        bar_dir=bar_dir,
        log_paths=log_paths_for(day, log_dir),
    )
    out_path = log_dir / f"daily_integrity_{day.strftime('%Y%m%d')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(format_summary(report), flush=True)
    mlog.log(
        "IntegrityReportGenerated",
        "일일 무결성 리포트 산출",
        date=report.date,
        symbol=symbol,
        restarts_by_process=report.restarts_by_process,
        breaches=len(report.breaches),
        path=str(out_path),
    )
    for breach in report.breaches:
        mlog.log("IntegrityThresholdBreached", breach, date=report.date, symbol=symbol)
    return report
