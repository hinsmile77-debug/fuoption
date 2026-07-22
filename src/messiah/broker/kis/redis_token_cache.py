"""RedisTokenDaemon — TokenDaemon을 Redis 캐시로 감싸 여러 프로세스가 Access Token 하나를
공유하게 한다 (NEXT_TODO "token_daemon을 단일 공유 프로세스로 격리").

배경: KIS 접근토큰 발급(/oauth2/tokenP)은 1분당 1회로 제한된다(2026-07-21 리서치). "한 계정
다중 봇" 운영에서 L1/L3/L5 등 프로세스마다 자기 TokenDaemon으로 개별 발급을 시도하면 두 번째
프로세스부터 즉시 리밋에 걸린다 — KISBrokerAdapter 실측 세션(2026-07-22)에서 검증 스크립트를
프로세스 두 개로 나눴다가 실제로 403을 겪으며 확인한 문제.

해법: Access Token을 Redis에 캐시하고, 캐시가 비어있을 때만 SET NX로 분산 락을 잡은 프로세스
하나만 실제로 KIS에 발급을 요청한다. 락을 못 잡은 프로세스는 새로 발급을 시도하지 않고 락
보유자가 캐시에 쓸 때까지 짧게 폴링한다 — 아무도 스스로 재발급하지 않으므로 스탬피드(다 같이
동시 발급 시도)가 근본적으로 발생하지 않는다.

실제 토큰 발급 로직 자체는 재구현하지 않고 기존 TokenDaemon(단위테스트 존재)을 그대로 감싼다.
"""

from __future__ import annotations

import time
from typing import Any

from messiah.broker.kis.credentials import KISCredentials
from messiah.broker.kis.token_daemon import TokenDaemon

_DEFAULT_LOCK_TIMEOUT_SEC = 15.0  # 락 보유자가 죽어도 이 시간 뒤엔 자동 해제 (교착 방지)
_DEFAULT_POLL_INTERVAL_SEC = 0.2
_DEFAULT_POLL_TIMEOUT_SEC = 20.0  # 락 보유자의 발급이 이 시간 안에 안 끝나면 포기
_SAFETY_MARGIN_SEC = 300.0  # TokenDaemon.AccessToken.is_expired()와 동일한 여유


class RedisTokenDaemon:
    """TokenDaemon과 동일한 get_token() -> str 계약. redis_client는 동기 redis.Redis
    (decode_responses 무관 — 바이트/문자열 둘 다 처리)를 그대로 주입받는다(테스트에서 실제
    Redis로 교체하기 쉬움)."""

    def __init__(
        self,
        creds: KISCredentials,
        redis_client: Any,
        key_prefix: str = "kis:token",
        inner: TokenDaemon | None = None,
        lock_timeout_sec: float = _DEFAULT_LOCK_TIMEOUT_SEC,
        poll_interval_sec: float = _DEFAULT_POLL_INTERVAL_SEC,
        poll_timeout_sec: float = _DEFAULT_POLL_TIMEOUT_SEC,
    ) -> None:
        env = "vps" if creds.is_mock else "real"
        self._redis = redis_client
        self._token_key = f"{key_prefix}:{creds.account_no}:{env}"
        self._lock_key = f"{self._token_key}:lock"
        self._inner = inner or TokenDaemon(creds)
        self._lock_timeout_sec = lock_timeout_sec
        self._poll_interval_sec = poll_interval_sec
        self._poll_timeout_sec = poll_timeout_sec

    def get_token(self) -> str:
        """
        계산: 캐시 적중 시 즉시 반환. 미스면 SET NX로 락 시도 — 성공하면 실제 발급 후 캐싱(TTL은
             실제 만료 시각에서 안전 여유(5분)를 뺀 값, TokenDaemon.is_expired()와 동일 기준이라
             Redis 캐시가 실제 토큰보다 먼저 만료돼 다음 get_token()이 선제적으로 갱신한다).
             실패하면(다른 프로세스가 락 보유 중) 캐시에 값이 나타날 때까지 폴링.
        실패 조건: 락 보유자의 발급이 poll_timeout_sec 안에 끝나지 않으면(락 보유자 자체가
             KIS 에러로 실패한 경우 포함) TimeoutError — 원본 KIS 에러는 락 보유자 쪽에서만
             전파되고 폴링 대기자는 그 에러 내용을 알 수 없다(트레이드오프, 모듈 docstring 참고).
        """
        cached = self._get_cached()
        if cached is not None:
            return cached

        acquired = self._redis.set(self._lock_key, "1", nx=True, ex=int(self._lock_timeout_sec))
        if acquired:
            try:
                token = self._inner.get_token()
                ttl = self._cache_ttl_seconds()
                self._redis.set(self._token_key, token, ex=ttl)
                return token
            finally:
                self._redis.delete(self._lock_key)

        deadline = time.monotonic() + self._poll_timeout_sec
        while time.monotonic() < deadline:
            time.sleep(self._poll_interval_sec)
            cached = self._get_cached()
            if cached is not None:
                return cached
        raise TimeoutError(
            f"다른 프로세스의 토큰 발급을 {self._poll_timeout_sec}초 기다렸지만 캐시에 값이"
            " 나타나지 않음 — 락 보유자 프로세스가 KIS 에러로 실패했을 수 있음"
        )

    def _get_cached(self) -> str | None:
        cached = self._redis.get(self._token_key)
        if cached is None:
            return None
        return cached.decode() if isinstance(cached, bytes) else cached

    def _cache_ttl_seconds(self) -> int:
        token = self._inner.current_token
        assert token is not None  # get_token() 직후라 항상 채워져 있음
        ttl = int(token.expires_at - time.time() - _SAFETY_MARGIN_SEC)
        return max(ttl, 60)
