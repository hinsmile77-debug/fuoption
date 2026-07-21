# 설계 결정 및 버그 근본 원인 로그 — MESSIAH

> 형식(미륵이 계승): 증상 → 원인 → 결정 → Why → How to apply → 검증
> "라이브 미검증" 항목은 반드시 검증 기한을 명기한다 (L15).

---

## 2026-07-21 (1차 — 프로젝트 착수: SYSTEM.md + W1 골격)

### [설계결정] 이중 브로커 전략 확정 — 주 KIS / 부 LS / 보조 CREON

**근거**: MESSIAH_Broker_API_Ranking_Ver1.0.md (커뮤니티·실측 비교·선행 프로젝트 자산 종합)
**결정**: 모든 브로커는 `broker/base.py` BrokerAdapter 인터페이스 구현. KIS 어댑터는 마흐디
`broker/` 계층 이식 기반으로 W3~5에 구현. LS 어댑터는 G3 전까지 데이터 전용으로 병행 구축.
**Why**: 아키텍처 정합성(REST+WS·다중실행·크로스플랫폼) + 마흐디 실측 자산 재사용 + 단절 리스크(R11) 이중화.
**How to apply**: 전략 코드는 브로커를 직접 import 금지 — BrokerAdapter와 OrderGateway만 사용.

### [설계결정] OrderGateway 단일 주문 경로 + 미매칭 체결 CRITICAL 정지

**근거**: 미륵이 유령 포지션 사건 (L1, 단일 최대 손실 -675만원)
**결정**: pending 등록을 submit() 내부에 원자화(전송 전 등록·실패 롤백). 미매칭 체결은
반대방향 포지션 해석 대신 게이트웨이 정지 + 사람 호출. resume()은 operator 명시 필수.
**검증**: tests/test_core_w1.py 9건 통과 (pending 선등록, 미매칭 정지, 롤백, naive datetime 거부, 태그 등록부).

### [설계결정] 로그 태그 등록부(TAG_LEVELS) — 태그 1개 = 심각도 1개 강제

**근거**: 미륵이 307차 Degraded Mode 오발동 (L10 — 같은 태그에 WARNING/CRITICAL 혼재로 exclude 불가)
**결정**: 미등록 태그 사용은 ValueError. 신규 태그는 core/logging.py 등록부에 레벨과 함께 추가.
FeatureSetMismatch=ERROR (L3 침묵 금지), FillUnmatched=CRITICAL, DataFallback=WARNING (L18).

### [설계결정] 버스 코덱에 타입 레지스트리 봉투(_type) 방식 채택

**결정**: encode()가 `{"_type": 클래스명, "payload": ...}` 봉투로 직렬화, decode()는
messages.py의 BusMessage 서브클래스 자동 레지스트리에서 복원. 미등록 타입은 즉시 예외.
**Why**: 신규 메시지를 messages.py에 정의하면 배선 없이 버스에 실린다(수동 등록 버그 차단, L13과
동일 철학). 미등록 타입 침묵 무시는 L3(침묵 실패) 계열이므로 예외로 시끄럽게.
**검증**: tests/test_bus_and_scripts.py — 왕복 4건 + 미등록 타입 거부. Redis 실서버 연동은
**라이브 미검증** (검증 기한: 2026-07-24, Redis 기동 후 self_check로 확인).

### [설계결정] self_check가 live+dirty git / live+번들 미지정 시 기동 거부

**근거**: 계명 10 (미커밋 수정 실전 반입 금지), L11 (PC 드리프트), L17 (스키마 정합).
**결정**: dev 모드는 관대하게, paper/live는 엄격하게 — 같은 코드로 모드에 따라 관문 강도만 변경.

### [설계결정] Python 3.11+ 타깃, 단 timeutil은 timezone.utc 사용

**증상**: `datetime.UTC`는 3.11 전용 — CI/검증 환경(3.10)에서 ImportError.
**결정**: `timezone.utc` 별칭으로 통일. ruff DTZ 규칙으로 naive datetime 생성을 lint 차단 (R3).

---
