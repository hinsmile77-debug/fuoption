"""Message Bus 스키마 단일 정의 — Ver 1.1 §4.3.

모든 프로세스 간 메시지는 이 모듈의 Pydantic 모델로만 정의한다 (SYSTEM.md §4-2).
스키마 변경 시 SCHEMA_VERSION을 올리고 하위 호환 2버전을 유지한다.

규칙:
- 시각 필드는 항상 tz-aware (validator로 강제, 레슨런 L21)
- 가격은 정수 틱(price_ticks) 또는 Decimal — float 화폐 금지 (SYSTEM.md R2)
- 모든 메시지에 instance_id 포함 (멀티 PC 리포트 병합용, Ver 1.1 §7.3)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from messiah.core.timeutil import ensure_aware, now_utc

SCHEMA_VERSION = 1


# ---------------------------------------------------------------- 공통 enum


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    OPTION = "OPTION"
    NO_TRADE = "NO_TRADE"


class Horizon(str, Enum):
    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M10 = "10m"
    M15 = "15m"
    M30 = "30m"


class Regime(str, Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    HIGH_VOL = "HIGH_VOL"
    EVENT = "EVENT"
    UNKNOWN = "UNKNOWN"  # 판단 불가 → 하위 AI 보수 모드 (Ver 1.1 §3-1)


class OrderKind(str, Enum):
    ENTRY = "ENTRY"
    EXIT_FULL = "EXIT_FULL"
    EXIT_PARTIAL = "EXIT_PARTIAL"
    HEDGE = "HEDGE"
    EMERGENCY = "EMERGENCY"


class HealthLevel(str, Enum):
    OK = "OK"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


# ---------------------------------------------------------------- 베이스


class BusMessage(BaseModel):
    """모든 버스 메시지의 공통 필드."""

    schema_version: int = SCHEMA_VERSION
    msg_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    ts_utc: datetime = Field(default_factory=now_utc)
    instance_id: str = "unset"

    @field_validator("ts_utc")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return ensure_aware(v)


# ---------------------------------------------------------------- L1 Data


class Tick(BusMessage):
    """정규화된 체결 틱 (md.tick.*)."""

    symbol: str
    ts_exchange: datetime
    price_ticks: int  # 정수 틱 단위 (SYSTEM.md R2)
    qty: int
    side_hint: int = 0  # +1 매수 체결 / -1 매도 체결 / 0 불명 (틱룰 보조)
    source: str = "kis"  # 데이터 출처 명기 (레슨런 L26 — 임시 소스 추적)

    @field_validator("ts_exchange")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return ensure_aware(v)


class BarClosed(BusMessage):
    """완성봉 확정 이벤트 (bar.{horizon}) — 완성봉 규율의 기준점 (Ver 1.2 §2.2)."""

    symbol: str
    horizon: Horizon
    bar_open_kst: datetime  # 봉 시작 (거래소 시각)
    o_ticks: int
    h_ticks: int
    l_ticks: int
    c_ticks: int
    volume: int
    quality_ok: bool = True  # 틱 수 부족 등 저품질 플래그 (마흐디 방식)

    @field_validator("bar_open_kst")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return ensure_aware(v)


# ---------------------------------------------------------------- L2 Feature


class FeatureVector(BusMessage):
    """Horizon 완성봉 시점의 Feature 벡터 (feat.{horizon})."""

    symbol: str
    horizon: Horizon
    feature_set: str  # 예: "v2026.08" — 불일치 시 추론 거부 (L3)
    values: dict[str, float | None]  # feature_id -> 값 (None = NaN 마킹)
    nan_ratio: float = 0.0  # 20% 초과 시 해당 Horizon 신호 정지 (Ver 1.1 §2-2)
    valid_until: datetime | None = None  # 다음 완성봉 시각 (신선도 f_h 계산용)


# ---------------------------------------------------------------- L3 Intelligence


class ExpertView(BusMessage):
    """Horizon Expert 1개의 의견 (intel.futures 구성요소)."""

    symbol: str
    horizon: Horizon
    p_up: float
    p_flat: float
    p_down: float
    ens_std: float  # 미니 앙상블 표준편차 (불확실성 원료)
    meta_passed: bool  # Meta-Labeler 통과 여부
    model_version: str  # 번들 ID — 롤백·재현의 열쇠
    top_features: list[tuple[str, float]] = Field(default_factory=list)
    valid_until: datetime | None = None


class DecisionIntent(BusMessage):
    """Meta Decision Engine의 최종 의도 (decision.intent) — Ver 1.1 §4.3."""

    symbol: str
    side: Side
    confidence: float  # 교정된 확률 (Isotonic 후) — 미교정 사용 금지 (계명 8)
    uncertainty: float  # Conformal 구간 폭
    horizon: Horizon | None = None
    option_strategy: str | None = None  # side=OPTION일 때만
    top_features: list[tuple[str, float]] = Field(default_factory=list)  # XAI 근거
    model_version: str = ""
    latency_trace: dict[str, float] = Field(default_factory=dict)  # 구간별 누적 ms
    rationale: str = ""  # NO_TRADE 사유 포함 — 침묵이 아니라 판단 (Ver 2.0 §3.2)


# ---------------------------------------------------------------- L4 Capital


class OrderRequest(BusMessage):
    """Risk 승인·사이징 완료된 주문 요청 (capital.order_request)."""

    intent_id: str  # 원 의도 msg_id 추적
    symbol: str
    kind: OrderKind
    side: Side
    qty: int
    limit_price_ticks: int | None = None  # None = 시장가
    ttl_ms: int = 30_000
    net_expected_return: Decimal = Decimal("0")
    risk_approved_by: str = ""  # Risk Engine 버전


# ---------------------------------------------------------------- L5 Execution


class OrderAck(BusMessage):
    """브로커 접수 확인 (exec.order)."""

    request_id: str  # OrderRequest.msg_id
    broker_order_no: str
    pending_key: str  # OrderGateway pending 원자 등록 키 (L1)


class Fill(BusMessage):
    """체결 (exec.fill)."""

    broker_order_no: str
    symbol: str
    qty: int
    price_ticks: int
    ts_exchange: datetime
    pending_matched: bool  # False = 미매칭 체결 → CRITICAL 정지 (계명·L1)

    @field_validator("ts_exchange")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return ensure_aware(v)


# ---------------------------------------------------------------- SYS


class Health(BusMessage):
    """컴포넌트 heartbeat (sys.health) — 5초 주기, 15초 미수신 = 사망 판정."""

    component: str
    level: HealthLevel
    detail: str = ""
    pid: int = 0  # PID 자가 등록 (L23)


class KillSignal(BusMessage):
    """Kill Switch 발동 (sys.kill) — 전 컴포넌트 최우선 처리."""

    reason: str
    triggered_by: str  # R2 손실한도 / R11 데이터 단절 / manual / model_anomaly
