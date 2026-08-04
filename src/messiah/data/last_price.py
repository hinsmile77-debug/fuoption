"""마지막 체결가 추적 — `md.tick.{symbol}`을 구독해 최신 가격을 지수 포인트로 들고 있는다.

`OptionChainPoller`의 ATM 기준가 공급원(`reference_price`)으로 만들었다. 옵션 행사가는 지수
포인트 단위(2.5 간격)인데 이 프로젝트의 가격은 전부 정수 틱(SYSTEM.md R2)이라, **단위 환산이
한 곳에만 있어야** 한다 — 폴러가 틱을 받아 스스로 나누게 두면 tick_size가 폴러마다 흩어진다.

## 왜 미니선물 가격을 KOSPI200 옵션의 기준가로 쓰나

옵션은 KOSPI200 **현물** 지수 기준인데 이 프로젝트는 현물지수 소스를 아직 연동하지 않았다
(RG 카테고리 갭). 선물은 베이시스만큼 현물과 어긋나지만, 2026-08-04 실측으로 그 크기가

    미니선물 998.08  vs  KOSPI200 현물 1000.03  →  베이시스 −1.95pt = 행사가 0.8칸

이라 ATM 창이 최대 한 칸 밀리는 정도다. ATM±10(21행사가) 창에서는 무해하다 — 창 가장자리
하나가 바뀔 뿐 관심 구간은 그대로 덮인다.

**더 정확한 값이 곧 생긴다**: `get_quote(O)` 응답의 `output3`이 KOSPI200 현물을 실어 나르므로
(`OptionQuoteSnapshot` docstring), 한 사이클 돈 뒤에는 그걸 기준가로 쓸 수 있다. 다만 그
전환은 실제 응답이 쌓여 지연·결측 특성을 본 뒤의 판단이라 지금은 선물로 간다.

## 신선도 — 오래된 가격은 없는 것으로 친다

WS가 끊겨 틱이 멈춰도 마지막 값은 메모리에 남는다. 그 값으로 ATM을 잡으면 **가격이 크게
움직인 뒤에도 옛 창을 계속 조회**하게 된다. `max_age_seconds`를 넘긴 값은 None을 돌려주고,
폴러는 그 사이클을 건너뛴다(전량 폴백은 하지 않는다 — 그게 22.6분짜리 폭주다).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from messiah.core.bus import TOPIC_TICK, BusLike
from messiah.core.messages import Tick
from messiah.core.timeutil import now_kst

DEFAULT_MAX_AGE_SECONDS = 180.0
"""이보다 오래된 틱은 기준가로 쓰지 않는다. 한산한 옵션이 아니라 **미니선물 근월물**을 보는
값이라 정상 장중이면 초 단위로 갱신된다 — 3분이 비면 수집이 끊긴 것이지 조용한 것이 아니다.
옵션 폴링 격자(300초)보다 짧게 잡아, 한 사이클을 통째로 건너뛰기 전에 먼저 드러나게 했다."""


class LastPriceTracker:
    """한 심볼의 최신 체결가를 지수 포인트로 보관한다."""

    def __init__(
        self,
        symbol: str,
        tick_size: Decimal,
        *,
        max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    ) -> None:
        self._symbol = symbol
        self._tick_size = Decimal(tick_size)
        self._max_age = timedelta(seconds=max_age_seconds)
        self._price_ticks: int | None = None
        self._seen_at: datetime | None = None

    @property
    def symbol(self) -> str:
        return self._symbol

    def update(self, price_ticks: int, *, seen_at: datetime | None = None) -> None:
        self._price_ticks = int(price_ticks)
        self._seen_at = seen_at or now_kst()

    def price_points(self, *, now: datetime | None = None) -> float | None:
        """
        반환: 최신 체결가(지수 포인트). 틱을 한 번도 못 받았거나 `max_age_seconds`를 넘겨
             오래됐으면 None — 호출자(`OptionChainPoller`)가 그 사이클을 건너뛴다.
        """
        if self._price_ticks is None or self._seen_at is None:
            return None
        if (now or now_kst()) - self._seen_at > self._max_age:
            return None
        return float(self._tick_size * self._price_ticks)

    async def handle_tick(self, tick: Tick) -> None:
        if isinstance(tick, Tick) and tick.symbol == self._symbol:
            self.update(tick.price_ticks)

    async def run_forever(self, bus: BusLike) -> None:
        await bus.subscribe([f"{TOPIC_TICK}.{self._symbol}"], self._dispatch)

    async def _dispatch(self, msg: object) -> None:
        if isinstance(msg, Tick):
            await self.handle_tick(msg)
