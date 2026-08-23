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
        # **언제부터 듣고 있었나** (2026-08-21 F-11). 「아직 한 번도 안 왔다」는 사실만으로는
        # 「미배선」인지 「아직 주기가 안 돌았다」인지 가를 수 없다 — 가르는 것은 **경과
        # 시간**이다. 창을 연 지 3분 만에 「미배선」이라고 말하는 화면은 거짓말을 한다.
        self._created_at = now_utc()

    @property
    def created_at(self) -> datetime:
        """이 캐시가 만들어진 시각 — 세션(창)이 듣기 시작한 시점이다."""
        return self._created_at

    def listening_seconds(self, *, now: datetime | None = None) -> float:
        """듣기 시작한 뒤 흐른 시간(초) — 부재 사유 판정의 유일한 관측 근거다."""
        return ((now or now_utc()) - self._created_at).total_seconds()

    def ever_seen(self, key: str) -> bool:
        """이 세션에서 그 토픽을 **한 번이라도** 받은 적이 있는가 (2026-08-21 F-11).

        엔트리는 지우지 않으므로 "받은 적 있음"은 영구히 참이다 — 그래서 이 값이 거짓이면
        「이 창이 열린 뒤로 한 번도 안 왔다」가 정확한 사실이다.
        """
        with self._lock:
            return key in self._entries

    def update(self, key: str, message: BusMessage, *, received_at: datetime | None = None) -> None:
        """캐시를 갱신한다. `received_at`을 주면 **그 시각을 나이의 기준으로 삼는다.**

        구독 전 이력을 1회 소급 적재할 때 필요하다 (2026-08-21 F-9). 소급분에 지금
        시각을 찍으면 어제 판단이 화면에서 「0초 전」으로 보인다 — 값을 채우려다
        **화면이 거짓말을 하게 만드는 것**이라 원래 결함보다 나쁘다. 호출측이 메시지의
        원래 발행 시각을 넘기면 신선도 배지가 정직하게 회색·앰버로 뜬다.
        """
        with self._lock:
            self._entries[key] = CacheEntry(message=message, updated_at=received_at or now_utc())

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
