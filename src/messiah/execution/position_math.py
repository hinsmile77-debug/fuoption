"""체결 한 건이 포지션과 실현손익에 어떻게 반영되는가 — **한 곳에만 있는 계산** (2026-09-17 F-114).

이 모듈이 따로 생긴 이유는 같은 계산이 두 곳에서 필요해졌기 때문이다:

- `broker/simulator/adapter.py`의 `SimBroker._apply()` — 브로커가 **자기** 장부를 갱신한다.
- `execution/position_reconciler.py` — 게이트웨이가 본 `Fill`만으로 **독립 장부**를 세운다.

두 장부가 같은 규칙으로 서지 않으면 대사(reconcile)가 무의미하다 — 불일치가 "체결이
새거나 겹쳤다"(잡으려는 것)인지 "두 구현의 평균단가 규칙이 달랐다"(잡을 필요 없는 것)인지
가릴 수 없기 때문이다. 규칙을 하나로 두면 불일치는 **오직 사건 자체의 불일치**를 뜻한다.

## 네 갈래 (2026-08-23 `SimBroker._apply()`에서 옮겨온 규칙, 판정 불변)

기존 수량 `q0`, 이번 체결의 부호 있는 수량 `s`일 때:

- `q0 == 0` — 신규 진입. 평균단가 = 체결가.
- `sign(s) == sign(q0)` — 물타기. 평균단가 = 수량가중평균. 실현 없음.
- `sign(s) != sign(q0)` 이고 `|s| <= |q0|` — 부분/전량 청산. 닫힌 `|s|`계약만큼 실현하고
  평균단가는 유지한다.
- `sign(s) != sign(q0)` 이고 `|s| > |q0|` — 전량 청산 후 뒤집기. `|q0|`만큼 실현하고, 남는
  수량의 평균단가는 이번 체결가다.

실현손익 = `닫은수량 × (청산가 − 진입가) × sign(기존포지션)`. LONG은 오를 때 이익이고
SHORT은 그 반대라 부호 곱이 필요하다.

## 단위는 **틱**이다

원으로 환산하려면 계약 승수(원/지수포인트)가 필요한데 그 값이 이 저장소 어디에도 없다
(`broker/simulator/adapter.py` 모듈 docstring "왜 원이 아니라 틱인가"). 없는 상수를 코드가
지어내는 것이 R4가 금지하는 바로 그것이다 — 그래서 이 모듈은 틱만 돌려주고, 원 환산은
사람이 `configs/instance.yaml`에 승수를 적는 날까지 **아예 하지 않는다.**
"""

from __future__ import annotations

from dataclasses import dataclass

from messiah.core.messages import OrderKind, Side

#: 체결가 기준 포지션을 줄이거나 닫는 주문 종류 — 부호를 기존 포지션에서 역산한다.
EXIT_KINDS = frozenset({OrderKind.EXIT_FULL, OrderKind.EXIT_PARTIAL})


@dataclass(frozen=True)
class PositionState:
    """한 심볼의 장부 한 줄. `broker/base.BrokerPosition`과 같은 뜻이지만 브로커 응답이
    아니라 **계산 중간값**이라 따로 둔다(그쪽은 진실원천의 스냅샷이다)."""

    qty: int  # 부호 있음: +Long / -Short
    avg_price_ticks: int


def signed_fill_qty(*, side: Side, kind: OrderKind, qty: int, current_qty: int) -> int:
    """`Fill`이 포지션을 얼마나 움직이는가 — 부호 있는 수량.

    `Fill` 메시지 자체에는 방향이 없다(`core/messages.Fill`: symbol·qty·price_ticks뿐).
    방향은 그 체결을 만든 `OrderRequest`에만 있으므로 이 함수는 둘을 같이 받는다 —
    `OrderGateway.on_fill()`이 pending 매칭으로 그 요청을 이미 손에 쥐고 있다는 것이
    `execution/position_reconciler.py`가 게이트웨이에 붙는 이유다.

    `EXIT_FULL`은 **기존 포지션 전량의 반대**다 — 주문서의 `side`가 아니라 장부가 방향을
    정한다(`SimBroker._apply()`가 2026-08-23부터 쓰던 규칙 그대로).
    """
    if kind is OrderKind.EXIT_FULL and current_qty != 0:
        return -current_qty
    return qty if side == Side.LONG else -qty


def closes_position(*, current_qty: int, signed_qty: int) -> bool:
    """이 체결이 **무언가를 닫는가** (2026-09-22 F-120).

    `apply_fill()`의 분기 조건 그 자체다 — 여기 한 줄로 두고 양쪽이 같이 쓴다. 따로
    적어 두면 조용히 어긋나고, 그때 「닫혔는데 안 센 체결」이 생긴다.

    이 술어가 따로 필요해진 이유: `apply_fill()`이 돌려주는 실현손익 `0.0`은 **"닫은 게
    없다"와 "본전에 닫았다" 둘 다**를 뜻한다. R10(연속손실 3회)은 그 둘을 반드시 갈라야
    한다 — 본전 청산은 스트릭을 끊고, 미청산은 아무 일도 아니다.
    """
    return current_qty != 0 and (current_qty > 0) != (signed_qty > 0)


def apply_fill(
    current: PositionState | None, *, signed_qty: int, price_ticks: int
) -> tuple[PositionState, float]:
    """체결 한 건을 장부에 반영하고 **닫힌 만큼의 실현손익(틱)** 을 함께 돌려준다.

    입력: 반영 전 장부(`None` = 무포지션), 부호 있는 체결 수량, 체결가(틱).
    반환: (반영 후 장부, 이번 체결로 실현된 손익 틱). 실현이 없으면 0.0이다 — 여기서는
         0.0이 "못 쟀다"가 아니라 "닫은 게 없다"라서 None을 쓰지 않는다(L18은 모르는 것을
         0으로 쓰지 말라는 규율이지, 아는 0을 None으로 쓰라는 규율이 아니다).
    """
    q0 = current.qty if current else 0
    entry = current.avg_price_ticks if current else price_ticks
    new_qty = q0 + signed_qty
    realized = 0.0

    if not closes_position(current_qty=q0, signed_qty=signed_qty):
        # 신규 진입 또는 같은 방향 물타기 — 실현 없음, 평균단가만 갱신.
        if q0 == 0:
            avg = price_ticks
        else:
            avg = round(
                (abs(q0) * entry + abs(signed_qty) * price_ticks) / (abs(q0) + abs(signed_qty))
            )
    else:
        closed = min(abs(signed_qty), abs(q0))
        direction = 1 if q0 > 0 else -1
        realized = closed * (price_ticks - entry) * direction
        # 뒤집혔으면 남은 수량은 이번 체결가가 진입가다. 아니면 원래 진입가를 지킨다.
        avg = price_ticks if (new_qty != 0 and (new_qty > 0) != (q0 > 0)) else entry

    return PositionState(qty=new_qty, avg_price_ticks=avg), float(realized)
