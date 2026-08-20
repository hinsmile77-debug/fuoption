# MESSIAH 일일 점검 — 2026-08-13 / 장후

- 점검 시각: 16:05 KST
- 대상 국면: post (장후) — 하루 전체
- HEAD `e37d387` · 실행 중 `e37d387` (`code_version.stale=false`) · 미커밋 179건 · **당일 커밋 없음**
- 증거: `logs/dailycheck/evidence_20260813_post.md`
- 장후 배치: **완주** — `postmarket_20260813.log` 15:45:02~15:47:25 · 5/5 단계 · 실패 0 · 볼 것 1
- 직전 보고서: `2026-08-13_pre_report.md`(08:59) · `2026-08-13_intra_report.md`(12:45) · `2026-08-12_post_report.md`

## 0. 한 줄 결론

**오전은 설계대로 살았고 15:20에 데이터가 끊겼는데, 그 15분 동안 아무도 소리치지 않았다** —
헬스는 CRITICAL을 알고 있었으나 로그 경보로도, 강제 재연결로도, 손실 계측으로도 이어지지 않았다.

| 국면 | 판정 | 근거 요약 |
|---|---|---|
| 장전 | 정상 | 기동 자가점검 26행 전건 OK · 정시 트리거 지연 +0.6분 · 첫 틱 08:44:58 · `collection_start_lag_minutes 0.6` |
| 장중 | 조건부 정상 (12:36까지) | 데이터 연속성 완전 · 국면 분포 3종 UNKNOWN 0% · 판단 14건 · 장중 점검 지적 3건은 전부 P1/P2 |
| 장후 | **이상** | 15:20~15:35 틱 0건(소급 불가 10분) · 스톨 재경보 0건 · CB 확정 후 해제 0회 · 임계 초과 4건 · 수정 재발 8건 |

---

## 1. 이상점 정리보고

### P0 — 오늘 거래에 직접 위험

#### 1-1. WS 재연결은 성공했는데 틱이 15분간 한 건도 오지 않았고, 스톨 감시가 다시 울리지 않았다 (신규 · 확정)

- **증상**: 15:19을 마지막으로 1분봉이 끊기고 세션 종료(15:35)까지 복구되지 않았다. 그 사이
  강제 재연결이 **한 번만** 걸렸고, 재연결 뒤 11분간 스톨 경보가 **0건**이었다.

- **근거** — 시각순 재구성:

  ```
  15:19        마지막 1분봉 (일자 통합본 395행 · 08:45~15:19, 결손 0분)
  15:20:06 [WARNING] ComposerFlushedIncomplete  5m 버킷(15:15)의 마지막 1분봉(15:19)이 5초 안에 안 와 짧은 채로 확정
  15:21:00 [WARNING] ComposerLateBarDropped     이미 확정한 5m 버킷(15:15)으로 1분봉(15:19)이 늦게 도착 — 버림
  15:22:18 [WARNING] CollectorTickStall         소켓은 열려 있으나 142초간 틱 없음 — 강제 재연결(임계 120초)
                                                 ticks_last_60s=0 · consecutive_stalls=1
  15:22:18 [WARNING] CollectorWSDisconnected    WS 연결 끊김 — 5초 후 재연결 시도
  15:22:24 [INFO]    CollectorWSReconnected     WS 재연결 성공 — 수신 재개     ← 이 문장이 사실이 아니다
  15:23:20 [WARNING] OptionChainSkipped         기준가 없음 — 이 사이클을 건너뛴다  (15:23·15:28·15:29·15:31·15:33 5건)
  15:30:06 [DEBUG]   FeaturePublish             30m/15m — 이 뒤로 발행 없음
  15:34:47           status_snapshot.json  l1.collector level=CRITICAL
                       detail="첫 틱이 09:00까지 없다 — 회선/구독 확인(scripts\recover_now.bat)"
                     l1.feature_engine level=CRITICAL detail="281초간 발행 없음"  (15:34:47-281s = 15:30:06 정확히 일치)
  15:35:00 [INFO]    TickArchiveSummary         체결틱 적재 65089행
  ```
  `logs/l1_daily_20260813.log` · `logs/status_snapshot.json`(15:34:47)

- **틱이 정말 0건이었다는 결정적 증거**: `CollectorFirstTick`이 **08:44:58 1건뿐**이다.
  `collector.py:519`는 `first = not self._watchdog.seen_first_tick` 로 첫 틱을 판정하고,
  재연결 시 `TickStallWatchdog.reset()`이 `_last_tick_at = None`으로 되돌린다(`collector.py:196`).
  **재연결 후 틱이 한 건이라도 왔다면 `CollectorFirstTick`이 두 번째로 찍혔어야 한다.** 안 찍혔다.
  같은 사실을 15:34:47 스냅샷이 `first_tick_overdue()==True`의 문구
  (`collector.py:467` `warmup_expired_detail`)로 독립 확인해 준다.

- **왜 다시 안 울렸는가 (코드 근거, 확정)**:
  ```python
  # src/messiah/data/collector.py — TickStallWatchdog.run_until_stalled()
  while True:
      await self._sleep(self._check_interval_seconds)
      if self._last_tick_at is None:
          continue          # 콜드스타트/워밍업 — 기준선 없음
  ```
  `reset()`이 `_last_tick_at`을 `None`으로 만들므로, **재연결 후 첫 틱이 영영 안 오면 워치독은
  영원히 `continue`만 한다.** 스톨 감시에 "첫 틱을 못 본 채 흐른 시간"에 대한 시한이 없다.
  이것이 오늘 15:22:24~15:35 **11분간 경보 0건**의 구조적 원인이다.

- **기준 위반**:
  - SYSTEM.md **R6**(태그 1개 = 심각도 1개) — `CollectorWSReconnected "수신 재개"`가 구독 성공
    시점에 INFO로 발화한다(`collector.py:404`의 `_on_connected`는 docstring상 "구독까지 성공"에
    호출). 수신이 재개되지 않았는데 재개라고 기록했다.
  - SYSTEM.md **R10 / 금지계명 12**(조용한 폴백 금지) — 데이터 없이 흘러간 15분이 경보 없이 지나갔다.
  - `src/messiah/data/collector.py` 모듈 docstring "데이터 흐름의 1차 책임은 L1(탐지·복구)" —
    탐지도 복구도 두 번째는 없었다.
  - `src/messiah/ops/integrity_report.py::analyze_data_flow_ownership` 규칙 1은
    "스톨 N회인데 재연결 0회"만 본다. **오늘처럼 재연결은 됐는데 틱이 안 돌아온 형태를 통과시킨다.**

- **영향**: 15:20~15:35 **16분 구간의 틱·1분봉이 영구 결손**(소급 불가 — `irrecoverable_loss_minutes 10.0`).
  종가 근방 15분은 하루 중 정보량이 가장 큰 구간이다. 오늘 파생된 2차 피해:
  `late_bar_drops 7건`(어제까지 0건) · `ComposerFlushedIncomplete 5건` · `OptionChainSkipped 5건`
  (기준가 없음 → 옵션체인도 못 모음) · CB 확정 후 게이트 11분 잔류 정지.
  **실주문 0건이라 금전 손실은 없다.** 그러나 이 상태로 실전에 들어가면 종가 구간 자동 복구가 없다.

- **신규 여부**: **신규**. `CollectorTickStall`은 `logs/l1_daily_20260731.log` 이후 오늘이 두 번째,
  "재연결 후 무틱"은 처음이다. 2026-07-31 P0-1(CB 복구 경로 대칭화, `NEXT_TODO.md:1215` `[x]`)이
  만든 짝 불일치 규칙이 오늘 정확히 발화했다는 점에서 **그 fix는 살아 있다** — 못 잡은 것은 L1 쪽이다.

---

### P1 — 정확성·관측 훼손

#### 1-2. 같은 스냅샷이 `CRITICAL` 두 개와 "오늘 소급 불가 손실 없음"을 동시에 말했다 (신규 · 확정)

- **증상**: 15:34:47 `status_snapshot.json` 안에서 `components`는 CRITICAL 2건인데
  `irrecoverable_loss`는 `clean: true`다. 45초 뒤 종료된 세션의 `daily_integrity`는 같은 축을 **10분**으로 계산했다.
- **근거**:
  ```json
  "l1.collector":     {"state":"OK","level":"CRITICAL","detail":"첫 틱이 09:00까지 없다 — 회선/구독 확인"}
  "l1.feature_engine":{"state":"OK","level":"CRITICAL","detail":"281초간 발행 없음"}
  "irrecoverable_loss":{"start_lag_minutes":0.6,"lost_items":0,"clean":true,"summary":"오늘 소급 불가 손실 없음"}
  ```
  `logs/status_snapshot.json` 15:34:47 ↔ `logs/daily_integrity_20260813.json` `irrecoverable_loss_minutes: 10.0`
  (`state`는 하트비트 신선도, `level/detail`은 Health 페이로드 — `ops/status_board.py:142-146`. 둘의 공존은
  형식 결함이 아니다. 결함은 **손실 축이 그 CRITICAL을 못 읽는다**는 것이다.)
- **기준 위반**: 마스터플랜 Ver2.0 관측 원칙 — 하나의 스냅샷이 자기 자신과 모순되면 사람은 둘 다 안 믿는다.
  `references/phases.md` D절 "건수 0은 두 가지다 — 진짜 없었거나, 계측이 없거나".
  오늘 `lost_items: 0`은 **후자**였고, 그것을 구분할 표시가 없다.
- **영향**: 장중 국면(12:30 점검)에 사람이 볼 유일한 실시간 산출물이 **진행 중인 사고를 "깨끗함"으로 표시**한다.
  오늘은 15:20 사고라 장중 점검(12:36)에 안 걸렸지만, 10:00에 같은 일이 나면 스냅샷은 여전히 clean을 말한다.
- **신규 여부**: 신규.

#### 1-3. 진입점 종료 코드가 `TimeoutExpired`로 조회 실패 — `daily-axes-measured`·`exit-code-matches-log` 재발

- **증상**: 장후 리포트가 축 하나를 `미측정`으로 남겼다. `FixVerificationRecurred` 2건의 실체다.
- **근거**:
  ```
  15:47:25 [ERROR] FixVerificationRecurred  daily-axes-measured: 2026-08-13에 기준 위반(오늘) (unmeasured_count ≤ 0)
  15:47:25 [ERROR] FixVerificationRecurred  exit-code-matches-log: 2026-08-11에 기준 위반(2거래일 전) (nonzero_task_exits ≤ 0)
  ```
  `logs/postmarket_20260813.log` · `daily_integrity_20260813.json`:
  `"task_exit_codes": {"exits": [], "available": false, "detail": "조회 실패: TimeoutExpired", "launches": []}`
  `"unmeasured": ["진입점 종료 코드(조회 실패: TimeoutExpired)"]`
- **기준 위반**: `references/phases.md` C-1 "프로세스 종료 코드와 로그가 일치하는가",
  C-4 "재발은 항상 P0 보고 대상". 실질 위험은 관측 손실이므로 P1에 둔다.
- **영향**: `schtasks` 조회가 타임아웃이라 **OS가 뭐라고 했는지 모른다.** 이 축이 없으면
  "로그는 정상 종료, OS는 비정상" 같은 형태를 영원히 못 잡는다. 오늘로 3거래일째 미측정.
- **신규 여부**: **재발** — `daily-axes-measured` 오늘 위반, `exit-code-matches-log` 08-11 위반.

#### 1-4. 상위 Horizon 버킷 유실 7건 — `composer-bucket-completeness` 재발 (원인은 1-1)

- **증상**: `late_bar_drops 7`(늦은 봉 2 · 미완 확정 5). 08-10~08-12 3거래일 연속 **0건**이었다.
- **근거**: `daily_integrity_20260813.json` `late_bar_drops: 7` ·
  `status_snapshot.json` `"l1.composer": {"level":"WARN","detail":"버킷 손실 7건(늦은 봉 2 · 미완 확정 5) — 최악 5m 252계약 유실(0.3%)"}` ·
  발생 시각 15:20:06 / 15:21:00 / 15:21:06 / 15:30:06 — **전부 1-1의 결손 구간 안**.
- **기준 위반**: 마스터플랜 Ver2.0 "거래량 항등식" — 상위봉이 1분봉 합과 같아야 한다.
- **영향**: 5m 최악 252계약(0.3%) 유실. **장후 재합성으로 회복 가능한 부분과 아닌 부분이 갈린다** —
  15:19까지의 봉은 `run_recompose.py`가 이미 재합성했고(292행), 15:20~15:35은 원본이 없어 회복 불가.
- **신규 여부**: **재발**하되 **원인이 다르다.** 종전 재발은 합성기 타이밍 문제였고 오늘은 입력 자체가 없었다.
  1-1을 고치면 이 항목은 따라 사라진다. **독립 fix를 만들지 않는다.**

#### 1-5. 목위클리 캘린더 위반 84건 — `thursday-weekly-listing-calendar` 재발 (이월 · 예상된 재발)

- **증상**: `OptionChainCalendarViolation` 08:23:20~15:18:20 **84건**, l1 ERROR 84건의 **전량**.
- **근거**: `daily_integrity_20260813.json` `series_findings`:
  `"option_chain/weekly_thu: 미상장으로 판정했는데 168분치가 쌓였다 ... (미상장(먼슬리 만기 주(08-13 만기) — KRX 미상장) · 08-14 재개 예정)"`
- **기준 위반**: 장전 보고서 이상점 1-1(P1, 확정)과 동일. 장전 F-1(캘린더 판정식) **미적용**.
- **영향**: 다른 ERROR가 섞여도 안 띈다 — 장중 점검이 지적한 "시끄럽다 → 가린다" 전환이 종일 유지됐다.
  **오늘 1-1의 15:23~15:33 `OptionChainSkipped` 5건이 이 84건 사이에 묻혀 있었다.** 실증됐다.
- **예측 대조**: 장중 W-5는 "종일 약 85건 ±5"를 예상했고 실측 **84건** — 주기 외 요인 없음, 5분 주기 그대로.
- **신규 여부**: **재발**(장전 항목 이월). 장전 F-1 미적용이므로 **결함의 재발이 아니라 미조치의 지속**이다.

---

### P2 — 운영 부담·기술부채

#### 1-6. `px_max_ret_60`이 10m에서 세션 내내 상수 — `no-degenerate-features` 재발

- **근거**: `15:35:00 [WARNING] FeatureHealthDegenerate "10m 피처 1개가 세션 내내 죽어 있었다 — 항상NaN [] · 상수 ['px_max_ret_60']"` (표본 40) ·
  `degenerate_features: {"10m": {"constant": ["px_max_ret_60"]}}` — 다른 5개 Horizon은 퇴화 0건.
- **기준 위반**: SYSTEM.md **금지계명 6**(피처 불일치 침묵 금지). 경보는 났으므로 침묵은 아니나, 입력은 죽은 채 들어갔다.
- **영향**: 10m 모델에 정보량 0인 입력 1개. `allowed_constant_values`에 없으므로 의도된 상수가 아니다.
  60분 최대수익률을 10m(40표본, 창 6봉)에서 재려면 창이 60분을 못 덮는 구조적 문제일 가능성.
- **신규 여부**: **재발**(오늘 위반).

#### 1-7. `OrderSubmit` 태그가 "주문을 안 냈다"에 붙는다 — 태그와 사실 불일치

- **근거**:
  ```
  15:24:00 [INFO] OrderSubmit  "gateway halted: circuit breaker confirmed"  reason="circuit breaker confirmed"
  ```
  `logs/g2_daily_20260813.log:74`. 한편 `self_eval_2026-08-13.json`은 `"주문 0건"`,
  `daily_integrity` `tag_counts`는 `"OrderSubmit": 1`. **같은 하루를 두 산출물이 1과 0으로 센다.**
- **기준 위반**: SYSTEM.md **R6**(태그 1개 = 심각도 1개, 확장하여 태그 1개 = 사실 1개).
- **영향**: 주문 건수를 태그로 세는 어떤 집계도 틀린다. 지금은 주문이 0이라 눈에 띄지만, 실주문이 섞이면 구분 불가.
- **신규 여부**: 신규(오늘 CB가 처음 확정돼 드러났다).

#### 1-8. 미커밋 179건 — 장전·장중과 변동 없음 (이월)

- **근거**: 다이제스트 §1 · 기동 자가점검 `[OK] git dirty(dev 허용)`. 08-12 174건 → 08-13 179건(+5), 종일 변동 0.
- **기준 위반**: `dev` 모드라 **금지계명 10 위반 아님**. 장전 제안(paper 승격 차단 조건으로 격상) 유지.
- **신규 여부**: 기존 항목(`NEXT_TODO.md` "미커밋 179건 범위 확인").

---

### 오탐으로 판정 — 결함 아님 (사람의 헛수고 방지용)

| 자동 적신호 | 판정 | 근거 |
|---|---|---|
| #19 `postmarket` SessionStart 2회 (15:45:02, 15:46:09) | **오탐** | 15:46:09는 5/5 단계가 띄운 `daily_integrity_report.py` **자식 프로세스**가 같은 로그에 찍은 것. `postmarket_20260813.log:52`가 `=== 5/5` 줄 바로 다음이다. 중복 기동 아님 |
| #5~#18 `g2_daily` 로그 공백 14건 | **오탐** | g2는 30분 카덴스 구동. 장중 점검 이상점 1-3과 동일. 장중 F-4(공백 임계를 구동 주기에 맞춤) 미적용이라 예상된 오탐 |
| #2·#4 기동 창 거절 (07:03) | **정상** | `LaunchWindowRefused` — 설계대로 정시 트리거에 양보. `9a4d4ea`가 이미 기동으로 안 세게 고쳤다 |
| 기동 자가점검 "20260812 장후 배치가 SessionEnd를 안 남겼다" | **오탐** | 장전 보고서에서 이미 거짓으로 판정. 오늘도 4회 전 기동에서 동일 문구 반복 — **G-3(검사 도입 시각 하한)** 미적용의 잔재 |

### 오늘 해소된 미결 항목 — 장전·장중의 "확인 필요"에 낸 결론

| ID | 장중까지의 상태 | **장후 결론** | 근거 |
|---|---|---|---|
| **W-1** | 결함 ①(첫 판단이 국면 없이 접힘) 범위 미확정 | **확정 — 첫 사이클 단건** | `daily_integrity.decision_funnel = {"regime": 1, "score": 13}`. `gate=regime`이 09:00 단건. 더 넓게 틀린 것 아님 |
| **W-2** | `\|S\|=0.000` 7연속 — 원인 미확정 | **정황 강화, 확정은 아직** | 종일 `DecisionEmitted` 14건 중 13건이 **문자열까지 완전 동일**한 `④ \|S\|=0.000 < 0.2`. 분산 0 → `n_experts=0` 가설 강화. **확정은 F-1(값 계측) 적용 후 W-6** |
| **W-3 ★** | V-9 장중 잠정 통과(12.5%) | **통과 확정** | `regime_distribution = HIGH_VOL 5 · RANGE 8 · TREND_DOWN 1` · **UNKNOWN 0%**(어제 100%). `9170ce8`(RegimeRuntime 웜스타트) **라이브 검증 성립** |
| **W-4** | 수급 재시도 예산 여유 0 (장전 관측 ③) | **장전 창의 성질로 확정** | `InvestorFlowPollRetried` 종일 **4건**, 전부 08:36~08:51. `InvestorFlowPollError` 0건. `flow_intraday/K2I` 커버리지 **99.8%** · 434분 08:21~15:34. **수급 F-3 긴급도 하향 확정** |
| **W-5** | 캘린더 위반 ~85건 예상 | **84건 — 궤도 내** | 5분 주기 × 425분. 주기 외 요인 없음 |
| **V-7** | F-5(배치 SessionEnd) 첫 채점 | **통과** | `postmarket_20260813.log` `SessionEnd` 1건 · `steps_planned=5 steps_run=5 steps_failed=0`. `3720e31` 라이브 검증 성립 |
| **V-8** | `late_bar_drops`·`missing_minutes` 둘 다 0 기대 | **실패** | `late_bar_drops 7` ❌ / `missing_minutes 0`(공통 구간 기준) ✅ — 이상점 1-4 |
| **V-10** | `regime_distribution` 수록 여부 | **통과** | 2개 이상 상태(3종) · `미측정` 아님 |
| **V-6** | `InvestorFlowPollError` 0 유지 | **통과** | 태그 0건 |
| **V-3** | `OptionChainSeriesMissing` 0건 | **0건 유지** | 다만 `series_findings`가 "미상장 판정인데 168분치 수신"을 독립으로 잡았으므로 **장전 F-1 착수 조건은 이제 충족**된다 |
| **F-5 관측 공백** | `ui-restart-observability` / `launch-window-refusal-not-counted` | **오늘은 위반 없음** | `observation_gaps: []`. 재발 표시는 **08-11 위반의 재인용**이지 오늘 위반이 아니다 |

### 확인 필요 (확정 아님)

- **15:20 이후 틱 부재의 책임 소재 — 우리 회선인가, 브로커/거래소 시세 공급인가.**
  - 우리 쪽이라는 정황: 같은 시각 **REST는 살아 있었다** — `flow_intraday/K2I`가 15:34까지 1분 카덴스로 434분 수신(커버리지 99.8%). 회선·인증·프로세스가 다 정상인데 WS 틱만 죽었다.
  - 브로커 쪽이라는 정황: `verify_archive_volume`이 조회한 **공식 분봉도 395분**(15:19까지)이었다 — `ratio 1.000 · 공통 395분 · 공식 395분`. 거래소 원본에도 15:20 이후가 없다는 뜻으로 읽힌다.
  - **무엇을 보면 판정되는가**: 2026-08-14 장전에 **같은 API로 08-13 분봉을 재조회**한다.
    420분(08:45~15:44)이 나오면 → 15:45:10 조회 시점에 마감 데이터가 아직 안 실렸던 것이고 **우리 수집 결함 확정**.
    395분 그대로면 → 브로커 시세 공급 문제. → **W-9**로 등록.
  - **어느 쪽이든 이상점 1-1의 워치독 사각지대는 확정 결함이다.** 데이터가 왜 안 왔든, 안 온 것을 아무도 안 외쳤다.
- **소급 불가 손실 예산 초과** — `IrrecoverableLossBudgetExceeded` "4거래일에 61분 (> 예산 20분) · 최대 08-10 41분(67%)".
  `cd394bb`(지배일 표시 + 분모 정정)가 이미 손댄 항목이고 오늘 10분이 더해졌다.
  **오늘 10분은 1-1이 원인이므로 1-1 fix로 흡수된다.** 예산 규칙 자체의 재조정은 08-10 41분이 창에서 빠지는 08-17 이후 재판정.
- **`px_max_ret_60`의 10m 상수가 창 길이 문제인지 계산 버그인지** — 다른 5개 Horizon은 정상이다.
  피처 정의(창 60분)와 10m 봉 40개의 관계를 코드로 확인해야 판정된다.

### 장후 체크리스트 통과 현황 (`references/phases.md` C절)

| 항목 | 결과 |
|---|---|
| C-1 종료 시퀀스 | `l1_daily` 15:36:28 정상 종료 · `g2_paper` 15:35:00 정상 종료 · `Messiah-Shutdown` 15:40:01 동작 · `abnormal_exits: []` · Forced Flat 불필요(포지션 0) · **종료 코드 판정 불가**(이상점 1-3) |
| C-2 장후 배치 | 5/5 완주 · `steps_failed 0` · 로그 20.4KB(정상 규모) · 5/5만 ⚠(리포트가 볼 것을 찾음 = 설계대로) |
| C-3 산출물 정합 | 6종 전부 생성 · `horizon_findings []` · `volume_check ok:true` · `vol_scorecard` 3 Horizon 전부 `measurable:true` · **`late_bar_drops 7` ❌** |
| C-4 수정 검증 | `FixVerificationPassed` 11 · `FixVerificationRecurred` 8(오늘 위반 4 · 과거일 재인용 4) · R18 섀도 승격 대상 없음(shadow 0개) |
| C-5 기록 의무 | dev_memory 양쪽 오늘 갱신됨 · 당일 커밋 없음 · 미커밋 179건(dev, 계명 10 비위반) |

---

## 2. Fix 작업 구현계획

### F-1. 재연결 후 "첫 틱 시한"을 워치독에 건다 — **P0** · 대응 이상점 1-1

- **원인 가설**: `TickStallWatchdog.reset()`이 `_last_tick_at=None`으로 되돌리고,
  `run_until_stalled()`이 `_last_tick_at is None`을 **무기한 워밍업**으로 해석한다.
  콜드스타트(08:20 기동, 첫 틱 08:45)를 위해 넣은 면제가 **재연결 경로에도 그대로 적용**된 것이 결함이다.
  콜드스타트와 재연결은 다르다 — 재연결은 이미 "이 시장은 틱이 흐른다"를 알고 하는 것이다.
- **변경 파일**:
  - `src/messiah/data/collector.py` — `TickStallWatchdog.__init__`: `self._reset_at: float | None = None` 추가.
  - 〃 `TickStallWatchdog.reset()`: `self._last_tick_at = None` 옆에 `self._reset_at = self._monotonic()` 기록.
    콜드스타트(생성 직후 첫 `reset()` 이전)와 구분하기 위해 `__init__`에서는 `None`으로 둔다.
  - 〃 `TickStallWatchdog.mark_tick()`: 첫 틱을 받으면 `self._reset_at = None`으로 해제.
  - 〃 `TickStallWatchdog.run_until_stalled()` — `if self._last_tick_at is None:` 분기를 아래로 교체:
    ```python
    if self._last_tick_at is None:
        if self._reset_at is None:
            continue                      # 진짜 콜드스타트 — 종전 동작 유지
        since_reset = self._monotonic() - self._reset_at
        if since_reset < self._reconnect_first_tick_grace_seconds:
            continue
        self._consecutive_stalls += 1
        self._ticks_since_stall = 0
        mlog.log(
            "CollectorReconnectNoTick",
            f"재연결 {since_reset:.0f}초가 지나도록 첫 틱이 없다 — 강제 재연결"
            f"(유예 {self._reconnect_first_tick_grace_seconds:.0f}초)",
            since_reconnect_seconds=since_reset,
            consecutive_stalls=self._consecutive_stalls,
            **({"symbol": describe} if describe else {}),
        )
        raise TickStallError(f"재연결 후 {since_reset:.0f}초간 첫 틱 없음")
    ```
  - `src/messiah/core/logging.py` — 태그 등록: `"CollectorReconnectNoTick": logging.WARNING`
    (기존 `CollectorTickStall`을 재사용하지 않는다 — 사유가 다르면 태그도 달라야 한다, R6).
  - `configs/instance.yaml` — `collector.reconnect_first_tick_grace_seconds: 60` 신설(하드코딩 금지, R4).
    60초 근거: 오늘 정상 구간 `recent_max_gap_seconds 12.6초`, `TickDeliveryLatency` 최대 1.371초 —
    60초는 정상 침묵의 4배 이상이라 오탐 여지가 없다.
- **회귀 위험**:
  - 08:20 기동~08:45 첫 틱 구간에서 오탐이 나면 **매일 아침 헛 재연결**이 걸린다.
    → `_reset_at`을 `__init__`이 아니라 `reset()`에서만 세팅해 콜드스타트를 명시적으로 면제한다.
  - 재연결이 반복 실패하면 지수 백오프와 겹쳐 재연결 폭주 가능 → `_consecutive_stalls` 페널티가
    `current_timeout_seconds()`를 2배씩 늘리는 기존 경로가 그대로 흡수한다(수정 불필요).
- **검증 방법**:
  - `pytest tests/ -k "stall or watchdog or collector"` — 기존 케이스 전건 통과.
  - 신규 테스트 3건 (`tests/data/test_collector_stall.py`):
    ① 콜드스타트(첫 `reset()` 전)에서는 유예가 지나도 발화하지 않는다
    ② `reset()` 후 유예 초과 + 틱 0건 → `TickStallError` + `CollectorReconnectNoTick` 1건
    ③ `reset()` 후 유예 내 `mark_tick()` → 발화하지 않고 `_reset_at`이 `None`이 된다
  - replay: 08-13 15:19~15:35 구간을 틱 0건으로 재생해 재연결이 최소 2회 걸리는지.
  - **다음 거래일 관측**: `CollectorReconnectNoTick` 0건이 기본. 뜨면 그 시각에 실제 무틱이 있었는지 대조.
- **적용 시점**: **오늘 장후(즉시)**. 내일 08:20 정시 기동이 새 코드를 태운다.
- **결정 필요 사항**: 유예 60초 vs 90초. **권고 60초** — 오늘 손실이 15분이었고, 유예가 길수록 그만큼 늦게 소리친다.

### F-2. "재연결 성공"을 구독 성공이 아니라 첫 틱으로 판정한다 — **P0** · 대응 이상점 1-1

- **원인 가설**: `run_forever()`의 `_on_connected` 훅이 **구독 응답**에서 호출되고,
  거기서 `CollectorWSReconnected "수신 재개"`를 찍는다(`collector.py:404`, `collector.py:737` 2곳).
  구독 성공과 수신 재개는 다른 사실인데 한 문장이 겸하고 있다.
- **변경 파일**:
  - `src/messiah/data/collector.py` — `TickCollector.run_forever._on_connected`(:396~406)과
    `MultiFeedCollector.run_forever._on_connected`(:729~741) **두 곳**:
    문구를 `"WS 재구독 성공 — 첫 틱 대기"` 로 바꾸고 태그를 `CollectorWSResubscribed`(INFO)로 분리.
  - 〃 `_note_first_tick` 경로(:519, :797) — `first`이고 직전에 `reset()`이 있었다면
    `CollectorWSReconnected "수신 재개 — 재연결 후 첫 틱"`을 **여기서** 발화(INFO).
    `CollectorFirstTick`은 지금 형태 유지(콜드스타트·재연결 공용, `received_kst` 그대로).
  - `src/messiah/core/logging.py` — `"CollectorWSResubscribed": logging.INFO` 등록.
  - `src/messiah/ops/integrity_report.py::analyze_data_flow_ownership` — 규칙 1을 두 갈래로:
    ```python
    resubscribes = tag_counts.get("CollectorWSResubscribed", 0)
    reconnects   = tag_counts.get("CollectorWSReconnected", 0)   # 이제 "첫 틱까지" 의미
    if stalls > 0 and (resubscribes + reconnects) == 0:
        findings.append(f"L1 스톨 감지 {stalls}회인데 재접속 시도 흔적 0건 — 탐지는 됐으나 복구 시도 없음")
    if resubscribes > reconnects:
        findings.append(
            f"L1 재구독 {resubscribes}회 대 수신 재개 {reconnects}회 — "
            f"{resubscribes - reconnects}회가 구독만 되고 틱이 안 돌아왔다"
        )
    ```
    (오늘 값으로 계산하면 재구독 1 · 수신 재개 0 → **오늘 리포트가 이 형태를 잡았을 것이다.**)
- **회귀 위험**: `CollectorWSReconnected`를 세는 기존 소비자가 의미 변화를 못 따라간다.
  → 소비자는 `ops/integrity_report.py` 한 곳뿐임을 확인했다(`grep -rn CollectorWSReconnected src/` 결과 3곳: 로깅 정의·collector 2곳·integrity 1곳). 함께 고친다.
- **검증 방법**: `pytest tests/ops/test_integrity_report.py -k "data_flow or ownership"` +
  신규 케이스 "재구독 1 · 수신재개 0 → findings 1건". 08-13 로그로 회귀 재생 시 `data_flow_findings`가 2건(기존 CB 1 + 신규 1)이 되어야 한다.
- **적용 시점**: **오늘 장후**, F-1과 같은 커밋.
- **결정 필요 사항**: 없음.

### F-3. `status_snapshot`의 손실 축이 컴포넌트 CRITICAL을 읽게 한다 — **P1** · 대응 이상점 1-2

- **원인 가설**: `irrecoverable_loss`는 **기동 지연·계열 결손**만 세고, 장중에 진행 중인 무틱 구간을 모른다.
  `lost_items: 0`이 "없었다"와 "안 셌다"를 겸한다.
- **변경 파일**:
  - `src/messiah/ops/status_board.py` — `snapshot()`(:134~): `irrecoverable_loss` 블록에
    `"live_critical_components": [name for name, c in components.items() if c["level"] == "CRITICAL"]` 추가.
    비어 있지 않으면 `clean=false`, `summary`를 `"진행 중 CRITICAL {n}건 — 손실 확정은 장후"`로 교체.
  - 〃 `format_snapshot()`(:263~) — 위 문구를 화면에 노출.
  - `src/messiah/ops/integrity_report.py` — 장후 산출 시 `status_snapshot`의 `clean`과
    자기 `irrecoverable_loss_minutes`가 어긋나면 `unmeasured`가 아니라 `breaches`에 넣는다:
    `"장중 스냅샷은 손실 없음이라 했는데 장후 계산은 {n}분 — 관측 축이 서로 다르다"`.
- **회귀 위험**: 콜드스타트 구간(08:20~08:45)에 collector가 CRITICAL이면 매일 아침 `clean=false`가 뜬다.
  → `first_tick_overdue()`가 09:00 시한을 이미 걸고 있으므로 08:45 이전 CRITICAL은 발생하지 않는다(오늘 실측 확인).
- **검증 방법**: `pytest tests/ops/test_status_board.py` + 신규 케이스 "컴포넌트 CRITICAL 1건 → clean False".
  **다음 거래일 관측**: 정상일이면 `clean: true` 유지(오탐 0).
- **적용 시점**: 오늘 장후, 커밋 ②.
- **결정 필요 사항**: 없음.

### F-4. `schtasks` 조회 타임아웃을 늘리고, 실패를 `미측정`이 아니라 재시도로 흡수한다 — **P1** · 대응 이상점 1-3

- **원인 가설**: `schtasks /query`가 기본 타임아웃 안에 안 끝난다. 3거래일 연속 같은 실패다.
- **변경 파일**:
  - `src/messiah/ops/integrity_report.py` — 종료 코드 조회 함수(`task_exit_codes` 생성부):
    `subprocess.run(..., timeout=…)`을 설정값으로 빼고 **1회 재시도**를 넣는다.
    `configs/instance.yaml`에 `ops.schtasks_timeout_seconds: 30`(현행 추정 5~10초) · `ops.schtasks_retries: 1`.
  - 〃 실패 시 `detail`에 **경과 초와 시도 횟수**를 남긴다 — 다음번에 "타임아웃이 얼마였는지"를 알기 위해.
  - 〃 `/fo CSV /nh` 로 출력 형식을 고정해 파싱·출력량을 줄인다(현재 형식 미고정이면 XML 조회가 느린 원인일 수 있다).
- **회귀 위험**: 타임아웃을 늘리면 장후 배치가 그만큼 늦어진다. 현재 1~2분 소요 → 최악 +30초. 허용 범위.
- **검증 방법**: `python scripts/daily_integrity_report.py --date 20260813 --symbol A05608 --configs configs`
  재실행으로 `task_exit_codes.available`이 `true`가 되는지 즉시 확인 가능(**오늘 바로 채점된다**).
- **적용 시점**: 오늘 장후, 커밋 ③.
- **결정 필요 사항**: 타임아웃 30초 vs 60초. **권고 30초** + 재시도 1회 — 총 최악 60초.

### F-5. `OrderSubmit` 태그를 사실대로 가른다 — **P2** · 대응 이상점 1-7

- **변경 파일**:
  - `src/messiah/broker/`(또는 `strategy/`)의 OrderGateway 정지 경로 — 게이트 정지로 제출을 안 했을 때
    `OrderSubmit` 대신 **`OrderBlocked`**(INFO)를 찍는다. `reason` 필드는 유지.
    (정확한 위치는 `grep -rn '"OrderSubmit"' src/messiah/` 로 확정 후 착수 — 계획 단계에서 파일을 단정하지 않는다.)
  - `src/messiah/core/logging.py` — `"OrderBlocked": logging.INFO` 등록.
  - `src/messiah/ops/integrity_report.py` — 주문 건수 집계가 `OrderSubmit`을 쓰는 곳이 있으면 그대로 두면 된다(이제 정확해진다).
- **회귀 위험**: `OrderSubmit`을 세는 대시보드/집계가 있으면 값이 바뀐다 — 바뀌는 게 맞는 방향이다.
- **검증 방법**: `pytest tests/ -k "order and (gateway or blocked)"` + 다음 CB 발생일에
  `self_eval`의 `주문 N건`과 `tag_counts["OrderSubmit"]`이 **같은 값**인지.
- **적용 시점**: 오늘 장후, 커밋 ④(선택 — P0/P1 다음).
- **결정 필요 사항**: 없음.

### 착수하지 않는 것 (판단 근거를 남긴다)

- **이상점 1-4(`late_bar_drops` 7건)** — F-1이 원인을 없앤다. 독립 fix를 만들면 증상만 가린다.
- **이상점 1-5(캘린더 84건)** — 장전 F-1이 이미 계획된 항목. 중복 착수하지 않는다.
- **이상점 1-6(`px_max_ret_60`)** — 원인이 창 길이인지 버그인지 미확정. **조사 먼저**(W-11).
- **장중 F-1~F-4(판단 관측·갈래 분리·국면 시드·공백 임계)** — 장중 점검이 이미 세운 계획.
  오늘 P0가 새로 생겼으므로 **순서만 뒤로 민다**, 내용은 그대로 유효하다.

### 적용 순서와 커밋 계획

| # | 포함 항목 | 커밋 메시지 초안 |
|---|---|---|
| ① | F-1 + F-2 | `[MW0601] 재연결은 됐고 틱은 없었다 — 첫 틱 시한 + 재구독/수신재개 분리 (P0)` |
| ② | F-3 | `[MW0601] 스냅샷이 자기 CRITICAL을 못 읽었다 — 손실 축에 컴포넌트 상태 결선` |
| ③ | F-4 | `[MW0601] 종료 코드를 3일째 못 물었다 — schtasks 타임아웃·재시도` |
| ④ | 장중 F-1 + F-2 (판단 갈래 계측·`n_experts` 분리) | `[MW0601] 나머지의 판정 근거 — 판단 갈래 값 계측` |
| ⑤ | 장중 F-3 (국면 시드) + 장중 F-4 (공백 임계) | `[MW0601] 첫 사이클도 국면을 보게 한다 + 점검 도구 공백 임계` |
| ⑥ | F-5 | `[MW0601] 안 낸 주문을 OrderSubmit으로 세지 않는다` |

각 커밋 전 `pytest`(해당 범위) + replay — 금지계명 2. 커밋 전 반입 금지 — 금지계명 10.

> **본 예약 실행은 보고까지만 한다.** 위 fix는 사용자가 "구현해"라고 지시했을 때 착수한다.

---

## 3. 고도화 방안

### G-1. 워치독에 "복구가 실제로 효과가 있었는가"를 세는 축을 둔다

- **관측 근거**: 오늘 강제 재연결이 1회 걸렸고 `CollectorWSReconnected`도 1건 찍혔다.
  기존 무결성 규칙(`analyze_data_flow_ownership` 규칙 1: "스톨 N회인데 재연결 0회")은
  **1 대 1이라 통과시켰다.** 복구 시도 횟수는 셌지만 복구 결과는 안 셌다.
- **제안 내용**: `daily_integrity`에 `recovery_efficacy` 블록 신설 —
  `{"stalls": n, "resubscribes": n, "first_tick_after_reconnect": n, "median_recovery_seconds": x, "unrecovered": n}`.
  `unrecovered > 0`이면 `breaches`. 같은 구조를 CB(`confirmed`/`resumed`)에도 적용해
  **"탐지 대 복구"를 한 표로 본다.**
- **기대 효과**: 오늘 값은 `stalls 1 · resubscribes 1 · first_tick_after_reconnect 0 · unrecovered 1` —
  숫자 한 줄로 P0가 드러난다. 사람이 로그 시각을 재구성하는 데 오늘 걸린 시간은 30분이었다.
- **비용·위험**: 집계만 하므로 런타임 위험 0. R18 섀도 계측 불요(차단 로직 아님).
- **선행 조건**: F-2(태그 분리).
- **우선순위 제안**: **이번 주**.

### G-2. `verify_archive_volume`이 "공통 구간"이 아니라 "기대 구간"으로 채점한다

- **관측 근거**: 오늘 15분이 통째로 없는데 거래량 대조는 `비율 1.000 · OK · 전 구간 정상`을 냈다.
  `공통 395분 · 공식 395분`이었기 때문이다. **양쪽에 똑같이 없으면 없는 줄 모른다.**
  `volume_check_20260813.json`의 `missing_minutes: 0`·`tail_missing_minutes: 0`도 같은 이유로 0이다.
- **제안 내용**: 거래일 캘린더가 정한 **기대 분(選 08:45~15:44, 420분)** 을 분모로 세우고,
  `expected_minutes` · `common_minutes` · `expected_but_absent_both` 세 값을 나란히 낸다.
  양쪽 다 없으면 `OK`가 아니라 **`판정 불가 — 공식 데이터도 없음`** 으로 표시한다(`references/phases.md` D절 "건수 0은 두 가지다").
- **기대 효과**: 오늘 산출이 `OK`가 아니라 `기대 420분 · 공통 395분 · 양쪽 부재 25분 — 판정 불가`가 된다.
  **이상점 1-1의 "확인 필요"가 자동으로 매일 채점된다.**
- **비용·위험**: 정규장 종료 시각을 캘린더에서 정확히 가져와야 한다(최종거래일·조기폐장 예외).
  잘못 잡으면 매일 오탐 → `configs/krx_holidays.yaml` 옆에 세션 시간표를 두고 예외를 명시.
- **선행 조건**: 없음. `scripts/verify_archive_volume.py` 단독 변경.
- **우선순위 제안**: **이번 주** (F-1보다 뒤, G-1과 병행 가능).

### G-3. 장중 산출물에 "지금 진행 중인 사고" 한 줄을 둔다

- **관측 근거**: 15:34:47 스냅샷은 CRITICAL 2건을 **담고 있었다.** 정보는 있었고 요약이 없었다.
  최상위 키 `code_version`·`components`·`circuit_breaker`·`irrecoverable_loss`·`command_center_ui` 중
  **"지금 괜찮은가"에 한 문장으로 답하는 키가 없다.** 장중 점검 G-1(`decision.funnel` 추가)과 같은 축의 다른 결핍이다.
- **제안 내용**: `status_snapshot.json` 최상위에 `verdict` 블록 —
  `{"ok": bool, "worst_level": "CRITICAL", "reasons": ["l1.collector: 첫 틱이 없다", "l1.feature_engine: 281초간 발행 없음"], "since_kst": "15:22"}`.
  `since_kst`는 그 상태가 시작된 시각 — **지속시간이 곧 손실량**이기 때문이다.
- **기대 효과**: 사람이든 점검 스킬이든 파일 첫 줄만 읽고 판정한다.
  오늘 12:30 장중 점검이 15:20 사고를 못 본 것은 시각 때문이지만, 같은 일이 10:00에 나면 이 한 줄이 잡는다.
- **비용·위험**: 표시 계층만. 위험 0. 단 `worst_level` 산출이 컴포넌트 등급 정의에 의존하므로
  `state`(하트비트 신선도)와 `level`(Health 페이로드)을 섞지 않도록 주의(L18 — 두 축을 화면이 합쳐 말하면 안 된다).
- **선행 조건**: F-3(손실 축 결선)과 같은 함수를 건드리므로 함께 한다.
- **우선순위 제안**: **이번 주**.

### 로드맵 반영 제안

| 항목 | 현 로드맵 위치 | 제안 위치 | 사유 |
|---|---|---|---|
| F-1·F-2 (재연결 후 첫 틱 시한) | 미등재 | **W-단계 최상단, 즉시** | 데이터 수집 신뢰성의 최하층. 이게 없으면 그 위 전부가 "언제 구멍 났는지 모르는 데이터" 위에 선다 |
| G-2 (기대 구간 채점) | 미등재 | 이번 주 | 오늘 `OK` 판정이 사고를 통과시켰다 — 채점기가 사고를 못 보면 채점이 아니다 |
| 장중 F-1·F-2 (판단 갈래 계측) | 장중 점검 계획 | 순서 유지, **커밋 ④로 이동** | 오늘 P0가 앞자리를 가져갔다. 내용의 긴급도는 변함없음 |
| paper 승격 조건 | 마스터플랜 Ver2.0 | **미커밋 0건을 차단 조건에 추가** | 179건이 3거래일째. dev에선 무해하나 승격 시점에 계명 10이 바로 걸린다 |

---

## 4. 다음 거래일 관측 예정

| ID | 관측 대상(태그/지표) | 판정 기준 | 판정 예정일 |
|---|---|---|---|
| **W-9 ★** | 08-13 분봉을 같은 API로 재조회한 분 수 | **420분 → 우리 수집 결함 확정** / 395분 → 브로커 시세 공급 문제 | 2026-08-14 장전 |
| **W-10** | (F-1 적용 후) `CollectorReconnectNoTick` | **0건**. 뜨면 그 시각의 실제 무틱 여부 대조 | 2026-08-14 장후 |
| **W-11** | `px_max_ret_60` 10m 상수 — 피처 정의 창 길이 조사 | 창 60분 < 10m×6봉이면 정의 문제 확정 | 2026-08-14 (코드 조사, 로그 무관) |
| **W-12** | (F-4 적용 후) `task_exit_codes.available` | **`true`** · `unmeasured` 0건 | 2026-08-13 재실행으로 즉시 / 2026-08-14 장후 확정 |
| **W-13** | (F-3 적용 후) 정상일 `status_snapshot.irrecoverable_loss.clean` | **`true` 유지**(오탐 0) | 2026-08-14 장중 |
| **V-4** (장전 이월) | `thursday_weekly_listed(08-14)` · `OptionChainCalendarViolation` | 재개일이면 **0건**. 그래도 뜨면 판정식이 하루가 아니라 더 넓게 틀렸다 | 2026-08-14 장전 |
| **W-6** (장중 이월) | (장중 F-1 적용 후) `DecisionEmitted`의 `n_experts` | **0 → "입력 없음" 확정 / 1↑ → "진짜 우위 없음" 확정** | 장중 F-1 적용 후 첫 거래일 |
| **W-7** (장중 이월) | (장중 F-3 적용 후) 09:00 `DecisionEmitted`의 `gate` | **`regime`이 아닐 것** + `RegimeSeeded` 1건 | 장중 F-3 적용 후 첫 거래일 |
| **W-14** | 소급 불가 손실 4거래일 창 | 08-10 41분이 창에서 빠지는 08-17 이후 재판정 | 2026-08-17 |

---

## 5. 재시동 권고

**권고: 지금 재시동하지 않는다. 대신 F-1·F-2를 오늘 커밋한다.**

손익 비교:

| | 얻는 것 | 잃는 것 |
|---|---|---|
| **재시동 없이** | — (오늘 재시동으로 보존할 관측이 없다: `l1_daily` 15:36:28 · `g2_paper` 15:35:00 이미 정상 종료, 살아 있는 프로세스가 없다) | 없음 |
| **재시동으로** | 없음 — **`code_version.stale = false`** (`process_git_sha e37d387 == head_git_sha e37d387`, 세션 전체가 `session_git_shas: ["e37d387"]`), **당일 커밋 0건**. 새로 태울 코드가 없다 | 장 마감 후 불필요한 프로세스 기동 |

판단 재료 그대로 인용:
```json
"code_version": {"process_git_sha": "e37d387", "head_git_sha": "e37d387",
                 "stale": false, "summary": "코드 e37d387 — 전 프로세스 동일"}
```

- **오늘 로그는 어느 코드의 결과인지 말할 수 있다** — `e37d387` 단일. 그 점에서 오늘 관측은 온전하다.
- 다만 **F-1·F-2를 커밋하면 그 순간 `stale`의 의미가 살아난다.** 내일 08:20 정시 기동이
  자동으로 새 코드를 태우므로 **오늘 밤 커밋 → 내일 아침 자동 적용**이 정상 경로다.
  오늘 커밋만 하고 재기동하지 않는 것에 위험이 없다(살아 있는 프로세스가 없으므로 `stale`이 뜰 대상 자체가 없다).
- **커밋하지 않고 하루를 더 가면** 미커밋이 179건 → 그 이상이 되고, 내일 로그도 `e37d387`의 결과가 되어
  **오늘 세운 P0 fix가 내일도 검증되지 않는다.** 그것이 이 권고의 실질이다.

---

## 6. dev_memory 반영

- `DECISION_LOG.md` 추가 항목: `## 2026-08-13 장후 — 재연결은 됐고 틱은 없었다 ([MW0601], 2026-08-13)`
- `NEXT_TODO.md` 추가 체크박스: **17건** (F-1~F-5 · G-1~G-3 · W-9~W-14 · 완료 처리 3건)
- 완료 처리한 기존 항목: **V-9/W-3**(국면 상수 아님 — `9170ce8` 라이브 검증 성립) ·
  **V-7**(배치 SessionEnd — `3720e31` 라이브 검증 성립) · **W-4**(수급 재시도 긴급도 하향 확정)

---

## 자체 검증

- [x] 장후 배치 완료 확인 후 산출물 판정 — `SessionEnd` 15:47:25 · 5/5 완주 확인 후 착수
- [x] 오늘 장전·장중 보고서의 "확인 필요" 전건에 결론 — W-1~W-5 · V-3·V-6·V-7·V-8·V-10 (표로 정리)
- [x] 모든 이상점에 로그 시각과 인용 — 1-1~1-8 전건
- [x] 각 이상점이 SYSTEM.md 조항 또는 설계 문구에 대응 — R4·R6·R10·계명 6·10·12, `phases.md` C-1/C-4/D
- [x] dev_memory 기존 항목 중복 보고 안 함 — 1-5(캘린더)·1-8(미커밋)은 **이월로 명시**, 신규로 세지 않음
- [x] `FixVerificationRecurred`(8건, 오늘 위반 4 / 과거 재인용 4) · `code_version.stale`(false) · 산출물 누락(없음) 전부 반영
- [x] Fix 계획이 파일·함수 수준 — `collector.py::TickStallWatchdog.run_until_stalled` 등 전건
- [x] 고도화가 당일 관측 근거 — G-1(오늘 1대1 통과) · G-2(오늘 `비율 1.000 OK`) · G-3(15:34:47 스냅샷)
- [x] 재시동 권고를 손익 비교와 함께 — §5
- [x] dev_memory 갱신 — §6, 본 보고서와 함께 반영
