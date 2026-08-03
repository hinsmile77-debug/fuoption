"""헤드리스 상태판 — 관측을 UI에서 분리 (2026-08-03 고도화 A).

## 왜 만들었나

지금까지 "지금 시스템이 어떤 상태인가"를 볼 수단은 Command Center UI **하나뿐**이었다.
그런데 그 UI가 5거래일 연속 네이티브 크래시로 죽었다:

    2026-07-30  08:57 크래시 → **32분간** 아무도 몰랐다
    2026-07-31  12:35 재기동 한도 소진 → **3시간** 무화면
    2026-08-03  11:25·14:20 크래시 → 각 37초·15초 공백

즉 **사고를 볼 수단이 사고로 사라지는 구조**였다. 07-31에 "포기 사실을 로그로만 남기면 그
로그를 볼 화면이 바로 그 죽은 UI"라는 걸 이미 기록했는데, 그 지적은 알림에만 적용됐고
상태 관측 자체는 여전히 UI에 묶여 있었다.

여기에 더해 매일 15:40이면 워치독이 UI를 종료한다 — **장후 리뷰 시점에는 화면이 아예 없다**.

이 모듈은 UI가 하던 구독을 **수집 프로세스 쪽으로 옮긴다**. `run_l1_daily.py`가 이미 버스에
붙어 있으므로 같은 토픽을 구독해 주기적으로 `logs/status_snapshot.json`에 스냅샷을 쓴다.
그러면 관측이 화면의 생사와 무관해진다:

    - UI가 죽어도 상태는 계속 기록된다
    - 15:40 이후에도 파일로 남아 장후 리뷰가 가능하다
    - `python -m messiah.ops.status_board`로 터미널에서 즉시 확인할 수 있다
    - **UI 자체의 생사**도 이 스냅샷이 기록한다(포트 응답 여부) — 화면 없이 화면 상태를 안다

## UI를 대체하지 않는다

이건 대시보드가 아니라 **관측의 최후 보루**다. 차트·상호작용은 여전히 UI의 몫이고, 여기엔
"지금 무엇이 살아있고 무엇이 멈췄나"만 담는다. 둘의 판정 로직이 갈리지 않도록 신선도
임계는 UI(`ui/app.py`의 `_STALE_AFTER`)와 같은 출처(`core/health.py`)를 쓴다.

## 원자적 쓰기

읽는 쪽(CLI·사람)이 쓰는 도중의 파일을 볼 수 있으므로 임시 파일 + `os.replace()`로 바꾼다 —
`data/archiver.py`가 2026-07-30 UI 크래시 대응으로 도입한 것과 같은 이유·같은 방식이다.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from messiah.core.bus import BusLike
from messiah.core.health import HEALTH_STALE_AFTER_SECONDS, health_cache_key
from messiah.core.messages import BusMessage, CircuitBreakerStatus, Health
from messiah.core.state_cache import CacheSubscriber, StateCache
from messiah.core.timeutil import now_kst, now_utc

DEFAULT_SNAPSHOT_PATH = Path("logs") / "status_snapshot.json"
DEFAULT_INTERVAL_SECONDS = 15.0

# UI와 같은 컴포넌트 목록을 **고정으로** 들고 있는다 — 동적으로 "수신된 것만" 담으면 프로세스가
# 통째로 죽었을 때 그 줄이 스냅샷에서 사라져 사고가 오히려 안 보인다(`ui/app.py`의
# `_HEALTH_COMPONENTS`와 같은 근거, `core/health.py` "침묵도 상태다").
DEFAULT_COMPONENTS: tuple[str, ...] = ("l1.collector", "l1.feature_engine", "g2.pipeline")

_CB_STALE_AFTER = 40.0  # `ui/app.py`의 `_STALE_AFTER["CircuitBreakerStatus"]`와 같은 값·같은 근거


def _cache_key_for(message: BusMessage) -> str:
    """`sys.health`는 여러 컴포넌트가 같은 토픽에 발행한다 — 타입 이름만 키로 쓰면 전부
    `"Health"` 하나로 뭉쳐 마지막 발행자만 남는다(`ui/app.py`의 동명 함수와 같은 규약)."""
    if isinstance(message, Health):
        return health_cache_key(message.component)
    return type(message).__name__


@dataclass(frozen=True)
class _Freshness:
    state: str  # "OK" / "STALE" / "NO_DATA"
    age_seconds: float | None


def _freshness(cache: StateCache, key: str, stale_after: float, now: datetime) -> _Freshness:
    age = cache.age_seconds(key, now=now)
    if age is None:
        return _Freshness("NO_DATA", None)
    return _Freshness("STALE" if age > stale_after else "OK", age)


class StatusBoard:
    """구독 캐시 → 직렬화 가능한 상태 스냅샷.

    UI 없이도 "지금 무엇이 살아있나"를 알 수 있게 하는 것이 유일한 책임이다.
    """

    def __init__(
        self,
        cache: StateCache,
        *,
        components: tuple[str, ...] = DEFAULT_COMPONENTS,
        ui_probe: Callable[[], bool] | None = None,
        now: Callable[[], datetime] = now_utc,
    ) -> None:
        self._cache = cache
        self._components = components
        self._ui_probe = ui_probe
        self._now = now

    def snapshot(self) -> dict[str, Any]:
        now = self._now()
        components: dict[str, Any] = {}
        for component in self._components:
            fresh = _freshness(
                self._cache, health_cache_key(component), HEALTH_STALE_AFTER_SECONDS, now
            )
            message = self._cache.get(health_cache_key(component))
            components[component] = {
                "state": fresh.state,
                "age_seconds": None if fresh.age_seconds is None else round(fresh.age_seconds, 1),
                "level": message.level.value if isinstance(message, Health) else None,
                "detail": message.detail if isinstance(message, Health) else None,
            }

        cb_message = self._cache.get("CircuitBreakerStatus")
        cb_fresh = _freshness(self._cache, "CircuitBreakerStatus", _CB_STALE_AFTER, now)
        circuit_breaker: dict[str, Any] = {
            "state": cb_fresh.state,
            "age_seconds": None if cb_fresh.age_seconds is None else round(cb_fresh.age_seconds, 1),
            "phase": cb_message.phase if isinstance(cb_message, CircuitBreakerStatus) else None,
            "gateway_halted": (
                cb_message.gateway_halted if isinstance(cb_message, CircuitBreakerStatus) else None
            ),
        }

        snapshot: dict[str, Any] = {
            "generated_at_kst": now_kst().isoformat(),
            "components": components,
            "circuit_breaker": circuit_breaker,
        }
        if self._ui_probe is not None:
            # **화면 없이 화면의 생사를 안다** — 이 한 줄이 07-30의 32분·07-31의 3시간
            # 무화면을 스냅샷만 보고도 알 수 있게 만든다.
            snapshot["command_center_ui"] = "UP" if self._ui_probe() else "DOWN"
        return snapshot

    def write(self, path: Path = DEFAULT_SNAPSHOT_PATH) -> None:
        """원자적으로 교체한다 — 읽는 쪽이 쓰는 도중의 파일을 볼 수 없게(모듈 docstring)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise


async def run_status_board_forever(
    bus: BusLike,
    *,
    symbol: str,
    path: Path = DEFAULT_SNAPSHOT_PATH,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    components: tuple[str, ...] = DEFAULT_COMPONENTS,
    ui_probe: Callable[[], bool] | None = None,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> None:
    """버스를 구독하며 주기적으로 스냅샷을 쓴다 — `run_l1_daily.py`의 gather에 붙는다.

    **예외를 밖으로 내지 않는다.** 이건 부가 임무이고, 관측 보조가 수집 본 임무를 죽이면
    본말전도다(같은 gather의 UI 감시·heartbeat와 같은 원칙). 실패는 조용히 삼키지 않고
    로그로 남긴다(L18).
    """
    from messiah.core import logging as mlog

    cache = StateCache()
    subscriber = CacheSubscriber(
        bus,
        ["sys.health", "sys.circuit_breaker", "intel.regime", "intel.futures", f"bar.5m.{symbol}"],
        cache,
        topic_key_fn=_cache_key_for,
    )
    board = StatusBoard(cache, components=components, ui_probe=ui_probe)

    async def _write_forever() -> None:
        while True:
            await sleep(interval_seconds)
            try:
                board.write(path)
            except OSError as exc:
                mlog.log(
                    "StatusSnapshotWriteFailed",
                    f"상태 스냅샷 기록 실패: {exc}",
                    path=str(path),
                )

    try:
        await asyncio.gather(subscriber.run_forever(), _write_forever())
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — 수집 본 임무를 죽이지 않는다
        mlog.log("StatusSnapshotWriteFailed", f"상태판 중단: {exc}", path=str(path))


def load_snapshot(path: Path = DEFAULT_SNAPSHOT_PATH) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def format_snapshot(snapshot: dict[str, Any] | None) -> str:
    """터미널 출력 — 화면이 없을 때 사람이 보는 마지막 수단이라 한 눈에 읽혀야 한다."""
    if snapshot is None:
        return (
            "상태 스냅샷 없음 — 수집 프로세스가 안 돌고 있거나 아직 첫 기록 전\n"
            "(장중이라면 그 자체가 사고 신호다)"
        )

    lines = [f"=== MESSIAH 상태판 (기록 시각 {snapshot.get('generated_at_kst', '?')}) ==="]
    ui = snapshot.get("command_center_ui")
    if ui is not None:
        lines.append(f"  Command Center UI: {'정상' if ui == 'UP' else '응답 없음'}")

    for name, info in sorted((snapshot.get("components") or {}).items()):
        state = info.get("state")
        if state == "NO_DATA":
            lines.append(f"  {name}: 데이터 없음 — 한 번도 heartbeat를 안 보냈다")
            continue
        age = info.get("age_seconds")
        mark = "정상" if state == "OK" else "응답 없음"
        detail = f" · {info['detail']}" if info.get("detail") else ""
        lines.append(f"  {name}: {mark}({info.get('level') or '?'}, {age}초 전){detail}")

    cb = snapshot.get("circuit_breaker") or {}
    if cb.get("state") == "NO_DATA":
        lines.append("  서킷브레이커: 미사용/데이터 없음")
    else:
        halted = " · 주문 게이트 정지 중" if cb.get("gateway_halted") else ""
        lines.append(f"  서킷브레이커: {cb.get('phase')}({cb.get('age_seconds')}초 전){halted}")
    return "\n".join(lines)


def main() -> int:
    """`python -m messiah.ops.status_board` — 화면 없이 상태를 본다."""
    import sys

    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(format_snapshot(load_snapshot()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
