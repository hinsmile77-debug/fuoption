import time

import httpx
import pytest
from messiah.broker.kis.credentials import KISCredentials
from messiah.broker.kis.token_daemon import TokenDaemon


def _creds(**overrides) -> KISCredentials:
    defaults = dict(app_key="key", app_secret="secret", is_mock=True)
    defaults.update(overrides)
    return KISCredentials(**defaults)


def _client_with(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_get_token_issues_and_caches():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 86400})

    daemon = TokenDaemon(_creds(), client=_client_with(handler))
    assert daemon.get_token() == "tok-1"
    assert daemon.get_token() == "tok-1"
    assert call_count["n"] == 1  # 두번째 호출은 캐시 사용, 재요청 없음


def test_get_token_reissues_when_expired():
    tokens = iter(["tok-1", "tok-2"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": next(tokens), "expires_in": 86400})

    daemon = TokenDaemon(_creds(), client=_client_with(handler))
    assert daemon.get_token() == "tok-1"

    # 캐시된 토큰을 강제로 만료시켜 재발급 경로를 검증
    daemon._token.expires_at = time.time() - 1
    assert daemon.get_token() == "tok-2"


def test_get_token_propagates_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid appkey"})

    daemon = TokenDaemon(_creds(), client=_client_with(handler))
    with pytest.raises(httpx.HTTPStatusError):
        daemon.get_token()


def test_uses_mock_domain_when_is_mock_true():
    requested_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})

    daemon = TokenDaemon(_creds(), client=_client_with(handler))
    daemon.get_token()
    assert "openapivts.koreainvestment.com" in requested_urls[0]
