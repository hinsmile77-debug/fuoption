"""거래소 시각과 로컬 시계의 어긋남 계측 (2026-08-05 일일점검 대응).

## 왜 이 모듈이 생겼나

2026-08-04 로그를 되짚다 **1분봉이 매분 로컬 :50초 근처에서 롤오버**한다는 것이 나왔다.
`MinuteBarAggregator`는 틱의 `ts_exchange`가 분 경계를 넘을 때 봉을 닫으므로(`data/
normalizer.py`), 그 :50은 곧 **거래소 시각이 로컬보다 ~10초 앞서 있었다**는 뜻이다. 같은
방법으로 이전 거래일을 재니 하루 4~5초씩 단조 증가하고 있었다(07-27 +13.8s → 07-30 +26.2s).
외부 기준(`w32tm /stripchart`)으로 확인한 결과 로컬 시계가 실제 시각보다 **14.41초 느렸고**,
원인은 Windows Time 서비스가 꺼져 있었던 것이다.

`self_check`에 "timezone" 항목이 있었지만 그건 **UTC 오프셋이 9시간인지만** 봤다 —
SYSTEM.md §4-6이 기동 자가 점검에 요구하는 "시간 동기"는 한 번도 측정된 적이 없었다.
[[measure-known-limitations]]와 같은 형태다: 요건은 문서에 있었고 검사는 이름만 있었다.

## 왜 최댓값이 추정량인가

KIS 체결 프레임의 영업시간 필드는 **초 단위(HHMMSS)** 라 소수점이 없다. 참 스큐를 s,
네트워크 지연을 d, 거래소 진짜 시각을 t라 하면

    표본 = floor(t) − (t − s + d) = s − d − frac(t)

즉 **모든 표본은 s 이하**이고, `frac(t)→0`·`d→0`인 표본에서 s에 가장 가까워진다. 그래서
중앙값이 아니라 **최댓값**이 s의 자연스러운 추정량이고, 그 값은 항상 **참값의 하한**이다.

이 방향이 안전한 쪽이라는 점이 중요하다 — 이 추정값의 소비처인
`MultiHorizonBarComposer`는 "거래소 시각으로 경계가 지났는가"를 판정하는 데 쓰는데,
스큐를 실제보다 작게 잡으면 **덜 기다릴** 뿐이고, 참 스큐가 더 크다는 것은 경계가 이미
더 일찍 지났다는 뜻이라 판정이 틀리지 않는다.

## 왜 롤링 창인가

시계는 하루 중에도 점프한다(Windows Time이 동기하는 순간). 세션 전체 최댓값을 쓰면 동기
이전의 큰 값이 하루 종일 남아 "지금" 스큐를 잘못 말한다. 최근 표본만 본다.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime

from messiah.core.timeutil import ensure_aware

# 롤링 창 크기 — 선물 체결 프레임은 초당 수 건이라 600표본은 대략 최근 수 분이다.
# 시계 점프를 몇 분 안에 따라잡을 만큼 짧고, `frac(t)→0`인 표본을 포함할 만큼 길다.
_WINDOW = 600

# 이 표본 수가 모이기 전에는 추정값을 내지 않는다 — 표본 1개는 [s−1, s] 구간의 어디든이라
# 최댓값 추정이 성립하지 않는다. 30표본이면 `frac(t)`가 0에 충분히 가까운 것이 섞인다.
MIN_SAMPLES = 30

# 이 값을 넘으면 "완성봉 규율의 500ms 유예"가 의미를 잃는다 — 경보 임계.
# 2026-08-05 실측 기준 정상값은 |스큐| < 0.5초(NTP 동기 직후 0.0006초)이며, 2.0초는
# 그보다 넉넉한 미검증 초기값이다.
WARN_THRESHOLD_SECONDS = 2.0


class ClockSkewTracker:
    """`ts_exchange − 수신 로컬시각`의 롤링 최댓값 — 양수면 거래소가 앞선다(로컬이 느리다).

    상태만 들고 있고 로깅은 하지 않는다 — 언제 몇 번 로그를 남길지는 호출측(수집기)의
    정책이고, 이 클래스는 순수 로직이라 테스트가 시계를 안 탄다.
    """

    def __init__(self, *, window: int = _WINDOW) -> None:
        self._samples: deque[float] = deque(maxlen=window)

    def observe(self, ts_exchange: datetime, received: datetime) -> None:
        """
        입력: 둘 다 tz-aware여야 한다(SYSTEM.md R3) — naive면 ValueError.
        계산: 두 시각의 차이를 초로 표본에 넣는다. 프레임당 한 번만 부르는 것이 맞다
             (같은 프레임의 N건은 같은 순간에 도착한 것이라 표본이 아니라 중복이다).
        """
        ensure_aware(ts_exchange)
        ensure_aware(received)
        self._samples.append((ts_exchange - received).total_seconds())

    @property
    def samples(self) -> int:
        return len(self._samples)

    @property
    def seconds(self) -> float | None:
        """추정 스큐(초). 표본이 `MIN_SAMPLES` 미만이면 **None** — "0초"와 구분한다(L18)."""
        if len(self._samples) < MIN_SAMPLES:
            return None
        return max(self._samples)

    @property
    def exceeds_threshold(self) -> bool:
        """경보 대상인가 — 추정값이 아직 없으면 False(모르는 것을 나쁜 쪽으로도 가정 안 함)."""
        skew = self.seconds
        return skew is not None and abs(skew) > WARN_THRESHOLD_SECONDS
