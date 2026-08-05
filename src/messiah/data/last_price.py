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

## 장전 시드 — 첫 틱 이전에만 (2026-08-05)

수집은 08:35에 뜨는데 미니선물 첫 틱은 **08:45 정각**에 온다(3거래일 연속 실측,
`data/collector.py`의 `_note_tick_received`). 그 10분 동안 이 추적기는 값이 없고, 옵션체인
폴러는 매 사이클 `OptionChainSkipped`를 남기며 건너뛴다 — 2026-08-05엔 5사이클이 그렇게
비었다. 옵션 스냅샷은 과거 조회 경로가 없어 **그 10분은 영원히 빈다.**

그래서 `seed_preopen()`으로 전일 종가를 넣을 수 있게 했다. 행사가 간격이 2.5pt이고 ATM±10
창이 50pt를 덮으므로, 하룻밤 갭이 그 창을 벗어나는 일은 사실상 없다.

**시드는 첫 실틱이 오기 전까지만 유효하다.** `update()`가 한 번이라도 불리면 그 뒤로는
영원히 무시된다 — 안 그러면 장중에 WS가 끊겼을 때 위의 신선도 규칙(오래된 값은 없는 것으로
친다)을 시드가 우회해 버린다. 그건 이 모듈이 막으려던 바로 그 실패다.
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
        self._seed_ticks: int | None = None

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def has_seen_tick(self) -> bool:
        """이 세션에서 실제 체결틱을 한 번이라도 받았는가 — 장전 시드의 유효 조건."""
        return self._seen_at is not None

    def seed_preopen(self, price_ticks: int) -> None:
        """첫 실틱 이전에만 쓰일 기준가(전일 종가 등)를 넣는다.

        **첫 틱이 오면 영구히 무시된다** — 장중 WS 단절 시 신선도 규칙을 우회하지 않게
        하기 위해서다(모듈 docstring "장전 시드"). 이 메서드는 `_seen_at`을 건드리지 않으므로
        시드만 있는 상태는 `has_seen_tick=False` 그대로다.
        """
        self._seed_ticks = int(price_ticks)

    def update(self, price_ticks: int, *, seen_at: datetime | None = None) -> None:
        self._price_ticks = int(price_ticks)
        self._seen_at = seen_at or now_kst()

    def price_points(self, *, now: datetime | None = None) -> float | None:
        """
        반환: 최신 체결가(지수 포인트). 틱을 한 번도 못 받았어도 장전 시드가 있으면 그 값을
             돌려준다. 틱을 받은 뒤라면 시드는 무시하고, `max_age_seconds`를 넘겨 오래된
             값은 None — 호출자(`OptionChainPoller`)가 그 사이클을 건너뛴다.
        """
        if self._price_ticks is None or self._seen_at is None:
            # 아직 실틱 전 — 시드가 있으면 그것으로 ATM을 잡는다(장전 08:35~08:45).
            if self._seed_ticks is None:
                return None
            return float(self._tick_size * self._seed_ticks)
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
