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

## 분할 시장가 — 한 번에 다 던지지 않는다 (2026-09-15, 사용자 결정)

Master Plan 체결전략 표는 "분할 시장가"라고 적는다. 전량을 한 장에 시장가로 던지면 호가를
훑고 내려가 체결가가 나빠진다 — 지금은 1~2계약이라 차이가 없지만, 수량이 커지는 날
갑자기 손해가 커지는 형태이고 그때 고치면 이미 늦다.

그래서 **창 안에서 30초 틱마다 `slice_contracts`씩** 내보낸다. 남은 수량은 매 틱 브로커
포지션을 다시 조회해 계산하므로, 체결이 되는 대로 자연히 줄어든다.

## 그리고 반드시 **끝을 정한다** — 마무리 쓸어담기

분할만 하고 끝을 안 정하면 마감까지 다 못 빠져나가는 날이 생긴다. 그건 이 기능이 고치려던
바로 그 문제(포지션이 밤을 넘긴다)로 되돌아가는 것이다. 그래서 마감 `final_sweep_minutes`
(기본 2분) 전부터는 **남은 전량을 한 번에** 내보낸다 — 분할의 이익보다 미청산의 손해가
훨씬 크다는 판단이고, 그 판단을 코드가 스스로 말하게 둔다(`final_sweep` 플래그).

## 체결 지연과 이중 발행 — 세는 것은 **미체결 잔량**이다 (`in_flight`)

시장가라도 브로커 응답과 포지션 갱신 사이에는 지연이 있다. 그 사이 다음 틱이 돌면 **이미
내보낸 수량을 또 내보낸다**. 그래서 호출자가 심볼별로 「보냈는데 아직 포지션에 안 잡힌
수량」을 건네고, 이 함수가 `남은 = |포지션| − 미체결`로 계산한다.

**「그날 보낸 누계」를 세면 안 된다.** 체결이 잡히는 순간 포지션이 이미 줄어드는데 누계까지
빼면 같은 수량을 두 번 차감해, 분할이 도중에 멈추고 포지션이 남는다 — 2026-09-15 구현 중
회귀 테스트가 실제로 이 형태로 잡아냈다(5계약 중 3계약만 나가고 2계약이 남았다). 누계는
시간이 지나도 안 줄지만 미체결 잔량은 체결분만큼 줄어든다는 것이 차이의 전부다.

잔량을 줄이는 일(체결 관측)은 호출자 몫이다 — 이 함수는 순수하게 남았다.
**접수된 것만 잔량에 올린다** — 게이트웨이가 거부한 주문(`submit()`이 None)은 올리지 않으므로
다음 틱이 자동으로 재시도한다. 거부를 "보냈다"로 세면 그 수량이 영영 안 나간다.

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from messiah.broker.base import BrokerPosition

#: 마감 몇 분 전부터 청산을 개시하는가. `RiskEngineConfig.overnight_flatten_lead_minutes`와
#: **같은 값이어야 한다** — 어긋나면 "진입은 아직 열려 있는데 청산이 이미 돌거나"(청산 창이
#: 더 넓을 때) "청산 전에 새로 들어간 것이 남는"(진입 창이 더 넓을 때) 구간이 생긴다.
#: 그래서 기본값을 여기 다시 쓰지 않고 호출자가 RiskEngine의 값을 그대로 건네게 한다.
DEFAULT_LEAD_MINUTES = 10.0


#: 한 틱에 내보내는 최대 계약 수. 보수적으로 1로 둔다 — 지금 실계약 규모가 1~2라 이 값이면
#: 완전 분할이고, 커지더라도 아래 마무리 쓸어담기가 꼬리 위험을 막는다.
DEFAULT_SLICE_CONTRACTS = 1

#: 마감 몇 분 전부터 남은 전량을 한 번에 쓸어담는가. 분할의 이익보다 미청산의 손해가 크다.
DEFAULT_FINAL_SWEEP_MINUTES = 2.0


@dataclass(frozen=True)
class FlattenSlice:
    """이번 틱에 한 심볼에서 내보낼 몫."""

    #: 원 포지션 — 방향(반대매매)은 `KillSwitch.liquidate()`가 이 부호에서 정한다.
    position: BrokerPosition
    #: 이번에 내보낼 수량(양수). `abs(position.qty)`보다 작을 수 있다 — 그게 분할이다.
    qty: int


@dataclass(frozen=True)
class EodFlattenPlan:
    """이번 틱의 판정 결과.

    `should_flatten`이 False면 `slices`는 항상 비어 있다. `reason`은 두 경우 모두
    채운다 — **왜 안 했는지가 왜 했는지만큼 중요하다**(이 저장소가 반복해서 배운 것).
    """

    slices: tuple[FlattenSlice, ...]
    reason: str
    #: 창(마감 N분 전) 안에는 들어왔는가. **"창 밖이라 안 쐈다"와 "창 안인데 쏠 게 없었다"를
    #: 가르는 유일한 근거**다 — 호출자는 후자만 로그로 남긴다(전자는 하루 수백 틱이라
    #: 남기면 로그가 못 쓰게 된다). 침묵 하나가 두 사실을 뜻하게 두지 않는다.
    in_window: bool = False
    #: 이번이 마무리 쓸어담기인가 — 분할을 그만두고 남은 전량을 내보내는 구간.
    final_sweep: bool = False

    @property
    def should_flatten(self) -> bool:
        return bool(self.slices)


def decide(
    *,
    minutes_to_close: float | None,
    positions: Sequence[BrokerPosition],
    in_flight: Mapping[str, int] | None = None,
    lead_minutes: float = DEFAULT_LEAD_MINUTES,
    slice_contracts: int = DEFAULT_SLICE_CONTRACTS,
    final_sweep_minutes: float = DEFAULT_FINAL_SWEEP_MINUTES,
) -> EodFlattenPlan:
    """지금 무엇을 얼마나 내보내야 하는가.

    입력:
        minutes_to_close    `EventCalendar.minutes_to_close()` 결과. **None이면 정규장이
                            아니다**(그 함수의 계약) — 장 밖에서는 아무것도 하지 않는다.
        positions           브로커가 답한 **현재** 보유. 매 틱 다시 조회한 값이어야 한다 —
                            체결된 만큼 저절로 줄어드는 것이 이 설계의 자기교정 장치다.
        in_flight           심볼별 **미체결 잔량**(보냈는데 아직 포지션에 안 잡힌 수량).
                            누계가 아니다 — 모듈 docstring "체결 지연과 이중 발행" 참고.
        lead_minutes        마감 몇 분 전부터인가. R6과 같은 값을 받는다.
        slice_contracts     한 틱 최대 계약 수. 1 미만이면 1로 올린다 — 0이면 영원히 안 나간다.
        final_sweep_minutes 이 안쪽이면 분할을 그만두고 남은 전량을 내보낸다.

    실패 조건: 없다 — 판정만 하므로 예외를 던지지 않는다. 호출자가 브로커 조회에
              실패하면 그건 호출자 쪽 이야기다.
    """
    if minutes_to_close is None:
        return EodFlattenPlan((), "정규장이 아니다 — 청산 창 판정 안 함")

    if minutes_to_close > lead_minutes:
        return EodFlattenPlan(
            (),
            f"마감까지 {minutes_to_close:.1f}분 — 청산 창(≤{lead_minutes:.0f}분) 밖",
        )

    outstanding = in_flight or {}
    final_sweep = minutes_to_close <= final_sweep_minutes
    # 0이나 음수를 그대로 쓰면 슬라이스가 0이 되어 **영원히 안 나간다** — 설정 실수가
    # 미청산으로 이어지는 경로를 막는다.
    step = max(1, int(slice_contracts))

    slices: list[FlattenSlice] = []
    for position in positions:
        if position.qty == 0:
            continue
        remaining = abs(position.qty) - int(outstanding.get(position.symbol, 0))
        if remaining <= 0:
            continue  # 보낸 것이 아직 안 잡혔을 뿐이다 — 또 보내면 반대 포지션이 생긴다
        take = remaining if final_sweep else min(remaining, step)
        slices.append(FlattenSlice(position=position, qty=take))

    if not slices:
        return EodFlattenPlan(
            (),
            f"마감까지 {minutes_to_close:.1f}분 — 청산 창 안이나 내보낼 수량 0",
            in_window=True,
            final_sweep=final_sweep,
        )

    total = sum(s.qty for s in slices)
    how = (
        f"마무리 쓸어담기(≤{final_sweep_minutes:.0f}분) 남은 전량"
        if final_sweep
        else f"분할 시장가 {step}계약씩"
    )
    return EodFlattenPlan(
        tuple(slices),
        f"마감까지 {minutes_to_close:.1f}분 (≤{lead_minutes:.0f}분) — {how}, "
        f"{len(slices)}개 심볼 {total}계약, Holding Policy §2.2 A",
        in_window=True,
        final_sweep=final_sweep,
    )
