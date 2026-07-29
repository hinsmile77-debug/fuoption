"""거래소 서킷브레이커(CB) 감지 — "미륵"(별도 선물 시스템) 실전 대응 설계 반영.

코스피 급락형 서킷브레이커(8/15/20% 하락)가 발동하면 KOSPI200 선물/옵션 거래소 매매도
함께 정지된다(한국거래소: 20분 정지 후 10분 단일가매매). KIS는 이 상황을 API로 직접
알려주지 않는다 — 미륵과 동일하게 "정상적으로 데이터를 받다가 갑자기 안 받는다"는 간접
신호(`data_age_seconds`)로 추정한다.

## 왜 CONFIRMED 이전 단계(WARNING·SUSPECTED)는 신규진입을 막지 않는가

`RiskEngine`의 R11(데이터단절 30초)이 이 클래스의 `warning_seconds`(90초)보다 훨씬 먼저
신규진입을 이미 차단한다. 따라서 이 클래스가 실제로 추가 가치를 내는 구간은 **재개 후
재진입 관망**뿐이다 — R11은 데이터가 재수신되는 즉시 풀리지만, 단일가매매 구간까지
커버하려면 R11보다 긴 관망(`reentry_cooldown_minutes`)이 필요하다. `blocks_entry()`가
바로 이 구간을 담당한다(`risk/risk_engine.py`의 R13이 소비).

## 재개 처리는 자동 복구 (KillSwitch와 다른 철학)

`KillSwitch`는 "사람 확인 후에만 재가동"이 원칙이다(이상 상황이라 사람 판단이 필요하다는
전제). 반면 CB는 **알려진·시장 전체·일시적** 이벤트라 미륵과 동일하게 자동 복구가 합당하다
— 데이터 재수신을 감지하면 `strategy/pipeline.py`가 사람 개입 없이 즉시 보유 포지션을
강제청산하고 재진입 관망 후 정상화한다(이 클래스는 상태 전이만 계산하고, 청산·정지/재개
실행은 호출자인 `TradingPipeline`의 책임 — `RiskEngine`/`KillSwitch`와 같은 "순수 판정 +
실행은 호출자" 분리 원칙).

## 임계값은 미검증 초기값

90/150/240초, 재진입 관망 10분은 미륵의 실측 보정값(6/8·6/23·6/26·7/7 CB 관측)을 출발점으로
차용했다 — MESSIAH 자체는 아직 실거래 CB를 관측한 적이 없어(2026-07-28 시점 실데이터
3거래일치) 이 값들의 타당성이 검증되지 않았다. 실측 CB 관측이 쌓이면 재조정이 필요하다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from messiah.core import logging as mlog


class CircuitBreakerPhase(str, Enum):
    NORMAL = "normal"
    WARNING = "warning"
    SUSPECTED = "suspected"
    CONFIRMED = "confirmed"


@dataclass(frozen=True)
class CircuitBreakerMonitorConfig:
    warning_seconds: float = 90.0
    suspected_seconds: float = 150.0
    confirmed_seconds: float = 240.0
    reentry_cooldown_minutes: float = 10.0  # KRX 단일가매매 10분과 동일


@dataclass(frozen=True)
class CircuitBreakerEvent:
    phase: CircuitBreakerPhase
    previous_phase: CircuitBreakerPhase
    just_confirmed: bool
    just_resumed: bool
    reentry_cooldown_until: datetime | None = None


class CircuitBreakerMonitor:
    """`RiskEngine`/`KillSwitch`와 같은 스타일 — 순수 상태머신, 실행은 호출자 책임."""

    def __init__(self, config: CircuitBreakerMonitorConfig | None = None) -> None:
        self._config = config or CircuitBreakerMonitorConfig()
        self._phase = CircuitBreakerPhase.NORMAL
        self._halt_started_at: datetime | None = None
        self._reentry_cooldown_until: datetime | None = None

    @property
    def phase(self) -> CircuitBreakerPhase:
        return self._phase

    @property
    def is_halt_active(self) -> bool:
        return self._phase == CircuitBreakerPhase.CONFIRMED

    def observe(self, data_age_seconds: float, as_of: datetime) -> CircuitBreakerEvent:
        """`data_age_seconds`(마지막 데이터 수신 후 경과 초)를 매 호출마다 재평가해 phase를
        갱신한다. 호출자는 이벤트 구동 경로(`handle_futures_view`)와 벽시계 워치독
        (`watch_circuit_breaker_forever`) 양쪽에서 동일하게 호출한다 — 동기 함수라 asyncio
        이벤트 루프 안에서 경쟁 조건 없이 안전하다."""
        cfg = self._config
        previous = self._phase

        if data_age_seconds >= cfg.confirmed_seconds:
            new_phase = CircuitBreakerPhase.CONFIRMED
        elif data_age_seconds >= cfg.suspected_seconds:
            new_phase = CircuitBreakerPhase.SUSPECTED
        elif data_age_seconds >= cfg.warning_seconds:
            new_phase = CircuitBreakerPhase.WARNING
        else:
            new_phase = CircuitBreakerPhase.NORMAL

        just_confirmed = (
            new_phase == CircuitBreakerPhase.CONFIRMED and previous != CircuitBreakerPhase.CONFIRMED
        )
        just_resumed = (
            new_phase == CircuitBreakerPhase.NORMAL and previous == CircuitBreakerPhase.CONFIRMED
        )

        if just_confirmed:
            self._halt_started_at = as_of
            mlog.log(
                "CircuitBreakerConfirmed",
                f"거래소 CB 추정 확정 — 데이터단절 {data_age_seconds:.0f}s",
                data_age_seconds=data_age_seconds,
                as_of=as_of,
            )
        elif new_phase == CircuitBreakerPhase.SUSPECTED and previous == CircuitBreakerPhase.WARNING:
            mlog.log(
                "CircuitBreakerSuspected",
                f"거래소 CB 의심 — 데이터단절 {data_age_seconds:.0f}s",
                data_age_seconds=data_age_seconds,
                as_of=as_of,
            )

        if just_resumed:
            self._reentry_cooldown_until = as_of + timedelta(minutes=cfg.reentry_cooldown_minutes)
            halted_for = (
                (as_of - self._halt_started_at).total_seconds() if self._halt_started_at else None
            )
            mlog.log(
                "CircuitBreakerResumed",
                "거래소 CB 해제 추정 — 데이터 재수신",
                as_of=as_of,
                halted_seconds=halted_for,
                reentry_cooldown_until=self._reentry_cooldown_until,
            )
            self._halt_started_at = None

        self._phase = new_phase
        return CircuitBreakerEvent(
            phase=new_phase,
            previous_phase=previous,
            just_confirmed=just_confirmed,
            just_resumed=just_resumed,
            reentry_cooldown_until=self._reentry_cooldown_until,
        )

    def blocks_entry(self, as_of: datetime) -> bool:
        """CONFIRMED(정지 중) 또는 재개 후 재진입 관망 구간이면 True — `RiskEngine`의 R13이
        이 값을 그대로 소비한다."""
        if self._phase == CircuitBreakerPhase.CONFIRMED:
            return True
        return self._reentry_cooldown_until is not None and as_of < self._reentry_cooldown_until
