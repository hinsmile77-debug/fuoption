# MESSIAH 일일 점검 — 2026-08-20 / 장전

- 점검 시각: 08:55 KST (증거 수집 08:51:22)
- 대상 국면: pre
- HEAD `50eff6c` · 실행 중 `50eff6c` · 미커밋 274건(`src/`+`scripts/` 실변경 14파일)
- 증거: `logs/dailycheck/evidence_20260820_pre.md`
- 모드: `messiah-dev-01` / **dev · simulator** — 실계좌 위험 없음
- **코드 변경 없음** (08:45 예약 · 09:00 개장 임박 — SYSTEM.md R11 / 금지계명 3·4)

## 0. 한 줄 결론

**기동 자격 자체는 통과했다(self-check PASS · 전 항목 OK · 공백 0). 그러나 어제 장후에 "구현 완료"로 기록한 F-1~F-6·G-3/G-4가 커밋되지 않았고, 08:25:30 기동한 G2 프로세스에는 그 중 F-5(국면 시드)가 실려 있지 않다** — 오늘 09:00 첫 사이클은 어제와 같은 `regime=UNKNOWN`으로 나갈 가능성이 높고, 오늘 예정된 J-5/J-5b 판정은 "설계 실패"가 아니라 "미반영"으로 읽어야 한다.

| 국면 | 판정 | 근거 요약 |
|---|---|---|
| 장전 | **조건부 정상** | 기동·자가점검·데이터 준비 전부 통과. 다만 어제 구현분 미반영(1-1)과 손실원장 오표기(1-3)가 오늘의 관측 기준선을 오염시킨다 |
| 장중 | 미도래 | — |
| 장후 | 미도래 | — |

**⚠ P0: 없음.** dev/simulator 모드이고 실주문 경로가 없으므로 오늘 거래 자격에 직접 위험은 없다. 개장 전 사람의 즉시 판단을 요구하는 항목도 없다 — **아래 P1은 전부 장후 적용을 권고한다.**

---

## 1. 이상점 정리보고

### P0 — 오늘 거래에 직접 위험

**해당 없음.**

---

### P1 — 정확성·관측 훼손

#### 1-1. 어제 "구현 완료"로 기록한 F-1~F-6·G-3/G-4가 미커밋 상태이고, 08:25 기동한 G2에 F-5가 실려 있지 않다

- **증상**: `dev_memory/NEXT_TODO.md`는 F-1~F-6·G-3/G-4를 `[x]`로 닫았고 DECISION_LOG에도 "구현 (2026-08-20)"으로 적혀 있다. 그런데 해당 코드는 전부 워킹트리 전용이며, 오늘 08:25:30에 뜬 G2 프로세스의 로그에는 F-5가 남기게 되어 있는 **세 갈래 출력 중 어느 것도 없다.**

- **근거**:

  실행 로그 — `logs/g2_daily_20260820.log`, 08:25:30~08:25:31 연속 4줄:
  ```
  국면 결선 — RegimeAI 상태 5개 · 명명 {0:HIGH_VOL, 1:RANGE, 2:RANGE, 3:TREND_UP, 4:TREND_DOWN} · 구동 30m
  08:25:31.646153 [INFO] RegimeWarmStart 과거 완성봉으로 국면 이력 사전 충전 (용량 200봉)
                         bars=200 min_bars=22 bars_by_source={"A05609": 41, "A05608": 159}
  국면 웜스타트: 200봉 (하한 22봉) · 출처 {'A05609': 41, 'A05608': 159}
  G2 페이퍼 운영 시작 — 2026-08-20T15:35:00+09:00까지 (25768초)
  ```
  `scripts/run_g2_paper_trading.py:635`는 `_load_regime_runtime()` **직후·`SimBroker` 생성 직전**에
  `await _seed_regime(...)`를 부르며, `_seed_regime`(:351~404)은 어떤 경로로도 침묵하지 않게 짜여 있다 —
  성공이면 `RegimeSeeded`(INFO), 하한 미달/UNKNOWN이면 `print("국면 시드 없음 …")`,
  예외면 `RegimeWarmStartFailed`. **셋 다 로그에 0건이다.**
  또한 버퍼는 200봉 · 하한 22봉으로 `seed()`의 `len(history) < min_bars` 조기 반환 조건에도 걸리지 않는다.

  코드 상태 — 세 곳 전부 미커밋 추가분:
  ```
  $ git log --oneline -1 -- scripts/run_g2_paper_trading.py
  06bdb0f   (HEAD 50eff6c 보다 4커밋 이전)
  $ git diff -- scripts/run_g2_paper_trading.py            | grep -c '^+.*_seed_regime'      → 2
  $ git diff -- src/messiah/strategy/regime/runtime.py     | grep -c '^+.*async def seed'    → 1
  $ git diff -- src/messiah/core/logging.py                | grep -c '^+.*RegimeSeeded'      → 1
  $ git ls-files configs/incident_causes.yaml | wc -l                                        → 0   (F-6 신설 파일, untracked)
  ```

- **기준 위반**:
  - SYSTEM.md **금지계명 12(조용한 폴백 금지)** — 설계상 반드시 흔적을 남기게 만든 분기가 흔적을 남기지 않았다면, 그 코드가 실행되지 않은 것이다.
  - SYSTEM.md **§7 dev_memory 기록 의무** — 기록(`[x]` 완료)과 운영 실체가 어긋난다.
  - **직전 커밋 `50eff6c`의 제목과 같은 유형**: *"장중에 재기동해야 하는데 실릴 코드가 커밋에 없었다"*. 그 사고를 겪고 커밋한 다음 날, 같은 형태가 재현됐다.

- **영향**:
  - 오늘 09:00 첫 사이클의 `AggregatorNoContribution.regime`은 **여전히 `UNKNOWN`**으로 나갈 공산이 크다 → 집계기 가중치표 폴백 + Meta 임계 +0.10. 즉 오늘 개장 첫 판단도 가장 보수적인 국면 가정으로 나간다(F-5가 없애려던 바로 그 상태).
  - **오늘 예정된 판정 J-5 / J-5b / J-4b / J-2b / J-12가 전부 무효**다. 실패로 읽으면 "F-5 설계가 틀렸다"는 오판을 낳는다. 판정 전제(코드 반영)가 성립하지 않았다.
  - 장중 재기동을 하면 **그때부터는** 워킹트리 코드가 실린다 → 하루 안에 두 가지 코드가 도는 상태가 되고, `git_sha`는 둘 다 `50eff6c`로 같아 구분되지 않는다(→ 1-2).

- **신규 여부**: **재발**(유형) — `50eff6c`가 고친 것과 같은 "기록된 구현 ≠ 실린 코드". 다만 F-5 미반영이라는 구체 사실은 **오늘 신규 관측**.

- **확인 필요**: 워킹트리 편집이 08:25:30 **이전**이었다면 위 추론이 뒤집힌다. 다만 그 경우에도 `_seed_regime`이 침묵한 사실은 그대로 남아 더 심각한 결함(무출력 경로)이 된다. 어느 쪽이든 조사 대상이다.

---

#### 1-2. `code_version`이 워킹트리 오염을 보지 못한다 — J-9(장전 F-2) 3일째 미적용

- **증상**: `src/`+`scripts/`에 실변경 14파일이 미커밋으로 남아 있는데, 계기는 "전 프로세스 동일"이라고 말한다.

- **근거**: `logs/status_snapshot.json` (08:51:26)
  ```json
  "code_version": {
    "process_git_sha": "50eff6c", "head_git_sha": "50eff6c",
    "stale": false, "summary": "코드 50eff6c — 전 프로세스 동일"
  }
  ```
  ```
  $ grep -rn "worktree_dirty" src/ scripts/   → 0건 (필드 자체가 존재하지 않음)
  ```
  자가점검도 `[OK ] git  dirty(dev 허용)` 한 줄로 통과시킨다 — dirty의 **크기**를 말하지 않는다.

- **기준 위반**: `dev_memory/NEXT_TODO.md` **J-9** `code_version.worktree_dirty == false (장전 F-2 적용 후) — A-2 이월`. SYSTEM.md **R10 / 금지계명 12**(상태를 배지 없이 통과시킴).

- **영향**: 1-1을 아침에 자동으로 잡아낼 수 있는 **유일한 계기가 없다.** 오늘도 사람이 `git diff`를 쳐서 찾았다. `stale: false`는 "실린 코드 = 커밋된 코드"라는 **틀린 안심**을 준다.

- **신규 여부**: 기존 TODO(A-2 → J-9), 3거래일 이월. 다만 **1-1로 인해 이 항목의 우선순위 근거가 오늘 처음 실증됐다.**

---

#### 1-3. 개장 전 08:51에 손실원장이 "장중 재기동 · 오늘 영구 소실"이라고 말한다 — 거절된 기동을 재기동으로 센다

- **증상**: 09:00 개장 전인데 `irrecoverable_loss.clean = false`이고 요약이 "장중 재기동"이다. 동시에 `start_lag_minutes`가 `null`로 지워졌다.

- **근거**:

  `logs/status_snapshot.json` (08:51:26):
  ```json
  "irrecoverable_loss": {
    "start_lag_minutes": null, "minutes_since_trigger": 0.3,
    "restarted_mid_day": true, "lost_items": 0, "clean": false,
    "summary": "오늘 영구 소실 — 장중 재기동 — 이전 세션 소실분은 장후 축이 판정(지금은 미상)"
  }
  ```
  그런데 오늘 `l1_daily`의 `SessionStart`는 둘뿐이고, 첫 번째는 **같은 초에 거절**됐다:
  ```
  06:42:31 [INFO] SessionStart          git_sha=50eff6c pid=13716
  06:42:31 [INFO] LaunchWindowRefused   기동 창(08:15~15:35) 이전 06:42:31 — 정시 트리거(08:20)에 맡기고 지금은 뜨지 않는다
  08:20:16 [INFO] SessionStart          git_sha=50eff6c pid=7768   ← 정시 트리거 정상 기동
  ```
  원인 지점 — `scripts/run_l1_daily.py:1109`
  ```python
  restarted = session_guard.prior_sessions_today(_LOG_DIR / f"l1_daily_{today:%Y%m%d}.log") > 0
  loss_ledger.record_start_lag(lag, restarted_mid_day=restarted)
  ```
  `ops/session_guard.py:282 prior_sessions_today()`는 `'"SessionStart"'` 문자열을 세고 **자기 자신(15초 이내)만** 제외한다. `LaunchWindowRefused`로 거절된 기동은 걸러내지 않는다.

- **기준 위반**:
  - `session_guard` 자신의 docstring이 세운 원칙 — *"판정 불가를 이유로 거짓 재기동을 만들지 않는다"* 를 스스로 어긴다.
  - dev_memory 등록부 항목 **`launch-window-refusal-not-counted`** 와 F-4가 신설한 지표 `refused_starts_counted_as_restart`가 겨냥한 것과 **정확히 같은 의미 오류**다. 어제 F-4는 이 현상을 **재는 계기**를 만들었을 뿐, 원인 경로(`prior_sessions_today`)는 고치지 않았다. 게다가 그 계기조차 지금 안 실려 있다(1-1).
  - SYSTEM.md **금지계명 12** — 실제로 없는 손실을 화면에 띄운다.

- **영향**:
  - **매일 아침 재현된다.** PC 부팅 트리거(06:42)가 매일 `SessionStart` 한 줄을 남기므로, 정시 기동한 프로세스는 **항상** 자기를 "장중 재기동"으로 판정한다. 2026-08-10에 38분 기동 지연을 놓쳐서 만든 `start_lag_minutes` 계기가, 그 결과 **매일 `null`로 침묵한다** — 진짜 기동 지연이 생긴 날 아무도 못 본다.
  - 오늘 장후 **J-12**(`SessionStart` 프로세스당 정확히 1회 → `abnormal_exits` 0)도 이 계수 방식이면 2회로 읽혀 오탐이 난다.

- **신규 여부**: **신규**. 등록부는 "거절 기동이 재기동으로 계수되는" 현상 일반을 알고 있으나, `loss_ledger` 기동지연 경로가 그 소비자라는 사실은 dev_memory에 없다.

---

### P2 — 운영 부담·기술부채

#### 1-4. `logs/ui_20260820.err.log` 부재 — `CrashForensicsArmed`가 무장했다고 말하는 대상이 없다

- **증상**: UI 프로세스의 stderr 파일이 오늘도 생성되지 않았다.
- **근거**:
  ```
  $ ls logs/ui_*.err.log        → logs/ui_20260730.err.log 가 마지막 (08-19, 08-20 모두 부재)
  $ ls -la logs/ui_20260820.log → 377B / 7행 (stdout만)
  ```
  그 7행 중 마지막이 `[crash_forensics] armed tag=ui target=stderr` — **stderr를 무장했다고 선언하는데 그 파일이 없다.**
- **기준 위반**: phases.md **A-4**(UI 기동 확인, `logs/ui_*.err.log`). dev_memory **D-3**(`scripts/install_scheduled_tasks.ps1` UI 액션 리다이렉션 의심). SYSTEM.md **금지계명 12**.
- **영향**: UI가 죽어도 스택트레이스가 남는 곳이 없다. `UISnapshotFreshness`가 오늘도 0건인 것(D-2)이 "UI를 안 열어서"인지 "예외로 죽어서"인지 구분할 수단이 없다.
- **신규 여부**: 기존 TODO(D-3), 3주째. 오늘은 07-30 이후 **21일 연속 부재**로 갱신.

#### 1-5. `git ls-files logs` = 0 — J-7 미충족(I-9에서 이월)

- **증상**: 로그 디렉터리에 추적 중인 파일이 하나도 없다.
- **근거**: `git ls-files logs | wc -l` → `0`.
- **기준 위반**: dev_memory **J-7** `git ls-files logs | wc -l > 0 (F-6/장전 F-1 적용 후) — I-9 미충족분 이월 (08-20 장전)`.
- **영향**: 장전 F-1 미적용 확인. 오늘 이 항목은 **판정 불가가 아니라 미충족**으로 확정.
- **신규 여부**: 기존 TODO(I-9 → J-7).

#### 1-6. 자가점검 `host` 라인에 활성시간이 여전히 없다 — J-8 미충족(I-10에서 이월)

- **근거**: 기동 2회 모두 동일 —
  `[OK ] host  disk=… · power=AC 절전 없음 · docker=v29.6.1 · cpu=… · 외부 파이썬 … · boot_recovery=… · schedule_drift=…`
  → **활성시간(08:00~16:00) 항목 없음.**
- **기준 위반**: dev_memory **J-8** (I-10 이월).
- **영향**: 08-19 09:50 사망의 유력 원인(Windows Update 재시작, `configs/incident_causes.yaml`에 사람 확정으로 기록)을 막는 설정이 실제로 걸려 있는지 아침마다 확인할 자리가 없다. **D-1**(활성시간 변경 후 값 미상)도 함께 미해결.
- **신규 여부**: 기존 TODO(I-10 → J-8).

#### 1-7. 완성봉 유예 500ms vs 전일 회선 p90 925ms — 4거래일 연속 경고, G-2 재착수 조건에 근접

- **근거**: 자가점검 `bar_close` (기동 2회 모두)
  ```
  [OK ] bar_close  1분봉 확정: timer (거래소 시각 경계 구동) · 경고: 유예 500ms vs 전일 회선 p90 925ms(2026-08-19)
                   — 완성봉이 늦은 틱을 놓칠 수 있다
  ```
- **기준 위반**: SYSTEM.md **불변원칙 3**(완성봉 규율 · 유예 500ms). dev_memory **J-10**.
- **영향**: 925 / 500 = **1.85배**. G-2 보류 시 명시한 재착수 조건은 ① `late_bar_drops` 1건 이상 관측, 또는 ② **p90이 유예의 2배 초과**. 오늘 ②에 미달하나 근접했다. `late_bar_drops`는 3거래일 연속 0(표본 20,000)이므로 보류 유지가 여전히 옳다 — **다만 오늘 장후 p90이 1,000ms를 넘으면 조건 충족이다.**
- **신규 여부**: 기존 보류 항목(G-2), 오늘은 **임계 접근 기록**이 새 정보.

---

### 확인 필요 (확정 아님)

- **[C-1] 1-1의 편집 시각.** 워킹트리 편집이 08:25:30 이전이었는지 이후였는지. Cowork 마운트를 통과한 파일 mtime은 동기화 시각으로 덮이므로(전부 08:52대로 관측됨) 판정에 쓸 수 없다. **로컬 PC에서 `Get-Item scripts\run_g2_paper_trading.py | select LastWriteTime` 을 직접 확인**하면 확정된다.
- **[C-2] `RegimeSeeded`가 실제로 동작하는가**는 아직 한 번도 라이브 검증된 적이 없다. 1-1로 인해 오늘도 미검증. 최초 검증은 **커밋 후 다음 정시 기동(08-21 08:25)** 이 된다.
- **[C-3] `_seed_regime`의 `await consumer.handle_regime(state)` 직접 호출.** docstring이 "버스 발행만으로는 안 닿으므로 직접도 건넨다"고 명시했는데, SYSTEM.md **불변원칙 2**(프로세스 간 통신은 Redis Bus로만 — 직접 함수 호출 금지)와 어떻게 양립하는지 판단이 필요하다. 같은 프로세스 내 두 객체이므로 "프로세스 간"이 아니라는 해석이 성립할 여지는 있으나, **불변원칙에 대한 예외는 SYSTEM.md에 명문화되어야 한다.** 지금은 함수 주석에만 있다.
- **[C-4] `UISnapshotFreshness` 0건(D-2).** 코드는 `ui/app.py:1264`에 결선돼 있다고 기록돼 있으나 오늘도 0건. UI를 브라우저로 1회 열어봐야 판정된다 — **장전에 사람이 `http://localhost:8511` 한 번 열면 즉시 결론**. 단, 1-4로 인해 예외가 나도 안 보인다.

---

### 통과 확인된 항목 (phases.md A절 전수)

| 항목 | 판정 | 근거 |
|---|---|---|
| A-1 스케줄 4종 무장 | **확인됨** | `boot_recovery=부팅 트리거 무장 2개(Messiah, Messiah-G2)` · `schedule_drift=정본 일치 Messiah=08:20, Messiah-G2=08:25` |
| A-1 기동 창 거부 후 실기동 완결 | **확인됨** | 06:42 `LaunchWindowRefused` → 08:20:16 / 08:25:30 정시 기동. 거부 자체는 정상 |
| A-1 `SessionStart` 프로세스당 1회 | **확인됨(실기동 기준)** | 각 2회이나 1회는 거절. **단 계기는 이를 구분 못 함 → 1-3** |
| A-1 `SessionStart.git_sha` = HEAD | **확인됨** | 4건 전부 `50eff6c` (워킹트리 오염은 별건 → 1-2) |
| A-2 자가점검 전 항목 `[OK ]` | **확인됨** | 기동 4회 · 총 60행 · 비-OK **0행** · `self-check: PASS — 기동 허용` |
| A-2 clock offset | **확인됨** | +0.298s / +0.287s · `w32time=Running` · 거래소 시각 대비 skew +0.156s(표본 30) |
| A-2 host | **확인됨** | disk 547.8GB · AC 절전 없음 · docker v29.6.1 · cpu 0~20% · 외부 파이썬 4~5개 |
| A-2 git dirty | **허용** | `dirty(dev 허용)` — dev 모드. live/paper였다면 금지계명 10 위반 |
| A-3 옵션체인 | **확인됨** | `OptionChainPolled` 12회 전부 **42/42 다리 발행** · 스킵 0 |
| A-3 브로커 재시도 | **확인됨** | `OptionChainPollRetried` 1건 (08:23:34, KIS 500, `C09FAWA03`) — **1회 재시도로 복구, attempts=2** |
| A-3 Redis / 스키마 | **확인됨** | `redis://localhost:6380/0` · `schema version=1 types=21` |
| A-3 번들·레지스트리 | **생략(dev)** | `bundle dev — 생략` · `registry dev — 생략` · live 번들 결선 `['30m']` (feature_set=v2026.08-ev) |
| A-3 전일 무결성 | **확인됨** | `postmarket 20260819 장후 배치 정상 종료 확인` |
| A-3 롤오버·달력 | **확인됨** | 비-롤일 · 근월물 `A05609` · 다음 롤 2026-09-11 / `covered_through=2026-12-31(D+133)` |
| A-4 08:15~09:00 로그 공백 | **확인됨** | 10분 이상 공백 **0건** (l1·g2 양쪽) |
| A-4 웜업 완료 | **확인됨** | `FeatureWarmStart` 6개 Horizon 전부 200/180봉 충족 · `CollectorFirstTick` 08:44:59 · `l1.feature_engine` NaN 임계 이하 4개 Horizon |
| A-4 컴포넌트 상태 | **확인됨** | `l1.collector`/`feature_engine`/`composer`/`g2.pipeline` 전부 **OK** · 합성봉 4개 거래량 항등식 일치(유실 0) · `circuit_breaker: normal`, `gateway_halted: false` |
| A-4 UI | **부분** | `command_center_ui: UP` (port 8511, pid 11580) — **단 err 로그 부재 → 1-4** |

---

## 2. Fix 작업 구현계획

> **전 항목 장후 적용.** 08:45 시점 점검이며 09:00 개장이 임박했다 — SYSTEM.md **R11**(장중 배포 금지) · **금지계명 3·4**. 오늘 코드는 한 줄도 건드리지 않았다.
> 적용 시점 권고: **15:40 셧다운 이후, 15:45 장후 배치 완주를 확인한 뒤.**

### F-1. 어제 구현분 커밋 — P1 · 대응 이상점 1-1

- **원인 가설**: 08-20 새벽~아침 구현 세션이 dev_memory 기록과 테스트까지 마쳤으나 **커밋 단계가 누락**됐다. `50eff6c`가 같은 유형을 하루 전에 겪었다는 점에서, 개인 규율이 아니라 **절차에 게이트가 없는 것**이 원인이다.
- **변경 파일**: 코드 변경 없음. 커밋 행위 자체.
  - 대상: `src/messiah/strategy/regime/runtime.py`(F-5 `seed()`), `scripts/run_g2_paper_trading.py`(`_seed_regime`), `src/messiah/core/logging.py`(`RegimeSeeded` 등록), `src/messiah/ops/loss_ledger.py`(F-2), `src/messiah/ops/integrity_report.py`(F-3/G-3/G-4), `src/messiah/ui/app.py`, `configs/incident_causes.yaml`(**untracked — `git add` 필수**), 그 외 `src/`·`scripts/` 실변경분.
  - **주의**: CRLF 잡음 76파일이 섞여 있다. `git add -p` 또는 `.gitattributes`로 개행을 먼저 정리하지 않으면 diff가 5,700줄로 부풀어 리뷰가 불가능하다.
- **회귀 위험**: 커밋 자체는 없음. 다만 **다음 기동에서 F-1~F-6·G-3/G-4가 한꺼번에 라이브 데뷔**한다 — 8개 변경이 동시에 첫 실전 노출된다.
- **검증 방법**: `pytest tests/` 전량 + `ruff check src/ scripts/ tests/`(DECISION_LOG 기록상 이미 통과했으나 커밋 직전 재실행). 08-21 08:25 기동 로그에 `RegimeSeeded` 1건 확인 → **J-5 재판정**.
- **적용 시점**: **장후 15:40 이후 최우선.**
- **결정 필요 사항**: 8개 변경을 한 커밋으로 묶을지 F 단위로 쪼갤지. **권고: F 단위 분할.** 한꺼번에 데뷔시키면 08-21에 무언가 틀어졌을 때 어느 F 탓인지 이분탐색이 안 된다.

### F-2. 커밋 없이 하루가 가는 것을 계기가 말하게 한다 — P1 · 대응 이상점 1-2 (= 이월 J-9 / 장전 F-2)

- **원인 가설**: `code_version.stale`이 `process_git_sha == head_git_sha`만 본다. 워킹트리는 애초에 관측 대상이 아니었다.
- **변경 파일**:
  - `src/messiah/ops/` 의 `status_snapshot` 생성부(`code_version` dict를 만드는 함수) — `worktree_dirty: bool`, `worktree_dirty_files: int`(`src/`+`scripts/` 실변경만, CRLF 잡음 제외) 두 키 추가. `summary`도 함께 고쳐 `"코드 50eff6c — 전 프로세스 동일 · 미커밋 14파일"` 형태로.
  - `scripts/self_check.py` — `git` 항목의 `dirty(dev 허용)`을 `dirty: src/scripts 14파일 (dev 허용)`로. **개수를 말하게 한다.** 0이 아니면 dev에서도 `[WARN]`.
  - `src/messiah/ui/app.py` — 헤더 배지에 미커밋 건수 노출(R10 배지 원칙).
- **회귀 위험**: `git status --porcelain` 호출이 기동 경로에 들어간다 — 대용량 `data/` 때문에 느릴 수 있다. **`git status --porcelain -- src scripts` 로 경로를 좁힌다.**
- **검증 방법**: `pytest tests/ops/` 신규 케이스 2건(clean/dirty). 08-21 아침 `status_snapshot.json`에 `worktree_dirty` 키 존재 확인 → **J-9 마감.**
- **적용 시점**: 장후 · F-1 커밋 **직후**(F-1이 먼저 들어가야 이 계기가 clean에서 출발한다).
- **결정 필요 사항**: 없음.

### F-3. 거절된 기동을 재기동으로 세지 않는다 — P1 · 대응 이상점 1-3

- **원인 가설**: `session_guard.prior_sessions_today()`가 `SessionStart` 문자열만 세고, 바로 뒤따르는 `LaunchWindowRefused`를 보지 않는다. `mlog.setup()`이 기동 창 검사보다 **먼저** `SessionStart`를 찍는 구조라 거절된 기동도 줄을 남긴다.
- **변경 파일**:
  - `src/messiah/ops/session_guard.py` — `prior_sessions_today()`: `SessionStart` 줄을 셀 때, **같은 로그에서 그 시각 ±N초(권고 5초) 안에 `LaunchWindowRefused`가 있으면 제외**한다. docstring에 "거절은 기동이 아니다"를 명문화.
  - 같은 파일 — 새 헬퍼 `refused_starts_today(process_log) -> int` 를 분리해 F-4 스코어보드 지표(`refused_starts_counted_as_restart`)가 같은 판정을 공유하게 한다. **두 곳이 따로 세면 지금과 같은 어긋남이 재발한다.**
  - `scripts/run_l1_daily.py:1109` 주변 — 변경 없음(헬퍼가 고쳐지면 자동 해소). 단 주석에 "거절 기동 제외"를 한 줄 추가.
- **회귀 위험**: 진짜 장중 재기동을 놓칠 수 있다. **거절 로그가 없는 `SessionStart`는 반드시 세야 한다** — 필터를 "거절이 있으면 제외"로 좁게 쓰고, "거절이 없으면 포함"을 기본으로 둔다.
- **검증 방법**:
  - `pytest tests/ops/test_session_guard.py` 신규 3건 — ① 거절 1 + 실기동 1 → `prior=0` ② 실기동 2 → `prior=1` ③ 거절 1 + 실기동 2 → `prior=1`.
  - **replay**: 오늘 `logs/l1_daily_20260820.log`를 그대로 먹여 `restarted_mid_day == false`가 나오는지. 08-19 로그(진짜 12:29 재기동)에는 `true`가 유지되는지 — **양방향 확인 필수.**
  - 08-21 08:51 `status_snapshot.json`에 `restarted_mid_day: false` · `clean: true` · `start_lag_minutes`가 **숫자**로 → 신규 관측 항목 **K-1**.
- **적용 시점**: 장후.
- **결정 필요 사항**: `SessionStart`를 기동 창 검사 **뒤로** 옮기는 근본 수정도 가능하다. **권고: 옮기지 않는다.** 거절된 기동도 "떴다가 물러났다"는 사실이 로그에 남는 편이 낫고(그 자체가 스케줄 드리프트의 증거다), 그 값을 어떻게 세느냐가 소비자 책임이다.

### F-4. UI stderr 리다이렉션 복구 — P2 · 대응 이상점 1-4

- **원인 가설**: `scripts/install_scheduled_tasks.ps1`의 UI 액션이 stdout만 `Out-File`로 받고 `2>&1`이 빠졌거나, `.err.log` 경로가 다른 날짜 포맷을 쓴다. 07-30까지는 생성됐으므로 그 이후 커밋에서 회귀했을 가능성이 크다.
- **변경 파일**:
  - `scripts/install_scheduled_tasks.ps1` — UI 액션의 인자 문자열. l1/g2 액션과 **같은 형태**로 통일(`2>&1 | ForEach-Object { $_ | Out-File -FilePath 'logs\ui_YYYYMMDD.err.log' -Append -Encoding utf8; $_ }`).
  - 검증 보조: `scripts/self_check.py` — `host` 항목 또는 신규 `ui` 항목에서 **전일 `ui_*.err.log` 존재 여부**를 한 줄로 보고.
- **회귀 위험**: 낮음. 다만 재설치(`install_scheduled_tasks.ps1` 재실행)가 필요하므로 `schedule_drift`가 일시적으로 어긋날 수 있다 — 재설치 후 자가점검으로 즉시 확인.
- **검증 방법**: `git log -S "err.log" -- scripts/install_scheduled_tasks.ps1` 로 07-30 이후 회귀 커밋 특정. 재설치 후 08-21 기동에 `logs/ui_20260821.err.log` 생성 → **J-11 / D-3 마감.**
- **적용 시점**: 장후.
- **결정 필요 사항**: 없음.

### F-5. `git ls-files logs` — J-7 원래 의도 확인 후 결선 — P2 · 대응 이상점 1-5

- **원인 가설**: 장전 F-1(로그 산출물 일부 추적)이 계획만 되고 미착수. `.gitignore`가 `logs/` 전체를 막고 있을 가능성.
- **변경 파일**: `.gitignore` — `logs/` 제외 규칙에 `!logs/dailycheck/` `!configs/incident_causes.yaml` 등 **의도된 예외**를 명시.
- **회귀 위험**: 로그 원본(일 130~240KB)을 통째로 추적하면 리포가 폭증한다. **점검 보고서와 사람이 확정한 사고 원인 파일만** 추적한다.
- **검증 방법**: `git ls-files logs | wc -l > 0` → **J-7 마감**.
- **적용 시점**: 장후. F-1 커밋에 포함 가능.
- **결정 필요 사항**: **무엇을 추적할 것인가.** 원안(I-9)의 의도가 dev_memory에서 명확하지 않다. **권고: `logs/dailycheck/*.md`(점검 보고서·증거 다이제스트)만.** 이것들은 "그날의 판정 기록"이라 08-14 F-12의 "나중 보고서가 앞의 것을 채점한다" 원칙상 소실되면 안 된다.

### F-6. 자가점검 `host`에 활성시간 표기 — P2 · 대응 이상점 1-6

- **변경 파일**: `scripts/self_check.py` — `host` 항목 문자열에 `active_hours=08:00~16:00` 추가. `powercfg` 또는 Windows Update 활성시간 레지스트리(`HKLM\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings\ActiveHoursStart/End`) 조회.
- **회귀 위험**: 레지스트리 접근 실패 시 기동을 막으면 안 된다 — 실패는 `active_hours=조회 실패`로 표기하고 `[OK ]` 유지(웜스타트와 같은 원칙).
- **검증 방법**: 08-21 기동 `host` 라인 육안 확인 → **J-8 마감 · D-1 동시 해소.**
- **적용 시점**: 장후.

### 적용 순서와 커밋 계획

| # | 커밋 | 포함 | 메시지 초안 |
|---|---|---|---|
| ① | 개행 정리 선행 | `.gitattributes` (CRLF 76파일 잡음 제거) | `[MW0601] 개행 잡음이 리뷰를 76파일만큼 가렸다 — .gitattributes 정본화` |
| ② | **F-1 (F 단위 분할)** | 어제 구현분 F-1~F-6·G-3/G-4 + `configs/incident_causes.yaml` | `[MW0601] 완료라 적고 커밋하지 않았다 — 08-19 장후 구현분 반입` |
| ③ | F-2 | `worktree_dirty` 계기 | `[MW0601] 커밋과 실린 코드가 같다고 말하는 계기가 커밋을 안 봤다 — worktree_dirty (J-9)` |
| ④ | F-3 | 거절 기동 계수 수정 | `[MW0601] 매일 아침 뜨지도 않은 기동을 재기동으로 셌다 — 거절 기동 제외 (1-3)` |
| ⑤ | F-4 | UI stderr | `[MW0601] 무장했다고 선언한 stderr에 파일이 없었다 — UI 리다이렉션 복구 (D-3)` |
| ⑥ | F-5 + F-6 | `.gitignore` · 활성시간 | `[MW0601] 이월 두 건 마감 — 점검 보고서 추적 + 활성시간 표기 (J-7·J-8)` |

> ②를 ③보다 먼저 넣어야 한다. 순서가 바뀌면 새 계기가 첫날부터 `worktree_dirty: true`로 울어 "정상 상태의 기준선"을 못 잡는다.

---

## 3. 고도화 방안

### G-1. 커밋되지 않은 하루를 **기동이** 막는다 — 관측이 아니라 게이트로

- **관측 근거**: 오늘 1-1. 그리고 직전 커밋 `50eff6c`의 제목이 **같은 사고**다("장중에 재기동해야 하는데 실릴 코드가 커밋에 없었다"). 즉 이 유형은 **이틀 연속**이며, F-2(계기 추가)는 사후 관측일 뿐 재발을 못 막는다.
- **제안 내용**: `scripts/self_check.py`의 `git` 항목을 **판정 항목으로 승격**한다.
  - dev 모드: `src/`+`scripts/` 미커밋 실변경이 있고 **직전 거래일에도 있었다면** `[WARN]` — "이틀 연속 미커밋 N파일. 어제 dev_memory가 완료로 적은 항목이 실려 있지 않을 수 있다."
  - live/paper 모드: 기존대로 **FAIL·기동 거부**(금지계명 10).
  - 판정 재료는 `logs/dailycheck/`에 남는 전일 스냅샷 — F-5로 추적되면 바로 쓸 수 있다.
- **기대 효과**: 1-1이 09:00 개장 전 자가점검 화면에서 **자동으로 눈에 든다.** 오늘은 사람이 `git diff`를 쳐서야 알았다.
- **비용·위험**: 소(小). `git status --porcelain -- src scripts` 1회. 위험은 오탐 — 정상적인 dev 작업 중에도 뜬다. 그래서 **"이틀 연속"** 조건을 둔다(하루짜리 작업 중 상태는 조용히 지나간다).
- **선행 조건**: F-2(계기) · F-5(전일 스냅샷 추적).
- **우선순위 제안**: **이번 주.** 오늘 이틀 연속이 확인됐으므로 착수 조건은 이미 충족.

### G-2. dev_memory의 `[x]`와 git 이력을 **대조하는 축**을 만든다

- **관측 근거**: 오늘 1-1의 본질은 코드가 아니라 **기록과 실체의 어긋남**이다. `NEXT_TODO.md`는 F-1~F-6을 `[x]`로 닫았고 DECISION_LOG는 "구현 (2026-08-20)"이라 적었는데, 그 어느 것도 커밋을 확인하지 않았다. 이 어긋남을 **아무 계기도 재고 있지 않다.**
- **제안 내용**: 장후 배치(`daily_integrity_report.py` 또는 신규 `scripts/check_dev_memory_updated.py` 확장)에 축 하나 추가 —
  - 당일 `NEXT_TODO.md`에서 새로 `[x]`가 된 항목 수 `n_closed`
  - 당일 `[MW0601]` 커밋 수 `n_commits`
  - `n_closed > 0 and n_commits == 0` → **`ClosedWithoutCommit`(WARNING)** 태그 + 장후 리포트 필드.
- **기대 효과**: "완료라 적었는데 반입되지 않은 날"이 그날 저녁에 이름을 얻는다. 오늘은 **다음 날 아침 점검에서** 잡혔다 — 그 사이 개장 한 번이 통째로 옛 코드로 돌았다.
- **비용·위험**: 중(中). `NEXT_TODO.md`가 480KB라 diff 파싱이 필요하다 — `git diff HEAD~1 -- dev_memory/NEXT_TODO.md | grep '^+.*\[x\]'` 로 충분하다. 위험: dev_memory가 커밋 안 된 날엔 diff가 안 나온다 → **그 경우 자체가 `unresolved`로 보고**되게 한다(G-4 negative_control과 같은 원칙 — 못 재는 날은 판정하지 않고 못 쟀다고 말한다).
- **선행 조건**: 없음. G-4가 어제 세운 "계기가 자기를 채점하지 못하게 한다" 사다리에 그대로 얹힌다.
- **우선순위 제안**: **다음 단계.** G-1이 개장 전 게이트라면 이것은 장후 채점이다 — 둘 다 있어야 하루가 닫힌다.

### G-3. 「거절된 기동」을 계수 대상에서 빼는 판정을 **한 곳으로 모은다**

- **관측 근거**: 오늘 1-3. F-4가 어제 `refused_starts_counted_as_restart`라는 **계기**를 새로 팠는데, 정작 같은 의미 오류를 저지르는 `loss_ledger` 기동지연 경로는 손대지 않았다. 하나의 의미(「거절은 기동이 아니다」)를 **두 곳이 따로 구현**하고 있는 것이 원인이다.
- **제안 내용**: `ops/session_guard.py`에 판정 단일 진입점을 둔다 — `effective_sessions_today(log) -> tuple[int, int]`(실기동 수, 거절 수). `prior_sessions_today` · F-4 스코어보드 · `abnormal_exits` · 장후 `daily_integrity_report`가 전부 이 하나를 부른다. **같은 질문에 두 답이 나오지 않게 한다.**
- **기대 효과**: 오늘 확인된 오표기가 사라지고, 장후 J-12 판정(`SessionStart` 프로세스당 1회)이 처음으로 신뢰 가능한 값이 된다.
- **비용·위험**: 소~중. 위험은 **의도된 이중 계측을 없애는 것** — 08-19 F-2b 기각의 교훈(`loss_ledger` docstring: *"둘이 어긋나면 그 자체가 볼 것이다"*)이 여기에도 걸리는지 먼저 확인해야 한다. **판단: 여기는 다르다.** 저기서 이중 계측된 것은 「무엇을 잃었나」라는 **값**이고, 여기서 어긋난 것은 「기동이 몇 번이었나」라는 **사실**이다. 사실은 하나여야 한다.
- **선행 조건**: F-3.
- **우선순위 제안**: 이번 주 (F-3과 같은 커밋에 넣어도 좋다).

### G-4. `_seed_regime`의 직접 호출을 불변원칙 2의 **명문화된 예외**로 올린다

- **관측 근거**: 오늘 [C-3]. `scripts/run_g2_paper_trading.py:351` docstring이 *"버스 발행만으로는 안 닿는다 — 구독 전 발행은 사라진다"* 라며 `consumer.handle_regime(state)` 직접 호출을 정당화하는데, 그 정당화가 **함수 주석에만** 있다. SYSTEM.md 불변원칙 2는 "직접 함수 호출 금지"라고만 적혀 있다.
- **제안 내용**: SYSTEM.md 불변원칙 2에 예외를 한 줄 명문화 — *"단, 동일 프로세스 내 기동 시드처럼 구독 성립 이전의 1회성 전달은 예외로 하되, 버스 발행을 **병행**하고 그 사실을 태그로 남긴다."* 그리고 `RegimeSeeded` 페이로드에 `delivery: "bus+direct"` 필드를 넣어 **어느 경로로 닿았는지 로그가 말하게** 한다.
- **기대 효과**: 다음 사람이 이 코드를 보고 "불변원칙 위반"으로 되돌리거나, 반대로 이것을 근거 삼아 다른 직접 호출을 늘리는 것을 둘 다 막는다. 불변원칙은 예외가 있어도 되지만 **예외가 주석에만 있으면 안 된다.**
- **비용·위험**: 소(문서 + 필드 1개). 위험: 예외를 열어 주면 남용된다 — 그래서 **"구독 성립 이전 · 1회성 · 버스 병행"** 세 조건을 모두 건다.
- **선행 조건**: F-1(코드가 먼저 커밋돼야 문서가 실체를 가리킨다).
- **우선순위 제안**: **이번 주** — F-1 커밋과 같은 날.

### 로드맵 반영 제안

| 항목 | 현 로드맵 위치 | 제안 위치 | 사유 |
|---|---|---|---|
| G-1 · G-2 (반입 규율의 계기화) | 없음 | 마스터플랜 **「관측 신뢰성」 절**(어제 G-4에서 신설 제안됨) | 어제 G-4가 "계기가 자기를 채점하는 것을 금지"를 세웠다. 그 절의 자연스러운 다음 항목이 **"기록이 자기를 채점하는 것을 금지"** 다 — 오늘 1-1이 정확히 그 사례다 |
| G-3 (세션 계수 단일화) | 없음 | 동 절 | 같은 사실에 두 답이 있는 것은 관측 신뢰성 문제다 |
| G-4 (불변원칙 2 예외 명문화) | SYSTEM.md §4 불변원칙 | SYSTEM.md 개정 | 예외가 코드 주석에만 있는 상태를 해소 |

---

## 4. 다음 거래일 관측 예정

| ID | 관측 대상(태그/지표) | 판정 기준 | 판정 예정일 |
|---|---|---|---|
| **K-1** | `status_snapshot.json` · `irrecoverable_loss` | `restarted_mid_day: false` · `clean: true` · `start_lag_minutes`가 **숫자**(null 아님). F-3 적용 후 | 08-21 장전 |
| **K-2** | `status_snapshot.json` · `code_version.worktree_dirty` | 키가 **존재**하고, F-1 커밋 후이므로 `false`. F-2 적용 후 → **J-9 마감** | 08-21 장전 |
| **K-3** | `logs/g2_daily_20260821.log` · `RegimeSeeded` | 08:25대 **1건**. 없으면 「국면 시드 없음」 print라도 있어야 한다 — **둘 다 없으면 F-5가 또 안 실린 것** → **J-5 재판정** | 08-21 장전 |
| **K-4** | 09:00 첫 사이클 `AggregatorNoContribution.regime` | K-3이 성립한 경우에 한해 `!= UNKNOWN`. **K-3 실패 시 이 항목은 판정하지 않는다**(못 재는 것을 근거로 선고하지 않는다 — G-4 원칙) | 08-21 장중 |
| **K-5** | `logs/ui_20260821.err.log` | 파일 **존재**(내용은 비어도 됨). F-4 적용 후 → **D-3 마감** | 08-21 장전 |
| **K-6** | 자가점검 `host` 라인 | `active_hours=` 문자열 포함. F-6 적용 후 → **J-8 · D-1 마감** | 08-21 장전 |
| **K-7** | `git ls-files logs \| wc -l` | `> 0`. F-5 적용 후 → **J-7 마감** | 08-21 장전 |
| **J-10** | `daily_integrity_20260820.json` · `delivery_latency.p90` | 4거래일째. **1,000ms 초과 시 G-2(완성봉 유예 연동) 재착수 조건 충족** — 오늘 925ms(1.85배) | **오늘 장후** |
| **J-5b** | `daily_integrity_20260820.json` · `regime_unseeded_cycles` | **판정 보류.** F-5 미반영이므로 오늘 값은 설계 채점이 아니다. 세션 수만큼 나오는 것이 **예상된 결과** | 오늘 장후(참고만) |
| **J-12** | 오늘 `SessionStart` 계수 → `abnormal_exits` | **판정 보류.** 1-3의 계수 오류가 F-3 전이라 오탐이 예상된다. 거절 1 + 실기동 1을 사람이 직접 확인할 것 | 오늘 장후(참고만) |
| **C-1** | 로컬 PC `Get-Item scripts\run_g2_paper_trading.py \| select LastWriteTime` | 08:25:30 이전/이후. 1-1의 인과 확정 | **오늘 장후, 즉시 가능** |
| **C-4** | `UISnapshotFreshness` | 브라우저로 `http://localhost:8511` 1회 개방 후 출현 여부 → **D-2 판정** | **즉시 가능** |

---

*작성: messiah-daily-check 스킬 (예약 실행 08:45 KST) · 코드 변경 없음 · dev_memory 갱신 완료*
