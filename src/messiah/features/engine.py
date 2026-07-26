"""Realtime Feature Engine 골격 — Master Plan Ver 2.0 §9 W6~8 (Ver 1.1 §2-2), VL 결선 W22~23.

`bar.{horizon}.{symbol}`을 구독해 Horizon별 롤링 윈도우를 갱신하고, `px_core`(PX 30개) +
`vl_core`(VL, W22~23 확장분) 계산기를 전부 돌려 `FeatureVector`를 조립·발행한다
(`feat.{horizon}.{symbol}`). MS/FL/OP/RG는 이번 스코프에 없음(각 모듈 docstring·
capability_matrix.md 참고) — 계산기 레지스트리(`WINDOWED_FEATURES`/`STATEFUL_FEATURES`)에
추가하기만 하면 이 엔진이 자동으로 같이 계산·발행하도록 설계했다(신규 카테고리 추가 시
`_build_feature_vector`에 루프 두 줄만 늘어남, 그 외 엔진 코드 변경 불필요).

완성봉 규율(Ver 1.2 §2.2): 발행은 `handle_bar()`가 완성봉을 받은 시점에만 한다 — 이 엔진
자체는 미완성 봉을 절대 보지 않는다(L1이 완성된 봉만 발행하므로).

**버그 발견·수정(2026-07-29)**: 롤링 히스토리를 `collections.deque`로 보관하는데 계산기
다수가 `bars[-window:]` 슬라이스를 쓴다 — `deque`는 슬라이스를 지원하지 않아(정수 인덱싱만
가능, 파이썬 표준 동작) 슬라이스를 쓰는 계산기는 전부 `TypeError`를 던지고 `_safe_call`이
조용히 None으로 삼켜 왔다. PX 30개 중 정수 인덱싱만 쓰는 소수(px_ret/px_mom/px_accel 등)를
제외한 대다수가 워밍업 완료 여부와 무관하게 **항상 NaN이었다**(80봉 워밍업 후 실측: 82개 중
72개 None). `handle_bar()`가 계산 직전 `list(history)`로 변환해 해결 — 계산기 쪽은 원래도
`Sequence[BarClosed]` 계약대로 짠 것이라 수정 불필요.
"""

from __future__ import annotations

from collections import deque
from datetime import timedelta
from typing import Sequence

from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_BAR, TOPIC_FEAT, BusLike
from messiah.core.messages import HORIZON_SECONDS, BarClosed, FeatureVector, Horizon
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
        # 계산기(px_core/vl_core)는 `bars[-window:]` 같은 슬라이스를 그대로 쓰는데
        # `collections.deque`는 슬라이스를 지원하지 않는다(정수 인덱싱만 가능— 파이썬 표준
        # 동작, 버그 아니라 deque의 알려진 제약). `history`를 deque 그대로 넘기면 슬라이스를
        # 쓰는 계산기가 전부 TypeError → `_safe_call`이 조용히 None으로 삼켜, PX 30개 중
        # 정수 인덱싱만 쓰는 소수(px_ret/px_mom/px_accel 등)를 제외한 대다수가 워밍업과
        # 무관하게 항상 NaN이었다(2026-07-29 발견 — 리스트로 바꾸자 실제 값 산출 확인).
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
