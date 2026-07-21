# Derivatives AI Master Plan — Ver 1.5

## Horizon별 Feature Mapping

### Date: 2026-07-21

### 기반 문서: Ver 1.2 (Horizon Expert), Ver 1.3 (Options AI), Ver 1.4 (Feature Dictionary)

---

# 0. 목적과 범위

Ver 1.4가 레시피북(재료 정의)이라면, 이 문서는 **메뉴판**이다 —
어느 요리사(전문가 모델)가 어떤 재료(Feature)를 받는지를 배정한다.

- 대상: Futures AI 6개 Horizon Expert + Options AI(Vol Forecaster·전략 Meta-Model) + Regime AI + Meta-Labeler
- 산출: Horizon별 **초기 후보군(50~80개)** 과 그로부터 **실효 세트(20~50개)** 를 뽑는 선정 절차
- 여기의 배정표는 "출발값"이다 — 최종 실효 세트는 데이터(선정 절차 §5)가 결정하고, 분기마다 갱신된다

---

# 1. 매핑 3원칙

## 1.1 정보 반감기 매칭

Feature마다 담고 있는 정보가 유효한 수명이 다르다.
**Feature의 정보 반감기 ≈ Horizon 길이**일 때 예측력이 최대가 된다.

> 비유: 생선(호가 불균형)은 오늘 팔아야 하고, 와인(수급 누적)은 묵혀야 값을 한다.
> 생선을 30분 전문가에게 주면 이미 상해 있고, 와인을 1분 전문가에게 주면 아직 맛이 안 든다.

| 정보 수명 | Feature 계열 | 적정 Horizon |
|---|---|---|
| 초~분 | 호가 잔량, OFI, 체결강도 (MS) | 1m~5m |
| 분~시간 | VWAP 괴리, 모멘텀, ATR (PX·VL) | 5m~15m |
| 시간~일 | 수급 누적, OI 변화 (FL) | 10m~30m |
| 일~주 | 베이시스, IV Rank, 시장폭, 매크로 (RG·OP) | 30m·Regime·Options |

## 1.2 완성봉 규율상의 윈도우 해석 (Ver 1.2 §2.2)

윈도우 단위는 "해당 Horizon의 완성봉 수"다. 같은 `px_mom_20`이라도
1m 전문가에겐 20분 모멘텀, 30m 전문가에겐 10시간 모멘텀이다.
**따라서 동일 기저라도 Horizon마다 사실상 다른 Feature이며, 교차 오염이 없다.**

## 1.3 시계 Feature는 전원 공통

EV 카테고리(시각·만기·이벤트)는 모든 전문가의 공통 기본 세트다.
"지금이 언제인가"를 모르는 전문가는 없어야 한다. (단, 요일 등 저정보 항목은 선정 절차에서 자연 탈락 허용)

---

# 2. 카테고리 × Horizon 적합성 매트릭스

●=주력, ◐=보조, ○=소수 후보, −=배제

| 카테고리 \ 대상 | 1m | 3m | 5m | 10m | 15m | 30m | Options | Regime |
|---|---|---|---|---|---|---|---|---|
| MS 마이크로구조 | **●** | **●** | ◐ | ○ | ○ | − | ○(유동성) | − |
| PX 가격·추세 | ◐ | ◐ | **●** | **●** | ◐ | ◐ | ○ | ◐ |
| VL 변동성 | ○ | ◐ | ◐ | ◐ | ◐ | ◐ | **●** | **●** |
| FL 수급·OI | − | ○ | ◐ | ◐ | **●** | **●** | ○ | ◐ |
| OP 옵션 파생 | − | − | ○ | ○ | ◐ | **●** | **●** | ◐ |
| RG 국면·매크로 | − | − | ○ | ○ | ◐ | **●** | ◐ | **●** |
| EV 이벤트·시간 | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ |

- 1m에서 FL·OP·RG를 배제하는 이유: 수급·매크로는 분 단위로는 갱신조차 안 되는 저속 데이터 — 1분 전문가에게는 상수나 다름없어 노이즈 학습만 유발
- 30m에서 MS를 배제하는 이유: 호가 미시 압력의 반감기는 분 단위 — 30분 뒤엔 이미 소멸

---

# 3. Horizon별 초기 후보군

형식: 후보 구성 비율 → 핵심 Feature(대표) → 후보 수 → 배정 논리

## 3.1 — 1m Expert (후보 ~55개)

- 구성: **MS 65%** + PX 15% + VL 10% + EV 10%
- 핵심: `ms_imb_l1/l5`, `ms_ofi(5,20)`, `ms_microprice`, `ms_vol_delta`, `ms_tflow`, `ms_aggr_ratio`, `ms_queue_imb`, `ms_cancel_ratio`, `ms_runlen`, `ms_absorb`, `ms_spread_rel` + `px_ret(5,20)`, `px_zscore_20` + `vl_range_exp` + `ev_tod`, `ev_close_remain`, `ev_lunch_flag`
- 논리: 1분의 승부는 호가창 안에서 난다. 추세·수급은 이 시계에선 배경 소음
- 특칙: 비용 잠식 위험(Ver 1.2 §10) 때문에 **스프레드·유동성 Feature는 필수 유지** (Meta-Labeler가 거래 가능 여부 판단에 사용)

## 3.2 — 3m Expert (후보 ~60개)

- 구성: MS 45% + PX 25% + VL 15% + FL 5% + EV 10%
- 핵심: 1m 세트의 상위 생존자 + `px_mom(5,20)`, `px_vwap_dev`, `px_accel`, `px_bb_pos` + `vl_atr_rel`, `vl_semi_ratio` + `fl_prog_arb`(프로그램은 분 단위 갱신되는 유일한 수급)
- 논리: 미시 압력이 짧은 모멘텀으로 번역되는 구간 — 두 언어를 다 듣는 통역 전문가

## 3.3 — 5m Expert (후보 ~70개) — 기준 전문가

- 구성: MS 25% + **PX 35%** + VL 15% + FL 10% + OP 5% + EV 10%
- 핵심: `px_vwap_dev`, `px_mom(5,20,60)`, `px_trend_slope/r2`, `px_don_pos`, `px_breakout`, `px_rsi` + `ms_ofi_20`, `ms_vol_delta_cum`, `ms_kyle_lambda` + `vl_atr`, `vl_squeeze`, `vl_jump` + `fl_frgn_net_20`, `fl_prog_arb` + `op_iv_chg` + EV 공통
- 논리: 미시와 거시가 만나는 균형점 — 가장 먼저 구현하는 기준 전문가(Ver 1.2 §4.2)이므로 후보 폭을 가장 넓게 준다

## 3.4 — 10m Expert (후보 ~65개)

- 구성: MS 10% + **PX 35%** + VL 15% + FL 20% + OP 5% + RG 5% + EV 10%
- 핵심: `px_trend_slope/r2(20,60)`, `px_adx`, `px_ema_cross`, `px_dd/runup` + `fl_frgn_net/cum`, `fl_flow_px_div`, `fl_oi_chg` + `vl_vol_ratio`, `vl_har_pred` + `rg_theo_dev` + `op_pcr_vol`
- 논리: 추세의 지속성 검증 구간 — "이 추세에 수급이 실려 있는가"가 질문

## 3.5 — 15m Expert (후보 ~60개)

- 구성: MS 5% + PX 20% + VL 15% + **FL 30%** + OP 10% + RG 10% + EV 10%
- 핵심: `fl_frgn_cum/streak`, `fl_inst_cum`, `fl_spot_frgn`, `fl_oi_px_combo`, `fl_flow_conc`, `fl_spot_fut_gap` + `px_mom_60`, `px_hurst` + `op_pcr_oi`, `op_gex` + `rg_basis_z`, `rg_breadth_adv`
- 논리: 주체들의 하루 설계가 드러나는 시계 — 외국인의 오전 누적이 오후를 말해준다

## 3.6 — 30m Expert (후보 ~65개)

- 구성: PX 15% + VL 15% + **FL 20%** + **OP 20%** + **RG 20%** + EV 10%
- 핵심: `rg_basis/basis_z/basis_mom`, `rg_breadth_*`, `rg_es_ret`, `rg_usdkrw_ret`, `rg_vix`, `rg_corr_avg` + `op_ivrank`, `op_skew_rr25`, `op_gex/gex_flip`, `op_vk_rv`, `op_term_slope` + `fl_frgn_cum`, `fl_frgn_oi_est` + `px_trend_comp` 계열 + `vl_vol_ratio`, `vl_vov`
- 논리: 국면의 전문가 — Regime AI와 입력 일부를 공유하되(Ver 1.2 §4.2) 출력 목적이 다르다 (방향 vs 국면 분류)

## 3.7 — Meta-Labeler (전 Horizon 공통 틀, 각 ~15개)

- 구성: 1차 모델 출력(확률·마진·앙상블 분산) + `ms_spread_rel`, `ms_depth_total`(거래 가능성) + `vl_range_exp`, `vl_vol_ratio`(환경 급변) + `intel.regime` + `ev_econ_prox`, `ev_close_remain`
- 논리: 관측수는 과녁(방향)이 아니라 바람(조건)만 본다 — 의도적으로 작은 세트 유지 (Ver 1.2 §5)

---

# 4. Options AI · Regime AI 매핑

## 4.1 Vol Forecaster (후보 ~40개)

- 핵심: `vl_har_pred`(기준모델 출력), `vl_rv/yz/gk(멀티윈도우)`, `vl_vov`, `vl_jump`, `vl_semi_ratio`, `vl_intraday_shape` + `op_iv_atm/iv_chg`, `op_vk_level/chg`, `op_term_slope` + `rg_vix`, `rg_usdkrw_vol` + `ev_econ_prox/grade`, `ev_dte_opt_*`
- 레이블: 미래 N일 실현변동성 (Ver 1.3 §9) — 이벤트 근접 Feature가 IV 프리미엄 예측의 핵심

## 4.2 전략 Meta-Model (후보 ~35개)

- 핵심: `op_ivrank/ivpct`, `op_iv_rv`, `op_skew_rr25/chg`, `op_smile_bf25`, `op_gex/gex_flip`, `op_maxpain_dist`, `op_wall_*`, `op_pcr_*` + Futures AI 통합점수 S + `rg_regime_age` + `ev_dte_opt_*`, `ev_econ_prox`
- 레이블: "해당 매트릭스 셀 전략의 사후 손익" (Ver 1.3 §9)

## 4.3 Regime AI (후보 ~45개)

- 핵심: `rg_*` 전체 + `vl_vol_ratio`, `vl_vov`, `vl_squeeze` + `op_ivrank`, `op_skew_rr25`, `op_term_slope`, `op_vk_rv` + `fl_frgn_cum/streak` + `px_hurst`, `px_autocorr`, `px_trend_r2` + `ev_*` 공통
- 논리: 국면 분류의 재료는 "가격이 어떻게 움직이는가의 성질"(추세성·회귀성·상관구조)이지 방향이 아니다

---

# 5. 선정 절차 — 후보군(50~80) → 실효 세트(20~50)

Horizon마다 독립적으로, 다음 4단계 깔때기를 통과시킨다.

```
후보군 (§3·§4)
 → ① IC 스크리닝        해당 Horizon 레이블과의 정보계수 |IC| 하위 30% 컷
 → ② 상관 클러스터링     |ρ|>0.7 군집화 → 군집당 IC 최상위 1~2개만 대표 선발
 → ③ 안정성 선택        Walk-Forward 5개 창 × LightGBM 순열중요도
                        → 상위 50% 진입 빈도 ≥ 3/5 인 것만 생존
 → ④ 실효 세트 확정     20~50개 상한 적용, 스프레드·유동성 등 "필수 지정" 예외 유지
```

- ①의 IC는 **해당 Horizon의 Triple Barrier 레이블** 기준 — 5m에서 좋은 Feature가 1m에서도 좋다고 가정하지 않는다
- ③ 안정성 선택이 본선이다: 한 구간에서 반짝인 Feature가 아니라 **여러 시장 국면에서 꾸준히 쓸모 있던** Feature만 남긴다
- 단조 제약 후보(Ver 1.2 §4.1): `ms_tflow`↑→P(long) 비감소, `fl_frgn_net`↑→P(long) 비감소 등 — 선정과 동시에 제약 목록 확정
- 전 과정은 스크립트로 자동화하고 결과 리포트(생존/탈락 사유)를 남긴다 — 사람은 리포트를 검토·승인만

---

# 6. 유지보수 — 매핑도 살아있는 문서다

| 활동 | 주기 | 트리거 |
|---|---|---|
| 실효 세트 재선정 (§5 재실행) | 분기 1회 | 정기 (재학습 사이클과 동기) |
| 긴급 재선정 | 수시 | Feature 격리 발생, Horizon 성능 급락 (Self Evaluation) |
| 후보군 갱신 | 분기 1회 | Discovery Engine 승격분 편입 (Ver 1.4 §5) — 신입은 해당 카테고리가 주력인 Horizon 후보군에 우선 배치 |
| 매트릭스(§2) 재검토 | 반기 1회 | 배제(−) 칸의 타당성 재확인 — "1m에 OP 배제"조차 영구 진리가 아니다 |

- 모든 실효 세트는 `feature_set` 버전으로 릴리스 번들에 포함 (Ver 1.4 §5.2) → 멀티 PC 복제 배포 시 전 인스턴스 동일 세트 보장

---

# 7. 다음 단계

**Ver 1.6 — Machine Learning 모델 설계**: LightGBM 사양(하이퍼파라미터 탐색 공간·조기종료·단조 제약), 미니 앙상블 구성, Conformal 교정 상세, Meta-Labeler 학습 파이프라인, Trainer/Validator 구현 설계, 모델 번들 포맷과 Registry 스키마.
