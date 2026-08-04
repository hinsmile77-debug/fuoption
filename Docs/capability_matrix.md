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
| bar_composer.MultiHorizonBarComposer | ✅ | ✅ 2026-07-23 | — | 신규 구현(W6~8) — 1분봉을 구독해 3/5/10/15/30분봉을 합성. 봉 확정은 FixedTickScheduler(이미 검증됨)로 절대시각 경계+500ms 유예 기반(완성봉 규율, Ver 1.2 §2.2). 단위 테스트 12건(OHLCV 합성 정확성·결측 분 시 quality_ok=false·복수 Horizon 독립 누적·적재/발행 실패 격리) 전부 mock. 실측: 오늘 세션에서 실제 KIS WS로 캡처해뒀던 진짜 1분봉 7건(diag_archive~3, 서로 다른 시점이라 갭 있음)을 그대로 흘려 3분봉·5분봉 합성 → OHLCV 정상 합성, 갭 때문에 quality_ok=false로 정확히 표시됨(설계대로) |
| archiver.ParquetArchiver 경로 버그 수정 | ✅ | ✅ 2026-07-23 | — | 2026-07-23 발견: 경로·dedup 키에 horizon이 없어 서로 다른 Horizon의 봉이 같은 bar_open_kst를 가지면(예: 5m봉과 1m봉이 둘 다 09:30:00 시작) 서로를 지우는 사고가 날 뻔했음(M1만 있을 때는 드러나지 않던 문제) — 경로를 `{symbol}/{horizon}/{date}.parquet`로, dedup 키에 horizon 추가. 회귀 테스트 신규(다른 Horizon 분리 확인) |

## L2 Feature (src/messiah/features/) — Master Plan Ver 2.0 §9 W6~8, Ver 1.4 §2.2

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| px_core — PX(가격·추세·모멘텀) 기저 30개 | ✅ | ✅ 2026-07-23 | — | 신규 구현. **MS(마이크로구조) 30개는 이번 스코프 밖**(아래 "알려진 갭" 참고) — PX만 우선 구현(전부 완성봉 OHLCV만으로 계산 가능). 단위 테스트 53건: 손으로 검산한 값 8개(px_ret/mom/accel/zscore/bb_pos·width/stoch·don_pos/high·low_dist/dd·runup/max_ret), 방향성 검증(RSI 단조추세=0/50/100, ADX 추세>횡보, EMA 교차 부호, MACD 등), 워밍업 부족 시 None 반환 전수 검증. **버그 발견·수정**: px_hurst의 R/S 회귀가 log(size) 실값이 아니라 등간격 인덱스로 회귀해 기울기가 왜곡되던 버그 — 별도 페어 (x,y) 회귀 헬퍼(`_linreg_xy`)로 분리해 수정(수정 전엔 추세 시계열의 Hurst가 평균회귀 시계열보다도 낮게 나왔음, 실측 중 발견) |
| features.engine.FeatureEngine | ✅ | ✅ 2026-07-24 | — | 신규 구현 — `bar.{h}.{symbol}` 구독→Horizon별 롤링윈도우(deque, maxlen=130)→px_core 30개 계산→FeatureVector 조립·발행(`feat.{h}.{symbol}`). 개별 Feature 계산 실패는 그 값만 None(전체 발행은 안 죽음), 발행 실패는 FeaturePublishError(ERROR)로 로깅 후 계속(L22). 세션 상태(당일 시가/고저, px_gap_open 등 4개 상태형)는 M1 봉으로만 갱신. 단위 테스트 13건. 실측: 오늘 실제 캡처한 진짜 1분봉을 bar_composer가 합성한 실제 3/5분봉과 함께 흘려 실제 FeatureVector 발행 확인 — 데이터가 7건뿐이라 대부분 워밍업 미달(nan_ratio 93~94%)이었지만, 워밍업 조건을 채운 px_ret_5/px_mom_5는 실제 가격 변동과 일치하는 값을 정상 산출(예: -0.0035 근방의 실제 소폭 하락). **개선(2026-07-24, 실제 운영 로그 리뷰 중 발견)**: FeatureNaN(WARNING) 로깅이 워밍업 중(예: 30m은 최대 윈도우를 채우는 데만 30시간)에도 매 봉마다 찍혀 agenda.py의 주간 경보 집계가 잡음에 파묻힐 뻔함 — `len(history) >= _MAX_HISTORY`("워밍업이 끝났어야 할 시점")를 넘긴 뒤에도 nan_ratio가 여전히 높을 때만 경고하도록 수정, 회귀 테스트 추가 |

## Digital Twin (src/messiah/simulator/, src/messiah/broker/simulator/) — Master Plan Ver 2.0 §9 W9~11

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| simulator.replay.ParquetBarReplaySource | ✅ | ✅ 2026-07-26 | — | 아카이브된 전 Horizon 완성봉을 확정시각(bar_open+horizon초, 동률 시 짧은 Horizon 우선) 순으로 정렬해 재생 시퀀스로 반환. 단위 테스트 5건(정렬·날짜 공백 스킵·심볼 없음·복수일 연속·Horizon 필터). 실측: 실제 2026-07-24 아카이브(`data/bars/A05608/*/2026-07-24.parquet`, 6개 Horizon 총 60행)로 `scripts/run_replay.py` 실행 — 로드·정렬 정상 |
| simulator.inprocess_bus.InProcessBus | ✅ | ✅ 2026-07-26 | — | `core.bus.MessageBus`와 같은 `publish/subscribe` 시그니처의 인메모리 버스(Redis 불필요) — Ver 1.0.1 §2.1 "동일 인터페이스" 원칙 실현. FeatureEngine을 코드 변경 없이 그대로 재사용해 검증(아래 DigitalTwinEngine 실측에 포함). 단위 테스트 4건 |
| broker.simulator.adapter.SimBroker (재작성) | ✅ | ✅ 2026-07-26 | — | 기존 "즉시체결" 골격을 pending 지정가·TTL·1분봉 터치 체결·시장가 슬리피지 모델로 재작성(모듈 docstring에 근거 상세 기록). **체결 판정은 1분봉으로만** 한다(호가 WS 미구독 — 알려진 갭 참고). 단위 테스트 10건(제출 전 거부·시장가 슬리피지·지정가 터치 체결 매수/매도·TTL 만료·취소·굵은 Horizon 무시·EXIT_FULL·qty 검증). 계약 변경으로 기존 `tests/test_core_w1.py` OrderGateway 테스트 2건이 "봉 1개로 시계 프라이밍" 필요하게 바뀜(반영 완료, 회귀 없음) |
| simulator.engine.DigitalTwinEngine | ✅ | ✅ 2026-07-26 | — | 재생봉 → InProcessBus 발행 → SimBroker.on_bar() 체결 판정 → OrderGateway.on_fill() 순으로 묶는 오케스트레이터. 단위 테스트 4건(버스 발행·심볼 필터링·지정가 체결이 실제 포지션까지 반영·게이트웨이 우회 주문의 미매칭 체결이 실제로 CRITICAL 정지시킴 — L1 안전장치가 재생 경로에서도 살아있음을 확인) |
| scripts/run_replay.py — 수동 스모크 진입점 | ✅ | ✅ 2026-07-26 | — | 실제 아카이브(A05608, 2026-07-24, 60행)로 전체 배선 end-to-end 실행: 재생 → FeatureEngine이 Horizon별 FeatureVector 발행(1m 33건·3m 11건·5m 7건·10m 5건·15m 3건·30m 1건 — 실제 아카이브 행 수와 일치) → 데모 시장가 주문 1건 제출·체결 → 최종 포지션(qty=1)·계좌·게이트웨이 정지 여부 출력까지 버그 없이 1회 성공 |

## 레이블링·CV (src/messiah/models/) — Master Plan Ver 1.2 §3·§8.2, Ver 2.0 §9 W12~13

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| models.labeling.triple_barrier_labels | ✅ | ✅ 2026-07-26 | — | Ver 1.2 §3.2 Horizon별 표(시간배리어·ATR 폭 배수)를 그대로 인코딩. ATR은 `features.px_core.atr`(이번에 공개 전환)을 재사용 — 중복 구현 없음. 동일 봉에서 상/하단 동시 터치 시 상단 우선(결정론적 타이브레이크, 모듈 docstring 근거 기록), 비용반영 강등(`cost_ticks`, Cost Model v1 나오기 전 호출자 전달 임시값), 워밍업·꼬리 트림. 단위 테스트 9건(상단/하단 터치·시간배리어·동시터치 타이브레이크·비용강등·워밍업부족·꼬리부족·심볼혼입 거부 — 전부 손으로 계산한 ATR/배리어 값 기준 known-value). 실측: 실제 2026-07-24 아카이브(A05608, 1m, 33행)로 `scripts/run_labeling_smoke.py` 실행 — 레이블 16건 생성(−1: 6·+1: 10), 버그 없음 |
| models.labeling.compute_uniqueness | ✅ | ✅ 2026-07-26 | — | Lopez de Prado(2018) 평균 고유도. 격자점은 전체 레이블의 t_start∪t_end(동시성이 바뀔 수 있는 지점은 구간 경계뿐이므로 정확한 격자 — t_start만 쓰면 시계열 꼬리에서 과소평가되는 버그를 known-value 테스트 작성 중 직접 발견·수정). 단위 테스트 3건(손으로 계산한 3이벤트 겹침 사례 A=0.75/B=0.75/C=1.0·안 겹치는 경우 전부 1.0·빈 입력) + 실제 생성 레이블 통합 테스트(가중치 (0,1] 범위·겹침으로 인한 감쇠 확인) |
| models.cv.PurgedKFold | ✅ | ✅ 2026-07-26 | — | de Prado(2018) Ch.7 표준 알고리즘(순수 Python, numpy 의존성 없음 — Optuna 탐색용 "Purged 5-Fold", Ver 1.6 §2.2). 폴드는 시간순 연속 구간, 겹치는 학습 샘플 제거(purge) + 경계 인접 샘플 추가 제외(embargo, 인덱스 단위). 단위 테스트 7건(균등분할 아닐 때 폴드 크기·전 인덱스가 정확히 한 번씩 test로 나뉘는지·purge가 구간 겹침 학습샘플을 실제로 제거하는지·embargo가 겹침 없어도 경계 인접분을 제거하는지·잘못된 n_splits/embargo 거부) |
| models.cv.WalkForwardSplitter | ✅ | ✅ 2026-07-26 | — | Ver 1.2 §8.2 "학습 6개월/검증 1개월, 1개월씩 전진" 스킴을 달력일 파라미터(train_days/test_days/embargo_days/step_days)로 일반화. Purge(배리어가 검증 구간을 침범하는 학습 샘플 제거) + Embargo(검증 직전 N일 추가 제외) 둘 다 구현. 단위 테스트 8건(빈 입력·롤링 창 개수·첫 창의 train/test 정확한 소속(embargo 반영)·검증 구간을 침범하는 장기 배리어 purge·기본 step=test_days·커스텀 step_days·잘못된 창 크기 거부) — 전부 30~60일 합성 데이터 기준(실제 아카이브가 하루치뿐이라 달력 롤링을 의미 있게 재현할 데이터가 없음, 아래 "알려진 갭" 참고) |
| scripts/run_labeling_smoke.py | ✅ | ✅ 2026-07-26 | — | 실제 2026-07-24 아카이브로 레이블링+고유도+PurgedKFold 전체 배선 end-to-end 실행 확인(위 행들 참고) |

## Cost Model·5m Expert 프로토타입·Trainer·Validator (Ver 2.0 §9 W14~16)

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| risk.cost_model.CostModel | ✅ | ✅ 2026-07-26 | — | Ver 1.1 §4-1 4요소(수수료+세금+슬리피지+시장충격) 구조 구현. 시장충격은 완성봉의 실제 volume 필드로 계산(구조적으로 정확), 슬리피지는 호가 WS 미구독으로 `expected_spread_ticks` 설정값 근사(알려진 갭). 단위 테스트 10건(편도/왕복 계산·시장충격 비례성·유동성 없을 때 폴백·qty 검증·봉 이력 평균거래량 근사·커스텀 설정·CostEstimate 덧셈, 전부 손으로 계산한 값 기준) |
| models.labeling.triple_barrier_labels/label_and_weight cost_ticks 결선 | ✅ | ✅ 2026-07-26 | — | `cost_ticks: int`(W12~13 임시값)를 `float`로 확장해 `CostModel.estimate_round_trip_from_bars(...).total_ticks`를 그대로 받을 수 있게 함(`models/trainer.py`가 실제 연결부). 기존 12개 테스트 회귀 없음 |
| strategy.futures.expert.HorizonExpert | ✅ | ✅ 2026-07-26 | — | Ver 1.2 §9 스켈레톤의 5m 프로토타입 1호 — 단일 LightGBM 3-class 분류기(미니 앙상블·Meta-Labeler·Isotonic 교정·Optuna 탐색은 전부 W17~19 "정식" 스코프, 모듈 docstring에 명기). `core/logging.py`에 W1부터 등록만 되고 미사용이던 `FeatureSetMismatch` 태그를 `predict()`에서 처음 실사용(추론 시점 feature_set 불일치 시 ERROR 로그+예외). 단위 테스트 7건(학습·예측 확률분포 검증·플레이스홀더 필드 확인·feature_set 불일치 예외·feature_row NaN 매핑·top_features 정렬·저장/재로드 왕복 동일 예측·커스텀 파라미터) |
| models.trainer.build_feature_vectors/build_training_data/train_prototype_expert | ✅ | ✅ 2026-07-26 | — | Ver 1.6 §7.1 파이프라인 1~3단계(데이터준비·레이블생성·학습)만 구현([4]교정 [5]번들패키징 [6]Validator제출 자동화는 W17~19). 봉을 실제 운영과 동일한 FeatureEngine(simulator.InProcessBus 재사용)에 직접 흘려 FeatureVector를 얻어 재현성 보장. CostModel→label_and_weight 실제 결선, 클래스불균형(inverse-frequency)×고유도 가중치 조립. 단위 테스트 12건 |
| models.validator.Validator | ✅ | ✅ 2026-07-26 | — | Ver 1.2 §8.3 성과 관문 3종(Deflated Sharpe 제외 — 알려진 갭) + Ver 1.6 §8 추가검사 4종(교정 Brier·Feature 의존도·추론지연·직렬화 왕복) 전부 구현. 성과 관문은 이미 계산된 시계열을 입력받는 순수 오케스트레이션(실제 walk-forward 백테스트 루프는 W17~19 이후, 알려진 갭). 모델 자체 검사 4종은 이번 주 프로토타입으로 바로 실행 가능함을 확인. 단위 테스트 14건(GateResult/ValidationReport 집계·성과 관문 3종 pass/fail 경계·교정 pass/fail·Feature 의존도 pass/fail(경계 비교 로직)·지연 pass/fail·직렬화·validate_all 7관문 조립) |
| models.metrics (sharpe_ratio/max_drawdown/negative_window_ratio/multiclass_brier_score) | ✅ | ✅ 2026-07-26 | — | 전부 순수 함수, Validator·향후 Self Evaluation(Phase 5) 재사용 가능하게 labeling.py에 의존하지 않음. 단위 테스트 15건 전부 손으로 계산한 known-value 기준(R16) |
| core.bus.BusLike (Protocol) | ✅ | ✅ 2026-07-26 | — | `models/trainer.py`가 `FeatureEngine`에 `simulator.InProcessBus`를 넘기면서 pyright가 처음으로 "MessageBus 구체클래스와 불일치" 오류를 냄(런타임은 이미 정상 동작 중이었음 — W9~11부터 `scripts/run_replay.py`가 같은 패턴을 썼지만 scripts/는 pyright 검사 대상 밖이라 안 드러났었음). `publish`/`subscribe`만 요구하는 Protocol을 신설해 `FeatureEngine.__init__`의 `bus` 타입힌트를 이걸로 교체 — 런타임 동작 변화 없이 타입 수준에서도 "동일 인터페이스"(Ver 1.0.1 §2.1) 원칙을 명시 |
| scripts/run_expert_training_smoke.py | ✅ | ✅ 2026-07-26 | — | 실제 2026-07-24 아카이브(A05608, 5m, 7행)로 Trainer→HorizonExpert→Validator(모델 검사 3개 관문) end-to-end 실행 확인. 성과 관문·교정 관문은 의도적으로 생략(스크립트 docstring — 백테스트 인프라 부재·홀드아웃 데이터 없음) |

## lightgbm 4.7.0 Windows 휠 크래시 (2026-07-26, `ml` extras 상한 고정으로 해결)

`lightgbm==4.7.0` + `numpy==2.5.1` + Python 3.12(이 프로젝트 .venv) 조합에서 `lgb.Dataset`
생성이 **항상** `OSError: exception: access violation reading 0x0000000000000000`로 죽음 —
`lgb.Dataset(x, label=y).construct()`만으로도 재현(데이터 크기·내용 무관, 15행·500행 전부
동일 실패). `set_label` 단계에서 네이티브 DLL 호출이 널 포인터를 역참조하는 것으로 보이며,
numpy를 1.26으로 내리면 이번엔 이미 설치된 scipy(numpy 2.0+ 요구)가 깨져 별개
`AttributeError: module 'numpy' has no attribute 'long'`가 남 — 두 패키지가 서로 다른
numpy 메이저 버전을 요구하는 상태였음. `lightgbm==4.3.0`으로 내리자 동일 환경(numpy
2.5.1 유지)에서 학습·weight·feature_name·저장/재로드·feature_importance까지 전부 정상
동작 확인 — `pyproject.toml`의 `ml` extras를 `lightgbm>=4.3,<4.7`로 상한 고정, 이유와
재현 시나리오를 주석으로 남김. **다음 lightgbm 버전을 올리려는 사람은 반드시
`tests/strategy/futures/test_expert.py`(학습→예측→저장→재로드 전체 경로) 통과를 먼저
확인할 것** — 이 버그는 수치 결과가 아니라 프로세스 크래시라 테스트가 없으면 그냥
빌드/CI가 죽는다.

## 알려진 갭 (Cost Model·Expert·Validator, 2026-07-26)

- **Cost Model의 슬리피지·수수료·세금은 전부 미실측 placeholder**: `CostModelConfig`
  기본값(commission=0.3틱, tax=0.0, spread=1.0틱, impact_coefficient=2.0)은 실제 KIS
  수수료 체계·호가 스프레드를 측정한 값이 아니다(호가 WS 미구독 — 기존 갭과 동일 원인).
  Ver 2.0 §6 "체결 품질 기록 → Cost Model이 매주 자기 보정" 루프가 실제 체결 데이터로
  교정할 자리로 남겨둠.
- **5m Expert 프로토타입 1호는 예측력이 없다(의도된 스코프)**: 실측 아카이브가 하루치(7~33
  행)뿐이라 Ver 1.2 §8.1 "최소 확보 목표: 틱/호가 2년치"에 한참 못 미침 — 스모크 실행 결과
  Feature 의존도가 전부 0(트리가 유의미하게 못 갈라짐)이었던 것도 이 때문. 목적은 배관
  검증(Feature→Label→Train→Predict→Save/Load→Validate)이었고 그 목적은 달성했다.
- **미니 앙상블·Meta-Labeler·Isotonic 교정·Optuna 탐색 전부 미구현**: Ver 1.2 §4.2·Ver 1.6
  §2.2~2.3·§6이 명시한 "정식" 5m Expert 구성요소 — W17~19 스코프로 명시적으로 미룸
  (strategy/futures/expert.py 모듈 docstring).
- **Deflated Sharpe(시행 횟수 보정) 미구현**: Ver 1.2 §8.3 관문 중 유일하게 빠진 항목 —
  시행 횟수를 세는 Optuna 탐색 기반 정식 Trainer가 있어야 의미가 있다(W17~19 이후,
  models/metrics.py 모듈 docstring).
- **Validator의 성과 관문(Sharpe·MDD·창별 일관성)은 실제 walk-forward 백테스트로 실행된
  적 없다**: Digital Twin(W9~11)·Expert(W14~16)·Cost Model(W14~16)이 전부 갖춰졌지만
  이들을 엮어 실제 성과 시계열을 뽑는 백테스트 하니스 자체가 아직 없다 — 합성 데이터
  기준 known-value 테스트로 관문 계산 로직의 정확성만 증명해 뒀다(models/validator.py
  모듈 docstring).
- **HorizonExpert의 top_features/XAI는 전역 중요도(gain) 근사다**: 개별 예측별 로컬 기여도
  (SHAP 등)는 스코프 밖(strategy/futures/expert.py 모듈 docstring) — `decision.intent`의
  "근거 top5"(Ver 1.1 §3-4)가 실제로 필요해지는 Meta Decision Engine 구현 시점(W24~26)에
  재검토 대상.

## 5m Expert 정식(탐색·앙상블·교정) + Meta-Labeler (Ver 2.0 §9 W17~19)

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| models.search.search_hyperparameters | ✅ | ✅ 2026-07-26 | — | Ver 1.6 §2.2 탐색 공간(num_leaves/max_depth/min_data_in_leaf/learning_rate/feature_fraction/bagging_fraction/lambda_l1/l2) 원문 그대로 인코딩. Optuna(TPE) + `PurgedKFold`(W12~13 재사용)로 "창 내부 CV로만 탐색" 실제 구현 — objective=폴드 평균 multi_logloss. early_stopping은 이번 스코프 제외(알려진 갭). 단위 테스트 5건(커스텀 탐색공간 키/범위 검증·시드 고정 시 결정론성·퇴화 폴드(train/test 어느 한쪽 빔) 안전 처리·기본 탐색공간이 프로덕션 공간과 일치) |
| models.calibration.ProbabilityCalibrator | ✅ | ✅ 2026-07-26 | — | Ver 1.6 §6.1 — 클래스별 Isotonic Regression(one-vs-rest) + 재정규화. out-of-fold 데이터로 학습해야 한다는 제약을 모듈 docstring에 명기. 단위 테스트 3건(재정규화 합=1 확인·1차원 입력 처리·과신 보정 손계산 known-value — 동일 입력 x=0.9 반복 시 PAVA가 pooled 평균으로 수렴하는 성질 이용) |
| models.calibration.ConformalCalibrator | ✅ | ✅ 2026-07-26 (메커니즘만) | — | Ver 1.2 §6 / Ver 1.6 §6.2 — 비적합도 분위수 계산 메커니즘 구현·합성 데이터로 정확성 검증(known-value 6건: 비적합도 계산·분위수 산출·이력 없을 때 최대보수값·구간 클리핑·잘못된 alpha 거부). **실제 운영 이력 없음** — 매일 갱신되는 라이브/페이퍼 예측 로그가 전제인데 그 이력이 아직 없다(G2 페이퍼트레이딩 W39~40부터, 알려진 갭). 어떤 운영 루프에도 아직 안 붙어 있음 |
| strategy.futures.expert.HorizonExpert (앙상블+교정 재설계) | ✅ | ✅ 2026-07-26 | — | W14~16 단일모델 프로토타입을 Ver 1.6 §2.3 미니 앙상블(×5, seed만 다름)로 확장 + `ProbabilityCalibrator` 선택적 부착. `ens_std`는 Ver 1.2 §6 원문 그대로 "P(+1) 표준편차"로 계산(다른 클래스 아님). 저장/로드가 앙상블 멤버 다중 파일(`{stem}_e{i}.lgb`) + 메타데이터(`.json`) + 교정기(`.pkl`, sklearn 객체라 pickle — Ver 1.6 §9.1 번들 포맷과 동일 확장자)로 확장. 단위 테스트 15건(기본 앙상블 크기 5·커스텀 멤버수/시드·단일멤버 ens_std=0 확인·플레이스홀더 필드(meta_passed 항상 True 등)·빈 부스터 거부·feature_set 불일치·feature_row NaN 매핑·top_features 정렬·저장/로드 왕복(교정기 유무 양쪽)·교정기 부착 시 확률 실제로 바뀌는지·교정기 제거) |
| strategy.futures.meta_labeler.MetaLabeler | ✅ | ✅ 2026-07-26 | — | Ver 1.2 §5 / Ver 1.6 §5 — Horizon별 얕은 LightGBM(depth≤4, leaves≤15) 이진 분류기. 메타 Feature 5개(1차확률·마진·앙상블분산·실현변동성 근사·시간대)만 지금 계산 가능 — Regime·스프레드·이벤트근접도는 각각 W20~21·호가WS·Event Calendar 미구현이라 제외(모듈 docstring에 명기). `select_threshold()`가 Ver 1.6 §5.2 "비용차감 후 기대수익 최대화"를 그리드서치로 실제 구현(정확도 최대화 아님). 단위 테스트 14건(메타Feature 조립 known-value·순net_return 부호 검증(up/down/flat 신호)·학습데이터 조립이 flat신호 제외+y라벨 정확히 산출하는지·임계값선택 known-value(그리드 평균 손계산)+동률 시 보수적 선택·MetaLabeler 학습/예측/임계값 교체(재학습 없음)/저장로드 왕복) |
| models.trainer.generate_out_of_fold_predictions | ✅ | ✅ 2026-07-26 | — | Ver 1.6 §5.1 "1차 모델을 Walk-Forward로 가상 운용 → out-of-fold 신호만 수집"을 `PurgedKFold`로 실제 구현(칸닝 방지 — W12~13에 만든 CV 인프라의 첫 실사용처). 폴드마다 그 폴드에서 제외된 데이터로 학습한 앙상블의 `HorizonExpert.predict()`를 그대로 호출해 예측을 얻는다(booster 내부에 안 손대고 공개 API만 재사용). 단위 테스트 3건(정상 산출 시 확률 합=1·ens_std≥0·길이불일치 거부·레이블 없을 때 빈 결과) |
| models.trainer.train_formal_expert | ✅ | ✅ 2026-07-26 | — | Ver 1.6 §7.1 [3]~[4]단계 전체 오케스트레이션(탐색→out-of-fold→최종 앙상블 전체데이터 재학습→교정 부착→Meta-Labeler 학습+임계값선택) — `ExpertTrainingResult`(expert, meta_labeler, best_params, n_oof_records, n_meta_signals) 반환. out-of-fold 신호가 0건이면(데이터 부족) 조용히 빈 Meta-Labeler를 만드는 대신 ValueError로 실패(정식 경로는 칸닝 방지 메커니즘이 실제로 작동했다는 보장이 핵심이라는 판단). `train_prototype_expert()`(W14~16)는 그대로 유지 — 빠른 배관 확인용 경로로 남김. 단위 테스트 4건(전체 결과 필드 검증·교정기 부착 확인·빈 bars 거부·데이터 부족 거부) + `scripts/run_formal_expert_training_smoke.py` 실제 실행 확인 |
| scripts/run_formal_expert_training_smoke.py | ✅ | ✅ 2026-07-26 | — | 실제 아카이브(A05608, 5m, 7건)로 먼저 시도 → 예상대로 "데이터 부족" ValueError로 실패(정직하게 보고) → 200건 합성(사인파+지터) 데이터로 전체 파이프라인 실행: 탐색 완료(8개 하이퍼파라미터 산출) → out-of-fold 192건 → Meta-Labeler 192개 신호로 학습·임계값 0.9 선택 → 5-멤버 앙상블+교정기 부착 → 마지막 봉 예측→Meta-Labeler 통과판정까지 end-to-end 1회 성공. 합성 데이터는 스크립트 출력에 "실제 시장 데이터 아님" 명시. **2026-08-04 정정**: "실제 아카이브는 데이터 부족으로 실패하는 게 정상"은 그날의 사실이었을 뿐 항구적 성질이 아니다 — 2026-08-04 실제 1분봉 2398건(7거래일)으로 재실행하니 out-of-fold 2334건으로 성공했다. 다개월 심사는 `scripts/run_g1_walk_forward.py`(후방조정 근월물 연속물 + G1 관문)를 쓸 것 |

## optuna 설치·동작 확인 (2026-07-26)

지난주 lightgbm 4.7.0 Windows 휠 크래시 사고 이후 신규 ML 의존성은 설치 직후 최소 스모크
테스트를 거치는 습관을 들임 — optuna 4.9.0은 기본 `create_study().optimize()` 호출로 별
문제 없이 동작 확인(sqlalchemy/alembic 등 부수 의존성이 딸려오지만 기본 `InMemoryStorage`
사용 시 문제 없음). `pyproject.toml` ml extras에 `optuna>=3.6` 추가.

## 알려진 갭 (5m Expert 정식·Meta-Labeler, 2026-07-26)

- **Meta-Labeler의 Regime·스프레드·이벤트근접도 입력 미구현**: Ver 1.2 §5.2 원문이 요구하는
  입력 중 Regime(HMM, W20~21)·스프레드(호가 WS 미구독)·이벤트 근접도(Event Calendar
  미구현)는 아직 못 낸다 — 지금은 1차확률·마진·앙상블분산·실현변동성 근사·시간대 5개만
  사용(strategy/futures/meta_labeler.py 모듈 docstring).
- **실현변동성은 ATR이 아니라 px_bb_width_20 재사용**: 별도 ATR 재계산 대신 FeatureVector가
  이미 갖고 있는 변동성 계열 Feature를 근사치로 재사용 — Ver 1.5 §5 Feature 선정 절차가
  아직 없어(기존 갭) "가장 적합한" 변동성 지표를 고른 게 아니라 편의상 하나를 고정 선택.
- **early_stopping 미구현**: Ver 1.6 §2.2가 명시한 항목이나, 폴드 내부에 학습/조기종료용
  홀드아웃을 추가로 쪼개는 복잡도 대비 지금 데이터 규모에서 실익이 작다고 판단해 생략 —
  `num_boost_round` 고정값 사용(models/search.py 모듈 docstring).
- **ConformalCalibrator는 메커니즘만 있고 실사용 이력이 없다**: 매일 갱신되는 라이브/
  페이퍼 예측 로그가 전제인데(Ver 1.6 §6.2) 그 운영 루프 자체가 없다(G2 페이퍼트레이딩,
  W39~40부터). 지금은 어떤 곳에도 안 붙어 있고 합성 데이터로 계산 정확성만 검증됨.
- **Deflated Sharpe·실제 walk-forward 백테스트 성과 관문은 여전히 미실행**(W14~16
  기존 갭 유지): Cost Model·Expert·Validator·Meta-Labeler가 전부 갖춰졌지만 이들을 엮어
  실제 성과 시계열을 뽑는 백테스트 하니스 자체가 아직 없다.
- **5m Expert의 예측력은 여전히 검증 불가**(W14~16 기존 갭과 동일 이유): 실측 아카이브가
  하루치뿐이라 탐색·out-of-fold·Meta-Labeler 전 구간이 실제 시장 데이터로는 못 돌아간다
  (실측: 7건으로 시도 → 예상대로 실패). 이번 주 산출물은 배관 검증(합성 데이터로 전체
  경로가 실제로 도는지)이지 예측력 검증이 아니다.
- **HorizonExpert 저장 포맷은 여전히 Ver 1.6 §9.1 정식 번들(manifest.yaml 등)이 아니다**:
  앙상블 멀티파일+JSON+선택적 pickle로 확장됐지만 Registry가 없어 정식 패키징은 그대로
  미룸(W17~19 이후 재검토, expert.py 모듈 docstring).

## Regime AI — HMM + 규칙 (Ver 2.0 §9 W20~21)

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| features.vl_core.vl_vol_ratio | ✅ | ✅ 2026-07-26 | — | W_STD의 앞 두 값(5, 20) 윈도우 표준편차의 비율 — 짧은 창이 긴 창보다 훨씬 크면(고변동성 국면) 1보다 크게 나온다. W_STD 세 번째 값(60)은 30시간 웜업 비용이 커 이번 스코프에서 제외(모듈 docstring). 단위 테스트 4건(known-value·등락 없는 구간은 비율 1 근처·NaN 웜업 구간 처리·창 크기 override) |
| strategy.regime.hmm_model.RegimeHMM / build_observations | ✅ | ✅ 2026-07-26 (합성 데이터) | — | hmmlearn GaussianHMM 래퍼. 관측 벡터는 px_trend_r2·vl_vol_ratio·px_autocorr 3개 Feature 조합(px_core/vl_core 재사용, 신규 계산 없음). `n_states_candidates`로 후보 상태 수를 주면 BIC 최솟값으로 자동 선택(Ver 1.6 §3.1). 단위 테스트 8건(BIC 선정 known-value·상태수 1개 고정 경로·관측치 부족 시 ValueError·predict_states 길이 일치) |
| strategy.regime.naming.label_states / describe_labels | ✅ | ✅ 2026-07-26 (합성 데이터) | — | 통계층(HMM 상태 index)을 Regime enum으로 매핑하는 명명층 — 상태별 관측 특성(추세 강도·변동성 비율 평균)을 기준으로 TREND_UP/TREND_DOWN/RANGE/HIGH_VOL에 통계적으로 배정, 애매하면 UNKNOWN. describe_labels()는 사람 검수용 상태별 사후 통계 요약 문자열을 만든다. 단위 테스트 6건(각 국면 유형 known-value 배정·동률 처리·요약 문자열 필드 포함 여부) |
| strategy.regime.rules.RuleContext / rules | ✅ | ✅ 2026-07-26 | — | 규칙층(하이브리드 구조 2단) — 통계층 판정을 필요시 덮어쓴다. 지금 살아있는 규칙은 변동성 극단(vol_ratio가 임계값 초과 시 무조건 HIGH_VOL, confidence=1.0) 1개뿐 — 이벤트 근접·세션 시가/종가 특수구간 등 Ver 1.6 §3.1이 언급한 나머지 규칙은 Event Calendar 미구현(기존 갭)이라 제외(모듈 docstring). 단위 테스트 5건(임계 이하/초과 경계값·오버라이드 시 confidence=1.0 고정·오버라이드 없을 때 통계층 결과 그대로 통과) |
| strategy.regime.service.RegimeAI | ✅ | ✅ 2026-07-26 (합성 데이터) | — | `fit()`(HMM 학습→명명)→`classify()`(최신 봉 윈도우로 통계층 판정→규칙층 오버라이드) 오케스트레이션. `RegimeState`(core/messages.py 신규 — symbol/regime/confidence/state_duration_bars/transition_prob/rule_override/valid_until) 메시지 조립까지 담당. `n_states`/`labels`/`hmm_model` 공개 프로퍼티로 내부 모델 상태를 노출(스모크 스크립트·사람 검수용, private 속성 직접 접근 방지). 단위 테스트 9건(classify 국면 판정 known-value·상태 지속 봉수 증가/리셋·규칙 오버라이드가 confidence=1.0 강제·전이확률 합=1·최소 관측치 부족 시 UNKNOWN) — 최소 관측 길이가 `window+2`(px_autocorr가 다른 두 Feature보다 1봉 더 필요)임을 놓쳐 classify()가 항상 UNKNOWN을 반환하던 버그를 테스트로 발견·수정 |
| scripts/run_regime_ai_smoke.py | ✅ | ✅ 2026-07-26 | — | 실제 아카이브(A05608, 30분봉 1건)로 먼저 시도 → 예상대로 "관측치 부족" ValueError로 실패(정직하게 보고) → 추세상승/횡보/고변동성 3구간 반복 합성 30분봉으로 전체 파이프라인(HMM 학습→BIC 상태수 선정→국면 판정→규칙 오버라이드 시연→사람 검수용 요약) end-to-end 1회 성공. 합성 데이터는 "실제 시장 데이터 아님" 명시 |

## 알려진 갭 (Regime AI, 2026-07-26)

- **HMM은 여전히 실제 다개월 아카이브로 학습·검증된 적이 없다**: 실측 아카이브가 30분봉
  기준 1건뿐이라(`RegimeAI.fit()`이 요구하는 최소 관측치에 크게 못 미침) 이번 주 산출물은
  전부 합성(추세상승/횡보/고변동성 3구간 반복) 데이터로 배관만 검증했다 — 실제 상태 분리가
  의미 있게 되는지는 G1 백테스트 준비 단계(W17~ 이후, 다개월 아카이브 확보 시) 재검증 필요.
- **규칙층이 사실상 1개 규칙(변동성 극단)뿐이다**: Ver 1.6 §3.1 원안이 언급한 이벤트 근접·
  세션 시가/종가 특수구간 규칙은 Event Calendar 미구현(기존 갭)이라 전부 제외했다 — 지금은
  통계층(HMM) 의존도가 원안보다 훨씬 높은 상태.
- **RegimeState는 아직 어떤 운영 루프에도 발행되지 않는다**: `core/messages.py`에 스키마만
  추가됐고 `intel.regime` 토픽으로 실제로 publish하는 상시 구동 서비스는 없음(스모크
  스크립트가 직접 `classify()`를 호출해 확인할 뿐) — Aggregator·Meta Decision Engine이
  이 메시지를 소비하려면 그 전에 상시 구동 배선이 먼저 필요(W24~26, 전 경로 관통 스코프).
- **HMM 상태 수(n_states)는 후보 목록 중 BIC 최솟값을 고르는 방식이라, 후보 목록 자체를
  잘못 좁히면(예: 실제로 5개 국면인데 후보를 2~3개로만 줌) 과소적합을 못 알아챈다**: 지금
  스모크 스크립트는 4~6 범위로 고정 — 실제 시장 데이터로 재학습할 때 더 넓은 후보 범위로
  민감도 분석이 필요하다.
- **온라인/점증 갱신 없음**: `fit()`은 전체 배치 학습만 지원 — 매일 새 데이터로 HMM을
  다시 학습해야 하는지, 아니면 일정 주기로만 재학습할지(레짐 시프트 대응과 안정성의
  트레이드오프)는 아직 정책이 없다.

## hmmlearn 0.3.3 + numpy 2.5 DeprecationWarning (2026-07-27 발견, 추적 중 — 미해결)

`RegimeHMM`(hmmlearn `GaussianHMM`) 관련 테스트를 실행할 때마다
`hmmlearn/utils.py:27`의 `a_sum.shape = shape`(배열에 shape을 직접 재할당하는 방식)가
`DeprecationWarning: Setting the shape on a NumPy array has been deprecated in NumPy 2.5.
As an alternative, you can create a new view using np.reshape (with copy=False if needed).`을
띄운다 — 2026-07-27 일일점검 중 전체 테스트 실행 로그에서 553건 확인(`test_hmm_model.py`
64건·`test_runtime.py` 183건·`test_service.py` 306건).

**원인**: hmmlearn 0.3.3(현재 설치·PyPI 최신 안정판, 2026-07-27 기준 재확인)의 내부 구현이
아직 numpy 2.5의 신규 배포 정책(배열 shape 직접 대입 금지 예고, `np.reshape`로 대체 권고)에
맞춰지지 않았다 — 우리 코드가 아니라 hmmlearn 라이브러리 자체의 내부 코드라 우리 쪽에서
고칠 수 없다. numpy를 낮추는 우회는 이미 lightgbm 사고(위 "lightgbm 4.7.0 Windows 휠 크래시"
섹션)에서 확인했듯 이 프로젝트의 numpy 2.5.1 고정과 충돌하는 scipy를 다시 깨뜨리므로
선택지가 아니다.

**현재 영향**: 없음 — `DeprecationWarning`은 예외가 아니라 경고이므로 515건 전체 테스트
통과에 영향 없다. **잠재 위험**: numpy가 향후 릴리스에서 이 배포 정책대로 실제로 shape
직접 대입을 제거하면(경고 문구가 그렇게 예고하고 있음), 그 시점 numpy로 올리는 순간
hmmlearn의 `GaussianHMM` 학습이 예외를 던지며 깨진다 — `RegimeAI`/`RegimeRuntime`(W20~21,
W24~26) 전체가 영향을 받는다.

**대응**: 코드 수정 없이 추적만 한다 — `pyproject.toml`의 `ml` extras에 주석으로 근거를
남기고, numpy를 올릴 때는 반드시 `tests/strategy/regime/`(hmm_model·naming·rules·service·
runtime) 전체 통과를 먼저 확인하도록 명시했다. hmmlearn 쪽 릴리스 노트도 numpy 올리기 전에
확인할 것 — 이 경고가 없어진 버전이 나왔다면 hmmlearn을 먼저 올리는 게 우선.

## VL 확장 + FeatureEngine deque 버그 수정 + 15m·30m Expert 검증 (Ver 2.0 §9 W22~23)

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| features.engine.FeatureEngine — deque 슬라이스 버그 수정 | ✅ | ✅ 2026-07-26 | — | **버그 발견·수정(회귀 영향 큼)**: 롤링 히스토리를 `collections.deque`로 보관하는데 `px_core`/`vl_core` 계산기 다수가 `bars[-window:]` 슬라이스를 쓴다 — Python `deque`는 슬라이스를 지원하지 않아(정수 인덱싱만 가능) 슬라이스 쓰는 계산기는 전부 `TypeError`를 던졌고 `_safe_call`의 광범위 `except Exception`이 조용히 None으로 삼켜 왔다. **실측으로 확인**: 80봉 완전 워밍업 후에도 82개 키 중 72개가 None(정수 인덱싱만 쓰는 px_ret/px_mom/px_accel 등 소수만 정상). W6~8 원 실측 노트("px_ret_5/px_mom_5는 실제 값 산출 확인")가 정확히 그 소수 사례만 확인한 것이었어서 그동안 발견되지 못했다 — W14~19의 5m Expert 프로토타입·정식 학습도 전부 이 축소된 유효 Feature 집합으로 진행된 셈(실측 데이터가 원래도 극소량이라 학습 자체의 배관 검증 목적은 훼손 안 됐지만, 실제 신호 폭은 예상보다 좁았음). 수정: `handle_bar()`가 계산 직전 `list(history)`로 변환 — 계산기 쪽 계약(`Sequence[BarClosed]`)은 원래도 맞게 짜여 있어 수정 불필요. 회귀 테스트 신규 1건(`test_slice_based_calculators_produce_real_values_once_warmed` — 40봉 워밍업 후 `px_vwap_dev_5`/`vl_atr_5` 등 슬라이스 기반 계산기가 실제로 None이 아님을 확인) |
| features.vl_core — VL(변동성) 1개 → 14개 확장 + FeatureEngine 결선 | ✅ | ✅ 2026-07-26 | — | Ver 1.4 §2.3 VL 16개 중 OHLCV만으로 계산 가능한 13개 신규(`vl_rv`·`vl_park`·`vl_gk`·`vl_yz`·`vl_atr`·`vl_atr_rel`·`vl_semi_dn`·`vl_semi_up`·`vl_semi_ratio`·`vl_jump`·`vl_range_exp`·`vl_vov`·`vl_squeeze`) + 기존 `vl_vol_ratio`(W20~21, Regime AI 전용 직접호출 경로였음). Ver 1.5 §3.5(15m Expert, VL 15%)·§3.6(30m Expert, VL 15%) 배정 대응. `WINDOWED_FEATURES`/`STATEFUL_FEATURES` 레지스트리를 px_core.py와 동일 형태로 노출해 `features/engine.py`가 자동으로 계산·발행(엔진 코드는 카테고리별 루프 2줄만 추가, `_build_feature_vector` 본체 변경 없음) — **이번에 VL이 처음으로 `FeatureVector`에 실제로 실린다**(전엔 Regime AI가 `build_observations()`로 별도 직접호출만 했음). `vl_vov`/`vl_squeeze`는 이중 윈도우 구조(하위윈도우로 지표를 여러 시점에 굴려 그 분포를 봄)라 하위윈도우를 표준 20 대신 5로 낮춰(`_INNER_SUBWINDOW`) `engine._MAX_HISTORY`(130) 예산 안에 맞춤(W_SLOW 최댓값 120 기준 120+5=125<130, 20이면 140>130으로 영원히 워밍업 안 끝나는 죽은 칸이 됐을 것). 분산 성분은 전부 모집단 분산(프로젝트 기존 관례), `vl_rv`는 연율화 미적용(Horizon마다 하루 봉 수가 달라 통일 상수가 없고 트리 모델엔 실익 없음). 단위 테스트 36건 신규(각 함수 known-value/cross-check + 워밍업 부족 None + 주요 경계 케이스, `vl_jump`의 0-클립·`vl_squeeze`의 창 구성 실패로 인한 재설계 1건 포함) |
| **여전히 스코프 밖**: `vl_har_pred`·`vl_intraday_shape` | ❌ | — | — | 둘 다 "상태형"으로 여러 거래일에 걸친 시간대별 통계(일/주/월 RV, 시간대별 평균 RV)가 필요한데 이 프로젝트엔 그 통계를 쌓는 인프라(세션별 시계열 저장소)가 없다 — Event Calendar 미구현으로 Regime AI 이벤트 근접 규칙을 미룬 것과 같은 이유(vl_core.py 모듈 docstring). |
| models.trainer.train_formal_expert / train_prototype_expert — M15/M30 검증 | ✅ | ✅ 2026-07-26 (합성 데이터) | — | `HorizonExpert`/`Trainer`/`MetaLabeler`/`labeling.BARRIER_PARAMS`는 W14~19부터 이미 Horizon을 데이터로만 받는 설계(하드코딩된 "5m" 분기 없음)였다 — 이번 주는 그 일반성이 M15/M30에서도 실제로 성립함을 처음 못 박았다(Ver 1.2 §4.2 구현 순서 "5m → 15m → 30m …" 대응). `tests/models/test_trainer.py`의 `_bars()` 헬퍼가 M5가 아니면 무조건 1분 간격으로 봉을 만들던 버그를 발견·수정(`HORIZON_SECONDS[horizon]//60`으로 실제 Horizon 길이를 반영) — 고치기 전엔 M15/M30의 시간배리어(3봉=45분/90분)가 봉 간격(1분)보다 훨씬 길어 거의 모든 레이블 구간이 서로 겹쳐 PurgedKFold가 사실상 전부 purge하는 왜곡이 있었을 것. `scripts/run_formal_expert_training_smoke.py --horizon 15m`/`--horizon 30m`으로 실제 실행 확인: 두 경우 다 실제 아카이브(15m 3건/30m 1건)는 예상대로 데이터 부족 실패(정직 보고), 합성 200봉으로는 탐색→out-of-fold 192건→앙상블+교정기→Meta-Labeler 학습·임계값 선택까지 5m과 동일하게 end-to-end 성공. 단위 테스트 4건 신규(M15/M30 × formal/prototype 파라미터화) |

## 알려진 갭 (VL·15m·30m Expert, 2026-07-26)

- **15m/30m Expert는 여전히 Ver 1.5 §3.5~3.6이 배정한 후보 구성의 절반 이하만 받는다**:
  15m은 FL(수급) 30%·OP 10%·RG 10%(합 50%), 30m은 FL 20%·OP 20%·RG 20%(합 60%)가
  배정돼 있는데, **데이터 수집은 2026-08-04에 셋 중 둘이 시작됐다**(피처는 아직 없다 —
  수집과 피처는 별개 단계다):
  - FL(외국인/기관 순매수) — 2026-08-04 결선, 08-05 기동분부터 적재.
  - OP(옵션 체인 그릭스·IV) — 2026-08-04 결선. `get_quote(O)` 실측으로 **IV·델타·감마·
    미결제약정이 응답에 그대로 들어 있음**을 확인했다(`op_gex`에 필요한 감마×OI가 한 호출로
    나온다). 다만 KIS Greeks를 그대로 쓸지 BS로 재계산할지는 별도 판단이라 `raw` 보존만 한다.
  - RG(베이시스·시장폭·VIX·USDKRW) — 여전히 소스 없음. **단, KOSPI200 현물지수는 이제
    옵션체인 응답(`output3`)에 매 폴링 실려 온다** — `rg_basis` 계열의 입력 하나가 부수적으로
    확보됐다(현물지수 전용 소스 연동 전까지의 임시 경로). 지금 15m/30m Expert가 실제로 쓸 수 있는 건 PX+VL(두 Horizon 모두
  Ver 1.5 배정 20%+15%=35%에 해당하는 카테고리)뿐 — Ver 1.5 §5 선정 절차(IC 스크리닝→
  상관 클러스터링→안정성 선택)도 이 축소된 후보군에서만 의미가 있다. FL/OP/RG는 각각
  전용 Collector/Normalizer/Archiver급 작업이 필요해 이번 2주 스코프에 넣지 않았다 —
  별도 착수 필요(다음 분기회의 안건 후보).
- **deque 슬라이스 버그가 W14~19에 학습된 모델에도 영향을 줬을 가능성**: 그 시점 5m
  Expert 학습은 전부 극소량 실측 데이터(7~33행) 또는 합성 데이터였고 목적 자체가 "배관
  검증"이었다고 명시돼 있었지만, 실제로 사용 가능했던 Feature 폭이 기록된 것(82개 중 대부분)
  보다 훨씬 좁았다는 뜻 — 다만 저장된 모델 자산이 없어(Registry 부재, 매번 스모크에서
  즉석 학습) 재학습이 필요한 대상은 없음, 향후 실제 학습부터는 이번 수정이 반영된 상태.
- **vl_vov/vl_squeeze의 하위윈도우(5)는 표준 관례(20)보다 작다**: `engine._MAX_HISTORY`
  예산(130) 안에 맞추기 위한 판단(vl_core.py 모듈 docstring) — Ver 1.5 §5 선정 절차에서
  이 두 Feature가 실제로 쓸모 있다고 나오면, 하위윈도우를 표준값으로 늘리는 대신
  `_MAX_HISTORY` 자체를 올리는 재검토가 필요할 수 있다(메모리 비용은 여전히 작음).
- **Aggregator/Meta Decision Engine과의 결선은 여전히 없다**(기존 갭 유지, W24~26 스코프):
  이번 주는 Expert 학습 파이프라인이 M15/M30에서도 성립함을 확인했을 뿐, Regime 가중치
  매트릭스(Ver 1.2 §7.1)를 적용한 다중 Horizon 통합점수 S 계산은 아직 어디에도 없다.

## 운영 스크립트 (scripts/)

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| run_l1_daily.py — L1 파이프라인 일일 진입점 | ✅ | ✅ 2026-07-24 | — | 신규 구현 — 웜업(self_check→근월물 심볼 확인→Redis 연결→Collector/Composer/Engine 구성·WS 연결까지 미리 끝냄)→정규장 수집(09:00~15:35 KST, TickCollector+MultiHorizonBarComposer+FeatureEngine을 asyncio.gather로 동시 구동)→daily_close(미완성 봉 flush·버스 종료, 15:40 KST 안전판 데드라인) 흐름. `scripts/run_l1_daily.bat`(Windows 배치 래퍼)도 함께 준비 — **작업 스케줄러(schtasks) 등록은 아직 안 함**(매일 무인으로 실제 KIS API를 호출하는 상시 자동화라 사용자 확인 후 별도 진행하기로 함). 실측: 세션-스톱 시각을 20초 뒤로 패치해 전체 생애주기(웜업→실제 WS 연결→실틱 수신→1분봉 완성→3/5/10/15/30분봉 합성→FeatureVector 발행→daily_close→정상 종료 exit 0)를 실제 KIS 서버로 확인, `data/bars/A05608/{1m,3m,5m,10m,15m,30m}/2026-07-24.parquet` 전부 정상 생성. **버그 5건 발견·수정**: (1) `websockets` 패키지가 core 런타임 의존성인데 `ui` extras로 잘못 분류돼 있어 base 의존성만 설치한 무인 운영용 venv에서 ImportError로 죽었을 것(pyproject.toml에서 이동), (2) `.bat` 파일에 한글 주석을 UTF-8로 저장하면 cmd.exe가 시스템 로캘(cp949)로 잘못 해석해 배치 자체가 실행 안 됨(전부 영문 주석으로 재작성), (3) self_check.py를 서브프로세스로 부를 때 `subprocess.run(text=True)`가 인코딩을 명시 안 해 self_check의 UTF-8 출력을 cp949로 디코딩하려다 UnicodeDecodeError(encoding="utf-8" 명시로 수정), (4) 최초 버전이 `>> 로그파일 2>&1`로 전부 파일에만 리다이렉트해 cmd 창에 아무 것도 안 보임(사용자 실사용 중 발견) — PowerShell 경유로 콘솔에도 동시에 뿌리도록 수정, `chcp 65001`로 콘솔 코드페이지도 UTF-8로 전환, (5) 그 수정에 처음 쓴 `Tee-Object -Encoding utf8`이 Windows PowerShell 5.1엔 `-Encoding` 파라미터 자체가 없어 즉시 실패, 파라미터 없이 쓰면 `-FilePath` 출력이 기본 UTF-16LE로 저장됨(둘 다 실측으로 확인) — `Out-File -Encoding utf8`로 줄 단위 수동 tee로 교체해 최종 해결 |
| stop_l1_daily.bat — 데일리 종료 안전망 워치독 | ✅ | ✅ 2026-07-24 | — | 신규 구현 — run_l1_daily.py 자체의 daily_close()·15:40 안전판과는 별개의 독립 워치독. mahdi 프로젝트가 2026-07-21 실제로 겪은 사고(창 제목 기반 taskkill이 사람이 수동으로 재시작한 프로세스를 못 잡아 자동 종료가 조용히 실패)를 참고해, 창 제목이 아니라 커맨드라인 내용(`*run_l1_daily.py*`)으로 매칭 — 어떻게 띄워졌든 실제로 무슨 코드를 실행 중인지로 찾음. **버그 2건 발견·수정**(둘 다 실제 잔존 프로세스로 실측): (1) 이 파일에도 실수로 한글 텍스트가 한 줄 남아있어 위 run_l1_daily.bat와 똑같은 cp949 오분석으로 배치 실행 시 알 수 없는 명령 오류가 무더기로 남 — 파일 전체를 바이트 단위로 ASCII 검증해 제거, (2) `$procs | Stop-Process -Force`(파이프)가 아무 에러 없이 조용히 아무것도 안 죽임 — Win32_Process CIM 객체는 `ProcessId` 속성을 갖는데 `Stop-Process`의 파이프 바인딩은 `Id`를 찾아서 매칭 실패, 로그엔 "종료함"이라고 찍히는데 실제로는 살아있는 상태가 됐었음 — `foreach` 안에서 `Stop-Process -Id $p.ProcessId`로 명시 지정하도록 수정 |

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
- ~~**REST 폴링 루프(투자자매매동향·옵션체인 그릭스) 미구현**~~ — 2026-08-04 완료. 수급 3업종
  (60초)·옵션체인 3시리즈(먼쓰리 300초 / 위클리 각 600초, 위상 0/100/200초)를 `run_l1_daily.py`
  에 결선. `KISRestClient`를 **하나만 만들어 공유**한다(폴러별 페이서 분리는 마흐디 2026-07-08
  500 폭주로 203분치를 날린 전례가 있어 봉인). 총수요 0.330건/초(용량의 33%)·백오프 내성
  3.03배 — 기동 로그가 이 값을 매일 찍는다.
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
  제외)이라 실제 급증(장 시작 직후·지수 급변동 등) 구간의 처리량/안정성은 미검증. Phase 1
  파이프라인 완성 후 정기회의로 검증 이관(dev_memory/DECISION_LOG.md 2026-07-23, 기한
  2026-08-14).
- **MS(마이크로구조) Feature 30개 미구현**: Ver 1.4 §2.1 목록 대부분(ms_spread·ms_imb_l1/l5/l10·
  ms_ofi·ms_microprice·ms_queue_imb 등)이 호가창(bid/ask, L1~L10 잔량) 데이터를 필요로 하는데
  MESSIAH는 아직 호가 WS(H0IFASP0, "지수선물 실시간호가")를 구독하지 않는다. 호가 없이도 계산
  가능한 절반(ms_vpin·ms_tick_rule·ms_absorb·ms_trade_count 등, mahdi orderflow.py/volume.py에
  이미 포팅 가능한 형태로 존재 — 2026-07-23 세션에서 확인·판정)도 완성봉 요약(OHLCV)만으로는
  계산 불가능하고 원시 틱(md.tick)을 봉 경계 사이에 직접 누적해야 해서 FeatureEngine을 틱도
  구독하도록 확장해야 함 — 호가 WS 작업과 함께 나중에 한번에 처리하기로 함(px_core.py 모듈
  docstring 참고, 사용자와 스코프 합의).
- **EV(이벤트·시간) Feature 14개 미구현**: 로드맵 문구가 "MS/PX"만 명시해 이번 스코프 밖으로
  둠. 계산 자체는 간단(시각/만기 캘린더 기반, 외부 데이터 의존 없음)해 다음 순서로 유력한 후보.
- **Feature 선정 절차(Ver 1.5 §5, IC 스크리닝·상관 클러스터링·Walk-Forward 생존 검정) 미구현**:
  Triple Barrier 레이블이 있어야 하는데 Phase 2(W12~13) 산출물이라 아직 없음 — 지금 있는 30개는
  전부 "후보"(candidate) 상태이며 실전 투입 전 이 절차를 반드시 거쳐야 한다.
- **px_vwap_dev의 VWAP은 근사치**: 완성봉에 실제 체결가중평균이 없어(BarClosed엔 O/H/L/C/
  volume만 있음) 전형가(OHLC3) 거래량가중으로 근사. px_macd_h도 고정 12/26/9 대신 window로
  일반화한 근사(px_core.py 모듈 docstring 참고) — 둘 다 Ver 1.5 §5 IC 스크리닝에서 실제 예측력이
  없으면 자연 탈락하는 후보이므로 지금은 근사치인 채로 두고 넘어감.
- **run_l1_daily.py가 작업 스케줄러(schtasks)에 등록 안 됨**: 스크립트·배치파일은 실측
  완료했지만, 매일 무인으로 실제 KIS API를 호출하는 상시 자동화는 사용자 확인 후 별도
  진행하기로 함(2026-07-24) — 지금은 수동 실행만 가능.
- **run_l1_daily.py는 KRX 휴장일을 모른다**: Event Calendar Service 미구현(기존에 알려진 갭)이
  그대로 이 스크립트에도 적용됨 — 휴장일에 실행하면 self_check는 통과하고 WS 연결도 되지만
  하루 종일 틱이 안 옴(에러는 안 나지만 빈 수집). 작업 스케줄러 등록 시 최소 주중(월~금)
  트리거로는 걸러야 하고, 완전한 휴장일 인식은 Event Calendar 구현 이후.
- **Digital Twin은 호가창 수준 재생이 아니라 1분봉 기반 체결 모사다 (2026-07-26, 설계상 의도된 스코프 축소)**:
  Ver 1.0.1 §2.1 원안("호가창 수준 재생 + 자기 주문의 시장충격 모사")은 MESSIAH가 아직 호가
  (orderbook) WS를 구독하지 않아(위 "원시 틱 자체는 Parquet에 안 쌓임" 갭과 동일 원인) 이번
  스코프에서 불가능했다 — 대신 이미 아카이브된 완성봉(1분봉)의 고가/저가 터치로 지정가 체결을
  판정하는 근사로 W9~11을 완료했다(simulator/adapter.py 모듈 docstring 참고). 호가 WS가
  나중에 구현되면 더 정밀한 체결 모사로 교체할 자리로 남겨둠 — 지금은 "터치하면 지정가 그대로
  체결"(더 유리한 체결 가능성 무시, 보수적)이라는 단순 가정이다.
- **SimBroker의 TTL은 1분봉 단위로만 판정된다**: 실제 ttl_ms(기본 30초)가 1분봉 간격(60초)보다
  짧아도 체결 판정이 TTL 만료 판정보다 항상 우선이라(같은 봉에서 동시 발생 시) 실질적으로는
  "다음 1분봉에서 터치하면 체결, 아니면 그 시점 TTL을 넘겼는지 확인" 수준의 정밀도다 — 진짜
  틱 단위 TTL 정밀도가 필요해지면(Cost Model v1, W14~16) 재검토 대상.
- **SimBroker는 부분체결을 모델링하지 않는다**: 터치하면 항상 전량 체결, 아니면 미체결 — 실제
  체결 품질 대사가 쌓이면(Cost Model v1) 확장할 자리로 남겨둠.
- **DigitalTwinEngine에는 아직 전략(Expert) 레이어가 없다**: Phase 3(W17~) 이전이라 재생 중
  자동으로 주문을 내는 로직이 없음 — `scripts/run_replay.py`의 데모 주문은 배선 검증용 1건뿐이고,
  실제 Walk-Forward 백테스트(Ver 1.0.1 §8.2)에 쓰려면 Triple Barrier·CV 프레임(W12~13)과 최소
  1개 Expert(W14~16 이후)가 먼저 있어야 한다.
- **WalkForwardSplitter는 실제 다개월 아카이브로 실측한 적 없다 (2026-07-26)**: 정확성은
  합성(30~60일) 데이터 기준 known-value 테스트가 담당한다 — 실제 KRX 데이터가 여러 달 쌓이면
  (G1 백테스트 준비 단계, W17~ 이후) 진짜 롤링 창 여러 개가 나오는지 재검증 필요.
- **models/cv.py는 KRX 휴장일을 모른다**: WalkForwardSplitter는 달력일(calendar day) 기준
  창 경계를 계산한다 — Event Calendar 미구현(기존 갭)과 동일한 한계. 휴장일이 창 안에 껴도
  그날은 이벤트 자체가 없을 뿐 경계 계산 자체는 안전하지만, "학습 180일"이 실제 거래일
  기준으로는 그보다 적은 표본을 의미한다는 점은 Trainer 설계 시 감안 필요.
- **Triple Barrier의 비용반영 강등(cost_ticks)은 Cost Model v1이 아니라 호출자가 직접
  주는 임시값이다**: Cost Model v1(W14~16)이 나오면 `triple_barrier_labels(cost_ticks=...)`
  호출부(향후 Trainer)를 실제 추정 비용으로 교체할 자리로 남겨둠 — 지금은 기본값 0(강등
  없음)이라 호출자가 명시하지 않으면 비용을 전혀 반영하지 않는다.
- **Triple Barrier ATR 윈도우(14)는 명시된 근거 없이 선택한 기본값**: Ver 1.2 §3.2 표는
  배리어 폭이 "×ATR(주기)"라고만 하고 ATR 계산 윈도우 크기는 명시하지 않는다 — Wilder
  관례값 14를 기본으로 뒀으나(`atr_window` 파라미터로 언제든 override 가능), 실제 실효값은
  Ver 1.5 §5 Feature 선정 절차와 함께 재검토 대상.
- ~~**run_l1_daily.py는 선물(K200_MINI_FUT) 1개만 수집**~~ — 2026-08-04 옵션체인 결선으로 해소.
  **이 항목의 진단 자체가 틀렸었다**: "옵션을 같이 수집하려면 WS 다중연결을 먼저 풀어야
  한다"고 적혀 있었는데, 옵션 **체인 시세**는 `OptionChainPoller`가 REST로 받으므로 WS 연결을
  하나도 열지 않는다. WS 제약은 옵션 **틱(체결)** 구독에만 걸리는 별개 과제다. 실제 제약은
  REST 유량이었다 — 근월 체인 전량이 1,356다리(먼쓰리 780·월위클리 242·목위클리 334)라
  1건/초에서 **1회 폴링에 22.6분**이고, ATM±10 창(42다리)으로 푼다. 이 오진 때문에 착수가
  미뤄져 있었다.
  남은 것: 옵션 **틱**의 실시간 WS 구독(단일 연결·다중 subscribe 재설계) — 아래 항목 그대로.
- **WS 재연결이 짧은 시간 안에 반복되는 패턴 재관측(2026-07-24, 원인 미확정)**: 2026-07-23
  세션에선 "동시 WS 연결 2개"가 반복 단절의 원인으로 추정됐는데(위 갭 항목), 2026-07-24
  run_l1_daily.py 실측 중에는 **연결이 딱 1개뿐**인데도 20초 사이에 5회 연속 단절(전부
  "no close frame received or sent")이 재현됨 — 지난번 가설(동시 연결 충돌)만으로는 전부
  설명이 안 됨. 유력한 새 가설: approval_key 재발급도 OAuth 토큰처럼 계정당 재발급 빈도
  제한이 있고, 이 세션에서 짧은 시간 안에 배치파일·검증 스크립트를 여러 번 반복 실행하며
  approval_key를 자주 재발급받은 게 트리거였을 가능성(재연결마다 approval_key를 새로
  발급받는 설계라 — collector.py run_forever() 참고). run_forever() 자체의 재연결 로직은
  설계대로 정확히 동작함(단절 감지→백오프→재시도→CollectorWSReconnected) — 코드 결함이
  아니라 서버측/유량제한 추정. 확실히 밝히려면 approval_key 재발급 간격을 충분히 띄운
  상태에서 재검증 필요(이번 세션엔 실측을 더 반복하지 않고 보류 — API를 더 두들기지
  않기 위함).

## Aggregator·Meta Decision·Risk Engine·Sizer·Kill Switch (Ver 2.0 §9 W24~26, 전 경로 관통)

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| strategy.futures.aggregator.Aggregator | ✅ | ✅ 2026-07-27 | — | Ver 1.2 §7.1 가중치 매트릭스(6 Regime×6 Horizon)·§7.2 통합점수 공식 그대로 구현. 신선도(f_h) 구현 중 실제 버그 발견·수정(아래 "알려진 갭·버그" 참고). 단위 테스트 10건 |
| strategy.futures.service.FuturesAIService | ✅ | ✅ 2026-07-27 | — | Ver 1.2 §9 모듈 구조의 `service.py`(HorizonExpert→MetaLabeler→Aggregator→intel.futures) 최초 배선 — W14~19에 구현만 되고 실시간 루프에 안 붙어 있던 HorizonExpert/MetaLabeler를 실제로 연결. 단위 테스트 6건 |
| strategy.regime.runtime.RegimeRuntime | ✅ | ✅ 2026-07-27 | — | RegimeAI(W20~21, "어떤 운영 루프에도 아직 발행 안 됨"이던 상태)를 `bar.30m.{symbol}`에 배선해 `intel.regime` 상시 발행. 단위 테스트 4건 |
| strategy.decision.meta_decision.MetaDecisionEngine | ✅ | ✅ 2026-07-27 | — | Ver 2.0 §3.1 규칙 ①~⑤ 우선순위 그대로 구현. ⑥⑦(Options AI 비교)은 Options AI 자체가 없어(Phase 4) 코드 경로 자체가 없음(선택이 아니라 부재). NO TRADE도 근거와 함께 항상 발행(Ver 2.0 §3.2). 단위 테스트 11건 |
| risk.risk_engine.RiskEngine | ✅ | ✅ 2026-07-27 | — | Ver 2.0 §5 한도표 중 R2(일일손실)·R3(증거금)·R5(포지션수)·R10(연속손실)·R11(데이터단절)·R12(주문오류율) + Net ER>0 게이트 구현. R1은 게이트가 아니라 Sizer의 사이징 상한으로 구조적으로 강제(모듈 docstring 근거). R4·R6·R7·R8·R9는 세션 인식·옵션 Greeks 전제라 미구현(아래 "알려진 갭"). 단위 테스트 13건 |
| risk.sizer.PositionSizer | ✅ | ✅ 2026-07-27 | — | Ver 1.1 §4-3 "Vol Targeting × Fractional Kelly × 불확실성 페널티"를 Ver 2.0 §2 워크스루 예시 그대로 구현. Kelly 엣지는 대칭 페이오프(b=1) 가정 근사(실현 트랙레코드 없음, 알려진 갭). `point_value_krw`(50,000원/pt)는 공개적으로 알려진 값이나 KIS API 실측(계약승수 필드) 전 placeholder. 단위 테스트 10건 |
| risk.kill_switch.KillSwitch | ✅ | ✅ 2026-07-27 | — | Ver 1.1 §4-4·Ver 2.0 §5 트리거(R2+R11(지속)+수동+모델이상) 구현. 발동 시 `sys.kill` 발행 + `OrderGateway.halt()` + `liquidate()`(반대매매 시장가 EMERGENCY 주문 목록, 제출은 호출자가 OrderGateway로). **실제 버그 발견·수정**: 최초 구현은 `OrderGateway.halt()`가 EMERGENCY 주문까지 차단해 Kill Switch 자신의 청산 주문이 거부되는 모순이 있었음 — `scripts/run_full_path_smoke.py` 실행 중 청산 로그가 반복 발행되는 것으로 발견, `OrderGateway.submit()`이 `kind=EMERGENCY`는 halted 상태에서도 통과시키도록 수정(회귀 테스트 `tests/test_core_w1.py::test_halt_blocks_new_entries_but_not_emergency_liquidation` 추가). 단위 테스트 10건 |
| execution.order_gateway.OrderGateway.halt() | ✅ | ✅ 2026-07-27 | — | `resume()`과 대칭되는 신규 공개 진입점. 기존 61건 회귀 없음 + 신규 EMERGENCY 우회 테스트 1건 |
| strategy.pipeline.TradingPipeline | ✅ | ✅ 2026-07-27 | — | L3(FuturesView)→L4(Cost→Risk→Sizer)→L5(OrderGateway) 전 경로 관통 오케스트레이터. Net Expected Return은 크기 예측 모델이 없어 `edge×ATR(M1,14봉) − 왕복비용`으로 근사(명시적 근사, 알려진 갭). 단위 테스트 6건 |
| scripts/run_full_path_smoke.py | ✅ | ✅ 2026-07-27 | — | 실제 아카이브 시도(예상대로 데이터 부족 실패) → 합성 데이터로 Expert 2개(5m·30m)+Meta-Labeler 학습 → RegimeAI 학습 → 전체 실시간 배선(FeatureEngine·FuturesAIService·RegimeRuntime·SimBroker·TradingPipeline) 구동 → 직접 주입한 강한 LONG 신호로 Sizer→RiskEngine→OrderGateway→SimBroker 전 경로 주문 체결(포지션 16계약 개설) 확인 → 계좌 손실 조작으로 Kill Switch(R2) 실제 발동·청산·Gateway 정지까지 end-to-end 1회 성공. **이 스크립트 실행 중 위 KillSwitch/OrderGateway 버그와 아래 시각 도메인 버그를 실제로 발견** |

### 실측 중 발견·수정한 버그 2건 (2026-07-27)

1. **`FuturesView.ts_utc`/Aggregator 신선도 계산의 시각 도메인 불일치**: `Aggregator.compute()`가
   신선도(f_h) 계산에 쓰는 `as_of`와 무관하게 `FuturesView`의 `ts_utc`는 `BusMessage` 기본값
   (wall clock, `now_utc()`)으로 채워지고 있었다 — 실거래에선 wall clock≈봉 시각이라 안
   드러나지만, 재생/스모크처럼 과거·합성 시각을 빠르게 재생하면 `TradingPipeline`의
   R11(데이터단절) 판정이 "wall clock 기준 now" vs "봉 도메인 last_bar_confirm_at"을 비교해
   수억 초 단위 가짜 단절을 일으켰다(스모크 스크립트 첫 실행에서 실측 발견, 로그
   "R11 데이터단절 15172559s 지속"). `Aggregator.compute()`가 `FuturesView(ts_utc=as_of, ...)`로
   명시 오버라이드하도록 수정 + `FuturesAIService._publish()`가 `trigger.ts_utc` 대신
   `trigger.valid_until`(봉 도메인 시각)을 `as_of`로 넘기도록 수정(`strategy/futures/
   aggregator.py`·`service.py` 모듈 docstring에 상세 근거 기록).
2. **신선도(f_h) 공식 자체가 처음부터 거꾸로였음**: `valid_until` 필드는 스키마 주석("다음
   완성봉 시각")과 달리 `features/engine.py`가 실제로 "그 봉 자신의 확정 시각"(= `bar_open_kst
   + Horizon길이` = `bar_confirm_time`)으로 채운다 — 최초 구현은 이를 "미래 만료 시점"으로
   오독해 `(valid_until − as_of)/Horizon`으로 감쇠시켰는데, 실제 채움값 기준으로는 발행
   즉시 신선도가 0이 되는 반대 결과가 났다(위 1번 버그를 고치는 과정에서 단위 테스트로
   재현·발견). `(as_of − valid_until)`(경과 시간) 기준으로 공식을 반전해 수정 —
   `RegimeState.valid_until`·`ExpertView.valid_until` 등 이 필드를 채우는 다른 모든 발행자가
   전부 "확정 시각" semantics를 따른다는 것도 이번에 함께 확인(pre-existing, 이번 주 버그
   아님 — Aggregator만 잘못 해석하고 있었음).

## 알려진 갭 (Aggregator·Meta Decision·Risk Engine·Sizer·Kill Switch, 2026-07-27)

- **Regime 가중치 매트릭스(Ver 1.2 §7.1)는 Walk-Forward 재추정 없이 원문 초기값 고정**:
  "이 표 자체가 학습 대상"(원문)이지만 재추정 파이프라인은 스코프 밖 — 분기회의 안건 후보.
- **불확실성 정규화가 앙상블 분산(ens_std)뿐**: Ver 1.2 §6의 두 번째 겹(Conformal Prediction)은
  여전히 실사용 이력 없음(G2부터, 기존 갭 유지) — Aggregator의 u_h는 그 전까지 근사치.
- **Meta Decision Engine의 ⑥⑦(Options AI 비교·상관노출 합산)은 코드 경로 자체가 없음**:
  Options AI(Ver 1.3)가 Phase 4(W27~31)까지 존재하지 않아 방향 의도만 낸다.
- ~~Risk Engine의 R4(오버나이트 증거금)·R6(오버나이트 자격) 미구현~~ — **2026-07-27
  Event Calendar 도입으로 구현 완료**(아래 "Event Calendar" 섹션 참고).
- **Risk Engine의 R7(순델타)·R8(순베가)·R9(매도옵션 손실) 미구현**: 전부 옵션 포지션·
  Greeks 자체가 없어서(Options AI 부재, Phase 4까지 계속 남는 갭).
- **Sizer의 Kelly 엣지는 대칭 페이오프(b=1) 가정 근사**: `edge=2p−1`은 정식 Kelly
  `(p·b−q)/b`의 단순화 — 실현 트랙레코드(Self Evaluation, Phase 5)가 쌓이면 실제 페이오프
  비율로 교체할 자리.
- **`point_value_krw`(50,000원/pt) 미실측**: 공개적으로 알려진 수치(정규선물의 1/5)이나
  KIS API로 직접 확인(계약승수 필드)한 적은 없음 — `futures_tick_size`와 동일한 성격의 갭.
- **TradingPipeline의 Net Expected Return은 명시적 근사**: 방향 확률만 내는 Expert 구조상
  크기(magnitude) 예측이 없어 `edge×ATR(M1,14봉)`으로 기대이동폭을 대신한다 — 원문이 정한
  공식이 아니라 이 구현의 선택(모듈 docstring에 근거 기록), 크기 예측 Expert나 실측
  캘리브레이션이 생기면 교체 대상.
- **R10(연속손실) 결선 없음**: `RiskEngine.record_trade_result()`를 실제로 호출하는 포지션
  추적기(Ver 1.1 §5-3 Position Reconciler)가 아직 없다 — `record_order_error()`(R12)만
  `gateway.submit()` 실패로 결선됨.
- **R12 주문오류율은 심볼 구분 없이 전역 집계**: 현재 단일 심볼(A05608) 운용이라 문제되지
  않으나, 멀티 심볼 확장 시(Ver 1.1 §7 복제 배포와 별개로 한 인스턴스가 여러 심볼을 다루게
  되면) 심볼별 분리가 필요.
- **Kill Switch의 청산은 시뮬레이션 브로커 기준으로만 검증됨**: 실제 KIS 계좌로 EMERGENCY
  시장가 청산 주문을 낸 적은 없다(capability_matrix.md "KIS" 섹션의 기존 실측 범위 밖).
- **Meta Decision Engine의 `latency_trace`는 재생/스모크에서 무의미할 수 있음**: `ts_utc`가
  봉 도메인 시각일 때 `now_utc()`와의 차가 실제 지연이 아니게 된다 — 순수 정보성 필드라
  게이팅에는 영향 없음(모듈 docstring에 명기).
- **run_full_path_smoke.py의 RegimeRuntime 워밍업은 학습 데이터 꼬리를 재사용한 인위적
  사전 채움**: 실제 운영은 기동 후 실제로 22개 30분봉(window+2)이 쌓일 때까지 자연스럽게
  UNKNOWN 구간을 거친다 — 스모크는 그 대기를 건너뛰기 위한 장치일 뿐 실제 기동 절차가
  아님(스크립트 docstring에 명기).

## Event Calendar — KRX 휴장일·세션 인식 (2026-07-27 신설)

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| core.event_calendar.EventCalendar | ✅ | ✅ 2026-07-27 | — | `is_trading_day`·`next_trading_day`·`previous_trading_day`·`is_regular_session`·`minutes_to_close`·`is_expiry_day`(요일 규칙 근사). 휴장일 데이터는 `configs/krx_holidays.yaml`(공개 집계 사이트 2곳 교차대조 — **KRX 공식 확인 아님**, 파일 헤더에 출처 한계 명기). 단위 테스트 25건(경계값·naive datetime 거부·연도 데이터 없음 시 예외·0-휴장일 연도 구분 등). |
| scripts/run_l1_daily.py — 휴장일 스킵 | ✅ | — | — | `main()` 시작 직후(self_check 이전) `is_trading_day()` 확인, 휴장일이면 KIS API 호출 없이 즉시 종료. 실측 미실행(다음 실제 휴장일까지 대기해야 자연 검증 가능 — capability_matrix "알려진 갭" 참고). `REGULAR_SESSION_STOP`을 `EventCalendar.DEFAULT_SESSION`에서 파생하도록 리팩터(단일 소스). |
| risk.risk_engine.RiskEngine — R4·R6 | ✅ | ✅ 2026-07-27 | — | R6(오버나이트 자격, 기본 10분): 장마감 임박 시 신규 진입 전면 거부(Holding Policy §2.2 Type A "무포 오버나이트"). R4(오버나이트 증거금, 기본 30분): R3 한도(40%)보다 이른 구간부터 `overnight_margin_cap_pct`(25%)로 강화. `minutes_to_close=None`(미지정)이면 둘 다 조용히 비활성 — 회귀 없음. 단위 테스트 6건 신규. |
| strategy.pipeline.TradingPipeline — event_calendar 주입 | ✅ | ✅ 2026-07-27 | — | 생성자에 `EventCalendar` 선택적 주입 — 주입 시 매 `handle_futures_view()`가 `minutes_to_close`를 계산해 RiskEngine에 전달. 단위 테스트 2건(주입 시 R6 발동·미주입 시 기존 동작 유지). |

### 알려진 갭 (Event Calendar, 2026-07-27)

- **`configs/krx_holidays.yaml`는 KRX 공식 공지가 아니라 공개 집계 사이트 교차대조 결과다**:
  official kind.krx.co.kr 접속이 막혀(403/JS 렌더링) 직접 확인 못함 — schtasks 자동화
  전에는 반드시 공식 공지로 재검증할 것(파일 헤더에 상세 기록).
- **수능일(11/19) 지연개장·연말 마지막 거래일(12/31) 조기폐장을 이진(거래일/휴장일)
  모델로 표현 못함**: 둘 다 "휴장"이 아니라 "다른 시간에 개장"이라 의도적으로 휴장일
  목록에서 제외 — `is_trading_day()`는 두 날 다 True를 반환하지만 실제 세션 시간은
  다르다(미모델링, 파일 헤더에 명기).
- **`is_expiry_day()`는 symbol_master 실측이 아니라 요일 규칙(매주 월/목 + 매월 2번째
  목요일) 근사**: 그 요일이 마침 KRX 휴장일과 겹칠 때 실제 만기가 어떻게 재조정되는지는
  다루지 않는다(event_calendar.py 모듈 docstring).
- **`rule_economic_event`(Regime 규칙층)는 여전히 미발동**: Event Calendar가 다루는 건
  "KRX가 문을 여는가"뿐 — `ev_econ_prox`/`ev_econ_grade`(FOMC·CPI 등 경제지표 캘린더)는
  완전히 별개의 외부 데이터 소스가 필요해 이번 스코프 밖(event_calendar.py 모듈 docstring
  "스코프 경계").
- **run_l1_daily.py의 휴장일 스킵 경로는 라이브 미검증**: 다음 실제 KRX 휴장일에 이
  스크립트가 실행돼야 자연 검증 가능 — 인위적으로 날짜를 조작해 검증하는 건 시스템
  시계 신뢰도를 흔드는 방식이라 보류.

## Walk-Forward 백테스트 하니스 (2026-07-27 신설)

W14~16부터 W24~26까지 남아있던 공통 갭("Digital Twin·Expert·Cost Model·Validator가 전부
갖춰졌지만 이들을 엮어 실제 성과 시계열을 뽑는 백테스트 하니스가 없다")을 처음 메웠다.

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| backtest.harness.run_walk_forward_backtest | ✅ | ✅ 2026-07-27 (합성 데이터) | — | `WalkForwardSplitter`(W12~13)로 창을 굴리며 매 창 `train_formal_expert()`(W17~19)로 재학습 → 검증구간을 `MultiHorizonBarComposer`+`FeatureEngine`+`FuturesAIService`+`TradingPipeline`+`SimBroker`(전부 `InProcessBus`)로 실시간 재생 → 창별 자산 변화를 기록. 단위 테스트 9건(순수 헬퍼 7·통합 2). `scripts/run_backtest_harness.py`로 실제 실행 확인: 실제 아카이브 시도(예상대로 데이터 부족 실패) → 17일치 합성 데이터로 창 2개 완료 → `Validator.validate_performance()`가 처음으로 실제 walk-forward 산출물을 입력받아 3개 관문(Sharpe·MDD·창별 일관성) 전부 계산(둘 다 무거래로 수익률 0%, FAIL/PASS 혼재 — 성능 주장 아님, "관문이 실행된다"만 확인). |
| data.bar_composer.MultiHorizonBarComposer — BusLike 타입힌트 | ✅ | ✅ 2026-07-27 | — | `bus: MessageBus`(구체클래스) → `bus: BusLike`(Protocol)로 교체 — `backtest/harness.py`가 `InProcessBus`를 넘기며 pyright가 "MessageBus 불일치" 오류를 냄(`features/engine.py`가 W14~16에 같은 이유로 겪은 것과 동일 사례). `publish`/`subscribe`만 써서 안전 확인 후 교체, 회귀 없음. |

### 알려진 갭 (백테스트 하니스, 2026-07-27)

- **일별(daily) granularity 없음**: 창 하나 = 기간 하나로 근사(`test_days`를 좁히면 세분화
  가능하나 그만큼 창마다 재학습 비용이 커짐) — `harness.py` 모듈 docstring에 근거 기록.
- **Regime AI 미포함**: `RegimeRuntime`을 배선하지 않아 `FuturesAIService`가 항상
  `Regime.UNKNOWN`으로 집계 — Regime까지 엮은 백테스트는 향후 확장 대상.
- **Deflated Sharpe 여전히 미제출**: `models/metrics.py`의 기존 갭 그대로, 이 하니스도
  3종(Sharpe·MDD·창별 일관성)까지만 `Validator`에 넘긴다.
- **실제 시장 데이터로 실행된 적 없음**: 실제 아카이브가 하루치뿐이라(기존 갭) 다개월
  walk-forward를 의미 있게 재현할 데이터가 없다 — 합성 데이터로 "루프가 실제로 도는가"만
  확인했다(성과 주장 아님).
- **`aggregate_to_horizon()`은 학습용 오프라인 지름길**: 검증구간 재생(`MultiHorizonBarComposer`
  실사용)과 다른 별도 구현이라, 이론상 두 로직이 서로 다른 버그를 가질 여지가 있다(둘 다
  표준 OHLCV 롤업이라 실무 영향은 작을 것으로 판단, 재검토 대상).

## 옵션/수급 데이터 인프라 착수 (Ver 1.3 Options AI 선행 갭, 2026-07-27 신설)

Phase 4(Options AI) 착수 전 필요한 인프라 갭 2건 중 착수 가능한 부분만 이번 스코프에서
처리했다 — "동일 계좌 WS 연결 2개 → 반복 단절" 구조적 해법(WS 재설계)과 REST 폴링
인프라(FixedTickScheduler 첫 실사용)이다. **옵션체인 그릭스(OP) 수집기·매크로/현물지수
(RG) 데이터 소스는 이번에도 착수하지 않음**(아래 "알려진 갭" 참고, 여전히 열린 항목).

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| data.collector.MultiSymbolTickCollector | ✅ | — | — | 단일 WS 연결에 여러 (심볼,TR) 조합을 동시 구독(`KISWebSocketClient.subscribe()`가 처음부터 지원하던 방식을 처음 실사용) — 2026-07-23 실측으로 확인된 "동일 계좌 WS 연결 2개(선물+옵션) → 양쪽 다 반복 단절" 문제의 구조적 해법. 원시 메시지의 TR_ID로 파서를 고르고, 파싱된 심볼로 올바른 tick_size 재확인 후 심볼별 aggregator로 라우팅. 단위 테스트 16건(연결 하나에 subscribe 2회 확인·TR별 라우팅·심볼별 tick_size 정확성·모르는 TR/심볼 무시·빈 feeds/슬롯초과 거부·다중 flush) — 전부 mock `WSConnection`. **라이브 미검증(검증 기한: 2026-08-14, [[l1_gap_deferral_to_weekly_review]]와 동일 이관 — 오늘 이 계좌로 `run_l1_daily.py`가 실제 라이브 수집 중이라 이 클래스의 실계좌 검증을 지금 하면 그 세션과 리소스를 다툰다)**. |
| data.investor_flow_poller.InvestorFlowPoller | ✅ | ✅ 2026-07-27 (구조만, mock REST) | — | `FixedTickScheduler`(W3~5, "아직 실제 폴러에 안 물려봄") 첫 실사용처 — 이미 실측된 `get_investor_flow()`를 감싸 주기적으로 호출·`raw.investor_flow.{market}`로 발행. **필드는 파싱하지 않는다**(아래 "알려진 갭" 참고) — `raw` dict 그대로 `InvestorFlowSnapshot`에 담아 보존. 단위 테스트 5건(sector_code별 순차 조회·부분 실패 시 계속 진행·발행 실패 로깅·빈 sector_codes 거부·FixedTickScheduler와 실제 연동). |
| core.messages.InvestorFlowSnapshot | ✅ | — | — | `market_code`/`sector_code`/`raw: dict[str, object]` — 필드 매핑 없는 원시 passthrough 스키마(신규). |

### 알려진 갭 (옵션/수급 데이터 인프라, 2026-07-27)

- **MultiSymbolTickCollector 실계좌 미검증**: 위 표 참고 — 오늘 라이브 세션과의 리소스
  경합을 피하려 의도적으로 보류. 다음 검증 기회에 futures(A05608)+option(임의 위클리
  종목) 동시 구독으로 실제 단절 재발 여부까지 확인할 것.
- **FL Feature(`fl_frgn_cum`/`fl_frgn_streak` 등, Ver 1.5 §3.5) 파싱 미구현**: KIS
  `get_investor_flow()` 응답의 구체 필드 의미(외국인/기관/개인 순매수 수량·거래대금이
  몇 번째 필드인지)를 확정할 근거(docs/efriend 엑셀 또는 실계좌 실측 캡처)가 이 세션엔
  없었다 — `InvestorFlowSnapshot.raw`에 원시 dict만 보존. 실측 캡처가 생기면
  `normalizer.py`에 `parse_investor_flow()` 유형 함수를 추가해 정규화할 것.
- **OP(옵션체인 그릭스) REST 폴링 수집기 미착수**: `get_quote()`(옵션)가 이미 실측
  완료돼 있지만(capability_matrix.md "KIS" 섹션), 이를 주기적으로 폴링해 그릭스·IV를
  쌓는 수집기 자체는 아직 없다 — `InvestorFlowPoller`와 같은 패턴(FixedTickScheduler+
  REST+발행)으로 별도 착수 가능, 이번엔 스코프에 안 넣음.
- **RG(현물지수·매크로: VIX·USDKRW 등) 데이터 소스 미착수**: `get_overseas_future_price()`가
  이미 있지만(rest_client.py) 이를 물린 폴러가 없다 — OP와 마찬가지로 별도 착수 대상.
  RG/OP 둘 다 완성되기 전까지는 15m/30m Expert가 Ver 1.5 배정의 절반 이하만 받는다는
  기존 갭이 그대로 남는다.
- **ATM±N 옵션 체인 구독 롤링(RollingSubscriptionManager 이식) 미구현**: `MultiSymbolTickCollector`는
  생성 시 고정된 심볼 목록만 구독 — 시세에 따라 체인을 동적으로 갈아끼우는 롤링은
  별도 과제(collector.py 모듈 docstring에 명기).

## Options AI — Vol Engine 착수 (Ver 1.3 §3~4, Ver 2.0 §9 W27~29, 2026-07-28)

Phase 4 착수. `data.option_chain_poller.OptionChainPoller`로 OP(옵션체인) REST 폴링 갭을
닫고, `strategy/options/` 신규 패키지에 Vol Engine(IV Surface·Vol Forecaster)·전략 매트릭스를
구현했다. KIS 원시 Greeks/IV 필드는 여전히 미해석(마흐디 L16 재발 방지 — 아래 참고) —
Black-76(선물 기준, 현물지수 피드가 없어 선택) 프라이서로 이 프로젝트가 직접 IV·Greeks를
계산한다.

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| data.option_chain_poller.OptionChainPoller | ✅ | ✅ 2026-08-04 | — | **2026-08-04 재작성 + 실계좌 검증 + run_l1_daily 결선.** 인스턴스 하나가 시리즈 하나를 맡아 ATM±N 창만 조회한다(전량은 1,356다리=22.6분이라 성립 불가 — 기준가를 못 구하면 **전량 폴백 없이** 사이클을 건너뛴다). 원천을 `get_asking_price()`→`get_quote()`로 교체: 실호출 대조 결과 전자는 5단계 호가만, 후자는 IV·델타·감마·**미결제약정**·이론가·잔존일수 + KOSPI200 현물(output3)을 준다 — 현 스코프 OP Feature(`op_iv_chg`/`op_pcr_vol`/`op_pcr_oi`/`op_gex`/`op_skew_rr25`)는 전부 후자 쪽이고 호가를 쓰는 것은 하나도 없다. 필드는 여전히 미해석(`raw` 보존). 단위 테스트 15건. **실계좌 end-to-end 실측**: 기준가(미니선물 998.08) → ATM±1 6다리 선택 → 실호출 6건(델타 콜 0.42~0.52 / 풋 −0.49로 ATM 정확히 중심) → 버스 → 아카이버 → parquet 6행×12컬럼, `_RateLimiter`가 1초 간격 페이싱하는 것까지 타임스탬프로 확인. |
| data.option_chain_archiver.OptionChainArchiver | ✅ | ✅ 2026-08-04 | — | 신규 — `raw.option_chain.*` 구독 → `data/option_chain/{series}/{date}.parquet`. output1/2/3을 전부 보존(`idx2_`/`idx3_` 접두어로 충돌 회피). **수급 아카이버와 달리 사이클 단위로 flush**한다 — 하루 3,276행이라 스냅샷마다 전체 재작성하면 `data/archiver.py`가 겪은 O(n²)를 반복한다. 단위 테스트 12건(시리즈별 파일·3블록 보존·중복 덮어쓰기·flush 임계·close() 잔여 flush·날짜 전환 시 이전 날 확정·적재 실패 격리·KST 되읽기). |
| data.last_price.LastPriceTracker | ✅ | ✅ 2026-08-04 | — | 신규 — `md.tick.{symbol}` 구독해 최신가를 **지수 포인트**로 보관, `OptionChainPoller`의 ATM 기준가 공급원. 틱↔포인트 환산을 한 곳에 모은다. 오래된 값(기본 180초)은 None을 돌려줘 폴러가 사이클을 건너뛰게 한다(WS 단절 후 옛 창을 계속 조회하는 것 방지). 베이시스 실측: 미니선물 998.08 vs KOSPI200 현물 1000.03 = −1.95pt = 행사가 0.8칸이라 ATM±10 창에서 무해. 단위 테스트 7건. |
| core.messages.OptionQuoteSnapshot / GreeksProfile | ✅ | — | — | 전자는 원시 시세호가 passthrough(가격도 미해석 — bid/ask 필드명 unverified), 후자는 `surface.py`가 계산한 Greeks 전용 값 객체(단위 docstring에 명시, L16 백신). |
| strategy.options.surface (Black76 프라이서·IV 역산·스마일 피팅) | ✅ | — | — | `math.erf` 기반 정규분포, Newton-Raphson+이분법 IV 역산, `numpy.polyfit` 2차 스마일(SVI 단순화, 문서화됨), `find_strike_for_delta()`(25Δ 탐색). Greeks는 델타/감마/베가 해석식 + theta는 유한차분(자기 자신과의 정합성 우선, 손으로 옮긴 공식의 부호실수 리스크 회피). 단위 테스트 31건 — 특히 델타/감마/베가를 프라이서 자체의 유한차분과 교차검증(손으로 옮긴 해석식이 프라이서와 내적으로 일치함을 보장), put-call parity, IV round-trip. |
| strategy.options.vol_metrics (IVHistory·Skew·Term Structure·실현변동성·IV-RV) | ✅ | — | — | 전부 순수 함수 + `IVHistory`(deque 롤링, DB 의존 없음). 단위 테스트 9건, 전부 손계산 known-value. |
| strategy.options.vol_forecast (HAR-RV 기준모델) | ✅ | — | — | Corsi HAR-RV, `numpy.linalg.lstsq` 닫힌 형태 OLS. LightGBM 잔차 보정은 미구현(아래 갭). 단위 테스트 4건 — 알려진 계수로 잡음 없이 생성한 시계열을 fit해 계수를 그대로 복원하는지 검증(강한 정합성 테스트). |
| strategy.options.matrix / config (전략 후보 매트릭스) | ✅ | — | — | 방향×IV 3×3 매트릭스. **Ver 1.3 §4.1 원문의 "풋매도"/"콜매도"/"Strangle 매도"(네이키드) 라벨을 쓰지 않고 처음부터 `BULL_PUT_SPREAD`/`BEAR_CALL_SPREAD`/`IRON_CONDOR`로 치환**(§6-1 "네이키드 매도 금지, 예외 없음"과의 충돌을 매트릭스 생성 단계에서 해소 — `matrix.py` 모듈 docstring 참고). `configs/options.yaml`은 만들지 않음 — 이 저장소의 실제 관습(dataclass 기본값)을 따름(Ver 1.3 §10 문서와의 의도적 차이). 단위 테스트 19건(매트릭스 9칸 전수 + CandidateSpec 필드 + skew 필터). |

### 알려진 갭 (Options AI Vol Engine, 2026-07-28)

- **OptionChainPoller 라이브 미검증**: `get_asking_price()` 응답의 실제 필드 매핑이 없어
  이번 세션은 "raw 그대로 발행"까지만 — 실계좌로 돌려 원시 payload를 수집하고 필드를
  확정하는 것이 다음 단계(FL Feature 갭과 동일 성격, docs/efriend 엑셀 또는 실측 캡처 필요).
- **surface.py는 아직 실데이터에 물려본 적 없음**: 프라이서·피팅 로직 자체는 known-value·
  유한차분 교차검증으로 강하게 검증됐지만, `OptionQuoteSnapshot.raw`에서 실제 bid/ask를
  뽑아 `fit_smile()`에 넣는 배선은 위 필드매핑 갭이 풀려야 가능하다.
- **RG(현물지수) 대신 근월 선물가로 Black-76을 쓰는 설계 선택**: 현물지수 피드가 없다는
  기존 갭(위 "옵션/수급 데이터 인프라" 섹션)의 대응으로 Black-Scholes 대신 Black-76을
  택했다 — 이론적으로 다른 근사가 아니라 관측 가능한 입력에 맞춘 정합적 선택이지만, 향후
  현물지수 피드가 생기면 재검토 대상.
- **vol_forecast의 LightGBM 잔차 보정 미구현**: HAR-RV 기준모델만 완성 — Trainer/Validator/
  Registry 연결은 `HorizonExpert`급 별도 작업(Ver 1.3 §9, 알려진 갭으로 명시).
- **matrix.py의 CALENDAR 구조는 DTE/델타 규칙이 자리만 있음**: 다른 만기를 걸치는 구조라
  단일 만기 기준 델타/DTE 로직으로는 표현이 부족 — evaluator.py(다음 서브페이즈) 확장 시
  다룰 자리.
- **Evaluator·Safety·Lifecycle·Risk Engine R7-R9 연결은 다음 서브페이즈(W30~31)**: 이번엔
  후보 "생성 파라미터"(`CandidateSpec`)까지만 — 실제 다리(strike/만기) 구성과 평가는 아직
  없다.

## Options AI — Evaluator·Lifecycle·안전규칙 + Risk Engine R7-R9 (Ver 1.3 §5~8, Ver 2.0 §9
W30~31, 2026-07-28)

`intel.options`까지 전 경로 관통. `OptionsAIService`가 `bar.5m`/`intel.futures`를 구독해
매트릭스→평가→안전규칙 필터→상위 3개(또는 명시적 `NO_OPTION`) 발행까지 끝낸다. Risk Engine에
R7(순델타)·R8(순베가) 게이트와 R9(매도옵션 손실) 탐지도 추가됐다 — Ver 2.0 §5 한도표 R1~R12
전 항목이 이제 최소 하나의 형태로는 구현됐다(R1은 원래부터 Sizer 상한으로 구조적 강제,
`risk_engine.py` 모듈 docstring).

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| strategy.options.evaluator (다리 구성·시나리오 그리드·순위화) | ✅ | — | — | `build_legs()`가 매트릭스 스펙 + 스마일로 실제 행사가를 찾고, 시나리오 그리드(가격 21×IV 7)로 확률가중 손익·POP을 계산. **Max Loss는 그리드가 아니라 구조에서 직접 계산**(모듈 docstring — 항상 유한, §6-1 준수 구조만 들어오므로). 개발 중 **신용 스프레드 델타 배정 버그를 자체 발견·수정**: Ver 1.3 §4.2 델타 배정("매도=15~30Δ, 매수=30~50Δ")을 문자 그대로 신용 스프레드에 적용하면 매도/매수 행사가 순서가 뒤집혀 구조 자체가 무효가 됨을 계산으로 확인 — `matrix.py`가 신용/차변 구조에 따라 델타 밴드를 다르게 배정하도록 수정(두 모듈 docstring에 근거 기록). 단위 테스트 17건(4개 구조 행사가 순서 검증 포함). |
| strategy.options.safety (§6 Hard Rules) | ✅ | — | — | 독립 모듈 — `matrix`/`evaluator`/`lifecycle` 어느 것도 import 안 함. §6-1(네이키드)·§6-2(credit IV<50)·§6-4(만기일 진입금지)는 완전 구현, §6-3(이벤트 캘린더)는 매크로 이벤트 피드 부재로 부분 구현(호출측이 명시적으로 넘겨야 판정, 모듈 docstring). 단위 테스트 18건. |
| strategy.options.lifecycle (수명주기 상태기계) | ✅ | — | — | 순수 함수 `evaluate_position()` — 손절(safety.py 재사용)＞만기강제청산＞이벤트전매수청산＞조정＞이익실현＞보유 우선순위. 롤오버는 신호만(실제 롤 다리 구성은 정규 경로 재심사 필요, §8 "관성으로 롤 금지"). Weekly 전용 취급은 미구현(알려진 갭). 단위 테스트 16건. |
| strategy.options.hedging (밴드 델타 헤징) | ✅ | — | — | `compute_hedge_qty()` 순수 함수. 방향 중립 구조(IRON_CONDOR·CALENDAR)만 대상. 만기 임박 고감마 구간 밴드 자동 축소 구현. 단위 테스트 7건. |
| strategy.options.service.OptionsAIService | ✅ | — | — | `bar.5m`+`intel.futures` 구독 → `intel.options` 발행. **`smile_provider` 콜백으로 데이터 출처 분리**(핵심 설계 — OP 필드 미해석 갭 때문에 실시간 스마일 배선이 아직 없어, 백테스트/테스트가 합성 스마일을 주입해 나머지 로직 전부를 검증할 수 있게 함, `broker.base.BrokerAdapter`와 같은 "동일 인터페이스" 철학). `intel.futures` 미수신 시 항상 NO_OPTION(낙관적 중립 해석 금지). 단위 테스트 9건. |
| risk.risk_engine.RiskEngine.evaluate_options_portfolio (R7·R8) | ✅ | — | — | `evaluate()`와 분리된 별도 메서드(선물 단일 심볼 흐름과 옵션 포트폴리오 집계는 성격이 달라 억지로 합치지 않음). `.greeks` 없는 포지션은 집계 제외. R8은 옵션 전용 계약승수 미실측이라 선물과 같은 placeholder(`DEFAULT_POINT_VALUE_KRW`)를 잠정 적용. 단위 테스트 8건. |
| risk.risk_engine.RiskEngine.positions_requiring_forced_liquidation (R9) | ✅ | — | — | `strategy/options/safety.exceeds_loss_limit()`(§6-5)를 그대로 재사용 — 같은 규칙이 두 곳에서 따로 구현되어 어긋나는 위험 제거. 탐지만 하고 청산 주문 구성은 안 함(옵션 실행 경로 없음, 아래 갭). 단위 테스트 4건. |
| core.messages (StrategyLeg/StrategyCandidate/OptionsView) | ✅ | — | — | 전부 지수 포인트 단위(KRW 아님) — 옵션 계약승수 미실측이라 소비측이 환산하게 함. |
| broker.base.BrokerPosition.greeks | ✅ | — | — | 추가 필드(기본 None), 기존 호출부 전부 keyword 생성이라 하위호환 깨짐 없음(실측 확인). |

### 알려진 갭 (Options AI Evaluator~Risk Engine, 2026-07-28)

- **옵션 주문 실행 경로 자체가 없음**: `Sizer`/`OrderRequest`가 단일 심볼 수량만 지원 —
  다리 여러 개짜리 옵션 스프레드 주문 구성·제출은 이번 스코프 밖(계획 문서에 명시된 경계).
  `BrokerPosition.greeks`를 실제로 채우는 어댑터도 없어 R7~R9는 게이트만 준비된 상태.
- **`MetaDecisionEngine` 규칙 ⑥⑦(Options AI 비교·상관노출 합산) 미착수**: rule ⑥은 선물·옵션
  Net ER을 비교해야 하는데 그 계산이 `pipeline.py`에서 `MetaDecisionEngine.decide()` 호출
  *이후*에만 이뤄져 — 이미 검증된 실거래 경로를 흔드는 재구조화가 필요해 이번 스코프에서
  의도적으로 제외(계획 문서에 근거 기록). `TradingPipeline`에 Options AI를 실제로 엮는
  작업도 마찬가지로 미착수.
- **CALENDAR 구조는 여전히 자리만 있음**: 단일 만기 가정의 `evaluator.py`로는 다른 만기를
  걸치는 구조를 정확히 다룰 수 없다(`evaluator.py` 모듈 docstring "spec.dte_low가 곧 채택
  DTE다" 절 참고).
- **§6-3(이벤트 캘린더)·§8(IV Crush 사전청산)은 매크로 이벤트 피드가 있어야 실제로 작동**:
  `core/event_calendar.py`가 KRX 개장일만 알고 FOMC/CPI는 모른다(그 모듈 자체의 기존 갭) —
  `is_macro_event_window` 파라미터는 항상 호출측이 명시적으로 넘겨야 하고, 지금 이 프로젝트
  어디에도 그 값을 실제로 채워주는 소스가 없다.
- **`OptionsAIService`는 만기 1개짜리 `SmileFit`만 다룬다**: 여러 만기 IV Surface를 동시에
  들고 목표 DTE에 가장 가까운 것을 고르는 로직이 없다 — Term Structure(`vol_metrics.
  term_structure()`)는 순수 함수로 존재하지만 서비스 배선에는 아직 안 물려있다.

## Command Center UI — Streamlit 1단계 프로토타입 (Ver 1.0.1 §3, Ver 2.0 §9 W32~34, 2026-07-28)

핵심 4존 + 고정 상단바 전부 렌더링. `streamlit.testing.v1.AppTest`(공식 테스트 API)로 실제
스크립트 실행을 예외 없이 검증 — 이 작업 계획 문서는 "Streamlit 앱은 일반 pytest로 못
테스트한다"고 가정했지만 실제로는 가능함을 확인(계획 대비 개선점).

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| ui.state_cache (StateCache·CacheSubscriber) | ✅ | — | — | 스레드 세이프 최신값 저장소. `InProcessBus`로 pub/sub 배선 검증. 단위 테스트 8건. |
| ui.data_source (LiveDataSource·ReplayDataSource·배지) | ✅ | — | — | LIVE/REPLAY는 사용자가 명시적으로 고른다(L18 "폴백은 시끄럽게") — REPLAY는 메시지 자체 타임스탬프가 아무리 최신이어도 절대 LIVE 배지를 내지 않음을 테스트로 고정. 화면별 신선도 임계값 오버라이드 지원. 단위 테스트 11건. |
| ui.app (Command Center Streamlit 앱) | ✅ | ✅ 2026-07-28(AppTest) | — | 고정 상단바(모드·배지·KILL SWITCH 2단 확인) + 4존(① AI Decision ② Market View ③ Position & Risk ④ Bottom). 캔들 차트는 LIVE/REPLAY 공용으로 항상 Parquet에서 읽음(`ParquetArchiver`가 유일한 진실원천). Stream 토픽(`decision.intent`/`exec.fill`)은 pub/sub 구독으로 못 받는다는 것을 확인해 별도 `read_stream()` 폴링 루프로 분리 배선. **AppTest로 실제 검증**: REPLAY 기본 모드 무예외, LIVE 전환(Redis 없이도 백그라운드 스레드 예외가 메인 스레드로 안 새어나옴) 무예외, Kill Switch 2단 확인 클릭 흐름 무예외 — 스모크 테스트 3건. |

### 알려진 갭 (Command Center UI, 2026-07-28)

- **실제 브라우저·실제 Redis로는 아직 검증 안 됨**: AppTest는 스크립트 실행 자체의 예외
  유무만 확인한다 — 실제 브라우저 렌더링(레이아웃 깨짐 등)이나 실제 Redis에 연결된 LIVE
  모드의 배지 갱신은 사람이 눈으로 확인해야 하는 다음 단계.
- **Position & Risk 존이 `broker.positions()`를 조회하지 않음**: 계좌 실시간 연동 자체가
  아직 없다(화면에 그 사실을 명시적으로 표시) — Options AI 실행 경로 부재와 같은 성격의 갭.
- **KILL SWITCH 버튼이 `sys.kill`을 실제로 발행하지 않음**: 2단 확인 UI 흐름은 완성됐지만,
  확인 후 실제 버스 발행 배선은 다음 단계(현재는 사용자에게 "알려진 갭"임을 명시적으로 알림
  — L18과 같은 "조용히 가짜로 성공한 척하지 않는다" 원칙).
- **이벤트 캘린더·Self-Evaluation 미니보드는 자리만**: 각각 `EventCalendar` 미배선, Phase 5
  Self Evaluation 자체가 아직 없음 — 화면에 이미 "구현 안 됨"으로 명시.
- **옵션 IV Surface 3D/히트맵·마이크로 탭은 이번 MVP에 없음**: Ver 1.0.1 §3.2 ③의 옵션
  탭·마이크로 탭 상세는 다음 반복 대상으로 남김(계획 문서에 이미 명시된 경계).

## Registry·Shadow Manager·Self Evaluation·Release 패키징 (Ver 1.1 §6-3/6-4·Ver 1.6 §9·
Ver 2.0 §7·§9 W35~38, 2026-07-28)

> **선행 조사(2026-07-28)**: 사용자가 "Phase 5를 구현해서 페이퍼 운영을 시작하고 손익을
> 조사"를 요청 — 조사 결과 실제 수집 데이터가 3거래일치뿐이고 어떤 Expert도 G1 백테스트
> 관문을 실제 데이터로 통과한 적이 없어(기존 갭들 참고) "오늘 손익을 조사"는 원천적으로
> 불가능함을 먼저 보고, 사용자가 "Phase 5 인프라만 구현"으로 스코프를 명시적으로 확정했다.
> 아래 표는 전부 이 합의된 스코프(합성 데이터 배관 검증) 기준이다.

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| models.registry.ModelRegistry | ✅ | ✅ 2026-07-28(스모크·수동 재현) | — | SQLite(표준 라이브러리) 기반 candidate→shadow→live→retired 상태기계(Ver 1.6 §9.2). live는 Horizon당 정확히 1개(`promote_to_live()`가 이전 live 자동 retired, 레코드·파일 보존). 버스 발행은 `drain_events()`로 큐만 쌓고 호출자가 원하는 시점에 publish(동기/비동기 경계 명시적 분리). 단위 테스트 12건. **실측**: `run_phase5_smoke.py`로 실제 candidate 등록→shadow→live 승격 전 구간 실행, `self_check.py`의 `check_registry_consistency`를 정상 릴리스(PASS)·강등된 번들을 가리키는 stale 릴리스(FAIL) 양쪽 다 수동 재현으로 확인 |
| models.registry.pack_bundle/load_manifest/load_expert/load_meta_labeler | ✅ | ✅ 2026-07-28 | — | Ver 1.6 §9.1 번들 스펙(manifest.yaml+feature_set.yaml+thresholds.yaml+validation_report.json)을 따르되, 아티팩트 파일명은 원안(`experts/e1.lgb`)이 아니라 기존 `HorizonExpert.save()`/`MetaLabeler.save()`의 stem 기반 멀티파일 포맷을 그대로 재사용(중복 직렬화 로직 방지 — manifest.yaml이 실제 파일명의 진실 원천). 단위 테스트 다수(round-trip 포함) |
| models.registry.save_conformal_state/load_conformal_state | ✅ | ✅ 2026-07-28 | — | 번들의 "매일 갱신되는 유일한 부분"(Ver 1.6 §9.1) — 갱신 자체는 아직 어떤 운영 루프도 안 함(아래 알려진 갭) |
| models.release.pack_release/verify_release | ✅ | ✅ 2026-07-28(스모크) | — | Registry(Horizon 하나짜리 번들 상태기계)와 `configs/instance.yaml`의 `model_bundle`(Ver 1.1 §7.2, 여러 Horizon의 live를 한 시점 스냅샷으로 묶는 배포 단위)을 잇는 상위 계층. 부분 릴리스(일부 Horizon만 live)를 명시적으로 허용해 `missing_horizons`에 기록 — 지금은 전 Horizon이 빈 게 정상(G1 미통과). `verify_release()`는 릴리스 발행 후 참조 번들이 강등되는 "번들 손상 배포"를 감지(Ver 1.6 §12). 단위 테스트 5건 |
| models.shadow_manager.ShadowLedger/ShadowManager | ✅ | ✅ 2026-07-28 | — | Ver 1.1 §6-4 "shadow 상태 모델들에 실시간 Feature 공급, 가상 주문 성적 기록". 실주문 경로(Risk Engine/Sizer/OrderGateway)를 타지 않고 독립적인 단순 청산 규칙(시간배리어만 재사용, `models/labeling.py` BARRIER_PARAMS) — 챔피언과의 "상대 비교" 근사이지 재현이 아님을 모듈 docstring에 명시. 단위 테스트 11건. **실측**: `run_phase5_smoke.py`의 유기적 재생(60~300 M1봉)에서는 예측력 없는 합성 데이터라 자연 체결 0건(예상된 결과, 기존 갭과 동일 이유) — `ShadowLedger`를 직접 강신호로 시연해 메커니즘 자체가 실제 `ShadowFill`을 만듦을 확인 |
| models.shadow_manager.evaluate_promotion | ✅ | ✅ 2026-07-28 | — | Ver 1.1 §6-4 승격 규칙(20거래일+Net Sharpe 우위+MDD 한도) 그대로 — **자동 제안만, 승격 실행은 안 함**(`ModelRegistry.promote_to_live()`를 사람이 호출해야 실제 승격). 챔피언/Shadow 수익률 시계열은 입력으로만 받음(Position Reconciler 부재로 이 함수가 직접 못 만듦, 아래 알려진 갭과 동일 근본 원인). 단위 테스트 3건 |
| models.self_evaluation.run_self_evaluation | ✅ | ✅ 2026-07-28 | — | 승률·PF·Sharpe·MDD 집계(Ver 2.0 §7). Regime 정확도는 "관측 불가능한 잠재상태라 정답 정의 자체가 없다"는 이유로 명시적 스코프 제외. 단위 테스트 다수 |
| models.self_evaluation.reconcile_slippage | ✅ | ✅ 2026-07-28 | — | Ver 2.0 §6 "체결 품질 기록 → Cost Model 자기보정" 루프의 실제 계산부 — `OrderRequest.msg_id`→`OrderAck.request_id`→`Fill.broker_order_no` 3단 매칭, 지정가 주문만 대상(시장가는 "의도 가격" 개념 자체가 없음). 단위 테스트 4건 |
| models.metrics — win_rate/profit_factor/equity_curve_from_returns | ✅ | ✅ 2026-07-28 | — | 기존 순수함수 설계 원칙 유지(labeling.py 비의존) — Self Evaluation·Shadow Manager 공유. 단위 테스트 8건 |
| simulator.engine.LiveSimBrokerFeed | ✅ | ✅ 2026-07-28 | — | `bar.1m.{symbol}`을 **구독만** 해 `SimBroker.on_bar()`→`OrderGateway.on_fill()`로 잇는 다리 — 기존 `DigitalTwinEngine`(유한 봉 시퀀스 배치 재생+자체 발행)과 달리 상시 운영(G2)용. 단위 테스트 4건 |
| scripts.self_check — check_registry_consistency | ✅ | ✅ 2026-07-28(수동 재현) | — | live 모드에서 릴리스가 가리키는 각 Horizon 번들이 Registry상 지금도 live인지 교차검증. 정상/stale 릴리스 둘 다 수동 재현 확인(위 ModelRegistry 행 참고) |
| scripts.agenda — Self Evaluation·승격 제안 섹션 | ✅ | ✅ 2026-07-28(가짜 로그로 렌더링 확인) | — | `logs/self_eval_*.json`·`logs/promotion_proposals.jsonl`을 읽는 4·5번째 섹션 신규 |
| scripts/run_phase5_smoke.py | ✅ | ✅ 2026-07-28 | — | Registry pack/register/promote(candidate→shadow→live) → Shadow Manager 가상체결 → Self Evaluation → 승격 제안 → Release 패키징+정합성검증까지 전 경로 1회 실행 확인(`run_full_path_smoke.py`와 동일 패턴, 합성 데이터 — 실제 우위 검증 아님) |
| scripts/run_g2_paper_trading.py | ✅ | — (구조만, 미실행) | — | `run_l1_daily.py` 구조 확장(웜업→정규장→장후종료+Self Evaluation). **오늘 실행해도 거래가 안 남**(Registry에 live 번들 0개) — 증명 대상은 "시스템 무중단"(G2 통과기준)이지 우위가 아님을 모듈 docstring에 명시. Regime AI 미결선(RegimeState 미수신 시 UNKNOWN 기본값 → Meta Decision 규칙②로 안전하게 NO TRADE). 챔피언 일일수익률은 Position Reconciler 부재로 "포트폴리오 평가액 변화율" 근사(거래별 실현손익 아님) |
| scripts/run_replication_rehearsal.py | ✅ | ✅ 2026-07-28(실제 messiah-redis) | — | 실제 2번째 PC 없이 "설정 파일 하나로 인스턴스 분리"(Ver 1.1 §7.2) 자체를 검증 — 서로 다른 instance_id·자본의 InstanceConfig 2개를 각각 self_check 통과시키고 실제 Redis로 Health 발행 → instance_id 일치 확인. 실행 결과 PASS 2/2 |
| install.ps1 + docker-compose.yml | ✅ | — (구성만, `up -d` 미실행) | — | Ver 1.1 §7.3 "설치는 명령 한 번" — Redis 기동(messiah-redis 포트 6380 그대로 코드화)→venv→`pip install -e .[ml,ui,dev]`→self_check(무Redis→Redis)→`run_replay.py` 스모크. `docker compose config`로 문법만 검증(기존 수동 기동 컨테이너와 이름이 같아 실제 `up -d`는 재생성 위험 — 사용자 확인 후 실행) |

## lightgbm 극소 표본 학습 폴드 크래시 (2026-07-28, `models/search.py` 방어 코드로 해결)

3m Horizon 실제 아카이브(11봉→Triple Barrier 레이블 6건)로 `search_hyperparameters
(n_splits=2)`를 실행하면 `PurgedKFold`의 purge/embargo가 한쪽 폴드의 학습 표본을 정확히
1행까지 깎는다 — `bagging_freq=1`(고정)에 생산 탐색공간(`bagging_fraction` 0.5~0.9)이
그 1행을 `floor(1×fraction)=0`행으로 반올림시켜 LightGBM이 `Check failed: (num_data) >
(0)`로 네이티브 크래시한다(`lightgbm.basic.LightGBMError`, Python 예외가 아니라 C++ 단의
치명적 오류라 기존 "빈 폴드는 skip" 가드로도 못 막았음). **재현**: `x`가 3행뿐이고
`n_splits=2`면 한쪽 폴드의 train이 항상 정확히 1행이 되어 `bagging_fraction`이 (0,1) 구간
어떤 값이어도 결정론적으로 재현된다(`tests/models/test_search.py::
test_search_survives_single_row_training_fold_bagging_crash`). **해결**: `objective()`의
`lgb.train()` 호출을 `try/except lgb.basic.LightGBMError: continue`로 감싸 그 폴드/그
trial만 건너뛰고 전체 탐색은 계속되게 함. 프로덕션 데이터 규모(Ver 1.2 §8.1 "2년치")에서는
폴드가 이 정도로 작아지지 않아 실무상 발생하지 않는 경계 조건 — 다만 이 프로젝트는
당분간 실측 아카이브가 계속 작을 것이므로(데이터 축적이 유일한 근본 해법) 방어 코드
자체는 계속 유효하다.

## 알려진 갭 (Registry·Shadow Manager·Self Evaluation·G2 하네스, 2026-07-28)

- **G1 백테스트 관문을 실제 데이터로 통과한 모델이 전무하다**: 실측 아카이브가 3거래일치뿐
  이라(Ver 1.2 §8.1 "최소 확보 목표: 2년치"에 한참 못 미침) `ModelRegistry`가 완전히
  비어있는 게 정직한 현재 상태 — Registry/Shadow/Self Evaluation 코드 자체는 다 있지만
  "실제로 태울 검증된 모델"이 없다. 데이터 축적(매일 `run_l1_daily.py` 운영)이 유일한 해법.
- **G2 하네스에 Regime AI가 결선되지 않았다**: 실측 데이터 부족으로 학습된 `RegimeAI`
  인스턴스가 없다(W20~21 기존 갭) — `run_g2_paper_trading.py`는 의도적으로 Regime 미결선
  상태로 두되, `RegimeState` 미수신 시 `FuturesAIService`가 UNKNOWN 기본값을 쓰는 기존
  안전장치(Meta Decision 규칙② "Regime=UNKNOWN → NO TRADE")에 기댄다.
- **챔피언 일일수익률이 거래별 실현손익이 아니라 포트폴리오 평가액 변화율 근사다**:
  Position Reconciler(Ver 1.1 §5-3, 진입가·청산가 매칭기)가 없어(기존 갭,
  `strategy/pipeline.py` 모듈 docstring과 동일 원인) `evaluate_promotion()`·
  `run_self_evaluation()`이 정확한 거래별 손익 대신 하루 1개 표본(그날 시작/종료
  `SimBroker.account().total_equity` 변화율)으로 근사한다 — G2 "40거래일" 관찰이 쌓여도
  Sharpe/MDD가 거친 근사치임은 변하지 않는다.
- **Self Evaluation의 슬리피지 대사가 아직 실제 하루치 OrderRequest/OrderAck/Fill 이력을
  모으지 않는다**: `run_g2_paper_trading.py`는 이 세 시퀀스를 빈 값으로 호출 —
  `reconcile_slippage()` 계산 로직 자체는 검증됐지만(단위 테스트) 실제 운영에서는 항상
  "예측값만, 실현 0건"으로 찍힌다. 하루치 주문/체결 이력을 수집하는 배선이 다음 착수 항목.
- **Conformal 상태(`conformal_state.json`) 갱신이 아직 어떤 운영 루프에도 안 붙어 있다**:
  Ver 1.6 §6.2가 요구하는 "매일 갱신되는 (예측확률,실제결과) 이력"을 만들려면 예측 로그를
  사후에 Triple Barrier로 재라벨링하는 별도 파이프라인이 필요(아직 설계 안 함) —
  `save_conformal_state()`/`load_conformal_state()`는 파일 I/O만 준비된 상태.
- **`docker compose up -d`를 이번 세션에 실제로 실행하지 않았다**: 기존에 수동
  (`docker run`)으로 띄워둔 `messiah-redis` 컨테이너와 이름이 같아, compose가 이를
  인식 못 하고 재생성하려 하면 실행 중인 운영 인프라에 영향을 줄 위험이 있어 의도적으로
  보류(`docker compose config`로 문법만 검증) — 사용자 확인 후 실행할 것.
- **Windows 작업 스케줄러에 `run_g2_paper_trading.py`를 등록하지 않았다**: `run_l1_daily.py`
  와 같은 이유(매일 무인 실제 API 호출 자동화는 사용자 확인 후) — 지금은 수동 실행 전용.

## Task Scheduler·Docker 자동화 점검 + Docker Desktop 자가 기동 (2026-07-29)

사용자 요청으로 등록된 "Messiah"/"Messiah-Shutdown" 작업 스케줄러 + Docker를 감사.

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| Task Scheduler "Messiah"(평일 08:35, `run_l1_daily.bat`) | ✅(이미 등록돼 있었음) | ✅ 2026-07-29 감사 | ✅ | `schtasks`/`Get-ScheduledTask`로 확인 — 최근 실행 전부 성공(Last Result 0). 로그 실측(07-27·07-28): 08:35 정각 자동 트리거, CRITICAL 0건, "정상 종료"까지 완주. `run_l1_daily.bat`의 "Not yet registered" 주석은 stale 문서였음(정정) |
| Task Scheduler "Messiah-Shutdown"(평일 15:40, `stop_l1_daily.bat`) | ✅(이미 등록돼 있었음) | ✅ 2026-07-29 감사 | ✅ | 5회 실행 전부 "no leftover process found" — 본 스크립트 내부 종료 로직이 지금까지 한 번도 15:40을 넘긴 적 없어 순수 안전망으로만 존재(설계대로 작동) |
| core.docker_bootstrap.ensure_docker_ready | ✅ | ✅ 2026-07-29 | — | `docker info`로 daemon 응답 확인 → 미응답이면 Docker Desktop 실행 후 최대 2분(기본값) 폴링 → 준비되면 `docker start messiah-redis`로 컨테이너까지 재확인. 시간 초과 시 조용히 진행하지 않고 `ready=False` 반환. `runner`/`popen`/`sleep`/`now` 전부 주입 가능(`FixedTickScheduler`와 동일 설계) — 단위 테스트 11건. 실제 실행 중인 Docker로 직접 호출해 `already_running=True` 즉시 반환 확인 |

### 알려진 갭 (Task Scheduler·Docker, 2026-07-29)

- **Docker Desktop `AutoStart=False`였던 근본 원인**: 지금까지는 사용자 확인 결과 07:30에
  **다른 프로젝트**가 자기 필요로 Docker Desktop을 띄우는 부수효과에 실질적으로 기대고
  있었다(`ensure_docker_ready()`가 이 의존성을 없앰 — MESSIAH가 이제 스스로 확인·기동).
- **Task 트리거가 `LogonType=Interactive`·`StartWhenAvailable=False`·`WakeToRun=False`**:
  PC가 꺼져있거나 로그오프 상태로 08:35/15:40을 지나치면 그날은 캐치업 없이 영구히
  건너뛴다 — 이번 세션에서는 사용자가 요청하지 않아 미해결(Task Scheduler 로그온 방식을
  Interactive→S4U로 바꾸려면 계정 비밀번호를 이 도구에 입력해야 해 사용자가 직접 설정하는
  편을 권장했었음).
- **실패 시 능동적 알림이 없다**: Docker/Redis 기동 실패나 self_check 실패가 로그에는
  정직하게 남지만(자가점검 원칙) 텔레그램/이메일 등 push는 없다(Ver 1.1 §2 OBS "CRITICAL:
  텔레그램 푸시"가 아직 미구현) — 사람이 로그를 확인해야만 발견 가능.

## Command Center UI 데일리 자동화 통합 (2026-07-29)

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| run_l1_daily._launch_ui | ✅ | ✅ 2026-07-29 | ✅ | 거래일 확인 직후 Streamlit(`ui/app.py`)을 별도 백그라운드 프로세스로 기동. `MESSIAH_SKIP_UI=1`로 생략 가능, 기동 실패해도 데이터 수집은 계속(부가 기능이 전제조건 아님). 실제 실행으로 확인: 로그에 PID 출력, 포트 8501 LISTENING, 기본 브라우저 자동 접속(ESTABLISHED 커넥션)까지 확인 |
| stop_l1_daily.bat — UI 프로세스도 정리 | ✅ | ✅ 2026-07-29 | ✅ | 워치독의 명령줄 패턴 매칭에 `*messiah\ui\app.py*` 추가. 실제 실행으로 확인: 관련 프로세스 3개(streamlit 콘솔스크립트 stub + venv python.exe + anaconda base python.exe — stub의 내부 실행 방식) 전부 정확히 찾아 강제 종료, 이후 포트 8501·PID 소멸 재확인 |

### 알려진 갭 (Command Center UI 자동화, 2026-07-29)

- **UI는 여전히 REPLAY 기본 모드로 뜬다**: `ui/app.py`의 기존 원칙(LIVE는 사용자가 사이드바
  에서 직접 전환, 자동 전환 없음 — L18)을 이번 통합이 바꾸지 않았다 — 자동 기동되지만
  자동으로 LIVE를 보여주지는 않는다.
- **`run_g2_paper_trading.py`에는 통합 안 함**: 이번엔 `run_l1_daily.py`만 요청받아 그것만
  변경 — G2 스크립트도 원하면 같은 패턴(`_launch_ui()` 재사용)으로 쉽게 확장 가능.
- **streamlit 콘솔스크립트 stub이 왜 anaconda base 인터프리터까지 fork하는지는 조사 안 함**:
  이번엔 "죽이는 데 문제없다"만 확인했지, 그 프로세스 트리 구조 자체(venv가 anaconda3
  base에서 파생됐다는 `pyvenv.cfg`의 `home` 필드와 관련 있어 보임)의 근본 원인은 조사
  범위 밖.

## core.ui_launcher — UI 공용 모듈 + G2 통합 + 중복 기동 방지 (2026-07-29)

`run_g2_paper_trading.py` 통합을 진행하기 전 사용자에게 손익(장단점)을 먼저 보고 → 승인 후
진행. 진행 중 실측으로 예상 못 한 버그를 발견해 그 자리에서 대응(아래 갭 문단 참고).

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| core.ui_launcher.launch_command_center | ✅ | ✅ 2026-07-29 | ✅ | `run_l1_daily.py`/`run_g2_paper_trading.py`의 UI 기동 로직을 공용화. 기동 전 `is_ui_already_running()`(포트 8501 응답 확인)으로 먼저 확인해 이미 떠 있으면 새로 안 띄운다. `is_running`/`popen` 콜러블 주입 가능(`core/docker_bootstrap.py`와 동일 설계) — 단위 테스트 8건, 실제 소켓·streamlit 불필요 |
| run_g2_paper_trading.py — UI 통합 | ✅ | ✅ 2026-07-29 | — | `run_l1_daily.py`와 동일 패턴으로 거래일 확인 직후 UI 기동. `ModelRegistry`가 비어 있어(live 번들 0개) 화면의 AI Decision 존은 사실상 비어 보임(기존 갭과 동일 이유) |

### 알려진 갭/발견 (core.ui_launcher, 2026-07-29)

- **포트 충돌 시 Streamlit/Windows가 조용히 실패하지 않는다(실측으로 발견한 신규
  리스크)**: `run_l1_daily.py`의 UI가 이미 포트 8501을 점유한 상태에서 두 번째 Streamlit
  프로세스를 같은 포트로 띄우면, 에러 없이 두 프로세스가 동시에 LISTENING 상태로 남는
  것을 직접 재현 확인(어느 프로세스가 실제 요청을 받는지 예측 불가) — `is_ui_already_
  running()` 사전 확인으로 방어했다. 이 방어가 없었다면 G2 통합 자체가 조용한 이중 서버
  문제를 만들었을 것.
- **G2는 Task Scheduler에 등록돼 있지 않다**: `stop_l1_daily.bat` 워치독이 UI를 명령줄
  패턴으로 매칭해 정리하긴 하지만 그 워치독 자체는 평일 15:40에만 돈다 — G2를 그 시각
  이후(저녁·주말)에 수동 실행했다면 UI가 다음 평일 15:40까지 남아있을 수 있다(기존
  Task Scheduler 갭과 동일 성격).

## Command Center UI 포트 충돌 방지 — MESSIAH 전용 포트 고정 ([MW0601], 2026-07-29)

바로 위 "포트 충돌 시 조용히 실패하지 않는다" 갭은 "MESSIAH 두 스크립트끼리" 충돌만
다뤘는데, 2026-07-29 아침 실제 운영 로그를 조사하다 그와 다른 종류의 실사고를 발견:
`run_l1_daily.py`가 08:35:10에 "Command Center UI가 이미 응답 중(포트 8501) — 중복 기동
생략"으로 판단했는데, 실측 결과 그 포트를 점유하고 있던 건 MESSIAH가 아니라 완전히 다른
로컬 프로젝트(`PycharmProjects\options`)의 Streamlit이었다(그 프로젝트도 포트 미지정으로
Streamlit 기본값을 그대로 씀) — MESSIAH 자신의 화면이 아무 경고 없이 하루 종일 안 뜬 것.

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| core.ui_launcher.DEFAULT_PORT (8511 전용화) | ✅ | ✅ 2026-07-29 | ✅ | Streamlit 공용 기본값(8501)에서 MESSIAH 전용 고정값(8511)으로 분리, `launch_command_center()`가 `streamlit run`에 `--server.port`를 명시 전달(기존엔 `port` 인자가 `is_running()` 확인에만 쓰이고 실제 기동 명령엔 전달 안 돼 항상 8501에 바인딩되던 잠재 버그도 동시에 해결). 실제 호출로 포트 8511 LISTENING 확인, 8501을 점유 중이던 `options` 프로젝트 프로세스는 전혀 안 건드림 확인 |

### 알려진 갭 (Command Center UI 포트 충돌, 2026-07-29)

- **완전한 신원 확인은 아니다**: 포트를 MESSIAH 전용값으로 옮겨 충돌 확률만 낮췄을 뿐,
  `is_ui_already_running()`은 여전히 "어떤 프로세스든 응답하면 스킵"이다 — 제3자가 하필
  8511도 쓰면 같은 클래스의 문제가 재발할 수 있다. 완전한 해결(예: MESSIAH 전용 헬스체크
  엔드포인트로 신원 확인)은 이번 스코프에서 보류(Streamlit 정적 페이지는 앱별 식별 정보를
  초기 HTML에 안 담아 신뢰성 있는 구분이 간단하지 않다는 점도 고려).

## G2 페이퍼 트레이딩 Task Scheduler 등록 — 자체 WS 연결 제거 후 등록 ([MW0601], 2026-07-29)

바로 위 16차(2026-07-28) 기록의 "Windows 작업 스케줄러에 `run_g2_paper_trading.py`를
등록하지 않았다"는 갭에 대한 후속 — 그대로 등록하기 전에 코드를 다시 읽어보니 그 사이
새로 발견된 문제가 있어 등록 전에 먼저 고쳤다: G2가 L1과 완전히 같은 계좌·심볼·TR로 자기
WS 연결을 별도로 열고 있었고, 이는 이미 위(2026-07-23 항목)에 확정된 "동일 계좌 WS 연결
2개 → 반복 단절" 버그와 정면 충돌하는 설계였다.

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| run_g2_paper_trading.py — 자체 TickCollector/Composer/FeatureEngine 제거 | ✅ | ✅ 2026-07-29 | ✅ | `FuturesAIService`·`TradingPipeline`·`LiveSimBrokerFeed`·`ShadowManager` 전부 `bus.subscribe()`만으로 동작함을 확인 후 G2의 자체 데이터 수집 스택을 전부 제거 — 이제 L1이 버스에 발행한 `bar.*`/`feat.*`를 구독만 함(자체 WS 연결 0개). L1이 실계좌 WS로 정상 수집 중인 장중(2026-07-29 11:57)에 리팩터링본을 실제로 수동 실행해 검증: G2 워커 프로세스의 실제 TCP 연결은 Redis 4건+심볼마스터 REST(HTTPS) 1건뿐, KIS 실시간 WS 엔드포인트(`210.107.75.39:21000`) 연결 0건. 같은 시간 L1의 기존 WS 연결은 `Established` 그대로 유지, L1 로그도 끊김 없이 계속 발행 — G2 가동이 L1에 전혀 영향 없음을 실측 확인 |
| Task Scheduler "Messiah-G2"(평일 08:36, `run_g2_paper_trading.bat`) | ✅ | ✅ 2026-07-29 | — | 기존 "Messiah" 태스크(`Get-ScheduledTask`로 실측 확인한 설정 그대로 — `MW0601`/`LogonType=Interactive`/`RunLevel=Limited`, `StartWhenAvailable=False`·`WakeToRun=False`·무제한 실행시간, 평일 트리거)와 동일 설정으로 `Register-ScheduledTask` 신규 등록, 08:35(L1)보다 1분 늦은 08:36 — WS 연결이 없어져 순서 자체는 무관해졌지만 두 프로세스의 동시 기동 부하만 살짝 어긋내는 용도. `stop_l1_daily.bat` 15:40 워치독에도 `*run_g2_paper_trading.py*` 패턴 추가해 L1과 동등한 안전망 확보 |

### 알려진 갭 (G2 Task Scheduler 등록, 2026-07-29)

- **Registry가 비어 있어 여전히 "시스템이 안 죽고 도는가"만 증명한다**: G1 백테스트 관문을
  통과한 모델이 아직 없어(기존 갭, 위 16차 항목과 동일 원인) 매일 자동 실행돼도 실제
  거래는 여전히 0건 — 데이터 축적이 계속 유일한 선결 조건이다.
- **L1과 같은 기존 갭을 그대로 공유한다**: `LogonType=Interactive`(로그오프 시 캐치업
  없음)·`WakeToRun=False`·실패 시 능동 알림 없음(2026-07-29 Task Scheduler 감사 항목과
  동일) — 이번에 새로 만든 갭이 아니라 L1이 이미 갖고 있던 것을 그대로 물려받음.
- **첫 자동 트리거는 아직 미검증**: 오늘(2026-07-29)은 수동 실행으로만 검증했고, 등록된
  Task Scheduler 트리거를 통한 실제 첫 자동 실행은 다음 거래일(2026-07-30) 08:36 —
  `logs/g2_daily_20260730.log`로 다음 세션에 확인 필요.

## Command Center UI — Market View 3건 버그 수정 ([MW0601], 2026-07-29)

사용자가 실제 화면 스크린샷을 보여주며 "라이브 모드에서 market view가 업데이트되는지
점검하고 주기는 얼마인가"를 요청 — 조사 중 캔들 x축 타임존·자동 새로고침 부재·LIVE Redis
URL 기본값 오류 세 가지를 실측으로 발견해 전부 수정.

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| ui/app.py `_load_bars()` — 캔들 x축 KST 변환 | ✅ | ✅ 2026-07-29 | ✅ | `bar_open_kst`는 생성 시점엔 진짜 KST지만 Polars가 Parquet 왕복 시 `time_zone='UTC'`로 정규화해 저장(직접 스키마 확인) — 화면엔 실제보다 9시간 이른 시각 표시됨(스크린샷 실증). `.dt.convert_time_zone("Asia/Seoul")` 추가로 해결, `tzdata` 의존성은 이미 있음(`pyproject.toml`, 2026-07-22 다른 이유로 추가). 오늘 실제 parquet으로 실측: 수정 전 `2026-07-28 23:45:00+00:00` → 수정 후 `2026-07-29 08:45:00+09:00`(실제 웜업 시작 시각과 일치) |
| ui/app.py `_render_dashboard_body` + `st.fragment(run_every=5)` — LIVE 자동 새로고침 | ✅ | ✅ 2026-07-29 | — | 기존엔 자동 재실행 트리거가 전무해 사람이 위젯 조작·새로고침해야만 화면이 갱신됐다(실측 확인). LIVE 모드에서만 5초 간격 프래그먼트 재실행(REPLAY는 `run_every=None`) — 5초는 `_STALE_AFTER`의 가장 빡빡한 임계값(FuturesView 10초)보다 넉넉히 짧게 선택 |
| ui/app.py `_default_redis_url()` — LIVE Redis URL 기본값 수정 | ✅ | ✅ 2026-07-29 | ✅ | 예전 하드코딩값 `redis://localhost:6379/0`이 실제 MESSIAH Redis 포트(6380, `configs/instance.yaml`)와 달랐다 — 이 PC엔 공교롭게 6379에도 다른 Redis가 떠 있어 `bus.connect()`가 에러 없이 "성공"해버리고 화면은 엉뚱한 서버에 붙은 채 영원히 NO_DATA로 남는 조용한 오연결이 실측 확인됨. `load_instance().redis_url` 조회(실패 시 6380 폴백)로 수정 |
| ui/app.py `render_top_bar` — LiveConnectionError 화면 노출 | ✅ | ✅ 2026-07-29 | — | `_run_live_subscriber`가 연결 실패를 캐시에 이미 남기고 있었는데 어느 `render_*`도 그걸 안 읽어 사람 눈엔 늘 그냥 "NO_DATA"였다(부수 발견) — `st.error()`로 노출 추가 |

`tests/ui/test_app_helpers.py` 신규 6건 + 기존 UI 테스트 22건 전부 통과, 전체 815건 통과,
ruff 클린. 실제 streamlit(포트 8522, 별도)로 기동해 HTTP 200 확인 + `AppTest`로 LIVE 전환
후 실제 가동 중인 messiah-redis(6380)에 예외·오류 배너 없이 정상 연결됨을 확인.

### 알려진 갭 (Command Center UI Market View, 2026-07-29)

- **`run_every=5`의 실제 체감은 브라우저로 미검증**: 자동화 환경에 실제 브라우저가 없어
  코드 검토 + `AppTest` 정적 검증까지만 했다 — 사람이 화면을 열어 5초 간격이 자연스러운지
  최종 확인 필요.
- **Redis 오연결 방지가 포트 충돌 자체를 없애지는 않는다**: 6380을 기본값으로 고쳤어도
  제3자가 하필 6380을 쓰면 같은 클래스의 문제가 재발할 수 있다 — Command Center UI 자체의
  포트 8511 사고(위 항목)와 근본적으로 같은 성격의 잔여 리스크(로컬 포트는 전역 공유
  자원이라는 한계).

## 거래소 서킷브레이커(CB) 자동 대응 ([MW0601], 2026-07-29)

사용자가 코스피 급락형 서킷브레이커 발동 스크린샷을 제시하며 "미륵"(별도 선물 시스템)의
장중 서킷브레이커 대응체계를 조사·반영할 것을 요청. 미륵은 API로 직접 알 수 없는 CB를
"정상 연결 + 데이터 미수신" 간접 신호로 추정하는 상태머신을 쓰며, 재개 시 사람 확인 없이
자동으로 포지션을 강제청산하고 정상화한다 — 이 설계를 MESSIAH에 반영했다. 조사 과정에서
`KillSwitch.liquidate()`가 청산주문을 1회만 시도하고 재시도가 없다는 기존 구조적 갭도
함께 발견해 이번 구현으로 같이 해소됨(§ 아래 "R11/KillSwitch 충돌 회피" 참고).

| 기능 | 구현 | 단위테스트 | 비고 |
|---|---|---|---|
| `risk/circuit_breaker_monitor.py` — `CircuitBreakerMonitor` | ✅ | ✅ 2026-07-29 | NORMAL→WARNING(90s)→SUSPECTED(150s)→CONFIRMED(240s) 단계적 추정 상태머신. `RiskEngine`/`KillSwitch`와 동일 스타일(순수 판정, 실행은 호출자). `blocks_entry()`가 재개 후 10분 재진입 관망(KRX 단일가매매와 동일)을 판정 |
| `risk/risk_engine.py` — R13 신규 게이트 | ✅ | ✅ 2026-07-29 | `circuit_breaker_active` bool 주입 방식(`minutes_to_close`와 동일 패턴) — CONFIRMED 또는 재진입 관망 중이면 신규 진입 거부 |
| `strategy/pipeline.py` — CB 자동 감지·자동 복구 배선 | ✅ | ✅ 2026-07-29 | CONFIRMED 최초 도달 시 `gateway.halt()`. 재개(`just_resumed`) 감지 시 **사람 확인 없이 자동으로** `KillSwitch.liquidate()`를 재사용해 EMERGENCY 강제청산 후 `gateway.resume()` — `KillSwitch`(사람 확인 후에만 재가동)와 의도적으로 다른 철학. `watch_circuit_breaker_forever()`가 `FixedTickScheduler`(기존 `core/scheduler.py`)로 데이터가 끊긴 동안에도(완전 이벤트 구동 구조라 원래는 아무 코드도 안 돎) 단계적 phase 갱신을 담당 |
| `scripts/run_g2_paper_trading.py` 실배선 | ✅ | — | `TradingPipeline`에 `CircuitBreakerMonitor()` 주입 + `_run_regular_session()`의 `asyncio.gather()`에 `watch_circuit_breaker_forever()` 추가 |

**R11(`KillSwitch`)과의 충돌 회피**: `circuit_breaker_monitor`가 WARNING 이상이거나 이번
호출이 `just_resumed`면 `kill_switch.evaluate()`에 `data_age_seconds=0.0`을 넘긴다 —
그렇지 않으면 CB 자동복구(청산+`gateway.resume()`) 직후 같은 호출 안에서 KillSwitch의
R11이 동일한 데이터단절을 보고 다시 `gateway.halt()`를 걸어버려(사람이 KillSwitch를
`reset()`해야만 풀림) 자동복구가 무의미해지는 것을 막는다. `circuit_breaker_monitor`
주입 시 데이터단절 기반 전면정지 판단은 이 컴포넌트가 전담하고 KillSwitch는 R2·수동·
모델이상만 계속 감시한다 (`strategy/pipeline.py` 모듈 docstring 참고).

`pytest tests/risk/test_circuit_breaker_monitor.py tests/risk/test_risk_engine.py
tests/strategy/test_pipeline.py` 전체 통과, 전체 회귀 무손상.

### 알려진 갭 (거래소 서킷브레이커 자동 대응, 2026-07-29)

- **코스피 현물지수 기반 선제 감지 미착수**: KRX 공식 CB 발동 기준(8/15/20% 1분 지속)을
  직접 계산하는 방식은 RG(현물지수·매크로) 데이터소스 자체가 미착수라 이번 스코프에서
  제외(사용자 확인) — 지금은 데이터 갭 추정(반응형)만 구현.
- **재개 후 피처/국면 버퍼 "오염 제거" 없음**: 미륵의 `_post_exchange_cb_resume`(예측 버퍼
  리셋, 스케일러 재적합, 재학습 예약) 상당 조치가 없다 — `FeatureEngine`/`RegimeRuntime`에
  현재 reset() API 자체가 없어 신규 설계가 필요한 별도 작업.
- **능동 알림(Slack/텔레그램) 없음**: MESSIAH에 알림 인프라 자체가 아직 없음(2026-07-29
  Task Scheduler 감사 항목과 동일한 기존 갭) — 구조화 로그(`mlog.log`, `CircuitBreaker*`
  태그)까지만.
- **halt 이력 DB 영속화·EOD 리포트 요약 없음**: 미륵의 `record_exchange_cb_halt`/
  `daily_exporter.py` 상당 — MESSIAH엔 EOD 리포트/exporter 모듈 자체가 아직 없어(확인됨)
  구조화 로그 grep으로 사후 확인하는 수준까지만.
- **임계값 미검증**: 90/150/240초, 재진입 관망 10분은 미륵의 실측 보정값(6/8·6/23·6/26·
  7/7 CB 관측)을 차용한 초기값 — MESSIAH 자체는 아직 실거래 CB를 관측한 적이 없어
  타당성 미검증. 실측이 쌓이면 재조정 필요.
- **(수정 완료) 콜드스타트 오탐**: 실전 반영 직후 재시작 실측에서 발견 — `_last_bar_confirm_at
  is None`일 때 `data_age_seconds=inf`를 CB 판정에 그대로 흘려 시작하자마자 거짓
  CircuitBreakerConfirmed/Resumed 쌍이 찍히는 버그를 당일 발견·수정(가드 추가, 회귀 테스트
  포함). 상세는 `dev_memory/DECISION_LOG.md` "실전 재시작 직후 콜드스타트를 CB로 오판" 항목.
- **(미해결, 별도 스코프) KillSwitch R11의 동일한 콜드스타트 취약점**: 위 버그를 조사하다
  발견 — `handle_futures_view()`가 `handle_bar()`보다 먼저 호출되는 경로가 있으면
  `kill_switch.evaluate()`도 `data_age_seconds=inf`를 그대로 받아 R11이 스스로
  `gateway.halt()`를 걸 수 있다(단위테스트로 재현 확인, 실전에서는 오늘 재현 안 됨). CB
  기능과 무관한 `handle_futures_view()`의 기존 결함이라 이번 스코프에서 고치지 않음 — 다음
  세션 검토 항목.

## Command Center UI — CB 상태 배지 + LIVE 기본값 ([MW0601], 2026-07-29)

사용자 요청 2건: ① Market View에 CB 상태를 보여주는 배지가 없어 나이스하게 추가, ②
사이드바 "모드" 기본값을 REPLAY→LIVE로 변경.

| 기능 | 구현 | 단위테스트 | 비고 |
|---|---|---|---|
| `core/messages.py` `CircuitBreakerStatus` + `core/bus.py` `TOPIC_CIRCUIT_BREAKER`(`sys.circuit_breaker`) | ✅ | — | `Health`와 같은 heartbeat 철학 — phase가 그대로여도 매 `observe()` 호출마다(이벤트 구동+워치독 양쪽) 발행 |
| `strategy/pipeline.py` `_publish_circuit_breaker_status()` 배선 | ✅ | ✅ 2026-07-29 | `handle_futures_view()`·`watch_circuit_breaker_forever()` 양쪽에서 호출, `CircuitBreakerEvent`를 그대로 실어나름 |
| `ui/app.py` `_render_circuit_breaker_badge()` — Top Bar 5번째 컬럼 | ✅ | ✅ 2026-07-29 | phase별 색상(normal 청록/warning 앰버/suspected 주황/confirmed 적색) + 재진입 관망 남은 시간 캡션. CB 미사용 구성(스모크 등)에서는 "미사용/데이터 없음"으로 명시(마흐디 L18 — 값 없음과 정상을 혼동하지 않음) |
| `ui/app.py` 사이드바 모드 기본값 REPLAY→LIVE | ✅ | ✅ 2026-07-29 | `st.sidebar.radio(..., index=1)`. "착각의 여지 없음" 방어(LIVE 배지 신선도 노출, 연결실패 화면 노출)는 그대로 유지 — 기본값만 바뀜 |

신규 테스트: `tests/strategy/test_pipeline.py::test_circuit_breaker_status_published_for_command_center_ui`,
`tests/ui/test_app_smoke.py`의 기본모드 재확인(`test_app_runs_without_exception_in_default_live_mode`)
+ REPLAY 명시전환(`test_app_runs_without_exception_when_switched_to_replay_mode`) +
배지 렌더(`test_circuit_breaker_badge_shows_unused_when_no_status_published`). 전체 829건
통과, ruff/pyright 클린. 실제 L1/G2 재시작으로 UI(`localhost:8511`) HTTP 200 확인.

### 알려진 갭 (CB 상태 배지, 2026-07-29)

- **REPLAY 모드에서 과거 CB 이력 조회 불가**: `CircuitBreakerStatus`는 실시간 heartbeat만
  있고 DB/Parquet 영속화가 없어(위 "halt 이력 DB 영속화 없음" 갭과 동일 원인) REPLAY로
  과거 날짜를 봐도 그날 CB가 있었는지 배지로는 알 수 없다 — 항상 "미사용/데이터 없음".
