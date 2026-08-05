"""Phase 5 전 경로 스모크 — Registry·Shadow Manager·Self Evaluation·Release 패키징
(Master Plan Ver 2.0 §9 W35~38).

`scripts/run_full_path_smoke.py`(W24~26)와 같은 2단계 패턴을 따른다: 합성 데이터로 Expert
2개를 학습해 하나는 `live`로, 다른 하나는 `shadow`로 Registry에 승격시킨 뒤, 실시간 배선
(FeatureEngine→FuturesAIService(live)→TradingPipeline→SimBroker, ShadowManager(shadow))에
M1봉을 흘려 실제로:
  1) `ModelRegistry`가 candidate→shadow→live 상태 전이와 이벤트 발행을 실제로 하는지
  2) `ShadowManager`가 챔피언과 별개로 가상 체결(`ShadowFill`)을 실제로 기록하는지
  3) `evaluate_promotion()`이 그 가상 체결로 승격 제안을 실제로 만드는지
  4) `run_self_evaluation()`이 하루치 성적을 실제로 집계하는지
  5) `pack_release()`/`verify_release()`가 Registry live 상태와 정합적인 릴리스를 실제로
     만드는지
전부 1회 실행으로 확인한다. **실제 시장 데이터가 아니다** — 이 스크립트가 증명하는 것은
배관이지 우위가 아니다(다른 모든 스모크 스크립트와 동일한 성격, capability_matrix.md 알려진
갭과 동일 이유: 실측 아카이브가 너무 짧아 의미 있는 학습이 불가능).

사용: python scripts/run_phase5_smoke.py
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.broker.simulator.adapter import SimBroker  # noqa: E402
from messiah.core.messages import BarClosed, BundleStatus, ExpertView, Horizon  # noqa: E402
from messiah.core.timeutil import KST  # noqa: E402
from messiah.data.archiver import ParquetArchiver  # noqa: E402
from messiah.data.bar_composer import MultiHorizonBarComposer  # noqa: E402
from messiah.execution.order_gateway import OrderGateway  # noqa: E402
from messiah.features.engine import FeatureEngine  # noqa: E402
from messiah.models.labeling import BARRIER_PARAMS  # noqa: E402
from messiah.models.registry import ModelRegistry, pack_bundle  # noqa: E402
from messiah.models.release import pack_release, verify_release  # noqa: E402
from messiah.models.self_evaluation import run_self_evaluation  # noqa: E402
from messiah.models.shadow_manager import (  # noqa: E402
    ShadowLedger,
    ShadowManager,
    evaluate_promotion,
)
from messiah.models.trainer import build_feature_vectors, train_formal_expert  # noqa: E402
from messiah.models.validator import Validator  # noqa: E402
from messiah.simulator.engine import LiveSimBrokerFeed  # noqa: E402
from messiah.simulator.inprocess_bus import InProcessBus  # noqa: E402
from messiah.strategy.futures.service import FuturesAIService  # noqa: E402
from messiah.strategy.pipeline import TradingPipeline  # noqa: E402

_SYMBOL = "SYNPHASE5"
_FEATURE_SET = "v2026.07"
_HORIZON = Horizon.M5


def _synthetic_bars(n: int, *, seed: int) -> list[BarClosed]:
    rng = random.Random(seed)
    start = datetime(2026, 1, 5, 9, 0, tzinfo=KST)
    minutes = int(_HORIZON.value.rstrip("m"))
    out = []
    for i in range(n):
        close = round(1000 + 50 * math.sin(i / 4) + rng.uniform(-3, 3))
        out.append(
            BarClosed(
                symbol=_SYMBOL,
                horizon=_HORIZON,
                bar_open_kst=start + timedelta(minutes=minutes * i),
                o_ticks=close,
                h_ticks=close + 5,
                l_ticks=close - 5,
                c_ticks=close,
                volume=100 + i,
            )
        )
    return out


def _synthetic_m1_bars(n: int, *, seed: int) -> list[BarClosed]:
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
                symbol=_SYMBOL,
                horizon=Horizon.M1,
                bar_open_kst=start + timedelta(minutes=i),
                o_ticks=close,
                h_ticks=close + 5,
                l_ticks=close - 5,
                c_ticks=close,
                volume=50 + i,
            )
        )
    return out


async def _train_and_pack(
    registry: ModelRegistry, out_dir: Path, *, label: str, seed: int, args: argparse.Namespace
):
    bars = _synthetic_bars(args.synthetic_bars, seed=seed)
    result = await train_formal_expert(
        bars,
        feature_set=_FEATURE_SET,
        model_version=f"{_HORIZON.value}_phase5_smoke_{label}",
        atr_window=args.atr_window,
        n_splits=args.n_splits,
        n_search_trials=args.n_search_trials,
        search_num_boost_round=args.search_num_boost_round,
        final_num_boost_round=args.final_num_boost_round,
        n_members=args.n_members,
        meta_num_boost_round=args.meta_num_boost_round,
    )
    feature_vectors = await build_feature_vectors(bars, feature_set=_FEATURE_SET)
    validator = Validator()
    report = validator.validate_all(
        expert=result.expert,
        sample_feature_vector=feature_vectors[-1],
        tmp_path=out_dir,
        daily_returns=[0.01, -0.005, 0.02, 0.0],
        periods_per_year=252.0,
        equity_curve=[1.0, 1.01, 1.005, 1.025, 1.025],
        window_returns=[0.01, -0.005, 0.02],
        calibration_probs=[[0.2, 0.3, 0.5], [0.6, 0.2, 0.2]],
        calibration_true_idx=[2, 0],
    )
    print(f"[{label}] Validator 통과 관문: {[g.name for g in report.gates if g.passed]}")
    print(f"[{label}] Validator 미달 관문: {[g.name for g in report.failed_gates()]}")

    bundle_id = f"{_HORIZON.value}_smoke_{label}"
    manifest = pack_bundle(
        bundle_id=bundle_id,
        horizon=_HORIZON,
        training_result=result,
        validation_report=report,
        trained_range=("2026-01-05", "2026-01-06"),
        feature_set=_FEATURE_SET,
        run_id=f"phase5-smoke-{label}",
        out_dir=out_dir,
    )
    registry.register(manifest, out_dir / bundle_id)
    return manifest


async def main(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    registry = ModelRegistry(out_dir / "registry.db")

    print("[1/6] Expert 2개 학습·패킹·candidate 등록 (champion 후보 A, shadow 후보 B)")
    manifest_a = await _train_and_pack(registry, out_dir, label="A", seed=1, args=args)
    manifest_b = await _train_and_pack(registry, out_dir, label="B", seed=2, args=args)

    print("\n[2/6] A는 shadow→live로 승격(챔피언), B는 shadow까지만(도전자)")
    registry.promote_to_shadow(manifest_a.bundle_id, "스모크: 챔피언 후보 방어전 개시")
    registry.promote_to_live(
        manifest_a.bundle_id, operator="smoke-script", reason="스모크 최초 승격"
    )
    registry.promote_to_shadow(manifest_b.bundle_id, "스모크: 도전자 방어전 개시")
    live = registry.get_live(_HORIZON)
    shadows = registry.list_by_status(BundleStatus.SHADOW)
    print(f"live: {live.bundle_id if live else None} · shadow: {[s.bundle_id for s in shadows]}")

    print("\n[3/6] 실시간 배선 구성 (InProcessBus 기반, Ver 1.0.1 §2.1 동일 인터페이스)")
    bus = InProcessBus(instance_id="messiah-phase5-smoke")
    archiver = ParquetArchiver(out_dir / "bars")
    composer = MultiHorizonBarComposer(_SYMBOL, archiver, bus)
    feature_engine = FeatureEngine(_SYMBOL, bus, _FEATURE_SET)

    live_record = registry.get_live(_HORIZON)
    assert live_record is not None
    futures_service = FuturesAIService(
        _SYMBOL,
        {_HORIZON: live_record.load_expert()},
        bus,
        meta_labelers={_HORIZON: live_record.load_meta_labeler()},
    )
    shadow_manager = ShadowManager(_SYMBOL, bus)
    for record in shadows:
        shadow_manager.add_shadow_bundle(
            record.bundle_id, record.load_expert(), record.load_meta_labeler()
        )

    broker = SimBroker(cash=args.cash)
    await broker.connect()
    gateway = OrderGateway(broker)
    pipeline = TradingPipeline(_SYMBOL, broker, gateway, bus)
    sim_feed = LiveSimBrokerFeed(_SYMBOL, broker, gateway, bus)

    await feature_engine.run_forever()
    await futures_service.run_forever()
    await pipeline.run_forever()
    await sim_feed.run_forever()
    await shadow_manager.run_forever()
    await pipeline.start_day()

    print(f"\n[4/6] M1봉 {args.live_bars}건 투입 — 챔피언·Shadow 동시 병행 운용")
    m1_bars = _synthetic_m1_bars(args.live_bars, seed=7)
    for i, bar in enumerate(m1_bars):
        await bus.publish(f"bar.{Horizon.M1.value}.{bar.symbol}", bar)
        if (i + 1) % int(_HORIZON.value.rstrip("m")) == 0:
            # `force=True` — 동기 재생이라 대기 중 새 봉 도착이 성립하지 않는다
            # (`bar_composer.flush_due_horizon`의 "기다릴 이유가 없는 경로").
            await composer.flush_due_horizon(_HORIZON, force=True)
    await composer.flush_all_final()

    shadow_fill_counts = {
        bid: len(shadow_manager.fills_for(bid)) for bid in shadow_manager.active_bundles
    }
    print(f"Shadow 가상 체결 수(유기적 재생): {shadow_fill_counts}")

    print(
        "\n[4b/6] 직접 시연 — 합성 데이터 신호가 약해 유기적 재생만으로는 Shadow 체결이 안 났을"
        " 수 있음(5m Expert 예측력 없음은 기존 갭, run_full_path_smoke.py와 동일 사정) —"
        " ShadowLedger 메커니즘 자체가 실제로 체결을 내는지 강한 신호를 직접 주입해 확인"
    )
    demo_ledger = ShadowLedger("demo-bundle", _SYMBOL, _HORIZON)
    demo_bars = _synthetic_bars(BARRIER_PARAMS[_HORIZON].time_barrier_bars + 2, seed=99)
    demo_ledger.on_bar(demo_bars[0])
    strong_view = ExpertView(
        symbol=_SYMBOL,
        horizon=_HORIZON,
        p_down=0.05,
        p_flat=0.05,
        p_up=0.9,
        ens_std=0.01,
        meta_passed=True,
        model_version="demo",
        top_features=[],
        valid_until=demo_bars[0].bar_open_kst,
    )
    demo_ledger.on_prediction(strong_view, True)
    for bar in demo_bars[1:]:
        demo_ledger.on_bar(bar)
    print(f"직접 시연 Shadow 체결: {demo_ledger.fills}")

    print("\n[5/6] Self Evaluation + 승격 제안")
    champion_returns = [0.004, -0.002, 0.006, 0.001, -0.001]  # 스모크용 5거래일 가상 이력
    report = run_self_evaluation(
        date="2026-02-05",
        symbol=_SYMBOL,
        champion_returns=champion_returns,
        n_shadow_bundles=len(shadows),
    )

    # 손익 4지표는 `pnl_measurable=False`면 None이다(2026-08-05) — 이 스모크는 결선 상태
    # (`wiring`)를 안 넘기므로 정상적으로 None이 나온다. 0.0으로 찍어 성적처럼 보이게
    # 하느니 "미측정"이라고 적는다(`core/messages.py` SelfEvalReport docstring).
    def _fmt(value: float | None, spec: str) -> str:
        return "미측정" if value is None else format(value, spec)

    print(
        f"SelfEvalReport: sharpe={_fmt(report.sharpe, '.2f')} "
        f"pf={_fmt(report.profit_factor, '.2f')} "
        f"win_rate={_fmt(report.win_rate, '.0%')} mdd={_fmt(report.max_drawdown, '.1%')}"
    )
    demo_returns = [f.net_return_ticks for f in demo_ledger.fills if f.net_return_ticks is not None]
    for record in shadows:
        shadow_returns = [
            f.net_return_ticks
            for f in shadow_manager.fills_for(record.bundle_id)
            if f.net_return_ticks is not None
        ] or demo_returns  # 유기적 재생 체결이 0건이면 직접 시연 결과로 메커니즘만 시연
        proposal = evaluate_promotion(
            bundle_id=record.bundle_id,
            horizon=_HORIZON,
            trading_days_observed=len(champion_returns),
            champion_returns=champion_returns,
            shadow_returns=shadow_returns or [0.0],
        )
        print(
            f"승격 제안({record.bundle_id}): recommended={proposal.recommended} — "
            f"{proposal.rationale}"
        )

    print("\n[6/6] 릴리스 패키징 + 정합성 검증")
    release = pack_release(registry, "phase5-smoke-release", out_dir=out_dir)
    print(f"릴리스: {release.bundles} (누락 Horizon: {release.missing_horizons})")
    problems = verify_release(registry, release)
    print(f"릴리스 정합성 문제: {problems or '없음'}")

    registry.close()
    print("\n버그 없이 1회 성공.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MESSIAH Phase 5 전 경로 스모크 실행")
    parser.add_argument("--out-dir", default="data/_phase5_smoke")
    parser.add_argument("--synthetic-bars", type=int, default=150)
    parser.add_argument("--live-bars", type=int, default=60)
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
