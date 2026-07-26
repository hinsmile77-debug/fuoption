"""Cost Model v1 + 5m Expert 프로토타입 + Validator 골격 수동 스모크 — Master Plan Ver 2.0
§9 W14~16.

실제 아카이브된 완성봉으로 Trainer(models/trainer.py) → HorizonExpert(strategy/futures/
expert.py) → Validator(models/validator.py) 전체 배선이 실제로 도는지 확인하는 진입점
(scripts/run_replay.py·run_labeling_smoke.py와 같은 패턴).

**성과 관문(Sharpe·MDD·창별 일관성)은 이 스크립트로 시연하지 않는다** — 실제 walk-forward
백테스트 루프가 아직 없고(Validator 모듈 docstring 참고), 무엇보다 지금 아카이브가 하루치뿐
이라 의미 있는 성과 시계열 자체를 만들 수 없다. **교정(calibration) 관문도 생략**한다 —
홀드아웃 데이터가 없어 훈련 데이터로 평가하면 자기예측 대조가 돼 의미가 없다. 홀드아웃이
필요 없는 3개 관문(Feature 의존도·추론지연·직렬화 왕복)만 실행해 배선을 확인한다.

사용: python scripts/run_expert_training_smoke.py --symbol A05608 --horizon 5m
                                                    --start 2026-07-24 --end 2026-07-24
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.core.messages import Horizon  # noqa: E402
from messiah.models.trainer import build_feature_vectors, train_prototype_expert  # noqa: E402
from messiah.models.validator import Validator  # noqa: E402
from messiah.risk.cost_model import CostModel  # noqa: E402
from messiah.simulator.replay import ParquetBarReplaySource  # noqa: E402

_DATA_DIR = Path("data") / "bars"


async def main(args: argparse.Namespace) -> None:
    horizon = Horizon(args.horizon)
    source = ParquetBarReplaySource(Path(args.base_dir), args.symbol, horizons=[horizon])
    bars = source.load(date.fromisoformat(args.start), date.fromisoformat(args.end))
    if not bars:
        raise SystemExit(
            f"재생할 봉이 없음 — {args.base_dir}/{args.symbol}/{args.horizon}/"
            f"{{{args.start}..{args.end}}}.parquet 확인"
        )
    print(f"입력 봉: {len(bars)}건 ({args.symbol}, {args.horizon}, {args.start}~{args.end})")

    cost_model = CostModel()
    expert = await train_prototype_expert(
        bars,
        feature_set=args.feature_set,
        model_version=f"{args.horizon}_prototype1_smoke",
        cost_model=cost_model,
        atr_window=args.atr_window,
    )
    print(f"학습 완료: horizon={expert.horizon.value} model_version={expert.model_version}")
    print(f"Feature 개수: {len(expert.feature_names)}")

    feature_vectors = await build_feature_vectors(bars, feature_set=args.feature_set)
    views = [expert.predict(fv) for fv in feature_vectors]
    argmax_dist = Counter(
        "up"
        if v.p_up >= v.p_flat and v.p_up >= v.p_down
        else "down"
        if v.p_down >= v.p_flat
        else "flat"
        for v in views
    )
    print(f"예측 분포(argmax, 전 구간): {dict(argmax_dist)}")
    # 교정(calibration) 관문은 여기서 시연하지 않는다 — 홀드아웃 데이터가 없어 훈련 데이터로
    # 평가하면 자기예측 대조가 돼 의미가 없다(스크립트 docstring 참고). 나머지 모델 검사
    # 3개(Feature 의존도·추론지연·직렬화 왕복)는 홀드아웃이 필요 없어 그대로 시연한다.

    validator = Validator()
    tmp_path = Path(args.base_dir).parent / "_smoke_tmp"
    tmp_path.mkdir(parents=True, exist_ok=True)
    sample = feature_vectors[-1]

    gates = [
        validator.validate_feature_dependency(expert),
        validator.validate_latency(expert, sample, n_calls=args.latency_calls),
        validator.validate_serialization(expert, sample, tmp_path),
    ]
    print("\nValidator 골격 관문 결과(성과 관문 제외 — 스크립트 docstring 참고):")
    for gate in gates:
        status = "PASS" if gate.passed else "FAIL"
        print(f"  [{status}] {gate.name}: {gate.value:.4f} (기준 {gate.threshold}) {gate.detail}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MESSIAH Cost Model/Expert/Validator 스모크 실행")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--horizon", required=True, choices=[h.value for h in Horizon])
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--base-dir", default=str(_DATA_DIR))
    parser.add_argument("--feature-set", default="v2026.07")
    parser.add_argument(
        "--atr-window",
        type=int,
        default=2,
        help="실제 아카이브가 하루치뿐이라 기본값을 작게 둠(프로덕션 기본값은 14)",
    )
    parser.add_argument("--latency-calls", type=int, default=200)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main(_parse_args()))
