"""완성봉 유실이 **일어나기 전에** 뜨는 사전 경보 (2026-09-10 G-57).

## 무슨 일이 있었나

2026-09-07 14:54~14:55에 국소 지연이 5분봉 하나를 짧게 확정시켜 거래량 162가 빠졌다
(`ComposerFlushedIncomplete` → `ComposerLateBarDropped`). 그런데 **그날의 하루 단위
분위수는 전부 정상 범위였다** — `TickDeliveryLatency` p99 1.030초 · 최대 3.017초.
하루를 한 덩어리로 요약하는 계기는 「짧고 굵은」 스파이크를 구조적으로 못 본다.
그래서 사람이 장 마감 뒤 로그를 좁게 되짚어서야 원인을 짚었다.

이 모듈은 **5분 롤링 창**으로 같은 것을 본다. 창 안의 최댓값이 임계를 넘는 순간
경보가 뜨고, 그 시점은 합성기가 아직 상한(`MAX_CONSTITUENT_WAIT_SECONDS`)을
소진하지 않은 때다 — 즉 **유실 전**이다.

## 무엇을 재는가 — 틱 지연이 아니라 1분봉 발행 지연이다

G-57 원안은 `TickDeliveryLatency`의 롤링 p99에 「상한의 80%」를 걸자고 했다.
**그 조합은 원리적으로 울리지 않는다.** 틱 지연은 하루 최대가 1~3초대인데(2026-09-09
실측 p99 1.022초 · 최대 1.120초) 상한의 80%는 8초다 — 소비처 없는 축을 만드는 것이고,
이 저장소가 2026-08-14 F-4에서 그 대가를 이미 치렀다(유도 경로를 만들어 두고 6일간 0회).

합성기가 실제로 기다리는 것은 **그 버킷의 마지막 1분봉**이고, 상한이 재는 것도 그것이다
(`data/close_grace` 주석). 그래서 이 창의 입력은 1분봉 발행 지연
(`features/engine`의 `publish_offset_ms`, M1 계열)이다. 같은 경계를 같은 자로 잰다.

## 임계는 상한의 70%다 — 80%가 아니다

원안은 80%를 제안하면서 *"임계값은 실측 분포로 조정 필요"* 라고 단서를 달았다. 조정했다.
14거래일 5,726건(1분봉 발행 지연) 위에서 **경계 진입 시 1회만 세는**(edge-triggered)
경보를 시뮬레이션한 결과다:

    임계    발생   발생일수  거래일당   잡은 날
    4.0초    20      12      1.43     거의 매일 — 배경이 된다
    5.0초    10       8      0.71     이틀에 한 번
    6.0초     3       3      0.21     0901 · 0907 · 0908
    7.0초     3       3      0.21     0901 · 0907 · 0908
    8.0초     1       1      0.07     0901 **only**

**80%(8.0초)는 정작 유실이 난 09-07을 놓친다** — 그날 최대가 7.88초였다. G-57을 만들게 한
사건에 안 울리는 임계는 임계가 아니다. 6.0~7.0초 구간에는 표본이 없어(다음 값이 7.01초)
70%는 **평탄면의 위쪽 끝**이다 — 세 근접 사례를 다 잡으면서 가장 좁다. 5거래일당 1회는
WARNING이 배경이 되지 않는 빈도다("매일 우는 경보는 경보가 아니라 배경이고, 사람은 그것을
무시하는 법을 배운다" — `ui/app` F-3 주석).

비율로 두는 이유: 상한이 다시 바뀌면 임계가 **따라 움직여야** 한다. 원안이 「80%」라고 쓴
것의 요지는 숫자가 아니라 그 연동이었다.

## 이 경보가 못 하는 것 — 과대 선전하지 않는다

경보는 **늦은 봉이 도착하는 순간** 뜬다(그때 그 봉의 발행 지연이 측정되므로). 그래서
말하는 것은 정확히 「이번에 상한까지 N초 남기고 들어왔다」 = **아슬아슬하게 안 잃었다**다.
2026-09-01 · 09-07 · 09-08 세 건이 그 형태다(여유 1.27 · 2.12 · 2.99초).

반대로 **봉이 상한 안에 아예 안 오는 경우는 이 축이 못 잡는다** — 발행이 없으니 표본도
없다. 08-13형(마감 무거래 분)이 그것이고, 그 자리는 `ComposerFlushedIncomplete`가 맡는다.
둘은 짝이다: 이쪽은 「여유가 얇아지고 있다」, 저쪽은 「이번엔 못 기다렸다」.

## 실측 검증 (2026-09-10)

이 구현을 14거래일 실측 위에 그대로 돌린 결과 — 시뮬레이션과 일치한다:

    09-01  최댓값 8.73초  여유 1.27초(12.7%)  15:28:08
    09-07  최댓값 7.88초  여유 2.12초(21.2%)  14:55:08   ← 그날 유실이 난 그 순간이다
    09-08  최댓값 7.01초  여유 2.99초(29.9%)  13:22:07

합계 3건 / 14거래일 = 거래일당 0.21건.

## 막지 않는다 (R18)

이 모듈은 **로그만 남긴다.** 어떤 판단도 차단하지 않고 어떤 값도 바꾸지 않는다 —
차단 계층은 Meta-Labeler·Risk·KillSwitch 3개로 고정이다(R18). 신설 축이므로 20거래일
섀도 관찰 뒤에 사람이 승격 여부를 정한다. 그 관찰 항목은 `dev_memory/NEXT_TODO.md`에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from messiah.data.close_grace import MAX_CONSTITUENT_WAIT_SECONDS

#: 창의 폭 — G-57 원안의 「5분 롤링」 그대로. 1분봉이 입력이므로 창 안 표본은 5개 안팎이고,
#: 그 크기에서 p99는 최댓값과 같다. 그래서 이 모듈은 분위수라 부르지 않고 **최댓값**을 쓴다
#: (표본 5개에 p99라 적으면 읽는 사람이 실제보다 정교한 통계로 오해한다).
WINDOW_SECONDS = 300.0

#: 상한의 몇 할에서 경보를 낼지. 근거는 모듈 docstring의 시뮬레이션 표.
SPIKE_THRESHOLD_RATIO = 0.70


def spike_threshold_seconds() -> float:
    """지금 상한 기준의 경보 문턱 — 상한이 바뀌면 같이 움직인다."""
    return MAX_CONSTITUENT_WAIT_SECONDS * SPIKE_THRESHOLD_RATIO


@dataclass(frozen=True)
class DelaySpike:
    """경보 한 건이 말하는 것 — 로그 필드가 이 값들로 채워진다."""

    window_max_seconds: float
    threshold_seconds: float
    bound_seconds: float
    #: 상한까지 남은 여유. 0에 가까울수록 다음 버킷이 짧게 확정될 위험이 크다.
    headroom_seconds: float
    samples: int
    worst_at: datetime

    @property
    def headroom_ratio(self) -> float:
        """여유를 비율로도 낸다 — 상한이 바뀌면 같은 초 수가 같은 위험이 아니다."""
        return self.headroom_seconds / self.bound_seconds if self.bound_seconds else 0.0


class DelaySpikeWatch:
    """1분봉 발행 지연의 5분 롤링 최댓값이 문턱을 **넘는 순간**만 잡는다.

    ## 경계 진입에서 한 번만 센다

    창이 5분이라 한 번의 스파이크가 5분 동안 창에 남는다. 표본마다 경보를 내면 스파이크
    하나가 경보 다섯 건이 되고, 그러면 「사고 한 번」이 「경보 개수」로 부풀어 보인다 —
    2026-08-19 F-4가 등록부에서 겪은 것과 같은 형태다(사고 하나가 계기 수만큼 재발이 됨).
    그래서 넘어간 상태에 들어갈 때 한 번 내고, 창의 최댓값이 문턱 아래로 내려오면 다시
    무장한다.
    """

    def __init__(
        self,
        *,
        window_seconds: float = WINDOW_SECONDS,
        threshold_seconds: float | None = None,
    ) -> None:
        self._window = timedelta(seconds=window_seconds)
        self._threshold = (
            spike_threshold_seconds() if threshold_seconds is None else threshold_seconds
        )
        self._samples: list[tuple[datetime, float]] = []
        self._breached = False

    @property
    def threshold_seconds(self) -> float:
        return self._threshold

    def observe(self, moment: datetime, delay_seconds: float) -> DelaySpike | None:
        """표본 하나를 넣고, **이번에 경계를 넘었으면** 경보를 돌려준다(아니면 None).

        `moment`는 그 표본을 관측한 시각이다. 창은 시계가 아니라 **표본의 시각**으로
        접는다 — 발행이 멈춘 구간에서 창이 저절로 비지 않게 하려면 그래야 한다.
        """
        self._samples.append((moment, delay_seconds))
        cutoff = moment - self._window
        self._samples = [(t, v) for t, v in self._samples if t >= cutoff]

        worst_at, window_max = max(self._samples, key=lambda tv: tv[1])
        if window_max <= self._threshold:
            self._breached = False
            return None
        if self._breached:
            return None  # 이미 넘어간 상태다 — 같은 스파이크를 다시 세지 않는다
        self._breached = True
        return DelaySpike(
            window_max_seconds=round(window_max, 3),
            threshold_seconds=round(self._threshold, 3),
            bound_seconds=MAX_CONSTITUENT_WAIT_SECONDS,
            headroom_seconds=round(MAX_CONSTITUENT_WAIT_SECONDS - window_max, 3),
            samples=len(self._samples),
            worst_at=worst_at,
        )
