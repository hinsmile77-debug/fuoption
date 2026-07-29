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
~~라이브 미검증~~ (검증 기한: 2026-07-24) — **2026-07-21 당일 해소**: Docker
`messiah-redis`(redis:7-alpine) 컨테이너를 포트 6380에 기동하고 `configs/instance.yaml`의
redis_url을 갱신한 뒤 self_check PASS 확인(NEXT_TODO.md W1~2 "Redis 실서버 연동 검증"
항목 참고, 같은 날 세션 후반부). 이 절을 쓴 시점엔 아직 실행 전이라 미검증으로 남겨뒀는데,
기한(07-24)이 오기도 전에 이미 닫혔음에도 태그를 안 지워 2026-07-27 `agenda.py` 실행까지
계속 회의 안건에 잡히고 있었다(자동 안건화 도구 점검 중 발견 — L15 규율은 "기한을
명기한다"뿐 아니라 "닫히면 태그를 지운다"도 포함해야 한다는 교훈).

### [설계결정] self_check가 live+dirty git / live+번들 미지정 시 기동 거부

**근거**: 계명 10 (미커밋 수정 실전 반입 금지), L11 (PC 드리프트), L17 (스키마 정합).
**결정**: dev 모드는 관대하게, paper/live는 엄격하게 — 같은 코드로 모드에 따라 관문 강도만 변경.

### [설계결정] Python 3.11+ 타깃, 단 timeutil은 timezone.utc 사용

**증상**: `datetime.UTC`는 3.11 전용 — CI/검증 환경(3.10)에서 ImportError.
**결정**: `timezone.utc` 별칭으로 통일. ruff DTZ 규칙으로 naive datetime 생성을 lint 차단 (R3).

---

## 2026-07-23 (2차 — L1 Collector WS 재연결 + 옵션 틱 경로 실측)

### [설계결정] 장시간 운영·거래량 급증·3회+ 연속 재연결 검증은 Phase 1 파이프라인 완성 후 정기회의로 이관

**근거**: TickCollector.run_forever() 실측 세션(capability_matrix.md 참고)에서 20초→최대
180초로 관측 구간을 늘렸고 강제 단절 1회 재연결까지는 실측했지만, 수 시간 단위 연속 운영·
실제 거래량 급증(장 시작 직후·지수 급변동)·3회 이상 연속 재연결은 스크립트 하나로 흉내내기
어려운 종류다 — Phase 1 데이터 파이프라인이 장전·장중·장후를 매일 도는 상태가 되면 이 세 가지는
운영 중 자연히 관측된다.
**결정**: 별도 검증 스크립트를 더 만들지 않고, Phase 1 파이프라인(장전·장중·장후 흐름) 완성 후
첫 금요일 주간회의(SYSTEM.md §7.1)에서 실제 운영 로그 기준으로 재검토한다.
**Why**: 인위적으로 "장시간"을 스크립트로 흉내내는 것보다 실제 운영 데이터로 판단하는 게
정확하고, 이미 있는 회의체·에이징 메커니즘을 재사용하는 게 별도 트래킹 도구를 만드는 것보다 쌈.
**How to apply**: agenda.py가 이 항목을 자동으로 회의 안건화한다(아래 검증 기한 기준).
**라이브 미검증** (검증 기한: 2026-08-14, Phase 1 파이프라인 완성 후 첫 금요일 주간회의 — 그
전에 파이프라인이 완성되면 그 시점 회의에서 앞당겨 검토, 완성이 늦어져 기한을 넘기면
NEXT_TODO 에이징 규칙대로 자동으로 안건 최상단에 재배치됨).

---

## 2026-07-26 (3차 — Digital Twin 시뮬레이터, Ver 2.0 §9 W9~11)

### [설계결정] "호가창 수준 재생" 원안 대신 1분봉 기반 체결 모사로 스코프 확정

**근거**: Ver 1.0.1 §2.1은 "호가창 수준 재생 + 자기 주문의 시장충격 모사"를 제안했으나,
MESSIAH는 아직 호가(orderbook) WS를 구독하지 않고 ParquetArchiver도 완성봉(BarClosed)만
적재한다(원시 틱 미적재 — 기존 알려진 갭). 호가 데이터 없이 "호가 기반 체결"을 구현하는 건
불가능.
**결정**: 이미 있는 데이터(완성봉 OHLCV, 최소 단위 1분봉)로 낼 수 있는 가장 정직한 근사를
택한다 — 지정가는 pending 등록 후 1분봉의 고가/저가 터치로 체결 판정(체결가=지정가, 보수적),
시장가는 최근 종가±슬리피지로 즉시 체결. 근사임을 broker/simulator/adapter.py 모듈
docstring과 capability_matrix.md "알려진 갭"에 명시.
**Why**: 매 주차 산출물은 실행 가능해야 한다(Ver 2.0 §9 원칙) — 없는 데이터를 기다리며
W9~11을 통째로 미루는 것보다, 지금 있는 데이터로 정직하게 근사한 시험장을 먼저 돌려 이후
Expert·Risk·Execution 개발의 병목을 풀어주는 게 우선. 호가 WS가 나중에 갖춰지면 SimBroker의
체결 판정 부분만 교체하면 되도록 인터페이스(on_bar)를 설계해 뒀다.
**How to apply**: Cost Model v1(W14~16)이나 호가 WS 작업이 들어올 때 SimBroker._touched()/
_fill_market()의 가격 모델만 교체 대상으로 삼을 것 — 다른 컴포넌트(replay/inprocess_bus/
engine)는 봉 이벤트 소스가 바뀌어도 영향받지 않게 이미 분리돼 있음.

### [설계결정] InProcessBus로 "동일 인터페이스" 원칙(Ver 1.0.1 §2.1) 실현

**결정**: `core.bus.MessageBus`와 같은 publish/subscribe 시그니처의 인메모리 버스를
신설(simulator/inprocess_bus.py)해 FeatureEngine 등 하위 소비자를 재생 경로에서도 코드
변경 없이 재사용한다. `subscribe()`는 Redis 버전처럼 블로킹 루프가 아니라 핸들러 등록 후
즉시 반환 — 재생은 실시간 대기가 필요 없으므로 publish 시점 동기 디스패치로 충분.
**Why**: 백테스트/페이퍼/실전이 설정 한 줄 차이여야 한다는 5대 불변 원칙(Ver 2.0 §1)을
Digital Twin에서도 지키려면, FeatureEngine이 자신이 구독한 버스가 진짜 Redis인지 재생용
인메모리 버스인지 몰라야 한다. 실제로 `scripts/run_replay.py`에서 FeatureEngine을 아무
수정 없이 그대로 연결해 실제 아카이브 데이터로 FeatureVector가 정상 발행됨을 확인.
**검증**: 단위 테스트 4건(정확한 토픽 매치·불일치 무시·instance_id 자동 채움·복수 구독자)
+ scripts/run_replay.py 실제 실행(2026-07-24 아카이브 60행 재생, FeatureVector 60건 발행 —
아카이브 행 수와 정확히 일치).

### [설계결정] SimBroker 계약 변경 — "즉시체결"에서 "봉 기반 pending 체결"로

**증상**: 기존 SimBroker(W1 골격)는 submit() 즉시 무조건 체결하는 최소 구현이었고, 자체
docstring에 "W9~11에서 확장 예정"이라 명시돼 있었음.
**결정**: submit()이 이제 재생 시계(on_bar()로 진행)가 최소 1틱 진행되기 전엔 모든 주문을
거부한다("시장데이터 없이는 거래 없다") — 기존 `tests/test_core_w1.py`의 OrderGateway 테스트
2건이 이 계약 변경으로 실패해, "봉 1개로 시계 프라이밍" 헬퍼(`_primed_broker()`)를 추가해
반영(SimBroker 자체가 아니라 OrderGateway 로직 검증이 목적인 테스트라 스코프에 맞게 최소
수정).
**부수 발견**: 코드 리뷰 중 `_apply()`가 포지션 갱신가를 `req.limit_price_ticks`에서 가져오는
버그를 자체 발견(실측 아님) — 시장가 주문은 이 필드가 None이라 `avg_price_ticks=0`으로 잘못
기록될 뻔했음. 실제 체결가(`price_ticks`)를 명시적으로 전달하도록 즉시 수정, 이후 작성한
단위 테스트(시장가 슬리피지 체결가 검증)로 회귀 방지.
**Why**: 실계좌도 시세 없이 주문을 낼 수 없다 — 이 제약을 시뮬레이터에도 강제해 두면 향후
Expert/전략 코드가 "웜업 전 주문 시도" 같은 실수를 재생 단계에서부터 걸러낼 수 있다.
**검증**: SimBroker 단위 테스트 10건(제출 전 거부·시장가 슬리피지·지정가 터치 체결
매수/매도·TTL 만료 우선순위·취소·굵은 Horizon 무시·EXIT_FULL·qty 검증) + 전체 회귀
236건 통과.

---

## 2026-07-26 (4차 — Triple Barrier·uniqueness·Walk-Forward/Purged CV, Ver 2.0 §9 W12~13)

### [설계결정] compute_uniqueness() 격자를 t_start만이 아니라 t_start∪t_end로 확정

**증상**: 최초 구현은 레이블 집합의 t_start 값들만 이산 격자로 썼다. 손으로 계산한
known-value 테스트(A=[t0,t1], B=[t1,t2], C=[t3,t3] — 겹침 구조가 명확한 3이벤트 사례,
기대값 A=0.75/B=0.75/C=1.0)를 작성해 돌려보니 B의 실제 결과가 0.5로 나와 불일치.
**원인**: t2는 B의 t_end일 뿐 어떤 이벤트의 t_start도 아니라 격자에서 아예 빠져 있었다 —
B의 구간이 격자 위에서 1칸(t1)만 덮는 것으로 잘못 계산됨. `triple_barrier_labels()`가 매
봉마다 진입을 만드는 정상 상황에서는 거의 모든 t_end가 다른 레이블의 t_start와 우연히
일치해 드러나지 않지만(그래서 진짜 생성 레이블로 하는 통합 테스트는 통과했었다), 시계열
꼬리(그 봉 자체는 진입 후보가 못 된 경우)나 향후 CUSUM 필터링처럼 매 봉이 진입이 아닌
경우엔 조용히 동시성을 과소평가하는 버그였다.
**결정**: 격자를 전체 레이블의 t_start∪t_end 합집합으로 변경. 동시성은 구간 경계에서만
바뀌는 계단함수이므로 이 합집합이 수학적으로 정확한 격자다(그 사이 어떤 점을 더 추가해도
동시성 값은 안 바뀐다).
**Why**: `compute_uniqueness()`는 `labeling.py` 내부에서만 쓰이는 게 아니라 공개 API로
설계했다(호출자가 임의의 (t_start,t_end) 이벤트 집합을 넣을 수 있음, cv.py의 EventTimes와
같은 설계 원칙) — "보통은 맞는" 근사가 아니라 입력 형태에 무관하게 항상 정확해야 한다.
**How to apply**: 이 알고리즘을 건드릴 일이 생기면(예: 성능 최적화로 나이브 O(N·span)
동시성 집계를 차분배열 O(N+G)로 바꿀 때) 격자 정의(t_start∪t_end)는 그대로 유지할 것 —
정확성의 핵심 불변식이다.
**검증**: tests/models/test_labeling.py — 손으로 계산한 3이벤트 겹침 known-value 테스트 +
안 겹치는 경우 전부 1.0 + 빈 입력 + 실제 triple_barrier_labels() 생성 레이블 통합 테스트
(가중치 (0,1] 범위, 겹침 존재 시 일부 <1.0) 전부 통과.

### [설계결정] Triple Barrier 동일 봉 동시 터치 시 상단 우선(결정론적 타이브레이크)

**근거**: 완성봉(OHLC)만으로는 봉 내부에서 상단·하단 중 어느 쪽에 먼저 닿았는지 알 수
없다(원시 틱 미적재 — 기존 알려진 갭과 동일 원인).
**결정**: 상단을 우선한다 — 단순하고 결정론적이며, 배리어 폭이 ATR 기반이라 한 봉 안에서
양쪽을 다 뚫는 경우 자체가 드물어(급변동 한정) 표본 통계에 미치는 영향이 작다고 판단.
**Why**: 완벽한 재현이 불가능한 상황에서(호가/틱 데이터 없음) 애매한 규칙보다 "무엇을
어떻게 결정했는지 문서화된 단순한 규칙"이 낫다 — 나중에 실제 틱 재생이 가능해지면 이
근사를 정밀 판정으로 교체할 자리로 남겨둔다.
**How to apply**: 원시 틱/호가 적재가 구현되면(SimBroker의 "호가 기반 체결" 갭과 같은
선행조건) `_resolve_barrier()`를 실제 틱 순서 기반으로 교체.

### [설계결정] models/cv.py는 labeling.py에 의존하지 않고 (t_start,t_end) 튜플만 다룬다

**결정**: `PurgedKFold.split()`/`WalkForwardSplitter.split()`는 `TripleBarrierLabel`을
직접 받지 않고 `Sequence[tuple[datetime,datetime]]`(`EventTimes`)만 받는다. 호출자가
`[(l.t_start, l.t_end) for l in labels]`로 변환해 넘긴다.
**Why**: CV 스킴은 "이벤트가 [시작,끝] 구간을 갖는다"는 성질에만 의존하고 Triple
Barrier라는 특정 레이블링 방식과는 무관한 범용 도구다 — Trainer가 나중에 다른 레이블링
방식(예: Meta-Labeler의 이진 레이블)을 추가해도 cv.py를 그대로 재사용할 수 있게 결합도를
낮춰뒀다.
**How to apply**: 새 레이블링 방식을 추가할 때 cv.py 수정 불필요 — (t_start,t_end) 쌍만
만들어 넘기면 된다.

### [설계결정] pyproject.toml `[tool.pyright]`에 `pythonVersion = "3.11"` 명시

**증상**: cv.py의 `Sequence[tuple[datetime, datetime]]` 모듈 레벨 타입 별칭에서 pyright가
"Subscript for class tuple will generate runtime exception"라는 오탐을 냄 — 실제로는
Python 3.9+ 전부 안전(PEP 585)하고 실제 .venv도 3.12라 테스트는 전부 정상 통과.
**원인**: `[tool.pyright]`에 `pythonVersion`이 지정돼 있지 않아 pyright가 자체 기본
가정치(3.11보다 낮은 버전)로 타입을 검사하고 있었다 — `requires-python = ">=3.11"`
(pyproject.toml)·`target-version = "py311"`(ruff)과 어긋난 상태.
**결정**: `pythonVersion = "3.11"`을 `[tool.pyright]`에 추가.
**Why**: 이번 한 파일만의 문제가 아니라 앞으로 builtin 제네릭(`list[...]`/`dict[...]`/
`tuple[...]`)을 런타임 표현식(변수 어노테이션이 아닌 일반 대입문)으로 쓰는 모든 신규
코드에서 같은 오탐이 재발할 잠재 요인이었다 — 설정 파일 한 줄로 근본 해결.
**검증**: 수정 후 models 패키지 pyright 0 errors, 나머지 10개 에러는 전부 사전에 존재하던
별개 이슈(polars/redis/websockets/dotenv 모듈 해석 실패 — pyright가 .venv를 못 찾는 문제,
이번 세션과 무관)임을 확인.

---

## 2026-07-26 (5차 — Cost Model v1·Validator 골격·5m Expert 프로토타입 1호, Ver 2.0 §9 W14~16)

### [사고] lightgbm 4.7.0 Windows 휠이 Dataset 생성 단계에서 100% 크래시

**증상**: `ml` extras(lightgbm/scikit-learn/numpy)를 이번 세션에서 처음 설치 후
`lgb.Dataset(x, label=y).construct()`만 호출해도 `OSError: exception: access violation
reading 0x0000000000000000`. 데이터 크기(15행·500행)·내용과 무관하게 100% 재현.
**원인 조사**: `set_label()` 내부에서 네이티브 DLL(`lib_lightgbm.dll`) 호출이 널 포인터를
역참조하는 지점까지 추적. numpy를 1.26으로 내려 재시도했더니 이번엔 이미 설치된
scipy(numpy 2.0+ 요구)가 깨져 `AttributeError: module 'numpy' has no attribute 'long'` —
두 패키지가 서로 다른 numpy 메이저 버전을 요구하는 상태였다. lightgbm을 4.3.0으로
내리자(numpy는 2.5.1 그대로) weight·feature_name·저장/재로드·feature_importance까지
전부 정상 동작 확인 — lightgbm 4.7.0의 Windows 휠 자체가 이 numpy/Python 조합에서
깨져 있는 것으로 결론.
**결정**: `pyproject.toml`의 `ml` extras를 `lightgbm>=4.3,<4.7`로 상한 고정. 재현 시나리오와
검증 절차를 pyproject.toml 주석 + capability_matrix.md에 상세 기록.
**Why**: 이건 수치가 틀리는 버그가 아니라 프로세스 자체가 죽는 크래시라 테스트 없이는
CI/개발 환경이 그냥 멈춘다 — "구현됨≠검증됨" 원칙을 넘어 "설치됨≠동작함" 수준의 사고였다.
**How to apply**: 이후 누구든 lightgbm 버전을 올리려면(4.4~4.6 어딘가에서 이미 고쳐졌을 수도
있음 — 이번엔 4.3.0으로 내리기만 하고 4.4~4.6 각각을 개별 이분탐색하지 않았다) 반드시
`tests/strategy/futures/test_expert.py`(학습→예측→저장→재로드 전체 경로) 통과를 먼저
확인할 것. numpy도 함께 올리려면 scipy 호환성을 별도 확인.
**검증**: 4.3.0 고정 후 strategy/futures 테스트 7건 + 전체 회귀 319건 통과.

### [설계결정] Validator 성과 관문은 이미 계산된 시계열을 받는 순수 오케스트레이터로 한정

**근거**: Ver 1.2 §8.3 성과 관문(비용차감 Sharpe·MDD·창별 일관성)을 "제대로" 계산하려면
Digital Twin(W9~11) + HorizonExpert(이번 주) + Cost Model(이번 주) + Walk-Forward
Splitter(W12~13)를 전부 엮은 실제 백테스트 루프가 있어야 하는데, 그 루프 자체가 이번 주
스코프에 없다(5m Expert가 방금 프로토타입 1호일 뿐이라 있어도 의미 있는 백테스트가 아직
안 됨 — 실측 아카이브가 하루치뿐).
**결정**: `Validator.validate_performance()`는 `daily_returns`/`equity_curve`/
`window_returns`를 이미 계산된 시계열로 받기만 한다 — 그 시계열을 만드는 방법(실제
백테스트든 합성 테스트 데이터든)은 전혀 모른다. 정확성은 합성 데이터 기준 known-value
테스트로 지금 증명해 두고, 실제 백테스트 하니스가 생기면(W17~19 이후) 그 산출물을
그대로 흘려 넣기만 하면 되게 설계했다.
**Why**: "Validator 골격"이라는 이번 주 로드맵 문구에 정직하게 부합한다 — 없는 백테스트를
억지로 흉내 내 가짜 성과 곡선으로 관문을 통과시키는 것보다, 관문 계산 로직 자체가
정확하다는 걸 지금 증명해 두는 게 낫다. 모델 자체를 검사하는 4개 관문(교정·Feature
의존도·추론지연·직렬화)은 반대로 실제 프로토타입으로 지금 바로 실행 가능해 스모크
스크립트에서 실제로 돌렸다.
**How to apply**: W17~19 이후 실제 walk-forward 백테스트 하니스를 만들 때
`validate_performance()`의 시그니처(세 시계열)를 그대로 목표로 삼을 것 — Validator 쪽
수정은 불필요해야 한다.

### [설계결정] core/bus.py에 BusLike Protocol 신설 — MessageBus 구체클래스 의존 제거

**증상**: `models/trainer.py`가 `FeatureEngine`에 `simulator.InProcessBus`를 넘기자
pyright가 "InProcessBus는 MessageBus에 할당 불가"라며 처음으로 오류를 냄 — 런타임은
이미 W9~11 `scripts/run_replay.py`부터 똑같은 패턴(InProcessBus를 FeatureEngine에 주입)을
써 왔지만, scripts/는 `[tool.pyright] include=["src"]`에 안 걸려 있어 이번에 `src/` 안
코드(trainer.py)로 처음 들어오면서 드러난 것.
**결정**: `core/bus.py`에 `publish`/`subscribe`만 요구하는 `BusLike` Protocol을 신설,
`FeatureEngine.__init__`의 `bus` 타입힌트를 `MessageBus` 구체클래스에서 `BusLike`로 교체.
**Why**: 실제 설계 원칙(Ver 1.0.1 §2.1 "동일 인터페이스" — 백테스트/재생/실전이 같은
코드를 타야 한다)은 처음부터 이랬다. 타입힌트가 그 사실을 반영 못 하고 있었을 뿐이라
로컬 억제(`# type: ignore`)보다 근본 수정이 맞다고 판단.
**How to apply**: `bar_composer.py`/`collector.py` 등 다른 bus 소비자도 향후 InProcessBus를
받을 일이 생기면 같은 패턴(구체클래스 → BusLike)으로 교체할 것 — 지금은 실제로 그렇게
쓰이는 곳(FeatureEngine)만 바꿨다(불필요한 선제 리팩터링 지양).
**검증**: 수정 후 models 패키지 pyright 0 errors. `engine.py:132`·`trainer.py`의 나머지
Handler 분산성(variance) 경고는 별개의 기존 패턴(Callable 파라미터 반공변성 — 모든
handler가 특정 BusMessage 서브클래스로 좁게 타입돼 있어 발생, W9~11부터 있었음)이라
이번 수정 대상에서 제외 — 실행 동작에는 영향 없음(319건 전체 회귀 통과로 확인).

---

## 2026-07-26 (6차 — 5m Expert 정식(탐색·앙상블·교정) + Meta-Labeler, Ver 2.0 §9 W17~19)

### [설계결정] out-of-fold 생성 로직을 HorizonExpert 내부가 아니라 trainer.py에 둠

**근거**: Ver 1.6 §5.1 "1차 모델을 Walk-Forward로 가상 운용"과 §6.1 "검증 폴드에서
Isotonic 교정기 학습"은 사실상 같은 메커니즘(폴드별 재학습 + out-of-fold 예측 수집)을
요구한다 — 교정기 학습용과 Meta-Labeler 학습용 데이터를 각각 따로 만들면 같은 계산을
두 번 하게 된다.
**결정**: `models/trainer.py`의 `generate_out_of_fold_predictions()`가 `PurgedKFold`로
폴드를 나누고 폴드마다 `HorizonExpert.train()`(그 폴드에서 제외된 데이터만)을 호출해
`HorizonExpert.predict()`(공개 API)로 예측을 얻는 단일 경로를 만들고, 그 산출물
(`OutOfFoldRecord` 목록)을 `ProbabilityCalibrator.fit()`과
`build_meta_training_data()` 양쪽에 그대로 재사용한다.
**Why**: HorizonExpert 자신이 "내가 저 데이터로 학습됐는지"를 추적하게 만들면(Ver 1.2
§1 "전문가는 서로를 모른다"는 원칙과도 어긋나고) 클래스가 무거워진다 — 폴드 분할·재학습·
수집이라는 오케스트레이션은 Trainer(L6)의 책임이고, Expert(L3)는 학습된 상태로 예측만
하면 된다는 역할 분리를 그대로 지켰다. 부수적으로 `HorizonExpert.predict()`의 내부
booster 리스트를 trainer.py가 몰라도 되게 캡슐화가 유지된다(직접 booster를 순회하며
확률을 뽑는 대신 공개 predict() 결과의 p_up/p_flat/p_down/ens_std만 읽음).
**How to apply**: 향후 다른 Horizon Expert나 다른 1차 모델(예: Options AI 매트릭스)도
같은 "가상 운용" 데이터가 필요해지면 `generate_out_of_fold_predictions()`와 같은 패턴
(PurgedKFold + 공개 predict() 재사용)을 그대로 따를 것 — 모델 클래스 내부를 열어보지
않는다.
**검증**: tests/models/test_trainer.py — out-of-fold 레코드 확률 합=1·ens_std≥0 확인,
`train_formal_expert()` end-to-end 테스트(합성 데이터)로 전체 경로 통과 확인 + 실제
스모크 스크립트로 합성 200봉 기준 192건 out-of-fold 산출 확인.

### [설계결정] Meta-Labeler 임계값은 정확도가 아니라 비용차감 기대수익 최대화로 선택

**근거**: Ver 1.6 §5.2 원문 "Meta 통과 임계 τ는 검증 구간에서 비용 차감 후 기대수익
최대화로 선정(정확도 최대화가 아니다)".
**결정**: `select_threshold()`가 후보 임계값 그리드(기본 0.0~1.0, 0.05 간격)마다
"그 임계값 이상만 통과했을 때 남는 신호들의 평균 net_return"을 계산해 최댓값을 내는
임계값을 고른다. 동률이면 더 보수적인(높은) 임계값을 남긴다.
**Why**: 정확도(예측 성공률)를 최대화하는 임계값은 신호 수를 과도하게 줄여 "맞춘 것만
남기고 기회 자체를 버리는" 방향으로 갈 수 있다 — 실제 목적(수익)과 대리 지표(정확도)가
어긋나는 전형적인 사례라 원문이 명시적으로 경고한 것으로 이해했다.
**Why(동률 처리)**: 여러 임계값이 같은 평균 수익을 낸다면, 신호 수가 더 적더라도(더
엄격하더라도) 안정성 측면에서 보수적인 쪽이 낫다고 판단 — 이건 원문에 명시되지 않은
설계 판단이라 별도로 기록해 둔다(재검토 대상이면 여기를 먼저 볼 것).
**검증**: tests/strategy/futures/test_meta_labeler.py — 손으로 계산한 5-포인트 그리드
사례(최댓값이 중간 임계값에서 나오는 케이스)와 동률 케이스 둘 다 통과.

### [설계결정] lightgbm 이후 신규 ML 의존성(optuna)은 설치 직후 최소 스모크부터

**근거**: 지난주(W14~16) lightgbm 4.7.0이 Dataset 생성 단계에서 100% 크래시하는 사고를
겪었다 — "설치됨≠동작함"이 실제로 일어날 수 있다는 걸 학습.
**결정**: optuna를 pyproject.toml에 추가하기 전에 먼저 `uv pip install`로 설치해
`create_study().optimize()` 최소 예제부터 돌려 확인한 뒤에야 본 구현에 반영했다.
**Why**: 본 구현(하이퍼파라미터 탐색 전체)을 다 짜고 나서 실행 단계에서 크래시를
만나면 원인 추적 범위가 훨씬 넓어진다 — 의존성 자체의 건전성을 먼저 분리해 확인하는
게 싸다.
**How to apply**: 이후 신규 ML/데이터 계열 의존성을 추가할 때(scikit-learn 이미
있었으니 생략했었지만, 새로운 라이브러리라면) 같은 순서(설치→최소 스모크→본 구현)를
기본값으로 삼을 것.
**검증**: optuna 최소 스모크 통과(비선형 목적함수 20 trial 최적화 확인) 후 본 구현
착수, 문제 재발 없음.

---

## 2026-07-26 (7차 — Regime AI: HMM + 규칙, Ver 2.0 §9 W20~21)

### [설계결정] 통계층(HMM)과 규칙층을 분리하고 규칙층은 지금 1개 규칙만 둠

**근거**: Ver 1.6 §3.1이 명시한 하이브리드 구조 — 통계적으로 상태를 분리하는 HMM과,
사람이 정의한 예외(이벤트 근접, 세션 시가/종가 등)를 강제하는 규칙층을 별도 계층으로
둔다. 규칙층이 필요로 하는 입력(이벤트 근접도) 중 상당수가 Event Calendar 미구현이라
아직 계산 불가능하다.
**결정**: `strategy/regime/rules.py`는 지금 변동성 극단(vol_ratio 임계 초과 시 HIGH_VOL
강제, confidence=1.0) 1개 규칙만 구현하고, 나머지는 `RuleContext`에 필드를 미리
마련해두되(추후 채울 자리) 실제로 평가하지 않는다.
**Why**: 통계층이 못 잡는 걸 규칙층이 억지로 채우려고 미구현 입력에 임시값(0 고정 등)을
넣으면 "규칙이 있는 것처럼 보이지만 실제로는 아무 조건도 아닌" 조용한 가짜 구현이
된다 — R10(폴백·합성 데이터는 배지·경보 동반, 조용한 폴백 금지)과 같은 원칙. 차라리
지금 계산 가능한 규칙만 정직하게 구현하고 나머지는 갭으로 명시하는 편이 낫다.
**How to apply**: Event Calendar가 구현되면 이벤트 근접 규칙을, 호가 WS가 구현되면
스프레드 기반 규칙을 각각 추가할 자리로 `rules.py`를 남겨둔다 — 그 전까지 새 규칙을
추가할 땐 반드시 그 규칙이 실제로 평가 가능한 입력을 갖고 있는지부터 확인할 것.
**검증**: tests/strategy/regime/test_rules.py — 임계 이하/초과 경계값, 오버라이드 시
confidence=1.0 고정, 오버라이드 없을 때 통계층 결과 그대로 통과 확인.

### [버그] classify()가 px_autocorr에 필요한 최소 봉수를 과소 계산해 항상 UNKNOWN 반환

**증상**: `test_rule_override_forces_confidence_one_and_reason`,
`test_state_duration_increments_on_same_regime_and_resets_on_change` 두 테스트가
계속 실패 — `classify()`가 매번 `Regime.UNKNOWN`을 반환.
**원인**: `classify()`는 성능을 위해 꼬리에서 `window + 1`봉만 잘라 `build_observations()`
에 넘기는 최적화를 해뒀는데, 관측 Feature 3개(px_trend_r2·vl_vol_ratio·px_autocorr) 중
`px_autocorr`만 `window + 2`봉이 필요하다(다른 둘보다 엄격한 요구사항) — 그래서 자른
구간이 항상 1봉 부족해 `build_observations()`가 빈 결과를 반환하고, 관측치가 없으니
`classify()`가 안전하게 UNKNOWN으로 떨어졌다.
**결정**: `classify()`의 `min_length`를 `self._window + 2`로 수정 — 3개 Feature 중
가장 엄격한 요구사항 기준으로 통일.
**Why**: 여러 Feature를 조합해 관측 벡터를 만들 때 "각 Feature가 필요로 하는 최소
길이"를 개별적으로 확인하지 않고 대표 하나(가장 단순한 Feature)의 요구사항만 보고
최적화하면 이런 종류의 조용한 실패가 생긴다 — Feature 조합 지점에서는 항상 구성
요소 전체의 최댓값을 취해야 한다.
**How to apply**: 향후 관측 벡터에 Feature를 추가/교체할 때는 각 Feature의 최소 요구
길이를 다시 확인하고 `min_length` 계산도 함께 갱신할 것 — `build_observations()`
모듈 docstring에 각 Feature의 최소 길이 요구사항을 명시해 둠.
**검증**: 위 두 테스트 통과, 전체 406건 통과.

---

## 2026-07-26 (8차 — VL 확장 + FeatureEngine deque 버그 수정 + 15m·30m Expert 검증, Ver 2.0 §9 W22~23)

### [버그] FeatureEngine이 계산 직전 history(deque)를 list로 안 바꿔 슬라이스 기반 계산기 다수가 항상 None

**증상**: 15m/30m Expert 작업을 시작하며 "PX+VL 후보군이 실제로 얼마나 되는가"를 확인하려고
FeatureEngine에 합성 봉 80개를 흘려 실제 값을 찍어봤다 — 82개 Feature 키 중 72개가 여전히
None이었다(워밍업은 충분히 끝난 시점인데도).
**원인**: `FeatureEngine._history`는 `collections.deque(maxlen=130)`로 보관되는데,
`px_core`/`vl_core`의 계산기 다수(`atr`/`true_ranges`·`px_vwap_dev`·`px_trend_slope`·
`px_hurst`·`px_rsi`·`px_bb_pos/width`·`px_adx` 등, PX 30개 중 대다수)가 내부에서
`bars[-(window+1):]` 같은 슬라이스를 쓴다. Python `collections.deque`는 정수 인덱싱
(`d[-1]`)은 지원하지만 슬라이스(`d[-3:]`)는 지원하지 않는다(`TypeError: sequence index
must be integer, not 'slice'`) — 표준 라이브러리의 알려진 제약이지 이 프로젝트의 버그가
아니다. `FeatureEngine._safe_call()`은 "개별 Feature 계산 실패는 그 값만 None으로
마킹"(Ver 1.1 §2-2)이라는 의도된 설계로 넓은 `except Exception`을 쓰는데, 이 관용구가
의도치 않게 슬라이스 계산기의 TypeError까지 조용히 삼켜버렸다. 정수 인덱싱만 쓰는 소수
(px_ret/px_mom/px_accel 등)만 매번 정상 작동해 왔다.
**결정**: `handle_bar()`가 `history.append(bar)` 직후, 계산기를 부르기 전에
`bars = list(history)`로 한 번 변환해 이후 전 계산기(px_core+vl_core)에 리스트를 넘긴다.
계산기 쪽 계약(`Bars = Sequence[BarClosed]`)은 원래도 슬라이스 가능한 시퀀스를 가정하고
바르게 짜여 있었으므로 계산기 코드는 손대지 않았다.
**Why**: `Sequence` 타입힌트는 슬라이스 지원을 문법적으로 강제하지 않는다 — `deque`는
`Sequence`의 여러 연산(길이·정수 인덱싱·반복)은 만족하지만 슬라이스는 만족하지 않는
"불완전한 Sequence"였다. 타입 체커(pyright)도 이 구멍을 잡지 못했다(런타임 예외만
발생). 이런 종류의 "일부만 계약을 만족하는 자료구조"는 정적 타입만으로는 못 잡고,
실제로 파이프라인을 끝까지 흘려보고 값을 찍어봐야 드러난다 — 이번에도 발견 경로는
단위 테스트가 아니라 "80봉을 흘려 실제 값을 눈으로 확인"이었다(W6~8 원 실측 노트가
`px_ret_5`/`px_mom_5` 두 개만 확인하고 넘어간 것과 대비).
**How to apply**: 향후 `FeatureEngine`에 새 계산기 카테고리(MS/FL/OP/RG)를 추가할 때도
`handle_bar()`가 이미 리스트로 변환한 `history`를 계산기에 넘기므로 별도 조치 불필요 —
단, 계산기 category 모듈 자체를 테스트할 때 리스트가 아니라 deque를 직접 넣어보는 테스트를
최소 1건은 추가해 이런 종류의 회귀를 예방할 것(`test_slice_based_calculators_produce_real_values_once_warmed`가 그 역할).
**검증**: 신규 회귀 테스트 통과(40봉 워밍업 후 `px_vwap_dev_5`/`vl_atr_5` 등 슬라이스
기반 키가 None이 아님 확인), 전체 439건 통과.

### [설계결정] VL 하위윈도우(`vl_vov`/`vl_squeeze`)를 표준 20 대신 5로 고정

**근거**: `vl_vov`(변동성의 변동성)와 `vl_squeeze`(BB폭 백분위)는 이중 윈도우 구조 —
외부 윈도우(W_SLOW={20,60,120}) 안에서 하위 지표를 여러 시점에 굴려 그 분포를 본다.
표준 관례(BB(20))를 하위윈도우로 쓰면 외부 윈도우가 120일 때 필요 봉수가 120+20=140으로,
`features/engine.py`의 `_MAX_HISTORY`(130, px_hurst/px_accel 요구치 기준으로 이미 고정된
예산)를 넘는다.
**결정**: 하위윈도우를 5로 고정(`_INNER_SUBWINDOW`) — 120+5=125<130으로 기존 예산 안에
맞춘다.
**Why**: `_MAX_HISTORY`를 올리는 대신 하위윈도우를 낮추는 쪽을 택했다 — Ver 1.4가 VL의
하위윈도우 값을 못박지 않아 어차피 판단이 필요했고, `_MAX_HISTORY`를 건드리면 기존
px_hurst 등 이미 검증된 Feature들의 워밍업 판정 기준(`warmed_up = len(history) >=
_MAX_HISTORY`)까지 흔들어 회귀 위험이 커진다. 메모리 비용은 130이든 140이든 무시할
수준이라 이 트레이드오프의 실익은 "기존 예산을 안 건드림"에 있다.
**How to apply**: Ver 1.5 §5 선정 절차(IC 스크리닝 등)로 이 두 Feature가 실제 유용하다고
확인되면, 그때 하위윈도우를 표준값으로 늘리는 대신 `_MAX_HISTORY` 자체를 올리는 재검토를
할 것(당장은 배관 검증 우선).
**검증**: `test_vl_squeeze_low_percentile_when_compressing_after_expansion` 작성 중
처음 window=20으로 설계했다가 실패(0.85 vs 기대 <0.5) — 원인은 하위윈도우 자체가 아니라
테스트의 창 구성(quiet 구간이 창의 85%를 차지해 비교 기준이 무의미해짐)이었고, 창에
확장(wild) 구간이 다수·압축(quiet) 구간이 소수로 섞이도록 재설계(window=30)해 해결.

### [설계결정] 15m/30m Expert는 이번 주 PX+VL만 받는다 — FL/OP/RG는 명시적 갭으로 남김

**근거**: Ver 1.2 §4.2·Ver 1.5 §3.5~3.6은 15m Expert에 FL(수급) 30%, 30m Expert에
RG(국면)+OP(옵션) 각 20%를 배정한다 — 둘 다 카테고리 배정의 절반 이상이다. 그런데 FL은
투자자매매동향 REST 폴링 루프, OP는 옵션체인 그릭스 수집기, RG는 현물지수·매크로
데이터 소스가 각각 필요한데 셋 다 이 프로젝트에 전혀 연동돼 있지 않다(W3~8부터 이어진
기존 갭).
**결정**: 이번 W22~23 스코프를 "M15/M30에서도 Expert 학습 파이프라인이 실제로 동작하는가"
+ "PX+VL 후보군을 최대한 채운다(VL 1→14)"로 한정하고, FL/OP/RG 카테고리는 구현하지
않은 채 capability_matrix.md에 사유와 함께 명시적으로 기록했다.
**Why**: FL/OP/RG를 만들려면 각각 새 Collector/Normalizer/Archiver급 데이터 파이프라인이
필요하다 — 이미 알려진 갭(REST 폴링 루프, 옵션체인 수집기)을 "15m Expert 작업"이라는
명목으로 급하게 얼기설기 만들면, Regime AI의 규칙층이 겪을 뻔한 것과 같은 문제(미구현
입력에 임시값을 넣어 "있는 것처럼 보이지만 실제로는 의미 없는" 조용한 가짜 구현)가 될
위험이 크다. 데이터가 없으면 없다고 정직하게 기록하고, 있는 걸로 파이프라인의 정확성을
검증하는 편이 낫다.
**How to apply**: FL/OP/RG는 각각 독립된 착수 대상(다음 분기회의 안건 후보)으로
NEXT_TODO.md·capability_matrix.md에 남겨둔다 — "15m Expert 완료"라고 말할 수 있는 시점은
이 셋이 채워진 뒤다.
**검증**: `scripts/run_formal_expert_training_smoke.py --horizon 15m`/`--horizon 30m`
실제 실행 — 두 경우 다 실제 아카이브는 데이터 부족으로 정직하게 실패, 합성 데이터로는
탐색→out-of-fold→앙상블+교정기→Meta-Labeler까지 5m과 동일하게 성공. 단위 테스트
(M15/M30 파라미터화 4건) 통과, 전체 439건 통과.

---

## 2026-07-27 (9차 — Aggregator·Meta Decision·Risk Engine·Sizer·Kill Switch, Ver 2.0 §9 W24~26)

### [설계결정] R1(단일 포지션 최대손실 2%)은 RiskEngine 게이트가 아니라 Sizer의 사이징 상한으로 강제

**근거**: Ver 2.0 §5 한도표는 R1을 RiskEngine의 다른 한도(R2~R12)와 같은 줄에 나열하지만,
실제 파이프라인 순서는 L4에서 Cost→**Risk**→**Sizer** 순이라 RiskEngine이 통과 판정을
내리는 시점엔 아직 사이징 전이라 "검사할 수량 자체가 없다".
**결정**: R1은 RiskEngine의 게이트 목록에서 제외하고, PositionSizer가 산출 수량의
상한 자체를 2% 기준으로 계산하도록 구조적으로 강제한다(사후 검사가 아니라 사전 제약).
**Why**: 순서상 존재하지 않는 값을 검사하는 게이트를 만드는 대신, 애초에 그 값이
한도를 넘을 수 없게 만드는 쪽이 "침묵 실패"(계명 L3) 위험이 없다 — 게이트는 사이징이
끝난 뒤에야 의미가 생기는데, 그 시점엔 이미 Sizer가 상한을 지켰으므로 별도 게이트가
사실상 죽은 코드가 된다.
**How to apply**: 향후 한도표에 새 항목을 추가할 때, "사이징 이전에 검사 가능한 값인가"를
먼저 확인하고 RiskEngine/Sizer 중 어느 쪽 책임인지 결정할 것(risk/risk_engine.py 모듈
docstring에 근거 기록).
**검증**: risk_engine 13건·sizer 10건 단위 테스트, `scripts/run_full_path_smoke.py`
end-to-end(Sizer→RiskEngine→OrderGateway→SimBroker 전 경로 주문 체결 확인).

### [버그] `FuturesView.ts_utc`가 봉 도메인 시각이 아니라 wall clock으로 채워짐 → R11 가짜 데이터단절

**증상**: `scripts/run_full_path_smoke.py` 최초 실행에서 `TradingPipeline`의 R11(데이터단절)
판정이 "R11 데이터단절 15172559s 지속"이라는 터무니없는 값을 발생시킴.
**원인**: `Aggregator.compute()`가 신선도(f_h) 계산엔 `as_of`(봉 도메인 시각)를 쓰면서도,
발행하는 `FuturesView`의 `ts_utc` 필드 자체는 `BusMessage` 기본값(`now_utc()`, wall clock)으로
채워지고 있었다. 실거래에선 wall clock≈봉 시각이라 안 드러나지만, 재생/스모크처럼 과거·합성
시각을 빠르게 재생하면 "wall clock 기준 now" vs "봉 도메인 last_bar_confirm_at"을 비교해
수억 초 단위 가짜 단절이 발생한다.
**결정**: `Aggregator.compute()`가 `FuturesView(ts_utc=as_of, ...)`로 명시 오버라이드,
`FuturesAIService._publish()`도 `trigger.ts_utc` 대신 `trigger.valid_until`(봉 도메인 시각)을
`as_of`로 넘기도록 수정.
**Why**: `BusMessage` 기본값에 암묵적으로 의존하면 "발행 시각 필드는 항상 wall clock"이라는
가정이 봉 도메인 메시지에는 성립하지 않는데도 타입 체크만으로는 안 드러난다 — 발행자가
명시적으로 도메인 시각을 채우는 쪽이 안전하다.
**How to apply**: 봉 도메인 데이터로부터 파생되는 새 메시지 타입을 만들 때는 `ts_utc`를
기본값에 맡기지 말고 그 메시지가 대표하는 시각(보통 트리거가 된 상류 메시지의 `valid_until`)을
반드시 명시할 것.
**검증**: `tests/strategy/futures/test_aggregator.py`·`test_futures_service.py` 회귀 테스트,
`run_full_path_smoke.py` 재실행으로 R11 오탐 소멸 확인.

### [버그] 신선도(f_h) 공식이 처음부터 방향이 반대였음

**증상**: 위 버그를 고치는 과정에서 단위 테스트로 재현 — 신선도가 발행 직후 0이 되는(= 이미
완전히 stale한) 반대 결과가 나옴.
**원인**: `valid_until` 필드는 스키마 주석("다음 완성봉 시각")과 달리 `features/engine.py`가
실제로는 "그 봉 자신의 확정 시각"(`bar_confirm_time`)으로 채운다(`RegimeState`·`ExpertView`
등 다른 모든 발행자도 W6~8부터 전부 이 semantics를 따르는 pre-existing 관례). 최초
Aggregator 구현은 이를 "미래 만료 시점"으로 오독해 `(valid_until − as_of)/Horizon`으로
감쇠시켰다.
**결정**: `(as_of − valid_until)/Horizon`(경과 시간 기준)으로 공식을 반전.
**Why**: 스키마 주석 자체가 실제 구현과 어긋나 있었다 — 이번 기회에 Aggregator만 고치는
대신, `valid_until`을 채우는 다른 발행자들과 대조해 "확정 시각" semantics가 프로젝트
전체의 실제 관례임을 확인하고 그 관례에 Aggregator를 맞췄다(스키마 주석은 향후 정정 대상
후보로 남겨둠).
**검증**: `tests/strategy/futures/test_aggregator.py` 신선도 known-value 테스트.

### [설계결정] `OrderGateway.submit()`이 `kind=EMERGENCY`는 halted 상태에서도 통과시킴

**근거**: `scripts/run_full_path_smoke.py` 실행 중 Kill Switch가 발동한 뒤에도 청산 로그가
매 Horizon 갱신마다 반복 발행되는 것으로 발견 — `OrderGateway.halt()`가 EMERGENCY 주문까지
차단해 Kill Switch 자신의 청산 주문이 거부되는 모순이 있었다.
**결정**: `submit()`이 `kind=EMERGENCY`는 `halted` 여부와 무관하게 통과시키도록 수정.
**Why**: halt()의 원래 목적(계명 1 계열 — 사람 개입 전 신규 진입 차단)이 "청산조차 못 하는"
상태까지 의도한 게 아니었다 — Kill Switch의 존재 이유 자체가 위험 상황에서 포지션을
줄이는 것인데, 그 청산 주문이 자신을 촉발한 halt에 막히면 안전장치가 스스로를 무력화한다.
**How to apply**: 향후 게이트웨이에 새 정지 조건을 추가할 때 EMERGENCY 우회가 여전히
성립하는지 반드시 회귀 테스트로 확인할 것.
**검증**: `tests/test_core_w1.py::test_halt_blocks_new_entries_but_not_emergency_liquidation`
신규, `run_full_path_smoke.py` 재실행으로 청산 로그 반복 소멸 확인. 전체 510건 통과.

---

## 2026-07-27 (10차 — dev_memory/NEXT_TODO.md·DECISION_LOG.md·Docs/capability_matrix.md 완료일자 오류 정정)

**증상**: W12~13(Triple Barrier/CV)부터 W24~26(전 경로 관통)까지 6개 작업 블록의 "완료" 일자가
문서마다 2026-07-27·07-28·07-29·07-30으로 하루씩 순차적으로 밀려 기록돼 있었음 — 사용자의
일일 점검 요청(2026-07-27)에 대응해 `git log`로 실제 커밋 시각을 대조하다가 발견.
**원인**: `git log --format="%ad"`로 확인한 실제 커밋 시각은 다음과 같이 전부 **2026-07-26
22:28 ~ 2026-07-27 00:01, 약 93분 사이**에 몰려 있었다 — 즉 마스터 플랜상 "6주 분량"(W9~11
~ W24~26)이 실제로는 하룻밤 연속 세션 하나였는데, 기록 시점에 각 작업 블록을 마스터 플랜의
주차 번호에 맞춰 순차적인 달력일로 잘못 표기했다(실제 시각을 확인하지 않고 "다음 주차니까
다음 날"로 넘겨짚은 것으로 추정):
  - W9~11(79e30f7) 22:28, W12~13(e0a523f) 22:30, W14~16(a39ae8c) 22:32,
    W17~19(7c2743b) 22:33, W20~21(0a49b7f) 22:37, W22~23(54e8c1e) 23:10 — 전부 07-26
  - W24~26(674f387) 00:01 — 자정을 1분 넘겨 07-27
**결정**: 세 문서의 해당 날짜 전부를 실제 커밋 시각 기준으로 정정 — W12~13~W22~23은
2026-07-26, W24~26만 2026-07-27. DECISION_LOG.md에는 W24~26에 대응하는 절(9차, 위)이
아예 없었던 것도 함께 채웠다.
**Why**: NEXT_TODO.md의 "에이징 규칙"(30일/60일 초과 시 주간회의 강제 상정)과
DECISION_LOG.md의 "라이브 미검증 항목은 검증 기한을 명기한다"(L15) 원칙 둘 다 날짜를
신뢰할 수 있어야 성립한다 — `scripts/agenda.py`가 이 날짜들을 그대로 파싱해 회의 안건을
자동 생성하므로, 기록된 날짜가 실제와 다르면 에이징 계산 자체가 왜곡된다.
**How to apply**: 작업 완료를 문서에 기록할 때는 마스터 플랜 주차 번호와 달력일을 별개로
취급할 것 — "완료" 타임스탬프는 반드시 그 시점의 실제 날짜(필요시 `git log -1
--format=%ad`)로 확인 후 기입한다.
**검증**: `grep -n "2026-07-2[789]\|2026-07-30"`로 세 파일 전수 재검사, 정정 후 재실행
결과 W24~26(674f387) 한 곳만 2026-07-27로 남고 나머지는 전부 2026-07-26로 일치함을 확인.

---

## 2026-07-27 (11차 — 10차 날짜 정정의 후속 소탕: 코드/pyproject.toml에 남은 동일 오류)

**증상**: 10차에서 `dev_memory/NEXT_TODO.md`·`DECISION_LOG.md`·`Docs/capability_matrix.md`
세 문서만 대상으로 정정했는데, 저장소 전체(`src`·`scripts`·`tests`·`pyproject.toml`)를
`grep`으로 다시 훑어보니 같은 종류의 날짜 오류가 4곳 더 있었다: `pyproject.toml`의
lightgbm 주석("2026-07-27 실측")·pyright pythonVersion 주석("2026-07-27"),
`src/messiah/features/engine.py`의 deque 버그 docstring/주석 2곳("2026-07-29"),
`tests/features/test_engine.py`의 회귀 테스트 docstring("2026-07-29 버그").
**원인**: 10차 정정 작업의 검색 범위 자체가 처음부터 세 문서로 한정돼 있었다 — "완료
기록이 남는 곳"을 dev_memory·capability_matrix로만 좁혀 생각했지, 코드 docstring·주석에도
같은 날짜가 인용돼 있을 수 있다는 걸 놓쳤다(모두 `git blame`으로 대조한 결과 실제 커밋
시각과 어긋난 게 확인됨 — pyproject.toml 두 곳은 각각 a39ae8c 22:32·e0a523f 22:30, engine.py/
test_engine.py는 54e8c1e 23:10, 전부 2026-07-26).
**결정**: 네 파일 전부 실제 커밋 시각(2026-07-26)으로 정정.
**Why**: 10차의 교훈("완료 타임스탬프는 실제 날짜로 확인 후 기입")이 문서에만 적용되고
코드 주석엔 적용 안 되면 반쪽짜리 수정이다 — 코드 주석도 향후 누군가 "이 버그가 언제
발견됐지"를 판단하는 근거로 쓰인다.
**How to apply**: 이런 종류의 전수 정정을 할 때는 검색 범위를 "완료를 기록하는 문서"가
아니라 저장소 전체로 잡을 것 — `grep -rn`으로 확장자 무관하게 훑는다.
**검증**: `grep -rn "2026-07-27\|2026-07-28\|2026-07-29\|2026-07-30" src scripts tests
pyproject.toml Docs *.md`로 재검사, `scripts/agenda.py`(오늘 작업이라 정당하게 07-27) 외
잔여 없음 확인. 전체 516건 회귀 없음(docstring/주석만 변경, 로직 무변경).

## 2026-07-27 (12차 — 관찰: hmmlearn 0.3.3 + numpy 2.5 DeprecationWarning, 대응 없음)

**증상**: `tests/strategy/regime/` 실행 시 `hmmlearn/utils.py:27`의 `a_sum.shape = shape`가
`DeprecationWarning: Setting the shape on a NumPy array has been deprecated in NumPy 2.5`를
553건 띄운다(2026-07-27 전체 테스트 실행 로그에서 확인).
**원인 조사**: hmmlearn 0.3.3(현재 설치·2026-07-27 기준 PyPI 최신, WebSearch로 재확인)의
내부 구현이 numpy 2.5의 신규 배포 정책(배열 shape 직접 대입 금지 예고)에 아직 안 맞춰져
있다 — hmmlearn 저장소에 이 정확한 증상에 대한 공개 이슈/수정을 찾지 못했다(우리 코드가
아니라 서드파티 라이브러리 내부라 우리가 고칠 수 없음). numpy를 낮추는 우회는 lightgbm
4.7.0 사고(위 5차 절 참고)에서 이미 확인했듯 scipy를 다시 깨뜨려 선택지가 아니다.
**결정**: 코드 수정 없이 "관찰·추적"만 한다 — DeprecationWarning일 뿐 지금 515건 전체
테스트 통과에 영향 없다(`lgb.Dataset` 크래시처럼 즉시 조치가 필요한 사고가 아님, 사고가
아니라 관찰 항목으로 분류한 이유). `pyproject.toml`의 `ml` extras 주석 + Docs/
capability_matrix.md에 근거를 남기고, NEXT_TODO.md "등록된 관찰 항목(분기회의)"에도
추가했다.
**Why**: 경고 문구 자체가 "이 동작이 향후 제거될 것"이라고 예고하고 있어, numpy가 실제로
제거하는 릴리스가 나오면 이 DeprecationWarning이 그대로 런타임 예외로 바뀌어 `RegimeAI`/
`RegimeRuntime`(W20~21, W24~26)이 깨진다 — 지금은 무해하지만 "언젠가 터질 수 있는 조용한
시한폭탄"이라 완전히 무시하지 않고 추적 항목으로 남겨야 한다.
**How to apply**: numpy 버전을 올릴 일이 생기면(다른 이유로든) 반드시 `tests/strategy/
regime/`(hmm_model·naming·rules·service·runtime) 전체 통과를 먼저 확인할 것. hmmlearn
쪽에 이 경고를 없앤 새 릴리스가 나왔는지도 그때 함께 확인.
**검증**: 해당 없음(코드 변경 없는 관찰 기록) — 문서화만으로 이 절의 목적 달성.

---

## 2026-07-27 (13차 — Phase 4 착수 전 선행 인프라 갭 3건: Event Calendar·백테스트 하니스·Options AI 인프라)

사용자가 이전 일일점검에서 제시한 "고도화 제안 3종"(Phase 4 선행 갭·Event Calendar·백테스트
하니스)을 우선순위 그대로가 아니라 안전·의존성 순서로 재배열해 진행했다 — Event Calendar
(외부 의존 없음, 가장 안전) → 백테스트 하니스(오프라인, 합성 데이터로 완결 가능) →
Options AI 인프라(실계좌 WS/REST 개입 필요, 오늘 라이브 파이프라인이 도는 중이라 가장
조심스러움) 순.

### [설계결정] Event Calendar는 "KRX 개장일" 좁은 스코프만 — 경제지표 캘린더는 별개

**근거**: Ver 1.4 §2.7 EV Feature 14개 중 `ev_econ_prox`/`ev_econ_grade`(FOMC·CPI 캘린더)까지
"Event Calendar"라는 이름 하나에 넣으면 스코프가 무한정 커진다 — 전자는 정적 공휴일
테이블(연 1회 갱신)로 끝나지만 후자는 매일 갱신되는 경제지표 발표 일정 피드가 필요한
완전히 다른 종류의 외부 의존이다.
**결정**: `core/event_calendar.py`는 "KRX가 문을 여는가"·"지금이 장중인가"만 다룬다.
`strategy/regime/rules.py`의 `rule_economic_event`는 이번에도 미발동 상태로 남긴다.
**Why**: 좁은 스코프가 실제로 완결 가능했다 — 정적 공휴일 테이블 + 요일 기반 세션 판정은
외부 API 호출 없이 순수 계산으로 끝나, 이번 세션 안에 "구현·테스트·실사용처 연결"까지
전부 마칠 수 있었다. 경제지표 캘린더까지 넣었다면 데이터 소스 확보 단계에서 막혀 아무
것도 완결 못 했을 것이다.
**How to apply**: `ev_econ_*` 규칙을 살리려면 별도 프로젝트(경제지표 캘린더 피드 연동)로
착수할 것 — `RuleContext.econ_grade`/`econ_prox_days` 필드는 이미 준비돼 있다(rules.py).
**검증**: `tests/test_event_calendar.py` 25건, `tests/risk/test_risk_engine.py` R4/R6 6건,
`tests/strategy/test_pipeline.py` 2건 — 전체 570건 회귀 없음.

### [설계결정] R4/R6은 서로 다른 폭의 창(30분/10분)으로 분리 — 겹치되 독립적인 이중 방어

**근거**: Holding Policy Ver 1.0 §2.2 Type A는 "장 마감 전 강제 청산(예: 마감 10분 전)"
하나만 예시로 든다 — R4(오버나이트 증거금 25%)와 R6(오버나이트 자격)를 같은 창으로
겹치면 R6가 먼저 걸려 R4가 사실상 죽은 코드가 된다.
**결정**: R6은 10분(기본), R4는 그보다 넓은 30분(기본)로 분리 — 10~30분 구간에서는
증거금 한도만 강화되고, 10분 이내부터 신규 진입 자체가 전면 거부된다.
**Why**: 두 게이트가 각자 독립적으로 검증 가능해야 "R4가 실제로 동작한다"는 걸 테스트로
증명할 수 있다 — 겹치는 창이었다면 R4 전용 테스트를 짜는 것 자체가 불가능했을 것이다.
**How to apply**: 두 상수(`overnight_flatten_lead_minutes`·`overnight_margin_window_minutes`)를
바꿀 때는 항상 전자 < 후자를 유지할 것(역전되면 R6가 R4보다 늦게 걸려 설계 의도가 깨짐).
**검증**: `test_rejects_overnight_margin_window_r4_at_stricter_cap`·
`test_same_margin_usage_passes_outside_overnight_window` — 같은 증거금 사용률(30%)이
창 안/밖에서 다르게 판정됨을 직접 확인.

### [버그] `MultiHorizonBarComposer`가 `MessageBus` 구체클래스를 요구해 `InProcessBus` 주입 시 pyright 오류

**증상**: `backtest/harness.py`가 검증구간 재생에 `InProcessBus`를 넘기자 pyright가
"MessageBus와 불일치" 오류.
**원인**: `features/engine.py`가 W14~16에 똑같은 이유로 `BusLike` Protocol로 바뀐 적이
있는데, `bar_composer.py`는 그때 안 바뀌었다 — `scripts/run_full_path_smoke.py`가 이미
같은 패턴(InProcessBus를 MultiHorizonBarComposer에 주입)을 썼지만 `scripts/`는 pyright
검사 대상 밖(`[tool.pyright] include = ["src"]`)이라 안 드러났었다.
**결정**: `bus: MessageBus` → `bus: BusLike`로 교체. `publish`/`subscribe`만 쓰고 `connect`/
`close` 등 나머지 `MessageBus` 메서드는 안 쓴다는 걸 확인 후 적용.
**Why**: `src/` 안에서 InProcessBus를 쓰는 두 번째 소비자(harness.py)가 생기고 나서야
이 타입 불일치가 실제로 드러났다 — `scripts/`에 갇혀 있던 잠재 버그가 `src/`로 재사용
범위가 넓어지면서 표면화된 전형적인 사례.
**검증**: `tests/backtest/test_harness.py` 통합 테스트 2건이 실제로 `MultiHorizonBarComposer`
+`InProcessBus` 조합을 실행해 통과, pyright 재실행으로 오류 소멸 확인.

### [설계결정] `MultiSymbolTickCollector`는 만들되, 실계좌 검증은 오늘 세션 종료 후로 미룬다

**근거**: 2026-07-23 실측으로 "동일 계좌 WS 연결 2개 → 반복 단절"이 이미 확인돼 있는데,
바로 오늘(2026-07-27) `run_l1_daily.py`가 이 계좌로 실제 라이브 수집 중이다. 지금
`MultiSymbolTickCollector`를 실계좌로 검증하려면 정확히 그 "연결 2개" 조건을 의도적으로
재현해야 하는데, 그러면 오늘 라이브 세션의 안정성을 해칠 실질적 위험이 있다.
**결정**: 클래스 자체(단일 연결에 여러 `subscribe()`)는 지금 구현·mock 테스트까지 완료.
실계좌 검증은 [[l1_gap_deferral_to_weekly_review]]와 같은 이관 논리로 다음 기회(비거래일·
비거래시간 또는 오늘 세션 종료 후)로 명시적으로 미룬다.
**Why**: "구현됨≠검증됨" 원칙은 검증을 生略해도 된다는 뜻이 아니라, 검증되지 않은 채로
있다는 사실을 숨기지 않는다는 뜻이다 — 여기서는 오히려 "지금 검증하면 위험하다"는
사실 자체가 기록할 가치가 있는 판단이었다.
**How to apply**: 다음 검증 시점엔 futures(A05608)+option(임의 위클리 종목) 동시 구독으로
2026-07-23에 관측된 반복 단절이 실제로 해소됐는지까지 확인할 것.
**검증**: mock `WSConnection` 기반 단위 테스트 16건(연결 하나에 subscribe 2회·TR별 라우팅·
심볼별 tick_size 정확성 등) — 실계좌 검증은 없음(의도적 보류, 위 참고).

### [설계결정] `InvestorFlowSnapshot`은 필드를 파싱하지 않고 raw dict를 그대로 보존한다

**근거**: KIS `get_investor_flow()` 응답의 구체 필드(외국인/기관/개인 순매수 수량이 몇
번째 필드인지)를 확정할 근거(docs/efriend 엑셀 또는 실계좌 실측 캡처)가 이 세션엔 없다.
**결정**: 필드 인덱스를 추측해서 하드코딩하지 않는다 — `TOPIC_RAW`(W1부터 있었지만 아무도
안 쓴 토픽)로 raw dict를 그대로 발행하는 폴링 인프라만 이번 스코프로 좁혔다.
**Why**: symbol_master의 미니선물 상품종류 "B" 사례(2026-07-22)에서 이미 "추측 대신 실측"
원칙이 실제로 버그를 막아준 전례가 있다 — 필드 순서를 잘못 추측해 하드코딩했다가 나중에
실측으로 뒤집히면, 그 사이에 이미 그 필드로 학습된 Feature/모델이 조용히 오염된다.
**How to apply**: FL Feature(`fl_frgn_cum` 등)가 실제로 필요해지면, 먼저 실계좌로
`get_investor_flow()` 원시 응답을 캡처해 필드 의미를 확정한 뒤(docs/KIS_RAW_FIELD_RANGES.md
같은 문서에 기록) `normalizer.py`에 파서를 추가할 것 — 이 순서를 바꾸지 말 것.
**검증**: `tests/data/test_investor_flow_poller.py` 5건(부분 실패 시 계속 진행·발행 실패
로깅·`FixedTickScheduler` 실제 연동 등). 전체 570건 통과, ruff 클린, pyright는 신규 파일
기준 클린(사전 확인된 기존 오탐 2건 — `sys.stdout.reconfigure()`·`Handler`/`BarClosed`
분산성 — 과 무관함을 개별 대조로 확인).

---

## 2026-07-28 (14차 — Phase 4 전체 착수: Options AI(Vol Engine~안전규칙) + Risk Engine
R7~R9 + Command Center UI, W27~34)

Master Plan Ver 2.0 §9 Phase 4(W27~34) 세 서브페이즈를 한 세션에서 순서대로 완료 —
계획 문서(`C:\Users\82108\.claude\plans\lively-frolicking-petal.md`)를 먼저 작성해
사용자 승인을 받은 뒤 W27~29(Vol Engine·매트릭스) → W30~31(Evaluator·Lifecycle·안전규칙·
Risk Engine R7~R9) → W32~34(Command Center UI) 순으로 구현. 최종 739건 통과(신규 341건),
ruff/pyright 전체 클린.

### [설계결정] KIS 원시 Greeks/IV를 신뢰하지 않고 Black-76으로 자체 계산한다 (surface.py)

**근거**: 마흐디 L16(theta 필드가 원화 단위인 줄 모르고 좁은 스키마를 잡아 5일간 데이터가
조용히 잘린 사고) — 그리고 이 프로젝트엔 현물지수 실시간 피드가 없다(RG 데이터소스 기존
갭, capability_matrix.md).
**결정**: `OptionQuoteSnapshot`은 옵션 가격조차 파싱하지 않고 `raw` 그대로 보존(필드 매핑
근거 없음, FL Feature 갭과 동일 패턴). `strategy/options/surface.py`가 Black-76(현물이 아닌
선물 기준 — 이미 실시간 수집 중인 A05608을 그대로 씀) 프라이서로 IV·Greeks를 자체 계산.
**Why**: 없는 데이터(현물지수)를 있다고 가정하거나 검증 안 된 브로커 필드를 신뢰하는 대신,
있는 데이터(선물가)에 맞는 모델을 골랐다 — 단위도 처음부터 이 프로젝트가 정의하므로 L16류
사고 자체가 성립하지 않는다.
**How to apply**: 옵션 REST 응답 필드 매핑이 실측되더라도(docs/efriend 엑셀·실계좌 캡처)
Greeks/IV는 계속 자체 계산 유지 — 매핑은 가격(bid/ask)까지만 쓴다.
**검증**: `tests/strategy/options/test_surface.py` 31건 — 특히 손으로 옮긴 델타/감마/베가
해석식을 프라이서 자기 자신의 유한차분과 교차검증(공식 전사 실수를 그 자리에서 잡아내는
설계), put-call parity, IV round-trip 전부 통과.

### [버그] Ver 1.3 §4.2 델타 배정("매도=15~30Δ, 매수=30~50Δ")을 신용 스프레드에 문자 그대로
적용하면 행사가 순서가 뒤집혀 구조가 무효화된다

**증상**: `matrix.py` 최초 구현은 매도/매수 다리 구분 없이 원문 델타 밴드를 그대로 배정.
`evaluator.py` 작성 중 `BULL_PUT_SPREAD`의 실제 다리(매도풋 vs 매수풋) 행사가 순서를 손으로
검증하다가 발견.
**원인**: 콜은 행사가가 낮을수록, 풋은 행사가가 높을수록 델타 절대값이 크다(`surface.py`
단조성) — 원문 규칙은 차변(debit) 스프레드(매수 다리가 근접 등가격/큰 델타) 기준으로는
맞지만, 신용(credit) 스프레드는 반대로 **매도 다리가 근접 등가격**이어야 순수취가 나오고
행사가 순서(매도 행사가 > 매수 행사가, 풋 기준)가 성립한다. 원문 그대로 적용하면 매도
다리에 작은 델타(낮은 행사가)가 배정돼 매도/매수 행사가가 역전된다.
**결정**: `matrix._build_spec()`이 신용 스프레드에서는 두 델타 밴드를 바꿔 배정(매도=근접
등가격 30~50Δ, 매수=날개 15~30Δ), 차변 스프레드는 원문 그대로 유지. 두 모듈(matrix.py·
evaluator.py) docstring에 계산 근거를 남김.
**Why**: 실제 시장 관행이 아니라 "매도/매수 행사가 순서가 성립해야 구조 자체가 유효하다"는
수학적 필요조건에서 나온 결정 — 어느 델타 값을 쓰느냐(캘리브레이션)보다 우선하는 정합성
문제였다.
**How to apply**: 향후 델타 밴드 수치를 Walk-Forward로 재조정하더라도, 신용/차변 스프레드의
밴드 역할(매도↔매수)은 절대 통일하지 말 것 — 통일하는 순간 이 버그가 재발한다.
**검증**: `tests/strategy/options/test_matrix.py`(신용/차변 밴드 스왑 검증 2건) +
`tests/strategy/options/test_evaluator.py`(4개 스프레드 구조 전부 실제 행사가 순서 확인,
Iron Condor 4다리 포함) — 전부 통과.

### [설계결정] 매트릭스가 애초에 네이키드 매도 후보를 만들지 않는다

**근거**: Ver 1.3 §4.1 표는 IV 높음 칸에 "풋매도"·"콜매도"·"Strangle 매도"(전부 네이키드)를
후보로 적지만 §6-1 "네이키드 매도 금지 — 예외 없음"과 정면 충돌한다.
**결정**: `matrix.py`가 그 세 라벨을 각각 `BULL_PUT_SPREAD`·`BEAR_CALL_SPREAD`·
`IRON_CONDOR`(전부 스프레드, 최대손실 유한)로 치환 — `safety.py`가 사후에 걸러낼 필요
자체를 없앰.
**Why**: "생성 후 안전규칙이 거른다"보다 "애초에 위반을 생성하지 않는다"가 더 강한 보장 —
`risk_engine.py`가 R1을 게이트가 아니라 Sizer 사이징 상한으로 구조적으로 막는 것과 같은 철학.
**검증**: `test_matrix_cell_*_uses_*_not_naked_*` 3건, `evaluator.py`의 `_structural_max_loss`가
지원 구조 전부에서 항상 유한값 반환함을 회귀로 확인.

### [설계결정] Risk Engine R7/R8/R9은 `evaluate()`와 분리된 별도 메서드, R9는 `safety.py` 재사용

**근거**: R1~R6·R10~R12는 "의도 1개 + 계좌 상태"가 입력인데 R7(순델타)·R8(순베가)은 "옵션
포트폴리오 전체의 합산 Greeks"가 입력이라 성격이 다르다. R9(매도옵션 손실 2배)는
`strategy/options/safety.exceeds_loss_limit()`(§6-5)와 임계가 완전히 동일하다.
**결정**: `evaluate_options_portfolio()`(R7·R8, 신규 게이트)와
`positions_requiring_forced_liquidation()`(R9, 탐지 전용 — `exceeds_loss_limit()` 그대로
호출) 두 메서드로 분리 추가. `BrokerPosition`에 `greeks: GreeksProfile | None = None` 필드
추가(기본값 None, 기존 keyword 생성 호출부 전부 하위호환 확인).
**Why**: 같은 규칙(§6-5/R9)을 두 곳에서 따로 구현하면 나중에 어긋날 위험이 있다 — Options AI
Lifecycle Manager의 판정과 Risk Engine의 판정이 정확히 같은 함수를 쓰면 그 위험이 원천
차단된다.
**How to apply**: 옵션 주문 실행 경로(다리 여러 개짜리 스프레드 주문 구성)가 생기기 전까지는
`BrokerPosition.greeks`를 채우는 어댑터가 없다 — R7~R9는 게이트만 준비된 상태(알려진 갭).
**검증**: `tests/risk/test_risk_engine.py` R7 5건·R8 3건·R9 4건, 기존 R1~R6·R10~R12
회귀 없음(전체 61건).

### [설계결정] Command Center UI는 `streamlit.testing.v1.AppTest`로 실제 테스트 가능하다
(계획 문서의 가정을 뒤집음)

**근거**: 계획 수립 시점엔 "Streamlit 앱은 렌더 시마다 스크립트를 통째로 재실행하는 모델이라
일반 pytest로 못 돌린다"고 가정하고 UI 하위 모듈(state_cache·data_source)만 단위테스트
대상으로 잡았었다. 구현 후 수동 검증 단계에서 `AppTest`(Streamlit 공식 테스트 API, 브라우저·
실제 서버 포트 불필요)가 정확히 이 문제를 풀기 위해 존재함을 확인.
**결정**: `tests/ui/test_app_smoke.py` 신규 — REPLAY 기본 모드 무예외, LIVE 전환(Redis 없이도
백그라운드 스레드 예외가 메인 스크립트로 안 새어나옴) 무예외, Kill Switch 2단 확인 클릭
흐름 무예외를 실제로 실행해 확인.
**Why**: 계획이 틀렸다는 걸 구현 중 발견하면 계획을 고집하지 않고 더 나은 방법(실제 테스트)을
채택한다 — "이건 테스트 못 한다"는 가정 자체를 매번 재검증할 가치가 있다는 사례.
**How to apply**: React 이관(Ver 2.2) 전까지 Streamlit 화면을 고칠 때마다 이 스모크 테스트를
먼저 확장할 것 — 새 존/버튼을 추가하면 최소 "무예외" 테스트 하나는 같이 추가.
**검증**: `tests/ui/test_app_smoke.py` 3건 + `test_state_cache.py` 8건 + `test_data_source.py`
11건, 전부 통과.

### [설계결정] LIVE 모드에서 Stream 토픽(`decision.intent`/`exec.fill`)은 pub/sub 구독으로 못
받는다는 것을 UI 배선 중 재확인

**근거**: `core/bus.py`의 `MessageBus.publish()`는 `STREAM_TOPICS`를 `XADD`로 쓰는데
`subscribe()`는 `psubscribe`(pub/sub)만 구독한다 — 기존에도 있던 설계지만 UI가 두 종류
토픽을 동시에 필요로 하면서 처음으로 실무적으로 부딪힘.
**결정**: `ui/app.py`의 LIVE 배경 스레드가 `CacheSubscriber`(pub/sub)와 `read_stream()`
폴링 루프(`_poll_streams_forever`, `decision.intent`/`exec.fill` 전용)를 `asyncio.gather()`로
동시에 돌리도록 분리 배선.
**How to apply**: 향후 다른 Stream 토픽(`capital.order_request`/`exec.order`)을 화면에
띄우려면 `_poll_streams_forever`의 `last_ids` 딕셔너리에 추가하기만 하면 됨.
**검증**: `test_cache_subscriber_updates_cache_by_message_type_name` 등 `CacheSubscriber`
단위테스트로 pub/sub 경로만 우선 확인 — Stream 폴링 자체는 실제 Redis 없이는 단위테스트
불가(모듈 docstring에 명시), AppTest로 "예외 없이 스레드가 뜬다"까지만 확인.

---

## 2026-07-28 (15차 — Phase 5 착수: Registry·Shadow Manager·Self Evaluation·릴리스
패키징·복제 배포 리허설·잔여 Horizon·G2 페이퍼 트레이딩 하네스, Ver 2.0 §9 W35~40)

사용자가 "Phase 5를 구현해서 메시아를 완성하고 모의투자로 운영을 시작할 수 있는지 조사하고
그 손익을 조사해서 보고해"를 요청. 구현 착수 전에 먼저 조사한 결과를 사용자에게 보고하고
스코프를 합의받은 뒤 진행한 세션 — 그 조사·합의 과정 자체가 이번 세션의 첫 번째 중요한
결정이다.

### [설계결정] "손익 조사"는 이번 세션 스코프에서 뺀다 — "Phase 5 인프라만 구현"으로 합의

**근거**: 조사 결과 (1) 실제 KIS 서버로 수집된 시장 데이터가 단 3거래일치(2026-07-24·27·28)
뿐이라 Ver 1.2 §8.1 "최소 확보 목표: 틱/호가 2년치"에 한참 못 미치고, (2) 지금까지 학습된
모든 Expert는 전부 합성(사인파) 데이터 기준이라 G1 백테스트 관문을 실제 데이터로 통과한
모델이 하나도 없으며, (3) G2 관문 자체가 Ver 2.0 §8 표에 "40거래일" 관찰 기간의 산출물로
정의돼 있어 하루 세션으로 손익을 만들어낼 방법이 원천적으로 없다.
**결정**: `AskUserQuestion`으로 세 가지 진행 방향(① Phase 5 인프라만 구현 ② 데이터 축적
우선, Phase 5는 보류 ③ Phase 5 구현 + 합성/3거래일 데이터로 즉석 추정 손익 보고)을 제시,
사용자가 ①을 선택. 이후 전 작업을 "실제 우위 검증이 아니라 배관 검증"이라는 틀로 진행하고,
모든 신규 스크립트·모듈 docstring에 이 사실을 반복 명시했다.
**Why**: 마스터플랜 §8 스스로 "G3까지 최소 4~5개월: 서두름이 최대의 리스크"라고 경고하는데,
실제 캘린더는 8일째였다 — 없는 우위를 있는 것처럼 보고하면 이 프로젝트 전체가 서 있는
"실측으로 검증, 안 되면 정직하게 실패 보고" 원칙(W14~16부터 반복된 패턴)이 무너진다.
**How to apply**: 앞으로 이 프로젝트에서 "Phase N을 완성해라" 류의 큰 요청이 오면, 코드
구현 가능 여부와 그 코드가 만들어내는 숫자(손익·정확도 등)가 의미를 가지는지는 별개
질문이라는 것을 먼저 분리해서 사용자에게 보고할 것 — 데이터/시간이 필요한 관문(G1/G2/G3,
Shadow 20거래일)은 코드를 아무리 잘 짜도 앞당길 수 없다.
**검증**: 해당 없음(설계·소통 결정) — 이후 산출물이 전부 이 스코프를 지켰는지는 아래
항목들의 모듈 docstring·capability_matrix.md "선행 조사" 인용문으로 추적 가능.

### [설계결정] Registry(Horizon 단위 번들 상태기계)와 Release(멀티 Horizon 배포 스냅샷)를
두 계층으로 분리한다

**근거**: Ver 1.6 §9.1의 `bundle_id`(예: `"5m_v2026.08.01"`)는 Horizon 하나짜리인데,
`configs/instance.yaml`의 `model_bundle`(Ver 1.1 §7.2 예시 `"release-2026.07.21"`)은 PC
전체가 참조하는 배포 단위 — 이미 존재하는 `scripts/self_check.py`의 `check_bundle()`이
`data/models/{model_bundle}/manifest.yaml`을 찾는 것도 이 상위 개념을 전제하고 있었다(W1~2
부터 있었지만 아무도 안 채운 자리). 두 개념을 하나의 클래스로 합치면 "Horizon마다 승격
시점이 다르다"는 사실을 표현할 방법이 없어진다.
**결정**: `models/registry.py`(`ModelRegistry`, Horizon 하나짜리 candidate→shadow→live→
retired)와 `models/release.py`(`pack_release()`/`verify_release()`, 그 순간 각 Horizon의
live를 스냅샷으로 묶음)로 분리. `verify_release()`는 릴리스 발행 이후 참조된 번들이
강등되는 "번들 손상 배포"(Ver 1.6 §12)까지 감지한다.
**Why**: 실제 배포 시나리오(한 Horizon만 재승격되고 나머지는 그대로인 상태에서 릴리스를
새로 깎는 것)를 코드가 표현 못 하면, 나중에 "이 릴리스가 정확히 어느 모델들의 조합인지"를
사람이 수동으로 추적해야 한다 — 그게 바로 Ver 1.1 §7.3이 "릴리스 = git tag + 모델 번들"로
명시적으로 고정하려는 문제다.
**How to apply**: 향후 실제 `live` 번들이 생기기 시작하면(G1 통과 후), 릴리스는 항상
`pack_release()`로 만들고 `configs/instance.yaml`의 `model_bundle`을 그 `release_id`로
갱신할 것 — Horizon별 `manifest.yaml`을 직접 가리키게 하지 말 것(2계층 원칙이 깨짐).
**검증**: `tests/models/test_release.py` 5건(부분 릴리스의 `missing_horizons`·전체 커버리지·
manifest round-trip·정합성 검증 pass/fail 양쪽) + `run_phase5_smoke.py` 실제 실행(릴리스
1개 Horizon만 채운 상태로 정상 생성, `verify_release()` 문제 없음 확인).

### [설계결정] Shadow Manager는 실주문 경로(Risk Engine·Sizer·OrderGateway)를 타지 않고
독립된 단순 청산 규칙을 쓴다

**근거**: `FuturesAIService`/`TradingPipeline`(챔피언 경로)을 shadow 번들에도 그대로
재사용하면 Risk Engine의 증거금 사용률·연속손실 등 "실제 계좌 상태" 전제 로직까지 다시
거치게 된다 — 가상 모델을 위해 그 상태를 오염시키거나 챔피언/Shadow 분기 처리를 그
컴포넌트들에 추가하면 계명 1(주문 경로는 OrderGateway 하나)의 정신에 어긋난다.
**결정**: `ShadowLedger`(포지션 1개, `models/labeling.py`의 `BARRIER_PARAMS` 시간배리어만
재사용하는 독립 청산 규칙)로 별도 구현. Risk Engine이 거부·축소했을 신호도 Shadow는 전부
진입한다는 점을 모듈 docstring에 명시.
**Why**: "Shadow 성적이 챔피언과 정확히 같은 정교함으로 계산되는 것"보다 "실주문 경로에
부작용을 주지 않는 것"이 더 강한 제약이라고 판단했다 — Shadow는 어차피 "상대 비교"용
근사치이지 실전 재현이 목적이 아니다(Ver 1.1 §6-4 원문도 "가상 주문 성적 기록"이라고만
하지 실행 경로 재사용을 요구하지 않는다).
**How to apply**: Shadow 성적을 근거로 승격 여부를 최종 판단할 때는 이 근사의 한계(Risk
거부 신호도 전부 카운트됨)를 항상 함께 고려할 것 — `evaluate_promotion()`의 `recommended`는
그래서 "사람이 검토할 가치가 있다"는 신호일 뿐 자동 승격 근거가 아니다.
**검증**: `tests/models/test_shadow_manager.py` 11건(피라미딩 금지·시간배리어 청산·메타
필터링·버스 발행) + `run_phase5_smoke.py`의 직접 시연(강신호 주입으로 실제 `ShadowFill`
1건 생성 확인, 유기적 재생은 합성 데이터 예측력 부재로 0건 — 기존 갭과 동일 이유).

### [버그] `models/search.py` — 극소 표본 학습 폴드에서 LightGBM이 Python 예외가 아니라
네이티브 크래시를 낸다

**증상**: 잔여 Horizon(1m·3m·10m) 검증 중 3m 실제 아카이브(11봉)로
`run_formal_expert_training_smoke.py --horizon 3m`을 실행하자
`lightgbm.basic.LightGBMError: Check failed: (num_data) > (0)`로 스크립트 전체가 죽음.
**원인**: Triple Barrier 레이블이 6건뿐이라 `PurgedKFold(n_splits=2)`의 한쪽 폴드가 학습
표본 1행까지 깎였고, `bagging_freq=1`(고정)+생산 탐색공간의 `bagging_fraction`(0.5~0.9)이
그 1행을 `floor(1×fraction)=0`행으로 반올림 — LightGBM이 빈 배깅 서브셋으로 학습을 못 함.
기존 `if not train_idx or not test_idx: continue` 가드는 "폴드가 원래 비었는지"만 보지
"폴드가 비지 않았지만 배깅 후 비게 되는지"는 못 봐서 못 막았다.
**결정**: `objective()`의 `lgb.train()` 호출을 `try/except lgb.basic.LightGBMError: continue`
로 감싸 그 폴드/그 trial만 건너뛴다(전체 탐색은 계속). `n=3, n_splits=2`(폴드 하나가
결정론적으로 정확히 1행이 되는 최소 반례)로 회귀 테스트 고정.
**Why**: 원인이 "잘못된 하이퍼파라미터 조합"이 아니라 "너무 작은 데이터 규모의 구조적
경계 조건"이라 예외를 없애는 게 아니라 그 trial/폴드만 안전하게 버리는 게 맞는 대응이다 —
프로덕션 규모(2년치)에서는 이 경계 자체가 성립하지 않는다.
**How to apply**: 실측 아카이브가 지금처럼 작은 동안(며칠~몇 주치) 다른 Horizon·다른 날짜로
같은 스모크를 돌릴 때 비슷한 네이티브 크래시가 또 나면, 이 가드가 이미 흡수하고 있을
가능성이 높다 — 만약 여전히 크래시가 나면 다른 종류의 LightGBM 네이티브 오류일 수 있으니
에러 메시지를 먼저 확인할 것(이번처럼 `bagging_fraction` 반올림이 원인이 아닐 수 있음).
**검증**: `tests/models/test_search.py::
test_search_survives_single_row_training_fold_bagging_crash` — 수정 전 코드로는 즉시
재현, 수정 후 통과 확인. 이어서 `run_formal_expert_training_smoke.py --horizon 3m` 전체
재실행으로 실제 시나리오 해소 확인(실제 아카이브 out-of-fold 6건 성공).

### [버그] `risk/cost_model.py` — `CostModel`에 설정값 조회용 공개 프로퍼티가 없었다

**증상**: `models/self_evaluation.py`의 `reconcile_slippage()`가 예측 슬리피지(Ver 2.0 §6)를
읽으려 `cost_model.config.expected_spread_ticks`를 호출 → `run_phase5_smoke.py` 최초 실행
중 `AttributeError: 'CostModel' object has no attribute 'config'` 실측(사설 `_config`만
있었고 공개 프로퍼티가 없었음 — 지금까지는 `CostModel` 내부 메서드들만 `self._config`를
썼지 외부에서 설정값을 읽어야 하는 소비자가 없었다).
**결정**: `CostModel.config`(조회 전용 프로퍼티) 신규 추가.
**Why**: `HorizonExpert`가 `feature_set`/`horizon`/`model_version` 등을 이미 같은 방식(사설
필드 위 공개 프로퍼티)으로 노출하고 있어 — 새 소비자(Self Evaluation)가 생겼을 때 그
패턴을 그대로 따르는 것이 일관성 있는 선택이었다.
**검증**: `tests/risk/test_cost_model.py` 2건(커스텀 config 노출·기본값 노출) +
`run_phase5_smoke.py` 재실행으로 실제 호출 경로 정상 동작 확인.

---

## 2026-07-29 (16차 — Task Scheduler·Docker 자동화 점검 + Docker Desktop 자가 기동)

사용자 요청: "Messiah"/"Messiah-Shutdown" 작업 스케줄러 등록 상태 + Docker를 조사하고
자동 시작·종료에 문제없는지 점검. 조사 결과를 보고했더니 사용자가 취약점 원인을 정정
("07:30에 다른 프로젝트가 구동되며 Docker Desktop이 켜진다")하고, MESSIAH 자신이 Docker
On/Off를 확인해 꺼져 있으면 스스로 켠 뒤 구동하도록 코드 개선을 요청.

### [조사] Task Scheduler·Docker 실측 결과

`schtasks`/`Get-ScheduledTask`로 확인: "Messiah"(평일 08:35, `run_l1_daily.bat`)·
"Messiah-Shutdown"(평일 15:40, `stop_l1_daily.bat`) 둘 다 등록·활성 상태, 최근 실행 전부
성공(Last Result 0). 로그 실측(07-24/27/28): 07-27·07-28 두 거래일 모두 08:35 정각 자동
트리거, CRITICAL 0건, "정상 종료"까지 완주. Shutdown watchdog 5회 실행 전부 "no leftover
process found"(내부 종료 로직이 지금까지 한 번도 15:40을 넘긴 적 없어 순수 안전망으로만
존재). **`scripts/run_l1_daily.bat`의 "Not yet registered in Task Scheduler" 주석은 이미
사실과 어긋난 stale 문서였음** — 실제로는 이미 등록·정상 가동 중.

구조적 취약점 3가지 발견(사고는 아직 없었으나 재발 조건은 존재):
1. Docker Desktop `AutoStart=False` — 배치파일 어디에도 Docker 기동 로직 없음.
2. Task 트리거가 `LogonType=Interactive`·`StartWhenAvailable=False`·`WakeToRun=False` —
   PC가 꺼져있거나 로그오프 상태로 트리거 시각을 지나치면 그날은 영구히 건너뜀(캐치업 없음).
3. 실패 시 능동적 알림 없음(로그에만 남음, Ver 1.1 OBS "CRITICAL 텔레그램 푸시" 미구현).

`docker inspect` 실측: `messiah-redis`의 `StartedAt`이 2026-07-28 07:30 KST — 사용자 확인
결과 이는 **다른 프로젝트가 자기 필요로 그 시각에 Docker Desktop을 띄우는 부수효과**였다
(1번 취약점의 실제 원인 확정). 사용자가 "메시아 스스로 확인 후 필요시 켜라"는 방향으로
스코프를 확정.

### [설계결정] Docker Desktop 자가 기동을 MESSIAH 자신의 기동 시퀀스에 내재화한다

**근거**: 지금까지 정상 작동해온 이유가 "다른 프로젝트의 우연한 타이밍"이라는 걸 알게 된
이상, 그 프로젝트의 스케줄이 바뀌거나 그 프로젝트 자체가 그날 안 돌면 MESSIAH의 데이터
수집이 조용히 통째로 빠진다 — G1 관문 달성을 위한 데이터 축적이 유일한 목적인 지금 단계에서
하루라도 빠지는 건 누적 손해다.
**결정**: `core/docker_bootstrap.py` 신규 — `ensure_docker_ready()`가 `docker info`로 daemon
응답을 먼저 확인하고, 미응답이면 Docker Desktop을 스스로 띄운 뒤 최대 2분(기본값) 폴링,
daemon이 뜨면 `docker start messiah-redis`로 컨테이너까지 명시적으로 재확인(restart
policy=unless-stopped로 보통 자동으로 같이 뜨지만 한 번 더 확인). `run_l1_daily.py`·
`run_g2_paper_trading.py` 둘 다 `_run_self_check()`보다 먼저 이 단계를 실행 — self_check의
Redis 점검이 실패하기 전에 이미 Docker가 준비돼 있게 만든다. 2분 안에도 안 뜨면(Docker
Desktop 자체가 설치 안 됐거나 뭔가 근본적으로 문제) `SystemExit`으로 명시적 중단 — 조용히
넘어가지 않는다(L18 정신).
**Why**: 취약점 2·3번(로그온 필요·알림 없음)은 이번 스코프에서 안 건드렸다 — 사용자가
명시적으로 요청한 건 "Docker On/Off 확인 후 필요시 켜기"뿐이었고, Task Scheduler
설정(로그온 방식)이나 알림 체계 변경은 별도 판단이 필요한 더 큰 변경이라 스코프를 넘지
않았다.
**How to apply**: 실행파일 경로는 `MESSIAH_DOCKER_DESKTOP_EXE` 환경변수로 오버라이드
가능(하드코딩 금지 원칙, SYSTEM.md R4) — 기본값은 표준 설치 경로
(`C:\Program Files\Docker\Docker\Docker Desktop.exe`). 남은 취약점 2·3번(로그온 필요·
무알림)은 여전히 존재 — 다음에 "PC를 로그오프한 채로도 자동 수집이 되게 해달라"거나
"실패하면 알림을 달라"는 요청이 오면 이 두 항목부터 참고할 것.
**검증**: `tests/test_docker_bootstrap.py` 11건(전부 `runner`/`popen`/`sleep`/`now` 주입,
실제 docker CLI·실제 대기 없이 결정론적 검증 — `core/scheduler.py`의 `FixedTickScheduler`와
같은 설계 원칙) + 실제 실행 중인 Docker로 `ensure_docker_ready()` 직접 호출해
`already_running=True` 즉시 반환 확인(실통합 확인). 전체 테스트 800건 통과, ruff 클린.

### [검증] 실제 등록된 배치파일로 기동→종료 전체 흐름 수동 재현 (2026-07-28 18:20 KST)

사용자 요청으로 `scripts\run_l1_daily.bat`(Task Scheduler "Messiah"가 실제로 호출하는 그
파일)를 장 마감 후 시각(18:20, 정규장 15:35 마감 이후)에 직접 실행 → 이어서
`scripts\stop_l1_daily.bat`("Messiah-Shutdown"이 호출하는 그 파일)를 직접 실행. Docker
자가 기동(이미 떠 있어 즉시 통과, 자동 기동 메시지 없음 — 정상)→self_check PASS(신규
`registry` 항목 포함)→실제 KIS 마스터파일로 근월물 심볼(A05608) 조회 성공→"이미 15:35 이후
— 수집 생략" 정상 분기→`daily_close()`→"정상 종료."(exit 0)까지 전부 실제 프로덕션
진입점으로 확인. 이어서 `stop_l1_daily.bat`도 "no leftover process found"로 정상 판정(이미
자체 종료됐으므로 죽일 대상 없음 — 설계대로).
**부작용 없음 확인**: 오늘자 로그 파일(`logs/l1_daily_20260728.log`)에 이번 세션이 두 번째
`SessionStart`로 정상 추가(L24 다중 세션 처리 설계가 실제로 작동), 오늘 오전 정식 수집분
parquet 파일(`data/bars/A05608/*/2026-07-28.parquet`) 수정시각은 여전히 15:35:00 그대로(이번
테스트가 실수집 데이터를 건드리지 않음), `messiah-redis` 컨테이너 uptime도 끊김 없이 11시간
연속(재기동 없었음).
**남은 미검증 영역**: `stop_l1_daily.bat`가 **실제로 살아있는** `run_l1_daily.py` 프로세스를
찾아 죽이는 경로는 이번에도 재현하지 못함(정상 흐름에서 본 프로세스가 watchdog보다 먼저
항상 스스로 종료하기 때문) — 다만 이 경로 자체는 2026-07-24 개발 중 실제 프로세스로 이미
1회 확인된 바 있음(`Stop-Process -Id vs 파이프바인딩` 버그를 그때 발견·수정, 스크립트 자체
주석에 기록) — 인위적으로 오래 붙잡아두는 프로세스를 만들어 재현하는 것은 이번엔 실익 대비
리스크(실제 KIS WS를 장 마감 후 억지로 열게 됨)가 커 보류.

### [설계결정] 데일리 자동화(`run_l1_daily.bat`)에 Command Center UI(Streamlit)를 통합한다

**근거**: 사용자가 "메시아를 실행해도 UI는 없는가"라고 물어 확인해보니, Streamlit UI(`ui/
app.py`, Phase 4 W32~34)가 데이터 수집 자동화와 완전히 분리돼 있어 매일 자동으로는 전혀
뜨지 않고 사람이 수동으로 별도 실행해야만 하는 상태였다. 사용자가 이를 통합해달라고 요청.
**결정**: `run_l1_daily.py`의 `main()`에 `_launch_ui()` 신규 — 거래일 확인 직후(휴장일에는
기동 안 함) `subprocess.Popen`으로 Streamlit을 완전히 별도의 백그라운드 프로세스로 띄운다
(같은 venv의 `streamlit.exe`를 `sys.executable`의 형제 경로로 유도 — 하드코딩 대신).
`MESSIAH_SKIP_UI=1`로 생략 가능. UI 기동 실패(streamlit 미설치 등)는 데이터 수집을 막지
않는다(부가 기능이 전제조건이 되면 안 됨 — 로그만 남기고 계속 진행). UI 프로세스는
`stop_l1_daily.bat`(15:40 워치독)가 `run_l1_daily.py`와 같은 방식(명령줄 패턴 매칭,
`*messiah\ui\app.py*`)으로 함께 정리하도록 확장 — 새 트리거를 따로 만들지 않고 기존
독립 워치독을 재사용했다.
**Why**: 이 프로젝트는 이미 "프로세스 생애주기는 명령줄 패턴 매칭으로 관리"라는 확립된
패턴(계명, mahdi L3-1)을 갖고 있다 — 새 프로세스(UI)를 위해 별도 관리 체계를 만들기보다
그 패턴을 그대로 확장하는 게 일관성 있고, watchdog 코드도 한 줄(`-or` 조건 추가)만
늘어난다.
**How to apply**: UI는 REPLAY 기본 모드로 뜨고 사용자가 사이드바에서 LIVE로 직접 전환해야
한다(ui/app.py의 기존 원칙 그대로 — 이번 통합이 그 원칙을 바꾸지 않음). 향후 다른 부가
프로세스(예: 알림 봇)를 추가하게 되면 같은 패턴(명령줄에 고유 식별 가능한 경로 포함 →
watchdog 패턴에 `-or` 추가)을 따를 것.
**검증(실제 실행)**: 장 마감 후(18:32) `run_l1_daily.bat` 실행 → "Command Center UI
기동(PID=22604)" 로그 확인 → 실제로 `streamlit.exe` 프로세스 확인(`tasklist`), 포트 8501
LISTENING 확인, 기본 브라우저가 자동으로 접속해 ESTABLISHED 커넥션까지 형성됨을 확인.
이어서 `stop_l1_daily.bat` 실행 → 명령줄 패턴 매칭으로 **관련 프로세스 3개 전부**(streamlit
런처 stub 1개 + 그 stub이 실제로 실행한 python.exe 2개, venv 표준 인터프리터 1개 + 그
venv가 fork한 anaconda3 base 인터프리터 1개 — streamlit 콘솔스크립트 stub의 내부 동작
방식) 정확히 찾아 강제 종료 확인, 이후 `tasklist`로 PID 소멸·포트 8501 LISTENING 소멸까지
재확인. **이번 검증으로 "명령줄 패턴 매칭이 실제 살아있는 프로세스를 찾아 죽인다"는 경로
자체도 처음으로 다중 프로세스 트리 기준 확인됨**(기존엔 2026-07-24 단일 프로세스 기준
확인만 있었음).

### [설계결정] `run_g2_paper_trading.py`에도 UI를 통합하기 전에 손익(장단점)부터 물어봄 →
사용자 승인 후 진행, 진행 중 실측으로 새 버그 발견 → `core/ui_launcher.py`로 통합 재설계

**경위**: 사용자가 "지금 run_g2_paper_trading.py 통합의 손익을 조사해줘"라고 요청 —
바로 구현하지 않고 먼저 조사: G2는 아직 Task Scheduler 미등록(수동 실행 전용)이라
자동기동의 이득이 적고, Registry가 비어 있어(live 번들 0개) 화면이 사실상 빈 상태이며,
watchdog이 평일 15:40에만 도니 그 시각 밖에 돌리면 다음날까지 남을 수 있다는 세 가지
트레이드오프를 보고 → 사용자가 "지금 넣어"로 진행 확정.
**실측 중 신규 발견**: `run_l1_daily.py`의 UI가 이미 떠 있는 상태에서 `run_g2_paper_
trading.py`도 같은 방식으로 UI를 띄우게 하면 어떻게 되는지 직접 실험 — Streamlit(Windows)이
이미 점유된 포트 8501에 **두 번째 프로세스가 바인드를 시도해도 에러 없이 그냥 진행**되고,
`netstat`상 두 프로세스가 동시에 같은 포트에서 LISTENING 상태로 남는 것을 확인(요청이 둘 중
어느 쪽으로 가는지 예측 불가능해짐, 자원 낭비). 구현 전 조사 단계에서는 예상 못 했던
이번 세션의 실질적 신규 리스크.
**결정**: 중복 코드를 늘리는 대신(이미 `_ensure_docker_ready` 패턴처럼 두 스크립트에
복붙하려던 참이었음) `src/messiah/core/ui_launcher.py` 신규 — `launch_command_center()`가
기동 전 `is_ui_already_running()`(포트 응답 확인)으로 먼저 확인하고, 이미 떠 있으면
새로 안 띄운다. `core/docker_bootstrap.py`와 동일하게 `is_running`/`popen` 콜러블 주입
가능해 실제 소켓·실제 streamlit 없이 테스트 가능(테스트 8건). `run_l1_daily.py`/
`run_g2_paper_trading.py`의 `_launch_ui()`는 이제 이 공용 함수를 부르는 얇은 래퍼로 축소.
**Why**: 애초 조사에서 언급 안 한 리스크(포트 충돌)를 구현 중 발견했을 때, "일단 사용자가
승인한 범위니 그대로 진행"이 아니라 "새로 발견한 위험은 그 자리에서 막는다"를 택했다 —
이 프로젝트의 반복된 원칙("실측 중 버그 발견하면 그 자리에서 고친다")과 일치.
**How to apply**: 앞으로 세 번째 스크립트가 UI를 띄워야 하면 `launch_command_center()`를
그대로 재사용할 것 — 새 스크립트마다 복붙하지 말 것.
**검증**: `tests/test_ui_launcher.py` 8건(env skip·이미 실행 중 skip·포트 파라미터화·
exe/app 없음·정상 기동·기본 포트 상수·기동 예외 처리) + 실제 재현: `run_l1_daily.bat`로
UI를 띄운 채(포트 8501 LISTENING 확인) `run_g2_paper_trading.py`의 `_launch_ui()`를 직접
호출 → "이미 응답 중 — 중복 기동 생략" 정상 출력, 두 번째 프로세스 생성 안 됨 확인.
전체 테스트 808건 통과, ruff/pyright 클린(신규 파일 기준).

## 2026-07-29 (17차 — [MW0601] Command Center UI 포트 충돌 실사고 대응)

사용자 요청: 금일 장전·장중 로그 조사 → 이상점 정리보고 → fix 작업 우선순위 수립 → 구현
진행. 조사 중 바로 위 항목(16차)이 이미 문서화한 "MESSIAH 두 스크립트끼리 포트 충돌" 갭과는
다른 종류의 신규 사고를 실측으로 발견해 이 세션에서 바로 수정.

### [조사] 오늘 아침 Command Center UI가 하루 종일 안 뜬 사고

`logs/l1_daily_20260729.log` 08:35:10에 "Command Center UI가 이미 응답 중(포트 8501) —
중복 기동 생략"이 찍혔는데, `logs/shutdown_watchdog.log`상 전날(07-28) 18:46:58에 MESSIAH
자신의 Streamlit 프로세스는 전부 정상 종료가 확인돼 있었고, 오늘자 `logs/ui_20260729.log`
파일 자체가 생성되지 않음(= `launch_command_center()`가 popen을 시도조차 안 했다는 증거)
— 즉 08:35:10에 포트 8501을 점유하고 있던 건 MESSIAH의 UI가 아니었다. 실제로 조사 시점
(11:26)에 실측한 결과 포트 8501은 **완전히 다른 프로젝트**(`PycharmProjects\options`)의
Streamlit(PID 24236)이 점유 중이었다 — `options` 프로젝트도 포트를 지정하지 않아
Streamlit 기본값(8501)을 그대로 쓴 것뿐. `is_ui_already_running()`은 애초 설계부터 "어떤
프로세스든 응답하면 이미 뭔가 있는 것으로 간주"(제3자 프로젝트가 우연히 같은 포트를 써도
안전하게 스킵하려는 의도)였으므로 이 자체는 예상된 트레이드오프였지만, 그 대가가 "MESSIAH
자신의 화면이 아무 경고도 없이 하루 종일 안 뜬다"로 실제 발생한 것은 설계 시 문서화되지
않은 신규 사례(16차 갭 문단은 "MESSIAH 두 스크립트끼리"의 충돌만 언급).

### [설계결정] `DEFAULT_PORT`를 Streamlit 공용 기본값(8501)에서 MESSIAH 전용 고정값(8511)으로 분리

**근거**: 근본 원인은 "포트 충돌 감지 로직의 정확도"가 아니라 애초에 로컬 PC의 여러
프로젝트가 전부 Streamlit 기본값을 그대로 쓰고 있다는 것 — 감지 로직을 아무리 정교하게
만들어도(예: HTTP 응답 바디로 신원 확인 시도) Streamlit 정적 페이지는 앱마다 다른 식별
정보를 초기 HTML에 담지 않아(제목은 클라이언트 세션 연결 후 JS로 늦게 붙음) 신뢰성 있게
구분하기 어렵다고 판단. 포트 네임스페이스 자체를 안 겹치게 만드는 쪽이 더 단순하고 확실하다.
**결정**: `src/messiah/core/ui_launcher.py`의 `DEFAULT_PORT`를 8511로 변경(모듈 상단 주석에
Streamlit 기본값과 의도적으로 다르다는 점 명시), `launch_command_center()`가 `streamlit run`
호출에 `--server.port {port}`를 명시 전달하도록 수정(기존엔 포트 인자를 안 넘겨 항상 실제
Streamlit 기본값 8501에 바인딩되고 있었음 — `port` 파라미터가 `is_running()` 확인에만
쓰이고 실제 기동 명령에는 안 쓰이던 잠재 버그이기도 했음, 이번에 같이 해결). 포트 충돌 시
스킵 메시지도 `print()` 수준에서 "WARN:" 접두사 + "실제로 MESSIAH UI인지 확인 안 함, 직접
열어 확인할 것"이라는 행동지침을 포함하도록 강화(구조적으로 완전한 신원 확인은 포기하고,
사람이 로그를 봤을 때 바로 의심할 수 있게 하는 완화책 — `mlog`의 구조적 태그 로깅은 도입하지
않음, `core/docker_bootstrap.py`와 같은 순수/주입 가능 설계를 유지하기 위해 결합도를
늘리지 않는 쪽을 택함).
**Why**: 8511로 옮겨도 "제3의 어떤 프로젝트가 하필 8511을 쓰는" 경우는 이론상 여전히
가능하다 — 근본적으로 로컬 포트는 전역 공유 자원이라는 한계 자체는 없앨 수 없다. 다만
Streamlit 기본값(모든 미설정 Streamlit 앱이 쓰는 값)을 벗어나는 것만으로 실제 충돌 확률은
크게 낮아진다고 판단했고, 완전한 신원 확인(HTTP 응답 파싱 등)은 신뢰도 대비 복잡도가 안
맞다고 봐서 스코프에서 제외했다.
**How to apply**: 향후 8511에서도 같은 사고(제3자 프로젝트와 재충돌)가 재현되면, 그때는
포트를 또 바꾸는 미봉책보다 `is_ui_already_running()`에 실제 신원 확인(예: MESSIAH UI만
아는 헬스체크 엔드포인트를 `ui/app.py`에 추가하는 방향)을 검토할 것 — 이번엔 그 정도까지는
필요 없다고 판단해 보류.
**검증**: `tests/test_ui_launcher.py` 기존 8건 갱신(포트 상수·popen 인자 반영) + 신규 2건
(전용 포트값 확인, `--server.port` 인자 전달 확인) 총 10건, 전체 테스트 809건 통과, ruff
클린. **실제 streamlit로 재현 확인**: `launch_command_center()`를 실제로 호출해 포트 8511에
`streamlit.exe run ... --server.port 8511`이 실제로 LISTENING되는 것을 `Get-NetTCPConnection`
으로 직접 확인, 이미 8501을 점유 중이던 `options` 프로젝트 프로세스(PID 13944)는 전혀
건드리지 않고 그대로 유지됨을 함께 확인. 두 번째 호출 시 8511 자체 중복 기동도 정상 스킵
확인(기존 방어 로직이 새 포트에서도 그대로 작동). 검증에 쓴 프로세스는 종료 후 정리.
