"""Realtime Feature Engine 골격 — Master Plan Ver 2.0 §9 W6~8 (Ver 1.1 §2-2), VL 결선 W22~23.

`bar.{horizon}.{symbol}`을 구독해 Horizon별 롤링 윈도우를 갱신하고, `px_core`(PX 30개) +
`vl_core`(VL, W22~23 확장분) 계산기를 전부 돌려 `FeatureVector`를 조립·발행한다
(`feat.{horizon}.{symbol}`). MS/FL/OP/RG는 이번 스코프에 없음(각 모듈 docstring·
capability_matrix.md 참고) — 계산기 레지스트리(`WINDOWED_FEATURES`/`STATEFUL_FEATURES`)에
추가하기만 하면 이 엔진이 자동으로 같이 계산·발행하도록 설계했다(신규 카테고리 추가 시
`_build_feature_vector`에 루프 두 줄만 늘어남, 그 외 엔진 코드 변경 불필요).

완성봉 규율(Ver 1.2 §2.2): 발행은 `handle_bar()`가 완성봉을 받은 시점에만 한다 — 이 엔진
자체는 미완성 봉을 절대 보지 않는다(L1이 완성된 봉만 발행하므로).

**버그 발견·수정(2026-07-26)**: 롤링 히스토리를 `collections.deque`로 보관하는데 계산기
다수가 `bars[-window:]` 슬라이스를 쓴다 — `deque`는 슬라이스를 지원하지 않아(정수 인덱싱만
가능, 파이썬 표준 동작) 슬라이스를 쓰는 계산기는 전부 `TypeError`를 던지고 `_safe_call`이
조용히 None으로 삼켜 왔다. PX 30개 중 정수 인덱싱만 쓰는 소수(px_ret/px_mom/px_accel 등)를
제외한 대다수가 워밍업 완료 여부와 무관하게 **항상 NaN이었다**(80봉 워밍업 후 실측: 82개 중
72개 None). `handle_bar()`가 계산 직전 `list(history)`로 변환해 해결 — 계산기 쪽은 원래도
`Sequence[BarClosed]` 계약대로 짠 것이라 수정 불필요.
"""

from __future__ import annotations

import time
from collections import deque
from datetime import timedelta
from typing import Callable, Mapping, Sequence

from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_BAR, TOPIC_FEAT, BusLike
from messiah.core.health import HealthStatus, staleness_status
from messiah.core.messages import (
    HORIZON_SECONDS,
    BarClosed,
    FeatureVector,
    HealthLevel,
    Horizon,
)
from messiah.features import px_core, vl_core

# px_hurst(W_SLOW_HURST 최대 120) · px_accel(W_STD 최대 60이면 2*60+1=121 필요)를 전부
# 커버하는 넉넉한 여유(130개 완성봉, 1분봉 기준 하루 정규장 405분 내에서도 충분히 작음).
# 이 값은 두 가지로 쓰인다: ① 롤링 히스토리 보관 개수(deque maxlen) ② 아래 FeatureNaN 경고의
# "워밍업 완료" 판정 기준(len(history)가 maxlen에 도달했다는 건 최소 이만큼의 봉을 봤다는 뜻).
_MAX_HISTORY = 130

_NAN_RATIO_HALT_THRESHOLD = 0.20  # Ver 1.1 §2-2: 20% 초과 시 해당 Horizon 신호 정지


class FeatureEngine:
    """단일 심볼용 — 전 Horizon의 완성봉을 구독해 PX+VL Feature를 계산·발행한다."""

    def __init__(
        self,
        symbol: str,
        bus: BusLike,
        feature_set: str,
        horizons: Sequence[Horizon] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._symbol = symbol
        self._bus = bus
        self._feature_set = feature_set
        self._horizons = list(horizons) if horizons is not None else list(Horizon)
        self._history: dict[Horizon, deque[BarClosed]] = {
            h: deque(maxlen=_MAX_HISTORY) for h in self._horizons
        }
        self._session = px_core.SessionState()
        self._monotonic = monotonic
        self._last_publish_at: float | None = None
        self._last_nan_ratio: dict[Horizon, float] = {}

    def seconds_since_last_publish(self) -> float | None:
        if self._last_publish_at is None:
            return None
        return self._monotonic() - self._last_publish_at

    def last_nan_ratios(self) -> dict[str, float]:
        return {horizon.value: ratio for horizon, ratio in self._last_nan_ratio.items()}

    def health(self) -> HealthStatus:
        """`sys.health` heartbeat용 자가 판정.

        두 가지를 함께 본다 — ① 발행이 아예 멈췄는가(= 봉이 안 들어옴) ② 발행은 되는데
        값이 쓸모없는가(nan_ratio가 Ver 1.1 §2-2의 신호정지 임계 20%를 넘음). ②는 2026-07-30
        점검에서 15m/30m가 **하루 종일 NaN 2/3**였는데도 화면 어디에도 안 드러났던 문제의
        대응이다(사람이 로그를 직접 파싱해야만 보였다).

        M1 봉이 1분 주기이므로 발행 정체 임계는 그 배수로 잡는다(2분 WARN / 4분 CRITICAL) —
        수집기 스톨(120초)이 먼저 잡히고 그 여파로 여기 CRITICAL이 뜨는 순서가 되게 한다.
        """
        status = staleness_status(
            self.seconds_since_last_publish(),
            warn_after=120.0,
            critical_after=240.0,
            warming_up_detail="웜업 — 아직 첫 발행 전",
        )
        if status.level is not HealthLevel.OK:
            return status

        degraded = {
            horizon.value: ratio
            for horizon, ratio in self._last_nan_ratio.items()
            if ratio > _NAN_RATIO_HALT_THRESHOLD
        }
        if degraded:
            worst = ", ".join(f"{h} {r:.0%}" for h, r in sorted(degraded.items()))
            return HealthStatus(HealthLevel.WARN, f"NaN 비율 임계 초과 — 신호 정지 권고: {worst}")
        return status

    @property
    def history_capacity(self) -> int:
        """웜스타트 호출측이 "몇 개를 읽어와야 하는지" 알기 위한 값 — `_MAX_HISTORY`를
        호출측에 다시 하드코딩하지 않게 노출한다(단일 소스)."""
        return _MAX_HISTORY

    def warm_start(
        self, bars_by_horizon: Mapping[Horizon, Sequence[BarClosed]]
    ) -> dict[Horizon, int]:
        """과거 완성봉으로 롤링 윈도우를 미리 채운다 — 발행은 하지 않는다.

        **왜 필요한가 (2026-07-30 로그 실측)**: 이 엔진은 매 기동마다 빈 deque로 시작했다.
        그 결과 ① 매일 아침 전 Horizon이 nan_ratio 0.96에서 출발해 1m조차 30분 넘게 쓸모가
        없었고 ② 15m/30m는 하루에 각각 26/14봉밖에 안 생겨 **최대 윈도우를 영영 못 채웠다**
        (2026-07-29 실측 최저 nan_ratio 15m 0.678 / 30m 0.694 — 하루 종일 피처의 2/3가 NaN)
        ③ 장중 재시작 한 번이면 그때까지 쌓인 워밍업이 통째로 날아갔다(같은 날 12:16·14:49에
        1m nan_ratio가 0.025 → 0.702 → 0.959로 리셋된 것이 로그에 그대로 남아 있다).
        Parquet 아카이브에 필요한 봉이 이미 다 있는데도 안 읽고 있었을 뿐이다.

        `SessionState`도 함께 채운다 — M1 봉만, 시간 오름차순으로 흘린다(`handle_bar()`와
        같은 규율). 이건 부수효과가 아니라 목적 중 하나다: `px_gap_open`은
        `prev_day_close_ticks`가 있어야 값이 나오는데, 콜드스타트에서는 전일 종가를 볼 방법이
        없어 **항상 None이었다**. 전일 봉을 시간순으로 흘리면 `on_bar()`의 일자 롤오버가
        자연스럽게 전일 종가를 채운다.

        입력: Horizon별 완성봉 목록. 심볼/Horizon이 안 맞는 봉은 버린다. 시간순이 아니어도
             되며(여기서 정렬한다), 용량(`history_capacity`)을 넘으면 최신 것만 남는다.
        반환: Horizon별로 실제 적재된 봉 수 — 호출측이 로그로 남긴다.
        """
        for horizon, bars in bars_by_horizon.items():
            history = self._history.get(horizon)
            if history is None:
                continue  # 이 엔진이 구독하지 않는 Horizon
            accepted = sorted(
                (b for b in bars if b.symbol == self._symbol and b.horizon == horizon),
                key=lambda b: b.bar_open_kst,
            )
            history.clear()
            history.extend(accepted)  # deque(maxlen)이 알아서 오래된 것부터 버린다

        for bar in self._history.get(Horizon.M1, ()):
            self._session.on_bar(bar)

        return {horizon: len(history) for horizon, history in self._history.items()}

    async def handle_bar(self, bar: BarClosed) -> None:
        """
        입력: 완성봉(BarClosed) — 다른 심볼이거나 구독 대상이 아닌 Horizon이면 무시.
        계산: 세션 상태(px_gap_open 등)는 M1 봉으로만 갱신한다 — 하루 시가/고저를 놓치지
             않는 가장 촘촘한 단위이기 때문(다른 Horizon 봉으로 갱신하면 예컨대 30분봉
             경계 사이의 진짜 당일 고점을 놓칠 수 있음).
        """
        if bar.symbol != self._symbol or bar.horizon not in self._history:
            return
        if bar.horizon == Horizon.M1:
            self._session.on_bar(bar)

        history = self._history[bar.horizon]
        history.append(bar)
        # 계산기(px_core/vl_core)는 `bars[-window:]` 같은 슬라이스를 그대로 쓰는데
        # `collections.deque`는 슬라이스를 지원하지 않는다(정수 인덱싱만 가능— 파이썬 표준
        # 동작, 버그 아니라 deque의 알려진 제약). `history`를 deque 그대로 넘기면 슬라이스를
        # 쓰는 계산기가 전부 TypeError → `_safe_call`이 조용히 None으로 삼켜, PX 30개 중
        # 정수 인덱싱만 쓰는 소수(px_ret/px_mom/px_accel 등)를 제외한 대다수가 워밍업과
        # 무관하게 항상 NaN이었다(2026-07-26 발견 — 리스트로 바꾸자 실제 값 산출 확인).
        bars = list(history)
        vector = self._build_feature_vector(bar, bars)
        await self._publish(vector)

    def _build_feature_vector(self, bar: BarClosed, history: list[BarClosed]) -> FeatureVector:
        values: dict[str, float | None] = {}
        for name, fn, windows in px_core.WINDOWED_FEATURES:
            for window in windows:
                values[f"{name}_{window}"] = self._safe_call(fn, history, window)
        for name, stateful_fn in px_core.STATEFUL_FEATURES:
            values[name] = self._safe_call(stateful_fn, history, self._session)
        for name, fn, windows in vl_core.WINDOWED_FEATURES:
            for window in windows:
                values[f"{name}_{window}"] = self._safe_call(fn, history, window)
        for name, stateful_fn in vl_core.STATEFUL_FEATURES:
            values[name] = self._safe_call(stateful_fn, history, self._session)

        nan_ratio = sum(1 for v in values.values() if v is None) / len(values)
        # 워밍업 중(예: 30m은 최대 윈도우 60개를 채우는 데만 30시간 = 며칠이 걸림)엔 nan_ratio가
        # 높은 게 정상이라 매 봉마다 WARNING을 찍으면 agenda.py의 주간 경보 집계가 이 잡음에
        # 파묻힌다(2026-07-24, 실제 운영 로그 리뷰 중 발견) — len(history)가 _MAX_HISTORY에
        # 도달해 "워밍업이 끝났어야 할 시점"이 된 뒤에도 nan_ratio가 여전히 높을 때만 경고한다.
        warmed_up = len(history) >= _MAX_HISTORY
        if warmed_up and nan_ratio > _NAN_RATIO_HALT_THRESHOLD:
            mlog.log(
                "FeatureNaN",
                f"NaN 비율 {nan_ratio:.0%} — {bar.horizon.value} 신호 정지 권고",
                symbol=self._symbol,
                horizon=bar.horizon.value,
                nan_ratio=nan_ratio,
            )

        return FeatureVector(
            symbol=self._symbol,
            horizon=bar.horizon,
            feature_set=self._feature_set,
            values=values,
            nan_ratio=nan_ratio,
            valid_until=bar.bar_open_kst + timedelta(seconds=HORIZON_SECONDS[bar.horizon]),
        )

    @staticmethod
    def _safe_call(fn, *args) -> float | None:
        """개별 Feature 계산 실패는 그 Feature만 None으로 마킹 — 다른 Feature·전체 발행까지
        죽이지 않는다(Ver 1.1 §2-2 "특정 Feature 계산 오류 → 해당 Feature만 NaN 마킹")."""
        try:
            return fn(*args)
        except Exception:  # noqa: BLE001
            return None

    async def _publish(self, vector: FeatureVector) -> None:
        try:
            await self._bus.publish(f"{TOPIC_FEAT}.{vector.horizon.value}.{vector.symbol}", vector)
        except Exception as exc:  # noqa: BLE001 — 발행 실패로 구독 루프가 죽으면 안 됨(L22)
            mlog.log(
                "FeaturePublishError",
                f"FeatureVector 발행 실패: {exc}",
                symbol=vector.symbol,
                horizon=vector.horizon.value,
            )
            return
        self._last_publish_at = self._monotonic()
        self._last_nan_ratio[vector.horizon] = vector.nan_ratio
        mlog.log(
            "FeaturePublish",
            "FeatureVector 발행",
            symbol=vector.symbol,
            horizon=vector.horizon.value,
            feature_set=vector.feature_set,
            nan_ratio=vector.nan_ratio,
        )

    async def run_forever(self) -> None:
        patterns = [f"{TOPIC_BAR}.{h.value}.{self._symbol}" for h in self._horizons]
        await self._bus.subscribe(patterns, self.handle_bar)
