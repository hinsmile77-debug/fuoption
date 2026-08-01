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

## "한산"과 "단절"은 다르다 — 수집기 건강을 함께 본다 (2026-07-31 실측 수정)

`data_age_seconds` 하나로는 **"데이터가 안 온다"와 "거래가 없다"를 원리적으로 구분할 수
없다.** 2026-07-31에 그 한계가 그대로 사고로 나왔다: A05608이 14:21부터 마감까지 51814틱(그날
고가, 10:06에 처음 닿은 뒤 한 번도 안 넘김)에 고정된 채 **분당 1~17계약**만 체결되는 상한가
고착 구간이었는데, 체결 기반 봉의 나이만 보던 이 클래스가 그걸 데이터 단절로 읽어 하루에
의심 10회·확정 5회를 냈다. 그날 실제 거래소 CB는 없었다.

그래서 `observe(collector_healthy=...)`를 받는다. 수집기(`data/collector.py`)는 **마지막
틱 시각을 아는 유일한 곳**이고, 자기 상태를 이미 `sys.health`로 발행하고 있다 — 그 판정이
OK라면 "소켓도 살아 있고 틱도 임계 안에 들어오는데 체결이 없어 봉만 안 만들어지는" 상태,
즉 한산이다. 이 경우 SUSPECTED까지만 올리고 **CONFIRMED로는 승격하지 않는다**.

CONFIRMED만 막고 SUSPECTED는 그대로 두는 이유: SUSPECTED는 화면·로그에 "지금 뭔가 이상하다"를
남길 뿐 거래를 막지 않지만, CONFIRMED는 `gateway.halt()`를 걸어 그날 매매를 멈춘다. 오판의
대가가 비대칭이므로 승격만 막는다.

**이건 고도화 4의 "L1 탐지 / G2 판단 계층 분리, 런타임에 안 묶는다" 원칙을 부분적으로
완화한 것이다**(사용자 결정 필요 항목으로 보고했고, 권고대로 결선). 그 원칙의 근거는 "한쪽
버그가 조용히 다른 쪽을 오염시키지 않게"였는데, 2026-07-31엔 정반대로 **L1이 정상이라고 아는
사실을 G2가 못 써서** 오탐 5회가 났다. 어긋남이 사후에 드러나는 장치(`ops/integrity_report.py`
의 `analyze_data_flow_ownership()`)는 그대로 두고, 완화 범위를 CONFIRMED 승격 하나로 좁혔다.
`collector_healthy=None`(미지정)이면 기존 동작 그대로라 재생·스모크 경로엔 회귀가 없다.

## 해제 판정은 "CONFIRMED에서 나가는 모든 전이" (2026-07-31 실측 수정)

`just_resumed`는 원래 `new_phase == NORMAL and previous == CONFIRMED`, 즉 **정지에서 완전
정상으로 한 번에 돌아온 경우만** True였다. 이게 실전에서 조용히 안 걸린다는 것이 2026-07-31
로그로 확인됐다 — 그날 `CircuitBreakerConfirmed` 5회에 `CircuitBreakerResumed`는 3회뿐이었고,
15:05·15:09 두 건은 해제 로그가 아예 없다(15:07:30에 찍힌 `CircuitBreakerSuspected`는 코드상
`previous == WARNING`일 때만 나오므로, 그 사이에 CONFIRMED→WARNING 전이가 **로그 한 줄 없이**
일어났다는 증거다).

원인은 봉 확정의 구조적 지연이다: 1분봉은 "다음 분의 첫 틱"이 와야 확정 발행되므로, 한산한
구간에서 데이터가 돌아와도 첫 재평가 시점의 `data_age_seconds`가 90~150초(WARNING) 대역에
떨어지는 일이 흔하다. 그러면 CONFIRMED→WARNING→NORMAL로 내려오면서 `just_resumed`가 한 번도
True가 안 되고, 호출자(`strategy/pipeline.py`)의 자동청산·`gateway.resume()`·재진입 관망이
전부 건너뛰어진다. 2026-07-31엔 그 결과로 게이트웨이가 08:53부터 종료까지 6시간 42분간
halted로 남았다.

그래서 이제 **CONFIRMED에서 벗어나는 모든 전이**를 해제로 본다. WARNING/SUSPECTED로만 내려온
경우 "완전히 정상"은 아니지만, 그건 재진입 관망(`reentry_cooldown_minutes`)이 이미 담당하는
영역이다 — 다시 악화되면 `just_confirmed`가 또 발동해 정지가 다시 걸린다. 조기 재개의 위험보다
**정지가 영영 안 풀리는 위험**이 훨씬 크다는 판단.

같은 이유로 `CircuitBreakerSuspected` 로그도 "WARNING에서 올라온 경우"만이 아니라 **SUSPECTED에
새로 진입하는 모든 경우**(NORMAL에서 한 번에 뛴 경우 포함)로 넓혔다 — 단계가 침묵으로 건너뛰는
경로를 남기지 않는다(L18).

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

    def observe(
        self,
        data_age_seconds: float,
        as_of: datetime,
        *,
        collector_healthy: bool | None = None,
    ) -> CircuitBreakerEvent:
        """`data_age_seconds`(마지막 데이터 수신 후 경과 초)를 매 호출마다 재평가해 phase를
        갱신한다. 호출자는 이벤트 구동 경로(`handle_futures_view`)와 벽시계 워치독
        (`watch_circuit_breaker_forever`) 양쪽에서 동일하게 호출한다 — 동기 함수라 asyncio
        이벤트 루프 안에서 경쟁 조건 없이 안전하다.

        `collector_healthy`는 **수집기가 "내 연결은 멀쩡하다"고 보고 중인지**다(`sys.health`의
        `l1.collector`). `True`면 SUSPECTED까지만 올리고 CONFIRMED로는 승격하지 않는다 —
        모듈 docstring "한산과 단절" 절 참고. `None`(미지정)이면 기존 동작 그대로다."""
        cfg = self._config
        previous = self._phase

        if data_age_seconds >= cfg.confirmed_seconds and not collector_healthy:
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
        # CONFIRMED에서 벗어나는 **모든** 전이가 해제다 — NORMAL까지 한 번에 안 내려와도
        # (WARNING/SUSPECTED 경유) 해제로 본다(모듈 docstring "해제 판정" 절, 2026-07-31 실측).
        just_resumed = (
            previous == CircuitBreakerPhase.CONFIRMED and new_phase != CircuitBreakerPhase.CONFIRMED
        )

        if just_confirmed:
            self._halt_started_at = as_of
            mlog.log(
                "CircuitBreakerConfirmed",
                f"거래소 CB 추정 확정 — 데이터단절 {data_age_seconds:.0f}s",
                data_age_seconds=data_age_seconds,
                as_of=as_of,
            )
        elif new_phase == CircuitBreakerPhase.SUSPECTED and previous in (
            CircuitBreakerPhase.NORMAL,
            CircuitBreakerPhase.WARNING,
        ):
            # 상승 진입만 로깅한다 — CONFIRMED에서 내려온 경우는 바로 아래 해제 로그가 덮는다.
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
                f"거래소 CB 해제 추정 — 데이터 재수신(→{new_phase.value}, "
                f"데이터단절 {data_age_seconds:.0f}s)",
                as_of=as_of,
                halted_seconds=halted_for,
                resumed_to_phase=new_phase.value,
                data_age_seconds=data_age_seconds,
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
