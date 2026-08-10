"""Digital Twin 수동 스모크 실행 — Master Plan Ver 2.0 §9 W9~11.

아카이브된 완성봉(`data/bars/{symbol}/{horizon}/{date}.parquet`)을 재생해 FeatureEngine과
SimBroker/OrderGateway까지 전체 배선이 실제로 도는지 확인하는 진입점. Expert·Meta Decision은
아직 없으므로(Phase 3) 전략은 포함하지 않는다 — 대신 재생 시작 직후 시장가 주문 1건을 데모로
제출해 체결 경로(SimBroker.on_bar → Fill → OrderGateway.on_fill)까지 실제로 통과하는지 함께
보여준다.

사용: python scripts/run_replay.py --symbol A05608 --start 2026-07-24 --end 2026-07-24
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.broker.simulator.adapter import SimBroker  # noqa: E402
from messiah.core.config import load_instance  # noqa: E402
from messiah.core.messages import Horizon, OrderKind, OrderRequest, Side  # noqa: E402
from messiah.execution.order_gateway import OrderGateway  # noqa: E402
from messiah.features import sidecar  # noqa: E402
from messiah.features import spec as feature_spec  # noqa: E402
from messiah.features.engine import FeatureEngine  # noqa: E402
from messiah.simulator.engine import DigitalTwinEngine  # noqa: E402
from messiah.simulator.inprocess_bus import InProcessBus  # noqa: E402
from messiah.simulator.replay import ParquetBarReplaySource  # noqa: E402

_DATA_DIR = Path("data") / "bars"


async def main(args: argparse.Namespace) -> None:
    cfg = load_instance(args.configs)

    source = ParquetBarReplaySource(Path(args.base_dir), args.symbol)
    bars = source.load(date.fromisoformat(args.start), date.fromisoformat(args.end))
    if not bars:
        raise SystemExit(
            f"재생할 봉이 없음 — {args.base_dir}/{args.symbol}/*/{{{args.start}..{args.end}}}"
            ".parquet 확인"
        )
    print(f"재생 대상: {len(bars)}건 ({args.start} ~ {args.end}, {args.symbol})", flush=True)

    bus = InProcessBus(instance_id=f"{cfg.instance_id}-replay")

    feature_counts: dict[Horizon, int] = dict.fromkeys(Horizon, 0)

    async def _count_feature(horizon: Horizon, _vector) -> None:
        feature_counts[horizon] += 1

    # 재생은 **운영과 같은 `feature_set`**을 쓴다 — 사이드카를 안 주면 그 순간 재생이
    # 기동조차 못 하거나(EV) 카테고리가 통째로 빠진 벡터가 같은 이름을 달고 나간다.
    # 정본은 `features/sidecar.build()` 하나다(2026-08-10 B-4).
    feature_engine = FeatureEngine(
        args.symbol,
        bus,
        feature_set=cfg.feature_set,
        sidecars=sidecar.build(feature_spec.resolve(cfg.feature_set)),
    )
    await feature_engine.run_forever()
    for horizon in Horizon:
        await bus.subscribe(
            [f"feat.{horizon.value}.{args.symbol}"],
            lambda v, h=horizon: _count_feature(h, v),
        )

    broker = SimBroker(cash=cfg.capital.total)
    gateway = OrderGateway(broker)
    twin = DigitalTwinEngine(symbol=args.symbol, broker=broker, gateway=gateway, bus=bus)

    # 첫 봉으로 시계를 진행시킨 뒤 데모 시장가 주문 1건 제출 — 체결 경로 스모크 테스트.
    first_bar, remaining = bars[0], bars[1:]
    await twin.run([first_bar])
    if args.demo_order:
        ack = await gateway.submit(
            OrderRequest(
                intent_id="replay-smoke",
                symbol=args.symbol,
                kind=OrderKind.ENTRY,
                side=Side.LONG,
                qty=1,
                limit_price_ticks=None,  # 시장가 — 재생 데이터 가격대에 무관하게 항상 체결
            )
        )
        status = f"접수 {ack.broker_order_no}" if ack else "거부"
        print(f"데모 시장가 주문 제출: {status}", flush=True)

    await twin.run(remaining)

    print(f"봉 확정 시점 발행된 FeatureVector 수: {dict(feature_counts)}", flush=True)
    print(f"최종 포지션: {await broker.positions()}", flush=True)
    print(f"최종 계좌: {await broker.account()}", flush=True)
    print(f"게이트웨이 정지 여부: {gateway.halted}", flush=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MESSIAH Digital Twin 재생 스모크 실행")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--base-dir", default=str(_DATA_DIR))
    parser.add_argument("--configs", default="configs")
    parser.add_argument("--no-demo-order", dest="demo_order", action="store_false", default=True)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main(_parse_args()))
