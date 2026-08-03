"""Command Center 데이터 소스 — LIVE/REPLAY 명시적 전환 + 신선도 배지 (Ver 1.0.1 §3, 마흐디
L18, Ver 2.0 §9 W32~34).

마흐디 L18 "폴백이 가짜 데이터를 조용히 보여주는 위험" — 대시보드가 DB/버스 조회 실패 시
합성 데이터로 조용히 폴백해 사용자가 실시간인 줄 알고 가짜 데이터를 본 사고. 이 모듈의
방어는 두 겹이다:

1. **LIVE와 REPLAY는 사용자가 명시적으로 고른다** — 어느 한쪽이 실패했다고 다른 쪽으로
   조용히 전환하지 않는다. `ReplayDataSource`는 그 자체로 "지금 재생 중"임을 배지로 계속
   드러낸다(숨은 폴백이 아니라 드러난 모드).
2. **LIVE 모드 안에서도 신선도를 배지로 노출한다** — `LiveDataSource`가 오래된 값을 최신인
   것처럼 조용히 보여주지 않고, `age_seconds`가 임계를 넘으면 `STALE` 배지를 매긴다.

## 화면마다 다른 신선도 임계값을 허용한다

`intel.futures`는 5초만 지나도 이상하지만 `sys.health`는 30초까지 정상일 수 있다 — 그래서
`LiveDataSource`는 키별 임계값 딕셔너리를 받는다(`state_cache.py` 모듈 docstring에서 이미
예고한 설계)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from messiah.core.messages import BusMessage
from messiah.core.state_cache import StateCache

DEFAULT_STALE_AFTER_SECONDS = 30.0


class DataSourceMode(str, Enum):
    LIVE = "LIVE"
    REPLAY = "REPLAY"


class FreshnessBadge(str, Enum):
    LIVE = "LIVE"
    STALE = "STALE"
    REPLAY = "REPLAY"
    NO_DATA = "NO_DATA"  # 값 자체가 아직 한 번도 없음 — "0초 전 갱신"과 구분(L18)


@dataclass(frozen=True)
class TopicSnapshot:
    message: BusMessage | None
    badge: FreshnessBadge
    age_seconds: float | None


def compute_badge(
    mode: DataSourceMode, age_seconds: float | None, *, stale_after_seconds: float
) -> FreshnessBadge:
    if mode == DataSourceMode.REPLAY:
        return FreshnessBadge.REPLAY
    if age_seconds is None:
        return FreshnessBadge.NO_DATA
    if age_seconds > stale_after_seconds:
        return FreshnessBadge.STALE
    return FreshnessBadge.LIVE


class DataSource(Protocol):
    """`LiveDataSource`/`ReplayDataSource` 공통 계약 — `core/bus.py`의 `BusLike` Protocol과
    같은 구조적 타이핑 스타일(양쪽 구현이 이 클래스를 상속할 필요 없음)."""

    mode: DataSourceMode

    def snapshot(self, key: str) -> TopicSnapshot: ...


class LiveDataSource:
    mode = DataSourceMode.LIVE

    def __init__(
        self,
        cache: StateCache,
        *,
        stale_after_seconds: dict[str, float] | None = None,
        default_stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
    ) -> None:
        self._cache = cache
        self._stale_after = stale_after_seconds or {}
        self._default_stale_after = default_stale_after_seconds

    def snapshot(self, key: str) -> TopicSnapshot:
        message = self._cache.get(key)
        age = self._cache.age_seconds(key)
        threshold = self._stale_after.get(key, self._default_stale_after)
        badge = compute_badge(self.mode, age, stale_after_seconds=threshold)
        return TopicSnapshot(message=message, badge=badge, age_seconds=age)


class ReplayDataSource:
    """고정 스냅샷(백테스트/시뮬레이터 결과, Parquet에서 미리 읽어둔 값 등) — 실시간 버스가
    아직 없거나(G2 페이퍼 이전) 과거 세션을 복기할 때 쓴다. 항상 `REPLAY` 배지 — 절대
    `LIVE`로 오인되지 않는다(모듈 docstring 방어 ①)."""

    mode = DataSourceMode.REPLAY

    def __init__(self, snapshots: dict[str, BusMessage] | None = None) -> None:
        self._snapshots = dict(snapshots or {})

    def set(self, key: str, message: BusMessage) -> None:
        self._snapshots[key] = message

    def snapshot(self, key: str) -> TopicSnapshot:
        message = self._snapshots.get(key)
        badge = FreshnessBadge.NO_DATA if message is None else FreshnessBadge.REPLAY
        return TopicSnapshot(message=message, badge=badge, age_seconds=None)
