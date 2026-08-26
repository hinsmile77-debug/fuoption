"""KIS 선물옵션 주문체결통보(H0IFCNI0/H0IFCNI9) 수신 — 구독·복호·파싱.

이 프로젝트는 2026-07-21부터 `tr_codes.WS_TR_ORDER_NOTICE`에 이 TR ID를 들고 있었지만
**읽는 코드가 한 줄도 없었다.** 즉 "내 주문이 체결됐다"는 사실을 브로커가 밀어주는 경로가
정의만 되어 있고 배선은 없었다 — 체결 확인은 잔고 폴링(`adapter.positions()`)뿐이었다.

시세 구독(`data/collector.py`)과 **세 가지가 다르다.** 셋 다 조용히 틀리기 쉬운 자리다:

1. **도메인이 다르다.** 시세는 계좌 무관 공개 데이터라 모의계좌로 운용해도 실전 도메인
   하나(`MARKET_DATA_WS_DOMAIN`)로 붙는다. 체결통보는 계좌별이라 모의/실전 도메인이
   갈린다(`tr_codes.order_notice_ws_domain()`). 시세 코드를 복사해오면 모의계좌가
   실전 도메인에 붙어 통보가 영영 안 온다.
2. **tr_key가 종목코드가 아니라 HTS 로그인 ID다.** 계좌번호도 아니다(KIS 공식 샘플
   `examples_llm/domestic_futureoption/fuopt_ccnl_notice/fuopt_ccnl_notice.py`:
   `tr_key (str): [필수] 코드 (ex. dttest11)`).
3. **본문이 AES-CBC로 암호화되어 온다.** 시세는 `encrypt="N"`이라 파이프 문자열을 그대로
   읽었는데, 체결통보는 `encrypt="Y"`다. 복호 키(iv/key)는 **구독 성공 응답 본문에만**
   한 번 실려 오고 그 뒤로는 다시 오지 않는다 — 그 한 줄을 흘리면 이후 도착하는 모든
   통보가 해독 불가 쓰레기가 된다. `ws_client.listen()`의 docstring이 2026-07-22 실측
   당시 "iv/key는 체결통보 등 다른 TR의 복호화용으로 보이며 이 TR에서는 안 씀"이라고
   적어둔 그 필드가 여기서 처음 쓰인다.

**수신 루프를 `KISWebSocketClient.listen()`에 맡기지 않고 직접 돈다.** 이유는 PINGPONG이다.
`listen()`은 프레임을 dict로 파싱해서 넘기므로 원문이 사라지는데, 체결통보 세션은 **주문이
없는 동안 몇 시간씩 완전히 조용하다** — 시세 세션과 달리 서버 핑에 응답하지 않으면 끊긴다.
시세 수집기가 지금까지 PINGPONG 없이 버틴 건 장중 내내 틱이 흘러 핑을 받을 일이 없어서지,
안 해도 되기 때문이 아니다. 구독 봉투 조립만 `KISWebSocketClient`를 재사용한다.

"구현됨 ≠ 검증됨"(`broker/base.py` 원칙): **필드 22개의 이름과 순서는 위 공식 샘플의
컬럼 목록 그대로이고, 값의 의미(`cntg_yn="2"`가 체결 등)는 국내주식 체결통보(H0STCNI0)
문서 기준이라 선물옵션 실응답으로 재검증하기 전까지 미검증이다.** 그래서 이 모듈은 22개
필드를 **받은 문자열 그대로** 보관하고, 해석은 `is_*` 프로퍼티로 분리해 두었다 —
실측에서 의미가 다르게 나오면 프로퍼티만 고치면 되고 원본은 손상되지 않는다.
실측 절차는 `scripts/probe_order_notice.py`.
"""

from __future__ import annotations

import asyncio
import json
from base64 import b64decode
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import websockets
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

from messiah.broker.kis import tr_codes
from messiah.broker.kis.credentials import KISCredentials
from messiah.broker.kis.ws_client import ApprovalKeyIssuer, KISWebSocketClient, Subscription
from messiah.core import logging as mlog

# 시세 수집기와 동일한 단절 판정 (`data/collector.py` _WS_DISCONNECT_ERRORS와 같은 이유).
_WS_DISCONNECT_ERRORS = (OSError, websockets.WebSocketException)

#: 체결통보 본문 필드 — KIS 공식 샘플 `fuopt_ccnl_notice.py`의 컬럼 목록 순서 그대로.
#: **순서가 곧 의미다.** 캐럿(^) 구분 문자열을 이 순서로 읽는 것 외에 필드를 식별할 방법이
#: 없으므로, 이 튜플을 임의로 재정렬하면 체결가와 주문수량이 뒤바뀐 채 조용히 동작한다.
ORDER_NOTICE_FIELDS: tuple[str, ...] = (
    "cust_id",  # HTS ID
    "acnt_no",  # 계좌번호
    "oder_no",  # 주문번호
    "ooder_no",  # 원주문번호 (정정/취소의 대상)
    "seln_byov_cls",  # 매도매수구분
    "rctf_cls",  # 정정구분
    "oder_kind2",  # 주문종류2
    "stck_shrn_iscd",  # 종목코드(단축)
    "cntg_qty",  # 체결수량
    "cntg_unpr",  # 체결단가
    "stck_cntg_hour",  # 체결시각
    "rfus_yn",  # 거부여부
    "cntg_yn",  # 체결여부
    "acpt_yn",  # 접수여부
    "brnc_no",  # 지점번호
    "oder_qty",  # 주문수량
    "acnt_name",  # 계좌명
    "cntg_isnm",  # 종목명
    "oder_cond",  # 주문조건
    "ord_grp",  # 주문그룹
    "ord_grpseq",  # 주문그룹SEQ
    "order_prc",  # 주문가격
)

_FIELD_COUNT = len(ORDER_NOTICE_FIELDS)


class OrderNoticeError(Exception):
    """체결통보 경로의 설정/프로토콜 오류 — 재연결로 해결되지 않는다."""


def aes_cbc_base64_decrypt(key: str, iv: str, cipher_text: str) -> str:
    """구독 응답으로 받은 key/iv로 체결통보 본문을 복호한다.

    입력: 구독 성공 응답 `body.output`의 key/iv(둘 다 ASCII 문자열), base64 암호문.
    계산: AES-CBC 복호 후 PKCS#7 언패딩 → UTF-8 문자열. KIS 공식 샘플 `kis_auth.py`의
          `aes_cbc_base64_dec`와 동일한 연산이다(같은 라이브러리·같은 순서).
    실패 조건: key/iv가 비어 있으면 OrderNoticeError. 암호문이 손상됐거나 키가 틀리면
              패딩 검증에서 ValueError가 나는데, 그대로 전파한다 — 조용히 빈 문자열을
              돌려주면 "통보는 오는데 내용이 없다"가 되어 원인을 못 찾는다.
    """
    if not key or not iv:
        raise OrderNoticeError(
            "체결통보 복호 키(iv/key) 없음 — 구독 성공 응답의 body.output을 놓쳤다는 뜻"
        )
    cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, iv.encode("utf-8"))
    return unpad(cipher.decrypt(b64decode(cipher_text)), AES.block_size).decode("utf-8")


@dataclass(frozen=True, slots=True)
class OrderNotice:
    """체결통보 1건 — 22개 필드를 **받은 문자열 그대로** 보관한다.

    숫자로 미리 바꾸지 않는 이유: 거부/접수 통보에는 체결수량·체결단가가 빈칸으로 오는데,
    그때 0으로 강제 변환하면 "0계약 체결"이라는 존재하지 않는 사실이 만들어진다. 형변환은
    값이 실제로 필요한 프로퍼티에서만, 실패 가능성을 드러내며 한다.
    """

    cust_id: str
    acnt_no: str
    oder_no: str
    ooder_no: str
    seln_byov_cls: str
    rctf_cls: str
    oder_kind2: str
    stck_shrn_iscd: str
    cntg_qty: str
    cntg_unpr: str
    stck_cntg_hour: str
    rfus_yn: str
    cntg_yn: str
    acpt_yn: str
    brnc_no: str
    oder_qty: str
    acnt_name: str
    cntg_isnm: str
    oder_cond: str
    ord_grp: str
    ord_grpseq: str
    order_prc: str

    # ---- 해석 (H0STCNI0 문서 기준 — 선물옵션 실응답 재검증 전까지 미검증) ----------
    #
    # 이 세 프로퍼티만이 "값의 의미"를 가정한다. 나머지는 전부 원본 그대로라, 실측에서
    # 코드 체계가 다르게 나오면 여기 세 줄만 고치면 된다.
    @property
    def is_fill(self) -> bool:
        """체결 통보인가 (cntg_yn: "1"=주문/정정/취소/거부 접수, "2"=체결)."""
        return self.cntg_yn == "2"

    @property
    def is_rejected(self) -> bool:
        """거부 통보인가 (rfus_yn: "0"=승인, "1"=거부)."""
        return self.rfus_yn == "1"

    @property
    def filled_qty(self) -> int:
        """체결수량 — 빈칸(거부/접수 통보)은 0. 숫자가 아니면 ValueError를 그대로 낸다."""
        return int(self.cntg_qty) if self.cntg_qty.strip() else 0

    def redacted(self) -> dict[str, str]:
        """로그용 사영 — 계좌번호·계좌명·HTS ID는 뺀다(시크릿은 로그에 안 남긴다, SYSTEM.md §3)."""
        return {
            "oder_no": self.oder_no,
            "ooder_no": self.ooder_no,
            "symbol": self.stck_shrn_iscd,
            "seln_byov_cls": self.seln_byov_cls,
            "cntg_qty": self.cntg_qty,
            "cntg_unpr": self.cntg_unpr,
            "oder_qty": self.oder_qty,
            "order_prc": self.order_prc,
            "cntg_hour": self.stck_cntg_hour,
            "cntg_yn": self.cntg_yn,
            "rfus_yn": self.rfus_yn,
            "acpt_yn": self.acpt_yn,
        }


def parse_order_notices(plain: str) -> list[OrderNotice]:
    """복호된 캐럿(^) 구분 문자열을 통보 목록으로 만든다.

    입력: 복호 결과 원문. 한 프레임에 여러 건이 실릴 수 있어 22개씩 끊어 읽는다.
    실패 조건: 필드 수가 22의 배수가 아니면 OrderNoticeError — 레이아웃이 바뀌었거나
              복호가 틀렸다는 뜻이고, 둘 다 앞의 몇 개만 취해 진행하면 안 되는 상황이다.
              (뒤쪽 필드가 잘린 채 체결가만 읽히면 그 값이 맞는지 알 방법이 없다.)
    """
    fields = plain.split("^")
    if len(fields) % _FIELD_COUNT != 0 or not fields:
        raise OrderNoticeError(
            f"체결통보 필드 수 불일치 — {len(fields)}개는 {_FIELD_COUNT}의 배수가 아니다 "
            "(레이아웃 변경 또는 복호 실패)"
        )
    return [OrderNotice(*fields[i : i + _FIELD_COUNT]) for i in range(0, len(fields), _FIELD_COUNT)]


NoticeHandler = Callable[[OrderNotice], Awaitable[None]]


class OrderNoticeStream:
    """체결통보 WS 세션 — 구독 → 복호 → 파싱 → 핸들러 호출.

    시세 수집기(`data/collector.py`)와 같은 재연결 설계를 쓰되, 이 세션은 **조용한 것이
    정상**이라 "데이터가 안 온다"를 이상 신호로 삼을 수 없다(수집기의 stall watchdog에
    해당하는 장치가 여기엔 성립하지 않는다). 그래서 연결 유지의 책임이 전적으로 PINGPONG
    응답에 있다.
    """

    def __init__(
        self,
        creds: KISCredentials,
        on_notice: NoticeHandler,
        approval_issuer: ApprovalKeyIssuer | None = None,
        ws_connect: Callable[[str], Any] = websockets.connect,
        reconnect_initial_backoff_seconds: float = 5.0,
        reconnect_max_backoff_seconds: float = 60.0,
    ) -> None:
        """
        입력: creds.hts_id가 구독 tr_key다 — 비어 있으면 즉시 OrderNoticeError.
             빈 tr_key로 구독하면 KIS가 거부하지 않고 **통보만 안 오므로**(구독 성공 응답은
             그대로 온다) 그 상태를 며칠 못 알아챈다. 기동 시점에 깨지는 편이 낫다.
        """
        if not creds.hts_id:
            raise OrderNoticeError(
                "체결통보 구독에는 HTS ID가 필요하다 — .env의 KIS_HTS_ID 확인 "
                "(`core/config.BrokerConfig.hts_id_ref`)"
            )
        self._creds = creds
        self._on_notice = on_notice
        self._approval_issuer = approval_issuer or ApprovalKeyIssuer(creds)
        self._ws_connect = ws_connect
        self._reconnect_initial_backoff_seconds = reconnect_initial_backoff_seconds
        self._reconnect_max_backoff_seconds = reconnect_max_backoff_seconds
        self._tr_id = tr_codes.order_notice_tr_id(creds.is_mock)
        # 구독 성공 응답에서 단 한 번 받는 복호 재료. 연결마다 새로 받으므로 run_once()
        # 진입 때 반드시 초기화한다 — 이전 연결의 키로 새 연결의 본문을 풀면 조용히
        # 패딩 오류가 나거나, 더 나쁘게는 쓰레기가 22필드로 갈릴 수 있다.
        self._aes_key = ""
        self._aes_iv = ""
        self._encrypted = False

    async def run_once(self, on_connected: Callable[[], None] | None = None) -> None:
        """
        계산: approval_key 발급(동기 httpx라 to_thread) → 체결통보 전용 도메인 연결 →
             HTS ID로 구독 → 수신 루프.
        실패 조건: 단절은 그대로 전파(run_forever가 감쌈). 구독 거부(rt_cd != "0")는
                  OrderNoticeError — 재시도로 해결되는 문제가 아니다(HTS ID 오타, 계좌
                  권한 등). 시세 수집기가 ValueError를 재시도하지 않는 것과 같은 규칙.
        """
        self._aes_key = ""
        self._aes_iv = ""
        self._encrypted = False

        approval_key = await asyncio.to_thread(self._approval_issuer.issue)
        domain = tr_codes.order_notice_ws_domain(self._creds.is_mock)
        async with self._ws_connect(domain) as ws:
            client = KISWebSocketClient(approval_key, ws)
            await client.subscribe(Subscription(self._tr_id, self._creds.hts_id))
            if on_connected is not None:
                on_connected()
            await self._receive_loop(ws)

    async def _receive_loop(self, ws: Any) -> None:
        """원문 프레임을 직접 받는다 — PINGPONG 되돌려주기와 암호문 보존 때문(모듈 docstring)."""
        while True:
            raw = await ws.recv()
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            if raw.startswith("{"):
                await self._handle_control_frame(ws, raw)
            else:
                await self._handle_data_frame(raw)

    async def _handle_control_frame(self, ws: Any, raw: str) -> None:
        message = json.loads(raw)
        header = message.get("header") or {}
        if header.get("tr_id") == "PINGPONG":
            # 받은 프레임을 **그대로** 돌려준다 — 재조립하지 않는 이유는 서버가 무엇을
            # 대조하는지 문서에 없어서다. 원문 반사가 KIS 공식 샘플의 동작이다.
            await ws.send(raw)
            return

        body = message.get("body") or {}
        rt_cd = body.get("rt_cd")
        if rt_cd is not None and rt_cd != "0":
            raise OrderNoticeError(
                f"체결통보 구독 거부 — rt_cd={rt_cd} msg_cd={body.get('msg_cd')} "
                f"msg1={body.get('msg1')} (HTS ID/계좌 권한 확인)"
            )

        output = body.get("output") or {}
        if "key" in output and "iv" in output:
            self._aes_key = output["key"]
            self._aes_iv = output["iv"]
            self._encrypted = header.get("encrypt") == "Y"
            mlog.log(
                "OrderNoticeSubscribed",
                "체결통보 구독 성공 — 복호 키 수신",
                tr_id=header.get("tr_id"),
                encrypt=header.get("encrypt"),
                is_mock=self._creds.is_mock,
            )

    async def _handle_data_frame(self, raw: str) -> None:
        """`<암호화여부>|<TR_ID>|<건수>|<본문>` 파이프 프레임 처리."""
        parts = raw.split("|", 3)
        if len(parts) < 4:
            mlog.log("OrderNoticeMalformed", "체결통보 프레임 형식 불일치", parts=len(parts))
            return
        encrypted_flag, tr_id, _count, payload = parts
        if tr_id != self._tr_id:
            return  # 이 세션은 체결통보만 구독한다 — 다른 TR이 오면 무시

        try:
            if encrypted_flag == "1" or self._encrypted:
                payload = aes_cbc_base64_decrypt(self._aes_key, self._aes_iv, payload)
            notices = parse_order_notices(payload)
        except (OrderNoticeError, ValueError) as exc:
            # 여기서 루프를 죽이지 않는다(L22) — 한 프레임을 못 읽는 것과 통보 경로 전체를
            # 잃는 것은 피해 크기가 다르다. 대신 조용히 넘기지도 않는다.
            mlog.log(
                "OrderNoticeUndecryptable",
                f"체결통보 복호/파싱 실패 — {exc}",
                tr_id=tr_id,
                have_key=bool(self._aes_key),
            )
            return

        for notice in notices:
            # `redacted()`가 "무엇을 로그에 남길지"의 단일 지점이다 — 여기서 키를 다시
            # 나열하면 두 목록이 갈라져 언젠가 계좌번호가 로그로 샌다. pyright는 dict
            # 언패킹이 `log(level=...)`과 충돌할 **가능성**을 경고하는데, 실제 키는
            # 고정이고 그 사실을 테스트가 지킨다(test_redacted_has_no_reserved_keys).
            mlog.log(
                "OrderNoticeReceived",
                "체결통보 수신",
                **notice.redacted(),  # pyright: ignore[reportArgumentType]
            )
            try:
                await self._on_notice(notice)
            except Exception as exc:  # noqa: BLE001 — 핸들러 사고가 통보 수신을 끊으면 안 된다
                mlog.log(
                    "OrderNoticeHandlerError",
                    f"체결통보 핸들러 예외 — {exc}",
                    oder_no=notice.oder_no,
                )

    async def run_forever(self) -> None:
        """단절마다 지수 백오프 후 재연결 — `data/collector.py` run_forever와 동일 설계.

        재연결 시 approval_key와 복호 키를 **둘 다** 새로 받는다(run_once를 통째로 재호출).
        """
        backoff = self._reconnect_initial_backoff_seconds
        was_disconnected = False

        def _on_connected() -> None:
            nonlocal backoff, was_disconnected
            if was_disconnected:
                mlog.log("OrderNoticeWSReconnected", "체결통보 WS 재연결 성공 — 수신 재개")
                was_disconnected = False
            backoff = self._reconnect_initial_backoff_seconds

        while True:
            try:
                await self.run_once(on_connected=_on_connected)
            except _WS_DISCONNECT_ERRORS as exc:
                was_disconnected = True
                mlog.log(
                    "OrderNoticeWSDisconnected",
                    f"체결통보 WS 단절 — {backoff:.0f}초 후 재연결: {exc}",
                    backoff_seconds=backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._reconnect_max_backoff_seconds)
