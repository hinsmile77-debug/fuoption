"""기동 자가 점검 (Self-Check) — Ver 1.1 §7.3, 레슨런 L11·L17.

모든 프로세스는 이 점검을 통과해야만 거래(또는 수집)를 개시한다.
실패 시 exit code 1 — 기동 스크립트는 이를 보고 기동을 중단한다.

사용:  python scripts/self_check.py [--configs configs] [--skip-redis]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# src 레이아웃 실행 지원
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.core.bus import registered_types  # noqa: E402
from messiah.core.config import InstanceConfig, load_instance  # noqa: E402
from messiah.core.messages import SCHEMA_VERSION  # noqa: E402
from messiah.core.timeutil import now_kst, now_utc  # noqa: E402


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


def check_config(config_dir: str) -> tuple[CheckResult, InstanceConfig | None]:
    try:
        cfg = load_instance(config_dir)
        return CheckResult("config", True, f"instance={cfg.instance_id} mode={cfg.mode}"), cfg
    except Exception as e:  # noqa: BLE001 — 자가 점검은 모든 실패를 보고
        return CheckResult("config", False, str(e)), None


def check_schema() -> CheckResult:
    n = len(registered_types())
    ok = SCHEMA_VERSION >= 1 and n >= 5
    return CheckResult("schema", ok, f"version={SCHEMA_VERSION} types={n}")


def check_timezone() -> CheckResult:
    """시각 건전성 — naive 금지 체계 확인 + KST 오프셋 검증 (L21)."""
    u, k = now_utc(), now_kst()
    ok = (
        u.tzinfo is not None
        and k.utcoffset() is not None
        and k.utcoffset().total_seconds() == 9 * 3600
    )
    return CheckResult("timezone", ok, f"utc={u.isoformat(timespec='seconds')}")


def check_git_state(mode: str) -> CheckResult:
    """계명 10: 커밋 안 된 수정을 실전에 반입하지 않는다 (live/paper에서만 강제)."""
    try:
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return CheckResult("git", mode == "dev", "git 저장소 아님 (dev에서만 허용)")
    if dirty and mode in ("live", "paper"):
        return CheckResult("git", False, f"미커밋 변경 {len(dirty.splitlines())}건 — 계명 10")
    return CheckResult("git", True, "clean" if not dirty else f"dirty({mode} 허용)")


def check_secrets(cfg: InstanceConfig) -> CheckResult:
    """live/paper는 브로커 시크릿 env 필수. 값은 절대 출력하지 않는다."""
    if cfg.mode == "dev" or cfg.broker.name == "simulator":
        return CheckResult("secrets", True, "dev/simulator — 생략")
    import os

    missing = [
        ref[4:]
        for ref in (cfg.broker.account_ref, cfg.broker.app_key_ref, cfg.broker.app_secret_ref)
        if ref.startswith("env:") and not os.environ.get(ref[4:])
    ]
    return CheckResult("secrets", not missing, f"missing={missing}" if missing else "ok")


def check_bundle(cfg: InstanceConfig) -> CheckResult:
    """live는 모델 번들 필수 + 번들 매니페스트 존재 확인 (L11 — 릴리스 일치)."""
    if cfg.mode != "live":
        return CheckResult("bundle", True, f"{cfg.mode} — 생략")
    if cfg.model_bundle in ("", "none"):
        return CheckResult("bundle", False, "live 모드에 model_bundle 미지정")
    manifest = Path("data/models") / cfg.model_bundle / "manifest.yaml"
    return CheckResult("bundle", manifest.exists(), str(manifest))


def check_redis(cfg: InstanceConfig, skip: bool) -> CheckResult:
    if skip:
        return CheckResult("redis", True, "--skip-redis")
    try:
        import asyncio

        import redis.asyncio as aioredis

        async def ping() -> bool:
            r = aioredis.from_url(cfg.redis_url)
            try:
                return bool(await r.ping())
            finally:
                await r.aclose()

        return CheckResult("redis", asyncio.run(ping()), cfg.redis_url)
    except Exception as e:  # noqa: BLE001
        return CheckResult("redis", False, str(e))


def run_all(config_dir: str = "configs", skip_redis: bool = False) -> list[CheckResult]:
    results: list[CheckResult] = []
    cfg_result, cfg = check_config(config_dir)
    results.append(cfg_result)
    results.append(check_schema())
    results.append(check_timezone())
    if cfg is not None:
        results.append(check_git_state(cfg.mode))
        results.append(check_secrets(cfg))
        results.append(check_bundle(cfg))
        results.append(check_redis(cfg, skip_redis))
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="MESSIAH self-check")
    ap.add_argument("--configs", default="configs")
    ap.add_argument("--skip-redis", action="store_true")
    args = ap.parse_args()

    results = run_all(args.configs, args.skip_redis)
    all_ok = all(r.ok for r in results)
    for r in results:
        print(f"[{'OK ' if r.ok else 'FAIL'}] {r.name:<10} {r.detail}")
    print(f"\nself-check: {'PASS — 기동 허용' if all_ok else 'FAIL — 기동 거부 (Ver 1.1 §7.3)'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
