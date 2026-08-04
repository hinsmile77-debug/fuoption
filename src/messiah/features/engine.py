"""Realtime Feature Engine 골격 — Master Plan Ver 2.0 §9 W6~8 (Ver 1.1 §2-2), VL 결선 W22~23.

`bar.{horizon}.{symbol}`을 구독해 Horizon별 롤링 윈도우를 갱신하고, **`feature_set`이 지정한
카테고리의** 계산기를 전부 돌려 `FeatureVector`를 조립·발행한다(`feat.{horizon}.{symbol}`).

## 어느 피처를 계산할지는 이 파일이 정하지 않는다 (2026-08-04, F0-1)

`features/spec.py`가 `feature_set` 이름 하나를 카테고리 목록으로, 카테고리를 정확한 피처
이름 목록으로 푼다. 이 엔진은 그 스펙을 따라 돌 뿐이다.

그 전에는 여기 `if self._flow is not None`이 있어 **주입 여부가 벡터 모양을 바꿨다**.
카테고리가 MS·OP·RG·EV까지 늘면 그 분기는 2^5 조합이 되고, 어느 조합인지 `feature_set`
문자열로는 알 수 없다. 게다가 실제로 `FeatureEngine` 생성처 7곳 전부가 `flow_history`를
안 넘기고 있었다 — FL 9개는 코드가 있는데 **모델에 한 번도 도달한 적이 없었다**. 이제
스펙이 요구하는 사이드카가 없으면 **생성 시점에 거부**한다.

신규 카테고리는 `spec.CATEGORIES`에 한 줄 추가하면 되고, 이 파일은 안 고친다.

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
from messiah.features import px_core
from messiah.features import spec as feature_spec

# 등록된 **모든** 피처가 계산 가능한 최소 봉 수 이상이어야 한다.
#
# 2026-08-04까지 이 값은 130이었고, 근거로 "px_hurst(최대 120)·px_accel(2*60+1=121)를 전부
# 커버한다"고 적혀 있었다. 그런데 그 계산에서 두 피처가 빠져 있었다:
#
#     px_ema_cross_60 : slow EMA가 3*W = **180봉** 필요  → 130으로는 영원히 계산 불가
#     px_macd_h_60    : 2*W=120 + 시그널 EMA(W//3=20)   → **139봉** 필요
#
# 그래서 이 둘은 **프로덕션에서도 항상 NaN**이었다. 증거는 매일 찍히던 무결성 리포트의
# `nan_ratio` 중앙값 0.0165다 — 121개 피처 중 정확히 2개(2/121 = 0.01653). 값이 매일 똑같이
# 나오는데도 "정상 수준"으로 읽혀 아무도 그 2개가 무엇인지 묻지 않았다.
#
# 이제 200으로 올린다(최대 요구 180 + 여유). 상수 하나로 두면 같은 사고가 재발하므로,
# `tests/features/test_engine.py`가 **등록된 전 피처 × 전 윈도우**를 실제로 계산해 이 용량
# 안에서 값이 나오는지 검사한다 — 새 피처가 더 긴 윈도우를 요구하면 그 테스트가 먼저 깨진다.
#
# 이 값은 두 가지로 쓰인다: ① 롤링 히스토리 보관 개수(deque maxlen) ② 아래 FeatureNaN 경고의
# "워밍업 완료" 판정 기준(len(history)가 maxlen에 도달했다는 건 최소 이만큼의 봉을 봤다는 뜻).
_MAX_HISTORY = 200

_NAN_RATIO_HALT_THRESHOLD = 0.20  # Ver 1.1 §2-2: 20% 초과 시 해당 Horizon 신호 정지

# "가격 퇴화" 판정 창 (2026-07-31) — 최근 이만큼의 봉 종가가 전부 같은 값이면, 그 구간의
# 롤링 표준편차 계열(px_zscore·px_bb_*·vl_rv·vl_atr_rel …)은 **0으로 나누게 되어 정의 자체가
# 안 된다**. 값이 "빠진" 게 아니라 "없는" 것이다.
#
# 창 크기는 `px_core.W_STD`/`vl_core.W_STD`의 중간값 20을 쓴다 — 최솟값(5)은 정상 시장에서도
# 우연히 5봉 연속 동일가가 나올 수 있어 오탐이 잦고, 최댓값(60)은 2026-07-31 15:20 시점처럼
# "최근 20봉만 고정, 그 앞은 움직임"인 실제 형태를 못 잡는다(그날 nan_ratio가 0.0165에서
# 0.3306으로 뛴 구간이 정확히 이 형태였다).
_DEGENERATE_WINDOW = 20


def _is_price_degenerate(history: Sequence[BarClosed]) -> bool:
    """최근 `_DEGENERATE_WINDOW`봉의 종가가 전부 같은가 — "NaN인데 결측은 아닌" 경우의 판정.

    종가만 본다(고가·저가는 안 본다). 판정 목적이 "표준편차 계열이 0으로 나누는가"이고, 그
    계열들은 전부 종가 수익률에서 나오기 때문이다 — 봉 내부에서 가격이 조금 흔들렸어도
    종가가 동일하면 수익률은 0의 연속이라 결과는 같다.
    """
    if len(history) < _DEGENERATE_WINDOW:
        return False
    return len({bar.c_ticks for bar in history[-_DEGENERATE_WINDOW:]}) == 1


class FeatureEngine:
    """단일 심볼용 — 전 Horizon의 완성봉을 구독해 PX+VL Feature를 계산·발행한다.

    ## NaN에는 원인이 세 가지 있고, 셋은 다른 사건이다 (2026-07-31)

    - **워밍업**: 롤링 윈도를 아직 못 채웠다. 정상이고, 그래서 경고하지 않는다(2026-07-24).
    - **결측**: 봉이 안 들어오거나 계산이 실패했다. 진짜 데이터 사고 — `FeatureNaN`.
    - **퇴화**: 가격이 아예 안 움직여 표준편차 계열이 **정의 불가**다. 데이터는 멀쩡하다 —
      `FeatureDegenerate`.

    셋을 한 문구로 찍으면 사람이 매번 처음부터 조사하게 된다. 2026-07-31이 그 경우였다:
    15:20 이후 1m NaN 33% 경고가 15회 찍혔는데 전부 퇴화(상한가 고착, 14:21부터 마감까지
    51814틱 고정)였고, 결측과 구분이 안 돼 수집 장애를 먼저 의심하게 만들었다.
    """

    def __init__(
        self,
        symbol: str,
        bus: BusLike,
        feature_set: str,
        horizons: Sequence[Horizon] | None = None,
        sidecars: Mapping[str, object] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        입력: `feature_set`은 `features/spec.py`가 아는 이름이어야 한다 — 미등록 이름은 기저
             카테고리(PX+VL)로 해석되고 `FeatureSetUnregistered`가 남는다(운영 설정은
             `core/config.py` 검증기가 기동 시점에 먼저 거부한다).
             `sidecars`는 카테고리가 요구하는 봉 밖 상태(`features/sidecar.DailySidecar`) —
             FL이면 `{"flow": FlowHistory(...)}`.
        실패 조건: 스펙이 요구하는 사이드카가 빠졌거나, 스펙이 안 쓰는 사이드카를 넣었으면
                  **여기서** ValueError. 둘 다 "붙인 줄 알았는데 안 붙었다"의 서로 다른
                  얼굴이고, 런타임에는 `nan_ratio`로만 흐릿하게 드러난다(2026-08-04에
                  FL이 정확히 그렇게 7곳 전부에서 빠져 있었다).
        """
        self._symbol = symbol
        self._bus = bus
        self._feature_set = feature_set
        self._spec = feature_spec.resolve(feature_set)
        self._sidecars: dict[str, object] = dict(sidecars or {})
        self._assert_sidecars_match_spec()
        self._horizons = list(horizons) if horizons is not None else list(Horizon)
        self._history: dict[Horizon, deque[BarClosed]] = {
            h: deque(maxlen=_MAX_HISTORY) for h in self._horizons
        }
        self._session = px_core.SessionState()
        # `SessionState`를 갱신할 Horizon — M1을 구독하면 M1, 아니면 **구독 중 가장 촘촘한**
        # Horizon (2026-08-04).
        #
        # 그 전에는 M1으로만 갱신했다. 라이브는 M1을 구독하니 문제가 없었지만, 학습 경로
        # (`models/trainer.build_feature_vectors()`)는 학습 Horizon 하나짜리 엔진을 만들고
        # 그 Horizon 봉만 흘린다 — M1이 한 번도 안 들어와 `SessionState`가 영영 비었고,
        # `px_gap_open`/`px_open_ret`/`px_range_pos_d` 3개가 **학습에서만 항상 NaN**이었다.
        # 추론에서는 값이 나오므로 train/serve 불일치이기도 했다(모델은 그 3개를 안 쓰도록
        # 배우고, 실전에서는 값이 들어온다).
        #
        # 굵은 봉으로 갱신해도 이 3개는 정확하다: 세션 시가는 그날 첫 봉의 시가이고,
        # 세션 고/저는 구성 분봉의 max/min이라 어느 Horizon으로 집계해도 같은 값이 나온다.
        # M1을 우선하는 이유는 장중 갱신이 가장 촘촘해서지 결과가 달라서가 아니다.
        self._session_horizon = (
            Horizon.M1
            if Horizon.M1 in self._horizons
            else min(self._horizons, key=lambda h: HORIZON_SECONDS[h], default=Horizon.M1)
        )
        self._monotonic = monotonic
        self._last_publish_at: float | None = None
        self._last_nan_ratio: dict[Horizon, float] = {}

    def _assert_sidecars_match_spec(self) -> None:
        missing = feature_spec.missing_sidecars(self._spec, self._sidecars)
        if missing:
            raise ValueError(
                f"feature_set '{self._feature_set}'은 사이드카 {list(missing)}를 요구하는데 "
                f"주입되지 않았다 — 그대로 두면 해당 카테고리가 통째로 사라진 벡터가 "
                f"'{self._feature_set}' 이름을 달고 나간다"
            )
        unexpected = feature_spec.unexpected_sidecars(self._spec, self._sidecars)
        if unexpected:
            raise ValueError(
                f"feature_set '{self._feature_set}'이 안 쓰는 사이드카 {list(unexpected)}가 "
                f"주입됐다 — 주입한 쪽은 그 피처가 나온다고 믿고 있다(feature_set을 해당 "
                f"카테고리 포함 버전으로 바꿀 것: {list(feature_spec.registered_names())})"
            )

    @property
    def spec(self) -> feature_spec.FeatureSpec:
        """이 엔진이 계산하는 피처의 정본 — 호출측이 열 순서·개수를 확인할 때 쓴다."""
        return self._spec

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

        for bar in self._history.get(self._session_horizon, ()):
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
        if bar.horizon == self._session_horizon:
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
        # 계산할 카테고리는 `feature_set`이 정한다 — 여기서 분기하지 않는다(모듈 docstring).
        # 사이드카 유무로 모양이 갈리던 종전 구조와 달리, 같은 이름은 항상 같은 모양이다.
        values: dict[str, float | None] = {}
        for category in self._spec.category_specs:
            for name, fn, windows in category.windowed:
                for window in windows:
                    values[f"{name}_{window}"] = self._safe_call(fn, history, window)
            for name, stateful_fn in category.stateful:
                values[name] = self._safe_call(stateful_fn, history, self._session)
            if category.sidecar is not None:
                sidecar = self._sidecars[category.sidecar]  # 생성 시점에 존재를 보장했다
                for name, sidecar_fn in category.sidecar_features:
                    values[name] = self._safe_call(sidecar_fn, history, sidecar)

        nan_ratio = sum(1 for v in values.values() if v is None) / len(values)
        # 워밍업 중(예: 30m은 최대 윈도우 60개를 채우는 데만 30시간 = 며칠이 걸림)엔 nan_ratio가
        # 높은 게 정상이라 매 봉마다 WARNING을 찍으면 agenda.py의 주간 경보 집계가 이 잡음에
        # 파묻힌다(2026-07-24, 실제 운영 로그 리뷰 중 발견) — len(history)가 _MAX_HISTORY에
        # 도달해 "워밍업이 끝났어야 할 시점"이 된 뒤에도 nan_ratio가 여전히 높을 때만 경고한다.
        warmed_up = len(history) >= _MAX_HISTORY
        if warmed_up and nan_ratio > _NAN_RATIO_HALT_THRESHOLD:
            if _is_price_degenerate(history):
                # 원인이 데이터 결측이 아니라 시장 상태다 — 같은 문구로 찍으면 사람이 매번
                # 수집 장애를 의심하며 처음부터 조사하게 된다(2026-07-31 실측: 15:20 이후
                # 1m NaN 33% 경고 15회가 전부 이 경우였는데 결측과 구분이 안 됐다).
                mlog.log(
                    "FeatureDegenerate",
                    f"NaN 비율 {nan_ratio:.0%} — {bar.horizon.value} 최근 {_DEGENERATE_WINDOW}봉 "
                    f"종가가 전부 {history[-1].c_ticks}틱으로 고정, 변동성 계열 정의 불가"
                    "(결측 아님 — 상한/하한 고착 또는 일방시장 의심)",
                    symbol=self._symbol,
                    horizon=bar.horizon.value,
                    nan_ratio=nan_ratio,
                    cause="degenerate",
                    flat_close_ticks=history[-1].c_ticks,
                    flat_window=_DEGENERATE_WINDOW,
                )
            else:
                mlog.log(
                    "FeatureNaN",
                    f"NaN 비율 {nan_ratio:.0%} — {bar.horizon.value} 신호 정지 권고",
                    symbol=self._symbol,
                    horizon=bar.horizon.value,
                    nan_ratio=nan_ratio,
                    cause="missing",
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
