# SYSTEM.md — MESSIAH 개발 헌법

> **이 파일은 코드 작성 시 항상 우선 참조하는 단일 진실원천(SSOT)이다.**
> 설계 근거는 Master Plan Ver 1.0~2.0, 레슨런은 Lessons_from_Mireuk/Mahdi, 브로커 선정은 Broker_API_Ranking 참조.
> 충돌 시 우선순위: SYSTEM.md > Ver 2.0 > 개별 설계서.

---

## 1. 프로젝트 정의

- **코드네임**: MESSIAH (메시아) — 자율형 파생상품 AI (KOSPI200 미니선물 + 옵션)
- **선행 프로젝트**: 미륵이(선물, Kiwoom/Cybos) · 마흐디(옵션, KIS) — 레슨런 L1~L28, 금지 15계명 적용
- **현 단계**: Phase 1 (Ver 2.0 §9 로드맵 W1~) — 데이터 인프라 → Digital Twin → 5m Expert 순

## 2. 브로커 전략 (확정)

| 역할 | API | 용도 |
|---|---|---|
| **주 브로커** | 한국투자 KIS Developers (REST+WS) | 실행 + 실시간 데이터 + 모의(G2) |
| **부 브로커** | LS증권 신 OpenAPI (REST+WS) | 데이터 교차검증 → 유사시 실행 대체 |
| 보조(후순위) | 대신 CREON | 야간·데이터 보조 (Ver 3.0 재평가) |

- 모든 브로커는 `broker/base.py`의 **BrokerAdapter 추상 인터페이스**를 구현한다 (Ver 1.1 §5-2)
- Digital Twin 시뮬레이터도 같은 인터페이스 — `mode: live | paper | replay` 설정 한 줄로 전환
- 마흐디의 KIS 계층(token_daemon·ws_client·rest_client·order_state_machine·KIS_RAW_FIELD_RANGES)을 이식 기반으로 사용
- **Capability Matrix 의무**: 브로커 기능은 {구현됨, 실측 검증됨} × {모의, 실전}을 `docs/capability_matrix.md`에 기록. 실측 검증 안 된 기능은 사용 금지 (L9·L19·L26)

## 3. 개발 환경 (확정)

- **Python 3.11+ / 64bit** 고정, 가상환경 **uv** 관리, 의존성은 `pyproject.toml` + lock
- OS: 개발 Windows, 배포 대상 Windows N대 (코드는 크로스플랫폼 유지 — Docker/Linux 호환)
- 인프라: Redis (Message Bus, WSL2/Docker), TimescaleDB(후순위) + **Parquet(Polars)** 우선
- ML: **LightGBM (CPU)** — GPU·딥러닝은 명시적 보류 (Ver 1.6 §11), 스케일러 서브시스템 없음 (L5)
- 저장 표준: Parquet 파티션 `data/{종목}/{yyyy}/{mm}/{dd}/`, 설정 YAML + 시크릿 .env(git 금지)

## 4. 아키텍처 불변 원칙 (Ver 2.0 §1 — 코드로 강제)

1. **6 Layer, 아래로만 의존**: L1 Data → L2 Feature → L3 Intelligence → L4 Capital → L5 Execution / L6 Learning / OBS
2. **모든 프로세스 간 통신은 Redis Bus로만** — 직접 함수 호출·파일 공유 금지. 스키마는 `core/messages.py` 단일 정의
3. **완성봉 규율**: Feature 발행·전문가 판단은 해당 Horizon 완성봉 확정 시점에만 (유예 500ms). 예외는 안전장치(틱 단위)뿐
4. **L4 거부권**: Risk 거부는 항소 불가. Kill Switch는 독립 프로세스, 최소 의존
5. **주문은 OrderGateway.submit() 단 하나** — pending 원자 등록 내장, 우회 경로 신설 금지 (L1). 미매칭 체결 = CRITICAL 정지
6. **복제 배포**: 인스턴스 차이는 `configs/instance.yaml`뿐. 릴리스 = git tag + 모델 번들(`messiah-YYYY.MM`). 기동 자가 점검(스키마 정합·번들 해시·브로커 접속·시간 동기) 실패 시 거래 거부 (L11·L17)

## 5. 코딩 규칙 (강제)

| # | 규칙 | 근거 |
|---|---|---|
| R1 | 타입힌트 100%, 경계 데이터는 Pydantic 검증 | Ver 1.0.1 §4.4 |
| R2 | 가격·손익은 Decimal 또는 정수 틱. float 화폐 연산 금지 | 〃 |
| R3 | **naive datetime 생성 금지** — `core.timeutil.now_utc()`만 사용. DB는 TIMESTAMPTZ/UTC | L21 |
| R4 | 절대경로·포트·계좌 하드코딩 금지 — 전부 설정/환경변수 | Ver 1.1 §7.3, 마흐디 |
| R5 | 몽키패치 금지, 파일 500줄 상한, 죽은 코드 즉시 삭제 | L8 |
| R6 | 구조화 JSON 로깅: 태그 1개=심각도 1개, 세션 경계 마커(기동시각·instance_id·git SHA) 첫 줄 | L10·L24 |
| R7 | 수집 루프는 항목별 격리(개별 try+rollback), 실패 시 원본 페이로드 로깅 | L16·L22 |
| R8 | 외부 API 수치 필드: 실측 범위표 작성 후 스키마 확정, unit 명기, 삽입 실패율 경보 | L16 |
| R9 | REST 호출은 공유 RateLimiter 단일 인스턴스 경유, 폴링은 절대시각 고정 틱 | L20 |
| R10 | 폴백·합성 데이터는 배지·경보 동반. 운영 모드 합성 폴백 금지 | L18 |
| R11 | 장중 학습 금지·장중 배포 금지. 모든 변경은 replay 테스트 통과 후 | L2·L7 |
| R12 | 프로세스는 무상태. 재시작 복원은 브로커 API 재조회 → Reconciler 대사 → 구독 시작 순 | L12 |
| R13 | PID 자가 등록(Redis), 종료·Forced Flat 시퀀스는 자기검증 필수 | L23 |
| R14 | 라벨/enum 변경은 3종 세트: 코드+데이터 마이그레이션+조회 화이트리스트 | L25 |
| R15 | 배치(.bat)는 "Python 호출 한 줄"만. 로직은 Python으로 | L27 |
| R16 | 테스트: Feature 계산은 known-value 회귀 테스트 필수. 전략·리스크 로직은 pytest 순수 로직 우선 | 마흐디 방식 |
| R17 | UI는 `ui.*` 토픽 구독 모델 — 백엔드가 UI 함수를 직접 호출하지 않는다 | L13 |
| R18 | 게이트·차단 로직 신설은 섀도 계측 20거래일 후 승격. 차단 계층은 Meta-Labeler/Risk/KillSwitch 3개 고정 | L4 |

## 6. 리포 구조 (표준 — Ver 1.0.1 §4.3 기반)

```
fuoption/                        # 리포 루트 (프로젝트: messiah)
├─ SYSTEM.md                     # 본 문서 (개발 헌법)
├─ pyproject.toml
├─ configs/                      # dev.yaml / paper.yaml / live.yaml / instance.yaml
├─ src/messiah/
│   ├─ core/                     # messages(스키마) · timeutil · config · logging · bus
│   ├─ data/                     # collector · normalizer · archiver · calendar
│   ├─ features/                 # Feature Store (1 Feature = 1 클래스 = 1 파일)
│   ├─ models/                   # trainer · validator · registry · bundle
│   ├─ strategy/                 # futures/ · options/ · meta/
│   ├─ risk/                     # cost_model · risk_engine · sizer · kill_switch
│   ├─ execution/                # order_gateway · reconciler
│   ├─ broker/                   # base(인터페이스) · kis/ · ls/ · simulator/
│   ├─ ui/                       # FastAPI 백엔드 (Command Center)
│   └─ obs/                      # 로깅·헬스·알림
├─ tests/
├─ scripts/                      # self_check · install · 회의 안건 생성기
├─ db/migrations/                # 기동 시 전량 재적용 (L17)
├─ dev_memory/                   # DECISION_LOG · SESSION_LOG · CURRENT_STATE · NEXT_TODO · trade_review/
└─ docs/                         # Master Plan Ver1.0~2.0 · 레슨런 · capability_matrix
```

## 7. 개발 프로세스

- **dev_memory 의무**: 모든 세션은 DECISION_LOG(증상→원인→결정→Why→How to apply→검증) + NEXT_TODO 갱신. "라이브 미검증"엔 검증 기한 명기
- **회의체** (Ver 2.0 §7.1): 일간(장마감 후, 운영점검보고서 양식) · 주간(금, 에이징 전수) · 월간(승격 심사) · 분기(피처셋). 안건은 자동 생성, 결론은 채택/기한부 보류/사유부 폐기만
- **git**: `main` 항상 실행 가능, 전략 변경은 브랜치+백테스트 리포트 첨부. 커밋 안 된 수정을 실전 PC에 남기지 않는다
- **구현 순서** (Ver 2.0 §9): W1~2 골격 → W3~5 KIS 수집 → W6~8 완성봉·Feature → W9~11 Digital Twin → … 5m Expert 우선

## 8. 금지 15계명 (요약 — 위반 코드는 리뷰 반려)

주문은 게이트웨이로만(1) · replay 검증 없이 배포 금지(2) · 장중 학습 금지(3) · 장중 배포 금지(4) · 몽키패치 금지(5) · 피처 불일치 침묵 금지(6) · 게이트 무검증 투입 금지(7) · 미교정 확률 임계 금지(8) · 종목코드 맹신 금지(9) · 미커밋 수정 실전 반입 금지(10) · 필드 실측 없는 스키마 금지(11) · 조용한 폴백 금지(12) · naive datetime 금지(13) · 자기검증 없는 종료 시퀀스 금지(14) · 코드-DB 스키마 동일 가정 금지(15)
