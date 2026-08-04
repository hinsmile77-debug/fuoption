"""피처 품질 관문 실행 — Ver 1.4 §3을 실제 아카이브에 적용한다 (2026-08-04 신설, F0-3).

    python scripts/run_feature_gate.py --start 2025-12-12 --end 2026-08-04 --horizons 5m,15m,30m

## 무엇을 답하는가

"지금 모델에 들어가는 피처 중 **몇 개가 실제로 쓸모 있는가**". 이 질문이 코드로 답해진 적이
한 번도 없다 — 121개 전부가 검정 없이 학습에 들어갔고, 그 결과 `px_ema_cross_60`처럼
프로덕션에서 항상 NaN인 피처가 몇 달을 살아남았다.

**피처를 늘리기 전에 먼저 돌린다.** 지금 121개의 통과율을 모르는 채로 250개로 늘리면,
나중에 성능이 안 오를 때 원인이 새 피처인지 기존 잡음인지 구분할 수 없다.

## 겹침 보정은 자동이다

`models/labeling.BARRIER_PARAMS[horizon].time_barrier_bars`를 그대로 읽어 `run_gate()`에
넘긴다 — 사람이 숫자를 옮겨 적지 않는다. 2026-08-04에 3봉 겹침을 손으로 보정하다 한 번
빠뜨려 전 레이블 변형이 유의해 보였던 전례가 있고, 그 보정은 코드가 해야 한다.

## 학습 데이터와 정확히 같은 경로를 쓴다

`build_feature_vectors()`(운영과 동일한 `FeatureEngine`) → `label_and_weight()` →
`build_training_data()`. 관문이 학습과 다른 행렬을 보면 그 판정은 학습에 대해 아무 말도
못 한다. 그래서 별도 재현 코드를 쓰지 않고 `models/trainer.py`의 함수를 그대로 부른다.

## 생존 검정은 대개 건너뛴다

Ver 1.4 §3 ③은 Walk-Forward 3창 이상을 요구하는데 이 프로젝트는 아직 G1 창이 하나다.
`--survival-windows`로 창을 나눠 시도할 수는 있지만 기본값은 0(미실행)이다 — 데이터 부족을
피처 결함으로 오역하지 않기 위해서다(`features/gate.screen_survival` docstring).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np  # noqa: E402

from messiah.backtest.harness import aggregate_to_horizon  # noqa: E402
from messiah.core.messages import Horizon  # noqa: E402
from messiah.data import backfill  # noqa: E402
from messiah.data.archiver import ParquetArchiver  # noqa: E402
from messiah.features import gate, sidecar  # noqa: E402
from messiah.features import spec as feature_spec  # noqa: E402
from messiah.models.labeling import (  # noqa: E402
    BARRIER_PARAMS,
    forward_realized_volatility,
    trailing_realized_volatility,
)
from messiah.models.trainer import build_feature_vectors, build_training_data  # noqa: E402
from messiah.risk.cost_model import CostModel  # noqa: E402
from messiah.strategy.futures.expert import HorizonExpert  # noqa: E402
from messiah.strategy.options.vol_forecast import (  # noqa: E402
    DEFAULT_MONTHLY_WINDOW,
    DEFAULT_WEEKLY_WINDOW,
)

_DATA_DIR = Path("data") / "bars"
_SYMBOL = "K200MFC"  # 후방조정 근월물 연속물 라벨 (`run_model_sweep.py`와 동일)
_OUT_PATH = Path("logs") / "feature_gate.json"


def _baseline_columns(bars: list, *, horizon_bars: int, kind: str) -> np.ndarray | None:
    """변동성 지속성 기준선 — "직전에도 컸다"를 통제변수로 세운다.

    `rv`  : 직전 `horizon_bars`봉 RV 하나. **기본값이다** — 기준선이 하나면 순위 부분상관이
            단조 관계 전체를 정확히 제거한다(`gate.partial_spearman` 참고).
    `har` : Corsi(2009) HAR 구조를 봉 공간으로 옮긴 3성분(단기 N · 중기 5N · 장기 22N).
            창 배수는 `vol_forecast`의 상수를 그대로 읽는다 — 두 곳이 갈리지 않게.
            **주의**: 다변량 순위 부분상관은 기준선의 순위-선형 성분만 제거하므로 잔여
            누수가 있고, 그 방향이 피처에 유리하다(`test_rank_partialling_only_removes...`).

    적합하지 않는다 — 통제변수를 그대로 넣을 뿐이다. HAR-RV를 전 구간에 적합하면 in-sample
    과적합이 잔차에 섞여, 피처가 "과적합이 못 맞힌 부분"을 맞히는지를 재게 된다.
    """
    if kind == "none":
        return None
    windows = [horizon_bars]
    if kind == "har":
        windows += [horizon_bars * DEFAULT_WEEKLY_WINDOW, horizon_bars * DEFAULT_MONTHLY_WINDOW]
    columns = []
    for window in windows:
        values = trailing_realized_volatility(bars, horizon_bars=window)
        columns.append([np.nan if v is None else v for v in values])
    return np.column_stack(columns)


def _volatility_target(
    bars: list,
    vectors: list,
    feature_names: list[str],
    *,
    horizon_bars: int,
    tie_match: bool,
    baselines: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    """변동성 타깃용 (X, y) — 방향 경로와 **같은 열 순서·같은 결측 규약**으로 조립한다.

    `build_training_data()`를 못 쓰는 이유는 그게 Triple Barrier 레이블에 묶여 있어서다.
    행렬 자체는 같은 함수(`HorizonExpert.feature_row`)로 만들어 두 경로가 갈리지 않게 한다.

    `tie_match`가 참이면 연속 RV를 **3분위 계급(-1/0/+1)으로 이산화**한다. 비교의 공정성
    때문이다: 방향 레이블은 값이 셋뿐이라 동순위가 많고, Spearman은 동순위가 많을수록 달성
    가능한 |ρ| 상한이 낮아진다. 연속 타깃과 그대로 견주면 변동성 쪽 IC가 **기계적으로**
    커 보인다. 계급 수를 맞춘 통제군이 있어야 "정말 커졌는가"를 말할 수 있다.
    """
    labels = forward_realized_volatility(bars, horizon_bars=horizon_bars)
    keep = [i for i, v in enumerate(labels) if v is not None]
    if not keep:
        return np.empty((0, len(feature_names))), np.empty(0), None

    rows = [HorizonExpert.feature_row(vectors[i].values, feature_names) for i in keep]
    values = np.array([labels[i] for i in keep], dtype=float)
    if tie_match:
        lo, hi = np.quantile(values, [1 / 3, 2 / 3])
        values = np.where(values <= lo, -1.0, np.where(values >= hi, 1.0, 0.0))
    # 기준선은 **레이블과 같은 행**만 남긴다 — 어긋나면 통제가 다른 시점을 보게 된다.
    kept_baselines = None if baselines is None else baselines[keep, :]
    return np.array(rows, dtype=float), values, kept_baselines


async def _gate_one_horizon(
    m1_bars: list,
    horizon: Horizon,
    feature_set: str,
    *,
    args: argparse.Namespace,
    sidecars: dict[str, object],
) -> dict[str, object] | None:
    bars = aggregate_to_horizon(m1_bars, horizon)
    if len(bars) < args.min_bars:
        print(f"  {horizon.value}: {len(bars)}봉 — {args.min_bars}봉 미만이라 건너뜀")
        return None

    vectors = await build_feature_vectors(bars, feature_set=feature_set, sidecars=sidecars)
    # 열 순서는 방향/변동성 경로가 **반드시 같아야** 한다 — 두 실행의 IC를 피처 이름으로
    # 대조하기 때문이다. 방향 경로의 정본(`build_training_data`)을 먼저 돌려 이름을 얻는다.
    names, x, y, _weight = build_training_data(bars, vectors, cost_model=CostModel())
    if x.size == 0:
        print(f"  {horizon.value}: 레이블 정렬 후 표본 0 — 건너뜀")
        return None

    overlap = BARRIER_PARAMS[horizon].time_barrier_bars
    baselines = None
    if args.label == "volatility":
        # 창 길이를 방향 레이블의 시간배리어와 **같은 봉 수**로 맞춘다 — 예측 구간이 다르면
        # 두 축의 IC를 견줄 수 없다.
        baselines = _baseline_columns(bars, horizon_bars=overlap, kind=args.baseline)
        x, y, baselines = _volatility_target(
            bars,
            vectors,
            names,
            horizon_bars=overlap,
            tie_match=not args.no_tie_match,
            baselines=baselines,
        )
        if x.size == 0:
            print(f"  {horizon.value}: 변동성 레이블 0건 — 건너뜀")
            return None
        # 피처 열을 추가 통제변수로 — 종가 기반 RV는 비효율적 추정량이라(Parkinson 1980
        # 이래 알려진 사실), 그것만 통제하면 "OHLC로 현재 변동성을 더 잘 잰다"가 증분처럼
        # 보인다. 그건 새 정보가 아니라 추정 효율이다.
        extra = [n.strip() for n in args.baseline_features.split(",") if n.strip()]
        if extra:
            missing = [n for n in extra if n not in names]
            if missing:
                print(f"  ** 기준선 피처를 못 찾음: {missing}", file=sys.stderr)
                return None
            columns = [x[:, names.index(n)] for n in extra]
            stacked = np.column_stack(columns)
            baselines = stacked if baselines is None else np.column_stack([baselines, stacked])
        if baselines is not None:
            # 기준선 **자신의** 예측력을 먼저 찍는다 — 피처의 증분을 읽으려면 넘어야 할
            # 선이 얼마나 높은지부터 알아야 한다.
            labels = [f"직전RV({args.baseline})"] if args.baseline != "none" else []
            labels += extra
            ics = " · ".join(
                f"{lab} {gate.spearman(baselines[:, j], y):+.4f}"
                for j, lab in enumerate(labels)
                if gate.spearman(baselines[:, j], y) is not None
            )
            print(f"    [기준선] {ics}")
    elif args.baseline != "none":
        print("  ** --baseline은 --label volatility에서만 쓰인다(방향 축에는 무시됨)")

    report = gate.run_gate(
        x,
        y,
        names,
        label_overlap_bars=overlap,
        baselines=baselines,
        min_abs_ic=args.min_abs_ic,
        min_abs_t=args.min_abs_t,
        max_abs_corr=args.max_abs_corr,
    )

    print(f"  {horizon.value}: {report.summary()}")
    if report.dead_names:
        # 가장 먼저 볼 목록 — 계산 자체가 안 되는 피처는 성능 문제가 아니라 배관 문제다.
        print(f"    ** 전 구간 NaN {len(report.dead_names)}개: {', '.join(report.dead_names)}")
    top = sorted(
        (v for v in report.verdicts if v.ic is not None),
        key=lambda v: -abs(v.ic or 0.0),
    )[:10]
    for verdict in top:
        marginal = (
            f"  (통제전 {verdict.marginal_ic:+.4f})" if verdict.marginal_ic is not None else ""
        )
        print(
            f"    {verdict.name:<24} IC {verdict.ic:+.4f}  t {verdict.t_stat or 0:+6.2f}  "
            f"{verdict.status.value}{marginal}"
        )

    payload = report.to_dict()
    payload["horizon"] = horizon.value
    payload["n_bars"] = len(bars)
    return payload


async def main() -> int:
    args = _parse_args()
    spec = feature_spec.resolve(args.feature_set)
    problems = feature_spec.validate_registry()
    if problems:
        print("피처 레지스트리 정합성 오류:", *problems, sep="\n  ", file=sys.stderr)
        return 2

    archiver = ParquetArchiver(Path(args.base_dir))
    start = datetime.strptime(args.start, "%Y-%m-%d").date()  # noqa: DTZ007
    end = datetime.strptime(args.end, "%Y-%m-%d").date()  # noqa: DTZ007
    segments = backfill.front_month_days(start, end)
    m1_bars, _rolls = backfill.load_continuous_series(archiver, segments, symbol_out=_SYMBOL)
    if not m1_bars:
        print("연속 시계열이 비어 있다 — run_backfill.py를 먼저 실행할 것", file=sys.stderr)
        return 2

    print(f"연속 시계열 {len(m1_bars)}봉  {start} ~ {end}")
    print(f"feature_set '{spec.name}' — 카테고리 {list(spec.categories)} · {len(spec)}개 피처")
    if not spec.registered:
        print("  ** 미등록 feature_set — 기저 카테고리로 해석됐다(오타 확인)")

    axis = (
        "방향(Triple Barrier)"
        if args.label == "direction"
        else (
            "변동성(다음 N봉 실현변동성"
            + (", 3분위 이산화)" if not args.no_tie_match else ", 연속값)")
        )
    )
    print(f"예측 대상 — {axis}")

    sidecars = sidecar.build(spec, flow_path=args.flow_path)
    if sidecars:
        print(f"사이드카 — {sidecar.describe(sidecars)}")

    results: list[dict[str, object]] = []
    for horizon_name in args.horizons.split(","):
        horizon = Horizon(horizon_name.strip())
        payload = await _gate_one_horizon(m1_bars, horizon, spec.name, args=args, sidecars=sidecars)
        if payload is not None:
            results.append(payload)

    if not results:
        print("판정된 Horizon이 없다", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "label": args.label,
                "baseline": args.baseline,
                "baseline_features": args.baseline_features,
                "tie_matched": not args.no_tie_match,
                "feature_set": spec.name,
                "categories": list(spec.categories),
                "start": args.start,
                "end": args.end,
                "thresholds": {
                    "min_abs_ic": args.min_abs_ic,
                    "min_abs_t": args.min_abs_t,
                    "max_abs_corr": args.max_abs_corr,
                },
                "horizons": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n판정 저장 → {out}")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="피처 품질 관문 (Ver 1.4 §3)")
    parser.add_argument("--base-dir", default=str(_DATA_DIR))
    parser.add_argument("--start", default="2025-12-12")  # 백필 소급 한계
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))  # noqa: DTZ005
    parser.add_argument("--horizons", default="5m,15m,30m")
    parser.add_argument(
        "--label",
        choices=("direction", "volatility"),
        default="direction",
        help="예측 대상 축. volatility는 다음 N봉 실현변동성(N=그 Horizon의 시간배리어)",
    )
    parser.add_argument(
        "--baseline",
        choices=("none", "rv", "har"),
        default="rv",
        help="변동성 축의 통제변수. rv=직전 N봉 RV 1개(순위 부분상관이 정확) · "
        "har=단기/중기/장기 3성분(누수 있음, 보조용) · none=통제 없음(주변상관)",
    )
    parser.add_argument(
        "--baseline-features",
        default="",
        help="피처 행렬의 특정 열을 추가 통제변수로 쓴다(쉼표 구분). 예: vl_gk_5 — "
        "종가 기반 RV는 비효율적 추정량이라, 'OHLC 기반 최선의 추정량을 넘는가'를 재려면 "
        "이쪽이 결정적이다. 지정한 열 자신은 증분을 못 재므로 quarantined로 나온다",
    )
    parser.add_argument(
        "--no-tie-match",
        action="store_true",
        help="변동성 타깃을 3분위로 이산화하지 않고 연속값 그대로 쓴다(비교 공정성 통제 해제)",
    )
    parser.add_argument("--feature-set", default="v2026.07")
    parser.add_argument("--flow-path", default=str(Path("data") / "flow" / "kospi_daily.parquet"))
    parser.add_argument("--min-bars", type=int, default=200)
    parser.add_argument("--min-abs-ic", type=float, default=gate.DEFAULT_MIN_ABS_IC)
    parser.add_argument("--min-abs-t", type=float, default=gate.DEFAULT_MIN_ABS_T)
    parser.add_argument("--max-abs-corr", type=float, default=gate.DEFAULT_MAX_ABS_CORR)
    parser.add_argument("--out", default=str(_OUT_PATH))
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
