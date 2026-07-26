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

## Cost Model·5m Expert 프로토타입·Trainer·Validator (Ver 2.0 §9 W14~16)

| 기능 | 구현 | 모의 실측 | 실전 실측 | 비고 |
|---|---|---|---|---|
| risk.cost_model.CostModel | ✅ | ✅ 2026-07-27 | — | Ver 1.1 §4-1 4요소(수수료+세금+슬리피지+시장충격) 구조 구현. 시장충격은 완성봉의 실제 volume 필드로 계산(구조적으로 정확), 슬리피지는 호가 WS 미구독으로 `expected_spread_ticks` 설정값 근사(알려진 갭). 단위 테스트 10건(편도/왕복 계산·시장충격 비례성·유동성 없을 때 폴백·qty 검증·봉 이력 평균거래량 근사·커스텀 설정·CostEstimate 덧셈, 전부 손으로 계산한 값 기준) |
| models.labeling.triple_barrier_labels/label_and_weight cost_ticks 결선 | ✅ | ✅ 2026-07-27 | — | `cost_ticks: int`(W12~13 임시값)를 `float`로 확장해 `CostModel.estimate_round_trip_from_bars(...).total_ticks`를 그대로 받을 수 있게 함(`models/trainer.py`가 실제 연결부). 기존 12개 테스트 회귀 없음 |
| strategy.futures.expert.HorizonExpert | ✅ | ✅ 2026-07-27 | — | Ver 1.2 §9 스켈레톤의 5m 프로토타입 1호 — 단일 LightGBM 3-class 분류기(미니 앙상블·Meta-Labeler·Isotonic 교정·Optuna 탐색은 전부 W17~19 "정식" 스코프, 모듈 docstring에 명기). `core/logging.py`에 W1부터 등록만 되고 미사용이던 `FeatureSetMismatch` 태그를 `predict()`에서 처음 실사용(추론 시점 feature_set 불일치 시 ERROR 로그+예외). 단위 테스트 7건(학습·예측 확률분포 검증·플레이스홀더 필드 확인·feature_set 불일치 예외·feature_row NaN 매핑·top_features 정렬·저장/재로드 왕복 동일 예측·커스텀 파라미터) |
| models.trainer.build_feature_vectors/build_training_data/train_prototype_expert | ✅ | ✅ 2026-07-27 | — | Ver 1.6 §7.1 파이프라인 1~3단계(데이터준비·레이블생성·학습)만 구현([4]교정 [5]번들패키징 [6]Validator제출 자동화는 W17~19). 봉을 실제 운영과 동일한 FeatureEngine(simulator.InProcessBus 재사용)에 직접 흘려 FeatureVector를 얻어 재현성 보장. CostModel→label_and_weight 실제 결선, 클래스불균형(inverse-frequency)×고유도 가중치 조립. 단위 테스트 12건 |
| models.validator.Validator | ✅ | ✅ 2026-07-27 | — | Ver 1.2 §8.3 성과 관문 3종(Deflated Sharpe 제외 — 알려진 갭) + Ver 1.6 §8 추가검사 4종(교정 Brier·Feature 의존도·추론지연·직렬화 왕복) 전부 구현. 성과 관문은 이미 계산된 시계열을 입력받는 순수 오케스트레이션(실제 walk-forward 백테스트 루프는 W17~19 이후, 알려진 갭). 모델 자체 검사 4종은 이번 주 프로토타입으로 바로 실행 가능함을 확인. 단위 테스트 14건(GateResult/ValidationReport 집계·성과 관문 3종 pass/fail 경계·교정 pass/fail·Feature 의존도 pass/fail(경계 비교 로직)·지연 pass/fail·직렬화·validate_all 7관문 조립) |
| models.metrics (sharpe_ratio/max_drawdown/negative_window_ratio/multiclass_brier_score) | ✅ | ✅ 2026-07-27 | — | 전부 순수 함수, Validator·향후 Self Evaluation(Phase 5) 재사용 가능하게 labeling.py에 의존하지 않음. 단위 테스트 15건 전부 손으로 계산한 known-value 기준(R16) |
| core.bus.BusLike (Protocol) | ✅ | ✅ 2026-07-27 | — | `models/trainer.py`가 `FeatureEngine`에 `simulator.InProcessBus`를 넘기면서 pyright가 처음으로 "MessageBus 구체클래스와 불일치" 오류를 냄(런타임은 이미 정상 동작 중이었음 — W9~11부터 `scripts/run_replay.py`가 같은 패턴을 썼지만 scripts/는 pyright 검사 대상 밖이라 안 드러났었음). `publish`/`subscribe`만 요구하는 Protocol을 신설해 `FeatureEngine.__init__`의 `bus` 타입힌트를 이걸로 교체 — 런타임 동작 변화 없이 타입 수준에서도 "동일 인터페이스"(Ver 1.0.1 §2.1) 원칙을 명시 |
| scripts/run_expert_training_smoke.py | ✅ | ✅ 2026-07-27 | — | 실제 2026-07-24 아카이브(A05608, 5m, 7행)로 Trainer→HorizonExpert→Validator(모델 검사 3개 관문) end-to-end 실행 확인. 성과 관문·교정 관문은 의도적으로 생략(스크립트 docstring — 백테스트 인프라 부재·홀드아웃 데이터 없음) |

## lightgbm 4.7.0 Windows 휠 크래시 (2026-07-27, `ml` extras 상한 고정으로 해결)

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

## 알려진 갭 (Cost Model·Expert·Validator, 2026-07-27)

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
| models.search.search_hyperparameters | ✅ | ✅ 2026-07-28 | — | Ver 1.6 §2.2 탐색 공간(num_leaves/max_depth/min_data_in_leaf/learning_rate/feature_fraction/bagging_fraction/lambda_l1/l2) 원문 그대로 인코딩. Optuna(TPE) + `PurgedKFold`(W12~13 재사용)로 "창 내부 CV로만 탐색" 실제 구현 — objective=폴드 평균 multi_logloss. early_stopping은 이번 스코프 제외(알려진 갭). 단위 테스트 5건(커스텀 탐색공간 키/범위 검증·시드 고정 시 결정론성·퇴화 폴드(train/test 어느 한쪽 빔) 안전 처리·기본 탐색공간이 프로덕션 공간과 일치) |
| models.calibration.ProbabilityCalibrator | ✅ | ✅ 2026-07-28 | — | Ver 1.6 §6.1 — 클래스별 Isotonic Regression(one-vs-rest) + 재정규화. out-of-fold 데이터로 학습해야 한다는 제약을 모듈 docstring에 명기. 단위 테스트 3건(재정규화 합=1 확인·1차원 입력 처리·과신 보정 손계산 known-value — 동일 입력 x=0.9 반복 시 PAVA가 pooled 평균으로 수렴하는 성질 이용) |
| models.calibration.ConformalCalibrator | ✅ | ✅ 2026-07-28 (메커니즘만) | — | Ver 1.2 §6 / Ver 1.6 §6.2 — 비적합도 분위수 계산 메커니즘 구현·합성 데이터로 정확성 검증(known-value 6건: 비적합도 계산·분위수 산출·이력 없을 때 최대보수값·구간 클리핑·잘못된 alpha 거부). **실제 운영 이력 없음** — 매일 갱신되는 라이브/페이퍼 예측 로그가 전제인데 그 이력이 아직 없다(G2 페이퍼트레이딩 W39~40부터, 알려진 갭). 어떤 운영 루프에도 아직 안 붙어 있음 |
| strategy.futures.expert.HorizonExpert (앙상블+교정 재설계) | ✅ | ✅ 2026-07-28 | — | W14~16 단일모델 프로토타입을 Ver 1.6 §2.3 미니 앙상블(×5, seed만 다름)로 확장 + `ProbabilityCalibrator` 선택적 부착. `ens_std`는 Ver 1.2 §6 원문 그대로 "P(+1) 표준편차"로 계산(다른 클래스 아님). 저장/로드가 앙상블 멤버 다중 파일(`{stem}_e{i}.lgb`) + 메타데이터(`.json`) + 교정기(`.pkl`, sklearn 객체라 pickle — Ver 1.6 §9.1 번들 포맷과 동일 확장자)로 확장. 단위 테스트 15건(기본 앙상블 크기 5·커스텀 멤버수/시드·단일멤버 ens_std=0 확인·플레이스홀더 필드(meta_passed 항상 True 등)·빈 부스터 거부·feature_set 불일치·feature_row NaN 매핑·top_features 정렬·저장/로드 왕복(교정기 유무 양쪽)·교정기 부착 시 확률 실제로 바뀌는지·교정기 제거) |
| strategy.futures.meta_labeler.MetaLabeler | ✅ | ✅ 2026-07-28 | — | Ver 1.2 §5 / Ver 1.6 §5 — Horizon별 얕은 LightGBM(depth≤4, leaves≤15) 이진 분류기. 메타 Feature 5개(1차확률·마진·앙상블분산·실현변동성 근사·시간대)만 지금 계산 가능 — Regime·스프레드·이벤트근접도는 각각 W20~21·호가WS·Event Calendar 미구현이라 제외(모듈 docstring에 명기). `select_threshold()`가 Ver 1.6 §5.2 "비용차감 후 기대수익 최대화"를 그리드서치로 실제 구현(정확도 최대화 아님). 단위 테스트 14건(메타Feature 조립 known-value·순net_return 부호 검증(up/down/flat 신호)·학습데이터 조립이 flat신호 제외+y라벨 정확히 산출하는지·임계값선택 known-value(그리드 평균 손계산)+동률 시 보수적 선택·MetaLabeler 학습/예측/임계값 교체(재학습 없음)/저장로드 왕복) |
| models.trainer.generate_out_of_fold_predictions | ✅ | ✅ 2026-07-28 | — | Ver 1.6 §5.1 "1차 모델을 Walk-Forward로 가상 운용 → out-of-fold 신호만 수집"을 `PurgedKFold`로 실제 구현(칸닝 방지 — W12~13에 만든 CV 인프라의 첫 실사용처). 폴드마다 그 폴드에서 제외된 데이터로 학습한 앙상블의 `HorizonExpert.predict()`를 그대로 호출해 예측을 얻는다(booster 내부에 안 손대고 공개 API만 재사용). 단위 테스트 3건(정상 산출 시 확률 합=1·ens_std≥0·길이불일치 거부·레이블 없을 때 빈 결과) |
| models.trainer.train_formal_expert | ✅ | ✅ 2026-07-28 | — | Ver 1.6 §7.1 [3]~[4]단계 전체 오케스트레이션(탐색→out-of-fold→최종 앙상블 전체데이터 재학습→교정 부착→Meta-Labeler 학습+임계값선택) — `ExpertTrainingResult`(expert, meta_labeler, best_params, n_oof_records, n_meta_signals) 반환. out-of-fold 신호가 0건이면(데이터 부족) 조용히 빈 Meta-Labeler를 만드는 대신 ValueError로 실패(정식 경로는 칸닝 방지 메커니즘이 실제로 작동했다는 보장이 핵심이라는 판단). `train_prototype_expert()`(W14~16)는 그대로 유지 — 빠른 배관 확인용 경로로 남김. 단위 테스트 4건(전체 결과 필드 검증·교정기 부착 확인·빈 bars 거부·데이터 부족 거부) + `scripts/run_formal_expert_training_smoke.py` 실제 실행 확인 |
| scripts/run_formal_expert_training_smoke.py | ✅ | ✅ 2026-07-28 | — | 실제 아카이브(A05608, 5m, 7건)로 먼저 시도 → 예상대로 "데이터 부족" ValueError로 실패(정직하게 보고) → 200건 합성(사인파+지터) 데이터로 전체 파이프라인 실행: 탐색 완료(8개 하이퍼파라미터 산출) → out-of-fold 192건 → Meta-Labeler 192개 신호로 학습·임계값 0.9 선택 → 5-멤버 앙상블+교정기 부착 → 마지막 봉 예측→Meta-Labeler 통과판정까지 end-to-end 1회 성공. 합성 데이터는 스크립트 출력에 "실제 시장 데이터 아님" 명시 |

## optuna 설치·동작 확인 (2026-07-28)

지난주 lightgbm 4.7.0 Windows 휠 크래시 사고 이후 신규 ML 의존성은 설치 직후 최소 스모크
테스트를 거치는 습관을 들임 — optuna 4.9.0은 기본 `create_study().optimize()` 호출로 별
문제 없이 동작 확인(sqlalchemy/alembic 등 부수 의존성이 딸려오지만 기본 `InMemoryStorage`
사용 시 문제 없음). `pyproject.toml` ml extras에 `optuna>=3.6` 추가.

## 알려진 갭 (5m Expert 정식·Meta-Labeler, 2026-07-28)

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
| features.vl_core.vl_vol_ratio | ✅ | ✅ 2026-07-29 | — | W_STD의 앞 두 값(5, 20) 윈도우 표준편차의 비율 — 짧은 창이 긴 창보다 훨씬 크면(고변동성 국면) 1보다 크게 나온다. W_STD 세 번째 값(60)은 30시간 웜업 비용이 커 이번 스코프에서 제외(모듈 docstring). 단위 테스트 4건(known-value·등락 없는 구간은 비율 1 근처·NaN 웜업 구간 처리·창 크기 override) |
| strategy.regime.hmm_model.RegimeHMM / build_observations | ✅ | ✅ 2026-07-29 (합성 데이터) | — | hmmlearn GaussianHMM 래퍼. 관측 벡터는 px_trend_r2·vl_vol_ratio·px_autocorr 3개 Feature 조합(px_core/vl_core 재사용, 신규 계산 없음). `n_states_candidates`로 후보 상태 수를 주면 BIC 최솟값으로 자동 선택(Ver 1.6 §3.1). 단위 테스트 8건(BIC 선정 known-value·상태수 1개 고정 경로·관측치 부족 시 ValueError·predict_states 길이 일치) |
| strategy.regime.naming.label_states / describe_labels | ✅ | ✅ 2026-07-29 (합성 데이터) | — | 통계층(HMM 상태 index)을 Regime enum으로 매핑하는 명명층 — 상태별 관측 특성(추세 강도·변동성 비율 평균)을 기준으로 TREND_UP/TREND_DOWN/RANGE/HIGH_VOL에 통계적으로 배정, 애매하면 UNKNOWN. describe_labels()는 사람 검수용 상태별 사후 통계 요약 문자열을 만든다. 단위 테스트 6건(각 국면 유형 known-value 배정·동률 처리·요약 문자열 필드 포함 여부) |
| strategy.regime.rules.RuleContext / rules | ✅ | ✅ 2026-07-29 | — | 규칙층(하이브리드 구조 2단) — 통계층 판정을 필요시 덮어쓴다. 지금 살아있는 규칙은 변동성 극단(vol_ratio가 임계값 초과 시 무조건 HIGH_VOL, confidence=1.0) 1개뿐 — 이벤트 근접·세션 시가/종가 특수구간 등 Ver 1.6 §3.1이 언급한 나머지 규칙은 Event Calendar 미구현(기존 갭)이라 제외(모듈 docstring). 단위 테스트 5건(임계 이하/초과 경계값·오버라이드 시 confidence=1.0 고정·오버라이드 없을 때 통계층 결과 그대로 통과) |
| strategy.regime.service.RegimeAI | ✅ | ✅ 2026-07-29 (합성 데이터) | — | `fit()`(HMM 학습→명명)→`classify()`(최신 봉 윈도우로 통계층 판정→규칙층 오버라이드) 오케스트레이션. `RegimeState`(core/messages.py 신규 — symbol/regime/confidence/state_duration_bars/transition_prob/rule_override/valid_until) 메시지 조립까지 담당. `n_states`/`labels`/`hmm_model` 공개 프로퍼티로 내부 모델 상태를 노출(스모크 스크립트·사람 검수용, private 속성 직접 접근 방지). 단위 테스트 9건(classify 국면 판정 known-value·상태 지속 봉수 증가/리셋·규칙 오버라이드가 confidence=1.0 강제·전이확률 합=1·최소 관측치 부족 시 UNKNOWN) — 최소 관측 길이가 `window+2`(px_autocorr가 다른 두 Feature보다 1봉 더 필요)임을 놓쳐 classify()가 항상 UNKNOWN을 반환하던 버그를 테스트로 발견·수정 |
| scripts/run_regime_ai_smoke.py | ✅ | ✅ 2026-07-29 | — | 실제 아카이브(A05608, 30분봉 1건)로 먼저 시도 → 예상대로 "관측치 부족" ValueError로 실패(정직하게 보고) → 추세상승/횡보/고변동성 3구간 반복 합성 30분봉으로 전체 파이프라인(HMM 학습→BIC 상태수 선정→국면 판정→규칙 오버라이드 시연→사람 검수용 요약) end-to-end 1회 성공. 합성 데이터는 "실제 시장 데이터 아님" 명시 |

## 알려진 갭 (Regime AI, 2026-07-29)

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
