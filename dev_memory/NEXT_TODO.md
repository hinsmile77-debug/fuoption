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

- [x] labeling.py + cv.py 신규, px_core.py ATR 공개화 (2026-07-27 완료) —
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
      신규 (2026-07-27 완료) —
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
      models/trainer.py 확장 (2026-07-28 완료) —
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
      (2026-07-29 완료) —
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
      (2026-07-29 완료) —
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

## 등록된 관찰 항목 (분기회의)

- [ ] 키움 신 REST의 국내 선물옵션 확장 발표 여부 (발표 시 브로커 랭킹 재평가)
- [ ] KRX 야간 파생시장 API 지원 현황 (KIS·LS)
