"""주문체결통보 배선 테스트 — 복호·파싱·구독 경로.

이 경로는 **모의계좌 실주문 없이는 end-to-end 실측이 불가능**하고(통보는 실제 주문에만
온다), 반대로 여기서 잡히는 종류의 실수(도메인 오선택·tr_key 오선택·복호 키 누락)는
실측 때 전부 "통보가 안 온다"라는 똑같은 증상 하나로 뭉뚱그려진다. 그래서 실측 전에
원인별로 갈라놓는 것이 이 파일의 목적이다.
"""

from __future__ import annotations

import json
from base64 import b64encode

import pytest
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from messiah.broker.kis import tr_codes
from messiah.broker.kis.credentials import KISCredentials
from messiah.broker.kis.order_notice import (
    ORDER_NOTICE_FIELDS,
    OrderNoticeError,
    OrderNoticeStream,
    aes_cbc_base64_decrypt,
    parse_order_notices,
)

_KEY = "0123456789abcdef0123456789abcdef"  # KIS는 32자 키를 준다 (AES-256)
_IV = "abcdef9876543210"  # 16자


def _encrypt(plain: str) -> str:
    cipher = AES.new(_KEY.encode(), AES.MODE_CBC, _IV.encode())
    return b64encode(cipher.encrypt(pad(plain.encode(), AES.block_size))).decode()


def _notice_fields(**overrides: str) -> str:
    """22개 필드를 캐럿으로 이어 붙인 본문 하나."""
    values = dict.fromkeys(ORDER_NOTICE_FIELDS, "")
    values.update(
        {
            "cust_id": "@3137669",
            "acnt_no": "60046651",
            "oder_no": "0000012345",
            "seln_byov_cls": "02",
            "stck_shrn_iscd": "A05609",
            "cntg_qty": "1",
            "cntg_unpr": "1082.50",
            "stck_cntg_hour": "103015",
            "rfus_yn": "0",
            "cntg_yn": "2",
            "acpt_yn": "2",
            "oder_qty": "1",
            "order_prc": "1082.50",
        }
    )
    values.update(overrides)
    return "^".join(values[name] for name in ORDER_NOTICE_FIELDS)


class FakeWS:
    def __init__(self, incoming: list[str]) -> None:
        self.incoming = list(incoming)
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        if not self.incoming:
            raise ConnectionError("연결 종료(테스트 픽스처 소진)")
        return self.incoming.pop(0)

    async def close(self) -> None:
        pass


class FakeConnect:
    """ws_connect 대역 — 어느 도메인으로 붙었는지 기록한다."""

    def __init__(self, ws: FakeWS) -> None:
        self.ws = ws
        self.domains: list[str] = []

    def __call__(self, domain: str) -> "FakeConnect":
        self.domains.append(domain)
        return self

    async def __aenter__(self) -> FakeWS:
        return self.ws

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakeIssuer:
    def issue(self) -> str:
        return "APV-TEST"


def _creds(**kw: object) -> KISCredentials:
    base: dict = {
        "app_key": "key",
        "app_secret": "secret",
        "account_no": "60046651",
        "hts_id": "@3137669",
        "is_mock": True,
    }
    base.update(kw)
    return KISCredentials(**base)


def _subscribe_ack(tr_id: str, *, rt_cd: str = "0", with_keys: bool = True) -> str:
    body: dict = {"rt_cd": rt_cd, "msg_cd": "OPSP0000", "msg1": "SUBSCRIBE SUCCESS"}
    if with_keys:
        body["output"] = {"iv": _IV, "key": _KEY}
    header = {"tr_id": tr_id, "tr_key": "@3137669", "encrypt": "Y"}
    return json.dumps({"header": header, "body": body})


def _stream(incoming: list[str], creds: KISCredentials | None = None):
    ws = FakeWS(incoming)
    connect = FakeConnect(ws)
    received: list = []

    async def handler(notice) -> None:
        received.append(notice)

    stream = OrderNoticeStream(
        creds or _creds(),
        on_notice=handler,
        approval_issuer=FakeIssuer(),  # type: ignore[arg-type]
        ws_connect=connect,
    )
    return stream, ws, connect, received


# ---------------------------------------------------------------- 복호 / 파싱


def test_aes_decrypt_roundtrip():
    plain = _notice_fields()
    assert aes_cbc_base64_decrypt(_KEY, _IV, _encrypt(plain)) == plain


def test_aes_decrypt_without_key_raises():
    with pytest.raises(OrderNoticeError):
        aes_cbc_base64_decrypt("", _IV, _encrypt("x"))


def test_parse_maps_fields_by_position():
    (notice,) = parse_order_notices(_notice_fields())

    assert notice.oder_no == "0000012345"
    assert notice.stck_shrn_iscd == "A05609"
    assert notice.cntg_unpr == "1082.50"
    assert notice.is_fill is True
    assert notice.is_rejected is False
    assert notice.filled_qty == 1


def test_parse_handles_multiple_notices_in_one_frame():
    payload = _notice_fields(oder_no="1") + "^" + _notice_fields(oder_no="2")

    first, second = parse_order_notices(payload)

    assert (first.oder_no, second.oder_no) == ("1", "2")


def test_parse_rejects_field_count_mismatch():
    """앞쪽 몇 개만 취해 진행하면 체결가가 맞는지 알 방법이 없다 — 깨뜨린다."""
    with pytest.raises(OrderNoticeError):
        parse_order_notices("^".join(["x"] * (len(ORDER_NOTICE_FIELDS) - 1)))


def test_rejected_notice_has_no_fabricated_fill():
    """거부 통보는 체결수량이 빈칸으로 온다 — 0계약 '체결'이 만들어지면 안 된다."""
    (notice,) = parse_order_notices(_notice_fields(cntg_yn="1", rfus_yn="1", cntg_qty=""))

    assert notice.is_rejected is True
    assert notice.is_fill is False
    assert notice.filled_qty == 0


def test_redacted_omits_account_identifiers():
    (notice,) = parse_order_notices(_notice_fields())
    redacted = notice.redacted()

    assert "acnt_no" not in redacted
    assert "cust_id" not in redacted
    assert "acnt_name" not in redacted
    assert redacted["oder_no"] == "0000012345"


def test_redacted_has_no_reserved_keys():
    """`mlog.log(tag, msg, level=...)`의 인자명과 겹치면 로그 호출이 조용히 뒤틀린다 —
    `order_notice.py`가 이 dict를 그대로 언패킹하므로 여기서 못 박는다."""
    (notice,) = parse_order_notices(_notice_fields())

    assert not {"tag", "msg", "level"} & set(notice.redacted())


# ---------------------------------------------------------------- 구독 경로


def test_missing_hts_id_raises_at_construction():
    """빈 tr_key로 구독하면 KIS는 구독 성공을 돌려주고 통보만 안 온다 — 기동 때 깨뜨린다."""
    with pytest.raises(OrderNoticeError):
        OrderNoticeStream(_creds(hts_id=""), on_notice=lambda n: None)  # type: ignore[arg-type]


async def test_mock_account_uses_notice_domain_not_market_data_domain():
    stream, _ws, connect, _ = _stream([])
    with pytest.raises(ConnectionError):
        await stream.run_once()

    assert connect.domains == [tr_codes.VPS_WS_DOMAIN]
    assert connect.domains != [tr_codes.MARKET_DATA_WS_DOMAIN]


async def test_real_account_uses_real_notice_domain():
    stream, _ws, connect, _ = _stream([], creds=_creds(is_mock=False))
    with pytest.raises(ConnectionError):
        await stream.run_once()

    assert connect.domains == [tr_codes.REAL_WS_DOMAIN]


async def test_subscription_uses_hts_id_as_tr_key_and_mock_tr_id():
    stream, ws, _connect, _ = _stream([])
    with pytest.raises(ConnectionError):
        await stream.run_once()

    envelope = json.loads(ws.sent[0])
    assert envelope["body"]["input"] == {"tr_id": "H0IFCNI9", "tr_key": "@3137669"}
    assert envelope["header"]["tr_type"] == "1"


async def test_encrypted_notice_is_decrypted_and_dispatched():
    frame = f"1|H0IFCNI9|001|{_encrypt(_notice_fields())}"
    stream, _ws, _connect, received = _stream([_subscribe_ack("H0IFCNI9"), frame])

    with pytest.raises(ConnectionError):
        await stream.run_once()

    assert len(received) == 1
    assert received[0].oder_no == "0000012345"
    assert received[0].is_fill is True


async def test_notice_without_subscribe_keys_does_not_crash_loop():
    """구독 응답을 놓친 채 통보가 오면 — 조용히 넘기지도, 루프를 죽이지도 않는다."""
    frame = f"1|H0IFCNI9|001|{_encrypt(_notice_fields())}"
    later = f"1|H0IFCNI9|001|{_encrypt(_notice_fields())}"
    stream, _ws, _connect, received = _stream([frame, _subscribe_ack("H0IFCNI9"), later])

    with pytest.raises(ConnectionError):
        await stream.run_once()

    # 첫 프레임은 키가 없어 버려지고, 구독 응답 이후의 프레임은 정상 처리된다.
    assert len(received) == 1


async def test_subscribe_rejection_raises_instead_of_waiting_silently():
    ack = _subscribe_ack("H0IFCNI9", rt_cd="1", with_keys=False)
    stream, _ws, _connect, _ = _stream([ack])

    with pytest.raises(OrderNoticeError, match="구독 거부"):
        await stream.run_once()


async def test_pingpong_is_echoed_verbatim():
    """주문 없는 시간대에 연결을 유지하는 유일한 장치다."""
    ping = json.dumps({"header": {"tr_id": "PINGPONG", "datetime": "20260826103000"}})
    stream, ws, _connect, _ = _stream([_subscribe_ack("H0IFCNI9"), ping])

    with pytest.raises(ConnectionError):
        await stream.run_once()

    assert ws.sent[-1] == ping


async def test_other_tr_frames_are_ignored():
    stream, _ws, _connect, received = _stream(
        [_subscribe_ack("H0IFCNI9"), "0|H0IFCNT0|001|A05609^103015^-0.54"]
    )
    with pytest.raises(ConnectionError):
        await stream.run_once()

    assert received == []


async def test_handler_exception_does_not_stop_stream():
    frames = [
        _subscribe_ack("H0IFCNI9"),
        f"1|H0IFCNI9|001|{_encrypt(_notice_fields(oder_no='1'))}",
        f"1|H0IFCNI9|001|{_encrypt(_notice_fields(oder_no='2'))}",
    ]
    ws = FakeWS(frames)
    connect = FakeConnect(ws)
    seen: list[str] = []

    async def handler(notice) -> None:
        seen.append(notice.oder_no)
        raise RuntimeError("핸들러 사고")

    stream = OrderNoticeStream(
        _creds(),
        on_notice=handler,
        approval_issuer=FakeIssuer(),  # type: ignore[arg-type]
        ws_connect=connect,
    )
    with pytest.raises(ConnectionError):
        await stream.run_once()

    assert seen == ["1", "2"]  # 첫 건의 예외가 둘째 건 수신을 막지 않았다


async def test_reconnect_discards_previous_decrypt_keys():
    """이전 연결의 키로 새 연결 본문을 풀면 쓰레기가 22필드로 갈릴 수 있다."""
    stream, _ws, _connect, _ = _stream([_subscribe_ack("H0IFCNI9")])
    with pytest.raises(ConnectionError):
        await stream.run_once()
    assert stream._aes_key == _KEY

    stream._ws_connect = FakeConnect(FakeWS([]))  # type: ignore[assignment]
    with pytest.raises(ConnectionError):
        await stream.run_once()

    assert stream._aes_key == ""
