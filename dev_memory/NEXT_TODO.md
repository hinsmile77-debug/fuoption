# NEXT_TODO — MESSIAH

> 에이징 규칙: 30일 초과 시 주간회의 최상단 강제 배치, 60일 초과 시 폐기/즉시착수 양자택일 (Ver 2.0 §7.1)

## W1~2 잔여 (골격)

- [x] core/bus.py — Redis pub/sub + Streams 래퍼 (2026-07-21 완료, 코덱 테스트 4건)
- [x] scripts/self_check.py — 기동 자가 점검 (2026-07-21 완료, dev PASS·live 번들 미지정 거부 확인)
- [x] scripts/agenda.py — 회의 안건 자동 생성기 (2026-07-21 완료, 에이징·미검증 태그 자동 안건화 확인)
- [ ] git init + 첫 커밋 + pre-commit 훅 (.env 차단, ruff) — **사용자 PC에서 실행 필요** (2026-07-21 등록)
- [ ] Redis 실서버 연동 검증 (WSL2/Docker 기동 후 self_check --skip-redis 없이 PASS 확인) (2026-07-21 등록)

## W3~5 (KIS 수집 — 마흐디 이식)

- [ ] 마흐디 broker/ 계층 이식: token_daemon · rest_client · ws_client · order_state_machine
- [ ] KIS_RAW_FIELD_RANGES.md 이관 + 미니선물/옵션 필드 실측 범위표 작성 (R8 — 5거래일)
- [ ] docs/capability_matrix.md 작성 시작 — {구현, 실측} × {모의, 실전}
- [ ] 공유 RateLimiter (모의 1건/초 실측 반영, 적응형 백오프) — R9
- [ ] 절대시각 고정 틱 폴링 스케줄러 — R9
- [ ] **token_daemon을 단일 공유 프로세스로 격리** — Access Token을 Redis에 캐시, 전 프로세스는 캐시만 읽는다.
      KIS 토큰 재발급이 1분당 1회로 제한되어 있어(2026-07-21 리서치), 프로세스마다 개별 발급 시도 시
      즉시 리밋에 걸림. "한 계정 다중 봇" 운영의 전제조건.
- [ ] RateLimiter 카운터는 프로세스 로컬이 아니라 **Redis 전역 카운터** 기반으로 구현 —
      계좌(앱키) 단위 레이트리밋을 L1/L3/L5 등 여러 프로세스가 나눠 쓰므로 로컬 카운터로는
      예산 초과를 막을 수 없음 (2026-07-21 등록)

## 등록된 관찰 항목 (분기회의)

- [ ] 키움 신 REST의 국내 선물옵션 확장 발표 여부 (발표 시 브로커 랭킹 재평가)
- [ ] KRX 야간 파생시장 API 지원 현황 (KIS·LS)
