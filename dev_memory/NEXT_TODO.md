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
- [x] KIS_RAW_FIELD_RANGES.md 이관 (2026-07-22 부분 완료, R8 — 5거래일 관측 중 1회분) —
      docs/KIS_RAW_FIELD_RANGES.md. 마흐디 원본(정규옵션 기준)은 그대로 보존하고 messiah 자체
      관측을 분리 기록. 실계좌로 미니선물 근월(A05608)·정규옵션 근월 콜/풋 4종 get_quote() 실측
      — 신규 발견: (1) 미니선물은 Greeks가 전부 고정값(delta=1.0, 나머지 0)이라 오버플로우
      위험 없음, (2) 이 세션 표본(만기 3주 남음)의 theta(-1.6~-1.7)는 마흐디 최대 관측치
      (-9625.4268)보다 훨씬 작음 — "얇은/근접만기일수록 커진다"는 마흐디 메모와 일치, 이
      세션은 만기 임박 구간을 못 봤다는 뜻이라 theta 컬럼 폭을 좁히면 안 됨, (3) IV가 마흐디
      최대치(~103%)를 넘는 109%대 표본 발견(이 세션 KOSPI200 급등장 영향으로 추정) — raw/100
      저장 방식 자체는 여전히 안전. 미실측 갭: 미니옵션(D/E)은 이 시점 상장 자체가 없어 관측
      불가(symbol_master 실측으로 확인), 위클리·만기임박 옵션 미관측 — R8 "5거래일" 중 1회분만
      수행, 이후 세션에서 이어서 채울 것.
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
      일관성뿐 아니라 실제 거래 가능한 코드임을 확인.
- [x] 위클리 옵션 요일 대응(N/O=월,L/M=목) 재검증 (2026-07-22 완료) — 마흐디는 2026-07-10
      단일 관측(대시보드 표시명 교차확인)에만 의존했었음. 다른 날짜·다른 방법으로 재확인:
      symbol_master.nearest_expiry_chain()으로 뽑은 각 series 근월물 실제 종목코드를
      get_quote()로 조회해 futs_last_tr_date(만기일)를 직접 읽고 Python weekday()로 요일 계산
      — weekly_mon(N/O) 근월 만기 20260727=월요일, weekly_thu(L/M) 근월 만기 20260723=목요일,
      둘 다 마흐디 매핑과 일치. symbol_master.py 모듈 docstring·capability_matrix.md 갱신.
- [x] 공유 RateLimiter — Redis 전역 카운터 기반 (2026-07-22 완료) —
      src/messiah/broker/kis/redis_rate_limiter.py. rest_client._RateLimiter와 같은 계약(wait/
      record_rate_limit_hit/record_success)을 Lua 스크립트 3개로 원자화해 Redis에 백업 — 백오프
      상수(_BACKOFF_MULTIPLIER 등)는 _RateLimiter 클래스 속성을 직접 재사용해 로컬/Redis 버전이
      갈라지지 않게 함. KISRestClient(rate_limiter=...)로 주입 가능하게 rest_client.py에
      RateLimiterProtocol 추가(생략 시 기존과 동일한 로컬 _RateLimiter, 기존 61건 테스트 무변경
      통과). 테스트 8건을 실제 messiah-redis 컨테이너(redis://localhost:6380/15, 전용 DB)로 실행 —
      스레드 두 개가 서로 다른 RedisRateLimiter 인스턴스(=프로세스 흉내)로 동시에 wait()해도
      최소 간격이 지켜짐을 확인, 나머지 백오프/복구 계수는 로컬 버전과 정확히 같은 수치로 통과.
- [x] 절대시각 고정 틱 폴링 스케줄러 (2026-07-22 완료) — src/messiah/core/scheduler.py의
      FixedTickScheduler. "작업 후 sleep"(상대 간격) 대신 UNIX epoch 기준 절대시각의 배수에
      실행 시점을 고정 — 마흐디가 L20에서 겪은 드리프트 누적(5분 간격 폴링이 지연 누적으로
      4분치 유실)의 재발을 구조적으로 막는다. 콜백이 tick_seconds를 넘겨 틱을 건너뛰면
      SchedulerTickMissed로 로깅(침묵 금지 — 몰아서 실행하지 않고 다음 유효 미래 틱으로 바로
      이동, 몰아서 실행하면 그 순간 REST 호출이 몰려 공유 RateLimiter가 급감속돼 L20이 다른
      형태로 재발함). 콜백이 예외를 던지면 SchedulerCallbackError로 로깅하고 루프는 계속(L22).
      core/logging.py TAG_LEVELS에 두 태그 신규 등록. 순수 함수(next_tick_at)라 실제 대기 없이
      결정론적으로 테스트 가능 — 테스트 10건(경계 정렬·위상 오프셋·naive 거부·틱 스킵 로깅·
      콜백 예외 복구 등). 아직 실제 L1 수집 루프(옵션체인/선물/투자자매매동향 폴링)에 물려본
      적은 없음 — 그 수집 루프 자체가 아직 없음(별도 착수 필요).
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
- [x] RedisRateLimiter/RedisTokenDaemon을 실제 KIS 서버로 통합 실측 (2026-07-22 완료) —
      진짜 별도 OS 프로세스 두 개(스레드가 아니라 `python step7_redis_token_process.py` 두 번을
      거의 동시에 실행)로 RedisTokenDaemon.get_token()을 호출 — 실제 KIS 발급 호출은 한쪽
      프로세스에서만 1회 발생(0.09초), 다른 프로세스는 발급 시도 없이 캐시 폴링만으로 동일
      토큰을 받음. 지난 세션에 실제로 겪었던 "검증 스크립트를 프로세스 두 개로 나눴다가
      403" 문제가 이 컴포넌트로 실제 해결됨을 확인. 이어서 RedisRateLimiter까지 물린
      KISRestClient로 get_balance() 3연속 호출 → 전부 rt_cd=0, 호출 간격 2.61s/2.75s(최소
      1.0s 이상 — 페이싱 정상). 남은 갭: 3개 이상 프로세스 동시 경쟁, 장시간 운영 중 TTL 만료
      경계 상황은 미실측(capability_matrix.md 참고).
- [x] WS 시세 구독(ws_client.py) 실측 (2026-07-22 완료) — websockets 패키지 설치(이미 pyproject
      ui extras에 선언돼 있었음, 신규 의존성 아님). ApprovalKeyIssuer로 실제 접속키 발급 →
      REAL_WS_DOMAIN에 실제 연결 → 미니선물 근월(A05608) H0IFCNT0 구독 → 5건 수신(1건은 JSON
      구독응답 "SUBSCRIBE SUCCESS", 4건은 파이프구분 실시간 틱) → 구독 해제까지 전부 버그 없이
      1회 성공. listen()의 "{ 로 시작하면 JSON, 아니면 파이프구분" 분기가 실제 KIS 응답과
      정확히 일치함을 확인. 틱의 HHMMSS 필드가 수신 당시 KST 벽시계와 일치 — 지연 없는 실시간
      확인. ws_client.py listen() docstring에 실제 원시 메시지 샘플 기록. 남은 갭: 파이프구분
      필드의 실제 의미(체결가·거래량 등) 파싱은 아직 없음(Normalizer 미착수), 재연결·장시간
      연결 유지 미검증, encrypt="Y" TR(체결통보 등)의 iv/key 복호화 경로 미검증
      (capability_matrix.md 참고). 이걸로 W3~5 "브로커 API 연동"은 사실상 마무리 — 다음은
      마스터 플랜 Ver 2.0 §9 W3~5의 나머지 절반인 L1 Collector→Normalizer→Archiver(Parquet)
      골격 착수.
- [x] L1 Collector→Normalizer→Archiver(Parquet) 골격 — 미니선물 실시간체결가 단일 심볼
      (2026-07-22 완료, 계획 문서 기반 진행) — src/messiah/data/{normalizer,archiver,collector}.py.
      마흐디 mahdi/main.py(_parse_tick/_parse_futures_tick/run_observation_loop)와
      mahdi/data/collector.py(MinuteBarAggregator)를 이식하되 messiah Tick/BarClosed 스키마에
      맞춤(bid/ask·OFI/VWAP 등은 messiah 스키마에 없어 제외 — L2 Feature Engine 몫). 필드 인덱스는
      WS 실측 세션에서 실제 캡처한 라이브 H0IFCNT0 프레임을 테스트 픽스처로 재사용해 symbol/시각/
      가격/거래량이 맞음을 재확인. 부산물 발견: Windows에 `tzdata` 패키지가 없으면 polars가
      tz-aware datetime을 Parquet에서 다시 읽을 때 ZoneInfoNotFoundError — pyproject.toml에
      `sys_platform=='win32'` 조건부 의존성으로 추가(messiah 최초의 플랫폼 조건부 의존성).
      TickCollector(단일 연결·단일 심볼)는 mahdi가 run_observation_loop/
      run_observation_loop_forever로 나눈 것과 같은 설계로 전자만 구현 — 재연결은 의도적으로
      범위 밖. core/logging.py에 CollectorProcessingError 태그 신규 등록(완성봉 적재/버스 발행
      실패 시 로깅만 하고 WS 루프는 계속, L22). 테스트 25건(normalizer 13·archiver 6·
      collector 6), 전부 FakeConnection/실캡처 픽스처 기반 — 실제 KIS WS로 TickCollector
      자체를 돌려본 적은 없음(ws_client.py는 별도 실측 완료, 그 위에 조립한 이 클래스는 미실측).
      의도적으로 미룬 것: ATM±N 옵션 체인 구독 롤링(RollingSubscriptionManager 이식), WS
      재연결(exponential backoff), REST 폴링 루프(투자자매매동향·옵션체인 그릭스,
      FixedTickScheduler 첫 실사용처가 될 것), Event Calendar(KRX 휴장일), 원시 틱 자체의
      Parquet 적재(완성봉만 적재 — 호가 기반 체결 재생은 별도 설계 필요). 상세는
      capability_matrix.md "L1 Data" 섹션 참고.
- [x] TickCollector 실제 KIS 서버로 end-to-end 실측 (2026-07-23 완료) — approval_key 발급→
      실제 WS 연결→미니선물 근월(A05608, 마스터파일 재조회로 확인) 구독→20초간 실틱 64건 수신→
      Normalizer 파싱→1분봉 2개 완성(quality_ok 둘 다 true, 거래량 73·30건)→Archiver 적재까지
      전부 버그 없이 1회 성공. **Redis 버스 발행도 이번에 처음 실측** — 별도 실제 구독자가
      TickCollector가 발행한 Tick 메시지 64건을 실시간으로 수신 확인(core/bus.py MessageBus의
      실제 Redis 연동 자체가 이 프로젝트 최초 실측 — 지금까지는 코덱 단위 테스트만 있었음).
      CollectorProcessingError 로그 0건. 남은 갭: 20초·저활동 구간만 봐서 장시간 운영·거래량
      급증 시 성능/안정성, WS 재연결, 옵션 WS 경로는 여전히 미검증(capability_matrix.md 참고).
- [x] WS 재연결(run_forever) 구현 + 옵션 WS 경로 실측 (2026-07-23 완료) — 위 세 항목 중 두 개를
      마저 메꿈. **TickCollector.run_forever() 신규 구현**(collector.py) — run_once()를 감싸
      지수 백오프(5→60초, mahdi run_observation_loop_forever와 동일 설계)로 WS 단절 시
      재연결. 재연결마다 approval_key 재발급, on_connected 훅으로 재연결 성공 감지·백오프
      리셋, CollectorWSDisconnected/CollectorWSReconnected 로그 태그 신규 등록. 단위 테스트
      8건(연결 자체 실패·구독 후 즉시 단절·백오프 배증/리셋 mock으로 커버) 전부 통과, 기존
      135건 회귀 없음. 이어서 **실제 KIS 서버로 강제 단절→재연결 실측**: A05608 구독 90초
      실행 중 t=30s에 실제 WS 연결을 코드로 강제 종료 → 3초 후 실제 재연결 성공(재발급된
      approval_key로) → 수신 재개, 전후 합계 257틱, 재연결에 걸친 분봉도 정상 적재. **옵션
      WS(H0IOCNT0) 경로도 이번에 처음 실측** — 정규월물은 당일 거래량이 너무 얇아(누적
      0~23건) 여러 종목·최대 3분 구독해도 틱을 못 잡았으나, 위클리 목요일물(오늘이 만기일이라
      거래량 폭증, 당일 누적 최대 12,464건)로 바꿔 45초 만에 실틱 70건 이상 확보 —
      normalizer.parse_option_tick의 필드 인덱스(symbol/시각/가격/거래량)를 원시 프레임과
      직접 대조해 전부 확인. 부산물 발견(신규 갭): **같은 계좌로 WS 연결 두 개(선물+옵션)를
      동시에 열면 서로 반복 단절됨**(각각 단독 실행 시엔 문제없음) — 원인 미확정이나
      approval_key 재발급이 같은 계좌의 다른 세션을 무효화하는 것으로 추정. 여러 종목을
      동시에 구독하려면 연결을 여러 개가 아니라 하나(KISWebSocketClient 하나에 subscribe()
      여러 번)로 묶어야 함이 실측으로 확인됨 — ATM±N 옵션 체인 구독 롤링 설계 시 반드시 반영.
      남은 갭: 장시간(수 시간) 연속 운영, 실제 거래량 급증(장 시작 직후 등) 구간, 3회 이상
      연속 재연결은 여전히 미검증(capability_matrix.md 참고).

## W6~8 (시간 바 생성 + Feature Engine 골격 + PX 핵심 30개)

- [x] 다중 Horizon 완성봉 합성 + Feature Engine 골격 + PX 30개 (2026-07-23 완료) —
      **src/messiah/data/bar_composer.py 신규**: MultiHorizonBarComposer가 1분봉을 구독해
      3/5/10/15/30분봉을 합성(OHLCV는 구성 1분봉들만으로 정확히 재구성). 봉 확정은 "다음
      1분봉 도착"이 아니라 FixedTickScheduler(기존 검증된 컴포넌트 재사용)로 절대시각 경계
      +500ms 유예 기반(완성봉 규율, Ver 1.2 §2.2) — 조용한 구간에서 경계가 밀리는 문제를
      원천 차단. 부산물 버그 수정: ParquetArchiver 경로·dedup 키에 horizon이 없어 서로 다른
      Horizon의 봉이 같은 bar_open_kst를 가지면 서로 지우던 문제 발견·수정(경로를
      `{symbol}/{horizon}/{date}.parquet`로). **src/messiah/features/ 신규 패키지**:
      px_core.py에 PX(가격·추세·모멘텀) 기저 Feature 30개 전부 구현(Ver 1.4 §2.2) — 전부
      완성봉 OHLCV만으로 계산 가능. engine.py의 FeatureEngine이 `bar.{h}.{symbol}` 구독→
      Horizon별 롤링윈도우→30개 계산→FeatureVector 조립·발행(`feat.{h}.{symbol}`)까지 연결.
      **MS(마이크로구조) 30개는 이번 스코프 밖**(대부분 호가 WS 필요, MESSIAH는 아직 미구독 —
      capability_matrix.md 신규 갭 항목 참고, 사용자와 합의해 범위 확정). 단위 테스트 76건
      신규(bar_composer 12·px_core 53·engine 11), 기존 포함 전체 212건 통과. 실측 중 버그
      1건 발견·수정: px_hurst의 R/S 회귀가 log(size) 실값이 아니라 등간격 인덱스로 회귀해
      기울기가 왜곡되던 문제 — 별도 페어(x,y) 회귀 헬퍼로 분리해 수정. 실측: 오늘 세션에서
      실제 KIS WS로 캡처해둔 진짜 1분봉(서로 다른 시점, 갭 있음)을 파이프라인 전체(합성→
      Feature 계산→발행)에 흘려 실제 FeatureVector 산출 확인 — 데이터가 적어 대부분
      워밍업 미달이었지만 조건을 채운 Feature(px_ret_5 등)는 실제 가격 변동과 일치하는 값을
      정상 산출. 남은 갭: MS 30개, EV(시간·이벤트) 14개, Feature 선정 절차(Ver 1.5 §5, Triple
      Barrier 레이블 필요 — Phase 2 W12~13 이후)는 capability_matrix.md에 기록.
- [x] L1 파이프라인 일일 운영 진입점 (2026-07-24 완료) — **scripts/run_l1_daily.py 신규**:
      장전 웜업(self_check→근월물 심볼 확인→Redis 연결→Collector/Composer/Engine 구성 및 WS
      연결까지 미리 끝냄, "첫봉 대기 준비완료")→정규장 수집(09:00~15:35 KST)→daily_close(미완성
      봉 flush·버스 종료, 15:40 안전판 데드라인)까지 사용자와 합의한 시간대로 구현.
      scripts/run_l1_daily.bat(Windows 배치 래퍼)도 준비 — **작업 스케줄러 등록은 보류**(매일
      무인 실제 API 호출 자동화라 사용자 확인 후 별도 진행). 실측: 세션-스톱 시각을 20초 뒤로
      패치해 웜업→실제 WS 연결→실틱 수신→1분봉 완성→3/5/10/15/30분봉 합성→FeatureVector
      발행→daily_close→정상 종료(exit 0)까지 전체 생애주기를 실제 KIS 서버로 1회 확인,
      `data/bars/A05608/{1m,3m,5m,10m,15m,30m}/2026-07-24.parquet` 전부 정상 생성. **버그 5건
      발견·수정**: (1) `websockets`가 core 런타임 의존성인데 pyproject.toml에 `ui` extras로
      잘못 분류돼 있어 base 의존성만 설치한 무인 운영용 venv에서 ImportError로 죽었을 뻔함 —
      core dependencies로 이동, (2) `.bat` 파일에 한글 주석을 UTF-8로 저장하면 cmd.exe가
      시스템 로캘(cp949)로 잘못 해석해 배치 자체가 아예 안 돌아감(실측으로 발견) — 전부 영문
      주석으로 재작성(교훈: Windows 배치파일은 ASCII만), (3) self_check.py를 서브프로세스로
      호출할 때 `subprocess.run(text=True)`가 인코딩 미지정이라 self_check의 UTF-8 출력을
      cp949로 디코딩하려다 UnicodeDecodeError — encoding="utf-8" 명시로 수정, (4) 최초 버전이
      `>> 로그파일 2>&1`로 전부 파일에만 리다이렉트해 cmd 창에 아무 것도 안 보임(사용자 실사용
      중 발견) — PowerShell 경유로 콘솔에도 동시에 뿌리게 수정 + `chcp 65001`로 콘솔 코드페이지
      전환, (5) 그 수정에 처음 쓴 `Tee-Object -Encoding utf8`이 Windows PowerShell 5.1엔
      `-Encoding` 파라미터가 없어 즉시 실패, 파라미터 없이 쓰면 `-FilePath` 출력이 기본
      UTF-16LE로 저장됨(둘 다 실측) — `Out-File -Encoding utf8` 줄 단위 수동 tee로 교체해 해결.
      남은 갭: 작업 스케줄러 미등록, KRX 휴장일 인식 없음(Event Calendar 미구현), 옵션(K200_OPT)
      동시 수집 미지원(같은 계좌 WS 연결 2개 열면 서로 끊기는 문제, 별도 재설계 필요) —
      capability_matrix.md에 기록.
- [x] 실제 운영 로그 리뷰 반영 (2026-07-24 완료) — 사용자가 실제 run_l1_daily.bat 실행 로그를
      직접 리뷰하며 발견한 두 가지 반영. (1) **로그 ts를 UTC→KST로 전환**
      (core/logging.py JsonFormatter) — 내부 표준(BusMessage.ts_utc 등)은 여전히 UTC(SYSTEM.md
      R3), 사람이 읽는 로그 표시만 KST로 바꿈(ISO8601 오프셋이 그대로 찍혀 혼동 소지 없음).
      (2) **FeatureNaN 경고가 워밍업 중에도 매 봉마다 찍혀 잡음이 될 뻔함** — 30m처럼 최대
      윈도우를 채우는 데 며칠씩 걸리는 Horizon은 그동안 nan_ratio가 계속 높은 게 정상인데
      그때마다 WARNING을 찍으면 agenda.py 주간 경보 집계가 파묻힘 — `len(history) >=
      _MAX_HISTORY`("워밍업이 끝났어야 할 시점")를 넘긴 뒤에도 nan_ratio가 높을 때만 경고하도록
      FeatureEngine 수정, 회귀 테스트 2건 추가(213개 전체 통과). 실측 중 부산물 발견: 같은
      계좌 WS 연결이 **1개뿐인데도** 20초 안에 5회 연속 단절 재현(2026-07-23엔 "동시 연결 2개"가
      원인으로 추정됐었는데 이번엔 그 조건이 없었음) — approval_key 재발급 빈도 제한
      가능성으로 새로 추정, 확정 실측은 API를 더 두들기지 않기 위해 보류(capability_matrix.md
      "알려진 갭"에 기록).
- [x] 데일리 종료 안전망 워치독 (2026-07-24 완료) — **scripts/stop_l1_daily.bat 신규**.
      run_l1_daily.py 자체의 daily_close()·15:40 안전판과는 독립된 별도 워치독 —
      사용자가 제시한 mahdi 프로젝트의 실제 종료 배치(2026-07-21 사고 대응 이후 버전)를
      템플릿으로 삼되, MESSIAH엔 아직 창 제목 있는 프로세스가 없어(Command Center UI는
      Phase 4) 커맨드라인 매칭(`*run_l1_daily.py*`)만 채택. **버그 2건 발견·수정**(둘 다
      실제 잔존 프로세스로 실측 중 발견): (1) 파일에 실수로 한글 텍스트 한 줄이 남아있어
      run_l1_daily.bat와 동일한 cp949 오분석 문제 재발 — 바이트 단위 ASCII 검증으로 확인 후
      제거, (2) `$procs | Stop-Process -Force`(파이프)가 에러 없이 조용히 아무것도 안 죽임 —
      Win32_Process CIM 객체는 `ProcessId` 속성인데 Stop-Process 파이프 바인딩은 `Id`를
      찾음 — 로그엔 "종료함"이라 찍히는데 실제로는 프로세스가 계속 살아있는 상태였음(직접
      확인). `foreach` 안에서 `Stop-Process -Id $p.ProcessId` 명시로 수정.

## W9~11 (Digital Twin 시뮬레이터 — Phase 2 착수)

- [x] Parquet 재생 + 1분봉 기반 체결 모사 + InProcessBus + DigitalTwinEngine (2026-07-26 완료) —
      **스코프 확정**: Ver 1.0.1 §2.1 원안("호가창 수준 재생 + 시장충격 모사")은 호가 WS 미구독
      (기존 알려진 갭)으로 이번엔 불가능 판단 — 이미 아카이브된 완성봉(최소 단위 1분봉)의
      고가/저가 터치로 지정가 체결을 판정하는 근사로 대체, capability_matrix.md에 스코프
      축소 사유 명기.
      **src/messiah/simulator/replay.py 신규**: ParquetBarReplaySource — `{symbol}/{horizon}/
      {date}.parquet`를 전 Horizon 읽어 확정시각(bar_open+horizon초, 동률이면 짧은 Horizon
      먼저 — 실제 운영에서 굵은 봉은 구성 1분봉들이 전부 확정된 뒤에야 확정되므로 그 인과
      순서를 재생도 지킴) 순으로 정렬.
      **src/messiah/simulator/inprocess_bus.py 신규**: InProcessBus — `core.bus.MessageBus`와
      같은 publish/subscribe 시그니처의 인메모리 버스. FeatureEngine을 코드 변경 없이 그대로
      재사용해 "동일 인터페이스" 원칙(Ver 1.0.1 §2.1)을 실증.
      **src/messiah/broker/simulator/adapter.py 재작성**: 기존 "즉시체결" 골격(W1 임시
      구현, 자체 docstring에 "W9~11에서 확장 예정"이라 명시돼 있었음)을 pending 지정가
      등록·매 1분봉 터치 체결(체결가=지정가, 보수적 가정)·TTL 자동취소·시장가 최근종가±
      슬리피지로 재작성. **버그 1건 발견·수정(실측 아님, 코드 리뷰 중 자체 발견)**: `_apply()`가
      포지션 갱신 가격을 `req.limit_price_ticks`에서 가져왔는데 시장가 주문은 이 필드가 None이라
      `avg_price_ticks=0`으로 잘못 기록될 뻔함 — 실제 체결가(price_ticks)를 명시적으로 전달하도록
      수정.
      **src/messiah/simulator/engine.py 신규**: DigitalTwinEngine — 재생봉을 버스에 발행 →
      SimBroker.on_bar()로 체결 판정 → 새 Fill을 OrderGateway.on_fill()로 전달, 순서 고정(발행이
      체결 반영보다 먼저 — FeatureEngine이 그 봉을 본 뒤에야 그 봉으로 인한 체결도 반영돼야
      인과관계가 실제 운영과 같음).
      **scripts/run_replay.py 신규**: 수동 스모크 진입점(run_l1_daily.py 패턴 준용). 실측: 실제
      2026-07-24 아카이브(A05608, 6개 Horizon 총 60행)로 전체 배선 end-to-end 실행 — 재생→
      FeatureEngine이 실제 아카이브 행 수와 정확히 일치하는 FeatureVector 발행(1m 33·3m 11·
      5m 7·10m 5·15m 3·30m 1)→데모 시장가 주문 제출·체결(SIM00000001)→최종 포지션 qty=1
      확인, 버그 없이 1회 성공.
      **테스트 27건 신규**(SimBroker 10·replay 5·inprocess_bus 4·engine 4, 그리고 SimBroker
      계약 변경으로 tests/test_core_w1.py 기존 2건이 "봉 1개로 시계 프라이밍" 필요하게 바뀌어
      반영), 회귀 없음(전체 236건 통과). ruff/pyright 통과(신규 파일 기준 — 리포 전체 I001
      경고는 기존부터 있던 별개 항목, 이번 변경과 무관).
      남은 갭(capability_matrix.md 기록): 호가 기반 체결 미구현(호가 WS 선행 필요), TTL이
      1분봉 단위로만 판정(체결이 TTL보다 항상 우선), 부분체결 미모델링, 전략(Expert) 레이어
      없음(Phase 3 이전이라 당연함 — Triple Barrier/Walk-Forward CV는 W12~13, 첫 Expert는
      W14~16 이후).

## W12~13 (Triple Barrier·uniqueness·Walk-Forward/Purged CV 프레임)

- [x] labeling.py + cv.py 신규, px_core.py ATR 공개화 (2026-07-26 완료) —
      **src/messiah/features/px_core.py**: 기존 `_atr`/`_true_ranges`(모듈 내부 전용)를
      `atr`/`true_ranges`로 공개 전환 — models/labeling.py의 배리어 폭 계산이 재사용(ATR
      로직 중복 방지). 리네임 과정에서 **버그 1건 발견·수정**: 단순 문자열 치환으로
      `atr = atr(bars, window)` 형태의 지역변수 할당이 8곳 생겨 함수명을 그 스코프 안에서
      가려버려 UnboundLocalError가 날 뻔함(파이썬은 함수 내 어디서든 대입이 있으면 그 이름
      전체가 지역변수로 취급됨) — 지역변수를 `atr_val`로 전부 개명해 해결, 기존 53개 회귀
      테스트로 확인.
      **src/messiah/core/messages.py**: `bar_confirm_time(bar)` 공용 헬퍼 신규(봉 확정시각
      = bar_open_kst + Horizon초) — simulator/replay.py의 중복 로컬 함수를 이걸로 교체
      (재사용 정리), models/labeling.py도 이걸로 진입·판정 시각을 정렬.
      **src/messiah/models/labeling.py 신규**: Ver 1.6 §7.1이 명시한 파일명 그대로.
      `triple_barrier_labels()` — Ver 1.2 §3.2 Horizon별 표(시간배리어 봉수·ATR 폭 배수)를
      전부 인코딩, 동일 봉 동시터치 시 상단 우선(결정론적 타이브레이크, 근거를 모듈
      docstring에 명기), 비용반영 강등(`cost_ticks` — Cost Model v1 나오기 전 호출자
      전달값), ATR 워밍업·시간배리어 꼬리 트림된 진입은 레이블 자체를 안 만듦(결측 채우기
      아님). `compute_uniqueness()` — Lopez de Prado 평균 고유도. **버그 1건 발견·수정
      (테스트 작성 중 자체 발견, 실측 아님)**: 최초 구현은 격자점을 레이블들의 t_start만
      사용했는데, 손으로 계산한 known-value 테스트(3이벤트 겹침 사례)를 돌려보니 시계열
      꼬리에서 어떤 레이블의 t_end가 다른 레이블의 t_start와 우연히도 안 맞아떨어지는
      경우(그 시점 봉이 자기 자신은 진입 후보가 못 됐던 경우) 동시성이 과소평가됨을 발견 —
      격자를 t_start∪t_end 합집합으로 바꿔 해결(동시성은 구간 경계에서만 바뀌는 계단함수라
      이 합집합이 수학적으로 정확한 격자). `label_and_weight()` — 위 둘을 합성한 Trainer
      2단계 전체 편의 함수.
      **src/messiah/models/cv.py 신규**: `PurgedKFold` — de Prado(2018) Ch.7 표준 알고리즘
      (순수 Python, numpy 의존성 안 늘림) — Optuna 탐색용 "Purged 5-Fold"(Ver 1.6 §2.2).
      `WalkForwardSplitter` — Ver 1.2 §8.2 "학습 6개월/검증 1개월, 1개월씩 전진" 스킴을
      달력일 파라미터(train_days/test_days/embargo_days/step_days)로 일반화, Purge(배리어가
      검증 구간을 침범하는 학습 샘플 제거)와 Embargo(검증 직전 N일 추가 제외) 둘 다 구현.
      두 클래스 모두 `(t_start, t_end)` 튜플 시퀀스만 다뤄 labeling.py에 의존하지 않음
      (`[(l.t_start, l.t_end) for l in labels]`로 바로 연결).
      **scripts/run_labeling_smoke.py 신규**: 실제 2026-07-24 아카이브(A05608, 1m, 33행)로
      레이블링→고유도→PurgedKFold 전체 배선 end-to-end 실행 — 레이블 16건(−1:6, +1:10),
      비용강등 0건, 고유도 평균 0.503, PurgedKFold(5-fold) 정상 분할, 버그 없이 1회 성공.
      WalkForwardSplitter는 아카이브가 하루치뿐이라 이 스크립트로 의미 있게 시연 불가 —
      정확성은 합성(30~60일) 데이터 기준 단위 테스트가 담당(capability_matrix.md 갭 기록).
      **부수 발견**: `[tool.pyright]`에 `pythonVersion` 미지정 상태였음(project는 3.11+
      확정인데) — cv.py의 `Sequence[tuple[datetime, datetime]]` 타입 별칭에서 pyright가
      builtin 제네릭 구독을 구버전 기준으로 오탐(실제로는 3.9+ 전부 안전, .venv도 3.12) —
      pyproject.toml에 `pythonVersion = "3.11"` 명시로 해결(다른 파일에서도 향후 같은
      오탐 재발 방지).
      테스트 39건 신규(labeling 12·cv 13·기존 px_core 53건은 리네임 회귀 확인용 그대로 재실행
      — 새로 센 건 아님), 전체 261건 통과. ruff/pyright(models 패키지 기준) 클린.
      남은 갭(capability_matrix.md 기록): WalkForwardSplitter 다개월 실데이터 미실측,
      KRX 휴장일 미인식(달력일 기준), cost_ticks는 Cost Model v1 나오기 전 임시 호출값,
      ATR 윈도우(14) 근거 미확정(Ver 1.5 §5 Feature 선정과 함께 재검토 대상).

## W14~16 (Cost Model v1 + Validator 골격 + 5m Expert 프로토타입 1호)

- [x] risk/cost_model.py + strategy/futures/expert.py + models/{trainer,metrics,validator}.py
      신규 (2026-07-26 완료) —
      **ml extras(lightgbm/scikit-learn/numpy) 최초 설치** — 여태 선언만 돼 있었고
      실제 설치된 적 없었음. 설치 직후 `lgb.Dataset` 생성이 항상 access violation으로
      죽는 심각한 버그 발견: lightgbm 4.7.0 + numpy 2.5.1 + Python 3.12(이 프로젝트
      .venv) 조합의 Windows 휠 문제로 판명(데이터 크기·내용 무관, 재현율 100%). numpy를
      1.26으로 내리면 이번엔 scipy가 깨지는 별개 사고 발생 — lightgbm을 4.3.0으로
      내려서(numpy는 2.5.1 유지) 해결, `pyproject.toml` ml extras를 `lightgbm>=4.3,<4.7`로
      상한 고정(capability_matrix.md에 재현 시나리오 상세 기록 — 향후 버전 올릴 때 반드시
      `tests/strategy/futures/test_expert.py`로 먼저 검증할 것).
      **src/messiah/risk/cost_model.py 신규**: CostModel — Ver 1.1 §4-1 4요소(수수료+세금+
      슬리피지+시장충격) 구현. 시장충격은 완성봉의 실제 volume으로 계산(구조적으로 정확),
      슬리피지는 호가 WS 미구독으로 설정값 근사(알려진 갭, 기존 MS Feature 갭과 동일 원인).
      **models/labeling.py 소폭 확장**: `cost_ticks: int`(W12~13 임시값)을 `float`로 넓혀
      CostModel의 소수점 틱 출력을 그대로 받게 함 — 결선은 trainer.py가 실제로 함.
      **src/messiah/strategy/futures/expert.py 신규**: HorizonExpert — Ver 1.2 §9 스켈레톤의
      5m 프로토타입 1호(단일 LightGBM 3-class, 미니 앙상블·Meta-Labeler·Isotonic 교정·
      Optuna 탐색은 전부 W17~19 정식 스코프로 명시적으로 미룸). `core/logging.py`에 W1부터
      등록만 되고 한 번도 안 쓰인 `FeatureSetMismatch` 태그를 `predict()`에서 처음 실사용.
      **src/messiah/models/trainer.py 신규**: Ver 1.6 §7.1 파이프라인 1~3단계(데이터준비·
      레이블생성·학습)만 구현 — 봉을 실제 운영과 동일한 FeatureEngine(지난주 만든
      simulator.InProcessBus 재사용)에 직접 흘려 FeatureVector를 얻고, CostModel→
      label_and_weight로 레이블을 만들어 bar_confirm_time으로 정렬 매칭, 클래스불균형
      (inverse-frequency)×고유도 가중치를 조립해 HorizonExpert.train() 호출까지.
      **src/messiah/models/metrics.py 신규**: sharpe_ratio·max_drawdown·
      negative_window_ratio·multiclass_brier_score — 전부 순수 함수, labeling.py에
      의존 안 하는 cv.py와 같은 설계 원칙.
      **src/messiah/models/validator.py 신규**: Validator — Ver 1.2 §8.3 성과 관문 3종
      (Deflated Sharpe 제외 — 시행횟수 보정할 Optuna 탐색 기반 정식 Trainer가 없어서,
      W17~19 이후) + Ver 1.6 §8 추가검사 4종(교정 Brier·Feature 의존도·추론지연·직렬화
      왕복) 전부 구현.
      **부수 발견**: `models/trainer.py`가 `FeatureEngine`에 `InProcessBus`를 넘기자
      pyright가 처음으로 "MessageBus 구체클래스와 불일치" 오류를 냄(런타임은 이미 W9~11
      `scripts/run_replay.py`부터 같은 패턴을 썼지만 scripts/는 pyright 검사 대상 밖이라
      안 드러났었음) — `core/bus.py`에 `BusLike` Protocol(publish/subscribe만 요구)을
      신설해 `FeatureEngine.__init__`의 타입힌트를 이걸로 교체, 런타임 변화 없이 타입
      수준에서도 "동일 인터페이스"(Ver 1.0.1 §2.1) 원칙을 명시.
      **scripts/run_expert_training_smoke.py 신규**: 실제 2026-07-24 아카이브(A05608, 5m,
      7행)로 Trainer→HorizonExpert→Validator(모델 검사 3개 관문 — Feature 의존도·추론지연·
      직렬화) end-to-end 실행 확인, 버그 없이 1회 성공. 성과 관문·교정 관문은 의도적으로
      생략(백테스트 인프라·홀드아웃 데이터 부재, 스크립트 docstring에 명시). 데이터가
      7행뿐이라 Feature 의존도가 전부 0(트리가 유의미하게 못 갈라짐)이었던 것도 예상된
      결과 — 목적은 배관 검증이었고 달성함.
      테스트 58건 신규(cost_model 10·expert 7·metrics 15·validator 14·trainer 12), 전체
      319건 통과. ruff 클린, pyright는 models/risk/strategy 패키지 기준 클린(engine.py/
      trainer.py의 Handler 분산성 경고는 W9~11부터 있던 기존 패턴 — 신규 아님).
      남은 갭(capability_matrix.md 기록): Cost Model 수치 전부 미실측, 5m Expert 예측력
      없음(의도됨, 데이터 부족), 미니 앙상블·Meta-Labeler·교정·Optuna 미구현(W17~19),
      Deflated Sharpe 미구현, Validator 성과 관문 실제 백테스트 미실행(백테스트 하니스
      자체가 아직 없음), top_features는 전역 근사(로컬 XAI는 W24~26 재검토).

## W17~19 (5m Expert 정식 — 탐색·앙상블·교정 + Meta-Labeler)

- [x] models/{search,calibration}.py + strategy/futures/{expert 재설계,meta_labeler}.py +
      models/trainer.py 확장 (2026-07-26 완료) —
      **optuna 신규 설치·확인**: 지난주 lightgbm 4.7.0 크래시 사고 이후 습관대로 최소
      스모크(`create_study().optimize()`) 먼저 실행해 확인 — 문제 없음, `ml` extras에
      `optuna>=3.6` 추가.
      **src/messiah/models/search.py 신규**: `search_hyperparameters()` — Ver 1.6 §2.2
      탐색공간(num_leaves/max_depth/min_data_in_leaf/learning_rate/feature_fraction/
      bagging_fraction/lambda_l1/l2) 원문 그대로, Optuna(TPE) + W12~13에 만든
      `PurgedKFold`로 "창 내부 CV로만 탐색" 실제 구현. early_stopping은 폴드 내부
      홀드아웃 추가 분리의 복잡도 대비 실익이 작아 스코프 제외(갭으로 기록).
      **src/messiah/models/calibration.py 신규**: `ProbabilityCalibrator`(클래스별
      Isotonic + 재정규화, Ver 1.6 §6.1) + `ConformalCalibrator`(비적합도 분위수 메커니즘,
      Ver 1.6 §6.2 — 실제 운영 이력이 없어 메커니즘만 구현, G2부터 실사용).
      **src/messiah/strategy/futures/expert.py 재설계**: 단일 LightGBM → Ver 1.6 §2.3
      미니 앙상블(×5, seed만 다름). `predict()`가 확률 평균 + **P(+1) 표준편차**(Ver 1.2
      §6 원문 그대로, 다른 클래스 아님)를 `ens_std`로. `ProbabilityCalibrator` 선택적
      부착(`set_calibrator()`). 저장/로드를 앙상블 멀티파일(`{stem}_e{i}.lgb`)+메타데이터
      (`.json`)+교정기(`.pkl`)로 확장 — 기존 저장 포맷 하위호환 없음(W14~16 프로토타입은
      저장된 모델 자산이 없어 마이그레이션 불필요 확인 후 진행).
      **src/messiah/strategy/futures/meta_labeler.py 신규**: `MetaLabeler`(Horizon별 얕은
      LightGBM 이진분류, depth≤4·leaves≤15, Ver 1.2 §5.2) + `select_threshold()`(Ver 1.6
      §5.2 "비용차감 후 기대수익 최대화" 그리드서치, 정확도 최대화 아님) + 메타 Feature
      5개(1차확률·마진·앙상블분산·실현변동성 근사·시간대) — Regime·스프레드·이벤트근접도는
      각각 W20~21·호가WS·Event Calendar 미구현이라 제외(명시적 갭).
      **src/messiah/models/trainer.py 확장**: `generate_out_of_fold_predictions()` —
      Ver 1.6 §5.1 "1차 모델을 Walk-Forward로 가상 운용 → out-of-fold 신호만 수집"을
      `PurgedKFold`로 실제 구현(**W12~13에 만든 CV 인프라의 첫 실사용처** — 지난주까지는
      합성 테스트 데이터로만 검증됐었음). `train_formal_expert()` — 탐색→out-of-fold→
      최종 앙상블 전체데이터 재학습→교정 부착→Meta-Labeler 학습+임계값선택 전체
      오케스트레이션, `ExpertTrainingResult` 반환. out-of-fold 신호 0건이면 조용히 빈
      Meta-Labeler를 만드는 대신 ValueError(정식 경로는 칸닝 방지가 실제로 작동했다는
      보장이 핵심). `train_prototype_expert()`(W14~16)는 그대로 유지 — 빠른 배관 확인용.
      **scripts/run_formal_expert_training_smoke.py 신규**: 실제 아카이브(A05608, 5m,
      7건)로 먼저 시도 → 예상대로 "데이터 부족" 실패(정직하게 보고) → 200건 합성(사인파+
      지터) 데이터로 전체 파이프라인 실행 확인(탐색→out-of-fold 192건→Meta-Labeler
      학습·임계값 0.9→앙상블+교정기→예측→Meta-Labeler 통과판정까지). 순수 사인파(지터
      없음)는 여러 지표의 롤링 표준편차가 0이 되는 퇴화 케이스가 잦아 FeatureNaN 경고가
      대량 발생 — 작은 난수 지터 추가로 완화(완전히 없애진 못함, 잔여 경고는 정직한 신호로
      남겨둠).
      테스트 41건 신규(search 5·calibration 9·expert 15(전면 재작성)·meta_labeler 14·
      trainer 7 추가), 전체 362건 통과. ruff 클린, pyright는 models/strategy 패키지
      기준 클린(lightgbm/optuna 미해석은 기존 polars/redis 패턴과 동일한 pyright venv
      미탐지 이슈 — 신규 아님, trainer.py의 Handler 분산성 경고도 W9~11부터 있던 기존
      패턴).
      남은 갭(capability_matrix.md 기록): Meta-Labeler Regime/스프레드/이벤트근접도
      미구현, 실현변동성은 ATR 대신 px_bb_width_20 근사, early_stopping 미구현,
      ConformalCalibrator 실사용 이력 없음(G2부터), Deflated Sharpe·실제 백테스트 성과
      관문 미실행(기존 갭 유지), 5m Expert 예측력 검증 불가(데이터 부족, 기존 갭 유지),
      정식 번들 포맷(manifest.yaml) 여전히 미구현(Registry 없음).

## W20~21 (Regime AI — HMM + 규칙)

- [x] features/vl_core.py + strategy/regime/{hmm_model,naming,rules,service}.py 신규
      (2026-07-26 완료) —
      **src/messiah/features/vl_core.py 신규**: `vl_vol_ratio` — W_STD 앞 두 값(5, 20)
      윈도우 표준편차의 비율. 세 번째 값(60)은 30시간 웜업 비용이 커 제외. 모듈 docstring이
      처음엔 "W_STD의 최솟값/최댓값"이라고 잘못 써놨었는데(W_STD=(5,20,60)에서 20은
      중앙값이지 최댓값이 아님) 테스트가 `min(W_STD), max(W_STD)`로 60-윈도우를 요구해
      데이터 부족으로 실패 — docstring·테스트 둘 다 W_STD[0]/W_STD[1] 기준으로 수정.
      **src/messiah/strategy/regime/hmm_model.py 신규**: `RegimeHMM`(hmmlearn
      GaussianHMM 래퍼, BIC 최솟값으로 상태수 자동 선택) + `build_observations()`
      (px_trend_r2·vl_vol_ratio·px_autocorr 3개 Feature 조합, 신규 계산 없이 기존 Feature
      재사용).
      **src/messiah/strategy/regime/naming.py 신규**: `label_states()`(HMM 상태 index →
      Regime enum 통계적 매핑) + `describe_labels()`(사람 검수용 상태별 사후 통계 요약).
      **src/messiah/strategy/regime/rules.py 신규**: `RuleContext` + 규칙층 — 지금 살아있는
      규칙은 변동성 극단(vol_ratio 임계 초과 시 HIGH_VOL 강제, confidence=1.0) 1개뿐.
      이벤트 근접·세션 시가/종가 규칙은 Event Calendar 미구현이라 제외.
      **src/messiah/strategy/regime/service.py 신규**: `RegimeAI` — `fit()`(HMM 학습→명명)
      →`classify()`(윈도우 판정→규칙 오버라이드) 오케스트레이션, `RegimeState` 메시지 조립.
      `n_states`/`labels`/`hmm_model` 공개 프로퍼티(private 속성 직접 접근 방지 — 스모크
      스크립트에서 `regime_ai._model` 등으로 접근하던 걸 프로퍼티로 정리).
      **버그 발견·수정**: `classify()`가 꼬리에서 `window+1`봉만 잘라 썼는데 3개 관측
      Feature 중 `px_autocorr`만 `window+2`봉이 필요해(다른 둘보다 엄격) `build_observations()`
      가 항상 빈 결과를 반환 → `classify()`가 항상 UNKNOWN — 테스트로 발견,
      `min_length = window + 2`로 수정.
      **core/messages.py**: `RegimeState`(symbol/regime/confidence/state_duration_bars/
      transition_prob/rule_override/valid_until) 신규 — L3 Intelligence 섹션,
      `ExpertView` 앞.
      **pyproject.toml**: `ml` extras에 `hmmlearn>=0.3` 추가.
      **scripts/run_regime_ai_smoke.py 신규**: 실제 아카이브(A05608, 30분봉 1건)로 먼저
      시도 → 예상대로 "관측치 부족" 실패(정직하게 보고) → 추세상승/횡보/고변동성 3구간
      반복 합성 30분봉으로 전체 파이프라인(HMM 학습→BIC 상태수 선정→국면 판정→규칙
      오버라이드 시연→사람 검수용 요약) end-to-end 1회 성공.
      테스트 신규 다수(vl_core 4·hmm_model 8·naming 6·rules 5·service 9), 전체 406건
      통과. ruff 클린.
      남은 갭(capability_matrix.md 기록): HMM 실제 다개월 아카이브 미검증(합성 데이터만),
      규칙층 사실상 1개 규칙뿐, RegimeState는 어떤 운영 루프에도 아직 발행 안 됨(상시
      구동 배선은 W24~26 스코프), n_states 후보 범위 민감도 미검증, 온라인/점증 갱신 없음.

## W22~23 (15m·30m Expert — VL 확장 + FeatureEngine 버그 수정)

- [x] features/vl_core.py 확장(1→14) + FeatureEngine 결선 + deque 버그 수정 + M15/M30 검증
      (2026-07-26 완료) —
      **버그 발견·수정(이번 세션의 가장 큰 산출물)**: `features/engine.py`가 롤링 히스토리를
      `collections.deque`로 보관하는데 `px_core`/`vl_core` 계산기 다수가 `bars[-window:]`
      슬라이스를 쓴다 — `deque`는 슬라이스를 지원하지 않아(정수 인덱싱만 가능, 파이썬 표준
      동작) 슬라이스 쓰는 계산기는 전부 `TypeError`를 던졌고 `_safe_call`의 광범위
      `except Exception`이 조용히 None으로 삼켜 왔다. 실측으로 확인: 80봉 완전 워밍업
      후에도 82개 키 중 72개가 None — PX 30개 중 정수 인덱싱만 쓰는 소수(px_ret/px_mom/
      px_accel 등)를 제외한 대다수가 워밍업과 무관하게 항상 NaN이었다. W6~8 원 실측
      노트가 정확히 그 소수 사례만 확인해서 그동안 발견 안 됨. `handle_bar()`가 계산
      직전 `list(history)`로 변환해 해결(계산기 쪽은 원래도 `Sequence[BarClosed]` 계약대로
      짠 것이라 수정 불필요). 회귀 테스트 신규(`test_slice_based_calculators_produce_real_values_once_warmed`).
      **src/messiah/features/vl_core.py**: Ver 1.4 §2.3 VL 16개 중 OHLCV만으로 계산
      가능한 13개 신규(`vl_rv`·`vl_park`·`vl_gk`·`vl_yz`·`vl_atr`·`vl_atr_rel`·
      `vl_semi_dn`·`vl_semi_up`·`vl_semi_ratio`·`vl_jump`·`vl_range_exp`·`vl_vov`·
      `vl_squeeze`) — Ver 1.5 §3.5/3.6(15m/30m Expert의 VL 15% 배정) 대응.
      `WINDOWED_FEATURES`/`STATEFUL_FEATURES` 레지스트리를 px_core.py와 동일 형태로
      노출해 이번에 VL이 처음으로 실제 `FeatureVector`에 실린다(전엔 Regime AI 전용
      직접호출 경로뿐이었음). `vl_vov`/`vl_squeeze`는 이중 윈도우 구조라 하위윈도우를
      5로 낮춰(`_INNER_SUBWINDOW`) `engine._MAX_HISTORY`(130) 예산 안에 맞춤(표준 20을
      썼으면 W_SLOW 최댓값 120 기준 140>130으로 영원히 워밍업 안 끝나는 죽은 칸이 됐을
      것). `vl_har_pred`/`vl_intraday_shape`(다개월 시간대별 통계 필요)는 Event Calendar
      미구현과 같은 이유로 스코프 밖 유지.
      **src/messiah/features/engine.py**: vl_core 레지스트리 결선(카테고리별 루프 2줄
      추가, 본체 로직 변경 없음).
      **M15/M30 Expert 파이프라인 검증**: `HorizonExpert`/`Trainer`/`MetaLabeler`/
      `labeling.BARRIER_PARAMS`는 W14~19부터 이미 Horizon을 데이터로만 받는 설계였다 —
      이번 주는 그 일반성이 M15/M30에서도 실제로 성립함을 처음 못 박았다(Ver 1.2 §4.2
      구현 순서 "5m → 15m → 30m …" 대응). `tests/models/test_trainer.py`의 `_bars()`
      헬퍼가 M5가 아니면 무조건 1분 간격으로 봉을 만들던 버그도 발견·수정
      (`HORIZON_SECONDS[horizon]//60`으로 실제 Horizon 길이 반영 — 안 고쳤으면 M15/M30의
      시간배리어가 봉 간격보다 훨씬 길어 PurgedKFold가 사실상 전부 purge했을 것).
      `scripts/run_formal_expert_training_smoke.py --horizon 15m`/`--horizon 30m`으로
      실제 실행 확인 — 실제 아카이브(15m 3건/30m 1건)는 예상대로 데이터 부족 실패(정직
      보고), 합성 200봉으로는 5m과 동일하게 탐색→out-of-fold→앙상블+교정기→Meta-Labeler
      까지 end-to-end 성공.
      테스트 40건 신규(vl_core 36·engine 회귀 1·trainer M15/M30 파라미터화 4)+기존 수정
      3건(test_engine.py 모노키패치 2건에 vl_core 격리 추가, _EXPECTED_KEY_COUNT 재계산),
      전체 439건 통과. ruff 클린.
      남은 갭(capability_matrix.md 기록): 15m Expert는 여전히 Ver 1.5 배정(FL 30%·OP 10%·
      RG 10%)의 절반도 못 받음(FL/OP/RG 전부 미구현 — 투자자매매동향 폴링·옵션체인
      수집기·매크로 데이터 소스 전부 별도 착수 필요), 30m도 동일 이유로 배정
      (FL 20%·OP 20%·RG 20%) 미달, vl_har_pred/vl_intraday_shape 보류, Aggregator/Meta
      Decision과의 결선 여전히 없음(W24~26 스코프).

## W24~26 (Aggregator·Meta Decision·Risk Engine·Sizer·Kill Switch — 전 경로 관통)

- [x] strategy/{futures/aggregator,futures/service,decision/meta_decision,pipeline}.py +
      strategy/regime/runtime.py + risk/{risk_engine,sizer,kill_switch}.py 신규 +
      execution/order_gateway.py 확장 (2026-07-27 완료) —
      **src/messiah/core/messages.py**: `FuturesView` 신규(Aggregator 산출물, `intel.futures`)
      — score/agg_p_up/agg_p_down/uncertainty/dispersion/regime/n_experts/model_versions/
      top_features/valid_until.
      **src/messiah/strategy/futures/aggregator.py 신규**: `Aggregator` — Ver 1.2 §7.1
      가중치 매트릭스(6 Regime×6 Horizon, 원문 표 그대로 상수화) + §7.2 통합점수 공식
      (S=Σw×방향×meta×(1−u)×신선도). dispersion(Ver 2.0 §3.1 ③ 입력)은 원문에 계산식이
      없어 "집계 기여 Horizon들의 방향점수 표준편차"로 직접 정의(모듈 docstring에 근거 명기).
      XAI top5는 전문가×Feature 기여도를 가중치로 스케일해 합산.
      **src/messiah/strategy/futures/service.py 신규**: `FuturesAIService` — Ver 1.2 §9가
      명시한 `service.py`("feat.* 구독 → Expert → MetaLabeler → Aggregator → intel.futures
      발행, 프로세스 진입점")의 최초 구현. W14~19에 이미 만들어진 `HorizonExpert`/
      `MetaLabeler`가 이번 주 처음으로 실시간 루프에 연결됨. `BarClosed` 재구독 없이
      `meta_labeler.build_meta_features_from_feature_vector()`(신규 헬퍼, `valid_until`로
      봉 시가 역산)만으로 시간대 메타 Feature까지 계산 — `FeatureEngine`과 같은 `bar.*`를
      중복 구독해 핸들러 등록 순서에 의존하게 되는 취약점을 피함.
      **src/messiah/strategy/regime/runtime.py 신규**: `RegimeRuntime` — 이미 학습된
      `RegimeAI` 인스턴스를 `bar.30m.{symbol}`에 배선해 `intel.regime` 상시 발행(W20~21
      "어떤 운영 루프에도 아직 발행 안 됨" 상태를 해소).
      **src/messiah/strategy/decision/meta_decision.py 신규**: `MetaDecisionEngine` —
      Ver 2.0 §3.1 규칙 ①~⑤(kill 활성/이벤트·UNKNOWN 국면/의견분산>0.25/|S|<0.20/방향 결정)
      우선순위 그대로. ⑥⑦(Options AI 비교·상관노출 합산)은 Options AI 자체가 없어(Phase 4)
      코드 경로가 아예 없음. NO TRADE도 근거와 함께 항상 발행(Ver 2.0 §3.2). `horizon` 필드는
      의도적으로 항상 None(S가 이미 전 Horizon 통합값이라 특정 Horizon을 지목할 수 없음).
      **src/messiah/risk/risk_engine.py 신규**: `RiskEngine` — Ver 2.0 §5 한도표 중 R2(일일
      손실)·R3(증거금)·R5(포지션수)·R10(연속손실)·R11(데이터단절)·R12(주문오류율) + Net
      ER>0 게이트. **R1(단일포지션 최대손실 2%)은 이 클래스가 아니라 Sizer의 사이징 상한으로
      구조적으로 강제**(순서상 Risk Engine 통과 시점엔 아직 사이징 전이라 검사할 수량 자체가
      없음 — 모듈 docstring에 설계 근거 상세 기록). R3·R5는 "예상 증거금/포지션수"가 아니라
      "현재 상태" 기준 게이트로 구현해 사이징 전 순환 의존을 피함. R4·R6·R7·R8·R9는 세션
      인식·옵션 Greeks 전제라 미구현(명시적 갭).
      **src/messiah/risk/sizer.py 신규**: `PositionSizer` — Ver 1.1 §4-3 "Vol Targeting ×
      Fractional Kelly × 불확실성 페널티"를 Ver 2.0 §2 워크스루 예시("Vol Target × 1/4
      Kelly × (1−0.11) → 미니 3계약") 그대로 세 항의 곱으로 구현. Kelly 엣지는 실현
      트랙레코드가 없어 대칭 페이오프(b=1) 가정 근사(`edge=2p−1`)로 단순화(명시적 갭).
      **src/messiah/risk/kill_switch.py 신규**: `KillSwitch` — Ver 1.1 §4-4/Ver 2.0 §5
      트리거(R2+R11(지속)+수동+모델이상) 구현. 발동 시 `sys.kill` 발행 + 청산 주문 목록
      생성(`liquidate()`, 제출은 호출자가 OrderGateway로 — 계명 1 유지).
      **src/messiah/execution/order_gateway.py**: `halt()` 신규 공개 메서드(`resume()`과
      대칭) + `submit()`이 `kind=EMERGENCY`는 halted 상태에서도 통과시키도록 수정.
      **src/messiah/strategy/pipeline.py 신규**: `TradingPipeline` — L3(FuturesView)→
      L4(Cost→Risk→Sizer)→L5(OrderGateway) 전 경로 관통 오케스트레이터("전 경로 관통"의
      실체). Net Expected Return은 크기 예측 모델이 없어 `edge×ATR(M1,14봉)−왕복비용`으로
      근사(명시적 갭, 모듈 docstring에 근거 기록).
      **버그 2건 발견·수정(둘 다 `scripts/run_full_path_smoke.py` 최초 실행 중 실측으로
      발견, 실제 실행 없이는 안 드러났을 종류)**:
      (1) `FuturesView.ts_utc`가 Aggregator의 `as_of`(봉 도메인 시각)와 무관하게 `BusMessage`
      기본값(wall clock)으로 채워지고 있어, 재생/스모크처럼 과거·합성 시각을 빠르게 재생하면
      `TradingPipeline`의 R11 판정이 "wall clock now" vs "봉 도메인 시각"을 비교해 수억 초
      단위 가짜 데이터단절을 일으킴(최초 스모크 실행 로그: "R11 데이터단절 15172559s 지속")
      — `Aggregator.compute()`가 `ts_utc=as_of`로 명시 오버라이드, `FuturesAIService`가
      `trigger.valid_until`(봉 도메인)을 `as_of`로 사용하도록 수정.
      (2) 신선도(f_h) 공식이 처음부터 방향이 반대였음 — `valid_until`은 스키마 주석("다음
      완성봉 시각")과 달리 실제로는 "그 봉 자신의 확정 시각"(`features/engine.py`가 그렇게
      채움, `RegimeState`/`ExpertView` 등 다른 발행자도 전부 동일 — 이번에 처음 확인된
      pre-existing 관례)인데, 최초 구현은 이를 미래 만료 시점으로 오독해 발행 즉시 신선도가
      0이 되는 반대 결과를 냈음 — `(as_of−valid_until)/Horizon` 경과 기준으로 공식 반전.
      (3) 위 두 버그를 고치는 과정에서 세 번째(설계상) 문제도 발견: `OrderGateway.halt()`가
      `kind=EMERGENCY`까지 차단해 Kill Switch 자신의 청산 주문이 거부되는 모순이 있었음
      (청산 로그가 매 Horizon 갱신마다 반복 발행되는 것으로 발견) — `submit()`이 EMERGENCY는
      halted여도 통과시키도록 수정, 회귀 테스트 추가.
      **scripts/run_full_path_smoke.py 신규**: 실제 아카이브 시도(예상대로 실패) → 합성
      데이터로 Expert 2개(5m·30m)+Meta-Labeler 학습 + RegimeAI 학습 → 전체 실시간 배선
      구동(FeatureEngine·FuturesAIService·RegimeRuntime·SimBroker·TradingPipeline) → 직접
      주입한 강한 LONG 신호로 Sizer→RiskEngine→OrderGateway→SimBroker 전 경로 주문 체결
      확인(포지션 16계약 개설) → 계좌 손실 조작으로 Kill Switch(R2) 실제 발동·청산·Gateway
      정지까지 end-to-end 1회 성공.
      테스트 71건 신규(aggregator 10·futures_service 6·regime_runtime 4·meta_decision 11·
      risk_engine 13·sizer 10·kill_switch 10·pipeline 6·order_gateway EMERGENCY 우회 1),
      전체 510건 통과. ruff 클린(신규 파일 기준 — 리포 전체 I001 경고는 기존부터 있던 별개
      항목, W22~23부터 반복 확인된 사항). pyright는 신규 파일 기준 클린(`strategy/regime/
      runtime.py`의 `Handler`/`BarClosed` 분산성 경고 1건은 `features/engine.py`가 W6~8부터
      갖고 있던 동일 패턴 — 신규 아님, 일관성 확인 차 대조까지 완료).
      남은 갭(capability_matrix.md 기록): Regime 가중치 매트릭스 Walk-Forward 재추정 없음,
      Conformal 불확실성 미사용(기존 갭), Options AI 부재로 ⑥⑦·R7~R9 코드 경로 자체 없음,
      R4·R6(세션 인식 없음), Kelly 엣지 대칭 페이오프 근사, `point_value_krw` 미실측, Net ER
      계산식 명시적 근사, R10 결선 없음(포지션 추적기 부재), R12 심볼별 미분리, Kill Switch
      실제 KIS 계좌 청산 미실측.

## W27 (Phase 4 착수 전 선행 인프라 갭 3건 — Event Calendar·백테스트 하니스·Options AI 인프라)

- [x] core/event_calendar.py + configs/krx_holidays.yaml 신규, RiskEngine R4/R6·
      TradingPipeline·run_l1_daily.py 결선 (2026-07-27 완료) — KRX 휴장일 인식 + 세션
      판정. RiskEngine R4(오버나이트 증거금 25%, 30분 창)·R6(오버나이트 자격, 10분 창)
      최초 구현(Holding Policy §2.2 Type A "무포 오버나이트" 반영). run_l1_daily.py는
      휴장일이면 self_check조차 안 하고 즉시 종료. `rule_economic_event`(경제지표
      캘린더)는 별개 외부 데이터 소스가 필요해 이번에도 미발동 — 의도적 스코프 제외
      (dev_memory/DECISION_LOG.md 13차, capability_matrix.md 참고). 테스트 33건 신규.
- [x] backtest/harness.py 신규 — Walk-Forward 백테스트 하니스 (2026-07-27 완료) —
      WalkForwardSplitter+train_formal_expert+FeatureEngine/FuturesAIService/
      TradingPipeline/SimBroker를 엮어 Validator.validate_performance()가 처음으로
      실제 walk-forward 산출물을 입력받아 계산되게 함(W14~26 내내 남아있던 공통 갭
      해소). Regime AI 미포함·일별 granularity 없음·Deflated Sharpe 미제출은 명시적
      스코프 제외. scripts/run_backtest_harness.py로 실제 실행 확인(합성 데이터,
      창 2개, 무거래로 수익률 0% — 성능 주장 아니라 배관 검증). 테스트 9건 신규.
- [x] data/collector.py MultiSymbolTickCollector + data/investor_flow_poller.py 신규
      (2026-07-27 완료, 실계좌 미검증) — 단일 WS 연결 다중 구독(2026-07-23 "연결 2개
      → 반복 단절" 문제의 구조적 해법)과 FixedTickScheduler 첫 실사용(REST 폴링).
      MultiSymbolTickCollector 실계좌 검증은 오늘 라이브 세션과의 리소스 경합을 피해
      의도적으로 보류(다음 비거래시간 또는 [[l1_gap_deferral_to_weekly_review]] 같은
      이관). InvestorFlowSnapshot은 필드 매핑 없이 raw dict만 보존(FL Feature 파싱은
      실측 캡처 확보 후 별도 착수). 테스트 21건 신규(collector 16·poller 5).
      남은 갭: OP(옵션체인 그릭스)·RG(매크로/현물지수) 폴러 미착수, ATM±N 구독 롤링
      미구현 — capability_matrix.md "옵션/수급 데이터 인프라 착수" 섹션 참고.

## W27~34 (Phase 4 — Options AI Vol Engine·매트릭스·Evaluator·Lifecycle·안전규칙 + Risk
Engine R7~R9 + Command Center UI)

- [x] W27~29: data/option_chain_poller.py + strategy/options/{surface,vol_metrics,
      vol_forecast,matrix,config}.py (2026-07-28 완료) — OP REST 폴러(InvestorFlowPoller와
      동일 패턴, raw passthrough), Black-76 프라이서·IV 역산·스마일 피팅(KIS 원시 Greeks
      불신, DECISION_LOG.md 14차), HAR-RV 기준모델(LightGBM 잔차 보정은 제외), 방향×IV
      전략 매트릭스(네이키드 라벨 치환, DECISION_LOG.md 14차). 테스트 68건.
- [x] W30~31: strategy/options/{evaluator,safety,lifecycle,hedging,service}.py +
      risk/risk_engine.py R7~R9 (2026-07-28 완료) — 시나리오 그리드 평가(구조상 Max Loss),
      §6 Hard Rules 독립 모듈, 수명주기 상태기계(신호만, 실행 없음), 밴드 델타 헤징,
      `OptionsAIService`(intel.options 발행). Risk Engine에 R7(순델타)·R8(순베가)·R9(매도옵션
      손실, safety.py 재사용) 추가 — Ver 2.0 §5 한도표 R1~R12 전 항목 최소 형태 구현 완료.
      **신용 스프레드 델타배정 버그 발견·수정**(DECISION_LOG.md 14차 — 가장 중요한 발견).
      테스트 74건 신규(전체 717건).
- [x] W32~34: ui/{state_cache,data_source,app}.py — Command Center Streamlit 1단계
      (2026-07-28 완료) — 고정 상단바 + 핵심 4존, LIVE/REPLAY 명시적 전환(마흐디 L18),
      캔들 차트는 Parquet 공용 소스. `streamlit.testing.v1.AppTest`로 실제 스크립트 실행
      검증(계획 문서의 "테스트 불가" 가정을 뒤집음, DECISION_LOG.md 14차). 테스트 22건 신규
      (전체 739건). pyright 전체 재확인 — 신규 오류 0건(기존 오탐 12건은 미변경 파일).

  **남은 갭(capability_matrix.md에 상세)**:
  - 옵션 주문 실행 경로 자체 없음 — `Sizer`/`OrderRequest`가 단일 심볼만 지원, 다리 여러
    개짜리 스프레드 주문 구성·제출 미착수. `BrokerPosition.greeks`를 채우는 어댑터도 없음.
  - `MetaDecisionEngine` 규칙 ⑥⑦(Options AI 비교·상관노출) 미착수 — `TradingPipeline`이
    Net ER을 `decide()` 호출 *이후*에만 계산해 재구조화 필요(실거래 경로 안정성 우선, 의도적
    스코프 제외).
  - `CALENDAR` 구조는 자리만 있음(단일 만기 가정 한계), `OptionsAIService`는 만기 1개짜리
    `SmileFit`만 다룸(다중 만기 IV Surface 동시 관리 없음).
  - §6-3(이벤트 캘린더)·§8(IV Crush 사전청산)은 매크로 이벤트 피드 자체가 없어 항상 호출측
    수동 입력 의존.
  - OP(옵션체인) REST 응답 필드 매핑 여전히 미실측 — `OptionQuoteSnapshot.raw`만 보존
    (2026-07-27 등록 항목의 남은 절반, 아래 관찰 항목 갱신).
  - Command Center UI 실제 브라우저·실제 Redis LIVE 모드는 사람 눈 확인 필요(AppTest는
    스크립트 예외 유무만 검증).

## 등록된 관찰 항목 (분기회의)

- [ ] 키움 신 REST의 국내 선물옵션 확장 발표 여부 (발표 시 브로커 랭킹 재평가)
- [ ] KRX 야간 파생시장 API 지원 현황 (KIS·LS)
- [ ] hmmlearn 0.3.3 + numpy 2.5 DeprecationWarning (2026-07-27 등록) — 지금은 무해(경고일 뿐,
      전체 테스트 통과에 영향 없음)하나 numpy가 향후 릴리스에서 배열 shape 직접 대입을 실제로
      제거하면 RegimeAI/RegimeRuntime이 깨짐. numpy를 올릴 계기가 생기면(다른 사유로든)
      tests/strategy/regime/ 전체 통과 확인 + hmmlearn 신규 릴리스 여부 확인
      (dev_memory/DECISION_LOG.md 12차, Docs/capability_matrix.md 참고)
- [ ] MultiSymbolTickCollector 실계좌 검증 (2026-07-27 등록) — futures+option 동시 구독으로
      2026-07-23 관측된 반복 단절이 실제로 해소됐는지 확인. 오늘 라이브 세션과의 리소스
      경합을 피해 의도적으로 보류함 — 다음 비거래시간 또는 주간회의에서 일정 재확인
      (dev_memory/DECISION_LOG.md 13차 참고)
- [ ] FL Feature 필드 매핑 실측 (2026-07-27 등록) — get_investor_flow() 원시 응답 캡처로
      외국인/기관/개인 순매수 필드 위치 확정 필요(docs/efriend 엑셀 또는 실계좌 캡처).
      확정되면 normalizer.py에 parse_investor_flow() 유형 함수 추가
- [x] OP(옵션체인) REST 폴러 착수 — data/option_chain_poller.py (2026-07-28 완료, W27~34
      참고). RG(매크로/현물지수) 폴러는 여전히 미착수.
- [ ] OP 응답 필드 매핑 실측 (2026-07-28 갱신) — `OptionQuoteSnapshot.raw`는 아직 원시
      dict 그대로 보존 중(FL Feature 갭과 동일 패턴). 실측 캡처 확보되면
      `normalizer.parse_option_quote()` 유형 함수 추가 → `strategy/options/surface.py`가
      실제 bid/ask로 IV Surface를 피팅하도록 배선. 15m/30m Expert가 Ver 1.5 배정의 절반
      이하만 받는 문제 해소의 남은 절반.
- [ ] RG(매크로/현물지수) REST 폴러 착수 (2026-07-27 등록, 미착수 유지) —
      InvestorFlowPoller/OptionChainPoller와 동일 패턴으로 확장 가능.

## W35~40 (Phase 5 — 진화와 배포: Registry·Shadow Manager·Self Evaluation·릴리스 패키징·
복제 배포 리허설·잔여 Horizon·G2 페이퍼 트레이딩 하네스)

- [x] models/{registry,release,shadow_manager,self_evaluation}.py 신규 + self_check.py/
      agenda.py 확장 + 잔여 Horizon(1m·3m·10m) 검증 + G2 하네스 + 릴리스 패키징 + 복제
      배포 리허설 (2026-07-28 완료, 사용자 요청: "Phase 5를 구현해서 메시아를 완성하고
      모의투자로 운영을 시작할 수 있는지 조사") —
      **선행 조사 결과 사용자와 합의한 스코프**: 실제 수집 데이터가 3거래일치뿐이고(G1
      백테스트 관문을 실제 데이터로 통과한 모델이 전무) G2 관문 자체가 "40거래일 관찰"의
      산출물이라 이번 세션에서 실제 손익을 만들어낼 수 없음을 먼저 보고 — 사용자가 "Phase 5
      인프라만 구현"으로 스코프를 확정(손익 조사는 명시적으로 이번 세션 범위 밖).
      **core/messages.py**: `BundleStatus`(candidate/shadow/live/retired) +
      `RegistryStatusChanged`·`ShadowFill`·`PromotionProposal`·`SelfEvalReport` 신규(L6
      Learning/Self Evolution 섹션). **core/bus.py**: `TOPIC_REGISTRY`·`TOPIC_SHADOW_FILL`·
      `TOPIC_PROMOTION`·`TOPIC_SELF_EVAL` 신규, 전부 Streams(decision.intent와 같은 이유 —
      사람이 나중에 감사할 이력).
      **models/registry.py 신규**: `ModelRegistry` — Ver 1.6 §9.2 상태기계(candidate→shadow
      →live→retired)를 SQLite(표준 라이브러리, 신규 의존성 없음 — Ver 1.1 §5 "파일+Registry
      (SQLite→PostgreSQL)"의 1단계에 정확히 대응)로 구현. live는 Horizon당 정확히 1개 강제
      (`promote_to_live()`가 이전 live를 자동 retired, 레코드·파일 보존). 버스 발행은 이
      클래스가 직접 하지 않고 `drain_events()`로 큐를 꺼내게 함(동기 클래스가 비동기 버스에
      직접 결합되지 않도록 — Trainer/Validator와 같은 야간배치 성격 유지). `pack_bundle()`은
      Ver 1.6 §9.1 번들 스펙을 따르되 파일명 규칙은 이미 있는 `HorizonExpert.save()`/
      `MetaLabeler.save()`의 stem 기반 멀티파일 포맷을 그대로 재사용(중복 직렬화 로직 방지,
      manifest.yaml이 실제 파일명의 진실 원천). `save_conformal_state()`/
      `load_conformal_state()`로 번들의 "매일 갱신되는 유일한 부분"(conformal_state.json)도
      배선.
      **models/release.py 신규**: `pack_release()`/`verify_release()` — Registry(Horizon 하나
      짜리 번들 상태기계)와 `configs/instance.yaml`의 `model_bundle`(Ver 1.1 §7.2, 여러
      Horizon의 live를 한 시점 스냅샷으로 묶는 배포 단위)을 잇는 상위 계층. 부분 릴리스(일부
      Horizon만 live)를 명시적으로 허용하고 `missing_horizons`에 정직하게 기록 — 지금은
      전 Horizon이 다 비어 있는 게 정상(G1 미통과 상태).
      **models/shadow_manager.py 신규**: `ShadowLedger`(Horizon당 단일 포지션, 시간배리어
      경과 시 청산 — `models/labeling.py`의 `BARRIER_PARAMS` 재사용, Risk Engine/Sizer/
      OrderGateway를 타지 않는 독립 단순 규칙이라 "상대 비교" 근사임을 모듈 docstring에 명시)
      + `ShadowManager`(여러 shadow 번들 동시 병행, FuturesAIService와 같은 구독 패턴) +
      `evaluate_promotion()`(Ver 1.1 §6-4 "20거래일+Net Sharpe 우위+MDD 한도" 그대로 —
      **자동 제안만 하고 승격은 안 함**, 사람이 `ModelRegistry.promote_to_live()`를 호출해야
      실제 승격).
      **models/self_evaluation.py 신규**: `run_self_evaluation()`(승률·PF·Sharpe·MDD 집계 —
      Regime 정확도는 "정답 정의 자체가 없음"을 이유로 명시적 스코프 제외) +
      `reconcile_slippage()`(Ver 2.0 §6 "체결 품질 기록 → Cost Model 자기보정"의 실제 계산부
      — `OrderRequest.msg_id`→`OrderAck.request_id`→`Fill.broker_order_no` 3단 매칭, 지정가
      주문만 대상). **models/metrics.py 확장**: `win_rate`·`profit_factor`·
      `equity_curve_from_returns` 신규(기존 순수함수 원칙 유지, Self Evaluation·Shadow
      Manager 공유).
      **simulator/engine.py 확장**: `LiveSimBrokerFeed` 신규 — 기존 `DigitalTwinEngine`은
      유한 봉 시퀀스를 직접 순회하며 자기가 버스 발행까지 하는 배치 재생기라 상시 운영에
      못 씀. 이 클래스는 `bar.1m.{symbol}`을 **구독만** 해 `SimBroker.on_bar()`→
      `OrderGateway.on_fill()`로 잇는 얇은 다리 — G2가 실시간 버스(run_l1_daily.py가 이미
      실계좌로 검증한 그 배선) 위에서 페이퍼 브로커를 돌릴 수 있게 하는 핵심 결선.
      **scripts/self_check.py 확장**: `check_registry_consistency()` 신규 — live 모드에서
      릴리스가 가리키는 각 Horizon 번들이 Registry상 지금도 live인지 교차검증("번들 손상
      배포" 방어, Ver 1.6 §12). **scripts/agenda.py 확장**: 4번째 섹션(Self Evaluation 일일
      리포트, `logs/self_eval_*.json`) + 5번째 섹션(사람 결정이 필요한 승격 제안,
      `logs/promotion_proposals.jsonl`) 신규.
      **scripts/run_g2_paper_trading.py 신규**: `run_l1_daily.py`(L1 수집 전용) 구조를
      그대로 확장한 G2 일일 운영 진입점(웜업→정규장→장후종료+Self Evaluation). **정직한
      현재 상태를 모듈 docstring에 명시**: Registry에 live 번들이 하나도 없어 이 스크립트를
      오늘 당장 돌려도 거래가 전혀 안 남 — 증명하는 건 "시스템이 안 죽고 도는가"(G2 통과기준
      "시스템 무중단")이지 "우위가 있는가"가 아님. Regime AI도 실측 데이터 부족으로 결선
      보류(RegimeState 미수신 시 FuturesAIService 기본값 UNKNOWN → Meta Decision 규칙②로
      안전하게 NO TRADE). 챔피언 일일수익률은 Position Reconciler 부재로 "포트폴리오 평가액
      변화율" 근사(거래별 실현손익 아님, 명시적 갭) — `logs/g2_daily_returns.jsonl`에 매일
      追記해 누적, G2 "40거래일"이 의미를 가지려면 이 파일이 40줄 이상 쌓여야 함.
      **scripts/run_phase5_smoke.py 신규**: Registry pack/register/promote(candidate→shadow
      →live) → Shadow Manager 가상체결 → Self Evaluation → 승격 제안 → Release 패키징+
      정합성검증까지 전 경로 1회 실행 확인(`run_full_path_smoke.py`와 동일 패턴, 합성
      데이터). **scripts/run_replication_rehearsal.py 신규**: 실제 2번째 PC 없이 "설정
      파일 하나로 인스턴스 분리"(Ver 1.1 §7.2) 자체를 검증 — 서로 다른 instance_id·자본의
      두 InstanceConfig를 각각 self_check 통과시키고, 실제 messiah-redis로 각자 Health를
      발행해 `instance_id`가 정확히 자기 것으로 찍히는지 확인(실행 결과: PASS 2/2).
      **install.ps1 + docker-compose.yml 신규**: Ver 1.1 §7.3 "설치는 명령 한 번" — Redis
      기동(`docker compose up -d`, messiah-redis 포트 6380 그대로 코드화)→venv 생성→
      `pip install -e .[ml,ui,dev]`→self_check(무Redis→Redis)→`run_replay.py` 스모크까지
      한 스크립트로. 영문 주석 전용(레슨런 — `.bat`의 cp949 오분석 사고를 Windows 스크립트
      전반의 안전 습관으로 일반화). **`docker compose up -d`는 이번 세션에 실행하지 않음**
      (기존 수동 기동 `messiah-redis` 컨테이너와 이름이 같아 재생성 위험 — 실제 실행은
      사용자 확인 후).
      **잔여 Horizon(1m·3m·10m) 검증**: `run_formal_expert_training_smoke.py --horizon`이
      이미 전 Horizon을 받게 돼 있어(W22~23부터 일반화) 실제로 1m/3m/10m 각각 실행해 확인—
      1m은 실제 아카이브(33건)로도 정식 파이프라인 성공(흔치 않은 결과), 3m은 실제
      아카이브(11건)에서 **버그 발견**(아래), 10m은 실제 아카이브(5건)로는 예상대로 데이터
      부족 실패. 셋 다 합성 데이터로는 탐색→out-of-fold→앙상블→교정→Meta-Labeler 전체
      성공. Aggregator 가중치 매트릭스(6 Regime×6 Horizon)는 W24~26에서 이미 전 Horizon을
      상수화해 둔 상태라 이번엔 추가 작업 없이 그대로 재확인만 함.
      **[버그 발견·수정 1] `models/search.py` — 극소 표본 학습 폴드에서 LightGBM 네이티브
      크래시**: 3m 실제 아카이브(11봉→레이블 6건)로 `search_hyperparameters(n_splits=2)`
      실행 중 `lightgbm.basic.LightGBMError: Check failed: (num_data) > (0)`로 전체 스크립트
      가 죽음(ValueError가 아니라 네이티브 크래시라 기존 "빈 폴드 skip" 가드로도 못 막음).
      원인: PurgedKFold가 학습 폴드를 1행까지 깎았는데(purge/embargo, n=6·n_splits=2의 자연
      귀결) `bagging_freq=1`+샘플된 `bagging_fraction`(생산 탐색공간 0.5~0.9)이 그 1행을
      0행으로 반올림 — LightGBM이 빈 배깅 서브셋으로 Dataset을 못 만듦. 수정: `objective()`
      의 `lgb.train()` 호출을 `try/except lgb.basic.LightGBMError`로 감싸 그 폴드/그 trial만
      건너뛰게 함(전체 탐색은 안 죽음). 회귀 테스트 신규
      (`test_search_survives_single_row_training_fold_bagging_crash`, n=3·n_splits=2로 항상
      재현되는 최소 반례). 프로덕션 데이터 규모(Ver 1.2 §8.1 "2년치")에서는 폴드가 이 정도로
      안 작아져 발생하지 않는 경계 조건.
      **[버그 발견·수정 2] `risk/cost_model.py` — `CostModel`에 `config` 조회 프로퍼티
      없음**: `models/self_evaluation.py`의 `reconcile_slippage()`가 예측 슬리피지(Ver 2.0 §6)
      를 읽으려 `cost_model.config.expected_spread_ticks`를 호출했다가 `run_phase5_smoke.py`
      최초 실행에서 `AttributeError` 실측(사설 `_config`만 있고 공개 프로퍼티가 없었음) —
      `CostModel.config`(조회 전용) 프로퍼티 신규 추가로 해결. 테스트 2건 추가.
      테스트 50건 신규(registry 12·release 5·shadow_manager 11·self_evaluation 7·metrics
      +8·cost_model +2·live_sim_broker_feed 4·search +1), 전체 789건 통과. ruff 전체
      클린(신규/수정 파일 기준 I001 포함 — 이번 세션 코드는 사전에 정리, 리포 나머지의
      기존 I001 63건은 여전히 별개 항목). pyright는 신규 파일 기준 0건(search.py/bus.py의
      lightgbm/optuna/redis.asyncio 미해석은 W17~19·W1부터 있던 기존 venv 미탐지 패턴 —
      전체 재확인으로 신규 파일 관련 없음 확인).
      **실측(수동 실행) 확인 항목**: `run_phase5_smoke.py`(Registry 상태기계 전이+Shadow
      가상체결+Self Evaluation+Release 정합성 전부 실제 실행 성공), `run_replication_
      rehearsal.py`(실제 messiah-redis로 인스턴스 분리 PASS 2/2), `self_check.py`의
      `check_registry_consistency`(정상 릴리스 PASS·강등된 번들을 가리키는 stale 릴리스
      FAIL 둘 다 수동 재현 확인), `agenda.py`의 신규 4·5번 섹션(가짜 로그로 렌더링 확인).
      **남은 갭(capability_matrix.md 상세)**: G1 백테스트 관문을 실제 데이터로 통과한 모델
      전무(3거래일치로는 원천 불가 — 데이터 축적이 유일한 해법), Regime AI가 G2 하네스에
      결선 안 됨, Position Reconciler 부재로 챔피언 일일수익률이 거래별이 아니라 포트폴리오
      평가액 근사, Self Evaluation의 슬리피지 대사가 아직 실제 OrderRequest/Ack/Fill 이력을
      안 모음(전부 예측값·0건으로만 찍힘), Conformal 상태 갱신(운영 예측 로그 vs 실제 결과
      재라벨링)은 별도 파이프라인 필요해 미착수, `docker compose up -d` 실제 실행 미검증
      (기존 수동 컨테이너와 충돌 위험 회피), Windows 작업 스케줄러에 `run_g2_paper_trading.py`
      등록 안 함(무인 자동화는 사용자 확인 후).

## Task Scheduler·Docker 자동화 점검 + Docker Desktop 자가 기동 (2026-07-29)

- [x] core/docker_bootstrap.py 신규 + run_l1_daily.py/run_g2_paper_trading.py 결선
      (2026-07-29 완료, 사용자 요청: "Messiah"/"Messiah-Shutdown" 작업 스케줄러·Docker
      점검 후 자동 시작·종료 문제없는지 확인) —
      **조사 결과**: 두 작업 다 등록·정상 가동 중(최근 실행 전부 성공, CRITICAL 0건).
      `run_l1_daily.bat`의 "Not yet registered" 주석은 stale 문서였음(이번에 정정). 구조적
      취약점 3가지 발견: (1) Docker Desktop `AutoStart=False`로 배치파일 어디에도 Docker
      기동 로직 없음 — 사용자 확인 결과 지금까지는 07:30에 **다른 프로젝트**가 자기 필요로
      Docker Desktop을 띄우는 부수효과에 기대고 있었음, (2) Task 트리거가 `LogonType=
      Interactive`·`StartWhenAvailable=False`·`WakeToRun=False`라 PC가 꺼져있거나
      로그오프 상태면 그날은 캐치업 없이 영구히 건너뜀, (3) 실패 시 능동적 알림 없음(로그만
      남음). 사용자 요청으로 (1)번만 이번 스코프에서 해결.
      **src/messiah/core/docker_bootstrap.py 신규**: `ensure_docker_ready()` — `docker
      info`로 daemon 응답 확인, 미응답이면 Docker Desktop 실행(`MESSIAH_DOCKER_DESKTOP_EXE`
      환경변수로 경로 오버라이드 가능) 후 최대 2분 폴링, 준비되면 `docker start
      messiah-redis`로 컨테이너까지 재확인. 시간 초과 시 `ready=False` 반환(호출자가 명시적
      중단 — 조용히 진행 안 함). 전부 `runner`/`popen`/`sleep`/`now` 콜러블 주입 가능해
      실제 docker CLI·실제 대기 없이 결정론적 테스트 가능(`FixedTickScheduler`와 동일 설계
      원칙). **run_l1_daily.py/run_g2_paper_trading.py 둘 다 `_run_self_check()`보다 먼저
      호출**하도록 배선 — self_check의 Redis 점검이 실패하기 전에 이미 Docker가 준비돼
      있게 만듦.
      테스트 11건 신규, 전체 800건 통과. ruff 클린. 실제 실행 중인 Docker로
      `ensure_docker_ready()` 직접 호출해 `already_running=True` 즉시 반환 확인(실통합
      검증).
      **남은 갭**: 취약점 (2)·(3)은 사용자가 이번엔 요청하지 않아 미해결 — Task Scheduler의
      로그온 방식(Interactive→S4U 전환 시 비밀번호 필요)이나 실패 알림(텔레그램 등, Ver 1.1
      OBS)은 별도 요청 시 착수.
- [x] Command Center UI(Streamlit)를 데일리 자동화에 통합 (2026-07-29 완료, 사용자 질문
      "메시아를 실행해도 UI는 없는가"에서 출발) — `run_l1_daily.py`의 `_launch_ui()` 신규,
      거래일 확인 직후 Streamlit을 별도 백그라운드 프로세스로 기동(`MESSIAH_SKIP_UI=1`로
      생략 가능, 기동 실패해도 데이터 수집은 계속). `stop_l1_daily.bat` 워치독이
      `*messiah\ui\app.py*` 패턴도 함께 매칭하도록 확장해 UI 프로세스도 15:40에 정리.
      실제 실행으로 검증: UI 기동(포트 8501 LISTENING, 브라우저 자동 접속) → 워치독이
      관련 프로세스 3개(streamlit stub+venv 인터프리터+anaconda base 인터프리터) 전부
      찾아 강제 종료 확인 — "명령줄 패턴 매칭으로 살아있는 프로세스를 실제로 죽인다"는
      경로가 다중 프로세스 트리 기준으로는 이번에 처음 확인됨(기존엔 2026-07-24 단일
      프로세스 기준만 있었음). 남은 갭: UI는 여전히 REPLAY 기본 모드로 뜨고 LIVE 전환은
      사람이 수동으로 해야 함(기존 원칙 유지, 이번 통합이 안 바꿈), G2 스크립트에는 아직
      통합 안 함(요청 범위 밖).
- [x] run_g2_paper_trading.py에도 UI 통합 + core/ui_launcher.py로 공용화 + 중복기동 방지
      (2026-07-29 완료, 사용자 요청 전 손익 조사 → 승인 후 진행) — 조사 결과(G2 미등록·
      Registry 비어있어 화면 사실상 빔·watchdog이 15:40에만 돎) 보고 후 사용자가 진행 확정.
      **실측 중 신규 버그 발견**: `run_l1_daily.py`의 UI가 떠 있는 상태에서 G2도 UI를
      띄우면, Streamlit/Windows가 이미 점유된 포트 8501에 에러 없이 두 번째 프로세스를
      또 바인드시켜 두 프로세스가 동시에 LISTENING 상태로 남음(요청이 어느 쪽으로 가는지
      예측 불가) — 애초 조사에서 못 본 리스크를 구현 중 발견해 그 자리에서 방어.
      **src/messiah/core/ui_launcher.py 신규**: `launch_command_center()` — 기동 전
      `is_ui_already_running()`(포트 응답 확인)으로 먼저 확인 후 이미 떠 있으면 생략.
      `docker_bootstrap.py`와 동일하게 `is_running`/`popen` 주입 가능해 순수 테스트 가능.
      두 스크립트의 `_launch_ui()`는 이 공용 함수를 부르는 얇은 래퍼로 축소(중복 제거).
      테스트 8건 신규, 전체 808건 통과. 실제 재현으로 확인: `run_l1_daily.bat`로 UI를 띄운
      채 G2의 `_launch_ui()` 직접 호출 → "이미 응답 중 — 중복 기동 생략" 정상 출력, 두
      번째 프로세스 생성 안 됨.

## Command Center UI 포트 충돌 방지 ([MW0601], 2026-07-29)

- [x] `core/ui_launcher.py`의 `DEFAULT_PORT`를 Streamlit 공용 기본값(8501)에서 MESSIAH
      전용 고정값(8511)으로 변경 + `launch_command_center()`가 `streamlit run`에
      `--server.port`를 명시 전달 (2026-07-29 완료, 사용자 요청: 금일 장전·장중 로그
      조사에서 발견한 이상점 fix) —
      **실사고**: 오늘 08:35:10 `run_l1_daily.py`가 "Command Center UI가 이미 응답
      중(포트 8501) — 중복 기동 생략"으로 자기 UI 기동을 스킵했는데, 실측 결과 그 포트를
      점유하고 있던 건 완전히 다른 프로젝트(`PycharmProjects\options`)의 Streamlit이었다
      — MESSIAH 자신의 화면이 아무 경고 없이 하루 종일 안 뜬 것. `is_ui_already_running()`
      은 "어떤 프로세스든 응답하면 스킵"이 설계 의도였지만(2026-07-29 16차 기록), 제3의
      무관한 로컬 프로젝트와 충돌하는 케이스는 그때 문서화되지 않았었다.
      **수정**: 포트를 8511로 분리해 로컬의 다른 미설정 Streamlit 앱과 겹칠 확률을 낮췄고,
      기존에 `port` 파라미터가 `is_running()` 확인에만 쓰이고 실제 `streamlit run` 명령에는
      전달되지 않아 항상 실제 Streamlit 기본값(8501)에 바인딩되던 잠재 버그도 함께 고쳤다.
      포트 충돌로 스킵할 때 출력 메시지에 "WARN:" 접두사와 "실제로 MESSIAH UI인지 확인
      안 됨 — 직접 열어 확인할 것"이라는 행동지침을 추가.
      테스트 2건 신규(전용 포트값 확인, `--server.port` 인자 전달 확인) + 기존 8건 갱신,
      전체 809건 통과, ruff 클린. **실제 streamlit로 재현 확인**: 실제 호출로 포트 8511에
      LISTENING 확인, 8501을 점유 중이던 `options` 프로젝트 프로세스는 전혀 안 건드림,
      중복 기동 방지도 새 포트에서 정상 작동 확인. 검증용 프로세스는 종료 후 정리.
      **남은 갭**: 이론상 제3자가 8511도 쓸 가능성 자체는 남아있음(포트는 전역 공유 자원)
      — 재발 시 `is_ui_already_running()`에 실제 신원 확인(MESSIAH 전용 헬스체크 엔드포인트
      등) 도입을 검토할 것, 이번엔 스코프 밖으로 보류.

## G2 페이퍼 트레이딩 Task Scheduler 등록 ([MW0601], 2026-07-29)

- [x] `run_g2_paper_trading.py`에서 자체 `TickCollector`/`MultiHorizonBarComposer`/
      `FeatureEngine` 제거 + Task Scheduler "Messiah-G2"(평일 08:36) 등록 (2026-07-29
      완료, 사용자 요청: 고도화 제안 "G2를 L1과 같은 방식으로 등록" 구현) —
      **착수 전 발견한 블로커**: 코드를 직접 읽어보니 G2가 L1과 완전히 동일한 계좌·심볼·
      TR로 자기 자신의 WS 연결을 또 여는 구조였다 — `Docs/capability_matrix.md`
      (2026-07-23)에 이미 확정된 "동일 계좌 WS 연결 2개 → 반복 단절" 버그와 정면 충돌.
      제안대로 그대로 등록했다면 매일 아침 그 버그를 재현해 아직 무해한 G2를 위해 실제로
      가치 있는 L1 데이터 수집을 매일 망가뜨렸을 것 — 사용자에게 먼저 보고해 "리팩터링
      후 등록"으로 승인받고 진행.
      **수정**: `FuturesAIService`·`TradingPipeline`·`LiveSimBrokerFeed`·`ShadowManager`
      전부 `bus.subscribe()`만으로 동작한다는 걸 확인하고, G2의 자체 데이터 수집 스택을
      전부 제거 — 이제 L1이 버스에 이미 발행한 `bar.*`/`feat.*`를 구독만 한다(자체 WS
      연결 0개).
      `scripts/run_g2_paper_trading.bat` 신규(`run_l1_daily.bat`와 동일 패턴),
      `scripts/stop_l1_daily.bat` 워치독에 G2 명령줄 패턴 추가, `run_l1_daily.bat`의
      stale 주석도 정정.
      전체 테스트 809건 통과, ruff/pyright 클린. **실제 라이브 검증**: L1이 실계좌 WS로
      정상 수집 중인 상태(장중)에 리팩터링된 G2를 실제로 수동 실행 →
      `Get-NetTCPConnection`으로 G2 워커 프로세스 확인 결과 Redis 4건+REST 1건뿐,
      KIS 실시간 WS 엔드포인트(`210.107.75.39:21000`) 연결 0건 확인. 그 사이 L1의 기존
      WS 연결은 그대로 `Established` 유지, L1 로그도 끊김 없이 계속 발행 — G2 가동이
      L1에 전혀 영향 없음을 실측으로 증명. 검증 프로세스는 종료 후 정리.
      `Register-ScheduledTask`로 "Messiah-G2"를 기존 "Messiah" 태스크와 동일 설정(평일
      트리거·`MW0601`/Interactive/Limited·무제한 실행시간)으로 08:36 트리거 신규 등록,
      등록 후 설정값 재확인 완료.
      **남은 갭**: L1과 동일한 기존 갭(로그온 필요·무알림·`WakeToRun=False`) 공유, 새로
      만든 갭 아님. 내일(2026-07-30) 08:36 첫 자동 트리거 결과는 `logs/g2_daily_20260730.log`
      로 다음 세션에서 확인 필요 — Registry가 비어 있어 여전히 "시스템이 안 죽고 도는가"만
      증명하는 단계(실제 손익은 여전히 무의미).

- [x] `scripts/self_check.py`의 `check_timezone()` 표시를 UTC에서 KST로 통일 ([MW0601],
      2026-07-29 완료, 사용자 요청: "전체 ts를 KST 형식으로 변경 개선해" — G2 등록 직후
      재시작 로그 점검 중 self_check 블록만 `utc=...+00:00`으로 찍혀 그 아래 모든 `ts`
      필드(`core/logging.py`, 이미 KST)와 표기가 섞여 보이는 걸 발견) — `CheckResult`의
      detail 문자열을 `utc={u.isoformat(...)}`에서 `kst={k.isoformat(...)}`로 변경(판정
      로직 `ok`는 그대로 UTC/KST 오프셋 둘 다 검증). 내부 데이터 표준(`ts_utc` 등)은 이번에
      안 건드림 — SYSTEM.md R3 원칙 그대로, 사람이 읽는 표시줄 하나만 정리한 것.
      `python scripts/self_check.py`로 실제 실행해 `kst=2026-07-29T12:15:05+09:00` 정상
      출력 확인, 전체 테스트 809건 통과, ruff 클린(전용 단위 테스트는 원래 없음).

## Command Center UI — Market View 3건 수정 ([MW0601], 2026-07-29)

- [x] 캔들 x축 UTC 표시 버그 + 자동 새로고침 부재 + LIVE Redis URL 기본값 오류 — 3건
      동시 수정 (2026-07-29 완료, 사용자 요청: 스크린샷 보여주며 "라이브 모드에서 market
      view가 업데이트되는지 점검하고 주기는 얼마인가" → 조사 결과 세 가지 다 발견돼 "세가지
      모두 구현계획 꼼꼼히 수립하고 실수 없이 구현해"로 진행 확정) —
      **① 캔들 x축이 KST가 아니라 UTC로 보임**: `bar_open_kst`는 만들어질 때
      (`normalizer.py`의 `_combine_kst()`) 진짜 KST지만, Polars가 Parquet에 tz-aware
      datetime을 쓸 때 항상 `time_zone='UTC'`로 정규화해 저장(직접 스키마 확인:
      `Datetime(time_unit='us', time_zone='UTC')`) — `_load_bars()`가 이를 그대로 반환해
      화면엔 실제보다 9시간 이른 시각이 찍혔다(스크린샷 "00:00~03:00" = 실제 08:35~12:XX
      KST). `_load_bars()`에 `.dt.convert_time_zone("Asia/Seoul")` 추가로 해결 —
      `pyproject.toml`이 이미 이 정확한 Windows tzdata 왕복 문제 때문에
      `tzdata>=2026.1; sys_platform=='win32'`를 넣어뒀던 것(2026-07-22)이라 새 의존성
      아님. 오늘 실제 parquet(`data/bars/A05608/5m/2026-07-29.parquet`)으로 실측: 수정 전
      "2026-07-28 23:45:00+00:00", 수정 후 "2026-07-29 08:45:00+09:00" — 첫 봉이 실제
      웜업 시작 시각과 정확히 일치.
      **② 자동 새로고침이 전혀 없었다**: `main()` 전체를 읽어봐도 `st.rerun()`/
      `streamlit_autorefresh` 등 주기적 재실행 트리거가 없어, 화면은 사람이 위젯을
      만지거나 브라우저를 새로고침해야만 갱신됐다(백그라운드 스레드는 캐시는 계속
      갱신하지만 그게 화면 rerun을 유발하지 않음). LIVE 모드일 때만
      `st.fragment(run_every=5)`로 대시보드 본문(`_render_dashboard_body` 신규 분리)을
      감싸 5초마다 그 부분만 다시 그리도록 함 — 전체 페이지가 아니라 프래그먼트만 다시
      그려 사이드바 위젯 상태는 안 흔들림. 5초는 기존 `_STALE_AFTER`의 가장 빡빡한
      임계값(FuturesView 10초)보다 넉넉히 짧게 잡아, 새로고침 사이에 배지가 헛되이
      STALE로 안 보이게 한 것. REPLAY 모드는 `run_every=None`(데이터가 시간이 지난다고
      저절로 안 바뀌므로 타이머가 낭비).
      **③ LIVE 모드 Redis URL 기본값이 실제 포트와 다름**: 사이드바 기본값이
      `"redis://localhost:6379/0"` 하드코딩이었는데(SYSTEM.md R4 위반이기도 함), 실제
      MESSIAH Redis는 6380(`configs/instance.yaml`) — 공교롭게 이 PC 6379에도 다른
      Redis가 떠 있어 `bus.connect()`가 에러 없이 "성공"해버리고 화면은 엉뚱한 Redis에
      붙은 채 모든 배지가 영원히 NO_DATA로 남는 조용한 오연결이 실측으로 확인됐다.
      `_default_redis_url()` 신규(`load_instance().redis_url` 조회, 실패 시 6380
      폴백) — `load_instance()`는 시크릿을 안 읽어 KIS 자격증명 없이도 안전.
      **부수 발견**: `_run_live_subscriber`가 연결 실패를 `"LiveConnectionError"` 캐시
      키에 `Health(CRITICAL)`로 이미 남기고 있었는데, 그걸 읽어서 화면에 보여주는
      `render_*` 함수가 하나도 없어 ①②③ 어떤 조합의 사고가 나도 사람 눈엔 그냥 "NO_DATA"
      로만 보였다 — `render_top_bar()`에 `LiveConnectionError` 스냅샷을 읽어 `st.error()`
      로 표시하는 로직 추가.
      `tests/ui/test_app_helpers.py` 신규 6건(KST 변환·캔들 x축·설정 조회·폴백·회귀 방지)
      + 기존 UI 테스트(스모크·데이터소스·상태캐시) 22건 전부 통과, 전체 815건 통과, ruff
      클린. **실제 실행 검증**: 별도 포트(8522)로 실제 streamlit 기동 → HTTP 200 확인 →
      `AppTest`로 LIVE 전환 후 실제 가동 중인 messiah-redis(6380)에 예외 없이 연결(빈
      `st.error` 목록, `LIVE` 배지 정상 렌더) 확인 → 검증 프로세스 정리.
      **남은 갭**: `run_every=5`가 실제 브라우저 세션에서 체감상 자연스러운지는 사람이
      직접 화면을 열어 확인 필요(자동화 환경엔 실제 브라우저가 없어 타이머 발동 자체는
      코드 검토+`AppTest` 정적 검증까지만 가능). Redis 오연결 대비책이 "우리 프로젝트
      Redis"에는 유효하지만, 제3자가 하필 6380도 쓰면 같은 클래스의 문제가 재발할 수
      있음(포트 충돌은 근본적으로 안 없어짐 — Command Center UI 포트 8511 사고와 같은
      성격의 잔여 리스크).

## Command Center UI 네이티브 크래시 근본대응 + 장중 무성 장애 4종 ([MW0601], 2026-07-30)

- [x] **사고: UI가 매일 1~2회 조용히 즉사하고 있었다**. 사용자가 브라우저 "Connection
      error — Is Streamlit still running?" 스크린샷과 함께 당일 로그 전수 점검을 요청해
      발견. 실측 사슬: ① Windows 이벤트 로그 `Application Error` 3건이 **fault offset까지
      완전히 동일**(`_polars_runtime.pyd` +0x083973c7, 예외코드 0xc0000005 access
      violation) — 2026-07-29 13:28:32 / 15:21:39, 2026-07-30 08:57:05, 크래시 덤프도
      `%LOCALAPPDATA%\CrashDumps\python.exe.10400.dmp`로 남아 있음 ② **첫 크래시가 커밋
      `d993bfa`(07-29 12:35, `st.fragment(run_every=5)` 도입) 53분 뒤** — 그 이전 날짜엔
      polars 크래시 0건 ③ 네이티브 크래시라 `logs/ui_*.log`엔 traceback은커녕 한 줄도
      안 남아 "조용히 사라지는" 형태가 됨.
      **근본 원인**: `ui/app.py`의 `_load_bars()`가 LIVE에서 5초마다 `pl.read_parquet()`
      하는 같은 파일을, **다른 프로세스**인 L1 수집기가 `archiver.append_bar()`에서
      `write_parquet()`로 **제자리 덮어쓰기**(= truncate 중간 상태 관측 가능)하고 있었다.
      **스크래치 프로브로 재현 확인**(쓰기 1 + 읽기 2 프로세스, 60초): 읽기측 2개가 전부
      `PanicException: range end index 90 out of range for slice of length 0`(= 길이 0인
      파일을 읽음)으로 사망, 쓰기측은 `OSError(1224 ERROR_USER_MAPPED_FILE)` 6회 —
      polars의 읽기가 mmap이라 매핑 중엔 rename/truncate가 거부되고, 반대로 매핑된
      페이지가 교체로 무효화되면 파이썬 예외가 아니라 access violation으로 즉사한다.
- [x] **F1 3중 방어**. ① `ParquetArchiver`가 임시 파일(`{name}.{pid}.tmp`)에 쓰고
      `os.replace()`로 **원자적 교체** — 읽는 쪽은 이전 완본 아니면 새 완본만 본다.
      ② 그 교체가 1224로 거부되면 짧은 백오프로 5회 재시도 — 예전엔 이 실패를 호출측
      (`collector.py`/`bar_composer.py`)이 로깅만 하고 넘겨 **그 봉이 아카이브에서 영구
      소실**됐다(다음 append는 파일을 새로 읽어 합치므로 두 번 다시 안 채워짐).
      ③ 읽기는 `read_parquet_without_mmap()`(바이트로 먼저 읽어 메모리 파싱)으로 통일해
      mmap 경로 자체를 없애고, UI는 `_BarFileCache`로 mtime·크기가 같으면 재파싱을
      건너뛰고 읽기 실패 시 **직전 성공본 + 화면 경고**로 버틴다(L18 — 조용히 안 삼킴).
      회귀 테스트 `tests/data/test_archiver_atomicity.py` 19건 신규(교체 직전 대상 파일이
      항상 완본임을 주입한 `os.replace`로 직접 관측, 재시도·정리·실패 시 기존 데이터
      보존까지) + UI 방어 6건.
- [x] **F2 UI 생존 감시**. `launch_command_center()`는 띄운 뒤 아무도 안 봤다 — 08:57
      크래시 후 **32분간 무로그·무알림·무재기동**이었고 사람이 브라우저를 보고서야 발견.
      `watch_command_center_forever()` 신규(30초 격자, 포트 무응답이면 재기동, 최대 5회
      후 ERROR 남기고 감시 종료 — 크래시 루프 무한반복 방지). `run_l1_daily.py`의 수집
      gather에 결선. 예외를 밖으로 안 내보내 본 임무를 못 죽인다.
- [x] **F3 조용한 스톨 탐지 — 실측된 30분 공백 2건의 원인 봉쇄**. 1분봉 아카이브 연속성
      검사로 발견: 07-27 결손 0분, **07-28 10:13→10:43(29분)**, **07-29 12:32→13:02(29분)**.
      07-29 건은 `FeaturePublish`도 12:31:38 → 13:02:11로 끊겨 **프로세스가 30분간 로그를
      한 줄도 안 남겼다** — 소켓은 살아있어 예외가 안 나니 `run_forever()`의 재연결 경로가
      아예 안 탄 것. `_StallWatchdog`(두 Collector 공용) 신규: 마지막 틱 이후 120초 경과 시
      `TickStallError`(= `ConnectionError` → `OSError`)를 던져 **기존 재연결 경로를 그대로
      재사용**한다(새 복구 메커니즘을 만든 게 아니라, 있는 것이 발동 못 하던 구멍을 막음).
      콜드스타트 가드는 CB 워치독과 동일 논리 — 첫 틱 전엔 판정 안 함(없으면 08:35 기동
      후 첫 틱 08:45까지 10분을 스톨로 오판해 무한 재연결). 테스트 11건 신규(가짜 시계
      주입으로 실시간 대기 0초).
- [x] **F4 FeatureEngine 웜스타트 — 피처가 하루 종일 쓸모없던 문제**. 07-29 nan_ratio
      실측(전체 121개): 1m 최저 0.025지만 **15m 최저 0.678 / 30m 최저 0.694 — 하루 종일
      피처의 2/3가 NaN**(하루에 26/14봉밖에 안 생겨 최대 윈도우를 영영 못 채움). 게다가
      1m이 12:16·14:49에 0.025 → 0.702 → 0.959로 **리셋**된 것이 로그에 그대로 — 그날
      6회 재시작(08:35/12:07/13:10/14:39/14:48/15:05)과 정확히 대응, 즉 재시작 한 번이면
      워밍업이 통째로 증발했다. 원인은 `_load_warmup_artifacts()`가 빈 스텁이었던 것 —
      Parquet에 필요한 봉이 이미 다 있는데 안 읽었을 뿐. `FeatureEngine.warm_start()` +
      `ParquetArchiver.load_recent_bars()` 신규로 기동 시 롤링 윈도를 사전 충전(오늘 파일도
      포함 — 장중 재시작 복구가 주 용도). **검증**: 콜드스타트 첫 봉 nan_ratio > 0.9 →
      웜스타트 후 < 0.1. **부수 효과**: `px_gap_open`은 `prev_day_close_ticks`가 있어야
      값이 나오는데 콜드스타트에선 전일 종가를 볼 방법이 없어 **항상 None이었다** — 전일
      M1 봉을 시간순으로 흘리면 `SessionState`의 일자 롤오버가 자연히 채운다(테스트로 확인).
- [x] **F6 로그·산출물 정합성**. ① `JsonFormatter`가 비유한 float를 `Infinity`/`NaN`이라는
      **JSON 표준에 없는 리터럴**로 찍고 있었다(2026-07-29 `CircuitBreakerConfirmed` 라인에
      `"data_age_seconds": Infinity` 실제 잔존) — `_json_safe()`로 null 치환, "로그 1줄 =
      JSON 1줄"이 `agenda.py` 집계의 전제라 원천 차단. ② `self_eval_*.json`의
      `instance_id`가 `"unset"`이었다 — 이 리포트는 버스를 안 거치고 파일로 곧장 저장돼
      `MessageBus.publish()`의 스탬핑을 못 받는다. `run_self_evaluation(instance_id=...)`
      명시 전달로 수정.
- [x] **오해 정정**: 최초 보고에서 `self_eval`의 `n_trades=1`을 "번들 0개인데 체결 1건 —
      수동 스모크 오염"으로 적었으나 **틀렸다**. `n_trades = len(champion_returns)`이고
      호출측은 하루 1개(당일 총자산 변화율)를 표본으로 넘기므로 실제 의미는 **누적 거래일
      수**다(1 = 데이터 1일치). 오염 아님. 필드명 정정은 스키마 변경이라 Position
      Reconciler 결선 때로 미루고 `core/messages.py`에 의미를 명시해 둠.

- [ ] **미해결 ①: 장전 08:45~09:00 구간 처리 방침 (결정 필요)**. `run_l1_daily.py`
      docstring의 "실제로 틱이 오기 시작하는 건 장이 열려야 하므로"가 **틀린 가정**임이
      실측으로 확인 — 3거래일 연속 **08:45:00 정각부터** 틱이 들어오고, 그 15분치가
      정규장 봉과 구분 없이(`quality_ok=True`) 아카이브·피처·차트에 섞인다(07-30 08:45봉
      거래량 526 > 09:00 개장봉 506). **예상체결이면** 학습 데이터 오염이라 09:00 이전
      틱을 버려야 하고, **실체결이면** `BarClosed`에 세션 구분 필드를 더해 보존해야 한다.
      원시 프레임 확인이 필요한데 라이브 세션과 WS 연결을 다툴 수 없어(같은 계좌 2연결 =
      상호 단절) 비거래시간 검증으로 이관. 그때까지 판단 근거를 쌓으려 `CollectorFirstTick`
      로그를 매일 남기도록 함. docstring의 틀린 문장은 실측 내용으로 교체 완료.
- [ ] **미해결 ②: `tests/strategy/test_pipeline.py` 4건이 오늘부터 상시 실패**. 이번 작업과
      무관한 기존 문제임을 `git stash`로 확인(변경 전 코드에서도 동일 4건 실패).
      원인: `_START = datetime(2026, 7, 30, 9, 0, KST)`가 커밋 `b1a366d`(07-27) 시점엔
      **미래 날짜**여서 `now_utc() - bar_confirm_time`이 음수 → data_age가 임계 미달 →
      통과했는데, 오늘 그 날짜가 도래하면서 data_age가 1355초로 커져 CB가 확정되고 주문이
      막힌다. 시간이 갈수록 악화되므로 **내일부터는 매일 실패**한다. `TradingPipeline`에
      주입 가능한 시계가 없어 (a) 프로덕션에 clock 주입 (b) `_START`를 현재시각 기준
      상대값으로 변경 중 택일이 필요 — (b)는 15:10 이후 실행 시 마감임박 로직에 걸리는
      다른 시간의존성이 생기므로 (a)를 권장. 리스크 경로 수정이라 별도 합의 후 진행.

## 고도화 4종 구현 — 관측성·자동화·저장구조·계층분리 ([MW0601], 2026-07-30)

- [x] **고도화 1 — 컴포넌트 헬스 토픽 + 상단 신호등**. 이날 이상점 4건이 전부 "죽어도 화면에
      아무 표시가 없다"는 같은 형태였다. `core/health.py` 신설: `HealthReporter`가 `sys.health`에
      10초 주기 heartbeat를 발행하고, 상태 판정(`probe`)은 **근거를 가진 컴포넌트가 스스로**
      한다 — `TickCollector.health()`는 마지막 틱 이후 경과로(임계는 스톨 워치독과 동일한
      120초, WARN은 그 절반), `FeatureEngine.health()`는 발행 정체 + nan_ratio 임계 초과로.
      **정상일 때도 계속 발행**하는 게 핵심이다: "문제 있을 때만 알린다"면 프로세스가 통째로
      죽었을 때 아무 신호도 안 난다(이번 UI 크래시가 정확히 그 경우). Command Center 상단에
      컴포넌트 신호등을 붙였고 **목록을 고정**해 안 들어오는 항목이 "데이터 없음"으로 남게
      했다 — 동적 목록이면 죽은 프로세스의 줄이 화면에서 사라져 사고가 오히려 안 보인다.
      `sys.health`는 여러 컴포넌트가 같은 토픽에 쓰므로 UI 캐시 키를 `health:{component}`로
      분리(안 그러면 마지막 발행자만 남음). 테스트 23건.
- [x] **고도화 2 — 일일 무결성 리포트 자동화**. `ops/integrity_report.py` +
      `scripts/daily_integrity_report.py` 신설, `run_l1_daily.py` 장후 절차에 결선.
      이날 사람이 손으로 파낸 조사(봉 연속성·재기동 횟수·Horizon별 nan_ratio·네이티브
      크래시·태그 집계)를 그대로 코드로 고정. **실측 검증**: 07-29 로그로 돌리자 수동 조사와
      동일하게 결손 35분·최장 공백 29분·L1 6회/G2 5회 재기동을 재현했고, Windows 이벤트
      로그에서 그날 polars 크래시 2건까지 자동 검출했다. 07-27(정상일)은 임계 초과 0건.
      재기동은 **프로세스별로** 센다 — 합치면 "L1 6 + G2 5 = 11"이 되어 원인 프로세스가
      사라진다. 크래시 집계는 Windows 전용이라 다른 OS에서 0건이 아니라 `available=False`로
      "못 셌다"를 명시(L18). 임계 초과 시 종료코드 1. 테스트 21건.
- [x] **고도화 3 — 아카이브 시간대 조각 쓰기 + 장후 통합**. 장중에는
      `{symbol}/{horizon}/{date}/{HH}.parquet`에 쓰고(다시 쓰는 범위가 하루치 405행 →
      한 시간치 60행으로 고정, O(n²) 해소), 장 마감 후 `compact_day()`가 조각을 하루 1개
      `{date}.parquet`로 합치고 조각을 지운다. **저장 포맷 변경이 아니다** — 하루가 끝나면
      물리 배치가 조각화 이전과 정확히 같아져 Digital Twin·백테스트·Replay는 영향이 없다.
      당일 데이터를 보는 소비자(UI·웜스타트·무결성 리포트·Replay)는 전부
      `read_day()`/`day_sources()`/`available_days()`로 이관 — 경로를 직접 조립하면 장중에
      그날 데이터가 통째로 안 보인다. 통합본을 원자적으로 먼저 쓰고 **그 다음** 조각을
      지운다(반대면 중간에 죽었을 때 데이터 소실). 테스트에서 물리 경로 단언을 전부
      `read_day()` 기반 동작 단언으로 바꿨다 — 내부 배치 변경마다 테스트가 깨지지 않게.
      부수 발견: UI 봉 캐시 키에 `bar_dir`이 빠져 있어 서로 다른 아카이브 루트가 충돌했다(테스트가
      실제로 잡아냄) — 키에 추가. 테스트 10건 추가.
- [x] **고도화 4 — 탐지·복구 소유권 정리**. 계층을 `data/collector.py` 모듈 docstring에
      명문화: **L1 수집기 = 데이터 흐름의 탐지 + 복구**(마지막 틱 시각을 아는 유일한 곳),
      **G2 CB 모니터 = 그 위에서 매매 판단**(포지션·게이트웨이를 아는 유일한 곳).
      두 판정을 런타임에 묶지는 **않았다** — 리스크 경로 변경이라 별도 합의가 필요하고, 한쪽
      버그가 조용히 다른 쪽을 오염시키지 않는 편이 안전하다. 대신 어긋남을 사후에 반드시
      드러내도록 `analyze_data_flow_ownership()`을 무결성 리포트에 넣었다: ① 스톨 감지는
      됐는데 재연결 0회(탐지는 됐으나 복구 실패) ② CB 확정은 났는데 L1 단절 흔적 0건(거래는
      멈췄는데 데이터 흐름은 아무도 안 고침 — 07-28·29의 30분 공백과 정확히 같은 구조).
      테스트 6건.

- [x] **미해결 ③ 해소 — 장중 L1/G2 재기동으로 신규 코드 전면 적용 (12:12, 사용자 승인)**.
      경위: F1에서 UI 읽기를 바이트 복사로 바꾼 뒤에도 10:01:21에 **같은 fault offset으로
      재크래시** → Parquet 꼬리 매직(PAR1) 검증 추가 → 10:19:41에 **또 재크래시**. 프로브로
      확인한 바 매직 검사는 크기가 바뀌는 찢어진 상태를 확실히 걸러내지만(20초에 8,442건 차단)
      창을 완전히 닫지는 못했다. 근본 원인은 하나 — **다른 프로세스가 제자리 덮어쓰기 중인
      파일을 읽는 것은 원리적으로 안전할 수 없다.** 읽기측 방어는 창을 줄일 뿐이고 실제 해법은
      F1의 원자적 교체인데, 그건 그때까지 돌던 L1이 구코드라 적용돼 있지 않았다. 사용자 승인
      후 12:12에 L1·G2를 재기동해 해결.
      **재기동 실측 결과** — ① 데이터 공백 **1분**(12:12봉 1개, 08:45~12:14 그 외 결손 0)
      ② **웜스타트가 콜드스타트를 완전히 대체**: 재기동 직후 첫 발행 nan_ratio가 1m 0.033 /
      3m 0.025 / 5m 0.025 / 15m 0.066 — 같은 날 아침 콜드스타트가 0.959에서 출발해 1m조차
      30분 걸렸고 15m는 하루 종일 0.96이었던 것과 대비된다(07-29 15m 최저치 0.678도 크게 하회)
      ③ `sys.health` heartbeat 3종 전부 수신 확인(l1.collector OK / l1.feature_engine OK /
      g2.pipeline OK) ④ 조각화 동작 확인(`1m/2026-07-30/12.parquet` 생성, 기존 통합본과
      `read_day()`로 병합돼 209행 정상) ⑤ UI HTTP 200, 이후 크래시 0건.
      **작업 중 실수 1건**: 프로세스 종료 시 명령줄 매칭을 `run_l1_daily|run_g2_paper`로만 걸어
      **그 문자열을 포함한 내 PowerShell 자신까지 종료**시켰다(`stop_l1_daily.bat`가 `-ne $PID`로
      자기 자신을 제외하는 바로 그 이유 — 그 주석을 읽고도 같은 함정을 밟았다). L1/G2 종료는
      의도대로 완료됐고 Redis·데이터 영향은 없었으나, 임시 스크립트라도 자기 PID 제외는 필수.

## TradingPipeline 벽시계 주입 + 장전 세션 게이트 ([MW0601], 2026-07-30)

- [x] **미해결 ② 해소 — `TradingPipeline`에 벽시계 주입**. `now: Callable[[], datetime] =
      now_utc` 생성자 파라미터 추가. 조사 과정에서 확인된 사실이 하나 있다: `handle_futures_view()`
      의 데이터 신선도는 **파이프라인 시계가 아니라 `view.ts_utc`**(그 뷰가 대표하는 시장
      데이터의 시각)에서 온다 — 즉 주입만으로는 깨진 4건이 안 고쳐진다. 실제 시계를 쓰는 건
      `watch_circuit_breaker_forever()`(데이터가 안 오는 동안을 재는 순수 벽시계 폴링)뿐이라
      거기를 `self._now()`로 바꾸고, 검증을 위해 1틱을 `observe_circuit_breaker_tick()`으로
      추출했다(루프에서 떼어내면 주입 시계와 짝지어 실시간 대기 0초로 재현 가능).
      **테스트 4건의 실제 수정**: `_view()`의 기본 타임스탬프가 `FuturesView` 필드 기본값
      (`now_utc()` = 실제 벽시계)이었던 것을 워밍업 봉과 같은 타임라인(`_START + 20분 5초`)으로
      고정. 커밋 `b1a366d`(07-27) 당시엔 `_START`가 미래 날짜라 data_age가 음수 → 통과했으나
      2026-07-30이 도래하며 1355초가 되어 CB 확정 + R11 차단으로 4건이 한꺼번에 깨졌던 것.
      `FuturesView.ts_utc`의 의미상으로도 봉과 같은 타임라인에 있는 게 맞다 — 이제 언제 돌려도
      결과가 같다. **전체 947건 통과(오늘 처음 0 실패)**.
- [x] **미해결 ① 해소 — 장전 08:45~09:00 방침 확정(사용자 결정)**: **웜업만 하고 봉차트는
      그린다. 진입·청산 등 거래는 하지 않는다.** 즉 데이터는 버리지 않고(수집·아카이브·차트·
      피처 웜업에 그대로 사용) 주문만 안 낸다.
      구현: `handle_futures_view()`의 `decision.intent` 발행 **뒤**에
      `EventCalendar.is_regular_session()` 게이트를 둔다 — 판단은 평소대로 수행·발행하되 주문
      제출만 건너뛰고 `OutOfSessionNoTrade`로 남긴다. 게이트를 결정 앞이 아니라 뒤에 둔 이유는
      "장전에 시스템이 무엇을 하려 했는가"가 화면·로그에 남아야 개장 직후 동작을 예측·검증할
      수 있기 때문. 반개구간 [09:00, 15:35)이라 **마감 이후도 같은 판정이 함께 막는다**.
      `event_calendar` 미주입이면 비활성(재생/스모크 기존 동작 유지 — R4/R6·CB와 같은 옵션 패턴).
      비상 청산(KillSwitch·CB)은 게이트 대상이 아니다 — 안전 이벤트 반응이고 장 밖에서는 어차피
      거래소가 거부한다. 실경계 확인: 08:45·08:59 생략 / 09:00·12:30·15:34 허용 / 15:35·15:40 생략.
      테스트 9건 신규(장전 차단·정규장 통과·장전 봉이 웜업에 기여·마감후 차단·미주입 시 비활성
      + 시계 주입 3건).
      **부수 확인**: ATR은 15봉이 있어야 값이 선다(실측) — 08:45 첫 틱 기준 15분이면 09:00
      직전에 워밍업이 완성되므로, 장전 구간을 수집에 쓰는 것만으로 **개장 첫 뷰부터 바로 거래
      가능한 상태**가 된다(테스트로 못박음).
- [x] **G2 재기동(12:30)으로 게이트 적용**. L1은 `TradingPipeline`을 쓰지 않아 재기동 불필요.
      재기동 후 확인: 봉 연속성 이상 없음(12:12 L1 재기동분 1분 공백 외 0), heartbeat 3종 정상.
      이때 `l1.feature_engine`이 **WARN — 30m NaN 35%**를 실제로 보고하기 시작했다: 웜스타트가
      30m를 52봉만 채웠고(용량 130) 그 부족분이 그대로 드러난 것 — 고도화 1이 의도한 대로
      "전에는 로그를 직접 파야 보이던 것"이 화면 신호등에 뜨는 첫 사례.

## 2026-07-31 일일점검 대응 — CB 복구·스톨 오탐·UI 크래시·리포트 정확도 ([MW0601], 2026-08-01)

점검 대상: `logs/{l1_daily,g2_daily,ui}_20260731.log`, `daily_integrity_20260731.json`,
`self_eval_2026-07-31.json`, `data/bars/A05608/1m/2026-07-31.parquet`(380행), Windows 이벤트로그.
그날 프로세스는 무중단(L1·G2 재기동 0회, CRITICAL 0, 정상 종료)이었으나 **화면이 오후 3시간
사라졌고, 주문 게이트가 08:53부터 종료까지 6시간 42분 풀리지 않았으며, 오후 1시간 동안 1m
피처의 33%가 NaN**이었다. 셋의 공통 뿌리는 하나다 — 시스템이 "체결이 뜸한 시장"을 "데이터가
끊긴 시장"으로만 해석한다.

**그날의 시장 자체가 평소와 달랐다(실측)**: A05608이 10:06에 51814틱(=1036.28)을 처음 찍은
뒤 하루 종일 그 값을 1틱도 못 넘겼고, 14:21부터 마감까지 **전 봉이 o=h=l=c=51814, 분당
1~17계약**이었다(상한가/일방시장에 준하는 상태). 전일 종가 43473 → 당일 시가 ~49500(+13.9%)
→ 고가 51814(+19.2%). 장전(08:45~09:04)은 20봉이 전부 46633에 **완전히 고정**돼 있다가
09:05에 49488로 6.1% 점프했다 — 07-29·07-30 장전은 실제로 움직였고 거래량도 500대였으므로
(그때 "실체결로 보인다"고 판단한 근거) **날마다 성격이 다르다**는 것이 확인된 셈이다.

- [x] **P0-1 CB 복구 경로 대칭화 (가장 위험했던 결함)**. 그날 `CircuitBreakerConfirmed` 5회에
      `Resumed`는 3회뿐이었고 `gateway resumed` 로그는 **하루 종일 0줄**이었다. 원인 2개:
      **(a)** `observe_circuit_breaker_tick()`(벽시계 워치독)이 `just_confirmed`만 처리하고
      `just_resumed`를 아예 안 봤다. `gateway.resume()`은 `handle_futures_view()`에만 있었는데,
      Registry에 live 번들이 0개라 `intel.futures`가 발행되지 않아 **그 경로가 하루 종일 한
      번도 안 불렸다** — 즉 halt를 거는 경로만 살아 있었다.
      **(b)** `just_resumed`가 `new_phase == NORMAL`을 요구했다. 봉은 "다음 분의 첫 틱"이 와야
      확정되므로 한산 구간에서 데이터가 돌아와도 첫 재평가의 `data_age`가 WARNING 대역
      (90~150초)에 떨어지는 일이 흔하고, 그러면 CONFIRMED→WARNING→NORMAL로 **로그 한 줄 없이**
      빠져나간다(15:07:30의 Suspected 로그가 `previous == WARNING`일 때만 나오는 코드라는 점이
      그 증거).
      수정: `just_resumed = previous == CONFIRMED and new != CONFIRMED`(CONFIRMED에서 나가는
      모든 전이 = 해제, 조기 재개 위험은 재진입 관망 10분이 흡수하고 악화되면 `just_confirmed`가
      다시 발동), Suspected 로그도 상승 진입 전부로 확대. 정지·청산·재개·발행 실행을
      `_apply_circuit_breaker_event()` **하나로 합쳐** 두 호출 경로가 갈릴 구조 자체를 없앰.
      `CircuitBreakerStatus.gateway_halted` 신설 — 추정 phase와 **실제 게이트 상태**를 나란히
      실어 UI 배지에 "주문 게이트 정지 중"으로 표시(그날 6시간 42분을 아무도 못 본 이유).
      테스트 10건(CONFIRMED→WARNING/SUSPECTED 해제·부분해제 후 재악화·워치독 단독 왕복·
      워치독 해제 청산·발행 순서).
- [x] **P0-2 CB 판정에 수집기 heartbeat 결선 (계층 분리 원칙 부분 완화 — 보고한 권고안대로)**.
      `data_age` 하나로는 "데이터가 안 온다"와 "거래가 없다"를 **원리적으로** 구분할 수 없다.
      `observe(collector_healthy=...)` 추가 — `TradingPipeline.run_forever()`가 `sys.health`를
      함께 구독해 `l1.collector` heartbeat를 받아두고, OK인 동안엔 SUSPECTED까지만 올리고
      **CONFIRMED 승격을 막는다**(SUSPECTED는 화면·로그에만 남고 거래를 안 막지만 CONFIRMED는
      `gateway.halt()`를 건다 — 오판의 대가가 비대칭이라 승격만 막음). heartbeat가 없거나
      끊긴 지 30초 넘으면 **"모름"으로 다뤄 억제하지 않는다**(수집기 사망을 정상으로 오해하면
      진짜 단절에 CB가 영영 안 걸린다). `collector_healthy=None`이면 기존 동작 그대로라
      재생·스모크 회귀 없음. 컴포넌트 이름은 `core/health.py`의 `COLLECTOR_COMPONENT` 상수로
      단일화(발행측·구독측 문자열이 갈리면 조용히 결선이 끊긴다). 테스트 8건.
- [x] **P0-3 스톨 워치독 적응 임계 — 치료가 병보다 비쌌다**. 고정 120초의 근거였던 07-30 실측
      "정규장 최저 54틱/분"이 시장 전체를 대표하지 않았다: 07-31 오후엔 분당 1~5틱이 **정상**
      이었다. 그날 6회 강제 재연결은 매번 5~8초 만에 성공했지만 **재연결 후 첫 틱까지가**
      14:53:37→14:56:14(2분 37초), 15:08:26→15:11:06(2분 40초)로, 결손 30분·최장 공백 8분
      (15:04~15:13)의 상당 부분이 워치독이 만든 것이었다.
      수정: `임계 = clamp(최근 30분 최장 무틱 간격 × 2, 하한 120초, 상한 600초) ×
      2^(연속 무효 재연결)`. **최장(max)** 간격을 쓰는 이유는 중앙값이면 "가끔 2분씩 조용한
      시장"에서 임계가 여전히 낮아 같은 오탐이 나기 때문. 바쁜 구간에선 최장 간격이 1초 미만이라
      자동으로 하한 120초로 수렴 — 07-28·29의 진짜 30분 공백을 2분에 잡던 성질은 유지된다.
      간격 이력은 `_tick_times`가 아니라 **별도 `_intervals`**에 쌓는다(초기 구현의 버그를
      테스트 설계 중 발견): 타임스탬프에서 역산하면 **단절 구간 자체가 "이 시장은 이만큼
      조용하다"는 증거로 둔갑해 임계를 스스로 밀어올린다**(다음 장애 감지가 느려짐). `reset()`은
      연결 기준선만 지우고 시장 이력은 남긴다. `CollectorTickStall` 로그에 임계·최근 최장 간격·
      최근 1분/5분 틱수·연속 스톨 횟수를 추가(그전엔 "133초간 틱 없음"뿐이라 사후에도 장애인지
      한산인지 판단 불가). `TickCollector.health()`도 같은 적응 임계를 쓰게 맞춤 — 안 그러면
      화면만 CRITICAL로 붉어지고 재연결은 안 일어나는 "두 판정이 어긋난" 상태가 된다. 테스트 10건.
- [x] **P1-1 UI 크래시 — 3일째 같은 fault offset, 이번엔 구조로 막았다**. 07-31 크래시 6건
      (10:42·11:58·12:13·12:21·12:31·12:35)이 전부 `_polars_runtime.pyd +0x083973c7`,
      0xc0000005로 **07-29 3건·07-30 1건과 완전히 동일한 주소**였다 — 07-30의 F1 수정(원자적
      쓰기 + PAR1 매직 검사)이 이 경로를 못 덮었다는 뜻.
      단서 3개: (1) 같은 날 UI 로그에 `partially initialized module 'numpy'`가 3건(두 스레드가
      동시에 numpy 최초 임포트를 밟을 때만 나오는 형태) (2) 07-30 기록의 "첫 polars 크래시가
      `st.fragment(run_every=5)` 도입 53분 뒤, 그 이전 날짜엔 0건" (3) 크래시가 기동(08:35)이
      아니라 **10:42부터** 시작(Streamlit은 브라우저 세션이 붙어야 스크립트를 돌린다 = 사람이
      화면을 연 시점). → 남은 원인은 "찢어진 파일"이 아니라 **프로세스 내 동시성**으로 추정.
      대응: `app.py`에 numpy **명시적 선임포트**(지연 임포트 레이스 차단), `_BarFileCache`에
      프로세스 전역 `threading.Lock`(polars 네이티브 호출 직렬화), `_candlestick_figure`가
      polars Series 대신 **순수 파이썬 리스트**를 plotly에 넘김(크래시의 파이썬 레벨 흔적이
      전부 `go.Candlestick` → `is_homogeneous_array` → `np.ndarray` 경로였다). x축은 naive KST
      벽시계로 못박아 07-29 UTC 축 버그 재발도 차단.
      **`_UI_MAX_RESTARTS`의 의미도 고쳤다**: 하루 누적 절대치 → **최근 1시간 롤링 창**, 그리고
      한도를 넘어도 **반환하지 않고 재기동만 접는다**(매 점검마다 `on_gave_up`으로 계속 알림,
      사람이 수동으로 띄우면 정상 감시 복귀). 그날 12:35:53에 한도가 소진되자 **그날 다시는
      안 떴고**(3시간 무화면) 그 사실을 알리는 신호는 ERROR 로그 한 줄뿐이었는데 **그 로그를
      볼 화면이 바로 그 죽은 UI**였다. 1시간 창이었다면 07-31엔 애초에 소진되지 않았다(12:35
      시점 최근 1시간 내 재기동 4회 < 5). `run_l1_daily.py`가 포기 시 `sys.health`에 CRITICAL을
      발행하도록 결선. 테스트 5건.
      **재현 실패 — 해결로 간주하지 않는다**: `scripts/probe_ui_concurrency.py` 신설(읽기 N
      스레드 + 쓰기 1스레드 동시 부하, 판정은 종료 코드). 수정 후 통과(읽기 717회·차트 717회·
      크래시 0)했으나, **수정 전 코드 경로를 되살린 대조군도 8스레드 30초·차트 6,347회에서
      안 죽었다.** 즉 이 프로브는 아직 원인을 재현하지 못한다(numpy 레이스는 스크립트가
      `messiah.ui.app`을 임포트하는 순간 메인 스레드에서 끝나버리고, Streamlit ScriptRunner의
      재진입 구조는 단순 스레드 풀로 재현이 안 됨). 프로브 docstring에 이 음성 결과를 명기.
      **진짜 판정은 다음 거래일 `daily_integrity_*.json`의 `native_crashes` 0건**으로 한다 —
      07-29·07-30에 두 번 "고쳤다"고 판단했다가 같은 오프셋으로 재발한 이력이 있다.
- [x] **P1-3 리포트 정확도 — 그날 리포트 자체가 두 군데서 틀렸다**.
      **(a) 크래시 과대 계상**: "8건" 중 06:34:53·06:36:19 2건은 python.exe **3.10**(.venv는
      3.12) + `KERNELBASE.dll` + `0xc06d007f`로, MESSIAH 기동(08:35) 두 시간 전에 이 PC의 다른
      프로젝트가 낸 것이었다. 집계 창을 **그날 첫 SessionStart 이후**로 좁히고, 로캘 문자열
      정규식 대신 **`Properties` 배열 직접 읽기**로 전환([0] 프로세스명 · [1] **버전** ·
      [3] 결함 모듈 · [6] 예외코드 · [7] **결함 오프셋**). 버전이 있어야 남의 파이썬을 거르고,
      오프셋을 남겨야 "같은 주소에서 또 죽었다"를 사후 대조할 수 있다. 실데이터 재생성 결과
      8건 → **6건**, 오프셋까지 기록됨.
      **(b) `n_trades` 오해가 반복됐다**: 07-29에 한 번 오해하고 주석으로만 정정했는데,
      07-31 리포트의 `n_trades=3`이 **주문 0건·체결 0건인 날**에 같은 오해를 또 만들었다(실제
      의미는 수익률 표본 3개 = 거래일 3일). 주석은 리포트 JSON을 읽는 사람에게 안 따라간다 —
      필드를 `n_return_samples`로 개명하고, 진짜 체결 수는 `n_fills`로 분리하되 셀 수 없으면
      **0이 아니라 None**(모르는 것과 없는 것의 구분, L18). `agenda.py`는 옛 파일의
      `n_trades`도 함께 읽어 하위호환.
      **(c) 새 불일치 규칙**: "CB 확정 N회 대 해제 M회 — 짝이 안 맞음". 기존 규칙 둘은 "한쪽에
      흔적이 **아예** 없을 때"만 봐서 07-31을 통과시켰다(그날 findings 0건). "재연결 N회 대 CB
      확정 M회"류의 개수 비교도 후보였으나 **진짜 단절이 나서 L1이 복구하고 CB가 정지시킨 정상
      시나리오와 구분이 안 돼** 채택하지 않았다(오탐 규칙은 없느니만 못하다 — 실제로 넣었다가
      기존 테스트 2건이 정상 시나리오에서 발화하는 걸 보고 철회).
      **(d) 신규 지표**: 가격 고정 분 수(o=h=l=c)·장전 봉 수, UI 재기동 포기를 임계 초과로
      승격("관측 공백"), 시장 상태 findings 섹션. 실데이터 재생성으로 6개 임계 초과가 전부
      정확히 발화하는 것 확인(가격 고정 60분/380봉, CB 짝 불일치 2회, 관측 공백 1회 포함).
- [x] **P1-4 NaN에는 원인이 셋이고 셋은 다른 사건이다**. 그날 15:20 이후 1m NaN 33% 경고 15회는
      전부 **퇴화**(가격 고정으로 표준편차 계열이 정의 불가)였는데 결측과 같은 문구로 찍혀 매번
      수집 장애를 먼저 의심하게 만들었다. `FeatureDegenerate` 태그 신설(최근 20봉 종가가 전부
      동일하면) + `cause` 필드("degenerate"/"missing"). 판정 창 20은 W_STD의 중간값 — 최솟값
      5는 정상 시장에서도 우연히 걸리고, 최댓값 60은 07-31 15:20의 실제 형태("최근 20봉만 고정,
      그 앞은 움직임")를 못 잡는다. **실데이터 검증**: `run_replay.py --symbol A05608
      --start/--end 2026-07-31`로 그날 아카이브를 재생하니 문제의 15건이 정확히
      "1m 최근 20봉 종가가 전부 51814틱으로 고정"으로 재분류됨. 테스트 3건.
- [x] **P1-2 장전 세션 구분 + 시세 정합성 (스키마 변경)**. 07-30에 "장전은 웜업만, 거래는 안
      한다"고 정했지만 그 결정은 **주문만** 막았다 — 아카이브·웜스타트·차트엔 그대로 들어갔고,
      09:05봉은 `o=46633, h=49488`로 봉 하나가 6.1% 범위인 합성 봉이 되어 ATR·변동성 계열을
      오염시킨 뒤 다음날 웜스타트로 흘러갔다. `BarSession`(REGULAR/PRE_OPEN) 신설 —
      `BarClosed.session`, 합성봉은 구성 1분봉 중 하나라도 장전이면 장전(09:00을 걸치는 봉은
      장전 프린트를 실제로 품고 있다). `MinuteBarAggregator.PRICE_JUMP_RATIO`(3%) 초과 시
      `quality_ok=False` + `TickPriceJump` 로그(`quality_ok`는 여러 원인이 공유하는 값이라
      그것만으론 사후에 이유를 복원 못 한다). **버리지 않고 표시만** 한다 — 파기는 되돌릴 수
      없고 소비자별 정책은 나중에 바꿀 수 있다.
      **스키마 변경이 조용한 데이터 사고로 번지는 경로를 막았다**: 컬럼이 추가되는 그날엔
      통합본은 옛 스키마·새 조각은 새 스키마인 상태가 **하루 안에서도** 실제로 생기는데, 기본
      `pl.concat`은 거기서 예외를 던지고 그러면 그날 데이터가 UI·웜스타트·리포트에서 통째로
      사라진다 → `how="diagonal_relaxed"`로 전환, 없는 값은 REGULAR로 읽음. **실데이터 검증**:
      기존 `2026-07-31.parquet`(옛 스키마 380행) 단독 읽기·새 조각 혼재(381행)·`compact_day`
      전부 정상 확인. 테스트 9건.

**검증 총계**: 테스트 947 → **996건**(신규 49건) 전부 통과, ruff 신규 지적 0건(기존 I001 48건은
그대로 — 별개 항목), 스모크 3종(`run_replay.py` 실아카이브·`run_full_path_smoke.py`·
`run_phase5_smoke.py`) 전부 통과.

**남은 갭(다음 점검에서 확인할 것)**:
- UI 크래시 근본 원인 미확정 — 다음 거래일 `native_crashes` 0건으로만 판정 가능(위 P1-1).
- 적응 임계 파라미터(창 30분·여유 2배·상한 600초·완화 10틱)와 `PRICE_JUMP_RATIO`(3%),
  `_DEGENERATE_WINDOW`(20) 전부 **미검증 초기값** — 실측이 쌓이면 재조정.
- `n_fills`는 여전히 항상 None(Position Reconciler 부재) — 실제 체결 손익 집계는 그 컴포넌트
  결선 이후.
- 07-31 장전 프린트가 **왜** 고정값이었는지는 미확정(예상체결/스테일 값 여부). 이제 `session`
  라벨과 `TickPriceJump` 로그가 매일 근거를 쌓으므로 표본이 모이면 판정 가능.
- `quality_ok=False`·`session=PRE_OPEN` 봉을 웜스타트/학습에서 **뺄지**는 아직 미결정 —
  이번엔 표시만 했다(파기 불가역성 때문). Trainer 결선 시 정책 확정 필요.
- 고도화 A(MarketState 1급 개념)·B(수정 유효성 자동 검증)·C(관측을 UI에서 분리)·D(G2 마일스톤
  재정의)는 이번 스코프 밖 — 보고서의 고도화 방안 참고.

## 2026-08-03 일일점검 대응 — UI 크래시를 "가두기"로 전환 + 리포트 관측 공백 ([MW0601], 2026-08-03)

**근거 자료**: `logs/l1_daily_20260803.log`(751줄), `logs/g2_daily_20260803.log`,
`logs/ui_20260803.log`, `logs/daily_integrity_20260803.json`, `logs/self_eval_2026-08-03.json`,
Windows 이벤트로그(ID 1000/1001) + WER `Report.wer` 원본.

### 점검 결과 요약

데이터 계층은 **무결점**이었다 — 1m 410봉 08:45~15:34, 결손 0분, 가격 고정 0분, NaN 1.65%
안정, CB 0건, `data_flow_findings`/`market_findings` 0건. 반면:

- **Streamlit 3회 기동 = 초기 1 + 네이티브 크래시 재기동 2**(11:25:18·14:20:18). fault offset은
  `_polars_runtime.pyd +0x083973c7`, 0xc0000005 — **5거래일 연속 바이트 단위로 동일**.
- 전략 계층은 5거래일째 아무것도 안 했다(`live 번들 결선: []`, 판단·주문·체결 태그 0건).

### P1-1 07-31 수정이 왜 실패했나 — 락의 적용 범위가 절반이었다

07-31에 "polars 네이티브 호출 직렬화"를 목표로 `_BarFileCache`에 프로세스 전역 락을 넣었는데,
**코드상 그 목표가 성립하지 않았다**. 락은 Parquet 파싱만 감쌌고 `load()`는 캐시된
`pl.DataFrame` **객체 자체**를 돌려줬다 — 실제 소비는 전부 락 밖이었다:

    bars.is_empty()                      # app.py:584
    bars[col].to_list()            × 4   # _candlestick_figure
    bars["bar_open_kst"].to_list()       # _candlestick_figure

즉 여러 스레드가 같은 폴라스 객체를 동시에 만지는 경로가 그대로 남아 있었다.

다만 **부분적으로는 유효**했다(실측): 크래시 6건→2건, 생존시간 07-30 약 6분 → 07-31 최초
2h07m 후 가속(76→15→8→10→4분) → **08-03은 2h50m/2h54m로 가속 자체가 소멸**. 락·리스트 변환이
뭔가를 막긴 했다.

**미확정으로 남긴 것**: 08-03 두 크래시가 모두 5분봉 경계 +18초였지만, 07-30·07-31 크래시
시각(12:13:23·12:21:08·11:58:53 …)엔 그 패턴이 안 맞는다 — 표본 2개짜리 우연으로 보고 진단
근거에서 뺐다.

### 구현: P0-1(a) → P0-2 → P0-1(b) → P1 (사용자 지정 순서)

- [x] **P0-1(a) `faulthandler` 상시화 — 5일치 증거 공백을 메운다** (`core/crash_forensics.py`).
      세 번의 오진은 전부 "네이티브 크래시라 파이썬 스택이 안 남는다"에서 왔다. Windows에서
      CPython faulthandler는 SEH 핸들러를 걸어 **0xc0000005에도 동작**한다 — `all_threads=True`면
      죽는 순간 전 스레드 스택이 나오므로 "polars에 동시에 들어간 스레드가 몇 개인가"라는
      미해결 질문이 크래시 **한 번**으로 판정된다. **실측 검증**: `ctypes.string_at(0)`으로
      실제 access violation을 일으켜 덤프 + 2개 이상 스레드 스택 확인. UI·l1_daily·g2_paper
      3개 진입점 전부 결선. 멱등(Streamlit 5초 재실행 안전), 실패해도 본 기능 안 막음.
      마커는 **ASCII**로 쓴다 — 자식은 cp949로 쓰는데 런처는 utf-8로 파일을 열어 한글이 깨진다.

- [x] **P0-2 락 밖으로 polars 객체를 안 내보낸다** (`ui/bar_series.py`). 캐시가 프레임이 아니라
      **불변 `BarSeries` 스냅샷**(전부 파이썬 기본형)을 들고 있게 바꿨다. 변환은 락 안에서
      한 번. "락을 잘 걸었나"라는 **규율 문제가 "락 밖에 polars 타입이 존재할 수 없다"는 타입
      문제로** 바뀌었다. 부수 효과로 5초마다 반복하던 `to_list()` 재변환도 사라졌다.

- [x] **P0-1(b) 추측을 그만두고 크래시를 가둔다** — 네 번째 가설 대신 구조를 바꿨다.
      봉 파싱이 **자식 프로세스**에서 일어난다(`ui/bar_reader.py` → `data/bar_export.py`).
      자식이 죽으면 부모에겐 `returncode != 0`일 뿐이고, 이미 있는 "읽기 실패 → 직전 성공본"
      경로로 흡수된다. **크래시를 피하는 게 아니라 가둔다 — 원인을 끝내 못 밝혀도 화면은 산다.**
      - 보고서 초안의 1안(버스 발행)을 안 쓴 이유: **REPLAY가 과거 날짜 parquet을 직접 읽어야**
        해서 버스만으로는 UI에서 polars가 안 없어진다. 자식 프로세스는 LIVE·REPLAY를 다 덮는다.
      - `data/bar_paths.py`로 **polars 없는 경로 계층**을 분리했다(`day_sources`/`available_days`
        /`day_signature`). 이게 없으면 "파일 바뀌었나" 확인하려고 archiver를 임포트하는 순간
        polars가 부모에 딸려 올라와 분리가 무의미해진다. `ParquetArchiver`는 여기에 위임(계약 불변).
      - **실측 검증**: 깨끗한 인터프리터에서 `messiah.ui.app` 임포트 후
        `'polars' in sys.modules == False`, `'messiah.data.archiver' in sys.modules == False`.
        테스트로 못박음(`test_ui_process_never_loads_polars`).
      - 비용 실측: 자식 기동+polars 임포트 약 0.8~0.9초. 캐시 미스에만 발생 = Horizon당 봉
        마감 주기 1회(5m 차트면 5분에 1번). 5초 재렌더 대부분은 캐시 히트라 자식을 안 띄운다.

- [x] **P1-1 UI 재기동을 무결성 리포트 1급 임계로**. 08-03에 UI가 2번 죽었는데 breach가 난 건
      순전히 `native_crashes`(**Windows 전용**) 덕분이었다 — 다른 OS이거나 파이썬 레벨로 죽었으면
      화면이 두 번 사라진 날이 "임계 초과 0건"으로 지나갔다. **관측 도구가 관측 공백을 못 보는**
      상태였다. `ui_restarts` 임계(0) 추가 + 요약에 전용 줄.

- [x] **P1-2 리포트 문구 정확도 2건**. ⓐ 기동 횟수를 그대로 "재기동"이라 불러 정상일에도
      "재기동 1회"가 찍혔다 → `starts_by_process`/`restarts_by_process` 분리(재기동 = 기동 - 1),
      임계도 1→0. 사람이 매일 그 줄을 무시하는 법을 배우면 진짜 재기동도 같이 묻힌다.
      ⓑ 체결 0건인 날의 `slippage_realized_ticks: 0.0`이 "슬리피지 0틱"이라는 **성과처럼** 읽혔다
      → `None`(잴 수 없었다). 07-31 `n_trades=3`과 정확히 같은 실패 형태.

- [x] **P1-3 UI 런처 로그 파일 핸들 누수**. `launch_command_center()`가 실패 경로에서만 닫아,
      재기동마다 핸들이 하나씩 남았다(08-03에 2개) → `with`로 성공 경로도 닫는다.

**검증 총계**: 테스트 996 → **1015건**(신규 19건) 전부 통과. ruff E402/E501 0건(app.py의 E402는
`pyproject.toml` per-file-ignores로 이유와 함께 면제 — 무거운 임포트 **전에** 무장해야 함),
I001은 48 → 47(신규 0). 스모크 3종(`run_full_path_smoke.py`·`run_phase5_smoke.py`·`run_replay.py`
08-03 실아카이브 714건) 전부 통과. 실아카이브 왕복 실측(1m 410봉/5m 82봉/30m 15봉, KST 벽시계,
무결성 리포트 수치와 일치). 실제 런처→Streamlit 기동 경로에서 HTTP 200 + 마커 로그 기록 확인.

**08-03 리포트 재생성 결과**(수정 효과 실측):

    l1_daily 기동: 1회 · 재기동 0회      ← 예전엔 "재기동 1회"(헛경고)
    Command Center UI 자동 재기동: 2회    ← 예전엔 이 줄 자체가 없었음
    ⚠ 임계 초과: 네이티브 크래시 2건 / UI 자동 재기동 2회 — 그 사이 관측 공백

**남은 갭(다음 점검에서 확인할 것)**:
- **UI 크래시 근본 원인은 여전히 미확정.** 이번 작업은 원인 규명이 아니라 **영향 격리**다.
  판정은 다음 거래일에 ⓐ `native_crashes` 0건 **또는** ⓑ 자식에서 크래시가 나고 UI는 살아남은
  흔적(`ui_*.log`의 "네이티브 크래시" 경고 + 화면 생존)으로 한다. ⓑ면 격리는 성공, 원인은 계속
  미상 — 그때 `crash_forensics` 덤프의 스레드 수가 동시성 가설을 판정한다.
- **전략 계층 5거래일 무운영**(`live 번들 결선: []`, 판단·주문·체결 0건)은 이번 스코프 밖.
  self-check/무결성 breach 승격(보고서 P2-1)과 G2 마일스톤 재정의(고도화 C)가 다음 후보.
- 장후 UI 생존(15:35~15:40 무감시, 15:40 종료로 장후 리뷰 화면 없음)도 미착수(보고서 P2-2).
- 자식 프로세스 타임아웃 30초는 **미검증 초기값** — 실측이 쌓이면 재조정.

## 고도화 4종 구현 — 포렌식·수정검증·마일스톤·관측분리 ([MW0601], 2026-08-03)

2026-08-03 일일점검 보고서의 고도화 방안 A~D를 전부 구현했다. 착수 순서는 D→B→C→A
(D·B가 같은 파일을 건드려 먼저, A가 가장 큼).

### D. 크래시 포렌식 상시화 (`ops/crash_dumps.py`)

- [x] **오늘 손으로 한 조사를 코드로 고정했다**. 08-03 점검에서 UI 크래시 2건의 정체를 밝히려고
      Windows 이벤트로그(ID 1000/1001)를 뒤지고 WER `Report.wer` 원본을 열어 fault offset을
      과거 4일치와 대조했다 — 그 결론("5거래일 연속 같은 주소")은 **매일 자동으로 나왔어야 하는
      사실**이다. 이벤트로그 쪽 절반은 `_collect_native_crashes()`가 이미 자동화돼 있었고,
      이번에 나머지 절반(`core/crash_forensics.py`가 남기는 파이썬 레벨 덤프)을 붙였다.
- [x] **가장 중요한 산출물은 이 한 줄이다**: `"네이티브 크래시 N건인데 faulthandler 덤프 0건 —
      원인 규명 불가 상태"`. 5거래일 동안 세 번 오진한 근본 이유가 정확히 그 상태였는데,
      **증거가 없다는 사실 자체가 리포트에 안 나왔다**. 무장 마커(`[crash_forensics] armed ...`)
      유무와 크래시 건수를 대조하면 그 공백이 매일 드러난다.
- [x] `log_paths_for()`는 JSON 로그(l1/g2)만 주므로 포렌식은 **UI 로그를 따로 읽는다** —
      5거래일 크래시가 전부 그 프로세스였다.
- **실측 검증**: 08-03 실제 로그로 리포트 재생성 → breach에 "무장 마커 없음" 3건 +
      "크래시 2건인데 덤프 0건" 1건이 정확히 올라왔다(그날은 무장 전이었으므로 옳은 판정).

### B. 수정 유효성 자동 검증 (`ops/fix_verification.py` + `configs/pending_verifications.yaml`)

- [x] **이번 5거래일 실패의 구조적 원인을 없앴다.** 07-30·07-30·07-31 세 번 "고쳤다"고
      판정하고 세 번 재발했는데, 매번 판정 기준 자체는 기록돼 있었다(07-31 기록: *"진짜 판정은
      다음 거래일 native_crashes 0건"*). 문제는 그게 **사람 머릿속과 마크다운 산문에만** 있어서
      다음 거래일에 다시 꺼내 확인하는 일을 아무도 강제하지 않았다는 것 — 그래서 재발을
      "새로운 사고"로 취급하며 또 새 가설을 세웠다.
- [x] 이제 판정 기준을 YAML로 등록하면 장후에 `logs/daily_integrity_*.json` 이력으로 자동
      채점한다: **검증 완료 / 검증 대기(n/N) / 재발 / 기한 초과**. 재발은 `FixVerificationRecurred`
      (ERROR)로 로그에 남고 `agenda.py` §2-1 최상단에 배치된다.
- [x] 설계상 지킨 것 3가지: ⓐ **등록일은 채점 안 한다**(수정은 그날 장 마감 후 들어가므로 등록일
      리포트는 수정 이전의 세계) ⓑ **못 잰 날은 통과로도 위반으로도 안 센다**(`native_crashes.
      available=false`를 0건으로 세면 검증이 그냥 통과, L18) ⓒ **등록부 오타는 시끄럽게 실패**
      (조용히 건너뛰면 "검증 중"이라 믿는 항목이 실제로는 아무것도 안 본다 — 이 모듈이 막으려는
      실패 그 자체).
- [x] 지표는 무결성 리포트에 **실제로 있는 필드만** 연다(임의 표현식 허용 금지 — 등록부가 코드가
      되면 등록부 자체를 검증해야 한다). 현재 8종.
- [x] 오늘자 수정 3건 등록(`ui-crash-isolation`·`ui-restart-observability`·
      `crash-forensics-armed`, 전부 3거래일 연속·기한 08-14).
- **주의**: `검증 완료`로 굳은 항목은 등록부에서 **지워야 한다**. 안 지우면 통과 줄만 쌓여
      정작 봐야 할 `재발`이 묻힌다(DECISION_LOG의 "닫히면 태그를 지운다"와 같은 규율).

### C. G2 마일스톤 재정의 (`models/wiring_completeness.py`)

- [x] **같은 실패의 세 번째를 막았다.** `n_trades`(07-31)와 `slippage_realized_ticks`(08-03)를
      이름·None으로 갈랐는데 **손익 지표 전체**가 남아 있었다 — 4거래일 연속 `sharpe=0.0`이
      찍혔고 그건 "수익도 손실도 없었다"는 측정 결과처럼 읽혔지만, 실제로는 `live 번들 결선: []`
      즉 모델이 하나도 안 붙은 채 파이프라인만 돈 것이었다.
- [x] 결선 완성도를 **단계**로 표현한다: 번들 미결선 → 판단 미발생 → 주문 미발생 → 체결 집계
      불가 → 손익 측정 가능. 앞이 비면 뒤는 볼 필요가 없다는 순서가 이 도메인의 사실이고,
      **첫 번째로 비어 있는 칸이 곧 지금 해야 할 일**이다.
- [x] `SelfEvalReport`에 `pnl_measurable`·`wiring_stage`·`wiring_summary` 추가.
      **기본값 False가 의도다** — 호출자가 결선 상태를 안 넘기면 "측정 가능"이라 주장하지 않는다.
- [x] 계측 지점은 각 계층의 **유일한 관문**에 뒀다: 판단은 `TradingPipeline`(NO_TRADE도 센다 —
      "판단이 나왔나"와 "거래가 나왔나"는 다른 질문), 주문은 `OrderGateway.submit()`의 접수 경로
      (거부·롤백은 안 센다). 호출자마다 세면 경로가 늘 때 조용히 빠진다.
- [x] `agenda.py`는 이제 `pnl_measurable`이 아닌 날 손익 숫자를 **아예 안 찍는다**.
- **실측 검증**: OrderGateway 카운터가 접수 3건만 세고 게이트 정지 중 거부는 안 셈을 확인.
      08-03 리포트는 `pnl_measurable` 필드가 없는 옛 형식이라 "손익 지표 미측정(결선 상태
      미기록)"으로 정확히 표시된다.

### A. 관측을 UI에서 분리 (`ops/status_board.py`)

- [x] **사고를 볼 수단이 사고로 사라지는 구조를 끝냈다.** 지금까지 "지금 시스템이 어떤
      상태인가"를 볼 수단은 Command Center UI 하나뿐이었는데 그 UI가 5거래일 연속 죽었다
      (07-30 32분 무감지, 07-31 3시간 무화면, 08-03 2회). 게다가 매일 15:40이면 워치독이 UI를
      종료해 **장후 리뷰 시점엔 화면이 아예 없었다**.
- [x] UI가 하던 구독을 `run_l1_daily.py`로 옮겨 `logs/status_snapshot.json`에 15초마다 쓴다.
      컴포넌트 헬스·CB phase·게이트 정지 여부에 더해 **UI 자체의 생사**(포트 응답)까지 담는다 —
      화면 없이 화면 상태를 안다. `python -m messiah.ops.status_board`로 터미널에서 즉시 확인.
- [x] **`ui/state_cache.py` → `core/state_cache.py` 이동**. 이 모듈은 UI 의존성이 전혀 없는
      범용 버스→캐시 어댑터인데 `ui/` 밑에 있었다 — 관측 기계장치를 core로 내리는 것이 이
      항목의 정확한 형태다(`ops`가 `ui`를 임포트하는 계층 역전도 함께 해소).
- [x] 신선도 임계는 UI와 **같은 출처**(`core/health.py`)를 쓴다 — 두 판정이 갈리면 화면과
      스냅샷이 서로 다른 말을 하게 된다. 스냅샷 쓰기는 원자적(임시파일+`os.replace`,
      `data/archiver.py`와 같은 이유).
- **실측 검증**: 실제 Redis(6380) 버스로 왕복 — `sys.health`/`sys.circuit_breaker` 발행 후
      스냅샷에 `l1.collector: 정상(OK, 2.0초 전) · 실측 수신 중`, `서킷브레이커: normal`,
      미발행 컴포넌트는 `데이터 없음`으로 정확히 기록됨.

**검증 총계**: 테스트 1015 → **1048건**(신규 33건) 전부 통과. ruff **0건**(E501·I001·구문
전부 — `--fix`가 기존 I001 47건도 함께 정리). 스모크 3종 통과. 신규 CLI 2종
(`fix_verification`·`status_board`) 실행 확인. `agenda.py` §2-1 신설 + §4 손익 미측정 표시 확인.

**남은 갭**:
- 고도화 A는 **관측 분리의 1단계**다 — UI는 여전히 자기 구독을 따로 돌린다. UI가 스냅샷을
  읽게 바꾸면 구독이 한 곳으로 합쳐지지만, 그건 UI 렌더 경로 재작업이라 이번 스코프 밖.
- `consecutive_days: 3`, 상태판 주기 15초, 자식 프로세스 타임아웃 30초는 전부 **미검증 초기값**.
- 전략 계층 무운영(`live 번들 결선: []`)은 이제 **측정**되지만 **해결**되진 않았다 — C는
  "손익 지표를 성적으로 오해하지 않게" 만든 것이지 모델을 붙인 게 아니다.
- `crash-forensics-armed` 검증이 `breaches ≤ 0`이라는 **넓은 그물**을 쓴다(다른 사고에도 반응).
  좁은 지표가 필요해지면 `METRIC_EXTRACTORS`에 무장 여부 단독 지표를 추가할 것.

## 백필로 런웨이 붕괴 + WS 체결 절반 유실 수정 ([MW0601], 2026-08-04)

기동 로그 점검에서 시작해 조사가 세 단계로 번졌다. 순서대로 적는다 — 뒤의 발견이 앞의
결론을 뒤집었기 때문이다.

### 발견 1 — "데이터 부족"이 이미 사실이 아니었다

`live 번들 결선: []`의 원인을 계속 "실측 아카이브가 짧다"로 알고 있었는데, 실제 1분봉
2398건(7거래일)으로 `train_formal_expert()`를 돌리자 **out-of-fold 2334건으로 성공**했다.
`run_formal_expert_training_smoke.py` docstring과 `capability_matrix.md`는 여전히 "데이터
부족 실패가 정상"이라고 적혀 있었다 — 7거래일 전 사실에 멈춘 문서를 근거로 판단하고 있었다.

### 발견 2 — G1 런웨이는 2027-02-20이었고, 백필로 오늘이 됐다

`models/cv.py` 프로덕션 기본값(train 180 + embargo 1 + test 30 = 211 캘린더일)을 아카이브
시작일(2026-07-24)에 더하면 **2027-02-20**이다. 이 숫자가 어디에도 적혀 있지 않았다.

KIS에 국내 선물옵션 분봉 API(`inquire-time-fuopchartprice`)가 있는데 `tr_codes.py`에 차트
계열 엔드포인트가 **하나도 없었다**. 실측 결과:

- VPS/REAL 도메인 둘 다 200 OK, 미니선물도 `FID_COND_MRKT_DIV_CODE="F"`로 조회됨
- 1회 102건, `FID_PW_DATA_INCU_YN="Y"`면 날짜 경계를 넘어 과거로 이어짐
- **만기된 월물의 분봉도 남아 있다** (A05607/A05606/A05603 전부 410봉/일 완주)
- 소급 한계 **2025-12-12** (A05512 이하는 빈 응답)

2025-12-12~2026-08-04 = 235 캘린더일 → **G1 창 1개가 성립**. 런웨이 6.5개월 → 0.

### 발견 3 (최중대) — 수집한 7거래일이 전부 틀린 데이터였다

백필 검증용으로 거래소 공식 분봉과 우리 아카이브를 대조했더니 3거래일 전부 어긋났다:

| 날짜 | 공식 총거래량 | 우리 | 종가 불일치 | 고/저가 불일치 |
|---|---|---|---|---|
| 08-03 | 152,618 | 78,080 (51%) | 104/410 | 129 / 126 |
| 07-31 | 112,521 | 58,942 (52%) | 77/380 | 90 / 93 |
| 07-29 | 212,238 | 103,700 (49%) | 109/375 | 103 / 115 |

원인은 `data/normalizer.py` 모듈 docstring에 **이미 "알려진 한계"로 적혀 있었다** — WS
프레임의 데이터건수가 1보다 크면 첫 레코드만 파싱하고 나머지를 조용히 버렸다(마흐디 원본
이식분). 문서화만 돼 있었지 **영향 규모가 측정된 적이 없었고**, 측정해 보니 거래량 계열
피처 전부와 종가 기반 레이블의 1/4이 틀린 값 위에 서 있었다.

> 교훈: "알려진 한계"로 적어둔 것은 관리되고 있다는 뜻이 아니다. 크기를 재지 않은 한계는
> 그냥 모르는 버그다. 이번엔 **외부 기준(거래소 공식값)이 생기고 나서야** 잴 수 있었다.

### 한 일

- [x] **P0 — WS 다중 레코드 파싱**. `_split_ws_records()`가 헤더의 데이터건수만큼 본문을
      균등 분할한다. 레코드 폭은 TR별로 하드코딩하지 않고 `len(fields) // count`로 유도 —
      선물은 50필드 실측이지만 옵션(H0IOCNT0)은 자체 캡처가 없어 상수로 박으면 그 자체가
      미검증 가정이 된다. 안 나눠떨어지면 종전 동작(첫 레코드)으로 폴백하되
      `TickFrameSplitFallback`을 남긴다. `parse_futures_tick`(단수)은 **삭제**했다 —
      조용히 버리는 계약을 남겨두면 회귀를 부른다.
- [x] **P1 — 백필**. `data/backfill.py` 신설(월물 단축코드 규칙·만기 산출·근월 구간 분할·
      페이징·후방조정), `rest_client`에 분봉/일봉 2종, `ParquetArchiver.write_day()`(하루
      단위 **덮어쓰기** — 조각도 함께 지운다, 안 지우면 `read_day()`가 조각을 이긴 것으로
      취급해 옛 오염값이 되살아난다), `scripts/run_backfill.py`.
- [x] **롤오버 후방조정**. 이어붙이기만 하면 롤 경계마다 basis가 하루짜리 가짜 급등으로
      나타난다. 들어오는 월물은 근월이 되기 전에도 거래되므로(A05608은 2026-02-13부터
      데이터 있음) **나가는 월물의 마지막 날 하루만 더 받으면 진짜 겹침**이 생긴다 —
      그 날 같은 분의 두 계약 종가 차이가 basis다. 롤 1회당 1일 추가.
- [x] **만기 규칙을 조용히 안 믿는다**. "둘째 주 목요일"은 KRX 관례에서 온 가정이라
      `verify_expiry_against_chart()`로 거래소 일봉과 대조한다. A05601~A05607 7개 전부 일치.
- [x] **휴장일 달력을 안 믿는다**. `configs/krx_holidays.yaml`은 2026년만 있고 2025년은
      데이터 자체가 없으며 파일 스스로 "작성됨≠KRX 공식 확인됨"이라고 밝힌다. 그래서
      백필의 날짜 열거는 **평일 전부**를 담고 휴장은 빈 응답으로 드러나게 했다 — 틀린
      목록으로 날짜를 빼면 그 거래일이 조용히 영영 안 채워진다.
- [x] **상위 Horizon 재합성**(`scripts/run_recompose.py`). 합성 규칙을
      `bar_composer.compose_composite_bar()` 한 곳으로 모아 실시간 경로와 공유 — 복사하면
      아카이브 안에서 같은 Horizon이 두 규칙으로 만들어진다.
- [x] **G1 심사 스크립트**(`scripts/run_g1_walk_forward.py`). 기존 하니스와 달리 합성
      데이터로 도망치지 않는다 — 데이터가 모자라면 그대로 실패한다.

### 확정된 결정

- **08:45~09:00 프리마켓 15분은 정상 거래 봉**이다(거래소 공식 분봉도 거래량과 함께 준다).
  MESSIAH는 이 15분을 정규장 개시 전 지표 스케일링·웜업에 쓴다. `session=PRE_OPEN`으로
  **표시만** 하고 버리지 않는다 — 미해결 ①은 이것으로 닫힌다.
- 백필 봉의 `quality_ok`는 **True 고정**. 이 플래그의 뜻은 "우리가 믿을 만하게 관측했는가"
  이고 거래소 공식 집계에는 그 판정 근거(틱 수)가 없다. 거래량으로 흉내내면 원본과 다른
  의미의 같은 컬럼이 생긴다.

### 남은 갭

- **연속물 심볼 `K200MFC`는 거래 가능한 종목코드가 아니다** — 후방조정된 합성 시계열의
  이름일 뿐. 학습 경로가 단일 심볼을 요구해서 붙인 것이고, 주문 경로에 절대 들어가면 안 된다.
- 후방조정은 최근 월물을 기준으로 과거를 이동시킨다 — **차분은 보존되지만 과거 구간의
  절대가는 그날 실제 호가와 다르다**. 절대가에 의존하는 지표가 생기면 재검토 대상.
- 라이브 수집은 이 수정 **이후 재기동분부터** 공식값과 맞는다. 오늘(08-04) 장중 수집분은
  기동 시각(08:35)이 수정 전이라 여전히 절반이다 — 내일 백필로 덮을 것.
- 30초봉도 받을 수 있음을 확인했지만 쓰지 않았다(`FID_HOUR_CLS_CODE="30"`).

### 정정 — 2026-07-28·29의 "조용한 스톨 30분"은 스톨이 아니었다 ([MW0601], 2026-08-04)

F3(`_StallWatchdog`)은 2026-07-28 10:13~10:43, 07-29 12:32~13:02의 **각 29분 공백**을
"소켓은 살아있는데 틱만 안 들어온 조용한 스톨"로 진단하고 만든 것이다. 백필로 받은 **거래소
공식 분봉에도 정확히 같은 구간이 비어 있다**:

    2026-07-28  381봉  결손 10:13→10:43 (29분)
    2026-07-29  381봉  결손 12:32→13:02 (29분)

즉 그 29분은 **실제로 체결이 0건이었다**(또는 거래정지). 수집기는 고장 나지 않았다 —
틱이 없으니 봉이 없고, 봉이 없으니 `FeaturePublish`도 없고, 그래서 "30분간 로그 한 줄
없음"이 관측된 것이다. 당시 근거로 삼았던 "프로세스가 로그를 한 줄도 안 남겼다"는 스톨의
증거가 아니라 **무거래의 정상 결과**였다.

이 오진의 대가는 07-31에 실제로 치렀다: 고정 120초 임계가 상한가 고착 구간을 장애로 오판해
6회 강제 재연결을 걸었고, 재연결 후 첫 틱까지 2분 40초씩 걸려 **워치독이 결손을 오히려
키웠다**. 그 대응으로 적응 임계를 넣었지만, 근본적으로는 없는 고장을 고치려던 것이었다.

**남긴다, 지우지 않는다**: `_StallWatchdog`은 유지한다. 진짜 소켓 스톨이 없다는 증거는
없고(관측된 적이 없을 뿐), 적응 임계가 붙은 지금은 무거래 구간을 장애로 오판하지 않는다.
다만 **판정 근거를 바꿔야 한다** — "우리 쪽에 틱이 없다"만으로는 고장을 못 가른다.
거래소 분봉과 대조하면 갈린다(무거래면 거래소에도 봉이 없다). 일일 무결성 리포트가
결손 구간을 KIS 분봉과 자동 대조하도록 하는 것을 다음 과제로 올린다.

> 교훈: 우리 데이터만 보면 "수집 실패"와 "시장 무거래"가 **똑같이 생겼다**. 외부 기준이
> 없으면 둘을 못 가르고, 못 가른 채로 고치면 없는 병을 치료하다 진짜 부작용을 만든다.
> [[measure-known-limitations]]와 같은 형태의 실패다.

### G1 실행 결과 — 관문은 돌았지만 **거래 0건**이라 값이 성적이 아니다 ([MW0601], 2026-08-04)

백필 후 `scripts/run_g1_walk_forward.py`를 실제 데이터로 돌렸다. 여기까지는 성공:

- 연속 시계열 **63,098봉 / 2025-12-12~2026-08-03 / 234 캘린더일** (요건 211일 충족)
- 롤 7회 전부 실제 겹침(각 만기일 15:19 같은 분)으로 basis 측정: +49/+36/-50/+116/+161/+240/+202틱
- walk-forward **창 2개** 생성 — 실제 데이터로 창이 만들어진 것은 이번이 처음

그런데 두 창의 수익률이 **정확히 +0.0000%**였다. 즉 거래가 한 건도 안 났고, 관문 값
(sharpe 0.0 / MDD 0.0 / neg_window 0.0)은 **성적이 아니라 미측정**이다 —
`models/wiring_completeness.py`가 잡으려던 것과 정확히 같은 형태.

**원인 실측(추정 아님)**: 재생되는 봉은 과거인데 `BusMessage.ts_utc`의 기본값이
`now_utc()`라 재생 메시지가 **지금 시각으로 스탬프**된다. 그래서
`data_age_seconds = 지금 − 과거봉확정시각`이 된다:

    마지막 봉 확정시각 = 2026-07-27 15:35 KST
    BusMessage.ts_utc  = 2026-08-04 01:15 UTC (생성 시각)
    → data_age = 672,056초 = 7.8일
    KillSwitch R11 임계 = 30초  →  매 판단마다 발동 → 게이트웨이 전면정지 → 신규진입 0건

**이건 이번에 생긴 게 아니다.** `backtest/harness.py`가 만들어진 2026-07-27부터 있었고,
`docs/capability_matrix.md`에 "둘 다 무거래로 수익률 0%, FAIL/PASS 혼재 — 성능 주장 아님,
'관문이 실행된다'만 확인"으로 **정상인 것처럼 기록돼 있었다**. 합성 데이터로 돌렸을 때도
같은 이유로 0건이었는데, 데이터가 짧아서 그런 줄 알았던 것이다.

- [ ] **G1 하니스 시계 정합 — 리스크 경로 변경이라 합의 필요.** 후보:
      (a) `TradingPipeline.handle_futures_view()`가 `view.ts_utc` 대신 주입된
          `self._now()`를 쓰게 하고, 하니스가 **재생 시각을 돌려주는 시계**를 주입한다.
          라이브에서는 `self._now() ≈ view.ts_utc`(방금 발행된 메시지)라 동작 동일.
          미해결 ②를 `TradingPipeline`에 시계 주입으로 푼 것과 같은 방향의 연장.
      (b) 재생 경로가 메시지 `ts_utc`를 과거 시각으로 스탬프한다 — `FuturesAIService`가
          만드는 `FuturesView`까지 닿아야 해서 손이 더 많이 간다.
      (c) 백테스트에서만 R11을 끈다 — **반대**. 성과를 재는 하니스에서 리스크 규칙을
          조용히 끄면 그 하니스의 결과를 믿을 수 없게 된다.
      (a)를 권장. 이 수정 전까지 `run_g1_walk_forward.py`의 관문 값은 **전부 미측정**이며
      G1 PASS/FAIL을 논할 수 없다.

### 하니스 시계 정합 수정 — 안 (a) 채택·구현 ([MW0601], 2026-08-04, 사용자 승인)

**`TradingPipeline.handle_futures_view()`의 "지금"을 주입된 시계 하나로 통일했다.** 그
전에는 신선도·CB·세션·R4/R6 판정이 전부 `view.ts_utc`(메시지 스탬프)에서 왔고,
`watch_circuit_breaker_forever()`만 주입 시계를 썼다 — **한 메서드 안에서 시간 출처가 둘**
이었고 재생 경로에서 그 둘이 갈라졌다.

바뀐 곳(전부 `as_of = self._now()` 하나로):
`_data_age_seconds` · `CircuitBreakerMonitor.observe/blocks_entry` · `_collector_healthy` ·
`_in_regular_session` · `EventCalendar.minutes_to_close` · `RiskEngine.evaluate(as_of=)` ·
`record_order_error`.

**라이브 동작은 안 바뀐다** — 방금 발행된 메시지의 스탬프와 지금 시각의 차이는 밀리초다.

`backtest/harness.py`에 `ReplayClock` 신설: 봉을 투입하기 **직전에** 그 봉의
`bar_confirm_time()`으로 시계를 옮기고(`bar_open_kst`가 아니다 — 완성봉은 확정시각부터
소비 가능, `_last_bar_confirm_at`과 같은 기준) `TradingPipeline(..., now=clock)`으로 주입.

**테스트가 같은 가정 위에 있었다**: `tests/strategy/test_pipeline.py` 10건이 이 변경으로
깨졌는데, 원인은 그 테스트들이 "과거 시각 뷰 + 벽시계 파이프라인"이라는 **재생 경로와 똑같은
불일치**를 갖고 있었기 때문이다. `_view()`가 파이프라인 시계를 그 뷰의 시각으로 맞추게 해
해결했다(명시적으로 `now=`를 넘기는 CB 테스트는 그대로 자기 시계 사용).
`test_default_clock_is_the_real_wall_clock`은 테스트 헬퍼의 기본 주입을 우회하도록 생성자를
직접 부르게 바꿨다 — 프로덕션 기본값이 여전히 벽시계임을 지키는 회귀 테스트다.

회귀 테스트 3건 신규(`tests/backtest/test_harness.py`): 첫 봉 전 벽시계 폴백 · 확정시각 기준
전진 · **재생 봉의 data_age가 R11 임계 아래**(이 버그의 직접 회귀).

검증: 테스트 1093 → **1096건** 전부 통과, ruff 0건.

### 시계 수정 후 G1 재실행 — 배관은 뚫렸고, 막는 건 이제 **모델**이다 ([MW0601], 2026-08-04)

시계 정합 수정 후 재실행했더니 창 2개의 수익률이 **여전히 정확히 0.0000%**였다. 다만
막히는 지점이 완전히 달라졌다 — 단계별로 계측한 결과(`wiring_completeness` 단계 개념):

    intel.futures 발행   : 1366     ← 전략 계층이 실제 데이터로 처음 돌았다
    decision.intent      : 1366     ← 전부 NO_TRADE
    NO_TRADE 아닌 판단   : 0
    게이트웨이 수락 주문 : 0
    게이트웨이 halted    : False    ← R11은 더 이상 발동하지 않는다(시계 수정이 먹혔다)

즉 **2026-08-03까지의 "번들 0개·판단 0건"과는 다른 상태**다. 배관은 끝까지 뚫렸고 판단도
매번 나온다. 다만 그 판단이 전부 "거래 안 함"이다.

**원인 실측** — 집계 가중치가 0이 되는 지점을 특정했다
(`weight = 가중치표 × meta_h × (1−u_h) × f_h`):

    Meta-Labeler 통과   : 0건 / 1006건 (0.0%)
    통과확률 분포        : 중앙값 0.3151  최소 0.1753  **최대 0.5422**
    Meta-Labeler 임계값 : 0.6000
    ens_std 분포        : 중앙값 0.0013  최대 0.0043  → u_h ≈ 0 (불확실성은 원인 아님)
    f_h                 : 봉 도메인 시각 기준이라 정상 (원인 아님)

`meta_h = 0`이 유일한 원인이다. **검증 구간에서 임계 0.60에 닿는 표본이 단 하나도 없다.**

`select_threshold()`는 정상 동작한다 — "어떤 신호도 안 남는 임계값은 후보에서 제외"하므로
0.60은 **학습 out-of-fold에서는 도달 가능했던** 값이다. 즉 학습에서 "아주 확신할 때만
거래하라"를 배웠는데 검증 구간에선 그만큼 확신하는 순간이 한 번도 안 온 것 —
임계값이 학습 폴드에 과적합됐거나, 이 설정의 모델이 애초에 변별력이 없거나.

`ens_std`가 0.001 수준(앙상블 3멤버가 거의 동일한 출력)인 것이 후자를 시사한다.

**이건 배관 버그가 아니라 모델 결과다.** Meta-Labeler가 제 역할을 한 것 — 우위가 없으니
거래하지 않는다. **임계값을 낮춰 거래를 만들어내지 않았다.** 그건 관문을 통과시키려고
관문을 옮기는 짓이고, 이 프로젝트가 `run_*_smoke.py` 전체에서 지켜온 원칙과 정면으로
어긋난다.

- [ ] **모델 쪽 후속 (미착수)**. 이번 실행은 런타임을 줄이려고 탐색을 의도적으로 얕게 줬다
      (`n_search_trials=3~5`, `num_boost_round=15~30`, `n_members=3`) — "우위 없음"은 **이
      설정에 대한 판정**이지 전략에 대한 최종 판정이 아니다. 다음에 시도할 것:
      ① 탐색 예산을 정상 수준으로 올려 재학습, ② 5m 외 Horizon(15m/30m — 이제 데이터가
      충분하다), ③ 임계값 과적합 여부를 확인(학습 oof 통과확률 분포 vs 검증 분포를 나란히),
      ④ Regime AI 미결선 영향(지금은 항상 `Regime.UNKNOWN` 가중치 0.5로 집계).
- [ ] G1 관문 값은 **여전히 미측정**이다. 거래가 나기 전까지 sharpe/MDD를 성적으로 읽으면
      안 된다 — 다만 그 이유가 이제 "인프라가 막았다"가 아니라 "모델이 안 하겠다고 했다"로
      바뀌었고, 그 둘은 완전히 다른 상태다.

## 후속 4종 — 무거래의 진짜 원인은 임계값 선택이었다 ([MW0601], 2026-08-04, 사용자 승인)

전날 결론("모델에 우위가 없다")을 검증하려고 ①탐색예산 ②Horizon ③임계값 과적합
④Regime 결선 넷을 구현했는데, ③에서 **버그 두 개**가 나왔고 그걸 고치자 거래가 나기
시작했다. 즉 "우위 없음"은 판정이 아니라 **증상**이었다.

### 버그 1 — 임계값을 in-sample 확률로 골랐다

`trainer.py`는 Meta-Labeler를 `meta_x`로 학습한 **직후 같은 `meta_x`로 예측**해 그 확률로
임계값을 골랐다. Expert 쪽은 `PurgedKFold`로 look-ahead를 막았지만 **메타 모델 자신의
예측은 자기 학습 행에 대한 것**이었다. LightGBM이 학습 행을 잘 맞히니 확률이 0/1 쪽으로
밀리고, 그 위에서 고른 임계값은 새 데이터에서 아무도 못 넘는 높이가 된다.

→ `_meta_selection_probabilities()`로 out-of-fold 확률을 만들어 그걸로 고르게 함
   (`meta_threshold_splits`, 기본 5. 1이면 종전 동작 — 비교용으로만).

### 버그 2 (더 컸다) — 임계값 후보에 **지지도 하한이 없었다**

`select_threshold()`는 "어떤 신호도 안 남는 임계값은 제외"만 했다. 즉 **신호 1건만 남는
임계값도 후보**였고, 그 1건이 우연히 수익이면 평균 net_return이 최대가 되어 그 극단값이
선택된다 — 근거가 표본 1개인 임계값이다.

실측: 버그 1을 고친 뒤에도 선택된 임계값의 학습 지지도가 **0.5%**(약 3건)였고 검증 도달은
여전히 0%였다.

→ `DEFAULT_MIN_SUPPORT_FRACTION = 0.05` 신설. 하한을 못 채우는 후보는 제외하고, 하나도
   못 채우면 가장 많이 남기는 후보로 폴백한다(선택 불가로 죽지 않게).

### 효과 (30m/shallow/oof/regime=on, 동일 데이터)

| | 하한 없음 | 하한 5% |
|---|---|---|
| 선택 임계값 | 0.550 | **0.200** |
| 선택 지지도 | 0.5% | 13.6% |
| **추론 도달률** | **0.0%** | **8.9%** |
| 헤드룸 | −0.2252 | **+0.1248** |
| **거래 신호** | **0건** | **15건** |

5m/shallow/oof/regime=off에서는 **122건**. 즉 배관·시계·데이터를 다 고친 뒤 마지막까지
남아 있던 무거래의 원인은 모델의 무능이 아니라 **임계값 선택 로직 두 곳의 결함**이었다.

> 교훈: "모델에 우위가 없다"는 결론은 그 앞의 모든 단계가 옳다는 전제 위에서만 성립한다.
> 무거래는 어느 단계에서든 똑같이 생기므로, 단계별로 값을 재기 전엔 어떤 결론도 내면 안 된다.
> [[measure-known-limitations]]와 같은 형태 — 증상이 같으면 원인을 못 가른다.

### 함께 구현한 것

- **`models/threshold_report.py`** — 선택 시 확률 분포와 추론 시 확률 분포를 나란히 놓고
  "임계값 과적합"과 "우위 없음"을 구분하는 진단. `ThresholdReport.verdict`가 스스로 판정을
  말한다. 두 증상이 똑같이 무거래라 이게 없으면 매번 처음부터 조사하게 된다.
- **`scripts/run_model_sweep.py`** — Horizon × 탐색예산 × 임계값선택 × Regime을 단일
  train/test 분할로 훑는다. **G1(walk-forward)이 아닌 이유**: 알아야 할 것이 창별 성과가
  아니라 "어떤 설정에서 거래가 나기는 하는가"라서. 성과 판정은 G1의 몫으로 남긴다.
- **Regime 결선** — `run_walk_forward_backtest(regime_ai=...)`로 `RegimeRuntime`을 배선.
  **미주입은 중립이 아니다** — 항상 `UNKNOWN`이면 가중치표가 전 Horizon 0.5 고정이라
  국면별 가중(TREND 최대 1.5)이 통째로 죽는다. 성과 비교 시 이 축을 고정했는지 확인할 것.
  알려진 한계: G1 경로의 RegimeAI는 전 구간으로 한 번 학습한다(창마다 재학습하면 런타임이
  배로 든다) — 국면 판정에 검증 구간 정보가 새는 약한 look-ahead다.

### 최종 원인 — 탐색 공간이 데이터 규모와 안 맞아 **모델이 학습 자체를 못 했다** ([MW0601], 2026-08-04)

임계값 결함 두 개를 고친 뒤에도 G1은 무거래였다. 스윕은 "신호 87건"이라 했는데 G1은 0건 —
**두 측정이 서로 다른 것을 재고 있었다.** 스윕이 `abs(score) > 0`이라는 손수 만든 근사를
썼고, 실제 파이프라인은 `MetaDecisionEngine`을 탄다. 엔진을 그대로 태우자 진짜 그림이 나왔다:

    15m/regime=off  차단={'②': 841}        ← 전 표본이 게이트 ②
    15m/regime=on   차단={'②': 1, '④': 839}

- **게이트 ②**: `_EVENT_LIKE_REGIMES = {EVENT, UNKNOWN}` — regime 미결선이면 `UNKNOWN`이라
  **즉시 NO_TRADE**. 앞서 "단일 Horizon에선 국면 가중치가 상쇄되니 이 축은 측정 불가"라고
  적었던 건 **집계 계층만 본 오판**이다. regime은 상쇄되는 게 아니라 거래를 구조적으로
  막는다. 근사로 재면 이렇게 틀린다.
- **게이트 ④**: `|S| < SCORE_THRESHOLD(0.20)`. 실측 `|S| p90 = 0.006`. 요구치의 1/30.

`|S| = p_up − p_down`의 가중평균이라 Expert 출력을 직접 봤더니 검증 842건이 **전부 동일**했다:

    p_up 0.1861~0.1861 · p_flat 0.6537~0.6537 · p_down 0.1601~0.1601   (min == max)

상수 출력이다. 부스터를 열어보니 원인이 나왔다 — **트리 75개가 전부 잎 2개짜리 그루터기**.
탐색이 고른 `min_data_in_leaf=1285`인데 폴드 안 학습 표본이 약 2,200행이라 분기가 한 번밖에
안 된다. `PRODUCTION_SEARCH_SPACE`의 (200, 2000)은 Ver 1.6 §2.2 원문이고 **다년치 데이터를
전제한 값**인데, 현재 규모에 그대로 적용돼 있었다.

**즉 "모델에 우위가 없다"가 아니라 "모델이 학습을 못 하고 있었다".** 탐색 예산을 늘리면
오히려 나빠졌던 것(|S| p90 0.006 → 0.000)도 같은 이유다 — 시도가 많아질수록 더 큰
`min_data_in_leaf`를 우연히 고른다.

- [x] `scale_space_to_samples()` 신설 — `min_data_in_leaf` 상한을 `표본수//50`으로 좁힌다
      (최소 50개 잎을 만들 여지는 항상 남긴다). **좁히기만 하고 넓히지 않는다** — 호출자가
      준 작은 공간을 이 함수가 키우면 테스트용 전용 공간까지 망가진다. 표본이 10만 행을
      넘으면 원문 (200, 2000)이 그대로 복원된다.

### 수정 후 (15m, 동일 데이터)

| | 수정 전 | 수정 후 |
|---|---|---|
| 트리 잎 수 | 2 (그루터기) | **15** |
| 분기에 쓰인 피처 | 10개 | **103개** |
| Expert 출력 | 842건 전부 동일 | 변화함 |
| `\|S\|` p90 (5m) | 0.006 | **0.042** (7배) |
| 임계값 이전(선택→추론) | — | 15.1% → **14.9%** (거의 손실 없음) |

**여전히 `|S| p90 = 0.042` < 임계 0.20이다.** 이제야 이 격차가 진짜 모델 성능 문제다 —
버그가 아니라. 5m 26신호 / 15m 5신호 수준.

### 함께 고친 것

- 스윕이 `MetaDecisionEngine`을 **그대로** 쓰도록 변경 + 게이트별 차단 사유 집계.
  손으로 만든 근사가 파이프라인과 갈라지면 스윕 결과가 거짓말을 한다.
- `ThresholdReport.is_degenerate` 판정을 **통과율 하나로만** 하도록 수정. 처음엔 "임계값이
  0에 가까울 때만 퇴화"로 좁게 잡았는데, 15m에서 임계 0.100인데 도달률 100%인 경우를
  놓쳤다 — 임계값의 명목 크기와 무관하게 전부 통과하면 그 게이트는 꺼진 것이다.

### 남은 것 (미착수)

- [ ] `|S|`를 0.042 → 0.20으로 올리는 것은 **피처/레이블 재설계** 영역이다. 후보:
      ① `SCORE_THRESHOLD=0.20`이 이 상품에 맞는 값인지 재검토(Ver 2.0 §3.1 원문값, 미검증)
      ② 레이블 균형 — 15m는 flat 64.3%, 30m는 76.3%로 치우쳐 모델이 flat로 수렴하기 쉽다
      ③ 검증 구간에서 **항상 NaN인 피처 5개**(`px_gap_open`·`px_open_ret`·`px_range_pos_d`·
        `px_ema_cross_60`·`px_macd_h_60`) — 연속 합성물에 전일종가/세션시가 개념이 없어서일
        가능성. 121개 중 5개지만 전부 일중 위치 계열이라 정보 손실이 방향성에 직결될 수 있다
- [ ] `normal` 예산 재측정 — 탐색 공간 수정 **전** 값(|S| p90 0.000)이라 무효다

### NaN 피처 5개 조사 — 결함 2개, 그중 하나는 **프로덕션에서도 계속 죽어 있었다** ([MW0601], 2026-08-04)

학습 검증 구간에서 항상 NaN이던 5개(`px_ema_cross_60`·`px_macd_h_60`·`px_gap_open`·
`px_open_ret`·`px_range_pos_d`)를 조사했더니 **서로 다른 원인 두 개**였다.

#### 결함 1 — 히스토리 용량이 두 피처의 요구량보다 작다 (프로덕션 포함)

각 피처가 값을 내는 최소 봉 수를 실제로 계산해봤다:

    px_ema_cross_60 : slow EMA가 3*W = **180봉** 필요
    px_macd_h_60    : 2*W=120 + 시그널 EMA(W//3=20) → **139봉** 필요
    _MAX_HISTORY    : **130**

즉 이 둘은 **어떤 상황에서도 계산될 수 없었다**. 용량 주석은 "px_hurst(120)·px_accel(121)을
전부 커버한다"고 적혀 있었는데 그 계산에서 이 둘이 빠져 있었다.

**증거는 매일 눈앞에 있었다**: 무결성 리포트의 `nan_ratio` 중앙값이 1m/3m/5m/10m/15m 전부
**0.0165**로 매일 똑같이 찍혔다. 121개 중 2개 = 2/121 = **0.01653**. 정확히 일치한다.
값이 매일 같으니 "정상 수준"으로 읽혔고, 아무도 "그 2개가 뭔가"를 묻지 않았다.

- [x] `_MAX_HISTORY` 130 → **200**(최대 요구 180 + 여유)
- [x] **재발 방지 테스트** — `test_every_registered_feature_is_computable_within_history_capacity`
      가 등록된 전 피처 × 전 윈도우를 용량만큼의 봉으로 실제 계산해 None이 나오면 실패한다.
      상수만 고치면 다음에 윈도우 큰 피처가 추가될 때 같은 사고가 반복된다.

#### 결함 2 — 학습 경로에서 `SessionState`가 영영 비어 있었다 (train/serve 불일치)

`FeatureEngine`은 `SessionState`를 **M1 봉으로만** 갱신했다. 라이브는 M1을 구독하니 정상이지만,
학습 경로(`models/trainer.build_feature_vectors()`)는 **학습 Horizon 하나짜리 엔진**을 만들고
그 Horizon 봉만 흘린다 — M1이 한 번도 안 들어와 세션 시가/고저가 영영 None이었고,
`px_gap_open`/`px_open_ret`/`px_range_pos_d` 3개가 **학습에서만** 항상 NaN이었다.

추론에서는 값이 나오므로 **train/serve skew**이기도 하다: 모델은 그 3개를 안 쓰도록 배우고,
실전에서는 값이 들어온다.

- [x] `_session_horizon` 도입 — M1을 구독하면 M1, 아니면 구독 중 가장 촘촘한 Horizon으로
      갱신한다. **굵은 봉으로 갱신해도 이 3개는 정확하다**: 세션 시가는 그날 첫 봉의 시가이고,
      세션 고/저는 구성 분봉의 max/min이라 어느 Horizon으로 집계해도 같은 값이다.
      라이브 동작은 불변(M1 우선).

#### 효과 (5m, 동일 데이터)

| | NaN 수정 전 | 수정 후 |
|---|---|---|
| 항상 NaN인 피처 | 5개 | **0개** |
| 분기에 쓰인 피처 | 103개 | **109개** |
| 추론 도달률 | 14.9% | **74.9%** |
| 거래 신호 | 26건 | **50건** |

> 교훈: 매일 같은 값으로 찍히는 지표는 "안정적"이 아니라 **아무도 안 보는 것**일 수 있다.
> `nan_ratio=0.0165`는 8거래일 내내 동일했고, 그게 곧 "고정된 2개가 죽어 있다"는 신호였다.
> [[measure-known-limitations]]와 같은 형태 — 숫자는 있었는데 아무도 그 크기를 해석 안 했다.

### SCORE_THRESHOLD=0.20 재검토 — **질문 자체가 틀렸다** ([MW0601], 2026-08-04)

0.20이 이 상품에 맞는 값인지 보려고 |S| 분포를 쟀는데, 먼저 확인해야 할 게 있었다:
**|S|가 클수록 실제로 잘 맞는가.** 재보니 아니었다.

    방향 적중률 — 상위 20% |S|: 50.6%   하위 20% |S|: 51.1%   전체: 52.4%   (5m 단독)
                              52.9%              49.3%          52.2%   (5m+15m)
                              52.8%              49.9%          52.6%   (5m+15m+30m)

확신이 큰 구간이 작은 구간보다 **나을 게 없다**(둘 다 ~50%). 전체 52%대의 미약한 우위도
|S|와 무관하게 흩어져 있다. 이러면 임계값을 어디에 두든 동전던지기를 고르는 것이고,
**0.20을 낮추면 비용만 더 내고 더 많이 진다.**

- [x] **결론: SCORE_THRESHOLD는 건드리지 않는다.** 임계값은 병목이 아니었다.

#### 함께 확인된 사실 — S는 평균이 아니라 **합**이다

`aggregator.compute()`의 S는 Horizon 가중 **합**이다(Ver 1.2 §7.2 원문:
`S = Σ_h [w × (P_h(+1) − P_h(−1)) × meta_h × (1−u_h) × f_h]`). `agg_p_up`/`uncertainty`는
`total_weight`로 나누는데 S만 안 나눈다. 국면별 가중치 합이 2.6~6.2이므로, 임계 0.20은
**여러 Horizon이 함께 기여하는 상태**를 전제한 값이다.

그래서 "1개만 결선해서 못 넘는 것"이라는 가설을 세웠는데 **실측은 아니었다**:

    Horizon 1개: |S| >= 0.20이 2.6%   2개: 3.0%   3개: 3.0%

3개로 늘려도 거의 안 늘었다. Horizon 수가 원인이 아니라는 뜻이고, 위의 "적중률이 |S|와
무관하다"와 같은 이야기다. (가설을 세우고 재서 기각한 것 — 안 쟀으면 "Horizon을 늘리면
된다"는 틀린 처방으로 갔을 것이다.)

- [x] **`models/score_calibration.py` 신설** — |S| 구간별 방향 적중률을 재고
      `is_informative`/`verdict`로 스스로 판정한다. 구간은 등폭이 아니라 **동일 개수**
      분위다(|S|가 0 근처에 몰려 있어 등폭이면 상위 구간 표본이 한 자릿수가 된다).
      **임계값을 바꾸기 전에 반드시 이걸 먼저 돌릴 것** — `is_informative=False`인데 임계값을
      조정하는 건 동전던지기의 개수를 조절하는 것이다.

#### 남은 것

- [ ] 이제 진짜 질문은 **"어떻게 방향 예측력을 만드나"**다. 임계값·하이퍼파라미터·Horizon
      수 어느 것도 아니다. 이번 세션에서 결함 5개(WS 유실·시계·임계값 2종·탐색공간·NaN
      피처 2종)를 걷어낸 뒤 남은 순수 모델 성능이 **적중률 ~52%, |S|와 무관**이라는 것이
      측정된 사실이다. 다음 후보는 피처 재설계(현 121개는 전부 가격/거래량 파생 —
      MS/FL/OP/RG 카테고리가 미구현), 레이블 재설계(15m flat 64%·30m flat 76%),
      또는 예측 대상 자체의 재정의.

### FL(수급) 피처 착수 — 데이터·피처 결선 완료, **효과는 잡음 범위** ([MW0601], 2026-08-04)

#### 필드 매핑 실측 완료 (2026-07-27부터 미해결이던 항목)

장중 엔드포인트 원시 응답을 캡처했다. 72개 필드가 자기설명적이고, 의미를 **산술로 검증**했다:

    ntby = shnu − seln    (외국인 85674 − 85838 = −164 ✓, 전 투자자 카테고리 일치)
    orgn = 하위 8종 합    (319+658+0+81+23−26−158+0 = 897 ✓)

#### 그런데 파생 수급은 백필이 불가능하다

- 장중 엔드포인트는 **당일 누적만** 준다(과거 조회 없음)
- `InvestorFlowPoller`는 **어떤 스크립트에도 결선 안 됨**, `raw.investor_flow.*` 구독자도 없음
  → 이 프로젝트는 파생 수급을 **한 건도 수집한 적이 없다**
- 일별 엔드포인트(`inquire-investor-daily-by-market`)는 페이징으로 다년 소급되지만
  **현물 전용**이다 — F001/OC01을 넣으면 rt_cd=0에 전 항목 0(미지원인데 "수급 0"처럼 보임)

→ FL은 **KOSPI 현물 일별 수급**에서 나온다. 일 단위라 하루 안에서는 값이 고정된다
  (5분봉 78개가 같은 값) — 일중 타이밍은 못 주고 그날의 방향성 기울기만 준다.

- [x] `data/investor_flow_history.py` — 페이징·정규화·저장. `FlowHistory.flow_as_of()`가
      **요청일보다 엄격히 이전**만 준다(그날 순매수는 장 마감 후 확정 — 당일 값을 쓰면
      미래 참조이고 백테스트만 좋아진다). `looks_unsupported()`로 전량 0 응답을 걸러 저장 거부.
- [x] `scripts/run_flow_backfill.py` — 184거래일(2025-11-03~2026-08-03) 확보
- [x] `features/fl_core.py` — z-score·누적·연속·동조 9개. 전부 20일 표준화(절대 계약 수를
      그대로 쓰면 모델이 "최근인가"를 학습한다).
- [x] `FeatureEngine(flow_history=...)` — **주입됐을 때만** 벡터에 넣는다. 주입 안 됐는데
      NaN으로 자리만 채우면 `px_ema_cross_60`이 그랬듯 죽은 채로 학습되는 피처가 또 생긴다.
      대신 `feature_set`을 함께 바꿔야 하고 어긋나면 `FeatureSetMismatch`(ERROR)가 잡는다.

#### A/B 결과 — 효과 없음 (그리고 **내 진단 도구가 오탐을 냈다**)

15m, 동일 데이터:

| | FL 없음(121피처) | FL 있음(130피처) |
|---|---|---|
| \|p_up−p_down\| 중앙값 | 0.0595 | 0.0935 |
| 전체 적중률 | 46.5% | 49.5% |
| 상위 구간 / 하위 구간 | 43.6% / 47.3% | 49.7% / 45.5% |

`ScoreCalibration`이 FL 있음을 "**|S|가 방향을 가른다**"로 판정했는데 **틀렸다**:

- 격차 +4.2%p, 구간당 표본 165건 → 격차의 표준오차 5.5%p → **0.8σ, 순수 잡음**
- 상위 구간 적중률이 **49.7%로 50% 미만** — 격차가 있어도 상위가 동전던지기보다 나쁘면
  거래할 우위가 아니다("하위가 더 나쁠 뿐")

- [x] **도구 수정** — `is_informative`가 세 조건을 **다** 요구하도록: 크기(`MIN_EDGE_GAP`)
      · 유의성(`MIN_EDGE_SIGMA=2.0`, 이항비율 표준오차 기준) · 유용성(`MIN_TOP_HIT_RATE=0.50`).
      수정 후 두 케이스 다 올바르게 "우위 없음"으로 판정한다.

> 교훈: **자기가 만든 진단 도구도 검산 대상이다.** 이 도구는 바로 앞 커밋에서 "임계값은
> 문제가 아니다"를 옳게 판정했기에 신뢰가 생겼는데, 표본이 작아지자 바로 오탐을 냈다.
> 판정을 그대로 믿고 "FL이 효과 있다"고 보고했으면 없는 성과를 주장하는 것이었다.

#### 남은 것

- [ ] FL은 **일 단위**라 5분봉 모델의 일중 예측을 개선할 구조가 아니다. 쓰려면 일봉/일간
      모델이나 "그날의 방향 편향" 같은 별도 층이 맞다.
- [ ] 파생 장중 수급을 쓰려면 `InvestorFlowPoller`를 `run_l1_daily.py`에 결선하고 아카이빙
      경로를 만들어 **몇 달 모아야** 한다. 지금 시작해도 백테스트는 그 뒤의 일이다.

### 파생 장중 수급 수집 결선 ([MW0601], 2026-08-04, 사용자 지시)

`InvestorFlowPoller`는 2026-07-27에 만들어졌지만 **어떤 스크립트에도 결선돼 있지 않았고**
`raw.investor_flow.*` 구독자도 없었다 — 즉 켜져 있었어도 버스에서 증발했을 것이다.
그래서 이 프로젝트는 파생 수급을 **한 건도** 갖고 있지 않다.

봉 데이터와 성질이 결정적으로 다르다: **장중 수급은 과거 조회가 없다.** `data/backfill.py`
같은 소급이 불가능하므로 **안 받은 날은 영원히 빈다.** 그래서 아카이버를 먼저 만들고
폴러를 결선했다(순서가 반대면 첫 폴링이 사라진다).

- [x] `data/flow_archiver.py` 신설 — `raw.investor_flow.*` 구독 → `data/flow_intraday/
      {market}/{date}.parquet`. **원시 응답 74컬럼을 전부 저장한다**: 지금 쓸 건 순매수
      몇 개뿐이지만, 나중에 "그 필드도 받아둘 걸" 하는 순간이 오면 되돌릴 방법이 없다.
      숫자로 못 바꾸는 필드도 문자열로 남긴다(모르는 형식이라고 버리지 않는다).
- [x] `run_l1_daily.py` 결선 — 선물(F001)·콜(OC01)·풋(OP01) 3업종을 **60초 격자**로 폴링.
      원천이 "당일 누적"이라 더 촘촘히 받아도 정보가 안 늘고, 3업종 순차 조회라 유량
      (모의투자 1건/초)에 여유가 크다. 결선 실패는 봉 수집을 막지 않되 기동 로그에 남긴다.
- [x] 실측 확인 — 실제 KIS 호출 1회를 파일까지 흘려 3업종 × 74컬럼이 기록되는 것 확인
      (F001 외국인 −76 / OC01 +223 / OP01 +855).
- [x] `ts_kst`를 읽을 때 KST로 되돌린다 — polars가 tz-aware를 UTC로 정규화해 저장하므로
      그대로 읽으면 **이름은 `ts_kst`인데 dtype은 UTC**라 9시간 틀리게 읽힌다
      (`bar_open_kst`와 같은 규율).

**수집은 내일(2026-08-04) 08:35 기동분부터 시작된다.** 오늘은 이미 장중이라 반영 안 됨.

#### 이 데이터로 뭘 할 수 있고 언제 할 수 있나

- 지금 당장은 **아무것도 못 한다** — 백테스트할 이력이 0일이다.
- 의미 있는 학습에는 최소 수십 거래일이 필요하고, G1 walk-forward(211 캘린더일)까지는
  약 7개월이다. 봉과 달리 **이건 백필로 단축할 수 없다.**
- 그래서 이 결선의 가치는 "지금 성과를 올린다"가 아니라 **"7개월 뒤에 쓸 수 있게 오늘
  시작한다"**이다. 2026-07-27에 폴러만 만들고 결선을 안 했기 때문에 그 7개월이 이미
  한 번 날아갔다.

### flat 편중 딥다이브 — 원인은 flat 자체가 아니라 **flat과 게이트의 결합** ([MW0601], 2026-08-04)

출발 가설: "15m flat 64% / 30m flat 76%라서 모델이 flat으로 수렴한다."
**절반만 맞았다.** 재보니 인과가 한 단계 더 있었고, 그 단계를 모르면 처방이 정반대로 간다.

#### [1] Horizon 사다리가 붕괴해 있었다 (코드만 봐선 안 보임)

Ver 1.2 §3.2 표의 시간배리어(분)가 **전 Horizon에서 봉 크기의 정확히 3배**다. 봉 수로
환산하면 1m~30m 전부 **3봉**이다. 터치 확률이 대략 폭/(σ√H)의 함수인데 H가 고정이니
Horizon 축은 **배리어 폭 축 하나로 붕괴**한다 — flat 비율을 배수 하나가 단조 결정한다:

    5m(×1.0) 35.6%    15m(×1.5) 64.3%    30m(×2.0) 76.3%

"긴 Horizon일수록 flat이 많다"는 건 시장 성질이 아니라 **이 표의 모양**이었다. 원인이
`_TIME_BARRIER_MINUTES // (HORIZON_SECONDS//60)`이라는 나눗셈 한 줄에 숨어 있어서 표를
아무리 들여다봐도 안 보였다. 봉 수를 정본으로 바꿔 적었다(분 환산은 역함수로 제공).

#### [2] |S| 천장은 flat 비율이 **산술적으로** 정한다 — 이게 진짜 결함

    |S| = |p_up − p_down| <= p_up + p_down = 1 − p_flat

교정기(isotonic)가 하는 일이 평균 확률을 기저확률에 맞추는 것이므로, 교정 후 |S| 천장은
`1 − flat_share`로 내려앉는다. **모델 성능과 무관한 항등식이다.** 실측이 천장에 붙었다:

| | flat | 천장(1−flat) | 교정 후 \|S\| p99 | 게이트 통과율 (원시 → 교정) |
|---|---|---|---|---|
| 15m | 64.3% | 0.357 | 0.273 | 40.7% → **4.0%** |
| 30m | 76.3% | 0.237 | 0.246 | 33.4% → **3.6%** |

게이트는 절대상수 0.20이다. 30m은 천장 0.237이 게이트 바로 위라 **구조적으로 도달 불가**다.
`SCORE_THRESHOLD 리뷰`(e83ec6d)에서 "|S| >= 0.20이 2.6%"로 측정됐던 그 숫자의 정체가 이거다 —
임계값 문제도, 모델 우위 문제도 아니고 **레이블 flat 비율과 게이트 상수가 서로를 모른 채
정해진 결합 결함**이었다. 세 번째로 같은 증상(무거래)을 다른 원인이 만든 사례다.

#### [3] 그런데 flat을 낮추는 것만으로는 안 된다 — 재설계 A/B 결과

피처·모델·분할 전부 고정하고 **레이블 정의만** 6가지로 교체했다. 평가는 레이블에 무관한
공통 잣대 하나로 통일 — 3봉 뒤 청산 시 sign(S)·수익. **드리프트를 반드시 빼야 한다**:
이 구간(2025-12-12~2026-08-03)은 30000→49566틱(+65%) 단일 상승장이고 롤 오프셋은 754틱뿐
이라 전부 진성 추세다. 항상매수만 해도 15m +12.3틱 / 30m +27.5틱이 나온다. 첫 측정에서
드리프트를 안 뺐다가 "30m 전체 +71.6틱"이라는 **성과처럼 보이는 롱 편향**을 뽑았다.
t값도 3봉 겹침이라 유효표본 N/3으로 √3 보정했다(보정 전엔 전 변형이 유의해 보인다).

드리프트 차감·겹침 보정 후 t (교정 적용):

| 변형 | flat | 15m | 30m |
|---|---|---|---|
| **current**(현행) | 64/76% | +1.15 | **+2.38** |
| balanced3(×0.9, flat≈33%) | 34/33% | +0.67 | +1.38 |
| tight9(×1.25, 9봉) | 20/10% | +1.99 | +0.83 |
| sign3(부호만, flat≈0) | 1/0% | −0.16 | +0.64 |
| reg3(ATR정규화 수익 회귀) | — | +0.07 | +1.90 |

**flat을 33%로 되돌린 변형이 30m에서 오히려 나빠졌다**(2.38 → 1.38). 천장을 올려도 모델의
변별력이 약해 천장 근처에 가지도 못하기 때문이다. 즉 flat 비율 교정은 **필요조건이지
충분조건이 아니다.** 15m은 어느 변형도 유의성에 못 미쳤다.

→ 그래서 **배리어 숫자는 바꾸지 않았다.** 8개월 단일 국면 표본(30m 유효표본 ~690건)으로
새 표를 확정하는 건 앞선 FL A/B에서 한 번 저지른 실수의 반복이다. 구조만 드러내고 숫자는
보존했다.

#### [4] 비용 강등 규칙은 죽어 있다

Ver 1.2 §3.2의 비용 강등이 전 구간·전 Horizon에서 **한 건도 발동 안 했다.** 배리어 폭이
왕복 비용의 **94~557배**(5m 150틱 / 15m 410틱 / 30m 892틱 vs 1.6틱)이기 때문이다. 규칙이
틀린 게 아니라 배리어가 비용과 아무 관계 없는 크기라 검사 자체가 무의미하다. 덤으로 flat
레이블의 **98.7~99.6%가 비용을 넘는 이동(중앙값 52/132/232틱)을 가진 채 0으로 뭉개져** 있다.

#### 결선한 것

- [x] `models/label_geometry.py` 신설 — `LabelGeometry`(천장 대 게이트·비용규칙 생존·버려진
      방향정보) + `check_horizon_ladder()`. `score_calibration`/`threshold_report`와 같은
      자기판정 계열인데, **저 둘은 학습이 끝나야 돌리는 반면 이건 학습 전에 몇 초로 난다** —
      이 결함들은 레이블을 만든 순간 이미 확정돼 있다. 게이트 상수는 일부러 복제하고
      테스트로 동기를 잡는다(따라가면 어긋남 자체가 안 보인다).
- [x] `models/labeling.py` — 시간배리어를 **봉 수 정본**으로 재작성(값 불변, 구조만 노출).
- [x] `scripts/run_label_geometry.py` · 테스트 13건.

#### 남은 것

- [ ] **게이트를 절대상수에서 분위수 기준으로** — 이게 [2]의 정공법이다. `SCORE_THRESHOLD
      =0.20`은 flat 비율이 바뀔 때마다 의미가 달라지는 값이라 상수로 둘 수가 없다.
      레이블을 안 건드리고도 30m의 구조적 봉쇄가 풀린다.
- [ ] 배리어 표 재확정은 **국면이 하나 더 생긴 뒤**(하락/횡보). 지금 표본으로 고르면 상승장
      전용 표가 된다. 그때 `run_label_geometry.py` → `run_model_sweep.py` 순으로 돌릴 것.
- [ ] reg3(회귀 타깃)는 30m에서 current와 대등했고 flat 클래스가 아예 없어 [2]의 천장
      문제에서 자유롭다 — 국면이 늘면 1순위 후보.

### 거래 대상 확정 ([MW0601], 2026-08-04, 사용자 확정)

    선물: 미니선물
    옵션: 먼쓰리 · 월위클리 · 목위클리

근거·설계 논거는 `DECISION_LOG.md` 2026-08-04 3차 항목. 요점만: 종전 `K200_OPT`는 **소비자가
없는 죽은 토큰**이었고(설정에만 존재), 미니옵션은 2026-07-22 실측에서 **상장 0/0**이었다.

- [x] `core/universe.py` 신설 — 어휘 정본. 토큰 4종 + 검증 + 시리즈 매핑.
- [x] `InstanceConfig` 검증기 — 모르는 토큰은 **기동 시점에 거부**(구 `K200_OPT` 포함).
- [x] `configs/instance.yaml` 갱신 · `OptionChainPoller` 다중 시리즈화 · 테스트 14건.

#### 남은 것

- [ ] **`OptionChainPoller`를 `run_l1_daily.py`에 결선** (기한 2026-08-11) — 유니버스는
      정해졌는데 **폴러는 여전히 아무 스크립트에도 안 붙어 있다.** 같은 계좌 WS 다중연결
      문제와 함께 풀어야 한다. `InvestorFlowPoller`가 폴러만 만들고 7개월을 날린 전례가
      있고, 이건 그 세 번째다.
- [ ] 옵션 아카이빙 경로 — `flow_archiver.py`와 같은 성격. **결선보다 먼저** 만들 것
      (순서가 반대면 첫 폴링이 증발한다, 2026-08-04 파생 수급에서 배운 것).
- [ ] 미니옵션 상장 여부 재확인 — 마스터파일을 다시 받아 D/E 계열이 생겼는지. 생겼으면
      토큰만 추가하면 된다.

### 옵션체인 결선 — "WS 문제"는 오진이었다 ([MW0601], 2026-08-04)

`OptionChainPoller`(2026-07-28 신설)가 몇 달간 미결선이던 사유가 **"같은 계좌 WS 2연결 문제"**
였는데, 그 제약은 옵션 **틱** 구독에 걸리는 것이고 이 폴러는 **순수 REST**라 WS를 하나도 안
연다. 진짜 제약은 유량이었다 — 근월 체인 전량 1,356다리 = **1회 폴링 22.6분**.
설계 근거·마흐디 실측 인용은 `DECISION_LOG.md` 2026-08-04 항목.

- [x] `data/option_chain_archiver.py` 신설 — output1/2/3 전부 보존, **사이클 단위 flush**
      (하루 3,276행이라 스냅샷마다 전체 재작성하면 O(n²)).
- [x] `data/last_price.py` 신설 — ATM 기준가 공급(틱↔포인트 환산 단일화, 노후값은 None).
- [x] `OptionChainPoller` 재작성 — 시리즈 1개/인스턴스 · ATM±10 · **전량 폴백 금지** ·
      원천을 `get_asking_price()`→`get_quote()`로 교체(OP 피처가 필요로 하는 IV·감마·OI가
      전자엔 없고 후자에 다 있다).
- [x] `run_l1_daily.py` 결선 — 먼쓰리 300초@0s / 위클리 각 600초@100s·200s, 만기일 주기 교대.
      `KISRestClient` 단일 공유. 기동 로그가 **수요 0.330건/초·점유 33%·백오프 내성 3.03배**를
      매일 찍는다.
- [x] 테스트 47건 · 실계좌 end-to-end(6다리 → parquet) 통과.

#### 남은 것

- [ ] **2026-08-05 첫 실운영 로그 확인**(기한 08-11) — 위 실측은 6다리라 **장중 전량 사이클
      (42다리)은 아직 안 돌려봤다**. `SchedulerTickMissed` 건수와 `data/option_chain/` 행수로
      예산 계산(126초 / 300초 격자)이 실제와 맞는지 대조할 것.
- [ ] OP 피처 구현 — 데이터는 이제 쌓이지만 `op_iv_chg`/`op_pcr_*`/`op_gex`는 아직 없다.
      수집과 피처는 별개 단계다. **KIS Greeks를 그대로 쓸지 BS로 재계산할지**가 첫 결정
      (`raw` 보존해 뒀으니 둘 다 가능).
- [ ] 호가(`get_asking_price`) 수집은 **일부러 뺐다** — 현 OP 피처에 소비처가 없고 다리당
      호출이 2배가 된다. Options AI의 유동성 선발(% 스프레드, Cao-Wei)이 필요해지는 시점에
      ATM±2 정도만 저빈도로 추가하면 된다(마흐디 `LIQUIDITY_ATM_EACH_SIDE=2` 전례).
- [ ] `rg_basis` 계열 — KOSPI200 현물이 옵션 응답 `output3`에 매 폴링 실려 오므로 현물지수
      전용 소스 없이도 베이시스 계산 경로가 부수적으로 열렸다.

### 피처 재설계 — F0(인프라·관문) 완료 · F2(MS 수집) 결선 ([MW0601], 2026-08-04)

조사 결론: 설계 152개 중 **모델 도달 44개**. FL 9개는 "미구현"이 아니라 **미결선**이었다
(생성처 7곳 전부가 사이드카 미주입). 근거·설계 논거는 `DECISION_LOG.md` 2026-08-04 항목.

- [x] `features/spec.py` — `feature_set` → 카테고리 → 정확한 피처 이름 단일 정본.
      `v2026.07`=PX+VL(121) · `v2026.08-fl`=+FL(130)
- [x] `features/sidecar.py` — `DailySidecar` Protocol(미래참조 계약) · `flow_as_of`→`as_of`
- [x] `FeatureEngine` 스펙 구동 · 사이드카 불일치 **생성 시점 거부**
- [x] `InstanceConfig.feature_set` 기동 시점 검증 · 윈도우 예산 테스트 스펙 구동화
- [x] `features/gate.py` + `scripts/run_feature_gate.py` — Ver 1.4 §3 관문(테스트 27건)
- [x] **`px_macd_h_5` 상수 0 버그 수정** — 관문 첫 실행에서 발견(`_MIN_SIGNAL_PERIOD=2`)
- [x] `Tick` L1 호가 + `raw_fields` · `normalizer` quote rule · `data/tick_archiver.py` ·
      `run_l1_daily.py` 결선(아카이버가 수집기보다 먼저)

테스트 1250 → 1297건 통과, ruff 클린.

#### 관문 첫 실측 (연속물 63,508봉, 2025-12-12~2026-08-04, 겹침 3봉 보정)

| Horizon | 표본 | active | 통과율 |
|---|---|---|---|
| 5m | 12,684 | 7 | 5.8% |
| 15m | 4,216 | 12 | 9.9% |
| 30m | 2,099 | 17 | 14.0% |

생존자 대부분이 변동성 크기 계열(vl_atr·vl_rv·vl_semi·px_bb_width)이고 **IC 부호가 전부
음수**다. 표본이 8개월 단일 상승장이라 국면 특수적일 가능성이 높다 — 생존 검정(③)은 G1 창이
하나뿐이라 실행 자체가 안 됐다(관문이 의도대로 아무도 탈락시키지 않았다).

#### 남은 것

- [ ] **2026-08-05 첫 실운영 틱 적재 확인**(기한 08-12) — `TickArchiveSummary` 행수와
      `data/ticks/` 크기를 부하 추정(5~10만행 · 0.3MB)과 대조. 0행이면 결선이 안 붙은 것이다
      (이 프로젝트의 반복 실패 모드이므로 **첫날 반드시 확인**).
- [ ] **틱 프레임 미확정 필드 매핑 실측**(기한 08-12) — 미결제약정(idx18 추정)·이론가(12)·
      총잔량(38/39)·체결강도(45). 절차는 `Docs/KIS_RAW_FIELD_RANGES.md` "다음 실측에서 할 것".
      **보존은 이미 시작됐으므로 확정되면 소급 적용된다.**
- [ ] **F1 — EV 14개**(다음 착수 대상). 전부 시각/달력 함수라 **163거래일 전체 소급** →
      즉시 A/B 가능. `ev_econ_prox`/`ev_econ_grade` 2개만 외부 소스 필요라 제외.
      지금 모델은 "장 마감 10분 전"과 "개장 직후"를 구분할 수단이 하나도 없다.
- [ ] **F4 — FL 결선**: 스펙(`v2026.08-fl`)은 준비됐고 `flow` 사이드카를 실제로 주입하는
      일만 남았다(trainer·harness·run_l1_daily). 주입 없이 이 이름을 쓰면 이제 기동이 깨진다.
- [ ] **F3 — RG 매크로 일봉**(USDKRW·US10Y는 `get_overseas_daily_chartprice`로 **백필 가능**).
      VIX·CNH는 스냅샷뿐이라 폴러+아카이버 필요. 시장폭 3종은 소스 없음 — 명시적 제외.
- [ ] **F5/F6 — OP·MS 피처는 3개월 뒤**(2026-11경). 지금 붙이면 학습 이력이 163거래일에서
      0일로 리셋된다. 수집만 계속 돌린다.
- [ ] **관문 재실행은 국면이 하나 더 생긴 뒤** — 지금 통과한 7/12/17개는 "여러 국면에서
      살아남았다"가 아니라 "한 국면에서 유의했다"까지만 증명됐다.
- [ ] `side_hint` quote rule 편향 측정(F6 전) — 동시 스냅샷 근사라 일부 방향이 반대로 잡힌다.
      쏠림 방향이 있는지는 추측이 아니라 실측 대상.

### F1 — EV(이벤트·시간·만기) 완료 ([MW0601], 2026-08-04)

근거·설계 논거는 `DECISION_LOG.md` 2026-08-04 2차 항목.

- [x] `features/ev_core.py` — 기저 12개 / **16컬럼**(요일 one-hot 5). 확정 시각 기준.
- [x] `EventCalendar` 만기·거래일 질의 7종 + **만기 규칙 중복 제거**
      (`backfill.monthly_expiry`와 `is_expiry_day()` 인라인 판정이 두 벌이었고 후자는
      휴장 보정이 없었다 — 정본을 event_calendar로 합치고 backfill은 재수출)
- [x] `features/sidecar.py` — **관측/참조 2종 구분** + `build()` 단일 조립처
- [x] `spec`에 EV 등록 · `v2026.08-ev`(137) · `v2026.08-fl-ev`(146)
- [x] `trainer`·`run_feature_gate`에 `sidecars` 결선
- [x] `ev_lunch_flag` 창 **실측**(11:30~14:00 — 163거래일 분당 거래량 프로파일)

테스트 1297 → 1329건 통과, ruff 클린.

#### 관문 A/B 실측 — 얻은 것은 시각 축 하나다

| Horizon | 121개 | 137개 | 증가 |
|---|---|---|---|
| 5m | 7 | 7 | **0** |
| 15m | 12 | 13 | +1 (`ev_tod_cos` IC +0.067 t +2.50) |
| 30m | 17 | 19 | +2 (`ev_tod_cos` +0.084 t +2.24 · `ev_close_remain` −0.084 t −2.22) |

Horizon이 길수록 시각 축 IC가 커진다(0.021 → 0.067 → 0.084). 나머지 EV 전부(요일 5개·
만기 D-day 3종·만기플래그·롤오버·연휴인접·점심)는 이 표본에서 신호가 없다. 관문 ②가
예고된 중복(`ev_open_elapsed`/`ev_close_remain`)을 30m에서 실제로 잡아 떨어뜨렸다.

**소폭이다.** t값이 2.2~2.5로 임계 바로 위이고 8개월 단일 국면 값이다.

#### 남은 것

- [ ] **프로덕션 `feature_set` 전환 판단** — EV를 켜려면 그 피처로 모델을 재학습해야 한다
      (R11 장중 배포 금지). 이번 산출물은 측정까지다. `run_model_sweep.py`를
      `--feature-set v2026.08-ev`로 돌려 **손익 기준** 차이를 먼저 볼 것(관문 통과 수가
      늘었다는 것과 돈이 된다는 것은 다른 얘기다).
- [ ] **`configs/krx_holidays.yaml`에 2027년 추가**(기한 2026-12-01) — 지금 2025~2026뿐이라
      2027로 넘어가면 `ev_dte_*`가 통째로 NaN이 된다. 조용히 틀린 D-day보다 낫도록 일부러
      그렇게 뒀지만, 연말 전에 갱신해야 한다. **KRX 공식 공지로 확인할 것**(현 파일은
      집계 사이트 교차대조라 "작성됨≠확인됨").
- [ ] **롤오버 창 실측**(`ROLLOVER_TRADING_DAYS`, 현재 미실측 5거래일) — 절차: 각 만기
      직전 10거래일치 **차월물** 분봉을 추가 백필(`data/backfill.py`, 만기물도 조회된다)
      → 근월/차월 일별 거래량 비중 곡선 → 교차 시점. 지금은 두 월물이 만기일 하루만 겹쳐
      측정 자체가 불가능하다.
- [ ] **위클리 만기 규칙 symbol_master 대조** — ① 위클리 요일이 휴장이면 KRX가 앞당기는지
      미루는지 ② 정규월물 만기 목요일에 목위클리가 별도 상장되는지(현 구현은 그날도
      위클리로 세어 `ev_dte_opt_w`가 0을 낸다). 둘 다 요일 규칙 근사로만 처리 중.
- [ ] `ev_econ_prox`/`ev_econ_grade`/`ev_overnight_gap_risk` — 경제지표 발표 일정 피드가
      생기면. `ev_core.EXCLUDED_FEATURES`에 상수로 남겨 뒀다(빠뜨린 것과 구분).

### 마흐디 만기·운영 체계 조사 및 이식 ([MW0601], 2026-08-04)

근거는 `DECISION_LOG.md` 2026-08-04 3차 항목. **F1의 미해결 항목 하나가 마흐디에 이미
실측 답이 있었다** — 그리고 그 답대로면 F1 구현이 틀렸다(연 12회 오답).

- [x] `EventCalendar.has_thursday_weekly()` — 먼슬리 만기 주엔 목위클리 상장 없음
      (마흐디 2026-07-10 실측). `ev_dte_opt_w`가 먼슬리 만기일에 0을 내던 결함 수정
- [x] 위클리 휴장 보정을 **먼슬리와 같은 관례**(직전 거래일)로 통일 — 검증된 관례가 하나
      있는데 위클리에 검증 안 된 관례를 새로 만들고 있었다
- [x] `IntegrityReport.tick_rows` + `min_tick_rows` 하한 임계 + breach
- [x] `pending_verifications.yaml`에 **"존재한다부터 적는다"** 규약 명문화 +
      `tick-collection-live` 등록(min 1000행 · 3거래일 · 기한 2026-08-12)

테스트 1331 → 1334건 통과, ruff 클린.

#### 남은 것

- [ ] **`futs_last_tr_date`로 만기 요일 규칙 대체**(기한 2026-08-19) — 마흐디가 쓰는 권위
      있는 출처이고, MESSIAH `OptionQuoteSnapshot.raw`가 **이미 그 필드를 보존한다**.
      절차: 옵션체인 3~5거래일 적재 후 `data/option_chain/{series}/*.parquet`에서
      `futs_last_tr_date`를 시리즈별로 뽑아 → `EventCalendar.next_weekly_expiry()`/
      `monthly_expiry()` 산출과 대조 → 어긋나면 규칙이 아니라 **실측을 정본으로** 삼는다.
      이걸로 위클리 휴장 보정 관례(현재 미검증)도 함께 확정된다.
- [ ] **F5에서 `op_gex`를 시리즈 합산으로 계산하지 말 것** — 마흐디 2026-08-03 교훈:
      3개 북을 합산하면 만기별 정보가 서로를 덮고, **만기 Pinning은 만기 당일 북에서만**
      나온다(그 북은 잔존만기 0이라 BS 감마가 정의되지 않는다). 용도 분리: 먼슬리 → GEX/
      감마플립 주 입력 / 위클리 → 핀 리스크 전용. MESSIAH는 이미 시리즈별로 적재하므로
      분리는 가능하다(`mahdi/features/options_intel.legs_by_expiry` 참조).
- [ ] **자동 산출/사람 해석 문서 분리 규약 검토** — 마흐디는 `auto/`에 표와 델타만 두고
      (해석 없음) 사람 보고서가 그것을 인용한다("도구는 판정하지 않는다"). MESSIAH는
      `logs/daily_integrity_*.json` + `Docs/dailycheck_prompt.txt` 조합이라 경계가 덜
      분명하다. 이식 여부는 다음 주간회의 안건.
- [ ] **옵션 실거래 착수 시 참조**(지금은 수집만이라 보류):
      만기 북 2계층 선발(장전 복합 유동성 점수) · 유동성 강등 트리거(%스프레드 Cao-Wei,
      **달러 스프레드 금지**) · 0DTE 플레이북(사이즈 50%·시간손절 절반·14:00 이후 Charm).
      출처 `docs/Dev_md/RESEARCH_EXPIRY_SELECTION_v1.md` §2.2~2.3, v6 §11.4.

### 예측 대상 축 실측 — 방향은 비어 있고 변동성은 차 있다 ([MW0601], 2026-08-04)

근거·방법·주의는 `DECISION_LOG.md` 2026-08-04 4차 항목. 0804 메모의 2·3번 질문에 대한
**측정된 답**이다.

- [x] `models/labeling.forward_realized_volatility()` — 다음 N봉 실현변동성(N=시간배리어)
- [x] `run_feature_gate.py --label direction|volatility` + **동순위 통제**(3분위 이산화)
- [x] 실측 완료 (테스트 1334 → 1339건 통과)

| Horizon | 방향 축 통과 | 변동성 축 통과 | 최대 \|IC\| |
|---|---|---|---|
| 5m | 7 / 137 | **78 / 137** | 0.040 → **0.674** |
| 15m | 13 / 137 | **69 / 137** | 0.083 → **0.571** |
| 30m | 19 / 137 | **66 / 137** | 0.124 → **0.480** |

방향 축에서는 **부호 있는 방향 피처 39개가 3 Horizon 전부에서 전멸**했다(117 판정 중 1건,
그마저 무부호 추세강도 `px_adx`). 같은 입력이 변동성 축에서는 39개 중 22개가 통과한다.

#### 다음 — IC 0.67을 알파로 읽지 않기 위한 검정 3개

- [x] **① 단순 기준선 대비 증분** — 완료(2026-08-04). `DECISION_LOG.md` 5차 항목.
      `gate.partial_spearman()` + `--baseline rv|har|none` + `--baseline-features`.
      테스트 1339 → 1349건 통과.

      **답: 넘는다. 다만 넘는 주체가 바뀐다.**

      | Horizon | 통제 없음 | 직전RV 통제 | RV+GK 통제 |
      |---|---|---|---|
      | 5m | 78/137 | 63 | **59** |
      | 15m | 69/137 | 59 | **56** |
      | 30m | 66/137 | 62 | **47** |

      변동성 추정량 계열은 |IC| 중앙값 0.469 → **0.048**로 무너진다(5m) — 기준선의
      프록시였다. 대신 **시간 축이 상위를 독점**한다(`ev_tod_cos` 30m 0.442,
      `ev_close_remain` 15m은 통제 후 0.062 → **0.335**로 5배). 일중 변동성 계절성이
      변동성 수준과 직교하기 때문이다(억제변수 효과).

      → **어제 방향 축에서 "소폭"이라 보고한 EV가, 변동성 축에서 지속성을 통제하면
      가장 값어치 있는 카테고리다.**

      구현 중 테스트가 실제 버그 2개를 잡았다: 잔차가 수치적으로 0일 때 부분상관이 **1.0**
      (= 기준선 복사본이 "완벽한 증분"으로 보고됨) · 다변량 순위 부분상관의 누수(비보수적,
      그래서 기본 기준선을 단변량으로).

- [ ] **② 수익화 경로 검토** — 변동성 예측이 돈이 되려면 파는 수단이 필요하다(옵션
      스프레드·레인지 매매). **미니선물 방향 매매로는 직접 환금되지 않는다.** Ver 1.3
      Options AI가 원래 그 자리인데, 지금은 옵션을 수집만 한다.
- [ ] **③ IV 대비 우위**(2026-11경) — 진짜 질문은 "실현변동성 예측이 시장의 **내재**
      변동성보다 나은가"다. 옵션체인이 2026-08-05부터 쌓이므로 3개월 뒤 F5에서 측정 가능.

#### 이 결과가 F3·F4에 미치는 영향

- **F3 RG / F4 FL을 방향 축 기준으로 착수하지 말 것.** 둘 다 "방향 예측에 정보를 더 넣는"
  작업인데, 방금 측정이 그 축에 정보가 없다고 말한다. ①의 답을 보고 **어느 축으로 선별할지**
  정한 뒤 착수한다.

---

## 내일(2026-08-05 수, 거래일) 장후 점검할 일 — [MW0601], 2026-08-04 기록

> 오늘(08-04) 세 세션에서 결선한 것들의 **첫 실운영일**이다. 아래 A는 전부 "오늘 안 받으면
> 영구 소실"이거나 "결선이 조용히 안 붙는" 부류라 **장후 즉시** 확인한다.
>
> 순서: `logs/daily_integrity_20260805.json` 먼저 → 그 breaches가 가리키는 것부터.
> `uv run python scripts/agenda.py`가 등록부 판정(`FixVerification*`)을 안건으로 올린다.

### A. 오늘 결선분 첫 실운영 (최우선)

- [ ] **A-1 체결틱 수집** — 이 프로젝트가 폴러를 만들고 결선을 안 붙여 데이터를 잃은 전례가
      셋이다(수급 7개월·옵션체인 수개월·FL 피처 모델 미도달). 틱은 **백필 경로가 아예 없어**
      안 쌓인 날은 영원히 빈다.
      - 종료 로그 `TickArchiveSummary`의 `rows` — **0이면 결선이 안 붙은 것**
      - `data/ticks/A05608/2026-08-05/` 조각 존재 · 무결성 리포트 `tick_rows`
      - 등록부 `tick-collection-live`(min 1000행 · 3거래일 · 기한 08-12) 판정
      - **대조**: 부하 테스트 추정 하루 5~10만행 · 0.3MB. 자릿수가 어긋나면 원인 조사
        (틱 수가 적은 건 시장, 파일이 없는 건 배선)
- [ ] **A-2 `side_hint` 실분포** — 실데이터로 처음 본다. +1/−1/0 비율을 볼 것.
      quote rule은 **동시 스냅샷 근사**라(Lee-Ready는 체결 직전 호가를 쓴다) 일부 방향이
      반대로 잡힌다. 한쪽으로 크게 쏠리면 그건 잡음이 아니라 **편향**이고, F6 전에 재설계
      대상이다. 0의 비율이 비정상적으로 높으면 호가가 안 실려 오는 것(A-3과 함께 볼 것).
- [ ] **A-3 원시 필드 보존 확인** — 조각 컬럼이 **60개**인가(파싱 10 + `f00`~`f49`).
      `f34`~`f37`(호가)이 채워져 있는가. 이게 비면 `Docs/KIS_RAW_FIELD_RANGES.md`에 적어 둔
      "미확정 필드 매핑 실측"(기한 08-12)의 원자료 자체가 안 쌓이는 것이다.

### B. 기한이 도래한 이전 세션 항목

- [ ] **B-1 WS 다중 레코드 수정의 첫 검증** — 08-04 L1은 **수정 전 코드로 기동**했으므로
      08-05 수집분이 첫 검증이다. KIS 공식 분봉 대비 우리 아카이브 거래량 비율을 볼 것 —
      종전 실측 **49~52%**(3거래일)에서 ~100%로 올라와야 한다. 방법은
      `data/normalizer.py` 모듈 docstring의 대조표를 만든 절차와 동일.
      (거래량 계열 피처 전부와 종가 기반 레이블의 1/4이 이 값 위에 있다.)
- [ ] **B-2 옵션체인 첫 전량 사이클(42다리)** — 기한 08-11. 종전 실측은 **6다리**뿐이라
      장중 전량 사이클은 아직 안 돌려봤다.
      - `SchedulerTickMissed` 건수 · `data/option_chain/{series}/2026-08-05.parquet` 행수
      - 기동 로그의 예산(수요 **0.330건/초 · 점유 33% · 백오프 내성 3.03배**)이 실제와 맞는가
      - 먼쓰리 42다리 × 78사이클 ≈ 3,276행이 기준
- [ ] **B-3 파생 장중 수급 첫 수집** — `data/flow_intraday/K2I/2026-08-05.parquet`에
      3업종(F001 선물 · OC01 콜 · OP01 풋) × 74컬럼이 쌓이는가. 이것도 **당일 누적만 주는
      엔드포인트라 소급 불가**다.

### C. 등록부 자동 채점 (기한 2026-08-14)

- [ ] **C-1** `ui-crash-isolation`(native_crashes 0) · `ui-restart-observability`(ui_restarts 0)
      · `crash-forensics-armed`(breaches 0). **`재발`이 뜨면 그게 가장 중요한 신호다** —
      2026-07-29~08-03에 같은 UI 크래시를 세 번 "고쳤다"고 판정하고 세 번 재발시켰다.

### 점검 대상이 **아닌** 것 (오해 방지)

- **EV·피처 관문 결과**는 장후 로그로 확인할 대상이 아니다. 프로덕션 `feature_set`은 아직
  `v2026.07`이고, EV를 켜려면 재학습이 필요하다(R11 장중 배포 금지). 손익 판단은
  `run_model_sweep.py --feature-set v2026.08-ev`로 별도 세션에서.
- **MS/OP 피처**도 마찬가지 — 오늘 켠 것은 **수집뿐**이다. 3개월 누적 후(2026-11경).

---

## 2026-08-04 일일점검 대응 완료 — P0/P1/P2 전건 구현 ([MW0601], 2026-08-05)

상세 근거·유도·정정은 `dev_memory/DECISION_LOG.md` 2026-08-05 항목. 여기엔 **다음에 볼 것**만.

- [x] **P0-1 시계** — w32time Automatic + NTP(오프셋 14.41초 → −0.0006초) · `ops/clock_skew.py` ·
      `self_check.check_clock()`(>5초 기동 거부) · 리포트 `clock_skew_seconds`
- [x] **P0-2 종료 경합** — `wait_for_bar()` · 봉 도착 기반 롤오버 · 스큐 지연 · 늦은 봉 거부 ·
      **Horizon 총합 항등식**(매일 자동)
- [x] **P0-3 크래시 집계** — exit-code 오판 제거(센티널 판정) · `supported` 분리 · 집계 불가 breach
- [x] **P1-1 무장 마커** — `.bat` stderr 병합을 `cmd /c`로 · 탐지기 `.search()` · 구조화 로그 이중화
- [x] **P1-2 아카이브** — 08-04 재백필(84,346 → 152,963) · 재합성 · `verify_archive_volume.py`
- [x] **P1-3 장중 가드** — `ops/session_guard.py` + 연구 스크립트 6개
- [x] **P2** — `SelfEvalReport` 손익 4지표 `float | None`

### 2026-08-05 장후에 볼 것 — 위 수정들의 첫 실전 검증

기존 A/B/C 체크리스트(위 "내일(2026-08-05 수, 거래일) 장후 점검할 일")보다 **먼저** 본다.
전부 `logs/daily_integrity_20260805.json` 한 파일에서 확인된다.

- [ ] **D-1 시계** — `clock_skew_seconds`가 찍히는가(없으면 계측 배선이 안 붙은 것) ·
      |값| < 2초인가. **대조**: 08-04엔 이 필드가 아예 없었고 실제 스큐는 +9.72초였다.
      부수 확인: `1m` `FeaturePublish`의 로컬 초가 :50 → **:59~:00**로 올라와야 한다.
- [ ] **D-2 종료 경합** — `horizon_findings`가 빈 배열인가. 비어 있지 않으면 셋 중 하나다:
      ① 경합 재발 ② 스큐로 버킷이 잘림 ③ 1분봉만 백필하고 재합성 안 함.
      **대조**: 08-04 라이브 산출물은 1분봉 합보다 상위 봉 합이 137 적었다.
- [ ] **D-3 크래시 집계** — `native_crashes.available`이 **true**인가(건수가 0이어도).
      false면 P0-3이 안 들은 것. 이게 서야 `ui-crash-isolation`이 판정을 채운다.
- [ ] **D-4 무장** — `crash_forensics.armed`가 ui·l1_daily·g2_paper **셋 다 true**인가.
      08-05는 새 `.bat`(cmd /c 병합)로 기동하는 첫 날이다.
- [ ] **D-5 등록부** — `crash-forensics-armed`·`crash-count-measurable`·`horizon-volume-identity`·
      `clock-sync-restored`가 **1/N로 시작**하는가(08-05가 이들의 첫 채점일).

### 미착수로 남긴 것 (의식적 선택)

- **거래량 외부 대조의 장후 자동화** — `verify_archive_volume.py`는 사람이 부르는 도구로 뒀다.
  REST 호출을 15:35~15:40 종료 예산에 넣으면 종료 절차가 네트워크에 의존하고, 이 대조가
  필요한 상황(파서 변경·백필 이후)은 매일이 아니다. 대신 리포트가 `session_git_shas`를
  사실로만 기록한다 — 판정은 안 한다(연구 커밋이 잦아 매일 울리면 늑대소년).
- **장전 08:45~09:00 15봉의 성격 판정** — 예상체결인지 실체결인지. 보류 사유였던 "WS 연결을
  다툴 수 없다"는 08-04에 오진으로 판명됐고(커밋 `15041ad`), 원시 50필드 보존도 결선됐다.
  **판정 수단이 이미 손에 있다** — 다음 세션 후보.
- **고도화 3(상수 피처 검출)** — 하루 내내 분산 0인 피처를 리포트에 올리는 건. `px_macd_h_5`
  (값을 내므로 `nan_ratio`에 흔적이 없었다)류를 운영 경로에서 잡는다. 관문은 연구 경로에만 있다.
- **고도화 4(변동성 축 번들을 shadow로)** — G2가 5거래일 연속 `return 0.0`인 원인은
  파이프라인이 아니라 번들 부재다. 08-04 관문이 방향 축 전멸·변동성 축 생존을 실측으로
  확정했으므로, 결선의 최단 경로는 변동성 축 번들을 먼저 shadow에 올리는 것.

---

## 고도화 5종 완료 — 매일 돌려야 하는 것 ([MW0601], 2026-08-05)

상세는 `DECISION_LOG.md` 2026-08-05 2차 항목. 여기엔 **운영 절차**만.

- [x] 고도화 1 — 외부 대조(`volume_check_*.json`)를 리포트 1급 축으로
- [x] 고도화 2 — `unmeasured` 축 + `STALLED`(판정 불가 정체) 상태
- [x] 고도화 3 — 죽은 피처(상수·항상NaN)를 운영 경로에서 검출
- [x] 고도화 4 — 변동성 축 매 거래일 채점(`vol_scorecard`)
- [x] 고도화 5 — 호스트 위생(디스크·전원·Docker)을 `self_check`에

### 장후 절차 — 이 둘을 안 돌리면 리포트가 "미측정"으로 남는다

```
python scripts/verify_archive_volume.py --date <오늘>   # 공식 분봉 대비 거래량
python scripts/run_vol_scorecard.py     --date <오늘>   # 변동성 축 20거래일 채점
python scripts/daily_integrity_report.py --date <오늘>  # 위 둘을 읽어 리포트 재산출
```

나머지 축(시계 스큐·피처 건강도·호스트)은 수집 세션과 리포트가 자동으로 채운다.
`daily-axes-measured` 등록부가 `unmeasured_count ≤ 0`을 3거래일 연속으로 요구하므로,
안 돌리면 그 사실이 매일 리포트에 남는다.

> **Task Scheduler 등록은 아직 안 했다.** 두 도구 다 장후 실행이 전제이고
> (`session_guard`가 정규장이면 거부한다), 며칠 손으로 돌려 소요시간·실패모드를 본 뒤에
> 자동화하는 편이 낫다고 판단했다. 자동화 시 15:45 이후 트리거로 등록할 것.

### 변동성 축 첫 실측이 가리키는 다음 할 일

2026-08-04 기준 최근 20거래일에서 **측정 가능한 5개 중 기준선(직전RV+GK)을 넘은 것이
하나도 없다**(5m/15m/30m 전부 0/7). 그리고 관문이 상위로 지목했던 `ev_tod_cos`·
`ev_close_remain`은 **프로덕션 feature_set에 없어 측정조차 안 된다**.

- [ ] **EV를 프로덕션 feature_set에 올리기** — 이게 채점이 가리키는 최단 경로다.
      `run_model_sweep.py --feature-set v2026.08-ev`로 장후 세션에서 재학습(R11) →
      `configs/instance.yaml`의 `feature_set` 승격. 승격 후 변동성 축 채점이 `ev_*`를
      실제로 재기 시작하는지가 첫 확인점.
- [ ] **30m 기준선 IC가 +0.083까지 내려온 것** — 08-04 관문의 5m +0.576과 견주면 지속성
      자체가 최근 훨씬 약하다. 국면 변화인지 표본 문제인지 며칠 더 보고 판단.
- [ ] 변동성 모델(번들)은 **아직 만들지 않는다** — R18대로 이 채점이 20거래일 쌓이고
      기준선을 넘는 피처가 실제로 나온 뒤에.

### 2026-08-05 장후 점검 순서 (D-1~D-5 앞에 붙는다)

0. 위 장후 절차 3줄 실행
1. `unmeasured`가 비었는가 — 안 비었으면 그 항목이 곧 오늘 안 돌아간 것
2. `degenerate_features`에 뭐가 잡혔는가 — 웜스타트가 200봉을 채웠으면 비어야 한다
3. 그다음 기존 D-1~D-5(시계·Horizon 항등식·크래시 집계·무장·등록부)

---

## 2026-08-05 장중점검 P0/P1/P2 전건 구현 완료 ([MW0601], 2026-08-05)

상세 근거·유도는 `dev_memory/DECISION_LOG.md` 2026-08-05 장중점검 항목. 여기엔 **할 일**만.

- [x] **P0-1 겹④** — 마지막 구성 1분봉 도착 대기(상한 5초) + 대기 전 확정 대상 고정
      (겹②에 있던 잠복 결함 동반 수정) + 오프라인 재생 3곳 `force=True`
- [x] **P0-2 관측** — `l1.composer` heartbeat · 리포트 `late_bar_drops` · 등록부
      `composer-bucket-completeness`
- [x] **P1-1** — `FixedTickScheduler` 중복 발화 차단
- [x] **P1-2** — 옵션체인 다리 1회 재시도 + `OptionChainPollRetried` 태그 분리
- [x] **P2-1** — `LastPriceTracker.seed_preopen()`(첫 실틱 전까지만)
- [x] **P2-2** — `host_health.check_cpu_contention()`(기록만, 판정 없음)

### ⚠ 오늘(08-05) 장 종료 후 반드시 — 순서가 중요하다

**아직 적용 안 됐다.** R11(장중 배포 금지)에 따라 파일만 고쳤고, 돌고 있는 프로세스는
08:35에 뜬 옛 코드다 — **오늘 남은 수집분은 계속 손상된다**(3m 기준 ~17%).

```
1. python scripts/daily_integrity_report.py --date 2026-08-05
   → late_bar_drops가 26+ 로 찍히는가 · horizon_findings가 비지 않는가
   (관측 장치가 이 사고를 실제로 잡는지 보는 것이 목적 — 재합성 전에 봐야 한다)

2. python scripts/run_recompose.py --symbol A05608
   → 1분봉은 무손상이므로 상위 Horizon 전량 복구된다

3. python scripts/verify_archive_volume.py --date 2026-08-05
   python scripts/run_vol_scorecard.py     --date 2026-08-05
   python scripts/daily_integrity_report.py --date 2026-08-05
   → 재산출. horizon_findings는 비고 late_bar_drops는 남아 있어야 정상이다
     (전자는 아카이브, 후자는 수집 당시의 사건)

4. 프로세스 재기동 → 다음 거래일(08-06)이 이 수정들의 첫 실전 검증
```

> **EV 재학습(`run_model_sweep.py --feature-set v2026.08-ev`)은 2번보다 뒤에.**
> 지금 돌리면 손상된 상위 Horizon 봉으로 학습한다.

### 2026-08-06 장후에 볼 것 — 이 수정들의 첫 실전 검증

- [ ] **E-1** `late_bar_drops == 0`인가. 아니면 겹④ 상한 5초가 모자란 것이다 —
      `ComposerFlushedIncomplete`의 `awaited_bar_open_kst`로 어느 분이 안 왔는지 본다.
- [ ] **E-2** `horizon_findings`가 빈 배열인가(E-1과 짝. 둘이 어긋나면 재합성 흔적이다).
- [ ] **E-3** 장전 옵션체인 — `OptionChainSkipped`가 **0건**인가. 08:35 사이클부터
      스냅샷이 있는가(`data/option_chain/regular/2026-08-06.parquet`의 첫 `ts_kst`).
      기동 로그에 "장전 기준가 시드" 줄이 찍혔는지 먼저 확인.
- [ ] **E-4** `OptionChainPollRetried`가 나왔는가. 나왔는데 `OptionChainPollError`가 0이면
      재시도가 실제로 다리를 살린 것이다 — 사이클당 42다리가 채워지는지 행수로 대조.
- [ ] **E-5** 호스트 `cpu` 항목이 리포트에 찍히는가. 며칠 모아 "정상인 날의 경합"을
      본 뒤에 임계를 정한다(지금은 판정 안 함).
- [ ] **E-6** `SchedulerTickMissed`·중복 사이클이 없는가(P1-1).

### 앞선 체크리스트와의 관계

기존 A/B/C(08-11 기한)와 D-1~D-5는 그대로 유효하다. 다만 **D-2(Horizon 항등식)는 오늘
이미 답이 나왔다** — 비지 않았고, 원인은 예상했던 셋(①경합 재발 ②스큐 ③재합성 누락) 중
어느 것도 아닌 **네 번째**였다. 예상 목록이 틀렸다는 사실 자체가 기록해 둘 값이다.

---

## 2026-08-05 2차 — 고도화 5종 전건 구현 완료 ([MW0601], 2026-08-05)

상세는 `DECISION_LOG.md` 2026-08-05 2차 항목. 여기엔 **운영 절차와 다음에 볼 것**만.

- [x] **고도화 1** — `MinuteBarAggregator.flush_due()` + 수신 지연 계측(`TickDeliveryLatency`)
      + 늦은 틱 로깅. **기본값은 여전히 `tick`** — 유예를 실측으로 정한 뒤 승격한다.
- [x] **고도화 2** — 합성기 내부 거래량 회계(`volume_identity()`) → 장중 연속 항등식
- [x] **고도화 3** — `HealthLevel.UNKNOWN` + CB 억제 경로 수정 + "근거 있는 OK"
- [x] **고도화 4** — 등록부 `premise` 블록 + `PREMISE_BROKEN` 상태
- [x] **고도화 5** — 학습 전 아카이브 정합 가드 + `ev-features-measured` 등록부

### 며칠 모아야 답이 나오는 것 — `minute_bar_close: timer` 승격

**이것만은 지금 결정할 수 없다.** 유예를 정할 회선 지연 분포가 오늘 처음 측정된다.

1. 매일 리포트의 `delivery_latency` 확인 (p50/p90/p99/max)
2. **3~5거래일** 모은 뒤 p99의 최댓값을 본다
3. `MINUTE_CLOSE_GRACE_SECONDS`(현재 1.0)를 그 값 위로 확정 → `minute_bar_close: "timer"`
4. 승격 후 첫 거래일에 `AggregatorLateTickDropped`가 **0에 가까운지** 확인 —
   많으면 유예가 모자란 것이다(그 로그의 `bar_open_kst`로 어느 분인지 바로 보인다)

> 승격 전까지 1분봉 확정 동작은 종전과 완전히 같다. 서두를 이유가 없다 — 겹④가 이미
> 정확성은 확보했고, 이건 **지연을 줄이는** 개선이다.

### EV 승격 (기한 2026-08-21, 등록부 `ev-features-measured`)

순서가 강제된다 — 어기면 `session_guard`가 거부한다:

```
1. python scripts/run_recompose.py --symbol A05608          # 손상된 상위 봉 복구
2. python scripts/daily_integrity_report.py --date 2026-08-05   # horizon_findings 비는지 확인
3. python scripts/run_model_sweep.py --feature-set v2026.08-ev  # 장후에만(R11)
4. configs/instance.yaml → feature_set: "v2026.08-ev"
5. 다음 거래일 리포트에서 vol_axis.*.absent_features 가 비는지 확인
```

5번이 안 비면 **승격이 조용히 안 먹은 것**이다. 등록부가 08-12부터 채점을 시작한다.

### 2026-08-06 장후 점검 — E-1~E-6에 더해서

- [ ] **F-1** `delivery_latency`가 리포트에 찍히는가. p99가 몇 초인가(승격 판단의 1일차 표본)
- [ ] **F-2** `l1.composer` 축이 상태판·UI에 뜨는가. 장전에는 `UNKNOWN`("확정한 합성봉이
      아직 없다"), 09:00 이후엔 `OK`("합성봉 N개 · 거래량 항등식 일치")로 바뀌는가
- [ ] **F-3** `l1.collector`가 08:35~08:45에 `UNKNOWN`인가(종전엔 `OK`였다). 그 구간에
      G2가 CB를 억제하지 **않는지**도 함께 — 억제하면 UNKNOWN 매핑이 안 붙은 것이다
- [ ] **F-4** `AggregatorLateTickDropped`가 몇 건인가. **틱 구동 기본값에서도** 나올 수
      있다(순서 뒤바뀐 틱) — 종전엔 조용히 버려지던 것이라 **오늘이 첫 관측**이다.
      0이 아니면 그 크기가 곧 timer 승격의 비용 추정치다
- [ ] **F-5** 등록부에 `전제 붕괴`가 뜨는가. 떴다면 회선 p99가 3.0초를 넘은 것이다

---

## 2026-08-06 장후 점검 — P0 3종 + 커버리지 축 ([MW0601], 2026-08-06)

상세는 `DECISION_LOG.md` 2026-08-06 항목. 여기엔 **운영 절차와 다음에 볼 것**만.

그날 10:03:49에 호스트가 재부팅됐다(이벤트 1074, 계획되지 않음). 그 사건 하나로 옵션체인
약 1,500다리 · 수급 264행 · 1분봉 21개가 **영구 소실**됐고, 그중 앞의 둘에 대해 리포트는
한 줄도 말하지 않았다.

- [x] **P0-1 아카이버 재기동 복원** — `flow_archiver` · `option_chain_archiver`에 기동 복원
      (`_restore_day`/`_restore_series`) + 축소 쓰기 거부(`_write_is_safe`)
- [x] **P0-2 부팅 자동 복구** — `install_scheduled_tasks.ps1`(부팅 트리거·재시도·
      StartWhenAvailable) + 기동 창 가드(`session_guard.launch_window_verdict`) +
      매일 무장 실측(`host_health.check_boot_recovery`)
- [x] **P0-3a 합성기 겹⑤** — `MultiHorizonBarComposer.restore_open_buckets()`
- [x] **P0-3b 장후 절차 자동화** — 재합성은 종료 시퀀스로, 나머지는 `run_postmarket.py`(15:45)
- [x] **고도화 2 적재 계열 커버리지** — `ops/series_coverage.py` + 리포트 결선 + 등록부 지표

---

### 장후 절차 — 이제 **자동이다** (2026-08-06 변경)

종전에 손으로 돌리던 3줄이 스케줄러로 들어갔다. 이틀 연속(08-05·08-06) 안 돌아서 내린 결정이다.

```
15:35  run_l1_daily 종료 시퀀스 :  통합 → 재합성 → 리포트   ← 네트워크 안 탐
15:40  Messiah-Shutdown        :  잔여 프로세스 정리
15:45  Messiah-Postmarket      :  재합성 → 거래량 대조 → 변동성 채점 → 리포트 재생성
```

수동으로 돌릴 일이 생기면(소급·재조사) 진입점은 하나다:

```
python scripts/run_postmarket.py --date 2026-08-06
python scripts/run_postmarket.py --skip-rest       # 망 장애 시 나머지라도
```

> **종료 코드 읽는 법**: 이 도구들의 `exit 1`은 **"볼 것을 찾았다"**이지 실패가 아니다
> (`daily_integrity_report.py` 머리말). `run_postmarket.py`의 요약은 ✅(완료) /
> ⚠(발견 있음) / ❌(실패) 셋으로 갈라 찍는다 — ⚠에 놀라지 말 것.

### Task Scheduler 등록 상태는 이제 코드다

```
powershell -ExecutionPolicy Bypass -File scripts\install_scheduled_tasks.ps1 -DryRun
powershell -ExecutionPolicy Bypass -File scripts\install_scheduled_tasks.ps1
```

되돌리기: `schtasks /Create /TN "Messiah" /XML logs\task_backup_20260806-200601\Messiah.xml /F`

> **`.ps1`은 UTF-8 BOM으로 저장해야 한다.** PS 5.1은 BOM 없는 파일을 CP949로 읽어 한글
> 주석이 깨지고 파서가 죽는다(2026-08-06 실측). `.bat`의 "ASCII-only" 규율과 같은 계열.

---

### 2026-08-07 장후에 볼 것 — 이 수정들의 첫 실전 검증

- [ ] **G-1** `series_gap_findings == 0`인가. 아니면 어느 계열이 언제 비었는지
      리포트의 `적재 계열 커버리지` 표에서 **머리 구멍**과 **최장 구멍**을 본다.
      재기동이 없던 날이면 0은 당연하다 — **조용함을 증거로 착각하지 말 것**
      (등록부 `archiver-restart-restore` 주석).
- [ ] **G-2** 커버리지 표에 계열이 **5개 다** 뜨는가(flow 1 + option 3 + ticks).
      빠진 계열이 있으면 그날 파일이 아예 없다는 뜻이고, 그게 곧 판정 대상이다.
- [ ] **G-3** 카덴스가 실제 폴링 주기와 맞는가(regular 10분 · weekly_mon 10분 ·
      weekly_thu 5분 · flow 1분 · ticks 1분). 어긋나면 카덴스 추정이 깨진 것이고,
      그러면 구멍 판정 전체를 못 믿는다.
- [ ] **G-4** `boot_recovery=부팅 트리거 무장 2개`가 호스트 줄에 찍히는가.
      안 찍히면 **작업이 지워졌거나 다시 만들어진 것**이다.
- [ ] **G-5** 08:35 정시 기동이 **기동 창 가드에 안 막혔는가**. 로그에 `[기동 창]`이
      찍혔으면 그날 수집이 통째로 없다 — 가장 먼저 볼 줄이다.
- [ ] **G-6** 15:45 `Messiah-Postmarket`이 실제로 돌았는가
      (`logs/postmarket_20260807.log` 존재 + 요약 4줄). 안 돌았으면 절차 자동화가
      **또** 조용히 안 돈 것이고, 그건 이 세션 전체의 전제가 깨진 것이다.
- [ ] **G-7** `unmeasured`가 비어 있는가(= G-6이 성공했다는 다른 증거).
- [ ] **G-8** 종료 시퀀스의 `Recomposed` 로그가 찍히는가. 찍혔는데
      `horizon_findings`가 안 비면 재합성이 못 고치는 손상이라는 뜻이다.

### 재부팅이 실제로 나면 볼 것 (기회 대기)

부팅 복구가 **동작한다**는 증명은 장중 재부팅이 한 번 나야 나온다. 그때:

- [ ] `longest_gap_minutes`가 몇 분인가. 설계상 부팅 30초 + 트리거 지연 1분 + 기동 30초
      ≈ **2~3분**이어야 한다. 21분(2026-08-06)과 비교한다.
- [ ] `series_gap_findings`가 0인가 — 아카이버 복원이 재기동에서 실제로 살아남았는지의
      **유일한 진짜 증거**다.
- [ ] `ComposerBucketsRestored` 로그가 찍히는가(겹⑤가 미완 버킷을 되채웠는가).

---

### 다음 세션 P2 — `no-degenerate-features` 오탐 제거

등록부에서 유일하게 `재발`로 남은 항목이고, **원인은 결함이 아니라 검출기 오탐**이다.

10건 중 4건이 `px_gap_open` = `log(당일 시가 / 전일 종가)` — **정의상 장중 상수**다.
`_FeatureStat.constant`(`lo == hi`)가 이걸 "죽었다"로 잡는다. `px_ema_cross_*`(값역
{-1,0,+1})·`px_breakout_*`(대부분 0.0)도 같은 성격이고, 10m은 표본 31개라 저기수 피처가
안 변하는 것이 정상 범위다.

처방: 피처 레지스트리(`features/spec.py` 또는 `px_core` 등록부)에 표지를 둔다.

```
expected_constant_intraday   px_gap_open        → 상수를 경고하지 않는다.
                                                  대신 "항상 NaN"만 잡는다(그게 진짜 사고고,
                                                  2026-08-05 14:12 재기동에서 실제로 났다)
low_cardinality              px_ema_cross_*      → 표본 60 미만이면 판정 보류(unmeasured)
                             px_breakout_*
```

정정 후에도 0이 아니면 그때가 진짜 신호다.

> `max: 0`을 요구하는 한 이 항목은 구조적으로 통과 불가다. 매일 ERROR가 찍히면
> 늑대소년이 되고, 그건 이 등록부가 가장 경계하는 실패다.

### 그 밖에 남은 것 (2026-08-06 보고서 P1, 미착수)

- [ ] **UI 공백 계측** — `ui_restarts`는 인프로세스 워치독의 자동 재기동만 센다.
      2026-08-06에 UI가 10:04~10:25 죽어 있었는데 지표는 0이었고, 등록부
      `ui-restart-observability`는 그 위에서 "검증 완료 3거래일"을 찍었다.
      `ui_downtime_minutes` 신설 + 등록부 지표 교체 + `consecutive_days` 리셋 필요.
- [ ] **리포트가 "왜 끊겼는지" 말하게** — `_collect_native_crashes()`가 이미 이벤트로그를
      연다. 거기에 호스트 생명주기 이벤트(1074/6005/6006/12/13/41)를 더하면
      `관측 공백 10:04~10:25(21분) — 호스트 재부팅(10:03:49)` 한 줄이 자동으로 나온다.
- [ ] **크래시 덤프 판독** — 2026-08-06의 덤프 3건은 **전부 오탐**(first-chance)이었다.
      l1_daily는 08:36에 덤프를 찍고 10:04까지 88분을 계속 로깅했다. 그런데 리포트는
      `네이티브 크래시: 0건`과 `크래시 덤프 3건`을 나란히 찍어 서로 모순된다.
      필요한 것: 덤프에 시각 붙이기 + "프로세스가 죽었나"와 대조해 치명/비치명 분류 +
      `Current thread` 부재를 "프레임 없음"이 아니라 "네이티브 스레드에서 폴트"로 표기.

---

## 2026-08-06 2차 — P1·P2 완료 ([MW0601], 2026-08-06)

상세는 `DECISION_LOG.md` "2026-08-06 2차" 항목. 여기엔 **다음에 볼 것**만.

- [x] **P1-1** 관측 공백 축 신설(`ops/observation_gaps.py`) — UI 포함. 등록부
      `ui-restart-observability`의 지표를 `observation_gap_minutes_max`로 교체하고 연속일 리셋
- [x] **P1-2** 호스트 생명주기 이벤트(1074/6006/13/12/6005/41) → 공백마다 원인 인용
- [x] **P1-3** 크래시 덤프에 시각·생존 판정·프레임 부재 사유 + 이벤트로그 반대방향 대조
- [x] **P2-1** 퇴화 검출기 오탐 제거(`px_core.INTRADAY_CONSTANT_OK`) + 등록부 재등록
- [x] **P2-2** self_check git 실패 사유 분리(git stderr 그대로 인용)

### 2026-08-07 장후에 볼 것 — H-1~H-6

앞 세션의 G-1~G-8과 함께 본다.

- [ ] **H-1** `관측 공백: 없음 ✅`이 찍히는가. 공백이 있으면 **원인 문장이 붙었는가** —
      "원인 불명"이면 이벤트로그를 못 읽었거나 시각이 안 맞은 것이다.
- [ ] **H-2** `호스트 생명주기` 목록이 찍히는가. 안 찍히면 `Get-WinEvent`가 실패한 것이고
      그날 공백 원인은 추정치(`exact=False`)가 된다.
- [ ] **H-3** 퇴화 피처가 **0건**인가. 아니면 남은 것이 진짜다 — `px_gap_open`·
      `px_ema_cross_*`·`px_breakout_*`은 이제 상수로 안 잡히므로, 잡힌 것은 연속값 고착이다.
      **그때는 면제 목록을 넓히지 말고 그 피처를 조사할 것.**
- [ ] **H-4** `ui_restarts`(옛 지표)와 `observation_gaps`의 UI 항목이 **어긋나는가**.
      어긋나는 것이 정상이다 — 어긋남이 곧 옛 지표가 못 보던 것의 크기다.
- [ ] **H-5** 크래시 덤프가 났다면 판정이 `first-chance`인가. `생존 미확인 + 재기동`이고
      `native_crashes`가 0이면 **밖에서 종료된 것**이므로 관측 공백 원인과 대조한다.
- [ ] **H-6** 기동 로그의 `[OK ] git` 줄이 `clean`인가. 다른 문구면 이제 **git이 한 말이
      그대로** 나오므로 그 문장으로 조사를 시작할 수 있다.

### 관측 공백 축의 알려진 한계 (다음 사고 때 기억할 것)

- **마지막 기동 이후 조용히 사라진 경우는 안 센다.** 정상 종료(15:35)와 구분할 근거가
  이 모듈에 없기 때문이다 — 그 구분은 봉 연속성(`analyze_bar_continuity`)이 한다.
  즉 "14:00에 죽고 안 돌아온 날"은 이 축이 아니라 봉 결손으로 잡힌다.
- **조용한 프로세스의 공백은 호스트 이벤트가 없으면 과대평가된다**(`exact=False`).
  `g2_paper`가 번들 결선되어 장중에 로그를 내기 시작하면 이 한계는 자연히 줄어든다.

### 다음 우선순위 후보 (미착수)

- [ ] **G2 손익 측정으로 가는 사슬** — 8거래일째 `live 번들 결선: []`이다. P0-3b로
      재합성이 자동화됐으므로 `session_guard`의 아카이브 정합 거부가 풀렸다:
      `run_model_sweep --feature-set v2026.08-ev` → `instance.yaml` 승격 → shadow 결선.
      등록부 `ev-features-measured`가 08-12부터 채점을 시작한다.
- [ ] **`minute_bar_close: timer` 승격** — `delivery_latency` p99 표본이 2일치 모였다
      (08-05 1.024초 · 08-06 1.032초). 3~5거래일 모은 뒤 p99 최댓값 위로
      `MINUTE_CLOSE_GRACE_SECONDS`를 확정한다.
- [ ] **KIS REST 500 다발** — 8/6에 `OptionChainPollRetried` 43건(전건 복구),
      `InvestorFlowPollError` 4건. 재시도 계층이 설계대로 작동 중이라 조치는 불필요하나,
      며칠 모아 "정상인 날의 실패율"을 알아 두면 다음에 늘었을 때 판단이 선다.

---

## 2026-08-07 장중 점검 — 규정이 정상이라 말한 부재 ([MW0601], 2026-08-07)

상세는 `DECISION_LOG.md` 2026-08-07 항목. 여기엔 **운영 절차와 다음에 볼 것**만.

그날 목위클리 옵션체인이 하루 종일 비었고, 나는 그것을 **하루치 영구 소실로 오판했다**.
실제로는 KRX 규정상 미상장(8월 둘째 목요일 8/13 = 월물 최종거래일 → 그 만기 목위클리는
상장 안 됨)이었고, **그 규칙은 이미 `core/event_calendar.py`에 있었다**(7월부터, 테스트까지).
수집 경로가 그 정본을 안 물어봤을 뿐이다.

- [x] **P0-1** `EventCalendar.thursday_weekly_listed()` — 정본에 질문 하나 추가
      (`has_thursday_weekly()`와 **다른 질문**이다: 그쪽은 "이 주에 만기가 있나",
      이쪽은 "오늘 폴링하면 받을 체인이 있나")
- [x] **P0-2** 폴러 캘린더 게이트 + 태그 3분화 + **양방향 단언**
      (미상장 판정일에도 조회는 계속하고, 받으면 `OptionChainCalendarViolation`)
- [x] **P0-3** `series_coverage`에 `expected` 축 — 15:45 리포트 오탐 차단(5거래일치)
- [x] **P0-4** 기동 창 거절을 재기동·관측 공백으로 세던 오탐 제거(`LaunchWindowRefused`)
- [x] **P1-1** 유량 예산이 선언이 아니라 오늘 실수요를 센다
- [x] **P2** 아카이브 `expiry_date` 컬럼
- [x] **고도화 1** `ops/series_expectation.py` — `universe:` 선언을 캘린더 조건부 계약으로
- [x] **고도화 2** `ops/canonical_consumers.py` — 정본을 안 쓰는 소비자 검사
- [x] **고도화 3** 양방향 단언(P0-2·P0-3에 흡수)
- [x] **고도화 4** 소급 불가 등급을 캘린더 게이트 **뒤**로
- [x] **고도화 6** Kill Switch `sys.kill` 발행 경로 결선(UI 발행 + 파이프라인 수신)

---

### 목위클리 미상장 달력 — 외워 두면 다음에 안 헷갈린다

```
만기 다음날(금)에 다음 주 목요일물 하나만 신규 상장된다.
그 물의 만기가 그 달 **둘째 목요일**이면 → 상장되지 않는다(월물이 그 역할을 대신).
따라서 매월 한 번, 월물 만기 주를 포함해 5거래일간 목위클리가 존재하지 않는다.
```

2026년 8월: 8/7(금)~8/13(목) 부재 → **8/14(금) 8/20 만기물로 재개**.

`python -c "from messiah.core.event_calendar import EventCalendar; ..."`로 아무 날짜나
물어볼 수 있다 — 추측하지 말 것.

### 2026-08-14에 볼 것 — 이 수정들의 **진짜** 검증

- [ ] **I-1** weekly_thu가 **실제로 재개되는가**. 안 들어오면 규정이 아니라 판정식이
      틀린 것이고, 그때 `OptionChainSeriesMissing`(ERROR)이 3사이클째에 뜬다.
      **이게 등록부 `thursday-weekly-listing-calendar`의 진짜 채점일이다.**
- [ ] **I-2** 그날 아카이브의 `expiry_date`가 `2026-08-20`인가(= 라벨 파서가 맞는가).
- [ ] **I-3** 기동 로그에 `옵션체인 결선 — weekly_thu ...`가 **다시** 뜨는가
      (8/7~13엔 `옵션체인 — weekly_thu 미상장(...) · 단언 폴링만`).

### 2026-08-07~13 매일 볼 것 (미상장 구간)

- [ ] **J-1** 리포트에 `⊘ 오늘 안 모으는 계열: option_chain/weekly_thu`가 뜨고
      `series_findings`에는 **안 들어가는가**. 들어가면 P0-3이 안 먹은 것이다.
- [ ] **J-2** `OptionChainCalendarViolation`이 뜨는가. 뜨면 **규정 이해가 틀린 것**이고,
      그때는 면제 목록을 넓히지 말고 **KRX 공지를 다시 확인할 것**.
- [ ] **J-3** 유량 예산이 `0.220건/초(2계열)`로 찍히는가.

---

### 여전히 남은 것 — 우선순위 순

- [ ] **고도화 5 · G2 손익 측정으로 가는 사슬** — **9거래일째** `live 번들 결선: []`.
      `data/models/registry.db`를 직접 열어보니 `bundles` 테이블이 **0행**이다(파일
      mtime 7/29) — 승격 문제가 아니라 **등록된 번들이 하나도 없다**. 순서:
      ```
      1) run_postmarket.py 완료 확인   ← 재합성 전에 학습하면 손상된 상위 봉으로 학습한다
      2) python scripts/run_model_sweep.py --feature-set v2026.08-ev
      3) Registry에 shadow 등록 → 며칠 관측 → live 승격 + instance.yaml model_bundle
      ```
      등록부 `ev-features-measured`가 **08-12부터 채점**을 시작한다 — 실질 마감이 그날이다.
      이번 세션에서 손대지 않은 이유: 재합성(15:45) 전이라 2번을 돌리면 안 되는 시각이었다.
- [ ] **`minute_bar_close: timer` 승격** — 오늘로 표본 3거래일치. 8/7 장중 실측: 1분
      FeaturePublish 215건 중 **결손 0**, 다만 다음 분으로 밀린 발행 15건이고 **11건이
      11시 이후**(점심 구간 틱이 얇아 다음 분 첫 틱이 늦게 온다). p99 3일치
      (08-05 1.024초 · 08-06 1.032초 · 08-07 미산출)를 보고
      `MINUTE_CLOSE_GRACE_SECONDS`를 확정한다.
- [ ] **Kill Switch 실동작 검증** — 결선은 끝났지만 **한 번도 눌러본 적이 없다.**
      포지션이 생기는 날(= 고도화 5 이후) 장 마감 직전에 1계약으로 실제 발동해 볼 것.
      지금 누르면 청산할 포지션이 없어 "게이트 정지"만 확인된다 — 그것도 안 해본 것보다는
      낫지만 절반이다.
- [ ] **EventCalendar D-day UI 결선** — 화면 ④에 `이벤트 캘린더 D-day: 알려진 갭` 그대로.
      이제 캘린더가 목위클리 상장 여부까지 답하므로 붙일 재료가 늘었다.
- [ ] **`broker.positions()` UI 실시간 연동** — 화면 ③이 여전히 브로커 계좌를 직접 조회
      하지 않는다. Kill Switch가 살아난 지금, "무엇을 청산하는지 화면에서 못 보는" 상태다.

---

## 2026-08-07 3차 — 사고 처방 구현 완료 ([MW0601], 2026-08-07)

상세는 `DECISION_LOG.md` "2026-08-07 3차". 여기엔 **다음에 볼 것**만.

- [x] **P0-1** 버스 계약 — kill은 `on_kill`을 준 구독자에게만 · 루프 예외 격리 · 핸들러 가드
- [x] **P0-2** `BarContinuity.tail_gap_minutes` — 봉의 **잘림**을 본다
- [x] **P0-3** `SessionEnd` 마커 + 비정상 종료 판정(`abnormal_exits`)
- [x] **P0-4** `compare_day()`가 **공식에만 있는 분 수**를 함께 돌려준다
- [x] **P1-1** `scripts/run_compact.py` + 장후 절차 **1/5**로 편입
- [x] **P1-2** `tests/conftest.py` — `MessageBus.connect()`가 운영 포트면 거부
- [x] **고도화 1** `SeriesCoverage.coverage_pct` — 모든 계열에 같은 질문
- [x] **고도화 2** `status_board` `DEAD` 상태 + `CircuitBreakerStatus.collector_healthy`
- [x] **고도화 3** `scripts/run_chaos_check.py` — 비상 경로를 실제로 흘려본다
- [x] **고도화 5** Kill Switch 실동작 — 카오스 ②가 보유 1계약 → 게이트 정지 + 전량 청산 확인

### 임계 네 곳이 같은 크기다 — 하나를 바꾸면 넷을 바꾼다

```
series_coverage._HEAD_GAP_FLOOR_MINUTES / _TAIL_GAP_FLOOR_MINUTES   20분
integrity_report DEFAULT_THRESHOLDS["bar_tail_gap_minutes"]          20분
verify_archive_volume.MISSING_MINUTES_LIMIT                          20분
series_coverage._COVERAGE_FLOOR_PCT                                  95% (=창 420분의 21분)
```

같은 질문("얼마나 비어야 사고인가")에 답하므로 임계도 같다. 어긋나면 **어느 축은 울고
어느 축은 조용한 날**이 생기고, 그게 2026-08-07의 형태였다.

### 2026-08-10(월)에 볼 것 — K-1~K-6

- [ ] **K-1** `SessionEnd`가 15:35 종료 로그에 찍히는가. 안 찍히면 P0-3이 안 먹는다.
- [ ] **K-2** 리포트에 `봉 1m: ... 마감까지 N분 미수집`이 **안** 뜨는가(정상일이면 0).
- [ ] **K-3** 계열 커버리지가 전부 95% 이상인가 — 등록부 `truncation-is-visible`의 첫 채점.
- [ ] **K-4** 거래량 대조가 `공식 N분`을 함께 찍는가, 미수집 0분인가.
- [ ] **K-5** 장후 절차가 **1/5 장중 조각 통합**부터 5단계로 도는가.
- [ ] **K-6** `SubscriberHandlerFailed`가 0건인가. 1건이라도 있으면 **조용히 버려진
      메시지가 있다는 뜻**이다 — 그 태그가 없던 시절엔 그런 메시지가 루프를 죽였다.

### 배포 전·주간 점검 절차 (신설)

```
python scripts/run_chaos_check.py      # 비상 경로 3종 — 전부 ✅여야 배포한다
```

**이 점검이 첫 실행에서 실제 결함을 잡았다**(이중 청산 — 비상 청산이 반대 포지션을
만들었다). 단위 테스트로는 안 나온다: 버스를 통한 되먹임이 있어야 재현된다.

---

### 고도화 4 (G2 번들 결선) — **오늘 못 끝냈다. 왜인지가 중요하다**

`NEXT_TODO`에 적혀 있던 `run_model_sweep --feature-set v2026.08-ev`는 **실행 불가였다**:

1. `--feature-set` 플래그가 없었다(상수 하드코딩) → 이번에 추가
2. 사이드카를 안 만들었다 → `features/sidecar.build()` 호출 추가

둘을 붙이고 실제로 돌린 결과가 **오늘의 실질 성과**다:

```
                     거래신호      |S| p90    게이트④ 차단
v2026.07  (08-04)     5/842        0.094         836
v2026.08-ev (08-07)  37/842        0.147         804      ← 7.4배
```

**EV 카테고리가 신호를 7.4배로 늘렸다.** 다만 `|S|` 90분위 0.147은 여전히 게이트
`|S| ≥ 0.20` 아래다 — 표본의 90%가 우위 부족으로 막힌다.

**남은 것 두 가지, 그리고 그것이 왜 스크립트 실행이 아닌가**:

- `run_model_sweep.py`에는 **Registry 쓰기 경로가 없다**(진단 전용). "스윕 → 승격"이라는
  절차 이해 자체가 틀렸다. 번들을 만들려면 `run_g1_walk_forward.py`로 성과를 판정하고,
  그 뒤에 등록 경로를 **새로 만들어야** 한다.
- 게이트 ④(`SCORE_THRESHOLD = 0.20`, Ver 2.0 §3.1)와 모델 점수 분포가 안 맞는다.
  둘 중 하나다 — 모델에 우위가 부족하거나, 게이트가 더 강한 모델을 전제로 잡혀 있거나.
  **그 판단을 임계 조정으로 먼저 하면 안 된다**(수익이 아니라 거래 건수를 최적화하게 된다).

- [ ] **다음 단계**: `python scripts/run_g1_walk_forward.py`를 `v2026.08-ev` ·
      15m/shallow/oof/regime=on으로 돌린다. **성과 판정은 거기서만 나온다**(단일 분할
      수익률은 표본이 하나라 성적으로 읽으면 안 된다 — 스윕 자신의 docstring).
      등록부 `ev-features-measured`가 08-12부터 채점하므로 그 전에 답이 있어야 한다.


---

## 2026-08-10 커밋 ① — 잃은 것이 보이지 않던 네 자리 ([MW0601], 2026-08-10)

상세는 `DECISION_LOG.md` 2026-08-10 2차 항목. 여기엔 **운영 절차와 다음에 볼 것**만.

- [x] **A-1** 판정 창을 등록 정본에 앵커링 + `collection_start_lag_minutes` 축 신설
      (계열별 기준선 `series_expectation.FIRST_DATA_KST` 포함 · 장중 실행은 창 끝을 지금으로 자름)
- [x] **A-2** `ops/task_exit_codes.py` — 진입점 종료 코드 실측 + `SessionEnd` 대조 · `.bat` 2개가 로그에 기록
- [x] **A-3** 사이클 다리 완전성(`expected_legs`/`short_cycles`) — 시간 축이 못 보는 결손
- [x] **A-4** `data/poll_retry.py` — 재시도 정본 하나로, 수급 폴러 결선
- [x] 넓은 그물 셋 → 전용 지표(`option_calendar_violations` · `canonical_consumer_gaps` · `abnormal_exits`)
- [x] 등록부: `truncation-is-visible` 연속일 리셋 · `morning-launch-actually-happens` 지표 교체 ·
      `leg-completeness-measured` · `exit-code-matches-log` 신설

### 2026-08-11(화)에 볼 것 — L-1~L-7

**이 커밋의 진짜 채점일이다.** 오늘은 사고일 데이터로 "잡히는가"만 확인했고, 정상일에
**헛경고가 없는가**는 내일만 답할 수 있다.

- [ ] **L-1** `수집 기동 지연(정시 트리거 대비)`이 **+1분 이내**인가. 08:20 트리거에 08:20:2x
      기동이면 +0.4분쯤이다. 5분을 넘으면 아침이 또 늦은 것이고, 그때는 화면이 아니라
      이 줄이 먼저 말한다.
- [ ] **L-2** 계열 커버리지가 전부 95% 이상인가 — **창이 08:20으로 옮겨진 뒤 첫 정상일**이다.
      옵션체인 머리 구멍이 20분을 넘으면 첫 격자가 늦은 것이므로 `FIRST_DATA_KST`에
      옵션 계열 기준선을 추가할 근거가 된다(지금은 틱만 있다).
- [ ] **L-3** `결손 사이클`이 **0건**인가. A-4(수급 재시도)가 실제로 들었는지의 첫 답이다 —
      08-10엔 수급 3건 + 옵션 1건이었다. 수급 쪽이 계속 나면 재시도가 안 먹은 것이다.
- [ ] **L-4** `작업 종료 코드`가 전부 0인가. **G2가 또 255면 `f901de0` 회귀가 확정**이고,
      그때는 종료 경로(버스 계약 변경)를 파야 한다. 0이면 08-10 1회성이었다는 뜻이다.
- [ ] **L-5** 15:36경 `run_l1_daily`가 **한 번 더 뜨는가**. 뜨면 L-4와 같은 원인이다
      (0이 아닌 종료 → `RestartOnFailure`). 안 뜨면 08-10 한정 현상이다.
- [ ] **L-6** `InvestorFlowPollRetried`가 로그에 **찍히는가**. 500이 하루 3~4건 나던 계열이라
      정상적으로 몇 건 뜨는 것이 맞다. 한 건도 없고 `InvestorFlowPollError`만 있으면
      재시도가 배선되다 만 것이다.
- [ ] **L-7** 등록부에서 `thursday-weekly-listing-calendar`·`canonical-consumers-wired`·
      `no-silent-process-death`가 **재발이 아닌가**. 전용 지표 교체가 들었는지의 채점이다.

### 새 축을 붙일 때의 규율 (이번에 실측으로 배운 것)

```
새 축이 breach/finding을 올리기 시작하면, 넓은 그물로 채점하는 기존 등록부 항목이
그 축 때문에 뒤집힌다. 축을 붙인 뒤 반드시 그날 데이터로 재산출해 재발 목록을 확인할 것.
```

이번에 셋이 뒤집혔고 전부 오탐이었다. `configs/pending_verifications.yaml`에 남은 넓은
그물은 둘이다 — `daily-axes-measured`(`unmeasured_count`)와
`archiver-restart-restore`(`series_gap_findings`).

### 남은 것 — 커밋 ②~④ (계획은 2026-08-10 통합 구현계획)

- [ ] **B-1** 거래량 대조의 미수집 분을 머리/중간/꼬리로 분해 — 머리 임계는 0분.
      08-10에 13분이 임계 20분 아래라 `ok: true`였고, 그게 그날 유일하게 잘림을 본 축이었다.
- [ ] **B-3** 등록부에 `since:` 필드 — 재발 2건이 08-07 위반을 매일 다시 보고 중이다.
      새로 생긴 재발과 3거래일 전 것이 화면에서 구분되지 않는다.
- [ ] **B-4 / G-1** EV 피처 결선 + G2 번들 사슬 — `ev-features-measured` 마감이 **08-12**다.
      변동성 채점이 3개 Horizon 전부에서 `ev_tod_cos 미측정 — 피처셋에 없음`을 찍고 있다.
- [ ] **C-1** 옵션 시세를 실전 도메인으로. `rest_client.get_investor_flow()`가 이미
      `REAL_REST_DOMAIN`을 고정 사용하며 근거가 docstring에 실측으로 적혀 있는데
      (*"모의투자 앱키로도 200 OK"*), 옵션 시세만 그 처방을 못 받았다.
      08-10 실패율 실측: 모의 도메인 **1.05%**(53/5,050) vs 실전 도메인 **0.25%**(3/1,188).
- [ ] **C-2** `Docs/동작흐름과상태/` 미추적 — 진입 흐름 정본 문서가 git 밖에 있다.
- [ ] **G-4** `minute_bar_close: timer` 승격 — 표본 4거래일 확보(08-10 p99 **1.0353초**,
      최대 1.396초). `MINUTE_CLOSE_GRACE_SECONDS = 1.5초` 확정 근거는 충분하다.
      발행 타이밍을 바꾸는 변경이라 다른 변경과 **섞지 말 것**.


---

## 2026-08-10 커밋 ② — 임계와 기준일, 그리고 도메인 ([MW0601], 2026-08-10)

상세는 `DECISION_LOG.md` 2026-08-10 3차 항목.

- [x] **B-1** 미수집 분을 머리/중간/꼬리로 분해 · 머리 임계 **0분**(`DayComparison`)
- [x] **B-3** 등록부 `since:` 필드 + 재발 문구에 **거래일 거리** 표기
- [x] **B-3b** `archiver-restart-restore` 지표 교체(`series_head_gap_minutes_max`) +
      `premise: collection_start_lag_minutes ≤ 5` — 머리 구멍의 두 원인을 가른다
- [x] **C-1** 옵션 시세를 실전 도메인으로(`QUOTE_ON_REAL_DOMAIN`) — 전환 전 두 도메인 응답 대조 실측
- [x] **C-2** dev_memory 갱신 경고 훅 + `Docs/동작흐름과상태/` 추적

### 2026-08-11(화)에 볼 것 — M-1~M-4 (커밋 ①의 L-1~L-7과 함께)

- [ ] **M-1** `공식 분봉 대비 거래량`이 `미수집 0분`인가. 머리가 0이 아니면 그날도 늦게
      뜬 것이고, `수집 기동 지연`과 **같은 크기**여야 한다 — 두 축이 어긋나면 그 자체가 볼 것이다.
- [ ] **M-2** 등록부에 **재발 0건**인가. `archiver-restart-restore`가 `전제 붕괴`에서
      `검증 대기`로 돌아오는지가 정상 기동일의 첫 확인이다.
- [ ] **M-3** `OptionChainPollRetried`가 **몇 건**인가. C-1 전환 전 기준선은
      08-10 **53건**(실패율 1.05%)이다. 0.25%대(약 12건)로 내려가는 것이 예측이고,
      **늘면 즉시 `QUOTE_ON_REAL_DOMAIN = False`로 되돌린다.**
- [ ] **M-4** 옵션체인 아카이브가 정상 다리 수(42)를 유지하는가 — 도메인 전환이 응답 모양을
      바꾸지 않았다는 확인. 전환 전 실측으로 42개 필드가 동일했지만, **장중 응답으로는
      아직 확인 안 됐다**(전환 실측은 장 종료 후에 했다).

### 남은 것 — 커밋 ③~④

- [ ] **B-4 / G-1** EV 피처 결선 + G2 번들 사슬 — `ev-features-measured` 마감 **08-12**.
- [ ] **B-2** 소급 불가 손실의 장중 가시화(`status_snapshot` 배지)
- [ ] **G-2** 잘림 3축 교차검증 · **G-3** 스케줄러 이벤트를 관측 소스로 · **G-6** 손실 예산
- [ ] **G-4** `minute_bar_close: timer` 승격(p99 4거래일치 확보, 발행 타이밍이라 단독 커밋)
- [ ] **C-1 후속** 분봉 차트도 실전 도메인으로 옮길지 — 옵션 시세 3거래일 관측 뒤 판단.
      백필은 복구 경로라 근거 없이 건드리지 않는다.


---

## 2026-08-10 커밋 ③ — 화면과 예산 ([MW0601], 2026-08-10)

상세는 `DECISION_LOG.md` 2026-08-10 4차 항목.

- [x] **B-2** `ops/loss_ledger.py` — 오늘 영구 소실의 인프로세스 장부.
      `status_snapshot.json` + 상태판 CLI + Command Center 상단 배지
- [x] **G-2** 아침 잘림 3축 교차검증 — 값이 아니라 **각 축의 예/아니오**를 비교한다
- [x] **G-3** 스케줄러 기동 이력(107 정시 / 110 사람) — 종료 코드와 같은 질의에 얹었다
- [x] **G-6** `ops/loss_budget.py` — 5거래일 이동합. 리포트에 하루치 `irrecoverable_loss_minutes`

### 2026-08-11(화)에 볼 것 — N-1~N-4 (L-*, M-*과 함께)

- [ ] **N-1** 화면 상단에 `✅ 오늘 소급 불가 손실 없음`이 뜨는가. **08:58이 아니라 08:20에**
      떠야 한다 — 정시 기동이면 장부의 첫 값이 `기동 지연 0.4분`이다.
      뜨는데 `❌`면 그 줄이 오늘의 첫 신호다(장후까지 기다릴 이유가 없다).
- [ ] **N-2** 장중 장부와 장후 커버리지가 **같은 것을 말하는가**. 장부는 못 받은 것을,
      커버리지는 안 쌓인 것을 센다 — 어긋나면 발행과 적재 사이가 새는 것이고 그게 볼 것이다.
      (예: 장부 `option_chain/regular 1건`인데 커버리지 `결손 사이클 0건`)
- [ ] **N-3** `기동 이력`에 `사람이 실행`이 **0회**인가. 정상일이면 정시 트리거만으로 돈다.
- [ ] **N-4** `소급 불가 손실 예산`이 며칠에 걸쳐 어떻게 움직이는가. 08-11부터 축이 쌓이므로
      5거래일이 다 차는 것은 **08-15**다. 그전까지는 `이 축이 없는 날 N일`이 정상이다.

### 축 세 개가 서로를 받친다 — 다음에 헷갈리지 않게

```
기동 지연        왜 못 봤나        (원인)   장중: loss_ledger  장후: collection_start_lag
계열 커버리지    얼마나 못 봤나    (크기)   장후만 — 아카이브를 읽는다
손실 예산        얼마나 자주 잃나  (빈도)   장후, 5거래일 이동합
```

셋이 어긋나면 G-2 교차검증이 그 사실을 말한다.

### 남은 것 — 커밋 ④

- [ ] **B-4 / G-1** EV 피처 결선 + G2 번들 사슬 — `ev-features-measured` 마감 **08-12**.
      `run_model_sweep --feature-set v2026.08-ev` → shadow 등록 → live 승격.
      이게 풀리기 전까지 화면 ①③④와 Self-Eval은 전부 자리표시자다.
- [ ] **G-4** `minute_bar_close: timer` 승격 — p99 4거래일치 확보(08-10 1.0353초, 최대 1.396초).
      발행 타이밍을 바꾸는 변경이라 **단독 커밋**으로.
- [ ] **G-5** 아침 복구 자동화(`recover_now.bat` + 09:00까지 첫 틱 없으면 알림) —
      08-10 08:50 시도가 18초 만에 끊기고 로그에 흔적이 없던 그 자리.
      이제 G-3이 그 시도를 **관측은** 하지만, 여전히 사람 손이다.
- [ ] **C-1 후속** 분봉 차트도 실전 도메인으로 옮길지 — 옵션 시세 3거래일 관측 뒤.


---

## 2026-08-10 커밋 ④-a — EV 결선 ([MW0601], 2026-08-10)

상세는 `DECISION_LOG.md` 2026-08-10 5차 항목.

- [x] `sidecar.build` 미사용 소비자 **여섯 중 셋** 결선(harness·run_l1_daily·run_replay)
      + 채점자 자신이던 `run_vol_scorecard` + 스모크 둘
- [x] `ops/canonical_consumers.py`에 `sidecar.build` 정본 등록 — 일곱 번째가 빠뜨리면 리포트가 운다
- [x] `configs/instance.yaml` `feature_set` → `v2026.08-ev` (121개 → 137개)
- [x] `tests/features/test_production_feature_set.py` — 운영 설정으로 엔진이 서는지 고정

### 2026-08-11(화)에 볼 것 — O-1~O-3

- [ ] **O-1** 수집기가 **뜨는가**. 이 커밋은 운영 `feature_set`을 바꿨고, 사이드카 배선이
      틀렸으면 **아침에 프로세스가 안 뜨는 것**으로 드러난다. 기동 로그에 EV가 포함된
      피처 수(137)가 찍히는지 확인. 안 뜨면 `feature_set`을 `v2026.07`로 되돌리면 된다
      (사이드카 주입은 그때도 무해하다).
- [ ] **O-2** `피처 NaN 비율`이 종전과 비슷한가. 실측은 중앙 0.007이었지만 **장중 실시간
      경로에서는 처음 도는 것**이다(위 실측은 아카이브 재생). 20%를 넘으면 그 Horizon 신호가
      정지한다.
- [ ] **O-3** `nan_ratio`가 아니라 **퇴화(항상 NaN·상수)** 축을 볼 것 —
      `degenerate_feature_count`. EV 16종 중 요일 더미 5개는 하루 안에서 상수라
      정상적으로 상수로 보인다(퇴화 판정이 하루 단위면 오탐 가능). 뜨면 판정 창을 봐야 한다.

### 정정 — 두 가지

```
1. ev-features-measured의 2026-08-12는 **마감이 아니라 채점 시작일**이다(registered=08-12).
   실제 기한은 08-21. 08-13·14·15가 깨끗하면 08-15경 검증 완료.
2. "번들이 붙으면 손익이 측정된다"는 틀렸다. Meta Decision 규칙 ②가 Regime=UNKNOWN이면
   무조건 NO_TRADE이고, RegimeAI는 실데이터로 학습된 적이 없다. 사슬은 두 마디다.
```

### 남은 것 — G2 사슬(④-b·④-c), 범위 확정 필요

- [ ] **④-b 번들 생산 경로가 없다.** `pack_bundle`·`promote_to_live`를 부르는 코드는
      `scripts/run_phase5_smoke.py`(토이 번들) 하나뿐. 실데이터 학습→검증→shadow 등록
      스크립트를 새로 만들어야 한다.
      **선결 결정**: shadow 등록의 통과 관문을 무엇으로 볼지. 제안은 "성과 관문은 G1
      walk-forward로 미루고 홀드아웃 교정·피처의존·지연 셋만" — 167거래일이면 홀드아웃 가능.
- [ ] **④-c RegimeAI 실데이터 학습.** 30분봉 167거래일로 HMM 학습 → `RegimeState` 발행 결선.
      이게 없으면 ④-b를 끝내도 판단은 0건이다.
- [ ] **G-4** `minute_bar_close: timer` 승격(단독 커밋) · **G-5** 아침 복구 자동화 ·
      **C-1 후속** 분봉 차트 도메인


---

## 2026-08-11 장전 점검 — Fix 6종 ([MW0601], 2026-08-11)

상세는 `DECISION_LOG.md` 2026-08-11 항목.

- [x] **F-1** 기동 로그에 피처셋 정본 한 줄 — `FeatureSpec.describe()` + L1·G2 양쪽 결선
- [x] **F-2** CB 배지 3분기(미주입 / `warmup` / phase) — 콜드스타트 heartbeat 신설
- [x] **F-3** Market View 문구를 세션 시각으로 가름 — `SessionHours.first_tick_time` 단일 소스
- [x] **F-5** 화면 ④ EventCalendar D-day 결선 — 먼슬리·위클리·목위클리 미상장 사유·재개일
- [x] **F-6** UI 포트 점유자 신원 확인(마커) + 미확인 시 대체 포트 · `LaunchedUI`로 포트 전파
- [x] **F-4** `scripts/verify_kill_switch.py` — 화면 버튼→Redis→청산→재가동 전 구간

검증 — 1,835개 통과 · ruff 통과. 실운영 확인(재기동한 G2): `피처셋 v2026.08-ev — 137개
(PX 82 · VL 39 · EV 16) · 사이드카 ['calendar']` 출력 · CB `warmup` → `normal` 전이.

### 2026-08-12(수)에 볼 것 — P-1~P-5

- [ ] **P-1** 08:20 기동 로그에 `피처셋 ... 137개` 줄이 **L1·G2 양쪽 다** 찍히는가.
      두 줄이 다르면 그 자체가 볼 것이다 — G2는 피처를 만들지 않고 L1이 발행한 것을 쓰므로
      모양이 갈리면 번들이 붙어도 입력 벡터가 어긋난 채 판단이 나간다.
- [ ] **P-2** 08:2x~08:45 구간 화면이 `서킷브레이커 ● 웜업 — 첫 봉 대기(판정 전)`인가.
      `미사용/데이터 없음`이면 F-2가 안 먹은 것이다. 첫 봉 뒤 `정상`으로 바뀌는지까지 볼 것.
- [ ] **P-3** 08:20~08:45에 Market View가 **회색 캡션** `ℹ 장 개시 전(08:45 첫 틱 예정)`인가.
      노란/빨간 박스면 갈래 판정이 틀린 것이고, 09:00 이후에도 그 문구면 더 나쁘다.
- [ ] **P-4** F-6 마커가 정상 순환하는가 — L1(08:20)이 `command_center_ui.json`을 새로 쓰고
      G2(08:25)가 `CommandCenterUIPortConfirmed`(INFO)를 찍는가.
      **`CommandCenterUIPortForeign`(ERROR)가 뜨면 이행 구간이 안 끝난 것**이고 UI가 8512에
      하나 더 뜬다(08-11 09:40 실측). 그때는 8511 점유자가 실제로 누구인지부터 확인할 것.
- [ ] **P-5** 화면 ④에 `먼슬리 만기: 2026-08-13 — 오늘` + `목위클리 ... 2026-08-14 재개 예정`이
      뜨는가. **08-13은 만기 당일**이라 D-day가 `오늘`로 바뀌는 첫 채점이다.

### 이 점검이 만든 새 과제

- [ ] **수동 kill 뒤 재가동 수단이 없다** — `sys.kill`로 닫힌 게이트는 `KillSwitch._triggered`
      때문에 **프로세스 재기동으로만** 풀린다(08-11 09:27 실측). 화면에 Kill Switch 버튼은
      있는데 그 반대편이 없다. `sys.resume` 토픽 + 2단 확인 버튼이 자연스러운 짝이다.
      **선결 결정**: 재가동을 화면에서 허용할지(사람 확인 4단계 절차와의 관계, `risk/
      kill_switch.py` 모듈 docstring).
- [ ] **버스에 발행하는 도구는 서버 단위로 격리** — pub/sub가 DB로 안 갈린다는 사실이
      `verify_kill_switch.py` docstring에만 있다. 같은 함정을 밟을 다음 도구를 위해
      `core/bus.py` 모듈 docstring에도 한 줄 남길 것.
- [ ] **F-6 한계** — 마커는 "우리가 이 포트에 띄운 적이 있다"만 증명한다. 우리 UI가 죽고
      그 자리를 남이 차지한 경우는 못 잡는다(워치독이 별도로 다루지만 그쪽도 포트 응답만 본다).


---

## 2026-08-11 고도화 — G2 사슬 두 마디 ([MW0601], 2026-08-11)

상세는 `DECISION_LOG.md` 2026-08-11 고도화 항목.

- [x] **④-c 상** `scripts/train_regime_ai.py` — 30m 실데이터 HMM 학습 + 홀드아웃 국면 분포.
      판정은 UNKNOWN 비율 하나(`MAX_UNKNOWN_RATIO=0.5`, **미검증 초기값**)
- [x] **④-c 하** `run_g2_paper_trading.py`에 `RegimeRuntime` 결선 — 저장본이 없으면
      "오늘 판단은 번들과 무관하게 0건"이라고 기동 로그가 말한다
- [x] **④-b** `scripts/build_bundles.py` — 학습→홀드아웃 관문 4종→pack→register→
      shadow(기본)/부트스트랩 live. 성과 관문 3종은 `passed=False·미측정`으로 **명시 기록**
- [x] `build_bundles.py`를 `sidecar.build` 정본 소비자로 등록

검증 — 신규 13건 포함 전 테스트 통과 · ruff 통과. **실제 학습은 안 돌렸다**(R11, 아래).

### 오늘 15:35 이후에 할 것 — Q-1~Q-5 (순서가 중요하다)

```
1) run_postmarket.py 완료 확인   ← 재합성 전에 학습하면 손상된 상위 봉으로 학습한다
2) python scripts/train_regime_ai.py
3) python scripts/build_bundles.py --horizons 30m --promote live --operator MW0601
4) G2 재기동(또는 내일 08:25 정시)  ← 저장본은 기동 시점에만 읽는다
```

- [ ] **Q-1** `train_regime_ai.py`의 홀드아웃 UNKNOWN 비율이 **50% 이하인가**.
      넘으면 결선해도 판단이 안 난다 — 그때 볼 것은 관측 윈도우(20봉=10시간)가 30m에
      맞는지와 `n_states_candidates`(4~6)다. 두 축 다 미검증 초기값이다.
- [ ] **Q-2** `build_bundles.py`의 모델 관문 넷이 통과하는가. 특히 **교정(Brier < 0.5)** —
      홀드아웃에서 재는 것은 이번이 처음이다. `feature_dependency`(단일 피처 40%)가
      걸리면 EV 요일 더미가 원인일 수 있다(하루 안에서 상수).
- [ ] **Q-3** 등록 뒤 G2 기동 로그가 `live 번들 결선: ['30m']`인가. `[]`면 승격이 안 된 것이고,
      `--promote live`가 챔피언 존재로 거부됐는지부터 볼 것.
- [ ] **Q-4** 기동 로그에 `국면 결선 — RegimeAI 상태 N개`가 뜨는가. `국면 미배선`이면
      저장 경로(`data/models/regime_ai.json`)를 확인할 것.
- [ ] **Q-5** 그 다음 거래일에 **`decisions_emitted`가 0을 벗어나는가**. 이것이 11거래일간
      막혀 있던 사슬의 진짜 채점이다. 여전히 0이면 셋 중 하나다:
      국면이 UNKNOWN · Meta-Labeler가 전건 거부(`threshold_report`) · `|S| < 0.20`.
      **어느 쪽인지 `MetaDecisionEngine`의 사유별 카운트로 갈라볼 것**(run_model_sweep가
      이미 그 형태로 센다).

### 남은 고도화 (미착수)

- [ ] **G-4** `minute_bar_close: timer` 승격 — p99 4거래일치 확보(08-10 1.0353초, 최대 1.3964초).
      발행 타이밍을 바꾸는 변경이라 **단독 커밋**으로.
- [ ] **G-5** 아침 복구 자동화(`recover_now.bat` + 09:00까지 첫 틱 없으면 알림)
- [ ] **C-1 후속** 분봉 차트도 실전 도메인으로 옮길지 — 옵션 시세 3거래일 관측(08-13) 뒤
- [ ] **성과 관문 결선** — G1 walk-forward 산출물을 `validate_performance()`에 흘려
      넣어 번들의 성과 3종을 "미측정"에서 실제 값으로 바꾼다. 지금은 `build_bundles.py`가
      그 자리를 비워 두고 그 사실을 리포트에 적는다.
- [ ] **수동 kill 뒤 재가동 수단**(`sys.resume`) — 08-11 09:27 사고가 드러낸 자리


---

## 2026-08-11 고도화 2차 — G-4·G-5·재가동 ([MW0601], 2026-08-11)

상세는 `DECISION_LOG.md` 2026-08-11 고도화 2차 항목.

- [x] **G-4(상수)** `MINUTE_CLOSE_GRACE_SECONDS` 1.0 → 2.0초 — 3거래일 실측 최대 1.3964초 위로
      43% 여유. **정정: NEXT_TODO의 "4거래일치"는 사실이 아니었다**(08-07은 프로세스가 죽어
      `delivery_latency`가 null)
- [x] **G-5** 웜업에 시한 — `staleness_status(warmup_expired=)` + `TickCollector.first_tick_overdue()`
      + `CollectorFirstTickOverdue`(ERROR) + `scripts/recover_now.bat`
- [x] **재가동** `sys.resume` — `ResumeSignal(operator)` · `handle_resume()` · 화면 2단 확인.
      CB 의심/확정이면 **거부**한다
- [ ] **G-4(승격)** `configs/instance.yaml`의 `minute_bar_close: tick → timer` — **오늘
      15:35에 4일째 표본이 나온 뒤**. 그 값이 2.0초를 넘으면 상수를 먼저 다시 올린다

### 오늘 15:35~15:45 이후에 할 것 (Q-*와 함께, 순서 그대로)

```
1) run_postmarket.py 완료 확인(15:45 자동)     ← 재합성 전에 학습하면 잘린 봉을 배운다
2) logs/daily_integrity_20260811.json의 delivery_latency.max 확인
     ≤ 2.0  → instance.yaml을 timer로 (G-4 승격 완료)
     > 2.0  → MINUTE_CLOSE_GRACE_SECONDS를 먼저 올리고 승격은 다음 날로
3) python scripts/train_regime_ai.py
4) python scripts/build_bundles.py --horizons 30m --promote live --operator MW0601
5) G2 재기동(또는 내일 08:25 정시)
```

### 2026-08-12(수)에 볼 것 — R-1~R-4 (Q-1~Q-5와 함께)

- [ ] **R-1** `1분봉 확정: timer (거래소 시각 경계+2.0초)`가 기동 로그에 찍히는가.
      그리고 그날 리포트의 **`late_bar_drops`가 0인가** — 이것이 유예 2.0초의 진짜 채점이다.
      0이 아니면 유예가 여전히 부족한 것이고, 그 건수만큼 틱을 버린 것이다.
- [ ] **R-2** `봉 1m` 커버리지가 종전과 같거나 나은가. `timer`는 **틱이 늦는 구간에서만**
      동작이 달라지므로 정상 구간 수치는 안 바뀌어야 한다 — 바뀌면 그게 볼 것이다.
- [ ] **R-3** 08:45~09:00에 화면 수집기 배지가 `웜업 — 장 개시 전(첫 틱 대기)`(회색)이고,
      **09:00을 넘겨도 첫 틱이 없는 날에만** CRITICAL로 바뀌는가. 정상일에 09:00 직후
      붉어지면 시한 판정이 틀린 것이다(첫 틱은 08:45에 오므로 그럴 일이 없어야 한다).
- [ ] **R-4** `CollectorFirstTickOverdue`가 **안** 찍히는가(정상일이면 0건).

### 재가동 경로는 아직 **한 번도 안 눌러봤다**

- [ ] **resume 실동작 검증** — `scripts/verify_kill_switch.py`와 같은 형태로 격리 Redis에서
      kill → resume 왕복을 흘려볼 것. Kill Switch가 "구현됨≠검증됨"으로 며칠 있었던 것과
      같은 자리이고, 이번엔 그 교훈이 이미 있다.
      **주의**: 운영 Redis로 쏘면 안 된다(pub/sub는 DB로 안 갈린다, 08-11 09:27 실측).

### 남은 것

- [ ] **C-1 후속** 분봉 차트 실전 도메인 — **08-13 관측 뒤**. 노력이 아니라 데이터가 막는다
      (오늘 1일째: 재시도 0건, 기준선 52건)
- [ ] **성과 관문 결선** — G1 walk-forward 산출물을 `validate_performance()`에 흘려 번들의
      성과 3종을 "미측정"에서 실제 값으로


---

## 2026-08-11 장후 실행 — 사슬 결선 완료 ([MW0601], 2026-08-11)

상세는 `DECISION_LOG.md` 2026-08-11 장후 항목.

- [x] 장후 절차 5/5 완료(15:45 자동) — 오늘 관측 축 만점(봉 결손 0 · 커버리지 100% ×5 ·
      거래량 1.000 · `OptionChainPollRetried` **0건**, 기준선 52건)
- [x] **G-4 승격** `minute_bar_close: tick → timer` — 4일째 max 1.129, 4일 최악 1.3964 ≤ 유예 2.0
- [x] **session_guard 버그** 보관본(`*_pre_recompose`)을 판정으로 읽던 것 — 정본 파일명만 읽는다
- [x] **RegimeAI classify() 결함** 길이-1 시퀀스 → 최근 60관측 forward filtering
- [x] **assess() 상수 관문** `MAX_SINGLE_REGIME_RATIO = 0.8`
- [x] **부적합본은 `.rejected`로** — 저장 위치가 곧 결선 여부
- [x] **④-c** `data/models/regime_ai` 저장 (RANGE 40% · HIGH_VOL 25% · TREND_DOWN 18% · TREND_UP 17%)
- [x] **④-b** `real-20260811-1604-30m` → **live** (bundles 0행 → 1행)

### 2026-08-12(수) 아침에 볼 것 — S-1~S-6 (오늘 결선의 채점)

- [ ] **S-1** G2 기동 로그에 두 줄이 **나란히** 뜨는가:
      `live 번들 결선: ['30m']` + `국면 결선 — RegimeAI 상태 5개`.
      하나라도 빠지면 그 마디가 다시 끊긴 것이다.
- [ ] **S-2 ★ 오늘의 진짜 채점** `decisions_emitted`가 **0을 벗어나는가**(장후 self-eval의
      `wiring_stage`가 `번들 미결선`에서 바뀌는가). 여전히 0이면 셋 중 하나다:
      국면이 실시간에서 UNKNOWN · Meta-Labeler 전건 거부 · `|S| < 0.20`.
      **사유별로 갈라볼 것** — `MetaDecisionEngine`이 이미 그 형태로 센다.
- [ ] **S-3** 실시간 국면 분포가 홀드아웃과 비슷한가. `RegimeRuntime`은 30m 봉 하나당 한 번
      부르므로 하루 13판정뿐이다 — 하루치로 단정하지 말고 사흘을 볼 것.
      **한 국면이 하루 종일 하나면** 그건 `.rejected`로 갔어야 할 모델이 통과한 것이다.
- [ ] **S-4 (G-4 채점)** `late_bar_drops`가 **0인가**. 0이 아니면 그 건수만큼 유예 뒤 도착한
      틱을 버린 것 — 즉시 `minute_bar_close: "tick"`으로 되돌린다.
- [ ] **S-5** `봉 1m` 커버리지·거래량 대조가 오늘(만점)과 같은가. `timer`는 **틱이 늦는
      구간에서만** 동작이 달라지므로 정상 구간 수치는 안 바뀌어야 한다.
- [ ] **S-6** 등록부 재발이 **2건으로 줄었는가**. 오늘 5건 중 3건(`exit-code-matches-log`,
      `ui-restart-observability`, `launch-window-refusal-not-counted`)은 이 세션의 kill 검증과
      G2 재기동 탓이라 내일은 안 나야 한다. 남는 2건(`no-degenerate-features`,
      `daily-axes-measured`)이 진짜 남은 것이다.

### 오늘 드러난 구조적 오탐 — 다음 fix 후보

- [ ] **`no-degenerate-features`가 EV 상수를 오탐한다** — `ev_dow_*` 5개 + `ev_dte_*` 3개는
      **하루 안에서 상수인 것이 정상**이다(요일·잔존일). 퇴화 판정 창이 하루 단위라 매일 운다.
      O-3가 예측한 그대로였다. 처방 후보: (a) 판정 창을 며칠로 넓힌다 (b) 카테고리별로
      "하루 안 상수 허용" 화이트리스트를 둔다. **(b)는 늑대소년을 만든다** — 화이트리스트가
      길어지면 그 축이 아무것도 안 잡는다. (a)가 낫다고 본다.
- [ ] **`observation_gap`이 UI 침묵을 공백으로 센다** — Streamlit은 정상 동작 중 로그를 거의
      안 쓴다. 오늘 `ui: 08:20:33~09:40:20 80분 공백`이 그것이고 실제로는 살아 있었다
      (`command_center_ui: UP`이 15:34까지 찍혔다). 프로세스 생사는 이미 상태판이 아는데
      로그 침묵으로 또 판정하니 두 축이 어긋난다.
- [ ] **resume 실동작 검증** — 아직 한 번도 안 눌러봤다(격리 Redis로 kill→resume 왕복)
- [ ] **C-1 후속** 분봉 차트 실전 도메인 — 08-13 관측 뒤(오늘 1일째: 재시도 0건)
- [ ] **성과 관문 결선** — G1 walk-forward → `validate_performance()`. 지금 번들의 성과 3종은
      `passed=False · 미측정`이다


---

## 2026-08-11 오탐 둘 fix ([MW0601], 2026-08-11)

상세는 `DECISION_LOG.md` 2026-08-11 오탐 항목.

- [x] **① EV 상수 퇴화 오탐** — `ev_core.INTRADAY_CONSTANT_OK` 11종 선언(정의 옆) +
      `spec.intraday_constant_ok()` 집계 + `spec.is_intraday_constant_ok()` **단일 판정 함수**
      (엔진·리포트 공용) + `validate_registry()`가 죽은 선언(오타) 검출
- [x] **① 반대편 축** `FeatureHealth.allowed_constant_values` → 리포트 저장 →
      다음 날 `_calendar_freeze_finding()`이 요일 원-핫 동결 검사(오탐 0)
- [x] **② UI 침묵 오탐** — `_ui_activity_from_watchdog()`가 30초 워치독의 침묵을 생존
      증거로 읽는다. `Down`~`Restarted`·`GaveUp` 이후는 합성 안 함
- [x] 08-11 리포트 재생성 실측: 임계 초과 9→4 · 재발 5→3 · ui 공백 79.8분→0분

### 2026-08-12(수)에 볼 것 — T-1~T-4 (S-*와 함께)

- [ ] **T-1** 리포트에 `피처 퇴화 0건`인가 — 등록부 `no-degenerate-features`가 사상 처음
      **검증 대기**로 들어가는가(3거래일 연속이면 검증 완료).
- [ ] **T-2** `ui` 관측 공백이 **0건**인가. 뜨면 그날 UI가 실제로 죽은 것이고
      `CommandCenterUIDown`이 로그에 있어야 한다 — 없는데 공백이 뜨면 합성 로직이 틀린 것이다.
- [ ] **T-3** `allowed_constant_values`가 리포트에 실렸는가(`ev_dow_wed: 1.0`).
      안 실리면 엔진 로그에 그 필드가 안 나온 것이다.
- [ ] **T-4** `캘린더 사이드카 동결 의심`이 **안** 뜨는가. 08-11(화)→08-12(수)라 요일 벡터가
      반드시 달라져야 한다. 뜨면 EventCalendar 주입이나 봉 시각을 볼 것 — **이 축의 첫 채점**이다.

### 남은 것

- [ ] **resume 실동작 검증** — 격리 Redis로 kill→resume 왕복(아직 한 번도 안 눌러봤다)
- [ ] **C-1 후속** 분봉 차트 실전 도메인 — 08-13 관측 뒤
- [ ] **성과 관문 결선** — G1 walk-forward → `validate_performance()`
- [ ] **`g2_paper` 관측 공백 과대평가** — 감시자가 없어 상한만 안다. 고치려면 G2에도
      heartbeat성 관측자가 필요한데, 실제 사망은 종료 코드 축이 이미 정확히 잡는다
      (08-11 실증). **지금은 안 고치는 것이 맞다** — 관측자 없는 프로세스는 보수적으로
      우는 편이 낫고, 두 축이 서로를 검산한다.

---

## 2026-08-12 장후 점검 — Fix 6종 + 고도화 4종 ([MW0601], 2026-08-12)

상세는 `DECISION_LOG.md` 2026-08-12 장후 항목 · 보고서 `logs/dailycheck/2026-08-12_post_report.md`.

### 착수 전 조사 (F-1의 선행 조건 — 이것부터)

- [x] **조사-1** `scripts/train_regime_ai.py`의 시계열 분할 방식 — **연속으로 잇는다.
      F-1은 유효하다.** (2026-08-12 확인)

      `load_continuous_series()` → `aggregate_to_horizon(m1_bars, M30)`로 소급 한계일
      (2025-12-12)부터 오늘까지를 **하나의 시계열**로 적합하고(192행), 홀드아웃 판정도
      `classify(bars[: i + 1])`로 일자를 걸친 전체 이력을 넘긴다(109행). 즉 **휴장 경계에서
      끊지 않는 것이 이 모델의 전제**이고, 매일 빈 deque로 출발하던 런타임이 오히려 학습과
      어긋나 있었다. 웜스타트는 그 어긋남을 없애는 방향이다.

      → **G-2의 차단 질문이 여기서 답해졌다.** 구동 Horizon 15m 전환은 불필요하다
      (그 대안은 "일별로 끊는다"를 택했을 때만 따라오는 것이었다).

### Fix (P0부터) — **6종 전부 구현 완료 (2026-08-12 장후)**

- [x] **F-1 (P0)** `RegimeRuntime` 웜스타트 — 구현 완료.
      **계획과 다른 점**: `__init__` 인자가 아니라 `warm_start()` **메서드**로 넣었다.
      `FeatureEngine.warm_start()`가 이미 그 형태이고(계산은 클래스가, 적재는 호출측이),
      대칭을 맞추는 편이 두 웜스타트를 나란히 읽게 한다. 로더는 계획대로 정본 하나
      (`ParquetArchiver.load_recent_bars`)를 재사용했다 — 두 벌을 만들지 않았다.
      하한 미달 시 `RegimeWarmStartShort`(WARNING).
- [x] **F-2 (P0)** 국면 분포 축 — 구현 완료. `RegimeClassified` 태그 → `regime_distribution`
      (미측정은 None) → `regime_unknown_ratio` → 등록부 `regime-not-constant`(max 0.5).
      임계는 `train_regime_ai.MAX_UNKNOWN_RATIO`와 **같은 값**으로 맞췄다 — 홀드아웃에서
      결선을 허가한 기준을 운영에서 다르게 재면 두 판정이 어긋난다.
- [x] **F-3 (P1)** 예비 리포트 채점 제외 — 구현 완료. `provisional` 플래그 +
      `load_daily_reports`가 예비본 건너뜀 + `_stale_provisional_findings()`가 잔존 예비본을
      breach로. **둘은 계획대로 같은 커밋에 함께** 들어갔다.
- [x] **F-4 (P1)** 수급 재시도 예산 — 구현 완료.
      **계획 정정**: 보고서는 `RETRY_ATTEMPTS 2→3`이라고 적었으나 실제 상수는 **1**이었다
      (총 시도 = 1 + ATTEMPTS = 2회, 그래서 로그가 「2회 시도」였다). 의도(총 3회)는 그대로,
      **1 → 2**로 올렸다. 지수 백오프(0.5→1.0초) · 5xx/타임아웃만 · 총 상한 40초.
      `option_chain_poller`는 같은 정본을 공유하는 것으로 확인(카덴스 5·10분이라 40초 상한이
      먼저 걸릴 일 없음).
- [x] **F-5 (P2)** — 구현 완료. **계획과 다른 점**: `abnormal_exits` 대상에 postmarket을
      추가하지 **않았다**. 그 경로엔 함정이 둘인데 보고서는 하나만 봤다:
      ① 리포트를 postmarket 자신이 만들어 당일엔 자기 `SessionEnd`가 없다(보고서가 지적한 것)
      ② `postmarket_*.log`는 자식(`daily_integrity_report.py`)의 `SessionStart`도 담는 **합쳐진
      stdout**이라, postmarket이 자기 마커를 찍으면 그 파일의 `SessionStart`가 2개가 되어
      `restarts 1회` 오탐이 새로 생긴다.
      → 판정을 리포트가 아니라 **다음 날 장전 자가점검**(`check_prev_postmarket`)에 뒀다.
      전일 파일은 그 시점에 완결돼 있고, 기동을 막지 않는 경고로만 남는다.
      `check_bar_close`로 R-1(`1분봉 확정: timer`)도 함께 해소.
- [x] **F-6 (P2)** `collect_evidence.py` — 구현 완료. 08-12 로그 재실행으로 §9 적신호 3·8이
      「기동 창 거절 1회(정상)」로 바뀌는 것 확인.

### 고도화 — **4종 중 3종 구현 완료 · G-2는 조사로 종결**

- [x] **G-1** 판단 사슬 관문 통과율 축 — 구현 완료. `meta_decision.py`가 `gate` 구조화 필드를
      넘기고(`DECISION_GATES` = kill/regime/dispersion/score/pass) 리포트가 `decision_funnel`로
      집계한다. **사유 문자열을 파싱하지 않는다** — 문구를 다듬는 순간 조용히 0이 된다.
      `pass=0`이면 요약이 「Risk·Sizer·OrderGateway 미검증」을 덧붙인다. **판정(breach)은 안 한다**:
      원인이 국면이면 `regime_unknown_ratio`가 이미 울고, 같은 사실에 경보가 둘이면 늑대소년이다.
- [x] **G-2** RegimeAI 학습·추론 시계열 경계 — **조사로 종결(코드 변경 불필요)**.
      조사-1이 답했다: 학습·홀드아웃·(F-1 이후) 런타임이 모두 **연속 시계열**을 쓰고 관측
      생성도 이미 같은 함수(`build_observations`)를 공유한다. 어긋나 있던 것은 런타임의 빈
      deque 하나였고 F-1이 그것을 없앴다. **구동 Horizon 15m 전환은 불필요** — 마스터플랜
      Ver 1.1 §3-1(「입력: feat.30m」) 변경 제안을 철회한다.
- [x] **G-3** 손실 예산 경보에 최대 기여일 표시 — 구현 완료. `LossBudget.dominant_day`.
      실데이터 검증: 「3거래일에 51분 … · 최대 2026-08-10 41분(80%) · 나머지 2일 10분」.
      임계 판정은 무변경.
- [x] **G-4** 변동성 축 `undefined_after_control` — 구현 완료. 산출물 JSON + 콘솔 요약의
      **분모**까지 정정했다(「7개 중 1개」 → 채점 가능했던 개수 기준). `absent_features`
      (피처셋에 없음)와는 다른 상태로 분리해 남긴다.

### 구현 산물 (2026-08-12 장후)

- 테스트 신규: `tests/data/test_poll_retry.py`(11건, 신규 파일) · regime runtime 5건 ·
  integrity report 9건 · fix_verification 4건 · loss_budget 3건 · vol_scorecard 3건.
- 기존 테스트 수정 3건 — 재시도 횟수를 **박아둔 숫자에서 정본 상수 참조로** 바꿨다
  (`1 + poll_retry.RETRY_ATTEMPTS`). 예산을 조정할 때마다 폴러가 아니라 단언이 깨지고 있었다.

### 2026-08-13(목)에 볼 것 — U-1~U-12

- [ ] **U-1** `RegimeWarmStart` 1건 · 충전 봉 수 ≥ 22 (g2_daily)
- [ ] **U-2 ★ 오늘의 진짜 채점** `DecisionEmitted` 중 `Regime=UNKNOWN` 비율 **< 50%** (현재 100%)
- [ ] **U-3** `daily_integrity`에 `regime_distribution` 수록 · 2개 이상 상태 출현
- [ ] **U-4** `l1_daily` 15:36 ERROR **≤ 4건** · `daily-axes-measured` 미출현
- [ ] **U-5** `InvestorFlowPollError` 0건 · `short_cycles` 0건
- [ ] **U-6** `postmarket_20260813.log`에 `SessionEnd` 1건
- [ ] **U-7** 자가점검에 `bar_close  1분봉 확정: timer` 행 (R-1 이월)
- [ ] **U-8** `SessionStart.git_sha`가 `ce51375` 이후 — `code_version.stale` 자연 해소 확인
- [ ] **U-9** `IrrecoverableLossBudgetExceeded` **미출현** (08-10 41분이 3거래일 창에서 이탈)
- [ ] **U-10** 등록부 `truncation-is-visible` 3/3거래일 → 검증 완료 전환
- [ ] **U-11** 등록부 `morning-launch-actually-happens` 3/3거래일 → 검증 완료 전환
- [ ] **U-12** `exit-code-matches-log`·`launch-window-refusal-not-counted`·`ui-restart-observability`
      08-11 잔상 소멸 → ❌재발에서 ⏳검증 대기로 전환. 안 되면 **오늘 새로 위반한 것**이다.

### 오늘 완료 처리 — 2026-08-11 오탐 둘 fix는 4/4 통과

- [x] **T-1** 피처 퇴화 0건 — 6개 Horizon 전부 `{always_nan: [], constant: []}` ·
      등록부 `no-degenerate-features` **4거래일 연속 검증 완료**
- [x] **T-2** `ui` 관측 공백 **0건** (`observation_gaps: []`, 08-11 79.8분 → 0분)
- [x] **T-3** `allowed_constant_values` 리포트 수록 (`ev_dow_wed: 1.0` 포함 12개)
- [x] **T-4** 캘린더 사이드카 동결 의심 미출현 — **이 축의 첫 채점 통과** (`market_findings: []`)
- [x] **S-4** `late_bar_drops` 0 — G-4(timer) 승격 채점 통과 ·
      `composer-bucket-completeness` 5거래일 연속 검증 완료
- [x] **S-5** 봉 1m 커버리지·거래량 대조 만점 (0.998 · 410/410분 · missing 0)
- [x] **R-2** 봉 1m 커버리지 종전 이상 (410행 · 100% · 최장 공백 0분)
- [x] **R-4** `CollectorFirstTickOverdue` 0건 (`CollectorFirstTick` 08:44:58)

### 미결 — 판정 보류

- [ ] **미커밋 174건 범위 확인** — `git diff --stat 4825ffe -- src/`로 런타임 영향 범위 확인.
      dev 모드라 금지계명 10 위반은 아니나 **paper/live 승격 전 반드시 정리**.
- [ ] **S-6 이월** 등록부 재발 4건(목표 2건). 오늘 **신규 위반은 1건뿐**이고 3건은 08-11 잔상 —
      U-12가 답을 낸다.

### 이월 (변동 없음)

- [ ] **resume 실동작 검증** — 격리 Redis로 kill→resume 왕복(아직 한 번도 안 눌러봤다)
- [ ] **C-1 후속** 분봉 차트 실전 도메인 — 08-13 관측 뒤
- [ ] **성과 관문 결선** — G1 walk-forward → `validate_performance()`
      (self_eval 오늘도 `pnl_measurable: false` · `wiring_stage: 주문 미발생` — F-1이 선행)

## 2026-08-13 장전 점검 — Fix 3종 + 고도화 3종 ([MW0601], 2026-08-13)

정본: `logs/dailycheck/2026-08-13_pre_report.md`. **P0 없음** · 장전 코드 변경 없음(R11).
아래 F-*/G-*는 전부 **오늘 15:35 이후** 적용.

### 착수 전 차단 질문 (F-1의 선행 조건 — 이것부터)

- [ ] **V-1 ★ F-1의 착수 조건** 오늘 장후 `data/option_chain/weekly_thu/2026-08-13.parquet`의
      호가·거래량. **> 0 이면** 8/20물 상장 확정 → F-1 착수. **전부 0 이면** 캘린더는 옳고
      폴러 단언 조건이 틀린 것 → F-1 철회, F-2만 남는다. **답 전에 판정식을 고치지 않는다.**
- [ ] **KRX 공지 원문 확인** — 월물 만기 주 목위클리 상장일. J-2의 지시(*"면제 목록을 넓히지
      말고 KRX 공지를 다시 확인할 것"*)를 실제로 이행한다.

### Fix (P0부터 — 오늘은 P0 없음)

- [ ] **F-1 (P1, 조건부)** 목위클리 상장 판정식 — `core/event_calendar.py`
      `thursday_weekly_listed()`(274-307행)에 "`d`가 목요일이고 월물 만기일이면 다음 주
      목요일을 본다" 분기 + docstring 282-285행 전제를 08-13 실측으로 교체.
      **`has_thursday_weekly()`는 건드리지 않는다**(다른 질문 · EV 피처 16개 입력 · 7월부터 정상).
      `tests/features/test_ev_core.py`의 `2026-08-13 → False`도 그쪽 단언이라 **유지**.
      신규 테스트: `tests/test_event_calendar.py`에 08-07~08-12 False / 08-13·08-14 True.
      검증: 아카이브 6일치 대조 + 다음 거래일 `OptionChainCalendarViolation` 0건.
- [ ] **F-2 (P1)** 유량 예산 축 정정 — `data/option_chain_poller.py`
      `expected_legs_per_cycle`(193-203행)이 미상장 계열에도 `legs_per_cycle`을 반환.
      기동 문구 `단언 폴링만(수집 0)` → `단언 폴링(42다리, 예산 포함)`.
      `tests/data/test_option_chain_poller.py`에 "미상장 계열도 예산 > 0" 케이스(기존 0 단언 정정).
      **F-1과 독립 — F-1이 보류돼도 단독 적용한다.**
- [ ] **F-3 (P2)** 수급 재시도 소진율 계측 — 상수는 **안 만진다**(관측 30분·4샘플).
      수급 폴러에 재시도 소진율 카운터 → `scripts/daily_integrity_report.py`가
      `investor_flow_retry_rate`로 수록. **임계 없음**(R18: 게이트 신설은 섀도 20거래일).
      L18 확인: 값이 `0`인지 `미측정`인지 실데이터로 구분되는가.

### 고도화

- [ ] **G-2 ★ 즉시(장후 최우선)** 반복 ERROR 접기 — `core/logging.py`에 `(tag, payload_hash)`
      반복 억제. 첫 1건 원래 레벨 + N분마다 `{tag}Repeated {n}회 (최초 HH:MM:SS)` 요약.
      **강등하지 않는다**(08-07 `OptionChainPollEmpty` WARNING→DEBUG는 심각도 왜곡, R6).
      해시 변경 시 즉시 원래 레벨 복귀가 안전장치. 로깅 코어 변경 → replay 검증 필수(계명 2).
      기대: 오늘 ERROR 80건 → 8건. **F-1 보류 기간 중 U-4를 지키는 유일한 수단.**
- [ ] **G-1 (이번 주)** 캘린더 예측 채점 — `logs/calendar_predictions.jsonl`에
      `thursday_weekly_listed`·`resumes_on`·`monthly_expiry` 판정을 발행 시점에 1행 기록,
      장후 배치가 실측 대조해 `hit/miss` 채움 → `daily_integrity`에 `calendar_prediction_score`.
      선행: F-1 판정 확정(예측 축이 바뀌면 스키마도 바뀐다).
- [ ] **G-3 (이번 주)** 검사 도입 시각 하한 — `scripts/self_check.py`에
      `_POSTMARKET_MARKER_SINCE = date(2026,8,13)`, 그 이전 로그는 `판정 불가(마커 도입 전)`.
      일반화: `ops/fix_verification.py` 등록부 각 항목에 `since` 필드 + **`since` 없는 항목을
      세는 메타 검사**(잊고 넣으면 진짜 결함을 "판정 불가"로 덮는다).
      근거: 같은 형태 오탐 3회째(`daily-axes-measured` · `LaunchWindowRefused` · 08-12 postmarket).

### 2026-08-13 장후에 볼 것 — V-1~V-3, V-5, V-6, V-8~V-10

- [ ] **V-2** `OptionChainCalendarViolation` 당일 누적 건수(예상 ~80건) — U-4 실패 요인 분리용
- [ ] **V-3** `OptionChainSeriesMissing` **0건**. 뜨면 오늘 받은 체인이 도중에 사라진 것
      = 「마스터파일 선등재」 가설 쪽이고 F-1은 철회된다
- [ ] **V-5** 아카이브 `expiry_date` = `2026-08-20` (라벨 파서 `weekly_expiry(2026,8,3,3)`와 일치 · I-2)
- [ ] **V-6** `InvestorFlowPollError` **0건** 유지(U-5) · 종일 `InvestorFlowPollRetried` 건수와 사이클 대비 비율
- [ ] **V-8** `daily_integrity_20260813.json` `late_bar_drops` · `missing_minutes` **둘 다 0**
      → clock offset +2.0초가 완성봉 규율(유예 500ms)에 영향 없음 확정
- [ ] **V-9 ★ 오늘의 진짜 채점** `DecisionEmitted` 중 `Regime=UNKNOWN` **< 50%** (U-2와 동일).
      오늘 `RegimeWarmStart` 200봉 충전이 어제 P0을 실제로 풀었는가
- [ ] **V-10** `daily_integrity`에 `regime_distribution` 수록 · 2개 이상 상태 · `미측정` 아님 (U-3)

### 2026-08-14(금) 장전에 볼 것

- [ ] **V-4 ★ 재개일 오차의 확정 판정** `thursday_weekly_listed(08-14)=True`이고
      `OptionChainCalendarViolation` **0건** → 오늘 위반은 **재개일 1일 오차 단건**으로 확정.
      계속 뜨면 판정식이 더 넓게 틀린 것이다. (I-1·I-3와 함께 본다)
- [ ] **V-7** `postmarket_20260813.log`에 `"tag": "SessionEnd"` **1건** — F-5의 진짜 첫 채점(U-6).
      있으면 08-13 장전의 "08-12 SessionEnd 없음" 오탐이 자연 소멸한다

### 오늘 완료 처리 — 장전에 이미 통과한 U-*

- [x] **U-1** `RegimeWarmStart` 1건 · `bars: 200` ≥ `min_bars: 22` (g2_daily 08:25:52) — **통과**
- [x] **U-7** 자가점검 `bar_close  1분봉 확정: timer (거래소 시각 경계 구동)` — **통과** (R-1 이월 해소)
- [x] **U-8** `SessionStart.git_sha = e37d387` = HEAD · `code_version.stale: false` — **통과, 자연 해소 확인**

### 오탐 판정 — 조치 불필요

- [x] **08-12 postmarket `SessionEnd` 경고는 거짓** — 배치는 15:47:16에 5/5 완주했고,
      F-5(`3720e31`)가 마커를 붙인 시각이 **18:04:24**로 3시간 늦었다.
      **`run_postmarket.py --date 20260812` 재실행 불필요.** V-7이 최종 채점.

### 미결 — 판정 보류 (수치 갱신)

- [ ] **미커밋 179건 범위 확인** (08-12 174건 → **+5**). `git diff --stat 4825ffe -- src/`.
      dev라 계명 10 위반은 아니나 줄지 않고 늘고 있다 → **paper 승격 차단 조건으로 격상 제안**.

## 2026-08-13 장중 점검 — Fix 4종 + 고도화 2종 ([MW0601], 2026-08-13)

관측 구간 09:00~12:36. **P0 없음.** 어제 P0(국면 마비)는 풀렸고(UNKNOWN 100%→12.5%),
그것이 가리던 둘이 드러났다. 보고서: `logs/dailycheck/2026-08-13_intra_report.md`.

### 적용 시점 — 전 항목 장후(15:35 이후). 장중 적용 금지 (R11 · 금지계명 3·4)

### Fix (P0 없음 · P1부터)

- [ ] **F-1 (P1) ★ 나머지의 판정 근거** 판단 갈래 값 계측 — `strategy/decision/meta_decision.py`
      `_no_trade()`(:141)의 `mlog.log`에 `n_experts`·`score`·`dispersion`·`uncertainty`·
      `model_version` 구조화 필드 추가. **`rationale` 문자열은 안 건드린다**(모듈 주석의
      *"문구를 다듬는 순간 조용히 0이 된다"*). `GATE_PASS` 경로(:130)도 같은 필드 집합으로 통일 —
      현재 두 경로의 관측 스키마가 다르다. 검증: `pytest -k meta_decision` 기존 rationale 단언 통과 + W-6.
- [ ] **F-2 (P1)** `n_experts==0`을 ④에서 분리 — `meta_decision.py`에 `GATE_NO_EXPERT="no_expert"`,
      ①(kill) 다음 **②(regime) 앞**에 `if view.n_experts == 0` 갈래. `DECISION_GATES` +
      `ops/integrity_report.py::decision_funnel`에 편입. 모듈 docstring 규칙블록에 ⓪ 명기
      (Ver 2.0 §3.1 원문에 없는 구현 측 선행 갈래 — 폴백 가시화, R10).
      **R18 저촉 아님** — 차단 결과 동일, 표기만 분리. 차단 계층 3개 고정 유지.
      착수 전 `grep -rn "DECISION_GATES\|decision_funnel" src/ scripts/` 소비처 전수 확인.
      **F-1과 같은 커밋에** — 따로 넣으면 각각 반쪽이다.
- [ ] **F-3 (P1)** 첫 사이클 국면 시드 — `scripts/run_g2_paper_trading.py::_build_regime_runtime()`이
      웜스타트 직후 `classify()` 1회 → `RegimeState`를 `TOPIC_REGIME`에 발행 + `RegimeSeeded`(INFO) 1건.
      `core/logging.py`에 `"RegimeSeeded": logging.INFO` 등록. **별도 커밋**(F-1/F-2는 관측, 이것은
      행동 변경). 보류안(집계 건너뛰기)은 §3.2 *"침묵이 아니라 판단이다"* 위배로 **기각**.
      **선행 판단: F-1 관측에서 `n_experts=0`이 나오면 그쪽이 F-3보다 우선.**
- [ ] **F-4 (P2)** 점검 도구 공백 임계를 구동 주기에 맞춤 —
      `.claude/skills/messiah-daily-check/scripts/collect_evidence.py` 공백 판정에 프로세스별
      기대 주기(l1=60s, g2=1800s) 테이블, 임계 `기대주기×2+5분`. 다이제스트 §5 표에 `기대주기` 열 출력.
      검증: 오늘 다이제스트 재생성 시 g2 공백 적신호 **8건→0건**. G-3로 대체 가능(F-4는 급한 대응).

### 고도화

- [ ] **G-1 (이번 주)** 장중 `decision_funnel` — `status_snapshot.json`에
      `decision: {funnel:{kill,no_expert,regime,dispersion,score,pass}, last_decision_kst, cycles}`.
      누적 카운터는 **엔진에 심지 않고** 스냅샷 생성기가 당일 g2 로그 `gate` 필드를 센다.
      근거: 오늘 스냅샷 최상위 키에 판단 계열 **0개** — "7/8이 한 갈래로 접힘"을 장후에야 안다.
      선행: F-1·F-2(갈래 이름 확정).
- [ ] **G-3 확장 (이번 주)** 프로세스가 자기 구동 주기를 선언 — `SessionStart`에 `cadence_seconds`
      (l1=60 · g2_paper=1800 · postmarket=null). 점검 도구가 상수 테이블 대신 그 값을 읽는다.
      **`cadence_seconds` 누락 프로세스를 세는 메타 검사** 동반(장전 G-3의 `since`와 같은 규율).
      근거: 같은 형태 오탐 4회째 — `daily-axes-measured`·`LaunchWindowRefused`·08-12 postmarket·오늘.
      장전 G-3(시간 하한)과 한 쌍의 다른 축(주기 하한).

### 이월 — 장전 항목 중 오늘 근거가 갱신된 것

- [ ] **G-2 ★ 장후 최우선 유지** 반복 ERROR 접기 — 12:33 기준 **51건**(장전 12건), 15:35까지 ~85건 궤도.
      payload 51건이 한 글자도 다르지 않다. **오늘 l1 ERROR 51건이 전부 이 태그 하나** —
      장전엔 "시끄럽다"였던 것이 이제 **"가린다"**(다른 ERROR가 섞여도 안 띈다).
      `WARNING→DEBUG` 강등 금지(08-07의 실수, R6).
- [ ] **F-3(수급 재시도 소진율, 장전 항목) — 긴급도 하향** `InvestorFlowPollRetried`가 08:51 이후
      **3시간 45분간 0건**. 장전의 *"실패율 100% · 예산 여유 0"* 은 08:36~08:51 4샘플의 성질이었다.
      계측은 유지, 우선순위는 P2 하단으로. 종일 확정은 W-4.

### 2026-08-13 장후에 볼 것 — 장전 V-* 에 추가

- [ ] **W-1 ★ 결함 ①의 확정 판정** 종일 `DecisionEmitted`의 `gate=regime`이 **1건뿐**(09:00 단건)이면
      "첫 사이클 단건" 확정. 2건 이상이면 더 넓게 틀린 것
- [ ] **W-2** 종일 `④ |S|=` 값의 분산. 13사이클 전부 정확히 `0.000`이면 `n_experts=0` 가설 강화.
      하나라도 다르면 집계는 살아 있다. (확정은 F-1 필요 — 정황일 뿐)
- [ ] **W-3** V-9 최종 채점 — `Regime=UNKNOWN` 종일 13건 중 1건 = **7.7% < 50%**
- [ ] **W-4** `InvestorFlowPollRetried` 종일 건수. 4건에서 크게 안 늘면 장전 관측 ③은
      장전 창의 성질로 확정 → 수급 F-3 긴급도 하향 확정
- [ ] **W-5** `OptionChainCalendarViolation` 종일 누적 = 약 **85건** ±5 (5분 × 425분). 벗어나면 주기 외 요인

### 2026-08-14(금) 장전에 볼 것 — 장전 V-4·V-7에 추가

- [ ] **W-6 ★ 결함 ②의 확정 판정** (F-1 적용 후) `DecisionEmitted`에 `n_experts` 필드 존재.
      **값 0 → "입력 없음" 확정 / 1 이상 → "진짜 우위 없음" 확정**
- [ ] **W-7** (F-3 적용 후) 09:00 `DecisionEmitted`의 `gate`가 **`regime`이 아닐 것** +
      `RegimeSeeded` 1건이 08:25대에 존재 + `RegimeClassified` ts < `DecisionEmitted` ts 유지
- [ ] **W-8** (F-4 적용 후) 다이제스트 §9 적신호 중 g2 공백 **0건**

### 오늘 장중 통과 — 완료 처리하지 않는다 (하루가 안 끝났다)

- [ ] **V-9 장중 잠정 통과** — `Regime=UNKNOWN` 1/8 = 12.5% < 50% (어제 100%).
      `RegimeWarmStart` bars 200 → `RegimeClassified` 8건 `bars_used: 200`, HIGH_VOL 5 → RANGE 3.
      **상수가 아니라 분포다.** `9170ce8` 라이브 검증 성립 — **확정은 W-3(15:35 이후).**
      섣부른 완료 처리를 하지 않는다.
- [ ] **V-3 장중 잠정** `OptionChainSeriesMissing` **0건** → 「마스터파일 선등재」 가설 미배제,
      **장전 F-1(캘린더 판정식)의 착수 조건 여전히 미충족**

### 미결 — 판정 보류 (장전에서 변동 없음)

- [ ] **미커밋 179건 범위 확인** — 장중 변동 없음. `git diff --stat 4825ffe -- src/`.
      dev라 계명 10 위반 아니나 **paper 승격 차단 조건으로 격상 제안** 유지.

## 2026-08-13 장후 점검 — P0 1종 + Fix 5종 + 고도화 3종 ([MW0601], 2026-08-13)

### 적용 시점 — 장후이므로 적용 가능. 단 이 예약 실행은 보고까지만 했다

각 커밋 전 `pytest`(해당 범위) + replay — 계명 2. 커밋 전 실전 반입 금지 — 계명 10.
커밋 메시지 첫 단어 `[MW0601]`.

### Fix

- [ ] **F-1 (P0) ★ 재연결 후 첫 틱 시한** — `src/messiah/data/collector.py::TickStallWatchdog`.
      `__init__`에 `self._reset_at: float | None = None`, `reset()`에서 `self._reset_at = self._monotonic()`,
      `mark_tick()` 첫 틱에 `self._reset_at = None`.
      `run_until_stalled()`의 `if self._last_tick_at is None: continue`를 3분기로 —
      `_reset_at is None`이면 콜드스타트 면제(종전 동작), 유예 내면 대기,
      유예 초과면 `CollectorReconnectNoTick`(WARNING, `core/logging.py` 등록) + `TickStallError`.
      `configs/instance.yaml`에 `collector.reconnect_first_tick_grace_seconds: 60`(R4, 하드코딩 금지).
      근거: 오늘 15:22:24 재연결 후 **11분간 경보 0건**. `_last_tick_at is None`을 무기한 워밍업으로 해석.
      60초 근거: 정상 `recent_max_gap 12.6초` · `TickDeliveryLatency` 최대 1.371초 — 4배 이상.
      회귀 위험: 08:20 기동~08:45 첫 틱 구간 오탐 → `_reset_at`을 `reset()`에서만 세팅해 명시 면제.
      테스트 3종: 콜드스타트 면제 / 유예 초과 발화 / 유예 내 틱 도착 시 해제.
- [ ] **F-2 (P0) ★ 재구독과 수신 재개를 가른다** — `collector.py` `_on_connected` **2곳**
      (`TickCollector.run_forever` :396~406 · `MultiFeedCollector.run_forever` :729~741).
      거기서는 `CollectorWSResubscribed`(INFO) `"WS 재구독 성공 — 첫 틱 대기"`,
      `CollectorWSReconnected "수신 재개"`는 `_note_first_tick` 경로(:519 · :797)로 이동.
      `ops/integrity_report.py::analyze_data_flow_ownership` 규칙 1을 두 갈래로 —
      `stalls>0 and (resubscribes+reconnects)==0` / **`resubscribes > reconnects`**.
      오늘 값(재구독 1 · 수신재개 0)이면 후자가 발화했을 것.
      소비자는 `integrity_report.py` 한 곳뿐임을 `grep -rn CollectorWSReconnected src/`로 확인함.
- [ ] **F-3 (P1)** 손실 축이 컴포넌트 CRITICAL을 읽게 한다 — `ops/status_board.py::snapshot()`(:134~)의
      `irrecoverable_loss`에 `live_critical_components` 추가, 비지 않으면 `clean=false` +
      `summary="진행 중 CRITICAL {n}건 — 손실 확정은 장후"`. `format_snapshot()`(:263~)에 노출.
      `ops/integrity_report.py`는 스냅샷 `clean`과 자기 `irrecoverable_loss_minutes`가 어긋나면
      `unmeasured`가 아니라 **`breaches`**에 넣는다.
      근거: 15:34:47 스냅샷 CRITICAL 2건 ↔ `clean: true` ↔ 장후 계산 10분.
- [ ] **F-4 (P1)** `schtasks` 조회 타임아웃 — `ops/integrity_report.py` `task_exit_codes` 생성부.
      `subprocess.run(timeout=…)`을 `configs/instance.yaml` `ops.schtasks_timeout_seconds: 30` ·
      `ops.schtasks_retries: 1`로 빼고, `/fo CSV /nh` 형식 고정. 실패 시 `detail`에 경과 초·시도 횟수 기록.
      근거: 3거래일 연속 `"조회 실패: TimeoutExpired"` — `daily-axes-measured`·`exit-code-matches-log` 재발.
      **오늘 재실행으로 즉시 채점 가능**: `python scripts/daily_integrity_report.py --date 20260813 --symbol A05608 --configs configs`
- [ ] **F-5 (P2)** 안 낸 주문을 `OrderSubmit`으로 세지 않는다 — 게이트 정지 경로에서
      `OrderSubmit` 대신 `OrderBlocked`(INFO, `core/logging.py` 등록), `reason` 유지.
      착수 전 `grep -rn '"OrderSubmit"' src/messiah/`로 위치 확정(계획 단계에서 파일 단정 안 함).
      근거: `15:24:00 [INFO] OrderSubmit "gateway halted"` 인데 `self_eval`은 `주문 0건`,
      `tag_counts`는 `OrderSubmit: 1` — 같은 하루를 1과 0으로 센다 (R6).

### 고도화

- [ ] **G-1 (이번 주)** 복구 효능 계측 — `daily_integrity`에 `recovery_efficacy`
      `{stalls, resubscribes, first_tick_after_reconnect, median_recovery_seconds, unrecovered}`.
      `unrecovered > 0`이면 `breaches`. 같은 구조를 CB(`confirmed`/`resumed`)에도.
      오늘 값 = `1 / 1 / 0 / — / 1`. 선행: F-2.
- [ ] **G-2 (이번 주)** `scripts/verify_archive_volume.py`가 **캘린더 기대 분**(08:45~15:44, 420분)을
      분모로 채점 — `expected_minutes` · `common_minutes` · `expected_but_absent_both` 3값 병기.
      양쪽 다 없으면 `OK`가 아니라 **`판정 불가 — 공식 데이터도 없음`**.
      근거: 오늘 15분 결손인데 `비율 1.000 · OK · 전 구간 정상`. 되면 W-9가 매일 자동 채점.
      위험: 최종거래일·조기폐장 예외 → `configs/krx_holidays.yaml` 옆에 세션 시간표.
- [ ] **G-3 (이번 주)** `status_snapshot.json` 최상위 `verdict`
      `{ok, worst_level, reasons[], since_kst}` — `since_kst`는 **지속시간이 곧 손실량**이라서.
      근거: 15:34:47 스냅샷은 CRITICAL 2건을 담고 있었다 — 정보는 있었고 요약이 없었다.
      F-3과 같은 함수를 건드리므로 함께. L18 주의(`state`와 `level`을 합쳐 말하지 않는다).

### 2026-08-14(금) 장전에 볼 것

- [ ] **W-9 ★ 책임 소재의 확정 판정** 08-13 분봉을 같은 API로 재조회한 분 수.
      **420분 → 우리 수집 결함 확정 / 395분 → 브로커 시세 공급 문제.**
      정황: REST(`flow_intraday/K2I`)는 15:34까지 살아 있었다 ↔ 공식 분봉도 395분이었다.
- [ ] **V-4 (장전 이월)** `thursday_weekly_listed(08-14)=True` 확인 + `OptionChainCalendarViolation` **0건**.
      재개일인데도 뜨면 판정식이 하루가 아니라 더 넓게 틀렸다.

### 2026-08-14(금) 장후에 볼 것

- [ ] **W-10** (F-1 적용 후) `CollectorReconnectNoTick` **0건**이 기본. 뜨면 그 시각 실제 무틱 대조.
      **F-1의 라이브 검증 기한 = 08-14 장후.** 판정 안 나면 08-18까지 연장하되 그때는 replay로 강제 채점.
- [ ] **W-12** (F-4 적용 후) `task_exit_codes.available` **`true`** · `unmeasured` **0건**
- [ ] **W-13** (F-3 적용 후) 정상일 `status_snapshot.irrecoverable_loss.clean` **`true` 유지**(오탐 0)

### 코드 조사 (로그 무관)

- [ ] **W-11** `px_max_ret_60`이 10m에서만 상수인 이유 — 피처 정의 창 60분과 10m 40표본(창 6봉)의 관계.
      창 길이 문제면 정의 수정, 아니면 계산 버그. 다른 5개 Horizon은 정상.
      `no-degenerate-features` 재발의 실체이고, 계명 6(피처 불일치 침묵 금지) 경계 항목.

### 재판정 예정

- [ ] **W-14 (2026-08-17)** 소급 불가 손실 4거래일 창 재판정 — 오늘 `IrrecoverableLossBudgetExceeded`
      "4거래일에 61분(예산 20분) · 최대 08-10 41분(67%)". 오늘 10분은 F-1이 흡수한다.
      08-10 41분이 창에서 빠진 뒤 예산 규칙 자체를 재평가.

### 오늘 완료 처리 — 라이브 검증이 성립한 것

- [x] **V-9 / W-3 ★ 국면은 상수가 아니라 분포다** — `regime_distribution` HIGH_VOL 5 · RANGE 8 ·
      TREND_DOWN 1 · **UNKNOWN 0%**(어제 100%). `9170ce8`(RegimeRuntime 웜스타트) **라이브 검증 성립.**
      장중 잠정 통과(12.5%)를 장후 종일 데이터로 확정.
- [x] **V-7 배치도 자기 끝을 말한다** — `postmarket_20260813.log` `SessionEnd` 1건,
      `steps_planned=5 steps_run=5 steps_failed=0`. `3720e31` **라이브 검증 성립.**
      (SessionStart 2회는 5/5가 띄운 `daily_integrity_report.py` 자식 프로세스 — 오탐)
- [x] **V-10** `daily_integrity`에 `regime_distribution` 수록 · 3종 · `미측정` 아님
- [x] **W-4 수급 재시도 긴급도 하향 확정** — `InvestorFlowPollRetried` 종일 4건 전부 08:36~08:51,
      `InvestorFlowPollError` 0건, `flow_intraday/K2I` 커버리지 99.8%. 장전 관측 ③은 장전 창의 성질.
- [x] **W-5** 캘린더 위반 84건 = 예상 85±5 궤도 내. 주기 외 요인 없음
- [x] **W-1 결함 ① 범위 확정** — `decision_funnel = {"regime": 1, "score": 13}`. **첫 사이클 단건 확정**,
      더 넓게 틀린 것 아님. 장중 F-3(국면 시드)의 범위가 이로써 확정됐다.
- [x] **V-6** `InvestorFlowPollError` 0건 유지

### 미결 — 판정 보류

- [ ] **W-2** `|S|=0.000` 13건 문자열까지 동일 — 분산 0. `n_experts=0` 가설 **강화**되었으나 확정 아님.
      확정은 장중 F-1(값 계측) 적용 후 **W-6**.
- [ ] **미커밋 179건** — 3거래일째, 종일 변동 0. dev라 계명 10 위반 아니나
      **paper 승격 차단 조건으로 격상 제안** 유지 — 승격 시점에 계명 10이 바로 걸린다.
- [ ] **V-3** `OptionChainSeriesMissing` 0건 유지. 다만 `series_findings`가 "미상장 판정인데 168분치 수신"을
      독립으로 잡았으므로 **장전 F-1(캘린더 판정식) 착수 조건은 이제 충족**된다.

## 2026-08-14 장전 점검 — P0 1종(첫 월물 롤) + Fix 5종 + 고도화 3종 ([MW0601], 2026-08-14)

### 적용 시점 — 장전이므로 적용하지 않았다. 오늘 15:35 이후 착수

코드 변경·커밋·배포·재기동 일절 없음(R11 · 계명 3·4). 각 커밋 전 `pytest`(해당 범위) + replay.

### Fix

- [ ] **F-1 (P0) ★ 롤 경계를 넘는 웜스타트** — `src/messiah/data/archiver.py::ParquetArchiver.load_recent_bars()`
      에 `predecessor_symbols: Sequence[str] = ()` 추가. 주 심볼에서 `max_bars` 미달 시 부족분을
      직전 월물에서 시간 역순으로 채운다. 호출측 `scripts/run_l1_daily.py:303` ·
      `scripts/run_g2_paper_trading.py:242`가 `symbol_master`의 월물 계보로 선행 심볼을 구해 전달
      (하드코딩 금지 — R4). 로그 `bars_by_source={"A05609":0,"A05608":200}` 추가 —
      **조용히 잇지 않는다(R10)**. 이어붙인 구간은 **수익률/변동성 계열 화이트리스트에만** 허용,
      가격 수준 피처는 롤 경계 이전을 NaN으로(통째로 이으면 계명 6 위반). **선행 심볼 1개까지**
      (200봉=30m 약 9거래일이면 충분하고, 2개면 갭이 2개가 되어 오염이 곱해진다).
- [ ] **F-2 (P0) ★ 롤을 자가점검이 먼저 외친다** — `scripts/self_check.py`에 `rollover` 항목 신설.
      `front_month_future_code()`가 `data/bars/` 최신 아카이브 심볼과 다르면
      `[WARN] rollover A05608→A05609 · 웜스타트 가용 N봉`. **FAIL 아닌 WARN** — 롤은 매달 정상적으로
      일어나고 FAIL이면 매달 기동이 막힌다. 판정 기준은 "롤 여부"가 아니라 **"이어붙인 뒤 가용 봉 수"**.
      오늘 자가점검은 3회 전부 PASS를 냈다 — 불변원칙 6이 못 본 결함이다.
- [ ] **F-3 (P1)** 웜스타트 결손을 산출물이 세게 한다 — `src/messiah/ops/integrity_report.py`에
      `warm_start_bars_by_horizon` · `warm_start_bars_by_source` · `regime_warm_start_bars` 수록.
      지금은 오늘의 P0가 장후 리포트에 아무 자국도 안 남는다.
      **오늘 재실행으로 즉시 채점 가능**: `python scripts/daily_integrity_report.py --date 20260814 --symbol A05609 --configs configs`
- [ ] **F-4 (P1)** `configs/pending_verifications.yaml`에 `rollover-warmstart` 등록
      (`metric: warm_start_bars_by_horizon.30m`, `min: 22`, `registered: 2026-08-14`).
      **선행 F-3** — 그 파일 머리의 규칙("값이 실제로 생산된다는 것을 먼저 예측치로 적는다").
      사람 기억은 다음 롤(2026-09-14)까지 한 달을 못 간다.
- [ ] **F-5 (P2, 조건부)** `OptionChainSkipped`에 `reason` 열거형 필드
      (`underlying_price_missing` 등) + `ops/integrity_report.py`에 `option_chain_skips_by_reason`.
      **W-15 판정 전 착수 금지** — 롤 원인으로 확정되면 F-1에 흡수되어 불필요해진다.

### 커밋 계획

- [ ] 커밋 ① F-1+F-2 — `[MW0601] 심볼은 계약의 이름이지 시계열의 이름이 아니다 — 롤 경계 웜스타트`
- [ ] 커밋 ② F-3+F-4 — `[MW0601] 다음 롤은 한 달 뒤다 — 기억 대신 등록부에 맡긴다`
- [ ] 커밋 ③ F-5 (조건부, W-15 결과 대기)
- [ ] 커밋 ④ **어제(08-13) 세운 F-1~F-5** — 내용 그대로 유효, 순서만 뒤로. 오늘 P0가 더 급하다.

### 고도화

- [ ] **G-1 (다음 단계 · 기한 2026-09-14)** 연속 계약 아카이브 `data/bars/KOSPI200F_C1/{horizon}/`을
      장후 배치(`run_recompose.py` 뒤, `verify_archive_volume.py` 앞)에서 생성. 비율 조정(back-adjust),
      **원본 심볼 아카이브 병존**(가격 수준이 필요한 소비처는 원본을 본다).
      **선행 조사: 기존 학습 자산 "근월물 8심볼 167거래일"의 롤 경계 8곳이 어떻게 처리됐는가.**
      이미 이어져 있다면 G-1은 소비처 통일 작업으로 축소된다. 조정 방식이 백테스트 결과를 바꾸므로
      기존 모델과 직접 비교 불가 — R18 섀도 계측 대상.
- [ ] **G-2 (이번 주 · 어제 G-3에 병합)** `status_snapshot.json` `verdict.reasons[]`에
      `warm_start_short` 추가. **별도 `readiness` 키 신설 금지** — 화면이 또 나뉘면 L18의 반대편
      실수다. 근거: 08:49:57 스냅샷이 컴포넌트 4종 전부 `state:"OK"`를 냈고 자가점검도 PASS인데
      실제로는 국면 UNKNOWN·NaN 85%였다. 있어야 했던 값 =
      `{ok:false, reasons:["feature_nan_ratio_exceeded","warm_start_short"], since_kst:"08:20:38"}`.
- [ ] **G-3 (다음 단계 · F-1 효과 확인 후 재평가)** `src/messiah/strategy/meta/decision.py`에
      `regime == UNKNOWN and warm_start_short` → NO_TRADE 사유 `regime_axis_unavailable`.
      **R18에 따라 20거래일 섀도 후 승격** — 즉시 차단하면 오늘 같은 날의 데이터를 못 얻어
      게이트의 옳고 그름을 영영 모른다. 근거: `decision_funnel={"regime":1,"score":13}` =
      국면이 UNKNOWN이어도 regime 게이트는 열려 있다.

### 2026-08-14(금) 장중 12:30에 볼 것

- [ ] **W-15 ★ 1-2의 원인 확정** — 09:00 이후 `OptionChainSkipped` 건수.
      **0건 → 롤 직후 기준가 부재로 확정(F-1에 흡수, F-5 불필요) / 계속 뜨면 기준가 소스
      자체의 결함으로 별건 P1 승격.**

### 2026-08-14(금) 장후에 볼 것

- [ ] **V-11 ★** `RegimeClassified.regime` 종일 분포 — **UNKNOWN 100% 예상.**
      이게 안 나오면 웜스타트 이해가 틀린 것이다(예측을 먼저 적는다).
- [ ] **V-12** `daily_integrity_20260814.json` `nan_ratio_by_horizon` — 전 Horizon median > 0.5
      예상(전일 0.0). 낮게 나오면 장중 자연 회복 — **회복 곡선을 F-1 설계에 반영한다.**
- [ ] **W-18** (F-3 적용 후) `daily_integrity`에 `warm_start_bars_by_horizon` 존재 · `미측정` 아님
- [ ] **W-9 ★ (장전에서 재이월)** 08-13 분봉을 같은 API로 재조회한 분 수.
      **420분 → 우리 수집 결함 확정 / 395분 → 브로커 시세 공급 문제.**
      장전 이월 사유: 개장 직전 재조회는 유량을 라이브 수집과 다툰다
      (`run_backfill.py` docstring). **사유 없이 미루면 영구 미결이 되므로 사유를 남긴다.**
- [ ] **W-10** (어제 F-1 적용 후) `CollectorReconnectNoTick` 0건 — **어제 F-1이 아직 미적용이므로
      오늘 판정 연기 가능성 높음.** 연기되면 08-18까지 연장하되 그때는 replay로 강제 채점.
- [ ] 미커밋 건수 — 179건 → 커밋 ①② 후 감소 확인

### 2026-08-17(월) 장전에 볼 것

- [ ] **W-16 ★ F-1의 라이브 검증** `FeatureWarmStart.bars_by_horizon` 전 Horizon ≥ 22 ·
      `bars_by_source`에 `A05608` 등장 · `RegimeWarmStartShort` 0건 · `FeatureWarmStartShort` 0건
- [ ] **W-17** (F-2 적용 후) 자가점검 `rollover` 줄 — 비-롤일 `[OK]`, 롤일 `[WARN]` + 가용 봉 수.
      **롤일 채점은 2026-09-14.**

### 오늘 완료 처리 — 라이브 검증이 성립한 것

- [x] **V-4 ★ 목위클리 재개 판정이 실측과 일치** — `OptionChainCalendarViolation` **0건**
      (전일 장전 8건). `weekly_thu` 계열이 08:23:20·08:33:19·08:43:20 폴링에 실제 등장.
      08-14 재개(8/20 만기물) 예측 성립. → **장전 F-1(캘린더 판정식)을 P1→P2 하향.**
      판정식이 하루가 아니라 더 넓게 틀린 것이 아니었다.
- [x] **`3720e31` 장전 자가점검에서 효과 확인** — `[OK] postmarket 20260813 장후 배치 정상 종료 확인`.
      어제까지 4회 반복되던 오탐("20260812 SessionEnd 미기록")이 오늘은 뜨지 않았다.

### 무효화된 기존 완료 항목 — 커밋이 아니라 검증 범위의 결함

- [ ] **V-9/W-3 재개봉** — 08-13 장후에 *"국면은 상수가 아니라 분포다 — `9170ce8` 라이브 검증
      성립"* 으로 완료 처리했으나 오늘 롤 경계에서 성립하지 않았다. **`9170ce8` 자체는 옳다**
      (아카이브가 있으면 200봉을 정확히 읽는다 — 08-12·08-13 실측). 틀린 것은 **"하루 통과했으니
      검증됐다"는 판정**이다. 롤이라는 축이 등록부에 없었고, 관측 이력 전체(07-30~08-13)가 단일
      심볼 구간이라 그 축이 관측될 기회가 없었다. `FixVerificationRecurred`는 뜨지 않았다 —
      **그래서 F-4가 필요하다.** 재판정 기한: **2026-09-14(다음 롤) 장전.**

### 미결 — 판정 보류

- [ ] **1-2 장전 옵션체인 스킵 10건의 원인** — 롤인가 장전 창의 성질인가. W-15가 가른다.
      첫 틱 시각(08:44:58)은 전일과 초 단위 동일하므로 **첫 틱은 정상이고 그 이전 기준가 부재만이
      오늘의 차이**라는 데까지는 확정됐다.
- [ ] **미커밋 179건** — 4거래일째, 변동 0. dev라 계명 10 위반 아니나 paper 승격 차단 조건으로
      격상 제안 유지. 오늘 F-1~F-4가 얹히면 5거래일차.

## 2026-08-14 장중 점검 — P0 2종 + Fix 8종 + 고도화 4종 ([MW0601], 2026-08-14 10:51)

보고서 `logs/dailycheck/2026-08-14_intra_report.md` · 증거 `logs/dailycheck/evidence_2026-08-14_intra.md`

### 적용 시점 — 장중이므로 적용하지 않았다. 오늘 15:35 이후 착수

- [ ] **커밋 ① F-1+F-2** — `[MW0601] 심볼은 계약의 이름이지 시계열의 이름이 아니다 — 롤 경계 웜스타트`
      **★ 월요일(08-17) 개장 전 필착.** 안 들어가면 월요일도 종일 UNKNOWN이 확정이다(P0-2).
- [ ] **커밋 ② F-3+F-4** — `[MW0601] 화면이 어제 계약을 오늘이라 불렀다 — 근월물 동적 해석 + 배지 임계 유도`
- [ ] **커밋 ③ F-5+F-6** — `[MW0601] 0은 없었다는 뜻도 안 셌다는 뜻도 된다 — 기여 0·폴링 성공 계측`
- [ ] **커밋 ④ F-7** — `[MW0601] 이월된 숫자는 측정이 아니다 — 미커밋 두 축 분리`
- [ ] **커밋 ⑤** 08-13 세운 F-1~F-5(재연결 첫 틱 시한 등) — 내용 그대로 유효, 순서만 뒤로

### Fix

- [ ] **F-1 (P0) ★ 롤 경계를 넘는 웜스타트 — 소비처 2곳이 아니라 3곳이다**
      `src/messiah/data/archiver.py::ParquetArchiver.load_recent_bars()`에 `predecessors` 인자.
      `src/messiah/data/symbol_master.py::preceding_front_months()` 신설(하드코딩 금지 R4).
      호출부 3곳: `run_l1_daily._load_warmup_artifacts()` ·
      **`run_l1_daily._seed_preopen_reference_price()` ← 장중에 추가된 세 번째 소비처** ·
      `run_g2_paper_trading._warm_start_regime()`. 3곳 전부 `ops/canonical_consumers.py` 등록.
      **조용히 잇지 않는다(R10)** — `bars_by_source={"A05609":4,"A05608":196}` 로그 필수.
      비율 조정은 이 단계에서 하지 않는다(G-1이 다룬다).
- [ ] **F-2 (P0) ★ 롤을 자가점검이 먼저 외친다** — `scripts/self_check.py`에 `rollover` 항목.
      마스터파일 근월물 vs 직전 거래일 아카이브 심볼 대조, 다르면 `[WARN]` + 가용 봉 수.
- [ ] **F-3 (P1) ★ UI 심볼 하드코딩 제거** — `src/messiah/ui/app.py:109 DEFAULT_SYMBOL = "A05608"`
      **삭제**(R4 위반). `symbol_master.front_month_future_code()`로 동적 해석 —
      **정본 `run_g2_paper_trading.py:195 _resolve_front_month_symbol()`과 같은 경로를 쓴다**
      (두 벌 만들면 "정본 아닌 소비자"가 여섯 번째로 생긴다).
      해석 실패 시 화면을 죽이지 않고 배지 + 수동 입력 유지.
      `app.py:1013` 경보 문구를 `🛑 {symbol}의 오늘 봉이 없다 — ①수집기 ②심볼이 근월물과
      일치하는지 순으로 확인할 것`으로 — **원인 후보를 하나로 단정하지 않는다.**
      `app.py:110 DEFAULT_TICK_SIZE` 주석의 A05608 참조도 정리.
- [ ] **F-4 (P1)** `src/messiah/ui/app.py:125-139 _STALE_AFTER` — `FuturesView`/`RegimeState`
      임계를 구동 주기에서 유도(`주기×1.5`), `주기×2` 초과 시 "죽음" 승격(`app.py:272` 재사용),
      배지 캡션에 `LIVE (30m 주기 · 마지막 09:30)` 근거 병기.
      현재 10초 상수 vs 실제 발행 주기 1800초 → **거래일의 99.4%가 오탐.**
- [ ] **F-5 (P1) ★ W-2를 확정 가능하게 만든다** —
      `src/messiah/strategy/futures/aggregator.py:185` `total_weight<=0` 분기에
      `AggregatorNoContribution` **INFO** 로그: `views_received` · `blocked_by_meta[]` ·
      `blocked_by_uncertainty[]` · `blocked_by_freshness[]`.
      WARNING 아닌 이유 — 하루 15건 이하이고 국면이 죽은 날엔 정상 동작이기도 하다.
      `ops/integrity_report.py`에 `no_contribution_reasons` 집계.
- [ ] **F-6 (P1)** `src/messiah/data/option_chain_poller.py::poll_once()` 말미에
      `OptionChainPolled` **DEBUG** 사이클 요약(다리마다가 아니라 사이클당 1건, `legs`·`spot`).
      `OptionChainPollEmpty`가 2026-08-07에 WARNING이라 22번 울고 강등된 전례를 따른다.
      `scripts/collect_evidence.py` §9에 *"장중 `OptionChainPolled` 0건"* 축 추가.
- [ ] **F-7 (P2)** `.claude/skills/messiah-daily-check/scripts/collect_evidence.py` §1이
      두 축을 **이름을 갈라** 출력: `작업트리 미커밋 {porcelain -uall}` /
      `기준선 대비 src/ 변경 {diff --stat <baseline> -- src/} · 기준선 {sha} {날짜}`.
      기준선 sha는 설정에서 읽는다(R4). `references/report_template.md` 머리말도 분리.
      **결정 필요**: 기준선 `4825ffe` 유지 vs "마지막 paper 승격 심사 통과 커밋"으로 재정의.
      **권고 후자** — 그래야 숫자가 승격 판단에 의미를 갖는다. 사용자 확인 대기.
- [ ] **F-8 (P2)** 코드 변경 없음 — 장전 G-3 폐기 기록 + 경로 정정
      (`strategy/meta/decision.py` → `strategy/decision/meta_decision.py`).

### 폐기 — 착수하지 않는다 (판단 근거를 남긴다)

- [x] ~~**장전 F-5** `OptionChainSkipped`에 `reason` 열거형~~ — **W-15 판정 완료로 폐기.**
      롤 원인 확정(08-11·08-12 장전 스킵 0건 vs 오늘 10건 전량 장전). F-1이 흡수한다.
      장전의 조건부 판단("W-15가 가른다")이 옳았다.
- [x] ~~**장전 G-3** `regime_axis_unavailable` NO_TRADE 게이트 신설~~ — **불필요, 폐기.**
      `meta_decision.py:56 _EVENT_LIKE_REGIMES = {EVENT, UNKNOWN}` + `:92-97` 게이트 ②가
      **이미 무조건 NO_TRADE**. 오늘 `DecisionEmitted` 4/4가 `gate="regime"`으로 실증.
      장전이 근거로 삼은 어제 퍼널 `{"regime":1,"score":13}`을 **거꾸로 읽었다** — 그것은
      "게이트가 열려 있다"가 아니라 "어제는 국면이 UNKNOWN이 아니어서 13건이 ②를 통과해
      ④에서 접혔다"는 뜻. 착수했다면 이미 있는 동작을 다시 구현하고 **R18 섀도 20거래일을
      태웠다.** `e37d387`("조사가 제안의 절반을 지웠다")과 같은 교훈의 반복.

### 고도화

- [ ] **G-1 (다음 단계 · 기한 2026-09-14 다음 롤) 롤 D-1 사전 백필** —
      오늘 `nan_ratio` 회복 곡선 실측: 1m 84.7→**2.2%**(129발행) · 3m →31.4% · 5m →32.8% ·
      10m →59.9% · 15m →61.3% · **30m 84.7→84.7%(4발행, 회복 0.0%p)**.
      회복은 시간이 아니라 **누적 봉 수**의 함수이고 판단 구동 Horizon이 하필 30m이라
      회복이 정확히 0. **F-1이 읽는 쪽을 고쳐도 롤 당일 첫 사이클까지는 빈다.**
      → 장후 배치에 `run_backfill.py` 조건부 결선(`EventCalendar`가 다음 거래일을 롤로 판정 시).
      소급 한계 2025-12-12이므로 신규 월물 상장 이후 구간만 — F-1이 잇는 원월물 구간과 합산.
      **선행 F-1**(`bars_by_source`가 백필분과 이월분을 구분해야 한다).
- [ ] **G-2 (이번 주 · 조사만) 롤 경계 8곳 선행 조사** — `data/bars/A0560{1..9}/` 경계 8곳의
      종가-시가 점프와 봉 연속성 실측. NEXT_TODO가 학습 자산을 *"근월물 8심볼 167거래일"* 로
      적는데 그것은 **8번 끊긴 데이터**일 수 있고 아무도 확인한 적이 없다.
      이어져 있으면 연속계약 구축(`data/bars/KOSPI200F_C1/`)은 소비처 통일로 축소된다.
- [ ] **G-3 (이번 주) `status_snapshot.json` `verdict.reasons[]`** —
      오늘 10:51:27 스냅샷은 컴포넌트 4종 중 3종을 OK로, 자가점검은 PASS를 냈다.
      **세 화면이 각자 정상을 말하는 동안 시스템은 종일 판단 불능이었다.**
      있어야 했던 값: `verdict.ok=false · reasons:["warm_start_short",
      "feature_nan_ratio_exceeded","regime_unknown"] · since_kst:"08:20:38"`.
      **별도 `readiness` 키를 신설하지 않는다**(화면이 또 나뉘면 L18의 반대편 실수).
      F-5가 있으면 `reasons`에 한 축 추가. 없어도 착수 가능.
- [ ] **G-4 (다음 단계 · 선행 F-4) 신선도 임계를 전 배지로 일반화** —
      `CircuitBreakerStatus`만 이 함정을 알고 40초로 잡아 뒀고 `FuturesView`는 10초 상수로
      남았다. **한 곳에서만 피한 것은 설계가 아니라 우연이다.**
      발행자가 `expected_interval_seconds`를 선언하고 UI가 `임계=주기×1.5`,`죽음=주기×3` 계산.
      `tests/test_false_positive_axes.py`에 "상수 임계가 새로 추가되면 실패하는" 테스트.
      메시지 스키마 변경(현 `version=1 types=21`) 수반.

### 2026-08-14(금) 장후에 볼 것

- [ ] **V-11 ★** `RegimeClassified.regime` 종일 분포 — **UNKNOWN 100% 예상**(10:30까지 4/4 성립).
      아니면 웜스타트 이해가 틀린 것이다(예측을 먼저 적는다).
- [ ] **V-12** `daily_integrity_20260814.json` `nan_ratio_by_horizon` —
      **30m median ≈ 0.847(회복 0) · 1m median < 0.10** 예상. 오늘 §3 G-1 회복 곡선 표와 대조.
- [ ] **V-13** `decision_funnel` — `{"regime": 13~15}` 단독 예상. `score` 이하가 0이면
      ③④⑤(Risk·Sizer·OrderGateway) 전 계층이 오늘도 미검증이라는 뜻.
- [ ] **W-18** (F-3 적용 후) `daily_integrity`에 `warm_start_bars_by_horizon` 존재 · `미측정` 아님
- [ ] **W-9 ★ (장전→장중 재이월, 장후 필착)** 08-13 분봉을 같은 KIS API로 재조회한 분 수.
      **420분 → 우리 수집 결함 확정 / 395분 → 브로커 시세 공급 문제.**
      개장 중 재조회는 유량을 라이브 수집과 다툰다(`run_backfill.py` docstring).
      **사유 없이 미루면 영구 미결이 되므로 두 번째로 사유를 남긴다.**
- [ ] 미커밋 실측 재확인 — 작업트리 10 files / 기준선 대비 src/ 9 files (커밋 ①~④ 후 변화)

### 2026-08-17(월) 장전에 볼 것

- [ ] **W-16 ★★ F-1의 라이브 검증(축 4개)** — `FeatureWarmStart.bars_by_horizon` 전 Horizon ≥ 22 ·
      `bars_by_source`에 `A05608` 등장 · `RegimeWarmStartShort` 0건 ·
      **`OptionChainSkipped` 0건**(장중에 추가된 네 번째 축).
      **★ F-1 미적용이면 실패가 산술적으로 확정이다**(30m 14봉 < 22). 그 경우 **F-1의 실패가
      아니라 미적용의 결과**로 채점한다 — 둘을 섞으면 08-13에 V-9를 "하루 통과했으니
      검증됐다"로 잘못 닫은 것과 같은 실수를 반대 방향으로 하게 된다.
- [ ] **W-17** (F-2 적용 후) 자가점검 `rollover` 줄 — 비-롤일 `[OK]`. **롤일 채점은 2026-09-14.**
- [ ] **W-19** (F-3 적용 후) UI 상단 심볼 `A05609` · 붉은 경보 없음 · 차트가 당일 봉
- [ ] **W-23** (F-7 적용 후) 점검 보고서 머리말에 "작업트리 미커밋"·"기준선 대비 src/ 변경"
      두 축이 **실측값으로** 분리 표기

### 2026-08-17(월) 장중에 볼 것

- [ ] **W-20** (F-4 적용 후) `intel.futures` 배지 — 30분 주기에서 LIVE 유지, 65분 침묵 시 "죽음"
- [ ] **W-21 ★ W-2 확정** (F-5 적용 후) `AggregatorNoContribution` 1건 이상 관측 →
      네 갈래(views 비었음/meta_h=0/u_h=1/f_h=0) 중 무엇인지 **즉시 확정**.
      **주의**: `REGIME_WEIGHTS[UNKNOWN]`은 비어 있지 않다(전 Horizon 0.5) —
      "UNKNOWN이라 가중치 0"이라는 손쉬운 설명은 이미 반증됐다.
- [ ] **W-22** (F-6 적용 후) `OptionChainPolled` — 09:00 이후 3계열(regular/weekly_mon/
      weekly_thu) 전부 등장

### 2026-08-18(화)

- [ ] **W-10** `CollectorReconnectNoTick` 0건 — **오늘 재연결 0회라 판정 불성립**
      (`CollectorFirstTick` 1건뿐). 08-18까지 연장하되 그때는 **replay로 강제 채점.**
- [ ] **V-14** (F-1 미적용 시) 30m 웜스타트 28봉 ≥ 22 — 롤 비용이 정확히 2거래일이었는지 확인

### 오늘 완료 처리 — 라이브 검증이 성립한 것

- [x] **★ W-15 원인 확정** — 09:00 이후 `OptionChainSkipped` **0건** + 3계열 아카이브 정상 기록
      (`data/option_chain/{regular,weekly_thu,weekly_mon}/2026-08-14.parquet`, 10:52~10:55).
      대조: 08-11·08-12 장전 스킵 **0건** vs 오늘 **10건 전량 장전(08:21~08:43)**,
      첫 틱 08:44:58 직후 정지. → **롤 원인 확정. 장전 F-5 폐기, F-1에 흡수.**
- [x] **★ `dbe37df` 5xx 백오프 라이브 검증** — 09:33:02 `InvestorFlowPollRetried`
      *"1회 재시도로 복구: 500 Internal Server Error"* `attempts=2`. 실전 작동 확인.
- [x] **V-4 유지** — `weekly_thu` 오늘도 정상 수집(10:54 기록), `OptionChainCalendarViolation` 0건.

### 정정 — 이월된 숫자를 실측으로 교체

- [x] ~~**미커밋 179건** (4거래일째, paper 승격 차단 조건 격상 제안)~~ — **실측과 다르다. 철회.**
      `git diff --stat 4825ffe -- src/` = **9 files**(NEXT_TODO가 스스로 명시한 측정식의 답).
      `git diff --stat HEAD -- src/` = **변경 없음**(미커밋 src/ 0건).
      `git status --porcelain -uall` = **10 files**(tracked 수정 3건 전부 `.md`).
      `git rev-list --count 4825ffe..HEAD` = 10 — **4825ffe 이후 src/ 변경은 전부 커밋에 담겼다.**
      **존재하지 않는 부채를 근거로 4거래일간 승격 차단을 제안하고 있었다.**
      → F-7이 수집기에서 두 축을 갈라 매번 실측하게 한다.

### 미결 — 판정 보류

- [ ] **`n_experts=0`의 실제 갈래** — 네 갈래 중 무엇인지. F-5 적용 후 1회 관측이면 확정(W-21).
      30m `nan_ratio`가 종일 84.7%(회복 0)라 `u_h=1`(불확실성)이 유력하나 **확정 아님.**
- [ ] **08-13 15:23~15:33 `OptionChainSkipped` 5건** — 마감 후 꼬리. 폴러 정지 시각과 선물 틱
      종료 시각의 불일치로 추정. 실해 없음. **P2 기록만, 오늘 fix 대상 아님.**

## 2026-08-14 정기 장중(12:30) 점검 — P0 0 + Fix 5종 + 고도화 2종 ([MW0601], 2026-08-14 12:36)

관측 구간 09:00~12:36. 10:51 조기 점검의 **델타**다 — F-1~F-8 · G-1~G-4는 그대로 유효하며
여기서 다시 세지 않는다. 보고서 `logs/dailycheck/2026-08-14_intra_1230_report.md`.
**P0 없음 — 권고 조치는 관망.**

### 적용 시점 — 장중이므로 적용하지 않았다. 오늘 15:35 이후 착수

### Fix

- [ ] **F-9 (P1) ★ NaN 임계 초과 경보를 `warmed_up` 가드에서 분리** —
      `src/messiah/features/engine.py:536-538`. `warmed_up = len(history) >= _MAX_HISTORY(200)`이
      **억제**로 쓰여 롤 당일(웜스타트 0봉) 경보가 통째로 꺼졌다. 임계 초과를 **먼저** 판정하고
      `warmed_up`이면 기존 WARNING(문구 불변), 아니면 신규 `FeatureNanWarmupExceeded`(INFO,
      Horizon당 1회 + 30분 재고지, `bars`/`required` 동반). 억제 상태는
      `self._warmup_exceeded_last: dict[Horizon, datetime]`.
      **기존 WARNING에 합치지 않는다** — 2026-07-24가 없앤 잡음이 그대로 돌아온다(30m 매일 14건).
      **커밋 ① · 월요일 개장 전 필착**(F-1·F-2와 함께 replay).
- [ ] **F-10 (P2)** `src/messiah/ops/status_board.py` — ① `write()` 200-206행 `os.replace`를
      3회 재시도(0.1s·0.3s)로 감싼다(`tmp.unlink` 유지) ② `_write_forever()` 241-246행에
      `consecutive_failures` 카운터, 연속 4회(=1분) 이상이면 `StatusSnapshotStalled`(WARNING) 1회
      ③ **253행 상태판 영구 중단 경로를 `StatusBoardHalted`(ERROR)로 개명**.
      개명 전 `grep -rn StatusSnapshotWriteFailed` 로 `core/logging.py:225` ·
      `ops/fix_verification.py` · `scripts/agenda.py` 소비처 전수 수정. **커밋 ⑤.**
- [ ] **F-11 (P2) 완료** — `DECISION_LOG` G-1 근거 문구 정정(회복 0.0%p → 회복 개시 5봉,
      12:30 실측 61.3%). 인라인 정정 블록 삽입 완료. **커밋만 장후.**
- [ ] **F-12 (P2)** `.claude/skills/messiah-daily-check/references/report_template.md` +
      `SKILL.md` §4 — *"같은 날 같은 국면 재점검이면 `<날짜>_<국면>_<HHMM>_report.md`.
      기존 파일을 덮어쓰지 않는다"*. 오늘 규칙대로였으면 10:51 보고서 39.9KB가 소실될 뻔했다.
      **커밋 ⑤.**
- [ ] **F-13 (P2)** `.claude/skills/messiah-daily-check/scripts/collect_evidence.py` —
      §9 자동 적신호 11개 중 **8개가 g2의 정상 30분 공백**이라 진짜 신호(F-9·F-10)를 밀어냈다.
      프로세스별 기대 주기를 로그에서 유도(`RegimeClassified` 인접 간격 최빈값 = 오늘 1800초)해
      공백 임계를 **`max(10분, 최빈간격×1.5)`** 로. `9a4d4ea`(LaunchWindowRefused 오탐 제거)와 같은 종류.
      **F-7과 같은 커밋 ④**(같은 파일).

### 고도화

- [ ] **G-5 (이번 주 · G-1보다 선행 · 선행 F-9) Horizon별 "회복 개시 봉 수" 계측** —
      오늘 실측 회복 개시: 30m 5번째 · 15m 3번째 · 10m 3번째 · 5m 2번째 · 3m·1m 첫 발행 직후.
      `engine.py`에 `min_bars_for_signal`(전 피처 요구 윈도의 최댓값) 프로퍼티 →
      `FeatureWarmStart`에 `required_by_horizon` 동반 기록 → 웜스타트 0봉이면
      *"N번째 발행(=HH:MM)부터 회복 시작, 임계 도달 M거래일 후"* 를 기동 시 1줄 산출.
      **G-1의 백필 일수를 이 값이 정한다.** 사양 없이 G-1을 짜면 임의의 날짜를 고르게 된다.
- [ ] **G-6 (다음 단계 · 선행 G-3) 관측 표면 간 불일치 자체를 신호로** —
      G-3 `verdict.reasons[]` 각 항목에 `sources[]`/`missing_from[]`. `missing_from`이 비어
      있지 않으면 그 자체가 관측 결함. `tests/test_false_positive_axes.py`에
      "어떤 reason이 한 표면에만 나타나면 실패하는" 테스트.
      **별도 `readiness` 키는 신설하지 않는다**(L18의 반대편 실수 — 10:51 G-3 판단 승계).

### 2026-08-14(금) 장후에 볼 것 (12:30 점검 추가분)

- [ ] **V-18** 15:35 `FeatureHealthSummary`/`FeatureHealthDegenerate` — Horizon 6종 전부 등장
      (= "검사된 0") · 30m `always_nan` 개수. **오늘은 per-bar 임계 경보가 종일 0건이었으므로
      이것이 유일한 로그 기록이다.**
- [ ] **V-19** 종가 `data/bars/A05609/30m/2026-08-14/` 행 수 — **13~15행** 기대.
      14 미만이면 "월요일 웜스타트 14 < 22" 예측을 재계산.
- [ ] **V-11 확정** `RegimeClassified.regime` 종일 분포 — UNKNOWN 100%(12:30까지 8/8).
- [ ] **V-13 확정** `decision_funnel` — `gate=regime` 단독(12:30까지 8/8, 타 gate 0건).
- [ ] `delivery_latency` p99 — `run_l1_daily.py:924`가 장 마감 1회 호출. 장중 부재는 정상.

### 2026-08-17(월) 장중에 볼 것 (12:30 점검 추가분)

- [ ] **V-15 ★** (F-9 적용 후) `FeatureNanWarmupExceeded` — 임계 초과 Horizon마다 1회 이상 등장 ·
      `bars`/`required` 동반 · **1m에는 미등장**(1m NaN 0.7%).
- [ ] **V-16** (F-10 적용 후) `StatusSnapshotWriteFailed` **0건**. 발생 시
      `StatusBoardHalted`(ERROR)와 태그가 갈리는지.

### 2026-08-17(월) 장전에 볼 것 (12:30 점검 추가분)

- [ ] **V-17** (F-13 적용 후) 다이제스트 §9 — g2 30분 공백이 적신호에서 제외 · l1 공백 판정 불변.

### 오늘 12:30 시점 통과 — 재보고하지 않는다

- [x] ERROR/CRITICAL 0건 · 4컴포넌트 `state=OK` · `code_version.stale=false` ·
      장중 재기동/배포/학습 흔적 0(계명 3·4) · `FixVerification*` 0건.
- [x] **데이터 연속성 무결** — 1분봉 234행 **결손 0 · 거래량 0봉 0**(08:45~12:38) ·
      합성봉 169개 항등식 일치(유실 0) · `AggregatorLateTickDropped` **0건(측정된 0)** ·
      `irrecoverable_loss.clean=true`.
- [x] **`dbe37df` 5xx 백오프 표본 8건** — 전부 `attempts=2` 복구, 미복구 0.
      빈도 정상 범위(08-11 7 · 08-12 7 · 08-13 14 · 오늘 8). **오탐으로 올리지 않는다.**
- [x] **W-15 유지** — `OptionChainSkipped` 09:00 이후 0건 · `OptionChainCalendarViolation` 0건.

### 미결 — 판정 보류 (12:30 점검)

- [ ] **`WinError 5`의 상대 프로세스** — UI/백신/점검도구 중 무엇인지 사후 특정 불가.
      **F-10은 원인 특정 없이도 유효**하므로 선행조건으로 걸지 않는다.
- [ ] **`n_experts=0`의 갈래** — 30m `nan_ratio`가 61.3%로 **회복 중**임이 드러났으나 여전히
      임계의 3배라 ③ `u_h=1` 가설은 강해지지도 약해지지도 않았다. F-5 적용 후 1회 관측이면 확정(W-21).

### F-7 보강 (12:30 점검에서 원인 특정) — 별도 항목 아님, F-7에 흡수

- [ ] **"179건"의 출처는 CRLF 개행 잡음이다** —
      `git diff --stat HEAD -- src/ scripts/` → **83 files, 30095 insertions / 30095 deletions**
      (삽입=삭제, 정확히 동일 = 줄 끝만 바뀐 서명).
      `git diff --stat --ignore-all-space HEAD -- src/ scripts/` → **비어 있음**(실제 변경 0).
      `git config --get core.autocrlf` **미설정** · `file src/messiah/ui/app.py` → **CRLF**.
      → 개행 정규화 설정이 다른 환경에서 diff를 돌리면 전 파일이 "변경됨"으로 잡힌다.
      **4거래일간 존재하지 않는 부채가 승격 차단 근거로 살아 있었던 진짜 이유.**
      **F-7 사양 확정**: `--ignore-all-space` 로 산출하되, 무시 전/후가 다르면
      *"개행 잡음 N files — 실제 변경 M files"* **두 값을 나란히** 적는다(한쪽만 적으면 반대 오해).
      `core.autocrlf` 표준값을 리포 `.gitattributes`로 못박는 것도 함께 검토. **커밋 ④.**

## 2026-08-14 정기 장후(15:45) 점검 — P0 2 + P1 2 + Fix 4종 + 고도화 4종 ([MW0601], 2026-08-14 16:05)

보고서 `logs/dailycheck/2026-08-14_post_report.md`

### 적용 시점 — 장후이므로 적용 가능. 단 이 예약 실행은 보고까지만 했다. 구현은 "구현해" 지시 후

### Fix

- [ ] **F-A (P0) ★★ 장후 배치 심볼 자동 해석 — 커밋 ① 오늘 저녁 필착** —
      `scripts/run_postmarket.py:127` `default="A05608"` **제거** → `symbol_master.
      front_month_future_code(day)` 해석. 1분봉 부재 시 `SymbolResolutionMismatch`(ERROR) +
      **exit 2**로 배치 중단. 헤더(`:286`)에 해석 근거 명기. `core/logging.py` 태그 신설.
      **착수 시 `grep -rn 'default="A056' scripts/` 전수 확인**(오늘 확인분은 1개뿐).
      회귀 위험: 과거일 재실행 — 해석 함수에 반드시 `day`를 넘기고 넘긴 날짜와 결과를 나란히 로깅.
- [ ] **F-A 후속 (오늘 저녁 필착)** — `run_postmarket.py --date 2026-08-14 --symbol A05609` 재실행.
      원본은 `daily_integrity_20260814_wrong_symbol.json`으로 보존(08-05 `_pre_recompose` 선례).
- [ ] **F-B (P0) provisional 리포트 + 채점 제외 — 커밋 ②** —
      `ops/integrity_report.py` `build_report()` 조회 정합 가드 → **이미 스키마에 있는 `provisional`
      사용**(오늘 False). `ops/fix_verification.py`에 `검증 보류` 상태 추가, `provisional` 날짜는
      재발 판정 제외. `series_coverage`에 `symbol_scoped: bool` — 집계 시 심볼 종속 계열 분모 제외.
      회귀 위험: "N거래일 연속" 카운터가 늦어짐(옳은 방향). `configs/pending_verifications.yaml` 기한 동반 점검.
- [ ] **F-C (P1) 퇴화 판정 보류를 "0건"이라 말하지 않기 — 커밋 ④** —
      `features/engine.py` `FeatureHealth.judged: bool` 추가(`:131-148`, `:317-338`),
      `log_feature_health()`(`:340-367`) 3분기. 태그 둘로 분리(R6): `FeatureHealthNotJudged`(INFO,
      평시) / `FeatureHealthJudgmentDegraded`(WARNING, 악화). `ops/integrity_report.py:642-645`,
      `:1430-1440` 전파 + `unmeasured`에 추가. `ops/fix_verification.py:273` 분모 제외.
      **적용은 F-1보다 뒤** — F-1이 들으면 30m 표본이 늘어 분기 빈도가 준다.
- [ ] **F-D (P1) `task_exit_codes` 측정 실패를 위반과 구분** —
      `ops/fix_verification.py` 채점 전 `task_exit_codes.available` 확인 → False면 `검증 보류`.
      `ops/integrity_report.py` `schtasks` 타임아웃 설정화(R4) + 1회 재시도.
      **`exit-code-matches-log` 재발이 진짜인지 08-11 이후 계속 측정 실패였는지 이걸로 갈린다.**

### 기존 Fix — 오늘 관측이 우선순위를 바꾼 것

- [ ] **F-1 (P0) ★★ 롤 경계 웜스타트 — 마감 "월요일 개장 전" → 오늘 저녁으로 당김. 커밋 ③** —
      **근거: V-19 확정.** `data/bars/A05609/30m/2026-08-14.parquet` **15행 < 하한 22**.
      F-1 없이 월요일 08:25 웜스타트하면 또 UNKNOWN — 2거래일째 판단 정지가 산술이 됐다.
- [ ] **F-2 (P0) 롤 당일 자가점검 경고** — **F-A와 같은 커밋 ①로 묶는다**(둘 다 "오늘이 롤 당일임을
      시스템이 먼저 말한다").
- [ ] **F-13 (P2) 수집기 오탐 제거 — 범위 확대** — g2 30분 공백 + **postmarket 이중 `SessionStart`**
      (15:45:19는 5/5 서브프로세스 자기 마커) + 미커밋 179건.
- [ ] **F-10 (P2)** `status_board.py:200-206` `os.replace` 재시도 — 발생 **2건**으로 증가
      (12:07:51 · 14:15:51). 12:30에는 1건이었다.
- [ ] **F-7 (P2)** 미커밋 건수 CRLF 정정 — **5거래일차.** 오늘 저녁 커밋이 얹히면 실제 변경이 처음 생긴다.
- [ ] **F-9 (P1)** NaN 경보를 `warmed_up` 가드에서 분리 — 커밋 ③에 F-1과 동반. F-C와 인접하나 별건.

### 고도화

- [ ] **G-7 (이번 주 · 선행 F-A) "오늘의 정본 심볼"을 단일 소스로** —
      `core/symbol_resolution.py` `resolve_trading_symbol(day)` 단일 함수 +
      `logs/trading_symbol_<날짜>.json`. **해석이 아니라 조회가 되면 갈라질 수 없다.**
      오늘 해석 경로 최소 3갈래 확인. F-A는 응급, G-7이 구조.
- [ ] **G-8 (다음 단계 · 선행 G-6) 축 모순을 감지에서 원인 특정까지** —
      중재 규칙 3단(`sources[]` / 경로 차이를 원인 후보로 승격 / 소수파 경로 존재 여부 되묻기).
      `tests/test_false_positive_axes.py`. **오늘 `breaches`가 모순을 말하고 멈춘 것이 근거.**
- [ ] **G-9 (다음 단계 · 선행 F-C) 다일 누적 퇴화 판정** —
      `logs/feature_health_rolling.json`, 직전 3거래일 합산 ≥ 30. 30m는 하루 **15봉이 물리적 상한**
      (오늘 실측)이라 임계 조정으로는 영원히 해결 불가. `fix_verification`의 "N거래일 연속" 패턴 이식.
- [ ] **G-10 (9월 롤 전 필착 · 선행 F-A·F-1) 롤 당일을 1급 개념으로** —
      `ev_rollover_win`을 피처에서 운영 축으로 승격. 롤 캘린더 정본화 + 자가점검 `rollover` 줄(F-2 통합)
      + **롤 당일 강제 CI 게이트 `tests/test_rollover_day.py`**(심볼 인자를 받는 모든 진입점 전수 검사).
      **오늘 하루에 롤 결함이 독립된 두 곳에서 터졌다 — 세 번째 지점을 사람이 찾지 말게 한다.**

### 2026-08-17(월) 장전에 볼 것 (장후 점검 추가분)

- [ ] **W-24 ★★** (F-A 적용 후) 월요일 장후 배치 헤더 — `A05609 (근월물 자동 해석)` 등장.
      단, 월요일은 롤 당일이 아니므로 진짜 채점은 **다음 롤(2026-09-14 근방)**.
- [ ] **W-25** 자가점검 `postmarket 20260814 장후 배치 정상 종료 확인` — `[OK]` 유지되는가.
      오늘 재실행(F-A 후속)이 이 판정을 흐리지 않는지.
- [ ] **W-26 ★** (F-1 적용 후) `RegimeWarmStart.bars_by_horizon` 30m **≥ 22** ·
      `bars_by_source`에 A05609·A05608 **두 심볼 모두 등장** · `RegimeWarmStartShort` **0건**.
      **미적용 시 예측: 30m 15봉 → 또 UNKNOWN 100%.** 어느 쪽이든 F-1 효과의 직접 채점이다.

### 2026-08-17(월) 장후에 볼 것 (장후 점검 추가분)

- [ ] **W-9 재이월 (2회차)** — 08-13 분봉 **420분 vs 395분**의 책임 소재.
      오늘도 판정 불가(P0로 장후 배치가 그 축을 못 건드림). 기준 불변: 420=우리 수집 결함 /
      395=브로커 공급 문제. **F-A 적용 후라 정상 심볼로 돈다.**
- [ ] **W-27** (F-B 적용 후) `FixVerificationRecurred` — `provisional` 날짜 제외로 건수가 줄었는가.
      오늘 12건 중 `tick-collection-live`는 **허위였다**(실측 110,397행).
- [ ] **W-28** (F-C 적용 후) 15:35 — `FeatureHealthNotJudged` 15m·30m 등장 ·
      `unmeasured`에 2건 추가 · `FeatureHealthSummary` 문구가 `"검사된 0"`으로.
- [ ] **W-29** (F-D 적용 후) `task_exit_codes.available` — `True`가 되는가.
      계속 `False`면 `exit-code-matches-log`가 `재발`이 아니라 `검증 보류`로 나오는지.
- [ ] **W-30** `delivery_latency` p99 — **오늘 기준선 1.026초**(20,000표본). 전일 대비 델타를 본다.
- [ ] **W-31** 12:51 버킷 유실 2건 — 오늘 저녁 재합성으로 치유됐다면 `late_bar_drops`가
      08-17 리포트에서 0으로 돌아오는가(`composer-bucket-completeness` 검증 재개).

### 오늘 장후 시점 통과 — 재보고하지 않는다

- [x] **종료 시퀀스 무결** — `l1_daily` 15:36:29 · `g2_daily` 15:35:00 · `postmarket` 15:46:29
      전부 `SessionEnd` "정상 종료". `shutdown_watchdog` 15:40. 재기동 0 · 비정상 종료 0 · 크래시 0.
- [x] **수집 무결** — 1분봉 **410행 결손 0분** · 거래량 항등식 **1.000**(118,599/118,599) ·
      head/middle/tail missing 0 · `flow_intraday/K2I` 99.8% · 틱 **110,397행**.
- [x] **`delivery_latency` 산출** — p50 0.507 / p90 0.925 / p99 1.026 / max 1.212 · 20,000표본.
      12:30 「장중 부재는 정상」의 결론.
- [x] **계명 3·4 준수** — 장중 학습·배포·재기동 0. 당일 커밋 0. `session_git_shas` 단일 `e37d387`.
- [x] **`FixVerificationPassed` 9건** · **5xx 백오프 8건 전부 `attempts=2` 복구, 미복구 0**.
- [x] **V-11/V-13 확정** — `regime_distribution {UNKNOWN:14}` · `decision_funnel {regime:14}`.
- [x] **장전 「1-2의 원인」 → 롤 확정** — `OptionChainSkipped` 09:00 이후 0건. F-1에 흡수.
- [x] **오탐 격리** — postmarket `SessionStart` 2회(서브프로세스 마커) · g2 30분 공백 14건(설계대로) ·
      미커밋 179건(CRLF).

### 미결 — 판정 보류 (장후 점검)

- [ ] **`exit-code-matches-log`가 진짜 재발인가 측정 실패인가** — `task_exit_codes.available=False`
      (`TimeoutExpired`). 08-11 이후 계속 판정 불가였을 가능성. **F-D가 이걸 가른다.**
- [ ] **`WinError 5`의 상대 프로세스** — 미특정 유지(12:30 판단 승계). F-10은 원인 특정 없이도 유효.
- [ ] **`n_experts=0`의 갈래** — 미확정 유지. 30m `nan_ratio` 종가 0.60으로 임계 3배.
      F-5 적용 후 1회 관측이면 확정(W-21).
- [ ] **`archiver-restart-restore` / `truncation-is-visible`의 진짜 심각도** — 오늘 위반은 성립하나
      근거 수치가 오염됐다(410분→실제 33분 / 0.0%→실제 94.5%). **F-B 적용 후 재채점해야 실체가 보인다.**

## 2026-08-14 장후 구현 완료 — 커밋 6개 ([MW0601], 2026-08-14 저녁)

```
1b92f1f  F-A + F-2      2386bcb  F-B + F-D      dff7f49  F-1 + F-9  ← 월요일 필착분
80fea47  F-C            ccb2d13  F-13+F-7+F-10  0b80580  F-3 + F-4
```
전체 회귀 1,973건 통과 · 신규 테스트 57건.

### 완료 처리 — 구현되어 커밋됨

- [x] **F-A** 장후 배치 심볼 자동 해석 + 오조회 가드(`SymbolResolutionMismatch`, exit 3)
- [x] **F-2** 자가점검 `rollover` 항목 — 실측 `A05608 → A05609. 신규 30m 0일 · 직전 25일`
- [x] **F-B** `symbol_mismatch_suspected` + `symbol_candidates` 신설. **`provisional` 재사용
      안 함**(그쪽은 "예비본"이라는 다른 뜻 — 재사용하면 다음 날 허위 breach)
- [x] **F-D** `Get-WinEvent` 1회 재시도 + 시한 인자화. **계획의 절반(`available` 확인)은
      이미 구현돼 있어 착수 안 함**(`fix_verification.py:643-645`)
- [x] **F-1** 롤 경계 웜스타트 — `load_recent_bars_by_source()` + `warmstart_symbol_chain()`,
      소비처 3곳 + `canonical_consumers` 등록. 실측 30m **15봉 → 200봉**
- [x] **F-9** `FeatureNanWarmupExceeded`(INFO, Horizon당 1회 + 30분 재고지)
- [x] **F-C** `FeatureHealth.judged` + `FeatureHealthNotJudged`(INFO) + `unmeasured` 전파 +
      `degenerate_feature_count`가 판정된 Horizon만 계산
- [x] **F-13** 공백 임계를 최빈간격×1.5로 유도(g2 45분) + `NestedSessionStart`로 배치 자식
      구분. 실측 §9 적신호 **11 → 9건**
- [x] **F-7** 미커밋 두 축 분리(`--ignore-all-space`). 실측 "179건" → "실제 변경 2파일"
- [x] **F-10** `os.replace` 3회 재시도 + 태그 4분할(`WriteFailed`/`Stalled`/`Resumed`/
      `StatusBoardHalted` ERROR)
- [x] **F-3** UI `DEFAULT_SYMBOL` 삭제 → 상태판 `trading_symbol` **조회**. 경보 문구가
      원인 후보를 둘로 연다
- [x] **F-4** 신선도 임계를 `valid_until - ts_utc`에서 유도(`data_source.derived_stale_after`)

### ★ 신규 P0 — 구현 중 발견, 같은 커밋(2386bcb)에서 처리

- [x] **보존본이 정본을 9거래일간 덮었다** — `load_daily_reports()`가 파일명이 아니라 JSON
      안의 `date`로 키를 잡아, `daily_integrity_20260805_pre_recompose.json`이 08-05 채점을
      재합성 **이전** 값으로 되돌려 놓고 있었다(`horizon_findings` 0→5 · `unmeasured` 0→2 ·
      `breaches` 4→9). 그날 5→0을 만든 복구가 **채점에 한 번도 반영된 적이 없다.**
      → 파일명 규격 강제 + 파일명/내용 날짜 불일치 폐기. 보존본 2건 `logs/superseded/`로 이동.
      **오늘 장후 보고서 F-A의 보존 권고를 그대로 따랐다가 나도 한 번 밟았다** — 그 사본이
      정정본을 덮어 재채점이 하나도 안 바뀌었다.

### 오늘 실측된 정정 효과

- [x] `run_postmarket --date 2026-08-14` 재실행 — `tick_rows` **0 → 110,397** ·
      거래량 비율 0.0% → **1.000** · `vol_scorecard` 생성 · `unmeasured` 2→1 · `breaches` 13→11
- [x] 재채점 — **재발 12 → 11 · 통과 9 → 10**, `tick-collection-live` "8거래일 연속 충족"
- [x] **V-11 확정** `regime_distribution = {"UNKNOWN": 14}` — 예측 성립
- [x] **V-13 확정** `decision_funnel = {"regime": 14}` — ③④⑤ 전 계층 오늘도 미검증

### 2026-08-17(월) 장전에 볼 것 — 갱신

- [ ] **W-16 ★★ F-1 라이브 검증(축 4개)** — `FeatureWarmStart.bars_by_horizon` 전 Horizon
      ≥ 22 · `bars_by_source`에 **A05608 등장** · `RegimeWarmStartShort` **0건** ·
      `OptionChainSkipped` **0건**. **F-1이 들어갔으므로 이제 "미적용의 결과"라는 변명이
      성립하지 않는다** — 실패하면 그것은 F-1의 실패다.
- [ ] **W-17** 자가점검 `rollover` 줄 — 비-롤일이므로 `[OK] 비-롤일 — 근월물 A05609 유지`.
      **롤일 채점은 2026-09-14.**
- [ ] **W-19** UI 상단 심볼 `A05609` · 사이드바 "기본값 출처: 상태판(수집 프로세스가 기록)" ·
      붉은 경보 없음 · 차트가 당일 봉
- [ ] **W-23** 다이제스트 §1에 "작업트리 미커밋 N건" / "`src/`+`scripts/` 실제 변경 M파일"
      두 축이 실측값으로 분리 표기
- [ ] **W-24 (신규)** `code_version.stale=false` 복귀 — 월요일 기동이 오늘 6커밋을 태운다
- [ ] **W-25 (신규)** 자가점검에 `rollover` 항목이 실제로 뜨는지(장전 3회 기동 전부)

### 2026-08-17(월) 장중에 볼 것 — 갱신

- [ ] **W-20** `intel.futures` 배지 — 30분 주기에서 LIVE 유지, 캡션에 "N초 전 수신 · 주기 30분"
- [ ] **W-21 ★ W-2 확정** `AggregatorNoContribution`… **미구현**(F-5는 이번 6커밋에 없다).
      **W-21은 F-5 착수 전까지 판정 불가로 이월한다** — 기한 2026-08-21.
- [ ] **W-22** `OptionChainPolled`… **미구현**(F-6도 이번에 없다). 같이 이월.
- [ ] **W-26 (신규)** `FeatureNanWarmupExceeded` — F-1이 들었으면 **안 떠야 한다**(창이 차 있음).
      뜨면 F-1이 그 Horizon에서 안 들었다는 뜻이라 진단이 곧바로 나온다.
- [ ] **W-27 (신규)** `NestedSessionStart` — 장후 배치에서 4건, `SessionStart`는 1건.
      다이제스트 §9에 "중복 기동" 오탐이 안 떠야 한다.
- [ ] **W-28 (신규)** `StatusSnapshotWriteFailed` **0건**(재시도로 흡수) ·
      `StatusSnapshotStalled` 0건

### 2026-08-17(월) 장후에 볼 것

- [ ] **W-29 (신규)** `unmeasured`에 "15m/30m 피처 퇴화 판정(표본 N < 최소 30)" 등장 ·
      `degenerate_feature_count`가 판정된 Horizon만 셈
- [ ] **W-9 ★ (3회째 이월)** 08-13 분봉 420 vs 395. 오늘도 못 했다 — 장후 배치가 F-A 적용
      전이라 그 축을 못 건드렸다. **월요일 장후엔 정상 심볼로 도므로 조건이 갖춰진다.**
      420분 → 우리 수집 결함 / 395분 → 브로커 공급 문제.
- [ ] **W-10** `CollectorReconnectNoTick` — 오늘도 재연결 0회로 판정 불성립. 08-18 replay 강제 채점.

### 미착수 — 다음 순번

- [ ] **F-5** `AggregatorNoContribution`(P1) — W-21의 확정 조건. **이번 6커밋에 없다.**
- [ ] **F-6** `OptionChainPolled`(P1) — W-22의 확정 조건. **이번 6커밋에 없다.**
- [ ] **F-11** DECISION_LOG G-1 근거 문구 정정(코드 변경 없음)
- [ ] **F-12** 같은 날 같은 국면 2회 점검 시 보고서 파일명 규칙
- [ ] **F-8** 장전 G-3 폐기 기록 + 경로 정정 — 본 항목으로 대체됨(폐기 사유는 기록됨)

### 고도화 — 전부 미착수

- [ ] **G-1** 롤 D-1 사전 백필 — **기한 2026-09-14(다음 롤).** F-1이 읽는 쪽을 고쳤으므로
      우선순위 재평가 필요: F-1만으로 월요일이 해결되면 G-1은 "롤 당일 첫 사이클"만 남는다.
- [ ] **G-2** 롤 경계 8곳 선행 조사 (이번 주)
- [ ] **G-7** 심볼 해석 경로 통일 — **UI 구간은 F-3이 처리했다.** 남은 것은 `scripts/`·
      `src/messiah/ops/`. `grep -rn 'default="A056' scripts/` 결과 아직 6곳
      (`run_compact` · `run_vol_scorecard` · `run_backtest_harness` · `run_full_path_smoke` ·
      `run_formal_expert_training_smoke` · `run_regime_ai_smoke`). 스모크 5개는 우선순위 낮음.
- [ ] **G-9** 다일 누적 퇴화 판정 (선행 F-C — 완료됨)
- [ ] **G-10** 롤 당일 CI 게이트 — `tests/test_rollover_day.py`가 이미 그 자리로 만들어졌다.
      남은 것은 "심볼 인자를 받는 전 진입점 전수 검사"로 확장하는 것.

### 재시동 — 하지 않는다 (장후 보고서 §4 유지)

두 프로세스는 15:35~15:36에 정상 종료돼 지금 살아 있지 않다. 월요일 08:20/08:25 정시
트리거가 새 코드를 태운다. **`code_version.stale`은 오늘 저녁 6커밋으로 true가 됐고**
월요일 기동이 자동 해소한다(W-24가 검증점).

## 2026-08-14 고도화 구현 완료 — G-1~G-10 ([MW0601], 커밋 f52eed7)

전체 회귀 2,004건 통과 · 신규 테스트 43건.

### ★★ 날짜 정정 — 오늘 기록 전체에 적용

- [x] **2026-08-17은 광복절 대체휴일이다.** `configs/krx_holidays.yaml:53`.
      `EventCalendar.next_trading_day(2026-08-14)` = **2026-08-18(화)**.
      **오늘 네 보고서와 위 dev_memory 항목이 전부 "월요일 08-17"로 적었다 — 전부 08-18이다.**
      아래 관측 항목의 날짜를 그에 맞춰 적는다.

### 완료 — 구현되어 커밋됨

- [x] **G-2 (조사)** 롤 경계 8곳 실측. **전제가 틀렸다** — `load_continuous_series()`가 이미
      후방조정을 한다. 진짜 갭은 basis 측정 실패(이번 롤 `matched_minute=None`).
      basis 중앙값 116틱(2.32pt) = 1분봉 변동 중앙값의 3배. 고유 거래일 **164일**(167 아님).
- [x] **G-5** `required_bars_by_feature()` 측정 — `px_ema_cross_60`=180 · `px_macd_h_60`=139.
      `BARS_PER_SESSION` 실측표 + `recovery_forecast()`. **웜스타트 0봉이면 30m 12거래일.**
      `FeatureWarmStart`에 `required_bars`·`recovery_forecast` 실림 + `FeatureWarmStartShort` 신설.
- [x] **G-7** `core/symbol_resolution.py` — `resolve`/`record`/`recorded`/`resolve_for_tools`.
      우선순위 명시 > 런타임 기록 > 만기 규칙. 소비처 5곳 통일(postmarket·compact·
      vol_scorecard·self_check·UI). `TradingSymbolDisagreement`(ERROR) 신설.
- [x] **G-10** `is_rollover_day()`/`next_rollover_day()` + **진입점 전수 CI 게이트 2종**.
      운영 스크립트·모듈에 만기 심볼 상수가 새로 생기면 롤 당일까지 안 기다리고 깨진다.
      `run_compact`·`run_vol_scorecard`의 하드코딩 제거.
- [x] **G-1** `scripts/run_roll_overlap.py` + 장후 배치 **5/6단계 조건부 결선**.
      `RollOverlapCaptured`/`RollOverlapFailed`/`RollBasisUnmeasured` 신설.
      **다음 실동작: 2026-09-10(만기일) 장후.**
- [x] **G-9** `ops/feature_health_rolling.py` — 3거래일 합산(30m 45봉 > 하한 30).
      퇴화는 **교집합**. `spans_rollover`로 창 안 롤 경계 표시. 리포트 `unmeasured` 연동.
- [x] **G-3** `ops/verdict.py` + `status_snapshot.verdict` — 별도 `readiness` 키 안 만듦.
- [x] **G-6** 표면 대조를 **리포트**가 한다(상태판은 로그를 안 읽는다).
      `verdict_surface_gaps` + breach.
- [x] **G-8** `arbitrate_axes()` — 경로가 다르면 원인 후보로 승격, 소수파 경로를 되물어
      답을 싣는다. 기존 `cross_check_head_truncation`에 결선.
- [x] **G-4** `TopicSnapshot.cadence_seconds`/`dead` — 주기×3이면 "죽음". 상수 임계가
      다시 생기면 깨지는 회귀 테스트.

### 2026-08-18(화) 장전에 볼 것 — **날짜 정정됨**

- [ ] **W-16 ★★ F-1 라이브 검증** — `FeatureWarmStart.bars_by_horizon` 전 Horizon ≥ 22 ·
      `bars_by_source`에 A05608 등장 · `RegimeWarmStartShort` 0건 · `OptionChainSkipped` 0건.
      **F-1이 들어갔으므로 "미적용의 결과"라는 변명이 성립하지 않는다.**
- [ ] **W-17** 자가점검 `rollover` — 비-롤일이므로 `비-롤일 — 근월물 A05609 유지 ·
      다음 롤 2026-09-11`. **롤일 채점은 2026-09-11.**
- [ ] **W-19** UI 상단 `A05609` · 사이드바 "기본값 출처: 상태판(수집 프로세스가 기록)"
- [ ] **W-23** 다이제스트 §1 미커밋 두 축 실측 표기
- [ ] **W-24** `code_version.stale=false` 복귀(어제 7커밋을 태운다)
- [ ] **W-30 (신규 G-5)** `FeatureWarmStart.required_bars=180` · `recovery_forecast`에
      전 Horizon "충족" — 웜스타트가 200봉을 채웠으면 부족 Horizon이 0이어야 한다.
- [ ] **W-31 (신규 G-7)** `logs/trading_symbol_20260818.json` 생성 ·
      `TradingSymbolDisagreement` **0건**(마스터파일과 만기 규칙이 일치해야 한다)

### 2026-08-18(화) 장중/장후에 볼 것

- [ ] **W-20** `intel.futures` 배지 — 30분 주기에서 LIVE 유지 · 캡션 "주기 30분"
- [ ] **W-26** `FeatureNanWarmupExceeded` — F-1이 들었으면 **안 떠야 한다**
- [ ] **W-27** `NestedSessionStart` 4건 · `SessionStart` 1건 · 다이제스트 중복기동 오탐 0
- [ ] **W-28** `StatusSnapshotWriteFailed` 0건
- [ ] **W-29** `unmeasured`에 퇴화 판정 보류 표기 · `feature_health_rolling` 채워짐
- [ ] **W-32 (신규 G-3)** `status_snapshot.verdict.summary` — 정상일이면 "판단 가용"
- [ ] **W-33 (신규 G-6)** `verdict_surface_gaps` **0건**(F-9가 로그 침묵을 이미 막았다)
- [ ] **W-9 ★ (4회째 이월)** 08-13 분봉 420 vs 395. **이제 장후 배치가 정상 심볼로 돈다.**
- [ ] **W-10** `CollectorReconnectNoTick` — 재연결 0회면 replay 강제 채점

### 2026-09-10(목) 만기일 장후 · 2026-09-11(금) 롤 당일

- [ ] **W-34 (G-1 실동작)** `run_roll_overlap` 5/6단계가 `RollOverlapCaptured`를 남긴다
- [ ] **W-35 (G-1 효과)** 다음 `compute_roll_offsets`에서 A05609→A05610의
      `matched_minute`이 **None이 아니다** · `RollBasisUnmeasured` 0건
- [ ] **W-36 (G-10 실동작)** 자가점검 `rollover` 줄이 롤일 `[WARN]` 형태로 뜬다

### 미착수 — 남은 것

- [ ] **F-5** `AggregatorNoContribution`(P1) — W-21의 확정 조건. **여전히 미착수.**
- [ ] **F-6** `OptionChainPolled`(P1) — W-22의 확정 조건. **여전히 미착수.**
- [ ] **F-11** DECISION_LOG G-1 근거 문구 정정(코드 변경 없음)
- [ ] **F-12** 같은 날 같은 국면 2회 점검 시 보고서 파일명 규칙
- [ ] **포맷 비준수 6파일** — `models/score_calibration.py` + 테스트 5종.
      고도화 커밋에서 **의도적으로 뺐다**(무관한 포맷 변경이 섞이면 설계 변경과 잡음을
      못 가른다). 별건 커밋으로 처리할 것.
- [ ] **런타임 후방조정(신규 제안)** — F-1의 `load_recent_bars_by_source()`는 **무조정**
      스플라이스다. 학습은 back-adjust된 연속 시계열을 쓰는데 추론은 무조정 창을 쓴다 —
      **학습과 추론이 롤 경계에서 어긋난다.** G-1이 basis를 확보하면 그 값으로 런타임
      웜스타트도 조정할 수 있다. **선행: G-1 실동작(2026-09-10).** 그 전에는 조정할
      basis 자체가 없다.

## F-5·F-6 구현 완료 ([MW0601], 2026-08-14, 커밋 4b6cb27)

전체 회귀 2,013건 통과 · 신규 9건.

- [x] **F-5** `AggregatorNoContribution`(INFO) — 여섯 갈래 전부 기록.
      `integrity_report.no_contribution_reasons`로 하루 단위 집계.
      **W-21/W-2의 확정 조건이 갖춰졌다.**
- [x] **F-6** `OptionChainPolled`(DEBUG) — 사이클당 1건, `published/legs`.
      `_poll_one`이 bool 반환. 리포트는 "폴러 생존 + 완주 0"인 좁은 경우만 `unmeasured`.

### 이로써 2026-08-14 점검의 Fix가 전부 닫혔다

F-A·F-B·F-C·F-D · F-1·F-2·F-3·F-4·F-5·F-6·F-7·F-9·F-10·F-13 — **코드 항목 전부 완료.**
남은 F-11(DECISION_LOG 문구 정정)·F-12(보고서 파일명 규칙)는 코드 변경이 아니다.

### 2026-08-18(화) 장중에 볼 것 — **확정 조건이 이제 존재한다**

- [ ] **W-21 ★ W-2 확정** `AggregatorNoContribution` 1건 이상 관측 → 여섯 갈래 중
      무엇인지 **즉시 확정**. `no_contribution_reasons` 집계도 함께 본다.
      **예측을 먼저 적는다**: `blocked_by_uncertainty`가 지배적일 것이다(30m `nan_ratio`가
      임계의 3배였다). 아니면 그 가설이 틀린 것이고 그 자체가 수확이다.
      **단 F-1이 들어 웜스타트가 200봉을 채우면 `nan_ratio`가 내려가 이 태그가 아예 안 뜰
      수도 있다** — 그 경우도 답이다(원인이 웜스타트였다는 뜻).
- [ ] **W-22** `OptionChainPolled` — 09:00 이후 3계열(regular·weekly_mon·weekly_thu)
      전부 등장 · `published == legs`(부분 실패 0)
- [ ] **W-37 (신규)** `unmeasured`에 "옵션체인 성공 사이클" 항목이 **없어야 한다**
      (F-6 적용 후 첫 정상일이므로 `OptionChainPolled`가 0이 아니어야 한다)

### 남은 것 — 코드

- [ ] **런타임 후방조정** — F-1의 스플라이스는 무조정이라 학습(back-adjust)과 추론이
      롤 경계에서 어긋난다. **선행: G-1 실동작(2026-09-10 만기일).** 그 전엔 조정할
      basis 자체가 없다.
- [ ] **포맷 비준수 6파일** — `models/score_calibration.py` + 테스트 5종. 고도화 커밋에서
      의도적으로 제외했다(무관한 포맷 변경이 섞이면 설계 변경과 잡음을 못 가른다).
- [ ] **F-11** DECISION_LOG의 08-14 G-1 근거 문구 정정("30m 회복 0.0%p" → 실측 곡선)
- [ ] **F-12** 같은 날 같은 국면 2회 점검 시 보고서 파일명 규칙(스킬 파일)
