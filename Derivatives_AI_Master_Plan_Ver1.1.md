# Derivatives AI Master Plan — Ver 1.1

## 전체 시스템 아키텍처 (Full System Architecture)

### Date: 2026-07-21

### 기반 문서: Ver 1.0 (마스터 플랜), Ver 1.0.1 (검토·보완)

---

# 0. 문서 목적과 범위

이 문서는 Derivatives AI 전체 시스템을 **"어떤 부품(컴포넌트)들이, 어떤 대화(메시지)를 주고받으며, 어떤 순서(흐름)로 움직이는가"** 수준까지 정의한다.

- 포함: 레이어 구조, 컴포넌트별 책임·입출력·장애 시 동작, 메시지 버스 설계, 데이터 저장 설계, 지연 예산, 프로세스 배치
- 제외(후속 버전): Futures AI 내부 로직(Ver 1.2), Options AI 내부 로직(Ver 1.3), 개별 Feature 정의(Ver 1.4~1.5), 모델 알고리즘(Ver 1.6)

> 비유: Ver 1.0이 "도시 계획도"였다면, Ver 1.1은 "상수도·전기·도로망 설계도"다.
> 각 건물(AI)의 내부 인테리어는 Ver 1.2 이후에 그린다.

---

# 1. 아키텍처 개요 — 6 Layer 구조

Ver 1.0의 일렬 파이프라인을, 실전 운용이 가능한 **6개 레이어 + 관제탑(Observability)** 구조로 확장한다.

```
════════════════════════════════════════════════════════════════════
 L1. DATA LAYER          시세·수급·이벤트 수집 → 정규화 → 배포/저장
════════════════════════════════════════════════════════════════════
 L2. FEATURE LAYER       Feature Store 정의 기반 실시간/배치 Feature 계산
════════════════════════════════════════════════════════════════════
 L3. INTELLIGENCE LAYER  Regime AI → Futures AI · Options AI → Meta Decision
════════════════════════════════════════════════════════════════════
 L4. CAPITAL LAYER       Cost Model → Risk Engine → Position Sizing → 배분
                         (Kill Switch 상주)
════════════════════════════════════════════════════════════════════
 L5. EXECUTION LAYER     Execution Engine → Broker Adapter ↔ 거래소
                         (Digital Twin Simulator = 동일 인터페이스)
════════════════════════════════════════════════════════════════════
 L6. LEARNING LAYER      Trainer → Validator → Model Registry
                         → Shadow Trading → 승격/강등 (Self Evolution)
════════════════════════════════════════════════════════════════════
 OBS. OBSERVABILITY      구조화 로깅 · 메트릭 · 알림 · UI Command Center
════════════════════════════════════════════════════════════════════
```

설계 대원칙 4가지:

1. **레이어는 아래로만 의존한다** — L3는 L2의 Feature를 소비하지만, L2는 L3를 모른다.
2. **모든 레이어 간 통신은 Message Bus를 경유한다** — 직접 함수 호출 금지. 프로세스·PC 분리가 자유로워진다.
3. **L5는 교체 가능하다** — 실거래소 어댑터와 Digital Twin이 같은 인터페이스를 구현. 상위 레이어는 모의/실전을 구분하지 못한다.
4. **L4는 거부권을 가진다** — L3가 아무리 확신해도, L4(Risk)가 거부하면 주문은 나가지 않는다. 수익은 L3가 만들고 생존은 L4가 만든다.

---

# 2. 컴포넌트 상세 정의

각 컴포넌트는 다음 형식으로 정의한다: **책임 / 입력 / 출력 / 실패 시 동작(Fail-Safe)**

## L1. Data Layer

### 1-1. Market Data Collector

- 책임: 선물·옵션 체결/호가, 수급(외국인·기관·프로그램), 지수·환율 등 원시 데이터 수신
- 입력: 브로커/거래소 API 스트림
- 출력: `raw.*` 토픽으로 원시 틱 발행 + 원본 그대로 아카이브(Event Sourcing의 원천)
- 실패 시: 재접속 백오프(1s→2s→4s…), 30초 단절 시 `sys.health` CRITICAL 발행 → L4가 신규 주문 차단

### 1-2. Normalizer

- 책임: 심볼 표준화, UTC+거래소 타임스탬프 병기, 틱 정수화(Decimal), 중복·역순 틱 제거
- 입력: `raw.*` / 출력: `md.tick.*`, `md.book.*`, `md.flow.*`
- 실패 시: 파싱 불가 틱은 격리 큐(quarantine)에 저장하고 스킵 — 절대 추측으로 보정하지 않는다

### 1-3. Event Calendar Service

- 책임: 경제지표 발표, FOMC, 선물·옵션 만기일, 휴장일 관리
- 출력: `evt.calendar` (D-day, 이벤트 등급) — L3 Regime AI와 L4가 구독

### 1-4. Archiver

- 책임: 모든 정규화 데이터를 Parquet으로 영구 저장 (Digital Twin의 재생 원본)
- 파티셔닝: `data/{종목}/{yyyy}/{mm}/{dd}/{데이터종류}.parquet`
- 실패 시: 디스크 임계(85%) 도달 시 WARN, 쓰기 실패 시 로컬 버퍼 후 재시도

## L2. Feature Layer

### 2-1. Feature Store (정의 저장소)

- 책임: Feature의 **정의·계산 코드·버전·의존성**을 단일 관리. "학습 때의 계산식 = 실시간 계산식" 보장
- 구조: Feature 1개 = 클래스 1개 = 파일 1개, `id / version / horizon / 의존 데이터 / 계산식 / 테스트`
- Ver 1.4에서 전체 사전(300~500개)을 작성한다

### 2-2. Realtime Feature Engine

- 책임: 틱 스트림을 구독해 Horizon별 Feature 벡터를 증분 계산(incremental update)
- 입력: `md.*` / 출력: `feat.{horizon}` (예: `feat.1m`, `feat.5m`)
- 성능 규칙: 틱마다 전체 재계산 금지 — 롤링 윈도우 증분 갱신만 허용
- **발행 규칙(완성봉 규율)**: 계산은 틱 증분으로 하되, `feat.{h}` 발행은 해당 Horizon의 **완성봉 확정 시점에만** 한다. 미완성 봉 값은 발행하지 않는다 (상세: Ver 1.2 §2.2)
- 실패 시: 특정 Feature 계산 오류 → 해당 Feature만 NaN 마킹 후 발행, NaN 비율 20% 초과 시 해당 Horizon 신호 정지

### 2-3. Batch Feature Builder

- 책임: 학습용 Feature를 야간 배치로 재계산, 실시간 계산치와 **일치 검증(parity check)**
- 불일치 발견 시: 해당 Feature 자동 격리 + 리포트 — 이것이 데이터 누수·버그의 최전선 방어다

## L3. Intelligence Layer

### 3-1. Market Regime AI

- 책임: 현재 국면 분류 (추세상승/추세하락/횡보/고변동성/이벤트) + 국면 전환 확률
- 입력: `feat.30m`, `evt.calendar` / 출력: `intel.regime` (국면, 확신도, 지속시간)
- 실패 시: 판단 불가 → `UNKNOWN` 발행, 하위 AI들은 보수 모드(임계값 상향)로 전환

### 3-2. Futures AI (Horizon Expert Ensemble)

- 책임: Horizon별(1/3/5/10/15/30분) 전문가 모델의 방향 확률 + 불확실성 출력
- 입력: `feat.{h}`, `intel.regime` / 출력: `intel.futures` (Horizon별 {방향, 확률, 신뢰구간})
- 내부 구조는 Ver 1.2에서 상세 설계

### 3-3. Options AI

- 책임: IV·Greeks·Skew 기반 전략 후보 산출 (전략, 행사가, 만기, 기대손익 분포)
- 입력: `feat.*`, `md.book.options`, `intel.regime` / 출력: `intel.options`
- 내부 구조는 Ver 1.3에서 상세 설계

### 3-4. Meta Decision Engine

- 책임: 세 AI의 출력을 종합해 **의도(Intent)** 를 생성: `LONG / SHORT / OPTION(전략명) / NO TRADE`
- 입력: `intel.*` / 출력: `decision.intent` (의도, 확신도, 불확실성, **근거 Feature 상위 5개**)
- 규칙: 전문가 간 의견 분산이 임계 초과 → 강제 `NO TRADE` (의견이 갈리면 배팅하지 않는다)
- 근거(XAI)를 반드시 포함해 발행한다 — UI와 사후 분석의 원천

## L4. Capital Layer

### 4-1. Cost Model

- 책임: 의도별 예상 비용 산출 = 수수료 + 세금 + 예상 슬리피지(스프레드·호가잔량 기반) + 시장충격
- 출력: `capital.cost` — Meta 의도의 기대수익에서 차감하여 **Net Expected Return** 계산

### 4-2. Risk Engine

- 책임: 사전 리스크 검사 (VaR, 최대낙폭 진행률, 포트폴리오 Greeks 한도, 집중도, 이벤트 근접)
- 입력: `decision.intent`, `capital.cost`, 현재 포지션 / 출력: 승인·수정·거부
- **거부권 보유**: Net Expected Return ≤ 0 또는 한도 위반 → 무조건 거부

### 4-3. Position Sizer

- 책임: 승인된 의도의 크기 결정 — Volatility Targeting × Fractional Kelly(1/4~1/2) × 불확실성 페널티
- 출력: `capital.order_request` (종목, 방향, 수량, 유효시간)

### 4-4. Kill Switch (상주 감시자)

- 책임: 독립 프로세스로 상주. 트리거 — 일일 손실 한도, 주문 오류율, 데이터 30초 단절, 모델 출력 이상, 수동 버튼
- 발동 시: `sys.kill` 발행 → 전 컴포넌트 신규 주문 차단 → 전 포지션 청산 절차 → 사람 확인 전까지 재가동 금지
- **어떤 컴포넌트보다 단순하게 유지한다** (의존성 최소 — 소화기는 복잡하면 안 된다)

## L5. Execution Layer

### 5-1. Execution Engine

- 책임: 주문 요청을 실행 전술로 변환 (시장가/지정가/분할/추격/취소), 체결 추적, 부분체결 관리
- 입력: `capital.order_request` / 출력: `exec.order`, `exec.fill`, `exec.report`
- 실패 시: 주문 상태 불명 → 즉시 브로커 API 재조회, 3회 실패 시 해당 종목 거래 정지 + CRITICAL

### 5-2. Broker Adapter (실전) / Digital Twin Simulator (모의)

- 동일 추상 인터페이스: `submit() / cancel() / positions() / account()`
- Simulator: Archiver의 Parquet을 재생하며 호가창 기반 체결 모사 + 자기 주문의 시장충격 반영
- 설정 파일의 `mode: live | paper | replay` 한 줄로 전환 — 상위 레이어 코드는 불변

### 5-3. Position Reconciler

- 책임: 주기적(예: 10초)으로 내부 장부와 브로커 계좌를 대사(對査). 불일치 → 브로커를 정답으로 채택 + WARN
- 재시작 복구의 핵심: 로컬 기억을 신뢰하지 않는다

## L6. Learning Layer (야간/주말 가동)

### 6-1. Trainer

- 책임: Batch Feature + Triple Barrier 레이블로 후보 모델 학습 (LightGBM 계열 중심)

### 6-2. Validator

- 책임: Walk-Forward + Purged K-Fold 검증, Deflated Sharpe 산출, 비용 차감 후 성과 리포트
- 통과 기준 미달 모델은 Registry 등록 자체를 거부

### 6-3. Model Registry

- 책임: 모델 버전, 학습 구간, 검증 성적, 배포 상태(`candidate / shadow / live / retired`) 관리
- 모든 실시간 추론 로그에 모델 버전이 찍힌다 → 언제든 롤백 가능

### 6-4. Shadow Trading Manager (Self Evolution의 실체)

- 책임: `shadow` 상태 모델들에 실시간 Feature를 공급, 가상 주문 성적 기록
- 승격 규칙(예시): 20거래일 이상 & 현역 대비 Net Sharpe 우위 & 최대낙폭 한도 내 → 승격 심사 발행
- 승격은 **자동 제안 + 사람 승인**으로 시작하고, 신뢰가 쌓이면 자동화 범위를 넓힌다

### OBS. Observability

- 구조화 로깅(JSON): 모든 `decision.intent`에 Feature 스냅샷 + 모델 버전 + 결과를 결합 저장
- 메트릭: 지연시간(구간별), 처리량, NaN 비율, 모델 성적 — Prometheus 형식 노출
- UI Command Center(Ver 1.0.1 §3): `ui.*` 토픽 구독 + WebSocket 중계
- 알림: INFO(토스트) / WARN(사운드) / CRITICAL(텔레그램 푸시)

---

# 3. 핵심 데이터 흐름 3가지

## 3.1 실시간 거래 경로 (Hot Path) — 목표 총 지연 50ms 이내

```
거래소 틱
 → [Collector]   raw.tick          ( 5ms)
 → [Normalizer]  md.tick           ( 5ms)
 → [Feature Eng] feat.1m…30m       (15ms)
 → [Futures/Options/Regime AI]     (10ms)
 → [Meta]        decision.intent   ( 5ms)
 → [Cost→Risk→Sizer] order_request ( 5ms)
 → [Execution]   exec.order        ( 5ms)
 → 브로커
```

- 각 구간은 타임스탬프를 메시지에 누적 기록 → 구간별 지연을 UI에서 상시 감시
- 예산 초과가 상습화된 구간은 최적화 대상 1순위

## 3.2 학습 경로 (Cold Path, 야간)

```
Parquet 아카이브
 → [Batch Feature Builder] → parity check (실시간 계산과 대조)
 → [Trainer] Triple Barrier 레이블 → 후보 모델
 → [Validator] Walk-Forward + 비용 차감 성과
 → 통과 → [Model Registry] status=candidate
 → [Shadow Manager] status=shadow, 실시간 가상 운용 개시
```

## 3.3 진화 경로 (Evolution Loop, 매일 장 마감 후)

```
[Self Evaluation] 현역·섀도 모델 전 성적 집계
 → 승격 후보 발견 → 승격 제안 리포트 (사람 승인)
 → 성적 미달 현역 → 가중치 감소 or 강등(retired)
 → Feature 중요도 붕괴 감지 → Feature 격리 목록 갱신
 → 결과를 UI Self-Eval 보드와 일일 리포트로 발행
```

---

# 4. Message Bus 설계

## 4.1 기술 선택

- 1단계(현 PC 1대): **Redis pub/sub + Redis Streams** — 설치 간단, Windows는 WSL2/Docker로 구동
- 확장 시(멀티 PC): 동일 Redis를 중앙 배치하거나, 초저지연 구간만 ZeroMQ 직결로 승격
- 이력이 필요한 토픽(`decision.*`, `exec.*`)은 pub/sub이 아닌 **Streams**(재생 가능)를 사용

## 4.2 토픽 명명 규칙과 목록

`{레이어}.{종류}.{종목/Horizon}` 소문자 점 표기.

| 토픽 | 발행자 | 주요 구독자 | 성격 |
|---|---|---|---|
| `raw.*` | Collector | Normalizer, Archiver | 스트림 |
| `md.tick.*` `md.book.*` `md.flow.*` | Normalizer | Feature Engine, UI | 스트림 |
| `evt.calendar` | Calendar Svc | Regime AI, Risk, UI | 상태 |
| `feat.{1m,3m,5m,10m,15m,30m}` | Feature Engine | 각 AI, Shadow Mgr | 스트림 |
| `intel.regime` / `intel.futures` / `intel.options` | 각 AI | Meta, UI | 스트림 |
| `decision.intent` | Meta | Capital Layer, UI, 로그 | **Streams(이력)** |
| `capital.order_request` | Sizer | Execution | **Streams(이력)** |
| `exec.order` / `exec.fill` / `exec.report` | Execution | Reconciler, UI, 로그 | **Streams(이력)** |
| `sys.health` | 전 컴포넌트 | Kill Switch, UI | 상태(주기 발행) |
| `sys.kill` | Kill Switch | **전 컴포넌트** | 최우선 |

## 4.3 메시지 스키마 (Pydantic 단일 정의 — `core/messages.py`)

```python
from decimal import Decimal
from datetime import datetime
from pydantic import BaseModel
from enum import Enum

class Side(str, Enum):
    LONG = "LONG"; SHORT = "SHORT"; OPTION = "OPTION"; NO_TRADE = "NO_TRADE"

class DecisionIntent(BaseModel):
    ts_utc: datetime            # 발행 시각(UTC)
    ts_exchange: datetime       # 근거 데이터의 거래소 시각
    symbol: str
    side: Side
    confidence: float           # 0.0 ~ 1.0
    uncertainty: float          # 신뢰구간 폭 (클수록 불확실)
    horizon: str                # "5m" 등
    option_strategy: str | None # OPTION일 때만
    top_features: list[tuple[str, float]]  # XAI: (feature_id, 기여도) 상위 5
    model_version: str          # Registry 버전 — 롤백·재현의 열쇠
    latency_trace: dict[str, float]        # 구간별 누적 지연(ms)

class OrderRequest(BaseModel):
    ts_utc: datetime
    intent_id: str              # 원 의도 추적용
    symbol: str
    side: Side
    qty: int
    limit_price_ticks: int | None   # 가격은 정수 틱 단위
    ttl_ms: int                 # 유효시간 초과 시 자동 폐기
    net_expected_return: Decimal
    risk_approved_by: str       # Risk Engine 버전
```

- 모든 메시지는 이 패키지에서만 정의하고 전 프로세스가 동일 버전을 import한다
- 스키마 변경은 버전 필드를 올리고 하위 호환 2버전 유지

---

# 5. 저장소 설계

| 데이터 | 저장소 | 이유 |
|---|---|---|
| 원시/정규화 틱 | Parquet (일 단위 파티션) | 압축 효율, Polars 직독, Digital Twin 재생 원본 |
| Feature (배치) | Parquet | 학습 재현성 |
| 의사결정·주문·체결 이력 | Redis Streams → 일일 Parquet 아카이브 | 실시간 + 영구 보존 |
| 모델 아티팩트 | 파일 + Registry(SQLite→PostgreSQL) | 버전·메타데이터 관리 |
| 설정 | YAML(git) + .env(시크릿, git 제외) | 감사 가능 + 보안 |
| 일일 성과·평가 | SQLite → PostgreSQL | UI 조회, 리포트 |

- 규모 전환 기준: 조회가 느려지기 시작하면(수억 행) ClickHouse/TimescaleDB 도입 검토 — 그전에는 단순 유지

---

# 6. 상태 관리와 장애 복구

1. **모든 컴포넌트는 무상태(stateless)를 지향** — 상태는 Redis/브로커/Parquet에 있고, 프로세스는 언제든 죽고 재시작 가능
2. 재시작 절차: 기동 → 브로커 API에서 포지션·미체결 재조회 → Reconciler 대사 통과 → `sys.health` OK 발행 → 그 후에만 신호 소비 시작
3. Heartbeat: 전 컴포넌트 5초 주기 `sys.health` 발행, 15초 미수신 = 사망 판정 → Kill Switch 시나리오 평가
4. 장중 배포 금지 — 배포는 장 마감 후, 배포 직후 replay 모드 스모크 테스트 통과 필수

---

# 7. 배포 모델 — 1 PC 개발 → N PC 독립 복제 배포

핵심 개념: **배포 단위 = 시스템 전체 1세트(Full Stack Instance)**.
완성된 버전을 여러 PC에 복제 설치하면, **각 PC가 자기 계좌·자기 자본으로 독자적으로 거래**한다.
PC 간 실시간 의존은 없다 — 한 대가 죽어도 나머지는 계속 거래한다.

> 비유: 프랜차이즈 지점이다. 본사(개발 PC)가 레시피(코드+모델)를 확정해 배포하면,
> 각 지점(PC)은 자기 금고(계좌)로 독립 영업한다. 지점끼리는 서로 몰라도 된다.

## 7.1 인스턴스 내부 구성 (모든 PC 동일)

```
PC 1대 = Full Stack Instance
├─ WSL2/Docker: Redis (로컬 전용)
├─ proc-1: Collector + Normalizer + Archiver
├─ proc-2: Feature Engine
├─ proc-3: Intelligence (Regime+Futures+Options+Meta)
├─ proc-4: Capital + Execution + Reconciler
├─ proc-5: Kill Switch                (독립·최우선)
├─ proc-6: UI Backend (FastAPI)
└─ 야간(선택): Trainer + Validator    # 학습은 대표 1대만 수행해도 됨
```

## 7.2 인스턴스별 차이는 오직 설정 파일 하나

코드는 전 PC 동일 바이너리(이미지)이고, 차이는 `configs/instance.yaml` 하나뿐이다.

```yaml
instance_id: "pc-home-01"        # 로그·리포트 식별자
mode: live                        # live | paper | replay
broker:
  account_ref: env:ACCOUNT_01     # 계좌는 .env 참조 (PC마다 다름)
capital:
  total: 50_000_000               # 이 PC가 굴리는 자본
  daily_loss_limit_pct: 2.0       # PC별 독립 한도
universe: ["K200_MINI_FUT", "K200_OPT"]   # 선물은 미니선물 표준 (Holding Policy §1.1)
model_bundle: "release-2026.07.21"  # 고정된 모델 버전
```

## 7.3 배포를 위한 코딩 요구사항 (개발 단계부터 강제)

1. **절대경로 금지** — 모든 경로는 설정/환경변수 기반, 프로젝트 루트 상대경로
2. **포트·주소 하드코딩 금지** — Redis 주소, UI 포트 전부 설정화
3. **인스턴스 ID를 모든 로그·메시지에 포함** — 여러 PC의 리포트를 나중에 합쳐도 구분 가능
4. **설치는 명령 한 번**: `docker compose up -d` 또는 `install.ps1` 스크립트 하나로 Redis 기동 → 의존성 설치 → 설정 검증 → replay 스모크 테스트 → 기동까지 완료
5. **릴리스 = git tag + 모델 번들**: 코드와 모델 아티팩트를 묶어 버전 하나로 배포 (`release-YYYY.MM.DD`). 각 PC는 번들 교체 + 재시작만으로 업그레이드
6. 기동 시 **자가 점검(self-check)**: 설정 유효성, 브로커 접속, 시간 동기화, 모델 로드 검증을 통과해야만 거래 개시

## 7.4 중앙 모니터링 (선택, 읽기 전용)

- 각 인스턴스가 일일 성과·상태 리포트를 중앙 대시보드(또는 텔레그램)로 **푸시**한다
- 중앙 → 인스턴스 방향의 명령 채널은 두지 않는다 (독립성 보장, 해킹 표면 최소화)
- 학습(L6)은 대표 PC 1대(또는 연구용 PC)에서 수행하고, 산출된 모델 번들을 릴리스로 각 PC에 배포한다

---

# 8. 보안 원칙

- API 키·계좌: `.env` + OS 자격증명 저장소, git 커밋 금지 (pre-commit 훅으로 차단)
- 대시보드 외부 노출 금지 — 원격은 Tailscale 등 VPN 경유만
- 실행 PC: 불필요 소프트웨어 설치 금지, 자동 업데이트는 장외 시간으로 통제
- 주문 가능 IP를 브로커 설정에서 제한 (지원 시)

---

# 9. Ver 1.0 대비 아키텍처 변경 요약

| 항목 | Ver 1.0 | Ver 1.1 |
|---|---|---|
| 구조 | 일렬 파이프라인 | 6 Layer + Observability, 아래 방향 의존만 허용 |
| 통신 | 미정의 | Message Bus(Redis) + Pydantic 스키마 단일 정의 |
| 리스크 | 항목 나열 | L4 거부권 + 독립 Kill Switch 프로세스 |
| 모의/실전 | 미정의 | 동일 인터페이스, 설정 한 줄 전환 (Digital Twin) |
| 진화 | 개념 | Registry 상태기계(candidate→shadow→live→retired) |
| 재현성 | 미정의 | 모든 의도에 Feature 스냅샷 + 모델 버전 기록 |

---

# 10. 다음 단계

**Ver 1.2 — Futures AI 상세 설계**: Horizon별 Expert 모델 구조, Triple Barrier 파라미터, Meta-Labeling 2단 구조, Regime 연동 가중치, 학습 데이터 구성과 재학습 주기.
