import json
from decimal import Decimal

import httpx
import pytest
from messiah.broker.base import SubmitResult
from messiah.broker.kis.adapter import KISBrokerAdapter
from messiah.broker.kis.credentials import KISCredentials
from messiah.broker.kis.rest_client import KISRestClient
from messiah.broker.kis.token_daemon import TokenDaemon
from messiah.core.messages import OrderKind, OrderRequest, Side


def _creds(**overrides) -> KISCredentials:
    defaults = dict(app_key="key", app_secret="secret", account_no="12345678", is_mock=True)
    defaults.update(overrides)
    return KISCredentials(**defaults)


def _token_daemon() -> TokenDaemon:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})

    return TokenDaemon(_creds(), client=httpx.Client(transport=httpx.MockTransport(handler)))


def _adapter(handler, tick_size: Decimal = Decimal("0.02")) -> tuple[KISBrokerAdapter, list]:
    captured: list = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    creds = _creds()
    token_daemon = _token_daemon()
    rest = KISRestClient(
        creds,
        token_daemon,
        client=httpx.Client(transport=httpx.MockTransport(wrapped)),
        min_request_interval=0.0,
    )
    adapter = KISBrokerAdapter(creds, tick_size, token_daemon=token_daemon, rest_client=rest)
    return adapter, captured


def _order_request(**overrides) -> OrderRequest:
    defaults = dict(
        intent_id="intent-1",
        symbol="201W09",
        kind=OrderKind.ENTRY,
        side=Side.LONG,
        qty=1,
        limit_price_ticks=17500,
    )
    defaults.update(overrides)
    return OrderRequest(**defaults)


async def test_connect_issues_token_and_sets_connected_flag():
    adapter, _ = _adapter(lambda r: httpx.Response(200, json={"output": {}}))
    assert adapter.connected is False

    await adapter.connect()

    assert adapter.connected is True

    await adapter.close()
    assert adapter.connected is False


async def test_submit_maps_long_to_buy_and_converts_ticks_to_price():
    response = {"rt_cd": "0", "output": {"ODNO": "0000007045"}}
    adapter, captured = _adapter(lambda r: httpx.Response(200, json=response))

    result = await adapter.submit(_order_request(side=Side.LONG, limit_price_ticks=17500))

    assert result == SubmitResult(ok=True, broker_order_no="0000007045")
    body = json.loads(captured[0].content)
    assert body["SLL_BUY_DVSN_CD"] == "02"  # 매수
    assert body["ORD_DVSN_CD"] == "01"  # 지정가
    assert body["UNIT_PRICE"] == "350.00"  # 17500 ticks * 0.02


async def test_submit_maps_short_to_sell():
    response = {"rt_cd": "0", "output": {"ODNO": "1"}}
    adapter, captured = _adapter(lambda r: httpx.Response(200, json=response))

    await adapter.submit(_order_request(side=Side.SHORT, limit_price_ticks=17500))

    body = json.loads(captured[0].content)
    assert body["SLL_BUY_DVSN_CD"] == "01"  # 매도


async def test_submit_with_no_limit_price_sends_market_order():
    response = {"rt_cd": "0", "output": {"ODNO": "1"}}
    adapter, captured = _adapter(lambda r: httpx.Response(200, json=response))

    await adapter.submit(_order_request(limit_price_ticks=None))

    body = json.loads(captured[0].content)
    assert body["ORD_DVSN_CD"] == "02"  # 시장가
    assert body["UNIT_PRICE"] == "0"


async def test_submit_returns_rejection_when_kis_rt_cd_is_not_zero():
    adapter, _ = _adapter(lambda r: httpx.Response(200, json={"rt_cd": "1", "msg1": "증거금 부족"}))

    result = await adapter.submit(_order_request())

    assert result == SubmitResult(ok=False, error="증거금 부족")


async def test_submit_rejects_option_side_before_calling_kis():
    adapter, captured = _adapter(lambda r: httpx.Response(200, json={"rt_cd": "0"}))

    with pytest.raises(ValueError):
        await adapter.submit(_order_request(side=Side.OPTION))

    assert captured == []  # KIS를 호출하지 않아야 함


async def test_cancel_sends_original_order_number_and_returns_true_on_success():
    adapter, captured = _adapter(lambda r: httpx.Response(200, json={"rt_cd": "0"}))

    ok = await adapter.cancel("0000007045")

    assert ok is True
    body = json.loads(captured[0].content)
    assert body["ORGN_ODNO"] == "0000007045"
    assert body["RVSE_CNCL_DVSN_CD"] == "02"


async def test_cancel_returns_false_when_kis_rejects():
    adapter, _ = _adapter(lambda r: httpx.Response(200, json={"rt_cd": "1", "msg1": "이미 체결"}))

    ok = await adapter.cancel("0000007045")

    assert ok is False


async def test_positions_parses_output1_and_applies_sign_by_side():
    response = {
        "rt_cd": "0",
        "output1": [
            {
                "shtn_pdno": "101R12",
                "sll_buy_dvsn_name": "BUY",
                "cblc_qty": "6",
                "ccld_avg_unpr1": "402.28",
            },
            {
                "shtn_pdno": "101S03",
                "sll_buy_dvsn_name": "SLL",
                "cblc_qty": "3",
                "ccld_avg_unpr1": "406.00",
            },
            {
                "shtn_pdno": "111R10",
                "sll_buy_dvsn_name": "",
                "cblc_qty": "0",
                "ccld_avg_unpr1": "0",
            },
        ],
        "output2": {},
    }
    adapter, _ = _adapter(lambda r: httpx.Response(200, json=response))

    positions = await adapter.positions()

    assert len(positions) == 2  # 잔고수량 0인 행은 제외
    long_pos = next(p for p in positions if p.symbol == "101R12")
    short_pos = next(p for p in positions if p.symbol == "101S03")
    assert long_pos.qty == 6
    assert short_pos.qty == -3
    assert short_pos.avg_price_ticks == 20300  # 406.00 / 0.02


async def test_account_parses_output2_summary_fields():
    response = {
        "rt_cd": "0",
        "output1": [],
        "output2": {
            "dnca_cash": "90000000000",
            "mgna_tota": "1391065523",
            "prsm_dpast": "90016125000",
        },
    }
    adapter, _ = _adapter(lambda r: httpx.Response(200, json=response))

    account = await adapter.account()

    assert account.cash == Decimal("90000000000")
    assert account.margin_used == Decimal("1391065523")
    assert account.total_equity == Decimal("90016125000")


async def test_probe_front_month_is_not_implemented():
    adapter, _ = _adapter(lambda r: httpx.Response(200, json={}))

    with pytest.raises(NotImplementedError):
        await adapter.probe_front_month("K200_MINI_FUT")
