"""주문 경로가 **정기적으로 열리는가, 그날만의 우연이었나** (2026-09-09 고도화).

## 무슨 일이 있었나

2026-08-24에 등록한 `order-path-live`(신호가 ④Risk→⑤Sizer→OrderGateway 전 구간을 관통하는가)가
20거래일 기한 중 **2026-09-09에 처음** 통과했다 — 15:00:01에 `DecisionEmitted`(TREND_UP,
점수 0.410) → `OrderPendingSet`(수량 1) → `OrderSubmit`(모의 `SIM00000001`).

그런데 그 통과가 **재현되는 성질인지 그날 시장의 우연인지 말할 자리가 없었다.** 그날의
`sizer_funnel.submitted`는 매일 찍히지만 「최근 N일 중 며칠에 제출이 있었나」를 세는 축이
없었고, `fix_verification`의 `orders_submitted`는 연속 충족일만 본다(하루라도 0이면 끊긴다).
하루 단위 값과 연속일 사이에 **발생 빈도**가 빠져 있었다.

## 이 모듈이 세는 것

    days_measured          창 안에서 이 축을 **잰** 날 수
    days_with_submission   그중 제출이 1건 이상 있던 날 수
    dates                  그 날짜들 (사람이 "언제였나"를 바로 본다)

## 0과 못 잼을 섞지 않는다 (L18)

`sizer_funnel`이 `None`인 날은 **판단 사슬이 사이저 앞까지도 못 간 날**이고, 그것은
「제출 0건」이 아니라 「못 쟀다」다(`_sizer_funnel_axis` docstring). 그 날은 `days_measured`에
들어가지 않는다 — 0으로 세면 "주문 경로가 조용했다"가 되어, 정작 배선이 끊긴 날이 정상
관측치로 둔갑한다.

## 불완전일을 창에서 빼지 않는 이유

`feature_health_rolling`은 반나절짜리 날을 창에서 뺀다 — 그쪽은 **표본 품질**로 판정하는
축이라 절반짜리 하루가 임계를 오염시킨다. 이 축은 「제출이 있었나 없었나」의 사실 관측이고,
반나절이었어도 제출이 있었다면 그것은 일어난 일이다. 대신 창을 세는 근거를 그대로 실어
(`dates`) 사람이 직접 볼 수 있게 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

#: 5거래일 — 2026-09-09 고도화가 제안한 관찰 창. "다음 5거래일 동안 발생 빈도를 추적한다".
DEFAULT_WINDOW_DAYS = 5


@dataclass
class OrderPathRolling:
    window_days: int
    days_measured: int
    days_with_submission: int
    dates: tuple[date, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_days": self.window_days,
            "days_measured": self.days_measured,
            "days_with_submission": self.days_with_submission,
            "dates": [d.isoformat() for d in self.dates],
        }

    def summary_line(self) -> str:
        if not self.days_measured:
            return f"주문 제출 발생일수: 최근 {self.window_days}거래일 중 잰 날이 없다 — 미측정"
        when = ", ".join(d.isoformat() for d in self.dates) if self.dates else "없음"
        return (
            f"주문 제출 발생일수: 잰 {self.days_measured}일 중 "
            f"{self.days_with_submission}일 (창 {self.window_days}거래일) — {when}"
        )


def _submitted(report: Mapping[str, Any]) -> int | None:
    """그날 `sizer_funnel.submitted`. 축 자체가 없으면 `None`(못 잼)이다."""
    axis = report.get("sizer_funnel")
    if not isinstance(axis, Mapping):
        return None
    value = axis.get("submitted")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def judge(
    *,
    day: date,
    today_submitted: int | None = None,
    log_dir: Path | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    reports: Mapping[date, Mapping[str, Any]] | None = None,
) -> OrderPathRolling:
    """`day` 이하의 최근 `window_days`거래일에서 제출 발생일수를 센다.

    `today_submitted`는 **오늘 값을 직접 건네는 자리**다 — 오늘 리포트는 지금 만드는 중이라
    아직 파일에 없다(`feature_health_rolling.judge`의 `incomplete_known`과 같은 사정).

    창은 **파일에 실제로 있는 날**로 센다 — 달력상 거래일이 아니라. 수집이 안 돈 날을 창에
    넣으면 그 자리가 영원히 「못 잼」으로 남아 창이 앞으로 나아가지 않는다.
    """
    if reports is None:
        from messiah.ops.fix_verification import load_daily_reports

        resolved = log_dir or Path("logs")
        reports = load_daily_reports(resolved)

    measured: list[tuple[date, int]] = []
    for when in sorted((d for d in reports if d <= day), reverse=True):
        if when == day and today_submitted is not None:
            continue  # 오늘은 건네받은 값을 쓴다 — 파일본은 예비본일 수 있다
        value = _submitted(reports[when])
        if value is None:
            continue
        measured.append((when, value))

    if today_submitted is not None:
        measured.insert(0, (day, int(today_submitted)))

    window = measured[:window_days]
    hits = tuple(sorted(when for when, value in window if value >= 1))
    return OrderPathRolling(
        window_days=window_days,
        days_measured=len(window),
        days_with_submission=len(hits),
        dates=hits,
    )


def summary_line(axis: Mapping[str, Any] | None) -> str | None:
    """리포트 dict의 `order_path_window` 블록을 사람이 읽는 한 줄로.

    데이터클래스를 되짜지 않는다 — 리포트는 이미 dict를 들고 있고, 렌더가 그것을 그대로
    읽는 것이 이 축의 유일한 소비처다.
    """
    if not axis:
        return None
    measured = int(axis.get("days_measured") or 0)
    window = int(axis.get("window_days") or 0)
    if not measured:
        return f"주문 제출 발생일수: 최근 {window}거래일 중 잰 날이 없다 — 미측정"
    dates = list(axis.get("dates") or [])
    when = ", ".join(dates) if dates else "없음"
    return (
        f"주문 제출 발생일수: 잰 {measured}일 중 {int(axis.get('days_with_submission') or 0)}일 "
        f"(창 {window}거래일) — {when}"
    )
