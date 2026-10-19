"""사이징 0계약 원시 수량 전수 집계 — G-84 (2026-10-19 신설).

    python scripts/run_sizer_shortfall_tally.py                       # logs/ 전체
    python scripts/run_sizer_shortfall_tally.py --start 2026-09-01 --end 2026-10-19
    python scripts/run_sizer_shortfall_tally.py --out logs/dailycheck/sizer_tally.md

## 무엇을 답하는가

`order-path-live`가 "판정 불가"로 정체될 때마다 같은 질문이 나온다 — **신호가 약해서인가,
사이징 설계상 애초에 1계약이 거의 안 나오게 되어 있는가.** 이 스크립트는 판단하지 않고
그 판단의 재료만 만든다: `g2_daily_*.log`의 `SizerZeroQty`(사이클별)·`SizerZeroQtyStreak`
(세션 누계)·`OrderSubmit`을 날짜별로 모아 표 하나로 낸다.

- **raw_qty / edge / edge_needed_for_min_qty** — 1계약까지 얼마나 모자랐고, 그 순간 필요했던
  우위가 실제 우위의 몇 배였는가.
- **sized_calls − zero_qty_cycles** — 0계약 로그 없이 끝난 사이징 호출 수. `Sizer.size()`는
  자본 0 이하에서도 로그 없이 0을 돌려주므로 "1계약 이상 사이징"의 **상한**이지 확정치가 아니다.
  같은 날 `OrderSubmit` 수를 나란히 둔다(청산 주문도 섞여 있다).

**파라미터를 바꾸자는 도구가 아니다.** Kelly 축소 계수·변동성 목표 변경은 사람 결정이다.
읽기 전용 — 로그만 읽고 아무것도 쓰지 않는다(`--out` 지정 시 그 파일만).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

_ROOT = Path(__file__).resolve().parents[1]
_LOG_RE = re.compile(r"g2_daily_(\d{8})\.log$")


def _iter_records(path: Path):
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"Sizer' not in line and '"OrderSubmit"' not in line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def tally_day(records) -> dict:
    """하루치 레코드 → 집계. 레코드 순서는 상관없다."""
    zero: list[dict] = []
    streak: dict | None = None
    order_submits = 0
    for r in records:
        tag = r.get("tag")
        if tag == "SizerZeroQty":
            zero.append(r)
        elif tag == "SizerZeroQtyStreak":
            streak = r
        elif tag == "OrderSubmit":
            order_submits += 1
    raws = sorted(float(r.get("raw_qty") or 0.0) for r in zero)
    gaps = [
        float(r["edge_needed_for_min_qty"]) / float(r["edge"])
        for r in zero
        if r.get("edge_needed_for_min_qty") and r.get("edge")
    ]
    sized = streak.get("sized_calls") if streak else None
    zero_n = streak.get("zero_qty_cycles") if streak else len(zero)
    return {
        "zero_qty": len(zero),
        "raw_max": raws[-1] if raws else None,
        "raw_p50": raws[(len(raws) - 1) // 2] if raws else None,
        "edge_gap_min": min(gaps) if gaps else None,
        "sized_calls": sized,
        "non_zero_upper": (sized - zero_n) if sized is not None else None,
        "order_submits": order_submits,
        "details": [
            {
                "hhmm": str(r.get("ts", ""))[11:16],
                "raw_qty": r.get("raw_qty"),
                "edge": r.get("edge"),
                "edge_needed": r.get("edge_needed_for_min_qty"),
                "vol_target_qty": r.get("vol_target_qty"),
                "uncertainty_penalty": r.get("uncertainty_penalty"),
            }
            for r in zero
        ],
    }


def collect(log_dir: Path, start: date | None, end: date | None) -> dict[date, dict]:
    out: dict[date, dict] = {}
    for p in sorted(log_dir.glob("g2_daily_*.log")):
        m = _LOG_RE.search(p.name)
        if not m:
            continue
        s = m.group(1)
        day = date(int(s[:4]), int(s[4:6]), int(s[6:]))
        if (start and day < start) or (end and day > end):
            continue
        t = tally_day(_iter_records(p))
        if t["zero_qty"] or t["sized_calls"] or t["order_submits"]:
            out[day] = t
    return out


def _f(x, nd=3, unit="") -> str:
    return "—" if x is None else f"{x:.{nd}f}{unit}"


def _n(x) -> str:
    return "—" if x is None else str(x)


_DAY_HEAD = (
    "| 날짜 | 0계약 | raw 최대 | raw 중앙 | 필요/실제 우위 최소 "
    "| 사이징 호출 | 0계약 외(상한) | OrderSubmit |"
)
_CYCLE_HEAD = "| 날짜 | 시각 | raw_qty | edge | 필요 edge | vol_target_qty | 불확실성 감쇠 |"


def render(days: dict[date, dict]) -> str:
    L: list[str] = ["# 사이징 0계약 원시 수량 집계 (G-84)", ""]
    if not days:
        L.append("집계 대상 레코드 없음 — 기간 안에 사이징 호출이 한 번도 없었다(미측정).")
        return "\n".join(L)
    span = f"{min(days).isoformat()} – {max(days).isoformat()}"
    all_raw = [d["raw_qty"] for t in days.values() for d in t["details"] if d["raw_qty"]]
    reached = sum(1 for t in days.values() if (t["non_zero_upper"] or 0) > 0)
    L += [
        f"- 기간: {span} · 사이징·주문 기록이 있는 날 {len(days)}일",
        f"- 0계약 사이클 {sum(t['zero_qty'] for t in days.values())}건 · "
        f"raw_qty 최대 {_f(max(all_raw) if all_raw else None)} · "
        f"0.9 이상 {sum(1 for x in all_raw if x >= 0.9)}건",
        f"- 0계약 로그 없이 끝난 사이징 호출이 있던 날 {reached}일 "
        "(1계약 이상 사이징의 상한 — 자본 0 반환도 섞일 수 있다)",
        "",
        _DAY_HEAD,
        "|---|---|---|---|---|---|---|---|",
    ]
    for day, t in sorted(days.items()):
        L.append(
            f"| {day.isoformat()} | {t['zero_qty']} | {_f(t['raw_max'])} | {_f(t['raw_p50'])} "
            f"| {_f(t['edge_gap_min'], 2, '배')} | {_n(t['sized_calls'])} "
            f"| {_n(t['non_zero_upper'])} | {t['order_submits']} |"
        )
    L += ["", "## 사이클별 상세", "", _CYCLE_HEAD, "|---|---|---|---|---|---|---|"]
    for day, t in sorted(days.items()):
        for d in t["details"]:
            L.append(
                f"| {day.isoformat()} | {d['hhmm']} | {_f(d['raw_qty'])} | {_f(d['edge'])} "
                f"| {_f(d['edge_needed'])} | {_f(d['vol_target_qty'], 2)} "
                f"| {_f(d['uncertainty_penalty'])} |"
            )
    L += [
        "",
        "> 판단 재료다 — 사이징 파라미터 변경 여부는 사람 결정"
        "(SYSTEM.md 임계값·리스크 파라미터 규율).",
    ]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SizerZeroQty 원시 수량 전수 집계 (읽기 전용)")
    ap.add_argument("--start", type=date.fromisoformat, default=None)
    ap.add_argument("--end", type=date.fromisoformat, default=None)
    ap.add_argument("--log-dir", type=Path, default=_ROOT / "logs")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    text = render(collect(args.log_dir, args.start, args.end))
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8", newline="\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
