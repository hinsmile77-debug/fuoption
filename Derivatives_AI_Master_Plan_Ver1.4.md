# Derivatives AI Master Plan — Ver 1.4

## Feature Dictionary (Feature 표준 사전)

### Date: 2026-07-21

### 기반 문서: Ver 1.0~1.3, Holding Policy Ver 1.0

---

# 0. 목적과 사용법

이 문서는 Feature Store(Ver 1.1 §2-1)에 등록되는 **모든 Feature의 표준 사전**이다.

- 기저(Base) Feature 약 150개 × 윈도우 파라미터화 = **실제 등록 300~500개**
- 여기서는 "무엇을 계산하는가"를 정의하고, "어느 Horizon에 무엇을 쓰는가"는 Ver 1.5가 확정한다
- 모든 Feature는 **완성봉 규율**(Ver 1.2 §2.2)을 따른다: 해당 Horizon 완성봉 확정 시점 값만 사용

> 비유: 이 문서는 주방의 "표준 레시피북"이다. 어느 요리사(전문가 모델)가 어떤 재료를
> 쓸지는 메뉴판(Ver 1.5)이 정하지만, 재료의 손질법(계산식)은 여기 단 한 곳에만 적는다.
> 같은 재료를 두 곳에서 다르게 손질하는 순간 주방(시스템)은 오염된다.

---

# 1. Feature Store 스키마

## 1.1 등록 항목 (Feature 1개당)

```yaml
id: ms_ofi_20            # {카테고리}_{이름}_{윈도우}
version: 2               # 계산식 변경 시 증가 (구버전 계산 이력 보존)
category: MS
base: ms_ofi             # 기저 Feature
window: 20               # 룩백 윈도우 (해당 Horizon의 완성봉 개수)
inputs: [md.book.l1]     # 의존 데이터 토픽
formula: "Σ_{i∈W} (ΔBidQty_i − ΔAskQty_i)"   # 사람이 검증 가능한 수식
unit: contracts
range_check: [-1e6, 1e6] # 이상치 감지 경계
nan_policy: ffill_max3   # NaN 처리: 최대 3바 전방 채움 후 NaN 확정
test_vector: tests/features/ms_ofi.parquet    # 고정 입력→고정 출력 회귀 테스트
lineage: "v1: 단순합 → v2: 잔량가중 (2026-07-21)"
status: candidate        # candidate | active | quarantined | retired
```

## 1.2 명명 규칙

`{cat}_{name}_{window}` — 소문자 스네이크. 윈도우 없는 상태형은 `{cat}_{name}`.

| 접두어 | 카테고리 | 기저 개수 (목표) |
|---|---|---|
| `ms` | 마이크로구조 (호가·체결) | ~30 |
| `px` | 가격·추세·모멘텀 | ~30 |
| `vl` | 변동성 (실현·레인지) | ~16 |
| `fl` | 수급 (외국인·기관·프로그램·OI) | ~18 |
| `op` | 옵션 파생 (IV·Greeks·PCR·GEX) | ~26 |
| `rg` | 국면·시장폭·베이시스·매크로 | ~18 |
| `ev` | 이벤트·시간·만기 | ~14 |
| 합계 | | **~152 기저** |

## 1.3 표준 윈도우 세트 (파라미터화 규칙)

기저 Feature 하나는 아래 윈도우 세트로 전개된다 (단위: 해당 Horizon의 완성봉 수).

| 세트 | 윈도우 | 적용 대상 |
|---|---|---|
| W-fast | {5, 20} | ms 전체, px 단기형 |
| W-std | {5, 20, 60} | px, vl, fl 대부분 |
| W-slow | {20, 60, 120} | rg, op 추세형 |
| 상태형 | 윈도우 없음 | ev 전체, 수준값(op_ivrank 등) |

전개 예: `ms_ofi` × W-fast = `ms_ofi_5`, `ms_ofi_20` → 2개 등록.
**총 등록 수 추정: 기저 152 × 평균 2.4 ≈ 370개** (목표 300~500 충족).

---

# 2. 카테고리 사전

표 형식: **ID(기저) | 이름 | 정의(수식 요약) | 윈도우 세트**

## 2.1 MS — 마이크로구조 (30개)

호가창과 체결의 미시 압력. 1m~5m 전문가의 주식량이다.

| ID | 이름 | 정의 | 세트 |
|---|---|---|---|
| ms_spread | 호가 스프레드 | ask1 − bid1 (틱 단위) | W-fast |
| ms_spread_rel | 상대 스프레드 | (ask1−bid1)/mid | W-fast |
| ms_mid_ret | 미드 수익률 | Δlog(mid) | W-fast |
| ms_microprice | 마이크로프라이스 괴리 | (bid1·Qa+ask1·Qb)/(Qa+Qb) − mid | W-fast |
| ms_imb_l1 | L1 잔량 불균형 | (Qb1−Qa1)/(Qb1+Qa1) | W-fast |
| ms_imb_l5 | L5 잔량 불균형 | Σ5호가 잔량 기준 동식 | W-fast |
| ms_imb_l10 | L10 잔량 불균형 | Σ10호가 잔량 기준 동식 | W-fast |
| ms_depth_total | 총 호가 깊이 | Σ(Qb+Qa) 10호가 | W-fast |
| ms_depth_ratio | 매수/매도 깊이비 | ΣQb/ΣQa | W-fast |
| ms_book_slope | 호가 기울기 | 호가별 잔량의 가격 기울기 회귀 | W-fast |
| ms_ofi | 주문흐름 불균형(OFI) | Σ(ΔBidQty−ΔAskQty) | W-fast |
| ms_ofi_norm | 정규화 OFI | OFI/평균깊이 | W-fast |
| ms_vol_delta | 볼륨 델타 | 매수체결량−매도체결량 | W-fast |
| ms_vol_delta_cum | 누적 볼륨 델타 | 당일 누적 | 상태형 |
| ms_tflow | 체결강도 | 매수체결/매도체결 비율 | W-fast |
| ms_aggr_ratio | 공격 체결 비율 | 시장가성 체결/전체 | W-fast |
| ms_trade_count | 체결 건수 | 바 내 체결 수 | W-fast |
| ms_avg_trade_size | 평균 체결 크기 | 거래량/체결건수 | W-fast |
| ms_large_ratio | 대량 체결 비중 | 상위 5% 크기 체결의 양 비중 | W-fast |
| ms_tick_rule | 틱룰 부호합 | Σ sign(Δprice) 체결 기준 | W-fast |
| ms_uptick_ratio | 업틱 비율 | 업틱 체결/전체 | W-fast |
| ms_quote_rate | 호가 갱신률 | 호가 업데이트 수/초 | W-fast |
| ms_cancel_ratio | 취소 비율 | 취소량/신규호가량 | W-fast |
| ms_queue_imb | 대기열 불균형 | best 호가 대기잔량 변화 비대칭 | W-fast |
| ms_kyle_lambda | 카일 람다 | \|Δmid\|/서명거래량 회귀계수 (가격충격) | W-std |
| ms_amihud | 아미후드 비유동성 | \|ret\|/거래대금 | W-std |
| ms_roll_spread | 롤 유효스프레드 | 2√(−cov(Δp_t,Δp_{t−1})) | W-std |
| ms_vpin | VPIN 근사 | 볼륨버킷 불균형 평균 (독성 주문흐름) | W-std |
| ms_runlen | 연속 방향 체결 길이 | 동일 방향 체결 run 길이 | W-fast |
| ms_absorb | 흡수 지표 | 대량 체결 후 가격 미반응 정도 | W-fast |

## 2.2 PX — 가격·추세·모멘텀 (30개)

| ID | 이름 | 정의 | 세트 |
|---|---|---|---|
| px_ret | 로그 수익률 | Δlog(close) | W-std |
| px_mom | 모멘텀 | close/close[−W] − 1 | W-std |
| px_accel | 가속도 | mom_W − mom_2W (모멘텀의 변화) | W-std |
| px_vwap_dev | VWAP 괴리 | (close−VWAP)/ATR | W-std |
| px_ema_dev | EMA 괴리 | (close−EMA_W)/ATR | W-std |
| px_ema_cross | EMA 교차 상태 | sign(EMA_fast−EMA_slow), 경과 바 수 | W-std |
| px_rsi | RSI | 표준 RSI(W) | W-std |
| px_stoch | 스토캐스틱 %K | (close−minW)/(maxW−minW) | W-std |
| px_macd_h | MACD 히스토그램 | 표준 (ATR 정규화) | W-std |
| px_bb_pos | 볼린저 위치 | (close−MA)/2σ | W-std |
| px_bb_width | 볼린저 폭 | 4σ/MA (스퀴즈 감지) | W-std |
| px_don_pos | 돈치안 채널 위치 | (close−minW)/(maxW−minW) | W-std |
| px_high_dist | 고점 거리 | (maxW−close)/ATR | W-std |
| px_low_dist | 저점 거리 | (close−minW)/ATR | W-std |
| px_breakout | 돌파 강도 | 돌파 후 경과 바·되돌림 정도 | W-std |
| px_trend_slope | 추세 기울기 | 종가 회귀 기울기/ATR | W-std |
| px_trend_r2 | 추세 결정계수 | 위 회귀의 R² (추세 신뢰도) | W-std |
| px_adx | ADX | 표준 ADX(W) | W-std |
| px_zscore | 가격 Z-score | (close−MA)/σ | W-std |
| px_autocorr | 수익률 자기상관 | corr(ret_t, ret_{t−1}) over W | W-std |
| px_hurst | 허스트 지수 | R/S 근사 (추세성 vs 회귀성) | W-slow |
| px_skew_r | 수익률 왜도 | rolling skew | W-std |
| px_kurt_r | 수익률 첨도 | rolling kurtosis (팻테일 경보) | W-std |
| px_max_ret | 최대 단봉 수익 | max(ret) in W | W-std |
| px_dd | 롤링 드로다운 | close/maxW − 1 | W-std |
| px_runup | 롤링 런업 | close/minW − 1 | W-std |
| px_gap_open | 시가 갭 | log(open/전일 close) | 상태형 |
| px_open_ret | 당일 시가 대비 | log(close/open) | 상태형 |
| px_range_pos_d | 당일 레인지 위치 | (close−일중저가)/(일중고가−일중저가) | 상태형 |
| px_round_dist | 라운드넘버 거리 | 가까운 라운드 레벨까지 ATR 거리 | 상태형 |

## 2.3 VL — 변동성 (16개)

| ID | 이름 | 정의 | 세트 |
|---|---|---|---|
| vl_rv | 실현변동성 | √Σret² (연율화) | W-std |
| vl_park | 파킨슨 변동성 | 고저 레인지 기반 | W-std |
| vl_gk | 가먼-클래스 | OHLC 기반 | W-std |
| vl_yz | 양-장 변동성 | 갭 포함 OHLC 기반 | W-std |
| vl_atr | ATR | 표준 ATR(W) | W-std |
| vl_atr_rel | 상대 ATR | ATR/close | W-std |
| vl_vol_ratio | 변동성 비율 | RV_fast/RV_slow (레짐 전환 감지) | 상태형 |
| vl_vov | 변동성의 변동성 | σ(RV) over W | W-slow |
| vl_semi_dn | 하방 준분산 | √Σ(ret⁻)² | W-std |
| vl_semi_up | 상방 준분산 | √Σ(ret⁺)² | W-std |
| vl_semi_ratio | 준분산 비 | semi_dn/semi_up (하방 쏠림) | W-std |
| vl_jump | 점프 성분 | RV − Bipower Variation | W-std |
| vl_range_exp | 레인지 확장 | 현재바 레인지/평균 레인지 | W-fast |
| vl_squeeze | 압축 지표 | BB폭의 백분위 (폭발 전 압축) | W-slow |
| vl_har_pred | HAR-RV 예측치 | 일/주/월 RV 회귀 예측 | 상태형 |
| vl_intraday_shape | 일중 변동성 패턴 괴리 | 현재 RV/시간대별 평균 RV | 상태형 |

## 2.4 FL — 수급·미결제 (18개)

| ID | 이름 | 정의 | 세트 |
|---|---|---|---|
| fl_frgn_net | 외국인 선물 순매수 | 바 내 순매수 계약 | W-std |
| fl_frgn_cum | 외국인 누적 (당일) | 당일 누적 순매수 | 상태형 |
| fl_frgn_streak | 외국인 연속성 | 연속 순매수 바/일 수 | 상태형 |
| fl_inst_net | 기관 선물 순매수 | 동식 | W-std |
| fl_inst_cum | 기관 누적 (당일) | 동식 | 상태형 |
| fl_indiv_net | 개인 순매수 | 동식 (역지표 후보) | W-std |
| fl_prog_arb | 프로그램 차익 | 차익거래 순매수 | W-std |
| fl_prog_nonarb | 프로그램 비차익 | 비차익 순매수 | W-std |
| fl_spot_frgn | 현물 외국인 순매수 | KOSPI 현물 | W-std |
| fl_flow_px_div | 수급-가격 다이버전스 | sign(수급)≠sign(가격추세) 강도 | W-std |
| fl_flow_conc | 수급 집중도 | 주체별 순매수의 편중도(HHI) | W-std |
| fl_oi | 미결제약정 | 총 OI 수준 | 상태형 |
| fl_oi_chg | OI 변화 | ΔOI (바/일) | W-std |
| fl_oi_px_combo | OI-가격 조합 상태 | 가격↑OI↑=신규매수 등 4상태 분류 | 상태형 |
| fl_frgn_oi_est | 외국인 포지션 추정 | 누적 순매수 기반 추정 재고 | 상태형 |
| fl_top_trader | 상위 거래원 쏠림 | 거래원 상위 집중도 (제공 시) | W-std |
| fl_spot_fut_gap | 현선 수급 괴리 | 현물 수급 − 선물 수급 방향차 | W-std |
| fl_flow_mom | 수급 모멘텀 | 순매수의 이동평균 대비 가속 | W-std |

## 2.5 OP — 옵션 파생 (26개)

Vol Engine(Ver 1.3 §3) 산출물의 Feature화. 5m 이상 Horizon과 Options AI 전용.

| ID | 이름 | 정의 | 세트 |
|---|---|---|---|
| op_iv_atm | ATM IV | 근월 등가격 IV | 상태형 |
| op_iv_chg | IV 변화 | ΔATM IV | W-std |
| op_ivrank | IV Rank | 252일 내 위치 0~100 | 상태형 |
| op_ivpct | IV Percentile | 252일 백분위 | 상태형 |
| op_iv_rv | IV−RV 스프레드 | ATM IV − vl_har_pred (변동성 프리미엄) | 상태형 |
| op_skew_rr25 | 25Δ 리스크리버설 | put25Δ IV − call25Δ IV | 상태형 |
| op_skew_chg | 스큐 변화 | Δrr25 | W-std |
| op_smile_bf25 | 25Δ 버터플라이 | (put25+call25)/2 − ATM (스마일 곡률) | 상태형 |
| op_term_slope | 기간구조 기울기 | 차월 IV − 근월 IV | 상태형 |
| op_term_chg | 기간구조 변화 | Δslope (이벤트 프리미엄 유입 감지) | W-std |
| op_pcr_vol | 거래량 PCR | 풋 거래량/콜 거래량 | W-std |
| op_pcr_oi | 미결제 PCR | 풋 OI/콜 OI | 상태형 |
| op_pcr_prem | 프리미엄 PCR | 풋 거래대금/콜 거래대금 | W-std |
| op_gex | 감마 익스포저 | Σ(감마×OI×승수) 딜러 부호 가정 | 상태형 |
| op_gex_flip | 감마 플립 거리 | GEX=0 레벨까지 지수 거리 | 상태형 |
| op_dex | 델타 익스포저 | Σ(델타×OI×승수) | 상태형 |
| op_vanna_exp | 반나 익스포저 | Σ(반나×OI) — IV 변화 시 헤지 수요 | 상태형 |
| op_charm_exp | 참 익스포저 | Σ(참×OI) — 시간 경과 헤지 수요 | 상태형 |
| op_maxpain_dist | 맥스페인 거리 | 맥스페인 행사가까지 거리/ATR | 상태형 |
| op_wall_call | 콜 월 거리 | 최대 콜 OI 행사가 거리 | 상태형 |
| op_wall_put | 풋 월 거리 | 최대 풋 OI 행사가 거리 | 상태형 |
| op_oi_chg_call | 콜 OI 변화 | 주요 행사가 콜 OI 증감 | W-std |
| op_oi_chg_put | 풋 OI 변화 | 동식 | W-std |
| op_vk_level | VKOSPI 수준 | 변동성지수 | 상태형 |
| op_vk_chg | VKOSPI 변화 | Δ | W-std |
| op_vk_rv | VKOSPI−RV | 지수형 변동성 프리미엄 | 상태형 |

## 2.6 RG — 국면·시장폭·베이시스·매크로 (18개)

| ID | 이름 | 정의 | 세트 |
|---|---|---|---|
| rg_basis | 베이시스 | 선물 − 현물 (이론가 정규화) | 상태형 |
| rg_basis_z | 베이시스 Z-score | 최근 분포 대비 | W-slow |
| rg_theo_dev | 이론가 괴리 | (선물−이론가)/틱 | W-std |
| rg_basis_mom | 베이시스 모멘텀 | Δbasis 추세 | W-std |
| rg_breadth_adv | 등락 비율 | 상승종목/전체 (KOSPI) | 상태형 |
| rg_breadth_nh | 신고가−신저가 | 52주 기준 순신고가 수 | 상태형 |
| rg_breadth_vol | 상승 거래대금 비중 | 상승종목 거래대금/전체 | 상태형 |
| rg_sector_disp | 섹터 분산도 | 섹터 수익률 표준편차 | W-std |
| rg_corr_avg | 평균 종목상관 | 대형주 상관 평균 (공포 시 ↑) | W-slow |
| rg_usdkrw_ret | 환율 수익률 | Δlog(USDKRW) | W-std |
| rg_usdkrw_vol | 환율 변동성 | RV(USDKRW) | W-slow |
| rg_es_ret | 미국 지수선물 수익률 | ES/NQ 연동 (야간 갭 예측력) | W-std |
| rg_ust10y_chg | 미 금리 변화 | Δ10Y | W-std |
| rg_vix | VIX 수준·변화 | 미국 변동성 | 상태형 |
| rg_nk_ret | 닛케이 수익률 | 동조화 | W-std |
| rg_cnh_ret | 위안화 변화 | 아시아 리스크 프록시 | W-std |
| rg_trend_comp | 종합 추세 지표 | px_trend 계열의 30m 합성 | 상태형 |
| rg_regime_age | 국면 지속 시간 | 현 Regime 진입 후 경과 (Regime AI 부산물) | 상태형 |

## 2.7 EV — 이벤트·시간·만기 (14개)

전부 상태형. 모델이 "지금이 언제인가"를 알게 하는 시계 Feature.

| ID | 이름 | 정의 |
|---|---|---|
| ev_tod_sin / ev_tod_cos | 시각 인코딩 | 장중 시각의 사인/코사인 (주기성 보존) |
| ev_open_elapsed | 개장 경과 | 개장 후 경과 분 (정규화) |
| ev_close_remain | 마감 잔여 | 마감까지 잔여 분 (강제청산 인지) |
| ev_dow | 요일 | one-hot |
| ev_dte_fut | 미니선물 만기 D-day | 잔여 거래일 |
| ev_dte_opt_m | 먼스리 옵션 D-day | 잔여 거래일 |
| ev_dte_opt_w | 위클리 옵션 D-day | 잔여 거래일 |
| ev_expiry_flag | 만기일 플래그 | 당일 만기 여부 (동시만기 별도 가중) |
| ev_rollover_win | 롤오버 구간 | 롤오버 활성 구간 여부 |
| ev_econ_prox | 경제지표 근접도 | 다음 주요 지표까지 시간 (등급 가중) |
| ev_econ_grade | 이벤트 등급 | FOMC=3, CPI=3, 고용=2 … |
| ev_overnight_gap_risk | 야간 이벤트 예정 | 오늘 밤 주요 이벤트 유무 |
| ev_holiday_adj | 연휴 인접 | 연휴 전후 거래일 플래그 |
| ev_lunch_flag | 점심 구간 | 유동성 저하 시간대 플래그 |

---

# 3. 등록·품질 절차 (Ver 1.0.1 §1.4 구체화)

```
후보 등록(candidate) → ① 단독 검정 → ② 중복 검정 → ③ 생존 검정 → active
                                                      미달 시 → retired
     active → 중요도 붕괴/parity 불일치 감지 → quarantined (자동 격리)
```

| 관문 | 기준 (초기값) |
|---|---|
| ① 단독 예측력 | 목표 레이블과의 정보계수(IC) 유의성, 최소 \|IC\| > 0.02 |
| ② 중복 제거 | 기존 active Feature와 상관 \|ρ\| > 0.9 → 예측력 낮은 쪽 탈락 |
| ③ 생존 검정 | Walk-Forward 3개 창 이상에서 중요도 상위 80% 내 유지 |
| 상시 감시 | 실시간-배치 parity 불일치(Ver 1.1 §2-3), NaN 비율, 중요도 급변 → 격리 |

- 격리(quarantined)된 Feature는 재검정 통과 전까지 어떤 모델에도 공급되지 않는다
- 사전은 자산이다: retired도 삭제하지 않고 사유와 함께 보존 — 같은 아이디어의 재발명 방지

---

# 4. 카운트 집계

| 카테고리 | 기저 | 전개 후 (근사) |
|---|---|---|
| MS | 30 | ~66 |
| PX | 30 | ~78 |
| VL | 16 | ~40 |
| FL | 18 | ~40 |
| OP | 26 | ~44 |
| RG | 18 | ~34 |
| EV | 14 | ~15 |
| **합계** | **152** | **~317** |

- 300~500 목표 범위 내. 추가 확장은 신규 기저가 아니라 **검증된 기저의 윈도우 추가**를 우선한다
- 실전 투입은 Horizon당 20~50개 (Ver 1.0.1 §1.4) — 사전이 크다고 모델이 다 먹는 게 아니다

---

# 5. Feature Discovery Engine — 동적 피처셋 확장 (Champion–Challenger)

2장의 사전은 기관·헤지펀드·학계에서 검증된 "고전 레퍼토리"다. 훌륭한 출발점이지만,
좋은 Feature일수록 널리 알려진 순간부터 우위가 닳는다(**Alpha Decay**).
따라서 사전을 정적 자산이 아니라 **신진대사하는 생태계**로 운영한다.

> 비유: 챔피언(현 피처셋)은 방어전을 치르는 타이틀 홀더다.
> 스카우트(탐색 엔진)가 전 세계 체육관(논문·웹)을 돌며 도전자를 발굴하고,
> 도전자는 스파링(Shadow 검증)에서 이겨야만 링에 오른다.
> 챔피언이라는 이유만으로 영원히 타이틀을 보장받지 못한다.

## 5.1 3개의 탐색 소스

### 소스 A — 외부 스캔 (신상 발굴, 월 1회)

주기적으로 웹을 뒤져 새 Feature 아이디어를 수집하는 자동 파이프라인.

```
수집 → LLM 1차 해석 → 수식화 초안 → 사람 검토 → candidate 등록
```

| 단계 | 내용 |
|---|---|
| 수집 | arXiv q-fin, SSRN, 주요 학회(NeurIPS·ICML 금융 워크숍), 실무 블로그·오픈소스 릴리스를 키워드 크롤링 (microstructure, order flow, volatility forecasting, option flow …) |
| LLM 1차 해석 | 초록·본문에서 "계산 가능한 Feature 후보"만 추출, 사전 스키마(§1.1) 초안으로 자동 변환 — 필요한 입력 데이터가 우리에게 없으면 그 자리에서 탈락 |
| 사람 검토 | 월 1회 리뷰 세션: 경제적 논리가 설명되는가? (설명 안 되는 후보는 아무리 성적이 좋아도 기각 — 데이터 마이닝 함정 방지) |
| 등록 | 통과분만 candidate로 등록, `provenance`에 출처(논문 DOI·URL) 기록 |

### 소스 B — 내부 합성 (기존 재료의 재조합, 분기 1회)

- 검증된 기저의 변형: 새 윈도우, 비율·차분·교차항 (예: `ms_ofi × vl_squeeze`)
- 모델 힌트 활용: SHAP 상호작용 상위 쌍을 곱·비율 Feature로 승격 시험
- 조합 폭발 통제: 사이클당 합성 후보 상한 20개 — 무한 조합은 과적합 공장이다

### 소스 C — 결원 충원 (수시)

- 격리(quarantined)·은퇴(retired)로 빈자리가 생긴 카테고리를 우선 탐색 대상으로 지정
- Self Evaluation의 "Horizon별 성능 저하" 신호를 탐색 우선순위에 반영 — 아픈 곳부터 보강

## 5.2 Challenger 검증 경로 (기존 관문 재사용)

```
candidate (provenance 기록)
  → §3 관문 ①②③ (IC 검정·중복 제거·Walk-Forward 생존)
  → Challenger Set 편성: 챔피언 피처셋 + 도전 Feature로 재학습한 모델
  → Shadow Trading 20거래일 (Ver 1.1 §6-4) — 챔피언 모델과 실시간 병주
  → 우위 확인 시: 피처셋 버전 승격 (feature_set: v2026.08) → 릴리스 번들로 전 PC 배포
```

- **Feature 단독이 아니라 "피처셋 버전"으로 승격한다** — 모델·피처셋·설정이 한 릴리스로 묶여 멀티 PC 복제 배포(Ver 1.1 §7)와 정합
- 다중 검정 규율: 사이클당 도전자 수 상한(외부 10 + 합성 20), FDR 보정 및 Deflated Sharpe 적용 — 많이 던지면 우연히 몇 개는 맞는다는 함정을 통제
- 탈락 이력도 자산: "어느 논문의 아이디어가 우리 시장에서 왜 안 됐는지"를 retired 사유로 축적 → 같은 실험의 반복 방지

## 5.3 운영 주기 요약

| 활동 | 주기 | 산출물 |
|---|---|---|
| 외부 스캔 + LLM 해석 | 월 1회 | 신규 candidate ≤ 10 |
| 내부 합성 | 분기 1회 | 합성 candidate ≤ 20 |
| 챔피언 방어전 (Shadow 비교) | 분기 1회 | 피처셋 버전 승격/유지 결정 |
| 사전 대청소 | 반기 1회 | 장기 미사용·중복 기저 정리 |

## 5.4 스키마 추가 필드

§1.1 스키마에 다음을 추가한다.

```yaml
provenance:              # 출처 추적
  source: "arXiv:2605.01234"   # 논문/블로그/internal-synthesis
  discovered: 2026-08-01
  hypothesis: "옵션 딜러 감마 헤지 수요가 종가 부근 추세를 증폭"  # 경제적 논리 한 줄 (필수)
challenger_record:       # 방어전 이력
  shadow_period: [2026-08-10, 2026-09-05]
  vs_champion: "+0.18 Sharpe"
```

- `hypothesis` 필드는 **필수**다. "왜 되는지" 한 줄을 못 쓰는 Feature는 등록 자체가 안 된다

---

# 6. 다음 단계

**Ver 1.5 — Horizon별 Feature Mapping**: 이 사전의 Feature를 1m/3m/5m/10m/15m/30m 전문가와 Options AI·Regime AI에 배정. Horizon별 초기 후보군(50~80개) → 선정 절차 → 실효 세트(20~50개) 확정 프로세스.
