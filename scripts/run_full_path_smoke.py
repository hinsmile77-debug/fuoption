"""전 경로 관통 수동 스모크 — Aggregator·Meta Decision·Risk Engine·Sizer·Kill Switch
(Master Plan Ver 2.0 §9 W24~26).

W17~23까지 만들어진 컴포넌트(HorizonExpert·MetaLabeler·RegimeAI)는 각자 학습·추론은
되지만 실시간 파이프라인에 연결된 적이 없었다(각 모듈 docstring "어떤 운영 루프에도 아직
안 붙어 있음"). 이번 주 신설된 5개 컴포넌트(`strategy/futures/aggregator.py`,
`strategy/futures/service.py`, `strategy/regime/runtime.py`,
`strategy/decision/meta_decision.py`, `risk/{risk_engine,sizer,kill_switch}.py`,
`strategy/pipeline.py`)가 그 결선이다 — 이 스크립트는 L2(Feature)부터 L5(Execution)까지
"완성봉 하나가 실제로 주문까지 이어지는" 전 경로를 실제로 실행해 확인한다.

실제 아카이브가 하루치뿐이라(기존 갭, capability_matrix.md) HMM·Expert 학습이 의미 있게
안 되는 건 이전 주차들과 같은 한계다. 이 스크립트도 같은 2단계 패턴을 따른다:

1) 실제 아카이브로 먼저 시도 — 예상대로 데이터 부족 실패, 정직하게 보고.
2) 합성(추세+지터) 데이터로 ① Expert 2개(5m·30m) + Meta-Labeler 학습 ② RegimeAI 학습
   ③ 전체 실시간 배선 구동 ④ Kill Switch 강제 발동 시나리오까지 시연한다 —
   **실제 시장 데이터가 아니다**, 배관 검증 전용.

사용: python scripts/run_full_path_smoke.py --symbol A05608 --start 2026-07-24 --end 2026-07-24
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.broker.simulator.adapter import SimBroker  # noqa: E402
from messiah.core.bus import TOPIC_INTENT  # noqa: E402
from messiah.core.messages import (  # noqa: E402
    BarClosed,
    FuturesView,
    Horizon,
    Regime,
    bar_confirm_time,
)
from messiah.core.timeutil import KST  # noqa: E402
from messiah.data.archiver import ParquetArchiver  # noqa: E402
from messiah.data.bar_composer import MultiHorizonBarComposer  # noqa: E402
from messiah.execution.order_gateway import OrderGateway  # noqa: E402
from messiah.features.engine import FeatureEngine  # noqa: E402
from messiah.models.trainer import train_formal_expert  # noqa: E402
from messiah.risk.kill_switch import KillSwitch, KillSwitchConfig  # noqa: E402
from messiah.simulator.inprocess_bus import InProcessBus  # noqa: E402
from messiah.simulator.replay import ParquetBarReplaySource  # noqa: E402
from messiah.strategy.futures.service import FuturesAIService  # noqa: E402
from messiah.strategy.pipeline import TradingPipeline  # noqa: E402
from messiah.strategy.regime.hmm_model import OBSERVATION_WINDOW  # noqa: E402
from messiah.strategy.regime.runtime import RegimeRuntime  # noqa: E402
from messiah.strategy.regime.service import RegimeAI  # noqa: E402

_DATA_DIR = Path("data") / "bars"
_SYNTHETIC_SYMBOL = "SYNFULL"
_FEATURE_SET = "v2026.07"
_TRAIN_HORIZONS = (Horizon.M5, Horizon.M30)


# ---------------------------------------------------------------- 합성 데이터


def _synthetic_horizon_bars(n: int, horizon: Horizon, *, seed: int) -> list[BarClosed]:
    """단일 Horizon용 사인파+지터 합성봉 — Expert 학습 전용(run_formal_expert_training_smoke.py
    와 동일 발상). 실제 시장 데이터가 아니다."""
    rng = random.Random(seed)
    start = datetime(2026, 1, 5, 9, 0, tzinfo=KST)
    minutes = int(horizon.value.rstrip("m"))
    out = []
    for i in range(n):
        close = round(1000 + 50 * math.sin(i / 4) + rng.uniform(-3, 3))
        out.append(
            BarClosed(
                symbol=_SYNTHETIC_SYMBOL,
                horizon=horizon,
                bar_open_kst=start + timedelta(minutes=minutes * i),
                o_ticks=close,
                h_ticks=close + 5,
                l_ticks=close - 5,
                c_ticks=close,
                volume=100 + i,
            )
        )
    return out


def _synthetic_regime_bars(cycles: int, *, seed: int = 1) -> list[BarClosed]:
    """추세상승/횡보/고변동성 반복 30분봉 — RegimeAI 학습 전용(run_regime_ai_smoke.py와 동일)."""
    rng = random.Random(seed)
    start = datetime(2026, 1, 5, 9, 0, tzinfo=KST)
    price = 1000.0
    out: list[BarClosed] = []
    idx = 0
    for _ in range(cycles):
        for _ in range(20):
            price += 3 + rng.uniform(-1, 1)
            out.append(_regime_bar(idx, start, price))
            idx += 1
        for _ in range(20):
            price += rng.uniform(-1, 1)
            out.append(_regime_bar(idx, start, price))
            idx += 1
        for _ in range(15):
            price += rng.uniform(-15, 15)
            price = max(price, 100.0)
            out.append(_regime_bar(idx, start, price))
            idx += 1
    return out


def _regime_bar(idx: int, start: datetime, price: float) -> BarClosed:
    close = round(price)
    return BarClosed(
        symbol=_SYNTHETIC_SYMBOL,
        horizon=Horizon.M30,
        bar_open_kst=start + timedelta(minutes=30 * idx),
        o_ticks=close,
        h_ticks=close + 3,
        l_ticks=close - 3,
        c_ticks=close,
        volume=100,
    )


def _synthetic_live_m1_bars(n: int, *, seed: int = 42) -> list[BarClosed]:
    """ "라이브" 구간용 M1봉 — 완만한 상승 추세 + 지터. 학습 데이터와 시드를 분리해
    out-of-sample처럼 보이게 한다(성능 주장이 아니라 배선 시연이 목적이라 엄밀한 분리는
    아니다)."""
    rng = random.Random(seed)
    start = datetime(2026, 2, 1, 9, 0, tzinfo=KST)
    price = 1000.0
    out = []
    for i in range(n):
        price += 0.8 + rng.uniform(-1.5, 1.5)
        price = max(price, 100.0)
        close = round(price)
        out.append(
            BarClosed(
                symbol=_SYNTHETIC_SYMBOL,
                horizon=Horizon.M1,
                bar_open_kst=start + timedelta(minutes=i),
                o_ticks=close,
                h_ticks=close + 5,  # 학습 데이터(_synthetic_horizon_bars)와 동일 스케일 —
                l_ticks=close - 5,  # 너무 좁으면 ATR≈0이 돼 비용을 못 넘어 Net ER이 항상
                c_ticks=close,  # 음수가 된다(왕복비용은 스프레드·수수료 등 고정비가 있음).
                volume=50 + i,
            )
        )
    return out


# ---------------------------------------------------------------- 1) 실제 아카이브 시도


async def _try_real_archive(args: argparse.Namespace) -> None:
    source = ParquetBarReplaySource(Path(args.base_dir), args.symbol, horizons=list(Horizon))
    bars = source.load(date.fromisoformat(args.start), date.fromisoformat(args.end))
    print(f"[실제 아카이브] 입력 봉: {len(bars)}건 ({args.symbol}, 전 Horizon 합계)")
    m5_bars = [b for b in bars if b.horizon == Horizon.M5]
    try:
        if len(m5_bars) < 30:
            raise ValueError(f"5m 봉 {len(m5_bars)}건 — 학습에 부족")
        print("[실제 아카이브] 성공(데이터가 매우 적어 흔치 않은 결과)")
    except ValueError as exc:
        print(f"[실제 아카이브] 예상대로 실패(데이터 부족, 정상): {exc}")


# ---------------------------------------------------------------- 2) 합성 데이터 전체 배선


async def _train_experts(args: argparse.Namespace) -> dict[Horizon, tuple]:
    trained: dict[Horizon, tuple] = {}
    for horizon in _TRAIN_HORIZONS:
        bars = _synthetic_horizon_bars(args.synthetic_bars, horizon, seed=hash(horizon) % 1000)
        result = await train_formal_expert(
            bars,
            feature_set=_FEATURE_SET,
            model_version=f"{horizon.value}_full_path_smoke",
            atr_window=args.atr_window,
            n_splits=args.n_splits,
            n_search_trials=args.n_search_trials,
            search_num_boost_round=args.search_num_boost_round,
            final_num_boost_round=args.final_num_boost_round,
            n_members=args.n_members,
            meta_num_boost_round=args.meta_num_boost_round,
        )
        trained[horizon] = (result.expert, result.meta_labeler)
        print(
            f"[학습] {horizon.value} Expert: out-of-fold {result.n_oof_records}건, "
            f"Meta 임계값 {result.meta_labeler.threshold:.3f}"
        )
    return trained


async def _run_synthetic(args: argparse.Namespace) -> None:
    # `_feed_bars()`의 "N봉마다 flush" 근사가 실제 Horizon 경계(30분=최대공배수)와 어긋나지
    # 않으려면 투입 봉 수가 30의 배수여야 한다(모듈 하단 `_feed_bars` docstring 참고).
    args.live_bars = max(30, (args.live_bars // 30) * 30)

    print(f"\n[합성 데이터] Expert 2개({', '.join(h.value for h in _TRAIN_HORIZONS)}) 학습 시작")
    trained = await _train_experts(args)
    experts = {h: pair[0] for h, pair in trained.items()}
    meta_labelers = {h: pair[1] for h, pair in trained.items()}

    print("\n[합성 데이터] RegimeAI 학습(추세상승/횡보/고변동성 반복 30분봉)")
    regime_bars = _synthetic_regime_bars(args.regime_cycles)
    regime_ai = RegimeAI.fit(regime_bars, n_states_candidates=(3, 4, 5))
    print(f"HMM 상태 수(BIC 선정): {regime_ai.n_states}")

    print("\n[배선] Bus·FeatureEngine·FuturesAIService·RegimeRuntime·Digital Twin·Pipeline 구성")
    bus = InProcessBus(instance_id="messiah-full-path-smoke")
    archiver = ParquetArchiver(Path(args.base_dir))
    composer = MultiHorizonBarComposer(_SYNTHETIC_SYMBOL, archiver, bus)
    feature_engine = FeatureEngine(_SYNTHETIC_SYMBOL, bus, _FEATURE_SET)
    futures_service = FuturesAIService(_SYNTHETIC_SYMBOL, experts, bus, meta_labelers=meta_labelers)
    regime_runtime = RegimeRuntime(_SYNTHETIC_SYMBOL, regime_ai, bus)
    broker = SimBroker(cash=args.cash)
    await broker.connect()
    gateway = OrderGateway(broker)
    kill_switch = KillSwitch(bus, config=KillSwitchConfig(daily_loss_limit_pct=2.0))
    pipeline = TradingPipeline(_SYNTHETIC_SYMBOL, broker, gateway, bus, kill_switch=kill_switch)

    await feature_engine.run_forever()
    await futures_service.run_forever()
    await regime_runtime.run_forever()
    await pipeline.run_forever()

    # RegimeRuntime의 롤링 이력은 빈 상태로 시작한다 — HMM 관측 최소치(window+2, 기본 22개
    # 30분봉)를 채우기 전까지 classify()는 항상 UNKNOWN을 낸다(정상 동작, strategy/regime/
    # service.py 모듈 docstring "실패 시 UNKNOWN"). 실 운영에서도 기동 직후엔 같은 워밍업
    # 구간을 거친다 — 이 스모크는 그 구간을 학습 데이터의 꼬리로 미리 채워 건너뛴다(사람이
    # 매번 22개 봉을 기다리지 않고 국면 판정이 실제로 걸리는 경로까지 보기 위함).
    warmup = regime_bars[-(OBSERVATION_WINDOW + 2) :]
    for bar in warmup:
        await regime_runtime.handle_bar(bar)
    print(f"RegimeRuntime 워밍업: 30분봉 {len(warmup)}건으로 사전 이력 채움")

    intents = []
    orders = []
    await bus.subscribe([TOPIC_INTENT], _collector(intents))
    await bus.subscribe(["capital.order_request"], _collector(orders))

    await pipeline.start_day()

    print(f"\n[라이브 재생] M1봉 {args.live_bars}건 투입 (완만한 상승 추세, out-of-sample 시드)")
    m1_bars = _synthetic_live_m1_bars(args.live_bars)
    await _feed_bars(m1_bars, composer, broker, bus)

    print(f"decision.intent 발행 수: {len(intents)}")
    if intents:
        last = intents[-1]
        print(
            f"마지막 결정: side={last.side.value} confidence={last.confidence:.3f} "
            f"rationale={last.rationale!r}"
        )
    positions = await broker.positions()
    print(f"최종 포지션: {[(p.symbol, p.qty) for p in positions]}")

    print(
        "\n[주문 경로 직접 시연] 합성 데이터 Expert는 예측력이 없는 게 정상이라(기존 갭,"
        " capability_matrix.md) 위 유기적 재생만으로는 |S|가 임계를 못 넘을 수 있다 —"
        " Sizer·Risk Engine·OrderGateway까지 실제로 도는지 강한 LONG 신호를 직접 주입해 확인"
    )
    strong_view = FuturesView(
        symbol=_SYNTHETIC_SYMBOL,
        # ts_utc는 wall clock 기본값이 아니라 마지막 투입 봉의 봉 도메인 시각으로 맞춘다 —
        # aggregator.py 모듈 docstring "FuturesView.ts_utc = as_of" 원칙(재생 시나리오에서
        # 두 시각 도메인이 어긋나면 R11 데이터단절이 오탐한다)을 이 수작업 구성에도 지킨다.
        ts_utc=bar_confirm_time(m1_bars[-1]),
        score=0.5,
        agg_p_up=0.85,
        agg_p_down=0.05,
        uncertainty=0.05,
        dispersion=0.0,
        regime=Regime.TREND_UP,
        n_experts=2,
        model_versions=["direct-demo"],
    )
    await pipeline.handle_futures_view(strong_view)
    positions_after_demo = await broker.positions()
    print(f"직접 시연 후 포지션: {[(p.symbol, p.qty) for p in positions_after_demo]}")

    print("\n[Kill Switch 시연] 계좌 잔고를 강제로 깎아 R2(일일손실 2%) 발동을 유도한 뒤 계속 재생")
    account_before = await broker.account()
    broker._cash = account_before.total_equity * Decimal("0.97")  # noqa: SLF001 — 스모크 전용 조작
    more_bars = _synthetic_live_m1_bars(args.live_bars // 3, seed=99)
    for i, bar in enumerate(more_bars):
        bar = bar.model_copy(
            update={"bar_open_kst": m1_bars[-1].bar_open_kst + timedelta(minutes=i + 1)}
        )
        more_bars[i] = bar
    await _feed_bars(more_bars, composer, broker, bus)
    print(f"Kill Switch 발동 여부: {kill_switch.triggered}, Gateway 정지 여부: {gateway.halted}")


async def _feed_bars(
    bars: list[BarClosed], composer: MultiHorizonBarComposer, broker: SimBroker, bus
) -> None:
    """M1봉을 순서대로 투입 — 브로커 시계 진행, `bar.1m` 발행, Horizon 경계 도달 시
    합성봉 flush(정확한 경계 판정은 `bar_composer.floor_to_horizon`과 동일 원리를
    "N분마다 한 번씩" 카운팅으로 재현 — 봉 간격이 항상 정확히 1분이라 성립)."""
    for i, bar in enumerate(bars):
        broker.on_bar(bar)
        await composer.handle_one_minute_bar(bar)
        await bus.publish(f"bar.{Horizon.M1.value}.{bar.symbol}", bar)
        for horizon in Horizon:
            if horizon == Horizon.M1:
                continue
            minutes = int(horizon.value.rstrip("m"))
            if (i + 1) % minutes == 0:
                await composer.flush_due_horizon(horizon)


def _collector(sink: list):
    async def _handler(msg):
        sink.append(msg)

    return _handler


async def main(args: argparse.Namespace) -> None:
    await _try_real_archive(args)
    await _run_synthetic(args)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MESSIAH 전 경로 관통 스모크 실행")
    parser.add_argument("--symbol", default="A05608")
    parser.add_argument("--start", default="2026-07-24")
    parser.add_argument("--end", default="2026-07-24")
    parser.add_argument("--base-dir", default=str(_DATA_DIR))
    parser.add_argument("--synthetic-bars", type=int, default=150)
    parser.add_argument("--regime-cycles", type=int, default=6)
    parser.add_argument("--live-bars", type=int, default=90)
    parser.add_argument("--cash", type=int, default=50_000_000)
    parser.add_argument("--atr-window", type=int, default=5)
    parser.add_argument("--n-splits", type=int, default=3)
    parser.add_argument("--n-search-trials", type=int, default=5)
    parser.add_argument("--search-num-boost-round", type=int, default=20)
    parser.add_argument("--final-num-boost-round", type=int, default=30)
    parser.add_argument("--n-members", type=int, default=3)
    parser.add_argument("--meta-num-boost-round", type=int, default=20)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main(_parse_args()))
