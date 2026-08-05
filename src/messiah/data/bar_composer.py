"""다중 Horizon 완성봉 합성 — Master Plan Ver 2.0 §9 W6~8 "시간 바 생성".

`TickCollector`(+`MinuteBarAggregator`)가 만드는 1분봉(`bar.1m.{symbol}`)만이 원시 틱을
직접 본다. 3/5/10/15/30분봉은 그 1분봉들을 합성해서 만든다 — OHLCV는 구성 1분봉들만으로
정확히 재구성 가능하다(open=첫 봉의 open, high/low=구성봉들의 max/min, close=마지막 봉의
close, volume=합계). 원시 틱을 다시 구독할 필요가 없다.

**봉 확정은 "다음 1분봉 도착"이 아니라 시각 기반**(Ver 1.2 §2.2 완성봉 규율: 거래소 시각
경계 + 지연 틱 유예 500ms 후 확정). `MinuteBarAggregator`는 틱이 없는 분은 봉 자체를 발행하지
않으므로, 상위 Horizon 경계 판정을 "구성봉 개수 세기"로만 하면 조용한 구간에서 경계가 밀린다
— 이미 테스트를 거친 `core/scheduler.py`의 `FixedTickScheduler`를 Horizon마다 하나씩 붙여
절대시각 기준으로 "지금 이 버킷을 닫아라"라는 트리거만 받고, 실제 봉 경계(`bar_open_kst`)는
누적된 1분봉들의 시각으로부터 계산한다 — 스케줄러 트리거 시점의 미세한 지연에 값이
좌우되지 않는다.

KST(UTC+9:00)는 5/10/15/30분 격자와 정수 배로 맞아떨어져(540분 = 9시간이 5·10·15·30 전부로
나누어떨어짐) UTC epoch 기준 정렬이 곧 KST 벽시계 경계와 일치한다 — 별도의 장 시작 시각(09:00)
앵커링이 필요 없다.

## 왜 스큐를 보는가 (2026-08-05)

위 설계에는 **숨은 전제**가 하나 있었다: "로컬 시계로 경계+500ms면 그 버킷의 1분봉이 전부
도착해 있다". 그런데 봉 경계는 **거래소 시각**(`ts_exchange`)으로 정해지고 스케줄러는
**로컬 시계**로 쏜다. 둘이 어긋나면 전제가 깨진다.

2026-08-04 실측으로 이 PC의 시계는 거래소보다 **9.7초 느렸다**(그날 기준, 하루 4~5초씩
증가 중이었다 — `ops/clock_skew.py`). 느린 쪽은 우연히 안전했다: 거래소 시각이 앞서므로
1분봉이 로컬 경계보다 **먼저** 도착한다. 부호가 뒤집혀 **로컬이 앞서면** 매 버킷의 마지막
1분봉이 flush 뒤에 도착해, ① 그 버킷은 한 봉 모자란 채 확정되고 ② 늦게 온 봉이
`floor_to_horizon`상 **같은 버킷 시작 시각**으로 새 버킷을 열어, 다음 flush에서 같은 시각의
합성봉이 한 번 더 나간다.

②의 결과가 특히 나쁘다. `ParquetArchiver`는 `(bar_open_kst, horizon)`으로
`unique(keep="last")` 하므로(`data/archiver.py`) **두 줄이 남는 게 아니라 나중 것이 먼저
것을 덮어쓴다** — 즉 5분봉 하나가 구성봉 4개짜리에서 1개짜리로 조용히 바뀐다. 사람이
행 수를 세어 봐도 이상이 없고, 봉 연속성 검사도 통과한다. 조용히, 매 버킷마다.

세 겹으로 막는다:

1. **봉 도착 기반 롤오버** (`handle_one_minute_bar`) — 다른 버킷에 속하는 봉이 오면 그
   자리에서 이전 버킷을 확정한다. 이 경로는 **시계를 전혀 안 본다**. `compose_offline`과
   같은 규칙이라 실시간·오프라인 산출물이 정의상 일치한다.
2. **스큐만큼 flush 지연** (`flush_due_horizon`) — 스케줄러가 쐈을 때 거래소 시각으로
   경계가 아직 안 지났으면 그만큼만 더 기다린다. 지연 상한이 있어 무한 대기는 없다.
3. **늦은 봉 거부** (`handle_one_minute_bar`) — 이미 확정한 버킷으로 오는 봉은 새 버킷을
   열지 않고 `ComposerLateBarDropped`로 남긴다. 1·2가 다 뚫려도 **중복 행은 안 생기고**,
   유실은 조용하지 않다.

## 스큐를 고쳤더니 드러난 것 — 겹②의 숨은 전제 (2026-08-05 장중 실측)

위 셋을 넣은 바로 그날, **26건의 `ComposerLateBarDropped`**가 났다. 3분봉 18개 중 12개가
2분짜리로 확정됐고 30분봉은 2개 다 29분짜리였다. 거래량 결손은 3m 16.9% · 5m 11.2% ·
10m 4.8% · 15m 3.2% · 30m 3.9%.

겹③이 설계대로 동작해서 **중복 행 대신 로그가 남은 것**이 유일한 소득이었다. 원인은 겹②의
전제가 틀렸다는 것이다:

> 겹②는 "스케줄러가 거래소 경계를 지나서 쏘기만 하면 그 버킷의 1분봉은 다 와 있다"를
> 전제한다. 그런데 **1분봉은 시각으로 확정되지 않는다** — `MinuteBarAggregator`는
> *다음 분의 첫 틱이 도착해야* 이전 분의 봉을 내놓는다(`data/collector.py`의
> `_handle_message`). 즉 1분봉의 도착 시각은 시계가 아니라 **틱 도착률**이 정한다.

같은 날 실측한 1분봉 발행 지연(경계 이후): 중앙값 **0.655초** · p90 **1.62초** · 최대
**7.96초**. 스케줄러 위상은 0.5초다 — **69%가 그 뒤에 도착했다**. 관측된 드롭률(3m 67% ·
5m 70% · 30m 100%)과 그대로 맞는다.

그 전날까지 이게 안 터진 이유도 이걸로 설명된다. 로컬 시계가 거래소보다 9.7초 **느려서**
스케줄러가 거래소 기준 9.7초 늦게 쐈다 — 우연한 9.7초짜리 유예였다. 시계를 맞추자
(`ops/clock_skew.py`, 2026-08-05) 그 쿠션이 사라지고 잠복 결함이 첫날 드러났다.

그래서 겹을 하나 더 놓는다:

4. **마지막 구성 1분봉 도착 대기** (`_await_last_constituent`) — 스케줄러 경로는 그 버킷의
   **마지막 분봉이 실제로 도착할 때까지** (상한 `_MAX_CONSTITUENT_WAIT_SECONDS`) 기다린 뒤
   확정한다. 시계가 아니라 **데이터의 도착**을 기준으로 삼는 것이라, 스큐·틱 지연·이벤트
   루프 지연 중 무엇이 원인이든 같이 막힌다. 상한을 넘겨 그냥 확정할 때는
   `ComposerFlushedIncomplete`로 남긴다 — 거래가 없는 분은 봉 자체가 안 나오므로 무한
   대기는 성립하지 않고, 짧은 봉이 나갈 수 있다는 사실은 조용하면 안 된다(L18).

## 대기 중에 버킷이 바뀔 수 있다 — 확정 대상 고정

겹②·④는 둘 다 **await**다. 그 사이에 다음 버킷의 1분봉이 도착하면 겹①이 그 자리에서
이전 버킷을 (정확하게) 확정하고 새 버킷을 연다. 이때 깨어난 스케줄러가 아무 생각 없이
`_flush_bucket()`을 부르면 **방금 열린 새 버킷을 1봉짜리로 확정해 버린다** — 그리고
`_last_flushed_start`가 그 시각으로 올라가, 뒤이어 올 그 버킷의 나머지 봉이 전부 늦은 봉으로
버려진다. 겹②를 넣은 2026-08-05 시점의 코드에 이미 있던 잠복 결함이다(스큐가 그날 실제로
대기를 유발하지 않아 드러나지 않았을 뿐이다).

그래서 `flush_due_horizon`은 **대기 전에 확정 대상 버킷을 고정**하고, 깨어났을 때 그 버킷이
그대로일 때만 확정한다. 바뀌었으면 이미 겹①이 옳게 처리한 뒤이므로 할 일이 없다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Sequence

from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_BAR, BusLike
from messiah.core.health import HealthStatus
from messiah.core.messages import HORIZON_SECONDS, BarClosed, BarSession, HealthLevel, Horizon
from messiah.core.scheduler import FixedTickScheduler
from messiah.core.timeutil import KST, UTC, ensure_aware, now_kst
from messiah.data.archiver import ParquetArchiver

# Horizon.M1로부터 합성하는 상위 Horizon과 그 길이(초, core/messages.py HORIZON_SECONDS 재사용).
# M1 자신은 합성 대상이 아니다(원본).
_TARGET_HORIZON_SECONDS: dict[Horizon, int] = {
    h: s for h, s in HORIZON_SECONDS.items() if h != Horizon.M1
}

_BOUNDARY_GRACE_SECONDS = 0.5  # Ver 1.2 §2.2 "지연 틱 유예 500ms"

# 스케줄러가 쐈는데 거래소 시각으로 경계가 아직 안 지났을 때 더 기다릴 수 있는 상한.
# 이만큼을 넘는 스큐는 `self_check`의 clock 항목이 애초에 기동을 거부하는 영역이고
# (`scripts/self_check.py`), 여기서 더 기다리면 다음 버킷 경계를 침범한다 — 그 지점부터는
# 기다리는 것보다 짧은 봉을 내고 그 사실이 리포트에 남는 편이 낫다.
_MAX_FLUSH_DEFER_SECONDS = 30.0

# 겹④ — 그 버킷의 마지막 1분봉이 도착하기를 기다리는 상한.
#
# 2026-08-05 실측 1분봉 발행 지연: 중앙값 0.655초 · p75 0.966초 · p90 1.62초 · 최대 7.96초
# (모듈 docstring "스큐를 고쳤더니 드러난 것"). 5초면 그날 표본의 p99 위쪽을 덮는다.
#
# 상한을 이 크기로 둬도 안전한 이유: 가장 짧은 합성 Horizon이 3분(180초)이라 5초는 그 2.8%다.
# 그리고 대기의 최악은 "합성봉이 몇 초 늦게 나간다"인 반면, 안 기다린 최악은 **조용한 데이터
# 손상**이다 — 2026-08-05에 실제로 상위 봉의 3~17%가 그렇게 사라졌다. 비대칭이 명확하다.
_MAX_CONSTITUENT_WAIT_SECONDS = 5.0

# 대기 폴링 간격 — `wait_for_bar()`의 기본값과 같은 값·같은 근거(횟수로 세어 테스트가 실제
# 시계를 안 타게 한다).
_CONSTITUENT_POLL_SECONDS = 0.05


def floor_to_horizon(dt: datetime, horizon_seconds: int) -> datetime:
    """
    계산: dt를 UTC epoch 기준 horizon_seconds의 배수로 내림(floor)한다 — 해당 Horizon
         버킷의 시작 시각.
    실패 조건: dt가 naive면 ValueError(ensure_aware).
    """
    ensure_aware(dt)
    epoch = dt.timestamp()
    floored_epoch = (epoch // horizon_seconds) * horizon_seconds
    return datetime.fromtimestamp(floored_epoch, tz=UTC).astimezone(KST)


def compose_composite_bar(
    symbol: str, horizon: Horizon, bucket_start: datetime, bars: Sequence[BarClosed]
) -> BarClosed:
    """구성 1분봉들 → 상위 Horizon 합성봉 1개 (순수 함수).

    실시간 경로(`MultiHorizonBarComposer.flush_due_horizon`)와 오프라인 재합성
    (`compose_offline`, 백필한 1분봉으로 상위 Horizon을 다시 만들 때)이 **같은 규칙을
    쓰도록** 여기 한 곳에 둔다 — 두 곳에 복사하면 한쪽만 고쳐지는 순간 아카이브 안에서
    같은 Horizon의 봉이 서로 다른 규칙으로 만들어진다.

    해석:
      - `quality_ok`는 구성봉이 전부 `quality_ok=True`이고 **개수가 그 Horizon의 분 수와
        정확히 일치할 때만**(=빠진 분이 없을 때만) True.
      - `session`은 구성봉 중 하나라도 장전이면 장전(2026-07-31) — 09:00을 걸치는 봉
        (예: 08:55~09:00 5분봉)은 장전 프린트를 실제로 품고 있으므로 "정규장 봉"이라고
        말하면 사실과 다르다. `quality_ok`가 구성봉 전부를 요구하는 것과 같은 보수적 방향.
    실패 조건: `bars`가 비면 ValueError — 가짜 OHLC를 만들지 않는다(호출측이 걸러야 한다).
    """
    if not bars:
        raise ValueError("구성 1분봉이 비어 있음 — 합성봉을 만들 수 없다")
    expected_minutes = HORIZON_SECONDS[horizon] // 60
    return BarClosed(
        symbol=symbol,
        horizon=horizon,
        bar_open_kst=bucket_start,
        o_ticks=bars[0].o_ticks,
        h_ticks=max(b.h_ticks for b in bars),
        l_ticks=min(b.l_ticks for b in bars),
        c_ticks=bars[-1].c_ticks,
        volume=sum(b.volume for b in bars),
        quality_ok=len(bars) == expected_minutes and all(b.quality_ok for b in bars),
        session=(
            BarSession.PRE_OPEN
            if any(b.session is BarSession.PRE_OPEN for b in bars)
            else BarSession.REGULAR
        ),
    )


def compose_offline(
    symbol: str, horizon: Horizon, minute_bars: Sequence[BarClosed]
) -> list[BarClosed]:
    """이미 완성된 1분봉 목록 → 그 Horizon의 합성봉 목록 (오프라인 재합성).

    실시간 경로와 달리 스케줄러가 없다 — 봉의 `bar_open_kst`를 Horizon 경계로 내림해
    버킷이 바뀌는 지점에서 확정한다. 백필(`data/backfill.py`)이 1분봉만 받아오므로
    3/5/10/15/30분봉은 이 함수로 만든다.

    입력: `minute_bars`는 시간순 M1 봉(다른 Horizon이 섞이면 ValueError — 조용히 잘못된
         합성봉을 만드느니 실패한다).
    """
    seconds = HORIZON_SECONDS[horizon]
    out: list[BarClosed] = []
    bucket_start: datetime | None = None
    bucket: list[BarClosed] = []
    for bar in sorted(minute_bars, key=lambda b: b.bar_open_kst):
        if bar.horizon is not Horizon.M1:
            raise ValueError(f"M1 봉만 합성할 수 있다: {bar.horizon}")
        start = floor_to_horizon(bar.bar_open_kst, seconds)
        if bucket_start is not None and start != bucket_start:
            out.append(compose_composite_bar(symbol, horizon, bucket_start, bucket))
            bucket = []
        bucket_start = start
        bucket.append(bar)
    if bucket and bucket_start is not None:
        out.append(compose_composite_bar(symbol, horizon, bucket_start, bucket))
    return out


class MultiHorizonBarComposer:
    """단일 심볼용 — 1분봉을 구독해 3/5/10/15/30분봉을 합성·적재·발행한다.

    `bus` 타입힌트는 `MessageBus`(Redis) 구체클래스가 아니라 `BusLike` Protocol이다
    (2026-07-27, `backtest/harness.py`가 `simulator.InProcessBus`를 넘기면서 pyright가
    "MessageBus와 불일치" 오류를 처음 냄 — `features/engine.py`가 W14~16에 같은 이유로
    `BusLike`로 바뀐 것과 동일 사례, 이 클래스는 `publish`/`subscribe`만 쓰고 `connect`/
    `close` 등 나머지 `MessageBus` 메서드는 안 써서 좁혀도 안전함을 확인 후 교체)."""

    def __init__(
        self,
        symbol: str,
        archiver: ParquetArchiver,
        bus: BusLike,
        target_horizons: dict[Horizon, int] | None = None,
        *,
        clock_skew_seconds: Callable[[], float | None] | None = None,
        now: Callable[[], datetime] = now_kst,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """
        입력: target_horizons를 생략하면 3/5/10/15/30분 전부(_TARGET_HORIZON_SECONDS) 합성.
             테스트에서 일부만 골라 검증하고 싶을 때만 명시적으로 축소.
             clock_skew_seconds는 "거래소 시각 − 로컬 시계"를 초로 돌려주는 콜러블
             (보통 `TickCollector.clock_skew_seconds`) — 생략하거나 None을 돌려주면 0으로
             본다. 이때 동작은 2026-08-05 이전과 동일하다.
             now/sleep은 테스트 주입점(SYSTEM.md R3 — 프로덕션 기본값은 실제 시계).
        """
        self._symbol = symbol
        self._archiver = archiver
        self._bus = bus
        self._targets = target_horizons or dict(_TARGET_HORIZON_SECONDS)
        self._bucket_start: dict[Horizon, datetime | None] = dict.fromkeys(self._targets)
        self._constituents: dict[Horizon, list[BarClosed]] = {h: [] for h in self._targets}
        # 이미 확정해 내보낸 마지막 버킷 시작 시각 — 늦게 온 봉이 같은 시각으로 두 번째
        # 버킷을 여는 것을 막는다(모듈 docstring 3번).
        self._last_flushed_start: dict[Horizon, datetime | None] = dict.fromkeys(self._targets)
        # 지금까지 본 1분봉 중 가장 나중 것 — `wait_for_bar()`가 종료 시퀀스에서 본다.
        self._last_seen_bar_open: datetime | None = None
        # 이 세션에서 버린 늦은 봉 / 짧게 확정한 버킷의 누적 수 — `health()`가 이걸 발행해
        # 장중 화면과 상태판이 **손상이 일어나는 동안** 볼 수 있게 한다(2026-08-05).
        # 종전에는 이 사실이 로그에만 있었고, 26건이 나는 동안 heartbeat는 계속 OK였다.
        self._late_bar_drops = 0
        self._incomplete_flushes = 0
        # Horizon별 거래량 회계 (2026-08-05 2차, 고도화 2) — 항등식을 **장중에 연속으로**
        # 잰다. `ops/integrity_report.analyze_horizon_consistency`가 같은 항등식을 보지만
        # 그건 아카이브를 읽는 장후 검사라, 2026-08-05엔 첫 증거가 08:48에 있었는데도
        # 사람이 알아챈 것은 한 시간 뒤였다. 여기서는 **첫 버킷에서 즉시** 드러난다.
        self._composed_volume: dict[Horizon, int] = dict.fromkeys(self._targets, 0)
        self._lost_volume: dict[Horizon, int] = dict.fromkeys(self._targets, 0)
        self._composed_bars: dict[Horizon, int] = dict.fromkeys(self._targets, 0)
        self._clock_skew_seconds = clock_skew_seconds
        self._now = now
        self._sleep = sleep

    @property
    def last_seen_bar_open(self) -> datetime | None:
        """구독으로 실제 **도착한** 마지막 1분봉의 시작 시각 — 발행됐다는 사실과 다르다."""
        return self._last_seen_bar_open

    @property
    def late_bar_drops(self) -> int:
        """이미 확정한 버킷으로 늦게 와서 버린 1분봉 수 — 그만큼 상위 Horizon에서 사라졌다."""
        return self._late_bar_drops

    @property
    def incomplete_flushes(self) -> int:
        """마지막 구성 1분봉을 못 기다리고 확정한 버킷 수(겹④ 상한 초과)."""
        return self._incomplete_flushes

    def volume_identity(self) -> dict[str, dict[str, int | float]]:
        """Horizon별 거래량 항등식의 **현재까지** 상태 (고도화 2, 2026-08-05 2차).

        `composed`는 지금까지 내보낸 합성봉 거래량의 합, `lost`는 늦게 도착해 버린 1분봉
        거래량의 합이다. 정의상 `lost == 0`이어야 하고, 그때 이 Horizon의 합성봉 총합은
        같은 구간 1분봉 총합과 정확히 일치한다 — 장후 `analyze_horizon_consistency`가
        아카이브에서 보는 그 항등식을, 여기서는 **메모리에서 즉시** 본다.

        왜 아카이브를 다시 읽지 않나: 파일 I/O가 필요 없고(수집 루프에 붙는다), 조각/통합본
        배치를 신경 쓸 필요가 없고, 무엇보다 **첫 버킷에서 바로** 드러난다. 아카이브 검사는
        여전히 필요하다 — 그쪽은 적재 단계의 결함(예: 같은 시각 덮어쓰기)을 보는데 이건 못 본다.
        """
        out: dict[str, dict[str, int | float]] = {}
        for horizon in self._targets:
            composed = self._composed_volume[horizon]
            lost = self._lost_volume[horizon]
            total = composed + lost
            out[horizon.value] = {
                "composed_volume": composed,
                "lost_volume": lost,
                "bars": self._composed_bars[horizon],
                "lost_ratio": (lost / total) if total else 0.0,
            }
        return out

    def health(self) -> HealthStatus:
        """`sys.health` heartbeat용 자가 판정 — 합성 손상은 **일어나는 중에** 보여야 한다.

        `TickCollector.health()`/`FeatureEngine.health()`가 "최근에 받았나"(신선도)를 재는
        것과 달리, 이건 **"받은 것을 온전히 합쳤나"**를 잰다. 2026-08-05에 이 구분이 없어서
        상태판 3축이 전부 OK인 채로 상위 봉의 3~17%가 사라졌다.

        임계가 0인 이유: 늦은 봉 1건은 곧 상위 Horizon 1개가 한 분(分) 모자라게 확정됐다는
        뜻이고, 그건 정의상의 항등식(`ops/integrity_report.analyze_horizon_consistency`)
        위반이다. "조금은 괜찮다"가 성립하는 축이 아니다.

        **정상일 때도 근거를 말한다**(고도화 3): "손실 없음"만 찍으면 "검사했는데 0"과
        "합성기가 아직 아무것도 안 했다"가 구분되지 않는다. 확정한 봉이 0개면 OK가 아니라
        `UNKNOWN`이다 — 판정할 재료가 아직 없다는 뜻이고, 장전 구간이 실제로 그 상태다.
        """
        identity = self.volume_identity()
        bars = sum(int(entry["bars"]) for entry in identity.values())
        losses = self._late_bar_drops + self._incomplete_flushes
        if losses == 0:
            if bars == 0:
                return HealthStatus(
                    HealthLevel.UNKNOWN, "확정한 합성봉이 아직 없다 — 판정할 근거 없음"
                )
            return HealthStatus(HealthLevel.OK, f"합성봉 {bars}개 · 거래량 항등식 일치(유실 0)")
        worst = max(identity.items(), key=lambda kv: kv[1]["lost_ratio"])
        return HealthStatus(
            HealthLevel.WARN,
            f"버킷 손실 {losses}건(늦은 봉 {self._late_bar_drops} · "
            f"미완 확정 {self._incomplete_flushes}) — 최악 {worst[0]} "
            f"{int(worst[1]['lost_volume']):,}계약 유실({worst[1]['lost_ratio']:.1%})",
        )

    async def handle_one_minute_bar(self, bar: BarClosed) -> None:
        """
        입력: 완성된 1분봉(`bar.1m.{symbol}`에서 수신) — 다른 심볼의 봉이 섞여 들어오면
             무시한다(단일 심볼 구독을 전제하지만 방어적으로 한 번 더 확인).
        계산: Horizon별 누적 버킷에 추가한다. **그 봉이 지금 모으는 버킷보다 뒤 버킷에
             속하면 이전 버킷을 여기서 확정한다**(2026-08-05) — 종전에는 오직 스케줄러만
             확정했고, 그래서 로컬 시계가 거래소보다 앞선 날에는 버킷이 한 봉 모자란 채
             확정되고 늦게 온 봉이 같은 시각으로 두 번째 버킷을 열었다(모듈 docstring).
             이 경로는 시계를 안 보므로 `compose_offline`과 정의상 같은 결과를 낸다.
        실패 조건: 이미 확정한 버킷으로 오는 봉(늦은 봉)은 **버린다** — 새 버킷을 열면
                  아카이브에 같은 시각의 합성봉이 두 줄 생긴다. 버리되 조용히는 안 한다(L18).
        """
        if bar.symbol != self._symbol or bar.horizon != Horizon.M1:
            return
        if self._last_seen_bar_open is None or bar.bar_open_kst > self._last_seen_bar_open:
            self._last_seen_bar_open = bar.bar_open_kst

        for horizon, seconds in self._targets.items():
            start = floor_to_horizon(bar.bar_open_kst, seconds)
            flushed = self._last_flushed_start[horizon]
            if flushed is not None and start <= flushed:
                self._late_bar_drops += 1
                self._lost_volume[horizon] += bar.volume
                mlog.log(
                    "ComposerLateBarDropped",
                    f"이미 확정한 {horizon.value} 버킷({start:%H:%M})으로 1분봉"
                    f"({bar.bar_open_kst:%H:%M})이 늦게 도착 — 중복 합성봉을 막기 위해 버림",
                    symbol=self._symbol,
                    horizon=horizon.value,
                    bucket_start=start.isoformat(),
                    bar_open_kst=bar.bar_open_kst.isoformat(),
                    # 건수가 아니라 **거래량**이 항등식의 단위다 — 리포트가 "몇 봉이 늦었나"가
                    # 아니라 "얼마가 빠졌나"를 집계할 수 있어야 한다(2026-08-05 2차).
                    lost_volume=bar.volume,
                )
                continue

            current = self._bucket_start[horizon]
            if current is not None and start != current:
                await self._flush_bucket(horizon)
            if self._bucket_start[horizon] is None:
                self._bucket_start[horizon] = start
            self._constituents[horizon].append(bar)

    async def flush_due_horizon(self, horizon: Horizon, *, force: bool = False) -> None:
        """
        계산: 해당 Horizon의 누적 버킷을 합성봉으로 확정해 적재·발행하고 버킷을 비운다.
             누적된 1분봉이 하나도 없으면(조용한 구간) 아무 것도 발행하지 않는다 — 가짜
             OHLC를 만들지 않는다.
        해석: quality_ok는 구성봉이 전부 quality_ok=True이고, 개수가 이 Horizon의 분(分) 수와
             정확히 일치할 때만(=누락된 분이 없을 때만) True.

        `force=False`(스케줄러 경로)에서는 **거래소 시각으로 경계가 지났는지** 먼저 본다 —
        안 지났으면 그만큼(최대 `_MAX_FLUSH_DEFER_SECONDS`) 기다린 뒤 확정한다. 로컬 시계가
        거래소보다 앞선 날 버킷이 한 봉 모자란 채 확정되는 것을 막는 두 번째 겹이다
        (모듈 docstring 2번). 스큐가 0 이하(거래소가 앞섬 = 2026-08-04 실측 방향)면 대기는
        0초라 종전과 완전히 같은 동작이다.

        그 다음 **그 버킷의 마지막 1분봉이 실제로 도착했는지**를 본다(겹④, 2026-08-05 장중).
        스큐를 아무리 정확히 보상해도 1분봉 자체가 시각이 아니라 **다음 분의 첫 틱**으로
        확정되므로, 시계만 보는 판단으로는 이 경주에서 이길 수 없다(모듈 docstring).

        ## `force=True` — 기다릴 수 없거나, 기다릴 이유가 없는 경로

        둘 다 "지금 즉시 확정"이지만 근거가 다르다.

        - **종료 시퀀스**(`flush_all_final`) — 더 기다릴 수 없다. 호출측이 대신
          `wait_for_bar()`로 마지막 1분봉의 도착을 먼저 확인한다.
        - **오프라인 재생**(`backtest/harness.py`의 `_feed_m1_bars`, `run_full_path_smoke.py`,
          `run_phase5_smoke.py`) — 기다릴 이유가 **없다**. 봉을 동기 루프로 밀어 넣으므로
          대기 중에 새 봉이 도착하는 일 자체가 성립하지 않는다. 여기서 `force=False`를 쓰면
          겹④가 오지 않을 봉을 매 버킷 상한까지 기다려, 재생이 실시간보다 느려진다
          (2026-08-05에 실제로 그렇게 만들 뻔했다). 대기가 무의미한 경로에서 대기하는 것은
          안전이 아니라 그냥 결함이다.
        """
        if force:
            await self._flush_bucket(horizon)
            return

        # **확정 대상을 여기서 고정한다.** 아래 두 대기는 await이고, 그 사이에 다음 버킷의
        # 봉이 도착하면 겹①이 이 버킷을 이미 옳게 확정한 뒤 새 버킷을 연다 — 그때 깨어나
        # 그냥 flush하면 갓 열린 버킷을 1봉짜리로 확정해 버린다(모듈 docstring 마지막 절).
        target_start = self._bucket_start[horizon]

        await self._defer_until_boundary_passed(horizon, target_start)
        await self._await_last_constituent(horizon, target_start)

        if self._bucket_start[horizon] != target_start:
            return  # 대기 중 겹①이 처리했다 — 여기서 할 일이 없다
        await self._flush_bucket(horizon)

    async def _await_last_constituent(
        self, horizon: Horizon, bucket_start: datetime | None
    ) -> None:
        """그 버킷의 **마지막 분(分) 봉**이 도착할 때까지만 (상한을 두고) 기다린다 — 겹④.

        판단 기준이 시계가 아니라 **데이터의 도착**이라, 원인이 스큐든 틱 지연이든 이벤트
        루프 지연이든 같이 막힌다. 2026-08-05 실측으로 1분봉 발행 지연 중앙값이 0.655초라
        스케줄러 위상 0.5초보다 크다 — 즉 정상적인 날에도 매 버킷이 이 경주에서 졌다.

        상한을 넘겨도 확정은 한다. 거래가 한 건도 없는 분은 봉 자체가 발행되지 않으므로
        (`MinuteBarAggregator`) 무한정 기다리면 그 Horizon이 통째로 멈춘다 — 그건 유실보다
        나쁘다. 대신 짧은 봉이 나갔다는 사실을 `ComposerFlushedIncomplete`로 남긴다(L18).
        """
        if bucket_start is None:
            return
        last_minute_open = bucket_start + timedelta(seconds=self._targets[horizon] - 60)
        if self._last_seen_bar_open is not None and self._last_seen_bar_open >= last_minute_open:
            return  # 이미 다 왔다 — 정상 경로에서는 여기서 끝난다(대기 0초)

        arrived = await self.wait_for_bar(
            last_minute_open,
            timeout_seconds=_MAX_CONSTITUENT_WAIT_SECONDS,
            poll_seconds=_CONSTITUENT_POLL_SECONDS,
        )
        # 대기 중 겹①이 이 버킷을 확정했으면 짧게 나간 게 아니다 — 그 경우는 세지 않는다.
        if arrived or self._bucket_start[horizon] != bucket_start:
            return
        self._incomplete_flushes += 1
        mlog.log(
            "ComposerFlushedIncomplete",
            f"{horizon.value} 버킷({bucket_start:%H:%M})의 마지막 1분봉"
            f"({last_minute_open:%H:%M})이 {_MAX_CONSTITUENT_WAIT_SECONDS:.0f}초 안에 안 와 "
            "짧은 채로 확정 — 그 분의 거래량이 이 Horizon에서 빠진다",
            symbol=self._symbol,
            horizon=horizon.value,
            bucket_start=bucket_start.isoformat(),
            awaited_bar_open_kst=last_minute_open.isoformat(),
        )

    async def _defer_until_boundary_passed(
        self, horizon: Horizon, bucket_start: datetime | None
    ) -> None:
        """거래소 시각으로 이 버킷의 경계가 지날 때까지만 (상한을 두고) 기다린다."""
        if bucket_start is None:
            return
        skew = self._clock_skew_seconds() if self._clock_skew_seconds is not None else None
        boundary = bucket_start + timedelta(seconds=self._targets[horizon])
        exchange_now = self._now() + timedelta(seconds=skew or 0.0)
        shortfall = (boundary - exchange_now).total_seconds()
        if shortfall <= 0:
            return
        await self._sleep(min(shortfall + _BOUNDARY_GRACE_SECONDS, _MAX_FLUSH_DEFER_SECONDS))

    async def _flush_bucket(self, horizon: Horizon) -> None:
        """버킷을 비우고 합성봉 1개를 확정한다 — 대기 판단 없이 지금 즉시."""
        bars = self._constituents[horizon]
        bucket_start = self._bucket_start[horizon]
        self._constituents[horizon] = []
        self._bucket_start[horizon] = None
        if not bars or bucket_start is None:
            return
        self._last_flushed_start[horizon] = bucket_start

        # 합성 규칙 자체는 `compose_composite_bar()`에 있다 — 오프라인 재합성과 한 곳을 쓴다.
        composite = compose_composite_bar(self._symbol, horizon, bucket_start, bars)
        self._composed_volume[horizon] += composite.volume
        self._composed_bars[horizon] += 1
        await self._archive_and_publish(composite)

    async def wait_for_bar(
        self,
        bar_open_kst: datetime,
        *,
        timeout_seconds: float = 5.0,
        poll_seconds: float = 0.05,
    ) -> bool:
        """그 시각의 1분봉이 **구독으로 도착할 때까지** 기다린다. 도착했으면 True.

        종료 시퀀스 전용이다. `TickCollector.flush_final_bar()`는 버스에 발행만 하고
        돌아오는데, 구독자인 이 클래스의 콜백이 그 사이에 실행된다는 보장이 없다 — 그래서
        곧바로 `flush_all_final()`을 부르면 **그날 마지막 1분봉이 상위 Horizon 전부에서
        빠진다**(2026-08-04 실측). 그 경합을 관측 가능한 대기로 바꾼다.

        시간 대신 **횟수**로 센다 — 이 함수의 회귀 테스트가 실제 시계를 안 타게 하기 위해서다.
        """
        attempts = max(1, int(timeout_seconds / poll_seconds))
        for _ in range(attempts):
            if self._last_seen_bar_open is not None and self._last_seen_bar_open >= bar_open_kst:
                return True
            await self._sleep(poll_seconds)
        return self._last_seen_bar_open is not None and self._last_seen_bar_open >= bar_open_kst

    async def _archive_and_publish(self, bar: BarClosed) -> None:
        """TickCollector._archive_and_publish_bar()와 동일 원칙 — 적재/발행 실패는 독립
        try/except로 로깅만 하고 계속(L22)."""
        try:
            self._archiver.append_bar(bar)
        except Exception as exc:  # noqa: BLE001
            mlog.log("CollectorProcessingError", f"합성봉 적재 실패: {exc}", symbol=bar.symbol)

        try:
            await self._bus.publish(f"{TOPIC_BAR}.{bar.horizon.value}.{bar.symbol}", bar)
        except Exception as exc:  # noqa: BLE001
            mlog.log("CollectorProcessingError", f"합성봉 발행 실패: {exc}", symbol=bar.symbol)

    async def flush_all_final(self) -> None:
        """graceful shutdown 시 남은 모든 버킷을 강제 flush — 종료 시퀀스에서 호출.

        `force=True`다: 종료 시점엔 경계가 안 지났어도 기다릴 수 없다. 대신 호출측이
        `wait_for_bar()`로 마지막 1분봉의 도착을 먼저 확인한다(`scripts/run_l1_daily.py`
        `_daily_close`).
        """
        for horizon in self._targets:
            await self.flush_due_horizon(horizon, force=True)

    async def run_forever(self) -> None:
        """
        계산: `bar.1m.{symbol}` 구독 태스크 1개 + Horizon마다 `FixedTickScheduler`
             (phase_offset=500ms) 태스크 1개, 총 1+len(targets)개를 asyncio.gather로 동시
             구동한다. 어느 하나가 예외로 죽으면(재연결은 이 클래스의 책임이 아님) 전부
             함께 종료된다.
        """
        subscribe_task = self._bus.subscribe(
            [f"{TOPIC_BAR}.{Horizon.M1.value}.{self._symbol}"], self.handle_one_minute_bar
        )
        scheduler_tasks = [
            FixedTickScheduler(
                tick_seconds=seconds, phase_offset_seconds=_BOUNDARY_GRACE_SECONDS
            ).run_forever(lambda h=horizon: self.flush_due_horizon(h))
            for horizon, seconds in self._targets.items()
        ]
        await asyncio.gather(subscribe_task, *scheduler_tasks)
