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
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from messiah.core.bus import BusLike
from messiah.core.health import HEALTH_STALE_AFTER_SECONDS, health_cache_key
from messiah.core.messages import (
    CIRCUIT_BREAKER_PHASE_WARMUP,
    BusMessage,
    CircuitBreakerStatus,
    Health,
)
from messiah.core.state_cache import CacheSubscriber, StateCache
from messiah.core.timeutil import now_kst, now_utc
from messiah.core.version import (
    PROCESS_GIT_SHA,
    assess_version_drift,
    head_git_sha,
    worktree_dirty_files,
)
from messiah.ops import loss_ledger
from messiah.ops import verdict as verdict_mod

DEFAULT_SNAPSHOT_PATH = Path("logs") / "status_snapshot.json"
DEFAULT_INTERVAL_SECONDS = 15.0

# `os.replace` 재시도 간격 (2026-08-14 F-10). 길이가 곧 시도 횟수다 — 마지막 원소는
# 쓰이지 않고(그 시도에서 예외를 올린다) 자리만 지킨다. 총 대기 0.4초로 15초 주기 안에
# 넉넉히 들어간다. Windows 파일 경합은 대개 수십 ms 안에 풀린다.
_REPLACE_BACKOFF_SECONDS = (0.1, 0.3, 0.0)

# 이만큼 연속 실패하면 "한 번 미끄러졌다"가 아니라 "멈췄다"이다 — 15초 주기 기준 1분.
_STALL_AFTER_CONSECUTIVE = 4

# UI와 같은 컴포넌트 목록을 **고정으로** 들고 있는다 — 동적으로 "수신된 것만" 담으면 프로세스가
# 통째로 죽었을 때 그 줄이 스냅샷에서 사라져 사고가 오히려 안 보인다(`ui/app.py`의
# `_HEALTH_COMPONENTS`와 같은 근거, `core/health.py` "침묵도 상태다").
# `l1.composer`는 2026-08-05 장중에 추가됐다. 그날 상위 Horizon 봉의 3~17%가 사라지는 동안
# 이 세 축은 전부 OK였다 — 나머지가 **신선도**("최근에 받았나")를 재는 반면 합성 손상은
# "받은 것을 온전히 합쳤나"라서, 볼 축이 아예 없었다(`data/bar_composer.health()`).
DEFAULT_COMPONENTS: tuple[str, ...] = (
    "l1.collector",
    "l1.feature_engine",
    "l1.composer",
    "g2.pipeline",
)

_CB_STALE_AFTER = 40.0  # `ui/app.py`의 `_STALE_AFTER["CircuitBreakerStatus"]`와 같은 값·같은 근거


def _cache_key_for(message: BusMessage) -> str:
    """`sys.health`는 여러 컴포넌트가 같은 토픽에 발행한다 — 타입 이름만 키로 쓰면 전부
    `"Health"` 하나로 뭉쳐 마지막 발행자만 남는다(`ui/app.py`의 동명 함수와 같은 규약)."""
    if isinstance(message, Health):
        return health_cache_key(message.component)
    return type(message).__name__


# heartbeat가 임계의 몇 배를 넘으면 **죽은 것**으로 보는가 (2026-08-07 고도화 2).
#
# `STALE`과 `DEAD`는 처방이 다르다: 전자는 "느려졌다, 지켜본다"이고 후자는 "프로세스를
# 확인하라"다. 종전엔 둘이 같은 `STALE`이라, 2026-08-07 13:41에 수집기가 죽은 뒤에도
# 화면·스냅샷은 "응답 없음(N초)"만 반복했다 — 그 숫자가 커지는 것을 사람이 세고 있어야 했다.
#
# 6배(=180초): heartbeat 주기 10초 기준 18회 연속 결번이다. 일시적 지연으로는 안 나오고,
# G2의 CB가 `SUSPECTED`로 올라가는 180초와 같은 값이라 두 축이 같은 순간을 가리킨다.
DEAD_AFTER_MULTIPLE = 6.0


@dataclass(frozen=True)
class _Freshness:
    state: str  # "OK" / "STALE" / "DEAD" / "NO_DATA"
    age_seconds: float | None


def _freshness(cache: StateCache, key: str, stale_after: float, now: datetime) -> _Freshness:
    age = cache.age_seconds(key, now=now)
    if age is None:
        return _Freshness("NO_DATA", None)
    if age > stale_after * DEAD_AFTER_MULTIPLE:
        return _Freshness("DEAD", age)
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
        symbol: str | None = None,
    ) -> None:
        self._cache = cache
        self._components = components
        self._ui_probe = ui_probe
        self._now = now
        self._symbol = symbol

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
                "git_sha": message.git_sha if isinstance(message, Health) else None,
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

        # **어느 코드가 이 상태를 보고했나** (2026-08-05 3차, P0-1). 화면이 죽었을 때 이
        # 파일이 유일한 관측 수단인데(모듈 docstring), 버전 축이 없으면 "구버전이 보낸 초록"과
        # "최신 코드가 보낸 초록"이 파일에서도 똑같이 보인다 — UI의 버전 스트립과 같은 근거·
        # 같은 판정기를 쓴다(`core/version.py`).
        dirty_files = worktree_dirty_files()
        drift = assess_version_drift(
            process_sha=PROCESS_GIT_SHA,
            head_sha=head_git_sha(),
            component_shas={
                name: info["git_sha"] or ""
                for name, info in components.items()
                if info["git_sha"] is not None
            },
        )

        snapshot: dict[str, Any] = {
            "generated_at_kst": now_kst().isoformat(),
            "code_version": {
                "process_git_sha": PROCESS_GIT_SHA,
                "head_git_sha": head_git_sha(),
                "stale": drift.stale,
                "summary": drift.summary,
                # **커밋과 실린 코드가 같다고 말하는 계기가 커밋을 안 봤다** (2026-08-20 F-2).
                #
                # 위 `stale`은 두 SHA만 대조한다. 그런데 이 저장소는 워킹트리를 직접
                # 임포트하므로, 미커밋 변경이 있으면 두 SHA가 같아도 프로세스는 그 미커밋
                # 코드로 돈다. 2026-08-19 저녁 구현이 커밋 없이 끝난 날 `stale`은 false였고
                # (그 자체로는 옳다) 다음 날 개장이 통째로 갔다.
                #
                # `None`은 **미측정**이다(git 없음/조회 실패) — 0으로 적으면 "깨끗하다"가
                # 되어 L18을 어긴다.
                "worktree_dirty_files": dirty_files,
                "worktree_dirty": None if dirty_files is None else dirty_files > 0,
            },
            "components": components,
            "circuit_breaker": circuit_breaker,
            # **오늘 이미 잃은 것** (2026-08-10 B-2, `ops/loss_ledger.py`). 위 컴포넌트 넷은
            # *"지금 살아 있나"*에 답하고, 이 줄은 *"오늘 이미 잃은 것이 있나"*에 답한다.
            # 2026-08-10에 넷이 종일 초록인 채로 38분이 사라졌고, 그 사실이 사람 눈에 닿은
            # 것은 15:45 장후 리포트였다 — 둘은 다른 질문이고 화면에 둘 다 있어야 한다.
            "irrecoverable_loss": loss_ledger.current().to_dict(),
        }
        # **오늘 판단이 가능한가** (2026-08-14 G-3). 위의 `components`는 *"지금 살아 있나"*에
        # 답하고 이 한 줄은 *"살아 있는데 쓸 수 있나"*에 답한다. 2026-08-14 10:51에 컴포넌트
        # 4종 중 3종이 `OK`였고 자가점검도 PASS였는데 시스템은 종일 판단 불능이었다 —
        # 세 화면이 각자 정상을 말하는 동안 그 사실을 말하는 축이 하나도 없었다.
        snapshot["verdict"] = self._verdict(components).to_dict()
        if self._symbol is not None:
            # **오늘 이 시스템이 실제로 보고 있는 종목** (2026-08-14 F-3).
            #
            # 화면이 이 값을 **다시 해석하지 않고 읽게** 하기 위한 자리다. 2026-08-14 첫 월물
            # 롤에서 UI는 `DEFAULT_SYMBOL = "A05608"`(하드코딩, R4 위반)을 들고 있었고 수집은
            # `A05609`를 하고 있었다 — 화면은 만기된 월물의 어제 차트를 그리면서 붉은 경보로
            # *"봉 적재 정지 의심, 수집기를 먼저 확인할 것"* 을 띄웠다. 수집기는 멀쩡했다.
            #
            # 해석 경로를 하나 더 만들면(UI가 마스터파일을 따로 읽는 등) 갈릴 자리가 하나 더
            # 생긴다. **해석이 아니라 조회가 되면 갈라질 수 없다.**
            snapshot["trading_symbol"] = self._symbol
        if self._ui_probe is not None:
            # **화면 없이 화면의 생사를 안다** — 이 한 줄이 07-30의 32분·07-31의 3시간
            # 무화면을 스냅샷만 보고도 알 수 있게 만든다.
            snapshot["command_center_ui"] = "UP" if self._ui_probe() else "DOWN"
        return snapshot

    def _verdict(self, components: dict[str, Any]) -> verdict_mod.Verdict:
        """판단 가용성 — 살아 있는 것과 쓸 수 있는 것은 다른 질문이다 (2026-08-14 G-3).

        사유마다 **출처 표면**을 적는다(G-6). 다만 `missing_from`(그 사실이 없는 표면)은
        여기서 못 채운다 — 이 프로세스는 로그를 읽지 않기 때문이다. **표면 대조는 둘 다
        읽는 장후 리포트가 한다**(`ops/integrity_report._verdict_surface_gaps`). 모르는 것을
        추측해서 채우면 그 자체가 또 하나의 거짓 표면이 된다.
        """
        reasons: list[verdict_mod.Reason | None] = []

        engine = components.get("l1.feature_engine") or {}
        if engine.get("level") == "WARN" and engine.get("detail"):
            reasons.append(
                verdict_mod.Reason(
                    code=verdict_mod.REASON_NAN_RATIO_EXCEEDED,
                    detail=str(engine.get("detail")),
                    sources=("status_snapshot",),
                )
            )

        regime = self._cache.get("RegimeState")
        regime_value = getattr(getattr(regime, "regime", None), "value", None)
        if regime_value == "UNKNOWN":
            reasons.append(
                verdict_mod.Reason(
                    code=verdict_mod.REASON_REGIME_UNKNOWN,
                    detail="국면 UNKNOWN — MetaDecisionEngine 게이트 ②가 전건 NO_TRADE로 접는다",
                    sources=("intel.regime",),
                )
            )

        futures = self._cache.get("FuturesView")
        if futures is not None and getattr(futures, "n_experts", None) == 0:
            reasons.append(
                verdict_mod.Reason(
                    code=verdict_mod.REASON_NO_EXPERT_CONTRIBUTION,
                    detail="기여 전문가 0명 — 통합점수가 구조적으로 0이다",
                    sources=("intel.futures",),
                )
            )
        return verdict_mod.build(reasons)

    def write(
        self,
        path: Path = DEFAULT_SNAPSHOT_PATH,
        *,
        sleep: Callable[[float], Any] = time.sleep,
    ) -> None:
        """원자적으로 교체한다 — 읽는 쪽이 쓰는 도중의 파일을 볼 수 없게(모듈 docstring).

        ## 교체는 재시도한다 (2026-08-14 F-10)

        Windows에서 `os.replace`는 다른 프로세스가 그 파일을 연 순간 `WinError 5`(액세스
        거부)로 튕긴다. 이 스냅샷은 UI·점검 도구·사람이 동시에 읽는 파일이라 경합이
        구조적이다 — 2026-08-14에 15초 주기 하루치 중 **2회** 났다.

        빈도가 낮다고 둘 수 없는 이유: 이 파일이 못 써진 순간이 곧 관측의 공백이고,
        하필 사람이 화면을 열어 본 순간(= 사고를 의심한 순간)에 경합 확률이 가장 높다.

        마지막 시도까지 실패하면 예외를 그대로 올린다 — 삼키면 호출부가 "썼다"고 믿는다.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            for attempt, backoff in enumerate(_REPLACE_BACKOFF_SECONDS, start=1):
                try:
                    os.replace(tmp, path)
                    return
                except OSError:
                    if attempt == len(_REPLACE_BACKOFF_SECONDS):
                        raise
                    sleep(backoff)
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
    board = StatusBoard(cache, components=components, ui_probe=ui_probe, symbol=symbol)

    async def _write_forever() -> None:
        # **한 번 미끄러진 것과 영영 죽은 것은 다른 사건이다** (2026-08-14 F-10).
        # 재시도까지 실패한 1회는 경합이고, 연속 실패는 파일이 잠겼거나 디스크가 죽은 것이다.
        consecutive = 0
        stalled_announced = False
        while True:
            await sleep(interval_seconds)
            try:
                board.write(path)
            except OSError as exc:
                consecutive += 1
                mlog.log(
                    "StatusSnapshotWriteFailed",
                    f"상태 스냅샷 기록 실패({consecutive}회 연속): {exc}",
                    path=str(path),
                    consecutive=consecutive,
                )
                if consecutive >= _STALL_AFTER_CONSECUTIVE and not stalled_announced:
                    # 한 번만 운다 — 계속 울면 그 자체가 잡음이 되고, 회복은 아래 줄이 말한다.
                    stalled_announced = True
                    mlog.log(
                        "StatusSnapshotStalled",
                        f"상태 스냅샷이 {consecutive}회 연속 실패 — 관측이 멈췄다"
                        f"(약 {consecutive * interval_seconds:.0f}초)",
                        path=str(path),
                        consecutive=consecutive,
                    )
            else:
                if stalled_announced:
                    mlog.log(
                        "StatusSnapshotResumed",
                        f"상태 스냅샷 기록 재개 — {consecutive}회 연속 실패 뒤",
                        path=str(path),
                        after_failures=consecutive,
                    )
                consecutive = 0
                stalled_announced = False

    try:
        await asyncio.gather(subscriber.run_forever(), _write_forever())
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — 수집 본 임무를 죽이지 않는다
        # **개명** (2026-08-14 F-10). 종전엔 이 줄과 위의 1회 실패가 같은 태그를 썼다 —
        # 하나는 "이번 주기를 놓쳤다"이고 이건 "상태판이 그날 내내 죽었다"인데, 같은 이름
        # 같은 심각도로 나가면 사람이 둘을 구분할 수 없다(R6: 태그 1개 = 심각도 1개).
        mlog.log(
            "StatusBoardHalted", f"상태판 중단 — 오늘 남은 시간 관측 없음: {exc}", path=str(path)
        )


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
    version = snapshot.get("code_version") or {}
    if version:
        mark = "⚠ " if version.get("stale") else "  "
        summary = version.get("summary", "코드 버전 미상")
        # 미커밋 건수를 **같은 줄에** 붙인다 (2026-08-20 F-2). 별도 줄로 빼면 "코드 버전"을
        # 읽은 사람이 그 아래를 안 볼 수 있다 — 두 값은 같은 질문("지금 무엇이 도는가")의
        # 두 얼굴이다.
        dirty = version.get("worktree_dirty_files")
        if dirty is None:
            summary += " · 미커밋 미측정"
        elif dirty > 0:
            summary += f" · ⚠ 미커밋 {dirty}파일(src/scripts)"
        lines.append(f"{mark}{summary}")
    ui = snapshot.get("command_center_ui")
    if ui is not None:
        lines.append(f"  Command Center UI: {'정상' if ui == 'UP' else '응답 없음'}")

    # 컴포넌트 목록 **위**에 둔다 (2026-08-10 B-2). 아래 줄들은 "지금 살아 있나"에 답하고
    # 이 줄은 "오늘 이미 잃은 것이 있나"에 답하는데, 후자가 사람이 먼저 알아야 하는 것이다 —
    # 2026-08-10엔 넷이 전부 초록인 채로 38분이 이미 사라진 뒤였다.
    loss = snapshot.get("irrecoverable_loss") or {}
    if loss.get("summary"):
        lines.append(f"  {'✅' if loss.get('clean') else '❌'} {loss['summary']}")

    for name, info in sorted((snapshot.get("components") or {}).items()):
        state = info.get("state")
        if state == "NO_DATA":
            lines.append(f"  {name}: 데이터 없음 — 한 번도 heartbeat를 안 보냈다")
            continue
        age = info.get("age_seconds")
        level = info.get("level")
        # **"모른다"를 "정상"으로 쓰지 않는다** (2026-08-05 2차, 고도화 3). 신선도(state)와
        # 자가 판정(level)은 다른 축이다 — heartbeat는 제때 왔는데(state=OK) 그 내용이
        # "판정할 근거가 없다"(level=UNKNOWN)일 수 있고, 그게 장전 구간의 실제 상태다.
        if state != "OK":
            mark = "응답 없음"
        elif level == "UNKNOWN":
            mark = "판정 불가"
        else:
            mark = "정상"
        detail = f" · {info['detail']}" if info.get("detail") else ""
        lines.append(f"  {name}: {mark}({level or '?'}, {age}초 전){detail}")

    cb = snapshot.get("circuit_breaker") or {}
    if cb.get("state") == "NO_DATA":
        lines.append("  서킷브레이커: 미사용/데이터 없음")
    else:
        halted = " · 주문 게이트 정지 중" if cb.get("gateway_halted") else ""
        # `warmup`은 phase 문자열 그대로 두면 "정상 아님"으로도 "이상"으로도 안 읽힌다 —
        # 화면 배지와 같은 문구를 쓴다(2026-08-11 F-2, `ui/app._CB_PHASE_LABEL`).
        phase = cb.get("phase")
        if phase == CIRCUIT_BREAKER_PHASE_WARMUP:
            phase = "웜업 — 첫 봉 대기(판정 전)"
        lines.append(f"  서킷브레이커: {phase}({cb.get('age_seconds')}초 전){halted}")
    return "\n".join(lines)


def main() -> int:
    """`python -m messiah.ops.status_board` — 화면 없이 상태를 본다."""
    import sys

    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(format_snapshot(load_snapshot()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
