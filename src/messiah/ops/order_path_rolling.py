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

    days_measured            창 안에서 이 축을 **잰** 날 수
    days_with_submission     그중 제출이 1건 이상 있던 날 수
    dates                    그 날짜들 (사람이 "언제였나"를 바로 본다)
    days_with_exit_evidence  제출일 중 **청산 증거가 있던** 날 수 (2026-09-14 G-65)
    exit_dates               그 날짜들

## 0과 못 잼을 섞지 않는다 (L18)

`sizer_funnel`이 `None`인 날은 **판단 사슬이 사이저 앞까지도 못 간 날**이고, 그것은
「제출 0건」이 아니라 「못 쟀다」다(`_sizer_funnel_axis` docstring). 그 날은 `days_measured`에
들어가지 않는다 — 0으로 세면 "주문 경로가 조용했다"가 되어, 정작 배선이 끊긴 날이 정상
관측치로 둔갑한다.

## 제출 옆에 **청산 확인**을 놓는다 (2026-09-14 G-65, 대응 1-3)

09-09·09-10·09-14 사흘에 진입 주문이 나갔는데 **그중 어느 날도 청산 흔적이 없었다.** 그
사실이 사흘 동안 아무 리포트에도 안 걸린 이유는 단순하다 — 제출을 세는 칸은 있었고
청산을 세는 칸은 없었다. 한쪽만 세면 「열린 것」과 「닫힌 것」의 차이가 리포트 위에서
사라진다.

지금 시스템에는 **선물 방향 포지션을 정리하는 경로가 아예 없다**(F-104 사람 결정 대기).
그래서 이 칸은 당분간 `0/3`처럼 찍힐 것이고, 그것이 정확히 이 칸이 말해야 할 사실이다 —
축이 먼저 서 있어야 F-104가 반입되는 날 그 칸이 저절로 올라가는 것으로 검증이 된다.

## 왜 `FillMatched`를 청산 증거로 안 쓰나

`FillMatched`는 **진입 체결에도 뜬다**. 그것을 증거로 세면 진입만 하고 끝난 날이
「청산 확인됨」으로 둔갑한다 — 이 축이 잡으려는 바로 그 구멍을 이 축이 스스로 덮는 꼴이다.
증거로 인정하는 것은 **포지션을 실제로 내보내는 경로가 남기는 태그뿐**이고, 지금 등록부에
그런 태그는 강제청산 둘(`KillSwitchLiquidating`·`CircuitBreakerLiquidating`)이다.
F-104가 EOD 청산 태그를 만들면 `_EXIT_EVIDENCE_TAGS`에 한 줄 더하는 것으로 끝난다.

## 이 칸은 `breaches`에 넣지 않는다 (R18)

`breaches`는 관측이 아니라 **판정**이다 — `scripts/daily_integrity_report.py`가 그 길이로
종료 코드를 가르고(비면 0, 있으면 1), `fix_verification`이 `len(breaches)`를 등록부 채점
지표로 읽는다. F-104 이전에는 이 칸이 제출일마다 100% 발동하는 상수라, 넣는 순간
장후 배치가 매 실주문일에 붉어지고 등록부 채점이 매일 깎인다 — 정보량 0인 늑대소년이다.
`publish_sla`·`intraday_trend`가 선 길 그대로, **새 축은 며칠 돌아 본 뒤 사람이 승격한다.**

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

#: 포지션을 **실제로 내보낸** 경로만 (2026-09-14 G-65). 진입에도 뜨는 태그는 넣지 않는다 —
#: 이유는 모듈 docstring "왜 `FillMatched`를 청산 증거로 안 쓰나". F-104가 EOD 청산 태그를
#: 만들면 여기에 한 줄 더하면 된다.
_EXIT_EVIDENCE_TAGS = ("KillSwitchLiquidating", "CircuitBreakerLiquidating")


@dataclass
class OrderPathRolling:
    window_days: int
    days_measured: int
    days_with_submission: int
    dates: tuple[date, ...]
    #: 제출일 중 청산 증거가 있던 날 (2026-09-14 G-65). 분모는 `days_with_submission`이다 —
    #: 제출이 없던 날에 청산이 없는 것은 당연하고, 세야 할 것은 **연 것을 닫았는가**다.
    exit_dates: tuple[date, ...] = ()

    @property
    def days_with_exit_evidence(self) -> int:
        return len(self.exit_dates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_days": self.window_days,
            "days_measured": self.days_measured,
            "days_with_submission": self.days_with_submission,
            "dates": [d.isoformat() for d in self.dates],
            "days_with_exit_evidence": self.days_with_exit_evidence,
            "exit_dates": [d.isoformat() for d in self.exit_dates],
        }

    def summary_line(self) -> str:
        if not self.days_measured:
            return f"주문 제출 발생일수: 최근 {self.window_days}거래일 중 잰 날이 없다 — 미측정"
        when = ", ".join(d.isoformat() for d in self.dates) if self.dates else "없음"
        return (
            f"주문 제출 발생일수: 잰 {self.days_measured}일 중 "
            f"{self.days_with_submission}일 (창 {self.window_days}거래일) — {when}"
            + _exit_clause(self.days_with_submission, self.days_with_exit_evidence)
        )


def _exit_clause(submission_days: int, exit_days: int) -> str:
    """「청산 확인」 꼬리표 (2026-09-14 G-65).

    제출일이 0이면 아무 말도 안 한다 — 열지도 않은 것을 닫았는지 묻는 칸은 소음이다.
    """
    if not submission_days:
        return ""
    tail = " — 청산 경로 미구현(F-104)" if not exit_days else ""
    return f" · 청산 확인 {exit_days}/{submission_days}일{tail}"


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


def _has_exit_evidence(report: Mapping[str, Any], tag_counts: Mapping[str, Any] | None) -> bool:
    """그날 청산 증거 태그가 한 건이라도 찍혔나 (2026-09-14 G-65).

    `tag_counts`를 직접 건네받으면 그쪽이 이긴다 — 오늘치는 파일에 아직 없다.
    태그가 하나도 없는 날은 **미확인**이지 「청산 0건」이 아니지만, 이 축은 분모를 제출일로
    잡아 두었으므로 둘의 구분이 필요 없다 — 제출한 날에 흔적이 없으면 못 닫은 것이다.
    """
    counts = tag_counts if tag_counts is not None else report.get("tag_counts")
    if not isinstance(counts, Mapping):
        return False
    return any(int(counts.get(tag) or 0) >= 1 for tag in _EXIT_EVIDENCE_TAGS)


def judge(
    *,
    day: date,
    today_submitted: int | None = None,
    today_tag_counts: Mapping[str, Any] | None = None,
    log_dir: Path | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    reports: Mapping[date, Mapping[str, Any]] | None = None,
) -> OrderPathRolling:
    """`day` 이하의 최근 `window_days`거래일에서 제출 발생일수를 센다.

    `today_submitted`·`today_tag_counts`는 **오늘 값을 직접 건네는 자리**다 — 오늘 리포트는
    지금 만드는 중이라 아직 파일에 없다(`feature_health_rolling.judge`의 `incomplete_known`과
    같은 사정).

    창은 **파일에 실제로 있는 날**로 센다 — 달력상 거래일이 아니라. 수집이 안 돈 날을 창에
    넣으면 그 자리가 영원히 「못 잼」으로 남아 창이 앞으로 나아가지 않는다.
    """
    if reports is None:
        from messiah.ops.fix_verification import load_daily_reports

        resolved = log_dir or Path("logs")
        reports = load_daily_reports(resolved)

    measured: list[tuple[date, int, bool]] = []
    for when in sorted((d for d in reports if d <= day), reverse=True):
        if when == day and today_submitted is not None:
            continue  # 오늘은 건네받은 값을 쓴다 — 파일본은 예비본일 수 있다
        value = _submitted(reports[when])
        if value is None:
            continue
        measured.append((when, value, _has_exit_evidence(reports[when], None)))

    if today_submitted is not None:
        today_report = reports.get(day) or {}
        measured.insert(
            0, (day, int(today_submitted), _has_exit_evidence(today_report, today_tag_counts))
        )

    window = measured[:window_days]
    hits = tuple(sorted(when for when, value, _exited in window if value >= 1))
    exited = tuple(sorted(when for when, value, exit_seen in window if value >= 1 and exit_seen))
    return OrderPathRolling(
        window_days=window_days,
        days_measured=len(window),
        days_with_submission=len(hits),
        dates=hits,
        exit_dates=exited,
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
    submission_days = int(axis.get("days_with_submission") or 0)
    return (
        f"주문 제출 발생일수: 잰 {measured}일 중 {submission_days}일 "
        f"(창 {window}거래일) — {when}"
        # 옛 리포트에는 이 필드가 없다 — 그런 날은 꼬리표를 안 단다(0으로 세지 않는다, L18).
        + (
            _exit_clause(submission_days, int(axis.get("days_with_exit_evidence") or 0))
            if "days_with_exit_evidence" in axis
            else ""
        )
    )
