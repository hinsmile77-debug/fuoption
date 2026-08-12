# 증거 지도 — 어떤 파일이 무엇의 증거인가

`{d}` = `YYYYMMDD`, `{D}` = `YYYY-MM-DD` (KST 기준)

## 로그

| 파일 | 프로세스 | 무엇을 말해주는가 | 크기 감각 |
|---|---|---|---|
| `logs/l1_daily_{d}.log` | L1 메인 (수집·합성·피처·판단) | 하루의 거의 전부. 세션 경계, 자가점검, 완성봉, Feature 발행, 오류 | 130~240KB |
| `logs/g2_daily_{d}.log` | G2 페이퍼 트레이딩 | 모의 판단·주문 경로 | 2~8KB |
| `logs/ui_{d}.log` | Command Center (Streamlit/Uvicorn) | UI 기동·포트·접속 | 0.4~19KB |
| `logs/ui_{d}.err.log` | UI stderr | **존재하면 그 자체가 신호** | 보통 0 |
| `logs/postmarket_{d}.log` | 장후 5단계 배치 | 조각통합→재합성→검증→스코어카드→무결성. **크기가 작으면 중단** | 12~14KB 정상 |
| `logs/shutdown_watchdog.log` | 종료 감시 (롤링) | 종료 시퀀스, 강제 종료 | 누적 |

### 로그 형식

두 종류가 섞여 있다.

1. **JSON 라인** — `{"ts": ..., "level": ..., "tag": ..., "msg": ..., ...}`
   태그 1개 = 심각도 1개 (SYSTEM.md R6). 세션 첫 줄에 `instance_id` · `git_sha` · `pid`.
2. **비-JSON 라인** — 배치 스크립트 에코, 자가점검 `[OK ]`/`[WARN]`/`[FAIL]`, `self-check: PASS`.

파일 선두에 BOM(`﻿`)이 있다. 파싱 시 `utf-8-sig`.

### 자주 쓰는 태그

| 태그 | 의미 | 판정 |
|---|---|---|
| `SessionStart` / `SessionEnd` | 프로세스 생사 | End 없음 = 비정상 종료 |
| `LaunchWindowRefused` | 기동 창 밖 기동 거부 | 거부는 정상. 이후 정시 기동 여부까지 확인 |
| `CrashForensicsArmed` | 크래시 덤프 무장 | 없으면 크래시 시 원인 소실 |
| `FixVerificationRecurred` | **고친 것이 재발** | 항상 P0 |
| `FixVerificationFailed` | 수정 검증 자체 실패 | P0/P1 |
| `AggregatorLateTickDropped` | 순서 뒤바뀐 틱 폐기 | 건수가 timer 승격 비용 추정치 |
| `UnmatchedFill` | 미매칭 체결 | CRITICAL 정지 대상 (불변원칙 5) |
| `KillSwitch*` / `CircuitBreaker` | 안전장치 발동 | 발동 사유와 복구까지 추적 |
| `ForcedFlat` | 강제 청산 | 자기검증 완료 여부 확인 (R13) |

## 산출물 JSON

| 파일 | 생성 시점 | 핵심 키 |
|---|---|---|
| `logs/status_snapshot.json` | 장중 상시 갱신 | `code_version.stale` · `components.*.state` · `circuit_breaker` · `irrecoverable_loss` |
| `logs/daily_integrity_{d}.json` | 장후 배치 마지막 | 버킷 유실, `horizon_findings`, `late_bar_drops` |
| `logs/self_eval_{D}.json` | 장 마감 | 자기평가 지표 (**날짜에 하이픈**) |
| `logs/vol_scorecard_{d}.json` | 장후 | 변동성 스코어카드 |
| `logs/volume_check_{d}.json` | 장후 | 거래량 검증 |
| `logs/kill_switch_verification_{d}.json` | 점검 시 | Kill Switch 도달 범위 실측 |
| `logs/g2_daily_returns.jsonl` | 일별 append | 당일 행 존재 여부가 곧 G2 결선 여부 |
| `logs/command_center_ui.json` | UI 기동 | UI 상태 |
| `logs/g1_walk_forward_{d}.json` | 워크포워드 실행 시 | 검증 결과 |

## 기준 문서

| 파일 | 역할 |
|---|---|
| `SYSTEM.md` | **개발 헌법 / SSOT.** 충돌 시 최우선. 아키텍처 불변원칙 5, 코딩규칙 R1~R18, 금지 15계명 |
| `Derivatives_AI_Master_Plan_Ver2.0.md` | 현행 마스터플랜. §9 로드맵 W단계 |
| `Derivatives_AI_Master_Plan_Ver1.0~1.6.md` | 이력. 설계 근거 추적용 |
| `Derivatives_AI_Position_Holding_Policy_Ver1.0.md` | 포지션 보유 정책 |
| `MESSIAH_Lessons_from_Mireuk_Ver1.0.md` | 선행 프로젝트(선물) 레슨런 L1~ |
| `MESSIAH_Lessons_from_Mahdi_Ver1.0.md` | 선행 프로젝트(옵션) 레슨런 |
| `Docs/capability_matrix.md` | 브로커 기능 {구현됨, 실측 검증됨} × {모의, 실전}. **실측 미검증 기능 사용 금지** |
| `Docs/동작흐름과상태/` | 진입 흐름과 개발 상태 스냅샷 |
| `Docs/KIS_RAW_FIELD_RANGES.md` | 외부 API 필드 실측 범위 (R8) |

## dev_memory

| 파일 | 크기 | 읽는 법 |
|---|---|---|
| `dev_memory/DECISION_LOG.md` | ~315KB | 통째로 읽지 않는다. 헤딩 목록 + 꼬리 몇 KB만. 증상→원인→결정→Why→How to apply→검증 형식 |
| `dev_memory/NEXT_TODO.md` | ~280KB | 미완료 체크박스 `- [ ]` 만 뽑아 본다 |

## 스크립트

| 파일 | 용도 |
|---|---|
| `scripts/run_l1_daily.py` / `.bat` | L1 메인 기동 (bat은 Python 호출 한 줄만 — R15) |
| `scripts/run_g2_paper_trading.py` / `.bat` | G2 페이퍼 |
| `scripts/run_postmarket.py` / `.bat` | 장후 5단계 |
| `scripts/self_check.py` | 기동 자가 점검 |
| `scripts/daily_integrity_report.py` | 무결성 리포트 |
| `scripts/check_dev_memory_updated.py` | dev_memory 갱신 강제 |
| `scripts/install_scheduled_tasks.ps1` | 스케줄러 정본 (트리거 시각의 진실원천) |
| `scripts/recover_now.bat` / `stop_l1_daily.bat` | 수동 복구·중지 |
| `scripts/run_replay.py` | replay 검증 (금지계명 2 — 배포 전 필수) |

## git

```bash
git log --oneline --since="{D} 00:00" --until="<익일> 00:00"   # 당일 커밋
git status --porcelain                                          # 미커밋
git log --oneline -10                                           # 최근 흐름
```

커밋 메시지 첫 단어는 PC 식별자 `[MW0601]`. 없는 커밋이 있으면 규약 위반으로 지적한다.

## 유용한 원본 조회

```bash
# 특정 태그 전량
grep '"tag": "AggregatorLateTickDropped"' logs/l1_daily_{d}.log

# ERROR 이상만
grep -E '"level": "(ERROR|CRITICAL|FATAL)"' logs/l1_daily_{d}.log

# 특정 시간대
grep -E '"ts": "{D}T09:0[0-9]' logs/l1_daily_{d}.log

# 태그 분포
grep -o '"tag": "[^"]*"' logs/l1_daily_{d}.log | sort | uniq -c | sort -rn | head -30

# 자가점검 중 비-OK
grep -E '^\[(WARN|FAIL|ERR)' logs/l1_daily_{d}.log
```
