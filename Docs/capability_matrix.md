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
| KISBrokerAdapter.probe_front_month | ❌ | — | — | NotImplementedError — 종목코드 마스터파일 미연동 |
| WS 시세 구독 (ws_client) | ✅ | — | — | 포트만 완료, 실측 안 됨 |
| WS 주문체결통보 | ✅ | — | — | 포트만 완료, 실측 안 됨 |

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
