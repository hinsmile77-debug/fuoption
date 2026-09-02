"""KIS 옵션 시세 필드 의미 검증 — 수집된 실계좌 데이터로 재현해서 확정한다 (2026-09-02 신설).

    python scripts/verify_option_fields.py                      # 전 시리즈·전 수집일
    python scripts/verify_option_fields.py --series regular
    python scripts/verify_option_fields.py --dte-offset 0       # 확정 전 규약과 비교

## 무엇을 하는가

문서를 읽어서가 아니라 **KIS가 준 값을 우리 Black-76으로 재현해서** 규약을 확정한다.
재현되면 같은 규약이고, 안 되면 다른 규약이다 — 어느 쪽이든 추측이 아니다.

    ① IV: `hts_ints_vltl`/100 이 우리가 가격에서 역산한 IV와 같은가
    ② 시간: `hts_rmnn_dynu`를 그대로 쓰나, 하루 빼나
    ③ 그릭스: 같은 IV·forward·t로 계산한 우리 값과 KIS 값의 비
    ④ 유동성: 호가가 없는 자리에서 OI·거래량이 대체 축이 되는가

forward는 **풋-콜 패리티**(F = K + C − P의 ATM 근처 중앙값)로 시장에서 직접 뽑는다.
선물가를 쓰면 폴링 시각(옵션)과 봉 확정 시각(선물)이 어긋나 그 차이가 IV 오차로 샌다.

## 왜 스크립트로 남기나

2026-07-22에 같은 확인을 **1회 스냅샷**으로 했고 그 표본은 스크래치패드에만 있었다
(`Docs/KIS_RAW_FIELD_RANGES.md` "미실측 갭" 절 — R8이 요구한 5거래일을 못 채웠다). 손으로
한 번 본 것은 다음에 또 손으로 봐야 한다. 이 스크립트는 아카이브가 쌓이는 만큼 매번 다시
답하고, 값이 흔들리면 그 사실이 드러난다.

결론은 `strategy/options/chain_smile.py` §1에 상수로 박혀 있다 — 이 스크립트가 그 상수의
근거이고, 갈라지면 `tests/strategy/options/test_chain_smile.py`가 잡는다.
"""

from __future__ import annotations

import argparse
import glob
import math
import statistics
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pandas as pd  # noqa: E402

from messiah.strategy.options.chain_smile import (  # noqa: E402
    DAYS_PER_YEAR,
    DTE_SETTLEMENT_OFFSET_DAYS,
)
from messiah.strategy.options.surface import black76_greeks, implied_vol  # noqa: E402

_CHAIN_DIR = Path("data") / "option_chain"
_ATM_BAND = 0.03  # |log(K/F)| — 그릭스 대조는 ATM 근처만(외가는 가격 한 틱이 IV로 증폭된다)
_MIN_DTE_FOR_GREEKS = 5.0  # 만기 임박은 t→0에서 값이 폭주해 비율 비교가 무의미해진다


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="KIS 옵션 필드 의미 검증")
    p.add_argument("--base-dir", default=str(_CHAIN_DIR))
    p.add_argument("--series", default="regular,weekly_mon,weekly_thu")
    p.add_argument("--dte-offset", type=float, default=DTE_SETTLEMENT_OFFSET_DAYS)
    p.add_argument("--snapshots-per-day", type=int, default=3)
    return p.parse_args()


def _parity_forward(batch: pd.DataFrame, *, near_pairs: int = 6) -> float | None:
    calls = {r.strike: r for r in batch.itertuples() if r.option_type == "C" and r.futs_prpr > 0}
    puts = {r.strike: r for r in batch.itertuples() if r.option_type == "P" and r.futs_prpr > 0}
    pairs = [
        (k, k + calls[k].futs_prpr - puts[k].futs_prpr) for k in sorted(set(calls) & set(puts))
    ]
    if not pairs:
        return None
    rough = statistics.median(f for _, f in pairs)
    near = sorted(pairs, key=lambda item: abs(item[0] - rough))[:near_pairs]
    return statistics.median(f for _, f in near)


def _batches(path: Path, per_day: int):
    frame = pd.read_parquet(path)
    frame["ts_kst"] = pd.to_datetime(frame["ts_kst"], utc=True).dt.tz_convert("Asia/Seoul")
    frame = frame[frame.ts_kst.dt.time.astype(str).between("10:00:00", "15:00:00")]
    if frame.empty:
        return
    stamps = sorted(frame.ts_kst.dt.floor("5min").unique())
    for stamp in stamps[:: max(1, len(stamps) // per_day)][:per_day]:
        batch = frame[frame.ts_kst.dt.floor("5min") == stamp]
        if len(batch) >= 10:
            yield stamp, batch


def _median(values) -> float:
    values = [v for v in values if v == v]
    return statistics.median(values) if values else float("nan")


def _collect(base: Path, series: str, offset: float, per_day: int) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(glob.glob(str(base / series / "*.parquet"))):
        for stamp, batch in _batches(Path(path), per_day):
            forward = _parity_forward(batch)
            dte = float(batch.iloc[0].hts_rmnn_dynu)
            t = (dte - offset) / DAYS_PER_YEAR
            if forward is None or t <= 0:
                continue
            for r in batch.itertuples():
                if r.futs_prpr <= 0 or not (r.hts_ints_vltl > 0) or r.acml_vol <= 0:
                    continue
                iv_kis = r.hts_ints_vltl / 100.0
                iv_ours = implied_vol(
                    price=r.futs_prpr,
                    forward=forward,
                    strike=r.strike,
                    r=0.0,
                    t=t,
                    option_type=r.option_type,
                )
                if iv_ours is None:
                    continue
                greeks = black76_greeks(
                    forward=forward,
                    strike=r.strike,
                    r=0.0,
                    sigma=iv_kis,
                    t=t,
                    option_type=r.option_type,
                )
                rows.append(
                    {
                        "day": str(stamp)[:10],
                        "dte": dte,
                        "moneyness": math.log(r.strike / forward),
                        "iv_ratio": iv_ours / iv_kis,
                        "delta_ratio": r.delta_val / greeks.delta if greeks.delta else float("nan"),
                        "gamma_ratio": r.gama / greeks.gamma if greeks.gamma else float("nan"),
                        "theta_ratio": r.theta / greeks.theta if greeks.theta else float("nan"),
                        "vega_ratio": r.vega / greeks.vega if greeks.vega else float("nan"),
                        "theta_kis": r.theta,
                        "vega_kis": r.vega,
                        "oi": r.hts_otst_stpl_qty,
                        "volume": r.acml_vol,
                    }
                )
    return rows


def main() -> int:
    args = _parse_args()
    base = Path(args.base_dir)
    print(f"=== KIS 옵션 필드 검증 (t = (잔존일수 − {args.dte_offset:g}) / {DAYS_PER_YEAR:g}) ===")
    print(
        f"{'시리즈':<12}{'n':>6}{'일':>4}  {'IV비':>7}{'델타비':>8}{'감마비':>8}"
        f"{'세타비':>9}{'베가비':>9}   {'KIS세타중앙':>12}"
    )
    verdicts: list[str] = []
    for series in [s.strip() for s in args.series.split(",") if s.strip()]:
        rows = _collect(base, series, args.dte_offset, args.snapshots_per_day)
        atm = [
            r for r in rows if abs(r["moneyness"]) < _ATM_BAND and r["dte"] >= _MIN_DTE_FOR_GREEKS
        ]
        if not atm:
            print(f"{series:<12}{'표본 없음':>10}")
            continue
        iv_ratio = _median(r["iv_ratio"] for r in atm)
        print(
            f"{series:<12}{len(atm):>6}{len({r['day'] for r in atm}):>4}  "
            f"{iv_ratio:>7.4f}{_median(r['delta_ratio'] for r in atm):>8.3f}"
            f"{_median(r['gamma_ratio'] for r in atm):>8.3f}"
            f"{_median(r['theta_ratio'] for r in atm):>9.2f}"
            f"{_median(r['vega_ratio'] for r in atm):>9.2f}   "
            f"{_median(r['theta_kis'] for r in atm):>12.2f}"
        )
        # **임의 허용오차 대신 후보를 겨뤄 본다** — "1.00에서 몇 % 벗어나면 경보"라는 문턱은
        # 표본이 작은 시리즈에서 헛울고, 큰 시리즈에서는 실제 규약 변경을 놓친다(offset 0으로
        # 재보면 정규 월물 IV비가 0.9571로 5% 문턱을 아슬하게 통과한다 — 명백히 틀린 규약인데도).
        # 그래서 이웃 후보들을 같은 표본으로 재서 **가장 잘 재현하는 값**을 고르고, 그것이
        # 코드 상수와 다를 때만 운다. 문턱이 아니라 비교다.
        scores = {args.dte_offset: abs(iv_ratio - 1.0)}
        for candidate in (args.dte_offset - 1.0, args.dte_offset + 1.0):
            other = _collect(base, series, candidate, args.snapshots_per_day)
            other_atm = [
                r
                for r in other
                if abs(r["moneyness"]) < _ATM_BAND and r["dte"] >= _MIN_DTE_FOR_GREEKS
            ]
            if other_atm:
                scores[candidate] = abs(_median(r["iv_ratio"] for r in other_atm) - 1.0)
        best = min(scores, key=lambda off: scores[off])
        print(
            "    offset 후보 |IV비−1|: "
            + " · ".join(f"{off:g}일 {scores[off]:.4f}" for off in sorted(scores))
            + f"  → 최적 {best:g}일"
        )
        if best != args.dte_offset:
            verdicts.append(
                f"{series}: 잔존만기 규약이 어긋났다 — 지금 쓰는 {args.dte_offset:g}일보다 "
                f"{best:g}일이 더 잘 재현한다(|IV비−1| {scores[args.dte_offset]:.4f} → "
                f"{scores[best]:.4f}). `chain_smile.DTE_SETTLEMENT_OFFSET_DAYS`를 다시 볼 것"
            )
        # OI 임계를 올려도 IV 재현이 안 좋아지면, 잔여 오차는 유동성이 아니라 규약·시각이다.
        for threshold in (0, 50, 200):
            sub = [abs(r["iv_ratio"] - 1.0) for r in rows if r["oi"] >= threshold]
            if sub:
                print(f"    OI>={threshold:<4} n={len(sub):<5} |IV비−1| 중앙 {_median(sub):.4f}")

    print(
        "\n판독: IV비가 1.00 근처면 `hts_ints_vltl`은 %이고 시간 규약도 맞다. "
        "베가·델타비가 1.00 근처면 그 그릭스는 우리 규약과 같다.\n"
        "      감마·세타비가 1에서 멀거나 **시리즈마다 다르면** KIS 그릭스는 쓰지 않는다 "
        "— `surface.py`가 직접 계산한다(2026-09-02 실측 결론)."
    )
    for line in verdicts:
        print(f"\n⚠ {line}")
    return 1 if verdicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
