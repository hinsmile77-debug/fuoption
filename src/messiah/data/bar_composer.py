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
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Sequence

from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_BAR, BusLike
from messiah.core.messages import HORIZON_SECONDS, BarClosed, BarSession, Horizon
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
        self._clock_skew_seconds = clock_skew_seconds
        self._now = now
        self._sleep = sleep

    @property
    def last_seen_bar_open(self) -> datetime | None:
        """구독으로 실제 **도착한** 마지막 1분봉의 시작 시각 — 발행됐다는 사실과 다르다."""
        return self._last_seen_bar_open

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
                mlog.log(
                    "ComposerLateBarDropped",
                    f"이미 확정한 {horizon.value} 버킷({start:%H:%M})으로 1분봉"
                    f"({bar.bar_open_kst:%H:%M})이 늦게 도착 — 중복 합성봉을 막기 위해 버림",
                    symbol=self._symbol,
                    horizon=horizon.value,
                    bucket_start=start.isoformat(),
                    bar_open_kst=bar.bar_open_kst.isoformat(),
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

        `force=True`는 종료 시퀀스(`flush_all_final`) 전용 — 그 시점엔 더 기다릴 수 없다.
        """
        if not force:
            await self._defer_until_boundary_passed(horizon)
        await self._flush_bucket(horizon)

    async def _defer_until_boundary_passed(self, horizon: Horizon) -> None:
        """거래소 시각으로 이 버킷의 경계가 지날 때까지만 (상한을 두고) 기다린다."""
        bucket_start = self._bucket_start[horizon]
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
        await self._archive_and_publish(
            compose_composite_bar(self._symbol, horizon, bucket_start, bars)
        )

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
