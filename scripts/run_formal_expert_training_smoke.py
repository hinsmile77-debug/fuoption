"""5m Expert 정식(탐색·앙상블·교정) + Meta-Labeler 수동 스모크 — Master Plan Ver 2.0 §9 W17~19.

실제 아카이브가 하루치(A05608 5m 기준 7건)뿐이라 `train_formal_expert()`가 요구하는
PurgedKFold 기반 탐색·out-of-fold 가상운용을 의미 있게 돌릴 데이터가 없다
(WalkForwardSplitter가 W12~13에 겪은 것과 같은 한계 — capability_matrix.md 알려진 갭).
이 스크립트는 두 단계로 확인한다:

1) 실제 아카이브로 먼저 시도한다 — 데이터 부족으로 실패하는 게 정상이며, 그 사실 자체를
   정직하게 보고한다(성공해도 이상할 게 없다는 착각을 안 주기 위해).
2) 합성(사인파) 데이터로 전체 파이프라인(탐색→out-of-fold→앙상블→교정→Meta-Labeler)이
   실제로 동작하는지 시연한다 — **실제 시장 데이터가 아니다**, 배관 검증 전용.

사용: python scripts/run_formal_expert_training_smoke.py --symbol A05608 --horizon 5m
                                                           --start 2026-07-24 --end 2026-07-24
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np  # noqa: E402
from messiah.core.messages import BarClosed, Horizon  # noqa: E402
from messiah.core.timeutil import KST  # noqa: E402
from messiah.models.trainer import build_feature_vectors, train_formal_expert  # noqa: E402
from messiah.simulator.replay import ParquetBarReplaySource  # noqa: E402
from messiah.strategy.futures.meta_labeler import build_meta_features  # noqa: E402

_DATA_DIR = Path("data") / "bars"
_SYNTHETIC_SYMBOL = "SYN5M"


def _synthetic_bars(n: int, horizon: Horizon, *, seed: int = 0) -> list[BarClosed]:
    """사인파+지터 기반 합성 봉 — 실제 시장 데이터가 아니다, 파이프라인 배관 검증 전용.
    순수 사인파(지터 없음)는 구간별로 값이 완전히 반복돼 여러 지표의 롤링 표준편차가
    0이 되는 퇴화 케이스가 잦고, FeatureEngine의 FeatureNaN 경고가 대량으로 찍혀
    스모크 출력이 묻힌다 — 작은 난수 지터로 그 퇴화를 피한다."""
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


async def _try_real_archive(args: argparse.Namespace) -> None:
    horizon = Horizon(args.horizon)
    source = ParquetBarReplaySource(Path(args.base_dir), args.symbol, horizons=[horizon])
    bars = source.load(date.fromisoformat(args.start), date.fromisoformat(args.end))
    print(f"[실제 아카이브] 입력 봉: {len(bars)}건 ({args.symbol}, {args.horizon})")
    if not bars:
        print("[실제 아카이브] 봉이 없음 — 건너뜀")
        return
    try:
        result = await train_formal_expert(
            bars,
            feature_set=args.feature_set,
            model_version=f"{args.horizon}_formal_smoke",
            atr_window=2,
            n_splits=2,
            n_search_trials=2,
            search_num_boost_round=10,
            final_num_boost_round=10,
            n_members=2,
            meta_num_boost_round=10,
        )
        print(
            f"[실제 아카이브] 성공(데이터가 매우 적어 흔치 않은 결과): "
            f"out-of-fold {result.n_oof_records}건"
        )
    except ValueError as exc:
        print(f"[실제 아카이브] 예상대로 실패(데이터 부족, 정상): {exc}")


async def _run_synthetic(args: argparse.Namespace) -> None:
    horizon = Horizon(args.horizon)
    bars = _synthetic_bars(args.synthetic_bars, horizon)
    print(f"\n[합성 데이터] 입력 봉: {len(bars)}건 — 실제 시장 데이터 아님, 배관 검증 전용")

    result = await train_formal_expert(
        bars,
        feature_set=args.feature_set,
        model_version=f"{args.horizon}_formal_smoke_synthetic",
        atr_window=args.atr_window,
        n_splits=args.n_splits,
        n_search_trials=args.n_search_trials,
        search_num_boost_round=args.search_num_boost_round,
        final_num_boost_round=args.final_num_boost_round,
        n_members=args.n_members,
        meta_num_boost_round=args.meta_num_boost_round,
    )

    print(f"탐색 최적 파라미터: {result.best_params}")
    print(f"out-of-fold 레코드 수: {result.n_oof_records}")
    print(
        f"Meta-Labeler 학습 신호 수: {result.n_meta_signals}, "
        f"선택된 임계값: {result.meta_labeler.threshold:.3f}"
    )
    print(
        f"앙상블 멤버 수: {result.expert.n_members}, "
        f"교정기 부착: {result.expert.calibrator is not None}"
    )

    # 예측 → Meta-Labeler 통과 판정까지 실제로 이어지는지 마지막 봉으로 한 번 더 시연.
    feature_vectors = await build_feature_vectors(bars, feature_set=args.feature_set)
    sample_bar, sample_fv = bars[-1], feature_vectors[-1]
    view = result.expert.predict(sample_fv)
    meta_features = build_meta_features(
        probs=np.array([view.p_down, view.p_flat, view.p_up]),
        ens_std=view.ens_std,
        feature_vector=sample_fv,
        bar=sample_bar,
    )
    passes = result.meta_labeler.passes(meta_features)
    print(
        f"마지막 봉 예측: p_up={view.p_up:.3f} p_flat={view.p_flat:.3f} "
        f"p_down={view.p_down:.3f} ens_std={view.ens_std:.4f} → Meta-Labeler 통과={passes}"
    )


async def main(args: argparse.Namespace) -> None:
    await _try_real_archive(args)
    await _run_synthetic(args)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MESSIAH 5m Expert 정식 파이프라인 스모크 실행")
    parser.add_argument("--symbol", default="A05608")
    parser.add_argument("--horizon", default="5m", choices=[h.value for h in Horizon])
    parser.add_argument("--start", default="2026-07-24")
    parser.add_argument("--end", default="2026-07-24")
    parser.add_argument("--base-dir", default=str(_DATA_DIR))
    parser.add_argument("--feature-set", default="v2026.07")
    parser.add_argument("--synthetic-bars", type=int, default=200)
    parser.add_argument("--atr-window", type=int, default=5)
    parser.add_argument("--n-splits", type=int, default=5)
    parser.add_argument("--n-search-trials", type=int, default=10)
    parser.add_argument("--search-num-boost-round", type=int, default=30)
    parser.add_argument("--final-num-boost-round", type=int, default=50)
    parser.add_argument("--n-members", type=int, default=5)
    parser.add_argument("--meta-num-boost-round", type=int, default=30)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main(_parse_args()))
