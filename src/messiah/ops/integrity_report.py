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

from messiah.core.messages import BarSession, Horizon
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
    flat_price_minutes: int
    pre_open_minutes: int
    market_findings: list[str]
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
    cb_resumed = tag_counts.get("CircuitBreakerResumed", 0)

    findings: list[str] = []
    if stalls > 0 and reconnects == 0:
        findings.append(f"L1 스톨 감지 {stalls}회인데 재연결 성공 0회 — 탐지는 됐으나 복구가 안 됨")
    if cb_confirmed > 0 and (stalls + disconnects) == 0:
        findings.append(
            f"G2 CB 확정 {cb_confirmed}회인데 L1 단절 흔적 0건 — "
            "거래는 멈췄으나 데이터 흐름은 아무도 손대지 않음"
        )
    # 3. **정지와 해제의 짝이 안 맞는다** (2026-07-31 신규) — 그날 확정 5회에 해제 3회였고,
    #    나머지 2회는 게이트가 안 풀린 채 장이 끝났다(6시간 42분 halted). 위 두 규칙은 둘 다
    #    "한쪽에 흔적이 **아예** 없을 때"만 보므로 이 형태를 통과시켰다(그날 findings 0건).
    #
    #    "L1 재연결 N회인데 CB 확정 M회"류의 개수 비교 규칙도 후보였지만 채택하지 않았다 —
    #    진짜 단절이 나서 L1이 복구하고 CB가 정지시킨 **정상 시나리오**와 구분이 안 돼
    #    오탐만 늘린다. 짝 불일치는 그런 모호함이 없다: 확정됐으면 해제도 있어야 한다.
    if cb_confirmed > cb_resumed:
        findings.append(
            f"CB 확정 {cb_confirmed}회 대 해제 {cb_resumed}회 — "
            f"{cb_confirmed - cb_resumed}회가 해제 없이 남음(주문 게이트 잔류 정지 의심)"
        )
    return findings


# ---------------------------------------------------------------- 시장 상태 (2026-07-31)


# 가격이 이만큼 오래 1틱도 안 움직이면 "한산"이 아니라 별개의 시장 상태(상한/하한 고착,
# 일방시장)로 본다 — 스톨 오탐·CB 오탐·피처 퇴화가 전부 이 구간에서 나오므로, 사후 조사가
# 원인을 한 줄로 짚을 수 있어야 한다.
_FLAT_PRICE_ALERT_MINUTES = 30


def _bar_shape_metrics(bar_dir: Path, symbol: str, day: date) -> tuple[int, int]:
    """(가격 고정 분 수, 장전 봉 수) — 그날 1분봉의 "모양"에 대한 두 지표.

    **가격 고정(`o=h=l=c`)**: 2026-07-31엔 380봉 중 60봉이 이 형태였고(14:21 이후 마감까지
    51814 고정), 그게 그날 이상점 대부분의 공통 원인이었는데 리포트엔 그 사실을 가리키는
    숫자가 하나도 없었다 — 사람이 Parquet을 직접 열어야만 보였다.

    **장전 봉 수**: 같은 날 08:45~09:04의 20봉이 전부 스테일 프린트로 보였다
    (`core/messages.py`의 `BarSession`). 매일 몇 봉이 장전 구간에서 들어왔는지를 남겨,
    "그날 장전이 평소와 달랐는가"를 다음 점검이 손으로 안 파도 되게 한다.
    `session` 컬럼이 없던 시절 파일은 전부 정규장으로 읽히므로 0이 나온다(사실과 맞다 —
    그때는 구분 자체가 없었다).
    """
    frame = _load_day_frame(bar_dir, symbol, Horizon.M1, day)
    if frame is None or frame.height == 0:
        return 0, 0
    flat = (
        (pl.col("o_ticks") == pl.col("h_ticks"))
        & (pl.col("h_ticks") == pl.col("l_ticks"))
        & (pl.col("l_ticks") == pl.col("c_ticks"))
    )
    flat_minutes = int(frame.select(flat.sum()).item())
    if "session" not in frame.columns:
        return flat_minutes, 0
    pre_open = int(frame.select((pl.col("session") == BarSession.PRE_OPEN.value).sum()).item())
    return flat_minutes, pre_open


def analyze_market_state(flat_minutes: int, m1: BarContinuity | None) -> list[str]:
    """시장 자체가 평소와 달랐던 날을 드러낸다 — **장애가 아니라 상태**다.

    이걸 별도 항목으로 두는 이유: 이런 날은 결손·CB·NaN 임계가 한꺼번에 터지는데, 그 셋을
    각각 "장애"로만 보면 원인을 못 찾고 매번 처음부터 조사하게 된다. 시장 상태를 먼저 적어
    두면 나머지 초과 항목들이 그 결과라는 게 바로 읽힌다.
    """
    findings: list[str] = []
    if flat_minutes >= _FLAT_PRICE_ALERT_MINUTES:
        span = f"/{m1.rows}봉" if m1 is not None and m1.rows else ""
        findings.append(
            f"가격 고정 {flat_minutes}분{span}(o=h=l=c) — 상한/하한 고착 또는 일방시장 의심, "
            "이날의 스톨·CB·NaN 초과는 이 상태의 결과일 수 있음"
        )
    return findings


def _first_session_start(day: date, session_starts: Mapping[str, Sequence[str]]) -> datetime | None:
    """그날 MESSIAH 프로세스가 처음 기동한 시각(KST 벽시계) — 크래시 집계 창의 시작점.

    로그의 `SessionStart` ts에서 `HH:MM:SS`만 잘라 쓴다(`analyze_logs`가 그렇게 모은다).
    하나도 없으면 `None` — 그날 아예 안 돌았거나 로그가 없는 경우라 창을 좁힐 근거가 없다.

    반환은 **의도적으로 naive**다 — 이 값의 유일한 소비처가 `Get-WinEvent`의 `StartTime`
    문자열이고(`_collect_native_crashes`), Windows 이벤트 로그의 시각은 이 PC의 로컬 시각
    (= KST)이다. tz를 붙이면 오히려 오프셋 변환이 끼어들 여지가 생긴다.
    """
    stamps = [s for starts in session_starts.values() for s in starts if s]
    if not stamps:
        return None
    try:
        # noqa DTZ007: 로그가 남긴 KST 벽시계 문자열이라 tz를 붙일 대상이 아니다(위 docstring).
        earliest = min(datetime.strptime(s, "%H:%M:%S").time() for s in stamps)  # noqa: DTZ007
    except ValueError:
        return None
    return datetime.combine(day, earliest)


# ---------------------------------------------------------------- 네이티브 크래시


def _collect_native_crashes(
    day: date,
    *,
    runner=subprocess.run,
    since: datetime | None = None,
    python_version_prefix: str | None = None,
) -> NativeCrashes:
    """`Application Error`(이벤트 ID 1000) 중 해당 날짜의 python.exe 크래시만 센다.

    2026-07-30 사고의 **유일한** 흔적이 여기였다 — 네이티브 크래시는 프로세스를 즉사시켜
    애플리케이션 로그에 traceback은커녕 한 줄도 안 남긴다.

    ## 남의 크래시를 세지 않는다 (2026-07-31 수정)

    2026-07-31 리포트는 "네이티브 크래시 8건"으로 임계를 초과했는데, 실측해 보니 그중 2건은
    MESSIAH가 아니었다: 06:34:53·06:36:19의 두 건은 python.exe **3.10**(MESSIAH의 .venv는
    3.12) + `KERNELBASE.dll` + 예외코드 `0xc06d007f`로, MESSIAH가 기동하기(08:35) 두 시간
    전에 이 PC의 다른 프로젝트가 낸 것이었다. 임계 초과 목록이 남의 사고로 오염되면 매일
    늑대소년이 된다.

    두 가지로 좁힌다:

    - `since`: 그날 첫 `SessionStart` 시각 이후만 본다. 호출측(`build_report`)이 실제 로그에서
      읽은 값을 넘긴다 — "MESSIAH가 돌지도 않던 시간"을 아예 창에서 뺀다.
    - `python_version_prefix`: 이벤트에 찍힌 결함 프로세스 버전이 이 접두사로 시작하는 것만
      센다(기본값은 지금 이 인터프리터의 major.minor). 같은 PC에 여러 파이썬이 있는 환경에서
      가장 싸게 구분되는 축이다.

    좁힌 뒤에도 **못 세는 것과 0건은 계속 구분한다**(`available=False`, L18) — 이 사고의
    유일한 흔적이 이 집계였으므로 "못 셌다"는 사실 자체가 중요하다.
    """
    if sys.platform != "win32":
        return NativeCrashes(available=False, count=0, details=["Windows 전용 집계 — 건너뜀"])

    start = datetime.combine(day, datetime.min.time())
    if since is not None and since > start:
        start = since
    end = datetime.combine(day, datetime.min.time()) + timedelta(days=1)
    if python_version_prefix is None:
        python_version_prefix = f"{sys.version_info.major}.{sys.version_info.minor}."
    # 출력은 **ASCII만** 뽑는다. 이벤트 로그의 `Message`는 시스템 로캘 언어라(한글 Windows면
    # 한국어) 그대로 받으면 PowerShell이 CP949로 내보내는데, 파이썬이 utf-8로 디코딩하다
    # UnicodeDecodeError가 난다(2026-07-30 실측).
    #
    # 예전엔 그 `Message`에 정규식을 걸어 모듈명을 뽑았는데, 2026-07-31부터 `Properties`
    # 배열을 직접 읽는다 — 로캘 문자열을 아예 안 건드리고(근본 해법), 필요한 값이 전부
    # 구조화된 필드로 있다: [0] 프로세스명 · [1] 프로세스 버전 · [3] 결함 모듈 · [6] 예외코드 ·
    # [7] 결함 오프셋. 특히 **[1] 버전**이 있어야 같은 PC의 다른 파이썬(2026-07-31의 3.10 2건)을
    # 걸러낼 수 있고, **[7] 오프셋**을 남겨야 "같은 주소에서 또 죽었다"를 사후에 대조할 수 있다
    # (07-29·07-30·07-31 UI 크래시 10건이 전부 +0x083973c7 동일이었다).
    script = (
        "Get-WinEvent -FilterHashtable @{LogName='Application';"
        "ProviderName='Application Error';"
        f"StartTime='{start:%Y-%m-%d %H:%M:%S}';EndTime='{end:%Y-%m-%d %H:%M:%S}'"
        "} -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Properties.Count -ge 8 -and "
        "$_.Properties[0].Value -like 'python.exe*' -and "
        f"$_.Properties[1].Value -like '{python_version_prefix}*'"
        " } | "
        "ForEach-Object { $_.TimeCreated.ToString('HH:mm:ss') + ' ' + "
        "$_.Properties[3].Value + ' ' + $_.Properties[6].Value + ' +0x' + $_.Properties[7].Value }"
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

    per_process = {name: analyze_logs(paths) for name, paths in log_paths.items()}
    logs = analyze_logs([path for paths in log_paths.values() for path in paths])
    restarts_by_process = {
        name: len(result["session_starts"]) for name, result in per_process.items()
    }
    session_starts = {name: result["session_starts"] for name, result in per_process.items()}

    # 크래시 집계 창을 "MESSIAH가 실제로 돌던 시간"으로 좁힌다(2026-07-31 — 그날 08:35 기동
    # 전인 06:34·06:36의 남의 프로세스 크래시 2건이 리포트에 섞여 임계를 초과시켰다).
    crashes = crash_collector(day, since=_first_session_start(day, session_starts))

    m1 = next((c for c in continuity if c.horizon == Horizon.M1.value), None)
    restarts = max(restarts_by_process.values(), default=0)
    critical_lines = logs["level_counts"].get("CRITICAL", 0)
    flat_minutes, pre_open_minutes = _bar_shape_metrics(bar_dir, symbol, day)

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
    # 화면이 통째로 사라진 날은 "관측 공백"이라 그 자체가 임계 초과다 — 2026-07-31엔
    # 12:35~15:35 3시간 무화면이었는데 리포트엔 아무 표시도 안 났다.
    ui_gave_up = logs["tag_counts"].get("CommandCenterUIRestartGaveUp", 0)
    if ui_gave_up:
        breaches.append(f"Command Center UI 자동 재기동 포기 {ui_gave_up}회 — 관측 공백 발생")

    data_flow_findings = analyze_data_flow_ownership(logs["tag_counts"])
    breaches.extend(data_flow_findings)
    market_findings = analyze_market_state(flat_minutes, m1)
    breaches.extend(market_findings)

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
        flat_price_minutes=flat_minutes,
        pre_open_minutes=pre_open_minutes,
        market_findings=market_findings,
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
    if report.flat_price_minutes or report.pre_open_minutes:
        lines.append(
            f"  가격 고정(o=h=l=c): {report.flat_price_minutes}분 · "
            f"장전 봉: {report.pre_open_minutes}개"
        )
    for finding in report.market_findings:
        lines.append(f"  ⚠ 시장 상태: {finding}")
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
