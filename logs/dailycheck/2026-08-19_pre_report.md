# MESSIAH 일일 점검 — 2026-08-19 / 장전 (pre)

- 점검 시각: **08:50 KST** (예약 설계시각 08:45 → 실행 08:50 · 지연 5분 · **개장 10분 전**)
- 대상 국면: `pre` — 관측창 05:55~08:51
- HEAD `40e9968` · 실행 중 `40e9968` (`code_version.stale = false`) · 미커밋 256건 (개행 잡음 85 포함, **`src/` 실변경 2파일 + `.gitignore`**)
- 증거: `logs/dailycheck/evidence_20260819_pre.md`
- 직전 보고서: `logs/dailycheck/2026-08-18_pre_report.md` (실행 13:29 · 지연 284분)

> **⚠ 이 보고서는 코드를 변경하지 않았다.** 예약 지시(장전 금지)와 SYSTEM.md **R11 · 금지계명 3·4**가 이중으로 걸린다. 아래 Fix는 **전부 계획이며 적용 시점은 15:35 이후**다.

## 0. 한 줄 결론

**파이프라인은 설계대로 깨어났다. 오늘의 결함은 파이프라인이 아니라 「어제 저녁에 쓴 코드가 아직 커밋되지 않았다」는 한 지점에 모여 있다** — 자가점검 30행 비-OK 0, `verdict.ok=true`, 전 컴포넌트 OK, 소급불가 손실 0. 그런데 작업트리에는 **오늘 날짜로 작성된 미커밋 변경 3파일**이 있고, 그중 하나(`.gitignore`)는 「산출물을 git 추적으로 되돌렸다」고 선언하지만 **인덱스에 추가된 파일은 0건**이다.

**P0 없음.** 근거를 명시한다(P0 없음을 근거 없이 쓰지 않는다):

- `mode=dev` · G2는 페이퍼(`run_g2_paper_trading.py`) — **실주문 경로가 오늘 열려 있지 않다.**
- `self-check: PASS — 기동 허용` 2회(05:55·08:20/08:25), 비-OK 0행.
- `logs/status_snapshot.json` 08:50:53 — `circuit_breaker.phase: normal` · `gateway_halted: false` · `irrecoverable_loss.clean: true` · `lost_items: 0` · `verdict.ok: true` · `observation_gap_count: 0`.
- `code_version.stale: false` — 두 프로세스 모두 `git_sha=40e9968` = HEAD.

| 국면 | 판정 | 근거 요약 |
|---|---|---|
| 장전 | **조건부** | 기동·자가점검·웜스타트·수집 전부 정상. 조건은 **미커밋 3파일**이 장후 배치에 섞여 들어가는 것(P1-1)과 `.gitignore` 철회의 실효 부재(P1-2) |
| 장중 | 판정 불가 | 09:00 이전. 08:45:00 `CollectorFirstTick` · 08:46부터 `FeaturePublish` 정상 — 개장 준비는 어제와 동일 카덴스 |
| 장후 | 판정 불가 | 15:35 이전. W-1·W-3·Z-1~Z-4는 장후 점검으로 이월 |

### 전일 대비 델타 (2026-08-18 pre → 오늘)

| 축 | 08-18 | 08-19 | 판정 |
|---|---|---|---|
| 장전 점검 산출 시각 | 13:29 (지연 284분) | **08:50 (개장 10분 전)** | ✅ **Y-7 성립** |
| `src/`+`scripts/` 실변경 | 0파일 | **2파일 + `.gitignore`** | ⚠ 신규 |
| 자가점검 비-OK | 0행 | 0행 | 유지 |
| `bar_close` 전일 p90 경고 | 924ms(08-14) | **927ms(08-18)** | ✅ **W-2 성립** |
| 부팅 회차 `LaunchWindowRefused` | 07:23:19 | 05:55:35 | 유지(기존 TODO) |
| `InvestorFlowPollRetried` | 1건 08:21 | 1건 08:31 | 유지 |

---

## 1. 이상점 정리보고

### P0 — 오늘 거래에 직접 위험

**해당 없음.** (근거는 §0에 명시)

---

### P1 — 정확성·관측 훼손

#### 1-1. 오늘 날짜로 쓰인 코드 3파일이 미커밋인 채 장후 배치를 기다리고 있다 — `code_version.stale`이 못 보는 사각

- **증상**: 작업트리에 **오늘(2026-08-19) 날짜로 작성된** 변경이 세 파일에 있고 커밋되지 않았다. 오늘 08:20/08:25 기동 프로세스는 HEAD `40e9968`을 로드했으므로 장중에는 이 코드가 **없다**. 그러나 15:45 `run_postmarket`은 **새 프로세스로 뜨며 작업트리를 로드**한다 — 같은 날 장중과 장후가 서로 다른 코드로 돈다.

- **근거**:
  ```
  $ git diff --stat --ignore-all-space --ignore-blank-lines -- src scripts
   src/messiah/ops/feature_health_rolling.py | 39 ++++++++++++++++++++--
   src/messiah/ops/fix_verification.py       | 17 ++++++++++
   2 files changed, 54 insertions(+), 2 deletions(-)
  ```
  변경 본문이 스스로 날짜를 밝힌다 — `feature_health_rolling.py`: *"## `keep_days` 는 이 저장소의 유일한 자동 삭제다 (2026-08-19 안전장치)"*, `fix_verification.py`: *"## 이 함수는 백업된 파일을 읽는다고 전제한다 (2026-08-19 정정)"*. `.gitignore`는 68줄 추가(`M .gitignore`).

  같은 시각 계기는 아무 말도 하지 않는다:
  ```json
  // logs/status_snapshot.json · 08:50:53
  "code_version": { "process_git_sha": "40e9968", "head_git_sha": "40e9968",
                    "stale": false, "summary": "코드 40e9968 — 전 프로세스 동일" }
  ```
  ```
  logs/l1_daily_20260819.log · 08:20:29 기동 자가점검
  [OK ] git        dirty(dev 허용)
  ```

- **기준 위반**:
  - SYSTEM.md **금지계명 10**(미커밋 수정 실전 반입 금지) · §7 *"커밋 안 된 수정을 실전 PC에 남기지 않는다"*.
  - `assess_version_drift`(`src/messiah/core/version.py:123`)는 **`process_git_sha` vs `head_git_sha`만** 비교한다. 작업트리 dirty는 판정에 들어가지 않는다. 한편 `check_git_state`(`scripts/self_check.py:480`)는 dirty를 보지만 **dev면 `[OK ]`로 흘려보내고 건수·파일명을 남기지 않는다**. **두 계기가 같은 사실의 절반씩만 보고 서로를 모른다.**

- **영향**: 오늘 장후 `daily_integrity_20260819.json`이 어느 코드로 산출됐는지 리포트만으로 확정할 수 없다. 특히 `feature_health_rolling.record_day`는 **장후에 호출**되므로 새 `keep_days` 가드가 오늘부터 실동작하는데 커밋 이력에 그 사실이 없다. 사후에 "언제부터 바뀌었나"를 물으면 답할 근거가 없다.

- **신규 여부**: **신규**. 어제 pre 보고서는 *"`src/`+`scripts/` 실변경 **0파일**"* 이었다 — 0 → 2로 바뀐 것이 오늘의 델타다.

#### 1-2. `.gitignore` 철회가 실효 없다 — 「추적으로 되돌렸다」고 선언했지만 인덱스에 추가된 파일 0건

- **증상**: `.gitignore`에 2026-08-19자 주석 68줄이 붙어 `logs/dailycheck/*_report.md`·`daily_integrity_*.json`·`verification_scoreboard_*.json`·`feature_health_rolling.json`·`pass_cycles/*.json` 등을 무시 대상에서 제외했다. `git check-ignore`는 이제 통과한다. **그런데 `git ls-files logs` 가 0건이다.** `git add`가 없었고 `.gitignore` 자체도 미커밋이다.

- **근거**:
  ```
  $ git check-ignore -q logs/daily_integrity_20260818.json   → 무시 아님
  $ git check-ignore -q logs/verification_scoreboard_20260818.json → 무시 아님
  $ git ls-files logs | wc -l
  0
  $ git status --porcelain -- .gitignore
   M .gitignore
  ```
  `.gitignore` 주석 원문(작업트리): *"`git clean -xdf` 한 번이면 등록부 채점 이력이 통째로 사라지고…"*, *"실측 근거(2026-08-19): `git check-ignore -q` 로 12종을 판정한 결과…"*.
  `fix_verification.py` 새 주석은 한 발 더 나가 **완료형으로 단언**한다: *"이 파일은 2026-08-19부터 git 추적이라 흔적이 커밋 이력에 남는다 — 더 나은 자리다."*

- **기준 위반**: SYSTEM.md **R10 / 금지계명 12**(조용한 폴백 금지)의 문서판 — **상태와 서술이 어긋났는데 아무 경보도 없다.** `.gitignore`의 negation은 무효여도 오류를 내지 않고 `git add` 누락도 오류를 내지 않는다. 그 함정을 주석이 두 번이나 경고해 놓고 **정작 마지막 한 걸음에서 같은 함정에 빠졌다.**

- **영향**: 보호하려던 위험이 **전혀 줄지 않았다.** `git clean -xdf`는 untracked를 지운다 — 점검 보고서 15편과 지표 JSON 52파일(248KB)이 여전히 백업 없음. 더 나쁜 것은 코드 주석이 "이제 안전하다"고 적혀 있어 **다음 사람이 확인하지 않을 것**이라는 점이다. 위험 자체보다 위험에 대한 잘못된 믿음이 오래 간다.

- **신규 여부**: **신규**.

---

### P2 — 운영 부담·기술부채

#### 2-1. 관측 기준이 존재하지 않는 태그를 가리킨다 — Y-5의 `RegimeSeeded`

- **증상**: `dev_memory/NEXT_TODO.md:5494` — `- [ ] **Y-5** (F-0818I-1/2 후) \`gate\` 분포에 \`no_expert\` 등장 · \`RegimeSeeded\` 1건 08:25대 · …`. 그런데 `RegimeSeeded`라는 태그는 **소스 어디에도 없다**.
- **근거**:
  ```
  $ grep -rn "RegimeSeeded" src/ --include=*.py   → 0건
  $ grep -rn "RegimeWarmStart" src/messiah/core/logging.py
  242:    "RegimeWarmStart": logging.INFO,
  ```
  실제 코드가 내는 태그로 오늘 관측된 것:
  ```
  logs/g2_daily_20260819.log · 08:25:35 [INFO] RegimeWarmStart
  ```
  즉 **의도한 사건은 실제로 일어났다.** 이름만 어긋났다.
- **기준 위반**: SYSTEM.md **R6**(태그 1개 = 심각도 1개)의 전제 — 태그명이 관측 계약이다. 계약서에 없는 이름을 기준에 적으면 그 항목은 영원히 미충족으로 남는다.
- **영향**: Y-5가 오늘 장후 채점에서 "미달"로 기록될 수 있다. 실제로는 충족. 등록부 채점의 **위음성**을 만든다 — `daily-axes-measured` 계열이 근거 없이 벌점을 받는 형태다.
- **신규 여부**: **신규 발견**(기존 TODO 항목의 서술 오류이지, 이미 보고된 항목이 아니다).

#### 2-2. `schedule_drift`는 수집 계열 2종만 대조한다 — Postmarket·Shutdown 트리거는 아무도 안 본다

- **증상**: 오늘 자가점검 `schedule_drift=정본 일치 Messiah=08:20, Messiah-G2=08:25`. 정본(`scripts/install_scheduled_tasks.ps1`)에는 `Messiah-Shutdown`·`Messiah-Postmarket`도 등록돼 있는데 **시각 대조 대상이 아니다**.
- **근거**:
  ```python
  # src/messiah/ops/host_health.py:414 check_schedule_drift
  expected = {task.name: task.weekly for task in task_schedule.collection_tasks(schedule_path)}
  ...
  return HostCheck("schedule_drift", available=False, ok=True, detail="정본에 수집 계열 작업이 없다")
  ```
  `boot_recovery` 축도 마찬가지다: `boot_recovery=부팅 트리거 무장 **2개**(Messiah, Messiah-G2)`.
- **기준 위반**: 스킬 체크리스트 A-1이 **4종** 등록·무장 확인을 요구한다. 그리고 이 항목이 생긴 계기 — `check_schedule_drift` 독스트링의 *"사람이 스케줄러 GUI를 열어 시각을 바꿨고, 그 사실은 어느 파일에도 안 남았다"* — 는 Postmarket/Shutdown에 **똑같이 적용된다**.
- **영향**: 누군가 GUI로 15:40/15:45를 옮겨도 아침 자가점검은 침묵한다. 사후에는 `task_exit_codes`가 잡지만(08-18 `Messiah-Postmarket 15:48:35 code=0`) 그건 **다음 날 아침**에야 보인다 — 장후 배치가 통째로 안 돈 하루를 잃은 뒤다.
- **신규 여부**: **신규**.

---

### 확인 필요 (확정 아님 — 확정된 결함과 섞지 않는다)

| # | 항목 | 지금 아는 것 | 무엇을 보면 판정되는가 |
|---|---|---|---|
| C-1 | `logs/pass_cycles/` 디렉터리 부재 (W-3) | `ls` → No such directory. 장전엔 pass 사이클이 발생하지 않으므로 부재가 정상일 수 있다 | 장후 점검에서 `PassCycleSnapshot` 로그 유무. **0건이면 그 자체가 정보**(Y-6: 08-18의 1/14가 예외적 사건) |
| C-2 | `UISnapshotFreshness` 0건 (Z-3) | `ui_20260819.log` 377B — 08-18과 **바이트 단위로 동일**. 서버는 떴고(`Uvicorn :::8511`, `command_center_ui.json` pid=26064) 렌더 로그는 없다 | 조건이 *"화면을 연 날이면"* 이다. 오늘 사람이 UI를 열었는지 확인 후 장중/장후 재판정 |
| C-3 | `ui_20260819.err.log` 파일 자체가 없음 | 에러가 없어서 안 만들어진 것인지, 리다이렉트가 안 걸린 것인지 구분 불가 | `.bat`/스케줄 액션의 stderr 리다이렉트 확인. 빈 파일이 생기는 설계라면 부재는 결함 |
| C-4 | 05:55:35 회차 기동 사유 미기록 | `LaunchWindowRefused` 후 즉시 종료 — 설계대로. 어제는 07:23, 오늘은 05:55(부팅 시각으로 추정) | **기존 TODO 재현**(「07:23 회차 기동 사유 미기록」). 신규로 세지 않는다. `boot_recovery` 트리거가 원인인지 이벤트로그 대조 필요 |
| C-5 | `l1_daily`/`g2_daily` `SessionEnd` 부재 | 증거 다이제스트 ⚠ 표시. **장전이라 아직 안 끝났다** — 정상 | 장후 점검에서 R13·금지계명 14로 판정 |

---

## 2. Fix 작업 구현계획

> **본 계획은 수립만 하고 적용하지 않는다** (SYSTEM.md **R11** · 금지계명 3·4, 예약 지시 장전 금지).
> 적용 시점: **15:35 정규장 마감 이후**. F-1은 15:45 장후 배치보다 **먼저** 끝내야 한다(§적용 순서 참조).

### F-1. 미커밋 3파일 커밋 + `logs` 산출물 실제 인덱스 등록 — P1 · 대응 이상점 1-1, 1-2

- **원인 가설**: 어제 밤 세션이 `.gitignore` 철회 로직을 작성하고 `git check-ignore`로 **검증까지** 했으나, `git add` → `git commit` 이 남았다. `check-ignore`가 통과하면 끝났다고 느끼기 쉬운 자리다 — negation이 무효여도, add를 빼먹어도, 둘 다 **오류를 내지 않는다**.
- **변경 파일**: 코드 변경 없음. git 조작만.
  1. `git add .gitignore src/messiah/ops/feature_health_rolling.py src/messiah/ops/fix_verification.py`
  2. `git add logs/dailycheck/*_report.md logs/daily_integrity_*.json logs/vol_scorecard_*.json logs/volume_check_*.json logs/self_eval_*.json logs/verification_scoreboard_*.json logs/feature_health_rolling.json logs/g2_daily_returns.jsonl`
  3. **검증**: `git ls-files logs | wc -l` 이 **0이 아닌지** 확인. 이것이 이 fix의 유일한 합격 조건이다.
  4. `logs/pass_cycles/*.json` 은 아직 파일이 없다 — 오늘 장후 생성되면 그때 add(`.gitignore` 규칙은 이미 준비됨).
- **회귀 위험**: 낮음. 다만 `logs/dailycheck/evidence_*.md`가 **일부러 제외**돼 있으므로 `git add logs/` 같은 광역 add를 쓰지 않는다(`.gitignore` 주석이 명시한 방침). CRLF 개행 잡음 85파일이 같은 커밋에 딸려오지 않도록 **경로를 하나씩 지정**한다.
- **검증 방법**: `git ls-files logs | wc -l > 0` · `git status --porcelain -- src` 가 비는지 · 커밋 후 `git stash list` 비어 있는지.
- **적용 시점**: **15:35 직후, 15:45 장후 배치보다 먼저.** 배치가 미커밋 코드로 도는 상태를 하루라도 더 두지 않는다.
- **결정 필요 사항**: `fix_verification.py` 주석의 *"2026-08-19부터 git 추적"* 이 커밋 시점에 비로소 참이 된다. **주석을 고칠 필요는 없다** — 같은 커밋에 들어가므로 커밋 시점 기준으로 사실이 된다. 다만 커밋 메시지에 "선언과 인덱스가 오늘 처음 일치했다"를 남길 것을 권고.

### F-2. `code_version`에 작업트리 dirty를 실어 두 계기를 한 문장으로 합친다 — P1 · 대응 이상점 1-1

- **원인 가설**: `assess_version_drift`는 "실행 중 코드 == 커밋된 코드"만 묻는다. "커밋된 코드 == 디스크의 코드"는 아무도 묻지 않는다. 사슬이 한 칸 짧다.
- **변경 파일**:
  - `src/messiah/core/version.py` — `assess_version_drift()`: 반환 dataclass에 `worktree_dirty: bool` · `worktree_changed_paths: list[str]` 추가. 판정은 `git diff --name-only --ignore-all-space --ignore-blank-lines -- src scripts` (**CRLF 잡음을 세지 않는다** — 오늘 256건 중 실변경은 2건이었다). `stale` 자체는 **뒤집지 않는다**(R18: 임계·판정을 계측 추가로 바꾸지 않는다).
  - `src/messiah/ops/status_board.py:189` — `code_version` 딕셔너리에 위 두 필드 수록, `summary` 문구를 `"코드 40e9968 — 전 프로세스 동일 · 작업트리 실변경 2파일(ops/feature_health_rolling.py, ops/fix_verification.py)"` 형태로 확장.
  - `scripts/self_check.py:480` `check_git_state()` — `dirty(dev 허용)` 문구에 **실변경 건수와 파일명(최대 3개)** 을 붙인다. 판정(`ok`)은 그대로 dev 허용.
- **회귀 위험**: `status_snapshot.json` 스키마에 필드가 늘어난다 — 소비자는 `ops/status_board.py:397`의 렌더러와 UI. 둘 다 `.get()` 접근이므로 하위호환. `git diff` 서브프로세스 1회가 장중 매분 도는 경로에 들어가면 비용이 문제 — **기동 시 1회 측정 후 캐시**하고 매분 재측정하지 않는다.
- **검증 방법**: pytest `tests/core/test_version.py` 신규 3건(clean / CRLF만 변경 / 실변경) · 오늘의 작업트리 상태를 그대로 재현한 픽스처로 `2파일`이 나오는지 · 커밋 후 `status_snapshot.json`에 `worktree_dirty: false` 가 뜨는지.
- **적용 시점**: 장후, **F-1 커밋 이후**(F-1이 작업트리를 비워야 F-2의 clean 경로를 실측할 수 있다).
- **결정 필요 사항**: 없음.

### F-3. Y-5 관측 기준의 태그명 정정 — P2 · 대응 이상점 2-1

- **원인 가설**: 08-18 장후에 기준을 쓰면서 코드를 대조하지 않고 의미상 이름(`RegimeSeeded`)을 적었다.
- **변경 파일**:
  - `dev_memory/NEXT_TODO.md:5494` — `RegimeSeeded` → `RegimeWarmStart`. **정정 사유를 한 줄로 병기**한다(`(08-19 정정: 코드의 태그명은 RegimeWarmStart · core/logging.py:242)`). 원문을 조용히 바꾸지 않는다.
- **회귀 위험**: 없음(문서).
- **검증 방법**: 장후 채점에서 Y-5가 **충족**으로 뒤집히는지. 오늘 08:25:35 `RegimeWarmStart` 1건이 이미 증거다.
- **적용 시점**: 장후(dev_memory도 파일이므로 장전 변경을 하지 않는다는 원칙을 지킨다).
- **결정 필요 사항**: 없음.

### F-4. `check_schedule_drift`·`check_boot_recovery` 대조 범위를 4종 전체로 넓힌다 — P2 · 대응 이상점 2-2

- **원인 가설**: 2026-08-10 신설 당시 사건이 수집 계열(08:20/08:25)에서 났기 때문에 그 범위로 좁혔다. 사건의 원인(사람이 GUI로 시각 변경)은 계열을 가리지 않는다.
- **변경 파일**:
  - `src/messiah/ops/task_schedule.py` — `collection_tasks()` 옆에 `all_tasks()` 신설(정본의 전 항목). 기존 함수는 **그대로 둔다** — 기동 창 파생은 수집 계열만 봐야 옳다.
  - `src/messiah/ops/host_health.py:414` `check_schedule_drift()` — `expected` 를 `all_tasks()`로 바꾸되 **판정을 계열별로 나눈다**: 수집 계열의 창 이탈은 기존대로 `ok=False`, 비수집(Postmarket/Shutdown)의 시각 어긋남은 **finding 문구만 추가하고 `ok`는 안 뒤집는다**(R18 — 계측 추가가 기동을 막지 않는다). 08-18 실측처럼 Postmarket이 15:48에 정상 종료하는 날에도 문장이 나와야 하므로, 문구는 "정본 15:45 vs 등록 15:45 — 일치" 형태의 **전량 나열**로 한다.
  - `check_boot_recovery()` — `부팅 트리거 무장 2개` 를 `무장 2/4(Messiah, Messiah-G2 · Postmarket·Shutdown은 시각 트리거 전용)` 로 바꿔 **분모를 드러낸다**. 2개가 정상인지 부족인지 문장만 봐서는 지금 알 수 없다.
- **회귀 위험**: 자가점검 문구가 길어진다(현재 `host` 라인이 이미 200자 초과). `schedule_drift` 를 `host` 라인에서 **자체 축으로 분리**하는 것을 함께 검토.
- **검증 방법**: pytest — 정본 4종 픽스처로 (a) 전 일치 (b) Postmarket만 어긋남 → `ok=True` + finding 1건 (c) 수집 계열 창 이탈 → `ok=False`. 다음 거래일 아침 자가점검에 4종이 전부 인쇄되는지.
- **적용 시점**: 장후. 우선순위는 F-1·F-2 다음.
- **결정 필요 사항**: `schedule_drift` 축 분리 여부 — **권고: 분리한다.** `host` 한 줄이 disk/power/docker/cpu/외부파이썬/boot_recovery/schedule_drift 일곱을 겸하고 있어 R6(태그 1개=심각도 1개)의 정신에 어긋난다.

### 적용 순서와 커밋 계획

1. **커밋 ①** (15:35 직후, 배치 전) — F-1. 메시지 초안: `[MW0601] 「추적으로 되돌렸다」고 적어 두고 add를 하지 않았다 — .gitignore 철회 결선 + 산출물 인덱스 등록`
2. **커밋 ②** — F-2. 메시지 초안: `[MW0601] 실행 코드와 커밋 코드는 대조했고 디스크의 코드는 아무도 안 봤다 — worktree dirty 계측`
3. **커밋 ③** — F-3 + F-4. 메시지 초안: `[MW0601] 코드에 없는 이름을 관측 기준에 적었다 — Y-5 태그 정정 + 스케줄 대조 4종 확장`

---

## 3. 고도화 방안

당일 관측에서 출발한 것만 쓴다.

### G-1. 「선언했으나 실효 없음」을 기동 자가점검이 잡게 한다 — `.gitignore` negation 실효 검사

- **관측 근거**: 오늘 1-2가 정확히 그 형태다. `.gitignore` 주석이 *"무효인 negation은 오류를 내지 않고 그냥 아무 일도 안 하므로, 고쳤다고 믿은 채 몇 달이 지날 수 있는 자리다"* 라고 **스스로 경고해 놓고**, 정작 `git add` 누락으로 같은 결과에 도달했다. 경고문은 사람을 막지 못했다.
- **제안 내용**: `scripts/self_check.py`에 `check_tracked_artifacts()` 축 신설. `.gitignore`의 `!` 로 시작하는 패턴을 파싱해, **각 패턴에 실제로 매칭되는 파일이 하나라도 `git ls-files`에 있는가**를 확인한다. 0건인 패턴이 있으면 `[WARN] tracked  살리려 한 패턴 3종에 추적 파일 0건 — !logs/pass_cycles/*.json …`. **판정은 안 뒤집는다**(dev·live 공통 경고).
- **기대 효과**: 오늘의 P1-2를 **커밋 다음 날 아침에 자동으로** 잡는다. 측정 가능한 형태: `!` 패턴 수 대비 추적 파일 0건 패턴 수 = 오늘 기준 **7/7 → 목표 0/7**(`pass_cycles`는 파일 생성 전이므로 예외 허용 필요).
- **비용·위험**: 구현 반나절. `.gitignore` 파싱이 완전할 필요는 없다 — `!` 줄만 읽으면 된다. R18 섀도 계측 **불필요**(차단 로직이 아니라 경고 축).
- **선행 조건**: F-1(커밋)이 먼저. 안 그러면 첫날부터 7건 경고가 뜬다.
- **우선순위 제안**: **이번 주**.

### G-2. 완성봉 유예 ↔ 회선 p90 델타를 `daily_integrity`에 한 필드로 남긴다

- **관측 근거**: 오늘 자가점검이 같은 문장을 **두 번**(05:55·08:20 기동) 인쇄했다 — `유예 500ms vs 전일 회선 p90 927ms(2026-08-18) — 완성봉이 늦은 틱을 놓칠 수 있다`. G-0818P-2가 의도한 *"이 줄로 매 아침 분포가 쌓인다"* 는 **로그에만 쌓인다.** 로그는 로테이션 대상이고, 5거래일치를 세려면 사람이 5개 파일을 grep해야 한다. 실측: 08-14 924ms → 08-18 927ms 두 점뿐인데 이미 파일 두 개를 열었다.
- **제안 내용**: `src/messiah/ops/integrity_report.py` — `delivery_latency` 옆에 `bar_close_budget` 딕셔너리 추가: `{"grace_ms": 500, "prev_p90_ms": 927, "delta_ms": 427, "exceeded": true, "consecutive_days": n}`. 값의 원천은 이미 `check_bar_close`가 읽는 `fix_verification.load_daily_reports()` — **새로 계산하는 것이 없다**. `consecutive_days`는 `fix_verification`의 기존 연속일 계산 재사용.
- **기대 효과**: G-0818P-2가 대기 중인 *"5거래일 분포를 본 뒤 별건 결정(R18)"* 이 **파일 하나 읽어서** 판정 가능해진다. 측정: 5거래일 채워지는 날짜가 08-25 → 그날 유예 조정 결정 착수.
- **비용·위험**: 반나절. 기존 값의 재배치라 새 실패 모드 없음. **임계는 절대 자동 변경하지 않는다**(R18) — 이 필드는 읽기 전용 계측이다.
- **선행 조건**: 없음. F-1과 독립.
- **우선순위 제안**: **이번 주** (5거래일 창이 08-25에 닫히므로 그 전에).

### G-3. 장전 점검 산출 시각 자체를 계측한다 — 오늘 Y-7이 성립했다는 것을 사람만 안다

- **관측 근거**: 오늘 **Y-7이 성립했다** — 장전 점검이 08:50, 개장 10분 전에 나왔다(어제 13:29, 지연 284분). 2거래일 연속 실패가 끊겼다. 그런데 **그 사실을 아는 것은 이 보고서 헤더 한 줄뿐**이다. `logs/dailycheck/2026-08-19_pre_report.md`의 mtime 말고는 어느 계기도 이 회복을 세지 않는다. 08-18 장후에 겪은 *"9건이 회복됐는데 아무도 못 봤다"* 와 정확히 같은 형태다.
- **제안 내용**: `src/messiah/ops/fix_verification.py`의 등록부에 `premarket-check-before-open` 항목 등록. 판정: `logs/dailycheck/<날짜>_pre_report.md` 의 mtime이 당일 **09:00 이전**인가. 등록부에 들어가면 `verification_scoreboard_*.json`이 자동으로 연속일·회복을 세고, `clean_streak` 가 오른다.
- **기대 효과**: 오늘의 회복이 **내일 아침 스코어보드 한 줄로** 나타난다. 측정: `premarket-check-before-open` `clean_streak` 1 → 3(3거래일 연속이면 졸업 궤도).
- **비용·위험**: 2시간. 위험은 파일 mtime을 판정 근거로 쓰는 것 — 복사·백업 복원이 mtime을 바꾼다. **보고서 본문 첫 줄의 `점검 시각:` 을 파싱**하는 쪽이 안전하다(오늘 이 보고서가 그 형식을 지킨다). 둘 중 후자를 권고.
- **선행 조건**: F-1(보고서가 git 추적이 되어야 mtime 아닌 커밋 시각도 근거가 된다).
- **우선순위 제안**: **다음 단계** — F-1·G-1이 먼저.

### 로드맵 반영 제안

| 항목 | 현 로드맵 위치 | 제안 위치 | 사유 |
|---|---|---|---|
| 「등록부 회복률」 관측 항목 | 미등재 (08-18 제안, 승인 대기) | Ver2.0 W단계 관측 항목 | 08-18에 데이터 원천(스코어보드 파일) 생성 완료. **오늘 Y-7 회복이 어디에도 안 세어지는 것**이 필요성의 두 번째 실측. 마스터플랜은 사용자 문서 — **승인 후 반영** |
| 「선언-상태 정합」축 (G-1) | 미등재 | 기동 자가점검 항목(불변원칙 6) | 오늘 P1-2가 첫 사례. 자가점검은 이미 "스키마 정합·번들 해시" 를 보고 있고 이것은 그 계열이다 |

---

## 4. 다음 거래일 관측 예정

오늘 세운 fix/고도화 중 답이 나오는 것.

| ID | 관측 대상(태그/지표) | 판정 기준 | 판정 예정일 |
|---|---|---|---|
| **A-1** | `git ls-files logs \| wc -l` | **> 0** — F-1의 유일한 합격 조건 | 2026-08-19 장후 |
| **A-2** | `status_snapshot.json` `code_version.worktree_dirty` | `false` (F-1·F-2 적용 후) | 2026-08-20 장전 |
| **A-3** | NEXT_TODO Y-5 채점 | `RegimeWarmStart` 1건 08:25대 → **충족**으로 뒤집힘 | 2026-08-19 장후 |
| **A-4** | 자가점검 `schedule_drift` 문구 | 4종 전량 나열 · `boot_recovery` 가 `2/4` 형태로 분모 표시 | 2026-08-20 장전 |
| **A-5** | `bar_close` 전일 p90 (오늘 값 기준) | 08-19 `delivery_latency.p90` 이 세 번째 점. **500ms 초과 3일 연속이면** G-2 착수 근거 확정 | 2026-08-20 장전 |
| **A-6** | `!` 패턴 대비 추적 0건 패턴 수 (G-1) | 7/7 → 0/7 (`pass_cycles` 예외 허용 시 0/6) | G-1 구현 후 |

**장후 점검으로 이월** — 어제 세운 관측 항목은 오늘 장후가 채점한다: W-1(`verification_scoreboard_20260819.json`) · W-3(`logs/pass_cycles/`) · Z-1~Z-4 · Y-1~Y-6 · Y-8(p50 2일 연속 500ms 초과 여부).
**오늘 성립 확정** — **Y-7**(장전 점검 09:00 전 산출, 08:50) · **W-2**(`bar_close` 전일 p90 927ms 경고 · `daily_integrity_20260818.json` `delivery_latency.p90 = 0.9271` 과 **일치**).

---

## 5. dev_memory 반영

- `DECISION_LOG.md` 추가 항목: `[MW0601] 「추적으로 되돌렸다」고 적어 두고 add를 하지 않았다 — 2026-08-19 장전 점검`
- `NEXT_TODO.md` 추가 체크박스: **10건** (F-1~F-4 · G-1~G-3 · A-1·A-4·A-6)
- 완료 처리한 기존 항목: **Y-7**(장전 점검 09:00 전 산출 — F-0818I-4「스케줄러 이관」 **불필요 확정**) · **W-2**(`bar_close` 전일 p90 대조 일치)
