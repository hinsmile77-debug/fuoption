# MESSIAH 일일 점검 — 2026-08-14 / 장중

- 점검 시각: 10:51 KST (정기 예약은 12:30 — 사용자 요청으로 조기 실행)
- 대상 국면: intra
- HEAD `e37d387` · 실행 중 `e37d387` (전 프로세스 동일, `stale=false`) · 미커밋 10 files(tracked 수정 3건 전부 문서)
- 증거: `logs/dailycheck/evidence_2026-08-14_intra.md`
- 직전 보고서: 2026-08-14 장전 (DECISION_LOG `[MW0601] 심볼은 계약의 이름이지 시계열의 이름이 아니다`)

## 0. 한 줄 결론

**인프라는 한 군데도 죽지 않았고(ERROR 0건·유실 0·시계 정상), 판단만 죽었다** — 월물 롤로 `A05608→A05609`가 바뀌면서 심볼로 색인된 아카이브가 비었고, 그 하나가 국면·피처·옵션체인 기준가 **세 소비처를 동시에** 무너뜨려 4/4 판단이 전부 `NO_TRADE(gate=regime)`로 접혔다.

| 국면 | 판정 | 근거 요약 |
|---|---|---|
| 장전 | **이상** | 롤 웜스타트 0봉(국면·피처) + 옵션체인 10사이클 스킵. 자가점검은 PASS ×3을 냈다 |
| 장중 | **이상(거래 위험은 없음)** | 09:00~10:30 판단 4건 전부 gate=`regime` NO_TRADE. 파이프라인 자체는 전 컴포넌트 생존 |
| 장후 | 미도래 | — |

**오늘 실주문 위험은 없다.** 게이트 ②가 정확히 설계대로 막았다. 문제는 "막혔다"가 아니라 **막힌 이유가 시장이 아니라 우리 데이터 배선이라는 것**이다.

---

## 1. 이상점 정리보고

### P0 — 오늘 거래에 직접 위험

#### 1-1. 롤 경계에서 `load_recent_bars(symbol)` 하나가 세 소비처를 동시에 무너뜨렸다 (장전 확정분의 **범위 확대**)

- **증상**: 장전 점검은 소비처 **두 곳**(국면 웜스타트·피처 롤링 윈도)을 확인했다. 장중 실측 결과 **세 번째 소비처**가 추가로 확인됐다 — 옵션체인 ATM 기준가 시드.
- **근거**:
  ```
  08:20:38 [INFO] FeatureWarmStart  {"symbol":"A05609","bars_by_horizon":{"1m":0,"3m":0,"5m":0,"10m":0,"15m":0,"30m":0}}
  08:25:31 [WARNING] RegimeWarmStartShort  충전 0봉 < 하한 22봉  {"symbol":"A05609","bars":0}
  08:21:40 [WARNING] OptionChainSkipped  기준가 없음 — 이 사이클을 건너뛴다   ×10 (08:21:40~08:43:20)
  08:44:58 [INFO] CollectorFirstTick  연결 후 첫 틱 수신  {"symbol":"A05609"}
  ```
  `logs/l1_daily_20260814.log` · `logs/g2_daily_20260814.log`
- **인과 확정**: `scripts/run_l1_daily.py:475 _seed_preopen_reference_price()`는 **2026-08-05에 바로 이 증상(장전 5사이클 스킵)을 고치려고 만든 함수**인데, 내부에서 `archiver.load_recent_bars(symbol, M1, max_bars=1)`을 호출한다. `A05609` 아카이브는 오늘 처음 생겼으므로 시드가 비었고, 폴러는 첫 틱이 온 **08:44:58 직후 스킵을 멈췄다**(최종 스킵 08:43:20). 즉 국면·피처·옵션 기준가 셋이 **같은 한 함수**에 매달려 있었다.
- **대조 실측** — 롤이 원인임을 가르는 결정적 증거:
  | 날짜 | `OptionChainSkipped` | 시각대 |
  |---|---|---|
  | 08-11 | **0건** | — |
  | 08-12 | **0건** | — |
  | 08-13 | 5건 | 15:23~15:33 (장 마감 후 꼬리, 별건) |
  | **08-14** | **10건** | **08:21~08:43 (전량 장전)** |

  장전 창은 평소 기준가가 **있다**. 오늘만 없었다. → **롤 원인 확정.**
- **기준 위반**: SYSTEM.md R10(조용한 폴백 금지 — 시드 실패가 `print` 한 줄로만 나가고 구조화 태그가 없다) · 아키텍처 불변원칙 6(자가점검이 기동 허용을 냈다)
- **영향**: 09:00~10:30 판단 4건 100% `NO_TRADE(gate=regime)`. 옵션체인 08:21~08:44 구간 **영구 결손**(옵션 스냅샷은 과거 조회 경로가 없다 — `run_l1_daily.py:481` docstring).
- **신규 여부**: 장전 P0의 **범위 확대**(소비처 2 → 3). 새 결함이 아니라 같은 결함의 세 번째 얼굴.

#### 1-2. 롤 비용은 1거래일이 아니라 **2거래일**이다 — 월요일도 종일 UNKNOWN이 확정적이다 (신규, 확정)

- **증상**: 오늘 하루로 회복되지 않는다. 월요일(08-17) 웜스타트도 하한 미달이 산술적으로 확정된다.
- **근거** — 실측 3개로 계산이 닫힌다:
  ```
  10:30:00 [INFO] RegimeClassified  {"bars_used":4,"min_bars":22}      (09:00→1, 09:30→2, 10:00→3, 10:30→4)
  data/bars/A05608/30m/2026-08-13.parquet  →  14행  (하루가 만드는 30m 봉)
  run_g2_paper_trading.py:233 docstring   →  "하루가 만드는 30m 봉은 15봉"
  ```
  오늘 종료 시 `A05609/30m` ≈ **14봉** → 월요일 웜스타트 **14 < 22** → 월요일도 종일 `UNKNOWN` → 화요일(28봉)에야 하한 통과.
- **기준 위반**: 마스터플랜 Ver 2.0 §3.1 ② (국면 축이 판단의 전제) — 그 전제가 연속 2거래일 부재
- **영향**: **`NEXT_TODO`의 W-16(2026-08-17 장전 `RegimeWarmStartShort` 0건)은 F-1 미적용 시 반드시 실패한다.** 예측을 미리 적어 둔다: F-1이 월요일 개장 전에 안 들어가면 W-16은 실패로 채점되어야 하고, 그것은 F-1의 실패가 아니라 **미적용**의 결과다. 둘을 섞지 않는다.
- **신규 여부**: 신규(장전은 "오늘"만 계산했다)

### P1 — 정확성·관측 훼손

#### 1-3. UI가 만기 월물을 보여주며 **거짓 P0 경보**를 띄웠다 — `DEFAULT_SYMBOL` 하드코딩 (신규, 확정)

- **증상**: 화면 상단은 `A05608`, 실제 전 계층은 `A05609`. 차트는 08-13을 그리고, 그 위에 붉은 경보가 떴다 —
  > 🛑 08:45이 지났는데 오늘(2026-08-14) 봉이 없다 — 봉 적재 정지 의심, **수집기(l1.collector)를 먼저 확인할 것**
- **근거**:
  ```
  src/messiah/ui/app.py:109   DEFAULT_SYMBOL = "A05608"
  src/messiah/ui/app.py:1175  symbol = st.sidebar.text_input("종목", DEFAULT_SYMBOL)
  src/messiah/ui/app.py:1013  return "alert", (f"🛑 {first_tick}이 지났는데 오늘({today}) 봉이 없다 ...")
  ```
  같은 시각 `logs/status_snapshot.json`(10:51:27)은 `l1.collector: state=OK, age_seconds=0.4, "최근 수신 0초 전"` — **수집기는 완벽히 건강했다.**
  실제 적재 실측: `data/bars/A05609/{1m,3m,5m,10m,15m,30m}/2026-08-14/{08,09,10}.parquet` 전부 존재, 1m 최종기록 **10:56:59**.
- **기준 위반**: **SYSTEM.md R4(하드코딩 금지 — 전부 설정/환경변수)**. 더 무거운 것은 `src/messiah/ui/app.py:980-984`의 자기 docstring이다:
  > *"사람은 그 박스를 아침마다 보다가 무시하는 법을 배우고, 정작 적재가 멈춘 날에도 똑같이 넘긴다"*

  2026-08-11 F-3은 **늑대 소년을 없애려고** 이 경보를 만들었다. 롤 당일 그 경보가 **다른 원인으로 다시 늑대 소년이 됐다.**
- **영향**: 운영자를 정확히 **틀린 방향**으로 유도한다("수집기를 먼저 확인할 것"). 오늘 실제로 확인해야 했던 것은 수집기가 아니라 아카이브 심볼 색인이다. 롤은 월 1회 반복되므로 매월 재현된다.
- **정본은 이미 있다**: `scripts/run_g2_paper_trading.py:195 _resolve_front_month_symbol()`이 `symbol_master.front_month_future_code()`로 **매 기동 동적 해석**한다. UI만 그 정본을 안 쓴다.
- **신규 여부**: 신규(dev_memory 전문 검색 결과 `DEFAULT_SYMBOL` 언급 0건)

#### 1-4. `intel.futures` 배지는 거래일의 **99.4%를 STALE로 보낸다** — 구조적 오탐 (신규, 확정)

- **증상**: 화면 상단 `intel.futures ● STALE`. 그런데 발행은 정상이다.
- **근거**:
  ```
  src/messiah/ui/app.py:126   "FuturesView": 10.0          ← STALE 임계 10초
  g2_daily_20260814.log       DecisionEmitted ×4 — 09:00:02, 09:30:00, 10:00:00, 10:30:00   ← 30분 주기
  g2 stdout                   live 번들 결선: ['30m'] (feature_set=v2026.08-ev)
  ```
  live 번들이 `30m` **한 종뿐**이라 `intel.futures`는 30분 격자로만 나간다. 임계 10초 / 주기 1800초 → **1800초 중 10초만 초록**. 점검 시각 10:51은 마지막 발행 10:30에서 21분 뒤라 당연히 STALE.
- **기준 위반**: SYSTEM.md R6(태그 1개 = 심각도 1개) 정신 위배 — 같은 앰버가 "죽었다"와 "주기가 길다"를 겸한다. `app.py:252` 자기 docstring: *"STALE은 그 프로세스가 죽었거나 멈췄다는 뜻"* — 오늘 그 뜻이 아니었다.
- **영향**: 1-3과 같은 종류의 피해. `CircuitBreakerStatus`는 이 함정을 이미 알고 40초(주기 30초 대비)로 잡아 뒀는데(`app.py:129-132`), `FuturesView`만 발행 주기와 무관한 상수로 남았다.
- **신규 여부**: 신규

#### 1-5. `n_experts=0`의 **사유를 로그가 구분하지 못한다** — W-2가 3거래일째 미확정인 원인 (신규, 원인 규명)

- **증상**: 화면 `통합점수 S=+0.000 · 분산=0.000 · n=0 · 불확실성 1.00`. `NEXT_TODO` W-2가 *"`n_experts=0` 가설 강화되었으나 확정 아님"* 으로 3거래일째 멈춰 있다.
- **근거** — 코드를 읽으면 `n_experts=0`으로 가는 길이 **네 갈래**다:
  ```
  src/messiah/strategy/futures/aggregator.py:172-185
    weight = weight_table[horizon] * meta_h * (1.0 - u_h) * f_h
    if total_weight <= 0: return FuturesView(..., n_experts=0, uncertainty=1.0, ...)
  ```
  ① `views`가 비었다 ② `meta_h=0`(Meta-Labeler 거부) ③ `u_h=1`(`ens_std ≥ uncertainty_scale`) ④ `f_h=0`(신선도 만료).
  **`REGIME_WEIGHTS[UNKNOWN]`은 비어 있지 않다** — 전 Horizon 0.5(`aggregator.py:113-120`). 즉 "국면이 UNKNOWN이라 가중치가 0"이라는 손쉬운 설명은 **틀렸다.**
  네 갈래 중 무엇이었는지 **로그가 한 줄도 남기지 않는다.**
- **기준 위반**: 점검 체크리스트 D *"건수 0은 두 가지다 — 진짜 없었거나, 계측이 없거나"*. 여기선 계측이 없다.
- **영향**: 판단 사슬의 심장부가 관측 불능. 3거래일간 사람이 가설만 강화했고 확정은 못 했다. 오늘 30m의 `nan_ratio`가 **84.7%로 종일 미회복**(1-6 표)이라 ③이 유력하지만, **오늘도 확정 못 한다.**
- **신규 여부**: W-2(기존)의 **원인 규명이 신규**

#### 1-6. "미커밋 179건"은 실측과 다르다 — 4거래일째 재측정 없이 이월된 숫자가 승격 차단 근거로 쓰였다 (신규, 확정)

- **증상**: `NEXT_TODO`/`DECISION_LOG`가 08-12 174건 → 08-13/14 179건으로 *"미커밋"* 을 적고, **"paper 승격 차단 조건으로 격상 제안"** 의 근거로 쓴다. 오늘 증거 수집기는 같은 이름으로 **6건**을 냈다.
- **근거** — 세 축 전부 실측:
  ```
  git diff --stat 4825ffe -- src/     →   9 files changed, 546 insertions(+), 20 deletions(-)
  git diff --stat HEAD -- src/        →   (변경 없음)          ← 미커밋 src/ = 0건
  git status --porcelain -uall        →   10 files (tracked 수정 3건은 전부 .md 문서)
  git rev-list --count 4825ffe..HEAD  →   10 커밋
  ```
  `NEXT_TODO`가 명시한 측정식 `git diff --stat 4825ffe -- src/`의 답은 **9**다. **179가 아니다.** 그리고 4825ffe(2026-08-11) 이후 `src/` 변경은 10개 커밋에 **전부 담겨 있다** — 미커밋이 아니다.
- **기준 위반**: 사용자 원칙 *"알려진 한계는 측정 전까지 버그"* 의 정확한 반대면 — **측정했다고 적힌 숫자가 실은 재측정되지 않았다.**
- **영향**: 존재하지 않는 기술부채를 근거로 **paper 승격을 막자는 제안이 4거래일째 살아 있다.** 실재하는 차단 사유(예: 1-1 롤 결함)와 섞여 우선순위를 흐린다.
- **신규 여부**: 신규

#### 1-7. 옵션체인 폴링은 **성공을 한 줄도 남기지 않는다** — "0건"이 정상인지 정지인지 로그로 판별 불가 (신규, 확정)

- **증상**: `NEXT_TODO` W-15의 판정 기준은 *"09:00 이후 `OptionChainSkipped` 건수 — 0건이면 롤 확정"* 이다. 실제 0건이었다. 그러나 **그 0건은 판정 근거가 될 수 없다.**
- **근거**: `l1_daily` 당일 태그 전수 조사 —
  ```
  FeaturePublish 227 · OptionChainSkipped 10 · SessionStart 3 · CrashForensicsArmed 3
  LaunchWindowRefused 2 · InvestorFlowPollRetried 1 · ClockSkewMeasured 1
  FeatureWarmStart 1 · CollectorFirstTick 1                      ← 이것이 전부다
  ```
  `src/messiah/data/option_chain_poller.py:282 _poll_one()`의 성공 경로는 **버스 발행만 하고 로그가 없다.** DEBUG조차 없다(FeaturePublish는 DEBUG인데 227건 기록되므로 DEBUG는 켜져 있다). 즉 "폴러가 잘 돌고 있다"와 "폴러 태스크가 죽었다"가 **로그상 완전히 동일**하다.
- **오늘은 우회 확인이 가능했다** — 아카이브 파일로:
  ```
  data/option_chain/regular/2026-08-14.parquet      71,799B  최종기록 10:55:44
  data/option_chain/weekly_thu/2026-08-14.parquet   31,732B  최종기록 10:54:02
  data/option_chain/weekly_mon/2026-08-14.parquet   45,754B  최종기록 10:52:24
  ```
  → **3계열 전부 정상 폴링 중. W-15 판정 성립(롤 원인 확정).** 다만 이 확인은 로그가 아니라 파일시스템을 뒤져서 얻었다.
- **기준 위반**: 점검 체크리스트 D *"건수 0은 두 가지다"*. R6(관측 가능성)
- **영향**: 폴러 침묵을 사고로 인지하기까지 **장후 커버리지 축까지 기다려야 한다.**
- **신규 여부**: 신규

### P2 — 운영 부담·기술부채

#### 1-8. 장전 G-3은 **불필요한 게이트**였다 — 조사가 제안을 지웠다 (신규, 확정)

- **증상**: 장전 고도화 G-3이 *"국면이 UNKNOWN이어도 regime 게이트가 열려 있다"* 를 전제로 `regime_axis_unavailable` NO_TRADE 게이트 신설을 제안하고, **R18에 따라 20거래일 섀도 계측**까지 계획했다.
- **근거** — 그 전제가 틀렸다:
  ```
  src/messiah/strategy/decision/meta_decision.py:56
      _EVENT_LIKE_REGIMES = frozenset({Regime.EVENT, Regime.UNKNOWN})
  src/messiah/strategy/decision/meta_decision.py:92-97
      if view.regime in _EVENT_LIKE_REGIMES:
          return self._no_trade(view, f"② Regime={...} — 이벤트/미판정 국면", gate=GATE_REGIME)
  ```
  UNKNOWN은 **이미 무조건 NO_TRADE**다. 오늘 실측이 그대로 보여준다 — `DecisionEmitted` 4/4가 `gate="regime"`.
  장전이 근거로 삼은 어제 퍼널 `{"regime":1,"score":13}`은 *"게이트가 열려 있다"* 가 아니라 **"어제는 국면이 대부분 UNKNOWN이 아니어서 13건이 ②를 통과해 ④에서 접혔다"** 는 뜻이다. 퍼널을 거꾸로 읽었다.
- **영향**: 착수했다면 **이미 존재하는 동작을 다시 구현하고 20거래일을 섀도로 태울 뻔했다.** → **G-3 폐기.**
- **부수 발견**: G-3이 지목한 파일 경로 `src/messiah/strategy/meta/decision.py`는 **존재하지 않는다**(정본 `src/messiah/strategy/decision/meta_decision.py`). 계획서의 경로가 검증되지 않은 채 쓰였다.
- **신규 여부**: 신규(장전 계획의 정정)

#### 1-9. 08-13 장 마감 후 `OptionChainSkipped` 5건 (별건, 기록만)

- **증상**: `logs/l1_daily_20260813.log` 15:23:20~15:33:20에 5건. 오늘 것(장전)과 성격이 다르다.
- **영향**: 마감 후 구간이라 실해는 없다. 폴러 정지 시각과 선물 틱 종료 시각의 불일치로 추정.
- **판정**: **P2 기록만.** 오늘 fix 대상 아님.

### 확인 필요 (확정 아님)

- **`n_experts=0`의 실제 갈래** (1-5) — 네 갈래 중 무엇인지. **F-5 적용 후 다음 거래일 1회 관측이면 확정된다.**
- **W-9** (08-13 분봉 420 vs 395) — 장전에서 재이월된 항목. 개장 중 KIS 분봉 재조회는 라이브 수집과 유량을 다툰다(`run_backfill.py` docstring). **장후 이월 유지** — 사유를 다시 남기는 이유는 사유 없이 미루면 영구 미결이 되기 때문이다.
- **W-10** (`CollectorReconnectNoTick` 0건) — 오늘 재연결이 **0회**라 판정 자체가 성립하지 않는다(`CollectorFirstTick` 1건뿐). 08-18까지 연장하되 그때는 replay로 강제 채점.

### 정상 확인 — 오탐 방지를 위해 명시한다

- **자가점검 PASS ×3** (l1·g2 각 3회 기동, 비-OK 0행). `SessionStart` 3회 중 2회는 `LaunchWindowRefused`(00:51:58·07:18:21)로 **기동이 아니다** — `9a4d4ea`가 처리한 형태. 정시 기동 08:20:33·08:25:30 확인.
- **`SessionEnd` 없음(l1·g2)** — 장중이라 두 프로세스가 살아 있다. **구조적 오탐**(수집기 §9가 국면을 안 본다).
- **`g2` 30분 로그 공백 4건** — `RegimeRuntime` 구동 Horizon이 30m이므로 **설계대로다.** 공백이 아니라 주기.
- **코드 정합**: `code_version.stale=false`, 3회 기동 전부 `sha=e37d387`, HEAD와 동일.
- **ERROR/CRITICAL 0건.** 합성봉 92개 **거래량 항등식 일치(유실 0)**. `irrecoverable_loss.clean=true`. CB `phase=normal`, `gateway_halted=false`.
- **금지계명 3·4 준수** — 장중 학습·배포·재기동 흔적 없음.
- **★ `dbe37df` 라이브 검증 성립** — 09:33:02 `InvestorFlowPollRetried` *"1회 재시도로 복구: 500 Internal Server Error"*, `attempts=2`. 5xx 백오프가 실전에서 실제로 작동했다. **완료 처리.**
- **V-4 유지** — `weekly_thu` 계열 오늘도 정상 수집(`data/option_chain/weekly_thu/2026-08-14.parquet`, 10:54 기록). `OptionChainCalendarViolation` 0건.

---

## 2. Fix 작업 구현계획

> **장중이므로 본 계획은 수립만 하고 적용하지 않는다** — SYSTEM.md R11 · 금지계명 3·4.
> 적용 시점: **오늘 15:35 이후.** 각 커밋 전 `pytest`(해당 범위) + replay — 금지계명 2.

### F-1. 롤 경계를 넘는 `load_recent_bars` — P0 · 대응 이상점 1-1, 1-2 (장전 F-1 **유지 + 범위 확대**)

- **원인 가설**: 확정됨. `ParquetArchiver.load_recent_bars()`가 심볼 단일 색인이라 롤 당일 0봉을 돌려준다. 소비처 3곳이 같은 함수에 매달려 있다.
- **변경 파일**:
  - `src/messiah/data/archiver.py` — `ParquetArchiver.load_recent_bars()`에 `predecessors: Sequence[str] | None` 인자 추가. 요청 심볼이 부족하면 직전 월물에서 이어 읽는다. **조용히 잇지 않는다(R10)** — 반환과 함께 `bars_by_source={"A05609":4,"A05608":196}`를 로그로 남긴다.
  - `src/messiah/data/symbol_master.py` — `preceding_front_months(symbol, n)` 신설. 마스터파일 기준 직전 월물 코드 산출(하드코딩 금지 R4).
  - `scripts/run_l1_daily.py` — `_load_warmup_artifacts()`와 `_seed_preopen_reference_price()` 두 호출부에 `predecessors` 전달. **`_seed_preopen_reference_price`는 장전 계획에 없었다 — 오늘 1-1로 추가된 세 번째 소비처다.**
  - `scripts/run_g2_paper_trading.py` — `_warm_start_regime()` 동일 처리.
  - `src/messiah/ops/canonical_consumers.py` — 소비처 3곳을 등록해 네 번째가 생기면 테스트가 잡게 한다.
- **회귀 위험**: 롤 경계 가격 점프가 그대로 이어져 피처(수익률·변동성)에 인위적 점프가 생긴다. **이번 단계는 비율 조정 없이 원본 그대로 잇고**, 조정은 G-1(연속계약 아카이브)에서 다룬다. 그래서 `bars_by_source` 로그가 필수다 — 나중에 어느 구간이 이어진 것인지 사후 판별 가능해야 한다.
- **검증 방법**:
  - `pytest tests/data/ tests/test_regime_and_bundle_paths.py tests/ops/test_canonical_consumers.py`
  - 신규 테스트: 롤 경계 replay — `A05608` 200봉 + `A05609` 0봉 아카이브에서 `load_recent_bars` 200봉 반환 및 `bars_by_source` 정확성
  - **라이브 관측(2026-08-17 장전)**: `FeatureWarmStart.bars_by_horizon` 전 Horizon ≥ 22 · `bars_by_source`에 `A05608` 등장 · `RegimeWarmStartShort` **0건** · **`OptionChainSkipped` 0건** ← 마지막 축이 오늘 추가분
- **적용 시점**: **오늘 15:35 이후 최우선.** 1-2에 따라 **월요일 개장 전에 안 들어가면 월요일도 통째로 UNKNOWN이다.**
- **결정 필요 사항**: 없음(장전에 이미 결정).

### F-2. 롤을 자가점검이 먼저 외친다 — P0 · 대응 이상점 1-1 (장전 F-2 유지)

- **원인 가설**: 오늘 자가점검은 **PASS ×3**을 냈다. 롤이라는 축이 점검 항목에 없다.
- **변경 파일**: `scripts/self_check.py` — `rollover` 항목 신설. 마스터파일 근월물과 직전 거래일 아카이브 심볼을 대조해 다르면 `[WARN]` + 가용 봉 수 표기.
- **회귀 위험**: 낮음(관측 전용, 기동 차단 아님).
- **검증 방법**: `pytest tests/` self_check 범위 / **라이브(08-17)**: 비-롤일 `[OK]`, 롤일 `[WARN]`. **롤일 채점은 2026-09-14.**
- **적용 시점**: 장후. F-1과 같은 커밋.
- **결정 필요 사항**: 없음.

### F-3. UI 심볼 하드코딩 제거 — P1 · 대응 이상점 1-3 (신규)

- **원인 가설**: 확정됨. `DEFAULT_SYMBOL = "A05608"` 상수(R4 위반).
- **변경 파일**:
  - `src/messiah/ui/app.py:109` — `DEFAULT_SYMBOL` 상수 **삭제**. `symbol_master.front_month_future_code(PRODUCT_TYPE_MINI_FUTURES)`로 동적 해석. **`scripts/run_g2_paper_trading.py:195 _resolve_front_month_symbol()`과 같은 경로를 쓴다** — 두 벌을 만들면 이 저장소가 이미 다섯 번 겪은 "정본 아닌 소비자"가 여섯 번째로 생긴다.
  - 해석 실패 시(마스터파일 부재·오프라인) 화면 전체를 죽이지 않는다 — 사이드바에 `⚠ 근월물 자동 해석 실패 — 수동 입력` 배지를 띄우고 텍스트 입력은 살린다(`EventCalendar`가 예외를 삼키는 것과 같은 판단, `app.py:990`).
  - `src/messiah/ui/app.py:110` — `DEFAULT_TICK_SIZE` 주석의 `A05608` 참조도 함께 정리.
  - `src/messiah/ui/app.py:1013` — 경보 문구에 심볼 명시: `🛑 {symbol}의 오늘({today}) 봉이 없다 — ①수집기 ②심볼이 근월물과 일치하는지 순으로 확인할 것`. **원인 후보를 하나로 단정하지 않는다.**
- **회귀 위험**: UI가 기동 시 마스터파일을 읽는다 → 기동 지연·오프라인 실패. 위 폴백으로 흡수. 사용자가 사이드바에서 수동 입력하던 흐름은 그대로 유지된다.
- **검증 방법**: `pytest tests/` UI 범위 / 신규 테스트 2건 — ① 마스터파일 정상 시 근월물 자동 선택 ② 마스터파일 부재 시 배지 표시 + 화면 생존. **라이브(08-17)**: 화면 상단이 `A05609`, 붉은 경보 없음.
- **적용 시점**: 장후.
- **결정 필요 사항**: **없음 — 권고안 그대로 진행.** (사이드바 수동 입력은 유지하되 기본값만 동적으로.)

### F-4. `intel.futures` STALE 임계를 발행 주기에서 유도 — P1 · 대응 이상점 1-4 (신규)

- **원인 가설**: 확정됨. 임계 10초가 실제 발행 주기(1800초)와 무관한 상수.
- **변경 파일**:
  - `src/messiah/ui/app.py:125-139 _STALE_AFTER` — `FuturesView`/`RegimeState` 임계를 **구동 Horizon에서 유도**한다. `CircuitBreakerStatus`가 이미 쓰는 논리(주기 30초 → 임계 40초)를 일반화: `임계 = 구동 주기 × 1.5 + 여유`. 구동 Horizon은 `intel.futures` 메시지 자체 또는 `status_snapshot`에서 읽는다 — **UI가 추측하지 않는다.**
  - 배지 캡션에 근거를 적는다: `LIVE (30m 주기 · 마지막 09:30)`. 숫자만 보고 사람이 주기를 역산하게 두지 않는다.
- **회귀 위험**: 임계가 느슨해져 **진짜 정지를 늦게 잡는다.** 완화책 — 배지에 마지막 수신 시각을 항상 병기하고, `구동 주기 × 2`를 넘으면 `_HEALTH_COMPONENTS`처럼 **"죽음"** 문구로 승격한다(`app.py:272` 기존 논리 재사용).
- **검증 방법**: `pytest tests/` UI 배지 범위 / 신규 테스트 — 30m 구동 시 21분 경과가 LIVE, 65분 경과가 죽음. **라이브(08-17)**: 09:00~15:35 `intel.futures` 배지 앰버 지속시간이 발행 주기 대비 합리적인가 육안 확인.
- **적용 시점**: 장후. **F-3과 같은 커밋**(둘 다 "화면이 거짓말한다" 축).
- **결정 필요 사항**: 없음.

### F-5. `n_experts=0`의 사유를 세게 한다 — P1 · 대응 이상점 1-5 (신규)

- **원인 가설**: 미확정(그것이 문제다). 네 갈래 중 무엇인지 계측이 없다.
- **변경 파일**:
  - `src/messiah/strategy/futures/aggregator.py:185` — `total_weight <= 0` 분기에서 `FuturesView` 반환 **전에** 구조화 로그:
    ```python
    mlog.log("AggregatorNoContribution",
             "기여 의견 0 — 이 사이클의 FuturesView는 n=0으로 나간다",
             regime=regime_state.regime.value,
             views_received=len(views),
             blocked_by_meta=[h.value for h in ... if meta_h == 0],
             blocked_by_uncertainty=[h.value for h in ... if u_h >= 1.0],
             blocked_by_freshness=[h.value for h in ... if f_h == 0.0])
    ```
    **WARNING이 아니라 INFO** — 하루 15건 이하(30m 주기)라 소음이 아니고, 오늘처럼 국면이 죽은 날엔 정상 동작이기도 하다. 승격 여부는 20거래일 분포를 본 뒤 정한다.
  - `src/messiah/ops/integrity_report.py` — 장후 리포트에 `no_contribution_reasons` 집계 추가.
- **회귀 위험**: 없음(관측 전용, 판단 경로 불변).
- **검증 방법**: `pytest tests/strategy/futures/test_aggregator.py` + 네 갈래 각각의 신규 단위 테스트 4건. **라이브(08-17)**: `AggregatorNoContribution` 1건 이상 관측 시 **W-2 즉시 확정.**
- **적용 시점**: 장후.
- **결정 필요 사항**: 없음.

### F-6. 옵션체인 폴링 성공에 heartbeat를 남긴다 — P1 · 대응 이상점 1-7 (신규)

- **원인 가설**: 확정됨. 성공 경로에 로그가 없어 "0건"이 양의적이다.
- **변경 파일**:
  - `src/messiah/data/option_chain_poller.py:282 _poll_one()` 이후 — `poll_once()` 말미에 사이클 요약 1건(다리 수 기준, 다리마다가 아니라):
    ```python
    mlog.log("OptionChainPolled", f"{len(window)}다리 발행",
             underlying=..., series=..., legs=len(window), spot=spot)
    ```
    **DEBUG 레벨** — `OptionChainPollEmpty`가 2026-08-07에 WARNING이라 22번 울고 강등된 전례를 따른다(`option_chain_poller.py:260-262`). 사이클당 1건이면 3계열 × 종일 ≈ 550건으로 `FeaturePublish`(227건)와 같은 자릿수다.
  - `scripts/collect_evidence.py` — §9 자동 적신호에 *"장중 `OptionChainPolled` 0건"* 축 추가. 오늘 사람이 파일시스템을 뒤져서 한 확인을 도구가 하게 한다.
- **회귀 위험**: 로그 볼륨 증가(일 55.7KB → 약 90KB 추정). 허용 범위.
- **검증 방법**: `pytest tests/data/test_option_chain_poller.py` + 성공 시 태그 발행 테스트. **라이브(08-17)**: 09:00 이후 `OptionChainPolled` 3계열 전부 등장.
- **적용 시점**: 장후.
- **결정 필요 사항**: 없음.

### F-7. 점검 도구가 "미커밋"을 매번 실측하게 한다 — P2 · 대응 이상점 1-6 (신규)

- **원인 가설**: 숫자가 보고서 간 복사로 이월됐고, 수집기의 "미커밋 N건"(porcelain 엔트리)과 보고서의 "미커밋 N건"(baseline diff)이 **같은 이름 다른 정의**다.
- **변경 파일**:
  - `.claude/skills/messiah-daily-check/scripts/collect_evidence.py` §1 — 두 축을 **이름을 갈라** 함께 출력:
    - `작업트리 미커밋: {git status --porcelain -uall 파일 수} (tracked 수정 N / untracked M)`
    - `기준선 대비 src/ 변경: {git diff --stat <baseline> -- src/} · 기준선 {sha} {날짜}`
    기준선 sha는 `configs/` 또는 스킬 설정에서 읽는다(하드코딩 금지 R4).
  - `.claude/skills/messiah-daily-check/references/report_template.md` — 머리말의 `미커밋 {n}건`을 두 축으로 분리.
  - `dev_memory/NEXT_TODO.md` — 기존 *"미커밋 179건"* 항목을 **실측값으로 정정**하고, 그것을 근거로 한 *"paper 승격 차단 조건 격상 제안"* 을 **철회**한다.
- **회귀 위험**: 없음.
- **검증 방법**: 수집기 재실행 후 두 축이 위 실측(10 / 9)과 일치. **08-17 장전 보고서에 정정된 숫자가 반영됐는가.**
- **적용 시점**: 장후.
- **결정 필요 사항**: 기준선 sha를 `4825ffe`로 계속 쓸지, "마지막 paper 승격 심사 통과 커밋"으로 재정의할지. **권고: 후자** — 그래야 숫자가 승격 판단에 의미를 갖는다. 사용자 확인 요청.

### F-8. 장전 G-3 폐기 + 경로 정정 — P2 · 대응 이상점 1-8 (신규)

- **변경 파일**: 코드 변경 **없음.** `dev_memory/NEXT_TODO.md`의 G-3 항목을 폐기 처리하고 사유(오늘 실측 4/4 `gate="regime"`)와 잘못된 경로(`strategy/meta/decision.py` → `strategy/decision/meta_decision.py`)를 함께 기록한다.
- **적용 시점**: 오늘 dev_memory 갱신에 즉시 반영(문서 작성은 R11 대상이 아니다).
- **결정 필요 사항**: 없음.

### 적용 순서와 커밋 계획 (오늘 15:35 이후)

| # | 포함 | 메시지 초안 |
|---|---|---|
| ① | F-1 + F-2 | `[MW0601] 심볼은 계약의 이름이지 시계열의 이름이 아니다 — 롤 경계 웜스타트` |
| ② | F-3 + F-4 | `[MW0601] 화면이 어제 계약을 오늘이라 불렀다 — 근월물 동적 해석 + 배지 임계 유도` |
| ③ | F-5 + F-6 | `[MW0601] 0은 없었다는 뜻도 안 셌다는 뜻도 된다 — 기여 0·폴링 성공 계측` |
| ④ | F-7 | `[MW0601] 이월된 숫자는 측정이 아니다 — 미커밋 두 축 분리` |
| ⑤ | 08-13 세운 F-1~F-5 (재연결 첫 틱 시한 등) | 내용 그대로 유효, 순서만 뒤로 |

**①이 월요일 개장 전에 반드시 들어가야 한다** (1-2). ②③④는 다음 주로 밀려도 손실이 관측 품질에 그친다.

**장전 계획 대비 변경**: F-5(`OptionChainSkipped.reason` 열거형)는 **W-15 판정 완료로 폐기** — 롤 원인이 확정됐고 F-1이 흡수한다. 장전의 조건부 판단("W-15가 가른다")이 옳았다.

---

## 3. 고도화 방안

### G-1. 롤 D-1 사전 백필 — 자연 회복은 긴 Horizon에서 **원리적으로 불가능**하다 (신규)

- **관측 근거** — 오늘 `nan_ratio` 회복 곡선 실측(08:45→10:54, 227건 `FeaturePublish`):

  | Horizon | 발행 수 | 08:45 | 10:5x | 회복폭 |
  |---|---|---|---|---|
  | 1m | 129 | 84.7% | **2.2%** | −82.5%p |
  | 3m | 43 | 84.7% | 31.4% | −53.3%p |
  | 5m | 25 | 84.7% | 32.8% | −51.9%p |
  | 10m | 13 | 84.7% | 59.9% | −24.8%p |
  | 15m | 8 | 84.7% | 61.3% | −23.5%p |
  | **30m** | **4** | **84.7%** | **84.7%** | **0.0%p** |

  회복은 **경과 시간이 아니라 누적 봉 수**의 함수다. 그리고 **판단을 구동하는 Horizon이 하필 30m**(유일한 live 번들)이라 회복이 정확히 0이다. F-1이 읽는 쪽을 고쳐도 **롤 당일 아침 첫 사이클까지는 여전히 빈다.**
- **제안 내용**: `scripts/run_backfill.py`를 장후 배치에 조건부로 결선한다 — `EventCalendar`가 **다음 거래일이 롤 경계**라고 판정하면 그날 15:45 배치에서 신규 근월물의 1m을 소급 수집하고 `run_recompose.py`로 상위 Horizon을 합성해 아카이브에 심는다. 다음 롤은 **2026-09-14**로 달력에 이미 박혀 있다.
- **기대 효과**: 롤 당일 `FeatureWarmStart.bars_by_horizon` 전 Horizon ≥ 200, `RegimeWarmStartShort` 0건, 장전 `OptionChainSkipped` 0건. **F-1이 "이어 읽기"라면 이것은 "미리 채우기"** — 둘은 대체가 아니라 보완이다(F-1은 백필이 실패한 날의 안전망).
- **비용·위험**: 장후 배치 시간 증가(1m 소급 ≈ 400봉 × 월물 1종). API 유량은 장후라 라이브 수집과 다투지 않는다. **소급 한계 2025-12-12**(dev_memory 기록)이므로 신규 월물 상장 이후 구간만 가능 — 그래도 F-1이 잇는 원월물 구간과 합쳐 200봉을 채운다.
- **선행 조건**: F-1(어느 구간이 백필이고 어느 구간이 이월인지 `bars_by_source`가 구분해야 한다).
- **우선순위 제안**: **다음 단계 · 기한 2026-09-14**(다음 롤).

### G-2. 롤 경계 8곳을 먼저 조사한다 — 학습 자산의 진짜 크기 (장전 G-1 **유지**)

- **관측 근거**: `NEXT_TODO`가 학습 자산을 *"근월물 8심볼 167거래일"* 로 적는다. 오늘 롤 하나가 전 계층을 0봉으로 만든 것을 보면, 그 167거래일은 **8번 끊긴 데이터**이고 롤 경계 8곳의 처리를 아무도 확인한 적이 없다.
- **제안 내용**: `data/bars/A0560{1..9}/` 경계 8곳에서 종가-시가 점프와 봉 연속성을 실측한다. **선행 조사가 먼저다** — 이미 이어져 있으면 G-2는 소비처 통일로 축소되고, 끊겨 있으면 연속계약 아카이브(`data/bars/KOSPI200F_C1/`, 비율 조정·원본 병존)가 필요하다.
- **기대 효과**: 학습 데이터 유효 길이의 정직한 수치 확보. 지금은 167거래일이 사실인지 아무도 모른다.
- **비용·위험**: 조사 자체는 반나절. 연속계약 구축으로 이어지면 별건.
- **선행 조건**: 없음(조사는 즉시 가능).
- **우선순위 제안**: **이번 주** — 조사만. 구축 여부는 조사 결과가 정한다.

### G-3. `verdict` 한 줄에 판단 가용성을 싣는다 (장전 G-2 **유지 + 오늘 근거 보강**)

- **관측 근거**: 오늘 10:51:27 `status_snapshot.json`은 컴포넌트 4종 중 3종을 `level:"OK"`로, 자가점검은 `PASS`를, UI 상단은 초록·앰버 혼재를 냈다. **세 화면이 각자 정상을 말하는 동안 시스템은 종일 판단 불능이었다.** 오늘 사람이 그것을 알아내는 데 필요했던 것은 로그 3개 + 아카이브 디렉터리 + 소스 4개 대조였다.
- **제안 내용**: `src/messiah/ops/integrity_report.py`의 `verdict`에 사유를 추가한다. **별도 `readiness` 키를 신설하지 않는다** — 화면이 또 나뉘면 L18의 반대편 실수다. 오늘 있어야 했던 값:
  ```json
  "verdict": {"ok": false,
              "reasons": ["warm_start_short", "feature_nan_ratio_exceeded", "regime_unknown"],
              "since_kst": "08:20:38"}
  ```
- **기대 효과**: 한 줄로 "오늘 판단이 가능한가"가 나온다. 오늘 사람이 쓴 대조 시간을 0으로 만든다.
- **비용·위험**: 낮음(집계 전용). R18 무관(게이트가 아니라 관측).
- **선행 조건**: F-5(`AggregatorNoContribution`)가 있으면 `reasons`에 한 축이 더 붙는다. 없어도 착수 가능.
- **우선순위 제안**: **이번 주.**

### G-4. 신선도 임계를 발행 주기에서 유도하는 것을 **전 배지로 일반화** (신규)

- **관측 근거**: 오늘 `CircuitBreakerStatus`만 이 문제를 알고 40초로 잡아 뒀고(`app.py:129-132`), `FuturesView`는 10초 상수로 남아 종일 오탐이었다(1-4). **같은 함정을 한 곳에서만 피한 것은 설계가 아니라 우연이다.**
- **제안 내용**: F-4를 특수해가 아니라 일반해로 만든다 — `_STALE_AFTER`를 상수 딕셔너리에서 **"발행자가 자기 주기를 선언하고 UI가 그것을 읽는" 구조**로 바꾼다. 각 메시지 타입이 `expected_interval_seconds`를 실어 보내고, UI는 `임계 = 주기 × 1.5`, `죽음 = 주기 × 3`으로 계산한다. `tests/test_false_positive_axes.py`에 "상수 임계가 새로 추가되면 실패하는" 테스트를 건다.
- **기대 효과**: 발행 주기가 바뀔 때(live 번들이 30m 외에 늘어날 때) 배지가 자동으로 따라온다. 오늘 같은 오탐이 **구조적으로 재발 불가**.
- **비용·위험**: 메시지 스키마 변경 → `schema version=1 types=21` 증가. 중간 크기 작업.
- **선행 조건**: **F-4** — 먼저 한 곳에서 효과를 확인하고 일반화한다.
- **우선순위 제안**: **다음 단계** — F-4 라이브 관측(08-17) 이후 재평가.

### 로드맵 반영 제안

| 항목 | 현 로드맵 위치 | 제안 위치 | 사유 |
|---|---|---|---|
| G-1 롤 D-1 사전 백필 | 미등재 | 2026-09-14 이전 필착 | 오늘 30m 자연회복 0.0%p 실측. 다음 롤 날짜가 확정돼 있다 |
| 장전 G-3 (`regime_axis_unavailable`) | 고도화 대기 | **폐기** | 이미 구현돼 있다(1-8). R18 섀도 20거래일 절약 |
| "미커밋 179건" 승격 차단 제안 | paper 승격 차단 조건 | **철회** | 실측 9건(baseline diff)·0건(미커밋 src/). 근거 부재(1-6) |
| 옵션 실행 경로 결선 | 알려진 갭 | 유지 | 오늘 새 근거 없음. `OptionsAIService` 미결선은 기존 갭 그대로 |

---

## 4. 다음 거래일 관측 예정

| ID | 관측 대상(태그/지표) | 판정 기준 | 판정 예정일 |
|---|---|---|---|
| **W-15** | 09:00 이후 `OptionChainSkipped` | **★ 오늘 판정 완료 — 0건 + 3계열 아카이브 정상(10:52~10:55). 롤 원인 확정, F-5(reason 필드) 폐기** | ✅ 2026-08-14 |
| **V-11** | `RegimeClassified.regime` 종일 분포 | **UNKNOWN 100% 예상**(10:30까지 4/4 성립 중). 아니면 웜스타트 이해가 틀린 것 | 2026-08-14 장후 |
| **V-12** | `daily_integrity_20260814.json` `nan_ratio_by_horizon` | 30m median ≈ 0.847(회복 0), 1m median < 0.10. **§3 G-1 표와 대조** | 2026-08-14 장후 |
| **V-13** | `decision_funnel` | `{"regime": 13~15}` 단독 예상. `score` 이하가 0이면 ③④⑤ 전 계층 미검증 | 2026-08-14 장후 |
| **W-18** | `daily_integrity`에 `warm_start_bars_by_horizon` | (F-3 적용 후) 존재 · `미측정` 아님 | 2026-08-14 장후 |
| **W-9** | 08-13 분봉 재조회 분 수 | 420 → 우리 수집 결함 / 395 → 브로커 공급 문제. **장전에서 재이월, 오늘 장후 필착** | 2026-08-14 장후 |
| **W-16 ★** | `FeatureWarmStart.bars_by_horizon` · `bars_by_source` · `RegimeWarmStartShort` · **`OptionChainSkipped`** | (F-1 적용 후) 전 Horizon ≥ 22 · `A05608` 등장 · 0건 · **0건**. **F-1 미적용이면 1-2에 따라 실패가 확정 — 실패를 F-1 탓으로 채점하지 않는다** | 2026-08-17 장전 |
| **W-17** | 자가점검 `rollover` 줄 | (F-2 적용 후) 비-롤일 `[OK]`. **롤일 채점은 2026-09-14** | 2026-08-17 장전 |
| **W-19** | UI 상단 심볼 · 붉은 경보 | (F-3 적용 후) `A05609` 표시 · 경보 없음 | 2026-08-17 장전 |
| **W-20** | `intel.futures` 배지 | (F-4 적용 후) 30분 주기에서 LIVE 유지, 65분 침묵 시 "죽음" | 2026-08-17 장중 |
| **W-21 ★** | `AggregatorNoContribution` | (F-5 적용 후) 1건 이상 관측 시 **W-2 즉시 확정**(네 갈래 중 무엇인지) | 2026-08-17 장중 |
| **W-22** | `OptionChainPolled` | (F-6 적용 후) 09:00 이후 3계열 전부 등장 | 2026-08-17 장중 |
| **W-23** | 점검 보고서 머리말 | (F-7 적용 후) "작업트리 미커밋"·"기준선 대비 src/ 변경" 두 축이 실측값으로 분리 표기 | 2026-08-17 장전 |
| **W-10** | `CollectorReconnectNoTick` | **오늘 재연결 0회로 판정 불성립.** 08-18까지 연장, 그때는 replay로 강제 채점 | 2026-08-18 |

**완료 처리**: `dbe37df`(5xx 백오프) — 09:33:02 `InvestorFlowPollRetried attempts=2` 실전 복구 확인.

---

## 5. dev_memory 반영

- `DECISION_LOG.md` 추가 항목: `[MW0601] 인프라는 살았고 판단만 죽었다 — 2026-08-14 장중 점검 (롤의 세 번째 얼굴)`
- `NEXT_TODO.md` 추가 체크박스: Fix 8건(F-1~F-8) · 고도화 4건(G-1~G-4) · 관측 14건(W/V)
- 완료 처리한 기존 항목: **W-15**(롤 원인 확정) · **`dbe37df` 라이브 검증**
- 폐기 처리: 장전 **F-5**(`OptionChainSkipped.reason`) · 장전 **G-3**(`regime_axis_unavailable`)
- 정정: **"미커밋 179건"** → 실측 두 축(작업트리 10 files / 기준선 대비 src/ 9 files)으로 교체, 승격 차단 제안 철회
