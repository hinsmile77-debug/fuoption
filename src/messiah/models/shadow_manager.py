"""Shadow Trading Manager — Ver 1.1 §6-4 "Self Evolution의 실체" (Ver 2.0 §9 W35~36, Phase 5).

책임(Ver 1.1 §6-4 원문): "`shadow` 상태 모델들에 실시간 Feature를 공급, 가상 주문 성적
기록." 승격 규칙 예시: "20거래일 이상 & 현역 대비 Net Sharpe 우위 & 최대낙폭 한도 내 → 승격
심사 발행." 승격은 "자동 제안 + 사람 승인"(자동 승격 없음) — 이 모듈은 제안(`PromotionProposal`)
까지만 만들고, 실제 상태 전이는 `models/registry.py`의 `ModelRegistry.promote_to_live()`를
사람이 호출해야 일어난다.

## 실주문 경로를 타지 않는다 (의도적 설계)

`FuturesAIService`/`TradingPipeline`(챔피언 경로)을 shadow 번들에도 그대로 재사용하면
Risk Engine·Sizer·OrderGateway까지 다시 거치게 되는데, 이 컴포넌트들은 "실제 계좌 상태"를
전제로 설계돼 있다(포지션·증거금 사용률 등) — 가상 모델을 위해 그 상태를 오염시키거나
분기 처리를 추가하면 계명 1(주문 경로는 OrderGateway 하나)의 정신에 어긋난다. 대신
`ShadowLedger`가 독립적인 단순 청산 규칙(Triple Barrier의 시간배리어만 재사용, 손절/익절
가격배리어는 보지 않음 — 챔피언과의 "상대 비교"가 목적이라 완전히 같은 정교함은 필요 없다는
명시적 절충)으로 가상 체결을 기록한다. **따라서 Shadow 성적은 챔피언이 실제로 냈을 성적의
근사이지 재현이 아니다** — Risk Engine이 거부/축소했을 신호도 Shadow는 전부 진입한다.

## 승격 비교의 "현역(champion)" 수익률은 이 모듈이 만들지 않는다

Ver 1.1 §6-4가 요구하는 "현역 대비 Net Sharpe 우위" 비교는 챔피언의 실현 손익 시계열이
있어야 하는데, 그건 Position Reconciler(Ver 1.1 §5-3, 진입가·청산가 매칭기)가 있어야
계산 가능하고 그 컴포넌트는 아직 없다(`strategy/pipeline.py` 모듈 docstring의 기존 갭과
동일). `evaluate_promotion()`은 그래서 champion/shadow 수익률 시계열을 **입력으로만**
받는다(`models/metrics.py`가 `labeling.py`에 의존하지 않는 것과 같은 결합도 원칙) —
Self Evaluation이나 그 이후에 생길 Position Reconciler가 챔피언 시계열을 만들어 넘기면 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_BAR, TOPIC_FEAT, TOPIC_SHADOW_FILL, BusLike
from messiah.core.messages import (
    BarClosed,
    BusMessage,
    ExpertView,
    FeatureVector,
    Horizon,
    PromotionProposal,
    ShadowFill,
    Side,
)
from messiah.models.labeling import BARRIER_PARAMS
from messiah.models.metrics import equity_curve_from_returns, max_drawdown, sharpe_ratio
from messiah.risk.cost_model import CostModel
from messiah.strategy.futures.expert import HorizonExpert
from messiah.strategy.futures.meta_labeler import (
    MetaLabeler,
    build_meta_features_from_feature_vector,
)

DEFAULT_ENTRY_MARGIN = 0.1  # |p_up - p_down|이 이보다 작으면 신규 진입 안 함(방향 우위 없음)


@dataclass
class _OpenPosition:
    side: Side
    entry_price_ticks: int
    entry_bar_index: int


class ShadowLedger:
    """단일 Horizon·단일 번들의 가상 체결 장부. 포지션 1개(피라미딩 없음) — 시간배리어
    경과 시 청산(`models/labeling.py`의 `BARRIER_PARAMS[horizon].time_barrier_bars` 재사용,
    Triple Barrier와 같은 자연 보유기간 근사). 보유 중엔 반대 신호가 와도 무시한다(Triple
    Barrier 레이블링이 "진입 후엔 배리어만 본다"는 것과 동일 원칙)."""

    def __init__(
        self,
        bundle_id: str,
        symbol: str,
        horizon: Horizon,
        *,
        cost_model: CostModel | None = None,
        entry_margin: float = DEFAULT_ENTRY_MARGIN,
    ) -> None:
        self._bundle_id = bundle_id
        self._symbol = symbol
        self._horizon = horizon
        self._cost_model = cost_model or CostModel()
        self._entry_margin = entry_margin
        self._time_barrier_bars = BARRIER_PARAMS[horizon].time_barrier_bars
        self._position: _OpenPosition | None = None
        self._bar_index = 0
        self._bars: list[BarClosed] = []
        self._fills: list[ShadowFill] = []
        self._undrained: list[ShadowFill] = []

    @property
    def fills(self) -> list[ShadowFill]:
        """전체 체결 이력(조회 전용) — 일일 Self Evaluation 집계용."""
        return list(self._fills)

    def drain_new_fills(self) -> list[ShadowFill]:
        """마지막 호출 이후 새로 청산된 체결만 반환하고 비운다(버스 발행 중복 방지)."""
        new, self._undrained = self._undrained, []
        return new

    def on_bar(self, bar: BarClosed) -> None:
        if bar.symbol != self._symbol or bar.horizon != self._horizon:
            return
        self._bars.append(bar)
        self._bar_index += 1
        if self._position is not None and (
            self._bar_index - self._position.entry_bar_index >= self._time_barrier_bars
        ):
            self._close(bar)

    def on_prediction(self, view: ExpertView, meta_passed: bool) -> None:
        if self._position is not None or not self._bars or not meta_passed:
            return
        margin = view.p_up - view.p_down
        if abs(margin) < self._entry_margin:
            return
        side = Side.LONG if margin > 0 else Side.SHORT
        last_bar = self._bars[-1]
        self._position = _OpenPosition(
            side=side, entry_price_ticks=last_bar.c_ticks, entry_bar_index=self._bar_index
        )
        mlog.log(
            "ShadowFillRecorded",
            "shadow 진입",
            bundle_id=self._bundle_id,
            symbol=self._symbol,
            side=side.value,
        )

    def _close(self, bar: BarClosed) -> None:
        position = self._position
        assert position is not None
        direction = 1 if position.side == Side.LONG else -1
        raw_move_ticks = direction * (bar.c_ticks - position.entry_price_ticks)
        cost = self._cost_model.estimate_round_trip_from_bars(self._bars, qty=1)
        net_return_ticks = raw_move_ticks - cost.total_ticks
        fill = ShadowFill(
            bundle_id=self._bundle_id,
            horizon=self._horizon,
            symbol=self._symbol,
            side=position.side,
            qty=1,
            entry_price_ticks=position.entry_price_ticks,
            exit_price_ticks=bar.c_ticks,
            net_return_ticks=net_return_ticks,
        )
        self._fills.append(fill)
        self._undrained.append(fill)
        self._position = None
        mlog.log(
            "ShadowFillRecorded",
            "shadow 청산",
            bundle_id=self._bundle_id,
            symbol=self._symbol,
            net_return_ticks=net_return_ticks,
        )


class ShadowManager:
    """`shadow` 상태 번들 여러 개를 동시에 병행 운용한다(Ver 1.1 §6-4). 번들은
    `add_shadow_bundle()`로 명시적으로 등록/해제 — Registry 폴링은 호출자 책임(예:
    G2 일일 스크립트가 장 시작 전 `registry.list_by_status(SHADOW)`로 동기화)."""

    def __init__(self, symbol: str, bus: BusLike) -> None:
        self._symbol = symbol
        self._bus = bus
        self._experts: dict[str, HorizonExpert] = {}
        self._meta_labelers: dict[str, MetaLabeler] = {}
        self._ledgers: dict[str, ShadowLedger] = {}

    def add_shadow_bundle(
        self, bundle_id: str, expert: HorizonExpert, meta_labeler: MetaLabeler | None = None
    ) -> None:
        self._experts[bundle_id] = expert
        if meta_labeler is not None:
            self._meta_labelers[bundle_id] = meta_labeler
        self._ledgers[bundle_id] = ShadowLedger(bundle_id, self._symbol, expert.horizon)

    def remove_shadow_bundle(self, bundle_id: str) -> None:
        self._experts.pop(bundle_id, None)
        self._meta_labelers.pop(bundle_id, None)
        self._ledgers.pop(bundle_id, None)

    @property
    def active_bundles(self) -> list[str]:
        return list(self._experts)

    def fills_for(self, bundle_id: str) -> list[ShadowFill]:
        ledger = self._ledgers.get(bundle_id)
        return ledger.fills if ledger else []

    async def handle_bar(self, bar: BarClosed) -> None:
        if bar.symbol != self._symbol:
            return
        for bundle_id, expert in self._experts.items():
            if expert.horizon != bar.horizon:
                continue
            self._ledgers[bundle_id].on_bar(bar)
            await self._flush(bundle_id)

    async def handle_feature(self, feature_vector: FeatureVector) -> None:
        if feature_vector.symbol != self._symbol:
            return
        for bundle_id, expert in self._experts.items():
            if expert.horizon != feature_vector.horizon:
                continue
            view = expert.predict(feature_vector)
            meta_passed = self._meta_passes(bundle_id, view, feature_vector)
            self._ledgers[bundle_id].on_prediction(view, meta_passed)
            await self._flush(bundle_id)

    def _meta_passes(self, bundle_id: str, view: ExpertView, feature_vector: FeatureVector) -> bool:
        meta = self._meta_labelers.get(bundle_id)
        if meta is None:
            return view.meta_passed  # 챔피언 서비스와 동일한 폴백(모듈 미보유 시 통과)
        probs = np.array([view.p_down, view.p_flat, view.p_up])
        meta_features = build_meta_features_from_feature_vector(probs, view.ens_std, feature_vector)
        return meta.passes(meta_features)

    async def _flush(self, bundle_id: str) -> None:
        for fill in self._ledgers[bundle_id].drain_new_fills():
            await self._bus.publish(TOPIC_SHADOW_FILL, fill)

    async def run_forever(self) -> None:
        patterns = [f"{TOPIC_FEAT}.{h.value}.{self._symbol}" for h in Horizon] + [
            f"{TOPIC_BAR}.{h.value}.{self._symbol}" for h in Horizon
        ]
        await self._bus.subscribe(patterns, self._dispatch)

    async def _dispatch(self, msg: BusMessage) -> None:
        if isinstance(msg, FeatureVector):
            await self.handle_feature(msg)
        elif isinstance(msg, BarClosed):
            await self.handle_bar(msg)


def evaluate_promotion(
    *,
    bundle_id: str,
    horizon: Horizon,
    trading_days_observed: int,
    champion_returns: Sequence[float],
    shadow_returns: Sequence[float],
    periods_per_year: float = 252.0,
    min_trading_days: int = 20,
    max_drawdown_limit: float = 0.3,
) -> PromotionProposal:
    """Ver 1.1 §6-4 승격 규칙(예시)을 그대로 구현: 20거래일 이상 & 현역 대비 Net Sharpe
    우위 & 최대낙폭 한도 내 → `recommended=True`. **이 함수는 아무것도 승격시키지 않는다**
    — 사람이 이 제안을 보고 `ModelRegistry.promote_to_live()`를 호출해야 실제 승격이
    일어난다(모듈 docstring)."""
    champion_sharpe = sharpe_ratio(champion_returns, periods_per_year=periods_per_year)
    shadow_sharpe = sharpe_ratio(shadow_returns, periods_per_year=periods_per_year)
    champion_mdd = max_drawdown(equity_curve_from_returns(champion_returns))
    shadow_mdd = max_drawdown(equity_curve_from_returns(shadow_returns))
    recommended = (
        trading_days_observed >= min_trading_days
        and shadow_sharpe > champion_sharpe
        and shadow_mdd < max_drawdown_limit
    )
    rationale = (
        f"관찰 {trading_days_observed}일(최소 {min_trading_days}) · "
        f"Sharpe shadow={shadow_sharpe:.2f} champion={champion_sharpe:.2f} · "
        f"MDD shadow={shadow_mdd:.2%}(한도 {max_drawdown_limit:.0%})"
    )
    mlog.log(
        "ShadowPromotionProposed",
        rationale,
        bundle_id=bundle_id,
        horizon=horizon.value,
        recommended=recommended,
    )
    return PromotionProposal(
        bundle_id=bundle_id,
        horizon=horizon,
        trading_days_observed=trading_days_observed,
        champion_sharpe=champion_sharpe,
        shadow_sharpe=shadow_sharpe,
        champion_max_drawdown=champion_mdd,
        shadow_max_drawdown=shadow_mdd,
        recommended=recommended,
        rationale=rationale,
    )
