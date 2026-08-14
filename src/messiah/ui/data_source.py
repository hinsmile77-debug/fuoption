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
    # 메시지가 스스로 말한 발행 주기(초) — 유도 못 했으면 None (2026-08-14 G-4).
    # 화면이 배지 옆에 "주기 30분"을 적을 수 있어야 사람이 숫자를 역산하지 않는다.
    cadence_seconds: float | None = None

    @property
    def dead(self) -> bool:
        """**느려진 것과 죽은 것은 처방이 다르다** (2026-08-14 G-4).

        주기를 유도하지 못했으면 판정하지 않는다 — 모르는 것을 "죽었다"로 부르지 않는다.
        """
        if self.cadence_seconds is None or self.age_seconds is None:
            return False
        return self.age_seconds > self.cadence_seconds * _CADENCE_DEAD_MULTIPLE


# 유도된 주기의 몇 배까지 정상으로 볼 것인가 (2026-08-14 F-4).
#
# 1.5배: **1회 결손(2주기)은 반드시 걸리고 정상 간격은 안 걸린다.** 주기를 그대로 임계로
# 쓰면 매 주기 경계마다 배지가 깜빡이고, 2배로 잡으면 1회 결손을 놓친다
# (`data/bar_composer` 계열 판정과 같은 근거).
_CADENCE_STALE_MULTIPLE = 1.5

# 유도된 주기의 몇 배를 넘으면 **죽은 것**으로 보는가 (2026-08-14 G-4).
#
# `STALE`과 `DEAD`는 처방이 다르다 — 전자는 "느려졌다, 지켜본다"이고 후자는 "프로세스를
# 확인하라"다. `ops/status_board.DEAD_AFTER_MULTIPLE`이 heartbeat 축에서 같은 구분을 이미
# 하고 있었는데 판단 계열 배지엔 그 구분이 없었다. 3배(=3주기 결번)는 일시 지연으로는
# 안 나오는 값이다.
_CADENCE_DEAD_MULTIPLE = 3.0


def derived_stale_after(message, fallback: float) -> tuple[float, float | None]:
    """메시지가 스스로 말한 유효기간에서 신선도 임계를 계산한다 (2026-08-14 F-4).

    반환은 `(임계 초, 유도된 주기 초 또는 None)`.

    ## 왜 상수를 못 믿나

    `intel.futures`는 **구동 Horizon 격자**로만 나간다. 2026-08-14 기준 live 번들이 `30m`
    한 종이라 발행 주기가 1800초였는데 임계는 10초였다 — **거래일의 99.4%가 STALE**이고,
    그 앰버의 뜻("그 프로세스가 죽었거나 멈췄다")은 틀렸다. 화면이 종일 늑대소년이었다.

    `FuturesView`·`RegimeState`·`OptionsView`는 `valid_until`을 싣는다. 거기서 `ts_utc`를
    빼면 그것이 곧 그 판단의 유효 구간이자 다음 발행까지의 간격이다. **메시지가 자기 주기를
    스스로 말하므로 UI가 추측할 필요가 없다.**

    `valid_until`이 없으면(기여 전문가 0명이면 Aggregator가 그렇게 낸다) 유도 근거가 없으니
    호출부의 하한을 그대로 쓴다 — 추측해서 늘리면 진짜 정지를 늦게 잡는다.
    """
    valid_until = getattr(message, "valid_until", None)
    ts_utc = getattr(message, "ts_utc", None)
    if valid_until is None or ts_utc is None:
        return fallback, None
    try:
        cadence = (valid_until - ts_utc).total_seconds()
    except (TypeError, AttributeError):
        return fallback, None
    if cadence <= 0:
        return fallback, None
    return max(fallback, cadence * _CADENCE_STALE_MULTIPLE), cadence


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
        floor = self._stale_after.get(key, self._default_stale_after)
        threshold, cadence = derived_stale_after(message, floor)
        badge = compute_badge(self.mode, age, stale_after_seconds=threshold)
        return TopicSnapshot(message=message, badge=badge, age_seconds=age, cadence_seconds=cadence)


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
