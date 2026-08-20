# MESSIAH 일일 점검 — 2026-08-12 / 장후

- 점검 시각: 16:05 KST
- 대상 국면: post (장후) — 예약 실행(`messiah-postmarket-check`)
- HEAD `ce51375` · 실행 중 `4825ffe` · 미커밋 174건
- 증거: `logs/dailycheck/evidence_20260812_post.md`
- 장후 배치: **완주 확인** — `logs/postmarket_20260812.log` 15:46:14~15:47:16, `=== 장후 절차 요약 ===` 5/5 전 단계 완료(5단계 ⚠는 "볼 것이 있다" = 리포트가 임계 초과를 찾았다는 뜻이지 단계 실패가 아니다)

## 0. 한 줄 결론

**데이터 축은 오늘 사상 최고였는데(임계 초과 1건, 08-10 11건 → 08-11 4건 → 오늘 1건), 판단 축은 하루 종일 한 번도 살아 있지 않았다** — 국면 판정이 구조적으로 100% `UNKNOWN`이라 14건의 판단이 전부 첫 관문에서 접혔고, 그 사실을 재는 축이 리포트에 없다.

| 국면 | 판정 | 근거 요약 |
|---|---|---|
| 장전 | **정상** | 자가점검 2회 전 항목 `[OK ]` · `schedule_drift` 정본 일치 · 08:20/08:25 정시 트리거 · 수집 시작 지연 0.5분 · 기동 창 거절 후 정시 기동 완결 |
| 장중 | **조건부** | 파이프라인 4종 `OK` · 거래량 항등식 유실 0 · `late_bar_drops` 0 · 커버리지 99.8~100% — 그러나 판단 경로가 전량 NO_TRADE(P0-1)이고 11:05 수급 1사이클 결손(P1-2) |
| 장후 | **조건부** | 배치 5/5 완주 · 산출물 7종 전부 존재 · 거래량 대조 0.998 — 그러나 15:36 예비 리포트가 매일 확정 오탐 ERROR를 만들고(P1-1) postmarket 자신이 `SessionEnd`를 안 남긴다(P2-1) |

---

## 1. 이상점 정리보고

### P0 — 오늘 거래에 직접 위험

#### 1-1. 국면 판정이 하루 종일 `UNKNOWN` — 판단 사슬 전량이 첫 관문에서 접힌다

- **증상**: 당일 발행된 Meta Decision 14건이 **하나도 빠짐없이** `② Regime=UNKNOWN — 이벤트/미판정 국면` · `side=NO_TRADE`다. 국면 분포가 아니라 상수다.

- **근거**:
  ```
  38행  국면 결선 — RegimeAI 상태 5개 · 명명 {0:HIGH_VOL, 1:RANGE, 2:RANGE, 3:TREND_UP, 4:TREND_DOWN} · 구동 30m
  40행  09:00:01 [INFO] DecisionEmitted  ② Regime=UNKNOWN — 이벤트/미판정 국면  side=NO_TRADE
  41행  09:30:00 [INFO] DecisionEmitted  ② Regime=UNKNOWN — 이벤트/미판정 국면  side=NO_TRADE
   …    (09:00:01 ~ 15:30 · 30분 간격 · 총 14건 · 전량 동일)
  ```
  `logs/g2_daily_20260812.log` · `DecisionEmitted`×14 · 09:00:01~15:30:00
  ```json
  logs/self_eval_2026-08-12.json
  "wiring_summary": "… live 번들 real-20260811-1604-30m · shadow 0개 · 판단 14건 · 주문 0건 · 체결집계 불가"
  ```

- **기전 (확정 — 코드로 산술이 닫힌다)**:

  | 단계 | 값 | 출처 |
  |---|---|---|
  | `classify()`가 UNKNOWN을 즉시 반환하는 하한 | `min_length = window + 2` = **22봉** | `src/messiah/strategy/regime/service.py:133-141` |
  | `RegimeRuntime`이 기동 시 보유한 30m 봉 | **0봉** (`deque(maxlen=200)`을 빈 채로 생성, 웜스타트 인자 없음) | `src/messiah/strategy/regime/runtime.py:41`, `scripts/run_g2_paper_trading.py:265` |
  | 하루가 만드는 30m 봉 | **15봉** | `logs/postmarket_20260812.log` 「2026-08-12 1m=410 → 3m=137 5m=82 10m=42 15m=28 **30m=15**」 |

  15 < 22 이므로 **장 마감까지 단 한 번도 하한을 못 넘긴다.** 오늘만의 사고가 아니라 매 거래일 결정적으로 보장되는 상태다.

- **기준 위반**:
  - 마스터플랜 Ver 2.0 §9 W24~26 「Aggregator·Meta Decision·Risk Engine·Sizer·Kill Switch — **전 경로 관통**」 — 관통이 Meta Decision 첫 분기에서 끊긴다.
  - SYSTEM.md 금지계명 12(조용한 폴백 금지) — 모델은 정상 로드됐고("국면 결선" 출력) 판정만 계속 실패하는데 전부 `INFO`로 지나간다. `WARNING` 한 줄도 없다.
  - `references/phases.md` D 「건수 0은 두 가지다 — 진짜 없었거나, 계측이 없거나」 — 주문 0건이 "기회가 없어서 0"으로 읽히는 상태다.

- **영향**: Risk Engine · Sizer · OrderGateway가 **하루 한 번도 호출되지 않는다.** 08-11 장후에 결선한 사슬(`2ac5339`)의 실효가 0이고, 그 상태로 며칠이 더 가면 "판단은 나오는데 주문이 없다"를 시장 탓으로 오독하게 된다. self_eval의 `win_rate`/`PF`/`Sharpe`가 계속 `null`인 이유도 여기다.

- **신규 여부**: **재발 계열(신규 증상)**. 08-11 DECISION_LOG 「결함 ② — RegimeAI가 상수 분류기였다」를 커밋 `2ac5339`(*"관측 하나만 넘겨서 전이행렬이 죽어 있었다 — 사슬 결선 + 결함 넷"*)로 고쳤으나, 고친 것은 **추론**(forward filtering)이고 **급전(feed)** 은 손대지 않았다. 증상이 `TREND_DOWN` 상수 → `UNKNOWN` 상수로 자리만 옮겼다. NEXT_TODO **S-3**(「실시간 국면 분포가 홀드아웃과 비슷한가」)가 정확히 이것을 물었고, 답은 **아니다**.

- **같은 문제를 이미 푼 선례가 사내에 있다**:
  ```
  logs/l1_daily_20260812.log  FeatureWarmStart
  "과거 완성봉으로 롤링 윈도 사전 충전 (용량 200봉)"  bars_by_horizon: {… "30m": 200}
  ```
  FeatureEngine은 30m 200봉을 사전 충전받는다. RegimeRuntime만 0봉으로 출발한다. **처방이 이미 옆에 있다.**

#### 1-2. 국면 분포를 재는 축이 리포트에 없다 — 1-1이 오늘 어떤 경보도 못 울린 이유

- **증상**: 국면이 100% `UNKNOWN`인 날에 `daily_integrity_20260812.json`의 `breaches`는 **1건**이고 그 1건은 수급 다리 결손이다. 판단 축의 전면 마비가 리포트를 아무 흔적 없이 통과했다.

- **근거**: `daily_integrity_20260812.json` 최상위 키 41개 전수 확인 — `regime`, `decision`, `no_trade` 계열 필드 **0개**. 존재하는 판단 측 지표는 `tag_counts.DecisionEmitted: 14`(건수만) 뿐이다.
  ```json
  "breaches": ["flow_intraday/K2I: 11:05 사이클 2/3다리 — 그 구간은 영구 소실(소급 경로 없음)"]
  ```

- **기준 위반**: SYSTEM.md R6(구조화 로깅 — 세션 경계·심각도 규율) 및 `references/phases.md` B-3 「Expert 판단 발행 건수 — 0건이면 사슬 어딘가가 끊긴 것」. 이 체크리스트는 **0건**만 함정으로 보는데, 오늘은 **14건 전부 같은 사유**라는 더 나쁜 형태였고 그물이 없었다.

- **영향**: 1-1을 사람이 로그를 눈으로 읽어야만 발견할 수 있다. `FixVerification` 등록부에 올릴 지표가 없으니 "고쳤다"의 검증도 불가능하다 — 이 프로젝트가 네 번 반복한 실패 형태(측정 없는 수정)의 다섯 번째 자리다.

- **신규 여부**: **신규**. dev_memory에 국면 분포 축 신설 항목 없음(S-3은 "볼 것"으로만 적혀 있고 자동 축이 아니다).

### P1 — 정확성·관측 훼손

#### 1-3. 15:36 예비 리포트가 매일 확정 오탐 ERROR 1건을 만든다 (`daily-axes-measured`)

- **증상**: 같은 날짜에 대해 리포트가 두 번 생성되는데, 앞의 것이 반드시 거짓 `재발`을 낸다.
  - 15:36:07 `l1_daily` 생성분 → ERROR 5건 (`daily-axes-measured` 포함)
  - 15:47:13 `postmarket` 재생성분 → ERROR 4건 (`daily-axes-measured` **없음**)
  - 최종 `daily_integrity_20260812.json` → `"unmeasured": []` (**애초에 위반이 아니었다**)

- **근거**:
  ```
  logs/l1_daily_20260812.log
  15:36:07 [ERROR] FixVerificationRecurred
    daily-axes-measured: 2026-08-12에 기준 위반(오늘) — 수정이 듣지 않았다 (unmeasured_count ≤ 0)
  15:36:07 [INFO]  IntegrityReportGenerated  breaches=1  path=logs\daily_integrity_20260812.json
  ```
  산출물 생성 시각 — `volume_check_20260812.json` **15:45:14** · `vol_scorecard_20260812.json` **15:46:12**. 즉 15:36 시점에 두 축은 물리적으로 존재할 수 없다.

- **기전**: `src/messiah/ops/integrity_report.py:1023-1031` 주석이 설계 의도를 명시한다 — *"REST 호출을 장후 종료 절차(15:35~15:40)에 넣지 않는다는 판단은 유지하되 … 없으면 `unmeasured`로 올라간다."* 의도 자체는 옳다. 문제는 **그 판정이 등록부 채점에까지 그대로 흘러간다**는 점이다.

- **왜 지금 터졌는가 (전일 대비 델타)**: 08-10까지는 이 두 도구를 사람이 저녁에 수동 실행했다(`volume_check_20260810.json` **18:03**, `vol_scorecard_20260810.json` **19:39**) — 그날 15:36 리포트는 이미 다른 사유로 시끄러웠고 이 축은 "08-07에 기준 위반"이라는 과거 표기였다. **08-11에 장후 배치를 15:45로 정시화한 뒤 08-11·08-12 이틀 연속 "오늘 위반"**으로 바뀌었다. 자동화가 만든 부작용이다.
  ```
  logs/l1_daily_20260810.log : "daily-axes-measured: 2026-08-07에 기준 위반 …"   (과거 표기)
  logs/l1_daily_20260811.log : "daily-axes-measured: 2026-08-11에 기준 위반(오늘) …"
  logs/l1_daily_20260812.log : "daily-axes-measured: 2026-08-12에 기준 위반(오늘) …"
  ```

- **기준 위반**: `src/messiah/ops/fix_verification.py` 모듈 docstring 「**재발**이 이 모듈의 존재 이유다」 — 그 최고 신호가 매일 1건씩 가짜로 채워진다. `references/phases.md` D 「회색이 여러 뜻을 겸하면 그것부터 분리 대상」.

- **영향**: 등록부의 신호 대 잡음비가 매일 나빠진다. 오늘 `l1_daily` ERROR 5건 중 1건이 확정 거짓이고, 그것을 알려면 11분 뒤 파일과 대조해야 한다. 늑대소년이 만들어지는 정확한 기전이며, 이 프로젝트가 08-11에 「오탐을 끄지 않고 옮겼다」로 두 건을 처리한 것과 같은 계열이다.

- **신규 여부**: **신규**. dev_memory에 `daily-axes-measured` 언급은 3곳 있으나(NEXT_TODO 2562·3099·3439행) 전부 지표 정의·잔여 항목 맥락이고, **"15:36 예비 생성이 원인"이라는 기전은 기록에 없다.**

#### 1-4. `leg-completeness-measured` 오늘 위반 — 재시도가 안 든 게 아니라 덜 들었다

- **증상**: 수급 계열 `flow_intraday/K2I`의 11:05 사이클이 3다리 중 2다리만 적재됐다. 소급 경로 없음.

- **근거**:
  ```
  logs/l1_daily_20260812.log:302
  11:05:03 [WARNING] InvestorFlowPollError
    조회 실패(2회 시도): Server error '500 Internal Server Error' for url
    'https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/
     inquire-investor-time-by-market?FID_INPUT_ISCD=K2I&FID_INPUT_ISCD_2=F001'
    attempts=2  market_code=K2I  sector_code=F001
  ```
  ```json
  daily_integrity_20260812.json → series_coverage[flow_intraday/K2I]
  "coverage_pct": 99.8, "expected_legs": 3, "short_cycles": [["11:05", 2]], "gaps": []
  ```
  같은 날 `InvestorFlowPollRetried`×6 — **재시도 기구 자체는 작동했다.**

- **기준 위반 / 등록부 자신의 예고**: `configs/pending_verifications.yaml:499-500` —
  > *"A-4(수급 폴러 재시도 결선)의 결과를 재는 자리이기도 하다: 08-10의 3건은 재시도가 없어서 났고, 이제 있다. **며칠 뒤에도 3건이 계속 나면 재시도가 안 먹은 것**이다."*

  08-10 **3건** → 08-11 **0건** → 08-12 **1건**. 등록부가 정한 기준(3건 유지)에 비추면 **재시도는 먹었다.** 남은 1건은 재시도 2회로도 못 넘긴 KIS 서버 5xx다. 따라서 로그 문구 「수정이 듣지 않았다」는 이 건에 대해 **과장**이며, 처방은 "다시 고친다"가 아니라 "재시도 예산을 늘린다"다.

- **영향**: 소급 불가 손실 5분(`irrecoverable_loss_minutes: 5.0`). 상위 축 `IrrecoverableLossBudgetExceeded`(3거래일 51분 > 예산 20분)가 함께 울었으나 — **51분 중 41분이 08-10 하루 몫이다**(08-10 41분 · 08-11 5분 · 08-12 5분). 08-13이면 08-10이 창에서 빠져 10분이 되어 예산 내로 자연 복귀한다. **오늘의 결함이 아니라 08-10 사건의 잔상이다.**

### P2 — 운영 부담·기술부채

#### 1-5. `postmarket` 프로세스가 `SessionEnd`를 남기지 않는다

- **증상**: 장후 배치가 `SessionStart`는 찍고 `SessionEnd`는 안 찍는다. 비대칭이다.
- **근거**: `logs/postmarket_20260812.log` — 15:46:14 `SessionStart`(pid 22108, sha `ce51375`) 존재. `grep 'SessionEnd'` 결과 1행이나 그것은 `no-silent-process-death` **설명 문구 안의 단어**(140행)이고 태그로서는 **0건**. 그럼에도 `daily_integrity_20260812.json` → `"abnormal_exits": []` — 즉 이 프로세스가 감시 대상 목록에 없다.
- **기준 위반**: SYSTEM.md **R13**(종료 시퀀스 자기검증 필수) · **금지계명 14**(자기검증 없는 종료 시퀀스 금지). `references/phases.md` C-1 「프로세스별 `SessionEnd` 존재 — 없으면 비정상 종료」.
- **영향**: 장후 배치가 3/5단계에서 죽어도 `no-silent-process-death` 축은 조용하다. 오늘은 5/5 완주해 실피해 없으나, **이 상태가 "장후 배치보다 먼저 결론 내지 말라"는 운영 규율의 근거를 스스로 갉는다.**
- **신규 여부**: 신규(배치 정시화 08-11 이후 처음 관측 가능해진 자리).

#### 1-6. 점검 도구가 기동 창 거절을 중복 기동으로 센다

- **증상**: 증거 다이제스트 §9 자동 적신호 3·8이 「`l1_daily`/`g2_daily`: SessionStart 2회 — 중복 기동/재기동 확인 필요」를 올렸다. 실제로는 중복 기동이 아니다.
- **근거**: 07:23:34 `SessionStart` 직후 같은 초에 `LaunchWindowRefused` — *"기동 창(08:15~15:35) 이전 07:23:34 — 정시 트리거(08:20)에 맡기고 지금은 뜨지 않는다"*. 실기동은 08:20:28 하나뿐이다. **본 리포트는 이미 옳게 센다** — `daily_integrity_20260812.json` → `"starts_by_process": {"l1_daily": 1, "g2_paper": 1}`, `"restarts": 0`.
- **기준 위반**: 없음(도구 정확도 문제). 다만 `launch-window-refusal-not-counted` fix가 **리포트 쪽에만 반영되고 점검 도구에는 안 들어갔다**.
- **영향**: 매일 적신호 2건이 가짜로 뜬다. 도구가 어제(`5c5f621`·`b4fb6b5`) 막 들어왔으므로 지금 잡는 것이 싸다.

### 확인 필요 (확정 아님)

- **미커밋 174건** — 자가점검은 `[OK ] git dirty(dev 허용)`로 통과했고 현재 `mode=dev`라 금지계명 10 위반은 아니다. 다만 `src/messiah/` 하위 런타임 모듈이 다수 포함돼 있어(`core/bus.py`, `broker/kis/adapter.py`, `data/*` 등) **실행 중인 코드와 작업 트리가 얼마나 벌어져 있는지**를 이 점검만으로는 알 수 없다. → `git diff --stat 4825ffe -- src/` 로 런타임 영향 범위를 따로 확인하면 판정된다. paper/live 승격 전에는 반드시 정리 필요.
- **R-1 (G-4 승격 관측)** — `configs/instance.yaml:66 minute_bar_close: "timer"`로 설정은 확인되나, NEXT_TODO R-1이 요구한 「`1분봉 확정: timer (거래소 시각 경계+2.0초)`가 기동 로그에 찍히는가」는 **찍히지 않았다**(l1 로그 내 `timer` 문자열 1건, 자가점검 10행에 해당 항목 없음). 승격은 `late_bar_drops: 0`·커버리지 100%로 간접 확인되나 **직접 관측 축이 없다.** → 기동 자가점검에 한 줄 추가하면 판정된다(F-5에 포함).

---

## 2. Fix 작업 구현계획

### F-1. RegimeRuntime 웜스타트 — P0 · 대응 이상점 1-1

- **원인 가설**: `RegimeRuntime`이 인메모리 `deque`를 빈 채로 생성하고 당일 실시간 30m 봉만 받는다. `classify()`의 하한은 22봉, 하루 공급량은 15봉이라 산술적으로 도달 불가. FeatureEngine은 같은 문제를 웜스타트로 이미 풀었으나 RegimeRuntime에는 그 배선이 없다.

- **변경 파일**:
  - `src/messiah/strategy/regime/runtime.py` — `RegimeRuntime.__init__()`에 `warm_start_bars: Sequence[BarClosed] | None = None` 추가. 주어지면 `self._history.extend(warm_start_bars)`로 사전 충전하고, 충전 직후 `RegimeWarmStart` 태그를 남긴다(`FeatureWarmStart`와 대칭 — msg에 충전 봉 수와 최초/최종 봉 시각).
  - `scripts/run_g2_paper_trading.py` — `_build_regime_runtime()`(≈250-265행). `RegimeRuntime(symbol, regime_ai, bus)` 호출을 아카이브에서 최근 30m 완성봉을 읽어 넘기도록 변경. 충전량은 **200봉**(FeatureEngine과 동일, `_DEFAULT_HISTORY_LIMIT`와 일치, `min_length + _FILTER_OBSERVATIONS` = 22+60 = 82를 충분히 상회). 읽기 경로는 `FeatureWarmStart`가 쓰는 것과 **같은 로더를 재사용**한다 — 두 벌 만들면 이 프로젝트가 네 번 반복한 "정본 아닌 소비자"가 다섯 번째로 생긴다(`canonical-consumers-wired` 등록부 항목).
  - 충전 실패 시(아카이브 부족 등) `print("국면 결선 …")` 옆에 **충전 봉 수를 함께 출력**하고, 22봉 미만이면 `WARNING`으로 올린다 — 조용한 폴백 금지(금지계명 12).

- **회귀 위험**:
  1. 과거 봉과 당일 봉이 이어지면서 **휴장 경계**가 관측 계산에 섞인다. `build_observations()`는 봉 간 시간 간격을 보지 않고 순서만 쓰므로(`hmm_model.py:40-55`) 전일 종가→당일 시가 갭이 하나의 관측으로 들어간다. HMM 학습도 같은 방식으로 연속 시계열을 먹였다면 일관되지만, **학습 쪽 전처리를 먼저 확인해야 한다**(`scripts/train_regime_ai.py`). 학습이 일별로 끊었다면 런타임도 끊어야 한다.
  2. 첫 판정이 어제 국면을 그대로 이어받는다 — HMM 전제상 정상이나, 09:00 첫 판단이 전일 마감 국면으로 나가는 것이 의도인지 결정 필요.
  3. 기동 시간이 늘어난다(200봉 로드). 08:25 트리거 → 09:00 첫 판단까지 35분 여유가 있어 실질 위험 낮음.

- **검증 방법**:
  - `pytest tests/strategy/regime/` — 신규 케이스 2개: (a) 웜스타트 0봉이면 22봉 도달 전까지 UNKNOWN (현행 동작 보존), (b) 웜스타트 200봉이면 **첫 봉부터** 비-UNKNOWN 판정.
  - replay: 08-12 30m 봉 15개 + 아카이브 과거 봉으로 재생해 국면 분포가 5개 상태에 걸쳐 나오는지 확인(홀드아웃 437봉이 Viterbi로 73~100개씩 골고루 나왔다는 08-11 실측이 비교 기준).
  - 다음 거래일 관측: `RegimeWarmStart` 태그 1건 · `DecisionEmitted` 중 `Regime=UNKNOWN` 비율 **< 50%**.

- **적용 시점**: **장후(즉시 착수 가능)**. 커밋만 해두면 내일 08:25 정시 기동이 새 코드를 자동으로 태운다 — 별도 재시동 불필요.

- **결정 필요 사항**: 회귀 위험 1(휴장 경계 처리) — **권고: 학습 전처리와 동일하게 맞춘다.** 학습이 일별 분할이면 런타임 웜스타트도 당일분만 쓰게 되는데, 그러면 15봉 < 22봉이라 이 fix가 무효가 된다. 그 경우 대안은 **학습 쪽을 연속 시계열로 바꾸는 것**이고 그건 별건(G-2)이다. **착수 전 `train_regime_ai.py` 확인이 선행되어야 한다.**

### F-2. 국면 분포 축 신설 — P0 · 대응 이상점 1-2

- **원인 가설**: 판단 측 관측이 `tag_counts.DecisionEmitted`(건수)뿐이라 "몇 건 나왔나"는 알고 "무엇이 나왔나"는 모른다. 08-11에 사슬을 결선하면서 축을 같이 만들지 않았다.

- **변경 파일**:
  - `src/messiah/strategy/regime/runtime.py` — `handle_bar()`에서 `classify()` 결과를 발행할 때 `RegimeClassified` 태그 로깅 추가(regime 값·confidence·사용 봉 수). **F-1과 같은 커밋에 넣는다** — 지금은 국면의 유일한 증거가 MetaDecision의 NO_TRADE 사유 문자열이다.
  - `src/messiah/core/logging.py` — `_TAG_LEVELS`에 `"RegimeClassified": logging.INFO`, `"RegimeWarmStart": logging.INFO` 등록(R6: 태그 1개=심각도 1개).
  - `src/messiah/ops/integrity_report.py` — 리포트 dataclass에 `regime_distribution: dict[str, int]` 추가. `RegimeClassified` 태그를 집계한다. 태그가 하나도 없으면 `None`(= 미측정)이지 `{}`가 아니다 — **L18**(0과 미측정을 섞지 않는다).
  - `src/messiah/ops/fix_verification.py` `_METRICS` — `regime_unknown_ratio` 추가:
    ```python
    "regime_unknown_ratio": lambda r: (
        float(d.get("UNKNOWN", 0)) / float(sum(d.values()))
        if (d := r.get("regime_distribution")) else None
    ),
    ```
  - `configs/pending_verifications.yaml` — `regime-not-constant` 항목 등록. `metric: regime_unknown_ratio`, `max: 0.5`, 3거래일 연속. 주석에 08-12 실측(14/14 = 1.0)을 근거로 남긴다.

- **회귀 위험**: 30분마다 태그 1건 추가(하루 15행) — 로그 부피 영향 무시 가능. 기존 리포트에는 필드가 없으므로 과거 날짜 재생성 시 `None` 처리 경로를 반드시 탄다.

- **검증 방법**: `pytest tests/ops/test_fix_verification.py` — 옛 리포트(필드 없음)가 `None`을 내는지, 분포가 있으면 비율이 맞는지. 내일 리포트에 `regime_distribution`이 실리는지.

- **적용 시점**: 장후 — F-1과 **같은 커밋**. 축 없이 F-1을 넣으면 내일 고쳐졌는지 또 눈으로 봐야 한다.

- **결정 필요 사항**: 임계값. **권고 `max: 0.5`** — 국면이 정말 불확실한 날(개장 직후 웜업 구간)에 UNKNOWN이 일부 나오는 것은 정상이므로 0으로 두면 늑대소년이 된다. 절반을 넘으면 그것은 분포가 아니라 상수다.

### F-3. 15:36 예비 리포트를 등록부 채점에서 제외 — P1 · 대응 이상점 1-3

- **원인 가설**: 같은 날짜에 리포트가 두 번 생성되는데 둘을 구분하는 표시가 없어, 장후 배치 이전에 만들어진 불완전본이 등록부를 채점한다.

- **변경 파일**:
  - `src/messiah/ops/integrity_report.py` — `generate_and_write()`에 `provisional: bool = False` 인자 추가. `True`면 (a) `fix_verification` 평가·로깅을 건너뛰고 (b) JSON에 `"provisional": true`를 심는다.
  - `scripts/run_l1_daily.py` — `_write_integrity_report()`(751행)가 `provisional=True`로 호출.
  - `scripts/run_postmarket.py` — 5/5단계(무결성 리포트 재생성)는 `provisional=False`(기본값) 유지.
  - `src/messiah/ops/fix_verification.py` — 과거 리포트를 읽어 판정할 때 `provisional: true` 파일은 **건너뛴다**(그 날짜의 판정 자체를 미측정으로 둔다).
  - `src/messiah/ops/integrity_report.py` breach 규칙 — 리포트가 `provisional: true`인 채로 다음 거래일까지 남아 있으면 그 자체를 breach로 올린다.

- **왜 이 안인가 (기각한 대안)**: 대안 (b)「`unmeasured` 계산에서 15:45 이전에는 장후 산출물을 면제한다」는 **장후 배치가 아예 안 돈 날을 침묵시킨다** — 08-10이 정확히 그런 날이었고(도구를 저녁에 수동 실행), 그 침묵이 이 프로젝트가 가장 자주 반복한 실패다. (a)는 반대로 배치가 안 돌면 `provisional` 파일이 그대로 남아 **그 사실 자체가 신호**가 된다.

- **회귀 위험**: 15:47 배치가 실패하면 그날 등록부 채점이 통째로 사라진다. 위의 마지막 항목(잔존 provisional을 breach로)이 그 구멍을 막는다 — **두 변경은 반드시 함께 들어가야 한다.**

- **검증 방법**: `pytest tests/ops/test_integrity_report.py` — provisional 플래그 왕복. 08-12 로그로 리포트 2회 생성 재현해 15:36분에 `FixVerification*` 로그가 0건인지. 내일 관측: `l1_daily` 15:36 ERROR **≤ 4건**, `daily-axes-measured` 미출현.

- **적용 시점**: 장후.

- **결정 필요 사항**: 없음.

### F-4. 수급 폴러 재시도 예산 확대 — P1 · 대응 이상점 1-4

- **원인 가설**: `poll_retry.RETRY_ATTEMPTS = 2`가 KIS 서버 5xx의 지속 시간보다 짧다. 오늘 2회 모두 500을 받고 포기했다.

- **변경 파일**:
  - `src/messiah/data/poll_retry.py` — `RETRY_ATTEMPTS` 2 → **3**, `fetch_with_retry()`에 지수 백오프(`delay * 2**n`) 도입. **5xx·타임아웃만** 추가 재시도하고 4xx는 즉시 포기한다(잘못된 요청을 세 번 보내는 것은 낭비이자 레이트리밋 위험).
  - 총 재시도 시간에 상한을 둔다 — `flow_intraday`의 카덴스가 **1분**이므로(`series_coverage.cadence_minutes: 1.0`) 재시도 예산이 60초를 넘으면 다음 사이클을 밀어내 결손 1건이 2건이 된다. **상한 40초 권고.**
  - `src/messiah/data/investor_flow_poller.py` — 정본 상수를 그대로 쓰므로(45·59-60행) 별도 변경 불필요. `option_chain_poller`도 같은 정본을 공유하는지 확인할 것.

- **회귀 위험**: 옵션체인 폴러가 같은 상수를 쓴다면 그쪽 카덴스(5분·10분)에는 여유가 크지만, 실패가 잦아질 때 호출량이 1.5배가 된다 — KIS 레이트리밋 확인 필요.

- **검증 방법**: `pytest tests/data/test_poll_retry.py` — 5xx 3회 후 성공, 4xx 즉시 포기, 총 시간 상한. 내일 관측: `InvestorFlowPollError` **0건** · `short_cycles` **0건**.

- **적용 시점**: 장후.

- **결정 필요 사항**: 상한 40초 vs 3회 고정 중 무엇을 우선할지. **권고: 시간 상한이 우선**(카덴스를 밀지 않는 것이 다리 하나보다 중요하다).

### F-5. `postmarket` 종료 마커 + G-4 승격 관측 — P2 · 대응 이상점 1-5 및 확인 필요 R-1

- **변경 파일**:
  - `scripts/run_postmarket.py` — `main()`(263행) 종료 직전 `SessionEnd` 로깅(정상/비정상 구분, 5단계 요약의 ⚠ 여부를 함께). `try/finally`로 감싸 예외 경로에서도 남긴다.
  - `src/messiah/ops/integrity_report.py` — `abnormal_exits` 집계 대상 프로세스 목록에 `postmarket` 추가. **단**, 리포트를 생성하는 주체가 postmarket 자신이므로 자기 `SessionEnd`는 아직 안 찍혔다 → **당일이 아니라 다음 거래일 장전에 전일 파일을 검사**하는 방식이어야 한다. 이 순서 함정을 놓치면 매일 오탐 1건이 생긴다(1-3과 같은 형태).
  - `scripts/self_check.py` — 자가점검에 `[OK ] bar_close  1분봉 확정: timer (거래소 시각 경계+2.0초)` 한 줄 추가. `configs/instance.yaml`의 `minute_bar_close` 값을 그대로 노출한다(R-1 관측 축).

- **회귀 위험**: 위에 적은 순서 함정. 자가점검 항목 추가는 무해.

- **검증 방법**: 내일 `logs/postmarket_20260813.log`에 `SessionEnd` 1건 · 기동 자가점검에 `bar_close` 행 존재 · `abnormal_exits` 오탐 0건.

- **적용 시점**: 장후.

- **결정 필요 사항**: 없음.

### F-6. 점검 도구의 기동 창 거절 오탐 제거 — P2 · 대응 이상점 1-6

- **변경 파일**: `.claude/skills/messiah-daily-check/scripts/collect_evidence.py` — `SessionStart` 집계에서 **같은 초 내에 `LaunchWindowRefused`가 뒤따르는 건을 제외**한다. 제외 건수는 버리지 말고 「기동 창 거절 n회(정상)」로 별도 표기 — 거절 자체는 관측 가치가 있다.
- **회귀 위험**: 없음(점검 도구 전용).
- **검증 방법**: 08-12 로그로 재실행해 §9 적신호에서 3·8번이 사라지는지.
- **적용 시점**: 장후.
- **결정 필요 사항**: 없음.

### 적용 순서와 커밋 계획

1. **커밋 ①** — F-1 + F-2 (RegimeRuntime 웜스타트 + 국면 분포 축·태그) — `[MW0601] 국면이 22봉을 못 채우고 하루를 끝냈다 — RegimeRuntime 웜스타트 + 분포 축`
   - **선행**: `scripts/train_regime_ai.py`의 시계열 분할 방식 확인(F-1 결정 필요 사항)
2. **커밋 ②** — F-3 (예비 리포트 채점 제외) — `[MW0601] 11분 먼저 만든 리포트가 매일 거짓 재발을 냈다 — provisional 분리`
3. **커밋 ③** — F-4 (재시도 예산) — `[MW0601] 재시도는 먹었고 두 번이 모자랐다 — 5xx 백오프 + 시간 상한`
4. **커밋 ④** — F-5 + F-6 (종료 마커 · 승격 관측 · 점검 도구) — `[MW0601] 배치도 자기 끝을 말해야 한다 — postmarket SessionEnd + 관측 셋`

> 장후 국면이므로 코드 변경이 허용된다. 다만 **본 예약 실행은 보고까지만 한다.** 실제 구현은 "구현해" 지시가 있을 때 착수하며, 변경 후 `pytest`(해당 범위) + replay 검증을 거친다(금지계명 2). 미커밋 상태로 내일 기동에 반입하지 않는다(금지계명 10).

---

## 3. 고도화 방안

### G-1. 판단 사슬의 각 관문 통과율을 리포트 1급 축으로

- **관측 근거**: 오늘 `MetaDecisionEngine.decide()`의 5개 분기(① kill · ② 국면 · ③ 분산 · ④ |S| 미달 · ⑤ 통과) 중 **②에서 14/14가 접혔다.** 그런데 리포트가 아는 것은 `DecisionEmitted: 14`뿐이다. ③·④·⑤가 오늘 **한 번도 평가되지 않았다**는 사실 — 즉 Risk·Sizer·OrderGateway가 통째로 미검증 상태라는 사실 — 이 어디에도 남지 않았다.
- **제안 내용**: `meta_decision.py`의 `_no_trade()` 호출부마다 분기 ID(①~④)를 구조화 필드로 넘기고(`reason_code`), 리포트에 `decision_funnel: {"①": 0, "②": 14, "③": 0, "④": 0, "통과": 0}`를 싣는다. 사유 문자열 파싱이 아니라 **코드가 직접 세는** 방식이어야 한다.
- **기대 효과**: "사슬이 어디까지 살아 있는가"가 매일 한 줄로 답해진다. F-2의 `regime_unknown_ratio`가 국면 축 하나만 본다면 이쪽은 **전 경로**를 본다 — 마스터플랜 W24~26 「전 경로 관통」의 진척도를 그대로 수치화한 것이다.
- **비용·위험**: 반나절. 게이트 로직 변경이 아니라 계측 추가라 R18 섀도 계측 대상 아님.
- **선행 조건**: F-2(태그·리포트 필드 배선)가 먼저.
- **우선순위 제안**: **이번 주.** F-1이 국면을 살려내면 다음 병목은 ③ 또는 ④인데, 축이 없으면 그때 또 로그를 눈으로 읽어야 한다.

### G-2. RegimeAI 학습·추론의 시계열 경계를 하나로

- **관측 근거**: F-1을 설계하다 드러난 것 — 런타임은 인메모리 `deque`(일 단위 리셋), 학습(`train_regime_ai.py`)은 아카이브 전체를 본다. **두 쪽이 같은 시계열을 다르게 자른다.** 08-11에 발견한 「startprob이 원-핫이라 홀드아웃 437봉이 전부 TREND_DOWN」도 뿌리가 같다 — 단일 시퀀스 적합의 부작용이었다.
- **제안 내용**: 학습·추론이 **같은 관측 생성 함수**(`build_observations`)를 같은 경계 규칙으로 부르도록 강제하고, 휴장 경계 처리(잇는다/끊는다)를 한 곳에 명시적 상수로 둔다. 끊는 쪽으로 정하면 30m로는 하루 15봉이라 하한 22봉에 영원히 못 닿으므로, **그 경우 구동 Horizon을 15m(하루 28봉)로 내리는 것이 함께 검토되어야 한다.**
- **기대 효과**: 오늘 같은 "모델은 멀쩡한데 판정이 안 나오는" 상태가 구조적으로 불가능해진다.
- **비용·위험**: 1~2일. 구동 Horizon 변경은 Ver 1.1 §3-1(「입력: feat.30m」) 설계 변경이라 마스터플랜 반영 필요.
- **선행 조건**: F-1 착수 전 조사가 곧 이 항목의 1단계다.
- **우선순위 제안**: **즉시(조사)** / 이번 주(구현).

### G-3. 손실 예산에 "단일 사건 지배" 표시

- **관측 근거**: 오늘 `IrrecoverableLossBudgetExceeded`가 「3거래일에 51분(> 예산 20분)」으로 울었으나, 51분의 내역은 08-10 **41분** · 08-11 5분 · 08-12 **5분**이다. 오늘 이 경보를 읽은 사람이 처음 받는 인상은 "요즘 계속 새고 있다"인데 사실은 "사흘 전 한 번 크게 샜고 이후 안정적"이다. 내일이면 08-10이 창에서 빠져 10분으로 자동 복귀한다.
- **제안 내용**: `ops/loss_budget.py`의 경보 문구에 최대 기여일과 그 몫을 넣는다 — 「3거래일 51분(> 20분) · **최대 08-10 41분(80%)** · 나머지 2일 10분」. 임계 판정은 그대로 둔다(누적이 예산을 넘은 것은 사실이다).
- **기대 효과**: 「원인은 날마다 달랐지만 잃은 것은 같은 종류다」라는 이 축의 원래 취지는 유지하면서, **추세와 단발을 사람이 즉시 구분**한다.
- **비용·위험**: 1~2시간. 판정 로직 무변경이라 위험 없음.
- **선행 조건**: 없음.
- **우선순위 제안**: 이번 주.

### G-4. 변동성 축의 `통제후 정의불가`를 리포트가 말하게

- **관측 근거**: 오늘 채점에서 `vl_gk_5`가 3개 Horizon **전부** 「IC +0.515/+0.436/+0.128 · 통제후 **정의불가**」였다(`logs/postmarket_20260812.log` 4/5단계). 통제 변수 자신이 통제군에 들어 있어 생기는 구조적 결과인데, `vol_scorecard_20260812.json`에는 이 사실이 **아무 형태로도 남지 않는다** — `beats_baseline`에도 `absent_features`에도 없다. 콘솔 출력에만 있다.
- **제안 내용**: `run_vol_scorecard.py`가 Horizon별 `undefined_after_control: ["vl_gk_5"]`를 JSON에 싣는다. 통제군 자기 자신은 애초에 후보에서 빼거나, 뺄 수 없다면 그 사실을 산출물에 명시한다.
- **기대 효과**: 「7개 중 1개 초과」의 분모가 실제로는 6개라는 것이 산출물만 보고도 드러난다. 지금은 콘솔을 본 사람만 안다 — 산출물이 정본이어야 한다는 규율(`canonical-consumers-wired`)의 같은 계열이다.
- **비용·위험**: 2시간. R18 대상 아님(계측).
- **선행 조건**: 없음.
- **우선순위 제안**: 다음 단계.

### 로드맵 반영 제안

| 항목 | 현 로드맵 위치 | 제안 위치 | 사유 |
|---|---|---|---|
| RegimeRuntime 웜스타트 (F-1) | 미등재 (W24~26 「전 경로 관통」에 암묵 포함) | **W24~26 명시 항목으로 승격** | 관통의 첫 관문이며, 없으면 W24~26 이후 전 항목이 검증 불가 |
| 판단 사슬 통과율 축 (G-1) | 미등재 | W24~26 완료 판정 기준 | 「관통했다」를 무엇으로 증명할지가 지금 정의돼 있지 않다 |
| RegimeAI 구동 Horizon 재검토 (G-2) | Ver 1.1 §3-1 「입력: feat.30m」 | 재검토 항목으로 등재 | 30m·하루 15봉은 일 단위 경계에서 하한 22봉에 도달 불가 |

---

## 4. 다음 거래일 관측 예정

| ID | 관측 대상(태그/지표) | 판정 기준 | 판정 예정일 |
|---|---|---|---|
| U-1 | `RegimeWarmStart` 태그 (g2_daily) | 1건 · 충전 봉 수 ≥ 22 | 2026-08-13 |
| U-2 | `DecisionEmitted` 중 `Regime=UNKNOWN` 비율 | **< 50%** (현재 100%) | 2026-08-13 |
| U-3 | `regime_distribution` 필드 (daily_integrity) | 존재 · 2개 이상 상태 출현 | 2026-08-13 |
| U-4 | `l1_daily` 15:36 ERROR 건수 | **≤ 4건** · `daily-axes-measured` 미출현 | 2026-08-13 |
| U-5 | `InvestorFlowPollError` · `short_cycles` | 각 0건 (오늘 1건·1건) | 2026-08-13 |
| U-6 | `postmarket_20260813.log` `SessionEnd` | 1건 존재 | 2026-08-13 |
| U-7 | 자가점검 `bar_close` 행 (R-1 이월) | `1분봉 확정: timer` 노출 | 2026-08-13 |
| U-8 | `SessionStart.git_sha` (l1/g2) | `ce51375` **이후** — stale 해소 확인 | 2026-08-13 08:20 |
| U-9 | `IrrecoverableLossBudgetExceeded` | **미출현** (08-10 41분이 창에서 이탈) | 2026-08-13 |
| U-10 | `truncation-is-visible` 등록부 | 3/3거래일 → 검증 완료 전환 | 2026-08-13 |
| U-11 | `morning-launch-actually-happens` 등록부 | 3/3거래일 → 검증 완료 전환 | 2026-08-13 |
| U-12 | `exit-code-matches-log`·`launch-window-refusal-not-counted`·`ui-restart-observability` | 08-11 잔상 소멸 → ⏳ 검증 대기로 전환 | 2026-08-13 |

### 오늘 결론 난 어제의 "볼 것" (NEXT_TODO 대조)

| ID | 물음 | 결과 | 근거 |
|---|---|---|---|
| **T-1** | 피처 퇴화 0건인가 | ✅ **통과** | `degenerate_features` 6개 Horizon 전부 `{always_nan: [], constant: []}` · 등록부 `no-degenerate-features` 4거래일 연속 **검증 완료** |
| **T-2** | `ui` 관측 공백 0건인가 | ✅ **통과** | `observation_gaps: []` (08-11 79.8분 → 0분) |
| **T-3** | `allowed_constant_values`가 리포트에 실렸는가 | ✅ **통과** | `"ev_dow_wed": 1.0` 포함 12개 수록 |
| **T-4** | 캘린더 사이드카 동결 의심이 안 뜨는가 | ✅ **통과 (이 축의 첫 채점)** | `market_findings: []` · 08-11(화)→08-12(수) 요일 벡터 정상 전환 |
| **S-1** | G2 기동 로그 두 줄 나란히 | ✅ | 38행 「국면 결선 — RegimeAI 상태 5개 … 구동 30m」 |
| **S-2** | `decisions_emitted`가 0을 벗어나는가 | ⚠️ **형식상 통과, 실질 실패** | 14건 발행되나 **전량 NO_TRADE** → P0-1 |
| **S-3** | 실시간 국면 분포가 홀드아웃과 비슷한가 | ❌ **실패** | 분포가 아니라 상수(UNKNOWN 14/14) → P0-1 |
| **S-4** | `late_bar_drops`가 0인가 (G-4 채점) | ✅ **통과** | `late_bar_drops: 0` · 등록부 `composer-bucket-completeness` 5거래일 연속 검증 완료 |
| **S-5** | 봉 1m 커버리지·거래량 대조가 만점인가 | ✅ **통과** | 비율 0.998(410/410분) · `missing_minutes: 0` · `ticks` 커버리지 100% |
| **S-6** | 등록부 재발이 2건으로 줄었는가 | ⚠️ **미달, 그러나 내역은 개선** | 최종 4건. **오늘 새로 위반한 것은 `leg-completeness-measured` 1건뿐**이고 3건(`exit-code-matches-log`·`launch-window-refusal-not-counted`·`ui-restart-observability`)은 08-11 잔상이다. 별도로 `daily-axes-measured`는 15:36 예비본에서만 울고 최종본에서 소멸 → 오탐(P1-3) |
| **R-1** | 기동 로그에 `1분봉 확정: timer` | ⚠️ **관측 불가** | 설정은 `configs/instance.yaml:66 timer` 확인, 로그 노출 없음 → F-5 |
| **R-2** | 봉 1m 커버리지가 종전 이상인가 | ✅ | 1m 410행 · 커버리지 100% · 최장 공백 0분 |
| **R-4** | `CollectorFirstTickOverdue` 0건인가 | ✅ | 태그 미출현 · `CollectorFirstTick` 08:44:58 정상 |

> **오늘 장전·장중 점검 보고서는 `logs/dailycheck/`에 없다.** 예약이 오늘 처음 등록됐고(`5c5f621`, 시각 조정 `ce51375`) 장전분은 등록 이전이었다. 따라서 위 표는 보고서 대신 **`dev_memory/NEXT_TODO.md`의 08-12 관측 예정 항목(T-*/S-*/R-*)** 을 채점한 것이다. 내일부터는 세 국면 보고서가 모두 남는다.

---

## 5. 재시동 권고

**권고: 오늘 밤 재시동하지 않는다. 내일 08:20/08:25 정시 기동으로 자연 해소한다.**

- **현재 상태**: `status_snapshot.json` → `code_version.stale: **true**` · 실행 `4825ffe` / HEAD `ce51375` · 「코드 불일치 — 화면·g2.pipeline·l1.collector·l1.composer·l1.feature_engine 전부 4825ffe」

- **손익 비교**:

  | | 재시동 없이 | 재시동으로 |
  |---|---|---|
  | 얻는 것 | 오늘 관측의 연속성 · 프로세스 상태 보존 | 커밋된 새 코드의 실제 적용 |
  | 실제 값 | 장 마감(15:35) + 배치 완료(15:47) 후라 **보존할 관측이 남아 있지 않다** | **0** — 아래 근거 참조 |

- **핵심 근거 — 오늘의 4개 커밋은 런타임 코드를 한 줄도 안 건드렸다**:
  ```
  $ git diff --stat 4825ffe..ce51375
   .claude/skills/messiah-daily-check/SKILL.md                  | 128 ++++
   .claude/skills/messiah-daily-check/references/evidence_map.md | 116 ++++
   .claude/skills/messiah-daily-check/references/phases.md       | 111 +++
   .claude/skills/messiah-daily-check/references/report_template.md | 133 ++++
   .claude/skills/messiah-daily-check/references/schedule_prompts.md | 302 +++++
   .claude/skills/messiah-daily-check/scripts/collect_evidence.py    | 765 ++++++
   pyproject.toml                                                    |  14 +
   7 files changed, 1569 insertions(+)
  ```
  `src/` **0파일** · `scripts/`(런타임) **0파일** · `configs/` **0파일**. 전부 점검 스킬과 그 린트 면제다. **즉 `stale: true`는 사실이지만 "옛 코드로 돌았다"의 실질 위험이 오늘은 0이다.** 오늘 로그가 어느 코드의 결과인지는 명확하다 — `4825ffe`이고, 그것이 런타임 최신이다.

- **단, 방치하면 축이 무뎌진다**: `code_version.stale`은 「그날 로그가 어느 코드의 결과인지 말할 수 있는가」를 재는 축인데, 매일 true면 아무도 안 읽게 된다. 내일 08:20 정시 기동이 `ce51375`(F-1~F-6 커밋 시 그 이후)를 태우면 자동 해소된다. **U-8이 그 검증점이다.**

- **F-1~F-6을 오늘 구현·커밋하는 경우에도 재시동은 불필요하다** — 내일 08:20/08:25 기동이 새 코드를 태운다. 다만 **커밋을 마치지 않은 채 내일을 맞으면 금지계명 10 위반**이므로, 구현에 착수한다면 오늘 안에 커밋까지 끝낸다.

---

## 6. dev_memory 반영

- `DECISION_LOG.md` 추가 항목: `## 2026-08-12 장후 — 데이터는 만점, 판단은 하루 종일 꺼져 있었다 ([MW0601], 2026-08-12)`
- `NEXT_TODO.md` 추가 체크박스: **18건** (F-1~F-6 구현 6건 · G-1~G-4 고도화 4건 · U-1~U-12 중 신규 관측 8건)
- 완료 처리한 기존 항목: T-1 · T-2 · T-3 · T-4 (2026-08-11 오탐 둘 fix의 채점 — 4/4 통과, 해당 fix는 **검증 완료**)
