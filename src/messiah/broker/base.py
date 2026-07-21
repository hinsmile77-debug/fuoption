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

from messiah.core.messages import OrderRequest


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    qty: int  # 부호 있음: +Long / -Short
    avg_price_ticks: int


@dataclass(frozen=True)
class BrokerAccount:
    cash: Decimal
    margin_used: Decimal
    total_equity: Decimal


@dataclass(frozen=True)
class SubmitResult:
    ok: bool
    broker_order_no: str = ""
    error: str = ""


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
