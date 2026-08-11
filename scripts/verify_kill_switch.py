"""Kill Switch 실동작 검증 — 화면의 빨간 버튼이 **실제로 무언가를 하는가** (2026-08-11 F-4).

## 무엇이 아직 검증 안 됐나

`run_chaos_check.py`가 이미 `sys.kill` 수신 경로를 흘려본다 — 그러나 `InProcessBus`로
한다(그쪽 docstring: "운영 Redis에 붙지 않는다"). 그래서 다음 세 마디는 **한 번도 실제로
흘러본 적이 없다**:

    ① ui/app.py `_publish_kill()`  — 화면 버튼이 부르는 그 함수 자신
    ② Redis pub/sub 왕복           — 인코딩·채널명·`psubscribe` 패턴
    ③ MessageBus.subscribe(on_kill=) — 실제 버스의 kill 배달 분기

카오스 점검이 통과해도 이 셋 중 하나가 끊겨 있으면 버튼은 아무 일도 안 한다. 그리고 그
사실은 **정작 눌러야 하는 날**에 처음 드러난다. 이 저장소가 두 번 겪은 형태다
(`get_balance` 필드 누락, `sidecar.build` 미호출 — 둘 다 "구현됨≠검증됨").

## 격리 — **DB 번호로는 격리되지 않는다** (2026-08-11 실측, 이 스크립트 첫 실행)

처음엔 운영과 같은 Redis의 다른 DB(15)로 쏘면 안전하다고 적었다. **틀렸다.** Redis의
pub/sub는 keyspace가 아니라 **인스턴스 전역**이라 `SELECT`한 DB와 무관하게 모든 구독자에게
배달된다. 그 결과 이 점검의 첫 실행이 db 15로 쏘고도 **운영 db 0에 붙어 있던 G2 페이퍼
세션의 주문 게이트를 실제로 닫았다**(09:27:28, `logs/g2_daily_20260811.log`). 그날 G2는
live 번들이 0개라 주문이 애초에 0건이었던 것이 유일한 다행이다.

그래서 격리 단위는 **DB가 아니라 서버**다. 이 스크립트는 자기 전용 Redis 컨테이너를
띄웠다 지운다(`messiah-redis-verify`, 포트 6390) — 운영 인스턴스와 포트가 다르므로 채널이
섞일 수 없다. 그리고 운영과 **같은 host:port**를 가리키는 URL은 `--force-live-db` 없이는
거부한다.

이 사고 자체가 이 스크립트의 존재 이유를 증명한다: `sys.kill`은 흘려보기 전까지 아무도
그 도달 범위를 정확히 몰랐다.

## 언제 돌리나

장 마감 후. `run_chaos_check.py`와 같은 성격(코드 건강 점검)이라 장후 절차에 안 넣었다.
전용 컨테이너를 쓰므로 장중에 돌려도 운영에 닿지 않지만, 관례는 장후로 둔다.

    python scripts/verify_kill_switch.py

종료 코드: 0 = 전 구간 통과 · 1 = 끊긴 마디 있음 · 2 = 점검 자체를 못 했다(Docker 부재 등).
결과는 `logs/kill_switch_verification_YYYYMMDD.json`에 남긴다 — "언제 마지막으로 눌러봤나"에
답하는 유일한 기록이고, 없으면 다음 사람이 다시 "한 번도 안 눌러봤다"를 적게 된다.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.broker.simulator.adapter import SimBroker  # noqa: E402
from messiah.core.bus import MessageBus  # noqa: E402
from messiah.core.config import load_instance  # noqa: E402
from messiah.core.messages import (  # noqa: E402
    BarClosed,
    BarSession,
    Horizon,
    OrderKind,
    OrderRequest,
    Side,
)
from messiah.core.timeutil import KST, now_kst  # noqa: E402
from messiah.execution.order_gateway import OrderGateway  # noqa: E402
from messiah.strategy.pipeline import TradingPipeline  # noqa: E402

_SYMBOL = "A05608"
# 전용 Redis — 운영(6380)과 **포트가 달라야** 한다. pub/sub는 인스턴스 전역이라 DB 번호로는
# 안 갈린다(모듈 docstring "격리" 참고).
_VERIFY_CONTAINER = "messiah-redis-verify"
_VERIFY_PORT = 6390
_VERIFY_URL = f"redis://localhost:{_VERIFY_PORT}/0"
_REDIS_IMAGE = "redis:7-alpine"  # docker-compose.yml과 같은 이미지
_REPORT_DIR = Path("logs")
# 버스 왕복은 밀리초 단위지만, Redis가 도커에서 막 깨어난 직후엔 첫 왕복이 느리다.
# 실패로 단정하기 전에 넉넉히 기다린다 — 여기서 조급하면 "끊겼다"는 거짓 보고가 나온다.
_ROUND_TRIP_TIMEOUT_SECONDS = 10.0


def _bar(minute: int) -> BarClosed:
    open_kst = datetime(2026, 8, 11, 9, 0, tzinfo=KST) + timedelta(minutes=minute)
    price = 97_500 + minute
    return BarClosed(
        symbol=_SYMBOL,
        horizon=Horizon.M1,
        bar_open_kst=open_kst,
        o_ticks=price,
        h_ticks=price + 5,
        l_ticks=price - 5,
        c_ticks=price,
        volume=100,
        session=BarSession.REGULAR,
    )


def _endpoint(redis_url: str) -> str:
    """URL에서 host:port만 — 격리 판정의 단위다(DB 번호는 무시한다, 위 docstring 근거)."""
    return redis_url.split("//", 1)[-1].rsplit("/", 1)[0]


def _assert_isolated(redis_url: str, operational_url: str) -> None:
    """운영과 같은 서버면 거부 — 여기 도달하면 그건 09:27 사고의 재연이다."""
    if _endpoint(redis_url) == _endpoint(operational_url):
        raise SystemExit(
            f"거부 — 점검용 Redis({_endpoint(redis_url)})가 운영과 같은 서버다. "
            "pub/sub는 DB로 안 갈리므로 이대로 쏘면 구동 중인 G2의 주문 게이트가 닫힌다 "
            "(2026-08-11 09:27 실측). 다른 포트를 쓰거나 --force-live-db를 명시할 것."
        )


def _run_docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


@contextlib.contextmanager
def _throwaway_redis():
    """점검 전용 Redis 컨테이너 — 끝나면 지운다(`--rm`).

    운영 Redis를 재사용하지 않는 이유는 모듈 docstring "격리" 참고. 컨테이너를 못 띄우면
    점검을 **안 한 것**으로 끝낸다(종료 코드 2) — 운영 인스턴스로 조용히 물러나면 그게
    바로 이 스크립트가 한 번 저지른 사고다.
    """
    _run_docker("rm", "-f", _VERIFY_CONTAINER)  # 이전 실행이 남긴 것이 있으면 치운다
    created = _run_docker(
        "run",
        "--rm",
        "-d",
        "--name",
        _VERIFY_CONTAINER,
        "-p",
        f"{_VERIFY_PORT}:6379",
        _REDIS_IMAGE,
    )
    if created.returncode != 0:
        raise RuntimeError(f"점검용 Redis 컨테이너 기동 실패: {created.stderr.strip()}")
    try:
        # `docker run -d`는 즉시 반환하고 redis는 그 뒤에 뜬다 — 준비를 기다리지 않으면
        # 첫 `connect()`가 실패하고 "점검 못 함"으로 잘못 보고된다.
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("localhost", _VERIFY_PORT), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError(f"점검용 Redis가 15초 안에 안 떴다(포트 {_VERIFY_PORT})")
        yield _VERIFY_URL
    finally:
        _run_docker("rm", "-f", _VERIFY_CONTAINER)


async def _wait_until(predicate, *, timeout: float) -> bool:
    """조건이 참이 될 때까지 짧게 폴링 — 버스 왕복은 비동기라 즉시 참이 아니다."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return predicate()


async def _wait_until_async(predicate, *, timeout: float) -> bool:
    """`_wait_until`의 async 술어판 — 브로커 조회처럼 await가 필요한 조건용."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await predicate():
            return True
        await asyncio.sleep(0.05)
    return await predicate()


async def _verify(redis_url: str) -> tuple[bool, list[str]]:
    """UI 버튼 → Redis → 파이프라인 → 게이트·청산 한 바퀴. 반환은 (통과, 관측 목록)."""
    # 화면이 부르는 그 함수를 그대로 쓴다 — 사본을 만들면 이 점검이 사본을 검증하게 된다.
    from messiah.ui.app import _publish_kill

    notes: list[str] = []
    bus = MessageBus(redis_url, instance_id="kill-switch-verification")
    await bus.connect()
    notes.append(f"Redis 연결 — {redis_url}")

    broker = SimBroker(cash=50_000_000)
    await broker.connect()
    gateway = OrderGateway(broker)
    pipeline = TradingPipeline(_SYMBOL, broker, gateway, bus)

    # 실제 버스의 `subscribe()`는 무한 루프라 태스크로 띄운다(`InProcessBus`와 다른 점이고,
    # 이 차이 자체가 이 점검이 카오스 점검에 더하는 것 중 하나다).
    subscription = asyncio.create_task(pipeline.run_forever())
    await asyncio.sleep(0.5)  # psubscribe가 실제로 걸릴 시간 — 이전에 쏘면 메시지가 증발한다

    try:
        broker.on_bar(_bar(0))
        await gateway.submit(
            OrderRequest(
                intent_id="kill-switch-verification",
                symbol=_SYMBOL,
                kind=OrderKind.ENTRY,
                side=Side.LONG,
                qty=1,
                limit_price_ticks=None,
                ttl_ms=5_000,
                risk_approved_by="verify_kill_switch",
            )
        )
        before = await broker.positions()
        if not any(p.qty for p in before):
            return False, [*notes, "점검 준비 실패 — 보유를 못 만들었다(청산을 검증할 수 없다)"]
        notes.append(f"보유 생성 — {before[0].qty}계약")

        # **화면 버튼과 같은 경로.** `_publish_kill()`은 자기 이벤트 루프를 열고 닫으므로
        # (Streamlit 콜백이 동기라서 그렇게 만들어졌다) 스레드로 부른다.
        await asyncio.to_thread(_publish_kill, redis_url, "Kill Switch 실동작 검증(F-4)")
        notes.append("ui/app._publish_kill() 호출 — sys.kill 발행")

        halted = await _wait_until(lambda: gateway.halted, timeout=_ROUND_TRIP_TIMEOUT_SECONDS)
        if not halted:
            return False, [
                *notes,
                "게이트가 안 닫혔다 — 화면→Redis→파이프라인 중 한 마디가 끊겼다",
            ]
        notes.append("게이트 정지 확인")

        # 청산은 `halt()` **다음**이라 게이트가 닫힌 순간에는 아직 안 끝났을 수 있다 —
        # 여기서 한 번만 보고 판정하면 정상인 날에도 "청산 안 됨"이 나온다.
        async def _is_flat() -> bool:
            return not any(p.qty for p in await broker.positions())

        if not await _wait_until_async(_is_flat, timeout=_ROUND_TRIP_TIMEOUT_SECONDS):
            remaining = await broker.positions()
            return False, [*notes, f"게이트는 닫혔는데 **청산이 안 됐다** — 잔여 {remaining}"]
        notes.append("전량 청산 확인")

        # **재가동까지가 절차다** (`risk/kill_switch.py` 모듈 docstring 4단계). 청산만 되고
        # 다시 못 열면 다음 거래일 아침에 그 사실을 처음 알게 된다.
        pipeline._kill_switch.reset(operator="verify_kill_switch")
        await gateway.resume(operator="verify_kill_switch")
        if gateway.halted or pipeline._kill_switch.triggered:
            return False, [*notes, "재가동 실패 — reset/resume 뒤에도 정지 상태가 남는다"]
        notes.append("reset + resume 확인 — 재가동 가능")

        return True, notes
    finally:
        subscription.cancel()
        await asyncio.gather(subscription, return_exceptions=True)
        await bus.close()
        await broker.close()


def _write_report(passed: bool, notes: list[str], redis_url: str) -> Path:
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = _REPORT_DIR / f"kill_switch_verification_{now_kst():%Y%m%d}.json"
    path.write_text(
        json.dumps(
            {
                "checked_at_kst": now_kst().isoformat(),
                "redis_url": redis_url,
                "passed": passed,
                "observations": notes,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Kill Switch 실동작 검증(장후용)")
    parser.add_argument(
        "--redis-url", default=None, help="생략하면 점검 전용 Redis 컨테이너를 띄운다"
    )
    parser.add_argument(
        "--force-live-db",
        action="store_true",
        help="운영 Redis로 쏜다 — **구동 중인 G2의 주문 게이트가 닫히고 재기동 전엔 안 풀린다**",
    )
    args = parser.parse_args()

    operational = load_instance("configs").redis_url

    try:
        with contextlib.ExitStack() as stack:
            if args.redis_url:
                redis_url = args.redis_url
            elif args.force_live_db:
                redis_url = operational
            else:
                redis_url = stack.enter_context(_throwaway_redis())

            if args.force_live_db:
                print(
                    "⚠ 운영 Redis로 발행한다 — 구동 중인 G2 파이프라인이 함께 정지하고, "
                    "프로세스를 재기동할 때까지 안 풀린다(in-band reset 경로 없음).",
                    flush=True,
                )
            else:
                _assert_isolated(redis_url, operational)

            passed, notes = asyncio.run(_verify(redis_url))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — 점검을 못 한 것과 실패한 것은 다른 결과다
        print(f"점검 자체를 수행하지 못했다: {exc}", flush=True)
        print("(Docker Desktop이 꺼져 있으면 켠 뒤 다시)", flush=True)
        return 2

    for note in notes:
        print(f"  · {note}", flush=True)
    report = _write_report(passed, notes, redis_url)
    print(
        ("PASS — 화면 버튼부터 청산까지 전 구간 실동작 확인" if passed else "FAIL — 위 관측 참고")
        + f" (기록: {report})",
        flush=True,
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
