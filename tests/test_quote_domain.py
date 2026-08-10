"""시세 조회 도메인 (2026-08-10 C-1).

시세는 계좌와 무관한 공개 데이터라 모의투자 앱키로도 실전 도메인이 200 OK다 — 2026-07-21에
`get_investor_flow()`가 이미 실측하고 `REAL_REST_DOMAIN`을 고정 사용해 왔는데, **옵션 시세만
같은 처방을 못 받고 있었다.**

2026-08-10 실측: 옵션체인(모의 도메인) 실패 53건/약 5,050건 = 1.05% · 수급(실전 도메인)
3건/1,188건 = 0.25%. 같은 앱키·같은 시각·같은 종류의 조회인데 실패율이 4배였다.

전환 당일 실계좌로 두 도메인을 나란히 호출해 응답이 같은지 확인했다(3개 섹션 42개 필드가
값까지 전부 동일). 이 파일은 그 확인을 **코드 계약으로** 굳힌다 — 특히 마지막 테스트가
중요하다: 주문·잔고가 이 전환에 딸려 가면 그건 이 변경이 절대 만들면 안 되는 사고다.
"""

from __future__ import annotations

import pytest

from messiah.broker.kis import rest_client as rc
from messiah.broker.kis import tr_codes


class _StubToken:
    def get_token(self) -> str:
        return "T"


class _Creds:
    def __init__(self, is_mock: bool) -> None:
        self.is_mock = is_mock
        self.app_key = "k"
        self.app_secret = "s"
        self.account_no = "60046651"
        self.account_product_code = "01"


class _RecordingClient:
    """호출된 URL만 기록한다 — 네트워크를 안 탄다."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url, headers=None, params=None):  # noqa: ANN001
        self.urls.append(url)
        return _Response()


class _Response:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"rt_cd": "0", "output1": {}}


def _client(*, is_mock: bool) -> tuple[rc.KISRestClient, _RecordingClient]:
    http = _RecordingClient()
    client = rc.KISRestClient(
        _Creds(is_mock), token_daemon=_StubToken(), client=http, min_request_interval=0.0
    )
    return client, http


def test_option_quote_goes_to_the_real_domain_even_with_a_mock_key():
    client, http = _client(is_mock=True)

    client.get_quote("C01608967", tr_codes.FID_MRKT_DIV_INDEX_OPTION)
    client.get_asking_price("C01608967", tr_codes.FID_MRKT_DIV_INDEX_OPTION)

    assert all(url.startswith(tr_codes.REAL_REST_DOMAIN) for url in http.urls), http.urls


def test_the_tr_id_is_identical_on_both_domains():
    """이 전환이 **호스트만** 바꾸는 것이라는 근거 — 안 그러면 되돌리기가 한 줄이 아니다."""
    assert tr_codes.TR_OPTION_QUOTE["real"] == tr_codes.TR_OPTION_QUOTE["vps"]
    assert tr_codes.TR_OPTION_ASKING_PRICE["real"] == tr_codes.TR_OPTION_ASKING_PRICE["vps"]


def test_flipping_one_constant_reverts_the_whole_switch(monkeypatch: pytest.MonkeyPatch):
    """되돌림이 상수 하나여야 한다 — 장애 중에 코드를 읽어 가며 되돌릴 수는 없다."""
    monkeypatch.setattr(rc, "QUOTE_ON_REAL_DOMAIN", False)
    client, http = _client(is_mock=True)

    client.get_quote("C01608967", tr_codes.FID_MRKT_DIV_INDEX_OPTION)

    assert http.urls[0].startswith(tr_codes.VPS_REST_DOMAIN)


def test_orders_and_balance_never_follow_the_quote_switch():
    """**모의 계좌의 주문이 실전으로 나가는 것**은 이 변경이 절대 만들면 안 되는 사고다."""
    client, http = _client(is_mock=True)

    client.get_balance()

    assert http.urls, "호출이 하나는 있어야 판정이 성립한다"
    assert all(url.startswith(tr_codes.VPS_REST_DOMAIN) for url in http.urls), http.urls


def test_the_minute_chart_stays_on_the_account_domain():
    """백필은 **잃은 봉을 되찾는 복구 경로**다 — 실패율을 잰 적 없이 건드리지 않는다."""
    client, http = _client(is_mock=True)

    client.get_futureoption_minute_chart("A05608", date_yyyymmdd="20260810", hour_hhmmss="153500")

    assert http.urls[0].startswith(tr_codes.VPS_REST_DOMAIN)


def test_a_real_account_is_unaffected_by_the_switch():
    """실전 계좌는 원래 실전 도메인이다 — 이 전환이 그 경로를 바꾸지 않는다."""
    client, http = _client(is_mock=False)

    client.get_quote("C01608967", tr_codes.FID_MRKT_DIV_INDEX_OPTION)

    assert http.urls[0].startswith(tr_codes.REAL_REST_DOMAIN)
