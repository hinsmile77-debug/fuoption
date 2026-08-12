# 국면별 점검 체크리스트

각 국면에서 "설계상 있어야 할 일"의 목록이다. 로그에서 **확인됨 / 확인 안 됨 / 위반** 셋 중 하나로 판정한다.
"로그에 없다"는 "안 일어났다"가 아니라 **"관측되지 않았다"** 이다 — 그 자체가 이상점 후보다(SYSTEM.md R6·R10, 금지계명 12 조용한 폴백 금지).

---

## A. 장전 (pre) — "오늘 거래할 자격이 되는가"

### A-1. 기동과 스케줄
- [ ] Windows 작업 스케줄러 `Messiah`(L1) · `Messiah-G2` · `Messiah-Shutdown` · `Messiah-Postmarket` 4종이 등록·무장 상태인가 (`scripts/install_scheduled_tasks.ps1` 정본과 일치)
- [ ] 자가점검의 `schedule_drift` 가 "정본 일치"인가 — 불일치면 트리거 시각이 코드와 어긋난 것
- [ ] 기동 창(08:15~15:35) 밖 기동 시도가 있었는가 → `LaunchWindowRefused`. **거부 자체는 정상**이지만, 거부 후 정시 트리거로 실제 기동됐는지까지 확인해야 완결이다
- [ ] `SessionStart` 가 프로세스별로 정확히 1회인가 — 2회 이상이면 중복 기동 또는 크래시 후 재기동
- [ ] `SessionStart.git_sha` 가 HEAD와 같은가 — 다르면 옛 코드로 기동한 것

### A-2. 기동 자가 점검 (SYSTEM.md 불변원칙 6)
- [ ] `config` / `schema` / `timezone` / `clock` / `host` / `git` / `secrets` / `bundle` / `registry` / `redis` 전 항목 `[OK ]`
- [ ] `clock offset` 이 허용 범위인가 (수 초 단위 드리프트도 완성봉 규율에 영향)
- [ ] `host` 라인의 disk / power / docker / cpu / 외부 파이썬 개수 / boot_recovery
- [ ] `git dirty` 인 경우 — dev 모드면 허용이나, **live/paper 모드에서 dirty면 금지계명 10 위반**
- [ ] 최종 판정이 `self-check: PASS — 기동 허용` 인가. FAIL인데 거래가 진행됐다면 P0

### A-3. 데이터 준비
- [ ] 옵션 체인 / 종목 마스터 로드 성공 — 스킵·부분실패 건수 0인가
- [ ] 브로커(KIS) 토큰 발급·WS 접속 성공, 재시도 횟수
- [ ] Redis 접속 및 `core/messages.py` 스키마 버전 일치
- [ ] 모델 번들 해시 검증 통과, 레지스트리 로드
- [ ] 전일 데이터 무결성 — 전일 `daily_integrity_*.json` 이 clean 이었는가

### A-4. 국면 특유의 함정
- [ ] 08:15~09:00 사이 10분 이상 로그 공백 — 무엇으로 채워졌어야 하나
- [ ] UI(Command Center) 기동 확인, `logs/ui_*.err.log` 비어 있는가
- [ ] 웜업이 끝났는가 — 끝나지 않은 웜업이 회색(UNKNOWN)으로 표시되며 조용히 지나가는 사례가 과거에 있었다

---

## B. 장중 (intra) — "지금 설계대로 돌고 있는가"

### B-1. 파이프라인 생존
- [ ] `status_snapshot.json` 의 `components` 전부 `OK` — `l1.collector` / `l1.feature_engine` / `l1.composer` / `g2.pipeline`
- [ ] 각 컴포넌트 `age_seconds` 가 신선한가. **`UNKNOWN`/회색은 "정상"이 아니라 "모른다"** 이다
- [ ] `code_version.stale` — true면 커밋된 코드가 실행 중이 아니다
- [ ] `circuit_breaker.phase` 및 `gateway_halted`
- [ ] `irrecoverable_loss.clean` 과 `lost_items`

### B-2. 완성봉 규율 (SYSTEM.md 불변원칙 3)
- [ ] Feature 발행이 Horizon 완성봉 확정 시점(유예 500ms 이내)에만 일어나는가
- [ ] 늦은 틱 드롭(`AggregatorLateTickDropped` 계열) 건수 — 조용히 버려지고 있지 않은가
- [ ] 합성봉 개수와 거래량 항등식 일치(유실 0) — `l1.composer` detail
- [ ] NaN 임계 초과 Horizon 이 있는가
- [ ] `delivery_latency` p99

### B-3. 판단·주문 경로
- [ ] Expert 판단 발행 건수 — 0건이면 사슬 어딘가가 끊긴 것(과거 G2 번들 미결선 사례)
- [ ] Meta Decision → Risk Engine → Sizer → OrderGateway 각 단계 통과/거부 건수
- [ ] `OrderGateway.submit()` 외 경로로 나간 주문 0건 (불변원칙 5)
- [ ] 미매칭 체결(`UnmatchedFill`) 0건 — 1건이라도 있으면 CRITICAL 정지가 걸렸어야 한다
- [ ] Risk 거부 사유 분포 — 특정 사유로 전량 거부되고 있지 않은가

### B-4. 금지 사항 확인
- [ ] 장중 학습 흔적 없는가 (금지계명 3)
- [ ] 장중 배포/재기동 흔적 없는가 (금지계명 4)
- [ ] 합성·폴백 데이터가 배지·경보 없이 쓰이지 않았는가 (R10, 금지계명 12)

---

## C. 장후 (post) — "오늘 하루가 설계대로였는가"

### C-1. 종료 시퀀스 (R13 · 금지계명 14)
- [ ] 15:35 정규장 마감 처리
- [ ] `Messiah-Shutdown`(15:40) 동작 — `logs/shutdown_watchdog.log`
- [ ] 프로세스별 `SessionEnd` 존재 및 "정상 종료" 여부. **없으면 비정상 종료**
- [ ] Forced Flat 이 필요했다면 자기검증까지 완료됐는가
- [ ] 프로세스 종료 코드와 로그가 일치하는가 (`exit-code-matches-log` 계열 검증)

### C-2. 장후 배치 (15:45 `run_postmarket`)
- [ ] `run_compact.py` 조각 통합 — 성공/멱등/실패
- [ ] `run_recompose.py` 상위 Horizon 재합성
- [ ] `verify_archive_volume.py` · `run_vol_scorecard.py` · `daily_integrity_report.py` 순서와 성공 여부
- [ ] 5단계 전부 완주했는가 — 중간 중단 시 이후 산출물이 전부 오염된다
- [ ] `postmarket_*.log` 크기가 비정상적으로 작으면(수백 B) 즉시 중단된 것

### C-3. 산출물 정합
- [ ] `daily_integrity_<날짜>.json` — 버킷 유실, horizon_findings, late_bar_drops
- [ ] `self_eval_<날짜>.json`
- [ ] `vol_scorecard_<날짜>.json` · `volume_check_<날짜>.json`
- [ ] `g2_daily_returns.jsonl` 에 당일 행이 추가됐는가
- [ ] 전일 대비 지표 급변 — 급변 자체보다 **급변이 설명되는가**가 핵심

### C-4. 수정 검증 (가장 중요)
- [ ] `FixVerificationRecurred` — 고쳤다고 기록된 것이 재발했는가. **재발은 항상 P0 보고 대상**
- [ ] `FixVerificationFailed` / 검증 기준 위반
- [ ] 어제 세운 fix 계획 중 오늘 검증 예정이던 항목의 결과 (dev_memory NEXT_TODO 대조)
- [ ] 섀도 계측 중인 게이트의 경과 거래일 수 (R18 — 20거래일 후 승격)

### C-5. 기록 의무
- [ ] dev_memory DECISION_LOG · NEXT_TODO 갱신됨 (`scripts/check_dev_memory_updated.py`)
- [ ] 당일 커밋이 있다면 `[MW0601]` 접두어가 붙었는가
- [ ] 미커밋 변경이 실전 PC에 남아 있지 않은가 (금지계명 10)

---

## D. 국면 공통 — 놓치기 쉬운 것들

- **회색(UNKNOWN)은 정상이 아니다.** 하나의 회색이 여러 뜻을 겸하고 있으면 그것부터 분리 대상이다.
- **건수 0은 두 가지다** — 진짜 없었거나, 계측이 없거나. 어느 쪽인지 로그로 구분한다.
- **INFO 레벨로 지나간 폴백**은 R10 위반 후보다.
- **태그 1개 = 심각도 1개** (R6). 같은 태그가 INFO와 ERROR를 겸하면 규칙 위반이다.
- **파일 500줄 상한** (R5) — 점검 중 발견하면 P2로 올린다.
- 어제 다이제스트가 있으면 **전일 대비 델타**를 본다. 절대값보다 변화가 정보량이 크다.
