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
from datetime import datetime, timedelta
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


HORIZON_SECONDS: dict[Horizon, int] = {
    Horizon.M1: 60,
    Horizon.M3: 3 * 60,
    Horizon.M5: 5 * 60,
    Horizon.M10: 10 * 60,
    Horizon.M15: 15 * 60,
    Horizon.M30: 30 * 60,
}


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


def bar_confirm_time(bar: BarClosed) -> datetime:
    """봉이 실제로 확정(발행 가능)되는 시각 — bar_open_kst + Horizon 길이. 완성봉 규율
    (Ver 1.2 §2.2)의 기준점이라 여러 모듈이 공유한다: simulator/replay.py(재생 순서 정렬),
    models/labeling.py(Triple Barrier 진입·판정 시점 정렬)."""
    return bar.bar_open_kst + timedelta(seconds=HORIZON_SECONDS[bar.horizon])


class InvestorFlowSnapshot(BusMessage):
    """투자자매매동향(get_investor_flow) REST 폴링 원시 응답 스냅샷 (raw.investor_flow.*,
    2026-07-27 신설, `data/investor_flow_poller.py`).

    **필드 미해석 — 의도적**: Ver 1.5 §3.5가 15m Expert에 배정한 FL(수급) Feature
    (`fl_frgn_cum`/`fl_frgn_streak` 등, 외국인/기관 순매수 누적)를 만들려면 KIS 응답의
    구체 필드(외국인/기관/개인 순매수 수량·거래대금이 각각 몇 번째 필드인지)를 알아야
    하는데, 이 세션엔 그걸 확정할 근거(docs/efriend 엑셀 또는 실계좌 실측 캡처)가 없다 —
    "구현됨≠검증됨" 원칙 그대로, 이 메시지는 필드 매핑 없이 `raw` 그대로 보존한다. 폴링
    루프(스케줄러·유량제한·발행) 자체가 이번 스코프고, `fl_*` Feature 파싱은 실측 캡처가
    생기면 별도로 채울 자리(known gap, capability_matrix.md 참고)."""

    market_code: str  # FID_INPUT_ISCD(예: tr_codes.FID_MRKT_DIV_DERIVATIVES="K2I")
    sector_code: str  # FID_INPUT_ISCD_2(예: tr_codes.FID_INVESTOR_FLOW_FUTURES="F001")
    raw: dict[str, object]


class OptionQuoteSnapshot(BusMessage):
    """옵션체인 1개 다리(leg)의 REST 시세호가 스냅샷 (raw.option_chain.{underlying},
    2026-07-28 신설, `data/option_chain_poller.py`) — `KISRestClient.get_asking_price()`
    (이미 실측된 TR, `tests/broker/test_kis_rest_client.py`) 응답을 감싼다.

    **가격·Greeks 필드가 여기 없다 — 둘 다 의도적**: `InvestorFlowSnapshot` 모듈 docstring과
    동일 원칙("필드 미해석 — 의도적") — 이 세션엔 `get_asking_price()` 응답의 실제 JSON 키
    (매도/매수 호가가 몇 번째 필드인지)를 확정할 근거(docs/efriend 엑셀 또는 실계좌 실측
    캡처)가 없다. bid/ask를 잘못된 키로 파싱해 잘못된 값을 태우는 것이, 아예 안 태우고
    `raw`로 보존하는 것보다 위험하다(마흐디 L16 — 필드 단위를 확인 없이 스키마부터 정한
    사고). Greeks/IV도 KIS 원시 필드를 신뢰하지 않는다 — `strategy/options/surface.py`가
    (실측 완료된) bid/ask를 확정 파싱하게 되면 그 값으로 Black-Scholes IV·Greeks를 직접
    계산한다(Ver 1.3 §9 "IV Surface 피팅은 이론이 확립된 영역, ML 불필요") — 그러면 단위가
    처음부터 이 프로젝트가 정의한 값이라 L16류 사고 자체가 성립하지 않는다. `raw`는 응답
    전체를 그대로 보존한다(L16 처방 "원시값 로깅" — 실측 캡처가 생기면
    `normalizer.parse_option_quote()` 유형 함수를 추가해 정규화할 자리, FL Feature 갭과
    동일 패턴)."""

    underlying: str  # 예: "KOSPI200"
    option_type: str  # "C" | "P"
    strike: float
    expiry: str  # symbol_master.OptionLeg.month_label 원문(예: "콜 202608") — 정형 파싱은 소비측 몫
    symbol: str
    source: str = "kis"
    raw: dict[str, object] = Field(default_factory=dict)
    # 거래소 체결시각(ts_exchange)은 없음 — InvestorFlowSnapshot과 동일 이유(모듈 docstring):
    # raw의 실제 시각 필드를 확정 파싱할 근거가 없어 상속받은 BusMessage.ts_utc(폴링 수행
    # 시각, wall clock)만 신뢰한다.


# ---------------------------------------------------------------- L2 Feature


class FeatureVector(BusMessage):
    """Horizon 완성봉 시점의 Feature 벡터 (feat.{horizon})."""

    symbol: str
    horizon: Horizon
    feature_set: str  # 예: "v2026.08" — 불일치 시 추론 거부 (L3)
    values: dict[str, float | None]  # feature_id -> 값 (None = NaN 마킹)
    nan_ratio: float = 0.0  # 20% 초과 시 해당 Horizon 신호 정지 (Ver 1.1 §2-2)
    valid_until: datetime | None = None  # 다음 완성봉 시각 (신선도 f_h 계산용)


class GreeksProfile(BaseModel):
    """옵션 포지션/후보 1개의 그릭스 프로파일 — `BusMessage`가 아니라 다른 메시지에 내장되는
    값 객체(Ver 1.3 §10 `StrategyCandidate.greeks`, `broker/base.py`
    `BrokerPosition.greeks`). `strategy/options/surface.py`의 Black-Scholes 계산에서만
    나온다(§0 참고) — 그래서 단위가 항상 이 프로젝트가 정의한 값으로 고정된다(마흐디 L16
    "unit은 스키마 필수 항목" 처방):

    - delta: 기초자산(지수) 1pt 변화당 옵션가 변화(pt), 무차원 비율 [-1, 1]
    - gamma: 기초자산 1pt 변화당 delta 변화(pt^-1)
    - theta: 하루 경과당 옵션가 변화(index pt/day, 통상 음수)
    - vega: IV 1%p(0.01) 변화당 옵션가 변화(index pt)
    - iv: Black-Scholes 내재변동성(연율화, 예: 0.18 = 18%)
    """

    delta: float
    gamma: float
    theta: float
    vega: float
    iv: float


# ---------------------------------------------------------------- L3 Intelligence


class RegimeState(BusMessage):
    """국면 판정 결과 (intel.regime) — Ver 1.1 §3-1, Ver 1.6 §3.1.

    통계층(HMM)이 낸 상태를 명명층이 Regime으로 매핑한 뒤, 규칙층이 필요시 덮어쓴다
    (Ver 1.6 §3.1 하이브리드 구조 — strategy/regime/service.py 참고)."""

    symbol: str
    regime: Regime
    confidence: float  # 상태 확률(HMM posterior 최댓값) — 규칙 오버라이드 시 1.0
    state_duration_bars: int  # 현재 국면이 몇 봉째 지속 중인지(구동 Horizon 기준)
    transition_prob: dict[str, float] = Field(default_factory=dict)  # 국면명 -> 다음 전이확률
    rule_override: str | None = None  # 규칙층이 강제한 사유(없으면 통계층 그대로)
    valid_until: datetime | None = None


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


class FuturesView(BusMessage):
    """Futures AI 통합 출력 (intel.futures) — Aggregator 산출물, Ver 1.2 §7.2, Ver 2.0 §9 W24~26.

    개별 `ExpertView`는 "intel.futures 구성요소"(ExpertView 자체 docstring)이고, 이 메시지가
    Regime 가중치·Meta-Labeler 통과여부·불확실성·신선도를 반영해 실제로 합성한 최종 출력이다
    (`strategy/futures/aggregator.py`)."""

    symbol: str
    score: float  # S = Σ_h w(regime,h)×(P_h(+1)−P_h(−1))×meta_h×(1−u_h)×f_h (Ver 1.2 §7.2)
    agg_p_up: float  # 가중평균 P(+1) — score와 동일 가중치로 정규화(Σ가중치=1 기준)
    agg_p_down: float  # 가중평균 P(−1)
    uncertainty: float  # 가중평균 정규화 불확실성 [0,1] — 기여 Horizon이 없으면 1.0(최대 보수)
    dispersion: float  # Horizon 간 방향 의견 분산(Ver 2.0 §3.1 ③ NO TRADE 판정 입력)
    regime: Regime
    n_experts: int  # 이번 집계에 실제로 기여한(meta 통과 + 신선) Horizon 수
    model_versions: list[str] = Field(default_factory=list)  # 기여 Expert들의 번들 ID(중복 제거)
    top_features: list[tuple[str, float]] = Field(default_factory=list)  # XAI 근거 top5
    valid_until: datetime | None = None  # 기여 Horizon 중 가장 이른 다음 갱신 시각


class StrategyLeg(BaseModel):
    """옵션 전략 후보 1개 다리 — `broker/kis/symbol_master.OptionLeg`(체인 조회 결과, dataclass)
    와는 다른 값 객체다: 이쪽은 "델타 목표로 고른 구성 다리"(`strategy/options/evaluator.py`
    `build_legs()`)라 목표/실제 델타·매도여부 같은 전략 구성 정보를 담는다. `symbol`은
    실제 체인 종목코드 매핑 전이면 None(§0 참고 — 이 프로젝트는 아직 옵션체인 그릭스 필드를
    실측 파싱하지 않아 IVSurface가 실제 체인 종목이 아니라 이론가 계산에서만 나온다)."""

    option_type: str  # "C" | "P"
    strike: float
    dte: int
    is_short: bool
    delta: float  # 다리 구성에 쓰인 목표 델타(부호 포함 — 콜 양수, 풋 음수)
    symbol: str | None = None


class StrategyCandidate(BaseModel):
    """옵션 전략 후보 1개의 평가 결과 (Ver 1.3 §5.1~5.2, §10) — `intel.options`
    (`OptionsView.candidates`)의 구성요소. 금액이 아니라 **지수 포인트** 단위다(`GreeksProfile`
    과 동일 단위 계약 — KRW 환산은 소비측이 `point_value_krw`로, `risk/sizer.py`의 선물
    승수와 마찬가지로 옵션 승수도 아직 실측 전이라 이 메시지 자체는 포인트로 남긴다)."""

    structure: str  # strategy/options/matrix.py 구조 이름 상수
    legs: list[StrategyLeg]
    net_expected_return: Decimal  # 확률가중 기대손익 − 총비용, 지수 포인트
    pop: float  # Probability of Profit [0,1]
    max_loss: Decimal | None  # None = 무한(이 구현은 §6-1 준수로 항상 유한이어야 정상)
    reward_risk: float | None  # 기대이익/최대손실 — max_loss가 0/None이면 None
    greeks: GreeksProfile  # 후보 진입 시점 합산 Greeks
    rationale: dict[str, object] = Field(default_factory=dict)  # 매트릭스 셀·IV Rank 등 XAI


class OptionsView(BusMessage):
    """Options AI 통합 출력 (intel.options) — Ver 1.3 §5.2, §10 `OptionsAIService` 산출물.
    `candidates`가 비어 있으면 `no_option_reason`이 항상 채워진다(Ver 1.3 §5.2 "NO_OPTION도
    명시적 출력이다 — 침묵과 관망을 구분한다", `DecisionIntent.rationale`과 동일 철학)."""

    symbol: str
    underlying: str
    candidates: list[StrategyCandidate] = Field(default_factory=list)  # 상위 3개
    no_option_reason: str | None = None
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
