"""State Cache — Command Center UI의 스레드 세이프 최신값 저장소 (Ver 1.0.1 §3, Ver 2.0 §9
W32~34).

Streamlit은 상호작용마다 스크립트를 처음부터 다시 실행한다("매 렌더 전체 재실행" 모델,
persistent 프로세스가 아니다) — 그래서 `MessageBus.subscribe()`를 렌더 루프 안에서 직접
걸면 매번 새 구독이 생겨 곧 유실된다. 이 모듈은 "백그라운드에서 구독을 한 번만 열고, 최신값을
스레드 세이프 딕셔너리에 계속 갱신하는" 구조로 그 문제를 푼다 — Streamlit 메인 스레드는 이
캐시를 읽기만 한다(재구독 없음).

## LIVE/STALE 판정은 여기서 하지 않는다 — `data_source.py`의 몫

`StateCache`는 "언제 갱신됐는지"(타임스탬프)만 기록한다. "지금 이 값을 LIVE로 볼지 STALE로
볼지"의 임계값은 화면마다 다르다(예: `intel.futures`는 5초 지연도 STALE이지만 `sys.health`는
30초까지 정상) — 그 판단을 여기 하드코딩하면 화면마다 실제로 다른 임계가 필요할 때 이 모듈을
고쳐야 한다. 판정 로직은 `data_source.py`가 명시적으로 드러낸다(L18 "폴백은 시끄럽게").

## 캐시 키는 메시지 타입 이름이 기본값이다

이 프로젝트의 인스턴스는 종목 1개만 다룬다(Ver 1.1 §7 복제 배포 — 여러 종목은 여러 PC로
나눈다, `instance.yaml`). 그래서 `FuturesView`/`OptionsView`/`DecisionIntent` 같은 메시지
타입 이름 하나로 캐시 키를 삼아도 충돌이 없다 — 여러 종목을 한 대시보드에서 동시에 보여줘야
하면(향후 요구사항) `topic_key_fn`으로 메시지 내용 기반 키를 주입할 수 있게 열어뒀다."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from messiah.core.bus import BusLike
from messiah.core.messages import BusMessage
from messiah.core.timeutil import now_utc


@dataclass(frozen=True)
class CacheEntry:
    message: BusMessage
    updated_at: datetime


class StateCache:
    """토픽/타입 키 → 최신 `BusMessage` + 수신시각. `threading.Lock`으로 보호 — 백그라운드
    구독 스레드가 쓰고 Streamlit 메인 스레드가 동시에 읽어도 안전하다."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, CacheEntry] = {}

    def update(self, key: str, message: BusMessage) -> None:
        with self._lock:
            self._entries[key] = CacheEntry(message=message, updated_at=now_utc())

    def get(self, key: str) -> BusMessage | None:
        with self._lock:
            entry = self._entries.get(key)
        return entry.message if entry else None

    def age_seconds(self, key: str, *, now: datetime | None = None) -> float | None:
        """`key`가 한 번도 안 갱신됐으면 None — "아직 모른다"를 "0초 전에 갱신됨"으로
        낙관 해석하지 않는다(L18과 같은 철학, `age_seconds()`가 반환하는 무한대에 가까운
        큰 값이 아니라 명시적 None으로 "데이터 자체가 없다"를 구분)."""
        with self._lock:
            entry = self._entries.get(key)
        if entry is None:
            return None
        reference = now or now_utc()
        return (reference - entry.updated_at).total_seconds()

    def snapshot_keys(self) -> list[str]:
        with self._lock:
            return list(self._entries)


class CacheSubscriber:
    """`StateCache`를 버스 구독에 연결하는 얇은 접착층 — 토픽 패턴 매칭 자체는 이미
    `MessageBus`/`InProcessBus`가 한다(`core/bus.py`), 여기서는 수신 메시지를 어떤 캐시
    키로 저장할지만 결정한다."""

    def __init__(
        self,
        bus: BusLike,
        patterns: list[str],
        cache: StateCache,
        *,
        topic_key_fn: Callable[[BusMessage], str] | None = None,
    ) -> None:
        self._bus = bus
        self._patterns = patterns
        self._cache = cache
        self._topic_key_fn = topic_key_fn or (lambda message: type(message).__name__)

    async def _handle(self, message: BusMessage) -> None:
        self._cache.update(self._topic_key_fn(message), message)

    async def run_forever(self) -> None:
        await self._bus.subscribe(self._patterns, self._handle)
