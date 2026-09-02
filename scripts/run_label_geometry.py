"""레이블 기하 진단 — 학습을 돌리기 전에 "이 레이블로 거래가 가능하긴 한가"를 본다.

`run_model_sweep.py`는 설정마다 모델을 학습해야 답이 나오지만(수십 분), 이 스크립트가 보는
결함은 레이블을 만든 순간 확정돼 있어 **학습 없이 몇 초 만에** 나온다. 배리어 폭
(`labeling._WIDTH_ATR_MULT`)을 건드릴 때 가장 먼저 돌릴 것.

사용:
    python scripts/run_label_geometry.py
    python scripts/run_label_geometry.py --horizons 5m,15m,30m --gate 0.20
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.backtest.harness import aggregate_to_horizon  # noqa: E402
from messiah.core.messages import Horizon  # noqa: E402
from messiah.data import backfill  # noqa: E402
from messiah.data.archiver import ParquetArchiver  # noqa: E402
from messiah.models.label_geometry import (  # noqa: E402
    LabelGeometry,
    check_horizon_ladder,
    regime_reachability,
    time_barrier_minutes,
)
from messiah.models.labeling import BARRIER_PARAMS, label_and_weight  # noqa: E402
from messiah.risk.cost_model import CostModel  # noqa: E402

# **두 정의를 마주 놓는 자리** — 레이블(위)과 판단 게이트·국면가중(아래)은 서로 다른 절에서
# 정해졌고, S의 정의상 곱해진다. 모듈이 직접 import하지 않고 여기서 잇는 이유는
# `label_geometry.DEFAULT_SCORE_GATE` 주석 참고.
from messiah.strategy.decision.meta_decision import (  # noqa: E402
    SCORE_THRESHOLD_BY_REGIME,
)
from messiah.strategy.futures.aggregator import REGIME_WEIGHTS  # noqa: E402

_SYMBOL = "K200MFC"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2025-12-12")
    p.add_argument("--end", default="2026-08-03")
    p.add_argument("--base-dir", default=str(Path("data") / "bars"))
    p.add_argument("--horizons", default="5m,15m,30m")
    p.add_argument("--gate", type=float, default=0.20, help="meta_decision의 우위 게이트")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    ladder = check_horizon_ladder(BARRIER_PARAMS)
    print("=" * 88)
    print("Horizon 사다리")
    print("=" * 88)
    print(f"{'Horizon':<10}{'시간배리어(분)':<18}{'봉 수':<10}{'width_atr_mult':<16}")
    for h, params in BARRIER_PARAMS.items():
        print(
            f"{h.value:<10}{time_barrier_minutes(h, params):<18}"
            f"{params.time_barrier_bars:<10}{params.width_atr_mult:<16}"
        )
    print(f"\n판정: {ladder.verdict}")

    archiver = ParquetArchiver(Path(args.base_dir))
    start = datetime.strptime(args.start, "%Y-%m-%d").date()  # noqa: DTZ007
    end = datetime.strptime(args.end, "%Y-%m-%d").date()  # noqa: DTZ007
    m1_bars, _ = backfill.load_continuous_series(
        archiver, backfill.front_month_days(start, end), symbol_out=_SYMBOL
    )
    if not m1_bars:
        print("연속 시계열이 비어 있다 — run_backfill.py를 먼저 실행할 것", file=sys.stderr)
        return 2
    print(f"\n연속 1분봉 {len(m1_bars)}봉  {start} ~ {end}")

    cost_model = CostModel()
    unhealthy = 0
    geometries = []
    for name in args.horizons.split(","):
        horizon = Horizon(name.strip())
        bars = aggregate_to_horizon(m1_bars, horizon)
        cost_ticks = cost_model.estimate_round_trip_from_bars(bars, qty=1).total_ticks
        labels = label_and_weight(bars, cost_ticks=cost_ticks)
        geom = LabelGeometry.build(labels, cost_ticks=cost_ticks, score_gate=args.gate)
        geometries.append(geom)
        print("\n" + "=" * 88)
        for line in geom.format_lines():
            print(line)
        if not geom.is_healthy:
            unhealthy += 1

    # 국면가중을 곱한 실효 천장 (2026-09-02) — 위 판정은 가중치 1을 가정한 것이라 국면별로
    # 두 방향으로 동시에 틀릴 수 있다(`RegimeReachability` docstring).
    print("\n" + "=" * 88)
    print("국면별 실효 천장 (레이블 천장 × REGIME_WEIGHTS)")
    print("=" * 88)
    closed = []
    for card in regime_reachability(geometries, REGIME_WEIGHTS, score_gate=args.gate):
        gate = SCORE_THRESHOLD_BY_REGIME.get(card.regime)
        if gate is not None and gate != args.gate:
            print(
                f"  ※ {card.regime.value}의 판단 엔진 게이트는 {gate:g}다 "
                f"(여기선 {args.gate:g}로 쟀다)"
            )
        for line in card.format_lines():
            print(line)
        if card.is_closed:
            closed.append(card.regime.value)
    if closed:
        print(
            f"\n{len(closed)}개 국면이 **산술적으로 닫혀 있다**: {', '.join(closed)}. "
            "모델 성능과 무관하게 그 국면에서는 판단이 안 나간다 — 의도한 것이면 "
            "`SCORE_THRESHOLD_BY_REGIME`에 그렇게 적고, 아니면 배리어 폭이나 국면가중을 고칠 것."
        )

    print("\n" + "=" * 88)
    if unhealthy:
        print(
            f"{unhealthy}개 Horizon이 기하 검사에서 막혔다 — 모델을 학습하기 전에 이걸 먼저 "
            f"풀 것. 여기서 막히면 모델이 아무리 좋아도 판단 엔진을 통과하지 못한다."
        )
    else:
        print("전 Horizon 통과 — 단, 이건 '거래가 가능하다'일 뿐 '우위가 있다'가 아니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
