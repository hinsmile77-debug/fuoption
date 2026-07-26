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
**라이브 미검증** (검증 기한: 2026-07-24, Redis 기동 후 self_check로 확인).

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

## 2026-07-27 (4차 — Triple Barrier·uniqueness·Walk-Forward/Purged CV, Ver 2.0 §9 W12~13)

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

## 2026-07-27 (5차 — Cost Model v1·Validator 골격·5m Expert 프로토타입 1호, Ver 2.0 §9 W14~16)

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
