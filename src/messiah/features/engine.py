"""Realtime Feature Engine 골격 — Master Plan Ver 2.0 §9 W6~8 (Ver 1.1 §2-2).

`bar.{horizon}.{symbol}`을 구독해 Horizon별 롤링 윈도우를 갱신하고, `px_core`의 PX 30개
계산기를 전부 돌려 `FeatureVector`를 조립·발행한다(`feat.{horizon}.{symbol}`). MS는 이번
스코프에 없음(px_core.py 모듈 docstring·capability_matrix.md 참고) — 계산기 레지스트리에
추가하기만 하면 이 엔진이 자동으로 같이 계산·발행하도록 설계했다(신규 계산기 추가 시 엔진
코드 변경 불필요).

완성봉 규율(Ver 1.2 §2.2): 발행은 `handle_bar()`가 완성봉을 받은 시점에만 한다 — 이 엔진
자체는 미완성 봉을 절대 보지 않는다(L1이 완성된 봉만 발행하므로).
"""

from __future__ import annotations

from collections import deque
from datetime import timedelta
from typing import Sequence

from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_BAR, TOPIC_FEAT, MessageBus
from messiah.core.messages import HORIZON_SECONDS, BarClosed, FeatureVector, Horizon
from messiah.features import px_core

# px_hurst(W_SLOW_HURST 최대 120) · px_accel(W_STD 최대 60이면 2*60+1=121 필요)를 전부
# 커버하는 넉넉한 여유(130개 완성봉, 1분봉 기준 하루 정규장 405분 내에서도 충분히 작음).
_MAX_HISTORY = 130

_NAN_RATIO_HALT_THRESHOLD = 0.20  # Ver 1.1 §2-2: 20% 초과 시 해당 Horizon 신호 정지


class FeatureEngine:
    """단일 심볼용 — 전 Horizon의 완성봉을 구독해 PX Feature를 계산·발행한다."""

    def __init__(
        self,
        symbol: str,
        bus: MessageBus,
        feature_set: str,
        horizons: Sequence[Horizon] | None = None,
    ) -> None:
        self._symbol = symbol
        self._bus = bus
        self._feature_set = feature_set
        self._horizons = list(horizons) if horizons is not None else list(Horizon)
        self._history: dict[Horizon, deque[BarClosed]] = {
            h: deque(maxlen=_MAX_HISTORY) for h in self._horizons
        }
        self._session = px_core.SessionState()

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
        vector = self._build_feature_vector(bar, history)
        await self._publish(vector)

    def _build_feature_vector(self, bar: BarClosed, history: deque[BarClosed]) -> FeatureVector:
        values: dict[str, float | None] = {}
        for name, fn, windows in px_core.WINDOWED_FEATURES:
            for window in windows:
                values[f"{name}_{window}"] = self._safe_call(fn, history, window)
        for name, stateful_fn in px_core.STATEFUL_FEATURES:
            values[name] = self._safe_call(stateful_fn, history, self._session)

        nan_ratio = sum(1 for v in values.values() if v is None) / len(values)
        if nan_ratio > _NAN_RATIO_HALT_THRESHOLD:
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
