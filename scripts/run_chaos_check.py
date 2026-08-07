"""비상 경로 카오스 점검 — "설계했다"를 "흘려봤다"로 바꾼다 (2026-08-07 고도화 3).

## 왜 필요한가

`sys.kill`은 이 시스템 초기부터 설계돼 있었다. 그런데 **2026-08-07이 그 토픽에 메시지가
흐른 첫날**이었고, 흐르자마자 수집 프로세스가 죽었다 — `MessageBus.subscribe()`가 모든
구독자에게 kill을 자동 배달하는데 `FeatureEngine.handle_bar`는 그것을 견딜 수 없었다.
1시간 54분이 유실됐고 소급 불가 계열 3종은 영구 소실이다.

같은 일이 **R2(일일손실 한도) 자동 발동으로도 났을 것**이다. 손실 한도에 걸린 순간
데이터까지 잃는 구조였다는 뜻이고, 실계좌였다면 그날이 최악의 날이 됐다.

이 저장소는 "구현됨 ≠ 검증됨"을 계명으로 적어 두었다. 그런데 **비상 경로만은 그 검증을
사고가 대신해 왔다.** 이 스크립트가 그 자리를 메운다.

## 무엇을 하나 — 격리된 in-process 버스에서 실제로 흘려본다

운영 Redis에 붙지 않는다(그게 2026-08-07 사고의 형태였다). `InProcessBus`로 실제
컴포넌트를 조립하고 비상 신호를 흘린 뒤, **모두 살아 있는가**를 본다.

    1. sys.kill 발행 → 수집 계열 구독자(FeatureEngine)가 살아남는가
    2. sys.kill 발행 → 파이프라인이 게이트를 닫고 청산하는가 (고도화 5의 실동작 검증)
    3. 핸들러가 예외를 던져도 버스 루프가 사는가
    4. 알 수 없는 메시지가 섞여도 각 구독자가 자기 것만 보는가

## 언제 돌리나

장 마감 후(15:35~). `run_postmarket.py`에 넣지 **않았다** — 장후 절차는 그날 데이터를
확정하는 일이고 이건 코드 건강 점검이라 성격이 다르다. 배포 전과 주간 점검에 손으로 돌린다.

    python scripts/run_chaos_check.py

종료 코드: 0 = 전부 통과 · 1 = 살아남지 못한 경로 있음(그 경로가 곧 다음 사고다).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.core.bus import TOPIC_BAR, TOPIC_KILL  # noqa: E402
from messiah.core.messages import (  # noqa: E402
    BarClosed,
    BarSession,
    Horizon,
    KillSignal,
)
from messiah.core.timeutil import KST  # noqa: E402
from messiah.features.engine import FeatureEngine  # noqa: E402
from messiah.simulator.inprocess_bus import InProcessBus  # noqa: E402

_SYMBOL = "A05608"


def _bar(minute: int) -> BarClosed:
    open_kst = datetime(2026, 8, 7, 9, 0, tzinfo=KST) + timedelta(minutes=minute)
    return BarClosed(
        symbol=_SYMBOL,
        horizon=Horizon.M1,
        bar_open_kst=open_kst,
        o_ticks=1000,
        h_ticks=1002,
        l_ticks=999,
        c_ticks=1001,
        volume=10,
        quality_ok=True,
        session=BarSession.REGULAR,
    )


async def _case_collector_survives_kill() -> tuple[bool, str]:
    """① `sys.kill`이 수집 계열 구독자를 죽이지 않는가 — **2026-08-07 그 자리**."""
    bus = InProcessBus()
    engine = FeatureEngine(_SYMBOL, bus, feature_set="v2026.07")
    await bus.subscribe([f"{TOPIC_BAR}.{Horizon.M1.value}.{_SYMBOL}"], engine.handle_bar)

    await bus.publish(TOPIC_KILL, KillSignal(reason="카오스 점검", triggered_by="manual"))
    # 살아 있으면 이 봉을 정상으로 처리한다.
    await bus.publish(f"{TOPIC_BAR}.{Horizon.M1.value}.{_SYMBOL}", _bar(0))
    return True, "수집 구독자 생존 — kill이 배달되지 않거나 무시된다"


async def _case_pipeline_liquidates_on_kill() -> tuple[bool, str]:
    """② 비상 청산이 실제로 도는가 (고도화 5) — 눌러본 적 없던 버튼을 눌러본다."""
    from messiah.broker.simulator.adapter import SimBroker
    from messiah.core.messages import OrderKind, OrderRequest, Side
    from messiah.execution.order_gateway import OrderGateway
    from messiah.strategy.pipeline import TradingPipeline

    bus = InProcessBus()
    broker = SimBroker(cash=50_000_000)
    await broker.connect()
    gateway = OrderGateway(broker)
    pipeline = TradingPipeline(_SYMBOL, broker, gateway, bus)
    await pipeline.run_forever()  # InProcessBus는 등록만 하고 즉시 반환한다

    # **보유를 만들어 놓고** 누른다 — 게이트만 닫히는 것으로는 청산 경로가 검증되지 않는다.
    broker.on_bar(_bar(0))
    await gateway.submit(
        OrderRequest(
            intent_id="chaos-seed",
            symbol=_SYMBOL,
            kind=OrderKind.ENTRY,
            side=Side.LONG,
            qty=1,
            limit_price_ticks=None,
            ttl_ms=5_000,
            risk_approved_by="chaos-check",
        )
    )
    before = await broker.positions()
    if not any(p.qty for p in before):
        return False, "점검 준비 실패 — 보유를 만들지 못했다(청산을 검증할 수 없다)"

    await bus.publish(TOPIC_KILL, KillSignal(reason="카오스 점검", triggered_by="manual"))

    if not gateway.halted:
        return False, "게이트가 안 닫혔다 — sys.kill 수신 경로가 끊겼다"
    after = await broker.positions()
    if any(p.qty for p in after):
        return False, f"게이트는 닫혔는데 **청산이 안 됐다** — 잔여 {after}"
    return True, f"보유 {before[0].qty}계약 → 게이트 정지 + 전량 청산 확인"


async def _case_loop_survives_handler_exception() -> tuple[bool, str]:
    """③ 핸들러가 터져도 버스가 계속 도는가 — 2026-08-07 손실의 직접 원인."""
    from messiah.core.bus import MessageBus, encode

    seen: list[str] = []

    class _PubSub:
        def __init__(self, payloads):
            self._payloads = payloads

        async def psubscribe(self, *patterns):
            return None

        async def listen(self):
            for payload in self._payloads:
                yield {"type": "pmessage", "data": payload}

    class _Redis:
        def __init__(self, payloads):
            self._payloads = payloads

        def pubsub(self):
            return _PubSub(self._payloads)

    async def handler(msg):
        seen.append(type(msg).__name__)
        if len(seen) == 1:
            raise AttributeError("'KillSignal' object has no attribute 'symbol'")

    bus = MessageBus("redis://unused/0", instance_id="chaos")
    bus._redis = _Redis([encode(_bar(0)), encode(_bar(1)), encode(_bar(2))])  # noqa: SLF001
    await bus.subscribe([f"{TOPIC_BAR}.{Horizon.M1.value}.{_SYMBOL}"], handler)

    if len(seen) != 3:
        return False, f"첫 예외가 루프를 죽였다 — {len(seen)}/3건만 처리됨"
    return True, "핸들러 예외 격리 확인 — 3/3건 처리"


async def _main() -> int:
    cases = [
        ("① sys.kill이 수집 구독자를 안 죽인다", _case_collector_survives_kill),
        ("② sys.kill이 게이트를 닫는다", _case_pipeline_liquidates_on_kill),
        ("③ 핸들러 예외가 버스 루프를 안 죽인다", _case_loop_survives_handler_exception),
    ]
    print("=== 비상 경로 카오스 점검 ===\n")
    failed = 0
    for label, case in cases:
        try:
            ok, detail = await case()
        except Exception as exc:  # noqa: BLE001 — 예외 자체가 결과다
            ok, detail = False, f"예외로 중단: {type(exc).__name__}: {exc}"
        print(f"  {'✅' if ok else '❌'} {label}\n      {detail}")
        failed += 0 if ok else 1

    print()
    if failed:
        print(f"{failed}개 경로가 살아남지 못했다 — 그 경로가 곧 다음 사고다.")
        return 1
    print("전 경로 통과 — 비상 신호가 흘러도 시스템이 산다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
