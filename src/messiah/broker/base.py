"""BrokerAdapter 추상 인터페이스 — Ver 1.1 §5-2, SYSTEM.md §2.

원칙:
- KIS(주) · LS(부) · Digital Twin(simulator)이 전부 이 인터페이스를 구현한다.
  상위 레이어는 자신이 모의인지 실전인지 모른다 (설정 mode 한 줄 전환).
- "구현됨 ≠ 검증됨": 어댑터 기능은 docs/capability_matrix.md에 실측 기록 후 사용 (L9·L19·L26).
- 주문 관련 메서드는 OrderGateway에서만 호출한다 — 전략 코드가 직접 부르면 리뷰 반려 (계명 1).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from messiah.core.messages import Fill, GreeksProfile, OrderRequest


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    qty: int  # 부호 있음: +Long / -Short
    avg_price_ticks: int
    # 옵션 포지션의 그릭스(Ver 2.0 §9 W30~31, risk/risk_engine.py R7·R8·R9). 선물 포지션은
    # 항상 None — Greeks 개념 자체가 없다. 옵션 실행 경로(주문 생성·체결)가 아직 없어서
    # (Options AI는 후보 산출까지만, `strategy/options/service.py` 모듈 docstring) 지금은
    # 어떤 어댑터도 이 필드를 실제로 채우지 않는다 — R7/R8 게이트를 미리 준비해두되, 실측
    # 연동은 옵션 주문 실행 경로가 생긴 뒤의 몫(알려진 갭).
    greeks: GreeksProfile | None = None


@dataclass(frozen=True)
class BrokerAccount:
    cash: Decimal
    margin_used: Decimal
    total_equity: Decimal


@dataclass(frozen=True)
class SubmitResult:
    """주문 전송 결과.

    ## `fill`은 **제출 시점에 이미 체결된 경우**만 채운다 (2026-09-17 F-114)

    실전 브로커(KIS)는 체결을 별도 통지(`broker/kis/order_notice.py`)로 비동기 전달하므로
    여기는 항상 `None`이다. `SimBroker`의 **시장가**만 예외다 — 그 어댑터는 `submit()` 안에서
    즉시 체결시키는데(`_fill_market()`), 종전에는 그때 만든 `Fill`을 **아무에게도 주지 않고
    버렸다.**

    실측 피해: 2026-09-17 15:00:01 진입 1계약(`SIM00000001`)과 15:25:00 EOD 강제청산
    1계약(`SIM00000002`)이 둘 다 시장가였고, 그날 로그에 `FillMatched`·`FillUnmatched`가
    **0건**이다. 즉 두 계약이 실제로 오갔는데 체결 경로는 하루 종일 조용했다. 그 결과
    `OrderGateway`의 pending 두 건은 영영 안 지워졌고(누수), 체결을 세는 어떤 소비자도
    그 둘을 볼 수 없었다. 09-16의 첫 실거래(진입 2·청산 2)도 같은 형태다.

    `OrderGateway.submit()`이 이 필드를 보고 `on_fill()`로 넘긴다 — 주문 경로가 하나라는
    계약(계명 1)을 지키면서 즉시 체결을 정상 체결 흐름에 태우는 유일한 자리다.
    """

    ok: bool
    broker_order_no: str = ""
    error: str = ""
    fill: "Fill | None" = None


class BrokerAdapter(ABC):
    """모든 브로커(및 시뮬레이터)의 공통 계약."""

    name: str = "base"

    # ---- 연결 수명주기 -------------------------------------------------
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    # ---- 주문 (OrderGateway 전용 호출) ---------------------------------
    @abstractmethod
    async def submit(self, req: OrderRequest) -> SubmitResult: ...

    @abstractmethod
    async def cancel(self, broker_order_no: str) -> bool: ...

    # ---- 진실원천 조회 (Reconciler·재시작 복원용, L12) -------------------
    @abstractmethod
    async def positions(self) -> list[BrokerPosition]:
        """브로커 기준 포지션 — 로컬 기억보다 항상 이것이 정답이다."""

    @abstractmethod
    async def account(self) -> BrokerAccount: ...

    # ---- 종목코드 검증 (계명 9) ----------------------------------------
    @abstractmethod
    async def probe_front_month(self, product: str) -> str:
        """근월물 코드를 실측 프로브로 확정. 저장값을 신뢰하지 않는다 (미륵이 D49·D84)."""
