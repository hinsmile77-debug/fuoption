"""복제 배포 리허설 — Ver 1.1 §7 "1 PC 개발 → N PC 독립 복제 배포" (Ver 2.0 §9 W37~38).

실제로 PC 두 대를 마련하지 않고도 "코드는 전 PC 동일 바이너리, 차이는
`configs/instance.yaml` 하나뿐"(Ver 1.1 §7.2)이라는 배포 모델 자체를 이 PC 한 대에서
검증한다:

  1) 서로 다른 `instance_id`·`capital.total`을 가진 두 `InstanceConfig`를 각자의 임시
     디렉터리에서 로드해 `scripts/self_check.py`를 개별 통과시킨다(같은 코드베이스, 다른
     설정 파일 하나 — 실제 두 PC에 그대로 복제해도 같은 결과가 나와야 한다는 것의 근거).
  2) 같은 Redis(이미 실측 완료된 `messiah-redis`, `configs/instance.yaml`과 동일 서버)에
     순서대로 연결해 각자 `Health` 메시지를 발행하고, 그 메시지의 `instance_id`가 자신의
     설정과 정확히 일치하는지 확인한다 — "여러 PC의 리포트를 나중에 instance_id로 구분해
     합쳐도 된다"(Ver 1.1 §7.3 항목 3)는 것의 실제 증거. `MessageBus.publish()`가
     `msg.instance_id == "unset"`일 때만 자기 instance_id로 채우는 기존 동작(core/bus.py)을
     그대로 이용한다 — 새 코드를 추가하지 않는다.

**실제 두 번째 물리 PC 검증은 아니다** — 이 스크립트는 "설정 파일 하나로 인스턴스가
분리된다"는 코드 계약을 이 PC에서 재확인할 뿐, 서로 다른 네트워크·OS·시계 드리프트 등
실제 멀티 PC 환경에서만 드러나는 문제(Ver 1.1 §7.4 중앙 모니터링 등)는 검증 범위 밖이다.

사용: python scripts/run_replication_rehearsal.py [--redis-url redis://localhost:6380/0]
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.core.bus import TOPIC_HEALTH, MessageBus  # noqa: E402
from messiah.core.messages import Health, HealthLevel  # noqa: E402

_INSTANCES = [
    {"instance_id": "messiah-rehearsal-pc01", "capital_total": 30_000_000},
    {"instance_id": "messiah-rehearsal-pc02", "capital_total": 80_000_000},
]


def _write_instance_config(
    config_dir: Path, *, instance_id: str, capital_total: int, redis_url: str
) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "instance_id": instance_id,
        "mode": "dev",
        "broker": {"name": "simulator", "is_paper": True},
        "capital": {
            "total": capital_total,
            "daily_loss_limit_pct": 2.0,
            "margin_cap_pct": 40.0,
            "overnight_margin_cap_pct": 25.0,
            "max_overnight_positions": 2,
        },
        "universe": ["K200_MINI_FUT"],
        "model_bundle": "none",
        "redis_url": redis_url,
        "feature_set": "v2026.07",
        "futures_tick_size": "0.02",
    }
    (config_dir / "instance.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _run_self_check(config_dir: Path) -> bool:
    self_check_path = Path(__file__).parent / "self_check.py"
    result = subprocess.run(
        [sys.executable, str(self_check_path), "--configs", str(config_dir)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    print(result.stdout, end="")
    if result.returncode != 0:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode == 0


async def _verify_bus_isolation(instance_id: str, redis_url: str) -> bool:
    """이 인스턴스로 연결해 Health를 발행하고, 같은 연결로 받아 instance_id가 정확히
    이 인스턴스 것인지 확인한다(다른 인스턴스가 끼어들 수 없게 순차 실행)."""
    bus = MessageBus(redis_url, instance_id)
    await bus.connect()
    received: list[Health] = []

    async def _handler(msg):
        if isinstance(msg, Health):
            received.append(msg)

    async def _subscribe_briefly():
        try:
            await asyncio.wait_for(bus.subscribe([TOPIC_HEALTH], _handler), timeout=1.5)
        except TimeoutError:
            pass

    subscriber = asyncio.create_task(_subscribe_briefly())
    await asyncio.sleep(0.2)  # psubscribe가 실제로 걸릴 시간을 준다
    await bus.publish(
        TOPIC_HEALTH, Health(component="rehearsal", level=HealthLevel.OK, detail="ping")
    )
    await subscriber
    await bus.close()

    ok = any(h.instance_id == instance_id for h in received)
    print(f"  Health 수신: {len(received)}건, instance_id 일치: {ok}")
    return ok


async def main(args: argparse.Namespace) -> int:
    tmp_root = Path(tempfile.mkdtemp(prefix="messiah_rehearsal_"))
    all_ok = True
    try:
        for spec in _INSTANCES:
            config_dir = tmp_root / spec["instance_id"]
            _write_instance_config(
                config_dir,
                instance_id=spec["instance_id"],
                capital_total=spec["capital_total"],
                redis_url=args.redis_url,
            )
            print(f"\n=== {spec['instance_id']} (capital={spec['capital_total']:,}) ===")
            print(f"설정 파일: {config_dir / 'instance.yaml'}")

            ok = _run_self_check(config_dir)
            print(f"self_check: {'PASS' if ok else 'FAIL'}")
            all_ok = all_ok and ok

            bus_ok = await _verify_bus_isolation(spec["instance_id"], args.redis_url)
            all_ok = all_ok and bus_ok
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    verdict = "PASS — 설정 파일 하나로 인스턴스 분리 확인됨" if all_ok else "FAIL"
    print(f"\n복제 배포 리허설: {verdict}")
    return 0 if all_ok else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MESSIAH 복제 배포 리허설")
    parser.add_argument("--redis-url", default="redis://localhost:6380/0")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(_parse_args())))
