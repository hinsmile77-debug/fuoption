"""Regime AI(HMM + 규칙) 수동 스모크 — Master Plan Ver 2.0 §9 W20~21.

실제 아카이브가 30분봉 기준 1건뿐이라(A05608, 2026-07-24) `RegimeAI.fit()`이 요구하는
최소 관측치(2개 이상, 사실상 HMM이 의미 있으려면 수십~수백 개)를 만들 데이터가 없다
(5m Expert 정식 학습이 W17~19에 겪은 것과 같은 한계 — capability_matrix.md 알려진 갭).
이 스크립트는 두 단계로 확인한다:

1) 실제 아카이브로 먼저 시도 — 예상대로 실패, 정직하게 보고.
2) 합성(추세상승/횡보/고변동성 3구간 반복) 30분봉으로 전체 파이프라인(HMM 학습 → 상태
   명명 → 국면 판정 → 규칙 오버라이드 시연)이 실제로 동작하는지 시연한다 — **실제 시장
   데이터가 아니다**, 배관 검증 전용.

사용: python scripts/run_regime_ai_smoke.py --symbol A05608 --start 2026-07-24 --end 2026-07-24
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.core.messages import BarClosed, Horizon  # noqa: E402
from messiah.core.timeutil import KST  # noqa: E402
from messiah.simulator.replay import ParquetBarReplaySource  # noqa: E402
from messiah.strategy.regime.hmm_model import build_observations  # noqa: E402
from messiah.strategy.regime.naming import describe_labels  # noqa: E402
from messiah.strategy.regime.rules import RuleContext  # noqa: E402
from messiah.strategy.regime.service import RegimeAI  # noqa: E402

_DATA_DIR = Path("data") / "bars"
_SYNTHETIC_SYMBOL = "SYNREGIME"


def _synthetic_bars(cycles: int, *, seed: int = 0) -> list[BarClosed]:
    """추세상승(20봉) → 횡보(20봉) → 고변동성(15봉) 3구간을 `cycles`번 반복 — 실제 시장
    데이터가 아니다, HMM/규칙층이 구분할 수 있는 뚜렷한 구조를 주기 위한 합성 데이터."""
    rng = random.Random(seed)
    start = datetime(2026, 1, 5, 9, 0, tzinfo=KST)
    price = 1000.0
    out: list[BarClosed] = []
    idx = 0
    for _ in range(cycles):
        for _ in range(20):  # 추세상승
            price += 3 + rng.uniform(-1, 1)
            out.append(_bar(idx, start, price))
            idx += 1
        for _ in range(20):  # 횡보
            price += rng.uniform(-1, 1)
            out.append(_bar(idx, start, price))
            idx += 1
        for _ in range(15):  # 고변동성
            price += rng.uniform(-15, 15)
            price = max(price, 100.0)
            out.append(_bar(idx, start, price))
            idx += 1
    return out


def _bar(idx: int, start: datetime, price: float) -> BarClosed:
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


def _try_real_archive(args: argparse.Namespace) -> None:
    source = ParquetBarReplaySource(Path(args.base_dir), args.symbol, horizons=[Horizon.M30])
    bars = source.load(date.fromisoformat(args.start), date.fromisoformat(args.end))
    print(f"[실제 아카이브] 입력 30분봉: {len(bars)}건 ({args.symbol})")
    try:
        RegimeAI.fit(bars, n_states_candidates=(2, 3))
        print("[실제 아카이브] 성공(데이터가 매우 적어 흔치 않은 결과)")
    except ValueError as exc:
        print(f"[실제 아카이브] 예상대로 실패(데이터 부족, 정상): {exc}")


def _run_synthetic(args: argparse.Namespace) -> None:
    bars = _synthetic_bars(args.cycles)
    print(f"\n[합성 데이터] 입력 30분봉: {len(bars)}건 — 실제 시장 데이터 아님, 배관 검증 전용")

    regime_ai = RegimeAI.fit(bars, n_states_candidates=(4, 5, 6))
    print(f"HMM 상태 수(BIC 선정): {regime_ai.n_states}")

    state = regime_ai.classify(bars)
    print(
        f"통계층+명명층 판정: {state.regime.value} (확신도 {state.confidence:.3f}, "
        f"지속 {state.state_duration_bars}봉)"
    )
    print(f"전이확률: { {k: round(v, 3) for k, v in state.transition_prob.items()} }")

    # 규칙층 오버라이드 시연 — 변동성 극단(지금 유일하게 살아있는 규칙)이 통계층을 덮는지.
    overridden = regime_ai.classify(bars, rule_context=RuleContext(vol_ratio=100.0))
    print(
        f"규칙 오버라이드 시연(vol_ratio=100): {overridden.regime.value} "
        f"(확신도 {overridden.confidence:.3f}, 사유 {overridden.rule_override})"
    )

    # 사람 검수용 상태별 라벨링 요약(naming.py describe_labels) — 실제 봉 이력으로 재구성.
    observations, indices = build_observations(bars)
    states = regime_ai.hmm_model.predict_states(observations)
    print("\n상태별 사후 통계(사람 검수용):")
    print(describe_labels(regime_ai.labels, observations, indices, states))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MESSIAH Regime AI 스모크 실행")
    parser.add_argument("--symbol", default="A05608")
    parser.add_argument("--start", default="2026-07-24")
    parser.add_argument("--end", default="2026-07-24")
    parser.add_argument("--base-dir", default=str(_DATA_DIR))
    parser.add_argument("--cycles", type=int, default=8)
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    _try_real_archive(args)
    _run_synthetic(args)
