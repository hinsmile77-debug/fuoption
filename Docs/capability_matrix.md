# Capability Matrix — 브로커 어댑터 {구현, 실측} × {모의, 실전}

> "구현됨 ≠ 검증됨" (messiah/broker/base.py 원칙). 이 표는 각 기능이 코드로 존재하는지와,
> 실제 KIS 서버로 호출해 확인했는지를 분리해서 추적한다. 행 추가 시 실측 날짜·계좌·근거
> (커밋/스크립트 요약)를 반드시 남긴다 — "될 것 같다"는 실측이 아니다.

범례: 구현 = 코드 존재 / 실측 = 실제 서버 호출로 응답 확인 / — = 해당 없음(아직 시도 안 함)

## KIS (src/messiah/broker/kis/)

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| 토큰 발급 (token_daemon) | ✅ | ✅ 2026-07-21 | — | commit 3c6c9e3 |
| get_investor_flow | ✅ | ✅ 2026-07-21 | — | commit 3c6c9e3 |
| get_balance | ✅ | ✅ 2026-07-21 | — | commit 3c6c9e3. MGNA_DVSN 등 Required 필드 누락 버그 발견·수정 |
| get_quote | ✅ | ✅ 2026-07-22 | — | 미니선물 A05608(F 202608) 조회 성공, rt_cd=0 |
| get_asking_price | ✅ | ✅ 2026-07-22 | — | 5단계 호가 확인, 틱 크기 0.02 실측(호가 간격 역산) |
| submit_order (rest_client) | ✅ | ✅ 2026-07-22 | — | KISBrokerAdapter.submit() 경유로 실측(아래) |
| cancel_order (rest_client) | ✅ | ✅ 2026-07-22 | — | 신규 구현(마흐디에 없던 기능) — KISBrokerAdapter.cancel() 경유로 실측(아래) |
| KISBrokerAdapter.connect/close | ✅ | ✅ 2026-07-22 | — | |
| KISBrokerAdapter.submit | ✅ | ✅ 2026-07-22 | — | 미니선물 A05608 BUY 지정가(체결 불가능한 깊은 가격 1000.00, 최우선매수호가 1131.04 대비 -131pt), qty=1 → SubmitResult(ok=True, broker_order_no='0000009623') |
| KISBrokerAdapter.cancel | ✅ | ✅ 2026-07-22 | — | 위 주문 즉시 전량취소 → True, 이후 positions/account 변동 없음 확인(미체결 확정) |
| KISBrokerAdapter.positions | ✅ | ✅ 2026-07-22 | — | 빈 계좌 기준 확인(0건 반환) — 실제 보유 잔고가 있는 상태에서의 부호/필드 파싱은 아직 실측 안 됨 |
| KISBrokerAdapter.account | ✅ | ✅ 2026-07-22 | — | cash=50,000,000 (파생상품 계좌 개설 시 기본값과 일치), margin_used/total_equity 필드 존재 확인 |
| KISBrokerAdapter.probe_front_month | ✅ | ✅ 2026-07-22 | — | 실제 마스터파일 URL로 end-to-end 실행: K200_MINI_FUT→A05608, K200_FUT→A01609(둘 다 이전 세션 futures() 직접 조회 결과와 일치). 재호출 시 마스터 캐시 재사용 확인. K200_OPT/미지원 문자열은 의도대로 ValueError. 계좌·토큰 무관(정적 파일 다운로드)이라 모의/실전 구분 없음 |
| symbol_master (parse/futures/options/nearest_expiry_chain/option_symbol) | ✅ | ✅ 2026-07-22 (전체) | — | 마흐디에서 이식(pandas→polars, 미니선물 "B" 추가, 선물 월물랭크 필드 수정). futures 경로는 probe_front_month()로(위 행), 옵션 경로(options/nearest_expiry_chain/option_symbol)는 실제 마스터파일로 regular(콜 390·풋 390, 만기 202608)·weekly_mon(116/116)·weekly_thu(150/150) 체인 조회 확인. mini(D/E)는 이 시점 상장 없음(0/0, series 자체는 정상 동작 — 그냥 해당 상품이 없음). option_symbol() 재조회 일치·미상장 행사가 None 확인. 체인에서 뽑은 종목코드(B01608A46, strike 1112.5)로 get_quote() 실호출 → rt_cd=0, 체결가 101.45 확인 — 내부 일관성뿐 아니라 실제 거래 가능한 코드임을 검증 |
| WS 시세 구독 (ws_client) | ✅ | ✅ 2026-07-22 | — | ApprovalKeyIssuer.issue()로 실제 접속키 발급 성공. 실제 WS 서버(REAL_WS_DOMAIN)에 연결해 미니선물 근월물(A05608) H0IFCNT0(실시간체결가) 구독 → 5건 수신: 1번째는 JSON 구독응답("SUBSCRIBE SUCCESS"), 이후 4건은 파이프구분 실시간 틱(0\|H0IFCNT0\|001\|A05608^152953^...) — listen()의 "첫 글자가 {면 JSON, 아니면 파이프구분" 분기가 실제로 맞음을 확인. 틱의 HHMMSS 필드가 수신 당시 KST 벽시계와 일치 — 실시간 지연 없음 확인. 구독 해제까지 정상 |
| WS 주문체결통보 | ✅ | — | — | 포트만 완료, 실측 안 됨 — 계좌별 TR_ID 분리(H0IFCNI0/H0IFCNI9)라 시세 구독과 별도 검증 필요 |
| RedisRateLimiter (공유 유량 예산) | ✅ | ✅ 2026-07-22 | — | 자체 로직은 messiah-redis로 테스트 8건(스레드 동시성 포함) 실행. 이어서 실제 KISRestClient(rate_limiter=RedisRateLimiter(...))로 get_balance() 3연속 호출 → 전부 rt_cd=0, 호출 간격 2.61s·2.75s(최소 1.0s 이상 — 페이싱 위반 없음) 확인 |
| RedisTokenDaemon (Access Token 공유 캐시) | ✅ | ✅ 2026-07-22 | — | 자체 로직은 messiah-redis로 테스트 6건(mock KIS) 실행. 이어서 진짜 별도 OS 프로세스 두 개(스레드가 아니라 실제 `python ...` 두 번 동시 실행)로 실계좌 토큰 캐시를 재현 — 실제 KIS 발급 호출(`*** 실제 KIS 발급 호출 시작/끝 ***` 로그)은 한쪽 프로세스에서만 한 번 발생(0.09s), 다른 프로세스는 발급을 시도하지 않고 캐시 폴링만으로 동일 토큰을 받음(get_cached 2회 호출) — 지난 세션에 실제로 겪은 "프로세스 두 개→403" 문제가 이 컴포넌트로 해결됨을 실제 KIS 서버로 확인 |

## L1 Data (src/messiah/data/) — Master Plan Ver 2.0 §9 "Collector·Normalizer·Archiver"

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| normalizer.parse_futures_tick | ✅ | ✅ 2026-07-23 | — | 단위 테스트(실캡처 프레임 픽스처)에 이어 TickCollector 경유로 실제 WS 스트림 20초 구독 → 실틱 64건 정상 파싱·집계 확인(아래 TickCollector 행) |
| normalizer.parse_option_tick | ✅ | ✅ 2026-07-23 | — | 위클리 목요일물(만기 당일) 근월 풋 C09F7WA45 구독 → 실틱 70건 이상 확보, symbol/시각/가격/거래량 전부 원시 프레임과 파싱 결과를 직접 대조해 확인(정규월물은 이 세션 거래량이 너무 얇아 시도했으나 틱을 못 잡음 — normalizer.py 모듈 docstring 참고) |
| normalizer.MinuteBarAggregator | ✅ | ✅ 2026-07-23 | — | 단위 테스트에 이어 실제 틱 스트림으로 1분봉 2개 완성 확인(아래 TickCollector 행) |
| archiver.ParquetArchiver | ✅ | ✅ 2026-07-23 | — | 단위 테스트에 이어 실제 완성봉을 실제로 Parquet에 적재·재읽기까지 확인(아래 TickCollector 행). **Windows에 tzdata 패키지 필수**(2026-07-22 실측 발견 — polars가 tz-aware datetime을 Parquet 왕복시킬 때 zoneinfo로 "UTC" 존을 찾는데 Windows는 시스템 tzdata가 없어 ZoneInfoNotFoundError; pyproject.toml에 `sys_platform=='win32'` 조건부 의존성으로 추가함). bar_open_kst는 폴라스 내부 정규화로 파일엔 UTC로 찍히지만(예: KST 14:08 → UTC 05:08) 실제 시점은 정확 — 표시상 혼동 주의 |
| collector.TickCollector | ✅ | ✅ 2026-07-23 | — | 실제 KIS 서버로 end-to-end 실측: approval_key 발급→WS 연결→미니선물 근월(A05608) 구독→20초간 실틱 64건 수신→Normalizer 파싱→1분봉 2개 완성(quality_ok 둘 다 true, 거래량 73·30건)→Archiver 적재→**Redis 버스(bus.publish)로 실제 발행까지 확인**(별도 실제 구독자가 Tick 메시지 64건을 실시간으로 수신 — bus.py의 실제 Redis 연동이 이번 세션 최초로 실측됨). CollectorProcessingError 로그 0건(적재·발행 전부 성공) |
| collector.TickCollector.run_forever (WS 재연결) | ✅ | ✅ 2026-07-23 | — | run_once()를 감싸는 지수 백오프 재연결 래퍼 신규 구현(mahdi run_observation_loop_forever와 동일 설계) — 단위 테스트 8건(연결 자체 실패·구독 후 즉시 단절·백오프 배증/리셋 전부 mock으로 커버)에 이어 실제 KIS WS로도 검증: A05608 구독 후 90초 실행 중 t=30s에 실제 연결을 강제 종료(`conn.close()`) → CollectorWSDisconnected 로깅(3초 백오프) → 3초 후 실제 재연결 성공(approval_key 재발급 포함) → CollectorWSReconnected 로깅 → 수신 재개, 전후 합계 257틱, 재연결에 걸친 1분봉도 quality_ok=true로 정상 적재(재연결 시 그 분의 미완성 데이터는 설계대로 폐기 — collector.py docstring 참고). 별도로 60초 연속 무단절 실행도 확인(장시간 연결 유지가 최소 이 구간에서는 문제 없음) |

## 알려진 갭

- **포지션 보유 상태에서의 `positions()` 파싱 미검증**: 이번 실측은 빈 계좌라 `output1`이 빈 배열이었다.
  실제 보유 종목이 있을 때 `sll_buy_dvsn_name`(BUY/SLL/매수/매도) 값, `cblc_qty`·`ccld_avg_unpr1`
  필드가 문서 그대로인지는 다음 포지션 보유 시점에 재확인 필요.
- **부분체결·실제 체결 흐름 미검증**: 이번 주문은 의도적으로 체결 불가능한 가격이었다. 실제 체결이
  일어났을 때 `Fill` 이벤트 연계(Order State Machine)는 별도 검증 대상.
- **미니선물 상품종류 코드 "B" 신규 발견 (2026-07-22)**: 마흐디 `symbol_master.py`는 상품종류="1"
  (정규선물)만 알고 있었음 — 실제 KIS 종목코드 마스터파일(`fo_idx_code_mts.mst`)에는 미니선물이
  상품종류="B"(한글종목명 "미니F 202608" 등)로 별도 존재한다. 월물 랭크는 마흐디가 "월물구분코드"
  (필드 index 4)로 참조하던 자리가 선물 행에서는 항상 공란이고, 실제 랭크(1=근월,2=차근월,...)는
  그 다음 필드(index 6, 마흐디가 "ATM구분"으로 이름 붙인 옵션 전용 컬럼)에 들어있음. MESSIAH가
  symbol_master를 이식할 때 이 두 가지를 반영해야 함(NEXT_TODO 참고).
- **틱 크기(tick_size) 하드코딩 안 됨**: KISBrokerAdapter 생성자가 `tick_size: Decimal`을 요구한다.
  2026-07-22 실측으로 미니선물(A05608)의 틱 크기가 0.02임을 확인했으나(호가 5단계 간격), 옵션·타
  근월물에도 동일하다고 가정하지 말 것 — 상품·행사가 구간별 실측 필요.
- ~~symbol_master 옵션 체인 경로 미실측~~ — 2026-07-22 실측 완료(위 행). 마흐디가 실측한 필드
  배치(월물구분코드="2" 고정, ATM구분 대부분 공란)를 그대로 믿고 이식했는데, 실제로도 문제없이
  동작함을 확인(그 두 필드는 필터링/정렬에 안 쓰여서 원래 예상대로 영향 없었음).
- ~~위클리 옵션의 요일 대응(N/O=월요일, L/M=목요일) 재검증 안 됨~~ — 2026-07-22 재검증 완료.
  마흐디의 2026-07-10 단일 관측(대시보드 표시명 교차확인)과 다른 날짜·다른 방법(symbol_master
  nearest_expiry_chain()의 실제 종목코드로 get_quote() 호출 → futs_last_tr_date를 직접 읽어
  Python으로 요일 계산)으로 재확인 — weekly_mon(N/O) 근월물 만기 20260727(월요일), weekly_thu
  (L/M) 근월물 만기 20260723(목요일) — 둘 다 마흐디 매핑과 일치.
- ~~RedisRateLimiter/RedisTokenDaemon을 KISRestClient 실주문 경로에 물려본 적 없음~~ — 2026-07-22
  실측 완료(위 두 행). 다만 아직 시험한 건 진짜 별도 프로세스 2개(토큰)와 단일 프로세스 연속
  호출(레이트리미터)뿐 — 3개 이상 프로세스가 동시에 붙는 시나리오나 장시간(수 시간) 운영 중
  Redis 재연결·TTL 만료 경계 상황은 아직 실측 안 됨.
- ~~WS 실시간 틱 원시 필드 파싱 미구현~~ — 2026-07-22 완료(위 "L1 Data" 표 참고,
  normalizer.parse_futures_tick/parse_option_tick). `iv`/`key`(구독응답에 포함)가 정말
  encrypt="Y" TR 전용 복호화 키인지는 여전히 추정 — 체결통보 등 encrypt="Y" TR 실측 시 확인 필요.
- ~~WS 재연결 미구현~~ — 2026-07-23 완료. TickCollector.run_forever() 신규 구현(위 "L1 Data" 표
  참고) — run_once()는 여전히 "연결 하나가 끊기면 예외를 던지고 끝"이지만, run_forever()가
  이를 감싸 지수 백오프(5→60초, mahdi run_observation_loop_forever와 동일 설계)로 재연결한다.
  실제 KIS 서버로 강제 단절→재연결 성공까지 실측 완료. 남은 갭: PING/PONG 명시적 응답 로직은
  여전히 없음(연결이 살아있는 한 KIS 서버가 자체적으로 관리하는 것으로 보이나 별도 확인 안 됨),
  수 시간 단위 초장기 연결 유지·다회 반복 재연결(3회 이상 연속)은 미검증(이번엔 최대 1회
  강제 단절만 실측).
- **동시에 여러 WS 연결을 열면 서로 반복적으로 끊김 (2026-07-23 신규 발견)**: 같은 계좌/
  앱키로 TickCollector 두 개(선물 1개 + 옵션 1개)를 동시에 각자의 WS 연결로 실행했더니 양쪽
  다 "no close frame received or sent"로 몇 초 간격으로 반복 단절됨(run_forever()가 계속
  재연결을 시도하며 루프). 같은 두 심볼을 각각 **단독으로** 연결했을 때는(순차 실행) 60~180초
  동안 단 한 번도 끊기지 않아 원인이 동시 연결 자체에 있음을 확인 — 정확한 서버 측 메커니즘은
  미확인(approval_key 재발급이 같은 계좌의 다른 세션을 무효화하는 것으로 추정)이지만, 결론은
  명확함: **여러 종목을 동시에 실시간 구독하려면 TickCollector(연결)를 여러 개 띄우지 말고,
  연결 하나(KISWebSocketClient 하나)에서 subscribe()를 여러 번 호출**해야 한다 — 이는
  ws_client.py가 애초에 "세션당 구독 슬롯 최대 41건"으로 설계된 이유와도 일치한다. ATM±N
  옵션 체인 구독 롤링(아래 항목)을 구현할 때 이 제약을 그대로 따라야 함.
- **ATM±N 옵션 체인 구독 롤링 미구현**: mahdi의 RollingSubscriptionManager(스팟 추종 옵션 체인
  WS 구독 롤링)를 이식하지 않음 — TickCollector는 생성 시 주어진 심볼 1개만 구독한다.
- **REST 폴링 루프(투자자매매동향·옵션체인 그릭스) 미구현**: FixedTickScheduler를 아직 아무
  실제 폴러에도 물려보지 않음 — 옵션 체인 구독 롤링과 함께 별도 작업으로 남김.
- **Event Calendar(KRX 휴장일 인식) 미구현**: L1 DATA 다이어그램(Master Plan §9)의 구성요소
  중 하나지만 Collector/Normalizer/Archiver의 정확성과 독립적인 별개 관심사라 이번엔 다루지
  않음.
- **원시 틱 자체는 Parquet에 안 쌓임**: ParquetArchiver는 완성봉(BarClosed)만 적재한다.
  Digital Twin(W9-11)의 "호가 기반 체결" 재생은 호가(orderbook) WS(H0IFASP0 등, 아직 미검증)가
  필요해 지금 원시 틱 적재 스키마를 결정하기엔 이르다고 판단해 미룸.
- ~~TickCollector를 실제 KIS WS로 end-to-end 돌려본 적 없음~~ — 2026-07-23 완료(위 "L1 Data"
  표 참고) — approval_key 발급→WS 연결→실틱 64건 수신→1분봉 2개 완성→Archiver 적재→Redis 버스
  발행까지 전부 실측, 버그 없음.
- ~~옵션 WS 경로(H0IOCNT0) 미검증~~ — 2026-07-23 완료(위 "L1 Data" 표 참고). 정규월물은 이
  세션 시점 거래량이 너무 얇아(당일 누적 0~23건, 여러 종목·최대 3분 구독 시도) 틱을 못 잡음
  — 위클리 목요일물(만기 당일이라 거래량 폭증, 당일 누적 최대 12,464건)로 전환해 45초 만에
  실틱 70건 이상 확보, 필드 인덱스 검증 완료.
- **장시간(수 시간) 운영·거래량 급증(거래대금 상위 구간) 미검증**: 이번 세션 최장 연속
  구간은 180초(정규옵션, 무단절)·90초(선물, 강제 단절 1회 포함)로 확장했으나(기존 20초에서),
  여전히 "장시간"이라 부를 수준은 아니고, 오늘 관측한 거래량도 평상 수준(위클리 만기일
  제외)이라 실제 급증(장 시작 직후·지수 급변동 등) 구간의 처리량/안정성은 미검증.
