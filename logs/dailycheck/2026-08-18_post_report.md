# MESSIAH 일일 점검 — 2026-08-18 / 장후

- 점검 시각: 16:05 KST
- 대상 국면: post (장후) — D-day 1일차 마감
- HEAD `ef9807c` · 실행 중 `ef9807c` (`code_version.stale=false`) · 미커밋 179건(실변경은 문서 3파일뿐)
- 증거: `logs/dailycheck/evidence_20260818_post.md`
- 선행 보고서: `2026-08-18_pre_report.md` · `2026-08-18_intra_report.md` · `2026-08-17_post_report.md`

## 0. 한 줄 결론

**하루는 설계대로 살았다. 설계대로 살지 못한 것은 그 사실을 채점하는 도구다** — 데이터·판단·종료 전 구간이 무결했고 등록부 재발 11건 중 9건이 오늘 기준을 충족했는데, 채점기가 최초 위반에서 멈추는 구조라 그 회복도 오늘 유일한 새 위반도 둘 다 보이지 않는다.

| 국면 | 판정 | 근거 요약 |
|---|---|---|
| 장전 | **정상** | 자가점검 30행 비-OK 0 · `[OK] 20260817 장후 배치 정상 종료 확인` · `git_sha=ef9807c`=HEAD |
| 장중 | **정상** | 1m 410봉 결손 0분 · 거래량 항등식 1.000 · 10분 공백 0 · UNKNOWN 0% · ERROR/WARNING 각 0건(l1 WARNING 1건은 설계된 폴백 배지) |
| 장후 | **조건부** | 배치 6/6 완주·발견 0. 그러나 등록부 채점 3건이 구조적으로 성립하지 않는다(1-1·1-2·1-3) |

**P0 없음.** P1 3건 · P2 2건.

---

## 1. 이상점 정리보고

### P0 — 오늘 거래에 직접 위험

**해당 없음.** 거래 경로(수집→합성→피처→국면→집계→판단→리스크→종료)에서 오늘 데이터를 잃거나 잘못 쓴 지점이 없다. `breaches: []` · `abnormal_exits: []` · `observation_gaps: []` · `restarts: 0` · `circuit_breaker_events: {}`.

---

### P1 — 정확성·관측 훼손

#### 1-1. 오늘 유일한 실제 기준 위반이 채점기에 도달하지 못했다 — 등록부가 스스로 겨냥한 실패의 재현

- **증상**: `daily_integrity_20260818.json`의 `unmeasured`가 **3건**이다. `daily-axes-measured`의 기준은 `unmeasured_count ≤ 0`이므로 **오늘이 위반일**이다. 그런데 15:48:34 로그는 오늘을 한 번도 말하지 않는다.

- **근거**:
  ```
  15:48:34 [ERROR] FixVerificationRecurred
    daily-axes-measured: 2026-08-13에 기준 위반(2거래일 전) — 수정이 듣지 않았다 (unmeasured_count ≤ 0)
  ```
  `logs/postmarket_20260818.log` · `FixVerificationRecurred` 총 11건 전부 15:48:34

  오늘 값을 `METRIC_EXTRACTORS`로 직접 재계산한 결과 — **재발 11건 중 9건이 오늘 기준을 충족**한다:

  | fix_id | metric | 08-14 | **08-18** | 기준 | 오늘 충족 |
  |---|---|---|---|---|---|
  | `no-degenerate-features` | `degenerate_feature_count` | 57 | **0** | ≤0 | ✅ |
  | `regime-not-constant` | `regime_unknown_ratio` | 1.0 | **0.0** | ≤0.5 | ✅ |
  | `archiver-restart-restore` | `series_head_gap_minutes_max` | 33 | **5** | ≤20 | ✅ |
  | `truncation-is-visible` | `series_coverage_pct_min` | 94.5 | **99.1** | ≥95 | ✅ |
  | `composer-bucket-completeness` | `late_bar_drops` | 2 | **0** | ≤0 | ✅ |
  | `ui-restart-observability` | `observation_gap_minutes_max` | 0 | **0** | ≤5 | ✅ |
  | `launch-window-refusal-not-counted` | `observation_gap_minutes_max` | 0 | **0** | ≤5 | ✅ |
  | `thursday-weekly-listing-calendar` | `option_calendar_violations` | 0 | **0** | ≤0 | ✅ |
  | `leg-completeness-measured` | `series_leg_shortfall` | 0 | **0** | ≤0 | ✅ |
  | `exit-code-matches-log` | `nonzero_task_exits` | None | **None** | ≤0 | ⚪ 판정 불가 |
  | `daily-axes-measured` | `unmeasured_count` | 1 | **3** | ≤0 | ❌ **오늘 위반** |

- **원인(코드 확정)**: `src/messiah/ops/fix_verification.py::evaluate()` 의 채점 루프가 **최초 위반에서 `break`** 한다.

  ```python
  for day in judged_days:
      value = extractor(reports[day])
      ...
      if item.satisfied_by(value): clean += 1
      else:
          violated_on = day
          break            # ← 여기서 멈춘다
  ```
  `daily-axes-measured`는 `since: 2026-08-10`이므로 채점 대상은 08-11(0)·08-12(0)·08-13(**1**)에서 끝난다. **08-14(1)과 08-18(3)은 한 번도 채점되지 않았다.** `_trading_days_since()`가 붙이는 「2거래일 전」은 위반이 2거래일 전에 *마지막으로* 났다는 뜻이 아니라 *처음으로* 났다는 뜻이며, 이 값은 앞으로도 매일 커지기만 한다.

- **기준 위반**: `dev_memory/DECISION_LOG.md:3766` **[근본원인] 한 번 위반하면 영원히 재발이라 새 재발이 묻힌다 (B-3)** — *"문구는 `2026-08-07에 기준 위반`뿐이라 오늘 난 것과 사흘 묵은 것이 같은 무게로 읽힌다."* B-3의 처방은 `since:` 수동 리셋이었고, `_trading_days_since()` docstring이 명시한 목적은 *"오늘 새로 생긴 재발과 3거래일 전 것이 같은 무게로 읽히"*는 것을 막는 것이다. **오늘 그 목적이 실패했다** — 새 위반과 묵은 위반이 같은 문장에 눌려 있다. SYSTEM.md R10(조용한 폴백 금지)의 정신 위반이기도 하다: 오늘 위반이 오늘 문장으로 나오지 않는다.

- **영향**: 매 장후 ERROR 11건이 사실상 상수로 울린다(늑대소년). 그 안에서 (a) 오늘 새로 난 위반, (b) 이미 회복된 9건이 **둘 다 구분되지 않는다**. 특히 (b)의 손실이 크다 — 08-16 P0-1(웜스타트 적재 필터)이 `degenerate 57 → 0`을 만든 성과가 등록부 어디에도 기록되지 않는다.

- **신규 여부**: 근본원인은 **기존**(DECISION_LOG B-3, `since:` 필드로 완화 조치됨). **오늘 새로 드러난 것은 그 완화가 실효를 잃었다는 실측** — 수동 리셋에 의존하는 한 회복은 자동으로 보이지 않는다. 중복 보고가 아니라 처방의 한계에 대한 첫 라이브 증거다.

---

#### 1-2. `unmeasured`가 1 → 3으로 늘었다 — 새 계측축이 켜지면서 기한이 구조적으로 닫혔다

- **증상**: `daily_integrity_20260818.json`
  ```json
  "unmeasured": [
    "15m 피처 퇴화 판정(1거래일 누적 27 < 최소 30)",
    "30m 피처 퇴화 판정(1거래일 누적 14 < 최소 30)",
    "진입점 종료 코드(조회 실패: TimeoutExpired (2/2회 시도))"
  ]
  ```
  08-13·08-14는 각 1건(진입점 종료 코드)이었다. **늘어난 2건은 오늘 처음 등장한다.**

- **근거**: `feature_health_rolling` 필드가 `daily_integrity_20260813.json`·`daily_integrity_20260814.json`에는 **아예 없고** `20260818.json`에만 있다. 그 안의 `days`가 `['2026-08-18']` — 누적 1일이다.
  ```
  15m  days=['2026-08-18']  samples=27  judged=False
  30m  days=['2026-08-18']  samples=14  judged=False
  1m/3m/5m/10m             judged=True   (409/136/81/41)
  ```

- **기준 위반**: 지표 정의 자체(`fix_verification.py:213-222` `_degenerate_feature_count`) — *"표본이 하한(30)에 못 미친 Horizon은 분모에서 뺀다 … 30m은 하루 15봉이 물리적 상한이라 그 상태가 **매일** 이어진다."* 즉 30m은 **롤링 누적 없이는 영원히 판정 불가**다. 그런데 롤링 축이 오늘 처음 켜져 누적이 1일뿐이므로 오늘은 판정될 수가 없다. `configs/pending_verifications.yaml`의 `daily-axes-measured`는 `max: 0` · `consecutive_days: 3` · `deadline: 2026-08-19`.

- **영향**: `daily-axes-measured`의 기한은 **내일**이고 3거래일 연속이 필요한데, 오늘이 위반이므로 **기한 내 충족이 산술적으로 불가능하다.** 어제(08-17) 장후 보고서가 *"충족 불가"* 로 예보했고 오늘 확정됐으며, 값이 1→3으로 **악화**됐다는 것이 어제 예보에 없던 부분이다.

- **완화 요인(결함 아님)**: 15m은 하루 27표본이므로 **2거래일이면 54 ≥ 30**, 30m은 14×3=42로 **3거래일**이면 해소된다. 즉 이 2건은 08-20~08-21에 자연 소멸할 성질이다. 문제는 그 사이에 기한이 지나간다는 것뿐이다.

- **신규 여부**: **신규.** 진입점 종료 코드 1건은 기존(NEXT_TODO 3824 F-4 · 4314 F-D)이지만 15m/30m 2건은 오늘 첫 관측이다.

---

#### 1-3. `task_exit_codes` 3거래일 연속 조회 실패 — 어제 정한 「지표 교체」 조건이 오늘 발동했다

- **증상**: 진입점 종료 코드를 오늘도 못 쟀다.
  ```json
  "task_exit_codes": {"exits": [], "available": false,
                      "detail": "조회 실패: TimeoutExpired (2/2회 시도)", "launches": []}
  ```
  08-13 `TimeoutExpired` · 08-14 `TimeoutExpired (2/2회 시도)` · **08-18 `TimeoutExpired (2/2회 시도)`** — 리포트가 있는 3거래일 연속.

- **근거**: `logs/daily_integrity_20260818.json` (15:48:34 생성). 파생 결과로 `exit-code-matches-log`의 `nonzero_task_exits`가 오늘도 `None`(판정 불가)이다.

- **기준 위반**: `dev_memory/DECISION_LOG.md` 2026-08-17 장후 결정 — *"`exit-code-matches-log`는 08-18 장후에도 `None`이면 **연장이 아니라 지표 교체**다 — F-D 재시도(`2/2회 시도`)가 이미 들어간 뒤에도 실패한 것이므로 연장은 답이 아니다."* **조건이 성립했다.**

- **영향**: (a) `exit-code-matches-log`는 기한(08-21)까지 채점 자체가 성립하지 않는다. (b) 이 1건이 `unmeasured`에 상수로 들어앉아 `daily-axes-measured`를 함께 붙잡는다 — 등록부의 메타 항목이 개별 항목 하나에 인질로 잡힌 형태다. (c) `NEXT_TODO` **P-5**(*"`task_exit_codes.exits`의 `Messiah-Postmarket`에 08-17의 exit 3이 섞이지 않는가"*)는 `exits`가 빈 배열이라 **판정 불가로 종결**한다.

- **신규 여부**: **기존 항목의 조건 발동.** 증상 자체는 NEXT_TODO 3824(F-4)·4314(F-D)·4392에 있다. 오늘 새로운 것은 *"08-18에도 None이면"* 이라는 어제의 분기 조건이 참이 됐다는 사실 하나다.

---

### P2 — 운영 부담·기술부채

#### 2-1. 어제 결정한 F-5(기한 연장)가 미적용인 채로 기한 3건이 오늘·내일 닫힌다

- **증상**: `configs/pending_verifications.yaml`의 기한이 어제 결정 이전 그대로다 — `daily-axes-measured 2026-08-19` · `composer-bucket-completeness 2026-08-19` · `no-degenerate-features 2026-08-20`.
- **근거**: `git diff --stat -w --ignore-cr-at-eol -- src scripts configs` **빈 출력**(코드·설정 동결 유지). 당일 커밋 0건. `configs/pending_verifications.yaml`의 ` M` 표시는 CRLF 개행 잡음이며 실변경 아님.
- **기준**: 2026-08-17 장후 DECISION_LOG — *"F-5로 네 건의 기한을 08-24~08-26으로 연장하되 연장 사유를 주석으로 박는다."* 결정은 있고 적용은 없다. (08-17은 휴장, 08-18 장중은 R11·금지계명 3·4로 변경 금지 → **오늘 장후가 첫 적용 기회**다. 규율 위반은 아니다.)
- **오늘 실측으로 확정된 것**: 세 건 모두 기한 내 3거래일 연속이 **산술적으로 불가능**하다.

  | id | 08-14 | 08-18 | 기한 | 남은 거래일 | 판정 |
  |---|---|---|---|---|---|
  | `daily-axes-measured` | 1 | **3(위반)** | 08-19 | 1 | **충족 불가 확정** |
  | `composer-bucket-completeness` | 2 | **0(충족)** | 08-19 | 1 | **충족 불가 확정** (최대 2일) |
  | `no-degenerate-features` | 57 | **0(충족)** | 08-20 | 2 | **충족 불가 확정** (최대 3일이나 1-1의 `break`로 카운터 자체가 안 돈다) |

- **영향**: 내일·모레 장후에 「기한 초과」 3건이 뜬다. 그 문구는 *"고치지 못했다"* 로 읽히지만 실제 뜻은 *"채점할 날이 없었다"* 이다 — 어제 DECISION_LOG가 이미 이름 붙인 형태(`deadline_trading_days` 신설 + 두 판정 분리)가 오늘 그대로 재현될 예정이다.
- **신규 여부**: **기존 결정의 미적용.**

#### 2-2. 「소급 불가 손실」을 장중 화면과 장후 리포트가 다르게 말한다

- **증상**: 같은 날 같은 이름의 지표가 두 값이다.
  ```
  status_snapshot.json (15:34:58)  irrecoverable_loss: {clean: true, lost_items: 0,
                                    start_lag_minutes: 0.5, summary: "오늘 소급 불가 손실 없음"}
  daily_integrity_20260818.json (15:48:34)   irrecoverable_loss_minutes: 5.0
  ```
- **근거**: 5.0의 출처는 `series_coverage`의 `option_chain/regular` — `head_gap_minutes: 5.0`(창 시작 08:20, 첫 행 08:25). `src/messiah/ops/integrity_report.py:1017 irrecoverable_loss_minutes()`는 *"소급 불가 계열의 머리 구멍 최댓값과 기동 지연 중 큰 쪽"* 을 쓰고, `status_snapshot`의 `clean`은 `lost_by_series`(행 유실)만 본다. **정의가 다르다** — 코드로 확정, 추정 아님.
- **기준 위반**: SYSTEM.md 아키텍처 불변 원칙(단일 관측 표면) 및 R10의 정신 — 같은 이름이 두 뜻을 겸하면 그 이름은 관측 도구가 아니다. `dev_memory` 4941행이 이름 붙인 *"점검 도구가 대상의 전제를 모른 채 일반 임계를 적용한다"* 와 같은 계열의 병이다.
- **영향**: 장중 내내 화면이 *"오늘 소급 불가 손실 없음"* 이라 말했고, 장후 예산은 오늘 몫 **5.0분**을 차감했다. 그리고 `IrrecoverableLossBudgetExceeded`(WARNING, 15:48:34)가 *"5거래일에 58분 (> 예산 20분)"* 으로 울렸다 — 오늘 기여분은 장중에 한 번도 보이지 않았다. 손실 예산이라는 축의 조기 경보 기능이 죽는다.
- **신규 여부**: **신규.**

---

### 확인 필요 (확정 아님)

- **`option_chain/regular`의 머리 구멍 5분이 결함인가 카덴스인가.** `regular`는 카덴스 5분이고 창 시작 08:20, 첫 행 08:25다. 즉 **첫 사이클을 정상적으로 기다린 5분**일 가능성이 높다(`weekly_mon`은 카덴스 10분에 head_gap 1.0으로 성격이 다르다). 그렇다면 `irrecoverable_loss_minutes`가 카덴스를 손실로 세고 있다는 뜻이고, 5거래일 58분 예산 초과의 상당 부분이 위양성이다. → **무엇을 보면 판정되나**: `series_coverage._is_irrecoverable()`와 `head_gap_minutes` 계산이 `cadence_minutes`를 차감하는지 코드로 확인. 차감하지 않는다면 2-2와 같은 뿌리의 결함이며 우선순위가 P1로 오른다.
- **`meta` 통과확률의 실제 분포.** 14:30에 `gate=pass`가 처음 나왔으므로 임계 0.7을 넘는 사이클이 존재한다는 것은 실측으로 확정됐다. 그러나 확률값 자체는 여전히 로그에 없다. → **무엇을 보면 판정되나**: F-0818I-1 적용 후 하루치 `MetaGateEvaluated`. **임계는 건드리지 않는다**(R18).
- **P-9 UI 스냅샷 신선도.** `logs/ui_20260818.log` 7줄(377B) · `ui_20260818.err.log` 미생성(오류 0) · `command_center_ui.json` 08:20:32. 화면이 무엇을 그렸는지는 **어느 파일에도 없다.** 장전·장중에 이어 장후에도 판정 불가. → G-0818I-4(스냅샷 신선도 로그화) 적용 전까지 판정 보류. 다음 연휴는 09-24(추석)라 자연 관측 기회는 5주 뒤다.

---

### 장전·장중이 남긴 「확인 필요」 — 오늘의 고유 수확

장후만 볼 수 있는 것들이다. 어제·오늘 아침에 보류한 항목 전부에 결론을 낸다.

| 출처 | 항목 | **장후 결론** |
|---|---|---|
| 장전 | `clock-sync-restored`가 07:23 회차 `+2.016s`로 위양성 재발할까 | **기우였다.** `daily_integrity` `clock_skew_seconds=1.777`(08:45 개장 실측 `ClockSkewMeasured` 채택, samples=30). 등록부 **`검증 완료` 8거래일 연속**. 기동창 거절 회차를 세지 않았다 — F-P2와 같은 형태의 결함은 이 지표에 없다 |
| 장전 | P-4 `abnormal_exits` | **통과.** `abnormal_exits: []` · `restarts: 0` · `ui_restarts: 0` |
| 장전 | P-5 `task_exit_codes` | **판정 불가 종결** → 1-3 |
| 장전 | P-6 `regime-not-constant` 연속 카운터 실값 | **오늘 값 `regime_unknown_ratio=0.0` 충족.** 다만 등록부는 08-14 위반에서 `break`라 카운터가 돌지 않는다 → 1-1 |
| 장전 | P-7 등록부 기한 초과 | **실질 문제 없음.** 기한 경과 6건(`ui-crash-isolation`·`crash-forensics-armed`·`tick-collection-live`·`horizon-volume-identity`·`crash-count-measurable`·`boot-recovery-armed`) 전부 **`검증 완료`** 상태. 위험은 경과분이 아니라 임박분이다 → 2-1 |
| 장전 | P-8 `delivery_latency` p99가 부하로 나빠지는가 | **가설 기각 확정.** p50 0.5204 / p90 0.9271 / **p99 1.0323** / max 1.2988 (samples 20,000). 휴장일 관측과 사실상 동일 |
| 장전 | P-10 W-9 분봉 420 vs 395 | **410으로 확정.** 1m 410봉 08:45~15:34 = 장전 15분(`pre_open_minutes: 15`) + 정규장 395분. 결손 0분·최장 공백 0분 |
| 장전 | P-9 UI 스냅샷 | **판정 불가** (위 확인 필요) |
| 장중 | X-1 `TickDeliveryLatency` 존재·측정 여부 | **통과.** 1건, samples 20,000 |
| 장중 | X-2 ★ `steps_run == 6` | **통과.** `steps_planned 6 / steps_run 6 / steps_failed 0 / steps_with_findings 0`. → **DECISION_LOG 「라이브 미검증 L15」(08-17 비거래일 게이트의 거래일 회귀, 기한 오늘) 닫힌다.** 게이트가 거래일에 회귀를 일으키지 않았다 |
| 장중 | X-3 ★ 종일 `gate` 분포 | **`{score: 12, regime: 1, pass: 1}`** — `regime`이 **09:00:01 단건**. F-0818I-2의 「첫 사이클」 구조 **확정**. 더 넓게 틀린 것이 아니다 |
| 장중 | X-4 `AggregatorNoContribution` 종일 | **13건 전부 `blocked_by_meta=['30m']`** · `blocked_by_uncertainty` 전건 빈 배열. 14사이클 중 13 — 나머지 1건(14:30)은 **차단이 아니라 통과**다. W-21 종일 확정, 다른 갈래 혼입 없음 |
| 장중 | X-5 ★ 국면 어긋남 | **2/13.** 09:00:01 `UNKNOWN` vs 09:00:00 `TREND_DOWN`(첫 사이클) · 12:30:01 `HIGH_VOL` vs 12:30:00 `RANGE`(국면 전환 순간). 기준 *"2/13 초과면"* 에 **미달** — 오전 2/10 대비 빈도 증가 없음. F-0818I-2a 진단 유지, 긴급도 상향 근거 없음 |
| 장중 | X-6 `degenerate_feature_count` | **0** (08-14는 **57**). 판정된 4개 Horizon(1m 409·3m 136·5m 81·10m 41) 전부 `always_nan: []` · `constant: []`. **08-16 P0-1(웜스타트 적재 필터)이 들었다는 강한 증거** |
| 장중 | `delivery_latency` p99와 1-3(완성봉 500ms 초과)의 관계 | **원인이 뒤집혔다.** p50이 **0.5204s** — 완성봉 유예 500ms를 **중앙값이 이미 넘는다.** 장중 1-3을 「발행 오프셋」 문제로 봤는데, 실제로는 **회선 도달 지연 자체가 예산보다 크다.** F-0818I-3(자가점검이 완성봉 예산을 별도 축으로 판정)의 방향은 옳고, 처방은 「발행 시각 계측」이 아니라 「예산을 회선 실측에 연동」이어야 한다 → G-C |
| 장중 | `meta` 통과확률 분포 | **여전히 미계측**(위 확인 필요). 단 14:30 통과로 *"임계 0.7을 넘는 사이클이 존재한다"* 는 확정됐다 |

---

### 긍정 관측 — 결함 아님, 다음 점검의 출발점

**오늘이 D-day 1일차이고, 무결한 하루였다.**

- **데이터 무결.** `volume_check` 비율 **1.000**(공통 410분 · 아카이브 150,787 / 공식 150,787 · 결손 0분) · `bar_continuity` 1m 410봉 결손 0분·최장 공백 0분 · `horizon_findings: []` · `data_flow_findings: []` · `series_findings: []` · `series_contract: []` · `late_bar_drops: 0` · `tick_rows: 139,958` · `flat_price_minutes: 0`.
- **종료 시퀀스 정상(R13·금지계명 14).** `l1_daily` 15:37:31 정상 종료 · `g2_paper` 15:35:00 정상 종료 · `Messiah-Shutdown` 15:40:00~15:40:01 동작(`shutdown_watchdog.log`, 잔여 프로세스 없음) · `postmarket` 15:48:34 정상 종료.
- **장후 배치 6/6 완주, 발견 0.** 조각 통합(멱등) → 상위 Horizon 재합성(304행) → 거래량 대조(1.000) → 변동성 채점 → 롤 겹침(비만기일, 할 일 없음) → 무결성 리포트.
- **파이프라인 전 구간 최초 관통.** 14:30:00 `RegimeClassified TREND_UP(0.9946)` → 14:30:00.878 **`DecisionEmitted gate=pass` S=0.511(임계 ±0.2) LONG, n_experts=1** → 14:30:00.908 `RiskReject "Net ER -1.62틱 ≤ 0 (Ver 1.1 §4-2)"`. 관측 이래 **처음으로 meta 게이트를 넘은 판단이 나왔고 리스크단이 규정대로 기각**했다. `blocked_by_meta` 벽 뒤의 경로가 살아 있다는 첫 증거다.
- **국면이 상수가 아니다.** `regime_distribution: {HIGH_VOL 5, TREND_UP 5, RANGE 2, TREND_DOWN 2}` · UNKNOWN **0%** (08-14 라이브는 14/14 UNKNOWN). **W-26 종일 확정.**
- **코드 동결 지켜졌다(금지계명 10).** `git diff -w --ignore-cr-at-eol -- src scripts configs` 빈 출력 · 당일 커밋 0건 · `session_git_shas: ["ef9807c"]` 단일 · `code_version.stale: false` · 전 프로세스 동일 sha.
- **외부 API 실패가 조용하지 않다(R10).** KIS 500/disconnect 종일 9건 전부 `InvestorFlowPollRetried`(5)·`OptionChainPollRetried`(4)로 1회 재시도 복구, INFO 명시. 종일 산발 — 장전 창의 성질이 아니라 상시 배경 잡음(F-3 긴급도 하향 유지).
- **옵션체인 전량.** `OptionChainPolled` 174건 · 3계열 커버리지 `regular` 99.1% · `weekly_mon` 100% · `weekly_thu` 100% · `short_cycles: []`(다리 부족 0).
- **로그 위생.** l1 `ERROR` 0 · `WARNING` 1건뿐이고 그 1건(`DailyCloseBarHandedOff` 15:35:06)은 **설계된 폴백 배지**다 — DECISION_LOG 2683행 *"종료 경로에서만 합성기에 직접 전달하도록 했다(`DailyCloseBarHandedOff`, WARNING) … 우회한 사실은 매일 로그에 남긴다(R10)."* **결함 아님.** g2 `ERROR`·`WARNING` 각 0건.
- **`g2_daily_returns.jsonl`에 당일 행 추가됨** — `{"date": "2026-08-18", "symbol": "A05609", "return": 0.0}`. `self_eval` `wiring_stage: "주문 미발생"` · 판단 14건 · 주문 0건 — 설계 단계와 일치.
- **변동성 축 채점 정상.** 5m 표본 158(기준선 IC +0.292) · 15m 표본 48(+0.365) 측정 가능, 기준선 초과 0/7. 30m은 표본 21 < 30으로 **판정하지 않음**(0으로 안 내림 — L18 준수).

---

## 2. Fix 작업 구현계획

> **본 예약 실행은 보고까지만 한다.** 아래는 계획이며, 실제 구현은 사용자가 *"구현해"* 라고 지시할 때 착수한다. 장후는 적용 가능한 유일한 국면이다(R11 · 금지계명 3·4).

### F-0818P-1 (P1) ★ 최우선 — 채점기가 「최근 위반」과 「오늘 위반」을 말한다 · 대응 이상점 1-1

- **원인**: `evaluate()`가 최초 위반에서 `break`해 이후 날짜를 채점하지 않는다. 회복도 재악화도 보이지 않는다.
- **변경 파일**: `src/messiah/ops/fix_verification.py`
  - `evaluate()` — `break`를 제거하고 **전 구간을 끝까지 순회**한다. 수집할 값 4개: `first_violation`(현행 `violated_on`, 의미 보존) · `last_violation`(가장 최근 위반일) · `clean_streak`(마지막 위반 이후 연속 통과일 수) · `violated_today`(bool).
  - `VerificationVerdict`에 `last_violation: date | None` · `clean_streak: int` · `violated_today: bool` 추가. **기존 `violated_on`·`clean`은 이름과 의미를 바꾸지 않는다** — 다른 소비처(`daily_integrity_report.py`)를 흔들지 않기 위해서다.
  - `_verdict_for()` 문구 개정:
    - 오늘 위반: `⚠ 오늘 기준 위반 — {metric}={value} (최초 위반 {first}, 그 뒤 {n}회)` ← **오늘 것이 문장 맨 앞에 온다**
    - 오늘 충족 + 과거 위반: `재발 이력 있음(최초 {first}, 최근 {last}) — 그 뒤 {streak}거래일 연속 충족` **레벨을 `WARNING`으로 내린다**(현행 ERROR). 회복 중인 항목이 매일 ERROR를 내는 것이 늑대소년의 정체다.
  - `VerificationStatus`에 `RECOVERING = "회복 중"` 추가. `RECURRED`는 **오늘 위반한 항목 전용**으로 좁힌다.
- **회귀 위험**: 중간. `RECURRED`의 의미가 좁아지므로 이 상태를 읽는 곳을 전부 확인해야 한다 — `grep -rn "VerificationStatus.RECURRED\|needs_attention" src/ scripts/`. `needs_attention()`(fix_verification.py:549 *"사람이 반드시 봐야 하는 판정"*)은 `RECURRED` + `OVERDUE` + **신규 `RECOVERING`은 제외**로 정의한다.
- **검증 방법**:
  1. `pytest tests/ops/test_fix_verification.py` — 신규 케이스 3건: 위반 후 회복(`RECOVERING`) · 위반 후 재위반(`RECURRED`, `last_violation`이 최근일) · 오늘 위반(`violated_today=True`).
  2. **오늘 데이터로 재현 검증**: `python scripts/daily_integrity_report.py --date 2026-08-18 --symbol A05609 --configs configs` 재실행 후 `daily-axes-measured` 한 건만 `RECURRED`, 나머지 9건이 `RECOVERING`으로 나오는지 확인. 이 보고서 §1-1 표가 정답지다.
  3. 08-14 데이터로도 돌려 **과거 판정이 뒤집히지 않는지** 확인(그날은 실제로 여러 건이 위반이었다).
- **왜 `since:` 리셋으로 때우지 않는가**: `since:`는 사람이 매번 밀어야 하고, 밀지 않으면 오늘 같은 일이 반복된다. B-3의 처방이 오늘 실패한 이유가 바로 그 수동성이다. 자동으로 보이게 하는 것이 옳다.

### F-0818P-2 (P1) `unmeasured`를 「구조적 미측정」과 「측정 실패」로 가른다 · 대응 이상점 1-2·1-3

- **원인**: `unmeasured_count`가 성격이 다른 셋을 한 통에 넣는다 — (a) 표본이 아직 안 쌓인 것(15m/30m 롤링, **시간이 해결**), (b) 도구가 실패한 것(`TimeoutExpired`, **고쳐야 함**), (c) 로그가 안 남은 것.
- **변경 파일**:
  - `src/messiah/ops/integrity_report.py` — `unmeasured: list[str]`를 유지하되 **`unmeasured_kinds: dict[str, list[str]]`** 를 병기(`{"accruing": [...], "failed": [...], "absent": [...]}`). 기존 필드는 지우지 않는다(과거 리포트 호환).
  - `src/messiah/ops/fix_verification.py:424` — `"unmeasured_count"` 추출기를 **`failed` + `absent`만 세도록** 변경. `accruing`은 세지 않는다 — 표본이 쌓이는 중인 것은 결함이 아니고, 그걸 세면 새 계측축을 켤 때마다 등록부가 며칠씩 거짓 위반을 낸다(오늘 실제로 그랬다).
  - 판정 문구에 근거를 남긴다: `unmeasured_count=1 (누적 대기 2건 제외: 15m 27/30, 30m 14/30)`.
- **회귀 위험**: 낮음. 축 자체가 늘어나지 않고 분류만 붙는다.
- **검증 방법**: `pytest tests/ops/test_integrity_report.py`(⚠ Docker 의존 2건 실패 중 — DECISION_LOG 08-17. Docker Desktop 기동 후 실행) + 오늘 리포트 재산출 시 `unmeasured_count`가 **3 → 1**(진입점 종료 코드만)이 되는지 확인.

### F-0818P-3 (P1) `task_exit_codes` 지표 교체 — 연장이 아니다 · 대응 이상점 1-3

- **근거**: 어제 결정한 분기 조건(*"08-18에도 None이면 지표 교체"*)이 오늘 발동했다. F-D 재시도(2회)가 이미 들어간 뒤에도 실패했으므로 재시도 횟수를 더 늘리는 것은 답이 아니다.
- **변경 파일**:
  - `src/messiah/ops/task_exit_codes.py` — `schtasks` 동기 조회(현행)를 **배치 시작 시점 비동기 선조회 + 결과 캐시**로 바꾼다. 장후 배치는 15:45에 시작해 15:48에 끝나므로 3분의 여유가 있는데 지금은 리포트 생성 시점에 동기 호출해 타임아웃에 걸린다. `scripts/run_postmarket.py`의 1단계 진입 직후 조회를 띄우고 6단계에서 결과만 받는다.
  - 대안 경로(위가 또 실패하면): `schtasks` 대신 **`.bat`가 자기 종료 코드를 파일로 남긴다** — `scripts/run_postmarket.bat` 말미에 `echo %ERRORLEVEL% > logs\exit_postmarket_%DATE%.txt`. `run_l1_daily`·`run_g2_paper_trading` 래퍼도 동일. 로그와 OS가 같은 말을 하는지 보는 것이 이 축의 목적이므로 **OS에게 묻는 경로를 하나 더 두는 것**이 지표 교체의 실질이다.
  - `configs/pending_verifications.yaml` — `exit-code-matches-log`의 `metric`을 교체 지표로 바꾸고 **교체 사유를 주석으로 박는다**(DECISION_LOG 규율).
- **회귀 위험**: 낮음(관측 전용, 거래 경로 무관). `.bat` 변경은 스케줄 등록과 무관하므로 `schedule-single-source` 검증에 영향 없다.
- **검증 방법**: 다음 거래일 장후 `task_exit_codes.available == true` · `unmeasured`에서 「진입점 종료 코드」 소멸 (NEXT_TODO **W-12**·**W-29**가 이미 이 문장으로 대기 중이다).

### F-0818P-4 (P2) F-5 기한 재조정 적용 · 대응 이상점 2-1

- **변경 파일**: `configs/pending_verifications.yaml` — `daily-axes-measured` 08-19→**08-25** · `composer-bucket-completeness` 08-19→**08-24** · `no-degenerate-features` 08-20→**08-26** · `exit-code-matches-log`는 기한 연장이 아니라 F-0818P-3의 지표 교체로 처리(등록 재시작).
- **각 항목에 연장 사유 주석을 박는다** — *"2026-08-18 장후: 08-17 휴장으로 채점 가능일이 2일뿐이었고, 신규 롤링 축 도입으로 08-18 `unmeasured`가 구조적으로 3건. **연장은 1회로 제한**한다."*
- **동반 변경(구조 해법, 어제 G-2로 제안된 것)**: `fix_verification.py`에 `deadline_trading_days` 신설 + **`기한 초과`(못 고쳤다)와 `기한 불가 — 재조정 필요`(채점할 날이 없었다)를 다른 판정으로 분리**. 이게 없으면 연장은 매번 반복된다.
- **검증 방법**: 재산출 시 세 항목이 `기한 초과`로 안 뜨는지 + 08-24~08-26에 실제로 채점이 성립하는지 08-19부터 매일 `clean_streak` 추적(F-0818P-1이 그 값을 만든다).

### F-0818P-5 (P2) 「소급 불가 손실」 정의를 하나로 · 대응 이상점 2-2

- **변경 파일**:
  - `src/messiah/ops/integrity_report.py:1017 irrecoverable_loss_minutes()` — **머리 구멍에서 해당 계열의 `cadence_minutes`를 차감**한다. `option_chain/regular`는 카덴스 5분이라 첫 행이 창 시작 5분 뒤에 오는 것이 정상이며, 그것을 손실로 세면 예산이 매일 위양성으로 채워진다. (→ §확인 필요 첫 항목이 이 변경으로 함께 판정된다.)
  - `status_snapshot` 생성부 — `irrecoverable_loss`에 `minutes` 필드를 추가하고 `summary`가 **분 단위 값을 말하게** 한다. `clean`은 `minutes == 0`으로 재정의해 두 표면이 같은 정의를 쓴다.
- **회귀 위험**: 중간 — 과거 5거래일 이동합(58분)이 재계산되면 `IrrecoverableLossBudgetExceeded` 경보가 사라질 수 있다. **그게 옳은 결과인지 먼저 확인**한다: 08-10(41분)·08-14(33분)은 실제 사고였고 카덴스 차감으로도 남아야 한다. 남지 않으면 차감이 과도한 것이다.
- **검증 방법**: 08-10·08-14 리포트 재산출 후 두 날의 손실이 유지되는지 + 오늘 값이 5.0 → 0.0이 되는지 대조. `pytest tests/ops/` 해당 범위.

### 적용 순서와 커밋 계획

1. **F-0818P-1**(채점기) — 다른 모든 항목의 채점이 여기 걸려 있다. 단독 커밋.
2. **F-0818P-2**(unmeasured 분류) — 1의 판정이 정확해진 뒤에 축을 정리해야 효과를 볼 수 있다.
3. **F-0818P-3**(지표 교체) + **F-0818P-4**(기한 재조정) — 같은 커밋. 둘 다 등록부 손질이다.
4. **F-0818P-5**(손실 정의) — 회귀 위험이 가장 크므로 마지막. 과거 재산출 대조 후에만 커밋.
5. 장중에서 이월된 **F-0818I-1~4**(meta 확률 계측 · 국면 시드 · 완성봉 예산 · 점검 지각) — 위 넷과 파일이 겹치지 않으므로 병행 가능하나, `integrity_report.py`를 건드리는 F-0818I-3은 F-0818P-2 뒤에 둔다.

커밋 메시지 첫 단어는 **`[MW0601]`**. 변경 후 `pytest`(해당 범위) + replay 검증 — 금지계명 2. 미커밋 변경을 실전에 반입하지 않는다 — 금지계명 10.

---

## 3. 고도화 방안

> 전부 **오늘 관측**에서 출발한다.

### G-0818P-1. 등록부 일일 스코어보드 — 「오늘 몇 개가 회복됐나」를 한 줄로

**당일 근거**: 오늘 재발 11건 중 **9건이 기준을 충족**했고 그중 `degenerate 57→0`은 08-16 P0-1의 직접 성과인데, 어느 산출물도 그 사실을 말하지 않는다. 이 보고서를 쓰면서 추출기를 수동으로 돌려서야 알았다.

**제안**: `daily_integrity_*.json`에 `verification_scoreboard` 신설 —
```json
{"today_violating": ["daily-axes-measured"], "recovering": [{"id": "no-degenerate-features",
 "streak": 1, "last_violation": "2026-08-13", "value": 0.0, "prev_value": 57.0}],
 "clean": [...], "unjudgeable": ["exit-code-matches-log"]}
```
장후 로그 말미에 **한 줄 요약**: `등록부 23건 — 오늘 위반 1 · 회복 중 9 · 검증 완료 12 · 판정 불가 1`. F-0818P-1이 만드는 값을 그대로 쓴다(추가 계산 없음).

### G-0818P-2. 완성봉 유예를 회선 실측에 연동한다

**당일 근거**: `delivery_latency` **p50 0.5204s** — 완성봉 유예 **500ms**를 중앙값이 넘는다. p90 0.9271 · p99 1.0323. 기동 자가점검은 `clock offset=+2.016s`에 *"경고: 완성봉 유예 500ms보다 큼(임계 2초)"* 를 붙이면서 **정작 회선 지연은 예산과 대조하지 않는다.** 장중 1-3(발행 500ms 상시 초과 69.6%)의 원인이 발행 로직이 아니라 여기 있었다.

**제안**: `scripts/self_check.py`의 `bar_close` 축이 **직전 거래일 `delivery_latency.p90`을 읽어** 완성봉 유예와 대조한다. `p90 > 유예`면 `[WARN] bar_close 유예 500ms < 전일 회선 p90 927ms — 완성봉이 늦은 틱을 놓칠 수 있다`. 임계를 자동으로 바꾸지는 않는다(R18) — **말하게만 한다.** 유예 조정은 며칠치 분포를 본 뒤 별건으로 결정한다.

### G-0818P-3. `gate=pass` 사이클을 별도로 보존한다

**당일 근거**: 14:30 단 한 사이클이 관측 이래 처음으로 meta 게이트를 넘었고(`S=0.511`, `n_experts=1`), 리스크단이 `Net ER -1.62틱`으로 기각했다. **하루 14사이클 중 1건**이라 다음에 언제 또 나올지 모르는데, 지금 남은 것은 로그 3줄이 전부다.

**제안**: `gate=pass`인 사이클에 한해 **입력 스냅샷을 파일로 떨군다** — `logs/pass_cycles/2026-08-18T1430_A05609.json`에 `ExpertView` 원본 · `meta_features` · 국면 · `Net ER` 계산 내역. 하루 최대 몇 건이므로 용량 부담이 없고, **W-21의 벽이 뚫린 순간의 입력을 재현 가능하게 보존**하는 것이 이 시점에 가장 값진 데이터다. 리허설 경로와 대조할 첫 라이브 표본이기도 하다.

### G-0818P-4. 새 계측축을 켤 때 「누적 대기 기간」을 등록부가 미리 안다

**당일 근거**: `feature_health_rolling`이 오늘 처음 켜지면서 `unmeasured`가 1→3이 됐고, 그것이 **기한이 내일인 항목을 충족 불가로 만들었다.** 축을 켠 것은 옳은 일인데 등록부가 그 대가를 예상하지 못했다.

**제안**: `configs/pending_verifications.yaml`에 `warmup_trading_days` 필드 신설. 새 축 도입 시 그 값만큼은 **`판정 불가`이며 기한 카운터를 멈춘다.** 30m은 하루 14~15표본이므로 `warmup_trading_days: 3`이 자연스러운 값이다(14×3=42 ≥ 30). 계측을 늘리는 일이 등록부에 벌점이 되지 않게 한다.

### 로드맵 반영 제안

`Derivatives_AI_Master_Plan_Ver2.0.md` W단계 관측 항목에 **「등록부 회복률」**을 추가할 것을 제안한다. 지금 로드맵은 결함 발생만 보고 회복을 보지 않는데, 오늘처럼 9건이 한꺼번에 회복되는 날이 기록되지 않으면 **수정이 듣는지 아닌지를 장기적으로 판단할 근거가 없다.**

---

## 4. 다음 거래일(2026-08-19) 관측 예정

| ID | 무엇을 보는가 | 통과 기준 |
|---|---|---|
| **Y-1 ★** | F-0818P-1 적용 후 등록부 판정 | `RECURRED` 1건 이하 · `RECOVERING` 9건 등장 · 오늘 위반이 문장 맨 앞 |
| **Y-2 ★** | `unmeasured` 내용 | 15m `samples ≥ 54`(2일 누적)로 `judged=True` 전환 · 30m은 여전히 대기(정상) |
| **Y-3 ★** | `task_exit_codes.available` | `true`. 또 `TimeoutExpired`면 F-0818P-3의 `.bat` 대안 경로로 즉시 전환 |
| **Y-4** | `irrecoverable_loss_minutes` | F-0818P-5 적용 시 오늘 5.0 → 0.0. 08-10·08-14 재산출에서 41·33은 **유지**되어야 한다 |
| **Y-5** | `gate` 분포에 `no_expert` 등장(F-0818I-1) · `RegimeSeeded` 1건(F-0818I-2) | X-7·X-8과 동일 |
| **Y-6** | `gate=pass` 재출현 빈도 | 오늘 1/14. 0건이면 14:30이 예외적 사건이었다는 뜻 → G-0818P-3의 보존 가치가 더 커진다 |
| **Y-7** | 장전 점검이 09:00 전에 나오는가 | X-10 이월. 또 늦으면 F-0818I-4 「스케줄러 이관」 확정 |
| **Y-8** | `delivery_latency` p50 | 오늘 0.5204. 이틀 연속 500ms 초과면 G-0818P-2의 유예 재검토를 별건으로 승격 |

---

## 5. 재시동 권고

**재시동하지 않는다.**

| | 얻는 것 | 잃는 것 |
|---|---|---|
| **재시동 없이** | D-day 1일차 **무중단 기록**의 연속성 · 프로세스 상태 보존 · 다음 거래일 로그가 오늘과 같은 코드(`ef9807c`)의 결과임이 확실 | 없음 — 적용할 새 코드가 없다 |
| **재시동으로** | **없음** | 오늘 기록의 연속성 |

**근거**: `logs/status_snapshot.json`의 `code_version.stale` = **`false`** (`process_git_sha: "ef9807c"` == `head_git_sha: "ef9807c"`, *"코드 ef9807c — 전 프로세스 동일"*). 당일 커밋 0건이고 `git diff -w --ignore-cr-at-eol -- src scripts configs`가 빈 출력이므로 **프로세스가 옛 코드로 돌고 있는 상태가 아니다.** 재시동으로 새로 적용될 것이 없는데 재시동하면 잃기만 한다.

**단, 조건부다.** 위 Fix(F-0818P-1~5)를 오늘 장후에 **실제로 커밋한다면**, 그때는 `stale`이 `true`로 바뀐다. 그 경우 재시동 시점은 **오늘 밤이 아니라 내일 08:20 정시 트리거**다 — 스케줄 기동이 어차피 새 프로세스를 띄우므로 별도 재시동은 불필요하고, 오늘 밤에 띄우면 `SessionStart`가 하루에 셋이 되어 `restarts` 축이 오염된다.

---

## 6. dev_memory 반영

- `dev_memory/DECISION_LOG.md` — `## [MW0601] 하루는 설계대로였고, 그 사실을 채점하는 도구가 아니었다 — D-day 1일차 장후 (2026-08-18)` 항목 append.
- `dev_memory/NEXT_TODO.md` — F-0818P-1~5 · G-0818P-1~4 · Y-1~Y-8 체크박스 추가.
- 오늘 닫히는 항목: **X-1·X-2·X-3·X-4·X-5·X-6** 전부 결론 · **P-1·P-3·P-4·P-6·P-7·P-8·P-10** 결론 · **W-16·W-21·W-22·W-37·W-26** 종일 확정 · DECISION_LOG **「라이브 미검증 L15」**(08-17 비거래일 게이트 거래일 회귀) 통과로 마감 · **P-5** 판정 불가로 종결.
- 이월: **P-9**(UI 스냅샷, G-0818I-4 적용까지) · **F-3**(수급 재시도 소진율, 긴급도 하향 유지) · **G-2**(반복 ERROR 접기 — l1·g2 ERROR 0건이 이틀째라 **근거 소멸, 항목 폐기 권고**. 단 장후 `FixVerificationRecurred` 11건이 같은 형태의 문제이므로 **F-0818P-1이 그 자리를 대신한다**).
