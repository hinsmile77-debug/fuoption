"""RegimeRuntime — RegimeAI를 bar.{driving_horizon}.{symbol}에 상시 배선 (Ver 2.0 §9 W24~26).

`strategy/regime/service.py`(RegimeAI)의 `fit()`/`classify()`는 W20~21에 이미 구현됐지만
"어떤 운영 루프에도 아직 발행 안 됨"(그 모듈 docstring) — `RegimeAI.fit()`은 야간 배치로
미리 학습을 끝낸 결과물이라는 전제이므로, 이 클래스는 **이미 학습된 인스턴스**를 받아 굴러가는
봉을 먹여 `intel.regime`을 발행하는 얇은 배선만 담당한다(`features/engine.py`의
`FeatureEngine`이 계산 로직과 발행 배선을 분리하지 않는 것과 달리, RegimeAI는 학습(오프라인)과
추론(온라인) 경계가 원래도 뚜렷해 배선을 별도 클래스로 뺐다).

구동 Horizon은 기본 30분(Ver 1.1 §3-1 "입력: feat.30m" — 이 클래스는 `feat`가 아니라
`bar`을 구독한다. RegimeAI.classify()는 FeatureVector가 아니라 BarClosed 시퀀스를 직접
받는 설계이기 때문(`strategy/regime/hmm_model.py`의 `build_observations()` 참고) — Regime
관측치 계산과 Futures Expert의 Feature 계산은 서로 다른 재료를 쓴다).
"""

from __future__ import annotations

from collections import deque
from typing import Sequence

from messiah.core.bus import TOPIC_BAR, TOPIC_REGIME, BusLike
from messiah.core.messages import BarClosed, Horizon
from messiah.strategy.regime.service import RegimeAI

_DEFAULT_HISTORY_LIMIT = 200  # HMM 관측 윈도우(기본 20)보다 넉넉히 큰 롤링 버퍼


class RegimeRuntime:
    def __init__(
        self,
        symbol: str,
        regime_ai: RegimeAI,
        bus: BusLike,
        *,
        driving_horizon: Horizon = Horizon.M30,
        history_limit: int = _DEFAULT_HISTORY_LIMIT,
    ) -> None:
        self._symbol = symbol
        self._regime_ai = regime_ai
        self._bus = bus
        self._horizon = driving_horizon
        self._history: deque[BarClosed] = deque(maxlen=history_limit)

    async def handle_bar(self, bar: BarClosed) -> None:
        # 타입부터 본다 (2026-08-07 P0-1) — `features/engine.py handle_bar`와 같은 이유.
        # 그날 같은 형태의 줄이 `KillSignal`을 받아 수집 프로세스를 통째로 죽였다.
        if not isinstance(bar, BarClosed):
            return
        if bar.symbol != self._symbol or bar.horizon != self._horizon:
            return
        self._history.append(bar)
        state = self._regime_ai.classify(self._bars())
        await self._bus.publish(TOPIC_REGIME, state)

    def _bars(self) -> Sequence[BarClosed]:
        return list(self._history)

    async def run_forever(self) -> None:
        topic = f"{TOPIC_BAR}.{self._horizon.value}.{self._symbol}"
        await self._bus.subscribe([topic], self.handle_bar)
