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

## 웜스타트 — 하루치 30m 봉으로는 하한을 영영 못 넘는다 (2026-08-12 F-1)

이 클래스는 매 기동마다 빈 `deque`로 출발했다. `classify()`의 하한은 `window+2` = **22봉**
인데 하루가 만드는 30m 봉은 **15봉**(2026-08-12 실측 `1m=410 → 30m=15`)이다. 15 < 22이므로
**장 마감까지 단 한 번도 하한을 못 넘는다** — 오늘만의 사고가 아니라 매 거래일 결정적으로
보장되는 상태였고, 실제로 그날 발행된 Meta Decision 14건이 전량 `Regime=UNKNOWN` →
`NO_TRADE`였다. 국면이 분포가 아니라 **상수**였다.

`FeatureEngine`은 같은 문제를 이미 웜스타트로 풀었다(`features/engine.py warm_start()` —
"매 기동이 콜드스타트라 15m/30m 피처는 하루 종일 2/3가 NaN이었다"). 처방이 옆에 있었는데
이 클래스에만 그 배선이 없었다. 그래서 같은 모양으로 맞춘다: 계산은 여기가 하고 **적재는
호출측이 아카이브에서 읽어 넘긴다**(`scripts/run_g2_paper_trading.py`).

**과거 봉을 이어 붙이는 것이 학습과 일치한다**: `scripts/train_regime_ai.py`는
`load_continuous_series()` → `aggregate_to_horizon(M30)`로 소급 한계일부터 오늘까지를
**하나의 연속 시계열**로 적합하고, 홀드아웃 판정도 `classify(bars[:i+1])`로 일자를 걸친
전체 이력을 넘긴다. 즉 휴장 경계에서 끊지 않는 것이 이 모델의 전제이고, 런타임이 매일
잘라 온 것이 오히려 학습과 어긋나 있었다.
"""

from __future__ import annotations

from collections import deque
from typing import Sequence

from messiah.core import logging as mlog
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

    @property
    def history_capacity(self) -> int:
        """웜스타트 호출측이 "몇 개를 읽어와야 하는지" 알기 위한 값 — `features/engine.py`의
        같은 이름 프로퍼티와 동일한 취지(용량을 호출측에 다시 하드코딩하지 않는다)."""
        return self._history.maxlen or _DEFAULT_HISTORY_LIMIT

    @property
    def min_bars_for_classify(self) -> int:
        """판정이 UNKNOWN을 벗어나려면 필요한 봉 수 — 판단의 정본은 `RegimeAI`다."""
        return self._regime_ai.min_bars_for_classify

    def warm_start(self, bars: Sequence[BarClosed]) -> int:
        """과거 완성봉으로 이력 버퍼를 미리 채운다 — **발행은 하지 않는다**.

        입력: 구동 Horizon의 완성봉. 심볼/Horizon이 안 맞는 봉은 버린다. 시간순이 아니어도
             되며(여기서 정렬한다), 용량(`history_capacity`)을 넘으면 최신 것만 남는다 —
             전부 `FeatureEngine.warm_start()`와 같은 계약이다.
        반환: 실제 적재된 봉 수 — 호출측이 로그로 남긴다(`RegimeWarmStart`). 이 수가
             `min_bars_for_classify` 미만이면 그날 판정은 여전히 UNKNOWN으로 시작하며,
             그 사실은 조용히 지나가면 안 된다(금지계명 12).
        """
        accepted = sorted(
            (
                b
                for b in bars
                if isinstance(b, BarClosed)
                and b.symbol == self._symbol
                and b.horizon == self._horizon
            ),
            key=lambda b: b.bar_open_kst,
        )
        self._history.clear()
        self._history.extend(accepted)  # deque(maxlen)이 알아서 오래된 것부터 버린다
        return len(self._history)

    async def handle_bar(self, bar: BarClosed) -> None:
        # 타입부터 본다 (2026-08-07 P0-1) — `features/engine.py handle_bar`와 같은 이유.
        # 그날 같은 형태의 줄이 `KillSignal`을 받아 수집 프로세스를 통째로 죽였다.
        if not isinstance(bar, BarClosed):
            return
        if bar.symbol != self._symbol or bar.horizon != self._horizon:
            return
        self._history.append(bar)
        bars = self._bars()
        state = self._regime_ai.classify(bars)
        # **국면 판정 그 자체를 남긴다** (2026-08-12 F-2). 그 전까지 국면의 유일한 흔적은
        # Meta Decision의 NO_TRADE **사유 문자열**이었고, 그래서 "14건 전부 UNKNOWN"이라는
        # 하루 종일의 전면 마비가 리포트를 아무 자국 없이 통과했다(그날 `breaches`는
        # 수급 다리 결손 1건뿐이었다). 30분마다 한 줄 — 하루 15행이라 부피 영향은 없다.
        mlog.log(
            "RegimeClassified",
            f"국면 판정 {state.regime.value} (확신도 {state.confidence:.2f})",
            symbol=self._symbol,
            horizon=self._horizon.value,
            regime=state.regime.value,
            confidence=round(float(state.confidence), 4),
            bars_used=len(bars),
            min_bars=self.min_bars_for_classify,
            rule_override=state.rule_override,
        )
        await self._bus.publish(TOPIC_REGIME, state)

    def _bars(self) -> Sequence[BarClosed]:
        return list(self._history)

    async def run_forever(self) -> None:
        topic = f"{TOPIC_BAR}.{self._horizon.value}.{self._symbol}"
        await self._bus.subscribe([topic], self.handle_bar)
