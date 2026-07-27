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
