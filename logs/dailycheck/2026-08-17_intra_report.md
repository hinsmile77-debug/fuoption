# MESSIAH 일일 점검 — 2026-08-17 / 장중

> **관측 구간: 2026-08-17 09:00 ~ 16:22 KST (실행 시각 기준).**
> 스케줄상 국면은 `intra`(장중)이나 **실제 실행이 16:22에 이뤄져 정규장 마감(15:35)을 이미 지났다.**
> 이 어긋남 자체를 §1-P2에 이상점으로 올린다. 아래 판정은 16:22까지 파일에 남은 것만을 근거로 한다.

> **★ 2026-08-17은 KRX 휴장일이다** — `configs/krx_holidays.yaml:53` *"2026-08-17 광복절 대체휴일(8/15가 토요일)"*.
> `dev_memory/DECISION_LOG.md:5981` 에 *"★ 정정 — 2026-08-17은 휴장이다. 다음 거래일은 08-18(화)"* 로 이미 확정돼 있고,
> `NEXT_TODO.md:4671` *"2026-08-17(월·휴장)에 남은 것 — 없음. 월요일은 **아무것도 하지 않는 것이 계획이다**"*.
> **따라서 수집·판단·주문의 부재는 결함이 아니라 설계대로다.** 오늘의 점검 대상은 "거래가 잘 됐는가"가 아니라
> **"거래하지 않는 날을 시스템이 올바르게 표현했는가"** 다.

- 점검 시각: 16:22 KST
- 대상 국면: `intra`
- HEAD `f3ea02e` · 실행 프로세스 sha `f3ea02e` (**stale 아님**) · 미커밋 179건(전부 CRLF 개행 잡음 82파일 + 문서/설정, `src/`+`scripts/` 실제 변경 **0파일**)
- 증거: `logs/dailycheck/evidence_20260817_intra.md`
- 직전 보고서: `logs/dailycheck/2026-08-14_post_report.md` (08-14는 마지막 거래일)

---

## 0. 한 줄 결론

**⚠ P0 없음.** 휴장일 조기 종료 경로는 세 프로세스 전부 설계대로 작동했으나, **그 "정상"을 로그가 정상으로 말하지 못한다** — L1·G2는 종료 기록(`SessionEnd`)을 남기지 않았고 장후 배치는 정상 상황에 `ERROR`를 냈다. 오늘 자동 적신호 5건 중 **4건이 이 표현 결함이 만든 위양성**이다.

| 국면 | 판정 | 근거 요약 |
|---|---|---|
| 장전 | **조건부 정상** | 자가점검 2회 전항목 `[OK ]` · `self-check: PASS` · `git clean` · `schedule_drift 정본 일치`. 다만 휴장일에도 Docker 기동(21초)과 self_check 전량이 먼저 돌았다(§1-2-1) |
| 장중 | **정상 (휴장 — 판단 대상 없음)** | L1 08:20:29 · G2 08:25:27 에 각각 `KRX 휴장일 — 즉시 종료`. 09:00~15:35 무기록은 **결함 아님**. 파이프라인 지표(`status_snapshot`)는 오늘 갱신될 대상이 아니다 → **판단 불가**, 결함 아님 |
| 장후 | **관측 범위 밖 (부분)** | 15:45 `run_postmarket` 이 6단계 중 0단계 실행 후 중단 — 휴장일이므로 결과는 맞으나 **경로가 ERROR**다(§1-1-1). 장후 국면 전체 판정은 `post` 점검에서 한다 |

**아직 일어날 차례가 아닌 것 — 결함으로 세지 않음**: `daily_integrity_20260817.json` · `self_eval_20260817.json` · `vol_scorecard_20260817.json` · `g2_daily_returns.jsonl` 당일 행 · `FixVerificationRecurred` 판정 · W-16/W-26/W-21 라이브 채점. **전부 08-18(화) D-day에 나온다.**

---

## 1. 이상점 정리보고

### P0 — 오늘 거래에 직접 위험

**해당 없음.** 휴장일이라 포지션·주문·리스크 한도 노출이 0이다. `logs/l1_daily_20260817.log`·`logs/g2_daily_20260817.log` 어디에도 `OrderSubmitted`/`UnmatchedFill`/`CircuitBreaker` 계열 태그가 없고, 두 프로세스는 각각 4행(JSON)만 남기고 종료했다. **사람이 지금 개입해야 할 사안 없음.**

---

### P1 — 정확성·관측 훼손

#### 1-1-1. 휴장일 조기 종료가 `SessionEnd`를 남기지 않는다 — "정상 종료"와 "죽어서 사라짐"이 구분되지 않는다

- **증상**: L1·G2 두 프로세스 모두 휴장 판정 후 종료했으나 **구조화 종료 기록이 없다.** 마지막 기록은 비-JSON plain text 한 줄이고, `SessionEnd` 태그는 발행되지 않았다. 반면 같은 날 `run_postmarket`은 같은 상황에서 `SessionEnd`를 정상 발행했다 — **저장소 안에서 옳은 형태를 이미 알고 있는데 두 곳만 안 한다.**
- **근거**:
  ```
  08:20:29 {"tag": "SessionStart", "git_sha": "f3ea02e", "pid": 5372}
  08:20:29 {"tag": "CrashForensicsArmed", "process": "l1_daily", "armed": true}
  2026-08-17은 KRX 휴장일(Event Calendar) — 수집 생략, 즉시 종료      ← 파일 마지막 줄, JSON 아님
  ```
  `logs/l1_daily_20260817.log` (37행) · 동일 형태가 `logs/g2_daily_20260817.log` 08:25:27 (*"KRX 휴장일 — G2 운영 생략, 즉시 종료"*)
  대조군 — `logs/postmarket_20260817.log`:
  ```
  15:45:03 {"tag": "SessionEnd", "msg": "중단", "process": "postmarket", "steps_planned": 6, "steps_run": 0}
  ```
  코드 위치: `scripts/run_l1_daily.py:1041-1046` 의 `return` 이 `SessionEnd` 발행 지점(`:1237`)을 우회한다. `scripts/run_g2_paper_trading.py:526-528` → `:629` 동일 구조.
- **기준 위반**:
  - **SYSTEM.md §8 금지계명 14 — "자기검증 없는 종료 시퀀스 금지"**. 종료했다는 사실 자체가 기록되지 않았다.
  - `scripts/run_l1_daily.py:1233-1237` **자기 주석이 이 결함을 정확히 예언한다**: *"이 한 줄이 없으면 리포트가 「정상 종료」와 「죽어서 사라짐」을 구분할 근거가 없다 — 2026-08-07에 그 한계 때문에 1시간 54분 유실이 `관측 공백: 없음 ✅`으로 지나갔다."* 그 한 줄을 휴장 경로에서만 빠뜨렸다.
  - SYSTEM.md R6(태그 규율) — 종료 사유가 태그가 아니라 자유 텍스트로 표현됐다.
- **영향**: 관측기가 정상을 이상으로 읽는다. 오늘 `collect_evidence.py` §2 표가 두 프로세스에 **`SessionEnd 없음 ⚠`** 을 찍었고, §9 자동 적신호 **#2·#3** 이 *"SessionStart 2회 — 중복 기동/재기동 확인 필요"* 를 올렸다(실제로는 부팅 트리거 07:22 + 정시 트리거 08:20/08:25 의 정상 이중 발화). 휴장일마다 매번 재생산되는 위양성이다. **더 나쁜 방향**: 거래일에 프로세스가 진짜로 조용히 죽어도 로그 모양이 오늘과 같아서 — 마지막이 비-JSON 한 줄, `SessionEnd` 없음 — 사람이 "아 휴장일 패턴이네"로 흘려보낼 소지가 생겼다. 오경보는 진짜 경보의 민감도를 깎는다.
- **신규 여부**: **신규.** `dev_memory` 전문 검색상 휴장일 `SessionEnd` 누락을 다룬 항목 없음. `EventCalendar` 휴장 인식은 2026-07-27 도입(`DECISION_LOG.md:628-631`), `SessionEnd`는 2026-08-07 P0-3 도입 — **뒤에 들어온 규율이 앞에 있던 조기 종료 경로에 소급되지 않은 형태다.**

#### 1-1-2. 장후 배치가 휴장일에 `ERROR` + exit 3 을 낸다 — 메시지 스스로 "정상이다"라고 말하면서

- **증상**: `run_postmarket`이 15:45:03 에 `SymbolResolutionMismatch`(**ERROR**)를 발행하고 exit 3(`_SYMBOL_MISMATCH_EXIT_CODE`)으로 중단했다. 오늘 전 프로세스를 통틀어 **유일한 ERROR**다. 그런데 같은 코드가 출력한 사람용 안내문이 *"휴장일이면 정상이다"* 라고 적혀 있다. **정상 상황을 ERROR로 부르고 있다.**
- **근거**:
  ```
  15:45:03 [ERROR] SymbolResolutionMismatch
    2026-08-17 A05609의 1분봉이 아카이브에 없다 — 중단 (그날 데이터를 가진 심볼 없음)
    extra={"date":"2026-08-17","symbol":"A05609","origin":"만기 규칙 계산","symbols_holding_day":[]}

    ** 조회 대상 불일치 ** — A05609/1m/2026-08-17 부재. 그날 데이터를 가진 심볼 없음.
       휴장일이면 정상이다. 아니라면 --symbol로 명시하거나 해석 규칙을 확인할 것.
  ```
  `logs/postmarket_20260817.log` · 1건 · 15:45:03 유일
  코드 위치: `scripts/run_postmarket.py:428-444` (`_has_day()` 실패 분기). `run_postmarket.py` 전체에 **`EventCalendar.is_trading_day()` 호출이 없다** (grep 확인 — `is_trading_day` 는 `run_l1_daily.py:1041`·`run_g2_paper_trading.py:526`에만 존재).
- **기준 위반**:
  - **SYSTEM.md R6 — "태그 1개 = 심각도 1개"**. `SymbolResolutionMismatch` 하나가 (a)만기된 심볼 오조회라는 진짜 결함과 (b)휴장일이라 데이터가 없는 정상을 겸한다. `references/phases.md §D` *"같은 태그가 INFO와 ERROR를 겸하면 규칙 위반"* 의 변형이다.
  - **정본 하나 원칙(NEXT_TODO G-7 계열)** — 휴장 판정의 정본은 `src/messiah/core/event_calendar.py`의 `is_trading_day()`인데 장후 배치만 그것을 안 쓰고 "아카이브에 파일이 있느냐"로 대리 판정한다.
- **영향**: ① ERROR 집계 기반 경보·채점에 휴장일마다 고정 1건의 노이즈가 들어간다. 오늘 다이제스트 §9 자동 적신호 **#4**(*"postmarket: ERROR 이상 1건"*)가 그것이다. ② 스케줄러 `LastTaskResult`에 exit 3(실패)이 남는다 — **2026-08-10에 "조용한 exit 0"으로 오전을 통째로 잃은 뒤 종료 코드를 갈라놨는데**(`run_l1_daily.py:1057-1068`), 이번엔 반대 방향으로 정상일에 실패 코드가 남는 형태다. 종료 코드의 신뢰가 양방향으로 깎인다.
- **신규 여부**: **신규.** 원인 코드는 2026-08-14 **F-A**(`NEXT_TODO.md:4410` `[x] 장후 배치 심볼 자동 해석 + 오조회 가드(SymbolResolutionMismatch, exit 3)`)로 **의도대로 들어간 것**이고, 오늘이 그 가드가 만난 **첫 휴장일**이다. 재발이 아니라 **커버되지 않은 분기**다.

---

### P2 — 운영 부담·기술부채

#### 1-2-1. 휴장 판정이 Docker 기동·self_check **뒤**에 있다 — docstring이 코드와 반대를 말한다

- **증상**: `run_l1_daily.py` docstring이 *"휴장일이면 self_check조차 실행하지 않고(불필요한 KIS API 호출 회피) 즉시 종료한다"* 고 선언하지만, 실제 실행 순서는 **Docker Desktop 자동 기동 → self_check 전량 → SessionStart → 그제서야 휴장 판정**이다. 오늘 이 순서가 **2회** 반복됐다.
- **근거**:
  ```
  [run_l1_daily] Docker Desktop 자동 기동 완료 (21초 대기)     ← 파일 첫 줄
  [OK ] config ... [OK ] redis  redis://localhost:6380/0        ← self_check 14항목 전량
  self-check: PASS — 기동 허용
  {"ts":"2026-08-17T07:22:56", "tag":"SessionStart", "pid":7660}
  2026-08-17은 KRX 휴장일(Event Calendar) — 수집 생략, 즉시 종료  ← 여기서 처음 판정
  ```
  `logs/l1_daily_20260817.log` 1~19행 · 07:22:56 및 08:20:29 **동일 순서 2회**
  코드: 진입점 `scripts/run_l1_daily.py:1247-1251`
  ```python
  if __name__ == "__main__":
      args = _parse_args()
      _ensure_docker_ready()      # ← 1249
      _run_self_check(args.configs)  # ← 1250
      asyncio.run(main(instance_cfg))  # ← main():1041 에서 휴장 판정
  ```
  docstring: `scripts/run_l1_daily.py:28-30`
- **기준 위반**: **설계 문구(docstring)와 구현의 직접 모순.** SYSTEM.md R5 계열의 문서 규율 — 이 저장소는 docstring을 정본으로 취급해 왔고(`front_month_days` docstring이 달력 함정을 두 번 잡은 전례), 그 신뢰가 여기서 깨진다. 부수적으로 SYSTEM.md 불변원칙 6(기동 자가점검)의 취지 — *"거래할 자격을 묻는다"* — 가 거래하지 않는 날에도 소모된다.
- **영향**: 휴장일마다 Docker Desktop 기동 21초 + self_check 서브프로세스 2회 + Redis 컨테이너 상승. **실동작 피해는 없다**(오늘 dev 모드라 secrets/bundle/registry 점검은 생략됐고 KIS API 호출도 없었다). 진짜 비용은 **문서 신뢰**다 — 다음 사람이 docstring을 읽고 "휴장일엔 아무것도 안 돈다"를 전제로 뭔가를 얹으면 틀린다.
- **신규 여부**: **신규.** `DECISION_LOG.md:628-631`이 2026-07-27 도입 시점에 *"휴장일이면 self_check조차 안 하고 즉시 종료"* 로 기록했으므로 **원래 의도는 docstring 쪽**이었고, 이후 `_ensure_docker_ready`(2026-07-29 추가, docstring:22-26)와 self_check가 진입점으로 올라오면서 순서가 뒤집힌 것으로 보인다. 즉 **의도가 조용히 무효화된 형태**다.

#### 1-2-2. `collect_evidence.py`가 휴장일을 모른다 — 오늘 자동 적신호 5건 중 4건이 위양성

- **증상**: 증거 수집기가 `configs/krx_holidays.yaml`을 읽지 않아, 휴장일에 당연히 없어야 할 것들을 전부 적신호로 올렸다.
- **근거**: `logs/dailycheck/evidence_20260817_intra.md` §9
  ```
  1. status_snapshot 이 오늘 것이 아니다 (generated_at=2026-08-14T15:34:45)  ← 휴장이라 당연
  2. l1_daily: SessionStart 2회 — 중복 기동/재기동 확인 필요                  ← 부팅+정시 정상 이중
  3. g2_daily: SessionStart 2회 — 중복 기동/재기동 확인 필요                  ← 동상
  4. postmarket: ERROR 이상 1건                                              ← 1-1-2 그 자체
  5. 산출물 누락(pre): logs/ui_20260817.log                                   ← 설계상 정상
  ```
  #5 반증: `dev_memory/DECISION_LOG.md:1007` — *"`_launch_ui()` 신규 — **거래일 확인 직후(휴장일에는 기동 안 함)**"*. UI 로그 부재는 **설계대로**다.
  §7 산출물 표도 `logs/ui_20260817.log` **없음 ⚠** · `status_snapshot.json` **있음 ⚠오래됨** 으로 표시했다.
- **기준 위반**: `references/phases.md §D` *"건수 0은 두 가지다 — 진짜 없었거나, 계측이 없거나"* 의 3번째 경우: **"오늘은 있을 차례가 아니다"** 를 도구가 표현하지 못한다.
- **영향**: 휴장일 점검마다 사람이 같은 4건을 손으로 기각해야 한다. 연간 KRX 휴장일 15~18일 × 3국면 = 45~54회. 더 중요한 건 **적신호 목록의 신호 대 잡음비**가 휴장일에 20%로 떨어진다는 점이다.
- **신규 여부**: **신규.** `NEXT_TODO`의 F-12(보고서 파일명 규칙, 2026-08-16 종결)와 같은 계열 — 점검 스킬 자체의 부채. **F-13**으로 신규 등록한다.

#### 1-2-3. 장중 점검이 16:22에 실행됐다 — 관측 창이 닫힌 뒤다

- **증상**: 국면 인자가 `intra`인 스케줄 작업이 정규장 마감(15:35)과 장후 배치(15:45)를 모두 지난 **16:22**에 실행됐다. 같은 분(16:22)에 `evidence_20260817_pre.md`·`evidence_20260817_post.md`도 함께 생성됐다 — 세 국면 점검이 한꺼번에 뒤늦게 몰려 돈 것으로 보인다.
- **근거**:
  ```
  -rwx--- 33138  Aug 17 16:22  logs/dailycheck/evidence_20260817_pre.md
  -rwx--- 33711  Aug 17 16:22  logs/dailycheck/evidence_20260817_intra.md
  -rwx--- ~      Aug 17 16:22  logs/dailycheck/evidence_20260817_post.md
  ```
  대조 — 정상 운영일의 실행 시각: `evidence_20260814_pre.md` 08:50 · `evidence_20260814_intra.md` 12:36 · `evidence_20260814_post.md` 15:58.
  또한 `logs/dailycheck/`에 **2026-08-17 보고서가 하나도 없었다**(본 파일이 최초) — 08-13·08-14는 pre/intra/post 3종이 다 있다.
- **기준 위반**: 스킬 `SKILL.md §0` 국면 정의 — 장중은 `09:00~15:35`. 그 밖에서 실행된 장중 점검은 **국면이 묻는 질문("지금 설계대로 돌고 있는가")에 답할 수 없다.**
- **영향**: 오늘은 휴장이라 실손해 0. **거래일이었다면 장중 점검의 유일한 존재 이유 — 그날 안에 사람이 개입할 시간을 주는 것 — 이 사라진다.** 08-18(D-day)에 같은 일이 반복되면 리허설 예보(W-16·W-26·W-21) 대비 라이브 이탈을 장중에 못 잡는다.
- **신규 여부**: **신규.** 리포 코드가 아니라 **점검 스케줄러(Cowork 예약 작업) 쪽** 문제일 가능성이 크다. 아래 §1-확인필요 참조.

---

### 확인 필요 (확정 아님)

- **점검 스케줄 작업의 트리거 시각이 실제로 어긋났는가, 아니면 오늘만 지연됐는가** (1-2-3 관련). 리포 안에는 이 스케줄의 정본이 없다 — `scripts/install_scheduled_tasks.ps1`이 관리하는 4종(`Messiah`·`Messiah-G2`·`Messiah-Shutdown`·`Messiah-Postmarket`)에 dailycheck는 포함되지 않는다. **무엇을 보면 판정되는가**: Cowork 예약 작업 목록의 `messiah-premarket/intraday/postmarket-check` 트리거 시각과 마지막 실행 시각. **08-18(화) 장전에 반드시 확인할 것** — D-day다.
- **`status_snapshot.json`의 3일 묵은 값을 소비자가 어떻게 읽는가.** 현재 파일은 08-14 15:34 기준이고 `"command_center_ui": "UP"`·전 컴포넌트 `OK`로 남아 있다. `code_version.stale=false`이지만 그건 **08-14 시점 `e37d387` 기준**이며 오늘 HEAD는 `f3ea02e`다. 휴장일에 갱신 안 되는 것은 정상이나, **UI가 이 값을 "지금 상태"로 그리면 3일 전을 현재로 표시한다.** 다만 신선도 임계는 커밋 `0b80580`(*"화면이 어제 계약을 오늘이라 불렀다 — UI 심볼 조회 + 신선도 임계 유도"*)에서 이미 다뤄진 영역이라 **중복 보고를 피해 판정을 보류한다**. **무엇을 보면 판정되는가**: `src/messiah/ui/app.py`의 스냅샷 신선도 임계가 "거래일 기준"인지 "경과 시간 기준"인지 — 후자면 연휴 3일 뒤 화면이 조용히 낡는다.
- **작업트리 미커밋 179건.** dev 모드이므로 **금지계명 10(미커밋 수정 실전 반입 금지) 위반이 아니다.** self_check도 `[OK ] git clean`을 냈다(추적 파일의 실질 변경이 없다는 뜻). `src/`+`scripts/` 실제 변경 0파일 · CRLF 개행 잡음 82파일. **다만 D-day 아침에 `git status`가 179건을 뿜는 상태는 "동결 확인"을 눈으로 하기 어렵게 만든다.** 판정 보류 — 08-18 장전 점검에서 `git diff --stat -w`(공백 무시)로 재확인 권고.

---

## 2. Fix 작업 구현계획

> **⚠ 본 계획은 수립만 하고 오늘 적용하지 않는다** — SYSTEM.md R11 · 금지계명 3(장중 학습 금지)·4(장중 배포 금지).
> 더 강한 이유가 하나 더 있다: `DECISION_LOG.md:409` **"여기서부터 D-day 아침까지 코드를 넣지 않는다"**(2026-08-16 동결 선언, 커밋 `cc93366`→`f3ea02e`).
> **모든 항목의 적용 시점은 2026-08-18(화) D-day 장후 15:35 이후다.** 아래 F-1~F-4 중 D-day를 막는 것은 **하나도 없다.**

### F-1. 휴장일 조기 종료에 `SessionEnd` 발행 — P1 · 대응 이상점 1-1-1

- **원인 가설**: `SessionEnd`(2026-08-07 P0-3)가 정상 경로 말미에만 심어졌고, 그보다 먼저 존재하던 휴장 조기 종료 경로(2026-07-27)에 소급되지 않았다. `LaunchWindowRefused` 경로는 같은 함정을 이미 인지해 `mlog.log`를 넣어 뒀다(`run_l1_daily.py:1055`) — **휴장 경로만 빠졌다.**
- **변경 파일**:
  - `scripts/run_l1_daily.py` — `main()` :1041-1046. `print` 뒤·`return` 앞에
    ```python
    mlog.log("SessionEnd", "휴장일 — 수집 생략",
             process="l1_daily", date=today.isoformat(), reason="krx_holiday")
    ```
    를 추가. **`reason` 필드로 정상 종료와 구분 가능하게 한다** — `SessionEnd` 태그 하나에 사유를 실어 R6(태그 1개=심각도 1개)를 지킨다.
  - `scripts/run_g2_paper_trading.py` — `main()` :526-528. 동일, `process="g2_paper"`.
  - `.claude/skills/messiah-daily-check/scripts/collect_evidence.py` — 세션 경계 표에서 `SessionEnd.reason == "krx_holiday"` 면 `없음 ⚠` 대신 `휴장 종료` 로 표기.
- **회귀 위험**: 낮음. `mlog.log` 한 줄 추가이고 반환 흐름은 그대로다. 유일한 주의점 — `mlog.setup()`이 이미 호출된 뒤여야 한다(`:1027`에서 호출됨, 조건 충족).
- **검증 방법**:
  - `pytest tests/ -k "session_end or holiday"` (해당 테스트가 없으면 신규: `test_holiday_exit_emits_session_end`).
  - **재생 검증**: 시스템 날짜를 고정하지 말고 `EventCalendar`를 주입 가능하게 두었는지 확인 후, 휴장일 date를 넣어 `main()` 조기 반환 경로를 태우고 `SessionEnd` 1건 발행을 단언.
  - **다음 휴장일 관측**: 2026-09-25~26(추석 예상, `configs/krx_holidays.yaml` 확인 필요) 또는 그 이전 휴장일에 `collect_evidence` §9 적신호에서 `SessionStart 2회` 위양성이 **사라지는가.**
- **적용 시점**: **2026-08-18(화) 장후 15:35 이후.** 이유 — 오늘은 휴장 동결일이고, 내일은 D-day라 장전 코드 투입이 금지된다.
- **결정 필요 사항**: `SessionEnd`에 `reason` 필드를 추가할지, 별도 태그(`SessionSkippedHoliday`)를 신설할지. **권고: `SessionEnd` + `reason`.** 새 태그를 만들면 "종료했다"를 세는 모든 소비처가 둘 다 알아야 한다 — 이 저장소가 반복해 당한 형태다(`WarmStartBarsDropped` write-only, NEXT_TODO G-A).

### F-2. 장후 배치에 휴장일 가드 — P1 · 대응 이상점 1-1-2

- **원인 가설**: F-A(2026-08-14) 가드가 "아카이브에 그날 1분봉이 있는가"로 조회 대상 정합을 판정한다. 이 대리 판정은 **오조회**와 **휴장**을 구분하지 못한다. 정본(`EventCalendar.is_trading_day()`)이 있는데 이 스크립트만 안 쓴다.
- **변경 파일**:
  - `scripts/run_postmarket.py` — `_has_day()` 분기(:428) **앞**에 휴장 가드를 삽입:
    ```python
    if not EventCalendar.from_file().is_trading_day(day):
        mlog.log("PostmarketSkippedHoliday",
                 f"{day.isoformat()}은 KRX 휴장일 — 장후 배치 생략",
                 date=day.isoformat(), level="INFO")
        mlog.log("SessionEnd", "휴장일 — 배치 생략",
                 process="postmarket", date=day.isoformat(),
                 reason="krx_holiday", steps_planned=len(planned_steps), steps_run=0)
        return 0        # ← exit 0. 실패가 아니다
    ```
    import 추가: `from messiah.core.event_calendar import EventCalendar`
  - **`_SYMBOL_MISMATCH_EXIT_CODE = 3`(:121)은 그대로 둔다** — 거래일의 진짜 오조회는 여전히 실패여야 한다. 가드를 앞에 세우는 것만으로 휴장 오경보가 사라진다.
- **회귀 위험**: **중간.** 휴장 판정이 틀리면(달력 미갱신) 거래일 배치를 통째로 건너뛴다 — 오늘 발견한 것보다 훨씬 나쁜 실패 모드다. 완화책: `EventCalendar.is_trading_day()`는 해당 연도 데이터가 없으면 이미 예외를 던진다(`event_calendar.py:167` *"{d.year}년 KRX 휴장일 데이터 없음 — configs/krx_holidays.yaml 갱신 필요"*). **그 예외를 삼키지 말 것.** 조용히 True/False로 폴백하면 금지계명 12(조용한 폴백 금지) 위반이 된다.
- **검증 방법**:
  - `pytest tests/ -k postmarket` 전량 + 신규 `test_postmarket_skips_holiday_with_exit_zero`.
  - **거래일 회귀 확인이 더 중요하다**: 2026-08-18 장후 배치가 6단계 전부 실행되는지(`steps_run == 6`) — 가드가 거래일을 잡아먹지 않았다는 증거.
  - 관측 태그: `PostmarketSkippedHoliday` 0건(거래일) / 1건(휴장일).
- **적용 시점**: **2026-08-18(화) 장후 배치 완주 확인 **후**.** 순서가 중요하다 — D-day 장후 배치는 W-16·W-26·W-21 라이브 채점의 원천이다. **그 배치가 끝나기 전에 배치 코드를 건드리지 않는다.** 2026-08-14에 장후 리포트가 오염돼 fix 채점 전체를 오판시킨 전례가 근거다(NEXT_TODO G-A 항목의 같은 논리).
- **결정 필요 사항**: 휴장일 exit code를 0으로 할지 별도 코드(예: 4)로 할지. **권고: 0.** 스케줄러의 `LastTaskResult`는 "사람이 봐야 하는가"를 뜻해야 하고, 휴장은 볼 필요가 없다. 대신 `PostmarketSkippedHoliday` 태그가 "돌긴 돌았다"의 증거로 남는다.

### F-3. 휴장 판정을 진입점 최상단으로 이동 (또는 docstring 정정) — P2 · 대응 이상점 1-2-1

- **원인 가설**: `_ensure_docker_ready()`(2026-07-29)와 `_run_self_check()`가 나중에 `if __name__` 블록으로 올라오면서, `main()` 안에 있던 휴장 판정보다 앞서게 됐다. 아무도 순서를 다시 읽지 않았다.
- **변경 파일** (두 안 중 택일):
  - **안 A (권고)** — `scripts/run_l1_daily.py:1247-1251` 진입점에 조기 반환:
    ```python
    if __name__ == "__main__":
        args = _parse_args()
        today = now_kst().date()
        if not EventCalendar.from_file().is_trading_day(today):
            mlog.setup(load_instance(args.configs).instance_id)
            mlog.log("SessionEnd", "휴장일 — 수집 생략",
                     process="l1_daily", reason="krx_holiday")
            print(f"{today.isoformat()}은 KRX 휴장일 — 즉시 종료", flush=True)
            raise SystemExit(0)
        _ensure_docker_ready()
        ...
    ```
    `main()`:1041의 기존 가드는 **남겨 둔다** — 이중 방어이고, `main()`을 직접 부르는 테스트가 있을 수 있다.
    `scripts/run_g2_paper_trading.py` 동일 적용. **이 안을 택하면 F-1이 여기에 흡수된다.**
  - **안 B (최소)** — 코드는 두고 `run_l1_daily.py:28-30` docstring을 사실대로 정정: *"휴장일 판정은 `main()` 진입 직후에 한다 — Docker 기동과 self_check는 그보다 먼저 돈다."*
- **회귀 위험**: **안 A가 더 크다.** `mlog.setup()`을 진입점에서 부르면 로거 초기화 순서가 바뀐다. 특히 `crash_forensics.enable()`이 `main()`에서 먼저 돌던 것과의 관계를 확인해야 한다 — **휴장일엔 크래시 포렌식 무장이 불필요하므로 논리적으로는 문제없으나, `CrashForensicsArmed` 태그가 휴장일에 사라지는 변화**가 생긴다. 그 태그를 세는 소비처가 있는지 grep 필요.
- **검증 방법**: `pytest tests/ -k "l1_daily or entrypoint"` + 휴장일 재생 실행 시 Docker 기동 로그가 **안 찍히는지** 육안 확인.
- **적용 시점**: **2026-08-18 장후 이후.** F-1과 같은 커밋으로 묶는다(안 A 채택 시).
- **결정 필요 사항**: **안 A vs 안 B.** 권고는 **안 A + F-1 흡수** — docstring의 원래 의도(`DECISION_LOG.md:631`)가 안 A이고, 문서를 코드에 맞추는 것보다 코드를 원래 의도에 맞추는 쪽이 옳다. 다만 회귀 위험이 있으므로 **F-1(안전·즉효)을 먼저 넣고 F-3(안 A)은 그다음 거래일에 분리 적용**하는 순서를 권고한다.

### F-4. `collect_evidence.py` 휴장일 인식 (F-13) — P2 · 대응 이상점 1-2-2

- **원인 가설**: 수집기가 "오늘 있어야 할 것" 목록을 국면만으로 결정하고 **달력을 묻지 않는다.**
- **변경 파일**:
  - `.claude/skills/messiah-daily-check/scripts/collect_evidence.py`
    - 상단에 휴장 판정 추가 — `configs/krx_holidays.yaml`을 직접 파싱한다(스킬 스크립트가 `src/messiah` 임포트에 의존하지 않게. 리포 밖에서도 돌 수 있어야 한다).
    - **§0 머리말에 한 줄 삽입**: `> ★ 2026-08-17은 KRX 휴장일이다 — 이하 "없음/오래됨" 표기는 대부분 정상이다.`
    - **§7 산출물 표**: 휴장일이면 상태를 `없음 ⚠` → `해당 없음(휴장)` 으로.
    - **§9 자동 적신호**: 휴장일에는 `status_snapshot 오늘 것 아님`·`ui 로그 누락`·`SessionStart 2회`를 **적신호에서 빼고** 별도 절 *"휴장일이라 기각한 항목"* 으로 옮긴다. **지우지 않는다** — 기각했다는 사실이 보여야 한다(오늘 §1-2-2가 그 사례다).
- **회귀 위험**: 낮음(점검 도구, 런타임 영향 0). **유일한 위험은 반대 방향** — 휴장 판정이 틀려 거래일 적신호를 기각하는 것. 완화: 달력에 해당 연도가 없으면 **휴장 인식을 끄고 기존 동작 유지**(적신호 전량 표시) + 다이제스트에 *"달력 미갱신 — 휴장 필터 미적용"* 명기.
- **검증 방법**: 오늘(08-17) 로그로 재실행 → 적신호가 5건 → **1건**(`postmarket ERROR`, F-2 적용 전이므로 남아야 정상)으로 줄어드는지. 08-18 로그로 재실행 → 적신호 필터가 **하나도 걸리지 않는지**.
- **적용 시점**: **2026-08-18 장후 이후.** 런타임 무관이라 더 일찍도 가능하나, **관측기를 D-day 직전에 건드리지 않는다** — NEXT_TODO G-A가 `integrity_report.py`에 대해 세운 것과 같은 원칙이다.
- **결정 필요 사항**: 없음.

### 적용 순서와 커밋 계획

> 전제: **2026-08-18(화) D-day 장후 배치가 완주하고, 그 산출물로 W-16·W-26·W-21 채점이 끝난 뒤**에 착수한다.

1. **커밋 ①** — F-1 (휴장 `SessionEnd`, L1+G2 2파일) + F-4 (`collect_evidence` 휴장 인식)
   메시지 초안: `[MW0601] 쉬는 날을 로그가 쉬었다고 말하게 한다 — 휴장 SessionEnd + 점검기 휴장 인식`
   *(런타임 위험이 가장 낮고 효과가 즉시 확인되는 조합)*
2. **커밋 ②** — F-2 (장후 배치 휴장 가드)
   메시지 초안: `[MW0601] 장후 배치가 휴장을 오류라 부르지 않는다 — EventCalendar 정본 결선`
   *(D-day 장후 배치 완주를 확인한 **뒤에만** 넣는다)*
3. **커밋 ③** — F-3 안 A (휴장 판정 진입점 이동) *또는* 안 B (docstring 정정)
   메시지 초안: `[MW0601] docstring이 옳았고 코드가 밀렸다 — 휴장 판정을 진입점으로`
   *(커밋 ①이 다음 휴장일에 검증된 뒤 착수 권고. 급하지 않다)*

**커밋 전 필수**: `pytest`(해당 범위) + 재생 검증 — 금지계명 2. 커밋 메시지 첫 단어 `[MW0601]`.

---

## 3. 고도화 방안

### G-1. "오늘은 무엇이 있을 차례인가"를 달력이 정하게 한다 — 국면 기대치의 정본화

- **관측 근거**: 오늘 다이제스트 §7 산출물 표와 §9 적신호가 **휴장일에도 거래일과 똑같은 기대 목록**을 들이댔고, 5건 중 4건이 위양성이었다(§1-2-2). 동시에 `run_postmarket`은 정반대 문제 — **달력을 아예 안 물어서** 정상을 ERROR로 불렀다(§1-1-2). 하나의 원인의 두 얼굴이다: **"오늘 무엇이 있어야 하는가"의 정본이 없다.**
- **제안 내용**: `src/messiah/core/event_calendar.py`에 `expected_artifacts(day) -> set[str]` 계열을 두거나, 별도 `core/day_profile.py`를 신설해 **날짜 → 기대 산출물·기대 태그·기대 프로세스 집합**을 한 곳에서 답하게 한다. 소비처: `run_postmarket`(F-2), `collect_evidence`(F-4), `daily_integrity_report.py`, UI 신선도 판정. 각 소비처가 저마다 달력을 재구현하지 않는다.
- **기대 효과**: 휴장일 위양성 4건 → 0건(측정 가능). 장후 배치 휴장 오경보 1건 → 0건. **연휴 다음 거래일의 "전일 대비" 지표가 3일 전과 비교되는 문제**도 같은 함수로 풀린다(오늘 `status_snapshot`이 08-14 것인데 UI가 어떻게 읽는지 판정 보류한 항목 — §1-확인필요).
- **비용·위험**: 중간(2~3시간). 위험은 **정본이 틀렸을 때 전부가 함께 틀린다**는 것 — 지금은 소비처마다 달라서 하나가 틀려도 다른 하나가 드러낸다. 완화: 달력 미갱신 연도는 예외를 던져 **관측 가능한 방향으로 실패**시킨다(`event_calendar.py:167`이 이미 그렇다).
- **선행 조건**: F-2·F-4 적용. 그 둘이 각자 달력을 부른 뒤, 중복이 명확해졌을 때 합친다. **먼저 합치지 않는다** — 소비처가 둘뿐일 때 추상화하면 세 번째가 안 맞는다.
- **우선순위 제안**: **다음 단계 (G2 40거래일 중).** D-day를 막지 않고, 다음 휴장일까지 시간이 있다.

### G-2. 종료 사유를 태그가 아니라 필드로 — `SessionEnd.reason` 분류축 신설

- **관측 근거**: 오늘 `SessionEnd`가 세 프로세스에서 **세 가지 다른 의미**로 쓰이거나 안 쓰였다 — L1/G2는 **없음**(휴장 종료), postmarket은 **`"중단"`**(exit 3), 거래일 정상 경로는 **`"정상 종료"`**. 문자열 `msg`로만 구분되니 기계가 셀 수 없다. `collect_evidence`가 실제로 못 세서 `SessionEnd 없음 ⚠`와 위양성 적신호가 나왔다.
- **제안 내용**: `SessionEnd`에 필수 필드 `reason ∈ {normal, krx_holiday, launch_window_refused, symbol_mismatch, crash_recovered, forced_flat}` 추가. `src/messiah/core/logging.py`의 태그 주석에 허용값을 정본으로 박고, `collect_evidence.py` §2 표를 `reason`별로 집계. **`msg`는 사람용, `reason`은 기계용**으로 역할을 가른다.
- **기대 효과**: "종료했다"를 세는 모든 소비처가 종료 **사유별**로 셀 수 있다. 구체적 지표 — 다음 휴장일 다이제스트에서 `SessionEnd 없음 ⚠` 0건, 적신호 0건.
- **비용·위험**: 낮음(1시간). 위험: 기존 `SessionEnd` 소비처가 `reason` 부재를 어떻게 다루는가. 완화: `reason` 없으면 `unknown`으로 세고 **`unknown` 건수를 리포트에 노출**한다 — 조용히 0으로 세지 않는다(금지계명 12).
- **선행 조건**: F-1(휴장 `SessionEnd` 발행). F-1의 "결정 필요 사항"에서 `reason` 필드를 택하면 G-2가 그 자연스러운 확장이 된다.
- **우선순위 제안**: **이번 주** — F-1과 같은 커밋에 절반이 들어간다.

### G-3. 장중 점검의 실행 시각을 점검 자신이 채점하게 한다

- **관측 근거**: 오늘 장중 점검이 **16:22**에 돌아 관측 창(09:00~15:35) 밖이었다(§1-2-3). 그런데 **도구는 이것을 전혀 문제 삼지 않았다** — 다이제스트도, 스킬 절차도 "지금 몇 시에 돌고 있는가"를 묻지 않는다. 08-14는 12:36에 정상 실행됐으니 회귀다. **계측기가 자기 지각을 스스로 못 잡는 형태**로, `DECISION_LOG.md:370` 이 적어 둔 *"계측기가 자기 공백을 정상으로 읽는 형태(L18)"* 와 같은 계열이다.
- **제안 내용**: `collect_evidence.py`에 **국면-실행시각 정합 검사**를 넣는다. `--phase intra`인데 실행 시각이 15:35을 지났으면 다이제스트 §0 최상단에 경고를 찍고 §9 적신호 1번으로 올린다. `--phase pre`가 09:00 이후, `--phase post`가 15:45 이전인 경우도 동일. **국면 인자를 무조건 믿지 않는다.**
- **기대 효과**: 점검 파이프라인의 지각이 **그날 다이제스트 안에서** 드러난다. 오늘은 사람이 파일 타임스탬프를 대조해서야 발견했다.
- **비용·위험**: 낮음(30분, 스킬 스크립트 한정, 런타임 영향 0). 위험 없음 — 경고만 추가한다.
- **선행 조건**: 없음.
- **우선순위 제안**: **즉시(장후).** 08-18 D-day 장중 점검이 또 늦게 돌면 **그날 안에** 알아야 한다.

### 로드맵 반영 제안

| 항목 | 현 로드맵 위치 | 제안 위치 | 사유 |
|---|---|---|---|
| G-1 (날짜→기대치 정본) | 없음(신규) | G2 40거래일 중 · **G-7(정본 하나) 계열에 합류** | NEXT_TODO의 G-B(두 parquet 로더 시간대 통일)와 같은 병 — 정본이 여러 곳에 흩어진 것. 묶어서 다루는 편이 낫다 |
| G-2 (`SessionEnd.reason`) | 없음(신규) | **이번 주** · F-1과 동반 | 관측 축 신설 비용이 F-1에 이미 절반 포함 |
| G-3 (점검 실행시각 자기채점) | 없음(신규) | **즉시(장후)** | D-day 장중 관측을 지키는 유일한 안전장치 |
| F-13 (`collect_evidence` 휴장 인식) | 없음(신규) | NEXT_TODO 신규 등록 | F-12(보고서 파일명, 08-16 종결)와 같은 스킬 부채 계열 |

---

## 4. 다음 거래일 관측 예정

**2026-08-18(화)는 D-day다.** 오늘 세운 항목보다 **이미 예보된 항목의 채점**이 우선한다.

| ID | 관측 대상(태그/지표) | 판정 기준 | 판정 예정일 |
|---|---|---|---|
| **W-16** ★★ | `FeatureWarmStart.bars_by_horizon` · `WarmStartBarsDropped` | 4축 충족 + Dropped **0건**. 30m이 하한 22봉 초과 | 2026-08-18 장전~장중 |
| **W-26** ★ | 국면 분류 `RegimeClassified` | UNKNOWN 탈출. 리허설 예보 `TREND_DOWN`(0.999)와 라이브 일치 여부 | 2026-08-18 장중 |
| **W-21** ★ | `AggregatorNoContribution` 갈래 | `blocked_by_meta`로 나오는가(리허설 15건 전량 그랬다) | 2026-08-18 장중 |
| **meta 분포** | meta 통과확률 라이브 분포 | 리허설 최대 0.6576 vs 임계 0.7 — 라이브가 0.7을 넘는 사이클이 있는가 | 2026-08-18 장후 |
| — | **리허설 vs 라이브 이탈** | **갈리면 그 자체가 P0** (계획서 §3-3 예보) | 2026-08-18 장중 |
| **오늘-1** | `run_postmarket` `steps_run` | **6단계 완주**. F-2는 이 완주를 확인한 뒤에만 착수 | 2026-08-18 15:45~ |
| **오늘-2** | dailycheck 실행 시각 | pre≤09:00 · intra 12:00~13:00 · post≥15:45. **오늘 셋 다 16:22였다** | 2026-08-18 각 국면 |
| **오늘-3** | `git status` (`-w` 공백 무시) | `src/`+`scripts/` 실제 변경 **0파일** 유지 = 동결 확인 | 2026-08-18 장전 |
| F-1 | 휴장 `SessionEnd` 발행 | 다음 휴장일 다이제스트 §9 적신호에서 `SessionStart 2회` 위양성 소멸 | 다음 휴장일 |

---

## 5. dev_memory 반영

- `DECISION_LOG.md` 추가 항목: `## [MW0601] 쉬는 날을 로그가 쉬었다고 말하지 못했다 — 2026-08-17 휴장일 장중 점검 (2026-08-17)`
- `NEXT_TODO.md` 추가 체크박스: **7건** (F-1 · F-2 · F-3 · F-13 · G-1 · G-2 · G-3)
- 완료 처리한 기존 항목: **없음** — 오늘 코드 변경 0건(휴장 동결일).

> **오늘 코드를 한 줄도 변경하지 않았고 커밋도 하지 않았다.** SYSTEM.md R11 · 금지계명 3·4, 그리고 `DECISION_LOG.md:409` *"여기서부터 D-day 아침까지 코드를 넣지 않는다"*.
