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
- [x] 마흐디 symbol_master.py를 messiah로 이식 (2026-07-22 완료) —
      src/messiah/broker/kis/symbol_master.py. pandas 대신 polars 사용(이 프로젝트 의존성과 통일,
      pandas 신규 추가 안 함). 위에서 발견한 미니선물 상품종류 "B"(PRODUCT_TYPE_MINI_FUTURES로
      추가)와 선물 행 월물랭크 필드 위치(마흐디가 "ATM구분"이라 이름 붙인 필드가 선물 행에서는
      실제 월물랭크임 — front_month_future_code()가 이제 이 필드로 명시 정렬, 이전엔 공란 컬럼
      정렬이라 우연히 파일 순서에 기대고 있었음) 버그 둘 다 반영. KISBrokerAdapter.probe_front_month
      를 이걸로 구현(product="K200_MINI_FUT"/"K200_FUT" 지원, "K200_OPT"는 단일 코드 개념이 아니라
      ValueError — 옵션 종목은 IndexDerivativesMaster.option_symbol()/nearest_expiry_chain() 직접
      사용). 마스터파일은 어댑터 인스턴스당 최초 1회만 받고 캐싱. 테스트 14건 추가(symbol_master
      11건 — 근월물 정렬 회귀 테스트 포함, adapter probe_front_month 3건 — 전부 로컬 축소판 파일
      기준). docs/capability_matrix.md 갱신.
- [x] probe_front_month() 실제 마스터파일 URL로 end-to-end 실측 (2026-07-22 완료) —
      K200_MINI_FUT→A05608, K200_FUT→A01609(둘 다 이전 세션 실측값과 일치), 재호출 시 캐시
      재사용 확인, K200_OPT/미지원 문자열 ValueError 확인. 버그 없음 — 계좌·토큰 무관(정적 파일
      다운로드)이라 리스크 없이 실행.
- [x] symbol_master 옵션 체인 경로(options/nearest_expiry_chain/option_symbol) 실측 (2026-07-22
      완료) — 실제 마스터파일로 regular(콜/풋 각 390건, 만기 202608)·weekly_mon(116/116)·
      weekly_thu(150/150) 체인 조회, mini(D/E)는 이 시점 상장 없음(정상, series 자체는 동작).
      처음엔 390건이 여러 만기가 섞인 버그로 의심했으나 group_by로 재확인한 결과 전부 단일
      만기(202608)의 실제 상장 행사가 수였음(KOSPI200 옵션이 2.5pt 간격으로 이렇게 넓게 상장돼
      있음 — 버그 아님). option_symbol() 재조회 일치·미상장 행사가 None 확인. 체인에서 뽑은
      종목코드(B01608A46, strike 1112.5)로 get_quote() 실호출 → rt_cd=0, 체결가 101.45 — 내부
      일관성뿐 아니라 실제 거래 가능한 코드임을 확인. 남은 갭: 위클리 요일 대응(N/O=월,L/M=목)은
      마흐디의 2026-07-10 단일 실측에 의존 중 — 이번엔 재검증 안 함(capability_matrix.md 참고).
- [x] 공유 RateLimiter — Redis 전역 카운터 기반 (2026-07-22 완료) —
      src/messiah/broker/kis/redis_rate_limiter.py. rest_client._RateLimiter와 같은 계약(wait/
      record_rate_limit_hit/record_success)을 Lua 스크립트 3개로 원자화해 Redis에 백업 — 백오프
      상수(_BACKOFF_MULTIPLIER 등)는 _RateLimiter 클래스 속성을 직접 재사용해 로컬/Redis 버전이
      갈라지지 않게 함. KISRestClient(rate_limiter=...)로 주입 가능하게 rest_client.py에
      RateLimiterProtocol 추가(생략 시 기존과 동일한 로컬 _RateLimiter, 기존 61건 테스트 무변경
      통과). 테스트 8건을 실제 messiah-redis 컨테이너(redis://localhost:6380/15, 전용 DB)로 실행 —
      스레드 두 개가 서로 다른 RedisRateLimiter 인스턴스(=프로세스 흉내)로 동시에 wait()해도
      최소 간격이 지켜짐을 확인, 나머지 백오프/복구 계수는 로컬 버전과 정확히 같은 수치로 통과.
- [ ] 절대시각 고정 틱 폴링 스케줄러 — R9
- [x] token_daemon을 단일 공유 프로세스로 격리 — Redis 캐시 (2026-07-22 완료) —
      src/messiah/broker/kis/redis_token_cache.py의 RedisTokenDaemon. 캐시 적중 시 즉시 반환,
      미스면 SET NX 분산락을 잡은 프로세스 하나만 기존 TokenDaemon으로 실제 발급하고 나머지는
      새로 발급을 시도하지 않고 캐시에 값이 나타날 때까지 폴링(스탬피드 방지). 발급 실패 시
      finally로 락 해제(다음 시도가 불필요하게 안 막힘). Redis 캐시 TTL은 실제 만료 5분 전으로
      선제 갱신(TokenDaemon.is_expired()와 동일 여유). TokenDaemon에 current_token 프로퍼티
      추가(TTL 계산용, 기존 동작 변경 없음). rest_client.py에 TokenSource 프로토콜 추가해
      KISRestClient가 TokenDaemon/RedisTokenDaemon 어느 쪽이든 받게 함. 테스트 6건을 실제
      messiah-redis로 실행 — 스레드 두 개(=프로세스 흉내)가 동시에 get_token()해도 실제 KIS
      발급 호출은 정확히 1회만 일어남을 확인(가짜 발급 지연 0.3초로 경쟁 상황 재현), 발급 실패
      시 락 해제·폴링 타임아웃도 확인.

## 등록된 관찰 항목 (분기회의)

- [ ] 키움 신 REST의 국내 선물옵션 확장 발표 여부 (발표 시 브로커 랭킹 재평가)
- [ ] KRX 야간 파생시장 API 지원 현황 (KIS·LS)
