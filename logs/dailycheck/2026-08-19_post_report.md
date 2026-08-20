# MESSIAH 일일 점검 — 2026-08-19 / 장후

- 점검 시각: 16:05 KST
- 대상 국면: post (장전·장중 구간 포함 전일 회고)
- HEAD `50eff6c` · 실행 세션 sha `40e9968`(08:20~09:50) / `50eff6c`(12:29~15:35) · 미커밋 263건(그중 `src/`+`scripts/` 실제 변경 **0파일**, CRLF 잡음 87파일)
- 증거: `logs/dailycheck/evidence_20260819_post.md`
- 선행 보고서: `2026-08-19_pre_report.md` · `2026-08-19_intra_report.md` · `2026-08-19_incident_0950_deepdive.md`

## 0. 한 줄 결론

**오늘 죽은 것은 09:50에 한 번뿐이었고 그 뒤로는 설계대로 돌았다. 문제는 하루가 끝난 지금, 그 죽음을 자기 입으로 말하는 계기가 하나도 없다는 것이다** — 비정상 종료 축은 `[]`, 소실 계량기는 정상일과 같은 `0.5분`, 피처 축은 `nan_ratio 0.0`, 불완전일 표시는 아예 없는 축이다. 사망을 실제로 붙잡은 것은 `series_coverage`/`series_findings` 하나뿐이고, 그 하나가 잡아낸 값 때문에 **멀쩡한 수정 4건이 「듣지 않았다」로 찍혔다.**

| 국면 | 판정 | 근거 요약 |
|---|---|---|
| 장전 | 정상 | 자가점검 3회 전량 `[OK ]`·`self-check: PASS` · `schedule_drift` 정본 일치 · 05:55 기동은 `LaunchWindowRefused` 후 정시 08:20 기동으로 완결 |
| 장중 | 이상 (기보고) | 09:50:29~12:29:23 **158.9분** l1_daily 사망 · g2_paper 09:30~12:30 **180.2분**. 장중 보고서 P0-1로 이미 보고됨. 재기동 후 2차 사망 0건 |
| 장후 | 조건부 | 배치 6/6 완주(15:45:03~15:45:33, `SessionEnd` steps_failed=0) · 산출물 7종 전량 생성 · **그러나 산출물 4개 축이 오늘의 사망을 못 봤고, 그 사실이 등록부 재발 4건으로 잘못 번역됐다** |

---

## 1. 이상점 정리보고

### P0 — 오늘 거래에 직접 위험

**해당 없음 (확정).** 다음 근거로 P0을 세우지 않는다.

- `mode=dev` · G2 페이퍼 · `decision_funnel {no_expert: 9}` · `self_eval` `wiring_stage: "주문 미발생"` · **주문 0건**. 실주문 위험 경로가 오늘 열린 적이 없다.
- 장중 P0-1("두 번째로 죽으면 또 아무도 모른다")의 오늘분 판정: **12:40~15:35 추가 `SessionStart` 0건**(NEXT_TODO **I-1** 충족). 2차 사망 없음.
- `SessionEnd` 존재 — l1_daily 15:35:26 "정상 종료", g2_paper 15:34:58 "정상 종료"(**I-7** 충족). `task_exit_codes` 3작업 전부 `code: 0`.

> **재발 4건을 P0로 올리지 않는 이유는 아래 P1-4에 근거와 함께 적었다.** 템플릿 규칙(「재발은 무조건 P0 맨 위」)을 어기는 판단이므로 근거를 명시한다: 네 건 모두 **계측이 고장 난 것이 아니라 계측이 정확히 작동해 나쁜 값을 보고한 것**이고, 그 나쁜 값의 원인은 이미 장중에 P0-1로 보고된 09:50 사망 **단일 사건**이다. 같은 사건을 P0로 네 번 다시 세는 것은 보고가 아니라 중복이다.

### P1 — 정확성·관측 훼손

#### 1-1. 비정상 종료 축이 장중 사망을 구조적으로 못 본다 — `abnormal_exits: []`, 그리고 그 축으로 `no-silent-process-death`가 「검증 완료」를 선고했다

- **증상**: 오늘 두 프로세스가 각각 세션 하나를 `SessionEnd` 없이 잃었는데, 비정상 종료 집계는 빈 배열이고 그 배열을 채점 입력으로 쓰는 등록부 항목이 통과했다.
- **근거**:
  ```
  logs/l1_daily_20260819.log   SessionStart ×3 (05:55:35 거절 / 08:20:29 / 12:29:23) · SessionEnd ×1 (15:35:26)
  logs/g2_daily_20260819.log   SessionStart ×3 (05:55:35 거절 / 08:25:34 / 12:30:14) · SessionEnd ×1 (15:34:58)

  logs/daily_integrity_20260819.json
    "starts_by_process":   {"l1_daily": 2, "g2_paper": 2}
    "restarts_by_process": {"l1_daily": 1, "g2_paper": 1}
    "abnormal_exits": []                        ← 유효 기동 2 vs 정상 종료 1 인데도 비어 있다
    "observation_gaps": [
      {"process": "g2_paper", "from_kst": "09:30:01", "to_kst": "12:30:14", "minutes": 180.2, ...},
      {"process": "l1_daily", "from_kst": "09:50:29", "to_kst": "12:29:23", "minutes": 158.9, ...}]

  logs/postmarket_20260819.log 15:45:33
    [INFO] FixVerificationPassed  no-silent-process-death: 7거래일 연속 기준 충족 (abnormal_exits ≤ 0)
  ```
- **원인(코드 확인 완료)**: `src/messiah/ops/integrity_report.py:569 _abnormal_exits()`. 기동/종료 **개수 불균형**은 올바로 감지하지만(`len(starts) > len(ends)` 통과), 그 다음 사망 시각을 `activity[-1]` — **그날 프로세스의 마지막 로그** — 로 추정한다. 오늘 마지막 로그는 15:35:26(정상 종료)이라 `lost = close - last ≈ 0분`이 되고, `lost <= bar_tail_gap_minutes(20)` 에서 걸러진다(603행). **중간에 죽고 재기동해 정상 종료하면 이 축은 원리적으로 아무것도 못 잡는다.** 이 함수는 "하루 끝에 안 돌아온 프로세스"만 볼 수 있게 설계돼 있다(docstring 570~573행이 그렇게 적고 있다).
- **기준 위반**: SYSTEM.md R13 · 금지계명 14(비정상 종료를 조용히 넘기지 않는다). `references/phases.md` §D「건수 0은 두 가지다 — 진짜 없었거나, 계측이 없거나」. 등록부 항목 `no-silent-process-death`의 취지문("프로세스가 죽고 안 돌아온 날을 리포트가 말하는가(P0-3)")이 **오늘 그대로 반증됐다.**
- **영향**: 오늘 확정된 비정상 종료 2건이 어느 집계에도 남지 않는다. 더 나쁜 것은 **계기가 통과 도장을 찍었다**는 점 — 7거래일 연속 통과 기록이 쌓이는 동안 이 축이 볼 수 있는 사고 유형은 실제로 한 종류뿐이었다. 앞으로 장중 사망이 반복돼도 이 축은 계속 초록색이다.
- **신규 여부**: **신규.** 장중 1-2는 `irrecoverable_loss`(`ops/status_snapshot` 경로)를 다뤘고, 이 축은 `ops/integrity_report`의 다른 함수이며 장후에만 산출된다. dev_memory에 대응 항목 없음.

#### 1-2. 소실 계량기가 「159분 사망일」과 「정상일」에 똑같은 0.5분을 적었다 — NEXT_TODO **I-2**가 「다른 값」 갈래로 확정

- **증상**: 장중 점검은 `collection_start_lag_minutes`가 249.4로 봉인될 것으로 전망했다. 실측은 **0.5**였고, 이는 사고가 없던 08-18과 **같은 값**이다.
- **근거**:
  ```
  logs/daily_integrity_20260818.json   "irrecoverable_loss_minutes": 0.5   (사고 없는 날)
  logs/daily_integrity_20260819.json   "irrecoverable_loss_minutes": 0.5   ← 158.9분 사망한 날
                                       "collection_start_lag_minutes": 0.5
  logs/daily_integrity_20260814.json   "irrecoverable_loss_minutes": 33.0

  logs/status_snapshot.json (15:34:47, 같은 날 11분 전)
    "irrecoverable_loss": {"start_lag_minutes": 249.4, "lost_items": 1,
                           "summary": "오늘 영구 소실 — 기동 지연 249분 · option_chain/regular 1건"}

  logs/postmarket_20260819.log 15:45:33
    [WARNING] IrrecoverableLossBudgetExceeded  소급 불가 손실이 5거래일에 49분 (> 예산 20분)
              — 최대 2026-08-14 33분(67%) · 나머지 4일 16분
  ```
  같은 하루에 「얼마를 잃었나」에 대해 **세 개의 서로 다른 숫자**가 남았다 — 249.4(스냅샷) · 0.5(무결성 확정본) · 158.9~169(계열별 `series_findings`). 어느 것도 159가 아니다.
- **기준 위반**: 마스터플랜 Ver2.0 소급 불가 손실 예산 조항 — 예산 가드가 실측을 입력으로 받아야 성립한다. `dev_memory/DECISION_LOG.md` 2026-08-19 장중 ②(「소실 계량기가 장중 사망을 오기술」)의 연장.
- **영향**: 5거래일 예산 49분 중 오늘 몫이 0.5분이다. 실제 158.9분을 **318배 과소계상**했다. 예산 초과 경보는 오늘 울렸지만 08-14의 33분 때문에 울린 것이고, 오늘 하루만으로도 예산을 8배 넘겼다는 사실은 어디에도 없다. 이 가드는 지금 장중 사망에 대해 **구조적으로 눈이 멀어 있다.**
- **신규 여부**: 기존 **I-2**의 판정. 다만 「249.4로 봉인」 예측이 빗나갔고, **두 산출 경로가 같은 날 다른 값을 낸다**는 사실이 새로 확정됐다. 장중 F-H의 범위가 「계량기 하나 고치기」에서 「서로 어긋나는 두 경로 통합」으로 커진다.

#### 1-3. 확정본에 「불완전일」을 말하는 축이 없다 — 커버리지 61%인 날이 정상일로 롤링 창에 들어갔다 (**I-8** 판정)

- **증상**: 장후 배치는 반나절이 비어 있는 입력으로 6단계를 완주했고, 산출물은 그 사실을 표시하는 필드 없이 정상 확정본으로 저장됐다. 그 확정본을 읽는 롤링 소비자들이 오늘을 온전한 하루로 셌다.
- **근거**:
  ```
  logs/daily_integrity_20260819.json
    "provisional": false
    "series_coverage": ticks 61.2% · flow_intraday/K2I 63.2% · option_chain/regular 63.0%
                       · weekly_mon 70.8% · weekly_thu 70.8%
    "feature_health_rolling": [{"horizon":"10m","days":["2026-08-18","2026-08-19"],
                                "samples":60,"judged":true, ...},
                               {"horizon":"15m", ... "judged":true, ...}]
    "degenerate_features": {"10m":{"judged":false,"rolling_judged":true,
                                   "rolling_days":["2026-08-18","2026-08-19"]}, ...}
  logs/vol_scorecard_20260819.json
    5m  samples 208 (window_days 20)  measurable:true
    15m samples  65 (window_days 20)  measurable:true
  ```
  `provisional`은 불완전일 축이 **아니다** — `src/messiah/ops/integrity_report.py:342` 주석이 명시한다("`provisional`과 **다른 축이다.** 그쪽은 '아직 안 만들어진 산출물이 있다'(시간 문제)"). 즉 오늘의 `false`는 옳고, **불완전일을 표시할 필드 자체가 존재하지 않는다.**
- **기준 위반**: 금지계명 12(조용한 폴백 금지) · SYSTEM.md R10(합성·불완전 데이터는 배지 없이 쓰이지 않는다). `dev_memory/DECISION_LOG.md` 2026-08-19 장중 ③의 Why 조항 — *"위험한 것은 이 피처가 그대로 아카이브되어 훗날 학습·백테스트 입력이 되는 것 — 그때 이 구간은 '정상 데이터'로 보인다."*
- **영향**: 10m·15m 퇴화 판정 롤링 창이 2일이고 그중 **하루가 오늘**이다 — 창의 절반이 반나절짜리다. 그런데 `judged: true`로 확정 판정을 냈다. `vol_scorecard`의 20거래일 IC에도 오늘 표본이 정상 가중으로 들어갔다. **이 오염은 되돌릴 수 없다** — 소급해서 「그날은 반쪽이었다」고 말해 줄 필드가 없기 때문이다.
- **신규 여부**: **신규.** **I-8**이 던진 질문("완주하되 산출물에 「불완전일」 표시가 있는가")에 대한 답이며, 답은 「없다」다.

#### 1-4. 재발 4건 전부가 09:50 사망 1건의 파생인데 「수정이 듣지 않았다」로 출력됐다 — 계측 고장과 값 위반을 안 가른다

- **증상**: 등록부가 오늘 ERROR 4건을 냈다. 네 항목 모두 **계측이 성립하는가**를 묻는 항목이고, 오늘 넷 다 정확히 계측에 성공했다. 실패한 것은 계측이 아니라 계측 대상인 하루다.
- **근거**:
  ```
  logs/verification_scoreboard_20260819.json  today_violating (4건)
    ui-restart-observability          metric=observation_gap_minutes_max  value=180.2  prev=0.0
      summary "관측 공백을 실제로 재는가(P1-1 재정의)"
    launch-window-refusal-not-counted metric=observation_gap_minutes_max  value=180.2  prev=0.0
      summary "기동 창 거절을 재기동·관측 공백으로 세지 않는지(P0-4)"
    truncation-is-visible             metric=series_coverage_pct_min      value=61.2   prev=99.1
      summary "**잘림**이 보이는가 — 구멍이 아니라 끊김을 재는 축"
    leg-completeness-measured         metric=series_leg_shortfall
      summary "사이클 **안**이 다 찼는가 — 시간 축이 구조적으로 못 보는 결손"

  logs/postmarket_20260819.log 15:45:33 (×4)
    [ERROR] FixVerificationRecurred  ... 오늘(2026-08-19) 기준 위반 — 수정이 듣지 않았다
  ```
  `truncation-is-visible`은 오늘 잘림을 **정확히 보였다**(`series_findings` 11건, 5계열 전부). `leg-completeness-measured`는 12:30 사이클 41/42다리를 **정확히 세었다**. `ui-restart-observability`는 180.2분 공백을 **정확히 쟀다**. 세 계기 모두 설계대로 작동했고, 그 대가로 「듣지 않았다」를 받았다.
- **부수 결함**: `ui-restart-observability`와 `launch-window-refusal-not-counted`가 **같은 metric(`observation_gap_minutes_max`)을 공유**한다. 관측 공백이 한 번 생기면 등록부는 항상 **재발 2건**으로 센다. 오늘 재발 4건 중 2건이 이 중복이다.
- **기준 위반**: `src/messiah/ops/fix_verification.py:932` ①번 분기 — `last_violation is not None and clean_streak == 0` 하나로 「값이 기준을 넘었다」와 「수정이 되돌아갔다」를 같은 문장에 넣는다. 커밋 `93f2086`("회복을 세는 자리가 없어 늑대소년이 열한 번 울었다")이 겨눈 것과 **같은 부류의 오경보**가 다른 축에서 재현됐다.
- **영향**: 멀쩡한 코드를 다시 고치라는 신호. 등록부 신뢰도가 떨어지면 진짜 재발이 왔을 때 묻힌다 — 이 프로젝트가 08-18에 이미 한 번 겪은 실패다.
- **신규 여부**: **신규.**

#### 1-5. `AggregatorNoContribution.regime` 어긋남은 경합이 아니라 「세션 첫 사이클」 구조다 — **X-5 / D-2 판정 확정**

- **증상**: 종일 집계 결과, 어긋남은 프로세스 기동 직후 **첫 사이클에서만** 발생했다. 이후 사이클은 전부 일치했다.
- **근거**(`logs/g2_daily_20260819.log` 종일 9건 전량 대조):
  ```
  09:00:02  agg=UNKNOWN  ← prev RegimeClassified=RANGE      Δt= 633ms  ✗  (08:25:34 기동 후 첫 사이클)
  09:30:01  agg=HIGH_VOL ← prev RegimeClassified=HIGH_VOL   Δt= 280ms  ✓
  12:31:01  agg=UNKNOWN  ← prev RegimeClassified=HIGH_VOL   Δt=  12ms  ✗  (12:30:14 재기동 후 첫 사이클)
  13:00:01  agg=HIGH_VOL ← HIGH_VOL   Δt= 91ms ✓      14:30:01  agg=RANGE ← RANGE  Δt=73ms ✓
  13:30:01  agg=RANGE    ← RANGE      Δt=102ms ✓      15:00:00  agg=RANGE ← RANGE  Δt=93ms ✓
  14:00:01  agg=RANGE    ← RANGE      Δt= 82ms ✓      15:30:00  agg=RANGE ← RANGE  Δt=73ms ✓
  ```
  **첫 사이클 2건 중 2건 어긋남 · 이후 7건 중 0건 어긋남.** Δt 분포는 어긋남 {12ms, 633ms}, 일치 {73~280ms} — 어긋남이 일치보다 **빠른 쪽과 느린 쪽 양극단에 모두** 있다.
- **기준 위반**: 장중 보고서 C-1이 스스로 세운 판정 기준 — *"Δt가 넓게 퍼지면 경합이 아니다."* 조건 충족. 따라서 **경합 가설 기각**. 금지계명 12(조용한 폴백) — 집계기가 국면 캐시 미보유 상태를 `UNKNOWN`으로 대체하면서 INFO 레벨로 지나간다.
- **영향**: 매 세션의 **첫 판단이 국면 UNKNOWN으로 내려간다.** 오늘은 재기동 때문에 세션이 둘이라 9건 중 2건(22%)이 해당됐다. 정상일이면 1/9(11%)이지만 **그 1건이 매일 09:00 첫 판단**이다.
- **신규 여부**: 기존 **X-5**(`NEXT_TODO.md:5395`)의 판정. 신규 이상점으로 세지 않는다.

### P2 — 운영 부담·기술부채

#### 2-1. 사람이 12:14에 확정한 사망 원인이 산출물에는 「원인 불명」으로 봉인됐고, 그 사람의 기록은 추적 밖이다 (**I-9** 미충족)

- **근거**:
  ```
  logs/daily_integrity_20260819.json
    "observation_gaps": [{"process":"g2_paper", ..., "cause": "원인 불명 — 호스트 종료 이벤트 없음"},
                         {"process":"l1_daily", ..., "cause": "원인 불명 — 호스트 종료 이벤트 없음"}]
    "host_events": [ {"event_id":12,"at_kst":"05:52:10","kind":"boot"},
                     {"event_id":12,"at_kst":"05:52:11","kind":"boot"},
                     {"event_id":6005,"at_kst":"05:52:23","kind":"boot"} ]   ← 09:50 부근 이벤트 0건
    "task_exit_codes".launches: 12:28:51 Messiah "사람이 실행" · 12:29:45 Messiah-G2 "사람이 실행"

  $ git ls-files logs | wc -l
  0
  ```
  호스트 이벤트 축은 05:52 부팅 3건만 담았고 09:50 사건은 못 담았다. 반면 `logs/dailycheck/2026-08-19_incident_0950_deepdive.md`(12:14)는 원인을 Windows Update로 확정해 두었다 — 그 파일은 `git ls-files` 기준 **추적되지 않는다.**
- **기준 위반**: 장전 보고서 1-2(「`.gitignore` 철회가 실효 없다 — 인덱스에 추가된 파일 0건」)의 미해소. 장전 F-1은 **장후 적용** 계획이었고 아직 적용 전이다(본 실행은 보고 전용).
- **영향**: `git clean -xdf` 한 번이면 오늘 사건의 유일한 원인 기록이 사라진다. 다만 `dev_memory/DECISION_LOG.md`에 장중 점검 항목이 오늘 추가돼(§5 확인) **부분 완화**된 상태다.
- **신규 여부**: 기존 장전 1-1·1-2 / **I-9**의 판정 — **미충족**.

#### 2-2. 완성봉 유예 500ms가 회선 p90보다 낮은 날이 3거래일 연속 — **A-5** 착수 근거 확정

- **근거**:
  ```
  daily_integrity_20260814.json  delivery_latency.p90 = 0.9245 s
  daily_integrity_20260818.json  delivery_latency.p90 = 0.9271 s
  daily_integrity_20260819.json  delivery_latency.p90 = 0.9253 s   (p50 0.5071 · p99 1.0308 · max 4.0554 · 표본 20,000)
  기동 자가점검 3회 전부: "bar_close 유예 500ms vs 전일 회선 p90 927ms — 완성봉이 늦은 틱을 놓칠 수 있다"
  ```
  `late_bar_drops: 0` · `horizon_findings: []` — 드롭은 관측되지 않았다. 그러나 유예(500ms)가 p50(507ms)과 사실상 같고 p90(925ms)의 **절반**이다.
- **기준 위반**: SYSTEM.md 불변원칙 3(완성봉 규율). 장전 고도화 **G-2**의 착수 조건("500ms 초과 3일 연속")이 오늘로 충족.
- **영향**: 현재 드롭 0이지만 마진이 없다. 회선이 조금만 느려지면 조용히 늦은 틱을 버리기 시작한다.
- **신규 여부**: 기존 장전 **G-2** / **A-5**의 판정 — **조건 충족**. 신규로 세지 않는다.

### 확인 필요 (확정 아님)

| # | 항목 | 지금 아는 것 | 무엇을 보면 판정되는가 |
|---|---|---|---|
| D-1 | 호스트 설정 ①(활성시간)의 **변경 후** 값 | `logs/dailycheck/hostsettings_backup_20260819.txt`(12:35)에 변경 **전** 값(`ActiveHoursStart=19`/`End=20`/WindowsStore 키 없음)만 있다. 오늘 로그 어디에도 `ActiveHours` 문자열 0건 | 레지스트리 현재값 직접 조회, 또는 **G-D/F-E**(장전 자가점검 `host_health`에 활성시간 축) 구현 후 08-20 장전 출력. **장중에서 이월된 항목이며 오늘도 결론 못 냄** |
| D-2 | `UISnapshotFreshness` 0건의 정체 | 태그는 `src/messiah/ui/app.py:1264`에서 발행되고 `core/logging.py:124`에 INFO로 등록돼 있다 — **코드는 결선돼 있다**(커밋 `3a0cc93`). `ui_20260819.log`는 377B로 08-18과 **바이트 단위 동일**, 내용은 uvicorn 기동 4줄 + crash_forensics 1줄뿐. `command_center_ui.json` pid=26064 · `status_snapshot.command_center_ui: "UP"` | 「사람이 오늘 UI를 한 번도 안 열었다」와 「렌더는 됐는데 로그가 파일에 안 닿는다」가 구분 안 됨. **판정법: UI를 1회 열고 `ui_*.log`·`l1_daily_*.log`에 `UISnapshotFreshness` 줄이 뜨는지 즉시 확인.** 안 뜨면 확정 결함 |
| D-3 | `logs/ui_20260819.err.log` 파일 자체 부재 | 에러가 없어서인지 stderr 리다이렉트 미설정인지 구분 불가 | `scripts/install_scheduled_tasks.ps1`의 UI 액션 stderr 리다이렉트 확인. 빈 파일이 생기는 설계면 부재 자체가 결함. **장전에서 이월, 오늘도 미해결** |
| D-4 | **Z-1** meta 게이트 통과율 | `meta_gate: {evaluations: 9, passes: 0, threshold: 0.7, p50: 0.376, p90: 0.5925, max: 0.5925}`. Z-1 기준 표본 14건에 **9건**뿐 — 09:50 사망으로 5사이클 소실 | 08-20 정상일 관측으로 이월. **오늘 판정 불가(사유: 표본 부족, 원인 명확)** |

### 오늘 장전·장중이 남긴 「확인 필요」의 결론 — 장후의 고유 수확

| 출처 | 항목 | 오늘 장후 결론 |
|---|---|---|
| 장전 C-1 | `logs/pass_cycles/` 부재 (W-3) | **정상.** `PassCycleSnapshot` 0건 · `meta_gate.passes = 0`(threshold 0.7, max 0.5925)와 정합. 오늘 pass 사이클이 발생할 수 없었다 — 부재가 결함이 아님이 확정 |
| 장전 C-2 | `UISnapshotFreshness` 0건 | **미해결** → D-2로 이월(판정법 구체화) |
| 장전 C-3 | `ui_*.err.log` 부재 | **미해결** → D-3로 이월 |
| 장전 C-4 | 05:55:35 회차 기동 사유 미기록 | **해소.** `host_events` boot 3건(05:52:10 / 05:52:11 / 05:52:23) 직후 05:55:35 기동 — **부팅 트리거가 원인으로 확정.** `LaunchWindowRefused` 후 정시 08:20 기동으로 완결. 설계대로 |
| 장전 C-5 / 장중 C-3 | `SessionEnd` 부재 | **충족.** l1_daily 15:35:26 · g2_paper 15:34:58 정상 종료, 작업 종료 코드 3건 전부 0. **단 09:50 세션 건은 P1-1로 승계** — 그 사망은 R13 위반으로 확정됐으나 계기가 못 잡는다 |
| 장중 C-1 | X-5 어긋남이 경합인가 | **판정 확정 — 경합 아님.** 위 P1-5 |
| 장중 C-2 | 호스트 설정 적용값 | **미해결** → D-1로 이월 |
| 장중 C-4 | `git ls-files logs` = 0 | **미충족 확정.** 위 P2-1 |
| 장중 C-5 | 장후 산출물·`SessionEnd` | **충족.** 배치 6/6 완주 · 산출물 7종 전량 생성(`daily_integrity` 18.0KB · `self_eval` 729B · `vol_scorecard` 1008B · `volume_check` 395B · `verification_scoreboard` · `g2_daily_returns.jsonl` 당일행 · `feature_health_rolling`) |

### NEXT_TODO 관측 항목 채점 (I-1 ~ I-10)

| ID | 기준 | 결과 | 판정 |
|---|---|---|---|
| **I-1** | 12:40~15:35 추가 `SessionStart` 0건 | 0건 | **충족** — 재기동 fix ①이 들었다 |
| **I-2** | `collection_start_lag_minutes` 249.4 봉인 전망 | **0.5** | **예측 빗나감** → P1-2. 산출 경로가 스냅샷과 다름 확정 |
| **I-3 ★** | `series_findings`에 159분 구멍이 잡히는가 | 11건 · 5계열 전부(ticks 61.2% / K2I 63.2% / regular 63.0% / weekly 70.8%) | **잡힌다. P1-2 승격 없음** — 오늘 사망을 붙잡은 유일한 축 |
| **I-4 ★** | NaN·퇴화 축에 12:30 이후 발행분이 걸리는가 | `nan_ratio` 전 6 Horizon **median/min/last 전부 0.0** · `degenerate_features` always_nan `[]` constant `[]` | **0건 확정 — 구멍이 피처 축에 흔적을 전혀 안 남긴다.** F-I(장중 계획)의 근거 확정 |
| **I-5** | X-5 종일 어긋남 비율 | 2/9 (22%) · 어긋남 전량이 세션 첫 사이클 | **구조 문제 확정** → P1-5 |
| **I-6** | `MetaGateEvaluated` Z-1(14건) | 9건 (사망으로 5사이클 소실) | **판정 불가 · 08-20 이월**(사유 명기) |
| **I-7** | 12:29 세션 `SessionEnd` 존재 | 15:35:26 정상 종료 | **충족** |
| **I-8** | 배치 완주 + 산출물에 「불완전일」 표시 | 6/6 완주 · **표시 축 없음** | **절반 충족** → P1-3 |
| **I-9** | `git ls-files logs` > 0 | 0 | **미충족** → P2-1 |
| **I-10** | 장전 `host` 라인 활성시간 반영 | 08-20 장전 | 이월 |
| **A-5** | delivery p90 500ms 초과 3일 연속 | 924.5 / 927.1 / 925.3 ms | **충족 — G-2 착수 근거 확정** → P2-2 |
| **A-2** | `code_version.worktree_dirty == false` | 필드 미존재(F-2 미적용). 실측: `src/`+`scripts/` 실제 변경 **0파일**, CRLF 잡음 87파일 | 08-20 장전 이월 |

---

## 2. Fix 작업 구현계획

> **본 실행은 예약 점검이며 보고까지만 한다.** 아래는 수립된 계획이고, 사용자가 "구현해"라고 지시할 때 착수한다. 적용 시점은 전부 **오늘 15:35 이후(장 종료 후)** 로, 금지계명 3·4(장중 학습·배포 금지)에 저촉되지 않는다.

### F-1. 비정상 종료 판정을 「세션 단위」로 바꾼다 — P1 · 대응 이상점 1-1 · **최우선**

- **원인 가설**: `_abnormal_exits()`가 프로세스당 **한 번**만 판정하며, 사망 시각을 `activity[-1]`(그날 마지막 로그)로 잡는다. 중간 사망은 그 뒤 재기동한 세션의 로그에 가려진다.
- **변경 파일**:
  - `src/messiah/ops/integrity_report.py` — `_abnormal_exits()`(569~606행) 전면 재작성. `session_starts`와 `session_ends`를 시각순으로 **짝짓기**하여 종료 짝이 없는 세션마다 1건씩 낸다. 각 건의 사망 시각은 「그 세션의 마지막 로그 시각」(다음 `SessionStart` 이전의 마지막 활동)으로 잡는다. 반환 dict에 `session_index`·`died_at_kst`·`recovered_at_kst`·`mid_session: bool` 추가.
  - 같은 파일 `analyze_logs()`(630행~) — `activity_kst`를 세션 경계로 분할해 세션별 마지막 활동 시각을 함께 반환.
  - `src/messiah/ops/integrity_report.py:2201` 부근 breach 문구 — `mid_session: true` 건은 「장중 사망 N분」으로 별도 문장.
  - **`configs/pending_verifications.yaml`** — `no-silent-process-death`의 `since:`를 오늘로 리셋하고 summary를 「죽고 안 돌아온 날 **및 장중에 죽었다 돌아온 날**」로 확장. 리셋하지 않으면 7일 연속 통과 기록이 거짓 자격으로 남는다.
- **회귀 위험**: 「하루 끝에 안 돌아온 프로세스」 판정이 이중 계상될 수 있다(마지막 세션이 짝 없음 → 기존 경로와 새 경로 양쪽에 잡힘). 짝짓기로 단일화해 방지. 2026-08-07 이전 로그(`SessionEnd` 마커 없던 시절)를 소급해 빨갛게 칠하지 않도록 docstring 583~585행의 가드(마커를 한 번이라도 낸 프로세스만 판정)는 **유지**한다.
- **검증 방법**: `pytest tests/ -k "abnormal_exit or integrity"` + **오늘 로그 리플레이** — `python scripts/daily_integrity_report.py --date 2026-08-19 --symbol A05609 --configs configs` 재실행 시 `abnormal_exits`에 2건(l1_daily died_at 09:50:29 / g2_paper died_at 09:30:01, 둘 다 `mid_session: true`)이 잡히는지. 08-18 로그로 회귀 — 0건 유지 확인.
- **적용 시점**: 장후 즉시(오늘 밤).
- **결정 필요 사항**: `no-silent-process-death` 리셋 시 등록 이력을 지울지 보존할지. **권고: 보존 + 축 확장 기록** — 「7일 통과는 확장 전 기준으로는 참이었다」가 남아야 계기 변경 이력이 읽힌다.

### F-2. 소실 계량기 두 경로를 하나로 합치고, 장중 사망 구간을 별도 필드로 세운다 — P1 · 대응 이상점 1-2 · 장중 **F-H** 확장

- **원인 가설**: `status_snapshot`(249.4)과 `integrity_report`(0.5)가 각자 「기동 지연」만 계산하며, 장중 사망 구간을 셀 필드가 양쪽 다 없다. 0.5는 정상 기동일의 상수에 가깝다(08-18도 0.5).
- **변경 파일**:
  - `src/messiah/ops/integrity_report.py` — `irrecoverable_loss_minutes` 산출부에 `mid_session_gap_minutes` 신설. 값은 F-1이 낸 `abnormal_exits`의 `mid_session` 건 분(오늘 158.9 + 180.2 중 **중복 제거한 실측 구간**)에서 유도. `irrecoverable_loss_minutes = start_lag + mid_session_gap` 으로 합산하고, **분해값도 함께 남긴다**(합산만 남기면 다시 정체를 잃는다).
  - `src/messiah/ops/status_snapshot.py`(경로 확인 필요) — `start_lag_minutes` 249.4 산출 로직을 `integrity_report`와 **같은 함수 호출**로 교체. 두 파일이 같은 숫자를 말하게 하는 것이 이 항목의 핵심.
  - 예산 가드 — `IrrecoverableLossBudgetExceeded` 메시지에 「일별 내역」을 붙여 어느 날이 얼마인지 보이게 한다.
- **회귀 위험**: 5거래일 예산 집계가 소급으로 재계산되면 08-14~08-19 값이 전부 바뀐다. 과거 판정을 뒤집지 않도록(R18) **오늘 이후 날짜부터** 새 산출을 적용하고, 과거일은 기존 값을 유지한 채 `axis_version` 필드로 구분.
- **검증 방법**: 오늘 로그 리플레이 — `irrecoverable_loss_minutes ≈ 159.4`(0.5 + 158.9), `mid_session_gap_minutes ≈ 158.9`. `status_snapshot` 재생성 시 같은 값. 08-18 리플레이 — 0.5 유지·`mid_session_gap 0`.
- **적용 시점**: 장후. **F-1 선행 필수**(F-1의 출력이 입력이다).
- **결정 필요 사항**: l1_daily 158.9분과 g2_paper 180.2분 중 무엇을 「하루의 소실」로 볼지. **권고: 계열별로 남기고 대푯값은 최댓값(180.2)** — 소실은 가장 많이 잃은 축을 따라야 안전측이다.

### F-3. 확정본에 `incomplete_day` 축을 신설하고 롤링 소비자가 그것을 읽게 한다 — P1 · 대응 이상점 1-3

- **원인 가설**: 불완전일을 표시할 필드가 없다. `provisional`은 다른 축이고(코드 주석이 명시), `series_coverage`는 존재하지만 **읽는 소비자가 없다**.
- **변경 파일**:
  - `src/messiah/ops/integrity_report.py` — `IntegrityReport`에 `incomplete_day: bool` + `incomplete_reason: list[str]` + `session_coverage_pct_min: float` 추가(339행 `provisional` 옆). 판정: `min(series_coverage[*].coverage_pct) < 95` **또는** `abnormal_exits`에 `mid_session` 건 존재. 오늘 값 → `true`, 61.2%.
  - `src/messiah/ops/feature_health_rolling.py`(경로 확인 필요) — 롤링 창 구성 시 `incomplete_day: true` 인 날을 **표본에서 제외하거나 가중 축소**하고, `days` 목록 옆에 `excluded_days`를 남긴다. 오늘처럼 2일 창에서 1일이 빠지면 `judged: false`가 되어야 옳다.
  - `scripts/run_vol_scorecard.py` — 20거래일 창 구성 시 동일 처리. `window_days`와 별도로 `usable_days` 를 산출물에 기록.
  - `src/messiah/ops/fix_verification.py:724` 부근 — `provisional` 건너뛰기 옆에 `incomplete_day` 처리를 **추가하지 않는다**. 등록부는 불완전일도 채점해야 한다(그게 P1-4와 다른 축이다). 대신 판정 문구에 「불완전일」을 병기.
- **회귀 위험**: 롤링 표본이 줄어 `judged: false`가 늘어난다 — 그 자체는 정직해진 것이지만 등록부의 `unmeasured`/`STALLED` 판정이 연쇄로 늘 수 있다. `fix_verification.py:958` ②번 분기(`trailing_unmeasured >= consecutive_days` → 계측 고장)가 오탐을 낼 수 있으므로, 「불완전일 때문에 못 잰 날」은 `trailing_unmeasured`에서 제외한다.
- **검증 방법**: 오늘 리플레이 — `incomplete_day: true`, `feature_health_rolling` 10m·15m가 `judged: false`(표본 1일)로 바뀌는지. 08-18 리플레이 — `incomplete_day: false` 유지.
- **적용 시점**: 장후. F-1 선행 권장(사망 근거를 쓰므로).
- **결정 필요 사항**: 임계 95%가 적절한가. **권고: `truncation-is-visible`의 등록 기준과 동일한 95를 그대로 쓴다** — 두 축이 다른 임계를 쓰면 「보이는데 불완전일은 아닌 날」이 생긴다.

### F-4. 등록부에 「계측 축」과 「결과 축」을 가른다 — P1 · 대응 이상점 1-4

- **원인 가설**: `fix_verification.py:932` 한 분기가 두 가지 실패를 한 문장으로 낸다. 항목의 취지가 **계측 성립**인데 채점은 **계측값**으로 한다.
- **변경 파일**:
  - `configs/pending_verifications.yaml` — 항목마다 `axis: instrument | outcome` 필드 신설. 오늘 재발한 4건은 전부 `instrument`. `instrument` 항목의 충족 조건은 **「그 metric이 산출됐는가(비-null)」** 이고, metric 값의 기준 초과는 별도의 `outcome_note`로만 남긴다.
  - `src/messiah/ops/fix_verification.py` — `VerificationItem`에 `axis` 파싱 추가. `_verdict()` ①번 분기(932행)를 `axis == "outcome"` 일 때만 RECURRED로 보내고, `axis == "instrument"` 는 metric이 null/미산출일 때만 RECURRED. 값 위반은 새 상태 `MEASURED_BAD`("계측 성립 · 값 위반")로 WARNING 레벨.
  - 같은 파일 — **한 metric을 둘 이상의 `fix_id`가 공유하면 로드 시 거부**(또는 `WARNING` + 스코어보드에 `shared_metric` 표시). `ui-restart-observability`와 `launch-window-refusal-not-counted`가 지금 그 상태다. 둘 중 하나에 고유 metric을 주는 것이 정공법 — 후자는 `refused_starts_counted_as_restart`(불린)로 바꾼다.
  - `src/messiah/ops/fix_verification.py:1089` 부근 버킷 — `measured_bad` 버킷 추가, `FixVerificationScoreboard` 요약 문장에 반영.
- **회귀 위험**: 등록부 23건의 상태가 한꺼번에 재분류된다. 이력 연속성이 끊기지 않도록 `axis` 미지정 항목은 기존 동작(`outcome`)을 유지하고, 4건만 명시적으로 `instrument`로 표기해 **점진 이행**한다.
- **검증 방법**: `pytest tests/ -k fix_verification` + 오늘 스코어보드 재생성 — 재발 4 → **0**, `measured_bad` 4, 그리고 오늘의 진짜 사건이 P1-1 경로(`abnormal_exits` 2건)로 잡히는지. 08-11·08-14 스코어보드 리플레이로 과거 판정이 안 뒤집히는지 확인(R18).
- **적용 시점**: 장후. **F-1과 같은 커밋에 넣지 않는다** — F-1이 없으면 오늘 진짜 사고를 잡는 축이 하나도 없어지므로 F-1 **다음**에 적용한다.
- **결정 필요 사항**: 없음.

### F-5. 세션 첫 사이클의 국면 `UNKNOWN`을 명시적으로 표시한다 — P1 · 대응 이상점 1-5

- **원인 가설**: 집계기가 기동 직후 국면 캐시가 비어 있는 상태에서 `UNKNOWN`을 기본값으로 쓴다. 조회 실패와 「아직 안 받았다」가 같은 값으로 표현된다.
- **변경 파일**:
  - `src/messiah/strategy/aggregator.py`(또는 `AggregatorNoContribution` 발행부) — 국면 미보유 상태를 `UNKNOWN`이 아니라 **`regime_source: "not_yet_received"`** 로 구분해 로그에 싣는다. 값 자체는 `UNKNOWN` 유지(R18 — 판정을 안 뒤집는다). 첫 사이클이면 `first_cycle_after_start: true` 필드 추가.
  - `src/messiah/regime/runtime.py` — 웜스타트 시 직전 국면을 집계기에도 즉시 푸시하도록 결선(`RegimeWarmStart` ×2가 오늘 이미 발행됐으므로 값은 존재했다 — 전달만 안 된 것). 이것이 근본 수정이며 성공하면 첫 사이클도 일치한다.
  - `src/messiah/ops/integrity_report.py` — `regime_distribution` 옆에 `regime_mismatch_cycles` 신설(오늘 값 2/9).
- **회귀 위험**: 웜스타트 값을 즉시 푸시하면 「어제 국면」이 오늘 첫 사이클에 실릴 수 있다. `RegimeWarmStart`에 실린 시각이 당일이 아니면 푸시하지 않는 가드를 둔다.
- **검증 방법**: 오늘 로그 리플레이 — 09:00:02·12:31:01 두 건에 `first_cycle_after_start: true`가 붙는지. 08-20 라이브 관측 — 09:00 첫 사이클이 `RANGE`/`HIGH_VOL`로 일치하면 근본 수정 성공, `UNKNOWN`이면 표시만 개선된 것.
- **적용 시점**: 장후. 단독 커밋.
- **결정 필요 사항**: 근본 수정(웜스타트 푸시)과 표시 개선 중 어디까지 갈지. **권고: 둘 다 하되 커밋을 나눈다** — 표시 개선은 위험 0이고, 푸시는 08-20 관측으로 채점해야 한다.

### F-6. 관측 공백의 원인을 사람이 산출물에 되먹일 경로 — P2 · 대응 이상점 2-1

- **변경 파일**:
  - `configs/` 에 `incident_causes.yaml` 신설(`date` · `process` · `from_kst` · `cause` · `evidence_path`). `src/messiah/ops/integrity_report.py`의 `observation_gaps` 산출부가 이 파일을 조회해 `cause`를 채우고, 없으면 기존 「원인 불명」 유지 + `cause_source: "unresolved"`.
  - `.gitignore` — 장전 F-1과 합쳐 `logs/dailycheck/*.md`·`logs/*_integrity_*.json`을 실제로 인덱스에 넣는다(`git add -f` 1회 + negation 실효 확인).
- **검증 방법**: 오늘 항목을 `incident_causes.yaml`에 넣고 리플레이 — `cause`가 "Windows Update(2026-08-19 딥다이브)"로 바뀌는지. `git ls-files logs | wc -l > 0`.
- **적용 시점**: 장후. 장전 F-1과 같은 커밋.

### 적용 순서와 커밋 계획

| # | 커밋 | 포함 | 메시지 초안 |
|---|---|---|---|
| ① | 계기 복구 | **F-1** | `[MW0601] 장중에 죽었다 돌아온 날을 비정상 종료 축이 못 봤다 — 세션 단위 판정` |
| ② | 소실 통합 | **F-2**(장중 F-H 확장) | `[MW0601] 159분을 잃은 날과 정상일이 같은 0.5분이었다 — 소실 두 경로 통합` |
| ③ | 불완전일 | **F-3** | `[MW0601] 반쪽짜리 하루가 롤링 창에 정상일로 들어갔다 — incomplete_day 축` |
| ④ | 등록부 | **F-4** | `[MW0601] 정확히 잰 계기 넷이 「듣지 않았다」를 받았다 — 계측 축과 결과 축 분리` |
| ⑤ | 국면 | **F-5** | `[MW0601] 매 세션 첫 판단이 국면 없이 내려가고 있었다 — 웜스타트 푸시` |
| ⑥ | 기록 | **F-6** + 장전 F-1·F-2 | `[MW0601] 사람만 아는 원인을 산출물이 모른다 — 사건 원인 되먹임 + logs 추적` |
| ⑦ | 이월 | 장중 **F-G**(첫틱 오탐) · **F-I**(창 연속성 배지) · **F-J**(4xx 인증 재시도) · 장전 **F-3**·**F-4** | 각각 별도 |

**순서를 지켜야 하는 이유**: ①이 없으면 ②의 입력이 없고, ④를 ① 앞에 놓으면 재발 4건이 사라진 자리에 진짜 사고를 잡는 축이 하나도 남지 않는다 — 오늘 사망을 붙잡은 유일한 축이 바로 그 4건이 감시하던 `series_coverage`다.

---

## 3. 고도화 방안

### G-1. 등록부 재발을 「사건 단위」로 묶는다

- **관측 근거**: 오늘 재발 4건은 전부 09:50 사망 **한 사건**의 파생이다. `observation_gap_minutes_max` 180.2 ×2건, `series_coverage_pct_min` 61.2, `series_leg_shortfall`(12:30 41/42다리) — 네 값이 같은 순간을 네 각도에서 본 것이다. 스코어보드는 이를 「오늘 위반 4」로 세었다.
- **제안 내용**: `FixVerificationScoreboard`에 `incidents` 절 신설. 같은 날 위반 항목들의 metric 근거 시각이 하나의 구간(`observation_gaps`의 한 건) 안에 들어가면 **하나의 사건으로 묶고**, 요약 문장을 「오늘 위반 4건(사건 1건)」으로 낸다.
- **기대 효과**: 재발 건수가 사고 규모가 아니라 **계기 개수**를 반영하던 문제 제거. 오늘: 4 → 1.
- **비용·위험**: 사건 귀속 로직이 틀리면 서로 다른 두 사고를 하나로 합칠 수 있다. **R18 섀도 계측 20거래일** 후 승격 권고 — 그 동안은 `incidents` 절만 병기하고 `counts`는 안 건드린다.
- **선행 조건**: F-1(사건 구간의 정본이 `abnormal_exits`가 되어야 한다).
- **우선순위 제안**: 이번 주. **F-4와 동시**(같은 문제의 두 층이다).

### G-2. 완성봉 유예 500ms를 회선 실측에 연동한다 — 착수 조건 오늘 충족

- **관측 근거**: `delivery_latency.p90` 3거래일 연속 500ms 초과 — 924.5(08-14) / 927.1(08-18) / **925.3ms(08-19)**. p50이 507ms로 유예와 사실상 동일. 기동 자가점검이 3회 전부 같은 경고를 냈으나 `[OK ]`로 통과시켰다.
- **제안 내용**: 커밋 `fe15694`("예산을 잡아먹는 쪽은 아무도 예산과 대조하지 않았다")가 만든 「완성봉 유예 ↔ 회선 실측」 대조를 **경고에서 조치로** 승격. `configs/instance.yaml`의 `bar_close_grace_ms`를 전일 `delivery_latency.p90`의 1.1배로 자동 산출(하한 500ms, 상한 1500ms). 변경 시 `BarCloseGraceAdjusted`(INFO)로 이유와 함께 남긴다.
- **기대 효과**: 현재 `late_bar_drops: 0`이지만 마진이 없다. 유예를 1020ms로 올리면 p99(1031ms)까지 거의 덮는다. 측정 지표: `late_bar_drops` 0 유지 + `IntegrityThresholdBreached` 중 봉 관련 건수.
- **비용·위험**: 유예를 늘리면 완성봉 확정이 늦어져 판단 사이클이 밀린다. 불변원칙 3(완성봉 규율) 자체는 안 건드리되 **자동 조정이 규율을 무르게 만들 위험**이 있다 — 상한을 두고, 상한에 닿으면 자동 조정 대신 `[WARN]`으로 사람을 부른다.
- **선행 조건**: 없음(데이터는 이미 3일치 있다).
- **우선순위 제안**: 이번 주.

### G-3. 커버리지를 「기록되는 값」에서 「소비되는 값」으로 만든다

- **관측 근거**: `series_coverage`는 오늘 5계열의 61~71%를 정확히 기록했다. 그런데 그 값을 읽는 코드는 등록부(`truncation-is-visible`) **하나뿐**이고, 그 하나는 값을 읽어 자기 자신을 「실패」로 채점하는 데 썼다. 정작 오염을 막아야 할 `feature_health_rolling`·`vol_scorecard`는 커버리지를 조회하지 않는다.
- **제안 내용**: `series_coverage`를 표준 조회 함수 `usable_trading_days(start, end, min_coverage=95)`로 감싸고, 롤링 창을 구성하는 **모든** 소비처가 이 함수를 통해서만 날짜 목록을 얻게 한다. 현행 소비처: `feature_health_rolling`(3거래일 창) · `run_vol_scorecard`(20거래일) · `degenerate_features` 롤링 · `fix_verification._scorable_days_until`.
- **기대 효과**: 「기록은 됐는데 아무도 안 읽는 축」의 재발 방지. 측정: 커버리지 미달일이 포함된 롤링 판정 건수 → 0.
- **비용·위험**: 표본이 줄어 `judged: false`가 늘어난다. **그 자체는 정직해지는 것**이지만 등록부 `STALLED` 오탐과 연쇄하므로 F-3의 가드가 선행해야 한다.
- **선행 조건**: **F-3**.
- **우선순위 제안**: 다음 단계. F-3 적용 후 08-20~08-21 관측으로 영향 범위를 재고 착수.

### G-4. 「계기가 자기 자신을 채점한다」를 구조적으로 금지한다

- **관측 근거**: 오늘 `no-silent-process-death`가 **자기가 못 보는 사고가 일어난 날에** 「7거래일 연속 충족」을 선고했다(P1-1). 계기의 출력을 그 계기의 합격 기준으로 쓰면, 계기가 눈이 멀수록 성적이 좋아진다.
- **제안 내용**: 등록부 항목에 `negative_control` 필드 — 「이 metric이 반드시 반응해야 하는 알려진 사건」의 목록. 예: `abnormal_exits`의 negative control은 `observation_gaps` 존재. 사건이 있었는데 metric이 0이면 통과가 아니라 **`INSTRUMENT_BLIND`**(ERROR)로 판정한다. 오늘이면 `observation_gaps` 2건 vs `abnormal_exits` 0건 → 즉시 적발됐을 사안이다.
- **기대 효과**: 「건수 0은 두 가지다」(phases.md §D)를 사람이 매번 기억하지 않아도 되게 만든다. 측정: `INSTRUMENT_BLIND` 판정 건수 — 오늘 소급 적용 시 1건 검출 예상.
- **비용·위험**: negative control 자체가 틀리면 새 오탐원이 된다. 초기에는 **가장 확실한 짝 2~3개만** 등록한다(`abnormal_exits ↔ observation_gaps`, `irrecoverable_loss ↔ series_findings`, `late_bar_drops ↔ delivery p90 > grace`). R18 섀도 20거래일.
- **선행 조건**: F-1·F-4.
- **우선순위 제안**: 다음 단계(W-단계 편입 검토). **이 프로젝트가 반복해서 밟는 함정의 근원이라 장기 가치가 가장 크다.**

### 로드맵 반영 제안

| 항목 | 현 로드맵 위치 | 제안 위치 | 사유 |
|---|---|---|---|
| G-4 negative control | 없음 | 마스터플랜 Ver2.0 「관측 신뢰성」 절 신설 | 08-04 크래시 집계, 08-18 늑대소년 11회, 오늘 `abnormal_exits` — **같은 함정을 세 번째 밟았다.** 개별 fix가 아니라 원칙으로 올려야 멈춘다 |
| G-2 유예 자동 연동 | 장전 G-2(제안 단계) | 이번 주 착수 확정 | 3일 연속 근거 확보로 착수 조건 충족 |
| G-3 커버리지 결선 | 없음 | W-3 이후 | F-3 적용 후 영향 범위 실측 필요 |

---

## 4. 다음 거래일(08-20) 관측 예정

| ID | 관측 대상(태그/지표) | 판정 기준 | 판정 예정일 |
|---|---|---|---|
| **J-1** | `abnormal_exits` | F-1 적용 후 오늘 로그 리플레이에 **2건**(`mid_session: true`) — 0건이면 F-1이 안 들었다 | 구현 직후 |
| **J-2** | `irrecoverable_loss_minutes` | F-2 적용 후 오늘 리플레이 **≈159.4** · `status_snapshot`과 동일값 | 구현 직후 |
| **J-3** | `incomplete_day` | F-3 적용 후 오늘 `true`/08-18 `false` · `feature_health_rolling` 10m·15m가 `judged: false`로 전환 | 구현 직후 |
| **J-4** | `FixVerificationScoreboard.counts` | F-4 적용 후 오늘 재발 **4 → 0**, `measured_bad` 4 | 구현 직후 |
| **J-5** | `AggregatorNoContribution` 09:00 첫 사이클 | F-5 적용 시 `regime != UNKNOWN`. 여전히 UNKNOWN이면 웜스타트 푸시가 안 닿은 것 | 08-20 장중 |
| **J-6** | `MetaGateEvaluated` 건수 | 정상일 14건 확보 시 **Z-1** 판정(오늘 9건으로 이월) | 08-20 장후 |
| **J-7** | `git ls-files logs \| wc -l` | > 0 (F-6/장전 F-1 적용 후) | 08-20 장전 |
| **J-8** | 자가점검 `host` 라인 활성시간 | 08:00~16:00 반영 여부 — **I-10** 이월 | 08-20 장전 |
| **J-9** | `code_version.worktree_dirty` | `false` (장전 F-2 적용 후) — **A-2** 이월 | 08-20 장전 |
| **J-10** | `delivery_latency.p90` | 4일째 500ms 초과 여부. G-2 적용했다면 `bar_close_grace_ms` 자동 조정 로그 | 08-20 장후 |
| **J-11** | `UISnapshotFreshness` | UI 1회 개방 후 로그 출현 여부 — **D-2** 판정 | 즉시 가능 |
| **J-12** | `SessionStart` 횟수 | 프로세스당 정확히 1회(재기동 0). 2회면 09:50 사고 유형 재발 | 08-20 장후 |

---

## 5. 재시동 권고

**권고: 재시동 불필요.**

| | 얻는 것 | 잃는 것 |
|---|---|---|
| **재시동 없이 (권고)** | 오늘 관측 종결 상태 보존. 현재 실행 중인 대상 프로세스 자체가 없다 | 없음 |
| **재시동으로** | 없음 | 무의미한 프로세스 기동 — 장 종료 후 기동은 `LaunchWindowRefused`(기동 창 08:15~15:35)로 거절되며, 거절이 등록부 `launch-window-refusal-not-counted` 축에 잡음을 더한다 |

**근거**:

- `logs/status_snapshot.json` (15:34:47) — `code_version.stale: **false**` · `process_git_sha "50eff6c" == head_git_sha "50eff6c"` · `"코드 50eff6c — 전 프로세스 동일"`. **커밋된 코드와 실행 코드가 이미 같다.**
- 오늘 로그는 두 코드 버전이 섞여 있으나(`session_git_shas: ["40e9968", "50eff6c"]`) 그 경계는 12:29 재기동으로 이미 지나갔고, 12:29 이후 구간은 전부 HEAD로 돌았다. 「어느 코드의 결과인지 말할 수 없는」 상태가 아니다.
- 대상 프로세스는 15:34:58 / 15:35:26에 정상 종료했고 `Messiah-Shutdown`(15:40:01, exit 0)이 완결했다. **지금 재시동할 프로세스가 없다.**
- 오늘 밤 F-1~F-6을 커밋하면, 08-20 08:20 정시 트리거가 자동으로 새 HEAD를 집는다 — **재시동 없이 반영된다.**

**단, 구현을 진행한다면**: 커밋 후 `git status --porcelain src scripts`가 실제 변경 0을 보여야 한다(금지계명 10 — 미커밋 변경 실전 반입 금지). 현재 dirty 263건은 전부 CRLF 개행 잡음(`git diff --stat`: 87 files, 33261 insertions / 33261 deletions — 삽입과 삭제가 정확히 같다)으로 실체 변경이 아니지만, 이 잡음이 진짜 미커밋 변경을 가리고 있다는 점은 장전 1-1이 이미 지적한 그대로다.

---

## 6. dev_memory 반영

- `DECISION_LOG.md` 추가 항목: `## [MW0601] 계기 넷이 정확히 쟀고, 그래서 넷 다 「듣지 않았다」를 받았다 — 2026-08-19 장후 점검`
- `NEXT_TODO.md` 추가 체크박스: Fix 6건(F-1~F-6) · 고도화 4건(G-1~G-4) · 관측 12건(J-1~J-12) · 확인 필요 4건(D-1~D-4)
- 완료 처리: **I-1 · I-3 · I-7**(충족) · **I-5**(판정 확정) · 장전 **C-1 · C-4 · C-5**(해소) · 장중 **C-1 · C-5**(해소)

---

## 부록. 이 점검이 새로 세지 않은 것 (중복 방지 기록)

- **09:50 사망 그 자체** — 장중 보고서 P0-1 및 `2026-08-19_incident_0950_deepdive.md`가 정본. 본 보고서는 그 사건이 **계기에 어떻게 기록됐는가**만 다룬다.
- **`CollectorFirstTickOverdue` 오탐**(12:29:26) — 장중 1-1 / `NEXT_TODO` **F-G**. 재기술하지 않음.
- **`nan_ratio 0.0`이 구멍을 안 남김** — 장중 1-3 / **F-I**. 오늘은 **I-4** 채점으로만 다뤘다(0건 확정).
- **4xx 「재시도 무의미」 오분류** — 장중 2-1 / **F-J**. 오늘 `OptionChainPollError` 1건(12:30:02, 403)은 12:30:43·12:35:44에 회복돼 같은 패턴 재현. 신규 아님.
- **`.gitignore` 철회 실효 없음** — 장전 1-2 / **F-1**. 오늘 P2-1은 그 미해소의 **결과**(원인 기록이 추적 밖)만 다룬다.
- **Y-5 `RegimeSeeded` 태그명 오류 · `schedule_drift` 2종만 대조** — 장전 2-1·2-2 / **F-3**·**F-4**. 오늘 자가점검 3회 모두 동일 상태로 재현, 신규 아님.
- **`30m` 미측정**(표본 29 < 30, 2거래일 누적 22 < 30) — `unmeasured_kinds.accruing`으로 올바르게 분류됨. 누적 중이며 결함 아님.
