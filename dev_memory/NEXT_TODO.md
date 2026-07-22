# NEXT_TODO — MESSIAH

> 에이징 규칙: 30일 초과 시 주간회의 최상단 강제 배치, 60일 초과 시 폐기/즉시착수 양자택일 (Ver 2.0 §7.1)

## W1~2 잔여 (골격)

- [x] core/bus.py — Redis pub/sub + Streams 래퍼 (2026-07-21 완료, 코덱 테스트 4건)
- [x] scripts/self_check.py — 기동 자가 점검 (2026-07-21 완료, dev PASS·live 번들 미지정 거부 확인)
- [x] scripts/agenda.py — 회의 안건 자동 생성기 (2026-07-21 완료, 에이징·미검증 태그 자동 안건화 확인)
- [x] git init + 첫 커밋 + pre-commit 훅 (.env 차단, ruff) — (2026-07-21 완료, root commit b55580c)
- [x] Redis 실서버 연동 검증 (2026-07-21 완료) — Docker `messiah-redis`(redis:7-alpine) 컨테이너를
      포트 **6380**에 격리 기동. 6379는 별개 프로젝트 `mahdi_redis`가 이미 점유 중이라 메시지 버스
      혼선 방지 위해 분리. `configs/instance.yaml`의 redis_url을 6380으로 갱신. self_check PASS 확인.

## W3~5 (KIS 수집 — 마흐디 이식)

- [x] 마흐디 broker/ 계층 이식: token_daemon · rest_client · ws_client · order_state_machine
      (2026-07-21 완료, commit 9cd0f9d) — src/messiah/broker/kis/, 소스는
      C:\Users\82108\PycharmProjects\options\mahdi\broker\. KISSettings(pydantic-settings) 대신
      KISCredentials(messiah.core.config.BrokerConfig+resolve_secret)로 설정 소스 교체. 테스트
      41건 이식. 아직 BrokerAdapter 구현체(KIS용)는 없음 — 별도 착수 필요.
- [x] 실제 KIS 모의투자 서버 연동 검증 (2026-07-21 완료, commit 3c6c9e3) — 마흐디와 별도로 신규
      발급받은 모의투자 앱키/계좌(60046651)로 token_daemon(토큰 발급)·get_investor_flow·get_balance
      전부 실계좌 확인. 이 과정에서 마흐디 원본 `get_balance()`가 공식 문서 Required=Y 필드
      (MGNA_DVSN·EXCC_STAT_CD·CTX_AREA_FK200/NK200)를 안 보내 KIS가 거부하던 버그 발견·수정(실측
      전에는 아무도 몰랐던 문제 — "구현됨≠검증됨"). KIS_APP_KEY/SECRET/ACCOUNT는 `.env`에 저장(git
      제외 확인됨).
- [x] KISBrokerAdapter(BrokerAdapter 구현체) 작성 (2026-07-22 완료) —
      src/messiah/broker/kis/adapter.py. connect/close/submit/cancel/positions/account/
      probe_front_month 전부 구현. submit()/cancel()/positions()/account()의 필드 구성은
      마흐디 원본이 아니라 공식 문서(docs/efriend 엑셀, API ID v1_국내선물-001/002/004)를
      openpyxl로 직접 읽어 파생시킴 — 특히 cancel()은 마흐디에도 없던 신규 구현(rest_client.py에
      cancel_order() 추가, PATH_FUTUREOPTION_ORDER_MODIFY_CANCEL, 전량취소만 지원). tick_size는
      상품별 값이 이 프로젝트에 아직 없어 하드코딩하지 않고 생성자 주입으로 미룸. probe_front_month는
      종목코드 마스터파일 미연동으로 NotImplementedError. 테스트 13건 추가(전부 mock, 실계좌
      미검증) — submit/cancel/positions/account는 "구현됨≠검증됨" 상태로 capability_matrix.md
      실측 전 프로덕션 주문 경로 사용 금지.
- [x] KISBrokerAdapter 실계좌(모의투자) submit→cancel→positions/account 흐름 실측 (2026-07-22
      완료, docs/capability_matrix.md 신설) — 미니선물 근월물(A05608, F 202608)로 BUY 지정가
      1000.00(최우선매수호가 1131.04 대비 -131pt, 체결 불가능하게 의도적으로 낮춤) qty=1 제출 →
      SubmitResult(ok=True, broker_order_no='0000009623') → 즉시 전량취소 → True → 이후
      positions/account 변동 없음 확인(미체결 확정, 부수효과 없음). 전 과정 버그 없이 1회 성공
      (get_balance MGNA_DVSN 때와 달리 이번엔 문서 그대로 맞았음). 부산물 발견: 마흐디
      symbol_master.py가 몰랐던 미니선물 상품종류 코드 "B"(정규선물은 "1")를 KIS 종목코드
      마스터파일(fo_idx_code_mts.mst) 실측으로 확인 — 상세는 capability_matrix.md "알려진 갭"
      참고. 틱 크기(tick_size)는 호가 5단계 간격 역산으로 A05608=0.02 확인(다른 상품/행사가에
      일반화 금지). 남은 갭: 실보유 포지션 상태에서의 positions() 파싱 미검증(이번은 빈 계좌),
      실제 체결→Fill 이벤트 연계 미검증.
- [ ] KIS_RAW_FIELD_RANGES.md 이관 + 미니선물/옵션 필드 실측 범위표 작성 (R8 — 5거래일)
- [ ] 마흐디 symbol_master.py를 messiah로 이식 — probe_front_month() 구현에 필요. 이식 시 위에서
      발견한 미니선물 상품종류 "B"와 선물 행의 월물랭크 필드 위치(index 6, 마흐디가 "ATM구분"으로
      잘못 이름 붙인 자리) 버그를 함께 고칠 것 (2026-07-22 등록)
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
