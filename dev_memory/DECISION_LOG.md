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

## 2026-07-29 (18차 — [MW0601] G2 페이퍼 트레이딩 Task Scheduler 등록)

사용자 요청: 고도화 제안("G2를 L1과 같은 방식(08:35 트리거)으로 Task Scheduler에 등록")의
구현계획 수립 후 구현. 착수 전 코드를 직접 읽다가 등록을 그대로 진행하면 안 되는 구조적
이유를 발견해 사용자에게 먼저 확인 → "리팩터링 후 등록"으로 승인받아 진행.

### [조사] G2를 제안대로 그대로 등록하면 안 되는 이유

`scripts/run_g2_paper_trading.py`의 `main()`을 실제로 읽어보니, 이전 dev_memory 서술("G2는
L1이 이미 발행한 버스를 구독만 한다")과 달리 **실제 코드는 G2도 자기 자신의
`TickCollector`(L1과 완전히 같은 계좌 자격증명·심볼·TR)를 새로 만들어 독자적인 KIS WS
연결을 열고 있었다**. `Docs/capability_matrix.md`(2026-07-23)에 이미 실측으로 확정돼 있던
"동일 계좌로 WS 연결을 2개 열면 서로 반복적으로 끊긴다"는 사실과 정면으로 충돌하는 설계 —
제안대로 L1과 같은 08:35 트리거로 그대로 등록했다면 매일 아침 그 반복 단절 버그를
재현했을 것이고, 최악의 경우 아직 무해한 G2(Registry 빈 상태라 거래 자체가 안 남)를 위해
실제로 가치 있는 L1의 실데이터 수집(G1 관문 달성을 위한 유일한 자산)을 매일 망가뜨렸을
것이다. `MultiSymbolTickCollector`(2026-07-23, 단일 연결·다중 subscribe)는 **같은 프로세스
안**에서 여러 (심볼,TR)을 함께 구독하는 경우의 해법이지, **서로 다른 두 프로세스가 각자
별도 WS 연결을 여는 경우**는 애초에 다룬 적이 없었다.

### [설계결정] G2에서 TickCollector·MultiHorizonBarComposer·FeatureEngine을 전부 제거하고 버스 구독만 남긴다

**근거**: `FuturesAIService`·`TradingPipeline`·`LiveSimBrokerFeed`·`ShadowManager` 넷 다
확인해보니 전부 `bus.subscribe()`만으로 동작하고(`feat.*`/`bar.*`/`intel.futures` 패턴),
데이터 수집 계층에 직접 의존하지 않는다 — 즉 G2가 자기 WS 연결을 열 필요가 애초에 없었다.
**결정**: `main()`에서 `TickCollector`/`MultiHorizonBarComposer`/`FeatureEngine`
인스턴스화를 전부 제거(관련 import — `KISCredentials`·`tr_codes`·`ParquetArchiver`·
`parse_futures_tick` — 도 함께 정리), `_run_regular_session()`/`_daily_close()`도 그
셋을 더는 받지 않도록 시그니처 축소(봉 flush는 이제 L1의 책임). G2는 `run_l1_daily.py`가
이미 살아서 버스에 발행 중이라는 전제 없이는 아무 신호도 못 받는 상태가 되지만, 애초에
그게 이 스크립트의 원래 설계 의도였다.
**Why**: 데이터 수집을 중복 구현하는 대신 이미 검증된 L1의 실시간 배선을 재사용하는 것이
"WS 연결 자체를 아예 안 여는" 유일하게 안전한 해법이다 — 감지·재시도 로직을 더 정교하게
만드는 접근은 근본 원인(계좌당 WS 세션 1개 제한으로 추정)을 안 건드리므로 채택하지 않았다.
**How to apply**: 앞으로 G2에 새 데이터 소스가 필요해지면(예: 옵션) 같은 원칙 — 그 데이터도
L1(또는 그 데이터의 유일한 발행자)이 버스에 이미 올려주는 것을 구독하는 방향으로 설계할 것,
G2가 스스로 새 WS 연결을 여는 방향은 다시 검토하지 말 것.
**검증**: 전체 테스트 809건 통과(이 스크립트를 직접 커버하는 단위테스트는 원래도 없음 —
통합 스크립트), ruff/pyright 클린(pyright의 `sys.stdout.reconfigure` 관련 에러 2건은
`run_l1_daily.py`에도 동일하게 있는 기존 패턴, 이번 변경과 무관 확인). **실제 라이브
재현 확인(가장 중요)**: 오늘 장중(11:57, L1이 실제 계좌로 WS 연결을 열고 정상 수집 중인
상태)에 리팩터링된 `run_g2_paper_trading.py`를 실제로 수동 실행 →
`Get-NetTCPConnection`으로 G2의 실제 워커 프로세스(PID 12928) 연결 목록 확인 결과
Redis(`::1:6380`) 4건 + 심볼마스터 REST(HTTPS 443) 1건뿐, **KIS 실시간 WS 엔드포인트
(`210.107.75.39:21000`)로의 연결은 0건** — 그 사이 L1의 기존 WS 연결(같은 엔드포인트,
포트 64260)은 `Established` 상태 그대로 유지, `l1_daily_20260729.log`도 끊김 없이 계속
발행(11:56:39→11:57:00→11:57:39, 재연결 이벤트 없음)됨을 함께 확인 — G2 가동이 L1에
아무 영향도 안 준다는 것을 실측으로 증명. 검증에 쓴 프로세스는 종료 후 정리.

### [작업] Task Scheduler "Messiah-G2" 등록 + 관련 배선

- `scripts/run_g2_paper_trading.bat` 신규(`run_l1_daily.bat`와 동일 패턴 — UTF-8 콘솔·
  ASCII 전용 파일·PowerShell tee로 `logs/g2_daily_YYYYMMDD.log` 기록).
- `scripts/stop_l1_daily.bat`의 15:40 워치독 명령줄 매칭 패턴에
  `*run_g2_paper_trading.py*` 추가 — G2도 자체 `daily_close()`/하드 데드라인 로직이
  있지만(L1과 동일 형태), 혹시 못 끝냈을 때의 안전망을 L1과 동등하게 갖추기 위함.
  `Get-ScheduledTask -TaskName Messiah`로 기존 설정(트리거 요일 비트마스크 62=평일,
  `Principal.UserId=MW0601`·`LogonType=Interactive`·`RunLevel=Limited`,
  `StartWhenAvailable=False`·`WakeToRun=False`·무제한 `ExecutionTimeLimit`)을 그대로
  확인 후 `Register-ScheduledTask`로 "Messiah-G2"를 **동일 설정·08:36 트리거**(L1보다
  1분 늦게 — WS 연결이 없어져 순서 자체는 무관해졌지만, 두 프로세스의 동시 기동 부하를
  살짝 어긋내는 용도로만 유지)로 신규 등록, 등록 직후 `Get-ScheduledTask`로 설정값이
  의도대로 반영됐는지 재확인 완료.
- `run_l1_daily.bat`의 오래된 stale 주석("Not yet registered in Task Scheduler")도
  이 김에 정정(16차에서 "이미 stale"이라고 기록만 해두고 실제 파일은 안 고쳐져 있었음
  — 실제로는 이미 "Messiah"로 등록·가동 중이라는 사실과 "Messiah-G2"가 나란히 돈다는
  사실을 반영).
**남은 갭**: G2도 L1과 같은 기존 갭(로그온 필요·무알림·`WakeToRun=False`)을 그대로
공유한다 — 이번 스코프에서 새로 만든 갭은 아님. 내일(2026-07-30) 08:36 첫 자동 트리거
결과는 `logs/g2_daily_20260730.log`로 확인 필요(다음 세션 점검 항목).

## 2026-07-29 ([MW0601]) — 거래소 서킷브레이커(CB) 자동 대응 신설

### [설계결정] "미륵" 대응 설계를 반영해 CB를 데이터단절 추정으로 감지, 재개 시 자동복구

**배경**: 사용자가 코스피 급락형 서킷브레이커 발동 스크린샷(한국거래소 20분 정지+10분
단일가매매)을 제시하며, 별도 선물 시스템 "미륵"(`C:\Users\82108\PycharmProjects\futures`)의
장중 CB 대응체계를 조사·반영할 것을 요청. 조사 결과 미륵은 KIS류 API가 CB 발동을 직접
알려주지 않아 "정상 연결 + 분봉 미수신"이라는 간접 신호로 90~300초 단계적 워치독을 돌리며,
재개(데이터 재수신) 감지 즉시 **사람 확인 없이 자동으로** 포지션을 강제청산하고 정상화한다.
MESSIAH엔 CB 대응 코드가 전혀 없었으나, 조사 중 `KillSwitch.liquidate()`가 청산주문을
1회만 시도하고 실패해도 재시도가 없다는 기존 구조적 갭을 발견 — 실제 CB가 오면 청산이
영구히 누락되고, `KillSwitch`는 "사람 확인 후에만 재가동"이 원칙이라 사람이 올 때까지
무한정 매매정지된다는, 미륵보다 훨씬 나쁜 결과가 될 수 있었다.

**검토한 옵션**: 재개 처리 — ① 미륵처럼 자동복구 ② `KillSwitch` 철학 유지(사람 확인 후
재개). 감지 범위 — ① 반응형(데이터 갭 추정)만 ② 반응형+코스피 현물지수 기반 선제 감지.
`AskUserQuestion`으로 확인한 결과 ①+① 채택 — 선제 감지는 RG(현물지수·매크로) 데이터소스가
`capability_matrix.md`상 미착수라 스코프 확대가 크다는 이유로 이번엔 제외.

**결정**: `risk/circuit_breaker_monitor.py`(신규) — `RiskEngine`/`KillSwitch`와 동일
스타일(순수 상태머신, 실행은 호출자)로 NORMAL→WARNING(90s)→SUSPECTED(150s)→
CONFIRMED(240s) 단계적 추정 + 재개 후 10분 재진입 관망(KRX 단일가매매와 동일)을 구현.
`risk_engine.py`에 R13(신규 진입 거부) 게이트 추가 — `minutes_to_close`와 동일한
"호출자가 계산해 주입" 패턴. `strategy/pipeline.py`가 CONFIRMED 시 `gateway.halt()`,
재개(`just_resumed`) 시 `KillSwitch.liquidate()`를 재사용해 EMERGENCY 강제청산 후
`gateway.resume()` — 사람 개입 없음. 이벤트 구동 구조라 데이터가 끊긴 동안은
`handle_futures_view()` 자체가 안 돌기 때문에, `watch_circuit_breaker_forever()`가
기존 `core/scheduler.py`의 `FixedTickScheduler`로 벽시계 기준 워치독을 별도로 돌린다.

**부수 발견·해결**: `circuit_breaker_monitor` 주입 시 CB로 설명되는 데이터단절 동안은
`kill_switch.evaluate()`에 `data_age_seconds=0.0`을 넘겨 `KillSwitch`의 R11(30초 지속
전면정지)이 같은 데이터단절로 별도 발동하지 않게 했다 — 안 그러면 CB 자동복구 직후 같은
호출 안에서 KillSwitch R11이 다시 `gateway.halt()`를 걸어 자동복구가 무의미해진다.

**Why**: CB는 알려진·시장 전체·일시적 이벤트라 `KillSwitch`(이상 상황 → 사람 판단 필요)와
다른 철학이 합당하다는 게 사용자와 합의한 판단. R11 30초 임계값이 CB 전용 임계값(90초)보다
먼저 걸리므로, `CircuitBreakerMonitor`가 실제로 가치를 내는 구간은 재개 후 재진입 관망뿐
이라는 게 설계의 핵심 근거(`risk_engine.py` R13 절 참고).

**스코프 밖(명시적)**: 코스피 현물지수 기반 선제 감지, 재개 후 피처/국면 버퍼 리셋
(`FeatureEngine`/`RegimeRuntime`에 reset() API 없음), 능동 알림(Slack 등 인프라 없음),
halt 이력 DB 영속화(EOD exporter 없음). 임계값(90/150/240초, 재진입 관망 10분)은 미륵의
실측 보정값을 차용한 미검증 초기값 — MESSIAH 자체 실거래 CB 관측 후 재조정 필요.

**검증**: 신규 `tests/risk/test_circuit_breaker_monitor.py`(8건) + `test_risk_engine.py`
R13 1건 + `test_pipeline.py` CB 자동청산·재진입관망 2건, 전부 통과. 전체 회귀 무손상
(`Docs/capability_matrix.md` "거래소 서킷브레이커(CB) 자동 대응" 절 참고).

### [버그] 실전 재시작 직후 콜드스타트를 CB로 오판 — `_last_bar_confirm_at is None`이면 CB 판정 자체를 건너뛰도록 수정

**증상**: 위 CB 기능을 실전 반영하려고 L1/G2를 재시작(14:39:48)한 직후, `watch_circuit_breaker_forever()`
의 첫 워치독 틱(14:40:00, `FixedTickScheduler` 30초 격자)이 봉을 한 번도 못 본 상태에서
`_data_age_seconds()`가 반환하는 `inf`를 그대로 CB 판정에 흘려 시작하자마자
`CircuitBreakerConfirmed(데이터단절 infs)` → `gateway.halt()` → 60초 뒤 첫 실봉 도착으로
`CircuitBreakerResumed`라는 거짓 CB 이벤트 한 쌍이 실제 로그에 찍힘(`logs/g2_daily_20260729.log`).

**원인**: `inf`는 R11(RiskEngine/KillSwitch) 관점에선 "봉을 아직 못 봐서 최대 위험"이라는
의도된 값이지만, CB 판정은 "이전에 흐르던 데이터가 끊겼다"는 **편차**를 감지하는 것이라
기준선 자체가 없는 콜드스타트에는 적용 대상이 아니다.

**수정**: `strategy/pipeline.py`의 `handle_futures_view()`와 `watch_circuit_breaker_forever()`
양쪽에 `self._last_bar_confirm_at is not None` 가드 추가 — 봉을 한 번도 못 본 동안은
`CircuitBreakerMonitor.observe()` 자체를 호출하지 않는다. R11(RiskEngine)은 이 경우에도
`data_age_seconds > 30s`로 이미 신규진입을 막으므로 안전 공백은 없음. 회귀 테스트
`test_cold_start_without_bars_does_not_false_positive_circuit_breaker` 추가.

**부수 발견(미해결, 별도 스코프)**: 같은 콜드스타트 상황에서 `kill_switch.evaluate()`에도
`data_age_seconds=inf`가 그대로 들어가 R11이 `gateway.halt("kill switch triggered")`를
걸 수 있다는 것을 단위테스트로 확인(`handle_futures_view()`가 `handle_bar()`보다 먼저
호출되는 경로가 이론상 가능 — 실제로는 오늘 재현 안 됨, L1/G2가 별도 프로세스라 Redis
pub/sub 교차 채널 순서가 보장 안 되는 구조적 위험은 남아있음). CB 기능과 무관한 기존
`handle_futures_view()`의 잠재 결함이라 이번 스코프에서 고치지 않고 사용자에게 보고만 함
— 다음 세션에서 필요하면 별도로 다룰 것.

**실전 검증**: 수정 후 L1/G2 재재시작(14:48:46) → self-check PASS, KIS WS
(`210.107.75.39:21000`) 연결, Redis(6380) 연결, Command Center UI(`localhost:8511`)
HTTP 200, ERROR/CRITICAL/Traceback 0건, 거짓 CB 이벤트 재현 안 됨 확인. 전체 테스트
827건 통과, ruff/pyright 클린.

## 2026-07-29 ([MW0601]) — Command Center UI: CB 상태 배지 신설 + 모드 기본값 LIVE 전환

**요청**: 사용자가 (1) Market View에 CB 상태를 알 수 있는 표시가 없다며 나이스하게 추가,
(2) 사이드바 데이터소스 모드 기본값을 LIVE로 바꿔달라고 요청.

**결정 1 — CB 상태를 버스로 발행**: `CircuitBreakerMonitor`가 `TradingPipeline` 내부
상태로만 존재해(위 항목) UI(별도 프로세스)가 볼 방법이 없었다. `core/messages.py`에
`CircuitBreakerStatus`(신규, `sys.circuit_breaker`) 추가 — `Health`(5초 heartbeat)와
같은 철학으로 phase가 그대로여도 `observe()` 호출마다(이벤트 구동 경로 + 30초 워치독
양쪽) 매번 발행한다. "값이 조용히 그대로"와 "발행이 멈췄다(STALE)"를 구분하려면 heartbeat여야
하기 때문 — 전이 시에만 발행했다면 UI의 기존 신선도 배지 인프라(`_STALE_AFTER`, 마흐디
L18)를 못 재사용했을 것.

**결정 2 — Top Bar에 5번째 컬럼으로 배지 추가**: `render_top_bar()`의 기존 4컬럼([모드,
intel.futures, decision.intent, KILL SWITCH])에 CB 배지를 끼워 5컬럼으로 확장. phase별
색상(normal 청록/warning 앰버/suspected 주황/confirmed 적색, `_CB_PHASE_COLOR`)과 재진입
관망 남은 시간 캡션을 보여준다. `circuit_breaker_monitor`를 안 쓰는 구성(스모크 등)에서는
토픽 자체가 안 와서 "미사용/데이터 없음"으로 명시 — "정상(normal)"과 혼동되지 않게
마흐디 L18 원칙을 그대로 적용.

**결정 3 — 모드 기본값 REPLAY→LIVE**: `st.sidebar.radio(..., index=1)`로 변경.
`ui/data_source.py`가 이미 갖춘 "착각의 여지 없음" 방어(LIVE 배지 항상 신선도 노출,
연결실패 시 `st.error` 노출)가 REPLAY 기본값이었던 이유였는데, 그 방어 자체는 그대로
살아있으므로 기본값만 바꿔도 안전하다는 판단 — 평소 장중 모니터링이 압도적으로 많은
용도라 매번 수동 전환하는 게 번거로웠다는 사용자 피드백 반영.

**검증**: 신규 테스트(`test_pipeline.py` CB 상태 발행 1건, `test_app_smoke.py` 기본모드
재확인·REPLAY 전환·배지 렌더 3건) 포함 전체 829건 통과, ruff/pyright 클린. L1/G2 재시작
(15:05)으로 Command Center UI(`localhost:8511`) HTTP 200, ERROR/CRITICAL 0건 확인.

**알려진 갭**: `CircuitBreakerStatus`는 실시간 heartbeat만 있고 영속화가 없어 REPLAY로 과거
날짜를 봐도 그날 CB가 있었는지는 배지로 알 수 없다(halt 이력 DB 미착수와 같은 근본 원인).

## 2026-07-29 ([MW0601]) — Command Center "Connection error" 팝업 원인 조사·워치독 오프스케줄 방지

**요청**: 사용자가 브라우저에서 Streamlit 표준 팝업("Connection error - Is Streamlit still
running?")을 목격, 이력 조사와 근본원인 딥다이브를 요청.

**조사 결과 — 같은 날 두 가지 별개 사고를 확인**:
1. `logs/shutdown_watchdog.log`: `stop_l1_daily.bat`("Messiah-Shutdown", 원래 15:40
   전용 Task Scheduler 트리거)가 **13:08:03에 오프스케줄로 실행**돼 UI 프로세스 3개(PID
   16464/25108/26324, `messiah\ui\app.py --server.port 8511`)를 포함해 강제 종료함.
   같은 시각대에 `run_g2_paper_trading.py` 수동 검증 실행 후 "검증에 쓴 프로세스는 종료 후
   정리"(위 항목, 11:57 검증 기록)한 정황과 일치 — 명령줄 패턴 매칭 방식이라 어떤 경로로
   호출되든 UI까지 함께 죽는 구조. `logs/ui_20260729.log`에 13:10:09 재기동 확인, 그 사이
   브라우저 탭은 WebSocket이 끊겨 정확히 이 팝업을 봤을 것.
2. 같은 날 15:02:17·15:04:02에도 별도로 `ImportError: cannot import name
   'CircuitBreakerStatus' from 'messiah.core.messages'`로 앱이 크래시(`logs/ui_20260729.log`)
   — 커밋 `34c9b8c` 작업 중 `app.py`만 먼저 저장되고 `messages.py`는 아직 저장 전이던
   순간을 Streamlit 파일워처가 자동 리로드하며 발생. 15:05:40 재시작으로 해소, 커밋
   완료(15:12:39) 이후로는 재현 안 됨 — **코드 수정 불필요, 별도 클래스의 사고**.

**수정(1번 사고만 대상)**: `scripts/stop_l1_daily.bat`에 15:35 KST 시각 게이트 추가 —
`run_l1_daily.py`의 `REGULAR_SESSION_STOP`(=`HARD_SHUTDOWN_DEADLINE` 15:40의 5분 전)를
기준으로 그 이전에 실행되면 아무것도 죽이지 않고 스킵 로그만 남긴다(`MESSIAH_FORCE_SHUTDOWN=1`
로 강제 우회 가능 — `core/ui_launcher.py`의 `MESSIAH_SKIP_UI` 관례와 동일 패턴). UI를 매칭
대상에서 빼는 대신(그러면 UI를 영영 아무도 안 치움) 스크립트 자체를 "정말 15:40 안전망"으로
동작하게 만드는 방향 — 원래 파일 헤더 주석이 이미 그렇게 설명하고 있었지만 실제로는 시각
검사가 없어 그 설명과 실제 동작이 어긋나 있었다. Task Scheduler 트리거(15:40)는 변경 없음 —
게이트는 무엇이 이 스크립트를 호출하든(스케줄/수동/Task Scheduler "Run" 버튼) 방어하도록
스크립트 내부에 둠.

**검증**: 배치파일이라 pytest 대상 아님 — PowerShell로 게이트 로직 3분기(컷오프 이전 스킵 /
`MESSIAH_FORCE_SHUTDOWN=1` 강제통과 / 컷오프 이후 정상통과) 각각 직접 실행해 예상대로 동작
확인. 실제 `stop_l1_daily.bat`도 현재시각(15:52, 컷오프 이후) 기준 실행해 기존과 동일하게
"command-line match: no leftover process found" 로그·프로세스 영향 없음 확인(현재 매칭되는
MESSIAH 프로세스 자체가 없었음).

**남은 갭**: 2번 사고(파일 저장 타이밍에 따른 import 크래시)는 이번 스코프에서 다루지 않음 —
편집 중 파일을 개별 저장하는 개발 습관에 기인한 일회성 사고라 구조적 방지책이 필요한지는
다음에 비슷한 패턴이 재현되면 재검토.

---

## 2026-08-04 (3차 — 거래 대상 확정)

### [설계결정] 유니버스 확정 — 미니선물 + 먼쓰리/월위클리/목위클리

**증상**: 딥다이브 중 "MESSIAH의 거래 대상이 미니선물·미니옵션인가" 확인 요청을 받아 실측한
결과 **선물만 맞고 옵션은 어긋나 있었다.**

- 선물: `K200_MINI_FUT` → 상품종류 "B", A056xx, 틱 0.02 — 조회·주문·WS 전부 실측 완료. 맞음.
- 옵션: `universe`에 적힌 건 `K200_OPT`(**정규 월물**)였고 미니옵션이 아니었다.
- 미니옵션(D/E)은 `symbol_master`에 코드가 있고 `series="mini"`로 조회도 되지만, 2026-07-22
  실측에서 **상장 종목 0/0**이었다(같은 날 먼쓰리는 콜 390·풋 390). 물건이 없다.

**원인**: `K200_OPT`는 **소비자가 하나도 없는 죽은 토큰**이었다. `_PROBE_PRODUCT_TYPES`에
없어 `probe_front_month()`는 ValueError를 냈고, `OptionChainPoller`는 `series="regular"`
기본값이라 먼쓰리만 볼 수 있었으며, 그나마 **어떤 스크립트에도 결선돼 있지 않았다**(테스트
에서만 인스턴스화). 설정 파일에 한 줄 적혀 있으니 "옵션도 대상"처럼 보였을 뿐이다 —
`InvestorFlowPoller`(2026-07-27~08-04, 7개월 날림)와 **정확히 같은 실패 형태로 세 번째**다.

**결정**(사용자 확정): 선물은 미니선물, 옵션은 **먼쓰리·월위클리·목위클리 3종**. 미니옵션은
상장이 없어 제외(설계상 배제가 아니라 물건이 없어서 — 상장되면 토큰만 추가).

옵션 토큰을 **시리즈마다 하나씩** 쪼갰다:

    K200_MINI_FUT / K200_OPT_MONTHLY / K200_OPT_WEEKLY_MON / K200_OPT_WEEKLY_THU

**Why**: 먼쓰리/월위클리/목위클리는 만기 주기가 달라 **체인 크기·잔존만기·롤 시점·그릭스
민감도가 전부 다르다.** 하나의 `K200_OPT`로 묶으면 "옵션을 수집한다"고 적어 놓고 실제로는
셋 중 하나만 하거나 아무것도 안 하는 상태를 **설정만 봐서는 구분할 수 없다.** 실제로 그
상태였다. 토큰을 쪼개면 설정을 읽는 것만으로 무엇이 켜져 있는지 확정된다.

**How to apply**:
- 어휘의 정본은 `src/messiah/core/universe.py` 하나다. `configs/instance.yaml`,
  `probe_front_month(product=)`, `OptionChainPoller(series=)`가 전부 여기서 나온다 —
  어휘가 세 곳에 흩어져 있던 것이 죽은 토큰이 생긴 직접 원인이다.
- `InstanceConfig`에 `field_validator`를 붙여 **모르는 토큰은 기동 시점에 거부**한다.
  조용히 무시하면 "수집되는 줄 알았다"가 그대로 재현된다. 구 `K200_OPT`를 넣으면 무엇으로
  쪼개졌는지 알려주는 에러가 난다.
- `universe.py`는 series 이름을 `symbol_master`에서 import하지 않고 문자열로 복제한다
  (core가 broker를 import하면 의존 방향이 뒤집힘). 대신 `tests/test_universe.py`가 두
  목록의 일치를 검사한다 — `label_geometry`가 게이트 상수를 다루는 방식과 같은 규율.
- `OptionChainPoller`는 `series: str` → `Sequence[str]`. 시리즈마다 폴러를 띄우지 않는
  이유는 그러면 셋이 REST 유량을 서로 모른 채 나눠 쓰기 때문 — 한 폴러가 순차로 돈다.
  **시리즈 하나가 비어도 나머지는 계속 돈다**(위클리는 만기 주간에 따라 실제로 빌 수 있고,
  그게 먼쓰리 수집까지 멈추면 안 된다).

**검증**: `tests/test_universe.py` 10건 + 옵션체인 폴러 4건 신규(3종 순회·전 시리즈 조회·빈
시리즈 내성·빈 목록 거부), 전체 1208건 통과. `configs/instance.yaml` 실제 로드 확인
(옵션 시리즈 `['regular','weekly_mon','weekly_thu']`), `self_check.py` PASS.

**남은 갭(결선 미완 — 기한 2026-08-11)**: 유니버스 확정으로 "무엇을 수집할지"는 정해졌지만
`OptionChainPoller`는 **여전히 어떤 스크립트에도 안 붙어 있다.** `run_l1_daily.py` 결선은
같은 계좌 WS 다중연결 문제(capability_matrix.md)와 함께 풀어야 한다. 폴러만 만들고 결선을
미룬 것이 이 프로젝트의 반복 실패 패턴이므로 기한을 명시한다.

### [설계결정] 옵션체인 REST 폴링 결선 — "WS 문제"는 오진이었다

**증상**: `OptionChainPoller`는 2026-07-28에 만들어졌지만 **어떤 스크립트에도 결선되지 않은
채** 있었다. 미결선 사유로 `run_l1_daily.py` docstring과 capability_matrix가 일관되게
"같은 계좌 WS 연결 2개는 서로 끊긴다 — 단일 연결·다중 subscribe 재설계가 먼저"를 들고 있었다.

**원인 (오진)**: 그 WS 제약은 옵션 **틱(체결)** 구독에 걸리는 것이고, `OptionChainPoller`는
`get_asking_price()`/`get_quote()`를 쓰는 **순수 REST 폴러**라 WS 연결을 하나도 열지 않는다.
capability_matrix 자신도 다른 줄에서는 "WS 동시구독 문제와 **별개로** REST 폴링도 미구현"이라
정확히 구분해 적어 뒀는데, 결선 여부를 판단하는 자리에서는 둘이 뭉개져 있었다. **오진 하나가
착수를 몇 달 막고 있었다.**

**진짜 제약은 REST 유량이다** (2026-08-04 마스터파일 실측):

    먼쓰리 780다리 + 월위클리 242 + 목위클리 334 = 1,356다리
    모의투자 1건/초 → 1회 폴링에 **22.6분**

그래서 전량 폴링은 성립하지 않고 ATM±N 창이 필수다. 마흐디도 같은 곳에서 두 번 크게 잃었다
(2026-07-08 폴러별 페이서 분리 → 500 폭주로 **203분치 유실**, 2026-07-30 3북 균등 60초 →
수요 0.663건/초에서 백오프 2.61배에 부딪혀 **25사이클 유실**, 2026-08-03 위클리 2북을 같은
분에 몰아 **결손 41분 중 39분**).

**결정**:

1. **ATM±10** (42다리/시리즈). 마흐디의 ATM±2를 안 따랐다 — 그 값은 **WS 슬롯 한도(41)**
   때문이지 피처 요구가 아니었고(`main.py:82-85`), 문헌의 "ATM±1~2"도 **진입 대상** 규칙이지
   관측 규칙이 아니다(같은 문서가 "깊은 OTM은 진입 금지, **관측만**"으로 분리). 오히려 좁으면
   위험하다는 실증이 있다: 마흐디 `GAMMA_FLIP_MIN_LEGS=6` 하한에 ATM±2(10다리)가 바짝 붙어
   있었고 **감마플립 산출 실패가 조용해 버그가 넉 달간 안 보였다**.
2. **시리즈별 주기 차등 + 위상 분리** — 먼쓰리 300초@0s / 위클리 각 600초@100s·200s.
   균등이 아닌 근거는 유량(균등이면 내성 2.13배로 마흐디 실측 최대 백오프 2.61배 **미만**)과
   기능(먼슬리=GEX 주 입력, 위클리=핀 리스크 전용, `options_intel.py`) 둘 다다.
   → 총수요 **0.330건/초(용량의 33%)·백오프 내성 3.03배**.
3. **만기일 주기 교대** — 위클리 만기 요일엔 그 북과 먼쓰리의 주기를 맞바꾼다. 만기 당일 북은
   0DTE라 BS 감마가 정의 안 되는 대신 **핀 리스크가 거기서만** 나오는데, 마흐디는 위클리도
   WS로 체결을 받아 괜찮았지만 MESSIAH는 옵션 WS가 없다. 총수요 증가 0.
4. **원천을 `get_asking_price()` → `get_quote()`로 교체**. 실계좌 응답 대조 결과 전자는
   5단계 호가만 주고 후자가 IV·델타·감마·**미결제약정**·이론가·잔존일수 + KOSPI200 현물을
   준다. 현 스코프 OP Feature는 전부 후자 쪽이고 **호가를 쓰는 것이 하나도 없다**
   (`op_gex`의 감마×OI가 한 호출로 나온다). 다리당 2회 호출은 예산을 두 배로 먹는데 그 절반이
   몇 달간 소비처가 없다.
5. **`KISRestClient` 단일 인스턴스 공유**(`_RestCollection`) — `_RateLimiter`는 클라이언트마다
   생기므로 폴러별로 만들면 실효 호출률이 배수로 뛴다(마흐디 2026-07-08의 정확한 원인).

**Why**: 이 결정들의 공통 근거는 "마흐디가 이미 값을 치른 실측"이다. 다만 **그대로 베끼지
않았다** — ATM±2처럼 마흐디 고유 제약(WS 슬롯)에서 나온 값은 우리 제약(REST 유량)에서 다시
계산했고, 오히려 그 좁음이 만든 마흐디의 버그를 회피했다.

**How to apply**:
- 폴링 계획을 바꿀 땐 `tests/test_option_chain_wiring.py`의 내성 테스트를 먼저 볼 것 —
  균등 주기로 되돌리면 깨지도록 만들어 뒀다(마흐디 2026-07-30 재현 방지).
- 기준가를 못 구하면 **전량 폴백 금지**. 폴백이 곧 22.6분짜리 폭주다.
- 아카이버(구독)를 폴러(발행)보다 먼저 태울 것(`_rest_tasks()` 순서) — 파생 수급에서 이
  순서를 틀려 7개월을 날렸다.
- 기동 로그가 **수요·점유율·백오프 내성**을 매일 찍는다. 내성이 2.61배 밑이면 경고가 붙는다 —
  설정 실수를 몇 달 뒤 데이터 유실로 발견하는 대신 1일차에 보라는 뜻이다.

**검증**: 단위 테스트 47건 신규(폴러 15 · 아카이버 12 · 기준가 7 · 결선 13), 전체 1246건 통과.
**실계좌 end-to-end 실측** — 기준가(미니선물 998.08) → ATM±1 6다리 선택(델타 콜 0.42~0.52 /
풋 −0.49로 ATM 정확히 중심) → 실호출 6건 → 버스 → 아카이버 → parquet 6행×12컬럼, `_RateLimiter`
1초 페이싱까지 타임스탬프로 확인. self_check PASS.

**남은 갭(라이브 미검증, 기한 2026-08-11)**: 장중 전량 사이클(42다리)을 실제로 돌려본 적은
없다 — 위 실측은 6다리다. 2026-08-05 기동분이 첫 실운영이고, 그날 로그의 `SchedulerTickMissed`
건수와 `data/option_chain/` 행수로 예산 계산(126초/300초 격자)이 실제와 맞는지 확인할 것.

## 2026-08-04 ([MW0601]) — 피처 재설계 F0(인프라·관문) + F2(MS 수집 개시)

### 증상 / 조사

사용자 요청: "현 121개가 전부 가격/거래량 파생이다. MS·FL·OP·RG가 전부 미구현이니 조사하고
구현계획을 세워라." 조사 결과 전제가 절반만 맞았다.

| 카테고리 | 설계(Ver 1.4) | 코드 | **모델 도달** |
|---|---|---|---|
| PX | 30 | 30 | 30 |
| VL | 16 | 14 | 14 |
| FL | 18 | **9** | **0** |
| MS·OP·RG·EV | 88 | 0 | 0 |

**FL은 미구현이 아니라 미결선이었다.** `fl_core.py` 9개가 완성돼 있고 엔진이
`if self._flow is not None`으로 조건부 계산하는데, `FeatureEngine` 생성처 7곳(trainer·
harness·run_l1_daily·replay·smoke 3종) **전부**가 `flow_history`를 안 넘겼다. 전날 커밋
`1dea245`가 "wired end to end"라고 적었지만 결선된 것은 A/B 측정 경로뿐이었다. 이 프로젝트가
`InvestorFlowPoller`(7개월)·`OptionChainPoller`(수개월)로 겪은 것과 같은 패턴의 네 번째다.

그리고 **EV(14개)가 사용자 목록에서 빠져 있었다** — 전부 시각/달력 함수라 163거래일 전체에
소급 계산이 가능한 유일한 카테고리인데, 비용 대비 효과가 가장 큰 자리다.

### 결정 1 — 백필 가능성이 우선순위를 지배한다

병목은 피처 개수가 아니라 **학습 가능한 이력 길이**다(G1 창 = 211캘린더일). 새 피처를 붙이면
학습 창이 그 피처의 시작일부터 다시 시작하므로, MS/OP를 지금 학습에 넣으면 데이터가
163거래일 → 0일로 리셋된다. 그래서 **수집 개시와 피처 투입을 분리**했다.

- A트랙(소급 가능, 즉시 A/B): EV · RG 매크로 일봉 · FL 결선 · VL 잔여 2개
- B트랙(오늘부터 누적, 피처는 3개월 뒤): **MS · OP · RG 베이시스 · FL 파생 장중**

사용자 확정: F2(MS 수집) 오늘 착수 · F0-3(품질 관문) 선행 · OP Greeks는 Black-76 유지.

### 결정 2 — 관문을 피처보다 먼저 만든다 (F0-3)

Ver 1.4 §3의 IC·중복·생존 관문은 처음부터 요구됐으나 **코드로 존재한 적이 없었다**(리포 전역
grep 0건). 그동안 121개 전부가 검정 없이 학습에 들어갔고 결과가 두 번 드러났다 —
`px_ema_cross_60` 프로덕션 상시 NaN(08-04), 탐색공간이 데이터를 앞질러 75그루가 전부 2-leaf
그루터기(08-03). 121 → 250으로 늘리면서 관문이 없으면 과적합만 증폭된다.

**Why**: 관문을 나중에 만들면, 성능이 안 오를 때 원인이 새 피처인지 기존 잡음인지 구분할
방법이 영영 없어진다. 기준선을 먼저 재야 한다.

**How to apply**: `features/gate.py`. 설계에서 셋을 강제했다 —
① **Spearman(순위상관)**: 레이블이 {-1,0,1} 이산이고 데이터가 팻테일이라 피어슨의 선형성
가정이 성립하지 않는다.
② **겹침 보정 강제**(`label_overlap_bars`): Triple Barrier 3봉 겹침을 무시하면 t가 √3배
부풀어 잡음이 전부 유의해 보인다 — 2026-08-04에 실제로 한 번 밟은 함정이라 코드가 barrier
표에서 직접 읽게 했다(사람이 옮겨 적지 않는다).
③ **크기·유의성·효과크기 3중 요구**: 같은 세션에 `ScoreCalibration`이 165표본 +4.2pp(0.8σ)를
"유의미"로 판정한 사고가 있었다. 하나만 보면 반드시 잡음을 통과시킨다.
④ **판정 불가와 탈락을 구분**(`SKIPPED`): 생존 검정은 창이 부족하면 **아무도 탈락시키지
않는다**. G1 창이 하나뿐인 지금 3창을 요구하면 관문이 데이터 부족을 피처 결함으로 오역한다.

### 실측 — 관문 첫 실행에서 결함 하나를 잡았다

통과율 5m 7/121 · 15m 12/121 · 30m 17/121. 그리고 **`px_macd_h_5`가 프로덕션에서 항상 정확히
0**이었다: `window=5` → `5//3=1` → `_ema_series(x, 1)`은 k=2/(1+1)=1이라 EMA가 항등이 되고
히스토그램이 상수 0이 된다.

`px_ema_cross_60`과 같은 종류인데 **검출 수단이 달랐다** — 그건 NaN이라 `nan_ratio`에 흔적이
남았고(그래서 결국 발견됐고), 이건 값을 내므로 무결성 리포트에 **아무 흔적도 없었다**.
검출 수단이 하나뿐이면 그 수단이 못 보는 결함은 영원히 안 보인다. `_MIN_SIGNAL_PERIOD=2`로
고정, 회귀 테스트 추가.

### 결정 3 — 벡터 모양은 이름의 함수여야 한다 (F0-1)

`if self._flow is not None`은 카테고리가 5개면 2^5 조합이 되고 `feature_set` 문자열로는
어느 조합인지 알 수 없다. 금지 6계명("피처 불일치 침묵 금지")이 그 구조에서는 못 버틴다.

`features/spec.py`가 이름 → 카테고리 → 정확한 피처 이름을 단일 정의한다. 두 가지가 설계의
핵심이다: ① 이름 목록을 **손으로 안 적는다**(계산기 모듈을 참조하되 **호출 시점에** 읽는다 —
import 시점 스냅샷은 계산기를 갈아끼우는 테스트와 갈린다) ② 스펙이 요구하는 사이드카가
없으면 **생성 시점 ValueError**. 반대(스펙이 안 쓰는 사이드카 주입)도 거부한다 — 둘 다
"붙인 줄 알았는데 안 붙었다"의 서로 다른 얼굴이다.

`features/sidecar.py`는 `FlowHistory`가 이미 세운 "요청일보다 **엄격히 이전**만 준다" 계약에
이름을 붙인 것이다(`flow_as_of` → `as_of` 통일). OP·RG 사이드카가 이 규율을 각자 다시
발명하면 그중 하나가 안 지켰을 때 그 카테고리만 미래를 본다.

### 결정 4 — 호가는 이미 도착하고 있었다 (F2)

MS 30개가 "호가 WS 미구독"으로 미착수였는데, **H0IFCNT0 체결 프레임 50필드 중 idx34~37이
매도호가1/매수호가1/잔량**이었다(2026-07-22 캡처로 이미 교차검증된 위치). 파서가 4필드만
읽고 46개를 버렸을 뿐이다. 데이터가 없던 게 아니라 스키마가 좁았다.

**How to apply**: 확정 위치만 이름을 붙이고(호가 4개) **나머지는 이름을 안 붙인다.** 미결제
약정·이론가·총잔량·체결강도로 보이는 필드가 더 있지만 값의 모양으로 추정만 했고 공식 문서
대조를 안 했다 — 추정으로 스키마를 정하는 것이 정확히 마흐디 L16이다. 대신 프레임 전체를
`Tick.raw_fields`로 실어 `data/tick_archiver.py`가 **위치 이름**(`f00`~`f49`)으로 보존한다.
컬럼 이름에 추정한 의미를 박으면 그 추정이 틀렸을 때 컬럼 이름이 거짓말을 한다.

순서가 요점이다: **매핑 확정은 다음 주에 해도 되지만 그때 쓸 데이터는 오늘 안 받으면 없다.**
틱은 봉과 달리 백필 경로가 아예 없다(KIS 분봉 API는 OHLCV만 준다).

`side_hint`도 이제 채운다(quote rule). **알려진 근사**: Lee-Ready는 체결 직전 호가를 쓰지만
여기 있는 것은 같은 프레임의 동시 스냅샷이라 일부 방향이 반대로 잡힌다 — 0(불명)으로 두는
것보다 낫다는 판단이고, 편향이 한쪽으로 쏠리는지는 F6 전에 **측정할 대상**이다.

### 검증

- 테스트 1250 → **1297건 전부 통과**, ruff 클린.
- 관문 실 아카이브 실행(63,508봉 · 3 Horizon) — 위 실측표.
- 틱 아카이버 부하: 5만틱 7.3초 · **하루 0.3MB**(원시필드가 반복이라 압축률이 높다).
- 실캡처 WS 프레임으로 호가 파싱 확인 — ask1 54015 / bid1 54002 / 스프레드 13틱
  (0.26pt ÷ 0.02, 틱 크기와 정합) / 체결가=최우선매도호가 → `side_hint=+1`.
- **미검증(기한 2026-08-12)**: 실운영 첫날(2026-08-05 08:35) 틱 적재. `TickArchiveSummary`
  행수와 `data/ticks/` 실제 크기를 부하 추정(5~10만행·0.3MB)과 대조할 것.

## 2026-08-04 2차 ([MW0601]) — F1: EV(이벤트·시간·만기) 카테고리

### 증상

121개가 전부 OHLCV 파생이라 모델이 **"장 마감 10분 전"과 "개장 직후"를 구분할 수단이 하나도
없다** — 같은 가격 패턴이면 같은 판단을 낸다. 유동성·변동성·강제청산 압력이 전혀 다른 두
순간인데도.

EV를 F1(첫 순번)으로 고른 이유는 **이력 전체에 소급 계산되는 유일한 카테고리**라서다. 전부
시각·달력 함수라 과거 봉의 타임스탬프만 있으면 값이 나온다 — MS/OP처럼 3개월을 기다릴 필요
없이 지금 163거래일에 붙여 즉시 A/B가 된다.

### 결정 1 — 만기 규칙 사본이 세 벌이 될 뻔했다

구현 전 조사에서 발견: 정규월물 만기 규칙이 이미 **두 벌** 있었다.

    data/backfill.monthly_expiry()      둘째 목요일 + **휴장이면 직전 거래일**
                                        (2026-08-04에 A05601~A05607 7개 월물의 실제
                                         마지막 거래일과 전부 일치 확인 — 검증된 쪽)
    EventCalendar.is_expiry_day()       `8 <= d.day <= 14` 인라인 판정, **휴장 보정 없음**

후자는 둘째 목요일이 휴장이라 만기가 수요일로 당겨진 경우를 못 잡는다. EV의 `ev_dte_fut`가
세 번째 사본을 만들기 직전이었다.

**Why**: Ver 1.4 §0이 정확히 이걸 경고한다 — "같은 재료를 두 곳에서 다르게 손질하는 순간
주방은 오염된다". 사본이 셋이면 결과가 갈렸을 때 어느 것이 맞는지 알 방법이 없다.

**How to apply**: 정본을 `core/event_calendar.py`로 옮기고(달력 모듈이 자연스러운 자리)
`backfill.monthly_expiry`는 재수출로 남겼다(기존 임포트 경로 유지). `is_expiry_day()`도
이제 정본 규칙(`is_monthly_expiry()`)을 쓴다 — 휴장 보정이 공짜로 따라왔다.

### 결정 2 — 사이드카에 두 종류가 있다 (관측 vs 참조)

F0에서 만든 `DailySidecar`는 "요청일보다 **엄격히 이전**만 준다"가 핵심 계약이다. 그런데
`EventCalendar`에 그걸 강요하면 `ev_dte_fut`(만기까지 잔여 거래일)가 **아예 계산 불가**가
된다 — 미래 날짜를 봐야 하는 피처이기 때문이다.

**Why**: 둘은 성질이 다르다. 일별 순매수는 **관측** 데이터라 그날 값이 장 마감 후에야
확정되지만, 휴장일·만기일은 **참조** 데이터다. 내일이 휴장일이라는 걸 오늘 아는 것은 미래
참조가 아니라 몇 달 전부터 공표된 사실이다.

이 구분을 안 하면 둘 중 하나가 반드시 틀린다: 참조에 "엄격히 이전"을 강요하면 EV가 죽고,
관측에 자유 조회를 허용하면 백테스트 성과가 극적으로 좋아진다(= 미래를 본다).

**How to apply**: `features/sidecar.py`에 `ReferenceSidecar`를 추가하고 두 종류를 모듈
docstring에 명시. 조립처가 넷(trainer·harness·run_l1_daily·run_feature_gate)이라 각자
만들면 네 벌이 갈리므로 `sidecar.build(spec)` 하나로 모았다.

### 결정 3 — 알려진 중복을 내 판단으로 지우지 않는다

구현 전에 두 쌍이 중복임을 알고 있었다:
- `ev_open_elapsed` + `ev_close_remain` — 세션 길이가 상수라 합이 정확히 1
- `ev_dte_fut` + `ev_dte_opt_m` — KRX 규칙상 둘 다 둘째 목요일 만기

하나씩 지우고 싶었지만 **둘 다 구현했다.**

**Why**: "내가 보기에 중복"과 "측정된 중복"은 다른 근거다. KRX가 미니선물 만기 주기를
바꾸면 전자만 조용히 틀린다. 그리고 이번 주에 만든 관문 ②(중복 검정)가 정확히 이 일을
하려고 있는 것이다 — 자동 절차가 있는데 손으로 대신하면 그 절차의 신뢰도를 못 쌓는다.

**검증**: 관문이 실제로 잡았다 — 30m에서 `ev_open_elapsed`·`ev_tod_sin`이
`ev_close_remain`과 |ρ|>0.9로 탈락. `ev_dte_fut`/`ev_dte_opt_m`은 전 Horizon IC가 소수점
4자리까지 동일해 예측한 항등이 확인됐다(다만 둘 다 ①에서 먼저 탈락해 ②까지 안 갔다).

### 결정 4 — 상수는 재고, 못 재면 못 잰다고 적는다

- **`ev_lunch_flag` 창은 실측했다.** Ver 1.4는 "유동성 저하 시간대"라고만 적고 시각을
  안 정했다. 우리 아카이브(163거래일)로 분당 거래량 프로파일을 재니 정규장 평균 345계약
  대비 11:00 1.01x → 11:30 0.84x → **12:20 0.65x(최저)** → 14:00 0.95x. 창 = 11:30~14:00.
  (15:30 이후도 0.48x로 낮지만 종가단일가 전환이라 성격이 다르다 — 포함하지 않았다.)
- **`ROLLOVER_TRADING_DAYS`는 못 쟀다.** 백필이 날짜마다 근월물만 저장해 두 월물이 겹치는
  날이 만기일 하루뿐이다. 그 하루의 실측(차월물 비중 36~64%, 7개 월물)은 "만기일엔 이미
  이전이 끝나 있다"만 알려주고 언제 시작되는지는 안 보인다. 5거래일로 두되 **측정 가능한
  갭**임을 명시했다 — KIS 분봉 API가 만기물도 주므로 만기 직전 10거래일치 차월물을 추가
  백필하면 곡선이 나온다(절차는 NEXT_TODO).

### 결정 5 — 시각은 세션이 아니라 24시간 주기로 인코딩

`ev_tod_sin/cos`를 세션 길이(6h35m) 한 바퀴로 잡으면 개장(위상 0)과 마감(위상 2π)이 **같은
점으로 겹쳐** 둘을 구분할 수 없게 된다 — 이 피처를 만든 목적 자체가 무효가 된다. 자정 기준
24시간 주기를 쓰면 장중 시각이 서로 다른 점에 놓인다.

그리고 시각의 기준은 `bar_open_kst`가 아니라 **`bar_confirm_time`**이다. 벡터가 존재하게
되는 순간이 봉 종료 시점이고 판단도 그때 내려진다(완성봉 규율) — 30분봉이면 둘이 30분
차이라 `ev_lunch_flag` 같은 경계 피처가 한 봉씩 밀린다.

### 실측 결과 — 얻은 것은 시각 축 하나다

| Horizon | 121개 active | 137개 active | 증가 |
|---|---|---|---|
| 5m | 7 | 7 | **0** |
| 15m | 12 | 13 | +1 |
| 30m | 17 | 19 | +2 |

통과한 EV 피처:

| Horizon | 피처 | IC | t |
|---|---|---|---|
| 5m | `ev_tod_cos` | +0.0214 | +1.39 (미달) |
| 15m | `ev_tod_cos` | +0.0665 | **+2.50** |
| 30m | `ev_tod_cos` | +0.0844 | **+2.24** |
| 30m | `ev_close_remain` | −0.0838 | **−2.22** |

Horizon이 길수록 IC가 커진다(0.021 → 0.067 → 0.084) — 긴 Horizon일수록 일중 계절성에 더
노출된다는 해석과 방향이 맞는다. 나머지 EV **전부**(요일 5개·만기 D-day 3종·만기플래그·
롤오버·연휴인접·점심)는 이 표본에서 측정 가능한 신호가 없다.

**정직하게 말하면 이건 소폭이다.** 137개 중 EV가 기여한 것은 15m 1개·30m 2개이고, t값도
2.2~2.5로 임계 바로 위다(30m 겹침 보정 유효표본 ~700). 8개월 단일 상승장 한 국면의 값이고
생존 검정(③)은 여전히 창 부족으로 실행 자체가 안 됐다. "장 마감 임박을 모델이 인지하게
됐다"는 사실이 확인된 것이고, 그것이 손익으로 이어지는지는 별개 문제다.

### 검증

- 테스트 1297 → **1329건 전부 통과**(EV 32건 신규), ruff 클린.
- EV 테스트는 합성 날짜가 아니라 **실제 2026년 달력**을 쓴다(2026-08-13 8월 만기,
  2026-09-10 동시만기) — 합성 날짜를 쓰면 "둘째 목요일" 규칙을 테스트가 다시 계산하게 되고
  구현과 같은 실수를 공유한다. D-day 값(6·19·4·2)은 전부 손으로 셌다.
- 관문 실행: `logs/feature_gate_ev.json`.

## 2026-08-04 3차 ([MW0601]) — 마흐디 만기 관리·운영 체계 조사 및 이식

### 조사 범위

선행 프로젝트 마흐디(`C:\Users\82108\PycharmProjects\options\`)의 만기 관리와 운영 체계.
읽은 것: `mahdi/data/symbol_master.py` · `mahdi/main.py` · `mahdi/features/options_intel.py` ·
`mahdi/ops/hypotheses.py` · `mahdi/dashboard/panels/expiry_liquidity_panel.py` ·
`docs/Dev_md/RESEARCH_EXPIRY_SELECTION_v1.md` · `docs/Dev_md/MAHDI_ULTIMATE_SYSTEM_v6.md` ·
`docs/동작점검/README.md`·`hypotheses.yaml`.

### 발견 1 — F1이 틀렸다. 마흐디가 이미 실측으로 답을 갖고 있었다

F1(2026-08-04 2차)에서 `ev_dte_opt_w`의 미해결 항목으로 *"정규월물 만기 목요일에 목위클리가
별도 상장되는지 미확인"*을 NEXT_TODO에 적어 뒀다. 마흐디는 그걸 **2026-07-10에 실측했다**:

> KRX는 먼슬리 만기 주의 목요일에 위클리(목)을 별도 상장하지 않는다 — 먼슬리가 그 역할을
> 대신한다. (`symbol_master.py` L/M 상수 주석 + `expiry_liquidity_panel._is_monthly_expiry_week`)

마흐디도 이 사실을 몰라 한동안 대시보드의 위클리(목) 행이 비는 것을 **데이터 누락으로
오인**했고, 그래서 패널에 안내 문구까지 넣었다.

**실측 확인**: MESSIAH의 `ev_dte_opt_w`는 2026-08-13(8월 먼슬리 만기, 목)에 **0**을 냈다 —
"오늘 위클리도 만기"라는 없는 사실을 주장한 것이다. 연 12회 발생한다.

**How to apply**: `EventCalendar.has_thursday_weekly()` 신설(ISO 주 기준 — 마흐디 패널과 같은
판정). `next_weekly_expiry()`가 먼슬리 만기 주 목요일 후보를 건너뛴다.

### 발견 2 — 휴장 보정에서 관례를 두 개 만들고 있었다

수정 중 드러난 것: `next_weekly_expiry()`가 휴장 만기일을 **건너뛰고** 있었는데,
`monthly_expiry()`는 **직전 거래일로 당긴다**. 같은 모듈에서 같은 종류의 질문에 서로 다른
관례를 쓰고 있었다.

**Why**: 직전 거래일 관례는 KRX 실측으로 검증된 유일한 것이다(2026-08-04, A05601~A05607
7개 월물의 실제 마지막 거래일과 전부 일치). 검증된 관례가 하나 있는데 위클리에 검증 안 된
관례를 새로 만드는 것은 근거 없는 분기다.

**검증**: 2026-08-11(화) 기준 다음 위클리가 08-20 → **08-14**로 바뀐다(08-17 광복절
대체휴일이라 월위클리가 08-14로 당겨짐). `ev_dte_opt_w` 6 → 3.

### 발견 3 — 만기의 권위 있는 출처는 요일 규칙이 아니다

마흐디는 `get_quote()` 응답의 **`futs_last_tr_date`**(그 종목의 실제 최종거래일)를 쓴다
(`main.py`). 요일 규칙은 근사일 뿐이다.

MESSIAH의 `OptionQuoteSnapshot.raw`가 **이미 그 필드를 보존한다**(docstring에 명시돼 있다).
즉 2026-08-05부터 쌓이는 옵션체인 데이터에 실제 만기일이 매 폴링 실려 온다 — 요일 규칙
전체를 실측으로 대체할 경로가 이미 열려 있다. NEXT_TODO에 절차를 적었다.

### 발견 4 — 만기별 GEX 분리 (F5에 직접 적용될 설계 제약)

`options_intel.legs_by_expiry()`: **3개 북(먼슬리·위클리월·위클리목)을 합산하면 만기별
정보가 서로를 덮는다.** 특히 만기 당일 북은 잔존만기 0이라 Black-Scholes 감마가 정의되지
않는 반면, v6 §A3의 **만기 Pinning은 바로 그 북에서만** 나온다.

용도 분리: 먼슬리(최근월) → GEX/감마플립/감마월의 주 입력 / 위클리 → 핀 리스크 전용
(만기일 ATM 집중도). 2026-08-03에 마흐디가 세 북을 합산해 하루를 날린 뒤 나온 결론이다.

→ **F5(OP 피처)에서 `op_gex`를 시리즈 합산으로 계산하면 같은 실수를 반복한다.** MESSIAH
옵션체인은 이미 시리즈별로 적재되므로(`data/option_chain/{series}/`) 분리는 가능하다.

### 발견 5 — 운영 체계: "예측치를 못 적겠으면 그 fix는 근거가 부족한 것이다"

마흐디 `docs/동작점검/README.md`의 규약:

> **fix를 구현하는 세션은 그 자리에서 `hypotheses.yaml`에 예측치를 적는다.**
> 다음 거래일 리포트가 자동으로 대조해 §0에 낸다.
> **예측치를 못 적겠으면 그 fix는 아직 근거가 부족한 것이다.**

그리고 2026-08-03에 그 규약의 빈틈을 발견해 한 줄을 더 붙였다:

> **fix가 어떤 값을 "쓰는" 경우, 그 값이 실제로 생산되고 있다는 것을 먼저 예측치로 적는다.**
> "X를 개선한다"의 예측치는 "X가 좋아진다"가 아니라 **"X가 존재한다"** 부터다.

그날 예측 13개 중 12개가 자동 대조로 확인됐지만 **그 어떤 가설도 `find_gamma_flip()`이 전
이력에서 한 번도 값을 낸 적이 없다는 사실을 잡지 못했다** — 아무도 "계산되는가"를 예측치로
적지 않았기 때문이다(`iv=0` 레그 하나가 41개 그리드를 NaN으로 오염). 앙상블 멤버
`options_flow`가 **넉 달간 영구 미가용**이었고, 넉 달 동안 "개선"해 온 대상이 없었다.

**MESSIAH도 같은 형태를 이미 두 번 겪었다**: `px_ema_cross_60`(NaN이라 `nan_ratio`에 흔적은
남았다)과 `px_macd_h_5`(**값을 내므로 아무 흔적도 없었다** — 오늘 피처 관문이 처음 발견).

#### 대비 — MESSIAH가 이미 더 나은 부분과, 없던 부분

| | 마흐디 `hypotheses.yaml` | MESSIAH `pending_verifications.yaml` |
|---|---|---|
| 예측 등록 | ✅ | ✅ |
| 자동 채점 | ✅ (expect 3문법) | ✅ (max/min + 연속 N거래일) |
| **재발 판정** | ✗ (사람이 확정) | ✅ 위반 1회 = 즉시 `재발` |
| 기한 초과 | ✗ | ✅ |
| **"존재한다" 지표** | ✅ `signal_reach` | ✗ → **오늘 추가** |
| 자동/사람 문서 분리 규약 | ✅ | ✗ (미이식) |

**How to apply**(오늘 이식한 것):
- `IntegrityReport.tick_rows` 신설 + `min_tick_rows` 하한 임계 + breach. 이 등록부에서
  "일어나면 안 되는 일"이 아니라 **"일어나야 하는 일"을 재는 첫 지표**다.
- `pending_verifications.yaml` 헤더에 "존재한다부터 적는다" 규약을 근거와 함께 명문화.
- `tick-collection-live` 항목 등록(min 1000행 · 3거래일 · 기한 2026-08-12).

틱이 이 지표를 가장 필요로 한다: 봉과 **수집 경로가 달라** 결선이 조용히 끊겨도 다른 지표는
전부 정상으로 보이고, 백필 경로가 없어 그 하루가 영구히 빈다.

### 참조만 하고 이식하지 않은 것 (근거 기록)

- **만기 북 2계층 선발 체계**(`RESEARCH_EXPIRY_SELECTION_v1.md` §2.2): 선발 단위를 개별
  종목이 아니라 **만기 북**으로 정의하고, 행사가는 ATM 롤링. 장전 복합 유동성 점수(전일
  거래량·OI·ATM±2 %스프레드 중앙값·깊이·잔존만기)로 주 거래 북 선정, 차점은 관측 북.
  → MESSIAH는 아직 옵션을 **거래하지 않는다**(수집만). Options AI 단계에서 필요.
- **유동성 강등 트리거**(§2.3 예외 1): 주 북 ATM %스프레드가 20거래일 동시간대 중앙값의
  2배를 M분 연속 초과하면 관측 북으로 강등, 차점 승격. 핑퐁 방지로 하루 1회 제한.
  → 같은 이유로 보류. **%스프레드(Cao-Wei)를 쓰고 달러 스프레드를 금지**하는 근거는 지금
  기록해 둔다(옵션은 만기·머니니스에 따라 달러 스프레드가 기계적으로 달라진다).
- **0DTE 플레이북**(v6 §11.4): 만기 당일 전용 파라미터(사이즈 상한 50%·시간손절 절반·
  14:00 이후 Charm 우선), 구조 거래만 허용·네이키드 방향성 매수 금지.
  → MESSIAH `exit_stack`에 해당 개념 없음. 옵션 실거래 착수 시 재검토.

### 검증

- 테스트 1331 → **1334건 전부 통과**(EV 만기 3건 + 틱 지표 3건 신규 − 기존 1건 대체),
  ruff 클린.
- 등록부 파싱 확인: `tick-collection-live metric=tick_rows min=1000.0 days=3 기한=2026-08-12`.

## 2026-08-04 4차 ([MW0601]) — 예측 대상 축 실측: 방향 vs 변동성

### 왜 이걸 쟀나

2026-08-04 메모의 세 질문 중 2·3번이 미답 상태였다("적중률 50%의 벽이 방향에만 있는
문제인가" / "예측 대상 전환 — 방향 대신 변동성/레인지"). F0-3 관문이 그 답을 잴 수 있는
도구였고, 방향 축 결과가 이미 강한 힌트를 주고 있었다.

**방향 축 관문 실측(2026-08-04)**: 부호 있는 방향 피처 39개가 3 Horizon 전부에서 사실상
전멸했다(117 판정 중 통과 1건 — 그마저 `px_adx`로 **무부호 추세 강도**다). 통과 목록 39개를
전수 확인하면 전부 무부호 크기(`vl_*`·`px_bb_width`·`px_max_ret`·`px_runup`·`px_round_dist`)
또는 무부호 강도(`px_adx`·`px_trend_r2`) 또는 시각(`ev_tod_cos`·`ev_close_remain`)이다.

### 방법 — 비교 가능성을 먼저 확보했다

같은 137개 피처·같은 데이터·같은 겹침 보정으로 **레이블만 교체**했다.

- 타깃: `models/labeling.forward_realized_volatility()` — 다음 N봉 실현변동성
  `sqrt(Σ r²)`. **N을 그 Horizon의 시간배리어(3봉)와 같게** 맞췄다. 예측 구간이 다르면
  두 축의 IC를 견줄 수 없다.
- **동순위 통제**: 연속 RV를 3분위로 이산화했다. 방향 레이블은 값이 셋뿐이라 동순위가
  많고, Spearman은 동순위가 많을수록 달성 가능한 |ρ| 상한이 낮아진다 — 연속 타깃과 그대로
  견주면 변동성 쪽 IC가 **기계적으로** 커 보인다. 계급 수를 맞춘 통제군이 있어야
  "정말 커졌는가"를 말할 수 있다(`run_feature_gate.py::_volatility_target`).
- 행렬은 방향 경로와 **같은 함수**(`HorizonExpert.feature_row`)·같은 열 순서로 조립.

### 결과

| Horizon | 방향 축 통과 | 변동성 축 통과 | 최대 \|IC\| (방향 → 변동성) |
|---|---|---|---|
| 5m | 7 / 137 | **78 / 137** | 0.040 → **0.674** |
| 15m | 13 / 137 | **69 / 137** | 0.083 → **0.571** |
| 30m | 19 / 137 | **66 / 137** | 0.124 → **0.480** |

t값은 겹침 3봉 보정 후에도 5m에서 2.6 → **59.4**다.

계열별 |IC| 중앙값(5m): 방향 축 0.004/0.026/0.011/0.003 → 변동성 축 **0.126/0.469/0.146/0.089**
(방향성/변동성크기/위치기타/시간달력). **방향성 피처조차 변동성을 예측할 때는 39개 중 22개가
통과한다** — 같은 입력이 축을 바꾸자 살아난다.

### 해석 — 그리고 이 숫자를 과대해석하면 안 되는 이유

측정된 사실: **지금 입력은 "어디로 가는가"에 대해 사실상 아무 정보도 없고, "얼마나
움직이는가"에 대해서는 많은 정보를 갖고 있다.** 이건 임계값·하이퍼파라미터·Horizon 수의
문제가 아니라는 것을 세 번째로, 그리고 이번엔 반대 방향에서 확인한 것이다.

**그러나 IC 0.67을 알파로 읽으면 안 된다.** 변동성 군집(volatility clustering)은 금융
시계열의 가장 강건한 정형화된 사실이고, `vl_atr_5`가 다음 3봉 RV를 예측하는 것의 상당 부분은
**변동성의 지속성** 그 자체다. 즉 "예측 가능하다"와 "우위가 있다"는 다르다 — 시장이 이미
그 지속성을 옵션 가격에 반영하고 있다면 남는 것이 없다.

그래서 다음 검정이 반드시 필요하다(NEXT_TODO):
1. **단순 기준선 대비 증분** — 지연 RV 하나(또는 HAR-RV, `strategy/options/vol_forecast.py`에
   이미 있다)를 기준선으로 두고, 137개가 그것을 **넘어서는지**. 안 넘으면 78개 통과는
   전부 같은 것의 프록시다.
2. **수익화 경로** — 변동성 예측이 돈이 되려면 그걸 파는 수단이 있어야 한다(옵션 스프레드,
   레인지 매매). 미니선물 방향 매매로는 직접 환금되지 않는다.
3. **IV 대비** — 실현변동성 예측이 시장의 **내재변동성**보다 나은가. 이게 진짜 질문이고,
   옵션체인이 2026-08-05부터 쌓이므로 3개월 뒤 측정 가능해진다(F5).

### 검증

- 테스트 1334 → **1339건 전부 통과**(변동성 레이블 known-value 5건 신규), ruff 클린.
- 산출물: `logs/feature_gate_vol.json`(변동성) · `logs/feature_gate_ev.json`(방향).
  JSON에 `label`/`tie_matched`를 기록한다 — 두 실행을 나중에 구분 못 하면 비교가 무의미하다.

## 2026-08-04 5차 ([MW0601]) — ① 기준선 대비 증분: 변동성 축의 IC는 지속성이 아니다

### 왜 쟀나

4차에서 변동성 축 IC 0.67(5m)이 나왔지만 **변동성 군집**은 금융 시계열의 가장 강건한
정형화된 사실이라, 그 값이 피처의 정보인지 **지속성 그 자체**인지 주변상관만으로는 구분이
안 된다. 축 전환을 확정하기 전에 반드시 넘어야 할 검정이었다.

### 방법 — 순위 부분상관, 모델 적합 없이

`gate.partial_spearman()`: 통제변수의 순위로 피처와 타깃을 **둘 다 잔차화**한 뒤 상관.

**HAR-RV를 적합해 잔차를 쓰지 않았다.** 전 구간 적합은 in-sample 과적합이 잔차에 섞여,
피처가 "과적합이 못 맞힌 부분"을 맞히는지를 재게 된다. 순위 잔차화는 적합 없이 같은 통제를 한다.

통제 3단계로 나눠 쟀다:

| 단계 | 통제변수 | 의도 |
|---|---|---|
| 통제 없음 | — | 주변상관(4차 결과) |
| 직전RV | 직전 N봉 종가기반 RV | "직전에도 컸다"를 뺀다 |
| **RV+GK** | + `vl_gk_5`·`vl_gk_20` | **"OHLC로 현재 변동성을 더 잘 잰다"까지 뺀다** |

세 번째가 결정적이다. 종가 기반 RV는 **비효율적 추정량**이고(Parkinson 1980 이래 알려진
사실 — 레인지 기반이 훨씬 효율적), 그것만 통제하면 `vl_gk`·`vl_yz`·`vl_park`가 보이는
증분이 새 정보가 아니라 **추정 효율**일 뿐이다. 그 구분을 안 하면 "피처가 기준선을 넘는다"는
결론이 사실은 "OHLC가 종가보다 낫다"는 교과서 사실이 된다.

### 테스트가 잡은 진짜 버그 2개

1. **잔차가 수치적으로 0일 때 부분상관이 1.0을 냈다.** 피처가 기준선의 복사본이면 lstsq
   잔차가 수치오차만 남는데, 그 잡음 벡터 둘을 상관내면 1.0이다 — 즉 **"기준선의 복사본"이
   "완벽한 증분"으로 보고된다.** 관문이 잡으려는 것과 정확히 반대되는 오답이다.
   `_RESIDUAL_DEGENERATE_RATIO`로 퇴화를 판정해 None(= 잴 수 없다)으로 돌린다.
2. **순위 부분상관은 기준선의 "순위-선형" 성분만 제거한다.** 기준선이 하나면 단조 관계
   전체가 순위로 보존돼 정확하지만, **여럿이면** 값 공간의 선형결합이 순위 공간에서 선형이
   아니라 잔여가 남는다(실증: 기준선들의 선형결합인 피처가 증분 0.55). 방향이 중요하다 —
   이 누수는 부분 IC를 **크게** 만들어 피처에 유리하다(비보수적). 그래서 기본 기준선을
   단변량(`--baseline rv`)으로 두고 HAR 3성분은 보조로 내렸다. 테스트로 고정했다.

### 결과 — 증분은 남는다. 그리고 남는 것이 바뀐다

기준선 자신의 예측력(직전RV 단독 IC): 5m +0.576 · 15m +0.492 · 30m +0.373. 넘어야 할 선이 높다.

| Horizon | 통제 없음 | 직전RV 통제 | **RV+GK 통제** |
|---|---|---|---|
| 5m | 78 / 137 | 63 | **59** |
| 15m | 69 / 137 | 59 | **56** |
| 30m | 66 / 137 | 62 | **47** |

**통과 수보다 중요한 것은 상위 목록이 완전히 바뀐다는 것이다.**

통제 전 상위: `vl_gk_5`·`vl_atr_rel_5`·`vl_yz_5`·`vl_park_5` (전부 변동성 추정량).
RV+GK 통제 후 상위: **`ev_tod_cos`**(5m 0.159 / 15m 0.377 / 30m 0.442) · `ev_close_remain` ·
`px_kurt_r` · `px_high_dist` · `px_ema_dev`.

변동성 추정량 계열은 |IC| 중앙값이 0.469 → 0.194 → **0.048**로 무너진다(5m). 예상대로
기준선의 프록시였다. 그런데 **시간 축은 오히려 커진다**:

    ev_tod_cos    15m  0.331 → 0.377      (통제 후 증가)
    ev_close_remain 15m 0.062 → 0.335     (5배 증가)

이건 억제변수(suppressor)의 전형적 서명이다 — **일중 변동성 계절성이 변동성 수준과 직교**
하므로, 수준을 통제하면 계절 성분이 오히려 또렷해진다.

### 그래서 답

① 의 질문("137개가 단순 기준선을 넘는가")의 답은 **넘는다**. 다만 넘는 주체가 바뀐다:

- **변동성 크기 계열은 대부분 기준선의 프록시다.** 78개 통과가 축 전환의 근거가 아니었다.
- **실제 증분은 시간(일중 계절성)과 꼬리·위치 계열에서 나온다.** 특히 **EV** — 어제 방향
  축에서 137개 중 3개만 통과시켜 "소폭"이라 보고했던 그 카테고리가, 변동성 축에서
  지속성을 통제하고 나면 **상위를 독점한다**.

### 주의 — 이 숫자로 하면 안 되는 것

- **부호 뒤집힘이 있다**(`px_max_ret_20` 15m: +0.155 → −0.234). 부분상관은 통제 후 부호가
  바뀔 수 있고 인과로 읽으면 안 된다. 예측력의 존재만 말한다.
- 여전히 **8개월 단일 국면**이고 생존 검정(③)은 창 부족으로 미실행이다.
- 증분이 있다는 것과 **돈이 된다**는 것은 다르다 — 변동성 예측을 파는 수단(옵션 스프레드·
  레인지 매매)이 이 프로젝트엔 아직 없다. ②가 그 질문이고 미착수다.
- 진짜 기준선은 시장의 **내재변동성**이다. 옵션체인이 2026-08-05부터 쌓이므로 F5에서 측정.

### 검증

- 테스트 1339 → **1349건 전부 통과**(부분상관 known-value 10건 신규 — 닫힌 식 대조 포함),
  ruff 클린.
- 산출물: `logs/feature_gate_vol.json`(통제 없음) · `_vol_rv.json` · `_vol_strict.json`.
  JSON에 `baseline`/`baseline_features`를 기록한다.

---

## 2026-08-04 일일점검 대응 — 관측 장치가 자기 자신을 검증하지 못했다 ([MW0601], 2026-08-05)

> 요청: "8/4 당일 로그를 조사해 장전·장중·장후 이상점 정리 → fix 구현계획 → 고도화 방안".
> 이어서 "제안한 P0, 1, 2 Fix 모두 구현계획 꼼꼼히 수립하고 실수 없이 구현해".
>
> 테스트 **1349 → 1403건 전부 통과**, ruff 클린.

### 이 세션의 한 문장

8/4 무결성 리포트는 **"CRITICAL 0 · ERROR 0 · WARNING 0 · 결손 0분"으로 깨끗했는데**, 실제로는
그날 아카이브가 거래량 **55%**짜리였고 마지막 1분봉이 상위 Horizon 전부에서 빠졌으며 로컬
시계가 9.7초 느렸다. 셋 다 리포트가 보는 축 **바깥**이었다.

### 발견 1 — 로컬 시계가 하루 4~5초씩 느려지고 있었다 (P0-1)

로그만으로 찾아 외부 기준으로 확정한 건이다.

`FeaturePublish(1m)` 409건의 로컬 시각 분포가 **p5~p95 = 49.8~51.3초**로 극도로 타이트했다.
1분봉은 `ts_exchange`가 분 경계를 넘을 때 닫히므로(`data/normalizer.py`), 그 :50은 곧
"거래소 시각이 로컬보다 ~9.7초 앞선다"는 뜻이다. 독립 증거: `CollectorFirstTick` 수신
시각이 로컬 08:44:49.59인데 그 틱이 만든 첫 봉은 **08:45**다(→ 스큐 ≥ 10.4초).

전 거래일에 같은 방법을 적용하니 **단조 증가**였다:

| 07-27 | 07-28 | 07-29 | 07-30 | 07-31 | 08-03 | 08-04 |
|---|---|---|---|---|---|---|
| +13.8s | +17.8s | +21.8s | +26.2s | 판독 불가 | +4.5s | +9.7s |

(07-31은 그날 첫 틱이 15:13이고 네이티브 크래시 8건이라 표본이 오염됐다. 07-31~08-02 사이에
한 번 리셋된 흔적이 있다.)

**외부 확인**: `w32tm /stripchart` → 로컬이 **14.41초 느림**. `Get-Service w32time` →
**Stopped / Manual**. 부팅해도 안 켜지니 드리프트가 누적되고 있었다. 로그에서 유도한 값
(08-04 +9.7초) + 하루치 드리프트가 NTP 실측 14.41초와 **정확히 맞았다** — KIS 타임스탬프는
정확했고 이 PC의 시계가 틀렸다.

**왜 아무도 몰랐나**: SYSTEM.md §4-6은 기동 자가 점검에 "시간 동기"를 요구하는데,
`self_check.check_timezone()`은 **UTC 오프셋이 9시간인지만** 봤다. 요건은 문서에 있었고
검사는 이름만 있었다 — [[measure-known-limitations]]의 정확한 재현.

### 발견 2 — 그날 마지막 1분봉이 상위 Horizon 전부에서 빠졌다 (P0-2)

1분봉 합 **84,346** vs 3/5/10/15/30분봉 전부 **84,209**. 차이 137은 정확히 15:34봉 하나다.

원인은 `_daily_close()`의 경합이다. `collector.flush_final_bar()`는 버스에 발행만 하고
돌아오는데, 구독자(합성기) 콜백이 그 사이에 실행된다는 보장이 없다. 곧바로
`composer.flush_all_final()`이 돌면 그 봉은 어느 버킷에도 안 들어간다.

**자기 정정**: 처음엔 "07-29~08-03엔 총합이 일치했으니 간헐 결함"이라고 보고했다. **틀렸다.**
그 5일치 1분봉은 8/4 09:52에 백필로 교체되고 상위 봉도 `compose_offline`로 재합성된 것이라
**양쪽이 같은 함수의 산물**이었다 — 라이브 경로를 검증한 값이 아니다. 라이브 경로의 미교체
증거는 08-04 하루뿐이고 그 하루가 유실을 보였다. 즉 이 결함은 간헐적인 게 아니라
**한 번도 검증된 적이 없었다**.

### 발견 3 — 크래시가 0건인 날에만 집계가 실패한다 (P0-3)

`native_crashes: {available: false, details: ["Get-WinEvent 실패"]}`. 재현해 보니 원인이
거꾸로였다: 창 안에 `Application Error` 이벤트가 **하나도 없으면** Get-WinEvent가 비종료
오류를 내고 powershell.exe가 exit 1로 끝난다. `-ErrorAction SilentlyContinue`는 출력만 막고
종료 코드는 못 막는데, 파이썬이 `returncode != 0`을 실패로 읽었다.

| 07-29 | 07-30 | 07-31 | 08-03 | **08-04** |
|---|---|---|---|---|
| 2건 ✅ | 10건 ✅ | 8건 ✅ | 2건 ✅ | **0건 ❌ 집계 실패** |

**UI 크래시 격리(P0-1b)가 처음 성공한 그 날, 성공을 증명할 수치가 사라졌다.** 그리고
"3거래일 연속 `native_crashes ≤ 0`"을 조건으로 건 등록부는 그 상태로는 **영원히 판정을
못 채운다**(기한 08-14).

### 발견 4 — 무장 마커 오탐이 만든 `재발` ERROR (P1-1)

리포트의 유일한 ERROR(`crash-forensics-armed 재발`)는 오탐이었다. `.bat`가 stderr를
PowerShell 파이프라인(`2>&1 | ForEach-Object`)에 태우는데, PS 5.1이 네이티브 exe의
**첫 stderr 줄**을 NativeCommandError로 감싸 `python.exe : ` 접두사를 붙인다. 마커는
프로세스가 내는 첫 stderr 줄이라 정확히 그 자리에 걸렸고, `^...$` 앵커 매치가 깨졌다.

부수 위험이 더 컸다: 실제 faulthandler 덤프도 같은 스트림이라 첫 줄이 똑같이 오염된다 —
**증거를 남기려고 만든 장치가 증거를 훼손하는 경로에 물려 있었다.**

### 발견 5 — 그날 수집분만 거래량 절반짜리로 남았다 (P1-2)

수집 프로세스는 08:35에 `d5e6b01`로 떠서 하루를 돌았고, WS 프레임 다중 레코드 유실 수정
(`2b8b912`)은 같은 날 **12:22**에 들어갔다. 그런데 09:52~09:56에 백필이 07-20~08-03 전
구간을 공식 분봉으로 교체하면서 **08-04만 옛 규칙 데이터로 홀로 남았다.**

재백필로 확정: **84,346 → 152,963** (수집분이 공식값의 **55.1%**).

### 발견 6 — 장중에 백필·학습이 5회 돌았다 (P1-3)

09:52 백필 · 13:27·13:50·14:03·14:24 모델 스윕 · 13:42 워크포워드. R11(장중 학습·배포 금지)은
**문서에만 있었고** 아무것도 막지 않았다. 라이브에 배포된 건 없어 실해는 없었지만, 백필은
KIS REST를 쓰고 08-04 저녁 결선된 옵션체인 폴러가 장중 용량의 33%를 상시 점유한다 —
마흐디가 정확히 이 형태로 07-30에 옵션체인 25사이클을 잃었다.

---

### 구현 — 세 겹으로 막는다

**P0-1 시계**
- 호스트: `w32time` → Automatic + NTP 피어(time.nist.gov, 1024초). 오프셋 **14.41초 → −0.0006초**.
- `ops/clock_skew.py` 신설 — `ts_exchange − 수신시각`의 롤링 **최댓값**. 최댓값인 이유는
  거래소 스탬프가 초 단위 절삭이라 모든 표본이 참값 이하로 깎이기 때문이고, 그래서 이
  추정값은 **항상 참값의 하한**이다(안전한 방향).
- `self_check.check_clock()` 신설 — w32time 상태 + NTP 오프셋. |오프셋| > 5초면 **기동 거부**,
  > 2초면 경고. 못 재는 경우는 통과시키되 사실을 남긴다(오프라인 PC를 못 돌게 하진 않는다).
- 무결성 리포트에 `clock_skew_seconds` + 임계 2.0초.

**P0-2 종료 경합 + 합성기 내성** (`data/bar_composer.py`)
1. **봉 도착 기반 롤오버** — 다른 버킷의 봉이 오면 그 자리에서 이전 버킷 확정. 이 경로는
   **시계를 전혀 안 본다**. `compose_offline`과 정의상 같은 결과.
2. **스큐만큼 flush 지연** — 스케줄러가 쐈을 때 거래소 시각으로 경계가 안 지났으면 그만큼만
   더 기다린다(상한 30초). 스큐 ≤ 0이면 대기 0초 = 종전과 동일 동작.
3. **늦은 봉 거부** — 이미 확정한 버킷으로 오는 봉은 `ComposerLateBarDropped`(WARNING).
- `wait_for_bar()` + `flush_final_bar()`가 봉을 반환 → `_daily_close()`가 경합을
  **관측 가능한 대기**로 바꾼다. 실패해도 종료는 계속하되 `DailyCloseBarNotDrained`(ERROR).

**중요한 정정**: 늦은 봉이 만드는 것은 중복 행이 **아니다**. `ParquetArchiver`가
`(bar_open_kst, horizon)`으로 `unique(keep="last")` 하므로 **나중 것이 먼저 것을 덮어쓴다** —
5분봉 하나가 구성봉 5개짜리에서 1개짜리로 조용히 바뀐다. 행 수·연속성·NaN 비율 전부 정상으로
보이고 **거래량 총합만이 유일한 흔적**이다. 그래서 신설한 검사가 행 수가 아니라 총합을 본다.

**P0-3 크래시 집계** — 스크립트가 항상 exit 0으로 끝나고 첫 줄에 `OK <건수>` / `ERR <예외형>`
센티널을 찍는다. "이벤트 없음"은 로케일 문자열이 아니라 번역 안 되는
`FullyQualifiedErrorId`(`NoMatchingEventsFound*`)로 식별. `NativeCrashes.supported` 신설로
"원래 못 세는 플랫폼"과 "질의 실패"를 갈랐고, 후자는 **그 자체가 breach**다.

**P1-1 마커** — 탐지기를 `.search()`로 느슨하게 + **두 번째 출처**(`CrashForensicsArmed`
구조화 로그) 추가. 근본은 `.bat`에서 stderr 병합을 PowerShell이 아니라 `cmd /c`가 하게 바꾼 것
(실측 검증: 마커 무손상 · 한글 UTF-8 무손상 · 종료 코드 전파).

**P1-2** — 08-04 재백필·재합성 완료. `scripts/verify_archive_volume.py` 신설(공식 분봉 대비
거래량 비율, 임계 0.95). 장후 자동 실행에 **안 넣었다** — REST 호출을 15:35~15:40 종료 예산에
넣으면 종료 절차가 네트워크에 의존하고, 이 대조가 필요한 상황(파서 변경·백필 이후)은 매일이
아니다. 대신 무결성 리포트가 `session_git_shas`를 **사실로만** 기록한다(판정 안 함 — 연구
커밋이 잦은 이 프로젝트에서 매일 울리면 늑대소년).

**P1-3** — `ops/session_guard.py` 신설, 연구 스크립트 6개(백필·수급백필·재합성·스윕·
워크포워드·관문)에 배선. 정규장이면 거부(exit 2), `--force-intraday`로만 통과하되 그 사실을
표준출력에 남긴다.

**P2** — `SelfEvalReport`의 손익 4지표를 `float | None`으로. `pnl_measurable=False`면 None.
`n_fills`(07-31)·`slippage_realized_ticks`(08-03)에서 이미 두 번 쓴 해법의 **네 번째 적용**이다 —
플래그는 같이 안 읽히면 소용이 없고, None은 포맷 문자열에서라도 걸린다.

### 신설: Horizon 총합 항등식 — 매일 자동으로 도는 유일한 정합성 검사

상위 봉은 1분봉의 합이라는 것이 `compose_offline`의 **정의**이므로 외부 기준이 필요 없다.
`analyze_horizon_consistency()`가 매일 그 항등식을 확인한다. 이 한 줄이 있었으면 08-04
유실을 **당일** 잡았다. 함께 잡히는 것: 1분봉만 백필하고 상위 Horizon 재합성을 안 한 상태,
그리고 위에 적은 덮어쓰기.

### 등록부 정정 — 넓은 그물이 만든 오탐 두 건

`crash-forensics-armed`와 신설 `crash-count-measurable`을 `breaches`(넓은 그물)로 채점하면
**무관한 사고 하나가 이 수정들을 "재발"로 만든다**. 실제로 08-04 재산출에서 체결틱 0행(그날
결선 전이라 정상) 때문에 또 ERROR가 났다. 좁은 지표 둘을 신설해 재배치:
`crash_forensics_unarmed`(무장 안 된 프로세스 수) · `native_crashes_measurable`(1=쟀다/0=못 쟀다).

### 08-04 리포트 재산출 결과 (수정 전 → 후)

| 항목 | 수정 전 | 수정 후 |
|---|---|---|
| 네이티브 크래시 | 집계 불가 | **0건** (available=true) |
| `crash_forensics.armed` | l1_daily·g2_paper **false** | **셋 다 true** |
| Horizon 항등식 | (검사 없음) | **위반 없음** (재백필 후) |
| 1분봉 거래량 합 | 84,346 | **152,963** |
| `ui-crash-isolation` | 0/3 (판정 불가) | **1/3** |
| `crash-forensics-armed` | ❌ 재발(ERROR) | **1/3 검증 대기** |
| 남은 breach | 2건(둘 다 오탐) | 1건(체결틱 0행 — 사실) |

### 검증

- 테스트 **1349 → 1403건** 전부 통과, ruff 클린.
- 회귀 테스트가 **수정 전 코드에서 실제로 깨지는지 확인함** — 합성기 3겹을 일시 무력화하니
  신규 4건이 정확히 실패했다.
- `.bat` 파이프라인을 실제 `crash_forensics` 모듈로 관통 시험: 마커 무손상 → 실제 탐지기가
  `armed={l1_daily: True}`, findings 없음.

### 다음 거래일(2026-08-05) 관측 포인트

1. `1m` 롤오버 로컬 초가 **:59~:00** 부근으로 올라오는가(08-04엔 :50). 안 오면 시계 재확인.
2. `clock_skew_seconds`가 리포트에 찍히는가, |값| < 2초인가.
3. `horizon_findings`가 비어 있는가 — 종료 경합 수정의 첫 실전 검증.
4. `native_crashes.available`이 true인가(크래시 0건이어도).
5. 기존 체크리스트 A-1~A-3·B-1~B-3(`NEXT_TODO.md`)은 그대로. B-1은
   `scripts/verify_archive_volume.py --date 2026-08-05`로 자동화됨.

---

## 고도화 5종 구현 — 리포트가 "무엇을 모르는지"까지 말하게 ([MW0601], 2026-08-05)

> 요청: "고도화 방안 5종 모두 구현계획 꼼꼼히 수립하고 실수 없이 구현해".
> 테스트 **1403 → 1432건 전부 통과**, ruff 클린.

### 먼저 정정 — 어제 고도화 4의 전제가 틀렸다

어제 "변동성 축 번들을 shadow로 올리면 `wiring_stage`가 움직인다"고 썼다. **틀렸다.**
`WiringCompleteness.stage`는 `live_bundles`만 보고 shadow는 별도 필드다. 그리고 더 중요한
것: `ShadowLedger`는 방향 예측을 **가상 체결**로 환산해 손익을 내는데, 변동성 예측을 거기
태우면 무의미한 숫자가 나온다("변동성이 커진다"는 매수도 매도도 아니다). 이 프로젝트엔
아직 변동성을 파는 수단이 없다(옵션 스프레드·레인지 매매 미구현).

그래서 고도화 4를 **손익이 아니라 예측 품질로 채점하는 경로**로 만들었다.

### 고도화 1 — 외부 대조를 리포트의 1급 축으로

`verify_archive_volume.py`가 `logs/volume_check_YYYYMMDD.json`을 남기고 리포트가 그걸 읽는다.
REST를 종료 절차(15:35~15:40)에 안 넣는다는 판단은 유지하되, **안 돌린 날이 조용히
지나가지 않는다** — 없으면 `unmeasured`로 올라간다.

### 고도화 2 — 측정 불능의 승격 (둘)

- **`unmeasured` 축 신설**: 못 잰 것을 한자리에 모은다(크래시 집계·시계 스큐·거래량 대조·
  변동성 축·피처 건강도·호스트 항목). 요약 출력에서 **임계 초과 바로 앞**에 둔다 — 사람이
  "깨끗한 날"이라고 읽기 전에 "무엇을 모르는 날인지"를 먼저 보게 하는 것이 목적이다.
- **`STALLED`(판정 불가 정체) 상태 신설**: N거래일 연속 판정 불가면 `재발`과 같은 급으로
  올린다(ERROR). 2026-08-04가 정확히 이 상태였다 — `Get-WinEvent`가 크래시 0건인 날에만
  실패해 `ui-crash-isolation`이 며칠이 지나도 0/3이었고, 아무도 그게 "진행 중"이 아니라
  **"계측 고장"**이라는 걸 몰랐다.

### 고도화 3 — 죽은 피처를 운영 경로에서 검출

`FeatureEngine`이 세션 동안 피처별 `n/n_nan/min/max`를 누적하고, 장 마감에
`log_feature_health()`가 **항상 NaN**과 **상수**를 갈라 남긴다. 퇴화 0건도 남긴다 —
로그가 없는 날은 "검사했는데 0건"과 "검사를 안 함"이 구분되지 않는다.

`nan_ratio`가 못 보는 것을 본다: `px_macd_h_5`는 **값을 내므로** 8거래일 내내 죽어 있었는데
무결성 리포트에 아무 흔적이 없었다. 비용은 피처당 float 4개다.

### 고도화 4 — 변동성 축을 매 거래일 out-of-sample로 채점

`models/vol_scorecard.py` + `scripts/run_vol_scorecard.py`. 모델도 학습도 배포도 없다 —
그날 아카이브와 로컬 계산뿐이다. **구현하면서 스스로 함정 셋에 빠졌고 셋 다 실측으로 잡혔다:**

1. **관심 목록에 창 접미사가 빠졌다** (`px_kurt_r` vs `px_kurt_r_5`) — 첫 실행에서 다섯 개가
   전부 "미측정"으로 나와 발견. 그리고 `ev_*` 둘은 **프로덕션 feature_set(v2026.07)에 아예
   없다**(08-04에 만들었지만 아직 안 켬). 일부러 목록에 남겼다 — 매일 "피처셋에 없음"으로
   찍히는 것이 "관문이 가장 값어치 있다고 지목한 카테고리가 아직 운영에 없다"를 계속 드러낸다.
2. **하루치로는 15m(20표본)·30m(7표본)이 매일 표본 부족**이었다 — 관문이 정작 검증하고
   싶었던 두 Horizon이 영원히 판정 불가가 된다. 대상일로 **끝나는 최근 20거래일**을
   채점하도록 바꿨다(R18의 섀도 계측 기간과 같은 값).
3. **통과 기준이 "부분 IC ≠ 0"이었다** — 거의 모든 실수는 0이 아니므로 7개 중 5개가 무조건
   통과했다. 판정이 아니라 **판정하는 척**이었다. 관문(`features/gate.py`)과 **같은 기준**
   (|IC| ≥ 0.02 **그리고** 겹침 보정 |t| ≥ 2.0)으로 고쳤다.

그리고 가장 중요한 네 번째: **직전 RV만 통제하면 매일 거짓 양성이 난다.** RV-only 통제에서
`vl_gk_5`(t +8.8)·`vl_atr_rel_5`(t +9.0)가 5m·15m을 통과했는데, 그건 08-04 관문이 이미
"기준선의 프록시"라고 판정한 바로 그 계열이다(RV+GK 통제 시 |IC| 중앙 0.469 → 0.048).
종가 기반 RV는 비효율적 추정량이라 그것만 통제하면 "OHLC로 현재 변동성을 더 잘 잰다"가
증분처럼 보인다 — 새 정보가 아니라 추정 효율이다. **레인지 기반 GK를 기준선에 추가**했다.

#### 첫 실측 결과 (2026-08-04 기준, 최근 20거래일)

| Horizon | 표본 | 기준선 IC | 기준선 초과 |
|---|---|---|---|
| 5m | 1,448 | +0.381 | **0 / 7** |
| 15m | 478 | +0.312 | **0 / 7** |
| 30m | 236 | +0.083 | **0 / 7** |

**측정 가능한 5개 중 어느 것도 RV+GK 기준선을 넘지 못했다.** 관문이 상위로 지목한 `ev_*`
둘은 프로덕션 feature_set에 없어 아예 측정되지 않는다. 즉 **다음 할 일은 모델이 아니라
EV를 켜는 것**이라는 게 이 채점의 첫 답이다. 30m 기준선 IC가 +0.083까지 내려온 것도
주목할 값이다 — 08-04 관문의 5m +0.576과 견주면 지속성 자체가 최근 훨씬 약하다.

### 고도화 5 — 호스트 위생을 자가 점검 범위로

`ops/host_health.py` — 디스크 여유·전원 계획(절전)·Docker 응답. `self_check`에 `host` 항목.
2026-08-04에 시계가 14.41초 밀린 원인이 코드가 아니라 **꺼져 있던 서비스**였는데, 그때까지
자가 점검은 애플리케이션 안쪽만 봤다. 복제 배포에서 호스트 상태는 `instance.yaml`에 안 적힌다.

**로케일 함정을 두 번 밟았다**: `powercfg /query`의 라벨이 한글("현재 AC 전원 설정 색인")이라
`Select-String 'Current AC Power Setting'`이 안 걸렸다. 레지스트리 직접 조회도 시도했으나
설정이 기본값이면 키가 아예 없어(실측) 그 경로도 못 썼다. 결국 **16진 토큰의 위치**(뒤에서
두 번째가 AC)로 읽는다 — `ops/integrity_report.py`가 이벤트로그에서 로케일 문자열을 피해
`Properties` 배열을 읽는 것과 같은 회피법이다.

그리고 `check_clock`의 NTP 표본을 1개 → **3개**로 늘렸다. 첫 표본이 자주 `0x800705B4`
(타임아웃)로 실패해서(실측) 시계가 멀쩡한 날에도 "측정 실패"가 됐다 — 그러면 이 검사가
있으나 마나가 된다.

### 테스트에서 나온 것 — 검출기가 맞고 픽스처가 틀렸다

피처 건강도 테스트를 "깨끗한 하루"로 짜려다 세 번 실패했다:

- 주기적 톱니(`i % 13`) 입력 → `px_rsi_*`·`px_breakout_*`·`px_max_ret_*`·`vl_range_exp_*`가
  전부 상수로 잡힘
- 의사난수 보행 + 고정 고저폭 → `vl_range_exp_*`가 상수(레인지가 늘 같으니 당연하다)
- 35봉 콜드스타트 → 창 180짜리 피처가 정당하게 항상 NaN

전부 **검출기가 옳았다.** 그래서 "깨끗한 하루"는 통계를 직접 주입해 로깅 계약만 격리하고,
콜드스타트 케이스는 **웜스타트 실패를 잡는 테스트**로 뒤집어 남겼다(2026-07-29에 L1이 6번
재시작돼 워밍업이 전량 소실된 전례가 있는데, 그날 리포트엔 어느 피처가 죽었는지가 없었다).

### 등록부에 추가한 둘

- `daily-axes-measured`(`unmeasured_count ≤ 0`) — **이 등록부의 메타 항목이다.** 다른
  항목이 판정 불가로 정체되는 근본 원인이 전부 여기 모인다.
- `no-degenerate-features`(`degenerate_feature_count ≤ 0`)

### 검증

- 테스트 1403 → **1432건** 전부 통과, ruff 클린.
- 08-04 리포트 재산출로 5축이 전부 찍히는 것 확인 — 거래량 비율 1.000(재백필 후),
  변동성 축 3 Horizon, 호스트 3항목, 미측정 2건(그날 세션이 이 코드 이전이라 정상).

---

## 2026-08-05 장중점검 대응 — 시계를 고쳤더니 9.7초짜리 쿠션이 사라졌다 ([MW0601], 2026-08-05)

전날 P0-1(시계 NTP 동기)이 실전 첫날인 08-05 장중에, **바로 그 수정이 잠복 결함 하나를
드러냈다.** 09:40 시점 조사에서 P0 1건 · P1 2건 · P2 2건을 확정하고 전건 구현했다.

### [P0-1] 상위 Horizon 합성봉이 매 버킷 마지막 1분봉을 잃고 있었다

**증상** (09:40까지 실측, `logs/l1_daily_20260805.log` + `data/bars/A05608/`)

| Horizon | 봉 수 | 거래량 | 같은 구간 1분봉 합 | 결손 | quality_ok |
|---|---|---|---|---|---|
| 3m | 18 | 19,000 | 22,858 | −3,858 (16.9%) | 6/18 |
| 5m | 11 | 20,752 | 23,371 | −2,619 (11.2%) | 3/11 |
| 10m | 6 | 22,255 | 23,371 | −1,116 (4.8%) | 2/6 |
| 15m | 3 | 18,274 | 18,875 | −601 (3.2%) | 1/3 |
| 30m | 2 | 18,141 | 18,875 | −734 (3.9%) | **0/2** |

`ComposerLateBarDropped` 26건. 3분봉 18개 중 12개가 2분짜리, 30분봉은 2개 다 29분짜리였다.

**원인 — 겹②의 전제가 틀렸다**

전날 넣은 3겹 방어(`data/bar_composer.py`)는 셋 다 **시계**를 전제한다. 그런데 1분봉은
시각으로 확정되지 않는다 — `MinuteBarAggregator`는 *다음 분의 첫 틱이 도착해야* 이전 분의
봉을 내놓는다(`data/collector.py`의 `_handle_message`). 즉 1분봉의 도착 시각은 시계가 아니라
**틱 도착률**이 정한다.

같은 날 실측한 1분봉 발행 지연(경계 이후): 중앙값 **0.655초** · p75 0.966 · p90 **1.62** ·
최대 **7.96초**. 스케줄러 위상은 0.5초다 — **69%가 그 뒤에 도착했다.** 관측된 드롭률
(3m 67% · 5m 70% · 30m 100%)과 그대로 맞는다.

**왜 전날까지는 안 터졌나**: 로컬 시계가 거래소보다 9.7초 **느려서** 스케줄러가 거래소 기준
9.7초 늦게 쐈다. 우연한 9.7초짜리 유예였다. P0-1이 그 쿠션을 걷어냈다.

> 겹③(늦은 봉 거부)이 설계대로 동작해 **중복 행 대신 로그가 남은 것**이 이 진단을 가능하게
> 했다. 전날 수정이 없었으면 `unique(keep="last")`가 조용히 덮어써서 행 수로도 안 보였다.

**결정 — 겹④: 마지막 구성 1분봉 도착 대기**

스케줄러 경로는 그 버킷의 마지막 분봉이 실제로 도착할 때까지(상한 5초) 기다린 뒤 확정한다.
판단 기준이 시계가 아니라 **데이터의 도착**이라 스큐·틱 지연·이벤트 루프 지연 중 무엇이
원인이든 같이 막힌다. 상한 초과 시 `ComposerFlushedIncomplete`로 남기고 확정한다 — 거래
없는 분은 봉 자체가 안 나오므로 무한 대기는 유실보다 나쁘다.

**Why 5초**: 그날 표본의 p99 위쪽. 가장 짧은 합성 Horizon이 180초라 2.8%다. 비대칭이 명확하다
— 대기의 최악은 "몇 초 늦게 나간다", 안 기다린 최악은 조용한 데이터 손상이다.

**구현 중 발견한 잠복 결함 (같은 커밋에서 수정)**

겹②·④는 둘 다 await다. 그 사이 다음 버킷의 봉이 도착하면 겹①이 이전 버킷을 정확히 확정하고
새 버킷을 연다. 이때 깨어난 스케줄러가 그냥 `_flush_bucket()`을 부르면 **갓 열린 버킷을
1봉짜리로 확정**하고, `_last_flushed_start`가 올라가 그 버킷의 나머지 봉이 전부 늦은 봉으로
버려진다 — 원래 결함보다 나쁘다. 겹②를 넣은 시점의 코드에 이미 있었고, 그날 스큐가 실제로
대기를 유발하지 않아 드러나지 않았을 뿐이다.

→ `flush_due_horizon`이 **대기 전에 확정 대상 버킷을 고정**하고, 깨어났을 때 그 버킷이
그대로일 때만 확정한다.

**검증 — 오늘 실측 지연 분포를 그대로 재생**

96개 1분봉(거래량 41,683)에 그날의 실제 발행 지연을 입혀 가상 시계로 재생:

| | 3m 결손 | 5m 결손 | 30m 결손 | 버킷 유실 |
|---|---|---|---|---|
| 수정 전 | 10,665 (25.6%) | 6,037 (14.5%) | 1,242 (3.0%) | 46 |
| **수정 후** | **0 (0.00%)** | **0 (0.00%)** | **0 (0.00%)** | **0** |

전 Horizon 거래량이 41,683으로 1분봉 합과 정확히 일치 = Horizon 항등식 성립.

**부수 발견 — 오프라인 재생은 기다리면 안 된다**

`backtest/harness.py`의 `_feed_m1_bars`, `run_full_path_smoke.py`, `run_phase5_smoke.py`는
봉을 **동기 루프**로 밀어 넣는다. 대기 중에 새 봉이 도착하는 일 자체가 성립하지 않으므로
겹④를 타면 오지 않을 봉을 매 버킷 상한까지 기다린다. `tests/backtest` 실측:

| | 스위트 | 최장 테스트 |
|---|---|---|
| 기준선(수정 전) | 13.9초 | 4.87초 |
| 겹④만 넣고 `force` 누락 | **885초** | **441.25초** (90배) |
| 세 곳 `force=True` | 14.9초 | 5.16초 |

전체 스위트도 1,017초 → **144초**로 돌아왔다.
**대기가 무의미한 경로에서 대기하는 것은 안전이 아니라 그냥 결함이다.**

> 이 지연은 **테스트가 아니었으면 못 봤을 것이다.** 백테스트는 사람이 가끔 돌리는 경로라
> "오늘따라 느리네"로 넘어갔을 가능성이 높다. 실시간 경로만 보고 수정하면 재생 경로가
> 조용히 망가진다 — 같은 클래스를 두 종류의 구동자가 쓴다는 사실 자체가 위험 지점이다.

### [P0-2] 손상이 일어나는 동안 heartbeat는 하루 종일 OK였다

26건이 나는 내내 `status_snapshot.json`의 3축(`l1.collector`·`l1.feature_engine`·
`g2.pipeline`)이 전부 OK였다. 그 축들이 **신선도**("최근에 받았나")를 재는 반면 합성 손상은
"받은 것을 온전히 합쳤나"라서, 볼 축이 아예 없었다.

- `MultiHorizonBarComposer.health()` 신설 → `l1.composer` heartbeat(`run_l1_daily`),
  상태판·UI 컴포넌트 목록에 추가
- 무결성 리포트에 `late_bar_drops`(로그 기반) + 임계 0 + 0건도 찍는 요약 줄
- 등록부 `composer-bucket-completeness`(`late_bar_drops ≤ 0`, 3거래일, 기한 08-19)

**Why `horizon_findings`로 대체 안 되나**: 그쪽은 아카이브를 보므로 장 종료 후
`run_recompose.py`를 돌리면 0이 된다. 그러면 "재합성했으니 등록부도 통과"가 되어 정작 고쳐야
할 수집 경로의 결함이 판정에서 사라진다. **결과가 아니라 원인을 채점하는 자리가 따로 필요하다.**

### [P1-1] `FixedTickScheduler`가 같은 틱을 두 번 쐈다

```
08:43:19.998  OptionChainSkipped  series=weekly_thu
08:43:20.013  OptionChainSkipped  series=weekly_thu   ← 15ms 뒤 같은 틱
```

`asyncio.sleep`은 이벤트 루프의 **단조 시계**로 자는데 목표는 `now_utc()`의 **벽시계**로
계산한다. 몇 밀리초 일찍 깨면 `next_tick_at`의 `math.floor()`가 같은 n을 돌려준다.
옵션체인에서는 42다리 REST 사이클이 통째로 두 번 도는 것이라 유량 예산이 2배가 된다 —
그날은 기준가가 없어 실제 폴링까지 안 가서 운이 좋았다.

**결정**: 목표가 직전 틱보다 뒤가 아니면 격자상 다음 칸으로 올린다. 격자는 epoch 기준
그대로라 위상은 안 밀린다.

**관측 방법이 없다는 것도 기록**: 중복 발화 자체는 조용하다. 회귀 테스트는 콜백을 일부러
실패시켜 `SchedulerCallbackError`에 실린 `target`을 비교한다.

### [P1-2] 옵션체인 다리 유실 — 재시도 경로가 없었다

`OptionChainPollError` 5건(KIS VTS 500 ×3 · `Server disconnected` ×2) → 먼쓰리 3사이클과
목위클리 1사이클이 42다리가 아니라 **41다리**로 남았다.

유량 점유가 **33%**(내성 3.03배)였으므로 여유가 없어서가 아니라 **경로가 없어서**였다.
같은 사이클 안에서 1회 재시도(0.5초 뒤, 공유 RateLimiter가 이미 1초를 강제하므로 실효 1.5초).
다음 사이클로는 넘기지 않는다 — 격자 규율이 이 모듈 설계의 전부다.

**태그를 가른다**: 재시도로 살아나면 `OptionChainPollRetried`(INFO), 끝내 실패하면
`OptionChainPollError`(WARNING) + `attempts`. 둘 다 WARNING이면 그 건수가 더 이상 "잃은 다리
수"를 뜻하지 않게 된다.

### [P2-1] 장전 08:35~08:45 옵션체인이 구조적으로 비었다

수집은 08:35에 뜨는데 첫 틱은 **08:45 정각**이다(3거래일 연속 실측). 그 10분간 ATM 기준가가
없어 5사이클이 통째로 스킵됐고, 옵션 스냅샷은 소급 경로가 없어 **영원히 빈다**.

`LastPriceTracker.seed_preopen()` — 직전 완성 1분봉 종가를 시드로 넣는다(행사가 간격 2.5pt,
ATM±10이 50pt를 덮으므로 하룻밤 갭이 창을 벗어나지 않는다).

**시드는 첫 실틱 전까지만 유효하다.** `update()`가 한 번이라도 불리면 영원히 무시된다 —
안 그러면 장중 WS 단절 시 신선도 규칙("오래된 값은 없는 것으로 친다")을 시드가 우회한다.
그것도 하필 사고 중에. 이 제약을 테스트로 고정했다.

### [P2-2] CPU 경합을 잴 축이 없었다

P0-1 진단의 근거가 된 지연 분포의 꼬리가 **최대 7.96초**(중앙값의 12배)였는데, 이벤트 루프
지연을 뒷받침하거나 기각할 측정이 하나도 없었다. 같은 시각 이 PC의 실제 상태(`Win32_Process`):

```
futures/main.py (py37_32)         CPU 260.6초
mahdi.main ×2 + 대시보드 ×2        CPU 250 / 58.9초
MESSIAH l1_daily / g2_paper / UI   CPU 39 / 4.6 / 27초
```

`host_health.check_cpu_contention()` — 사용률 + MESSIAH 밖 파이썬 워크로드 수·누적 CPU초.
**판정은 안 한다**(`ok`가 항상 True). 임계를 정할 근거가 없다 — 며칠 실측 후에 정한다.
대신 `available=False`(못 쟀다)와는 구분되므로 측정이 사라지면 `unmeasured`에 뜬다.

**MESSIAH 자신을 거르는 판별이 까다로웠다**: 수집·페이퍼 프로세스는 명령줄에 프로젝트 루트가
안 나온다(`-u scripts\run_l1_daily.py` 상대 경로). 그래서 `scripts/`의 실제 파일 이름을
표지로 쓴다 — 진입점이 늘면 자동으로 따라온다.

> 부수 확인: `.venv\Scripts\python.exe`와 `anaconda3\python.exe`가 같은 스크립트로 짝지어
> 보이는 것은 **uv가 만든 venv의 트램폴린**이다(부모가 기저 인터프리터를 자식으로 띄운다).
> 이상 징후가 아니다.

### 적용 시점 — R11

전건 **15:35 이후 재기동으로 적용**한다(SYSTEM.md R11 장중 배포 금지). 파일을 고쳐도 이미
떠 있는 프로세스는 옛 코드로 돈다 — 오늘 남은 수집분은 그래서 계속 손상된다.

**장 종료 후 순서** (재합성 전에 리포트를 먼저 떠서 관측 장치가 이 사고를 실제로 잡는지 본다):

```
python scripts/daily_integrity_report.py --date 2026-08-05   # ① late_bar_drops·horizon_findings 확인
python scripts/run_recompose.py --symbol A05608              # ② 상위 Horizon 전량 재합성(1분봉은 무손상)
python scripts/verify_archive_volume.py --date 2026-08-05
python scripts/run_vol_scorecard.py     --date 2026-08-05
python scripts/daily_integrity_report.py --date 2026-08-05   # ③ 재산출
```

> **EV 재학습은 ②보다 뒤에 해야 한다.** 지금 재학습하면 손상된 상위 Horizon 봉으로 배운다.

### 검증

- 테스트 1432 → **1456건** 전부 통과, ruff 클린, pyright 신규 오류 0(기존 4건 그대로).
- 새 회귀 테스트가 실제로 결함을 잡는지 **수정을 되돌려 확인**했다: 합성기 4건·스케줄러 1건
  전부 실패 → 복원 후 통과.
- 오늘 실측 지연 분포 재생으로 결손 25.6% → 0.00%.

---

## 2026-08-05 2차 — 고도화 5종: 근본 처방과, 그것을 재는 장치 ([MW0601], 2026-08-05)

장중점검 P0/P1/P2(같은 날 1차 항목)를 넣고 나서, 그 보고서가 함께 제안한 고도화 5종을
전건 구현했다. 성격이 뚜렷하게 갈린다 — **1은 근본 처방, 2~4는 그것을 재는 장치, 5는 그
장치가 승격을 자기검증하게 하는 것**이다.

### 고도화 1 — 1분봉 확정을 틱 도착에서 뗀다 (측정을 먼저 붙였다)

겹④(P0-1)는 정확하지만 느리다. 매 상위 봉이 1분봉을 기다린 만큼 늦게 나간다. 근본은
`MinuteBarAggregator`가 **다음 분의 첫 틱이 와야** 이전 분을 닫는 구조다.

`flush_due(exchange_now, grace)`를 넣되 **기본값은 여전히 틱 구동**으로 뒀다
(`configs/instance.yaml`의 `minute_bar_close: tick|timer`). 이유가 하나뿐이다:

> 시각으로 닫으면 유예 뒤에 도착한 틱을 버린다. 그 크기를 정하는 것은 회선의 수신 지연
> 분포인데, **2026-08-05까지 이 프로젝트엔 그걸 잰 데이터가 하나도 없었다** — 틱 아카이브는
> 거래소 시각만 남기고 수신 시각을 안 남긴다.

그래서 유예를 고르는 대신 **재는 것을 먼저 붙였다.** `ClockSkewTracker`가 이미 갖고 있던
표본에서 지연 분포가 공짜로 나온다. ŝ = max(표본)일 때

    ŝ − 표본ᵢ = (dᵢ + frac(tᵢ)) − min(d + frac(t))

**절대 지연이 아니라 "가장 빠른 프레임 대비 초과분"**이다. 처음엔 절대 지연을 재려다
테스트에서 p50이 0으로 나와 유도를 다시 했는데, 초과분이 나오는 것이 **오히려 맞았다**:
봉 경계 판정이 쓰는 ŝ 자체가 최소 지연을 이미 흡수하므로, 유예가 덮어야 하는 것은
d가 아니라 d − d_min이다. frac(t)만큼(최대 1초) 과대평가되는 안전한 방향이다.

- 세션당 한 줄 `TickDeliveryLatency`(p50/p90/p99/max/표본수) → 리포트 `delivery_latency`
- **판정은 안 한다.** 임계를 정할 근거를 모으는 중이라 breach가 되면 안 된다.
- 며칠 p99를 본 뒤 `MINUTE_CLOSE_GRACE_SECONDS` 확정 → `minute_bar_close: timer` 승격.

부수로 **종전의 조용한 유실을 고쳤다**: `minute < _current_minute`인 틱을 로그 없이 버리고
있었다(L18 위반). 이제 `AggregatorLateTickDropped`로 남기되 **분마다 한 줄**만 — 매 틱
남기면 하루 수만 줄이 되어 아무도 안 본다(`FeaturePublish`가 그랬다).

### 고도화 2 — 항등식을 장후가 아니라 장중에, 그것도 연속으로

원래 계획은 "5분마다 직전 30분 구간을 아카이브에서 다시 읽어 검사"였는데, 구현하면서 더
나은 방법이 나왔다: **합성기가 자기 회계를 들고 있으면 된다.**

- `composed_volume[h]` — 지금까지 내보낸 합성봉 거래량 합
- `lost_volume[h]` — 늦게 도착해 버린 1분봉 거래량 합 (`ComposerLateBarDropped`에 실린다)
- 정의상 `lost == 0`이어야 하고, 그때 항등식이 성립한다

파일 I/O가 없고, 조각/통합본 배치를 신경 쓸 필요가 없고, 무엇보다 **첫 버킷에서 바로**
드러난다. 08-05엔 첫 증거가 08:48에 있었는데 사람이 안 것은 한 시간 뒤였다.

아카이브 검사(`analyze_horizon_consistency`)는 **그대로 둔다** — 그쪽은 적재 단계의 결함
(같은 시각 덮어쓰기 등)을 보는데 메모리 회계는 그걸 못 본다. 대체가 아니라 보완이다.

건수가 아니라 **거래량**을 단위로 삼은 것이 중요하다. "3봉이 늦었다"는 크기를 말해주지 않는다.

### 고도화 3 — "모른다"를 말할 수 있게 (`HealthLevel.UNKNOWN`)

**소비처가 이미 3분법을 쓰고 있었다는 점이 결정적이었다.**
`TradingPipeline._collector_healthy()`는 `None`(모름)/`True`/`False`를 나눠 쓰는데, 발행
쪽 열거형에는 `OK`/`WARN`/`CRITICAL`뿐이라 "모름"을 표현할 수단이 없었다. 그 결과:

> `staleness_status()`의 웜업 구간(첫 틱 이전)이 `OK`로 나갔고, 파이프라인이 그걸
> "한산하다"로 읽어 **서킷브레이커 승격을 억제했다.** 08:36~08:45의 9분 동안 수집기가
> 데이터를 한 건도 못 받은 상태가 CB 억제 근거로 쓰였다. 재연결 직후마다 같은 창이 열린다
> (워치독이 `reset()`된다).

`UNKNOWN`은 그 소비처의 `None` 갈래로 접힌다 — 억제하지 않고 원래 규칙대로 승격.

**중간에 한 번 헛짚었다.** 처음 쓴 회귀 테스트는 `UNKNOWN`을 손으로 주입했는데, 수정을
되돌려도 통과했다. `CircuitBreakerMonitor.observe()`가 `not collector_healthy`로 읽어
`None`과 `False`를 구분하지 않기 때문이다. **실제 결함은 `staleness_status`의 `OK`였고**
테스트가 그 경로를 안 탔다. 레벨을 손으로 넣지 않고 판정 함수를 통과시키도록 고쳐서
회귀를 실제로 잡게 했다(되돌려 확인함).

파이프라인의 `UNKNOWN → None` 분기는 오늘 동작이 같지만 그대로 뒀다 — `False`는
"수집기가 이상하다고 말하는 중"이라는 적극적 주장인데 `UNKNOWN`은 그런 주장을 한 적이 없다.

같은 원칙으로 **정상일 때도 근거를 말하게** 했다: 합성기는 "합성봉 N개 · 항등식 일치",
피처엔진은 "NaN 임계 이하 N개 Horizon". 근거를 못 대면 OK가 아니라 UNKNOWN이다
(합성봉 0개인 장전 구간이 실제로 그 상태다).

### 고도화 4 — 수정의 **전제**를 등록부에 적고 매일 잰다

08-05에 일어난 일의 형태: P0-1(시계 동기)이 P0-2(합성기 방어)의 **전제를 깼다**. 등록부는
각 수정을 자기 지표로만 채점하므로 그걸 볼 수 없었다 — **08-04 리포트에서
`horizon-volume-identity`는 깨끗하게 통과 중이었고, 통과하던 바로 그 순간 전제가 이미
거짓이었다.**

`PendingVerification`에 `premise` 블록을 넣고, 새 상태 `PREMISE_BROKEN`("전제 붕괴")을
추가했다. 우선순위는 **재발 다음**이다 — 결과가 이미 나빠졌으면 그게 더 급한 사실이다.

붙인 전제는 둘 다 `delivery_latency_p99_seconds ≤ 3.0`이다(겹④ 상한 5초의 60%):

- `composer-bucket-completeness` — 겹④가 마지막 구성봉을 5초 기다린다
- `horizon-volume-identity` — 봉 확정 경로 전체가 "1분봉이 경계 뒤 곧 도착한다"를 전제한다

전제도 결과와 **같은 엄격도**로 로드 시점에 검증한다(오타 난 지표는 `RegistryError`) —
조용히 건너뛰면 "전제를 감시 중"이라고 믿는 항목이 실제로는 아무것도 안 본다.

### 고도화 5 — EV 승격: 오늘 실행할 수 없고, 그래서 **막고 자기검증하게** 했다

승격 자체(재학습 → `feature_set` 교체)는 오늘 못 한다. 두 제약이 독립적으로 걸린다:

1. **R11 장중 학습 금지** — 지금 11시, 정규장이다. `run_model_sweep.py`는 이미
   `session_guard`가 막고 있다(2026-08-05 1차에 결선).
2. **재합성이 먼저** — 오늘 상위 Horizon 봉의 3~17%가 잘렸다. 복구 전에 학습하면 잘린
   봉을 그대로 배운다.

2번은 **문서에만 있던 순서**였다. 2026-07-29~08-03에 "다음 거래일에 확인한다"가 문서에만
있어 세 번 재발한 것과 같은 형태라, 코드로 옮겼다:

- `session_guard.refuse_if_archive_corrupt()` — 학습 구간에 `horizon_findings`가 비지 않은
  날이 있으면 거부하고 재합성 명령을 안내한다.
- **`late_bar_drops`는 일부러 안 본다.** 그건 수집 당시의 사건을 세는 지표라 재합성 후에도
  남는다(그게 존재 이유다). 그걸로 막으면 2026-08-05가 영원히 학습 불가가 된다.
- 리포트가 없는 날은 안 막는다 — 리포트는 07-27부터고 학습 구간은 수개월이다.

그리고 **승격이 조용히 안 먹는 것**을 잡을 자리를 만들었다. `vol_scorecard.summarise()`가
피처별 상태를 버리고 있어서, 리포트만 봐서는 `ev_*`가 "측정됐는데 못 넘었다"인지 "아예
없다"인지 알 수 없었다 — `STATUS_ABSENT`를 애써 갈라 놓고도 리포트까지 오면 뭉개졌다.

- `summarise()`에 `absent_features` 추가 → 지표 `absent_watchlist_features`
- 등록부 `ev-features-measured` (max 0, 3거래일, 기한 08-21)
- **`registered`가 미래 날짜(08-12)다.** `evaluate()`는 등록일 이후만 채점하므로 승격 전엔
  조용히 "검증 대기"로 남는다 — 승격 전 며칠을 매일 "재발"로 울리면 늑대소년이 되고,
  그건 이 등록부가 가장 경계하는 실패다. 승격이 늦으면 기한이 잡는다.

### 검증

- 테스트 1456 → **1500건** 전부 통과, ruff 클린, pyright 신규 오류 0.
- 새 회귀 테스트 중 되돌려 실패를 확인한 것: 겹④ 4건 · 스케줄러 1건(1차) ·
  CB 억제 1건(2차, 첫 시도가 결함을 못 잡아 다시 씀).
- 기존 테스트 3건이 옛 의미(`웜업 = OK`)를 고정하고 있어 갱신했다 — 의도("웜업은 장애가
  아니다")는 유지하고 레벨만 바꿨다.

### 적용 시점

전건 **15:35 이후 재기동**으로 적용된다(R11). `minute_bar_close`는 `tick` 기본값이라
1분봉 확정 동작은 오늘도 내일도 종전과 같다 — 바뀌는 것은 관측(지연 분포·합성기 회계·
UNKNOWN)과 가드(학습 전 아카이브 정합)다.

---

## 2026-08-05 장후 점검 — 겹④가 첫날 증명됐고, 절차 자체가 조용히 안 돌았다 ([MW0601], 2026-08-05)

> 테스트 **1508 → 1545건 전부 통과**, ruff 클린.

### 이 세션의 한 문장

오늘 오전에 만든 겹④(마지막 구성봉 대기)가 **같은 날 오후에 증명됐다** — 재기동 전 5시간
34분에 103건, 재기동 후 1시간 21분에 **0건**. 그런데 그것을 고치라고 만든 장후 절차 2단계가
**아무것도 안 하고 성공처럼 끝났다.**

### 하루가 두 코드로 갈렸다

    08:35~14:09  bb60f19 (겹①②③)     → ComposerLateBarDropped 103건
    14:12~15:34  2d61f55 (겹④ 포함)    → 0건

`session_git_shas: ["2d61f55", "bb60f19"]`. 재기동은 의도적이었고(오늘 커밋 적용), 그 대가로
14:10~14:12 2분이 비었다.

### 발견 1 — 시계를 고쳤더니 부호가 뒤집혔다 (예고된 위험이 그대로 왔다)

`bar_composer.py` 모듈 docstring에 어제 이렇게 적어 뒀다: *"부호가 뒤집혀 **로컬이 앞서면**
매 버킷의 마지막 1분봉이 flush 뒤에 도착한다."* 오늘 실측 스큐가 **−0.315초 → −1.106초**로
정확히 그 상태가 됐다(어제는 +9.7초였다). w32time을 고친 직접적 결과다.

그래서 겹②(스큐만큼 대기)만으로는 못 막았다. **수신 지연을 안 세기 때문이다** —
오늘 실측 p50 0.506초 · p99 1.024초 · 최대 1.297초인데 스케줄러 위상은 0.5초다.
즉 정상적인 날에도 매 버킷이 이 경주에서 졌다.

겹④(`_await_last_constituent`)는 시계가 아니라 **데이터 도착**을 기준으로 삼아 원인이
스큐든 지연이든 같이 막는다. 오후 81분 0건이 그 증거다.

Horizon별 유실: 3m 52 · 5m 26 · 10m 12 · 15m 9 · 30m 4. 짧은 Horizon일수록 경계가 잦아
더 많이 맞는다. 재합성 전 상위봉 거래량은 1분봉 대비 **3m 86.8% · 5m 93.0% · 10m 96.8% ·
15m 95.0% · 30m 95.8%**였다.

### 발견 2 (P0) — 장후 절차 2단계가 조용히 아무것도 안 했다

`run_recompose.py`를 문서대로 돌렸더니 `완료 — 0일 / 상위봉 0행`. 원인은 기본값
`--include-today` 미지정 → **오늘 제외**. 그런데 출력에는 "오늘을 제외했다"는 말이 한 줄도
없어서, 성공적으로 재합성한 것과 구분이 안 된다.

"오늘 제외"의 원래 이유(라이브가 조각을 쓰는 중)는 유효하지만 **연속거래가 끝나면 사라진다** —
`run_l1_daily.py`가 15:35에 `_compact_archive()`로 통합하고 종료하기 때문이다.

- 15:35 이후면 **자동 포함**으로 바꿨다. 장중엔 종전대로 제외하되 이유를 출력한다.
- 0일로 끝나면 그 이유를 반드시 찍는다(조용한 무동작 금지).

### 발견 3 (P0) — `px_gap_open`이 장중 재기동 후 영구 NaN

오늘 처음 붙은 피처 건강도 검사가 `1m 피처 1개가 세션 내내 죽어 있었다(px_gap_open)`로
잡았다. 원인: `SessionState.prev_day_close_ticks`는 **일자가 바뀐 봉을 봐야만** 채워진다.
08:35 기동에서는 웜스타트 200봉이 통째로 전일 것이라 저절로 롤오버되지만, 14:12 재기동에서는
최근 200봉이 **전부 오늘 것**이라 경계가 창 안에 없다.

`warm_start(prev_day_close_ticks=...)`로 명시 주입하고, `_previous_day_close()`가 아카이브
에서 직접 읽는다. 웜스타트 봉이 일자를 걸치면 그쪽이 이긴다(실측이 힌트보다 정확).

> 2026-08-04에 "px_gap_open은 **학습에서만** NaN이고 추론에서는 값이 나온다"고 적었는데,
> 재기동한 날에는 추론에서도 NaN이었다. 그 판단이 절반만 맞았다.

### 발견 4 (P1) — 마지막 1분봉은 버스로 **절대** 도달할 수 없다

`DailyCloseBarNotDrained`가 15:35:06에 떴다. 어제 만든 `wait_for_bar`가 제 일을 한 것이지만,
원인을 보니 대기로는 절대 성공할 수 없는 구조였다: `composer.run_forever()`가
`_run_regular_session()`의 `gather` 안에 있고, 15:35에 `asyncio.wait_for`가 그 gather를
통째로 취소한다. 그 뒤의 `flush_final_bar()`는 **아무도 안 듣는 버스**로 발행하는 셈이다.

종료 경로에서만 합성기에 **직접 전달**하도록 했다(`DailyCloseBarHandedOff`, WARNING).
아키텍처 불변 원칙 2는 프로세스 **사이**의 규칙이고 이 둘은 같은 프로세스이며 버스가 이미
내려간 시점이다. 우회한 사실은 매일 로그에 남긴다(R10 "폴백에는 배지를 단다").

### 검증 항목 채점

**E (1차 커밋)**
- E-1 버킷 유실 0 — **부분 통과**: 재기동 전 103건, 후 0건. 온전한 하루 검증은 08-06.
- E-2 장전 옵션체인 스킵 0 — **미검증**: 5건 났지만 전부 08:40~08:45로 **수정 이전 코드**
  구간이다(`_seed_preopen_reference_price`가 bb60f19에 없다). 08-06 08:40이 첫 시험.
- E-3 다리 재시도 — **통과**: 재기동 후 15시대 실패 0건 · 재시도 복구 9건. 총 재시도 25건.

**F (2차 커밋)**
- F-1 delivery_latency p99 — **1.024초**(표본 9,115). 스케줄러 위상 0.5초의 2배다.
- F-2 `l1.composer` 축 — 결선 확인.
- F-3 08:35~08:45 수집기 UNKNOWN — 첫 틱 08:45, 그 전 구간은 웜업으로 표시됨.
- F-4 `AggregatorLateTickDropped` — **0건**. 순서 뒤바뀐 틱은 오늘 없었다.
- F-5 — 재기동으로 구간이 갈려 온전한 하루 관측은 08-06.

### 장후 절차 실행 결과

1. 재합성 전 리포트 보존 → `logs/daily_integrity_20260805_pre_recompose.json`
2. 재합성 → 상위 Horizon 전부 **거래량 119,846으로 일치**(항등식 위반 0)
3. 거래량 대조 **1.000**(아카이브 119,846 / 공식 119,876) · 변동성 축 3 Horizon · 리포트 재산출
4. `unmeasured` **0건**

### 오늘 확정된 두 수치

- **WS 다중 레코드 수정이 실전 검증됐다** — 어제 0.551 → 오늘 **1.000**.
- **체결틱 107,255행** — 결선 후 첫 온전한 하루(어제는 0행).

### 변동성 축은 그대로 0/7

5m +0.387 · 15m +0.327 · 30m +0.095(기준선 IC), 기준선 초과 **전 Horizon 0개**. 어제와 같다.
`ev_*` 둘은 여전히 "피처셋에 없음" — **다음 할 일은 여전히 EV를 켜는 것**이다.

### 남은 것 (미착수, 의식적)

- `minute_bar_close: timer` 승격 — p99를 3~5거래일 모은 뒤. 겹④가 이미 정확성을 확보했으므로
  서두를 이유가 없다. 오늘 p99 1.024초가 첫 표본이다.
- 재기동 시 2분 결손 — 무정지 재기동은 별도 과제.

---

## 2026-08-06 장후 점검 — 호스트가 재부팅됐고, 그 사실을 말할 수 있는 축이 없었다 ([MW0601], 2026-08-06)

> 테스트 **1545 → 1596건 전부 통과**, ruff 클린.

### 이 세션의 한 문장

10:03:49에 PC가 재부팅됐다. 그 사건 하나가 복구·계측 설계를 네 겹으로 뚫었는데, **가장
비싼 손실(옵션체인 약 1,500다리 + 수급 264행 영구 소실)에 대해 리포트는 한 줄도 말하지
않았다** — 그 계열들을 보는 축이 아예 없었기 때문이다.

### 사건 재구성 (Windows 이벤트로그 실측)

```
08:35:23 / 08:36:16  l1_daily / g2_paper 정상 기동, self-check PASS
10:03:49  이벤트 1074 — RuntimeBroker.exe가 재시작 개시, 사유 "기타(계획되지 않음)"
10:04:31  이벤트 13   — OS 종료
10:05:03  이벤트 12   — OS 기동
10:04~10:25  MESSIAH 전면 정지 (21분) — 아무도 안 띄웠다
10:25:31  사람이 수동 재기동
```

`late_bar_drops`는 **0이었다.** 전날 넣은 겹④는 자기 몫을 다 했다(8/5 103건 → 0건).
오늘 무너진 것은 전부 **"프로세스가 죽은 뒤"** 의 이야기다.

---

### [버그] P0-1 — 아카이버 재기동이 그날 오전치를 **파괴**한다

**증상**: `data/flow_intraday/K2I/2026-08-06.parquet`의 첫 행이 **10:26**, 옵션체인 3시리즈의
첫 사이클이 **10:28~10:31**. 08:35~10:03의 111~116분이 통째로 없다. 폴러는 그 시간 내내
정상이었고 `OptionChainPollRetried`가 09:18·09:31·09:43·10:01·10:03에 남아 있다.

**원인**: `InvestorFlowArchiver._flush()`와 `OptionChainArchiver._flush()`가 **메모리에 있는
`self._rows` 전부**를 `pl.DataFrame(...)` → `os.replace()`로 쓴다. 재기동하면 그 메모리가
비어 있으므로, 재기동 후 첫 flush가 오전치를 담은 파일을 갈아엎는다. 버퍼링 여부와 무관한
**쓰기 방식 자체의 결함**이다.

두 모듈의 docstring이 대가를 잘못 적어 두고 있었다:

    flow_archiver     "프로세스가 어느 시점에 죽어도 직전 폴링까지가 온전히 남는다"
                      → 크래시엔 맞고 **재기동엔 틀렸다**
    option_chain      "대가는 마지막 미완 사이클을 잃는 것"
                      → 실제 대가는 **재기동 전 그날 전부**. 세 자릿수 배 차이다

**이건 8/6만이 아니다.** 두 계열의 전체 아카이브가 이틀치인데 이틀 다 잘려 있었다 —
8/5(14:11 재기동)에는 같은 방식으로 **5시간 35분치**가 날아갔고 아무도 몰랐다.

**결정**: 두 겹.

1. **기동 복원** (`_restore_day` / `_restore_series`) — 그날 파일이 있으면 읽어 버퍼를 채우고
   시작한다. 중복 키는 나중 값이 이기는 **기존 규율**이 병합을 이미 정의해 두고 있었다.
2. **축소 쓰기 거부** (`_write_is_safe`) — 디스크 행수 > 메모리 행수면 병합을 재시도하고,
   그래도 줄어들면 **그 쓰기를 건너뛴다**. 새 행은 메모리에 남아 다음 사이클에 다시 나가지만
   지워진 옛 행은 못 돌아온다 — 비대칭이 명확하다.

**Why**: 이 계열들은 **소급 조회가 아예 없다**(장중 수급은 당일 누적만, 옵션 시세는 과거
조회 경로 없음). 봉처럼 백필로 메울 수가 없어서, 다음 재기동 때 또 지워지면 그날도 영구
소실이다. 겹②를 따로 두는 이유는 겹①이 파일 손상·스키마 변경으로 실패할 수 있고, 그때
**조용히 파괴로 되돌아가면 안 되기** 때문이다.

**How to apply**: 하루 1파일을 통째로 교체하는 아카이버를 새로 만들 때는 **읽기-병합-쓰기**가
기본형이다. "메모리에 있는 것만 쓴다"는 재기동이 없는 세계의 가정이고, 이 프로젝트에
그런 세계는 없다.

**검증**: `tests/data/test_flow_archiver.py` 5건 · `test_option_chain_archiver.py` 6건 신설.
실데이터 대조 — 8/6 파일로 재기동을 시뮬레이션해 flow 924→**925행**, 옵션 1302→**1303행**
(종전 결함이면 1행), 컬럼 74/48개와 KST 전부 보존 확인.

---

### [버그] P0-2 — 재부팅 후 자동 복구 장치가 없었다

**증상**: OS가 10:05:03에 올라왔는데 MESSIAH는 10:25까지 안 올라왔다. 1분봉 21개 영구 소실.

**원인**: Task Scheduler 실측 — 트리거가 평일 08:35 하나뿐이고 `RestartCount=0`,
`StartWhenAvailable=False`였다. at-startup 트리거도, 실패 시 재시작도 없었다.

그 설정들은 **손으로 등록돼 있었고 아무 데도 적혀 있지 않았다.** 어떤 값이 왜 그런지 알려면
`Get-ScheduledTask`를 쳐 보는 수밖에 없었고, 그래서 "at-startup이 없다"는 사실을 사고가 난
뒤에야 알았다.

**결정**:

- `scripts/install_scheduled_tasks.ps1` 신설 — 등록 상태를 **코드로** 둔다. 부팅 트리거
  (1분 지연) + 실패 시 1분 간격 3회 재시도 + `StartWhenAvailable` + `MultipleInstances=IgnoreNew`.
  `Messiah-Postmarket`(15:45)도 여기서 등록한다.
- `ops/session_guard.launch_window_verdict()` — 기동 창 08:30~15:35. 부팅 트리거가 붙으면서
  **아무 시각에나** 프로세스가 불릴 수 있게 됐다. 재부팅 복구는 살리고 새벽 재부팅에 하루
  종일 빈 프로세스가 뜨는 것은 막는다. 두 진입점(`run_l1_daily` · `run_g2_paper_trading`)이
  같은 판정을 쓴다.
- `ops/host_health.check_boot_recovery()` — **그 설정이 오늘도 살아 있는가**를 매일 실측한다.

**Why (마지막 항목)**: 설정은 코드가 아니라 OS 상태라 **테스트로 못 잡는다.** 누가 작업을
지우거나 다시 만들면 조용히 08-06 이전으로 돌아가고, 그 사실은 다음 재부팅 때 관측 공백
으로만 드러난다. 이 프로젝트가 반복한 "결선했다고 믿는데 안 붙어 있음"의 OS판이다.

**How to apply**: `Register-ScheduledTask -Force`는 Principal도 새로 쓴다. 기존 값을 읽어
그대로 넘기지 않으면 LogonType/RunLevel이 조용히 바뀌고, **그 사실은 다음 거래일 08:35에
아무것도 안 뜨는 것으로만** 드러난다. 스크립트가 기존 Principal을 재사용하는 이유다.

**함정 (실측)**: PS 5.1은 BOM 없는 `.ps1`을 시스템 ANSI 코드페이지(CP949)로 읽는다 —
한글 주석이 깨진 바이트로 들어가 파서가 죽었다("/c"를 나눗셈 연산자로 읽음).
`run_l1_daily.bat`의 "keep this file ASCII-only"와 같은 계열이고, `.ps1`은 BOM으로 푼다.

**검증**: `tests/ops/test_session_guard.py` 7건 · `test_host_health.py` 5건 신설. 실적용 후
`Get-ScheduledTask` 재조회로 트리거·Principal·작업경로 확인. 백업은
`logs/task_backup_20260806-200601/*.xml`(되돌리기: `schtasks /Create /TN "Messiah" /XML <파일> /F`).

---

### [버그] P0-3a — 겹①~④를 다 통과하는 다섯 번째 구멍: 장중 재기동

**증상**: 5개 Horizon 전부가 버킷을 하나씩 잃었다. 3m 10:03(482계약) · 5m/10m/15m
10:00(2,043) · 30m 10:00은 1,627 ≠ 3,670(재기동 후 5봉만으로 확정).

**원인**: 겹①~④는 전부 "프로세스가 살아 있는 동안"의 경합을 막는다. 프로세스가 죽으면
`_constituents`에 쌓여 있던 1분봉이 메모리와 함께 사라지고, **재기동한 합성기는 아카이브에
남아 있는 그 봉들을 다시 안 읽는다.** 1분봉 10:00~10:03 네 개는 디스크에 멀쩡히 있었다.

**결정**: 겹⑤ `MultiHorizonBarComposer.restore_open_buckets(day)` — 아카이브의 그날 1분봉과
이미 나간 상위 봉을 대조해 안 나간 버킷을 되채우고, **가장 마지막 하나만 열어 둔다.**

**Why (마지막 하나를 안 닫는 이유)**: 재기동 시각이 그 버킷 **안**일 수 있다. 닫아 버리면
짧게 확정되고 뒤이어 도착하는 나머지 분봉이 겹③에 의해 늦은 봉으로 버려진다 — 고치려던
것과 같은 손실이 반대 방향으로 난다. 열어 두면 재기동 시각이 버킷 안이든 밖이든 겹①이
알아서 옳게 처리한다(30m 1,627 → 3,670이 되는 경로가 정확히 이것이다).

**Why (발행 안 하는 이유)**: 복원으로 확정한 봉은 **적재만** 한다. 구독자인 `FeatureEngine`은
같은 아카이브로 `warm_start()`를 따로 하므로, 발행하면 같은 봉이 롤링 윈도에 두 번 들어간다.

**검증**: `tests/data/test_bar_composer.py` 7건 신설 — 오늘 사건(30m 4봉+5봉 합류)을 그대로
재현. 실데이터 복구 후 5개 Horizon 전부 111,451로 1분봉 합과 일치.

---

### [설계결정] P0-3b — 장후 절차를 사람 손에서 뺐다

**증상**: `horizon_findings` 5건 · `unmeasured` 2축. 원인은 재합성과 대조 도구가 안 돌아서다.
**이건 어제 커밋 제목 그대로의 재발이다** — `63724e9 "그것을 쓰라던 절차는 조용히 안 돌았다"`.
그 교훈을 적은 **다음 거래일에 같은 절차가 또 안 돌았다.**

**결정**: 절차를 문서에서 코드로 옮긴다.

- 네트워크를 안 타는 **재합성은 종료 시퀀스 안으로** (`run_l1_daily._recompose_today`).
  순서를 `통합 → 재합성 → 리포트`로 강제한다 — 그래야 `horizon_findings`가 "지금 아카이브가
  정합한가"를 말한다.
- REST를 쓰는 나머지는 **`scripts/run_postmarket.py` + Task Scheduler 15:45**.
  재합성 → 거래량 대조 → 변동성 채점 → **리포트 재생성**(반드시 마지막).

**Why (분리한 이유)**: `verify_archive_volume.py`를 15:35~15:40 종료 예산에 넣으면 종료
절차가 네트워크에 의존한다. 그 판단은 이미 내려져 기록돼 있고(`NEXT_TODO.md` "거래량 외부
대조의 장후 자동화"), 뒤집지 않으면서 사람 손만 뺐다.

**함정 (실측)**: 첫 실행에서 러너가 "1개 단계 실패"라고 찍었는데 리포트는 정상 산출됐다.
이 도구들의 규약은 **exit 1 = "볼 것을 찾았다"**이지 실패가 아니다 —
`daily_integrity_report.py` 머리말에 *"임계 초과 항목이 있으면 1"*이라고 적혀 있다. 임계
초과가 하나라도 있는 날 매일 "실패"로 찍히면 늑대소년이 된다. 종료 코드를 셋(완료 /
발견 있음 ⚠ / 실패 ❌)으로 읽도록 고쳤다.

**검증**: 8/6 실데이터에 적용 — 위반 13→8건, `horizon_findings` 5→**0**, `unmeasured` 2→**0**,
공식 분봉 대비 거래량 **0.997 ✅**(새로 측정). 등록부의 `horizon-volume-identity`와
`daily-axes-measured`가 `재발` → `검증 대기 1/3`로 복귀.

---

### [설계결정] 고도화 2 — 적재 계열 전수 커버리지 (`ops/series_coverage.py`)

**증상**: 위 P0-1의 소실이 **일어나는 동안에도** 리포트가 완벽하게 초록이었다. 리포트가 보는
계열이 봉과 틱뿐이라, 옵션체인·수급은 하루 종일 0행이어도 아무 말이 없다. 이 프로젝트가 같은
형태로 이미 세 번 당했다(`InvestorFlowPoller` 7개월 · `OptionChainPoller` 수개월 · FL 피처).

**결정**: 모든 적재 계열의 **시간 커버리지**를 매일 잰다. 계열 목록은 디렉터리에서 발견한다
(하드코딩하면 새 계열이 붙을 때 리포트가 조용히 그것만 안 본다).

**Why (행수가 아니라 시간)**: 그날 `option_chain/regular`는 1,302행이었다. 행수만 보면 많아
보인다. 첫 사이클이 10:30이었다는 사실은 **시간으로만** 드러난다.

**Why (카덴스를 데이터에서 뽑는 이유)**: 계열마다 폴링 주기가 다르고(수급 60초 · 먼쓰리
300초 · 위클리 600/300초) 그 상수는 `run_l1_daily._option_chain_plan()`에 있다. 복사하면
두 곳이 어긋나는 순간 이 검사가 거짓말을 시작한다. 그래서 그날 데이터의 **사이클 간격
중앙값**을 카덴스로 쓴다 — 8/6 실데이터에서 regular 10분 · weekly_thu 5분 · 수급 1분으로,
그 상수를 한 번도 안 읽고 정확히 맞췄다.

**구현 중 세 번 틀렸고 셋 다 실데이터가 잡았다** (전부 카덴스 추정 문제):

| 틀린 것 | 증상 | 처방 |
|---|---|---|
| 분 간격 중앙값을 카덴스로 | 옵션 42다리가 2~3분에 걸쳐 중앙값을 1분으로 끌어내림 → 한 계열에 헛경고 30건 | 연속된 분을 **사이클로 묶고** 사이클 시작 간격으로 |
| 사이클 2개로 중앙값 추정 | 그 간격 자신이 기준이 되어 틱의 22분 재부팅 공백을 **못 잡음** | 사이클 5개 미만이면 추정 안 함(연속 계열로) |
| 긴 블록을 사이클로 오인 | 끊긴 연속 계열의 구멍이 "정상 주기"로 흡수 | 사이클 길이가 간격의 절반 초과면 연속 계열 |

**정직한 한계**: 하루 종일 **고르게** 절반이 빠지면 중앙값이 2배가 되어 아무것도 안 걸린다.
이 축이 겨냥하는 것은 **연속된 구멍**이다 — 8/5(5시간 35분)·8/6(1시간 55분)이 전부 그
모양이었고, 재기동·크래시·결선 끊김은 원래 그 모양으로 난다.

**How to apply**: 정상인 계열도 리포트에 한 줄씩 남긴다. "검사했는데 이상 없다"와 "그 계열을
아예 안 본다"가 구분돼야 하고, 후자가 8/6의 상태였다.

**검증**: `tests/ops/test_series_coverage.py` 13건 · `test_integrity_report.py` 3건(결선 회귀).
8/6 실데이터에서 **사고 5건만 정확히 판정, 헛경고 0건**:

```
flow_intraday/K2I        머리 111분 ⚠     option_chain/regular    머리 115분 ⚠
option_chain/weekly_mon  머리 116분 ⚠     option_chain/weekly_thu 머리 113분 ⚠
ticks                    10:03~10:25 22분 구멍 ⚠
```

---

### [설계결정] P0-1·P0-2를 등록부가 채점하게 했다

이 커밋 전까지 두 수정은 **사람 기억이 판정**하는 상태였다 — 5거래일 동안 세 번 틀렸던 자리와
정확히 같은 형태다. 커버리지 축과 부팅 무장 검사가 생기면서 지표가 생겼다:

```
archiver-restart-restore  series_gap_findings ≤ 0    5거래일  기한 08-20
boot-recovery-armed       boot_recovery_armed ≥ 1    3거래일  기한 08-14
```

`archiver-restart-restore`를 3일이 아닌 **5거래일**로 잡은 이유: 장중 재기동이 매일 나지
않으므로 짧게 잡으면 "재기동이 없었던 날"만 세고 통과한다.

**두 지표 다 조용한 날을 증거로 삼을 수 없다**(등록부 주석에도 명기). `series_gap_findings=0`은
재기동이 없던 날에도 0이고, `boot_recovery_armed=1`은 **무장 여부**이지 복구가 동작한다는
증명이 아니다 — 후자는 장중 재부팅이 한 번 나야 알 수 있고 그때 `longest_gap_minutes`가
답한다. 그럼에도 등록하는 이유는, 이 축이 없던 8/5·8/6에는 소실이 일어나는 중에도 리포트가
초록이었기 때문이다. **조용함을 증거로 착각하지 않되, 시끄러움은 반드시 잡히게 해 둔다.**

---

### 사전 등록 체크리스트 답변 (E-1~F-5)

| 항목 | 결과 |
|---|---|
| **E-1** `late_bar_drops == 0` | ✅ **0** (8/5 103건 → 0). 겹④가 첫 정식 검증 통과 |
| **E-2** `horizon_findings` 빔 | ❌ 5건 → 재합성 후 0. E-1과 어긋난 원인은 종료 경합이 아니라 **재부팅**이었다(예상 목록에 없던 네 번째) |
| **E-3** 장전 옵션체인 | ❌ `OptionChainSkipped` 0건이나 첫 스냅샷이 10:30 — P0-1로 파괴됨 |
| **E-4** 재시도가 다리를 살렸나 | ✅ regular 1302 = 31×42, weekly_thu 2604 = 62×42, weekly_mon 1302 = 31×42. 재기동 후 구간은 100% 완전 |
| **E-5** 호스트 cpu 기록 | ✅ |
| **E-6** `SchedulerTickMissed`·중복 | ✅ 0건 |
| **F-1** `delivery_latency` | ✅ p50 0.512 / p90 0.931 / p99 **1.032** / max 1.130초 (표본 20,000) — timer 승격 2일차 표본 |
| **F-2** `l1.composer` 축 | ✅ `OK · 합성봉 227개 · 거래량 항등식 일치` |
| **F-3** collector 08:35~08:45 UNKNOWN | ❓ 확인 불가 — 그 구간 상태판 스냅샷이 안 남는다(15:34분 것만 존재) |
| **F-4** `AggregatorLateTickDropped` | ✅ 0건 |
| **F-5** `전제 붕괴` | ✅ 없음 (p99 1.03초 ≪ 3.0초) |

**요약**: 전날 넣은 수정은 전부 자기 몫을 했다. 오늘 리포트가 빨간 이유는 새 결함이 아니라
**호스트 재부팅 + 그 뒤를 받쳐 줄 복구·계측의 부재**였다.

---

### 남은 것 — `no-degenerate-features`는 여전히 재발이고, 원인은 오탐이다

10건 중 4건이 `px_gap_open`이다. 정의가 `log(당일 시가 / 전일 종가)`라 **장중에 변할 수가
없다** — 정의상 상수인데 `_FeatureStat.constant`(`lo == hi`)가 "죽었다"로 잡는다. 매일,
영원히. `px_ema_cross_*`(값역 {-1,0,+1})와 `px_breakout_*`(대부분 0.0)도 성격이 같고,
특히 10m은 표본이 31개뿐이라 저기수 피처가 안 변하는 것이 정상 범위다.

`max: 0`을 요구하는 한 이 항목은 **구조적으로 통과 불가**다. 처방은 피처 레지스트리에
"정의상 상수" / "저기수" 표지를 두고 판정에서 가르는 것 — 다음 세션 P2로 남긴다.
