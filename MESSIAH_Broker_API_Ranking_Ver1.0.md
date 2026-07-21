# MESSIAH — 국내 증권사 API 랭킹 및 Top 3 추천

## Ver 1.0 / Date: 2026-07-21

### 조사 방법: 자동매매 커뮤니티·전문 블로그(알고랩 400건 실측 비교 등)·공식 개발자 포털·GitHub 생태계·선행 프로젝트(미륵이/마흐디) 실전 기록 종합

---

# 1. 평가 기준 — 메시아 요건 기반 가중치

메시아의 확정 설계에서 도출한 7개 기준. **일반적 인기 순위가 아니라 "메시아를 가장 훌륭히 구현할 수 있는가"의 순위**다.

| # | 기준 | 가중치 | 근거 설계 |
|---|---|---|---|
| C1 | 파생상품 API 완전성 (미니선물·옵션·위클리 월/목·Greeks·야간) | 25 | 미니선물 표준(Holding Policy), 위클리 2북(L19) |
| C2 | 아키텍처 적합성 (REST+WS, 64bit Python 3.11+, 무 HTS 상주, 크로스플랫폼) | 20 | 프로세스 분리·Docker (Ver 1.1) |
| C3 | 멀티 PC 복제 배포 (OAuth 다중 실행, 인스턴스별 계좌, 지정단말 제약 없음) | 15 | N대 독자 거래 (Ver 1.1 §7) |
| C4 | 모의투자 환경 (실전과 동일 구조) | 10 | G2 페이퍼 40거래일 관문 (Ver 2.0 §8) |
| C5 | 레이트리밋·실시간 한도 (호가·체결 스트림 충분성) | 10 | Hot Path 50ms, L20 교훈 |
| C6 | 생태계 (문서·커뮤니티·GitHub·한국어 자료) | 10 | 디버깅 속도 |
| C7 | 선행 자산 재사용 (미륵이·마흐디 검증 코드 이식성) | 10 | 마흐디 KIS 계층 실측 완료 |

---

# 2. 종합 랭킹

| 순위 | API | C1 파생 | C2 아키 | C3 멀티PC | C4 모의 | C5 한도 | C6 생태 | C7 재사용 | **총점** |
|---|---|---|---|---|---|---|---|---|---|
| **1** | **한국투자 KIS Developers** (REST+WS) | 22 | 19 | 14 | 9 | 7 | 8 | 10 | **89** |
| **2** | **LS증권 신 OpenAPI** (REST+WS) | 23 | 17 | 12 | 6 | 6 | 5 | 3 | **72** |
| **3** | **대신 CybosPlus (CREON)** (COM) | 22 | 6 | 4 | 5 | 6 | 6 | 7 | **56** |
| 4 | 키움 구 OpenAPI+ (OCX) | 18 | 5 | 4 | 7 | 6 | 10 | 7 | 57* |
| 5 | 키움 신 REST API | 0† | 18 | 12 | 8 | 7 | 8 | 2 | 55 |
| 6 | LS 구 xingAPI (COM) | 21 | 5 | 4 | 5 | 5 | 6 | 2 | 48 |
| 7 | NH·신한·미래에셋·삼성 등 | — | — | — | — | — | — | — | 평가 제외 |

\* 키움 구 OCX는 총점이 대신과 비슷하나 32bit·단일 실행 제약이 동일해 순위 하향 (파생 커버리지가 대신보다 좁음)
† **키움 신 REST는 현재 국내주식만 거래 가능 — 선물옵션 미지원이 결정적 결격.** 파생 확장 발표 시 재평가 1순위
NH·신한 등: 커뮤니티·문서·모의환경 미흡으로 자동매매 채택률이 낮아(전문 업체 의뢰의 95%가 상위 4사 집중) 상세 평가에서 제외

참고: 2025-06-09부터 **KRX 야간 파생시장이 도입**(EUREX 연계 종료)되어, 야간 거래도 주요 증권사 정규 API 체계로 편입되는 흐름 — "야간=대신/LS COM"이라는 과거 공식은 약화 중.

---

# 3. Top 3 상세

## 🥇 1위 — 한국투자증권 KIS Developers (주 브로커 확정 권고)

**메시아와의 정합성이 압도적이다.**

- **C2·C3 만점급**: 순수 REST+WebSocket, OAuth 2.0, 64bit·크로스플랫폼(Linux/Docker 가능), HTS 상주 불요, 한 계정 다중 봇 동시 운영 — 메시아의 "N대 PC 복제 배포·독자 거래"(Ver 1.1 §7)를 제약 없이 수용하는 유일 3사 중 최상
- **C1 검증됨**: 국내선물옵션 공식 샘플 제공(공식 GitHub `koreainvestment/open-trading-api`), get_quote 응답에 Greeks/IV/OI 포함 — **마흐디가 이미 실측 검증** (TR ID·필드 순서·위클리 2북·레이트리밋 실측표 보유)
- **C4 강점**: 모의투자가 실전과 동일 구조 — G2 페이퍼 관문(Ver 2.0 §8)에 그대로 사용
- **C7 만점**: 마흐디의 `broker/` 계층(token_daemon·ws_client·rest_client·order_state_machine)과 `KIS_RAW_FIELD_RANGES.md`가 곧바로 이식 가능 — **개발 수 주 단축**
- **생태계**: 공식 GitHub 샘플 + python-kis·pykis·mojito 등 서드파티 라이브러리 활발

**약점과 대응** (마흐디 실측 기반):
- WS 구독 슬롯 41건 한도 → 3북 × ATM±2 롤링 구독 전략 검증 완료(마흐디 방식 계승)
- 모의투자 레이트리밋 실측 1건/초(문서와 상이) → 공유 리미터 + 적응형 백오프(L20) 필수
- 모의투자에서 옵션체인 REST 제약 등 모의≠실전 차이 존재 → 환경 차이 매트릭스(L26)로 관리
- 옵션 체인 폴링은 REST 의존 → 절대시각 고정 틱 스케줄링(L20) 전제

## 🥈 2위 — LS증권 신 OpenAPI (부 브로커·이중화 권고)

- **파생 전통 강자**: KOSPI200 옵션·선물옵션 자동매매에 전통적으로 가장 널리 쓰인 계열 — 호가 10단계 풀·체결강도·시장지표가 풍부해 C1 점수는 KIS보다 오히려 높음
- 신 OpenAPI는 REST+WS·OAuth·64bit·크로스플랫폼 — 메시아 아키텍처 수용 가능
- 개발자 콘솔·테스트베드 제공, 실시간 뉴스 API는 향후 이벤트 인텔리전스(Ver 1.0.1 §2.5) 확장에 유용
- **약점**: 커뮤니티·한국어 자료가 KIS·키움 대비 얇음, TR별 레이트리밋 보수적(예: 초당 2회 계열), 신 API 성숙도는 실측 검증 필요, 선행 자산 재사용 없음(어댑터 신규 개발)

## 🥉 3위 — 대신증권 CybosPlus/CREON (야간·데이터 보조, 조건부)

- **파생 데이터 깊이·안정 시세는 여전히 최상급**, 야간 파생의 전통 강자 — 미륵이(MW0601이 Cybos)로 코드·노하우 보유
- **그러나 메시아 주력으로는 부적합**: COM·32bit Python·HTS 상시 실행·1계정 1실행 — 프로세스 분리·Docker·복제 배포와 정면 충돌. 미륵이가 겪은 COM STA 데드락·세션만료 access violation(L9)이 그 실증
- **권고 역할**: 주력이 아니라 **데이터 교차검증·야간 세션 보조·비상 수동 창구** — Ver 3.0(다자산·야간 확장) 시점에 재평가

---

# 4. 최종 권고 — 이중 브로커 전략

```
[주 브로커] KIS Developers          [부 브로커] LS 신 OpenAPI
  실행 + 실시간 데이터 + 모의 G2        어댑터만 선구현(주문 제외)
  마흐디 계층 이식                      데이터 교차검증 · 유사시 대체
        └────── Ver 1.1 §5-2 Broker Adapter 동일 인터페이스 ──────┘
```

1. **W3~5(데이터 수집 단계)부터 KIS로 확정 착수** — 마흐디 자산 이식으로 리스크 최소
2. Broker Adapter 인터페이스(Ver 1.1 §5-2) 덕에 브로커는 교체 가능 부품 — **LS 어댑터를 G3(소액 실전) 진입 전까지 데이터 전용으로 병행 구축**해 단절 리스크(R11)와 데이터 품질 교차검증을 동시 해결
3. 키움 신 REST의 **선물옵션 확장 발표를 분기회의 관찰 항목**으로 등록 — 지원 시 생태계 1위(자료·커뮤니티)와 REST 아키텍처가 결합되므로 재평가 가치 큼
4. Capability Matrix(L9·L19·L26)를 KIS·LS 각각 작성 — "구현됨 vs 실측 검증됨 vs 모의/실전 차이"를 착수 첫 주부터 기록

**한 줄 결론**: 메시아의 주 브로커는 **한국투자증권 KIS Developers** — 아키텍처 정합성·파생 지원·마흐디 실측 자산의 삼박자가 유일하게 갖춰진 선택이고, LS 신 OpenAPI로 이중화하며, 대신 CREON은 야간·보조로 남긴다.

---

# Sources

- [알고랩 — 증권사 자동매매 API 비교 2026 (400건 실측)](https://algolab.co.kr/blog/kr-broker-api-comparison)
- [KIS Developers 공식 포털](https://apiportal.koreainvestment.com/intro) · [KIS 공식 GitHub (국내선물옵션 샘플 포함)](https://github.com/koreainvestment/open-trading-api)
- [키움 REST API 포털 (현재 국내주식 중심)](https://openapi.kiwoom.com/) · [알고랩 — 키움 REST API 가이드 2026](https://algolab.co.kr/blog/kiwoom-rest-api-algotrading-guide-2026)
- [LS증권 OPEN API 포털](https://openapi.ls-sec.co.kr/about-openapi) · [LS증권 xingAPI 안내](https://ls-sec.co.kr/xingapi/xingMain.jsp?front_menu_no=1293&front_menu_no=603&left_menu_no=360&left_menu_no=360&parent_menu_no=360&parent_menu_no=603)
- [한국투자증권 — KRX 야간거래 도입 및 EUREX 연계 종료 공지](https://m.koreainvestment.com/main/customer/notice/Notice.jsp?cmd=TF04ga000002&num=44720)
- [python-kis (KIS REST 라이브러리)](https://github.com/Soju06/python-kis) · [pykis](https://github.com/pjueon/pykis) · [mojito](https://www.gitdetail.com/repositories/sharebook-kr/mojito/646568)
- [TG's Blog — KIS API 초당 20건 제한 해결](https://tgparkk.github.io/robotrader/2025/10/09/robotrader-1-70stocks-problem.html)
- 선행 프로젝트 실전 기록: 미륵이(hinsmile77-debug/futures — Kiwoom·Cybos COM 실증), 마흐디(hinsmile77-debug/options — KIS 실측)
