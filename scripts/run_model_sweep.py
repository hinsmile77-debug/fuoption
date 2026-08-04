"""모델 설정 스윕 — Horizon × 탐색예산 × 임계값선택 × Regime 결선을 한 번에 비교한다.

## 왜 G1(walk-forward)이 아니라 단일 분할인가

2026-08-04 G1 실행은 "판단 1366건, 전부 NO_TRADE"로 끝났고 원인은 Meta-Labeler가 검증
구간을 100% 거부한 것이었다(`models/threshold_report.py`). 그 상태에서 창마다 재학습하는
walk-forward를 정상 탐색예산으로 돌리면 수 시간이 걸리는데, **정작 알아야 할 것은 창별
성과가 아니라 "어떤 설정에서 거래가 나기는 하는가"**다. 그래서 이 스크립트는 단일
train/test 분할로 축을 훑고, 살아남은 설정만 `run_g1_walk_forward.py`로 넘긴다.

## 보는 것

설정마다 세 가지를 낸다 — 셋이 서로 다른 질문에 답한다:

  1. **임계값 진단**(`ThresholdReport`) — 임계값이 추론에서 도달 가능한 높이인가.
     "모델에 우위가 없다"와 "임계값이 과적합됐다"는 증상이 똑같이 무거래라 이게 없으면
     구분이 안 된다.
  2. **Meta 통과율** — 실제로 몇 %가 게이트를 통과하는가.
  3. **판단 결과** — 통과한 신호가 실제로 NO_TRADE가 아닌 판단이 되는가. 여기서
     `MetaDecisionEngine`을 **그대로 쓴다**. 손으로 `abs(score) > 0` 같은 근사를 두면
     파이프라인이 실제로 요구하는 것과 달라진다 — 2026-08-04에 정확히 그 실수를 했다:
     스윕은 "신호 87건"이라 했는데 같은 설정의 G1은 무거래였고, 원인은 엔진의 게이트 두 개
     (`Regime.UNKNOWN`이면 즉시 NO_TRADE, `|S| < 0.20`이면 우위 부족)를 스윕이 안 봤기
     때문이다. 어느 게이트가 막았는지도 사유별로 센다.

**성과(P&L)는 재지 않는다.** 단일 분할 수익률은 표본이 하나라 성적으로 읽으면 안 되고,
그 판단은 G1의 몫이다.

사용:
    python scripts/run_model_sweep.py                       # 기본 스윕
    python scripts/run_model_sweep.py --horizons 5m,15m,30m
    python scripts/run_model_sweep.py --budgets shallow,normal
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np  # noqa: E402

from messiah.backtest.harness import aggregate_to_horizon  # noqa: E402
from messiah.core.messages import (  # noqa: E402
    Horizon,
    Regime,
    RegimeState,
    Side,
    bar_confirm_time,
)
from messiah.data import backfill  # noqa: E402
from messiah.data.archiver import ParquetArchiver  # noqa: E402
from messiah.models.threshold_report import ThresholdReport  # noqa: E402
from messiah.models.trainer import build_feature_vectors, train_formal_expert  # noqa: E402
from messiah.ops import session_guard  # noqa: E402
from messiah.strategy.decision.meta_decision import MetaDecisionEngine  # noqa: E402
from messiah.strategy.futures.aggregator import Aggregator  # noqa: E402
from messiah.strategy.futures.meta_labeler import (  # noqa: E402
    build_meta_features_from_feature_vector,
)
from messiah.strategy.regime.service import RegimeAI  # noqa: E402

_DATA_DIR = Path("data") / "bars"
_SYMBOL = "K200MFC"
_FEATURE_SET = "v2026.07"

# 탐색 예산 — "shallow"는 2026-08-04 G1 실행이 쓴 값(런타임 우선), "normal"은
# `train_formal_expert()`의 프로덕션 기본값에 가깝다. 그날 "우위 없음" 판정이 예산 탓인지
# 아닌지가 이 축의 질문이다.
_BUDGETS: dict[str, dict[str, int]] = {
    "shallow": {
        "n_search_trials": 3,
        "search_num_boost_round": 15,
        "final_num_boost_round": 25,
        "n_members": 3,
        "meta_num_boost_round": 15,
    },
    "normal": {
        "n_search_trials": 20,
        "search_num_boost_round": 100,
        "final_num_boost_round": 100,
        "n_members": 5,
        "meta_num_boost_round": 50,
    },
}


@dataclass
class SweepRow:
    horizon: str
    budget: str
    threshold_mode: str
    regime: str
    n_train: int
    n_test: int
    threshold: float
    selection_reach: float
    inference_reach: float
    headroom: float
    meta_pass_rate: float
    non_no_trade: int
    abs_score_p90: float
    blocked_by: dict[str, int]
    degenerate: bool
    verdict: str


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2025-12-12")
    p.add_argument("--end", default="2026-08-03")
    p.add_argument("--base-dir", default=str(_DATA_DIR))
    p.add_argument("--horizons", default="5m,15m,30m")
    p.add_argument("--budgets", default="shallow,normal")
    p.add_argument(
        "--threshold-modes",
        default="oof,insample",
        help="oof=out-of-fold 확률로 임계값 선택(2026-08-04 수정본), insample=종전 동작",
    )
    p.add_argument("--regime", default="both", choices=["off", "on", "both"])
    p.add_argument("--test-fraction", type=float, default=0.2)
    p.add_argument(
        "--min-support",
        type=float,
        default=0.05,
        help="임계값 후보가 남겨야 할 최소 신호 비율(0이면 하한 없음 — 종전 동작)",
    )
    p.add_argument("--out", default="logs/model_sweep_20260804.json")
    session_guard.add_force_intraday_argument(p)
    return p.parse_args()


async def _evaluate(
    *,
    horizon: Horizon,
    budget_name: str,
    threshold_mode: str,
    min_support: float,
    regime_states: list[tuple[datetime, RegimeState]] | None,
    train_bars,
    test_bars,
) -> SweepRow:
    budget = _BUDGETS[budget_name]
    training = await train_formal_expert(
        train_bars,
        feature_set=_FEATURE_SET,
        model_version=f"sweep-{horizon.value}-{budget_name}",
        meta_threshold_splits=5 if threshold_mode == "oof" else 1,
        meta_min_support_fraction=min_support,
        **budget,
    )
    meta = training.meta_labeler

    fvs = await build_feature_vectors(test_bars, feature_set=_FEATURE_SET)
    aggregator = Aggregator()
    inference_probs: list[float] = []
    passed = 0
    non_no_trade = 0
    regime_state = RegimeState(
        symbol=_SYMBOL, regime=Regime.UNKNOWN, confidence=0.0, state_duration_bars=0
    )

    engine = MetaDecisionEngine()
    blocked: dict[str, int] = {}
    scores: list[float] = []
    cursor = 0
    for fv in fvs:
        view = training.expert.predict(fv)
        mf = build_meta_features_from_feature_vector(
            np.array([view.p_down, view.p_flat, view.p_up]), view.ens_std, fv
        )
        prob = meta.predict_pass_probability(mf)
        inference_probs.append(prob)
        view = view.model_copy(update={"meta_passed": prob >= meta.threshold})
        if view.meta_passed:
            passed += 1

        # 국면은 **30m 구동 봉**에서만 나온다(`RegimeRuntime`의 driving_horizon) — 이 Horizon의
        # 봉으로 classify()를 부르면 관측 윈도의 의미가 달라진다. 시간순으로 앞서 확정된
        # 가장 최근 국면을 집어 쓴다(미래 참조 없음: valid_until <= 이 피처의 valid_until).
        if regime_states is not None and fv.valid_until is not None:
            while (
                cursor + 1 < len(regime_states) and regime_states[cursor + 1][0] <= fv.valid_until
            ):
                cursor += 1
            if regime_states and regime_states[cursor][0] <= fv.valid_until:
                regime_state = regime_states[cursor][1]

        aggregate = aggregator.compute(_SYMBOL, {horizon: view}, regime_state, as_of=fv.valid_until)
        scores.append(aggregate.score)
        intent = engine.decide(aggregate, kill_active=False)
        if intent.side is Side.NO_TRADE:
            # 사유 머리글자(①~⑤)로 어느 게이트가 막았는지 센다 — "무거래"만 알면 원인을
            # 못 가른다(`meta_decision.py`의 `_no_trade` rationale 규약).
            reason = intent.rationale[:1] if intent.rationale else "?"
            blocked[reason] = blocked.get(reason, 0) + 1
        else:
            non_no_trade += 1

    report = ThresholdReport.build(
        threshold=meta.threshold,
        selection_probabilities=training.threshold_selection_probabilities,
        inference_probabilities=inference_probs,
    )
    return SweepRow(
        horizon=horizon.value,
        budget=budget_name,
        threshold_mode=threshold_mode,
        regime="on" if regime_states is not None else "off",
        n_train=len(train_bars),
        n_test=len(test_bars),
        threshold=report.threshold,
        selection_reach=report.selection_reach_rate,
        inference_reach=report.inference_reach_rate,
        headroom=report.headroom,
        meta_pass_rate=passed / max(1, len(fvs)),
        non_no_trade=non_no_trade,
        abs_score_p90=(
            sorted(abs(s) for s in scores)[int(0.9 * (len(scores) - 1))] if scores else 0.0
        ),
        blocked_by=blocked,
        degenerate=report.is_degenerate,
        verdict=report.verdict,
    )


def _report_regime_axis(rows: list[SweepRow]) -> None:
    """regime on/off가 실제로 결과를 바꿨는지 — 안 바꿨으면 그 이유를 말한다.

    **regime은 두 곳에서 작용하고, 크기가 전혀 다르다**(2026-08-04에 이걸 잘못 읽었다):

    1. `Aggregator`의 국면별 가중치 — Expert가 한 Horizon뿐이면 가중평균이 그 하나로
       정규화되어 **상쇄된다**. 여기만 보면 regime이 무의미해 보인다.
    2. `MetaDecisionEngine`의 게이트 ② — `Regime.UNKNOWN`은 `_EVENT_LIKE_REGIMES`라
       **즉시 NO_TRADE**다. 즉 regime 미결선이면 거래가 구조적으로 불가능하다.

    처음엔 1만 보고 "이 축은 측정 불가"라고 적었는데, 판단 엔진을 실제로 태우자
    regime=off는 검증 표본 **전부**가 게이트 ②에서 막혔다. 근사로 재면 이렇게 틀린다.
    """
    if len({r.regime for r in rows}) < 2:
        return
    groups: dict[tuple[str, str, str], list[SweepRow]] = {}
    for r in rows:
        groups.setdefault((r.horizon, r.budget, r.threshold_mode), []).append(r)
    comparable = [g for g in groups.values() if len(g) > 1]
    if comparable and all(len({x.non_no_trade for x in g}) == 1 for g in comparable):
        print(
            "\n참고: regime on/off가 모든 설정에서 동일한 결과를 냈다. 단일 Horizon에서는"
            " 집계 가중치가 상쇄되기 때문인데, 판단 엔진의 게이트 ②(UNKNOWN=즉시 NO_TRADE)"
            "까지 태웠는데도 같다면 그 게이트를 안 타고 있다는 뜻이므로 배선을 확인할 것."
        )


async def main() -> int:
    args = _parse_args()
    session_guard.refuse_if_regular_session("모델 스윕", force=args.force_intraday)
    archiver = ParquetArchiver(Path(args.base_dir))
    start = datetime.strptime(args.start, "%Y-%m-%d").date()  # noqa: DTZ007
    end = datetime.strptime(args.end, "%Y-%m-%d").date()  # noqa: DTZ007
    segments = backfill.front_month_days(start, end)
    m1_bars, _ = backfill.load_continuous_series(archiver, segments, symbol_out=_SYMBOL)
    if not m1_bars:
        print("연속 시계열이 비어 있다 — run_backfill.py를 먼저 실행할 것", file=sys.stderr)
        return 2
    print(f"연속 시계열 {len(m1_bars)}봉  {start} ~ {end}")

    regime_states: list[tuple[datetime, RegimeState]] | None = None
    if args.regime in ("on", "both"):
        regime_bars = aggregate_to_horizon(m1_bars, Horizon.M30)
        split30 = int(len(regime_bars) * (1 - args.test_fraction))
        print(f"RegimeAI 학습 — 30m {split30}봉(학습 구간만, 검증 구간 미포함)")
        regime_ai = RegimeAI.fit(regime_bars[:split30])
        print(f"  상태 수 {regime_ai.n_states} · 명명 {regime_ai.labels}")
        # 검증 구간의 국면 시퀀스를 미리 계산한다 — 봉 하나씩 늘려가며 classify()를 불러
        # 실시간 경로(`RegimeRuntime.handle_bar`)와 같은 순서로 만든다(미래 참조 없음).
        regime_states = []
        for i in range(split30, len(regime_bars)):
            state = regime_ai.classify(regime_bars[: i + 1])
            regime_states.append((bar_confirm_time(regime_bars[i]), state))
        kinds = Counter(s.regime.value for _, s in regime_states)
        print(f"  검증 구간 국면 분포: {dict(kinds)}")

    regimes = {
        "off": [None],
        "on": [regime_states],
        "both": [None, regime_states],
    }[args.regime]
    rows: list[SweepRow] = []

    for horizon_name in args.horizons.split(","):
        horizon = Horizon(horizon_name.strip())
        bars = aggregate_to_horizon(m1_bars, horizon)
        split = int(len(bars) * (1 - args.test_fraction))
        train_bars, test_bars = bars[:split], bars[split:]
        print(f"\n=== {horizon.value}: 학습 {len(train_bars)}봉 / 검증 {len(test_bars)}봉 ===")

        for budget_name in args.budgets.split(","):
            for mode in args.threshold_modes.split(","):
                for ai in regimes:
                    label = (
                        f"{horizon.value}/{budget_name.strip()}/{mode.strip()}/"
                        f"regime={'on' if ai is not None else 'off'}"
                    )
                    print(f"  {label} 학습 중...", flush=True)
                    try:
                        row = await _evaluate(
                            horizon=horizon,
                            budget_name=budget_name.strip(),
                            threshold_mode=mode.strip(),
                            min_support=args.min_support,
                            regime_states=ai,
                            train_bars=train_bars,
                            test_bars=test_bars,
                        )
                    except ValueError as exc:
                        print(f"    실패(정직 보고): {exc}")
                        continue
                    rows.append(row)
                    print(
                        f"    임계 {row.threshold:.3f}  선택도달 {row.selection_reach:.1%}  "
                        f"추론도달 {row.inference_reach:.1%}  헤드룸 {row.headroom:+.4f}  "
                        f"거래신호 {row.non_no_trade}  |S|p90={row.abs_score_p90:.3f}  "
                        f"차단={row.blocked_by}"
                    )

    print("\n" + "=" * 108)
    print(
        f"{'Horizon':<8}{'예산':<9}{'임계선택':<10}{'Regime':<8}{'임계':<8}"
        f"{'선택도달':<10}{'추론도달':<10}{'헤드룸':<10}{'거래신호':<9}{'비고':<8}"
    )
    print("-" * 108)
    for r in sorted(rows, key=lambda x: (x.degenerate, -x.inference_reach)):
        note = "게이트무력" if r.degenerate else ""
        print(
            f"{r.horizon:<8}{r.budget:<9}{r.threshold_mode:<10}{r.regime:<8}"
            f"{r.threshold:<8.3f}{r.selection_reach:<10.1%}{r.inference_reach:<10.1%}"
            f"{r.headroom:<+10.4f}{r.non_no_trade:<9}{note:<8}"
        )

    tradable = [r for r in rows if r.non_no_trade > 0]
    healthy = [r for r in tradable if not r.degenerate]
    print(
        f"\n거래 신호가 나온 설정: {len(tradable)}/{len(rows)}  (그중 게이트 정상: {len(healthy)})"
    )

    degenerate = [r for r in tradable if r.degenerate]
    if degenerate:
        print(
            f"\n게이트 무력화 {len(degenerate)}건 — 임계값이 0에 붙어 전부 통과한다. "
            f"신호 수가 많은 건 성과가 아니라 **거르지 않은 것**이라 후보에서 뺀다:"
        )
        for r in degenerate:
            print(f"  {r.horizon}/{r.threshold_mode}/regime={r.regime} — {r.non_no_trade}건")

    _report_regime_axis(rows)

    # `insample`은 **결함이 확인된 경로**다(임계값을 자기 학습 행 예측으로 고름,
    # `models/trainer._meta_selection_probabilities`) — 비교 축으로만 두고 권장에서는 뺀다.
    # 어떤 지표가 우연히 그쪽을 가리켜도 그건 지표의 결함이지 그 경로의 장점이 아니다.
    candidates = [r for r in healthy if r.threshold_mode == "oof"] or healthy
    if candidates:
        # 신호 수로 고르지 않는다 — 그러면 게이트가 가장 헐거운 설정이 항상 이긴다.
        # 퇴화가 아닌 설정 중 **추론 도달률이 가장 높은** 것이 "임계값이 새 데이터로도
        # 살아남으면서 선별은 유지한" 설정이다.
        best = max(candidates, key=lambda r: r.inference_reach)
        print(
            f"\n권장(임계값이 가장 잘 옮겨간 설정): {best.horizon}/{best.budget}/"
            f"{best.threshold_mode}/regime={best.regime}"
        )
        print(
            f"  임계 {best.threshold:.3f} · 선택도달 {best.selection_reach:.1%} → "
            f"추론도달 {best.inference_reach:.1%} · 신호 {best.non_no_trade}건"
        )
        print("→ 이 설정으로 run_g1_walk_forward.py를 돌릴 것 (성과 판정은 거기서)")
    else:
        print("→ 게이트가 살아있는 설정이 없다. 임계값 문제가 아니라면 모델 자체의 문제다.")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps([r.__dict__ for r in rows], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n결과 저장: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
