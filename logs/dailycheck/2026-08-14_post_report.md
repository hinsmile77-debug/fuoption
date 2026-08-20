# MESSIAH 일일 점검 — 2026-08-14 / 장후

- 점검 시각: 16:05 KST
- 대상 국면: post
- HEAD `e37d387` · 실행 중 `e37d387` (전 프로세스 동일, `code_version.stale=false`) · 미커밋 179건(표시상 — 실제 변경 0, CRLF 잡음. F-7 기록 참조)
- 증거: `logs/dailycheck/evidence_20260814_post.md`
- 당일 커밋: **0건**

## 0. 한 줄 결론

**수집과 매매 경로는 오늘 하루 무결했다. 그러나 그 사실을 확인해야 할 장후 검증 경로 전체가 어제 종목을 보고 있었다 — 오늘 "이상"이라고 보고된 것의 상당수는 오늘 일어난 일이 아니다.**

| 국면 | 판정 | 근거 요약 |
|---|---|---|
| 장전 | 이상(오전 보고 유지) | 롤 웜스타트 전면 결손(P0) — 오늘 종일 UNKNOWN으로 귀결됨이 확정 |
| 장중 | 정상 | 1분봉 410행 · 결손 0분 · 거래량 항등식 1.000(118,599/118,599) · ERROR 0 · 재기동 0 · 계명 3·4 위반 0 |
| 장후 | **이상 (P0 2건)** | 배치 5단계 중 4단계가 `A05608`(어제 종목)을 조회 — 무결성 리포트 전면 오염, `vol_scorecard` 미산출, fix 재발 판정 오판 |

**오늘 거래 자체에는 위험이 없었다**(주문 0건, 판단 14건 전부 NO_TRADE). P0로 올리는 이유는 **관측 경로가 무결한 하루를 "410분을 잃은 하루"로 기록했기 때문**이다. 그 기록이 다음 거래일 자가점검·fix 채점·승격 판단의 입력이 된다.

---

## 1. 이상점 정리보고

### P0 — 오늘 거래에 직접 위험

#### 1-1. 장후 배치 5단계 중 4단계가 어제 종목(A05608)을 조회했다 — 롤 당일 심볼 하드코딩

- **증상**: 2026-08-14는 선물 롤 당일이다(근월물 `A05608` → `A05609`). 장후 배치는 `A05608`로 5단계를 돌았고, 그 종목에는 당일 데이터가 한 행도 없다. 4단계가 "데이터 없음"을 **정상적인 0**으로 처리하고 통과했다.

- **근거**:
  ```
  15:45:03  === MESSIAH 장후 절차 — 2026-08-14 / A05608 ===
  15:45:03  === 1/5 장중 조각 통합 — run_compact.py --symbol A05608 --date 2026-08-14
              통합할 조각 없음 — 이미 통합됐거나 그날 데이터가 없다(멱등)
  15:45:0x  === 2/5 상위 Horizon 재합성 — run_recompose.py --symbol A05608 ...
              완료 — 0일 / 상위봉 0행 재합성
              ** 재합성한 날이 없다 ** — 구간에 1분봉이 있는 날이 없다.
  15:45:1x  === 4/5 변동성 축 채점 — run_vol_scorecard.py --date 2026-08-14 --symbol A05608
              아카이브 없음 — 그날은 수집이 안 돌았다
  15:45:19  === 5/5 무결성 리포트 재생성 — daily_integrity_report.py --symbol A05608
              봉 1m: 0개 데이터 없음 · ticks: 0분 · 소급 불가 손실(오늘): 410분 ❌
  ```
  `logs/postmarket_20260814.log` · 15:45:03~15:46:29

  하드코딩 지점:
  ```python
  # scripts/run_postmarket.py:127
  parser.add_argument("--symbol", default="A05608")
  ```

  **오늘의 정본 심볼이 `A05609`라는 증거 5종** (전부 독립 소스):
  | 소스 | 값 |
  |---|---|
  | `data/bars/A05609/1m/2026-08-14.parquet` | **410행** (A05608은 08-13이 마지막) |
  | `data/ticks/A05609/2026-08-14/` | 8파일 · **110,397행** |
  | `CollectorFirstTick` 08:44:58 | `symbol=A05609` |
  | `logs/self_eval_2026-08-14.json` | `"symbol": "A05609"` |
  | `logs/g2_daily_returns.jsonl` 마지막 행 | `{"date":"2026-08-14","symbol":"A05609"}` |

  3/5단계(`verify_archive_volume.py`)만 `--symbol` 인자를 받지 않아 정본 심볼을 스스로 찾았고, **그 단계만 정확한 답을 냈다**:
  ```
  A05609 2026-08-14  비율 1.000  (공통 410분 · 공식 410분 · 아카이브 118,599 / 공식 118,599)  OK
  ```

- **기준 위반**:
  - **금지계명 9 — 종목코드 맹신 금지**(SYSTEM.md §8). 정확히 이 계명이 막으라고 만들어진 사고다.
  - **R4 — 절대경로·포트·계좌 하드코딩 금지, 전부 설정/환경변수**(SYSTEM.md 규칙표). `default="A05608"`은 만기가 있는 값을 소스에 박은 것이다.
  - **R10 / 계명 12 — 조용한 폴백 금지**. 4개 단계가 "데이터 없음"을 WARNING 없이 정상 종료로 처리했다. `run_recompose`의 `** 재합성한 날이 없다 **`는 별표를 달았으나 종료 코드는 0이고 배치는 계속 진행했다.

- **영향**:
  1. **`logs/daily_integrity_20260814.json` 전면 오염** — `1m rows=0`, `tick_rows=0`, `irrecoverable_loss_minutes=410`. 실제는 410행 / 110,397틱 / 손실 0.
  2. **`logs/vol_scorecard_20260814.json` 미산출** — `unmeasured: ['변동성 축 채점(run_vol_scorecard.py 미실행)']`. 변동성 축이 오늘 채점되지 않았다.
  3. **2/5 재합성이 실제로 수행되지 않았다** — A05609 상위봉은 장중 합성분만 존재한다(3m 137 · 5m 82 · 10m 42 · 15m 28 · 30m 15행). `IntegrityThresholdBreached`가 15:36:29에 *"상위 Horizon 버킷에서 1분봉 2개 유실 — 장 종료 후 `run_recompose.py`로 재합성 필요"*라고 지시했는데, **그 재합성이 다른 종목에 대해 돌았다.** 12:51의 버킷 유실 2건이 오늘 치유되지 않은 채 남아 있다.
  4. **fix 재발 채점 오판** — 아래 1-2.

- **신규 여부**: **신규(확정)**. dev_memory 어디에도 없다. 장전 보고서가 P0로 잡은 F-1(웜스타트 롤 경계)과 **같은 뿌리**다 — 장전 F-1 원인 문구 *"심볼은 계약의 이름이지 시계열의 이름이 아니다"*가 아카이브 조회에 대한 진단이었는데, **같은 착오가 장후 배치 진입점에도 독립적으로 존재한다.** 롤이 4주에 한 번이라 오늘 처음 드러났다.

#### 1-2. 오염된 리포트가 fix 검증을 오판했다 — 재발 12건 중 1건 허위 · 3건 수치 오류

- **증상**: `FixVerificationRecurred` 12건(ERROR)이 15:46:29에 한꺼번에 찍혔다. `fix_verification`은 `logs/daily_integrity_*.json` 이력을 대조해 채점하므로(`src/messiah/ops/fix_verification.py` 모듈 docstring), 1-1의 오염이 그대로 채점 입력이 됐다.

- **근거** — 오늘(2026-08-14) 날짜로 위반 판정된 4건의 실사:

  | fix_id | 판정 근거(리포트) | 실측 | 판정 |
  |---|---|---|---|
  | `tick-collection-live` | `tick_rows` 0 < 1000 | **110,397행** (`data/ticks/A05609/2026-08-14/` 8파일) | **허위 — 오늘 위반 없음** |
  | `archiver-restart-restore` | `series_head_gap_minutes_max` **410분** > 20 | ticks(410분)는 허수. 제외 시 실제 최대는 `option_chain/weekly_thu` **33분** | **위반은 성립 · 근거 수치 12배 오류** |
  | `truncation-is-visible` | `series_coverage_pct_min` **0.0%** < 95 | ticks(0.0%)는 허수. 제외 시 실제 최소는 `option_chain/regular` **94.5%** | **위반은 성립 · 근거 수치 오류** |
  | `regime-not-constant` | `regime_unknown_ratio` 100% > 50% | `RegimeClassified` **14/14 UNKNOWN** — 실측 일치 | **진짜 — 1-3 참조** |

  ```
  15:46:29 [ERROR] FixVerificationRecurred  tick-collection-live: 2026-08-14에 기준 위반(오늘)
                   — 수정이 듣지 않았다 (tick_rows ≥ 1000)
  15:46:29 [WARNING] IrrecoverableLossBudgetExceeded  소급 불가 손실이 5거래일에 471분
                   (> 예산 20분) · 최대 2026-08-14 410분(87%) · 나머지 4일 61분
  ```
  `logs/postmarket_20260814.log` · 15:46:29

  **정면 모순 — 같은 날 두 산출물이 반대를 말한다**:
  ```
  logs/status_snapshot.json (15:34:45)  "irrecoverable_loss": {"clean": true, "lost_items": 0,
                                          "summary": "오늘 소급 불가 손실 없음"}
  logs/daily_integrity_20260814.json    "irrecoverable_loss_minutes": 410.0
  ```
  `IrrecoverableLossBudgetExceeded`의 5거래일 471분 중 **87%가 이 허수**다. 실제 누적은 61분이고, 예산 20분 초과는 여전하나 **"급증했다"는 인상은 전적으로 오늘의 오조회에서 나왔다.**

- **기준 위반**: SYSTEM.md **L18 계열 / 국면 공통 원칙** — *"건수 0은 두 가지다: 진짜 없었거나 계측이 없거나. 어느 쪽인지 로그로 구분한다"*(phases.md §D). `tick_rows=0`은 계측이 없었던 것인데 진짜 없었던 것으로 채점됐다. **R6**(태그 1개=심각도 1개)도 걸린다 — `FixVerificationRecurred`(ERROR)가 참·거짓을 구분 없이 겸했다.

- **영향**: 재발 12건은 이 시스템에서 **최우선 보고 대상**이다(phases.md C-4: *"재발은 항상 P0 보고 대상"*). 그 신호의 신뢰도가 오늘 훼손됐다. 허위 ERROR가 섞이면 다음번 진짜 재발이 같은 무게로 읽히지 않는다 — `fix_verification` 모듈이 만들어진 이유(*"재발을 새로운 사고로 취급하며 또 새 가설을 세웠다"*)와 정확히 반대 방향의 실패다.

- **신규 여부**: **신규(확정)** · 1-1의 하위 증상.

> **덧 — 시스템이 스스로 모순을 감지했다.** 오늘 `breaches` 마지막 항목:
> *"아침 잘림 판정이 축마다 다르다 — 잘렸다: 계열 머리 구멍 410분 / 아니다: 기동 지연 +0.6분 · 거래량 아침 미수집 0분. 어느 축이 옳은지 모르는 상태 자체가 볼 것이다"*
> 12:30 점검이 세운 **G-6(관측 표면 간 불일치 자체를 신호로)이 이미 부분 구현돼 있었고, 오늘 그것이 작동했다.** 다만 모순을 **감지**만 하고 원인(심볼 오조회)까지 밀어붙이지 못했다 → G-8.

### P1 — 정확성·관측 훼손

#### 1-3. 퇴화 검사가 표본이 적은 Horizon을 "정상"이라고 말한다 — V-18의 결론

- **증상**: 15:35:06 퇴화 판정에서 **표본이 적을수록 결과가 관대해지는 역전**이 나타났다. 가장 위험한 30m가 "퇴화 0건"으로 기록됐다.

- **근거**:
  ```
  15:35:06 [WARNING] FeatureHealthDegenerate  10m 피처 45개 … {samples: 41}
  15:35:06 [WARNING] FeatureHealthDegenerate   5m 피처  8개 … {samples: 81}
  15:35:06 [WARNING] FeatureHealthDegenerate   3m 피처  3개 … {samples: 136}
  15:35:06 [WARNING] FeatureHealthDegenerate   1m 피처  1개 … {samples: 409}
  15:35:06 [INFO]    FeatureHealthSummary     15m 피처 퇴화 0건 (27표본)   ← 판정 스킵
  15:35:06 [INFO]    FeatureHealthSummary     30m 피처 퇴화 0건 (14표본)   ← 판정 스킵
  ```
  `logs/l1_daily_20260814.log` · 15:35:06 · 6건

  ```python
  # src/messiah/features/engine.py:88
  _MIN_SAMPLES_FOR_HEALTH = 30
  # :121  return self.n >= _MIN_SAMPLES_FOR_HEALTH and self.n_nan == self.n
  ```
  15m(27) · 30m(14)은 임계 30 미만이라 `always_nan`이 **구조적으로 False**다. 45개가 죽은 10m와 달리, 이 둘은 검사되지 않았다.

- **기준 위반**: **코드 자신의 명시적 의도와 로그 출력이 반대다.** `engine.py:298-301` docstring:
  > *"표본이 `_MIN_SAMPLES_FOR_HEALTH` 미만인 Horizon은 판정하지 않는다(빈 목록) … 30m처럼 하루에 15봉밖에 안 나오는 Horizon은 그래서 대부분의 날 판정되지 않는데, **그게 맞다: 표본이 없는 것을 "정상"이라고 말하지 않는다.**"*

  그런데 `log_feature_health()`(engine.py:340-367)는 `degenerate_count == 0` 하나로 분기해 **`FeatureHealthSummary`(INFO) "퇴화 0건"**을 찍는다. 판정 스킵과 측정된 0이 같은 태그·같은 문구로 나간다. `log_feature_health` 자신의 docstring이 *"`0건`이 **측정된 0**이라는 뜻이 되기 때문(L18)"*이라고 적은 그 목적이 무효화된다. **SYSTEM.md L18 · R6**(태그 1개=심각도 1개 — `FeatureHealthSummary`가 "검사해서 0"과 "검사 안 함"을 겸함) · **계명 6**(피처 불일치 침묵 금지).

- **영향**:
  1. **30m는 오늘 웜스타트 0봉으로 시작한 가장 위험한 Horizon인데 검사에서 빠졌다.** `nan_ratio` 30m는 중앙 0.61 / 최종 0.60으로 임계 20%의 3배였다 — 그 상태의 피처가 퇴화 검사에서는 백지로 나온다.
  2. `no-degenerate-features` fix 채점(`degenerate_feature_count ≤ 0`)이 이 값을 읽는다(`fix_verification.py:273`). 검사 안 된 Horizon이 분모에서 조용히 빠진다.
  3. **롤 당일마다 재현된다** — 롤 직후엔 상위 Horizon 표본이 항상 얕다.

- **신규 여부**: **신규(확정)**. 12:30 점검이 `NEXT_TODO`에 남긴 **V-18** *"Horizon 6종 전부 등장(= 검사된 0)"*의 결론이다. **6종은 전부 등장했으나, 그중 2종의 0은 "검사된 0"이 아니라 "검사 안 된 0"이었다.** 예측의 전제가 틀렸다.

#### 1-4. 국면 UNKNOWN 100% 종일 확정 — V-11 · V-13 결론, 월요일도 UNKNOWN 확정

- **증상**: 종일 14회 판정 전부 UNKNOWN, 14회 결정 전부 NO_TRADE, 게이트는 `regime` 단독.

- **근거**:
  ```
  08:25:31 [WARNING] RegimeWarmStartShort  충전 0봉 < 하한 22봉 — 오늘 국면은 UNKNOWN으로 시작한다
  09:00:02 [INFO] DecisionEmitted  ② Regime=UNKNOWN — 이벤트/미판정 국면
                  {"symbol":"A05609","side":"NO_TRADE","gate":"regime"}
  ```
  `logs/g2_daily_20260814.log` · `RegimeClassified`×14 · `DecisionEmitted`×14
  ```
  regime_distribution: {'UNKNOWN': 14}      ← V-11 확정 (12:30 8/8 → 종가 14/14)
  decision_funnel:     {'regime': 14}       ← V-13 확정 (타 gate 0건)
  grep -o '"gate": "[a-z_]*"' → 14 "gate": "regime"   (단일)
  ```

  **V-19 결론 — 월요일 예측이 확정됐다**: `data/bars/A05609/30m/2026-08-14.parquet` = **15행**. 12:30 점검의 기대 범위(13~15) 상단이지만 **하한 22에는 7봉 모자란다.** F-1(선행 심볼 이어읽기) 없이 월요일 08:25에 웜스타트하면 **15 < 22 → 또 UNKNOWN으로 시작한다.** 2거래일째 판단 정지가 확정이다.

- **기준 위반**: 마스터플랜 Ver2.0 판단 사슬 ②. `daily_integrity` breach: *"국면 판정의 100%가 UNKNOWN(임계 50%) — Meta Decision 규칙 ②가 그만큼을 NO_TRADE로 보낸다(Risk·Sizer·OrderGateway가 그 비율만큼 미검증)"*. **R18**(섀도 계측 20거래일 후 승격)의 분모가 오늘도 안 늘었다.

- **신규 여부**: **기존** — 장전 보고서 P0 이상점 1-1(F-1). **새 발견으로 보고하지 않는다.** 오늘의 수확은 세 가지 확정뿐이다: ① V-11/V-13 종가 확정, ② `regime-not-constant` 재발 판정이 **오염 없는 진짜**, ③ **V-19로 월요일까지 판단 정지가 예측이 아니라 산술이 됐다** — F-1의 마감이 "월요일 개장 전"에서 **"오늘 저녁"**으로 당겨진다.

### P2 — 운영 부담·기술부채

#### 1-5. `StatusSnapshotWriteFailed` 2건 — 12:30 이후 1건 추가

- **증상**: 12:07:51 · 14:15:51 2건. 12:30 점검 시점에는 1건이었다.
- **근거**: `logs/l1_daily_20260814.log` · `[WinError 5] 액세스가 거부되었습니다: 'logs\\status_snapshot.json.24264.tmp' -> 'logs\\status_snapshot.json'`
- **기준 위반**: R10(관측 훼손이 조용하다).
- **영향**: 스냅샷 2회 결손. 상대 프로세스는 여전히 미특정.
- **신규 여부**: **기존** — `NEXT_TODO` **F-10**(`status_board.py:200-206` `os.replace` 재시도). **카운터만 2건으로 갱신한다.** 12:30 판단 승계: F-10은 원인 특정 없이도 유효.

#### 1-6. `ComposerFlushedIncomplete` / `ComposerLateBarDropped` 각 1건 — 오늘 치유되지 않았다

- **증상**: 12:51:06 3m 버킷(12:48)이 마지막 1분봉(12:50) 없이 짧게 확정 → 12:51:08 그 봉이 늦게 도착해 폐기.
- **근거**: `logs/l1_daily_20260814.log` 12:51:06~12:51:08 · `late_bar_drops: 2` · `status_snapshot`: *"버킷 손실 2건(늦은 봉 1 · 미완 확정 1) — 최악 3m 129계약 유실(0.1%)"*
- **기준 위반**: `composer-bucket-completeness` 재발(`late_bar_drops ≤ 0`, 08-13 위반).
- **영향**: 3m 129계약(0.1%). **1-1 때문에 오늘 재합성으로 치유되지 못했다** — 정상 경로였다면 2/5단계가 이 2봉을 복구했을 것이다. 이것이 1-1의 유일한 **실질적 데이터 영향**이다.
- **신규 여부**: 기존(재발) · 1-1의 하위 피해.

#### 1-7. 미커밋 179건 — 5거래일째, 원인 규명 완료 상태로 방치

- **증상**: `git status` 179건. 실제 변경은 0(CRLF 개행 잡음).
- **근거**: 증거 다이제스트 §1 · 12:30 점검 §1.8 실측 — `git diff --stat HEAD -- src/ scripts/` → 83 files, 30095 insertions / 30095 deletions(삽입=삭제) · `--ignore-all-space` → **비어 있음**.
- **기준 위반**: 없음(dev 모드라 계명 10 미해당). paper 승격 시 즉시 걸린다.
- **신규 여부**: **기존** — `NEXT_TODO` F-7. **카운터만 5거래일차로 갱신.** 오늘 저녁 커밋이 얹히면 실제 변경이 처음 생긴다.

### 오탐 — 조치 불필요

- **`postmarket` `SessionStart` 2회 (15:45:03 pid 6732 · 15:45:19 pid 23220)** — 두 번째는 5/5단계 서브프로세스 `daily_integrity_report.py`가 찍은 자기 세션 마커다(로그 27행, 바로 다음 줄이 `=== 일일 무결성 리포트 ===`). **중복 기동 아님.** 수집기 §9 적신호 17번 → **F-13에 흡수.**
- **`g2_daily` 30분 로그 공백 14건** — live 번들이 `30m` 단일종이라 판단 격자가 30분. 08:25→09:00 첫 35분 포함 정확히 14회 = `RegimeClassified` 14건과 일치. **설계대로.** 12:30에 8건이던 것이 종가 14건이 됐을 뿐이다. 기존 F-13.
- **`LaunchWindowRefused` 2회 (00:51:58, 07:18:21) · `SessionStart` 3회** — 실기동 1회. `9a4d4ea` 처리 형태. `git_sha` 3회 전부 `e37d387` = HEAD 동일.
- **`logs/ui_20260814.err.log` 없음** — stderr 출력 없음. `[crash_forensics] armed tag=ui target=stderr` 무장 확인.

### 통과 항목 — 오늘 라이브로 성립한 것

- **종료 시퀀스 무결(C-1)** — `l1_daily` 15:36:29 · `g2_daily` 15:35:00 · `postmarket` 15:46:29 전부 `SessionEnd` "정상 종료". `shutdown_watchdog` 15:40:00.87~15:40:01.71 정상. **재기동 0 · 비정상 종료 0 · 네이티브 크래시 0.**
- **수집 무결(C-3)** — 1분봉 **410행 · 결손 0분** · 거래량 항등식 **1.000**(아카이브 118,599 = 공식 118,599, 공통 410분) · `head/middle/tail_missing_minutes` 전부 0 · `flow_intraday/K2I` 커버리지 99.8%(434분, 최장 구멍 0분) · 틱 **110,397행**.
- **`delivery_latency` 산출됨** — p50 0.507 / p90 0.925 / **p99 1.026** / max 1.212 · 20,000표본. 12:30에 "장중 부재는 정상"으로 남긴 항목의 결론. **p99가 1초 근방** — 임계는 없으나 다음 점검의 기준선으로 기록.
- **계명 3·4 준수** — 장중 학습·배포·재기동 흔적 0. 당일 커밋 0건. `session_git_shas: ["e37d387"]` 단일.
- **W-15 유지** — `OptionChainSkipped` 10건 전량 08:21:40~08:43:20, **09:00 이후 0건**. 장전 보고서가 「확인 필요」로 이월한 *"1-2의 원인 — 롤인가 장전 창의 성질인가"*는 **12:30에 이미 롤 직후 기준가 부재로 확정**됐고 종가까지 유지됐다. → F-1에 흡수, 별건 P1 승격 안 함.
- **V-4 유지** — `OptionChainCalendarViolation` 0건. 목위클리 재개(08-14) 판정식 정확.
- **`InvestorFlowPollRetried` 4 + `OptionChainPollRetried` 4** — 전부 `attempts=2` 1회 재시도 복구, 미복구 0. `dbe37df` 5xx 백오프 유효. 빈도 정상 범위(08-11 7 · 08-12 7 · 08-13 14 · 오늘 8).
- **`clock_skew` +1.264초** · **디스크 546.5GB** · **`command_center_ui` UP(:8511) · 자동 재기동 0회** · `circuit_breaker.phase normal` · `gateway_halted false`.
- **`FixVerificationPassed` 9건** — `ui-crash-isolation`(9거래일) · `crash-forensics-armed`(9) · `clock-sync-restored`(7) · `horizon-volume-identity`(7) · `crash-count-measurable`(7) · `boot-recovery-armed`(6) · `canonical-consumers-wired`(5) · `no-silent-process-death`(5) · `morning-launch-actually-happens`(4).

### 확인 필요 (확정 아님)

- **W-9 — 08-13 분봉 420분 vs 395분의 책임 소재.** 장전에서 장후로 이월했으나 **오늘도 판정하지 못했다.** 판정에는 KIS 분봉 API 재조회가 필요하고, 1-1 때문에 오늘 장후 배치가 그 축을 아예 건드리지 못했다. **판정 기준 불변**(420분이면 우리 수집 결함, 395분이면 브로커 공급 문제). **2026-08-17(월) 장후로 재이월** — 그때는 F-A 적용 후라 정상 심볼로 돈다.
- **`WinError 5`의 상대 프로세스** — UI(`app.py:873`)/백신/점검도구 중 미특정. `handle.exe`·Process Monitor 없이 사후 특정 불가. **F-10은 원인 특정 없이도 유효**하므로 선행조건으로 걸지 않는다(12:30 판단 승계).
- **`n_experts=0`의 갈래** — 오늘도 미확정. 30m `nan_ratio` 종가 0.60으로 임계의 3배 유지. ③ `u_h=1` 가설은 강해지지도 약해지지도 않았다. **F-5 적용 후 1회 관측이면 확정(W-21).**
- **`exit-code-matches-log` 재발의 실체** — `task_exit_codes: {available: False, detail: "조회 실패: TimeoutExpired"}`. **"위반"이 아니라 "측정 실패"다.** 08-11 위반 이후 오늘까지 이 축은 계속 `TimeoutExpired`로 판정 불가였을 가능성이 있다. `schtasks` 조회 타임아웃 자체가 결함일 수 있으나 **오늘 증거만으로는 구분 불가** → F-D에서 다룬다.

---

## 2. Fix 작업 구현계획

> **본 점검은 보고까지만 한다.** 장후는 적용 가능한 유일한 국면이지만(R11 · 계명 3·4 해제), 예약 실행 규약상 실제 구현은 사용자의 "구현해" 지시 이후다. 아래는 그대로 착수 가능한 수준으로 내린 계획이다.

### F-A. 장후 배치의 심볼을 오늘의 정본에서 받는다 — P0 · 대응 이상점 1-1

- **원인 가설**: `run_postmarket.py:127`의 `default="A05608"`이 **만기가 있는 값을 소스에 박았다.** 근월물은 `symbol_master.front_month_future_code()`(`src/messiah/broker/kis/symbol_master.py:221`)가 매일 결정하는데, 장후 배치만 그 경로를 안 탄다. 3/5단계(`verify_archive_volume.py`)는 `--symbol`을 아예 안 받고 스스로 찾도록 만들어져 **오늘 유일하게 옳았다** — 옳은 설계가 이미 리포 안에 있다.
- **변경 파일**:
  - `scripts/run_postmarket.py:127` — `default="A05608"` **제거**, `default=None`. 미지정 시 `symbol_master.front_month_future_code(day)`로 해석한다. 해석 결과를 `print`와 배치 헤더(`:286`)에 함께 남긴다: `=== MESSIAH 장후 절차 — 2026-08-14 / A05609 (근월물 자동 해석) ===`.
  - `scripts/run_postmarket.py` — 심볼 해석 직후 **선행 가드**: `data/bars/{symbol}/1m/{date}.parquet` 부재면 5단계에 들어가기 전에 `SymbolResolutionMismatch`(**ERROR**) 로그 + **exit 2**. "그날 데이터가 없다"를 정상 종료로 통과시키지 않는다(R10 · 계명 12).
  - `scripts/run_postmarket.py:189/202/237/254` — 4개 서브프로세스 인자에 해석된 심볼 전달(코드 변경 불필요, 해석값이 흐름).
  - `src/messiah/core/logging.py` — `SymbolResolutionMismatch`(ERROR) 태그 신설.
  - `scripts/run_recompose.py` · `scripts/run_vol_scorecard.py` · `scripts/run_compact.py` · `scripts/daily_integrity_report.py` — **`--symbol` 기본값 하드코딩이 있으면 전부 동일 처리.** 착수 시 `grep -rn 'default="A056' scripts/`로 전수 확인한다(오늘 확인한 것은 `run_postmarket.py` 하나뿐이다).
- **회귀 위험**: **과거일 재실행이 깨질 수 있다.** `--date 2026-08-12`로 돌리면 그날의 근월물(A05608)로 해석돼야 하는데, `front_month_future_code()`가 인자로 받은 날짜가 아니라 오늘 기준으로 답하면 조용히 틀린다. → **해석 함수에 반드시 `day`를 넘기고**, 넘긴 날짜와 해석 결과를 로그에 나란히 남긴다. 이 검증이 F-A의 pytest 핵심 케이스다.
- **검증 방법**:
  - `pytest tests/` 중 `run_postmarket` 범위 + 신규 케이스 3종: ① 롤 당일 해석(08-14 → A05609) ② 롤 전일 해석(08-13 → A05608) ③ 데이터 부재 시 exit 2 + `SymbolResolutionMismatch`.
  - **재실행 검증(오늘 즉시 가능)**: `python scripts/run_postmarket.py --date 2026-08-14 --symbol A05609` → `vol_scorecard_20260814.json` 생성 · `daily_integrity_20260814.json` 재생성(1m 410행 · tick_rows 110,397 · irrecoverable 0) · **2/5 재합성이 12:51 버킷 유실 2건을 실제로 치유하는지** 확인.
  - 다음 거래일: 배치 헤더에 `A05609 (근월물 자동 해석)` 등장.
- **적용 시점**: **즉시(오늘 저녁).** 프로세스는 이미 종료됐으므로 재시동 불필요. 월요일 08:20 정시 기동이 새 코드를 태운다.
- **결정 필요 사항**: **오늘자 `daily_integrity_20260814.json`을 덮어쓸 것인가.** **권고: 덮어쓰되 원본을 `daily_integrity_20260814_wrong_symbol.json`으로 보존한다.** 덮어쓰지 않으면 `fix_verification`이 월요일에도 오염된 이력을 읽어 같은 허위 재발을 반복한다. 지우면 "그날 무슨 일이 있었는지"의 증거가 사라진다. 08-05에 `daily_integrity_20260805_pre_recompose.json`을 남긴 선례가 그대로 적용된다.

### F-B. "데이터 0행"과 "조회 대상이 틀림"을 구분한다 — P0 · 대응 이상점 1-1 · 1-2

- **원인 가설**: F-A는 이번 원인을 막지만, **다음번 다른 원인으로 같은 종류의 오조회가 나면 리포트는 또 조용히 0을 쓴다.** 방어선이 진입점 하나뿐이면 얇다.
- **변경 파일**:
  - `src/messiah/ops/integrity_report.py` — `build_report()` 초입에 **조회 정합 가드**: 대상 심볼의 1분봉이 0행인데 **같은 날 다른 심볼 디렉터리에 데이터가 있으면** `symbol_mismatch_suspected`(bool) + 후보 심볼 목록을 리포트 최상단에 싣고, `provisional=True`로 표시한다. 이미 `provisional` 키가 스키마에 있다(오늘 `False`) — **신설이 아니라 사용이다.**
  - `src/messiah/ops/fix_verification.py` — 채점 시 `provisional=True`인 날짜는 **재발 판정에서 제외**하고 `VerificationStatus`에 `검증 보류`를 추가한다. 오염된 입력으로 ERROR를 찍지 않는다.
  - `src/messiah/ops/integrity_report.py` — `series_coverage`의 `ticks` 항목처럼 **심볼 종속 계열**과 `option_chain/*`·`flow_intraday/*` 같은 **심볼 무관 계열**을 필드로 구분(`symbol_scoped: bool`). `series_head_gap_minutes_max` / `series_coverage_pct_min` 집계 시 `provisional` 상태에서는 심볼 종속 계열을 분모에서 뺀다 — 오늘 410분·0.0%가 최대/최소를 삼킨 것이 정확히 이 문제다.
- **회귀 위험**: `provisional` 날짜가 fix 검증의 "N거래일 연속" 카운터에서 빠지면 **검증 완료가 하루씩 늦어진다.** 이는 옳은 방향이다(모르는 날을 통과로 세지 않는다). 다만 `boot-recovery-armed`(6/N) 같은 진행 중 항목의 기한이 밀릴 수 있으므로 `configs/pending_verifications.yaml`의 기한을 함께 점검한다.
- **검증 방법**: `pytest tests/ops/test_integrity_report.py` + `test_fix_verification.py` 신규 케이스 2종 — ① 심볼 오조회 합성 입력 → `provisional=True` ② `provisional` 날짜가 재발 판정에서 제외됨. replay: 오늘 오염 리포트를 입력으로 넣어 `FixVerificationRecurred`가 12건 → **`tick-collection-live` 제외 11건**이 되는지.
- **적용 시점**: 오늘 저녁, F-A 직후 별도 커밋.
- **결정 필요 사항**: 없음.

### F-C. 퇴화 판정 보류를 "0건"이라고 말하지 않는다 — P1 · 대응 이상점 1-3

- **원인 가설**: `log_feature_health()`(`engine.py:340-367`)가 `degenerate_count == 0` 단일 조건으로 분기한다. `samples < _MIN_SAMPLES_FOR_HEALTH`(=30)라는 **세 번째 상태**가 로그 어휘에 없다.
- **변경 파일**:
  - `src/messiah/features/engine.py:131-148` — `FeatureHealth`에 `judged: bool` 필드 추가(`samples >= _MIN_SAMPLES_FOR_HEALTH`). `feature_health()`(`:317-338`)에서 설정.
  - `src/messiah/features/engine.py:340-367` — 3분기로 변경: `judged=False` → **`FeatureHealthNotJudged`(WARNING)** `"30m 퇴화 판정 보류 — 14표본 < 최소 30"` / `judged=True and degenerate` → 기존 `FeatureHealthDegenerate`(WARNING) / `judged=True and not degenerate` → 기존 `FeatureHealthSummary`(INFO) 단, 문구를 `"퇴화 0건 (검사된 0 · 409표본)"`으로.
  - `src/messiah/core/logging.py:272` 부근 — `FeatureHealthNotJudged`(WARNING) 태그 등록.
  - `src/messiah/ops/integrity_report.py:642-645`, `:1430-1440` — `judged` 전파. `unmeasured` 배열에 `"{horizon} 피처 퇴화 판정(표본 {n} < 30)"` 추가. **`unmeasured`는 이미 있는 축이다** — 오늘 `['변동성 축 채점…', '진입점 종료 코드…']` 2건이 들어 있다.
  - `src/messiah/ops/fix_verification.py:273` — `degenerate_feature_count` 집계 시 `judged=False` Horizon을 분모에서 제외하고, 제외 사실을 판정 사유에 남긴다.
- **회귀 위험**: **매일 WARNING이 2건씩 는다**(15m·30m는 대부분의 날 표본 미달). *"매일 울리는 경고는 결국 아무도 안 본다"*(engine.py:308)는 이 파일 자신의 경고다. → **WARNING을 매일 찍지 말고, `unmeasured` 축에 싣는 것을 정본으로 한다.** 로그 태그는 남기되 **표본 미달이 전일 대비 악화됐을 때만** WARNING, 평시는 INFO `FeatureHealthNotJudged`. 태그 1개=심각도 1개(R6)를 지키려면 **태그를 둘로 나눈다**: `FeatureHealthNotJudged`(INFO, 평시) / `FeatureHealthJudgmentDegraded`(WARNING, 악화).
- **검증 방법**: `pytest tests/features/test_engine.py` 신규 3종 — ① 29표본 → `judged=False` ② 30표본 → `judged=True` ③ 로그 태그 분기. 다음 거래일 관측: 15:35에 `FeatureHealthNotJudged` 15m·30m 등장, `unmeasured`에 2건 추가.
- **적용 시점**: 오늘 저녁. **F-1(웜스타트)보다 뒤.** F-1이 들으면 30m 표본이 늘어 이 분기의 빈도가 줄기 때문에, 순서가 바뀌면 F-C의 효과를 잘못 읽는다.
- **결정 필요 사항**: `_MIN_SAMPLES_FOR_HEALTH = 30`을 Horizon별로 나눌 것인가. **권고: 지금은 나누지 않는다.** 30m는 하루 15봉이 상한이라 어떤 임계를 줘도 일간 판정은 불가능하고, 답은 임계 조정이 아니라 **다일 누적 판정**이다 → G-9.

### F-D. `task_exit_codes` 측정 실패를 위반과 구분한다 — P1 · 대응 「확인 필요」

- **원인 가설**: `schtasks` 조회가 `TimeoutExpired`로 실패하는데, `exit-code-matches-log`(`nonzero_task_exits ≤ 0`)가 이를 위반으로 채점한다. `task_exit_codes.available == False`가 이미 리포트에 있으나 채점이 안 읽는다.
- **변경 파일**:
  - `src/messiah/ops/fix_verification.py` — `nonzero_task_exits` 채점 전 `task_exit_codes.available` 확인. `False`면 `검증 보류`(F-B에서 추가하는 상태 재사용).
  - `src/messiah/ops/integrity_report.py` — `schtasks` 조회 타임아웃 값을 설정화하고(R4) 1회 재시도. 재시도 후에도 실패하면 `unmeasured`에 유지(이미 그렇게 동작 — 오늘 `'진입점 종료 코드(조회 실패: TimeoutExpired)'`).
- **회귀 위험**: 낮음. 재시도로 장후 배치가 수 초 길어진다.
- **검증 방법**: `pytest tests/ops/test_fix_verification.py` — `available=False` 입력 시 재발 판정 안 남. 다음 거래일: `exit-code-matches-log`가 `재발`이 아닌 `검증 보류` 또는 실측 판정.
- **적용 시점**: 오늘 저녁 또는 월요일 장전. **F-A/F-B보다 후순위.**
- **결정 필요 사항**: 없음.

### 이미 계획된 항목 — 오늘 관측이 우선순위를 바꾼 것

| 항목 | 출처 | 오늘의 변화 |
|---|---|---|
| **F-1** 롤 경계 웜스타트 (P0) | 장전 보고서 | **마감을 "월요일 개장 전" → "오늘 저녁"으로 당긴다.** V-19가 30m 15행을 확정해 월요일 UNKNOWN이 산술이 됐다(1-4) |
| **F-2** 롤 당일 자가점검 경고 (P0) | 장전 보고서 | 유지. **F-A와 같은 커밋으로 묶는다** — 둘 다 "오늘이 롤 당일임을 시스템이 먼저 말한다" |
| **F-9** NaN 경보를 `warmed_up` 가드에서 분리 (P1) | 12:30 보고서 | 유지. F-C와 인접하나 별건 |
| **F-10** `status_board.py` `os.replace` 재시도 (P2) | 12:30 보고서 | 유지. 발생 2건으로 증가(1-5) |
| **F-13** 수집기 오탐 제거 (P2) | 12:30 보고서 | **범위 확대** — g2 30분 공백 + **postmarket 이중 `SessionStart`**(오탐 절 참조) + 미커밋 179건 |
| **F-7** 미커밋 건수 CRLF 정정 (P2) | 12:30 보고서 | 유지. 5거래일차(1-7) |
| **F-5** `AggregatorNoContribution` (P1) | 기존 | 유지 — W-21 확정 조건 |

### 적용 순서와 커밋 계획

1. **커밋 ① `[MW0601] 배치가 어제 종목을 보고 하루를 채점했다 — 장후 심볼 자동 해석 + 오조회 가드`** — F-A + F-2. **최우선.** 커밋 후 즉시 `run_postmarket.py --date 2026-08-14` 재실행으로 오늘 리포트 정정.
2. **커밋 ② `[MW0601] 0행은 없었다는 뜻이 아니다 — provisional 리포트와 채점 제외`** — F-B + F-D.
3. **커밋 ③ `[MW0601] 심볼은 계약의 이름이지 시계열의 이름이 아니다 — 롤 경계 웜스타트`** — F-1 + F-9. **월요일 개장 전 필착**(장전 보고서 판단 승계, 오늘 저녁 권고로 강화).
4. **커밋 ④ `[MW0601] 검사 안 한 0을 0건이라 말하지 않는다 — 퇴화 판정 보류`** — F-C.
5. **커밋 ⑤ `[MW0601] 점검 도구의 오탐 제거`** — F-13 + F-7 + F-10.

각 커밋 전 `pytest`(해당 범위) + replay — 금지계명 2. 커밋 ①~③은 오늘 저녁 필수, ④~⑤는 주말 가능.

---

## 3. 고도화 방안 (당일 관측 근거)

### G-7. "오늘의 정본 심볼"을 단일 소스로 — 근거: 1-1

오늘 하나의 리포에서 **두 심볼이 동시에 정본 행세를 했다.** 수집·매매·`verify_archive_volume`은 A05609를, 장후 배치 4단계는 A05608을 봤다. 어느 쪽도 자기가 소수파인지 몰랐다.

`scripts/`와 `src/messiah/ops/` 전역에서 심볼 해석 경로가 몇 갈래인지 세고(오늘 최소 3갈래 확인: 하드코딩 default · `symbol_master` 조회 · 아카이브 스캔), **`core/symbol_resolution.py`에 `resolve_trading_symbol(day) -> str` 단일 함수를 두어 전부 그리로 모은다.** 해석 결과를 `logs/trading_symbol_<날짜>.json`으로 남겨 **모든 도구가 같은 파일을 읽게 한다** — 해석이 아니라 조회가 되면 갈라질 수 없다.

`verify_archive_volume.py`가 오늘 유일하게 옳았던 이유가 "인자를 안 받았기 때문"이라는 사실이 설계 방향을 이미 가리키고 있다. **선행: F-A**(F-A는 응급 처치, G-7이 구조 처치). **이번 주.**

### G-8. 관측 축 모순을 감지에서 원인 특정까지 — 근거: 1-2 · 12:30 G-6

오늘 `breaches` 마지막 항목이 스스로 말했다: *"아침 잘림 판정이 축마다 다르다 — 잘렸다: 계열 머리 구멍 410분 / 아니다: 기동 지연 +0.6분 · 거래량 아침 미수집 0분."* **12:30 점검이 세운 G-6이 이미 부분 구현돼 있었고 오늘 작동했다** — 그런데 거기서 멈췄다. 모순을 말했지만 "어느 축이 옳은가"를 풀지 않았고, 사람이 `data/bars/`를 직접 `ls` 해서야 답이 나왔다.

모순 판정에 **중재 규칙**을 붙인다: 축이 갈리면 ① 각 축이 어느 심볼·경로를 봤는지 `sources[]`에 명시(G-6의 원안) ② **경로가 서로 다르면 그 자체를 원인 후보로 승격** ③ 소수파 축의 경로에 대해 "그 경로에 데이터가 존재하는가"를 되물어 답을 리포트에 싣는다. 오늘 데이터로는 ③에서 *"A05608/1m/2026-08-14 부재 · A05609/1m/2026-08-14 410행 — 다수 축이 A05609를 본다"*가 자동으로 나왔어야 한다.

`tests/test_false_positive_axes.py`에 "축 모순이 있는데 `sources[]`가 비어 있으면 실패하는" 테스트. **선행 G-6.** 다음 단계.

### G-9. 일간 표본이 구조적으로 부족한 축은 다일 누적으로 판정 — 근거: 1-3 · V-19

30m는 하루 **15봉**이 물리적 상한이다(오늘 실측). 퇴화 판정 임계 30은 어떤 값으로 조정해도 일간으로는 못 넘는다 — **임계를 낮추면 오탐이 늘고, 두면 영원히 판정 불가다.** 오늘 그 결과가 "30m 퇴화 0건(14표본)"이라는, 가장 위험한 Horizon에 대한 가장 안심되는 문장이었다.

답은 축을 하루에서 **N거래일 롤링**으로 옮기는 것이다. `FeatureHealth`를 일간 산출로 두되 `logs/feature_health_rolling.json`에 누적하고, **직전 3거래일 합산 표본 ≥ 30**이면 판정한다. 30m는 3일이면 45봉이라 성립한다. 롤 경계에서 심볼이 바뀌면 F-1의 선행 심볼 이어읽기와 **같은 화이트리스트 규율**을 적용한다(수익률·변동성 계열만).

이 발상은 오늘 이미 리포에 존재한다 — `fix_verification`의 "N거래일 연속 기준 충족"이 정확히 같은 구조다. **판정 축을 하루에 가두지 않는 패턴을 퇴화 검사에도 적용하는 것**이지 새 개념이 아니다. **선행 F-C.** 다음 단계.

### G-10. 롤 당일을 시스템의 1급 개념으로 — 근거: 1-1 · 1-4 · 장전 F-1/F-2

오늘 하루에 롤 관련 결함이 **서로 독립인 두 곳**에서 터졌다: 아카이브 조회(장전 F-1)와 장후 배치 진입점(F-A). 둘 다 "심볼이 시계열의 이름인 줄 알았다"는 같은 착오인데, **한 곳을 고쳐도 다른 곳은 안 고쳐진다.** 4주에 한 번 오는 날이라 다음 롤(2026-09-14 근방)까지 세 번째·네 번째 지점이 남아 있는지 알 방법이 없다.

`ev_rollover_win` 피처가 이미 존재한다(오늘 값 0.0, `allowed_constant_values`에 등장). **이 값을 피처에만 쓰지 말고 운영 축으로 승격**한다: ① `configs`에 롤 캘린더를 정본화 ② 기동 자가점검에 `rollover` 줄 추가(장전 F-2와 동일 — 통합) ③ **롤 당일에는 `tests/test_rollover_day.py`가 강제로 도는 CI 게이트** — 심볼을 인자로 받는 모든 진입점에 대해 "롤 당일 해석이 새 심볼을 내는가"를 전수 검사한다.

세 번째 지점을 사람이 찾지 말고 **테스트가 찾게 한다.** 오늘 F-A를 쓰면서 `grep -rn 'default="A056' scripts/`를 돌려야 한다고 적은 것이, 그 검사가 자동화돼야 한다는 뜻이다. **선행 F-A · F-1.** 이번 주 · 다음 롤(9월) 전 필착.

---

## 4. 재시동 권고

**결론: 재시동 불필요. 코드 변경 후에도 재시동하지 않는다.**

| 판단 재료 | 값 | 함의 |
|---|---|---|
| `status_snapshot.code_version.stale` | **false** | 커밋과 실행 코드가 일치 — 재시동으로 얻을 새 코드가 없다 |
| `process_git_sha` / `head_git_sha` | `e37d387` / `e37d387` | 4컴포넌트 전부 동일 |
| `session_git_shas` | `["e37d387"]` 단일 | 하루 종일 한 코드로 돌았다 — 오늘 로그는 어느 코드의 결과인지 말할 수 있다 |
| 당일 커밋 | **0건** | 프로세스가 옛 코드로 도는 상태 자체가 없다 |
| 프로세스 상태 | `l1_daily` 15:36:29 · `g2_daily` 15:35:00 **정상 종료** | **이미 내려가 있다 — 재시동할 프로세스가 없다** |

**손익 비교**:
- **재시동으로 얻는 것: 없음.** `stale=false`이므로 적용을 기다리는 새 코드가 0이고, 두 프로세스는 15:35~15:36에 정상 종료돼 지금 살아 있지 않다. 장 마감 후 재시동은 시장 데이터 없이 프로세스만 띄우는 것이라 관측 가치도 없다.
- **재시동 없이 얻는 것: 오늘 관측의 연속성.** 오늘 로그는 단일 sha로 봉인된 완결된 하루다. 여기에 저녁 커밋 후의 기동 로그가 섞이면 **월요일 아침 자가점검의 `postmarket 20260814 장후 배치 정상 종료 확인` 판정이 흐려진다.**

**오늘 저녁 커밋 이후에도 재시동하지 않는다.** 월요일 08:20 `Messiah` / 08:25 `Messiah-G2` 정시 트리거가 자동으로 새 코드를 태운다(`schedule_drift=정본 일치` 확인됨, 3회 자가점검 전부). **단 하나의 예외: F-A 적용 후 `run_postmarket.py --date 2026-08-14 --symbol A05609` 재실행** — 이것은 재시동이 아니라 **오늘 못 돈 배치를 마저 도는 것**이고, `vol_scorecard` 산출과 12:51 버킷 유실 2건 치유가 걸려 있으므로 **오늘 저녁 안에 반드시 한다.**

---

## 5. 자체 검증

- [x] 장후 배치 완료 여부를 확인한 뒤 산출물을 판정했다 — `=== 장후 절차 요약 ===` 5/5 완주, `SessionEnd` 15:46:29 확인 후 `vol_scorecard` 누락을 판정
- [x] 오늘 장전·장중 보고서의 「확인 필요」에 결론을 냈다 — 장전 *"1-2의 원인"* → 롤 확정(통과 항목) · 장전 **W-9** → 판정 불가, 08-17 재이월(사유 명시) · 12:30 *"WinError 5 상대"* → 미특정 유지 · 12:30 *"n_experts=0"* → 미확정 유지 · **V-11/V-13/V-18/V-19** → 전부 결론(1-3·1-4)
- [x] 모든 이상점에 로그 시각과 인용이 붙었다
- [x] 각 이상점이 SYSTEM.md 조항에 대응된다 — 1-1 계명 9·R4·R10 / 1-2 L18·R6 / 1-3 L18·R6·계명 6 / 1-4 마스터플랜 판단사슬 ②·R18 / 1-5 R10
- [x] dev_memory 기존 항목을 중복 보고하지 않았다 — 1-4·1-5·1-7은 **기존**으로 명시하고 카운터만 갱신, g2 30분 공백·미커밋 179건·`LaunchWindowRefused`는 오탐 절로 격리
- [x] `FixVerificationRecurred`(12건 전수 실사) · `code_version.stale`(false, §4) · 산출물 누락(`vol_scorecard` → 1-1)을 빠뜨리지 않았다
- [x] Fix 계획이 파일·함수·행 수준까지 구체적이다
- [x] 고도화 4종이 전부 당일 관측에서 출발한다
- [x] 재시동 권고를 손익 비교와 함께 냈다
- [x] dev_memory 갱신 완료 — `DECISION_LOG.md` append · `NEXT_TODO.md` 체크박스 추가
