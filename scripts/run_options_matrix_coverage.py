"""매트릭스 정합 진단 실행 — 0순위 (2026-09-02 신설).

    python scripts/run_options_matrix_coverage.py            # 정적 + 아카이브 경험적
    python scripts/run_options_matrix_coverage.py --static   # 데이터 없이 정적 판정만

## 무엇을 답하는가

**"매트릭스가 배정하는 구조를 평가기가 만들 수 있는가, 그리고 못 만드는 셀에 실제로 몇 %가
걸리는가."** 앞쪽은 데이터 없이 코드만으로 답이 나오고(`matrix_coverage.check_matrix_coverage()`),
뒤쪽은 수집된 옵션 체인 + 그날의 `FuturesView.score`가 있어야 답이 나온다.

`scripts/run_label_geometry.py`와 같은 자리다 — 학습·주문을 짓기 **전에**, 지금 구조로
후보가 나오기는 하는지부터 묻는다. 옵션 주문 경로를 만들기 전에 이걸 먼저 돌리는 이유는
간단하다: 후보가 3%에서만 나오는 상태로 주문 경로를 전부 지으면 97%를 위해 짓는 것이 없다.

## score 조인은 30분 격자로 정확히 맞춘다

체인 스냅샷은 300초 주기, 판단(`DecisionEmitted`)은 30분 격자다. 2026-09-02 1차 측정은 시(hour)
단위로 조인해 한 시간 안의 두 사이클이 같은 점수를 공유했다 — 그 편의가 (중립) 셀 비중을
부풀렸을 수 있어, 여기서는 **스냅샷 시각 이전의 가장 최근 판단**을 그 사이클의 점수로 쓴다
(실시간 서비스가 보는 것과 같은 순서 — `OptionsAIService._latest_score`).
"""

from __future__ import annotations

import argparse
import bisect
import glob
import json
import sys
from datetime import timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pandas as pd  # noqa: E402

from messiah.strategy.options.chain_smile import ChainLeg, build_smile  # noqa: E402
from messiah.strategy.options.matrix_coverage import check_matrix_coverage  # noqa: E402
from messiah.strategy.options.vol_metrics import IVHistory  # noqa: E402

_CHAIN_DIR = Path("data") / "option_chain"
_LOG_DIR = Path("logs")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="옵션 매트릭스 정합 진단")
    p.add_argument("--chain-dir", default=str(_CHAIN_DIR))
    p.add_argument("--log-dir", default=str(_LOG_DIR))
    p.add_argument("--series", default="regular")
    p.add_argument("--static", action="store_true", help="아카이브를 안 읽고 정적 판정만")
    return p.parse_args()


def _decision_scores(log_dir: Path) -> list[tuple[pd.Timestamp, float]]:
    """`(판단 시각, S)` 시계열 — 스냅샷마다 **그 이전의 가장 최근 판단**을 붙이기 위한 것."""
    out: list[tuple[pd.Timestamp, float]] = []
    for path in sorted(glob.glob(str(log_dir / "g2_daily_2026*.log"))):
        for line in Path(path).read_text(encoding="utf-8-sig", errors="replace").splitlines():
            if '"DecisionEmitted"' not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if "score" in record and "ts" in record:
                out.append(
                    (pd.Timestamp(record["ts"]).tz_convert("Asia/Seoul"), float(record["score"]))
                )
    out.sort(key=lambda item: item[0])
    return out


def _legs(batch: pd.DataFrame) -> list[ChainLeg]:
    return [
        ChainLeg(
            symbol=r.symbol,
            series=r.series,
            option_type=r.option_type,
            strike=float(r.strike),
            price=float(r.futs_prpr),
            dte_days=float(r.hts_rmnn_dynu),
            open_interest=float(r.hts_otst_stpl_qty),
            volume=float(r.acml_vol),
            kis_iv=None,
            ts_utc=r.ts_kst.to_pydatetime().astimezone(timezone.utc),
        )
        for r in batch.itertuples()
    ]


def _samples(chain_dir: Path, series: str, log_dir: Path) -> list[tuple[float, float | None]]:
    """아카이브를 재생해 사이클마다 `(score, iv_rank)`를 만든다 — 서비스와 같은 순서로."""
    scores = _decision_scores(log_dir)
    stamps = [ts for ts, _ in scores]
    iv_history = IVHistory()
    samples: list[tuple[float, float | None]] = []
    for path in sorted(glob.glob(str(chain_dir / series / "*.parquet"))):
        frame = pd.read_parquet(path)
        frame["ts_kst"] = pd.to_datetime(frame["ts_kst"], utc=True).dt.tz_convert("Asia/Seoul")
        frame = frame[frame.ts_kst.dt.time.astype(str).between("09:30:00", "15:00:00")]
        for stamp, batch in frame.groupby(frame.ts_kst.dt.floor("30min")):
            if len(batch) < 6:
                continue
            smile, _ = build_smile(_legs(batch))
            if smile is None:
                continue
            atm_iv = smile.iv_at(smile.forward)
            iv_history.add(atm_iv)
            index = bisect.bisect_right(stamps, stamp) - 1
            score = scores[index][1] if index >= 0 else 0.0
            samples.append((score, iv_history.rank(atm_iv)))
    return samples


def main() -> int:
    args = _parse_args()
    samples = [] if args.static else _samples(Path(args.chain_dir), args.series, Path(args.log_dir))
    if not args.static:
        print(f"아카이브 재생: {args.series} 시리즈 {len(samples)} 사이클\n")

    coverage = check_matrix_coverage(samples=samples)
    for line in coverage.format_lines():
        print(line)

    if coverage.is_healthy:
        return 0
    print(
        "\n메우는 방법은 셋이고 선택은 사람 몫이다(위험 성향을 바꾸는 결정):\n"
        "  ㉠ 그 구조를 evaluator에 구현한다\n"
        "  ㉡ 그 셀에 만들 수 있는 다른 구조를 배정한다\n"
        "  ㉢ 그 셀은 관망이 맞다고 확정해 빈 셀로 만든다(그러면 사유가 「관망」으로 정직해진다)"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
