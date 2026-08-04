import threading
import time

import httpx
import pytest

from messiah.broker.kis.credentials import KISCredentials
from messiah.broker.kis.redis_token_cache import RedisTokenDaemon
from messiah.broker.kis.token_daemon import TokenDaemon

redis = pytest.importorskip("redis")

_TEST_REDIS_URL = "redis://localhost:6380/15"  # messiah-redis 컨테이너, 테스트 전용 DB 인덱스


@pytest.fixture
def redis_client():
    client = redis.Redis.from_url(_TEST_REDIS_URL)
    try:
        client.ping()
    except redis.exceptions.ConnectionError:
        pytest.skip(f"{_TEST_REDIS_URL} 접속 불가 — messiah-redis 컨테이너가 떠 있어야 실행됨")
    client.flushdb()
    yield client
    client.flushdb()
    client.close()


def _creds(**overrides) -> KISCredentials:
    defaults = dict(app_key="key", app_secret="secret", account_no="12345678", is_mock=True)
    defaults.update(overrides)
    return KISCredentials(**defaults)


def _inner_token_daemon(handler) -> TokenDaemon:
    return TokenDaemon(_creds(), client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_cache_miss_issues_token_and_caches(redis_client):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 86400})

    daemon = RedisTokenDaemon(_creds(), redis_client, inner=_inner_token_daemon(handler))

    token = daemon.get_token()

    assert token == "tok-1"
    assert len(calls) == 1
    cached = redis_client.get(daemon._token_key)
    assert cached.decode() == "tok-1"


def test_cache_hit_never_calls_kis(redis_client):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("캐시 적중이면 KIS를 호출하면 안 됨")

    daemon = RedisTokenDaemon(_creds(), redis_client, inner=_inner_token_daemon(handler))
    redis_client.set(daemon._token_key, "cached-tok", ex=3600)

    assert daemon.get_token() == "cached-tok"


def test_concurrent_get_token_issues_kis_call_exactly_once(redis_client):
    # 두 RedisTokenDaemon 인스턴스가 같은 Redis 키를 공유(= 서로 다른 프로세스를 흉내) — 동시에
    # get_token()을 호출해도 실제 KIS 호출(발급)은 한 번만 일어나야 한다.
    calls = []
    calls_lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        with calls_lock:
            calls.append(request)
        time.sleep(0.3)  # 발급 지연 흉내 — 그 사이 다른 스레드가 락을 못 잡고 폴링해야 함
        return httpx.Response(200, json={"access_token": "tok-shared", "expires_in": 86400})

    daemon_a = RedisTokenDaemon(_creds(), redis_client, inner=_inner_token_daemon(handler))
    daemon_b = RedisTokenDaemon(_creds(), redis_client, inner=_inner_token_daemon(handler))

    results: list[str] = []

    def run(daemon: RedisTokenDaemon) -> None:
        results.append(daemon.get_token())

    t1 = threading.Thread(target=run, args=(daemon_a,))
    t2 = threading.Thread(target=run, args=(daemon_b,))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert len(calls) == 1
    assert results == ["tok-shared", "tok-shared"]


def test_lock_released_after_kis_failure_so_next_caller_can_retry(redis_client):
    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"msg_cd": "EGW00000", "msg1": "서버 오류"})

    daemon = RedisTokenDaemon(_creds(), redis_client, inner=_inner_token_daemon(failing_handler))

    with pytest.raises(httpx.HTTPStatusError):
        daemon.get_token()

    assert redis_client.get(daemon._lock_key) is None  # 락이 남아있으면 다음 시도가 불필요하게 막힘


def test_poll_timeout_when_lock_holder_never_writes_cache(redis_client):
    daemon = RedisTokenDaemon(
        _creds(),
        redis_client,
        inner=_inner_token_daemon(lambda r: httpx.Response(200, json={})),
        poll_interval_sec=0.05,
        poll_timeout_sec=0.3,
    )
    redis_client.set(daemon._lock_key, "1", ex=5)  # 다른 프로세스가 락을 쥐고 있는 상황을 흉내

    with pytest.raises(TimeoutError):
        daemon.get_token()


def test_cache_ttl_leaves_safety_margin_before_real_expiry(redis_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "tok-2", "expires_in": 3600})

    daemon = RedisTokenDaemon(_creds(), redis_client, inner=_inner_token_daemon(handler))
    daemon.get_token()

    ttl = redis_client.ttl(daemon._token_key)
    assert 0 < ttl <= 3600 - 300  # 5분 안전 여유만큼 실제 만료보다 먼저 캐시가 비어야 함
