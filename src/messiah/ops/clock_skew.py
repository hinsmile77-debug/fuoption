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

## 같은 표본에서 **수신 지연의 초과분**이 나온다 (2026-08-05 2차)

위 유도를 뒤집으면 유예를 정할 값이 공짜로 나온다. ŝ = max(표본)이라 할 때

    ŝ − 표본ᵢ = (dᵢ + frac(tᵢ)) − min(d + frac(t))

즉 **절대 지연이 아니라 "가장 빠른 프레임 대비 얼마나 늦었나"**다. 이 프로젝트가 필요한
값이 정확히 이쪽이라는 점이 중요하다:

봉 경계 판정은 `거래소시각 ≈ 로컬시각 + ŝ`로 한다(`MultiHorizonBarComposer`,
`MinuteBarAggregator.flush_due`). 그런데 ŝ 자체가 **최소 지연을 이미 흡수**하고 있다 —
가장 빨리 도착한 프레임을 기준으로 잡힌 값이기 때문이다. 그래서 유예가 덮어야 하는 것은
절대 지연 d가 아니라 **d − d_min**, 곧 이 값이다.

여전히 안전한 방향이다: frac(t)가 섞여 최대 1초까지 **과대평가**되므로, 이 분포로 유예를
잡으면 필요한 것보다 더 기다릴 뿐 봉이 잘리지 않는다.

이 분포가 필요해진 이유: 2026-08-05 장중점검에서 상위 Horizon 손상의 근본 처방으로
"1분봉을 틱 도착이 아니라 시각으로 닫는다"가 나왔는데, 그러려면 **유예를 몇 초로 둘지**를
알아야 한다. 그런데 그 답을 줄 측정이 이 프로젝트에 하나도 없었다 — 틱 아카이브는
거래소 시각만 갖고 있고(`data/tick_archiver.py`) 수신 시각을 안 남긴다.
[[measure-known-limitations]] — 재기 전까지는 임계를 정하지 않는다.

**창이 아니라 세션 전체**로 모은다. 스큐 추정은 "지금"이 필요해서 롤링이지만, 지연 분포는
"오늘 이 회선이 어땠나"라 장 시작의 혼잡까지 들어가야 한다. 대신 각 표본의 기준 ŝ는 그
시점의 창 최댓값이라, 시계가 하루 중 점프해도 분포가 통째로 밀리지 않는다.
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

# 수신 지연 표본을 세션 전체로 몇 개까지 들고 있을지. 프레임이 초당 수 건이라 정규장
# 405분이면 대략 10만 건 규모인데, 분위수를 보는 데 그만큼은 필요 없다 — 2만이면 p99가
# 200표본으로 뒷받침된다. float 2만 개는 메모리에서 무시할 크기다.
_LATENCY_CAPACITY = 20_000


def _quantile(ordered: list[float], q: float) -> float:
    """정렬된 표본의 분위수 — `statistics.quantiles`는 보간을 하고 경계에서 표본 밖 값을
    낼 수 있다. 여기서는 **실제로 관측된 값**만 돌려주는 쪽이 맞다(유예를 정하는 근거라
    "관측된 적 없는 지연"을 기준 삼으면 안 된다)."""
    if not ordered:
        raise ValueError("빈 표본의 분위수는 정의되지 않는다")
    index = min(len(ordered) - 1, int(q * len(ordered)))
    return ordered[index]


class ClockSkewTracker:
    """`ts_exchange − 수신 로컬시각`의 롤링 최댓값 — 양수면 거래소가 앞선다(로컬이 느리다).

    상태만 들고 있고 로깅은 하지 않는다 — 언제 몇 번 로그를 남길지는 호출측(수집기)의
    정책이고, 이 클래스는 순수 로직이라 테스트가 시계를 안 탄다.
    """

    def __init__(self, *, window: int = _WINDOW, latency_capacity: int = _LATENCY_CAPACITY) -> None:
        self._samples: deque[float] = deque(maxlen=window)
        self._latencies: deque[float] = deque(maxlen=latency_capacity)

    def observe(self, ts_exchange: datetime, received: datetime) -> None:
        """
        입력: 둘 다 tz-aware여야 한다(SYSTEM.md R3) — naive면 ValueError.
        계산: 두 시각의 차이를 초로 표본에 넣는다. 프레임당 한 번만 부르는 것이 맞다
             (같은 프레임의 N건은 같은 순간에 도착한 것이라 표본이 아니라 중복이다).
             같은 자리에서 수신 지연 상한(`ŝ − 표본`)도 세션 전체 목록에 쌓는다 —
             기준 ŝ는 **그 시점의** 창 최댓값이라 하루 중 시계 점프에 분포가 안 밀린다.
        """
        ensure_aware(ts_exchange)
        ensure_aware(received)
        delta = (ts_exchange - received).total_seconds()
        self._samples.append(delta)
        self._latencies.append(max(self._samples) - delta)

    @property
    def samples(self) -> int:
        return len(self._samples)

    @property
    def seconds(self) -> float | None:
        """추정 스큐(초). 표본이 `MIN_SAMPLES` 미만이면 **None** — "0초"와 구분한다(L18)."""
        if len(self._samples) < MIN_SAMPLES:
            return None
        return max(self._samples)

    def delivery_latency_seconds(self) -> dict[str, float] | None:
        """수신 지연 **초과분**의 분위수(p50/p90/p99/max)와 표본 수. 표본 부족이면 None.

        해석: 절대 지연이 아니라 **가장 빠른 프레임 대비 초과분**이다(모듈 docstring의 유도).
             봉 경계 판정이 쓰는 스큐 추정 ŝ가 최소 지연을 이미 흡수하므로, 유예가 덮어야
             하는 값이 정확히 이쪽이다. frac(t)만큼(최대 1초) 과대평가되는 안전한 방향이다.

        소비처: `MinuteBarAggregator.flush_due()`의 유예를 정하는 근거이자,
               등록부의 **전제 지표**(`ops/fix_verification.py` "전제를 채점한다").
        """
        if len(self._latencies) < MIN_SAMPLES:
            return None
        ordered = sorted(self._latencies)
        return {
            "p50": _quantile(ordered, 0.50),
            "p90": _quantile(ordered, 0.90),
            "p99": _quantile(ordered, 0.99),
            "max": ordered[-1],
            "samples": float(len(ordered)),
        }

    @property
    def exceeds_threshold(self) -> bool:
        """경보 대상인가 — 추정값이 아직 없으면 False(모르는 것을 나쁜 쪽으로도 가정 안 함)."""
        skew = self.seconds
        return skew is not None and abs(skew) > WARN_THRESHOLD_SECONDS
