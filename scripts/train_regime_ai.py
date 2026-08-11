"""RegimeAI 실데이터 학습 — 사슬의 **첫 마디** (2026-08-11 ④-c).

## 왜 이것이 번들보다 먼저인가

`registry.db`의 `bundles`가 0행이라 `intel.futures`가 안 흐른다는 것이 지금까지의 진단이었다.
그런데 커밋 ④-a가 정정했듯 **사슬은 두 마디다**: `MetaDecisionEngine`의 규칙 ②가
`Regime.UNKNOWN`이면 무조건 `NO_TRADE`이고(`_EVENT_LIKE_REGIMES`가 UNKNOWN을 포함한다),
`RegimeAI`는 **실데이터로 학습된 적이 없다**(`run_regime_ai_smoke.py`는 합성 데이터 전용).

즉 번들만 붙이면 판단은 여전히 0건이다. 이 스크립트가 그 두 번째 마디를 푼다.

## 학습과 추론의 경계

`RegimeAI.fit()`은 **야간 배치**라는 전제로 설계됐고(`strategy/regime/runtime.py` 모듈
docstring), 운영 루프는 이미 학습된 인스턴스를 받아 굴리는 `RegimeRuntime`이다. 그래서
이 스크립트는 학습·저장까지만 하고, 결선은 `scripts/run_g2_paper_trading.py`가 저장본을
읽는 것으로 이뤄진다.

## 홀드아웃을 왜 보나 — "학습됐다"와 "쓸 수 있다"는 다르다

HMM은 비지도라 **항상 학습에 성공한다**. 성공했다는 사실만으로는 결선해도 되는지 알 수 없다.
결선 뒤 실제로 달라지는 것은 딱 하나 — `MetaDecisionEngine`이 UNKNOWN에서 벗어나는가다.
그래서 홀드아웃 구간을 `RegimeRuntime`과 **같은 순서로**(봉 하나씩 늘려가며 `classify()`)
흘려 국면 분포를 낸다. `UNKNOWN` 비율이 높으면 결선해도 판단은 안 난다 — 그 사실을 결선
전에 알아야 한다.

미래 참조는 없다: 학습은 `[:split]`만 쓰고 홀드아웃 판정은 그 시점까지의 봉만 본다.

## 언제 돌리나

**장 마감(15:35) 후.** `session_guard.refuse_if_regular_session()`이 막는다 —
SYSTEM.md R11(장중 학습 금지). 아카이브만 읽고 REST는 안 쓰지만 CPU를 수집 파이프라인과
나눠 쓰고, 그 규율을 스크립트마다 다르게 두면 규율이 아니게 된다.

    python scripts/train_regime_ai.py                     # 기본(전 구간, 홀드아웃 20%)
    python scripts/train_regime_ai.py --holdout-fraction 0.3
    python scripts/train_regime_ai.py --dry-run           # 저장 안 하고 분포만 본다

종료 코드: 0 = 학습·저장 완료 · 1 = 홀드아웃이 결선 부적합(아래 판정) · 2 = 데이터 부족.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.backtest.harness import aggregate_to_horizon  # noqa: E402
from messiah.core.messages import Horizon, Regime  # noqa: E402
from messiah.core.timeutil import now_kst  # noqa: E402
from messiah.data import backfill  # noqa: E402
from messiah.data.archiver import ParquetArchiver  # noqa: E402
from messiah.ops import session_guard  # noqa: E402
from messiah.strategy.regime.service import RegimeAI  # noqa: E402

_DATA_DIR = Path("data") / "bars"
_SYMBOL = "K200MFC"  # 후방조정 연속 시계열의 이름(`run_model_sweep.py`와 같은 값)

# 운영이 읽는 자리 — `run_g2_paper_trading.py`가 이 경로를 본다. 번들(`data/models/registry.db`)과
# 같은 디렉터리에 두되 파일로 분리한다: RegimeAI는 Horizon별 번들이 아니라 **프로세스당 하나**라
# Registry의 상태기계(candidate→shadow→live)에 얹을 대상이 아니다.
DEFAULT_MODEL_PATH = Path("data") / "models" / "regime_ai"
_REPORT_DIR = Path("logs")

# 결선 적합 판정 — 홀드아웃에서 UNKNOWN이 이 비율을 넘으면 붙여도 판단이 안 난다.
# 0.5는 미검증 초기값이다(첫 실측 전에 정할 수 있는 근거가 없다) — "절반 넘게 모른다면
# 국면 입력이라 부를 수 없다"는 상식선이고, 첫 실측 뒤 재조정 대상이다.
MAX_UNKNOWN_RATIO = 0.5

# 한 국면이 홀드아웃에서 이 비율을 넘게 차지하면 상수 분류기로 본다 (2026-08-11 추가).
# 0.8도 미검증 초기값이다. 실제 시장에 한 국면이 오래 이어지는 구간은 있으므로 낮게 잡으면
# 정상 모델을 막는다 — 잡으려는 것은 "80% 넘게 한 상태"가 아니라 첫 실행에서 관측된
# **100%**다. 상한을 낮출 근거는 실측이 쌓인 뒤에 생긴다.
MAX_SINGLE_REGIME_RATIO = 0.8


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="RegimeAI 실데이터 학습(장후용)")
    p.add_argument("--start", default="2025-12-12", help="아카이브 소급 한계일")
    p.add_argument("--end", default=None, help="생략하면 전 거래일")
    p.add_argument("--base-dir", default=str(_DATA_DIR))
    p.add_argument("--holdout-fraction", type=float, default=0.2)
    p.add_argument("--out", default=str(DEFAULT_MODEL_PATH))
    p.add_argument("--random-state", type=int, default=0)
    p.add_argument("--dry-run", action="store_true", help="저장하지 않는다 — 분포만 보고 판단할 때")
    session_guard.add_force_intraday_argument(p)
    session_guard.add_force_corrupt_archive_argument(p)
    return p.parse_args()


def holdout_regime_distribution(regime_ai: RegimeAI, bars, split: int) -> Counter:
    """홀드아웃 구간을 `RegimeRuntime`과 **같은 순서로** 흘려 국면 분포를 낸다.

    봉을 하나씩 늘려가며 `classify()`를 부르는 것이 핵심이다 — 한 번에 전 구간을 주면
    HMM이 미래 관측까지 보고 상태를 매기고(`predict_states`는 Viterbi 전역해다), 그러면
    실시간 경로에서 절대 못 얻는 정확도가 나온다. 이 함수가 재현하는 것은 **운영에서
    실제로 얻을 분포**다.
    """
    counts: Counter = Counter()
    for i in range(split, len(bars)):
        state = regime_ai.classify(bars[: i + 1])
        counts[state.regime.value] += 1
    return counts


def assess(counts: Counter) -> tuple[bool, str]:
    """홀드아웃 분포 → (결선해도 되는가, 사유). 관문은 둘이다: **모름**과 **한결같음**.

    ## UNKNOWN 비율

    결선 뒤 실제로 달라지는 것은 엔진이 UNKNOWN에서 벗어나는가뿐이다. 절반 넘게 모르면
    붙여도 대부분 NO_TRADE다.

    ## 그리고 상수 (2026-08-11 추가 — 첫 실행이 이 관문을 요구했다)

    처음엔 UNKNOWN 하나만 봤다. *"나머지는 붙인 뒤 관측할 수 있다"*고 적었는데 **틀렸다**:
    첫 실학습이 홀드아웃 437봉을 **전부 `TREND_DOWN` 하나**로 분류하면서 UNKNOWN 0%로
    관문을 통과했다. 상수 국면은 정보가 0인데 UNKNOWN보다 **나쁘다** — UNKNOWN은 "모른다"고
    정직하게 말하고 하위 AI를 보수 모드로 보내지만, 상수 `TREND_DOWN`은 재지 않은 사실을
    단언하고 가중치 매트릭스가 그것을 믿는다.

    같은 저장소가 피처에 대해 이미 아는 규율이다(`no-degenerate-features` — "세션 내내
    상수인 입력은 죽은 입력이다"). 국면도 모델의 입력이므로 같은 잣대를 쓴다.

    한 국면이 홀드아웃의 `MAX_SINGLE_REGIME_RATIO`를 넘게 차지하면 거부한다. UNKNOWN도
    이 검사의 대상이다 — "전부 모름"은 위 관문이 먼저 잡지만, 두 관문의 뜻이 겹치는 것이
    비어 있는 것보다 낫다.
    """
    total = sum(counts.values())
    if total == 0:
        return False, "홀드아웃 판정이 0건 — 데이터가 관측 윈도우보다 짧다"
    unknown = counts.get(Regime.UNKNOWN.value, 0) / total
    if unknown > MAX_UNKNOWN_RATIO:
        return False, (
            f"홀드아웃 UNKNOWN {unknown:.1%}(한도 {MAX_UNKNOWN_RATIO:.0%}) — 결선해도 "
            "MetaDecisionEngine 규칙 ②가 대부분 NO_TRADE를 낸다"
        )
    top_regime, top_count = counts.most_common(1)[0]
    top_ratio = top_count / total
    if top_ratio > MAX_SINGLE_REGIME_RATIO:
        return False, (
            f"홀드아웃의 {top_ratio:.1%}가 `{top_regime}` 하나다"
            f"(한도 {MAX_SINGLE_REGIME_RATIO:.0%}) — 상수 국면은 정보가 0이면서 "
            "재지 않은 사실을 단언한다(UNKNOWN보다 나쁘다)"
        )
    return True, (
        f"홀드아웃 UNKNOWN {unknown:.1%} · 최빈 `{top_regime}` {top_ratio:.1%} — 결선 가능"
    )


def main() -> int:
    args = _parse_args()
    session_guard.refuse_if_regular_session("RegimeAI 학습", force=args.force_intraday)

    start = datetime.strptime(args.start, "%Y-%m-%d").date()  # noqa: DTZ007
    end = (
        datetime.strptime(args.end, "%Y-%m-%d").date()  # noqa: DTZ007
        if args.end
        else now_kst().date()
    )
    # 손상 판정된 날 위에서 학습하지 않는다 — `run_model_sweep.py`와 같은 근거(2026-08-05):
    # 상위 Horizon 봉이 잘린 날은 재합성으로 복구되지만 **복구 전에 학습하면 잘린 봉을
    # 그대로 배운다.** 30m은 그 잘림이 가장 크게 나타나는 축이라 여기서 특히 중요하다.
    session_guard.refuse_if_archive_corrupt(
        "RegimeAI 학습",
        [start + timedelta(days=i) for i in range((end - start).days + 1)],
        force=args.force_corrupt_archive,
    )

    archiver = ParquetArchiver(Path(args.base_dir))
    segments = backfill.front_month_days(start, end)
    m1_bars, _rolls = backfill.load_continuous_series(archiver, segments, symbol_out=_SYMBOL)
    if not m1_bars:
        print("연속 시계열이 비어 있다 — run_backfill.py를 먼저 실행할 것", file=sys.stderr)
        return 2

    bars = aggregate_to_horizon(m1_bars, Horizon.M30)
    split = int(len(bars) * (1 - args.holdout_fraction))
    print(f"30m {len(bars)}봉  {start} ~ {end}  (학습 {split} · 홀드아웃 {len(bars) - split})")
    if split <= 0:
        print("학습 구간이 비어 있다 — --holdout-fraction을 낮출 것", file=sys.stderr)
        return 2

    regime_ai = RegimeAI.fit(bars[:split], random_state=args.random_state)
    labels = {state: regime.value for state, regime in sorted(regime_ai.labels.items())}
    print(f"상태 수 {regime_ai.n_states} · 명명 {labels}")

    counts = holdout_regime_distribution(regime_ai, bars, split)
    total = sum(counts.values())
    print("홀드아웃 국면 분포:")
    for name, n in counts.most_common():
        print(f"  {name:12s} {n:5d}  {n / total:6.1%}")

    ok, verdict = assess(counts)
    print(("✅ " if ok else "❌ ") + verdict)

    report = {
        "trained_at_kst": now_kst().isoformat(),
        "range": [start.isoformat(), end.isoformat()],
        "bars_30m": len(bars),
        "train_bars": split,
        "holdout_bars": len(bars) - split,
        "n_states": regime_ai.n_states,
        "labels": labels,
        "holdout_distribution": dict(counts),
        "wireable": ok,
        "verdict": verdict,
        "saved_to": None if args.dry_run else str(args.out),
    }
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = _REPORT_DIR / f"regime_ai_training_{now_kst():%Y%m%d}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.dry_run:
        print(f"--dry-run — 저장 생략 (리포트: {report_path})")
        return 0 if ok else 1

    # **저장 위치가 곧 결선 여부다** (2026-08-11 수정).
    #
    # 처음엔 "부적합이어도 운영 경로에 저장한다 — 저장은 결선이 아니다"라고 적었다. 그게
    # 거짓이었다: `run_g2_paper_trading._load_regime_runtime()`은 그 경로에 **파일이 있으면**
    # 붙인다. 리포트의 `wireable`을 아무도 안 읽으므로, 부적합 판정을 내리고도 다음 날 아침
    # 그것이 결선됐을 것이다 — 그리고 상수 국면은 UNKNOWN보다 나쁘다(`assess()` docstring).
    #
    # 그래서 부적합본은 `.rejected` 접미로 남긴다. 비교 기준선은 그대로 보존되고(다음 실행이
    # "나아졌나"를 잴 수 있다), 운영은 못 읽는다.
    out = Path(args.out) if ok else Path(f"{args.out}.rejected")
    regime_ai.save(out)
    report["saved_to"] = str(out)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if ok:
        print(f"저장(운영 경로) — {out} (리포트: {report_path})")
    else:
        print(
            f"저장(보류) — {out} · **운영 경로가 아니다**. 결선하려면 판정을 통과해야 한다 "
            f"(리포트: {report_path})",
            flush=True,
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
