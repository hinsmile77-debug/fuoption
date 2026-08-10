"""Walk-Forward 백테스트 하니스 — Validator 성과 관문(Sharpe·MDD·창별 일관성)을 실제로
실행 가능하게 만드는 최초 구현 (Ver 1.2 §8.2/§8.3, Ver 2.0 §9, 2026-07-27 신설).

Digital Twin(W9~11)·Expert(W14~19)·Cost Model(W14~16)·Validator(W14~16)가 전부 갖춰졌지만
이들을 엮어 실제 성과 시계열을 뽑는 백테스트 하니스 자체가 없다는 게 W14~16부터 W24~26까지
계속 남아있던 공통 갭이었다(`models/validator.py` 모듈 docstring "성과 관문... 그 산출물을
만드는 실제 walk-forward 백테스트 루프는 이번 스코프 밖"). 이 모듈이 그 루프다.

## 동작 방식

1. `WalkForwardSplitter`(W12~13)가 전체 이력을 학습/검증 창으로 굴린다 — 창 경계는
   `triple_barrier_labels()`로 한 번 계산한 이벤트 시각들로부터 얻는다(경계 계산 전용,
   `cost_ticks`는 0으로 — 실제 레이블 품질은 창별 재학습이 다시 계산하므로 여기선 무관).
2. 매 창마다 **학습 구간**(embargo 반영, purge된 실제 경계 — 아래 `_slice_by_date` 참고)
   bars로 `train_formal_expert()`(W17~19)를 호출해 그 창 전용 Expert를 새로 학습한다 —
   Look-ahead 없음(다음 창의 정보가 이전 창 학습에 안 섞임, PurgedKFold와 같은 원칙을
   창 단위로 한 번 더 적용).
3. **검증 구간** M1봉을 `MultiHorizonBarComposer`(bar_open_kst만 있는 순수 합성봉이라 실제
   L1 라이브 경로와 동일 로직)로 재생 → `FeatureEngine`→`FuturesAIService`(방금 학습한
   Expert 1개)→`TradingPipeline`→`SimBroker`(모두 `InProcessBus`, `run_full_path_smoke.py`와
   동일 배선 패턴) → 창 시작/종료 시점 계좌 자산으로 창 수익률을 기록한다.
4. 전체 창을 모은 `equity_curve`/`window_returns`가 `Validator.validate_performance()`에
   처음으로 실제(비록 합성 데이터지만 최소한 walk-forward 구조를 갖춘) 시계열을 준다.

## 스코프 경계·근사

- **일별(daily) granularity 없음**: `sharpe_ratio()`가 기대하는 "기간별 수익률"을 창
  하나 = 한 기간으로 근사한다(진짜 일별 세분화를 원하면 `test_days=1`로 좁히면 됨,
  `periods_per_year`를 `365/test_days`로 스스로 맞춰 호출할 것 — 이 모듈은 그 값을
  강제하지 않는다).
- **Regime AI**: `regime_ai=`를 주면 `RegimeRuntime`(30m 완성봉 구독)이 배선되어
  `intel.regime`이 실제로 발행된다(2026-08-04 신설). **미주입이면 종전대로**
  `FuturesAIService`가 항상 `Regime.UNKNOWN`으로 집계하는데, 그 경우 가중치표가 전 Horizon
  0.5 고정이라 국면별 가중(TREND 계열 최대 1.5)이 통째로 죽는다
  (`strategy/futures/aggregator.py`의 `REGIME_WEIGHTS`). 즉 미주입은 "중립"이 아니라
  **한 가지 특정 가정**이므로, 성과를 비교할 때 이 축을 고정했는지 확인할 것.
- **Deflated Sharpe 미제출**: 여전히 미구현(`models/metrics.py` 모듈 docstring 알려진 갭) —
  이 하니스도 3종(Sharpe·MDD·창별 일관성)까지만 `Validator`에 넘긴다.
- **실제 시장 데이터 아님**: 실제 아카이브가 하루치뿐이라(기존 갭) 다개월 walk-forward를
  의미 있게 재현할 데이터가 없다 — `scripts/run_backtest_harness.py`가 다른 모든 W17~
  스크립트와 같은 2단계 패턴(실제 아카이브 시도→예상 실패→합성 데이터)을 따른다.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from messiah.broker.simulator.adapter import SimBroker
from messiah.core.bus import TOPIC_BAR
from messiah.core.messages import BarClosed, Horizon, bar_confirm_time
from messiah.core.timeutil import now_utc
from messiah.data.archiver import ParquetArchiver
from messiah.data.bar_composer import MultiHorizonBarComposer
from messiah.execution.order_gateway import OrderGateway
from messiah.features import sidecar
from messiah.features import spec as feature_spec
from messiah.features.engine import FeatureEngine
from messiah.models.cv import WalkForwardSplitter
from messiah.models.labeling import triple_barrier_labels
from messiah.models.trainer import train_formal_expert
from messiah.simulator.inprocess_bus import InProcessBus
from messiah.strategy.futures.meta_labeler import DEFAULT_MIN_SUPPORT_FRACTION
from messiah.strategy.futures.service import FuturesAIService
from messiah.strategy.pipeline import TradingPipeline
from messiah.strategy.regime.runtime import RegimeRuntime
from messiah.strategy.regime.service import RegimeAI


@dataclass(frozen=True)
class WindowResult:
    train_start: date
    train_end: date  # = test_start (embargo는 실제 학습 슬라이스에서만 반영, 아래 참고)
    test_start: date
    test_end: date
    start_equity: Decimal
    end_equity: Decimal
    n_train_bars: int
    n_test_bars: int

    @property
    def return_pct(self) -> float:
        if self.start_equity <= 0:
            return 0.0
        return float((self.end_equity - self.start_equity) / self.start_equity)


def aggregate_to_horizon(m1_bars: Sequence[BarClosed], horizon: Horizon) -> list[BarClosed]:
    """M1봉을 굵은 Horizon으로 OHLCV 롤업하는 순수 함수 — 학습용 봉 준비 전용(검증 구간
    재생은 `MultiHorizonBarComposer`를 실제로 통과시켜 라이브 경로와 동일 로직을 쓴다,
    이 함수는 그 비동기 배선 없이 학습 데이터를 빠르게 준비하기 위한 오프라인 지름길).
    Ver 1.2 §2.2 완성봉 규율과 동일하게 마지막 미완성 묶음은 버린다."""
    minutes = int(horizon.value.rstrip("m"))
    if minutes <= 1 or not m1_bars:
        return list(m1_bars)
    out: list[BarClosed] = []
    for i in range(0, len(m1_bars) - minutes + 1, minutes):
        chunk = m1_bars[i : i + minutes]
        out.append(
            BarClosed(
                symbol=chunk[0].symbol,
                horizon=horizon,
                bar_open_kst=chunk[0].bar_open_kst,
                o_ticks=chunk[0].o_ticks,
                h_ticks=max(b.h_ticks for b in chunk),
                l_ticks=min(b.l_ticks for b in chunk),
                c_ticks=chunk[-1].c_ticks,
                volume=sum(b.volume for b in chunk),
                quality_ok=all(b.quality_ok for b in chunk),
            )
        )
    return out


def _slice_by_date(bars: Sequence[BarClosed], start: date, end: date) -> list[BarClosed]:
    """[start, end) 반개구간(배타적 상한) — WalkForwardSplitter의 창 정의와 동일 관례."""
    return [b for b in bars if start <= b.bar_open_kst.date() < end]


class ReplayClock:
    """재생 중인 "지금" — 방금 투입한 봉의 확정시각을 돌려준다.

    ## 왜 필요한가 (2026-08-04)

    `TradingPipeline`은 데이터 신선도와 세션 판정을 **주입된 시계**로 한다. 그 시계를 안
    주면 실제 벽시계를 쓰는데, 재생은 과거 봉을 먹이므로 `data_age = 지금 − 과거봉확정시각`이
    수십 일이 된다 — KillSwitch R11(임계 30초)이 매 판단마다 발동해 게이트웨이를 정지시키고
    **신규 진입이 영영 안 난다**. 실측(2026-07-27 하루치 재생): data_age 672,056초(7.8일).

    이 하니스가 만들어진 2026-07-27부터 있던 문제이고, 그동안 창별 수익률이 항상 정확히
    0.0000%였던 것이 "데이터가 짧아 거래가 없었다"로 기록돼 있었다(`docs/capability_matrix.md`).
    실제로는 **어떤 데이터로도 거래가 날 수 없는 상태**였다.

    첫 봉을 먹이기 전에는 기준이 없으므로 실제 벽시계로 폴백한다 — 그 구간엔 아직 판단
    자체가 없어서(`FuturesView` 미발행) 값이 쓰이지 않는다.
    """

    def __init__(self) -> None:
        self._now: datetime | None = None

    def advance_to(self, bar: BarClosed) -> None:
        """봉 하나를 투입하기 직전에 호출 — 그 봉이 확정되는 시각으로 시계를 옮긴다.

        `bar_open_kst`가 아니라 `bar_confirm_time()`을 쓰는 이유: 파이프라인이 재는 것은
        "이 데이터가 언제부터 유효했나"이고, 완성봉은 확정시각에야 소비 가능해진다
        (Ver 1.2 §2.2 완성봉 규율 — `_last_bar_confirm_at`도 같은 기준).
        """
        self._now = bar_confirm_time(bar)

    def __call__(self) -> datetime:
        return self._now if self._now is not None else now_utc()


async def _feed_m1_bars(
    bars: Sequence[BarClosed],
    composer: MultiHorizonBarComposer,
    broker: SimBroker,
    bus,
    clock: ReplayClock | None = None,
) -> None:
    """M1봉을 순서대로 투입 — 재생 시계 전진, 브로커 시계 진행, `bar.1m` 발행, Horizon
    경계마다 합성봉 flush. `scripts/run_full_path_smoke.py`의 `_feed_bars`와 동일 로직(봉
    간격이 항상 정확히 1분이라 "N분마다 한 번" 카운팅으로 성립) — 하니스 전용으로 별도 보유."""
    for i, bar in enumerate(bars):
        # 시계를 **먼저** 옮긴다 — 이 봉이 유발하는 판단이 이 봉의 시각을 "지금"으로 봐야 한다.
        if clock is not None:
            clock.advance_to(bar)
        broker.on_bar(bar)
        await composer.handle_one_minute_bar(bar)
        await bus.publish(f"{TOPIC_BAR}.{Horizon.M1.value}.{bar.symbol}", bar)
        for horizon in Horizon:
            if horizon == Horizon.M1:
                continue
            minutes = int(horizon.value.rstrip("m"))
            if (i + 1) % minutes == 0:
                # `force=True` — 이 루프는 봉을 **동기로** 밀어 넣으므로 대기 중에 새 봉이
                # 도착하는 일이 성립하지 않는다. 실시간 경로의 겹④(마지막 구성봉 대기,
                # 2026-08-05)를 여기서 타면 오지 않을 봉을 매 버킷 상한까지 기다린다.
                await composer.flush_due_horizon(horizon, force=True)


async def run_walk_forward_backtest(
    m1_bars: Sequence[BarClosed],
    *,
    symbol: str,
    train_days: int,
    test_days: int,
    embargo_days: int = 1,
    step_days: int | None = None,
    train_horizon: Horizon = Horizon.M5,
    feature_set: str = "v2026.07",
    atr_window: int = 14,
    n_splits: int = 3,
    n_search_trials: int = 5,
    search_num_boost_round: int = 20,
    final_num_boost_round: int = 30,
    n_members: int = 3,
    meta_num_boost_round: int = 20,
    meta_threshold_splits: int = 5,
    meta_min_support_fraction: float = DEFAULT_MIN_SUPPORT_FRACTION,
    starting_cash: int = 50_000_000,
    regime_ai: "RegimeAI | None" = None,
) -> list[WindowResult]:
    """전체 M1봉 이력을 Walk-Forward 창으로 굴리며 창마다 재학습+검증재생한다.

    실패하는 창은 조용히 건너뛰지 않는다 — 학습 데이터가 부족하면(예: 창 크기가 너무
    작음) `train_formal_expert()`가 던지는 `ValueError`가 그대로 전파된다(정직한 실패,
    다른 모든 W17~ 스크립트와 동일 원칙). 호출자가 `train_days`/`test_days`를 데이터
    규모에 맞게 고를 책임이 있다.
    """
    horizon_bars = aggregate_to_horizon(m1_bars, train_horizon)
    boundary_labels = triple_barrier_labels(horizon_bars, atr_window=atr_window)
    event_times = [(lbl.t_start, lbl.t_end) for lbl in boundary_labels]

    splitter = WalkForwardSplitter(train_days, test_days, embargo_days, step_days)
    results: list[WindowResult] = []

    for window in splitter.split(event_times):
        if not window.train_indices or not window.test_indices:
            continue  # 데이터 경계에 걸쳐 한쪽이 빈 창 — WalkForwardSplitter 계약대로 그냥 건너뜀

        # embargo_cutoff는 splitter 내부와 동일하게 test_start(=train_end)에서
        # embargo_days를 뺀 값 — window.train_end 그대로 쓰면 splitter가 실제로 purge한
        # embargo 구간까지 학습에 새는 것과 같아진다(models/cv.py WalkForwardSplitter
        # docstring의 purge 정의 참고).
        embargo_cutoff = window.test_start - timedelta(days=embargo_days)
        train_bars = _slice_by_date(horizon_bars, window.train_start, embargo_cutoff)
        test_m1_bars = _slice_by_date(m1_bars, window.test_start, window.test_end)
        if not train_bars or not test_m1_bars:
            continue

        training = await train_formal_expert(
            train_bars,
            feature_set=feature_set,
            model_version=f"{train_horizon.value}_wfa_{window.train_start.isoformat()}",
            atr_window=atr_window,
            n_splits=n_splits,
            n_search_trials=n_search_trials,
            search_num_boost_round=search_num_boost_round,
            final_num_boost_round=final_num_boost_round,
            n_members=n_members,
            meta_num_boost_round=meta_num_boost_round,
            meta_threshold_splits=meta_threshold_splits,
            meta_min_support_fraction=meta_min_support_fraction,
        )

        with tempfile.TemporaryDirectory() as tmp:
            bus = InProcessBus(instance_id="messiah-backtest-harness")
            archiver = ParquetArchiver(Path(tmp))
            composer = MultiHorizonBarComposer(symbol, archiver, bus)
            # 사이드카 정본(`features/sidecar.build()`) — 학습 쪽(`models/trainer.py`)은
            # 이미 이걸 쓴다. 여기가 안 쓰면 **같은 feature_set으로 학습한 모델을 사이드카
            # 없는 벡터로 백테스트하게 되고**, 그 차이는 성과 숫자로만 흐릿하게 드러난다.
            feature_engine = FeatureEngine(
                symbol,
                bus,
                feature_set,
                sidecars=sidecar.build(feature_spec.resolve(feature_set)),
            )
            futures_service = FuturesAIService(
                symbol,
                {train_horizon: training.expert},
                bus,
                meta_labelers={train_horizon: training.meta_labeler},
            )
            broker = SimBroker(cash=starting_cash)
            await broker.connect()
            gateway = OrderGateway(broker)
            replay_clock = ReplayClock()
            pipeline = TradingPipeline(symbol, broker, gateway, bus, now=replay_clock)
            # Regime 결선 (2026-08-04). 미주입이면 `intel.regime`이 아예 발행되지 않아
            # `FuturesAIService`가 항상 `Regime.UNKNOWN`으로 집계한다 — 그 경우 가중치표는
            # 전 Horizon 0.5 고정이라 국면별 가중(0.5~1.5)이 통째로 죽는다.
            # `RegimeRuntime`은 30m 완성봉을 구독하므로 composer가 흘려주는 것으로 충분하다.
            if regime_ai is not None:
                await RegimeRuntime(symbol, regime_ai, bus).run_forever()

            await feature_engine.run_forever()
            await futures_service.run_forever()
            await pipeline.run_forever()
            await pipeline.start_day()

            start_equity = (await broker.account()).total_equity
            await _feed_m1_bars(test_m1_bars, composer, broker, bus, replay_clock)
            end_equity = (await broker.account()).total_equity

        results.append(
            WindowResult(
                train_start=window.train_start,
                train_end=window.train_end,
                test_start=window.test_start,
                test_end=window.test_end,
                start_equity=start_equity,
                end_equity=end_equity,
                n_train_bars=len(train_bars),
                n_test_bars=len(test_m1_bars),
            )
        )

    return results


def equity_curve_from_windows(results: Sequence[WindowResult], starting_cash: int) -> list[float]:
    """창별 종료 자산을 이어붙인 누적 자산곡선 — `models.metrics.max_drawdown()` 입력
    형식(첫 값이 시작 자본)."""
    curve = [float(starting_cash)]
    curve.extend(float(r.end_equity) for r in results)
    return curve


def window_returns_from_windows(results: Sequence[WindowResult]) -> list[float]:
    return [r.return_pct for r in results]
