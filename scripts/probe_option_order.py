"""옵션 단일 다리 매수 주문 실측 — 주문 경로 4a-4 (2026-09-02 신설).

    # 1) 주문을 만들기만 하고 **제출은 안 한다**(장 밖에서도 안전하게 확인)
    python scripts/probe_option_order.py --dry-run

    # 2) 실제 제출 — 모의투자 계좌, 장중에만, 사람이 직접
    python scripts/probe_option_order.py --submit --qty 1

## 왜 자동이 아니라 사람이 돌리나

4a의 판정 기준은 **"모의계좌에서 체결 1건"**이고, 그건 자동 파이프라인이 우연히 달성할
성질이 아니다. `MetaDecisionEngine`엔 아직 규칙 ⑥⑦이 없어 `Side.OPTION`이 나올 경로가
없고(그 모듈 docstring "선택이 아니라 부재"), 그래서 이 스크립트가 유일한 호출부다.
**주문은 사람이 `--submit`을 직접 쳐야만 나간다.**

## 이 한 번의 체결이 두 가지를 동시에 답한다

  ⑴ **4a-4** 옵션 주문이 실제로 접수·체결되는가(종목코드 9자리·틱 단위·TR 전부)
  ⑵ **4a-2 계약승수** — 승수는 마스터파일에도 시세에도 없다(`contract_spec` §②).
     체결 **전후의 예수금·증거금 차이**를 체결 프리미엄으로 나누면 승수가 나온다:

         승수 = (체결 전 예수금 − 체결 후 예수금) / (체결 프리미엄 × 수량)

     그래서 이 스크립트는 제출 **전후로 `account()`를 찍는다.** 두 숫자가 없으면 승수를
     역산할 수 없고, 승수가 없으면 사이징이 영영 막힌다.

## 안전장치

  - 기본이 `--dry-run`이다. `--submit` 없이는 주문이 절대 안 나간다.
  - 단일 다리 **매수**만(`option_order.build_option_order()`가 그 밖을 거부한다).
  - `--qty` 기본 1. 승수를 모르는 상태에서 원화 노출을 최소로 둔다.
  - 지정가만 낸다(시장가 금지) — 유동성이 얇은 옵션에서 시장가는 얼마에 체결될지 모른다.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.broker.kis.adapter import KISBrokerAdapter  # noqa: E402
from messiah.broker.kis.credentials import KISCredentials  # noqa: E402
from messiah.core import logging as mlog  # noqa: E402
from messiah.core.config import load_instance  # noqa: E402
from messiah.core.timeutil import now_kst  # noqa: E402
from messiah.data.option_chain_archiver import read_day  # noqa: E402
from messiah.strategy.options.chain_smile import ChainLeg, build_smile, parse_leg  # noqa: E402
from messiah.strategy.options.contract_spec import (  # noqa: E402
    OPTION_PRICE_UNIT,
    tick_size_for_premium,
)
from messiah.strategy.options.evaluator import EvaluatorConfig, evaluate_candidate  # noqa: E402
from messiah.strategy.options.iv_seed import _snapshot_from_row  # noqa: E402
from messiah.strategy.options.leg_resolution import resolve_candidate  # noqa: E402
from messiah.strategy.options.matrix import LONG_CALL, LONG_PUT, spec_for  # noqa: E402
from messiah.strategy.options.option_order import build_option_order  # noqa: E402

_CHAIN_DIR = Path("data") / "option_chain"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="옵션 단일 다리 매수 주문 실측 (4a)")
    p.add_argument("--chain-dir", default=str(_CHAIN_DIR))
    p.add_argument("--series", default="regular")
    p.add_argument("--date", default=None, help="체인 스냅샷 날짜(기본: 오늘)")
    p.add_argument("--structure", default=LONG_CALL, choices=[LONG_CALL, LONG_PUT])
    p.add_argument("--qty", type=int, default=1)
    p.add_argument("--slippage-units", type=int, default=0, help="현재가 위로 얹을 0.01 단위")
    p.add_argument("--submit", action="store_true", help="실제 제출 — 없으면 만들기만 한다")
    p.add_argument("--configs", default="configs")
    return p.parse_args()


def _latest_chain(base: Path, series: str, day: date) -> list[ChainLeg]:
    """그날 마지막 스냅샷 사이클의 다리들 — `iv_seed`와 같은 해석기를 탄다."""
    frame = read_day(base, series, day)
    if frame is None or frame.is_empty():
        return []
    rows = frame.sort("ts_kst").to_dicts()
    by_cycle: dict[str, list[dict]] = {}
    for row in rows:
        by_cycle.setdefault(str(row.get("ts_kst"))[:15], []).append(row)
    for stamp in sorted(by_cycle, reverse=True):
        legs = []
        for row in by_cycle[stamp]:
            snapshot = _snapshot_from_row(row, series)
            if snapshot is None:
                continue
            leg = parse_leg(snapshot)
            if leg is not None:
                legs.append(leg)
        if len(legs) >= 6:
            return legs
    return []


async def main() -> int:
    args = _parse_args()
    mlog.setup("probe-option-order")
    day = date.fromisoformat(args.date) if args.date else now_kst().date()

    chain = _latest_chain(Path(args.chain_dir), args.series, day)
    if not chain:
        print(f"{day} {args.series} 체인 스냅샷이 없다 — 수집이 돈 날짜를 --date로 줄 것")
        return 2
    smile, reason = build_smile(chain)
    if smile is None:
        print(f"스마일을 못 만들었다 — {reason}")
        return 2

    spec = spec_for(args.structure)
    candidate = evaluate_candidate(
        spec, smile, r=0.0, score=0.0, config=EvaluatorConfig(), rationale={"probe": True}
    )
    if candidate is None:
        print(f"{args.structure} 후보를 못 만들었다 — 스마일이 목표 델타에 안 닿는다")
        return 2

    resolved, resolve_reason = resolve_candidate(candidate, spec, smile, chain, r=0.0, score=0.0)
    if resolved is None:
        print(f"종목코드 확정 실패 — {resolve_reason}")
        return 2

    plan = build_option_order(
        resolved, qty=args.qty, slippage_ticks=args.slippage_units, intent_id="probe-4a"
    )
    print("=" * 78)
    print(f"주문 계획: {plan.describe()}")
    print(
        f"  스냅: 목표 {resolved.legs[0].requested_strike:.2f} → 상장 {plan.symbol} "
        f"({resolved.max_snap_distance:.2f}pt) · 재평가 {resolve_reason}"
    )
    print(
        f"  호가단위 {tick_size_for_premium(plan.premium_points)} · "
        f"표현단위 {OPTION_PRICE_UNIT} · NetER {resolved.candidate.net_expected_return:+.3f}pt"
    )
    print("=" * 78)

    if not args.submit:
        print("--dry-run(기본) — 제출하지 않았다. 실제로 내려면 --submit 을 명시할 것.")
        return 0

    cfg = load_instance(args.configs)
    creds = KISCredentials.from_env()
    adapter = KISBrokerAdapter(
        creds,
        tick_size=Decimal(cfg.futures_tick_size),
        # **옵션은 표현 단위가 다르다** — 심볼별 해석기를 주입한다(4a-3).
        tick_size_for=lambda symbol: OPTION_PRICE_UNIT,
    )
    await adapter.connect()

    before = await adapter.account()
    print(f"체결 전 계좌: 예수금 {before.cash} · 증거금 {before.margin_used}")

    result = await adapter.submit(plan.request)
    print(f"제출 결과: ok={result.ok} 주문번호={result.broker_order_no} {result.error}")
    if not result.ok:
        print("거부됐다 — msg1을 그대로 기록하고 호가단위·종목코드를 다시 볼 것")
        return 1

    after = await adapter.account()
    print(f"체결 후 계좌: 예수금 {after.cash} · 증거금 {after.margin_used}")

    # **4a-2 승수 역산** — 이 두 줄이 이 스크립트의 두 번째 목적이다.
    cash_delta = before.cash - after.cash
    premium_krw_per_point = plan.premium_points * args.qty
    if premium_krw_per_point > 0:
        implied = cash_delta / premium_krw_per_point
        print("-" * 78)
        print(
            f"예수금 변화 {cash_delta} / (프리미엄 {plan.premium_points} × {args.qty}계약) = "
            f"**승수 추정 {implied}**"
        )
        print("  ※ 수수료·세금이 섞여 있으니 정확한 값은 체결통보의 체결가로 다시 계산할 것.")
        print("  ※ 확정되면 `strategy/options/contract_spec.OPTION_POINT_VALUE_KRW`에 적는다.")
    print("-" * 78)
    print("포지션 확인:")
    for position in await adapter.positions():
        print(f"  {position.symbol} {position.qty}계약 @ {position.avg_price_ticks}단위")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
