"""번들 생산 경로 — 사슬의 **두 번째 마디** (2026-08-11 ④-b).

## 이 경로가 없었다

`registry.db`의 `bundles`는 11거래일째 0행이었고, 원인은 승격 실패가 아니라 **등록된 번들이
하나도 없다**는 것이었다. 저장소 전체에서 `pack_bundle()`/`promote_to_live()`를 부르는 코드는
`scripts/run_phase5_smoke.py`(토이 번들) 하나뿐이었다 — 실데이터로 학습해 Registry에 넣는
경로 자체가 존재하지 않았다.

학습 데이터는 있었다: 근월물 8심볼 167거래일(2025-12-12~). 없던 것은 이 스크립트다.

## 통과 관문 — 성과는 여기서 안 잰다

Validator의 관문은 두 종류다(그 모듈 docstring):

    성과 관문 3종   Sharpe · MDD · 창별 일관성   ← walk-forward 성과 시계열이 필요
    모델 관문 4종   교정 · 피처의존 · 지연 · 직렬화 ← 모델만 있으면 지금 잴 수 있다

**성과 관문은 G1(`run_g1_walk_forward.py`)로 미룬다.** 여기서 재려면 단일 분할 수익률을
써야 하는데 표본이 하나라 성적으로 읽으면 안 되고(`run_model_sweep.py`가 P&L을 안 재는 것과
같은 이유), 그 판단은 G1의 몫이다. 성과 관문을 shadow 등록의 조건으로 걸면 **아무것도
등록되지 않은 채로 또 몇 주가 간다** — shadow는 원래 "실전과 나란히 돌려보며 성적을 쌓는"
자리이고, 성적을 요구해서 shadow에 못 들어가면 그 자리의 의미가 없다.

대신 모델 관문 넷은 전부 건다. 그중 **교정은 홀드아웃에서 잰다** — 학습 구간의 교정은
언제나 좋아 보이고, 그건 관문이 아니라 장식이다.

## candidate → shadow → live, 그리고 첫 번들의 예외

정상 흐름은 `--promote shadow`(기본)다: 등록 → shadow → 20거래일 동안 챔피언과 겨룸
(`models/shadow_manager.evaluate_promotion`) → 사람이 `promote_to_live()`.

### 입력 계약이 바뀐 교체 — 성적 문제가 아니다 (2026-08-20 F-G 2단계)

위 규율에는 사각지대가 하나 있다. **피처 정의가 바뀌면 챔피언은 성적과 무관하게 무효**다 —
그 모델이 학습한 입력 분포가 더 이상 생산되지 않기 때문이다. 그 상태로 shadow 20거래일을
기다리면 그동안 매 추론이 **학습-서빙 왜곡**이고, 그건 어느 쪽 끝점보다도 나쁘다.

    python scripts/build_bundles.py --horizons 30m --promote live --operator MW0601 \
        --supersede-reason "세션 경계 인접쌍 제외(F-G) — 학습 입력 정의 변경"

`--supersede-reason` 없이는 종전대로 거부한다. 구 챔피언은 `retired`로 **보존**된다
(Ver 1.6 §9.2 — 롤백 가능).

그런데 **지금은 챔피언이 없다.** 챔피언 없는 shadow는 겨룰 상대가 없고
(`evaluate_promotion`은 `champion_returns`를 요구한다), shadow에만 넣으면 `get_live()`가
계속 `None`이라 `intel.futures`는 여전히 안 흐른다. 그래서 부트스트랩 경로를 명시적으로
둔다: `--promote live --operator NAME`은 **그 Horizon에 live가 하나도 없을 때만** 허용한다.
이미 챔피언이 있으면 거부하고 shadow 경로를 안내한다 — 챔피언 교체는 성적으로 하는
일이지 이 스크립트가 할 일이 아니다(Ver 1.1 §6-4 "승격은 사람이 한다").

## 언제 돌리나

**장 마감(15:35) 후.** SYSTEM.md R11(장중 학습 금지) — `session_guard`가 막는다.

    python scripts/build_bundles.py --horizons 30m --promote live --operator MW0601
    python scripts/build_bundles.py --horizons 5m,15m,30m          # shadow 등록만
    python scripts/build_bundles.py --dry-run                      # 관문만 보고 등록 안 함

종료 코드: 0 = 최소 한 개 등록 · 1 = 전부 관문 탈락(등록 0건) · 2 = 데이터 부족.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Mapping

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.backtest.harness import aggregate_to_horizon  # noqa: E402
from messiah.core.config import load_instance  # noqa: E402
from messiah.core.messages import BundleStatus, Horizon, bar_confirm_time  # noqa: E402
from messiah.core.timeutil import now_kst  # noqa: E402
from messiah.data import backfill  # noqa: E402
from messiah.data.archiver import ParquetArchiver  # noqa: E402
from messiah.features import sidecar  # noqa: E402
from messiah.features import spec as feature_spec  # noqa: E402
from messiah.models.registry import ModelRegistry, pack_bundle  # noqa: E402
from messiah.models.trainer import build_feature_vectors, train_formal_expert  # noqa: E402
from messiah.models.validator import GateResult, ValidationReport, Validator  # noqa: E402
from messiah.ops import session_guard  # noqa: E402

_DATA_DIR = Path("data") / "bars"
_SYMBOL = "K200MFC"
_REGISTRY_DB = Path("data") / "models" / "registry.db"
_BUNDLE_DIR = Path("data") / "models" / "bundles"
_REPORT_DIR = Path("logs")

# {-1,0,1} 원본 레이블 → 클래스 인덱스. `models/trainer.py`·`expert.py`·`search.py`가 각자
# 보유한 것과 같은 매핑이고, 여기도 같은 이유로 독립 보유한다(결합도 최소화).
_LABEL_TO_CLASS = {-1: 0, 0: 1, 1: 2}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="실데이터 번들 생산(장후용)")
    p.add_argument("--start", default="2025-12-12")
    p.add_argument("--end", default=None, help="생략하면 오늘")
    p.add_argument("--base-dir", default=str(_DATA_DIR))
    p.add_argument("--horizons", default="30m")
    p.add_argument("--holdout-fraction", type=float, default=0.2)
    p.add_argument("--feature-set", default=None, help="생략하면 운영 설정(instance.yaml)")
    p.add_argument("--out-dir", default=str(_BUNDLE_DIR))
    p.add_argument("--registry", default=str(_REGISTRY_DB))
    p.add_argument(
        "--promote",
        default="shadow",
        choices=["none", "shadow", "live"],
        help="live는 그 Horizon에 챔피언이 없을 때만(부트스트랩) — 모듈 docstring 참고",
    )
    p.add_argument("--operator", default=None, help="--promote live에 필수(감사 추적)")
    p.add_argument(
        "--supersede-reason",
        default=None,
        help=(
            "챔피언이 있어도 교체한다 — **입력 정의가 바뀐 경우 전용** (2026-08-20 F-G). "
            "성적을 근거로 한 교체에는 쓰지 않는다(그건 shadow 20거래일의 몫)."
        ),
    )
    p.add_argument("--dry-run", action="store_true", help="관문만 보고 등록하지 않는다")
    # 학습 예산 — 기본은 `train_formal_expert()`의 프로덕션 값에 가깝다.
    p.add_argument("--n-search-trials", type=int, default=20)
    p.add_argument("--n-members", type=int, default=5)
    session_guard.add_force_intraday_argument(p)
    session_guard.add_force_corrupt_archive_argument(p)
    return p.parse_args()


async def holdout_calibration(
    expert, holdout_bars, *, feature_set: str, sidecars, atr_window: int, cost_ticks: float
) -> tuple[list[list[float]], list[int]]:
    """홀드아웃 (확률, 정답) — **교정 관문의 유일하게 정직한 입력**.

    학습 구간의 교정은 언제나 좋아 보인다(모델이 그 답을 봤다). 그래서 이 함수는 학습에
    한 번도 안 쓰인 뒷구간만 본다. 확률 벡터의 열 순서는 `[p_down, p_flat, p_up]` —
    `models/metrics.multiclass_brier_score()`가 받는 `true_class_idx`(0=down,1=flat,2=up)와
    같은 규약이고, 이 순서가 틀리면 Brier가 조용히 나쁘게 나온다.
    """
    from messiah.models.labeling import label_and_weight

    labels = label_and_weight(holdout_bars, atr_window=atr_window, cost_ticks=cost_ticks)
    vectors = await build_feature_vectors(holdout_bars, feature_set=feature_set, sidecars=sidecars)
    # 키는 **`bar_confirm_time`**이다 — `TripleBarrierLabel.t_start`가 "진입봉 확정시각"이라
    # `bar_open_kst`로 맞추면 전건이 None이 되어 교정 표본 0건으로 조용히 떨어진다
    # (`models/trainer._align()`이 쓰는 것과 같은 키여야 한다).
    by_start = {label.t_start: label for label in labels}

    probs: list[list[float]] = []
    true_idx: list[int] = []
    for bar, vector in zip(holdout_bars, vectors):
        label = by_start.get(bar_confirm_time(bar))
        if label is None:
            continue  # ATR 워밍업·꼬리 트림 — 정답이 없는 봉은 교정을 못 잰다
        view = expert.predict(vector)
        probs.append([view.p_down, view.p_flat, view.p_up])
        true_idx.append(_LABEL_TO_CLASS[label.label])
    return probs, true_idx


def _deferred_performance_gates() -> list[GateResult]:
    """성과 관문 3종을 **"미측정"으로 명시 기록**한다 (모듈 docstring "통과 관문").

    빼버리면 `validation_report.json`이 "관문 넷을 다 통과했다"처럼 읽힌다 — 실제로는 일곱
    중 넷이고, 나머지 셋은 아직 아무도 안 쟀다. 이 저장소가 반복해서 배운 것이 그것이다:
    **없는 것과 통과한 것을 같은 모양으로 두면 안 된다**(마흐디 L18).
    `passed=False`라 `gates_passed`에도 안 담기므로 매니페스트가 통과를 주장하지 않는다.

    **값은 `NaN`이다** — "0.0"으로 두면 측정된 0으로 읽히기 때문이다. 대가로
    `validation_report.json`이 엄밀한 JSON이 아니게 된다(`json.dumps`가 `NaN`을 그대로
    쓴다). Python은 그대로 되읽지만 브라우저·jq 같은 엄격한 파서는 거부할 수 있다 —
    거짓 0보다 낫다고 판단했고, 성과 관문이 G1로 실제 값을 받으면 사라지는 상태다.
    """
    return [
        GateResult(
            name=name,
            passed=False,
            value=float("nan"),
            threshold=float("nan"),
            detail="미측정 — walk-forward 성과 시계열이 필요(scripts/run_g1_walk_forward.py)",
        )
        for name in ("sharpe", "max_drawdown", "negative_window_ratio")
    ]


async def build_one(
    *,
    horizon: Horizon,
    bars,
    holdout_fraction: float,
    feature_set: str,
    sidecars,
    run_id: str,
    out_dir: Path,
    atr_window: int | None = None,
    train_kwargs: Mapping[str, object] | None = None,
) -> tuple[str, ValidationReport, Path, tuple[str, str]] | None:
    """한 Horizon을 학습·검증·패킹한다. 반환은 (bundle_id, 리포트, 번들경로, 학습구간).

    `atr_window`는 학습과 홀드아웃 교정에 **같은 값**이 가야 한다 — 다르면 두 구간의
    레이블 기하가 달라져 교정 관문이 다른 문제를 채점하게 된다. 그래서 인자 하나로 묶어
    양쪽에 넘긴다(호출처가 각자 기본값을 쓰면 조용히 갈린다).
    """
    from messiah.models.labeling import DEFAULT_ATR_WINDOW
    from messiah.risk.cost_model import CostModel

    atr_window = DEFAULT_ATR_WINDOW if atr_window is None else atr_window
    split = int(len(bars) * (1 - holdout_fraction))
    train_bars, holdout_bars = bars[:split], bars[split:]
    if split <= 0 or not holdout_bars:
        print(f"  {horizon.value}: 분할 실패(학습 {split} · 홀드아웃 {len(holdout_bars)})")
        return None

    print(f"  {horizon.value}: 학습 {len(train_bars)}봉 · 홀드아웃 {len(holdout_bars)}봉")
    training = await train_formal_expert(
        train_bars,
        feature_set=feature_set,
        sidecars=sidecars,
        model_version=f"{run_id}-{horizon.value}",
        atr_window=atr_window,
        **(train_kwargs or {}),
    )

    cost_model = CostModel()
    cost_ticks = cost_model.estimate_round_trip_from_bars(holdout_bars, qty=1).total_ticks
    probs, true_idx = await holdout_calibration(
        training.expert,
        holdout_bars,
        feature_set=feature_set,
        sidecars=sidecars,
        atr_window=atr_window,
        cost_ticks=cost_ticks,
    )
    if not probs:
        print(f"  {horizon.value}: 홀드아웃 정답이 0건 — 교정을 못 잰다(관문 미충족)")
        return None

    sample_vector = (
        await build_feature_vectors(holdout_bars[:1], feature_set=feature_set, sidecars=sidecars)
    )[0]
    validator = Validator()
    with tempfile.TemporaryDirectory() as tmp:
        gates = [
            *_deferred_performance_gates(),
            validator.validate_calibration(probs, true_idx),
            validator.validate_feature_dependency(training.expert),
            validator.validate_latency(training.expert, sample_vector),
            validator.validate_serialization(training.expert, sample_vector, Path(tmp)),
        ]
    report = ValidationReport(gates=gates)

    trained_range = (
        train_bars[0].bar_open_kst.date().isoformat(),
        train_bars[-1].bar_open_kst.date().isoformat(),
    )
    bundle_id = f"{run_id}-{horizon.value}"
    manifest = pack_bundle(
        bundle_id=bundle_id,
        horizon=horizon,
        training_result=training,
        validation_report=report,
        trained_range=trained_range,
        feature_set=feature_set,
        run_id=run_id,
        out_dir=out_dir,
    )
    return manifest.bundle_id, report, Path(out_dir) / bundle_id, trained_range


def model_gates_passed(report: ValidationReport) -> bool:
    """모델 관문 넷만 본다 — 성과 셋은 의도적으로 `passed=False`이므로
    `report.passed`를 쓰면 **항상 거짓**이 된다."""
    deferred = {"sharpe", "max_drawdown", "negative_window_ratio"}
    return all(gate.passed for gate in report.gates if gate.name not in deferred)


def _promote(registry: ModelRegistry, bundle_id: str, horizon: Horizon, args) -> str:
    """등록 후 상태 전이. 반환은 최종 상태 문자열(리포트용)."""
    if args.promote == "none":
        return BundleStatus.CANDIDATE.value
    if args.promote == "shadow":
        registry.promote_to_shadow(bundle_id, "실데이터 번들 첫 등록(2026-08-11 ④-b)")
        return BundleStatus.SHADOW.value

    # --promote live: 부트스트랩 전용 (모듈 docstring "첫 번들의 예외")
    champion = registry.get_live(horizon)
    if champion is not None and not args.supersede_reason:
        raise SystemExit(
            f"거부 — {horizon.value}에 이미 챔피언이 있다({champion.bundle_id}). "
            "챔피언 교체는 shadow에서 20거래일 겨룬 뒤 성적으로 하는 일이다"
            "(models/shadow_manager.evaluate_promotion). --promote shadow로 다시 실행할 것.\n"
            "다만 **입력 정의가 바뀐 경우는 성적 문제가 아니다** — "
            "--supersede-reason 를 참고할 것."
        )
    if champion is None:
        registry.promote_to_shadow(bundle_id, "부트스트랩 — live 승격 직전 경유")
        registry.promote_to_live(
            bundle_id,
            operator=args.operator,
            reason="부트스트랩: 이 Horizon에 챔피언이 없어 첫 번들을 현역으로 세운다",
        )
        return BundleStatus.LIVE.value

    # **입력 계약이 바뀐 교체** (2026-08-20 F-G 2단계).
    #
    # 위 가드는 「챔피언 교체는 성적으로 하는 일」을 지키려고 있다. 옳은 규율이고 그대로 둔다.
    # 그런데 그 규율에는 사각지대가 하나 있다: **피처 정의가 바뀌면 챔피언은 성적과 무관하게
    # 무효**다. 그 모델이 학습한 입력 분포가 더 이상 생산되지 않기 때문이다.
    #
    # 그 상태로 shadow 20거래일을 기다리면 그동안 **매 추론이 학습-서빙 왜곡**이다 —
    # 옛 챔피언에게 새 정의의 값을 먹인다. 어느 쪽 끝점보다도 나쁘다.
    #
    # 그래서 좁게 연다: 사유를 문장으로 적어야 하고(`--supersede-reason`), 승인자가 있어야
    # 하며(`--operator`), 강등은 `registry.promote_to_live()`가 감사 추적과 함께 남긴다
    # (Ver 1.6 §9.2 — 레코드·파일 모두 보존해 롤백 가능).
    # **승인자 없이는 못 간다.** `main()`이 CLI 진입에서 같은 검사를 하지만 여기서 한 번 더
    # 본다 — 이 함수는 테스트·스크립트에서도 직접 불리고, 그때 `operator=None`이 통과하면
    # 감사 추적에 `승인: None`이 남는다. 남는 것이 없느니만 못한 기록이다.
    if not args.operator:
        raise SystemExit(
            "거부 — 챔피언 교체에는 --operator가 필요하다(감사 추적). "
            "사유만으로는 누가 승인했는지 남지 않는다."
        )
    registry.promote_to_shadow(bundle_id, "입력 계약 변경에 따른 교체 — live 승격 직전 경유")
    registry.promote_to_live(
        bundle_id,
        operator=args.operator,
        reason=(
            f"입력 계약 변경: {args.supersede_reason}"
            f" (구 챔피언 {champion.bundle_id} 자동 강등)"
        ),
    )
    print(
        f"  ⚠ 챔피언 교체 — {champion.bundle_id} → {bundle_id}\n"
        f"    사유: {args.supersede_reason}\n"
        f"    승인: {args.operator} · 구 챔피언은 retired로 보존(롤백 가능)",
        flush=True,
    )
    return BundleStatus.LIVE.value


async def main() -> int:
    args = _parse_args()
    if args.promote == "live" and not args.operator:
        print("--promote live에는 --operator가 필요하다(감사 추적)", file=sys.stderr)
        return 2
    if args.supersede_reason and args.promote != "live":
        print("--supersede-reason은 --promote live 에서만 뜻이 있다", file=sys.stderr)
        return 2
    session_guard.refuse_if_regular_session("번들 생산", force=args.force_intraday)

    start = datetime.strptime(args.start, "%Y-%m-%d").date()  # noqa: DTZ007
    end = (
        datetime.strptime(args.end, "%Y-%m-%d").date()  # noqa: DTZ007
        if args.end
        else now_kst().date()
    )
    session_guard.refuse_if_archive_corrupt(
        "번들 생산",
        [start + timedelta(days=i) for i in range((end - start).days + 1)],
        force=args.force_corrupt_archive,
    )

    feature_set = args.feature_set or load_instance("configs").feature_set
    spec = feature_spec.resolve(feature_set)
    print(spec.describe())  # F-1과 같은 줄 — 어떤 모양으로 학습했는지가 번들의 정체성이다
    sidecars = sidecar.build(spec)

    archiver = ParquetArchiver(Path(args.base_dir))
    segments = backfill.front_month_days(start, end)
    m1_bars, _rolls = backfill.load_continuous_series(archiver, segments, symbol_out=_SYMBOL)
    if not m1_bars:
        print("연속 시계열이 비어 있다 — run_backfill.py를 먼저 실행할 것", file=sys.stderr)
        return 2
    print(f"연속 시계열 {len(m1_bars)}봉  {start} ~ {end}")

    run_id = f"real-{now_kst():%Y%m%d-%H%M}"
    registry = ModelRegistry(Path(args.registry))
    results: list[dict] = []
    registered = 0
    try:
        for name in args.horizons.split(","):
            horizon = Horizon(name.strip())
            bars = aggregate_to_horizon(m1_bars, horizon)
            built = await build_one(
                horizon=horizon,
                bars=bars,
                holdout_fraction=args.holdout_fraction,
                feature_set=feature_set,
                sidecars=sidecars,
                run_id=run_id,
                out_dir=Path(args.out_dir),
                train_kwargs={
                    "n_search_trials": args.n_search_trials,
                    "n_members": args.n_members,
                },
            )
            if built is None:
                results.append({"horizon": horizon.value, "status": "skipped"})
                continue
            bundle_id, report, bundle_dir, trained_range = built

            for gate in report.gates:
                mark = "✓" if gate.passed else ("·" if "미측정" in gate.detail else "✗")
                print(f"    {mark} {gate.name:24s} {gate.value:.4f} (임계 {gate.threshold})")

            passed = model_gates_passed(report)
            row = {
                "horizon": horizon.value,
                "bundle_id": bundle_id,
                "trained_range": list(trained_range),
                "model_gates_passed": passed,
                "gates": [gate.__dict__ for gate in report.gates],
            }
            if not passed:
                row["status"] = "gate-failed"
                print(f"    ❌ {bundle_id} — 모델 관문 탈락, 등록하지 않는다")
            elif args.dry_run:
                row["status"] = "dry-run"
                print(f"    ✅ {bundle_id} — 관문 통과(--dry-run이라 등록 생략)")
            else:
                from messiah.models.registry import load_manifest

                registry.register(load_manifest(bundle_dir), bundle_dir)
                row["status"] = _promote(registry, bundle_id, horizon, args)
                registered += 1
                print(f"    ✅ {bundle_id} — {row['status']} 등록")
            results.append(row)
    finally:
        registry.close()

    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = _REPORT_DIR / f"bundle_build_{now_kst():%Y%m%d}.json"
    report_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "built_at_kst": now_kst().isoformat(),
                "feature_set": feature_set,
                "feature_spec": spec.describe(),
                "range": [start.isoformat(), end.isoformat()],
                "holdout_fraction": args.holdout_fraction,
                "promote": args.promote,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"기록: {report_path}  (등록 {registered}건)")
    # `--dry-run`도 **관문 결과로** 종료 코드를 낸다 — `or args.dry_run`으로 두면 전 Horizon이
    # 탈락한 예행이 성공으로 끝나고, 그건 이 스크립트가 스스로 금지한 "조용히 성공한 척"이다.
    if args.dry_run:
        return 0 if any(row.get("status") == "dry-run" for row in results) else 1
    return 0 if registered else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
