"""RedisRateLimiter — rest_client._RateLimiter와 같은 계약(wait/record_rate_limit_hit/
record_success)을 Redis에 백업해 여러 프로세스가 KIS 계좌 하나의 유량 예산을 공유한다
(NEXT_TODO "RateLimiter 카운터는 프로세스 로컬이 아니라 Redis 전역 카운터 기반").

배경: KIS 유량 제한은 계좌(앱키) 단위다(모의 1건/초, 실전 18건/초 — kis-api-rate-limit-policy
메모리). rest_client._RateLimiter는 프로세스 하나 안에서만 페이싱을 강제하므로, L1/L3/L5 등
여러 프로세스가 REST를 나눠 호출하면 프로세스별로는 1건/초를 지켜도 계좌 전체로는 초과할 수
있다 — 로컬 카운터로는 근본적으로 막을 수 없는 문제.

해법: "다음 호출 가능 시각"과 "현재 페이싱 간격/연속성공 횟수"를 Redis 키에 저장하고, 모든
읽기·계산·쓰기를 Lua 스크립트 하나로 묶어 원자적으로 실행한다(Redis는 스크립트 실행 중 단일
스레드로 동작하므로 여러 프로세스가 동시에 EVAL해도 경쟁 조건이 생기지 않는다) — _RateLimiter의
적응형 백오프 상수(_BACKOFF_MULTIPLIER 등)를 그대로 재사용해 동작이 갈라지지 않게 한다.
"""

from __future__ import annotations

import time
from typing import Any

from messiah.broker.kis.rest_client import _RateLimiter

# _RateLimiter와 정확히 같은 상수를 재사용 — 로컬/Redis 버전이 서로 다른 백오프 동작을 하면
# "같은 계좌인데 프로세스에 따라 페이싱이 다르다"는 혼란만 생긴다.
_BACKOFF_MULTIPLIER = _RateLimiter._BACKOFF_MULTIPLIER
_MAX_INTERVAL_MULTIPLIER = _RateLimiter._MAX_INTERVAL_MULTIPLIER
_RECOVERY_SUCCESS_THRESHOLD = _RateLimiter._RECOVERY_SUCCESS_THRESHOLD
_RECOVERY_FACTOR = _RateLimiter._RECOVERY_FACTOR

# KEYS[1]=next_allowed_ms, KEYS[2]=interval_ms
# ARGV[1]=min_interval_ms, ARGV[2]=now_ms(호출측 로컬시각)
# 반환값: 이 호출자가 실제로 요청을 보내도 되는 시각(ms) — 호출측이 자기 로컬 시계 기준으로 그
# 시각까지 sleep한다. Redis TIME을 안 쓰는 이유: 호출자 로컬시각을 기준으로 sleep해야 하므로,
# Redis 서버시각과 굳이 동기화할 필요가 없고 오히려 두 시계 사이 오차만 생긴다(같은 PC/근거리
# 프로세스 전제 — kis-api-rate-limit-policy 메모리의 "한 PC에서 여러 계좌" 운영 모델과 일치).
_WAIT_SCRIPT = """
local next_allowed = tonumber(redis.call('GET', KEYS[1]) or '0')
local interval = tonumber(redis.call('GET', KEYS[2]) or ARGV[1])
local now_ms = tonumber(ARGV[2])
local start = math.max(now_ms, next_allowed)
redis.call('SET', KEYS[1], start + interval, 'PX', 60000)
return tostring(start)
"""

# KEYS[1]=interval_ms, KEYS[2]=streak / ARGV[1]=min_interval_ms
_RATE_LIMIT_HIT_SCRIPT = """
local min_interval = tonumber(ARGV[1])
local current = tonumber(redis.call('GET', KEYS[1]) or min_interval)
local widened = math.min(math.max(current, min_interval) * %f, min_interval * %f)
redis.call('SET', KEYS[1], widened, 'EX', 3600)
redis.call('SET', KEYS[2], '0', 'EX', 3600)
return tostring(widened)
""" % (_BACKOFF_MULTIPLIER, _MAX_INTERVAL_MULTIPLIER)

# KEYS[1]=interval_ms, KEYS[2]=streak / ARGV[1]=min_interval_ms
_RECORD_SUCCESS_SCRIPT = """
local min_interval = tonumber(ARGV[1])
local current = tonumber(redis.call('GET', KEYS[1]) or min_interval)
if current <= min_interval then
    return tostring(current)
end
local streak = tonumber(redis.call('INCR', KEYS[2]))
redis.call('EXPIRE', KEYS[2], 3600)
if streak >= %d then
    redis.call('SET', KEYS[2], '0', 'EX', 3600)
    local reduced = math.max(current * %f, min_interval)
    redis.call('SET', KEYS[1], reduced, 'EX', 3600)
    return tostring(reduced)
end
return tostring(current)
""" % (_RECOVERY_SUCCESS_THRESHOLD, _RECOVERY_FACTOR)


class RedisRateLimiter:
    """rest_client._RateLimiter와 같은 인터페이스(wait/record_rate_limit_hit/record_success) —
    KISRestClient(rate_limiter=...)에 그대로 주입 가능. redis_client는 동기 redis.Redis 인스턴스."""

    def __init__(
        self, min_interval: float, redis_client: Any, key_prefix: str = "kis:ratelimit"
    ) -> None:
        self._min_interval_ms = min_interval * 1000.0
        self._redis = redis_client
        self._next_allowed_key = f"{key_prefix}:next_allowed_ms"
        self._interval_key = f"{key_prefix}:interval_ms"
        self._streak_key = f"{key_prefix}:streak"
        self._wait_script = redis_client.register_script(_WAIT_SCRIPT)
        self._rate_limit_hit_script = redis_client.register_script(_RATE_LIMIT_HIT_SCRIPT)
        self._record_success_script = redis_client.register_script(_RECORD_SUCCESS_SCRIPT)

    def wait(self) -> None:
        if self._min_interval_ms <= 0:
            return
        now_ms = time.time() * 1000.0
        start_ms = float(
            self._wait_script(
                keys=[self._next_allowed_key, self._interval_key],
                args=[self._min_interval_ms, now_ms],
            )
        )
        delay = (start_ms - now_ms) / 1000.0
        if delay > 0:
            time.sleep(delay)

    def record_rate_limit_hit(self) -> None:
        if self._min_interval_ms <= 0:
            return
        self._rate_limit_hit_script(
            keys=[self._interval_key, self._streak_key], args=[self._min_interval_ms]
        )

    def record_success(self) -> None:
        if self._min_interval_ms <= 0:
            return
        self._record_success_script(
            keys=[self._interval_key, self._streak_key], args=[self._min_interval_ms]
        )
