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
    """정규화된 체결 틱 (md.tick.*).

    ## L1 호가가 여기 있는 이유 (2026-08-04, F2)

    2026-08-04까지 이 메시지는 symbol/시각/가격/수량 4개만 들고 있었고, 그래서 MS(마이크로
    구조) 카테고리 30개가 통째로 "데이터 없음"으로 미착수였다. 그런데 **호가는 이미 매 틱
    도착하고 있었다** — H0IFCNT0 프레임은 50필드고 그중 idx34~37이 매도호가1/매수호가1/
    잔량/잔량인데(2026-07-22 라이브 캡처 교차검증), 파서가 4개만 읽고 나머지를 버렸다.
    데이터가 없던 게 아니라 스키마가 좁았던 것이다.

    `side_hint`도 그래서 항상 0이었다(틱룰을 계산할 bid/ask가 없었으므로). 이제 채운다 —
    아래 필드 주석 참고.

    ## `raw_fields`에 프레임 전체를 담는다

    idx34~37 말고도 미결제약정·이론가·총잔량·체결강도로 보이는 필드가 더 있지만, **필드
    위치를 실측으로 확정하기 전에는 이름을 붙이지 않는다**(R8/L16 — 단위를 확인 안 하고
    스키마부터 정해 5일치 데이터가 조용히 잘린 마흐디 사고). 대신 프레임 전체를 문자열
    그대로 실어 나르고 `data/tick_archiver.py`가 통째로 남긴다 — 그러면 다음 주에 실측으로
    매핑을 확정할 때 **소급해서 쓸 수 있는 데이터가 이미 쌓여 있다**.

    틱은 봉과 달리 과거 조회 경로가 없다. 안 받아둔 필드는 영원히 없다.
    """

    symbol: str
    ts_exchange: datetime
    price_ticks: int  # 정수 틱 단위 (SYSTEM.md R2)
    qty: int
    # +1 매수주도 / -1 매도주도 / 0 불명. 아래 호가가 있으면 quote rule(체결가 vs 미드)로
    # 채운다 — `data/normalizer._quote_rule_side()`의 한계(동시 스냅샷 근사)도 거기 명시.
    side_hint: int = 0
    source: str = "kis"  # 데이터 출처 명기 (레슨런 L26 — 임시 소스 추적)

    # L1 호가 (H0IFCNT0 idx34~37, 2026-07-22 실측 교차검증). 정수 틱 — 가격이므로 R2 적용.
    # None은 "이 피드가 호가를 안 실어 온다"(옵션 체결 프레임 등)이지 "잔량 0"이 아니다.
    bid1_ticks: int | None = None
    ask1_ticks: int | None = None
    bid_qty1: int | None = None
    ask_qty1: int | None = None

    # 원시 프레임의 전 필드(문자열 그대로). 해석하지 않는다 — 위 docstring 참고.
    raw_fields: tuple[str, ...] = ()

    @field_validator("ts_exchange")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return ensure_aware(v)

    @property
    def mid_ticks(self) -> float | None:
        """미드 가격 — 호가가 둘 다 있을 때만. MS 피처(`ms_spread`/`ms_microprice` 등)와
        `side_hint` 판정이 공유하는 유일한 정의(두 곳에서 따로 계산하면 갈린다)."""
        if self.bid1_ticks is None or self.ask1_ticks is None:
            return None
        return (self.bid1_ticks + self.ask1_ticks) / 2.0


class BarSession(str, Enum):
    """봉이 속한 세션 구분 (2026-07-31 신설).

    `PRE_OPEN`은 정규장 개시(09:00) 이전에 들어온 프린트로 만들어진 봉이다. 이 구분이 필요한
    이유는 2026-07-31 실측이다: 그날 08:45~09:04의 20봉이 **전부 `o=h=l=c=46633`으로 완전히
    고정**돼 있었고(분당 1~4계약), 09:05에 49488로 튀며 실제 거래가 시작됐다 — 6.1% 점프다.
    같은 구간이 07-29·07-30엔 실제로 움직였고 거래량도 500대였으므로(그래서 그때는 "실체결로
    보인다"고 판단했다), **날마다 성격이 다르다**는 것이 확인된 셈이다.

    2026-07-30에 "장전은 웜업만, 거래는 안 한다"고 정했지만 그 결정은 **주문만** 막았다 —
    아카이브·피처 웜스타트·차트에는 그대로 들어갔고, 09:05봉은 스테일 값과 실거래 값을 합친
    6% 범위의 합성 봉이 되어 ATR·변동성 계열을 오염시켰다. 이 필드는 그 데이터를 **버리지
    않되 지울 수 없게 표시**하기 위한 것이다(파기는 되돌릴 수 없고, 소비자별 정책은 나중에
    바꿀 수 있다).
    """

    REGULAR = "regular"
    PRE_OPEN = "pre_open"


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
    # 기본값이 REGULAR인 이유: 이 필드가 생기기 전에 적재된 Parquet과 재생·스모크 경로가
    # 전부 정규장 데이터라, 없는 값을 정규장으로 읽는 것이 사실과 맞다(`data/archiver.py`).
    session: BarSession = BarSession.REGULAR

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
    """옵션체인 1개 다리(leg)의 REST 시세 스냅샷 (raw.option_chain.{underlying},
    2026-07-28 신설, `data/option_chain_poller.py`).

    ## 원천을 `get_asking_price()` → `get_quote()`로 바꿨다 (2026-08-04 실측)

    처음엔 `get_asking_price()`(시세호가)를 감쌌는데, 실계좌로 옵션 종목을 실제 호출해 응답
    전문을 뜬 결과 **두 TR이 주는 것이 완전히 달랐다**:

        get_asking_price(O) → output1 시세 8필드 + output2 **5단계 호가 35필드**
                              (futs_askp1~5 / futs_bidp1~5 / askp_rsqn1~5 / ...)
        get_quote(O)        → output1 **29필드**: futs_prpr(현재가) · acml_vol(거래량)
                              · hts_otst_stpl_qty(**미결제약정**) · delta_val · gama
                              · theta · vega · rho · hts_ints_vltl(**내재변동성**)
                              · hts_thpr(이론가) · acpr(행사가) · hts_rmnn_dynu(잔존일수)
                              · futs_last_tr_date(최종거래일)
                              output2 KOSPI 종합지수 · output3 **KOSPI200 현물지수**

    Ver 1.5 §3.5~3.6이 배정한 OP Feature를 하나씩 대조하면 **전부 `get_quote` 쪽**이다 —
    `op_iv_chg`(IV) · `op_pcr_vol`(거래량) · `op_pcr_oi`(OI) · `op_gex`(감마×OI) ·
    `op_skew_rr25`(델타+IV). **호가(bid/ask)를 쓰는 OP Feature는 현재 스코프에 하나도 없다.**
    호가가 필요해지는 건 Options AI의 집행 품질·유동성 선발(% 스프레드, Cao-Wei)이고 그건
    한참 뒤다. 그래서 지금은 `get_quote` 하나만 부른다 — 다리당 2회 호출은 유량 예산을
    두 배로 먹는데(`data/option_chain_poller.py` 예산 계산) 그 절반이 몇 달간 소비처가 없다.

    ## 그래도 필드는 해석하지 않는다 — `raw` 전체 보존

    `InvestorFlowSnapshot`과 동일 원칙이다. 키 이름이 자기설명적이어도 **단위·부호 규약을
    실측으로 확정하기 전엔 파싱하지 않는다**(마흐디 L16 — 필드 단위를 확인 없이 스키마부터
    정한 사고). 특히 KIS가 주는 Greeks/IV를 그대로 신뢰할지는 별도 판단이다: `strategy/
    options/surface.py`가 Black-Scholes로 직접 계산하는 편이 단위가 처음부터 이 프로젝트
    것이라 안전하다(Ver 1.3 §9). **다만 미결제약정(OI)은 계산으로 못 만든다 — API가 유일
    출처다.** `raw`에 응답 전체를 담아 두면 그때 가서 어느 쪽을 택하든 데이터가 남아 있다.
    `output3`의 KOSPI200 현물지수도 그래서 통째로 보존한다(RG 카테고리가 아직 현물지수
    소스를 못 구했는데, 이 응답이 매 폴링마다 그걸 실어 나른다)."""

    underlying: str  # 예: "KOSPI200"
    series: str  # "regular"(먼쓰리) | "weekly_mon" | "weekly_thu" — core/universe.py 어휘
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


class CircuitBreakerStatus(BusMessage):
    """거래소 서킷브레이커(CB) 추정 상태 (sys.circuit_breaker) — Command Center UI 배지용
    (2026-07-29, `risk/circuit_breaker_monitor.py` 반영). `strategy/pipeline.py`가
    `CircuitBreakerMonitor.observe()` 호출마다(이벤트 구동 경로 + 벽시계 워치독 양쪽) 발행하는
    heartbeat다 — `phase`가 그대로면 "조용히 정상"이 아니라 매번 다시 확인시켜주는 쪽을
    택했다(`Health`가 5초 주기로 heartbeat하는 것과 같은 이유, UI의 신선도 배지가 이 주기에
    기대어 STALE을 판정한다).

    `gateway_halted`는 **추정 상태가 아니라 실제 주문 게이트의 상태**다(2026-07-31 추가).
    2026-07-31엔 `phase`가 정상으로 돌아온 뒤에도 `OrderGateway`가 halted로 남아 있었는데
    (해제 경로 누락, `risk/circuit_breaker_monitor.py` 모듈 docstring 참고) 화면에는 그
    사실이 전혀 안 보였다 — 6시간 42분간 주문이 막혀 있었다는 걸 아무도 몰랐다. 추정과 실제를
    한 메시지에 나란히 실어 둘이 어긋나면 즉시 드러나게 한다."""

    symbol: str
    phase: str  # CircuitBreakerPhase.value 그대로("normal"/"warning"/"suspected"/"confirmed")
    reentry_cooldown_until: datetime | None = None
    gateway_halted: bool = False


# ---------------------------------------------------------------- L6 Learning / Self Evolution
# Ver 1.6 §9.2 Registry 상태기계, Ver 1.1 §6-4 Shadow Trading Manager, Ver 2.0 §7 Self
# Evaluation — Ver 2.0 §9 W35~36(Phase 5)에서 신설. 기존 `ExpertView.model_version`/
# `FuturesView.model_versions`(자유 문자열)는 그대로 두고, 이 섹션은 그 버전 문자열이
# 가리키는 번들의 상태·승격·일일 성적을 감사 가능하게 기록하는 계층이다.


class BundleStatus(str, Enum):
    CANDIDATE = "candidate"
    SHADOW = "shadow"
    LIVE = "live"
    RETIRED = "retired"


class RegistryStatusChanged(BusMessage):
    """모델 번들 상태 전이 감사 이력 (sys.registry) — Ver 1.6 §9.2
    candidate→shadow→live→retired. `old_status=None`은 최초 등록(candidate)."""

    bundle_id: str
    horizon: Horizon
    old_status: BundleStatus | None = None
    new_status: BundleStatus
    reason: str = ""


class ShadowFill(BusMessage):
    """Shadow 상태 번들의 가상 체결 (sys.shadow_fill) — 실주문 없이 챔피언과 동일
    Feature 흐름을 관찰해 기록하는 병행 운용(Ver 1.1 §6-4). `exit_price_ticks=None`은
    아직 보유 중인 가상 포지션."""

    bundle_id: str
    horizon: Horizon
    symbol: str
    side: Side
    qty: int
    entry_price_ticks: int
    exit_price_ticks: int | None = None
    net_return_ticks: float | None = None  # 청산 시 비용차감 후 손익(틱) — 보유 중이면 None


class PromotionProposal(BusMessage):
    """Shadow → Live 승격 제안 (sys.promotion_proposal) — Ver 1.1 §6-4 "자동 제안 + 사람
    승인"의 그 제안. 이 메시지 자체는 승격을 실행하지 않는다(Registry 상태 전이는 사람이
    `ModelRegistry.promote_to_live()`를 호출해야만 일어난다)."""

    bundle_id: str
    horizon: Horizon
    trading_days_observed: int
    champion_sharpe: float
    shadow_sharpe: float
    champion_max_drawdown: float
    shadow_max_drawdown: float
    recommended: bool
    rationale: str = ""


class SelfEvalReport(BusMessage):
    """일일 자가 평가 리포트 (sys.self_eval) — Ver 2.0 §7 "매일 장중 Shadow 병주 → 마감 후
    Self Evaluation: 승률·PF·Sharpe 집계, 슬리피지 대사".

    ## `n_trades`가 사라지고 `n_return_samples`/`n_fills`로 갈라졌다 (2026-07-31)

    예전 필드명은 `n_trades`였는데 **이름이 값과 달랐다** — 실제로는 체결 건수가 아니라
    집계에 쓰인 수익률 표본 수였고, 유일한 호출자(`scripts/run_g2_paper_trading.py`)가 하루
    1개(당일 총자산 변화율)를 표본으로 넘기므로 실무상 "누적 거래일 수"였다.

    2026-07-29에 한 번 오해했고(그땐 주석으로만 정정했다), 2026-07-31 리포트의 `n_trades=3`이
    **주문 0건·체결 0건인 날**에 다시 같은 오해를 만들었다. 주석은 리포트 JSON을 읽는 사람에게
    안 따라간다 — 이름 자체를 고친다.

    - `n_return_samples`: 승률·PF·Sharpe·MDD 계산에 실제로 들어간 수익률 표본 수.
    - `n_fills`: 진짜 체결 건수. Position Reconciler가 없어 아직 셀 수 없으므로 **`None`**이다
      — 0이 아니라 None인 것이 핵심이다(모르는 것과 없는 것을 구분, 마흐디 L18).

    ## `pnl_measurable`이 False면 손익 지표를 읽지 말 것 (2026-08-03)

    같은 실패 형태의 세 번째다. `n_trades`(07-31)와 `slippage_realized_ticks`(08-03)는 이름과
    None으로 갈랐는데, **손익 지표 전체**가 아직 남아 있었다 — 4거래일 연속 `sharpe=0.0`이
    찍혔고 그건 "수익도 손실도 없었다"는 측정 결과처럼 읽혔지만 실제로는 `live 번들 결선: []`,
    즉 모델이 하나도 안 붙은 채 파이프라인만 돈 것이었다.

    - `pnl_measurable`: 승률·PF·Sharpe·MDD를 **측정값으로 읽어도 되는가**. False면 자리표시자.
    - `wiring_stage`: 지금 막혀 있는 첫 지점(`models/wiring_completeness.py`) — 다음에 할 일.

    ## 자리표시자를 0.0이 아니라 None으로 (2026-08-05)

    `pnl_measurable`을 넣고도 같은 오독이 계속됐다: 2026-07-29~08-04 **5거래일 연속**
    `win_rate=0.0 profit_factor=0.0 sharpe=0.0 max_drawdown=0.0`이 JSON에 남았고, 그 숫자만
    보면 "성적이 0"으로 읽힌다. 실제로는 `live 번들 결선: []` — 모델이 하나도 안 붙은 채
    파이프라인만 돈 것이다.

    `n_fills`(07-31)와 `slippage_realized_ticks`(08-03)에서 이미 두 번 쓴 해법을 여기에도
    적용한다 — **모르는 것은 None으로 쓴다.** 플래그는 같이 안 읽히면 소용이 없고, None은
    포맷 문자열에서라도 걸린다. 이 프로젝트에서 같은 실패 형태의 네 번째다.
    """

    date: str  # ISO 날짜(YYYY-MM-DD) — 거래일 단위가 자연 키, 시각이 아님
    symbol: str
    n_return_samples: int
    # 손익 4지표는 `pnl_measurable=False`인 날 **None**이다 — 0.0("본전이었다")과 구분한다.
    win_rate: float | None
    profit_factor: float | None
    sharpe: float | None
    max_drawdown: float | None
    n_shadow_bundles: int
    slippage_predicted_ticks: float
    # 체결이 0건이면 **None**이다 — 0.0("슬리피지가 없었다")과 구분한다. 2026-08-03까지는
    # 주문·체결이 0건인 날에도 0.0이 찍혀 성과처럼 읽혔다(`n_fills`와 같은 실패 형태).
    slippage_realized_ticks: float | None
    n_fills: int | None = None
    # 기본값이 False인 것이 의도다 — 호출자가 결선 상태를 안 넘기면 "측정 가능"이라고
    # 주장하지 않는다(모르는 것을 좋은 쪽으로 가정하지 않는다).
    pnl_measurable: bool = False
    wiring_stage: str | None = None
    wiring_summary: str | None = None
