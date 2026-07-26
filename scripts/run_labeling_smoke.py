"""Triple Barrier + uniqueness + Purged K-Fold 수동 스모크 실행 — Master Plan Ver 2.0 §9 W12~13.

실제 아카이브된 완성봉(`data/bars/{symbol}/{horizon}/{date}.parquet`)에 레이블링·고유도·
Purged K-Fold 배선이 실제로 도는지 확인하는 진입점(scripts/run_replay.py와 같은 패턴).

WalkForwardSplitter(달력 월 단위 롤링)는 지금 아카이브가 하루치뿐이라 의미 있게 시연할 수
없다 — 정확성은 tests/models/test_cv.py의 합성(다개월) 데이터 기반 known-value 테스트가
담당한다. 이 스크립트는 실제 시장 데이터가 레이블링·PurgedKFold 배선을 깨지 않고 통과하는지만
확인한다.

사용: python scripts/run_labeling_smoke.py --symbol A05608 --horizon 1m
                                            --start 2026-07-24 --end 2026-07-24
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.core.messages import Horizon  # noqa: E402
from messiah.models.cv import PurgedKFold  # noqa: E402
from messiah.models.labeling import label_and_weight  # noqa: E402
from messiah.simulator.replay import ParquetBarReplaySource  # noqa: E402

_DATA_DIR = Path("data") / "bars"


def main(args: argparse.Namespace) -> None:
    horizon = Horizon(args.horizon)
    source = ParquetBarReplaySource(Path(args.base_dir), args.symbol, horizons=[horizon])
    bars = source.load(date.fromisoformat(args.start), date.fromisoformat(args.end))
    if not bars:
        raise SystemExit(
            f"재생할 봉이 없음 — {args.base_dir}/{args.symbol}/{args.horizon}/"
            f"{{{args.start}..{args.end}}}.parquet 확인"
        )
    print(f"입력 봉: {len(bars)}건 ({args.symbol}, {args.horizon}, {args.start}~{args.end})")

    labels = label_and_weight(bars, atr_window=args.atr_window, cost_ticks=args.cost_ticks)
    if not labels:
        raise SystemExit(
            f"레이블 0건 — atr_window({args.atr_window})+시간배리어 봉수 대비 데이터가 부족함"
        )

    label_counts = Counter(lbl.label for lbl in labels)
    demoted = sum(1 for lbl in labels if lbl.cost_demoted)
    weights = [lbl.weight for lbl in labels]
    print(f"레이블 {len(labels)}건: {dict(sorted(label_counts.items()))} (비용강등 {demoted}건)")
    print(
        f"고유도 가중치: 평균={statistics.fmean(weights):.3f} "
        f"최소={min(weights):.3f} 최대={max(weights):.3f}"
    )

    n_splits = min(args.n_splits, len(labels) // 2) or 1
    if n_splits >= 2:
        events = [(lbl.t_start, lbl.t_end) for lbl in labels]
        fold_sizes = [
            (len(train), len(test)) for train, test in PurgedKFold(n_splits=n_splits).split(events)
        ]
        print(f"PurgedKFold({n_splits}-fold) train/test 크기: {fold_sizes}")
    else:
        print(f"레이블이 {len(labels)}건뿐이라 PurgedKFold 시연 생략(최소 2개 폴드 필요)")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MESSIAH Triple Barrier/CV 스모크 실행")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--horizon", required=True, choices=[h.value for h in Horizon])
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--base-dir", default=str(_DATA_DIR))
    parser.add_argument("--atr-window", type=int, default=14)
    parser.add_argument("--cost-ticks", type=int, default=0)
    parser.add_argument("--n-splits", type=int, default=5)
    return parser.parse_args()


if __name__ == "__main__":
    main(_parse_args())
