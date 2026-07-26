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
| models.labeling.triple_barrier_labels | ✅ | ✅ 2026-07-27 | — | Ver 1.2 §3.2 Horizon별 표(시간배리어·ATR 폭 배수)를 그대로 인코딩. ATR은 `features.px_core.atr`(이번에 공개 전환)을 재사용 — 중복 구현 없음. 동일 봉에서 상/하단 동시 터치 시 상단 우선(결정론적 타이브레이크, 모듈 docstring 근거 기록), 비용반영 강등(`cost_ticks`, Cost Model v1 나오기 전 호출자 전달 임시값), 워밍업·꼬리 트림. 단위 테스트 9건(상단/하단 터치·시간배리어·동시터치 타이브레이크·비용강등·워밍업부족·꼬리부족·심볼혼입 거부 — 전부 손으로 계산한 ATR/배리어 값 기준 known-value). 실측: 실제 2026-07-24 아카이브(A05608, 1m, 33행)로 `scripts/run_labeling_smoke.py` 실행 — 레이블 16건 생성(−1: 6·+1: 10), 버그 없음 |
| models.labeling.compute_uniqueness | ✅ | ✅ 2026-07-27 | — | Lopez de Prado(2018) 평균 고유도. 격자점은 전체 레이블의 t_start∪t_end(동시성이 바뀔 수 있는 지점은 구간 경계뿐이므로 정확한 격자 — t_start만 쓰면 시계열 꼬리에서 과소평가되는 버그를 known-value 테스트 작성 중 직접 발견·수정). 단위 테스트 3건(손으로 계산한 3이벤트 겹침 사례 A=0.75/B=0.75/C=1.0·안 겹치는 경우 전부 1.0·빈 입력) + 실제 생성 레이블 통합 테스트(가중치 (0,1] 범위·겹침으로 인한 감쇠 확인) |
| models.cv.PurgedKFold | ✅ | ✅ 2026-07-27 | — | de Prado(2018) Ch.7 표준 알고리즘(순수 Python, numpy 의존성 없음 — Optuna 탐색용 "Purged 5-Fold", Ver 1.6 §2.2). 폴드는 시간순 연속 구간, 겹치는 학습 샘플 제거(purge) + 경계 인접 샘플 추가 제외(embargo, 인덱스 단위). 단위 테스트 7건(균등분할 아닐 때 폴드 크기·전 인덱스가 정확히 한 번씩 test로 나뉘는지·purge가 구간 겹침 학습샘플을 실제로 제거하는지·embargo가 겹침 없어도 경계 인접분을 제거하는지·잘못된 n_splits/embargo 거부) |
| models.cv.WalkForwardSplitter | ✅ | ✅ 2026-07-27 | — | Ver 1.2 §8.2 "학습 6개월/검증 1개월, 1개월씩 전진" 스킴을 달력일 파라미터(train_days/test_days/embargo_days/step_days)로 일반화. Purge(배리어가 검증 구간을 침범하는 학습 샘플 제거) + Embargo(검증 직전 N일 추가 제외) 둘 다 구현. 단위 테스트 8건(빈 입력·롤링 창 개수·첫 창의 train/test 정확한 소속(embargo 반영)·검증 구간을 침범하는 장기 배리어 purge·기본 step=test_days·커스텀 step_days·잘못된 창 크기 거부) — 전부 30~60일 합성 데이터 기준(실제 아카이브가 하루치뿐이라 달력 롤링을 의미 있게 재현할 데이터가 없음, 아래 "알려진 갭" 참고) |
| scripts/run_labeling_smoke.py | ✅ | ✅ 2026-07-27 | — | 실제 2026-07-24 아카이브로 레이블링+고유도+PurgedKFold 전체 배선 end-to-end 실행 확인(위 행들 참고) |

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
- **WalkForwardSplitter는 실제 다개월 아카이브로 실측한 적 없다 (2026-07-27)**: 정확성은
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
- **run_l1_daily.py는 선물(K200_MINI_FUT) 1개만 수집**: `universe`에는 K200_OPT도 있지만,
  같은 계좌로 WS 연결을 2개 열면 서로 끊기는 문제가 이미 실측으로 확인됨(위 "L1 Data" 갭
  참고) — 옵션까지 같이 수집하려면 연결 하나에 다중 subscribe()로 묶는 재설계가 먼저 필요.
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
