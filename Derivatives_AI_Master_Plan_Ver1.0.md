# Derivatives AI Master Plan

## Version 1.0

### Date

2026-07-21

---

## 1. 프로젝트 목표

최종 목표는 **파생상품 AI (Derivatives AI)** 를 구축하는 것이다.

이 AI는

- 선물(Futures)
- 옵션(Options)

을 독립적으로 분석하면서, 최종적으로 두 시장의 정보를 통합하여 **최고의 위험대비 수익률(Risk Adjusted Return)** 을 달성하는 것을 목표로 한다.

---

## 2. 최종 Vision

궁극적인 목표는

> "AI가 스스로 생각하고, 시장을 분석하고, 거래 여부를 결정하고, 리스크를 통제하며, 자기 자신을 계속 발전시키는 자율형 파생상품 AI"

이다.

단순한 자동매매 프로그램이 아니다.

최종적으로는 다음 세 가지 기능을 가진 **Autonomous Derivatives AI**를 만든다.

- Self Learning
- Self Evaluation
- Self Evolution

---

## 3. 전체 Architecture

```
Global Market Data
        │
        ▼
Market Regime AI
        │
        ▼
Futures AI  ──────  Options AI
        │
        ▼
Meta Decision Engine
        │
        ▼
Portfolio Allocation Engine
        │
        ▼
Risk Management Engine
        │
        ▼
Execution Engine
        │
        ▼
Self Evaluation
        │
        ▼
Self Evolution
```

---

## 4. 개발 Phase

### Phase 1 — Data Infrastructure

모든 데이터 수집. 예)

- 선물 체결
- 옵션 체결
- 호가
- 미결제약정
- IV
- OI
- Basis
- Spread
- Greeks
- 외국인 / 기관 / 프로그램매매
- 시장폭(Breadth)
- 경제지표
- 만기정보
- 롤오버

### Phase 2 — Feature Engineering

- 수백 개의 Feature 제작 (예상 규모 200~500개)
- 후보 Feature 생성
- 각 Horizon별 중요 Feature 선정

### Phase 3 — Specialized AI 제작

각 AI는 하나의 목적만 수행. 예)

- Regime AI
- Direction AI
- Volatility AI
- Execution AI
- Risk AI
- Option Strategy AI
- Timing AI

### Phase 4 — Meta AI

각 AI들의 결과를 종합하여 최종 의사결정을 수행.

결과: `LONG` / `SHORT` / `OPTION` / `NO TRADE`

### Phase 5 — Self Evolution

매일 AI 자신의 성과를 평가한다.

- 잘되는 모델 → 가중치 증가
- 잘 안되는 모델 → 가중치 감소
- 새로운 Feature 추가
- 불필요한 Feature 제거
- 자동 진화

---

## 5. Futures AI

### 목적

시장 방향성 예측: `LONG` / `SHORT` / `NO TRADE` 판단

### Horizon

1분 / 3분 / 5분 / 10분 / 15분 / 30분

### Horizon별 Feature

각 Horizon마다 사용하는 Feature는 다르다. 예)

**1분**
- Order Flow
- Queue
- Micro Price
- OFI
- 체결강도
- 호가 불균형

**5분**
- VWAP
- ATR
- Momentum
- Volume Delta

**30분**
- Market Regime
- Foreign Flow
- Basis
- Macro Trend
- IV

각 Horizon마다 전용 Expert Model을 만든다.

---

## 6. Options AI

선물과 동일하지 않다. 추가적으로 다음을 고려한다.

- IV
- Greeks (Theta, Gamma, Vega)
- Skew
- Volatility

### 판단 대상

- 콜매수
- 풋매수
- 콜매도
- 풋매도
- Spread
- Straddle
- Strangle
- Calendar
- Iron Condor
- 관망

---

## 7. Futures + Options Integration

선물 AI와 옵션 AI를 독립 운영한다. 이후 Meta AI가 최종 결정을 수행한다.

예)

- 선물 LONG 확률 78%
- 옵션 IV 상승 85%
- → 콜매수 또는 선물 Long 등을 선택

---

## 8. Portfolio AI

자금을 선물 / 옵션 / 현금으로 자동 배분한다.

예) 선물 60% / 옵션 30% / 현금 10%

시장상황에 따라 자동 변경.

---

## 9. Risk AI

거래 전 반드시 Risk를 계산한다. 예)

- Expected Return
- Expected Loss
- Maximum Drawdown
- VaR
- Position Size
- Risk Reward

---

## 10. Execution AI

최적 주문 방식을 결정한다.

- 시장가
- 지정가
- 분할진입
- 분할청산
- 추격
- 관망

---

## 11. 만기 관리

반드시 포함되어야 하는 항목.

**선물**
- 근월물 / 차월물
- Roll Over
- Basis
- Spread
- 만기까지 남은 일수

**옵션**
- Weekly / Monthly Expiration
- Theta Decay
- IV Crush
- Gamma Explosion

---

## 12. Self Evaluation

매일 AI가 자신을 평가한다. 예)

- 최근 200회 LONG 성공률 / SHORT 성공률
- Regime Accuracy
- Execution Accuracy
- Sharpe Ratio
- Profit Factor
- Win Rate

---

## 13. Ultimate Goal

최종적으로는 하나의 AI가 아니라 **AI들의 집합체(System of AI)**를 만든다.

각 AI는 전문화되며, 최종 Meta AI가 모든 결과를 종합하여 거래 여부를 결정한다.

### 최종 목표

**Autonomous Derivatives AI**

Think → Analyze → Predict → Decide → Execute → Evaluate → Evolve

스스로 진화하는 세계 최고 수준의 파생상품 AI 구축.

---

## 앞으로의 계획

이 문서는 Ver 1.0으로 두고, 앞으로 다음 내용을 추가해 나가면 하나의 설계서가 아니라 100~200페이지 규모의 파생상품 AI 설계 문서로 발전시킬 예정이다.

| 버전 | 내용 |
|---|---|
| Ver 1.1 | 전체 시스템 아키텍처 |
| Ver 1.2 | Futures AI 상세 설계 |
| Ver 1.3 | Options AI 상세 설계 |
| Ver 1.4 | Feature Dictionary (300~500개) |
| Ver 1.5 | Horizon별 Feature Mapping |
| Ver 1.6 | Machine Learning 모델 설계 |
| Ver 2.0 | Derivatives AI Full Architecture |

이 프로젝트는 충분히 기관 수준을 넘어서는 독자적인 파생상품 AI 프레임워크로 발전시킬 수 있다.
