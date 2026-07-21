# Derivatives AI Master Plan — Ver 1.3

## Options AI 상세 설계 (Detailed Design)

### Date: 2026-07-21

### 기반 문서: Ver 1.0, Ver 1.0.1, Ver 1.1, Ver 1.2

---

# 0. 목적과 범위

Options AI는 Ver 1.1의 L3 컴포넌트 3-3이다.
책임: **변동성 관점의 분석과 옵션 전략 후보 산출** — "지금 어떤 옵션 구조가, 어떤 기대손익 분포를 갖는가"를 출력한다.

- 출력만 한다: `intel.options` (전략 후보 + 기대손익 분포 + 근거). 크기·승인·주문은 L4/L5의 몫
- 방향 예측을 **다시 하지 않는다**: 방향은 Futures AI(`intel.futures`)를 구독해 재사용한다
- 포함: Vol Engine, 전략 매트릭스, 후보 평가, 매도 안전규칙, Delta Hedging 정책, 만기 수명주기, 학습 요소, 모듈 구조
- 제외: 개별 Feature 정의(Ver 1.4~1.5), 포트폴리오 한도 수치(L4 소관, Ver 2.0)

> 비유: 선물이 "오를까 내릴까"의 1차원 게임이라면,
> 옵션은 **방향 × 변동성 × 시간**의 3차원 체스다.
> 방향이 맞아도 IV가 꺼지면 지고(IV Crush), 방향이 틀려도 시간가치로 이길 수 있다(Theta).
> 그래서 Options AI의 중심축은 방향이 아니라 **변동성**이다.

---

# 1. 내부 파이프라인 전체 구조

```
md.book.options   feat.*   intel.regime   intel.futures (방향은 여기서만)
      │             │           │              │
      ▼             ▼           ▼              ▼
┌──────────────────────────────────────────────────────┐
│ ① Vol Engine        IV Surface·IV Rank·Skew·기간구조   │
├──────────────────────────────────────────────────────┤
│ ② Vol Forecaster    실현변동성 예측 vs IV → 변동성 프리미엄 │
├──────────────────────────────────────────────────────┤
│ ③ Strategy Candidate Generator                        │
│    방향(Futures AI) × 변동성 상태 매트릭스 → 후보 구조    │
├──────────────────────────────────────────────────────┤
│ ④ Strategy Evaluator                                  │
│    후보별 기대손익 분포·승률·최대손실·비용 → 순위화        │
├──────────────────────────────────────────────────────┤
│ ⑤ Position Lifecycle Manager (보유 중 상시)            │
│    조정·청산·롤·헤지 신호 생성                           │
└──────────────────────────────────────────────────────┘
      │
      ▼
 intel.options  (신규 후보 + 보유 포지션 관리 신호, XAI 근거 포함)
```

원칙 2가지:

1. **방향의 단일 출처(Single Source of Direction)**: 방향 뷰는 Futures AI만 낸다. Options AI가 방향을 따로 예측하면 Meta Decision에서 "같은 데이터, 다른 방향"의 모순이 생긴다
2. **진입보다 관리**: ⑤는 신규 진입이 없어도 보유 포지션이 있는 한 상시 가동한다. 옵션 수익의 절반은 보유 중 관리에서 나온다 (Ver 1.0.1 §1.6)

---

# 2. 완성봉 규율 적용 (Ver 1.2 §2.2 계승)

옵션에도 동일한 규율을 적용하되, 주기를 조정한다.

| 컴포넌트 | 갱신 주기 | 이유 |
|---|---|---|
| Vol Engine (IV Surface) | **5분봉 완성 시** | 옵션 호가가 얇아 1분 IV는 노이즈 — 5분이 신뢰 하한 |
| Vol Forecaster | 30분봉 완성 시 | 변동성 예측은 느린 변수 |
| Strategy Generator/Evaluator | 5분봉 완성 시 + Futures AI 갱신 시 | |
| Lifecycle Manager 중 **위험 감시** | 틱 단위 상시 | 손절·Greeks 이탈·급변 감지는 봉을 기다리지 않는다 (안전장치 예외) |

---

# 3. Vol Engine 상세

## 3.1 산출물

| 지표 | 정의 | 용도 |
|---|---|---|
| IV Surface | 행사가(머니니스) × 만기별 IV 곡면. SVI 파라미터화 또는 스플라인 피팅, 차익거래 무결성(버터플라이·캘린더) 검사 | 모든 옵션 평가의 기준 |
| ATM IV | 만기별 등가격 IV | 대표 변동성 |
| **IV Rank / Percentile** | 현재 ATM IV의 최근 252일 내 위치 (0~100) | 매수/매도 전략 필터의 핵심 |
| Skew | 25Δ 풋 IV − 25Δ 콜 IV (리스크 리버설) | 하방 공포 측정, 급락 경계 |
| Term Structure | 근월 IV − 차월 IV 기울기 | Calendar 전략 판단, 이벤트 프리미엄 감지 |
| IV−RV Spread | IV − 실현변동성(예측) | 변동성 프리미엄 = 매도 전략의 원천 수익 |

## 3.2 품질 규칙

- 호가 스프레드가 임계 초과(유동성 없음)인 행사가는 Surface 피팅에서 제외
- 피팅 잔차 급증 → Surface 신뢰불가 플래그 → 신규 옵션 진입 전면 보류 (WARN 발행)
- 만기 임박(DTE≤1) 옵션의 IV는 별도 취급 (수치 불안정)

---

# 4. 전략 후보 생성 — 방향 × 변동성 매트릭스 (핵심)

Futures AI의 방향 뷰(통합 점수 S)와 Vol Engine의 IV 상태를 교차해 후보를 뽑는다.

## 4.1 매트릭스

| 방향 \ IV 상태 | **IV 낮음** (Rank < 30) | **IV 중립** (30~70) | **IV 높음** (Rank > 70) |
|---|---|---|---|
| **상승** (S > +임계) | 콜매수, Bull Call Spread(debit) | Bull Call Spread | **풋매도, Bull Put Spread(credit)** |
| **중립** (\|S\| ≤ 임계) | Calendar, 관망 | 관망 (우위 없음) | **Iron Condor, Strangle 매도** |
| **하락** (S < −임계) | 풋매수, Bear Put Spread(debit) | Bear Put Spread | **콜매도, Bear Call Spread(credit)** |

논리 (이 표의 존재 이유):

- **IV가 낮을 때는 옵션이 싸다 → 사는 전략** (프리미엄 지불이 아깝지 않음)
- **IV가 높을 때는 옵션이 비싸다 → 파는 전략** (비싼 보험을 파는 쪽에 선다)
- 방향 확신이 없고 IV도 중립이면 **관망이 정답** — 옵션은 우위가 없으면 비용(스프레드+Theta)만 낸다

## 4.2 생성 규칙

- 매트릭스 셀당 후보 2~3개 구조 × 행사가/만기 변형 = 진입 후보 최대 10개 내외로 제한
- 행사가 선택: 델타 기준 (예: 매도 다리는 15~30Δ, 매수 다리는 30~50Δ) — 가격이 아니라 델타로 표준화
- 만기 선택: 매도 전략은 DTE 15~45 (Theta 수확 최적 구간), 매수 전략은 DTE 20 이상 (Gamma 폭발 구간 회피), Weekly는 이벤트 플레이 전용
- Skew 보정: Skew 극단(급락 공포) 시 풋매도 후보 자동 제외

---

# 5. Strategy Evaluator — 후보 평가

각 후보를 동일한 잣대로 평가해 순위화한다.

## 5.1 평가 방법

- 기초자산 시나리오 그리드: 가격 변화(±3σ, 21포인트) × IV 변화(−30%~+30%, 7포인트) × 시간 경과 → 손익 곡면 계산
- 시나리오 확률 가중: Futures AI 방향 분포 + Vol Forecaster IV 분포로 가중 → **기대손익 분포**
- 산출 지표:

| 지표 | 정의 |
|---|---|
| Net Expected Return | 확률가중 기대손익 − 총비용 (옵션 스프레드 비용 필수 반영 — 옵션 호가 스프레드는 선물보다 훨씬 넓다) |
| POP (Probability of Profit) | 만기/청산 시점 이익 확률 |
| Max Loss | 구조상 최대손실 (무한이면 별도 플래그) |
| Reward/Risk | 기대이익 / 최대손실 |
| Vega·Theta 노출 | 진입 시 Greeks 프로파일 |

## 5.2 출력 (`intel.options`)

- 상위 후보 3개 + 각 후보의 위 지표 전부 + 근거(IV Rank, Skew, 방향 점수, 결정 매트릭스 셀)
- `NO_OPTION` (우위 없음)도 명시적 출력이다 — 침묵과 관망을 구분한다

---

# 6. 매도 전략 안전규칙 (Hard Rules — 학습 대상 아님)

옵션 매도는 "동전 줍기 앞의 증기롤러"가 될 수 있다. 다음은 ML이 아니라 **고정 규칙**이다.

1. **네이키드 매도 금지** — 모든 매도는 스프레드(정의된 최대손실) 구조로만. 예외 없음
2. IV Rank < 50에서 credit 전략 금지 (싼 보험을 파는 것은 우위가 아니다)
3. 이벤트 캘린더 D-1~D0 (FOMC·주요 지표): 신규 매도 진입 금지
4. 만기일(DTE=0) 신규 진입 금지, DTE≤2 매도 포지션은 이익/손실 무관 청산 (Gamma 폭발 구간)
5. 매도 포지션 손실이 수취 프리미엄의 2배 도달 → 무조건 청산 (물타기·롤 금지, Lifecycle Manager가 강제)
6. 포트폴리오 순 Vega·순 감마 한도는 L4 Risk Engine이 보유 — Options AI는 후보에 Greeks를 첨부할 의무만 갖는다

---

# 7. Delta Hedging 정책

목적: 옵션 포지션의 **방향 노출을 의도한 범위로 유지** (변동성 베팅이 방향 베팅으로 변질되는 것 방지).

- 대상: 방향 중립 의도의 포지션 (Iron Condor, Straddle/Strangle, Calendar)
  - 방향 의도 포지션(콜매수 등)은 헤지하지 않는다 — 방향이 논지다
- 방식: **밴드 헤징** — 포지션 순델타가 ±Δ_band를 벗어나면 **미니선물** 단위로 중화, 밴드 안이면 방치
  (미니선물 표준 채택으로 일반선물 대비 5배 정밀한 중화 가능 — Holding Policy §1.1)
  - 틱마다 헤지 금지 (수수료로 죽는다). 밴드 폭은 감마·거래비용의 함수로 설정, 초기값은 백테스트로
- 헤지 주문도 L4→L5 정상 경로를 탄다 (Options AI가 직접 주문하지 않는다 — 아키텍처 원칙)
- 만기 임박 고감마 구간에서는 밴드 자동 축소 또는 포지션 청산 우선

---

# 8. 만기 수명주기 관리 (Position Lifecycle)

모든 옵션 포지션은 진입 순간부터 **수명주기 상태기계**를 갖는다.

```
[진입] → [정상 보유] → [이익실현 조건] → 청산
            │
            ├─ [조정 조건] → 다리 롤/부분 청산 → 정상 보유로 복귀
            ├─ [손절 조건] → 강제 청산 (§6-5)
            └─ [만기 접근 DTE≤2] → 강제 청산/롤 (매도) · 청산/행사 판단 (매수)
```

| 규칙 | 초기값 (백테스트로 재조정) |
|---|---|
| 이익 실현 (credit 전략) | 수취 프리미엄의 50% 도달 시 청산 (끝까지 쥐지 않는다 — 마지막 20%가 가장 위험) |
| 조정 (Iron Condor) | 한쪽 다리 델타가 진입 시 2배 도달 → 반대쪽 다리 롤 검토 (1회 한도) |
| Weekly 취급 | 이벤트 플레이 전용, 보유 1~3일, 크기 절반 |
| IV Crush 경계 | 이벤트 통과 직후 IV 급락 구간 — 매수 포지션은 이벤트 전 청산이 기본값 |
| 롤오버 | 만기 연장 롤은 "새 진입 심사"와 동일한 관문 통과 필요 (관성으로 롤 금지) |

---

# 9. 학습 요소 — 무엇을 ML로 풀고, 무엇을 규칙으로 두는가

| 구분 | 방식 | 근거 |
|---|---|---|
| IV Surface 피팅 | 수치 피팅 (SVI/스플라인) | 이론이 확립된 영역, ML 불필요 |
| **Vol Forecaster** | HAR-RV 기준모델 + LightGBM 잔차 보정 | 실현변동성 예측 — 옵션 AI의 핵심 ML. 레이블 = 미래 N일 실현변동성 |
| **전략 Meta-Model** | LightGBM 이진 분류 | "이 조건에서 이 매트릭스 셀의 전략이 이익이었는가" — Futures AI의 Meta-Labeler와 동형 (Ver 1.2 §5) |
| 매트릭스 임계 (IV Rank 30/70 등) | Walk-Forward 재추정 | 학습이 아니라 주기적 캘리브레이션 |
| §6 안전규칙 | **고정 규칙 (학습 금지)** | 안전장치는 데이터가 아니라 원칙이다 |
| 검증 | Ver 1.2 §8과 동일 스킴 (Walk-Forward, Purged CV, Deflated Sharpe, 비용 차감) | 옵션 스프레드 비용 2배 스트레스 추가 |

---

# 10. 구현 모듈 구조

```
src/fuoption/strategy/options/
├─ surface.py         # IV Surface 피팅, 무결성 검사
├─ vol_metrics.py     # IV Rank, Skew, Term Structure, IV−RV
├─ vol_forecast.py    # HAR-RV + LightGBM 잔차 보정
├─ matrix.py          # 방향×IV 전략 매트릭스 (configs/options.yaml 로드)
├─ evaluator.py       # 시나리오 그리드 손익, POP, Net ER
├─ safety.py          # §6 Hard Rules (독립 모듈 — 다른 코드가 우회 불가)
├─ lifecycle.py       # 수명주기 상태기계, 조정/청산 신호
├─ hedging.py         # 밴드 델타 헤징 정책
├─ service.py         # 구독→발행 진입점 (intel.options)
└─ config.py          # 임계·밴드·DTE 규칙 (YAML)
```

```python
class StrategyCandidate(BaseModel):
    structure: str                  # "IRON_CONDOR" 등
    legs: list[OptionLeg]           # 행사가·만기·방향·델타
    net_expected_return: Decimal
    pop: float
    max_loss: Decimal | None        # None = 무한 (safety가 즉시 기각)
    greeks: GreeksProfile           # Δ, Γ, Θ, V
    rationale: dict                 # 매트릭스 셀, IV Rank, 방향 점수 (XAI)

class OptionsAIService:
    """5분봉 완성 + intel.futures 갱신 시 후보 산출, 보유 중 lifecycle 상시 감시"""
    async def on_bar_close(self, msg) -> None: ...
    async def on_tick_risk(self, msg) -> None: ...   # 안전장치 예외 경로
```

- 모든 임계값은 `configs/options.yaml` — 릴리스 번들에 포함, 멀티 PC 복제 배포 시 동일 적용 (Ver 1.1 §7)

---

# 11. 실패 모드와 방어

| 실패 모드 | 증상 | 방어 |
|---|---|---|
| IV Surface 오염 (얇은 호가) | 잘못된 평가로 후보 왜곡 | 유동성 필터 + 피팅 잔차 감시 → 신뢰불가 시 진입 보류 (§3.2) |
| IV Crush | 이벤트 후 매수 포지션 급손 | 이벤트 전 매수 청산 기본값 (§8) |
| Gamma 폭발 | 만기 직전 급변 손실 | DTE≤2 강제 청산 (§6-4) |
| 방향 변질 | 중립 전략이 방향 베팅화 | 밴드 델타 헤징 (§7) |
| 테일 리스크 | 급락 시 매도 전략 파멸 | 네이키드 금지 + Skew 극단 필터 + 프리미엄 2배 손절 (§6) |
| 유동성 증발 | 청산 불가 | 진입 시 유동성 필터, 포지션 크기에 호가잔량 상한 연동 (L4) |

---

# 12. 다음 단계

**Ver 1.4 — Feature Dictionary**: 전 Feature(300~500개)의 표준 정의서. Feature Store 스키마(id, version, 수식, 의존 데이터, Horizon 적합성, 테스트 케이스)와 카테고리별 사전 — 마이크로구조 / 가격·추세 / 변동성 / 수급 / 옵션 파생 / 국면·매크로 / 이벤트.
