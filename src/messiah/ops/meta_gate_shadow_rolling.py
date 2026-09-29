"""섀도 메타게이트 통과·차단을 **20거래일 창으로** 모은다 (2026-09-29 G-8).

## 무슨 일이 있었나

2026-08-31 S-2가 무결성 리포트 `meta_gate`에 섀도 축(`shadow_measured`·`shadow_passes`·
`shadow_blocks`·`blocked_by_regime`)을 세웠다. 그런데 그 값은 **그날 파일에만** 있었다 —
R18(게이트 신설·임계 변경은 섀도 20거래일 후 승격)이 요구하는 20일치 판단을 하려면 사람이
`daily_integrity_*.json` 스무 개를 일일이 열어야 했다. 2026-09-29 장후 점검이 이것을 짚었다.

## 이 모듈이 세는 것

    days_measured        창 안에서 섀도 축을 **잰** 날 수
    window_complete      days_measured가 창(20거래일)을 채웠나 — R18 판단 가능 여부
    shadow_measured      창 합계 판단 수
    shadow_passes        그중 섀도 임계를 통과했을 판단 수
    shadow_pass_rate     passes / measured (창 합계 기준 — 날짜 평균이 아니다)
    blocked_by_regime    창 합계 국면별 섀도 차단
    days                 날짜별 원본 값 (사람이 "어느 날이 튀었나"를 바로 본다)

## 누적 파일에 append하지 않고 **매번 일별 리포트에서 다시 계산한다**

제안 원문은 "매 장후 append"였다. 그렇게 하면 이 파일이 생기기 전 19거래일(09-01부터)이
영영 빠지고, 장후 배치가 두 번 돌면 같은 날이 두 번 들어간다. 원천은 이미
`daily_integrity_*.json`에 날짜별로 있으므로 그것을 다시 읽는 편이 소급·멱등 둘 다 얻는다.
파일은 **캐시이자 사람용 요약**이지 원장이 아니다.

## 0과 못 잼을 섞지 않는다 (L18)

`shadow_measured`가 `None`인 날(S-2 이전, 또는 메타게이트 평가가 0건인 날)은 창에 넣지
않는다 — 0으로 세면 통과율 분모가 부풀지 않은 채로 창만 채워져 「20일 찼다」가 거짓이 된다.

## 이 값은 어떤 판정에도 쓰이지 않는다 (R18)

`breaches`에도 등록부 채점에도 들어가지 않는다. 승격은 이 파일을 읽은 **사람**이 한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Mapping

DEFAULT_PATH = Path("logs") / "meta_gate_shadow_rolling.json"

#: R18 — 섀도 계측 20거래일 후 승격.
DEFAULT_WINDOW_DAYS = 20


@dataclass
class ShadowDay:
    day: date
    measured: int
    passes: int
    blocked_by_regime: dict[str, int] = field(default_factory=dict)

    @property
    def blocks(self) -> int:
        return self.measured - self.passes

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.day.isoformat(),
            "shadow_measured": self.measured,
            "shadow_passes": self.passes,
            "shadow_blocks": self.blocks,
            "blocked_by_regime": dict(sorted(self.blocked_by_regime.items())),
        }


@dataclass
class ShadowRolling:
    window_days: int
    days: tuple[ShadowDay, ...]  # 오래된 날 → 최근 날

    @property
    def days_measured(self) -> int:
        return len(self.days)

    @property
    def window_complete(self) -> bool:
        return self.days_measured >= self.window_days

    @property
    def shadow_measured(self) -> int:
        return sum(d.measured for d in self.days)

    @property
    def shadow_passes(self) -> int:
        return sum(d.passes for d in self.days)

    @property
    def shadow_pass_rate(self) -> float | None:
        # 분모 0은 「통과율 0」이 아니라 「못 잼」이다 (L18).
        if not self.shadow_measured:
            return None
        return round(self.shadow_passes / self.shadow_measured, 4)

    @property
    def blocked_by_regime(self) -> dict[str, int]:
        total: dict[str, int] = {}
        for d in self.days:
            for regime, count in d.blocked_by_regime.items():
                total[regime] = total.get(regime, 0) + count
        return dict(sorted(total.items()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_days": self.window_days,
            "days_measured": self.days_measured,
            "window_complete": self.window_complete,
            "shadow_measured": self.shadow_measured,
            "shadow_passes": self.shadow_passes,
            "shadow_blocks": self.shadow_measured - self.shadow_passes,
            "shadow_pass_rate": self.shadow_pass_rate,
            "blocked_by_regime": self.blocked_by_regime,
            "days": [d.to_dict() for d in self.days],
        }

    def summary_line(self) -> str:
        if not self.days_measured:
            return f"섀도 메타게이트: 최근 {self.window_days}거래일 중 잰 날이 없다 — 미측정"
        rate = self.shadow_pass_rate
        rate_text = "미측정" if rate is None else f"{rate:.1%}"
        state = (
            "창 충족 — R18 판단 가능"
            if self.window_complete
            else f"창 미충족 {self.days_measured}/{self.window_days}일"
        )
        return (
            f"섀도 메타게이트: 잰 {self.days_measured}일 · 판단 {self.shadow_measured}건 중 "
            f"통과 {self.shadow_passes}건({rate_text}) · {state}"
        )


def _shadow_day(when: date, report: Mapping[str, Any]) -> ShadowDay | None:
    """그날 `meta_gate`의 섀도 축. 축이 없거나 `shadow_measured`가 비면 `None`(못 잼)."""
    axis = report.get("meta_gate")
    if not isinstance(axis, Mapping):
        return None
    try:
        measured = int(axis.get("shadow_measured") or 0)
        passes = int(axis.get("shadow_passes") or 0)
    except (TypeError, ValueError):
        return None
    if measured < 1:
        return None
    raw = axis.get("blocked_by_regime")
    regimes: dict[str, int] = {}
    if isinstance(raw, Mapping):
        for regime, count in raw.items():
            try:
                regimes[str(regime)] = int(count)
            except (TypeError, ValueError):
                continue
    return ShadowDay(day=when, measured=measured, passes=passes, blocked_by_regime=regimes)


def judge(
    *,
    day: date,
    log_dir: Path | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    reports: Mapping[date, Mapping[str, Any]] | None = None,
) -> ShadowRolling:
    """`day` 이하에서 섀도 축을 잰 최근 `window_days`일을 모은다.

    창은 **잰 날**로 센다 — 달력 거래일이 아니라(`order_path_rolling.judge`와 같은 이유).
    예비본은 `load_daily_reports`가 이미 거른다.
    """
    if reports is None:
        from messiah.ops.fix_verification import load_daily_reports

        reports = load_daily_reports(log_dir or Path("logs"))

    measured: list[ShadowDay] = []
    for when in sorted((d for d in reports if d <= day), reverse=True):
        row = _shadow_day(when, reports[when])
        if row is not None:
            measured.append(row)
        if len(measured) >= window_days:
            break
    return ShadowRolling(window_days=window_days, days=tuple(reversed(measured)))


def write(result: ShadowRolling, *, day: date, path: Path = DEFAULT_PATH) -> Path | None:
    """요약 파일을 쓴다. 실패해도 예외를 올리지 않는다 — 리포트 종료 코드를 흔들면 안 된다."""
    body = {"as_of": day.isoformat(), **result.to_dict()}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except OSError:
        return None
