"""수정 유효성 자동 검증 — 고도화 B (2026-08-03).

## 왜 만들었나

2026-07-29부터 08-03까지 Command Center UI가 **5거래일 연속 같은 fault offset**으로 죽었다.
그 사이 세 번 "고쳤다"고 판정했고 세 번 재발했다:

    07-30  원자적 쓰기 + mmap 제거     → 다음 거래일 재발
    07-30  Parquet 꼬리 매직 검증       → 다음 거래일 재발
    07-31  numpy 선임포트 + 직렬화 락   → 다음 거래일(08-03) 재발

매번 판정 기준은 있었다 — 07-31 기록에도 *"진짜 판정은 다음 거래일 `native_crashes` 0건"*
이라고 적혀 있다. 문제는 그 기준이 **사람 머릿속과 마크다운 산문에만** 있었다는 것이다.
다음 거래일에 그걸 다시 꺼내 확인하는 일 자체를 아무도 강제하지 않았고, 그래서 재발을
"새로운 사고"로 취급하며 또 새 가설을 세웠다.

이 모듈은 그 판정 기준을 **기계가 읽는 형태로 등록**하고, 매일 장후에 자동으로 채점한다.
`configs/pending_verifications.yaml`에 "무엇을 언제까지 어떤 수치로 판정할지"를 적어두면
`logs/daily_integrity_*.json` 이력을 대조해 다음 넷 중 하나를 돌려준다:

    검증 완료   등록 이후 N거래일 연속으로 기준을 만족했다
    검증 대기   아직 표본이 모자란다 (n/N)
    재발        등록 이후 한 번이라도 기준을 위반했다  ← 가장 중요한 신호
    기한 초과   기한까지 검증이 안 끝났다

**재발**이 이 모듈의 존재 이유다. 그 판정이 자동으로 나왔다면 07-31에 "고쳤다"고 적은 다음
거래일 아침에 시스템이 먼저 "아니오"라고 말했을 것이다.

## 왜 무결성 리포트 breach가 아니라 별도 출력인가

이 채점은 **오늘 리포트가 쓰인 뒤에** 그 파일을 포함한 이력 전체를 읽어야 성립한다 —
`build_report()` 안에서 하면 자기 자신을 읽어야 하는 순환이 된다. 그래서 `generate_and_write()`
가 리포트를 쓴 **다음** 단계로 돌린다. 결과는 로그 태그(`FixVerification*`)와
`scripts/agenda.py` 안건으로 나간다.

## DECISION_LOG의 "라이브 미검증"과의 관계

기존에도 *"라이브 미검증 항목은 검증 기한을 명기한다"*(L15)는 규율이 있고 `agenda.py`가 그걸
회의 안건화한다. 그건 **사람이 판단하는** 항목용이고, 이 모듈은 **수치로 자동 판정 가능한**
항목용이다. 둘은 대체 관계가 아니라 보완 관계다 — 자동 판정이 가능한 것을 사람 규율에
맡겨 두었던 게 이번 5거래일의 실패였다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

import yaml

DEFAULT_REGISTRY_PATH = Path("configs") / "pending_verifications.yaml"
DEFAULT_LOG_DIR = Path("logs")


class VerificationStatus:
    """문자열 상수 — JSON/로그에 그대로 나가므로 Enum 대신 값 자체를 쓴다."""

    VERIFIED = "검증 완료"
    PENDING = "검증 대기"
    RECURRED = "재발"
    OVERDUE = "기한 초과"


def _bar_1m(report: dict[str, Any]) -> dict[str, Any] | None:
    for entry in report.get("bar_continuity", []):
        if entry.get("horizon") == "1m":
            return entry
    return None


def _native_crashes(report: dict[str, Any]) -> float | None:
    """집계 불가(`available=False`)는 **0건이 아니라 판정 불가**다 — 못 센 날을 "깨끗한 날"로
    세면 검증이 통과해 버린다(L18, 이 프로젝트에서 가장 자주 재발하는 실패 형태)."""
    crashes = report.get("native_crashes") or {}
    if not crashes.get("available"):
        return None
    return float(crashes.get("count", 0))


def _native_crashes_measurable(report: dict[str, Any]) -> float | None:
    """크래시를 셀 수 있었는가 — 1.0(쟀다) / 0.0(못 쟀다) / None(원래 못 세는 플랫폼).

    2026-08-04 회귀용 지표다. 그날 `Get-WinEvent`가 exit 1로 끝나 "집계 불가"가 됐는데
    원인은 **크래시가 0건이었기 때문**이었다(창에 이벤트가 없으면 비종료 오류가 난다).
    즉 계측이 성공한 날에만 계측이 실패하는 상태였고, 그 상태로는 `native_crashes`를 보는
    등록부가 매일 "판정 불가"라 **영원히 검증이 안 끝난다**.
    """
    crashes = report.get("native_crashes") or {}
    # `supported` 키가 없는 옛 리포트(08-04 이전)는 판정 근거가 없다 — 모르는 것을 좋은
    # 쪽으로도 나쁜 쪽으로도 가정하지 않는다.
    if "supported" not in crashes:
        return None
    if not crashes.get("supported"):
        return None
    return 1.0 if crashes.get("available") else 0.0


# 등록부에서 쓸 수 있는 지표 — **무결성 리포트에 실제로 있는 필드만** 연다. 임의 표현식을
# 허용하면 등록부가 코드가 되고, 그러면 등록부 자체를 검증해야 하는 문제가 생긴다.
METRIC_EXTRACTORS: dict[str, Callable[[dict[str, Any]], float | None]] = {
    "native_crashes": _native_crashes,
    "faulthandler_dumps": lambda r: float(len((r.get("crash_forensics") or {}).get("dumps", []))),
    "ui_restarts": lambda r: float(r.get("ui_restarts", 0)),
    "restarts": lambda r: float(r.get("restarts", 0)),
    "critical_log_lines": lambda r: float((r.get("log_level_counts") or {}).get("CRITICAL", 0)),
    "breaches": lambda r: float(len(r.get("breaches", []))),
    "missing_minutes": lambda r: (
        None if _bar_1m(r) is None else float(_bar_1m(r)["missing_minutes"])  # type: ignore[index]
    ),
    "longest_gap_minutes": lambda r: (
        None if _bar_1m(r) is None else float(_bar_1m(r)["longest_gap_minutes"])  # type: ignore[index]
    ),
    # **"존재하는가"를 재는 지표** (2026-08-04, F2). 다른 항목이 전부 "나쁜 일이 몇 번
    # 일어났나"(max 기준)인 반면 이건 "좋은 일이 일어나긴 했나"(min 기준)를 잰다.
    #
    # 선행 프로젝트 마흐디가 2026-08-03에 배운 것이다: 그날 예측 13개 중 12개가 자동으로
    # 확인됐는데 **그 어떤 가설도 `find_gamma_flip()`이 전 이력에서 한 번도 값을 낸 적이
    # 없다는 사실을 잡지 못했다** — 아무도 "계산되는가"를 예측치로 적지 않았기 때문이다.
    # 넉 달간 앙상블 멤버 하나가 죽어 있었고, 넉 달 동안 "개선"해 온 대상이 애초에 없었다.
    "tick_rows": lambda r: float(r.get("tick_rows", 0)),
    # 시계 스큐는 부호가 아니라 **크기**가 판정 대상이다 (2026-08-05) — 어느 쪽으로 벌어져도
    # 완성봉 경계 판정이 깨진다. 못 잰 날은 None(판정 불가)이지 0이 아니다.
    "clock_skew_abs_seconds": lambda r: (
        None if r.get("clock_skew_seconds") is None else abs(float(r["clock_skew_seconds"]))
    ),
    # 1분봉 ↔ 상위 Horizon 거래량 항등식 위반 건수 (`analyze_horizon_consistency`).
    "horizon_findings": lambda r: float(len(r.get("horizon_findings", []))),
    # **좁은 지표 둘** (2026-08-05). 종전에 이 둘은 `breaches`라는 넓은 그물로 채점됐는데,
    # 그러면 **아무 상관 없는 사고 하나가 이 수정들을 "재발"로 만든다**. 실제로 2026-08-04
    # 리포트에서 체결틱 0행(그날 결선 전이라 정상) 때문에 `crash-forensics-armed`가 ERROR로
    # 찍혔다 — 무장은 정상이었다. 넓은 그물은 늑대소년을 만든다.
    #
    # 무장 안 된 프로세스 수 — 0이어야 한다.
    "crash_forensics_unarmed": lambda r: float(
        sum(
            1
            for armed in ((r.get("crash_forensics") or {}).get("armed") or {}).values()
            if not armed
        )
    ),
    # 크래시를 **셀 수 있었는가** (1=쟀다, 0=못 쟀다). min 기준으로 쓴다.
    # 이 플랫폼에서 원래 못 세는 경우(`supported=False`)는 판정 대상이 아니므로 None.
    "native_crashes_measurable": _native_crashes_measurable,
}


@dataclass(frozen=True)
class PendingVerification:
    id: str
    summary: str
    registered: date
    metric: str
    consecutive_days: int
    deadline: date | None = None
    max_value: float | None = None
    min_value: float | None = None

    def satisfied_by(self, value: float) -> bool:
        if self.max_value is not None and value > self.max_value:
            return False
        if self.min_value is not None and value < self.min_value:
            return False
        return True

    def criterion_text(self) -> str:
        if self.max_value is not None and self.min_value is not None:
            return f"{self.min_value:g} ≤ {self.metric} ≤ {self.max_value:g}"
        if self.max_value is not None:
            return f"{self.metric} ≤ {self.max_value:g}"
        if self.min_value is not None:
            return f"{self.metric} ≥ {self.min_value:g}"
        return self.metric


@dataclass(frozen=True)
class VerificationVerdict:
    id: str
    summary: str
    status: str
    clean_days: int
    required_days: int
    detail: str

    @property
    def needs_attention(self) -> bool:
        """사람이 반드시 봐야 하는 판정 — 재발과 기한 초과."""
        return self.status in (VerificationStatus.RECURRED, VerificationStatus.OVERDUE)


class RegistryError(ValueError):
    """등록부 자체가 잘못됐다 — 조용히 넘기면 "검증하고 있다는 착각"이 생긴다."""


def load_registry(path: Path = DEFAULT_REGISTRY_PATH) -> list[PendingVerification]:
    """등록부를 읽는다. 파일이 없으면 빈 목록(아직 등록된 수정이 없는 정상 상태).

    실패 조건: 지표 이름이 `METRIC_EXTRACTORS`에 없거나 기준(max/min)이 하나도 없으면
              `RegistryError`. **오타 난 항목을 조용히 건너뛰면 "검증 중"이라고 믿는 항목이
              실제로는 아무것도 안 보고 있게 된다** — 이 모듈이 막으려는 실패 그 자체다.
    """
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = raw.get("verifications") or []

    out: list[PendingVerification] = []
    for entry in entries:
        metric = entry.get("metric")
        if metric not in METRIC_EXTRACTORS:
            raise RegistryError(
                f"{entry.get('id', '?')}: 알 수 없는 지표 '{metric}' — "
                f"사용 가능: {sorted(METRIC_EXTRACTORS)}"
            )
        if entry.get("max") is None and entry.get("min") is None:
            raise RegistryError(f"{entry.get('id', '?')}: max/min 중 최소 하나는 있어야 한다")
        out.append(
            PendingVerification(
                id=str(entry["id"]),
                summary=str(entry.get("summary", "")),
                registered=_as_date(entry["registered"]),
                metric=metric,
                consecutive_days=int(entry.get("consecutive_days", 1)),
                deadline=_as_date(entry["deadline"]) if entry.get("deadline") else None,
                max_value=None if entry.get("max") is None else float(entry["max"]),
                min_value=None if entry.get("min") is None else float(entry["min"]),
            )
        )
    return out


def _as_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def load_daily_reports(log_dir: Path = DEFAULT_LOG_DIR) -> dict[date, dict[str, Any]]:
    """`logs/daily_integrity_YYYYMMDD.json` 전부 — 날짜 → 리포트 dict."""
    reports: dict[date, dict[str, Any]] = {}
    for path in sorted(log_dir.glob("daily_integrity_*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            reports[date.fromisoformat(report["date"])] = report
        except (OSError, ValueError, KeyError):
            continue  # 깨진 리포트 하나가 나머지 채점을 막지 않는다
    return reports


def evaluate(
    registry: list[PendingVerification],
    reports: dict[date, dict[str, Any]],
    *,
    today: date,
) -> list[VerificationVerdict]:
    """등록된 수정들을 리포트 이력으로 채점한다.

    **등록일 자체는 채점하지 않는다** — 수정은 그날 장이 끝난 뒤에 들어가므로 그날 리포트는
    수정 이전의 세계다. 판정은 그 다음 거래일부터다.
    """
    verdicts: list[VerificationVerdict] = []
    for item in registry:
        extractor = METRIC_EXTRACTORS[item.metric]
        judged_days = sorted(day for day in reports if day > item.registered)

        clean = 0
        violated_on: date | None = None
        unjudged: list[date] = []
        for day in judged_days:
            value = extractor(reports[day])
            if value is None:
                unjudged.append(day)  # 못 잰 날 — 통과로도 위반으로도 안 센다(L18)
                continue
            if item.satisfied_by(value):
                clean += 1
            else:
                violated_on = day
                break

        verdicts.append(_verdict_for(item, clean, violated_on, unjudged, today))
    return verdicts


def _verdict_for(
    item: PendingVerification,
    clean: int,
    violated_on: date | None,
    unjudged: list[date],
    today: date,
) -> VerificationVerdict:
    note = f"({item.criterion_text()}"
    if unjudged:
        note += f", 판정 불가 {len(unjudged)}일"
    note += ")"

    if violated_on is not None:
        return VerificationVerdict(
            item.id,
            item.summary,
            VerificationStatus.RECURRED,
            clean,
            item.consecutive_days,
            f"{violated_on.isoformat()}에 기준 위반 — 수정이 듣지 않았다 {note}",
        )
    if clean >= item.consecutive_days:
        return VerificationVerdict(
            item.id,
            item.summary,
            VerificationStatus.VERIFIED,
            clean,
            item.consecutive_days,
            f"{clean}거래일 연속 기준 충족 {note}",
        )
    if item.deadline is not None and today > item.deadline:
        return VerificationVerdict(
            item.id,
            item.summary,
            VerificationStatus.OVERDUE,
            clean,
            item.consecutive_days,
            f"기한 {item.deadline.isoformat()} 경과 — "
            f"아직 {clean}/{item.consecutive_days}일 {note}",
        )
    return VerificationVerdict(
        item.id,
        item.summary,
        VerificationStatus.PENDING,
        clean,
        item.consecutive_days,
        f"{clean}/{item.consecutive_days}거래일 {note}",
    )


def format_verdicts(verdicts: list[VerificationVerdict]) -> list[str]:
    if not verdicts:
        return ["  (등록된 검증 대기 수정 없음)"]
    marks = {
        VerificationStatus.VERIFIED: "✅",
        VerificationStatus.PENDING: "⏳",
        VerificationStatus.RECURRED: "❌",
        VerificationStatus.OVERDUE: "⚠",
    }
    return [
        f"  {marks.get(v.status, '·')} [{v.status}] {v.id} — {v.summary}\n      {v.detail}"
        for v in verdicts
    ]


def run(
    *,
    today: date,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    log_dir: Path = DEFAULT_LOG_DIR,
) -> list[VerificationVerdict]:
    """등록부 + 리포트 이력 → 판정. 호출측(`ops/integrity_report.py`·`scripts/agenda.py`)이
    출력 형식을 정한다."""
    return evaluate(load_registry(registry_path), load_daily_reports(log_dir), today=today)


def main() -> int:
    """수시 확인용 CLI — `python -m messiah.ops.fix_verification`."""
    import sys

    from messiah.core.timeutil import now_kst

    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    verdicts = run(today=now_kst().date())
    print("=== 수정 유효성 검증 현황 ===")
    for line in format_verdicts(verdicts):
        print(line)
    return 1 if any(v.needs_attention for v in verdicts) else 0


if __name__ == "__main__":
    raise SystemExit(main())
