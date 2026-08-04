import threading
import time

import pytest

from messiah.broker.kis.redis_rate_limiter import RedisRateLimiter

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


def _interval_ms(redis_client, limiter: RedisRateLimiter) -> float:
    raw = redis_client.get(limiter._interval_key)
    return limiter._min_interval_ms if raw is None else float(raw)


def test_wait_paces_calls_to_respect_min_interval(redis_client):
    limiter = RedisRateLimiter(min_interval=0.2, redis_client=redis_client)
    start = time.monotonic()
    for _ in range(3):
        limiter.wait()
    span = time.monotonic() - start
    assert span >= 0.2 * 2 * 0.8  # 지터 감안 — 개별 간격이 아니라 총 스팬으로 검증


def test_wait_serializes_two_independent_limiter_instances(redis_client):
    # 서로 다른 프로세스를 흉내: 같은 Redis 키를 공유하는 두 RedisRateLimiter 인스턴스를
    # 스레드 두 개에서 각각 3번씩 wait() — 합쳐서 6번의 호출이 여전히 최소 간격을 지켜야 한다.
    call_times: list[float] = []
    lock = threading.Lock()

    def hammer(limiter: RedisRateLimiter) -> None:
        for _ in range(3):
            limiter.wait()
            with lock:
                call_times.append(time.monotonic())

    limiter_a = RedisRateLimiter(min_interval=0.15, redis_client=redis_client)
    limiter_b = RedisRateLimiter(min_interval=0.15, redis_client=redis_client)

    t1 = threading.Thread(target=hammer, args=(limiter_a,))
    t2 = threading.Thread(target=hammer, args=(limiter_b,))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    call_times.sort()
    assert len(call_times) == 6
    total_span = call_times[-1] - call_times[0]
    assert total_span >= 0.15 * 5 * 0.8


def test_record_rate_limit_hit_widens_interval(redis_client):
    limiter = RedisRateLimiter(min_interval=1.0, redis_client=redis_client)
    limiter.record_rate_limit_hit()
    assert _interval_ms(redis_client, limiter) == pytest.approx(1500.0)
    limiter.record_rate_limit_hit()
    assert _interval_ms(redis_client, limiter) == pytest.approx(2250.0)


def test_record_rate_limit_hit_caps_at_max_multiplier(redis_client):
    limiter = RedisRateLimiter(min_interval=1.0, redis_client=redis_client)
    for _ in range(20):
        limiter.record_rate_limit_hit()
    assert _interval_ms(redis_client, limiter) == pytest.approx(4000.0)


def test_record_success_recovers_after_sustained_success_threshold(redis_client):
    limiter = RedisRateLimiter(min_interval=1.0, redis_client=redis_client)
    limiter.record_rate_limit_hit()  # 1000 -> 1500ms
    for _ in range(19):
        limiter.record_success()
    assert _interval_ms(redis_client, limiter) == pytest.approx(1500.0)  # 임계값(20건) 미달
    limiter.record_success()  # 20번째 — 이제 한 단계 되돌림
    assert _interval_ms(redis_client, limiter) == pytest.approx(1500.0 * 0.9)


def test_record_success_never_recovers_below_min_interval(redis_client):
    limiter = RedisRateLimiter(min_interval=1.0, redis_client=redis_client)
    redis_client.set(limiter._interval_key, 1050.0)  # 되돌림 한 스텝이면 min 밑으로 내려갈 경계
    for _ in range(20):
        limiter.record_success()
    assert _interval_ms(redis_client, limiter) == pytest.approx(1000.0)


def test_record_success_is_noop_when_not_widened(redis_client):
    limiter = RedisRateLimiter(min_interval=1.0, redis_client=redis_client)
    for _ in range(100):
        limiter.record_success()
    assert _interval_ms(redis_client, limiter) == pytest.approx(1000.0)


def test_disabled_when_min_interval_is_zero(redis_client):
    limiter = RedisRateLimiter(min_interval=0.0, redis_client=redis_client)
    start = time.monotonic()
    limiter.wait()
    limiter.record_rate_limit_hit()
    limiter.record_success()
    assert time.monotonic() - start < 0.05
    assert redis_client.get(limiter._interval_key) is None  # Redis에 아무것도 안 씀
