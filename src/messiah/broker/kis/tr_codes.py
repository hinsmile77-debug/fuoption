"""KIS OpenAPI 엔드포인트·TR ID 상수 — 한국투자증권 공식 API 문서 기준.

마흐디(선행 옵션 프로젝트) mahdi/broker/tr_codes.py에서 2026-07-21 이식. 원본은
docs/efriend/한국투자증권_오픈API_전체문서_20260705_030000.xlsx(시트 "API 목록" +
"선물옵션 주문"/"선물옵션 시세"/"선물옵션 시세호가"/"지수옵션 실시간체결가"/"지수옵션 실시간호가")로
2026-07-06 검증 완료. 값이 바뀌면 이 파일만 고치면 되도록 단일 소스로 유지한다.
"""

from __future__ import annotations

REAL_REST_DOMAIN = "https://openapi.koreainvestment.com:9443"
VPS_REST_DOMAIN = "https://openapivts.koreainvestment.com:29443"  # 모의투자

# 실시간(WS) 도메인: 계좌 종류(모의/실전)가 아니라 "무엇을 구독하는가"에 따라 달라진다.
#  - 시세(시장 데이터: 체결가/호가 등)는 계좌와 무관한 공개 데이터라 모의투자 전용 도메인이
#    아예 없다 — 지수옵션 실시간체결가(H0IOCNT0)/실시간호가(H0IOASP0)는 "모의 TR_ID: 모의투자
#    미지원", "모의 Domain: 모의투자 미지원"으로 명시되어 있어, 모의투자 계좌로 운용 중이어도
#    시세 구독은 REAL_WS_DOMAIN 하나로만 접속한다.
#  - 반대로 "주문체결통보"(내 주문의 체결 알림)는 계좌를 특정해야 하므로 모의/실전이 TR_ID와
#    도메인 모두 분리되어 있다 (예: 선물옵션 실시간체결통보 H0IFCNI0(실전)/H0IFCNI9(모의),
#    모의 Domain: ws://ops.koreainvestment.com:31000). 시세 구독만 다루는 동안은
#    MARKET_DATA_WS_DOMAIN을 쓰고, 체결통보 연동에서만 REAL_WS_DOMAIN/VPS_WS_DOMAIN을
#    is_mock으로 분기해서 쓴다.
REAL_WS_DOMAIN = "ws://ops.koreainvestment.com:21000"
VPS_WS_DOMAIN = "ws://ops.koreainvestment.com:31000"  # 계좌별 체결통보 전용
MARKET_DATA_WS_DOMAIN = (
    REAL_WS_DOMAIN  # 시세 구독은 항상 이 도메인 (모의투자 전용 시세 도메인 없음)
)

PATH_TOKEN = "/oauth2/tokenP"
PATH_TOKEN_REVOKE = "/oauth2/revokeP"
PATH_WS_APPROVAL = "/oauth2/Approval"

PATH_FUTUREOPTION_ORDER = "/uapi/domestic-futureoption/v1/trading/order"
PATH_FUTUREOPTION_ORDER_MODIFY_CANCEL = "/uapi/domestic-futureoption/v1/trading/order-rvsecncl"
PATH_FUTUREOPTION_BALANCE = "/uapi/domestic-futureoption/v1/trading/inquire-balance"

# 단일 종목 시세/시세호가 — 실전·모의 겸용. "국내옵션전광판_*"(display-board-*) 계열은 모의투자
# 미지원이라 모의투자 단계에서는 쓸 수 없다. 전 종목 체인을 한 번에 받는 REST는 모의투자에
# 존재하지 않으므로, 스트라이크별로 이 엔드포인트를 반복 호출하거나(종목코드 마스터파일 필요,
# github.com/koreainvestment/open-trading-api/tree/main/stocks_info 참고) WS 실시간체결가로
# 체인을 구성해야 한다 — 아직 미해결 과제로 남겨둔다.
PATH_FUTUREOPTION_QUOTE = "/uapi/domestic-futureoption/v1/quotations/inquire-price"
PATH_FUTUREOPTION_ASKING_PRICE = "/uapi/domestic-futureoption/v1/quotations/inquire-asking-price"

# 선물옵션 차트 — 과거 봉 백필의 유일한 경로 (2026-08-04 신설·실측).
#
# 이 두 엔드포인트가 이 프로젝트에 없었기 때문에, 2026-08-03까지 "다개월 시계열은 매일 수집해서
# 쌓는 수밖에 없다"고 알고 있었고 G1 walk-forward 관문(train 180일+test 30일)의 도달 예정일이
# 2027-02-20이었다. 실측으로 그게 틀렸음이 확인됐다 — 만기된 월물의 분봉까지 남아 있어
# 월물 체인을 거슬러 접합하면 2025-12-12까지 소급된다(`data/backfill.py` 모듈 docstring).
#
# 2026-08-04 실측(모의투자 앱키 60046651, A05608):
#   - VPS/REAL 도메인 **둘 다** 200 OK. 미니선물(상품종류 B)도 FID_COND_MRKT_DIV_CODE="F"로 조회됨
#     (F는 "지수선물"이지 "정규선물"이 아니다 — 미니/정규 공통).
#   - 분봉: 1회 호출당 **최대 102건**. `FID_PW_DATA_INCU_YN="Y"`면 날짜 경계를 넘어 과거로
#     이어진다. 2026-08-03 하루치(410봉, 08:45~15:34)를 커서 5회로 결손 없이 완주 확인.
#   - 일봉: 1회 호출당 **최대 100건**.
PATH_FUTUREOPTION_TIME_CHART = (
    "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
)
PATH_FUTUREOPTION_DAILY_CHART = (
    "/uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice"
)
TR_FUTUREOPTION_TIME_CHART = "FHKIF03020200"  # 선물옵션 분봉조회
TR_FUTUREOPTION_DAILY_CHART = "FHKIF03020100"  # 선물옵션기간별시세(일/주/월/년)

# FID_HOUR_CLS_CODE (분봉조회 전용) — 30=30초봉, 60=1분봉. 둘 다 2026-08-04 실측 동작 확인.
FID_HOUR_CLS_30_SECONDS = "30"
FID_HOUR_CLS_1_MINUTE = "60"

# 분봉조회 1회 응답 상한(실측). 커서를 옮겨가며 페이징할 때 "더 받을 게 남았는가"를 이
# 값으로 판단하지 않는다 — 실제 판단은 "새로 받은 행이 있는가"로 한다(같은 커서에서
# 같은 행만 계속 오는 경우가 종료 조건).
TIME_CHART_MAX_ROWS_PER_CALL = 102
DAILY_CHART_MAX_ROWS_PER_CALL = 100

# 시장별 투자자매매동향(시세) — "모의 TR_ID/Domain: 모의투자 미지원"이지만 계좌 무관 공개 시세성
# 데이터라, 시세 WS와 같은 이유로 모의투자 앱키로도 REAL_REST_DOMAIN 호출이 그대로 성공한다
# (2026-07-06 실측 확인, 200 OK). 그래서 실전/모의 분기 없이 TR ID 하나만 둔다.
PATH_INVESTOR_FLOW_BY_MARKET = "/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market"
TR_INVESTOR_FLOW_BY_MARKET = "FHPTJ04030000"

# 시장별 투자자매매동향(**일별**) — FL Feature의 유일한 백필 경로 (2026-08-04 신설·실측).
#
# 위 장중 엔드포인트(K2I/F001)는 **당일 누적만** 준다 — 과거 조회가 없다. 게다가
# `InvestorFlowPoller`는 어떤 스크립트에도 결선돼 있지 않고 `raw.investor_flow.*` 구독자도
# 없어서, 이 프로젝트는 파생 수급을 **한 건도 수집한 적이 없다**. 즉 파생 장중 수급으로는
# 백테스트가 불가능하고, 앞으로 몇 달을 모아야 쓸 수 있다.
#
# 이 일별 엔드포인트는 다르다 — 날짜를 커서로 과거로 페이징된다. 대신 **현물 시장 전용**이다:
#
#   2026-08-04 실측 (같은 날 20260703, 업종코드만 바꿔 비교):
#     FID_INPUT_ISCD_2="0001"(KOSPI) → frgn_ntby_qty=1057  prsn=-15031  orgn=13846
#     FID_INPUT_ISCD_2="F001"(선물)  → **전 항목 0** (미지원)
#     FID_INPUT_ISCD_2="OC01"(콜옵션) → **전 항목 0** (미지원)
#
#   페이징: 1회 300행, `FID_INPUT_DATE_1`이 뒤로 가는 커서다(종료일자는 무시되는 것으로 보임).
#     커서 20260804 → 20250514~20260804,  20250408 → 20240112~20250408 … 다년 소급 확인.
#
# 그래서 FL Feature는 "KOSPI 현물 일별 수급"에서 나온다 — 파생 장중 수급이 아니다.
# **일 단위**라 하루 안에서는 값이 고정된다(5분봉 78개가 전부 같은 값) — 일중 타이밍
# 정보는 주지 못하고 그날의 방향성 기울기만 준다는 뜻이며, 이걸 모르고 쓰면 성능을
# 오해하게 된다.
PATH_INVESTOR_FLOW_DAILY_BY_MARKET = (
    "/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market"
)
TR_INVESTOR_FLOW_DAILY_BY_MARKET = "FHPTJ04040000"
INVESTOR_FLOW_DAILY_MAX_ROWS_PER_CALL = 300  # 실측

# 일별 엔드포인트 전용 파라미터 값 (실측).
FID_MRKT_DIV_SECTOR = "U"  # FID_COND_MRKT_DIV_CODE — 업종
FID_INPUT_ISCD_KOSPI = "0001"  # 업종코드: KOSPI 종합
FID_INPUT_ISCD_1_KOSPI = "KSP"  # 시장구분: KOSPI

# FID_INPUT_ISCD=K2I(선물/콜옵션/풋옵션 통합 시장구분)일 때 FID_INPUT_ISCD_2(업종구분) 값
FID_INVESTOR_FLOW_FUTURES = "F001"
FID_INVESTOR_FLOW_CALL_OPTION = "OC01"
FID_INVESTOR_FLOW_PUT_OPTION = "OP01"
FID_MRKT_DIV_DERIVATIVES = "K2I"

# 주문 TR ID (실전 T / 모의 V 접두 관례) — "선물옵션 주문" 시트 실측
TR_ORDER_NEW = {"real": "TTTO1101U", "vps": "VTTO1101U"}
TR_ORDER_MODIFY_CANCEL = {"real": "TTTO1103U", "vps": "VTTO1103U"}
TR_BALANCE_INQUIRY = {"real": "CTFO6118R", "vps": "VTFO6118R"}
TR_OPTION_QUOTE = {"real": "FHMIF10000000", "vps": "FHMIF10000000"}
TR_OPTION_ASKING_PRICE = {"real": "FHMIF10010000", "vps": "FHMIF10010000"}

# FID_COND_MRKT_DIV_CODE (선물옵션 시세/시세호가 공통 쿼리 파라미터)
FID_MRKT_DIV_INDEX_FUTURES = "F"  # 지수선물
FID_MRKT_DIV_INDEX_OPTION = "O"  # 지수옵션

# 실시간(WebSocket) TR ID — 계좌 무관, MARKET_DATA_WS_DOMAIN 하나로 접속
WS_TR_OPTION_CONTRACT = "H0IOCNT0"  # 지수옵션 실시간체결가
WS_TR_OPTION_ORDERBOOK = "H0IOASP0"  # 지수옵션 실시간호가
WS_TR_FUTURES_CONTRACT = "H0IFCNT0"  # 지수선물 실시간체결가 — "모의 미지원" 문서에도
# 옵션과 같은 이유로 실전 도메인 구독 가능

# 선물옵션 실시간체결통보 (계좌별 주문체결 알림 — 모의/실전 TR_ID·도메인 모두 분리)
WS_TR_ORDER_NOTICE = {"real": "H0IFCNI0", "vps": "H0IFCNI9"}

# Cross-asset stress 피처 — VIX 기간구조·USDCNH는 해외선물옵션 도메인(CME/CBOE/HKEx 상장 선물)으로
# 얻는다. 2026-07-10 모의투자 앱키로 실측: VX(CBOE)·CNH(HKEx)는 계좌 무관 즉시 조회 성공(HTTP 200)
# 했지만, ZN(CME/CBOT, US10Y 대용)은 "EGW00552: CBOT SUB거래소 신청 계좌가 아닙니다"로 거부됨 —
# CBOT 상장 상품만 별도 거래소 신청이 계좌에 걸려 있어야 한다(코드 문제 아님, KIS 앱/HTS에서
# 해외선물옵션 CBOT 거래소 신청 필요). REAL 도메인은 모의 앱키로 호출 시 "EGW02004: 실전투자
# 도메인은 모의투자 앱키로 호출하실 수 없습니다"로 거부되어(투자자매매동향과 달리 이 도메인은
# 계좌 종류를 엄격히 분리) is_mock에 따라 VPS_REST_DOMAIN/REAL_REST_DOMAIN을 그대로 따라간다.
PATH_OVERSEAS_FUTUREOPTION_PRICE = "/uapi/overseas-futureoption/v1/quotations/inquire-price"
TR_OVERSEAS_FUTUREOPTION_PRICE = "HHDFC55010000"

OVERSEAS_FUTURE_PRODUCT_VIX = "VX"  # CBOE VIX 선물 — VIX 기간구조(근월-차근월 스프레드)
OVERSEAS_FUTURE_PRODUCT_CNH = "CNH"  # HKEx USD/CNH 선물 — USDCNH 대용
OVERSEAS_FUTURE_PRODUCT_ZN = "ZN"  # CME/CBOT 10년 국채선물 — US10Y 급변 감지용. CME|CBOT는 KIS
# 유료 항목(월 228.8불)이라 모의투자 개발 단계에서는 미구독 — yfinance 등 대체 경로 필요.
OVERSEAS_FUTURE_PRODUCT_ES = "ES"  # CME E-mini S&P500 선물 — ES/거래소코드 CME(ZN의 CBOT와 별개
# 서브거래소지만 HTS상 "CME|CME"도 동일하게 유료) — 미구독, 대체 경로 필요.

# 해외주식 종목_지수_환율기간별시세(일_주_월_년) — US10Y는 CBOT 미구독 계좌에서도 이 경로
# (국채구분 I)로 "일봉"만 얻을 수 있다(2026-07-10 실측: 같은 API의 분봉 엔드포인트는 I구분에서
# "ERROR INVALID FID_COND_MRKT_DIV_CODE"로 거부되어 분봉은 미지원 확정). 환율구분(X)도 같은
# 엔드포인트로 무료 조회됨을 확인 — USDKRW는 CBOT 같은 계좌 게이트 자체가 없다(해외선물옵션
# 도메인이 아니라 해외주식 도메인이라 SUB거래소 신청 무관).
PATH_OVERSEAS_INDEX_DAILY_CHARTPRICE = "/uapi/overseas-price/v1/quotations/inquire-daily-chartprice"
TR_OVERSEAS_INDEX_DAILY_CHARTPRICE = "FHKST03030100"
FID_MRKT_DIV_OVERSEAS_TREASURY = "I"  # 국채(수익률) 구분 — 심볼(예: Y0202=US10Y)과 짝
FID_INPUT_ISCD_US10Y = "Y0202"  # "US T-Note 10 Years(Y)"
FID_MRKT_DIV_OVERSEAS_FX = "X"  # 환율 구분 — 심볼(예: FX@KRW=USDKRW)과 짝
FID_INPUT_ISCD_USDKRW = "FX@KRW"  # "대한민국 원/달러(KMB)"
