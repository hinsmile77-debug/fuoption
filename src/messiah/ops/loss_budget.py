"""소급 불가 손실의 **이동 예산** — 하루짜리 사고를 누적으로 본다 (2026-08-10 G-6).

## 왜 만들었나

이 저장소는 소급 경로가 없는 데이터를 반복해서 잃었다:

    2026-08-06   21분   호스트 재부팅, 사람이 손으로 띄울 때까지
    2026-08-07  114분   UI 스모크 테스트가 운영 버스에 sys.kill을 발행
    2026-08-10   38분   정시 트리거가 기동 창 가드에 막혔다

세 번 다 그날의 리포트는 그 사고를 (결국) 말했고, 세 번 다 처방이 들어갔다. 그런데
**"3주에 173분을 잃었다"** 고 말한 축은 하나도 없었다. 매번 "이번 한 번"으로 읽혔고,
그래서 매번 그날의 개별 원인만 고쳤다.

개별 원인을 고치는 것은 옳다. 다만 그것만 하면 **원인이 매번 다른 한, 손실은 계속 난다** —
축이 없으면 "얼마나 자주, 얼마나 크게 잃고 있나"를 아무도 못 묻기 때문이다.

## 왜 합이 아니라 이동합인가

누적 총합은 시간이 갈수록 커지기만 해서 어느 순간 아무도 안 본다. 5거래일 이동합은
**"최근 한 주에 얼마를 잃었나"**를 묻고, 좋아지면 실제로 내려간다 — 즉 고쳐진 것이
숫자로 보인다.

5거래일인 이유는 `archiver-restart-restore`·`schedule-single-source`가 5거래일을 쓰는 것과
같다: 장중 재기동이나 사람의 손편집처럼 **매일 나지 않는 사건**을 보려면 한 주가 필요하다.

## 임계는 왜 20분인가

하루치 임계(`_HEAD_GAP_FLOOR_MINUTES`·`bar_tail_gap_minutes`·`MISSING_MINUTES_LIMIT`)와
**같은 값**을 5거래일 창에 쓴다. 즉 "일주일에 사고 하루치를 넘게 잃으면 본다"는 뜻이다.
정상 주간은 0분이어야 한다 — 실제로 2026-08-04·08-05는 0이었다.

**이 임계는 미검증 초기값이다.** 08-06~08-10 실측이 173분이라 첫 주는 무조건 넘는다.
그게 맞다 — 지금 상태가 정상이 아니라는 것이 이 축의 첫 메시지다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DEFAULT_LOG_DIR = Path("logs")

# 이동 창의 길이(거래일). 위 docstring "왜 이동합인가" 참고.
WINDOW_TRADING_DAYS = 5

# 창 안의 합이 이만큼을 넘으면 판정 — 하루치 임계와 같은 크기다(미검증 초기값).
BUDGET_MINUTES = 20.0


@dataclass(frozen=True)
class LossBudget:
    """최근 `WINDOW_TRADING_DAYS`거래일의 소급 불가 손실."""

    days: list[tuple[date, float]]
    measured_days: int
    missing_days: int

    @property
    def total_minutes(self) -> float:
        return round(sum(minutes for _, minutes in self.days), 1)

    @property
    def over_budget(self) -> bool:
        return self.total_minutes > BUDGET_MINUTES

    def describe(self) -> str:
        if not self.days:
            return "소급 불가 손실 예산: 판정 불가 — 이 축이 있는 리포트가 아직 없다"
        worst = max(self.days, key=lambda item: item[1])
        detail = f"최근 {self.measured_days}거래일 합 {self.total_minutes:.0f}분"
        if worst[1] > 0:
            detail += f" (최대 {worst[0].isoformat()} {worst[1]:.0f}분)"
        # 못 잰 날을 **숨기지 않는다** — 창의 절반이 비어 있는데 "합 0분"이라고 말하면
        # 그건 좋은 소식이 아니라 계측 고장이다(L18).
        if self.missing_days:
            detail += f" · 이 축이 없는 날 {self.missing_days}일"
        return f"소급 불가 손실 예산: {detail}"

    def finding(self) -> list[str]:
        if not self.over_budget:
            return []
        return [
            f"소급 불가 손실이 {self.measured_days}거래일에 {self.total_minutes:.0f}분 "
            f"(> 예산 {BUDGET_MINUTES:.0f}분) — 원인은 날마다 달랐지만 잃은 것은 같은 종류다"
        ]


def load_daily_losses(log_dir: Path = DEFAULT_LOG_DIR) -> dict[date, float | None]:
    """`logs/daily_integrity_*.json` → {날짜: 손실 분}. 축이 없는 옛 리포트는 None.

    `ops/fix_verification.load_daily_reports()`와 같은 파일을 읽지만 필요한 한 필드만
    꺼낸다 — 이 함수는 장후 절차 끝에서 돌고, 리포트 전체를 메모리에 올릴 이유가 없다.
    """
    out: dict[date, float | None] = {}
    for path in sorted(log_dir.glob("daily_integrity_*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            day = date.fromisoformat(report["date"])
        except (OSError, ValueError, KeyError):
            continue  # 깨진 파일 하나가 나머지 집계를 막지 않는다
        value = report.get("irrecoverable_loss_minutes")
        out[day] = float(value) if isinstance(value, (int, float)) else None
    return out


def summarize(log_dir: Path = DEFAULT_LOG_DIR, *, window: int = WINDOW_TRADING_DAYS) -> LossBudget:
    """최근 `window`거래일의 예산 — 리포트가 있는 날만 거래일로 센다.

    달력이 아니라 리포트가 있는 날을 세는 이유는 `fix_verification._trading_days_since()`와
    같다: 주말·휴장을 세면 창이 실제로는 사흘치가 된다.
    """
    losses = load_daily_losses(log_dir)
    recent = sorted(losses)[-window:]
    measured = [(day, losses[day]) for day in recent if losses[day] is not None]
    return LossBudget(
        days=[(day, value) for day, value in measured if value is not None],
        measured_days=len(measured),
        missing_days=len(recent) - len(measured),
    )
