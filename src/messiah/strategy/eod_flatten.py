"""장마감 강제청산 — **연 것을 그날 안에 닫는다** (2026-09-15 F-104, 대응 이상점 1-3).

## 무슨 일이 있었나

2026-09-09·09-10·09-14 사흘에 실제 진입 주문이 나갔는데 **그중 어느 날도 청산되지 않았다.**
`risk_engine.py`의 R6은 마감 10분 전부터 **신규 진입을 거부**하는 것까지만 구현돼 있었고
(`overnight_flatten_lead_minutes`), 이미 들고 있는 것을 내보내는 짝이 없었다. R9
(`positions_requiring_forced_liquidation()`)는 매도옵션 전용 **탐지** 함수라 선물 방향
포지션에는 닿지 않는다. `models/regime_direction.py` docstring이 그동안 "시스템에 청산
엔진이 아직 없다"고 스스로 적고 있었다.

기준: `Derivatives_AI_Master_Plan_Ver2.0.md` 체결전략 표 "장 마감 강제청산 — 마감 10분 전
개시" · `Derivatives_AI_Position_Holding_Policy_Ver1.0.md` §2.2 Type A "무포 오버나이트가
기본값".

## 왜 벽시계인가 — 봉에 매달면 안 된다

2026-09-14에 `g2_daily`의 5분 판단 루프가 14:00~14:50 구간에서 **45분간 다섯 틱을 통째로
건너뛰었다**(이상점 1-4, 원인 미확정). 청산을 봉 도착이나 판단 사이클에 걸면 그 침묵이
15:20~15:30에 오는 날 **청산이 통째로 안 나간다** — 그리고 그날은 침묵했다는 사실조차
모른 채 포지션이 밤을 넘긴다.

그래서 이 정책은 `minutes_to_close` 하나만 보고 판정하고, 호출자는 서킷브레이커 워치독과
같은 고정 틱(`core/scheduler.FixedTickScheduler`)에서 부른다. 데이터가 끊겨도 시계는 간다.

## 이 모듈이 판정만 하는 이유

주문 생성은 `KillSwitch.liquidate()`가 이미 하고 있다 — 순수 변환 함수이고, 서킷브레이커
해제 경로(`strategy/pipeline._liquidate_after_circuit_breaker`)가 KillSwitch를 트리거하지
않고 그대로 재사용하는 선례가 있다. 같은 일을 하는 코드를 두 곳에 두지 않는다.

제출도 여기서 하지 않는다. 이 모듈은 **"지금 청산해야 하는가, 무엇을"**만 답하고 I/O를
갖지 않는다 — 그래야 "마감 3분 전인데 안 쏜다" 같은 판정을 실제로 3분을 기다리지 않고
테스트할 수 있다(`risk/circuit_breaker_monitor.py`와 같은 순수 판정 스타일).

## 하루 한 번 (`already_done_for`)

고정 틱은 30초마다 돈다. 창(마감 10분 전) 안에서는 매 틱이 전부 "청산 대상"으로 판정되므로,
호출자가 **그날 이미 했는지**를 넘겨 재발행을 막는다. 날짜로 거는 이유는 프로세스가 장중에
재기동돼도 같은 날 두 번 쏘지 않기 위해서다.

재진입 경주는 걱정하지 않아도 된다 — 같은 창에서 R6이 신규 진입을 이미 전면 거부한다
(`risk_engine.py:194`). 청산 창과 진입 차단 창이 **같은 설정값**을 보는 것이 그 보장이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

from messiah.broker.base import BrokerPosition

#: 마감 몇 분 전부터 청산을 개시하는가. `RiskEngineConfig.overnight_flatten_lead_minutes`와
#: **같은 값이어야 한다** — 어긋나면 "진입은 아직 열려 있는데 청산이 이미 돌거나"(청산 창이
#: 더 넓을 때) "청산 전에 새로 들어간 것이 남는"(진입 창이 더 넓을 때) 구간이 생긴다.
#: 그래서 기본값을 여기 다시 쓰지 않고 호출자가 RiskEngine의 값을 그대로 건네게 한다.
DEFAULT_LEAD_MINUTES = 10.0


@dataclass(frozen=True)
class EodFlattenPlan:
    """이번 틱의 판정 결과.

    `should_flatten`이 False면 `positions`는 항상 비어 있다. `reason`은 두 경우 모두
    채운다 — **왜 안 했는지가 왜 했는지만큼 중요하다**(이 저장소가 반복해서 배운 것).
    """

    should_flatten: bool
    positions: tuple[BrokerPosition, ...]
    reason: str
    #: 창(마감 N분 전) 안에는 들어왔는가. **"창 밖이라 안 쐈다"와 "창 안인데 쏠 게 없었다"를
    #: 가르는 유일한 근거**다 — 호출자는 후자만 로그로 남긴다(전자는 하루 수백 틱이라
    #: 남기면 로그가 못 쓰게 된다). 침묵 하나가 두 사실을 뜻하게 두지 않는다.
    in_window: bool = False


def decide(
    *,
    minutes_to_close: float | None,
    positions: Sequence[BrokerPosition],
    today: date,
    already_done_for: date | None,
    lead_minutes: float = DEFAULT_LEAD_MINUTES,
) -> EodFlattenPlan:
    """지금 청산해야 하는가, 무엇을.

    입력:
        minutes_to_close  `EventCalendar.minutes_to_close()` 결과. **None이면 정규장이
                          아니다**(그 함수의 계약) — 장 밖에서는 아무것도 하지 않는다.
        positions         브로커가 답한 현재 보유. 수량 0인 줄은 여기서 걸러낸다.
        today             판정 기준일(KST 날짜). 호출자가 자기 시계로 정한다.
        already_done_for  그날 이미 청산을 쏜 날짜. 같으면 재발행하지 않는다.
        lead_minutes      마감 몇 분 전부터인가. R6과 같은 값을 받는다(모듈 상수 주석 참고).

    실패 조건: 없다 — 판정만 하므로 예외를 던지지 않는다. 호출자가 브로커 조회에
              실패하면 그건 호출자 쪽 이야기다.
    """
    if minutes_to_close is None:
        return EodFlattenPlan(False, (), "정규장이 아니다 — 청산 창 판정 안 함", in_window=False)

    if minutes_to_close > lead_minutes:
        return EodFlattenPlan(
            False,
            (),
            f"마감까지 {minutes_to_close:.1f}분 — 청산 창(≤{lead_minutes:.0f}분) 밖",
            in_window=False,
        )

    if already_done_for == today:
        return EodFlattenPlan(False, (), f"{today} 청산 이미 발행됨 — 재발행 안 함", in_window=True)

    held = tuple(p for p in positions if p.qty != 0)
    if not held:
        return EodFlattenPlan(
            False,
            (),
            f"마감까지 {minutes_to_close:.1f}분 — 청산 창 안이나 보유 포지션 0",
            in_window=True,
        )

    return EodFlattenPlan(
        True,
        held,
        f"마감까지 {minutes_to_close:.1f}분 (≤{lead_minutes:.0f}분) — "
        f"{len(held)}개 포지션 강제청산, Holding Policy §2.2 A",
        in_window=True,
    )
