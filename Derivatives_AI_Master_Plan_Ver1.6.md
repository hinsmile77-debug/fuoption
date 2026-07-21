# Derivatives AI Master Plan — Ver 1.6

## Machine Learning 모델 설계

### Date: 2026-07-21

### 기반 문서: Ver 1.0~1.5, Holding Policy Ver 1.0

---

# 0. 목적과 설계 철학

이 문서는 시스템의 모든 ML 모델의 사양, 학습·검증 파이프라인, 모델 번들·Registry를 정의한다.

3대 철학:

1. **단순한 모델 + 좋은 Feature > 복잡한 모델 + 평범한 Feature.**
   금융 탭형 데이터에서 부스팅 트리는 여전히 왕이다. 복잡성은 성능이 아니라 관리 비용부터 늘린다.
2. **CPU 전제** (Ver 1.0.1 §4.1): 전 모델이 GPU 없는 일반 PC에서 학습·추론 가능해야 한다.
   야간 학습 예산: 전체 재학습이 8시간(1 야간) 안에 끝나야 한다.
3. **모든 모델은 같은 관문을 통과한다**: 어떤 모델도 Validator(§8)와 Shadow 20거래일 없이 live가 되지 않는다.

> 비유: F1 머신(딥러닝)이 아니라 랠리카(부스팅)를 만든다.
> 서킷(클린 데이터·GPU 팜)이 아닌 비포장길(노이즈 시장·일반 PC)에서는
> 정비가 쉽고 강건한 차가 이긴다.

---

# 1. 모델 인벤토리

| # | 모델 | 유형 | 알고리즘 | 학습 주기 | 추론 주기 |
|---|---|---|---|---|---|
| M1 | Horizon Expert ×6 | 3-class 분류 | LightGBM ×5 앙상블 | 월 1회 | 각 완성봉 |
| M2 | Meta-Labeler ×6 | 이진 분류 | LightGBM (얕음) | 월 1회 | Expert 신호 시 |
| M3 | Regime AI | 국면 분류 | HMM + 규칙 하이브리드 | 분기 1회 | 30m 완성봉 |
| M4 | Vol Forecaster | 회귀 | HAR-RV + LightGBM 잔차 | 월 1회 | 30m 완성봉 |
| M5 | 전략 Meta-Model | 이진 분류 | LightGBM | 월 1회 | 5m 완성봉 |
| M6 | Conformal 교정기 | 통계 교정 | 분위수 교정 | **매일** | 추론마다 적용 |

총 학습 대상: LightGBM 계열 6×5 + 6 + 1 + 1 = 38개 부스터 + HMM 1개 — 전부 CPU 야간 예산 내.

---

# 2. M1 — Horizon Expert 상세 사양

## 2.1 문제 정의

- 입력: 실효 Feature 세트 20~50개 (Ver 1.5 §5)
- 레이블: Triple Barrier 3-class {+1, 0, −1} (Ver 1.2 §3), 비용 반영 강등 적용
- 샘플 가중치: 평균 고유도(uniqueness) (Ver 1.2 §3.3)

## 2.2 LightGBM 사양

```yaml
objective: multiclass (num_class=3)
metric: multi_logloss
boosting: gbdt
# ---- 탐색 공간 (Optuna, 창 내부 CV로만 탐색) ----
num_leaves:        [15, 63]        # 작게 — 과적합 1차 방어
max_depth:         [3, 8]
min_data_in_leaf:  [200, 2000]     # 크게 — 노이즈 리프 방지
learning_rate:     [0.01, 0.1] (log)
feature_fraction:  [0.5, 0.9]
bagging_fraction:  [0.5, 0.9], bagging_freq: 1
lambda_l1:         [0, 10] (log)
lambda_l2:         [0, 10] (log)
# ---- 고정 ----
early_stopping:    100 라운드 (창 내부 검증 폴드 기준)
num_boost_round:   최대 2000 (조기종료가 실질 결정)
monotone_constraints: Ver 1.5 확정 목록 (예: ms_tflow↑ → P(long) 비감소)
seed: 앙상블 멤버별 상이
```

- 탐색 예산: Expert당 Optuna 50 trial × Purged 5-Fold — 6 Expert 병렬로 야간 예산 내
- **하이퍼파라미터는 화려하게 굴리지 않는다**: 탐색 공간 자체를 보수적으로 좁혀 두는 것이 Deflated Sharpe를 지키는 길

## 2.3 미니 앙상블 (×5)

- 구성: 동일 탐색 결과 상위 설정 1개 × {seed, bagging 서브샘플} 5종
- 출력 통합: 확률 평균 = 최종 P(+1/0/−1), 표준편차 = 불확실성 원료 (Ver 1.2 §6)
- 5개인 이유: 분산 추정에 최소한이면서 추론 지연(×5)이 예산(10ms) 내 — CPU에서 LightGBM 50 Feature 추론은 개당 1ms 미만

---

# 3. M3 — Regime AI 사양

## 3.1 하이브리드 구조 (Ver 1.0.1 §1.8)

```
[통계층] Gaussian HMM (상태 수 4~6, 30m 완성봉 구동)
   입력: vl_vol_ratio, px_trend_r2, px_autocorr, rg_corr_avg, rg_basis_z, op_ivrank …
   출력: 상태 확률 벡터 + 상태 지속시간
[규칙층] 오버라이드 (통계보다 우선)
   - ev_econ_grade ≥ 2 이고 ev_econ_prox ≤ 1일 → 강제 "이벤트" 국면
   - 위클리/동시만기 당일 → 강제 "이벤트" 국면
   - vl_vol_ratio > 극단 임계 → 즉시 "고변동성" (HMM 지연 보완)
[명명층] HMM 상태 → 의미 부여 (추세상승/추세하락/횡보/고변동성)
   상태별 사후 통계(평균 수익률·변동성)로 자동 라벨링 + 사람 검수
```

- HMM인 이유: 국면은 레이블이 없는 문제 — 비지도 + 전이확률(국면의 끈적함)이 자연스럽다
- 상태 수는 BIC + 사후 해석 가능성으로 선정 (해석 안 되는 상태가 나오면 수를 줄인다)
- 출력: `intel.regime` = {국면, 확신도(상태확률), 지속시간, 전환확률}

---

# 4. M4 — Vol Forecaster 사양

```
1단: HAR-RV 기준모델 (선형회귀)
     RV_{t+N} ~ RV_daily + RV_weekly + RV_monthly     ← 해석 가능한 뼈대
2단: LightGBM 잔차 모델
     잔차 ~ 나머지 Feature (op_*, ev_econ_*, vl_jump …)  ← 비선형 보정
최종 예측 = HAR 예측 + 잔차 예측
```

- 2단 구조인 이유: HAR만으로 설명되는 부분을 트리가 다시 배우게 하지 않는다 —
  트리는 "HAR이 놓치는 것"(이벤트 프리미엄, 점프 여파)에만 집중
- 레이블: 미래 5거래일 실현변동성 (Options AI 만기 시계와 정합)
- 평가: QLIKE + MSE, 기준모델 대비 개선률 — HAR을 못 이기면 2단은 배포하지 않는다 (1단 단독 운용)

---

# 5. M2·M5 — Meta 계열 학습 파이프라인

## 5.1 데이터 생성 (공통 패턴)

```
① 1차 모델(Expert/매트릭스)을 과거 구간에 Walk-Forward로 "가상 운용"
② 발생한 신호만 수집 (신호 없는 바는 버림)
③ 레이블: 그 신호를 따랐을 때 비용 차감 후 이익 여부 (이진)
④ 입력: 신호 시점의 조건 Feature (Ver 1.5 §3.7) + 1차 모델 출력
```

- **주의: 1차 모델이 학습에 쓴 구간의 신호는 Meta 학습에서 제외** — 1차가 암기한 구간의 신호는 실전보다 좋아 보이는 착시(look-ahead) 발생
- 모델: LightGBM, max_depth 3~4, num_leaves ≤ 15 고정 (필터는 단순해야 필터답다 — Ver 1.2 §5)

## 5.2 임계값 결정

- Meta 통과 임계 τ는 검증 구간에서 **비용 차감 후 기대수익 최대화**로 선정 (정확도 최대화가 아니다)
- Regime별 보정(Ver 1.2 §7.1)을 더해 최종 τ(regime) 테이블로 번들에 포함

---

# 6. M6 — 확률 교정과 Conformal

## 6.1 확률 교정 (Calibration)

- 부스팅 확률은 과신 경향 → 검증 폴드에서 **Isotonic Regression** 교정기 학습, 번들에 포함
- 효과: "P(long)=0.7"이 실제로 10번 중 7번 맞는 확률이 된다 — Kelly 사이징(L4)의 전제 조건
  (교정 안 된 확률로 Kelly를 돌리면 과대 배팅으로 직행한다)

## 6.2 Conformal 구간 (Ver 1.2 §6)

```
매일 장 마감 후:
  최근 20거래일의 (예측확률, 실제결과) 수집
  → 비적합도 점수 분위수 산출 (유의수준 α=0.1)
  → 다음 날 예측에 구간 폭 부여: "P=0.72 ± q"
```

- 재학습이 아니라 교정만 — 계산은 초 단위, 매일 실행 (M6이 매일 주기인 이유)
- 국면 전환 직후 구간이 자동으로 넓어진다 → Aggregator·Sizer가 자동 보수화 — **시장이 이상해지면 시스템이 스스로 소심해진다**

---

# 7. Trainer 구현 설계 (L6, Ver 1.1 §6-1)

## 7.1 파이프라인 단계

```
[1] 데이터 준비   Parquet 로드 → parity 검증 통과분만 (Ver 1.1 §2-3)
[2] 레이블 생성   Triple Barrier + uniqueness (labeling.py)
[3] 탐색·학습     Optuna(Purged 5-Fold) → 최적 설정 → 앙상블 5개 학습
[4] 교정          Isotonic + Conformal 초기화
[5] 번들 패키징   §9 포맷으로 직렬화
[6] Validator 제출 → 통과 시 Registry(candidate) 등록
```

## 7.2 재현성 규율

- 모든 학습 실행은 `run_id`와 함께 기록: 데이터 구간 해시, feature_set 버전, 코드 git SHA, 시드, 탐색 이력
- **같은 run_id 입력 → 비트 단위 동일 모델**이 재현 목표 (n_jobs 고정, deterministic 모드)
- 실험 추적: MLflow (로컬) — "무엇을 시도했고 왜 버렸는지"의 축적 (Ver 1.0.1 §2.2)

## 7.3 CPU 시간 예산 (일반 PC 8시간 야간 기준)

| 작업 | 추정 | 비고 |
|---|---|---|
| 레이블·Feature 준비 | ~1h | Polars 벡터화 |
| Expert 6개 탐색+학습 | ~4h | 프로세스 병렬 (코어 수만큼) |
| Meta·Vol·전략 모델 | ~1h | |
| 검증 리포트 생성 | ~1h | |
| 여유 | ~1h | 초과 시 탐색 trial 수 자동 축소 |

---

# 8. Validator 구현 설계 (L6, Ver 1.1 §6-2)

- 관문 수치: Ver 1.2 §8.3 표를 그대로 적용 (비용 차감 Sharpe > 1.0, Deflated Sharpe p < 0.05, 일관성 등)
- 추가 검사:
  1. **교정 품질**: Brier Score·신뢰도 다이어그램 — 교정 실패 모델은 성능 무관 기각
  2. **Feature 의존 건전성**: 단일 Feature 중요도 > 40% → 경고 (한 재료에 목숨 건 모델은 취약)
  3. **추론 지연**: 번들 로드 후 1000회 추론 벤치마크 — 예산(10ms) 초과 시 기각
  4. **직렬화 왕복**: 저장→로드→동일 출력 검증 (배포 사고의 고전적 원인)
- 산출물: HTML 검증 리포트 (성과 곡선, 창별 성과, 중요도, 교정 곡선) → Registry에 첨부, UI에서 열람

---

# 9. Model Bundle 포맷과 Registry 스키마

## 9.1 번들 구조 (릴리스의 원자 단위)

```
bundle_5m_v2026.08.01/
├─ manifest.yaml          # 아래 스키마
├─ experts/  e1..e5.lgb   # 앙상블 멤버 (LightGBM native 포맷)
├─ meta_labeler.lgb
├─ calibrator.pkl         # Isotonic
├─ conformal_state.json   # 분위수 상태 (매일 갱신되는 유일한 부분)
├─ feature_set.yaml       # 실효 Feature 목록+버전 (Ver 1.5) — 추론기가 이 순서로 벡터 구성
└─ thresholds.yaml        # τ(regime) 테이블, 가중치
```

```yaml
# manifest.yaml
bundle_id: "5m_v2026.08.01"
horizon: 5m
trained_range: [2024-07-01, 2026-06-30]
run_id: "..."             # 재현 키 (§7.2)
feature_set: "v2026.08"
validation_report: "reports/5m_v2026.08.01.html"
gates_passed: {sharpe_net: 1.34, dsr_pvalue: 0.021, latency_ms: 3.2}
status: candidate          # → shadow → live → retired
```

## 9.2 Registry 상태기계 (Ver 1.1 §6-3 확정)

```
candidate ──(Shadow 편성)──▶ shadow ──(20거래일 우위+사람 승인)──▶ live
    │                          │                                   │
    └──(관문 미달 즉시)────────┴──(성적 미달)──▶ retired ◀──(강등)──┘
```

- live는 Horizon당 정확히 1개. 승격 시 이전 live는 자동 retired (롤백 가능하게 보존)
- 전 인스턴스(멀티 PC)는 릴리스 번들의 모델만 사용 — PC별 임의 모델 금지 (Ver 1.1 §7)

---

# 10. 추론 서빙 (Hot Path 관점)

- 완성봉 이벤트 구동: `feat.{h}` 수신 → 벡터 구성(feature_set.yaml 순서) → 앙상블 5회 추론 → 교정 → Conformal 폭 → `intel.*` 발행
- LightGBM native 추론 사용 (ONNX 변환은 현 규모에서 불필요한 복잡성 — 벤치마크상 native로 예산 충족)
- 번들은 프로세스 기동 시 1회 로드 후 메모리 상주. 교체는 재시작으로만 (장중 핫스왑 금지 — Ver 1.1 §6 장중 배포 금지와 동일 철학)
- 입력 방어: 벡터 구성 시 feature_set과 수신 Feature의 버전 불일치 → 추론 거부 + WARN (침묵 오염 방지)

---

# 11. 딥러닝 확장 조건 (명시적 보류)

다음 조건이 **모두** 충족될 때만 착수하는 실험 항목으로 보류한다.

1. GPU 확보 (또는 클라우드 스팟 예산)
2. 부스팅 대비 개선 가설이 구체적일 것 (예: 호가창 시퀀스의 시간 구조 — TCN/작은 Transformer)
3. 통과 관문은 동일: Validator + Shadow 20거래일 — 딥러닝이라는 이유로 면제 없음

부스팅을 못 이기면 채택하지 않는다. 목표는 최신 기술이 아니라 위험조정수익이다.

---

# 12. 실패 모드와 방어

| 실패 모드 | 증상 | 방어 |
|---|---|---|
| 과신 확률 | Kelly 과대 배팅 | Isotonic 교정 필수 + 교정 품질 관문 (§6.1, §8) |
| 탐색 과적합 | 화려한 백테스트, 실전 부진 | 보수적 탐색 공간 + DSR + Shadow 강제 (§2.2) |
| 학습-추론 벡터 불일치 | 조용한 성능 저하 | feature_set 버전 검증, 불일치 시 추론 거부 (§10) |
| 국면 전환 직후 붕괴 | 연속 손실 | Conformal 구간 자동 확대 → 자동 보수화 (§6.2) |
| 야간 학습 초과 | 아침 배포 불가 | 시간 예산 감시, trial 자동 축소 (§7.3), 미완료 시 기존 live 유지 |
| 번들 손상 배포 | 기동 실패/오출력 | 직렬화 왕복 검증 + 기동 자가 점검 (§8, Ver 1.1 §7.3) |

---

# 13. 다음 단계

**Ver 2.0 — Derivatives AI Full Architecture**: 전 버전(1.0~1.6 + Holding Policy)의 통합 완결판.
Meta Decision·Portfolio·Risk·Execution의 수치 확정, Self Evolution 전체 루프의 운영 절차,
Phase별 구현 로드맵(무엇을 몇 주차에 만드는가), 그리고 100~200페이지 설계서의 목차 체계 확립.
