# MESSIAH 일일 점검 — 2026-08-18 / 장중

> **관측 구간: 2026-08-18 09:00 ~ 13:30 KST (실행 시각 13:29, 마지막 로그 13:30).**
> **하루가 아직 끝나지 않았다.** 장후 산출물 부재(`daily_integrity_20260818.json` ·
> `self_eval` · `vol_scorecard` · `postmarket_20260818.log`), 종가 기반 지표 미산출,
> `SessionEnd`·`TickDeliveryLatency` 등 15:35 이후에만 나오는 태그의 부재는
> **결함이 아니라 판정 불가**다. 아래에서 그 둘을 섞지 않는다.

> **★ 2026-08-18은 모의투자 D-day 1일차다** (`Docs/동작흐름과상태/2026-08-16_모의투자_Dday_준비계획.md`,
> `NEXT_TODO.md:4664` D-1④ *"2026-08-18을 1일차로"*). 오늘 점검이 묻는 것은
> **"D-day Go/No-Go 세 조건이 실제로 성립했는가"** 이고, 그 채점이 §0에 있다.

- 점검 시각: 13:29 KST (**예약 설계시각 12:30 대비 59분 지연** — §1-P2-1)
- 대상 국면: `intra`
- HEAD `ef9807c` · 실행 프로세스 sha `ef9807c` (**`code_version.stale=false`**) · 당일 커밋 **0건**
- 미커밋 179건(표시상) — `git diff --stat -w --ignore-cr-at-eol -- src scripts configs` **빈 출력**.
  전량 CRLF 개행 잡음 82파일 + 문서/설정. 08-14 F-7 기록과 동일 → **부채 아님**
- 증거: `logs/dailycheck/evidence_20260818_intra.md`
- 직전 보고서: `logs/dailycheck/2026-08-17_intra_report.md` (08-17은 휴장 — 실질 델타 기준일은 `2026-08-14_post_report.md`)

---

## 0. 한 줄 결론

**⚠ P0 없음.** 포지션 0 · 주문 0 · `ERROR`/`WARNING` **0건** · 수집 연속성 완전 · 리스크 한도 이탈 없음 —
**사람이 지금 개입할 일은 없다.** 다만 **D-day Go/No-Go ④를 「달성」으로 읽으면 안 된다**:
`gate=score` 9건은 *"우위가 없었다"* 가 아니라 *"입력이 0이었다"* 의 다른 이름이고,
그 둘을 가르는 계측(08-13 장중 F-1·F-2)이 **3거래일째 미착수** 상태다.

### D-day Go/No-Go 채점 (계획서 §4 · `NEXT_TODO.md:4695`)

| 조건 | 판정 | 근거 |
|---|---|---|
| ① 무중단 | **성립** | l1 08:20:30 기동 후 13:30까지 연속. 10분 이상 공백 **0건**. 재기동 0회 |
| ② 국면 UNKNOWN < 100% | **성립** | `RegimeClassified` 10건 전부 실판정(TREND_DOWN 2·HIGH_VOL 5·RANGE 2·TREND_UP 1). 확신도 0.56~1.00. **UNKNOWN 0%** |
| ③ `n_experts ≥ 1` **또는** 0의 사유 확정 | **성립(후자)** | `AggregatorNoContribution` 9/9 전건 `blocked_by_meta=['30m']`. 리허설 15/15와 **동일 갈래** |
| ④ `decision_funnel`에 `regime` 외 게이트 | **판정 보류** | `score` 9 · `regime` 1로 형식상 등장했으나, `score` 갈래가 `n_experts=0`을 흡수하고 있다(§1-P1-2). **분리 계측 전까지 채점 불가** |

**①~③ 성립 → D-day 1일차 성립.** 40거래일 관문의 1/40이 오늘부터 기산된다.

| 국면 | 판정 | 근거 요약 |
|---|---|---|
| 장전 | **정상** | 자가점검 2회 전항 `[OK ]` · `self-check: PASS` · `schedule_drift 정본 일치` · `postmarket 20260817 정상 종료 확인`(P-1 예보 적중) · `rollover 비-롤일 A05609` · `calendar covered_through=2026-12-31(D+135)` |
| 장중 | **조건부 정상** | 파이프라인 4축 전부 `OK`, 데이터 손실 0. 그러나 **국면 전파 경합**(P1-1)과 **판단 갈래 미분리**(P1-2)로 *판단 경로의 관측이 훼손*돼 있다 |
| 장후 | **판정 불가** | 15:35 이후 산출. `post` 점검에서 한다 |

---

## 1. 이상점 정리보고

### P0 — 오늘 거래에 직접 위험

**해당 없음.** 오늘 `DecisionEmitted` 10건 전부 `side=NO_TRADE`이고 `gate=pass`가 0건이라
Risk·Sizer·OrderGateway는 한 번도 돌지 않았다. `UnmatchedFill` 0건 ·
`circuit_breaker.phase=normal` · `gateway_halted=false` · `irrecoverable_loss.clean=true`(`lost_items=0`).
**사람이 취할 운영 조치 없음 — 관망이 정답이다.**

---

### P1 — 정확성·관측 훼손

#### 1-1. 국면 판정과 집계가 같은 사이클을 보지 않는다 — 09:00 재현 + **장중 갈래 신규 발견**

- **증상**: `RegimeClassified`가 낸 국면과, 같은 사이클 `AggregatorNoContribution`이 쓴 국면이
  10사이클 중 **2건에서 어긋났다.** 하나는 세션 첫 사이클(기존 진단), 다른 하나는 **장중 12:30(신규)** 이다.

- **근거** (`logs/g2_daily_20260818.log`):

  ```
  09:00:00.808844 RegimeClassified        국면 판정 TREND_DOWN (확신도 0.76)  bars_used=200
  09:00:01.022475 AggregatorNoContribution 기여 의견 0 … "regime": "UNKNOWN"          ← 어긋남 (Δ214ms)
  09:00:01.077484 DecisionEmitted          ② Regime=UNKNOWN — 이벤트/미판정 국면  gate=regime

  12:30:00.812364 RegimeClassified        국면 판정 RANGE (확신도 0.72)
  12:30:01.017834 AggregatorNoContribution 기여 의견 0 … "regime": "HIGH_VOL"         ← 어긋남 (Δ205ms, 직전 사이클 값)
  ```

  **대조군 — 지연 크기로 설명되지 않는다**:

  ```
  10:00:00.602981 RegimeClassified TREND_DOWN→HIGH_VOL  /  10:00:00.668506 Agg "regime":"HIGH_VOL"  ✓ (Δ 66ms)
  13:30:00.780359 RegimeClassified RANGE→TREND_UP       /  13:30:01.354790 Agg "regime":"TREND_UP"  ✓ (Δ574ms)
  ```

  Δ66ms에 맞고 Δ205ms에 틀렸다 → 지연 임계가 아니라 **비결정적 순서 경합**이다.

- **기준 위반**:
  - `src/messiah/strategy/futures/service.py` 모듈 docstring — UNKNOWN 대체를
    *"아직 한 번도 안 왔으면"* 으로 한정한다. 12:30 사례는 **RegimeState를 8회 받은 뒤**의 어긋남이라
    설계 문구가 실동작을 덮지 못한다.
  - 같은 docstring §「BarClosed 재구독 없음」 — *"둘 다 같은 `bar.*`를 구독하면 `InProcessBus`의 핸들러
    등록 순서가 곧 실행 순서라 결과가 취약해진다"* 며 `bar.*` 재구독을 피했다.
    **그런데 `feat.*`와 `intel.regime` 사이에 정확히 같은 취약성이 남아 있다** —
    `run_forever()`가 둘을 한 구독으로 묶을 뿐 순서를 보장하지 않는다(`service.py:120~131`).
  - SYSTEM.md **R6** — `_UNSEEN_REGIME`(`service.py:57`)의 UNKNOWN이 *"아직 못 받았다"* ·
    *"판정할 수 없다"* · *"이번 사이클에 못 따라잡았다"* **세 뜻을 겸한다.**
    `phases.md` D절 「하나의 회색이 여러 뜻을 겸하면 그것부터 분리 대상」.

- **영향**:
  - `aggregator.py:214 REGIME_WEIGHTS.get(regime_state.regime, …)` 가 **틀린 국면의 가중치표**를 조회한다.
    오늘은 `blocked_by_meta`로 전건이 막혀 n=0이라 **결과가 안 바뀌었다(손익 영향 0)**.
    **Meta-Labeler가 통과하기 시작하는 날 즉시 오작동한다** — 그날 처음 겪으면 원인 규명이 몇 배 어렵다.
  - `decision_funnel`의 `gate=regime` 1건이 **위양성**이다. 오늘 Go/No-Go ② 채점에서
    "UNKNOWN 1/10"으로 보였던 것이 실은 판정 실패가 아니라 전파 실패다.

- **신규 여부**: **기존 진단의 확대**.
  - 09:00 갈래 = `dev_memory/DECISION_LOG.md:4844` 「결함 ① — 세션 첫 판단이, 국면 판정이 이미 나온 뒤에도
    국면 없이 접혔다 (P1, 확정)」(2026-08-13). 처방 `F-3 (b) 선발행안`은
    `NEXT_TODO.md:3723`에 **여전히 `- [ ]`**. `grep -rn "RegimeSeeded" src/ scripts/` → **0건**(미구현).
    거래일 기준 **08-13·08-14·08-18 3거래일 미착수**.
  - **12:30 갈래는 신규다.** 그리고 이것이 오늘의 핵심 정보다 —
    **F-3 선발행안은 첫 사이클만 씨앗을 심으므로 12:30 갈래를 못 고친다.**
    처방을 「시드」가 아니라 **「사이클 정합」** 으로 다시 세워야 한다(§2 F-1).

---

#### 1-2. `n_experts=0`이 「우위 부족」으로 보고된다 — Go/No-Go ④의 채점 자체가 오염

- **증상**: 9사이클 전부 기여 전문가 0명인데, 판단 로그는 *"의견은 있으나 약하다"* 로 말한다.

- **근거**:

  ```
  logs/g2_daily_20260818.log
  09:30:00.634176 AggregatorNoContribution 기여 의견 0 … views_received=1, blocked_by_meta=['30m']
  09:30:00.662174 DecisionEmitted          ④ |S|=0.000 < 0.2 — 우위 부족   side=NO_TRADE  gate=score
  ```
  gate 분포(당일 10건): `score` **9** · `regime` **1** · `kill`·`dispersion`·`pass` **각 0**.
  `AggregatorNoContribution` **9건** — `score` 9건과 **1:1 대응**한다.

  코드 근거:
  - `src/messiah/strategy/decision/meta_decision.py:74`
    `DECISION_GATES = (GATE_KILL, GATE_REGIME, GATE_DISPERSION, GATE_SCORE, GATE_PASS)`
    — **`no_expert` 갈래 없음.**
  - 같은 파일 `_no_trade()` 말미 `mlog.log("DecisionEmitted", rationale, symbol=…, side="NO_TRADE", gate=gate)`
    — **`n_experts`·`score`·`dispersion`·`uncertainty` 전부 미기록.**
  - `aggregator.py`의 `total_weight <= 0` 폴백이 `score=0.0, dispersion=0.0, uncertainty=1.0, n_experts=0`을 내고,
    `dispersion 0.0`은 ③(임계 0.25)을 무사통과해 ④에서 접힌다.

- **기준 위반**: **금지 15계명 12(조용한 폴백 금지)** — `total_weight<=0`은 최대 보수 모드 폴백인데
  배지도 경보도 없이 INFO 한 줄로 지나간다. SYSTEM.md **R10**(폴백은 배지·경보 동반)의 로깅 측 대응물.
  **R6** — 사유 1개(`score`)가 두 상태를 겸한다.

- **영향**: **오늘 채점 결과를 왜곡한다.** 계획서 §4의 ④ *"`decision_funnel`에 `regime` 외 게이트 등장이면 이상적"*
  이 형식상 충족된 것처럼 보이나, 등장한 `score`는 ⓪(입력 0)의 위장이다.
  리허설 예보는 *"④는 안 날 가능성이 높다(meta 0.658 < 0.700)"* 였고 **그 예보가 맞았는데
  로그가 틀린 답을 냈다.** 이 상태로 40거래일을 쌓으면 관문 통계의 분모가 처음부터 오염된다.

- **신규 여부**: **기존 미착수** — `NEXT_TODO.md:3713` **F-1**(판단 갈래 값 계측) ·
  `NEXT_TODO.md:3718` **F-2**(`GATE_NO_EXPERT`를 ②(regime) 앞 ⓪ 갈래로 분리), 둘 다 2026-08-13 장중 기재,
  **여전히 `- [ ]`**. `NEXT_TODO.md:4611`의 *"코드 항목 전부 완료"* 선언은 **2026-08-14 점검 분에 한정**된
  문장이라 이 셋을 덮지 않는다(같은 `F-n` 번호가 날짜별로 재사용된 결과 — §3 G-3).

---

#### 1-3. 완성봉 발행이 거래소 시각 기준 유예 500ms를 상시 초과 — dev_memory가 「로그로 미확인」으로 남긴 자리의 첫 실측

- **증상**: `FeaturePublish`(1m) 286건의 봉 경계 대비 발행 시각을 거래소 시각으로 환산하면
  **중앙값 +615ms** 로 설계 유예 500ms를 넘고, **69.6%(199/286)** 가 예산 밖이다.

- **근거**:

  ```
  08:45:00.391076 [INFO] ClockSkewMeasured  거래소 시각 − 로컬 시계 = +1.78초  skew_seconds=1.777  samples=30
  ```
  `logs/l1_daily_20260818.log` · 1m `FeaturePublish` 286건(08:45:58 ~ 13:30:59)의
  분 경계 대비 오프셋(로컬) 중앙값 −1.162s → **skew +1.777s 보정 시 +0.615s**.
  p95 **+1.466s** · 최대 **+3.362s** · **500ms 초과 199/286 = 69.6%**.

  기동 자가점검도 같은 것을 한 번 말했다(07:23 회차):
  `[OK ] clock  offset=+2.016s · w32time=Running · 경고: 완성봉 유예 500ms보다 큼(임계 2초)`
  — **경고 문구를 달고도 `[OK ]`로 통과**한다(08:20 회차는 +1.880s로 문구조차 사라졌다).

- **기준 위반**: SYSTEM.md **아키텍처 불변 원칙 3** —
  *"Feature 발행·전문가 판단은 해당 Horizon 완성봉 확정 시점에만 (유예 500ms)"*.

- **기존 판단과의 관계 — 이것이 오늘 새로 채운 자리다**:
  - `DECISION_LOG.md:4955`(08-14 장중) *"`clock offset +2.036s`가 완성봉 유예 500ms를 넘지만
    **늦은 봉 드롭 0이라** `bar_close: timer(거래소 시각 경계 구동)`가 흡수 중"*
  - `NEXT_TODO.md:5017`(08-17 장전) *"완성봉 유예 500ms의 3배 이상이나 `bar_close`가 timer 구동이라
    **직접 영향은 로그로 미확인** → `delivery_latency` p99와 함께 볼 것"*
  - **정정: 두 축은 다른 것이다.** 「늦은 틱 드롭 0」은 *데이터 무결성*의 증거이고
    (오늘도 `AggregatorLateTickDropped` **0건** · 봉 결손 0 — §1 긍정 관측),
    「발행 시각이 경계 +615ms」는 *판단 신선도 예산*의 문제다.
    timer 구동이 흡수한 것은 **전자뿐**이다. 「미확인」이었던 후자를 오늘 처음 쟀다.

- **영향**: 오늘은 30m 단일 Horizon 판단이라 615ms의 비중이 작고 주문이 0건이라 **손익 영향 0**.
  1m·3m Horizon을 판단에 쓰기 시작하면 예산의 **123%** 를 상시 소진한다.

- **신규 여부**: **기존 미확정 항목의 확정** (`NEXT_TODO.md:5017` PRE-4의 열린 자리).

- **전제 명기**: 위 계산은 `FeaturePublish`의 `ts`를 **발행 완료 시각**으로 본다.
  이 로그에는 `valid_until`(= `bar_confirm_time`)이 실려 있지 않아 외부에서 두 시각을 가를 수 없다 —
  **그 자체가 관측 결함**이고 §3 G-2로 올린다.

---

### P2 — 운영 부담·기술부채

#### 2-1. 점검 예약이 설계시각을 상시 이탈 — 08-17에 이어 **재발 확정**

- **증상**: 오늘 장중 점검이 설계시각 12:30 대비 **13:29 실행(59분 지연)**.
  같은 시각에 **장전 점검이 함께 돌고 있다** — `logs/dailycheck/evidence_20260818_pre.md` 가
  `생성 2026-08-18 13:29:09 KST · 리포 /sessions/funny-adoring-bell/mnt/fuoption`(별도 세션)로
  기록돼 있어, **설계시각 08:45 대비 4시간 44분 지연**이다.
- **근거**: 위 증거 파일 헤더 2줄 · 본 보고서 헤더 실행 시각 · `logs/dailycheck/2026-08-17_pre_report.md:3`
  *"점검 시각: 16:22 KST (**예약 설계시각 08:45에서 7시간 37분 지연 실행**)"*.
- **기준 위반**: `NEXT_TODO.md` 08-17 장후 결론 ③ *"예약 지연 — 「스케줄러 일괄 지연」으로 확정,
  원인은 리포 밖. **[부분 · D-day 판정]**"* → **오늘이 그 D-day이고, 판정은 「재발」이다.**
  P-2 *"08:45 장전 보고서가 09:00 전에 나오는가"* → **아니오.**
- **영향**: 장전 점검의 존재 이유는 *"오늘 거래할 자격이 되는가"* 를 **개장 전에** 묻는 것이다.
  13:29에 나오는 장전 판정은 사후 부검이지 게이트가 아니다. 지연 폭이 7h37m → 4h44m로 줄었으나
  **09:00 이전이라는 계약은 여전히 파기**돼 있다.
- **신규 여부**: **재발**(08-17 확정 사안). 원인은 리포 밖(Cowork/Windows 스케줄러) — §2 F-4.

#### 2-2. `SessionStart`가 프로세스당 2회 — 기동 창 거절 회차가 기동으로 세어진다

- **증상**: l1·g2 모두 `SessionStart` 2회(07:23:19, 08:20:30 / 08:25:32). 첫 회차는 즉시
  `LaunchWindowRefused`(*"기동 창(08:15~15:35) 이전 07:23:19 — 정시 트리거(08:20)에 맡기고 지금은 뜨지 않는다"*)
  로 종료 → **실기동은 1회**이며 정상 동작이다.
- **기준**: `phases.md` A-1 *"`SessionStart`가 프로세스별로 정확히 1회인가"* 가 위양성으로 걸린다.
- **신규 여부**: **기존 등록 항목** — 검증 등록부 `launch-window-refusal-not-counted`(기한 **2026-08-21**).
  중복 보고하지 않는다. 오늘 실측치(l1 1회·g2 1회 거절)만 기록에 남긴다.

---

### 확인 필요 (확정 아님 — 전부 15:35 이후에 판정된다)

| 항목 | 왜 지금 판정 불가인가 | 무엇을 보면 판정되나 |
|---|---|---|
| `delivery_latency` p99 (`NEXT_TODO` P-8) | `TickDeliveryLatency`는 **장 마감 절차에서 세션당 한 줄**이다 (`scripts/run_l1_daily.py:1010` → `data/collector.py::log_delivery_latency()`, docstring *"장 마감 절차에서 부른다"*). 오늘 로그 0건은 **정상** | 15:35 이후 `TickDeliveryLatency` 태그. `measured=False`면 표본 부족 |
| P-4·P-5·P-6·P-7·P-10 (`daily_integrity`·`task_exit_codes`·연속 카운터·등록부·W-9) | 전부 장후 배치 산출물 | `logs/postmarket_20260818.log` 6/6 완주 + `daily_integrity_20260818.json` |
| P-9 UI 스냅샷 신선도 | UI는 08:20:32 기동(`command_center_ui.json`, `command_center_ui: "UP"`). **연휴 뒤 첫 기동에서 3일 묵은 값을 「지금」으로 그렸는가**는 화면 관측이 필요하고 로그에 남지 않는다 | 사람이 화면을 보거나, §3 G-4(스냅샷 신선도 로그화) 적용 후 |
| §1-1-3의 `ts` 해석 전제 | `FeaturePublish`에 `valid_until`(봉 확정 시각)이 없어 「확정 시각」과 「발행 시각」을 가를 수 없다 | §3 G-2 적용 후 하루치 |
| `meta` 통과확률 라이브 분포 | `blocked_by_meta` 사실은 확정됐으나 **확률값이 로그에 없다** (리허설 최대 0.6576 vs 임계 0.7) | §2 F-2 적용 후. **임계는 낮추지 않는다**(R18) |

---

### 긍정 관측 — 결함 아님, 다음 점검의 출발점

**데이터 연속성 완전.** 장중에 끊긴 것은 장후에 되메울 수 없으므로 산술로 확인했다.

- `FeaturePublish` 1m **286** = 08:45:58~13:30:59 **285분 +1** · 3m **95** · 5m **57** ·
  10m **29** · 15m **19** · 30m **10** — **전부 이론치 정확히 일치**(결손 0).
- `status_snapshot.json`(13:29:27) *"합성봉 **205**개 · 거래량 항등식 일치(유실 0)"* →
  같은 시각 절단 시 94+56+28+18+9 = **205 정확히 일치**.
- `AggregatorLateTickDropped` **0건** · `nan_ratio` 최대 **0.0073**(461/496건이 0.0) ·
  08:15~13:30 10분 이상 공백 **0건** · `irrecoverable_loss.clean=true`(`lost_items=0`, `start_lag 0.5분`).

**W-16 전항 통과** (08-16 P0-1 웜스타트 적재 필터의 라이브 채점):
`FeatureWarmStart.bars_by_horizon` 6개 Horizon 전부 **200 ≥ 22**(`required_bars=180`) ·
`bars_by_source`에 **A05608 등장**(696봉) · `RegimeWarmStartShort` **0** · `OptionChainSkipped` **0** ·
**`WarmStartBarsDropped` 0** — 새 축이 울지 않았다.

**W-21 확정** — `AggregatorNoContribution` 9/9 `blocked_by_meta=['30m']`.
08-16 리허설 15/15와 **동일 갈래**. `NEXT_TODO.md:4618`의 예측(*"`blocked_by_uncertainty`가 지배적일 것"*)은
**기각**된다. 리허설과 라이브가 갈리지 않았다는 것이 오늘 가장 값진 사실이다.

**W-22·W-37 통과** — `OptionChainPolled` **126건 전부 "42/42다리 발행"**(부분 실패 0),
3계열 전부 등장(`regular` 63 · `weekly_mon` 32 · `weekly_thu` 32).

**W-26 통과** — 국면 UNKNOWN 탈출. `RegimeClassified` 10건 전부 실판정, 상수 아닌 분포
(TREND_DOWN 0.76→0.56 · HIGH_VOL 0.90→0.99→1.00→0.99→0.71 · RANGE 0.72→0.99 · TREND_UP 0.66).

**장중 금지사항 준수** — 당일 커밋 **0건** · 재기동 0회 · `code_version.stale=false` ·
학습 흔적 0 (R11 · 금지계명 3·4).

**로그 위생** — l1·g2 통틀어 `ERROR` **0건** · `WARNING` **0건** ·
`FixVerificationRecurred` **0건** · `FixVerificationFailed` **0건**.
08-14 장중의 *"l1 ERROR 51건이 전부 한 태그"* 대비 **완전 소멸** — G-2(반복 ERROR 접기)의 근거가 사라졌다.

**외부 API 실패가 조용하지 않다** — KIS 500/disconnect **4건**
(08:21:02·08:53:48·10:38:03·12:20:28) 전부 `…PollRetried`로 **1회 재시도 복구**, INFO로 명시 기록.
R10(조용한 폴백 금지) 준수. 08-14 장중의 *"08:36~08:51 4샘플에 몰림"* 과 달리 **종일 산발** —
장전 창의 성질이 아니라 상시 배경 잡음이다(F-3 긴급도 하향 유지 근거 보강).

**예보 적중** (`NEXT_TODO.md` D-day 예보):
**P-1** 자가점검에 `[OK ] postmarket 20260817 장후 배치 정상 종료 확인` **나왔다** → 장후 P1 확정.
**P-3** `git diff --stat -w --ignore-cr-at-eol` **빈 출력** → 코드 동결 유지 확인.

---

## 2. Fix 작업 구현계획

> **장중이다. 본 계획은 수립만 하고 적용하지 않는다** (SYSTEM.md **R11** · 금지 15계명 **3·4**).
> **전 항목 적용 시점: 장후 15:35 이후.** 오늘은 D-day 1일차라 관측 연속성의 값이 특히 크다 —
> 재기동으로 얻을 것(새 코드)보다 잃을 것(1일차 무중단 기록)이 크다.

### F-1. 국면 전파를 「캐시 최신값」에서 「사이클 정합」으로 — P1 · 대응 이상점 1-1

- **원인 가설**: `FuturesAIService`가 집계를 **FeatureVector 도착으로 트리거**하면서 국면은
  `_latest_regime` **캐시**에서 읽는다(`service.py:77·85·111`). 두 메시지가 같은 30m 봉 경계에서
  거의 동시에 발생하고 `run_forever()`의 단일 구독은 순서를 보장하지 않는다 → 어느 쪽이 먼저인지가
  스케줄러 운에 달린다. **08-13에 세운 「첫 사이클 시드」 처방은 09:00만 덮고 12:30을 못 덮는다.**
- **변경 파일**:
  - `src/messiah/strategy/futures/service.py`
    - `handle_regime()` — `RegimeState`에 실린 봉 도메인 시각을 함께 보관
      (`self._latest_regime_as_of`). `RegimeState`에 해당 필드가 없으면 `core/messages.py`에
      `as_of`(또는 `valid_until`) 추가를 **선행**한다.
    - `_publish()` — `as_of = trigger.valid_until or trigger.ts_utc` 직후
      `self._latest_regime_as_of` 와 **같은 봉인지 비교**. 다르면
      `RegimeStalenessDetected`(**WARNING**, 신규 태그)를 남기고 `regime_as_of` · `feature_as_of` ·
      `regime` 값을 구조화 필드로 싣는다. **집계는 그대로 진행한다** —
      마스터플랜 §3.2 *"침묵이 아니라 판단이다"*, 08-13에 이미 「보류안 기각」으로 결정된 사안이다.
    - 모듈 docstring의 *"아직 한 번도 안 왔으면 UNKNOWN으로 대체"* 를
      *"같은 봉의 RegimeState가 아직 안 왔으면"* 으로 정정. **문서가 실동작을 덮게 한다.**
  - `src/messiah/strategy/futures/aggregator.py`
    - `_log_no_contribution()`에 `regime_as_of` 필드 추가 — 사후에 어긋남을 셀 수 있게.
  - `src/messiah/core/logging.py` — `"RegimeStalenessDetected": logging.WARNING` 등록.
  - **`scripts/run_g2_paper_trading.py::_load_regime_runtime()`** — 웜스타트(`_warm_start_regime`) 직후
    `classify()` 1회 → `TOPIC_REGIME` 발행 + `RegimeSeeded`(INFO). **08-13 F-3 원안 그대로.**
    위 정합 계측과 **별도 커밋**(이쪽은 행동 변경, 위는 관측).
- **회귀 위험**:
  - `RegimeState` 스키마 변경은 `core/messages.py` 단일 정의를 건드린다 → **R14 3종 세트**
    (코드 + 데이터 마이그레이션 + 조회 화이트리스트) 점검 필요.
    필드 추가만이고 기존 소비자는 미참조라 마이그레이션은 불요일 가능성이 높으나 **착수 전 전수 확인**:
    `grep -rn "RegimeState" src/ scripts/ tests/`
  - `RegimeSeeded` 발행이 09:00 이전에 `AggregatorNoContribution` 1건을 **추가로** 발생시킬 수 있다
    → 채점 분모가 9에서 10으로 바뀐다. 리포트 쪽 카운터 확인.
  - **WARNING 신설은 잡음 위험**이 있다. 오늘 실측 어긋남 2/10 = 20%라 하루 6건 수준 —
    허용 범위다. 20거래일 분포를 본 뒤 승격/강등을 정한다(R18 정신).
- **검증**: `pytest tests/ -k "futures_service or aggregator"` +
  **재생 시나리오**: `intel.regime`을 `feat.30m` **뒤에** 도착시키는 순서 역전 테스트 신규 1건 —
  `RegimeStalenessDetected` 1건 + 집계 정상 진행을 단언.
  다음 거래일: `RegimeSeeded` 1건이 08:25대에 존재 · `gate=regime` **0건** ·
  `RegimeStalenessDetected` 건수가 오늘 실측 2건 대비 **감소**.
- **적용 시점**: **장후(15:35 이후)**.
- **결정 필요 사항**: `RegimeStalenessDetected`를 WARNING으로 낼지 INFO로 낼지.
  **권고: WARNING.** `AggregatorNoContribution`이 INFO인 이유(*"국면 UNKNOWN인 날엔 정상 동작"*)는
  여기 적용되지 않는다 — 어긋남은 어느 날에도 정상이 아니다.

### F-2. `n_experts=0`을 판단 갈래로 분리하고 판단 값을 계측한다 — P1 · 대응 이상점 1-2

> **08-13 장중 F-1 + F-2 원안 그대로.** 새로 설계하지 않는다. 3거래일 미착수분을 그대로 집행한다.

- **원인 가설**: `aggregator.py`의 `total_weight <= 0` 폴백이 `score=0.0 · dispersion=0.0`을 내고,
  `dispersion 0.0`이 ③(임계 0.25)을 무사통과해 ④에서 접힌다 →
  **입력 부재가 우위 부족으로 위장**된다. 갈래 이름과 계측 필드가 둘 다 없어 사후 분리도 불가능하다.
- **변경 파일**:
  - `src/messiah/strategy/decision/meta_decision.py`
    - `GATE_NO_EXPERT = "no_expert"` 신설, `DECISION_GATES`(`:74`)에 편입.
    - `decide()` — ①(kill) 다음, **②(regime) 앞**에 `if view.n_experts == 0:` 갈래.
      **순서가 중요하다** — regime 앞에 두어야 §1-1-1의 국면 어긋남이 이 갈래를 가리지 않는다.
    - `_no_trade()`의 `mlog.log`에 `n_experts` · `score` · `dispersion` · `uncertainty` ·
      `model_version` 구조화 필드 추가. **`rationale` 문자열은 건드리지 않는다**
      (모듈 주석 *"문구를 다듬는 순간 조용히 0이 된다"*).
    - `GATE_PASS` 경로(`decide()` 말미 `mlog.log`)도 **같은 필드 집합으로 통일** —
      현재 두 경로의 관측 스키마가 다르다.
  - `src/messiah/ops/integrity_report.py::decision_funnel` — `no_expert` 갈래 편입.
  - `src/messiah/strategy/futures/aggregator.py` — `compute()`의 `total_weight <= 0` 폴백 지점에
    `meta_pass_prob`(Horizon별 실제 확률값)를 `_log_no_contribution()` 필드로 추가.
    **「확인 필요」의 meta 분포 항목이 이걸로 열린다.**
  - `src/messiah/core/logging.py` — 필요 시 태그 심각도 등록.
- **회귀 위험**:
  - `DECISION_GATES` 소비처가 조용히 깨질 수 있다. **착수 전 전수 확인**:
    `grep -rn "DECISION_GATES\|decision_funnel\|GATE_SCORE" src/ scripts/ tests/`
  - **R18 저촉 아님** — 차단 **결과**는 동일하고 표기만 분리한다.
    차단 계층 3개(Meta-Labeler/Risk/KillSwitch) 고정 유지 — 새 차단 계층이 아니다.
  - 오늘 이후 `gate=score` 카운트가 급감한다 → **불연속을 리포트에 명기**해야 한다.
    조용히 자르면 나중에 "왜 8-18까지 score가 9였다가 0이냐"를 아무도 못 푼다(08-16 D-1④와 같은 규율).
- **검증**: `pytest -k meta_decision` — 기존 `rationale` 단언 전부 통과(문자열 불변) +
  `n_experts=0` 입력에 `gate == "no_expert"` 신규 단언.
  다음 거래일: `gate` 분포에 `no_expert` 등장 · `score` 갈래는 **n_experts ≥ 1인 사이클만**.
- **적용 시점**: **장후(15:35 이후).** F-1과 **같은 커밋에 넣지 않는다** — F-1은 국면, 이쪽은 판단이라
  되돌릴 때 분리돼야 한다.
- **결정 필요 사항**: 없음(08-13에 이미 결정된 안).

### F-3. 기동 자가점검이 시계 오프셋을 「완성봉 예산」으로 판정한다 — P1 · 대응 이상점 1-3

- **원인 가설**: `clock` 축의 판정 임계가 **2초**(w32time 동기 여부 기준)라
  **완성봉 유예 500ms 예산**과 축이 다르다. +1.88s는 전자를 통과하고 후자를 3.8배 초과하는데
  `[OK ]` 한 글자가 그 사실을 삼킨다(07:23 회차는 경고 문구를 달고도 `[OK ]`였다).
- **변경 파일**:
  - `scripts/self_check.py` — `clock` 축을 **두 판정으로 분리**:
    ① 시계 동기 건전성(임계 2초, 기존) ② **완성봉 예산**(`|offset| < 500ms`).
    ②가 깨지면 `[WARN]`으로 표기하고 *"발행이 경계 +{offset}s에 일어난다"* 를 문장으로 남긴다.
    **기동 거부는 하지 않는다** — R18(게이트 신설은 섀도 20거래일 후 승격), 오늘 실측 1건뿐이다.
  - `src/messiah/ops/clock_skew.py` — `delivery_latency_seconds()` 옆에
    `publish_offset_seconds()` 신설: `FeaturePublish` 시각과 봉 경계의 차를 세션 누적으로 분포화.
  - `scripts/run_l1_daily.py` — 장 마감 절차의 `collector.log_delivery_latency()` **다음 줄**에
    `FeaturePublishOffset`(INFO) 1건: p50/p90/p99/max + 500ms 초과 비율.
    `log_delivery_latency()`와 **같은 규율** — 못 잰 날은 못 쟀다고 남긴다(L18).
  - `src/messiah/ops/integrity_report.py` — `publish_offset` 필드 수록,
    `unmeasured` 판정에 편입.
- **회귀 위험**: 낮음(계측 추가). `self_check` 축 추가는 `tests/ops/test_self_check.py` 계열의
  축 개수 단언을 깰 수 있다 — 현재 14축.
- **검증**: `pytest tests/ops/ -k "self_check or clock"` ·
  `python scripts/self_check.py --skip-redis` 15축 출력.
  다음 거래일: `FeaturePublishOffset` 1건 + `daily_integrity`의 `publish_offset` 비-null.
- **적용 시점**: **장후(15:35 이후).**
- **결정 필요 사항**: `[WARN]`이 `self-check: PASS`를 뒤집을지.
  **권고: 뒤집지 않는다.** 오늘 실측이 1거래일뿐이고, 여기서 기동을 막으면 D-day 40거래일 관문의
  분모를 시계 문제가 갉아먹는다. 20거래일 분포 후 재판단(R18).

### F-4. 점검 예약 지연을 리포가 스스로 말한다 — P2 · 대응 이상점 2-1

- **원인 가설**: 원인이 리포 **밖**(Cowork/Windows 스케줄러)이라 코드로 못 고친다.
  고칠 수 있는 것은 **「늦었다는 사실이 매번 드러나게 하는 것」** 이다.
- **변경 파일**:
  - `.claude/skills/messiah-daily-check/scripts/collect_evidence.py` — 다이제스트 §1 머리에
    `설계시각 → 실행시각 → 지연` 3연을 국면별 상수(pre=08:45 · intra=12:30 · post=16:00)로 출력.
    지연 60분 초과면 §9 자동 적신호에 편입.
  - `.claude/skills/messiah-daily-check/references/report_template.md` — 헤더 필수 항목에
    「점검 시각 + 설계시각 대비 지연」 명기(오늘 보고서는 수기로 넣었다).
  - `scripts/install_scheduled_tasks.ps1` — **점검 트리거가 이 파일의 정본에 없다.**
    `Messiah-DailyCheck-Pre/Intra/Post` 3종 등재를 검토 → 그러면 `schedule_drift` 자가점검이
    점검 자체의 지연도 잡는다. **이것이 근본 해법이다.**
- **회귀 위험**: 스킬 스크립트 변경은 과거 다이제스트와 형식이 갈린다.
  08-14 F-12(같은 날 2회 점검 시 파일명 규칙)와 같은 묶음으로 처리.
- **검증**: 오늘 다이제스트 재생성 시 머리 3연 출력 · 지연 59분이 적신호로.
- **적용 시점**: **장후.** 코드 동결 대상이 아니다(리포 밖 도구)나 규율상 같이 미룬다.
- **결정 필요 사항**: 점검을 Windows 작업 스케줄러로 옮길지, Cowork 예약을 유지할지.
  **권고: 옮긴다.** `schedule_drift` 축이 이미 정본 비교를 하고 있어 공짜로 감시가 붙는다.

### 적용 순서와 커밋 계획

> **전부 2026-08-18(화) 15:35 이후.** `run_postmarket` **6/6 완주 확인이 선행 조건**이다
> (`NEXT_TODO.md` PRE-5 *"완주 확인 후에야 착수"* · 거래일 회귀 실측 기한이 오늘이다).

1. **커밋 ①** — F-2 (판단 갈래 분리 + 값 계측) —
   `[MW0601] 입력이 0인 것과 우위가 없는 것은 다른 일이다 — no_expert 갈래 분리 + 판단 값 계측`
   *가장 먼저다. 오늘 채점을 오염시키는 유일한 항목이고, 40거래일 분모가 매일 쌓인다.*
2. **커밋 ②** — F-1 관측분(`RegimeStalenessDetected` + `regime_as_of`) —
   `[MW0601] 국면과 집계가 같은 봉을 보는지 로그가 말하게 한다`
3. **커밋 ③** — F-1 행동분(`RegimeSeeded` 선발행) —
   `[MW0601] 08:25에 이미 판정할 정보가 있었다 — 첫 사이클 국면 시드`
4. **커밋 ④** — F-3 (완성봉 예산 계측 + self_check 축 분리) —
   `[MW0601] 흡수된 것은 데이터였지 시간이 아니었다 — 발행 오프셋 계측`
5. **커밋 ⑤** — F-4 (점검 도구) — `[MW0601] 점검이 자기 지각을 스스로 적는다`

**②와 ③을 가르는 이유**: ②는 관측, ③은 행동 변경이다.
③이 부작용을 내면 ②만 남기고 되돌려야 하는데, 한 커밋이면 그 선택지가 사라진다.

---

## 3. 고도화 방안

### G-1. `decision_funnel`을 장중에 볼 수 있게 한다

- **관측 근거**: 오늘 13:29 `status_snapshot.json` 최상위 키에 **판단 계열이 0개**다.
  `verdict.reasons`에 `no_expert_contribution` 한 줄이 있을 뿐, *"9/9가 한 갈래로 접혔다"* 는
  **이 보고서를 쓰려고 g2 로그를 직접 센 뒤에야** 알았다. D-day 1일차인데 장중에 그걸 못 본다.
- **제안 내용**: `status_snapshot.json`에
  `decision: {funnel: {kill, no_expert, regime, dispersion, score, pass}, last_decision_kst, cycles}`.
  **누적 카운터를 엔진에 심지 않는다** — 스냅샷 생성기가 당일 g2 로그의 `gate` 필드를 센다
  (엔진에 상태를 심으면 R12 무상태 원칙과 부딪히고, 재기동 때 카운터가 리셋된다).
- **기대 효과**: 장중에 *"오늘 몇 번 판단했고 어디서 접혔는가"* 가 한 눈에.
  §1-1-2 같은 오염을 장후가 아니라 **당일 12:30에** 잡는다.
- **비용·위험**: 낮음. 로그 파싱이라 엔진 무변경. **선행: F-2**(갈래 이름 확정) — 먼저 하면 두 번 짠다.
- **우선순위 제안**: **이번 주.** (08-13 장중 G-1 원안 — 선행조건이 오늘 F-2로 풀린다)

### G-2. `FeaturePublish`가 「봉 확정 시각」과 「발행 시각」을 함께 말한다

- **관측 근거**: §1-1-3의 계산이 **전제 하나에 기대고 있다** — `ts`가 발행 완료 시각이라는 것.
  `features/engine.py`가 `valid_until`(= `bar_confirm_time`)을 이미 채우고 `service.py:110`이
  *"이 파이프라인 전체가 같은 시각 도메인을 써야 한다"* 며 그것을 쓰는데,
  **정작 로그에는 안 실린다.** 그래서 615ms가 「확정이 늦은 것」인지 「발행이 늦은 것」인지 못 가른다.
- **제안 내용**: `features/engine.py`의 `FeaturePublish` `mlog.log`에
  `bar_confirm_kst`(= `valid_until`) · `publish_offset_ms`(= 발행시각 − 확정시각) 2필드 추가.
  현재 필드는 `symbol/horizon/feature_set/nan_ratio` 넷뿐이다.
- **기대 효과**: 완성봉 규율(불변원칙 3) 위반을 **매 봉 단위로** 판정 가능.
  F-3의 세션 요약이 「분포」라면 이것은 「원본」이라, 특정 봉이 튄 이유를 되짚을 수 있다.
- **비용·위험**: 낮음(필드 2개). DEBUG 라인이 하루 500건이라 용량 영향 무시 가능.
- **선행 조건**: 없음. **F-3보다 먼저 넣으면 F-3의 계측이 더 정확해진다.**
- **우선순위 제안**: **이번 주** (F-3과 같은 커밋 ④에 합류).

### G-3. Fix ID를 날짜에 묶어 「완료」 선언이 남의 항목을 못 덮게 한다

- **관측 근거**: `NEXT_TODO.md:4611` *"F-A·F-B·F-C·F-D · F-1·F-2·F-3 … **코드 항목 전부 완료.**"*
  이 문장은 **2026-08-14 점검 분**을 가리키는데, 같은 파일 `:3713`·`:3718`·`:3723`의
  **2026-08-13 장중 F-1·F-2·F-3은 여전히 `- [ ]`** 다. 오늘 §1-1-1·1-1-2가 바로 그 셋의 증상이고,
  **3거래일 미착수를 오늘에야 알아챘다.** 415KB 파일에서 `F-1`을 grep하면 12곳이 나온다.
- **제안 내용**: 신규 항목의 ID를 `F-1` → **`F-0813I-1`**(날짜+국면 이니셜+일련)로.
  `.claude/skills/messiah-daily-check/references/report_template.md` §2 헤딩 규격과
  §5 dev_memory 반영 절에 규칙 명기. **기존 항목은 소급 개명하지 않는다**
  (과거 보고서와의 상호참조가 끊긴다) — 새로 다는 것부터 적용.
- **기대 효과**: *"어제 세운 fix 중 오늘 검증 예정이던 항목"*(`phases.md` C-4)을
  기계적으로 grep할 수 있다. 미착수 항목이 완료 선언에 묻히지 않는다.
- **비용·위험**: 문서 규칙 변경뿐. 과도기에 두 형식이 공존한다.
- **선행 조건**: 없음.
- **우선순위 제안**: **즉시(장후 커밋 ⑤에 합류).** 오늘 이 결함이 P1 두 건을 3거래일 지연시켰다.

### G-4. UI 스냅샷 신선도를 화면 밖에서도 판정 가능하게

- **관측 근거**: `NEXT_TODO` **P-9**(*"연휴 뒤 첫 기동에서 3일 묵은 값을 「지금」으로 그리는가"*)가
  오늘 판정 예정이었으나 **로그로는 판정할 수 없다.** UI 기동은 남았지만
  (`command_center_ui.json` 08:20:32 · `status_snapshot.command_center_ui: "UP"`)
  **화면이 무엇을 그렸는지는 아무 파일에도 없다.** 08-17 장후 결론 ⑤가 남긴 자리도 같은 이유로 열려 있다.
- **제안 내용**: `src/messiah/ui/app.py` 기동 직후 1회,
  읽어들인 `status_snapshot.json`의 `generated_at_kst`와 현재 시각의 차 ·
  신선도 배지 판정 결과를 `UISnapshotFreshness`(INFO)로 `logs/ui_*.log`에 남긴다.
  **임계가 「거래일 기준」인지 「경과 시간 기준」인지도 필드로 명시**한다.
- **기대 효과**: 연휴 뒤 첫 기동 오탐을 **다음 연휴를 기다리지 않고** 재생으로 재현·판정.
  다음 기회는 2026-09-24(추석)까지 5주 뒤다.
- **비용·위험**: 낮음. R17(UI는 `ui.*` 구독 모델) 저촉 없음 — 로깅만 추가한다.
- **선행 조건**: 없음.
- **우선순위 제안**: **다음 단계.** 오늘 실피해 0이고, 5주 유예가 있다.

### 로드맵 반영 제안

| 항목 | 현 로드맵 위치 | 제안 위치 | 사유 |
|---|---|---|---|
| F-2 (no_expert 갈래) | 08-13 장중 Fix, 미착수 | **D-day 1일차 장후 최우선** | 40거래일 관문의 분모가 매일 오염된다. 늦을수록 소급 정정 비용이 커진다 |
| G-1 (장중 funnel) | 08-13 장중 고도화 「이번 주」 | 유지 | 선행 F-2가 오늘 풀린다 |
| G-3 (Fix ID 규격) | 없음(신규) | **즉시** | 오늘 P1 2건의 3거래일 지연이 이 결함의 직접 산물이다 |
| meta 임계 0.7 재검토 | `NEXT_TODO.md:4687` *"임계를 낮추지 않는다(R18)"* | **유지** | 라이브 1일차다. F-2의 `meta_pass_prob` 계측으로 며칠치 분포부터 모은다 |

---

## 4. 다음 거래일 관측 예정

| ID | 관측 대상(태그/지표) | 판정 기준 | 판정 예정일 |
|---|---|---|---|
| **X-1** | `TickDeliveryLatency` | 오늘 15:35 이후 1건 존재 · `measured=true` · p99 값 확보. `measured=false`면 표본 부족 | 2026-08-18 장후 |
| **X-2** | `postmarket_20260818.log` `steps_run` | **6** — 08-17 비거래일 게이트의 거래일 회귀 실측(`DECISION_LOG` 라이브 미검증 L15, **기한 오늘**) | 2026-08-18 장후 |
| **X-3** | `DecisionEmitted` `gate` 분포 (종일) | `regime` **1건뿐**(09:00 단건)이면 §1-1-1의 「첫 사이클 + 산발」 구조 확정. 2건 이상이면 더 넓게 틀렸다 | 2026-08-18 장후 |
| **X-4** | `AggregatorNoContribution` 종일 건수 · `blocked_by_meta` 비율 | 13사이클 전건 `blocked_by_meta`면 W-21 종일 확정. 다른 갈래가 섞이면 국면 의존성이 있다는 뜻 | 2026-08-18 장후 |
| **X-5** | `AggregatorNoContribution.regime` vs 직전 `RegimeClassified.regime` | 종일 어긋남 건수. 오늘 오후분까지 포함해 **2/13 초과면** 경합 빈도가 오전 관측보다 높다 | 2026-08-18 장후 |
| **X-6** | `daily_integrity_20260818.json` `degenerate_feature_count` | 08-14의 **57**에서 감소. 0이면 08-16 P0-1(웜스타트 적재 필터)이 들었다는 강한 증거 | 2026-08-18 장후 |
| **X-7** | (F-2 적용 후) `gate` 분포 | `no_expert` 등장 · `score`는 `n_experts ≥ 1`인 사이클만 | 2026-08-19 장중 |
| **X-8** | (F-1 적용 후) `RegimeSeeded` · `RegimeStalenessDetected` | `RegimeSeeded` 1건이 08:25대 · `gate=regime` **0건** · Staleness 건수가 오늘 2건 대비 감소 | 2026-08-19 장중 |
| **X-9** | (F-3 적용 후) `FeaturePublishOffset` | 1건 존재 · 500ms 초과 비율이 오늘 실측 **69.6%** 대비 어떻게 움직이는가(시계 상태 의존) | 2026-08-19 장후 |
| **X-10** | 점검 예약 지연 | 08-19 장전 점검이 **09:00 전에** 나오는가. 또 늦으면 F-4의 「스케줄러 이관」이 확정된다 | 2026-08-19 장전 |

---

## 5. dev_memory 반영

- `DECISION_LOG.md` 추가 항목:
  `## [MW0601] 0의 사유는 찾았는데 0이라는 사실을 로그가 감췄다 — D-day 1일차 장중 (2026-08-18)`
- `NEXT_TODO.md` 추가 체크박스: **F-1~F-4 (4건) · G-1~G-4 (4건) · X-1~X-10 (10건)** = 18건
- 완료 처리한 기존 항목: **없음** — 하루가 안 끝났다.
  W-16 · W-21 · W-22 · W-26 · W-37 · P-1 · P-3은 **장중 잠정 통과**로만 기록하고
  체크는 `post` 점검에서 한다(08-13 장중의 V-9 처리와 같은 규율).
- **커밋하지 않는다** — 장중이다(R11 · 금지계명 3·4). 문서 갱신은 코드 변경이 아니므로 지금 한다.
