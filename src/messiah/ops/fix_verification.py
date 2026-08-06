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
    # 연속으로 못 잰 상태 (2026-08-05 고도화 2) — "진행 중"이 아니라 **계측 고장**이다.
    # 2026-08-04에 크래시 집계가 0건인 날에만 실패해 등록부가 영원히 0/3이었다.
    STALLED = "판정 불가 정체"
    # 결과 지표는 아직 깨끗한데 **그 수정이 딛고 선 전제**가 무너졌다 (2026-08-05 2차,
    # 고도화 4). 재발보다 이른 신호다 — 다음 사고의 예고에 가깝다.
    PREMISE_BROKEN = "전제 붕괴"


def _bar_1m(report: dict[str, Any]) -> dict[str, Any] | None:
    for entry in report.get("bar_continuity", []):
        if entry.get("horizon") == "1m":
            return entry
    return None


def _latency(report: dict[str, Any], key: str) -> float | None:
    """회선 수신 지연 분위수 — 못 잰 날은 None(판정 불가)이지 0이 아니다(L18)."""
    latency = report.get("delivery_latency")
    if not isinstance(latency, dict):
        return None
    value = latency.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _absent_watchlist_features(report: dict[str, Any]) -> float | None:
    """변동성 축 채점에서 "피처셋에 없음"으로 찍힌 관심 피처 수 (Horizon 전체 합)."""
    horizons = (report.get("vol_axis") or {}).get("horizons")
    if not isinstance(horizons, dict) or not horizons:
        return None  # 채점을 안 돌린 날 — 0개와 구분한다(L18)
    total = 0
    seen_field = False
    for entry in horizons.values():
        if not isinstance(entry, dict) or "absent_features" not in entry:
            continue  # 이 필드 이전에 쓰인 옛 산출물 — 판정 근거가 없다
        seen_field = True
        total += len(entry.get("absent_features") or [])
    return float(total) if seen_field else None


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


def _boot_recovery_armed(report: dict[str, Any]) -> float | None:
    """수집 작업의 부팅 트리거 무장 여부 — `host_health`의 `boot_recovery` 항목에서 읽는다.

    항목이 없는 옛 리포트(2026-08-06 이전)는 None이다 — 모르는 것을 좋은 쪽으로도 나쁜
    쪽으로도 가정하지 않는다(`_native_crashes_measurable`과 같은 규율).
    """
    for check in (report.get("host_health") or {}).get("checks") or []:
        if not isinstance(check, dict) or check.get("name") != "boot_recovery":
            continue
        if not check.get("available"):
            return None  # 못 쟀다 — 0(무장 안 됨)과 구분한다(L18)
        return 1.0 if check.get("ok") else 0.0
    return None


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
    # 상위 Horizon 버킷에서 빠진 1분봉 수 (2026-08-05 장중, `data/bar_composer.py`).
    #
    # `horizon_findings`와 **짝이되 대체재가 아니다**: 그쪽은 아카이브를 보므로 장 종료 후
    # `run_recompose.py`를 돌리면 0이 된다. 그러면 "재합성했으니 등록부도 통과"가 되어,
    # 정작 고쳐야 할 수집 경로의 결함이 판정에서 사라진다. 이 지표는 로그를 세므로 재합성에
    # 지워지지 않는다 — **결과가 아니라 원인을 채점하는 자리다.**
    "late_bar_drops": lambda r: float(r.get("late_bar_drops", 0)),
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
    # 세션 내내 죽어 있던 피처 수 (2026-08-05 고도화 3). 0이어야 한다 — 값을 내지만
    # 안 변하는 피처는 `nan_ratio`에 흔적이 없어 8거래일 내내 안 보였다(`px_macd_h_5`).
    "degenerate_feature_count": lambda r: float(
        sum(
            len(entry.get("always_nan") or []) + len(entry.get("constant") or [])
            for entry in (r.get("degenerate_features") or {}).values()
        )
    ),
    # 적재 계열 시간 커버리지 판정 수 (2026-08-06 고도화 2, `ops/series_coverage.py`).
    #
    # `horizon_findings`가 상위 봉의 정합을 보는 것과 같은 자리 — 이쪽은 **봉이 아닌 계열**
    # (옵션체인·수급·틱)이 세션 내내 끊김 없이 쌓였는가를 본다. 2026-08-06에 옵션 1,500다리와
    # 수급 264행이 사라졌는데 리포트가 조용했던 이유가 이 축의 부재였다.
    #
    # **행수가 아니라 시간을 센다**: 그날 regular는 1,302행이었고 행수로는 정상으로 보였다.
    # 첫 사이클이 10:30이었다는 사실은 커버리지로만 드러난다.
    "series_gap_findings": lambda r: float(len(r.get("series_findings", []))),
    # 계열 중 가장 큰 **머리 구멍**(세션 시작 → 첫 행, 분). 재기동 파괴의 직접 지표다 —
    # 2026-08-06에 4개 계열이 111~116분이었다. 정상일에는 카덴스 한 번(최대 10분)이다.
    # 계열이 하나도 없으면 None(판정 불가)이지 0이 아니다(L18).
    "series_head_gap_minutes_max": lambda r: (
        max((float(e.get("head_gap_minutes") or 0.0) for e in (r.get("series_coverage") or [])))
        if (r.get("series_coverage") or [])
        else None
    ),
    # 가장 긴 **관측 공백**(분) — 프로세스가 죽어 아무것도 못 본 구간 (2026-08-06 P1-1).
    #
    # `restarts`(횟수)가 못 보는 것을 본다: 2분 재기동과 21분 정지가 같은 "1회"였다.
    # 그리고 `ui_restarts`가 구조적으로 못 보는 **UI의 공백**이 여기 들어온다 — 그쪽은
    # 인프로세스 워치독의 자동 재기동만 세므로, 2026-08-06에 UI가 21분 사라졌는데 0이었다.
    #
    # 축이 없던 옛 리포트(2026-08-06 이전)는 None이다 — 그날들을 0으로 세면 "공백이 없었다"가
    # 되어 검증이 거짓으로 통과한다(L18).
    "observation_gap_minutes_max": lambda r: (
        max((float(g.get("minutes") or 0.0) for g in r["observation_gaps"]), default=0.0)
        if isinstance(r.get("observation_gaps"), list)
        else None
    ),
    # 수집 작업에 부팅 트리거가 걸려 있는가 — 1.0(무장) / 0.0(안 됨) / None(못 잼).
    # **min 기준으로 쓰는 "존재하는가" 지표다**(`tick_rows`와 같은 계열). 부팅 복구가 실제로
    # 동작하는지는 다음 재부팅까지 모르지만, 무장 여부는 매일 알 수 있다 — 2026-08-06 이전
    # 상태(트리거 없음)로 조용히 돌아가는 것을 잡는 자리다.
    "boot_recovery_armed": _boot_recovery_armed,
    # 그날 못 잰 축의 수 (2026-08-05 고도화 2). **이 지표가 이 등록부의 메타 지표다** —
    # 다른 항목들이 "판정 불가"로 정체되는 근본 원인이 여기 모여 있다.
    "unmeasured_count": lambda r: float(len(r.get("unmeasured") or [])),
    # 회선 수신 지연 초과분 (2026-08-05 2차, 고도화 1·4). 주로 **전제 지표**로 쓴다 —
    # 봉 확정 관련 수정들이 전부 "1분봉이 경계 뒤 얼마 안에 도착한다"를 전제하기 때문이다.
    "delivery_latency_p99_seconds": lambda r: _latency(r, "p99"),
    "delivery_latency_max_seconds": lambda r: _latency(r, "max"),
    # 변동성 축 관심 피처 중 **프로덕션 feature_set에 없어 측정조차 못 하는** 것의 수
    # (2026-08-05 2차, 고도화 5). `ev_tod_cos`·`ev_close_remain`이 그 상태다 — 2026-08-04
    # 관문이 상위로 지목했는데 아직 안 켜져 있다.
    #
    # **"존재하는가"를 재는 지표다**(`tick_rows`와 같은 계열). 재학습 후
    # `configs/instance.yaml`의 `feature_set`을 승격하면 0이 되어야 하고, 안 되면
    # **승격이 조용히 안 먹은 것**이다 — 이 프로젝트가 반복한 실패 형태(결선했다고 믿는데
    # 안 붙어 있음)를 이 자리에서 잡는다. 채점을 안 돌린 날은 None(판정 불가).
    "absent_watchlist_features": _absent_watchlist_features,
}


@dataclass(frozen=True)
class PendingVerification:
    """등록된 수정 하나 — 그 수정의 **결과**(metric)와 **전제**(premise_*)를 함께 채점한다.

    ## 왜 전제를 따로 적는가 (2026-08-05 2차, 고도화 4)

    2026-08-05에 일어난 일의 형태가 이렇다: 전날 넣은 P0-1(시계 동기)이 P0-2(합성기 3겹
    방어)의 **전제를 깼다**. 겹②는 "스케줄러가 거래소 경계를 지나 쏘면 그 버킷의 1분봉은
    다 와 있다"를 전제했는데, 시계를 맞추자 그 전제가 처음부터 거짓이었음이 드러났다
    (1분봉 발행 지연 중앙값 0.655초 > 스케줄러 위상 0.5초).

    **등록부는 이걸 볼 수 없었다.** 각 수정을 자기 지표로만 독립 채점하기 때문이다 —
    08-04 리포트에서 `horizon_findings`는 빈 배열이었고 `horizon-volume-identity`는 깨끗하게
    통과 중이었다. 통과하던 그 순간에 이미 전제가 무너져 있었다.

    그래서 수정마다 **"이 수정이 성립하려면 참이어야 하는 것"**을 지표로 함께 적는다. 결과가
    아직 나쁘지 않아도 전제가 깨지면 그 자체로 사람이 봐야 할 신호다 — 그게 곧 다음 사고의
    예고이기 때문이다.
    """

    id: str
    summary: str
    registered: date
    metric: str
    consecutive_days: int
    deadline: date | None = None
    max_value: float | None = None
    min_value: float | None = None
    # 이 수정이 성립하려면 참이어야 하는 조건. 지표 이름은 `metric`과 같은 등록부를 쓴다.
    premise_metric: str | None = None
    premise_max: float | None = None
    premise_min: float | None = None
    premise_summary: str = ""

    def satisfied_by(self, value: float) -> bool:
        if self.max_value is not None and value > self.max_value:
            return False
        if self.min_value is not None and value < self.min_value:
            return False
        return True

    def premise_holds(self, value: float) -> bool:
        if self.premise_max is not None and value > self.premise_max:
            return False
        if self.premise_min is not None and value < self.premise_min:
            return False
        return True

    def premise_text(self) -> str:
        if self.premise_metric is None:
            return ""
        if self.premise_max is not None and self.premise_min is not None:
            return f"{self.premise_min:g} ≤ {self.premise_metric} ≤ {self.premise_max:g}"
        if self.premise_max is not None:
            return f"{self.premise_metric} ≤ {self.premise_max:g}"
        if self.premise_min is not None:
            return f"{self.premise_metric} ≥ {self.premise_min:g}"
        return self.premise_metric

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
        return self.status in (
            VerificationStatus.RECURRED,
            VerificationStatus.OVERDUE,
            VerificationStatus.STALLED,
            VerificationStatus.PREMISE_BROKEN,
        )


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
        premise = entry.get("premise") or {}
        premise_metric = premise.get("metric")
        if premise:
            # 전제도 결과와 **같은 엄격도**로 검증한다 — 오타 난 전제를 조용히 건너뛰면
            # "전제를 감시 중"이라고 믿는 항목이 실제로는 아무것도 안 본다(이 모듈의 취지 그 자체).
            if premise_metric not in METRIC_EXTRACTORS:
                raise RegistryError(
                    f"{entry.get('id', '?')}: 알 수 없는 전제 지표 '{premise_metric}' — "
                    f"사용 가능: {sorted(METRIC_EXTRACTORS)}"
                )
            if premise.get("max") is None and premise.get("min") is None:
                raise RegistryError(
                    f"{entry.get('id', '?')}: 전제에 max/min 중 최소 하나는 있어야 한다"
                )
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
                premise_metric=premise_metric,
                premise_max=None if premise.get("max") is None else float(premise["max"]),
                premise_min=None if premise.get("min") is None else float(premise["min"]),
                premise_summary=str(premise.get("summary", "")),
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

        verdicts.append(
            _verdict_for(item, clean, violated_on, unjudged, today, _latest_premise(item, reports))
        )
    return verdicts


def _latest_premise(
    item: PendingVerification, reports: dict[date, dict[str, Any]]
) -> tuple[date, float] | None:
    """전제 지표의 **가장 최근 측정값** — 없으면 None(전제 미정의 또는 못 잼).

    가장 최근 것만 보는 이유: 전제는 "지금도 참인가"를 묻는 것이라 이력의 연속성이 필요
    없다. 결과 지표가 N거래일 연속을 요구하는 것과 성격이 다르다.
    """
    if item.premise_metric is None:
        return None
    extractor = METRIC_EXTRACTORS[item.premise_metric]
    for day in sorted(reports, reverse=True):
        value = extractor(reports[day])
        if value is not None:
            return day, value
    return None


def _verdict_for(
    item: PendingVerification,
    clean: int,
    violated_on: date | None,
    unjudged: list[date],
    today: date,
    premise: tuple[date, float] | None = None,
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

    # **재발 다음으로 이른 신호** (2026-08-05 2차, 고도화 4). 결과 지표는 아직 깨끗한데
    # 이 수정이 딛고 선 전제가 무너진 상태다 — 2026-08-04에 `horizon-volume-identity`가
    # 정확히 그랬다(통과 중이었고, 그 순간 이미 전제가 거짓이었다).
    if premise is not None and not item.premise_holds(premise[1]):
        measured_on, value = premise
        return VerificationVerdict(
            item.id,
            item.summary,
            VerificationStatus.PREMISE_BROKEN,
            clean,
            item.consecutive_days,
            f"결과는 아직 깨끗하지만 전제가 무너졌다 — {measured_on.isoformat()} 측정 "
            f"{item.premise_metric}={value:g} (요구: {item.premise_text()})"
            + (f" · {item.premise_summary}" if item.premise_summary else ""),
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
    # **판정 불가가 쌓이는 것도 사고다** (2026-08-05 고도화 2).
    #
    # 종전에는 못 잰 날을 그냥 건너뛰었다. 통과로도 위반으로도 안 세는 것 자체는 맞지만,
    # 그 결과 **매일 판정 불가인 항목이 영원히 "검증 대기"로 조용히 남는다**. 2026-08-04가
    # 정확히 그 상태였다: `Get-WinEvent`가 크래시 0건인 날에만 실패해서 `ui-crash-isolation`은
    # 며칠이 지나도 0/3이었고, 아무도 그게 "진행 중"이 아니라 "고장"이라는 걸 몰랐다.
    #
    # 연속으로 이만큼 못 재면 그 자체를 사람이 봐야 할 상태로 올린다.
    if len(unjudged) >= item.consecutive_days and clean == 0:
        return VerificationVerdict(
            item.id,
            item.summary,
            VerificationStatus.STALLED,
            clean,
            item.consecutive_days,
            f"{len(unjudged)}거래일 연속 판정 불가 — 계측이 고장 났을 수 있다 "
            f"(진행 중이 아니라 정체다) {note}",
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
        VerificationStatus.STALLED: "🚫",
        VerificationStatus.OVERDUE: "⚠",
        VerificationStatus.PREMISE_BROKEN: "🧨",
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
