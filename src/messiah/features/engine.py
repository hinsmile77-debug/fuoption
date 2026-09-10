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

import json
import math
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

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
from messiah.core.timeutil import KST, now_kst
from messiah.data.close_grace import close_grace_ms
from messiah.features import px_core
from messiah.features import spec as feature_spec
from messiah.obs.delay_spike import DelaySpikeWatch

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

# 워밍업 중 NaN 임계 초과의 **재고지 간격** (2026-08-14 F-9). 봉 시각 기준이다.
# 30분이면 1m은 하루 최대 13건, 30m은 15건이 아니라 실질 15건 그대로지만 — 중요한 것은
# **매 봉이 아니라는 것**이다. 롤 당일 전 Horizon이 동시에 초과해도 로그가 잠기지 않는다.
_WARMUP_NAN_RENOTIFY = timedelta(minutes=30)


# 이 개수 미만의 표본으로는 "상수다"라고 말하지 않는다 — 장 초반 몇 봉은 우연히 같은 값이
# 나올 수 있고, 워밍업 구간의 NaN도 아직 안 풀린 상태다. 30봉이면 1m 기준 30분치다.
_MIN_SAMPLES_FOR_HEALTH = 30

# 하루가 만드는 Horizon별 봉 수 — **실측값이다** (2026-08-14 재합성 출력:
# `1m=410 → 3m=137 5m=82 10m=42 15m=28 30m=15`). 계산으로 갈음하지 않는 이유: 장전
# 08:45~09:00의 15분과 마감 경계 처리 때문에 단순 나눗셈과 어긋난다(30m은 계산상 13봉인데
# 실제 15봉이다). 회복 시점을 **거래일 단위**로 환산할 때 이 표가 분모가 된다.
BARS_PER_SESSION: dict[Horizon, int] = {
    Horizon.M1: 410,
    Horizon.M3: 137,
    Horizon.M5: 82,
    Horizon.M10: 42,
    Horizon.M15: 28,
    Horizon.M30: 15,
}


def _probe_bars(count: int) -> list[BarClosed]:
    """요구 봉 수 측정용 합성 봉 — **가격이 움직여야 한다**.

    상수 가격이면 롤링 표준편차 계열이 0으로 나누게 되어 정의 자체가 안 되고
    (`_DEGENERATE_WINDOW` 주석), 그러면 "윈도가 모자라서 None"과 구분이 안 된다.
    톱니로 흔들어 두 원인을 가른다.
    """
    base = datetime(2026, 1, 5, 9, 0, tzinfo=KST)
    out: list[BarClosed] = []
    for i in range(count):
        close = 100_000 + (i * 37) % 811 - 405  # 단조증가가 아니라 진동 — 방향성 편향 제거
        out.append(
            BarClosed(
                symbol="PROBE",
                horizon=Horizon.M1,
                bar_open_kst=base + timedelta(minutes=i),
                o_ticks=close - 3,
                h_ticks=close + 11,
                l_ticks=close - 13,
                c_ticks=close,
                volume=100 + (i % 17),
                trades=10,
                quality_ok=True,
            )
        )
    return out


@lru_cache(maxsize=8)
def required_bars_by_feature(feature_set: str) -> dict[str, int]:
    """피처별로 **값이 나오기 시작하는 최소 봉 수** — 가정이 아니라 측정 (2026-08-14 G-5).

    ## 왜 윈도 크기로 갈음하면 안 되나

    `px_ema_cross_60`은 윈도가 60인데 slow EMA가 `3×W`를 요구해 **180봉**이 필요하고,
    `px_macd_h_60`은 `2×W + W//3` = **139봉**이다. 그래서 두 피처는 용량이 130이던 시절
    **프로덕션에서 영원히 NaN이었다**(모듈 상단 `_MAX_HISTORY` 주석). 윈도 최댓값으로
    갈음했으면 60이라고 답했을 것이고, 그 답은 8거래일간 아무도 못 잡은 결함을 그대로
    재생산한다.

    ## 무엇에 쓰나

    롤 당일 아침에 **"이 Horizon은 몇 번째 발행부터 회복이 시작되고 임계 도달은 언제인가"**
    를 계산해 로그 한 줄로 남긴다(`FeatureWarmStart.required_by_horizon`). 2026-08-14엔
    그 상수가 어디에도 없어서 사람이 12:30까지 세 번 계산했고 **그중 한 번은 틀렸다**
    (10:51에 "30m 회복이 정확히 0"이라 확정했는데 11:30에 62.0%로 회복 중이었다).

    사이드카를 요구하는 카테고리는 건너뛴다 — 그쪽은 외부 데이터의 유무가 값을 정하므로
    봉 수로 답할 수 있는 질문이 아니다.
    """
    spec = feature_spec.resolve(feature_set)
    bars = _probe_bars(_MAX_HISTORY)
    required: dict[str, int] = {}
    for category in spec.category_specs:
        if category.sidecar is not None:
            continue
        for name, fn, windows in category.windowed:
            for window in windows:
                key = f"{name}_{window}"
                # 윈도보다 적은 봉으로는 어떤 롤링 계산도 못 한다 — 거기서 시작한다.
                for n in range(min(window, _MAX_HISTORY), _MAX_HISTORY + 1):
                    try:
                        if fn(bars[:n], window) is not None:
                            required[key] = n
                            break
                    except Exception:  # noqa: BLE001 — 못 재는 피처는 목록에서 빠진다
                        break
    return required


@lru_cache(maxsize=8)
def required_bars(feature_set: str) -> int:
    """이 피처셋의 **전 피처가 값을 내는** 최소 봉 수 (2026-08-14 G-5)."""
    return max(required_bars_by_feature(feature_set).values(), default=0)


def recovery_forecast(bars_now: int, feature_set: str, horizon: Horizon) -> str:
    """지금 봉 수에서 **언제 회복되는가**를 사람 말로 (2026-08-14 G-5).

    하루가 만드는 봉 수로 나눠 거래일까지 환산한다 — "180봉 더 필요"보다 "3거래일 뒤"가
    운영 판단에 쓰이는 단위다. 2026-08-14에 사람이 손으로 세 번 한 계산이 이것이다.
    """
    need = required_bars(feature_set)
    if bars_now >= need:
        return f"{horizon.value} 충족({bars_now}/{need}봉)"
    per_day = BARS_PER_SESSION.get(horizon)
    short = need - bars_now
    if not per_day:
        return f"{horizon.value} {short}봉 부족({bars_now}/{need})"
    days = math.ceil(short / per_day)
    return (
        f"{horizon.value} {short}봉 부족({bars_now}/{need}) — "
        f"하루 {per_day}봉이므로 약 {days}거래일"
    )


@dataclass
class _FeatureStat:
    """피처 1개의 세션 누적 통계 — 상수·항상NaN을 **운영 경로에서** 잡기 위한 최소 상태.

    ## 왜 nan_ratio로는 부족했나 (2026-08-04 피처 관문이 처음 발견)

    `px_macd_h_5`가 프로덕션에서 **항상 정확히 0**이었다(`window=5` → `5//3=1` →
    `_ema_series(x,1)`이 항등 → 히스토그램 상수 0). `px_ema_cross_60`은 NaN이라
    `nan_ratio`에 흔적이 남았지만, **이건 값을 내므로 무결성 리포트에 아무 흔적도 없었다.**
    관문(연구 경로)을 처음 돌렸을 때 "IC 정의 불가 — 값이 상수"로 비로소 드러났다.

    검출 수단이 하나뿐이면 그 수단이 못 보는 결함은 안 보인다. 그래서 운영 경로에도
    같은 검출력을 둔다 — 비용은 피처당 float 4개다.
    """

    n: int = 0
    n_nan: int = 0
    lo: float = math.inf
    hi: float = -math.inf

    def observe(self, value: float | None) -> None:
        self.n += 1
        if value is None or math.isnan(value):
            self.n_nan += 1
            return
        self.lo = min(self.lo, value)
        self.hi = max(self.hi, value)

    @property
    def always_nan(self) -> bool:
        return self.n >= _MIN_SAMPLES_FOR_HEALTH and self.n_nan == self.n

    @property
    def constant(self) -> bool:
        """값을 내는데 **한 번도 안 변한** 피처. 항상 NaN인 것과는 다른 사건이다."""
        if self.n < _MIN_SAMPLES_FOR_HEALTH or self.n_nan == self.n:
            return False
        return self.lo == self.hi


@dataclass(frozen=True)
class FeatureHealth:
    """한 Horizon의 세션 누적 피처 건강도 — 리포트가 읽는 형태."""

    horizon: str
    samples: int
    always_nan: list[str]
    constant: list[str]
    # **허용된 상수의 실제 값** (2026-08-11). 퇴화로는 안 세지만 값은 남긴다 —
    # `ev_dow_*`는 매 거래일 반드시 달라져야 하므로, 이 값이 전일과 같으면 캘린더 사이드카가
    # 얼어붙었다는 뜻이다. 화이트리스트가 검출을 **끄는** 것이 아니라 하루 단위 축에서
    # 날짜 단위 축으로 **옮기는** 것이고, 이 필드가 그 이관의 재료다
    # (`ops/integrity_report._calendar_freeze_finding`).
    allowed_constant_values: dict[str, float] = field(default_factory=dict)

    @property
    def judged(self) -> bool:
        """표본이 판정 하한을 넘겼는가 (2026-08-14 F-C).

        **`degenerate_count == 0`에는 두 뜻이 있다**: 검사했는데 없었거나, 검사를 못 했거나.
        2026-08-14 리포트가 *"30m 피처 퇴화 0건(14표본)"* 이라고 말했는데, 30m은 하루 15봉이
        물리적 상한이라 하한 30을 **어떤 날에도 못 넘는다** — 가장 위험한 Horizon에 대한
        가장 안심되는 문장이 매일 나오고 있었다.

        이 속성이 그 둘을 가른다. 임계를 낮추는 것은 답이 아니다(오탐이 는다) — 답은
        다일 누적 판정이고 그건 별건이다(고도화 G-9).
        """
        return self.samples >= _MIN_SAMPLES_FOR_HEALTH

    @property
    def degenerate_count(self) -> int:
        return len(self.always_nan) + len(self.constant)


def _base_feature_name(name: str) -> str:
    """`px_ema_cross_20` → `px_ema_cross`. 윈도우형 이름은 `f"{기저}_{윈도우}"`로 만들어진다
    (`_build_feature_vector`) — 판정은 기저 이름으로 한다."""
    head, _, tail = name.rpartition("_")
    return head if head and tail.isdigit() else name


def _constant_is_normal(name: str) -> bool:
    """세션 내내 안 변해도 결함이 아닌 피처인가 (2026-08-06, 2026-08-11 정본 이관).

    근거와 목록은 **각 계산기 모듈의 `INTRADAY_CONSTANT_OK`** — 정의상 상수(`px_gap_open`)
    이거나 날짜만 보는 캘린더 값(`ev_dow_*`, `ev_dte_*`)이다. 선언은 정의 옆에 있고 여기서는
    `spec.intraday_constant_ok()`로 모아 읽는다.

    ## 왜 `px_core`를 직접 안 보는가 (2026-08-11)

    종전엔 이 줄이 `px_core.INTRADAY_CONSTANT_OK`를 직접 참조했다. 그래서 2026-08-10에 운영
    피처셋을 `v2026.08-ev`로 올리자 다음 날 리포트가 4개 Horizon 전부에 "피처 11개가 세션
    내내 죽어 있었다"를 찍었다 — 전부 EV 캘린더 값이고, 하루 안에서 상수인 것이 **정의**다.
    등록부 `no-degenerate-features`(임계 0)는 그날부터 구조적으로 통과 불가였다.

    카테고리가 늘 때마다 판정기를 고쳐야 하는 구조 자체가 문제였다. 이제 카테고리가 자기
    상수를 선언하고 판정기는 그것을 모아 본다.

    판정 자체는 `spec.is_intraday_constant_ok()` 한 곳이다 — 장후 리포트도 같은 함수를
    부르므로 두 경로가 갈릴 수 없다(그쪽 docstring).
    """
    return feature_spec.is_intraday_constant_ok(name)


def _is_price_degenerate(history: Sequence[BarClosed]) -> bool:
    """최근 `_DEGENERATE_WINDOW`봉의 종가가 전부 같은가 — "NaN인데 결측은 아닌" 경우의 판정.

    종가만 본다(고가·저가는 안 본다). 판정 목적이 "표준편차 계열이 0으로 나누는가"이고, 그
    계열들은 전부 종가 수익률에서 나오기 때문이다 — 봉 내부에서 가격이 조금 흔들렸어도
    종가가 동일하면 수익률은 0의 연속이라 결과는 같다.
    """
    if len(history) < _DEGENERATE_WINDOW:
        return False
    return len({bar.c_ticks for bar in history[-_DEGENERATE_WINDOW:]}) == 1


#: 발행 오프셋을 **어느 시간축으로 쟀는가** (2026-08-21 F-12).
#:
#: `exchange_vs_local`: 봉 확정은 거래소 시각 경계, 발행 시각은 로컬 시계 — 두 축이다.
#:   1m만 여기 해당하고, 롤링 스큐로 보정한다.
#: `local_only`: 봉 도착도 발행도 로컬 시계 — 한 축이다. 3m 이상이 여기 해당하고,
#:   보정하면 없던 계통오차가 생긴다.
_PUBLISH_OFFSET_AXIS_EXCHANGE = "exchange_vs_local"
_PUBLISH_OFFSET_AXIS_LOCAL = "local_only"

#: **발행 지연 예산** — 봉을 받고 나서 내보내기까지의 상한(ms) (2026-08-24 F-21).
#:
#: ## 무엇에 거는 값인가
#:
#: `publish_offset_ms`(= 봉 확정 → 발행)가 **아니다.** 그 값에는 **기다린 시간**이
#: 섞여 있다 — 합성 스케줄러의 위상 0.5초와, 마지막 1분봉이 오기를 기다린 겹④가
#: 전부 그 안에 들어간다. 대기와 계산이 한 숫자에 섞여 있으면 어떤 임계도 뜻이 없다.
#:
#: 이 예산이 재는 것은 `bar_to_publish_ms` — **`handle_bar()`가 봉을 받은 순간부터
#: 발행이 끝날 때까지**의 monotonic 경과다. 엔진이 자기 몫으로 쓴 시간만 남는다.
#:
#: ## 왜 1,000ms인가 — 그리고 **이 값은 아직 조여야 한다**
#:
#: 두 실측 사이에서 골랐다.
#:
#:   **상한** (2026-08-21·08-24 라이브 로그 598건). 대기를 걷어낸 계산 시간
#:   = 합성봉 발행 − max(합성 위상 500ms, 마지막 1분봉 발행):
#:       p50 111ms · p90 289ms · p99 479ms · 최대 602ms · 1초 이내 100%
#:   **다만 이 구간에는 합성기의 몫(버킷 조립·BarClosed 발행)이 함께 들어 있다** —
#:   즉 `bar_to_publish_ms`(엔진 몫만)의 **상한 추정치**다.
#:
#:   **하한** (같은 날 봉을 이 엔진에 다시 흘린 리플레이, 유휴 머신):
#:       1m p99 63ms · 3m p99 47ms · 5m 최대 31ms · 30m 0ms
#:   경합이 없는 환경이라 라이브의 **하한**이다.
#:
#: 라이브의 진짜 값은 이 둘 사이에 있고, **F-21 이전에는 아무도 그 값을 잰 적이 없다.**
#: 1,000ms는 상한 추정치(479ms p99)의 2.1배 · 관측 최대(602ms) 위로 66% 여유다 —
#: 첫 예산으로는 넉넉한 쪽이고, 그것이 의도다. 값을 고른 방식은
#: `data/normalizer.MINUTE_CLOSE_GRACE_SECONDS`(2026-08-11 G-4)와 같다: 관측 위로
#: 여유를 두고, 그 여유를 명시한다.
#:
#: **며칠 쌓인 뒤 사람이 조인다.** `ops/integrity_report.publish_sla`가 매일
#: `verdict: "recorded_only"`로 분포를 쌓는다(R18) — 그 분포가 조일 근거다.
#: 지금 타이트하게 잡으면 재 본 적 없는 값에 임계부터 세우는 것이고, 그것이 정확히
#: 이 항목(500ms)이 6거래일 연속 경고를 낸 방식이다.
#:
#: ## 배수(×4) 장치를 없앤 이유
#:
#: 종전에는 `유예 500ms × 4 = 2,000ms`였다. 배수가 필요했던 것은 밑값이 실측에서 온
#: 값이 아니었기 때문이다 — 500ms는 합성 스케줄러의 위상이라 3m~30m 발행 오프셋이
#: **구조적으로** 그보다 클 수밖에 없었고(2거래일 598건 중 500ms 미만 0건 · 최소 532ms),
#: 1배로 잡으면 매일 400줄이 났다. 이제 밑값 자체가 실측이므로 배수가 필요 없다.
_PUBLISH_SLA_MS = 1000.0


#: 그 Horizon의 발행이 **자료를 실제로 버리기까지** 남은 예산 (2026-08-25 F-43).
#:
#: `_PUBLISH_SLA_MS`(1,000ms)와 다른 질문이다. 저쪽은 「느린가」를 묻는 채점 기준이고,
#: 이쪽은 「넘으면 손실인가」를 묻는 **경계**다 — 2026-08-25에 1분봉 발행 오프셋 최대가
#: 3,119.7ms였는데 그날 자료 손실은 0건이었다. 두 값이 한 축에 섞여 있으면 그 사실을
#: 말할 수 없다: 예산 초과 25.7%와 손실 0건이 동시에 참인 이유가 바로 이 차이다.
#:
#: 값은 상류 정본에서 가져온다 — 여기에 숫자를 다시 적으면 두 곳이 갈라진다
#: (`tests/ops/test_publish_offset_axis.py`가 동일성을 강제한다).
def _grace_ms(horizon: Horizon) -> float:
    # 정책은 `data/close_grace.py`가 갖는다 (2026-09-01 F-82) — 화면도 같은 값을 써야
    # 하는데 이 모듈은 화면이 들일 수 없는 무게다. 이름은 남긴다: 이 파일의 호출부와
    # `tests/ops/test_intraday_tail_axis.py`가 「엔진이 쓰는 경계」를 이 이름으로 묻는다.
    return close_grace_ms(horizon)


#: 같은 정체로 묶는 시간 폭 — 여러 Horizon 경계가 한 순간에 겹칠 때 (2026-08-21 F-8).
#:
#: 2026-08-21 실측에서 14군집 중 5군집이 2개 이상 Horizon이 함께 늦은 형태였다.
#: 개별로 남기면 "한 Horizon이 늦었다"와 "루프가 멈췄다"가 로그에서 구분되지 않는다.
_PUBLISH_STALL_CLUSTER_MS = 100.0

#: 군집을 **시간으로** 닫는 상한 — 다음 정체를 기다리지 않는다 (2026-08-24 F-24).
#:
#: 종전 주석은 *"그날 마지막 한 건은 세션 요약 시점에 남는다"* 며 대가를 한 건으로
#: 적었다. 2026-08-24 실측은 그것이 과소평가였다: 그날 7군집 중 **6건이 3~149분 밀렸고**
#: 중앙 지연이 10분이었다. 08:56에 생긴 정체가 11:25에 기록됐다 — 군집을 닫는 유일한
#: 계기가 「다음 정체의 도착」이었기 때문이다.
#:
#: 값을 새로 쓰지 않고 군집 창의 2배로 유도한다(F-15 ②). 2배인 이유: 늦게 도착한
#: Horizon이 이미 닫힌 군집에 속하면 같은 정체가 두 줄로 쪼개진다 — 그 여유분이다.
_PUBLISH_STALL_FLUSH_MS = _PUBLISH_STALL_CLUSTER_MS * 2.0

#: **2차 방어다** (2026-08-24 F-28). 1차는 `mode`다 — 리플레이·학습 경로가 스스로
#: 그렇다고 말한다. 값으로 가르는 이 상한은 지우지 않고 남긴다: 상한을 넘는 값이
#: 라이브라고 주장하면 그것은 라이브가 아니다(2026-08-21 최대 5.5초).
#: 상한만으로 부족했던 이유는 **당일 재합성**이다 — 장후 배치가 그날 봉을 다시 흘리면
#: 오프셋이 몇 분 단위라 1시간 상한 **안쪽**에 들어온다. 2026-08-24 장후 배치가
#: 라이브와 글자 하나 다르지 않은 경보 문구를 11줄 찍은 것이 그 형태다(이상점 1-16).
#:
#: **재는 대상이 바뀌었다** (2026-08-24 F-21). 경보 축은 이제 `bar_to_publish_ms`
#: (monotonic 경과)이고 벽시계 차가 아니다 — 리플레이라고 수십억 ms가 나오지 않는다.
#: 그래도 이 상한을 남기는 이유는 그대로 옳다: 이만큼 걸린 계산은 「느린 것」이 아니라
#: 프로세스가 멈췄다 깨어난 것이고, 그것을 발행 지연 경보로 올리면 진짜 신호가 묻힌다.
#:
#: 이 값을 넘는 오프셋은 **발행 지연이 아니다** — 리플레이·학습이다 (2026-08-21).
#:
#: `models/trainer.build_feature_vectors()`는 같은 `FeatureEngine`으로 과거 봉을 흘린다.
#: 그때 `now()`는 벽시계고 `valid_until`은 몇 달 전이라 오프셋이 **수십억 ms**가 된다
#: (첫 구현에서 학습 테스트가 `발행 유예 초과 — 5m 2377834874ms`를 봉마다 찍었다).
#: 그것은 "늦게 발행했다"가 아니라 "지금 재생 중이다"이고, 경보로 올리면 진짜 신호가
#: 그 아래 묻힌다.
#:
#: 1시간은 어떤 실측 정체보다도 크다(2026-08-21 최대 5.5초). 그리고 이 경계를 넘는
#: **값 자체는 버리지 않는다** — `_publish_offsets`에 그대로 쌓여 세션 요약에 실린다.
#: 조용히 없애는 것이 아니라, 경보 축에서만 뺀다.
_PUBLISH_OFFSET_LIVE_CEILING_MS = 60 * 60 * 1000.0


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
        now: Callable[[], datetime] = now_kst,
        clock_skew_seconds: Callable[[], float | None] | None = None,
        mode: str = "replay",
    ) -> None:
        """
        입력: `feature_set`은 `features/spec.py`가 아는 이름이어야 한다 — 미등록 이름은 기저
             카테고리(PX+VL)로 해석되고 `FeatureSetUnregistered`가 남는다(운영 설정은
             `core/config.py` 검증기가 기동 시점에 먼저 거부한다).
             `sidecars`는 카테고리가 요구하는 봉 밖 상태(`features/sidecar.DailySidecar`) —
             FL이면 `{"flow": FlowHistory(...)}`.
             `mode`는 `"live"` 또는 `"replay"`(기본) — **기본값이 replay인 것이 의도다**
             (2026-08-24 F-28). 발행 유예 경보는 「지금 늦게 내보냈다」는 뜻인데,
             과거 봉을 다시 흘리는 경로에서는 그 문장이 애초에 성립하지 않는다.
             기본을 `live`로 두면 새 호출부가 조용히 라이브로 취급돼 라이브와 글자 하나
             다르지 않은 경보를 찍는다 — 2026-08-24 장후 배치가 정확히 그랬다(11줄).
             안전한 쪽을 기본으로 둔다: 라이브 경로가 **명시적으로** `mode="live"`를
             넘긴다.
        실패 조건: 스펙이 요구하는 사이드카가 빠졌거나, 스펙이 안 쓰는 사이드카를 넣었으면
                  **여기서** ValueError. 둘 다 "붙인 줄 알았는데 안 붙었다"의 서로 다른
                  얼굴이고, 런타임에는 `nan_ratio`로만 흐릿하게 드러난다(2026-08-04에
                  FL이 정확히 그렇게 7곳 전부에서 빠져 있었다).
        """
        if mode not in ("live", "replay"):
            raise ValueError(f"mode는 'live' 또는 'replay' — 받은 값: {mode!r}")
        self._symbol = symbol
        self._live = mode == "live"
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
        # 워밍업 NaN 재고지 억제 상태 (2026-08-14 F-9) — Horizon별 마지막 고지 봉 시각.
        self._warmup_nan_last: dict[Horizon, datetime] = {}
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
        self._now = now
        self._last_publish_at: float | None = None
        self._last_nan_ratio: dict[Horizon, float] = {}
        # **완성봉 확정에서 발행까지 몇 ms 걸렸나** (2026-08-20 F-E). `(시각, 오프셋ms)` 쌍을
        # 세션 내내 모아 장 마감에 한 줄로 낸다 — 하루 700건 남짓이라 메모리는 무시할 수준이고,
        # 사이클마다 집계하면 그 자체가 예산을 먹는다.
        # (발행 시각, 오프셋ms, Horizon, 축) — 축을 함께 들고 있어야 세션 요약이
        # "1m은 보정된 값, 3m+는 원래부터 한 축"임을 말할 수 있다 (2026-08-21 F-12).
        self._publish_offsets: list[tuple[datetime, float, Horizon, str]] = []
        # 유실 **전에** 뜨는 사전 경보의 창 (2026-09-10 G-57). 하루 단위 분위수가 못 보는
        # 「짧고 굵은」 스파이크를 5분 롤링으로 본다 — 근거는 `obs/delay_spike` docstring.
        # 막는 것이 아니라 남기는 것이다(R18: 신설 축은 20거래일 섀도 관찰 뒤 승격 판단).
        self._delay_spike = DelaySpikeWatch()
        # 발행 시점의 **롤링** 시계 스큐를 읽는 콜러블(보통 `TickCollector.clock_skew_seconds`).
        # 없으면 보정하지 않는다 — 보정 못 하는 것이지 스큐가 0인 것이 아니다(L18).
        self._clock_skew_seconds = clock_skew_seconds
        # 아직 안 남긴 정체 군집 (2026-08-21 F-8) — (봉확정시각, [(Horizon, 오프셋ms)]).
        #
        # **왜 즉시 안 남기는가.** 같은 봉 확정 순간에 몇 개 Horizon이 걸릴지는 그 순간이
        # 지나기 전에는 모른다. 즉시 남기면 3개짜리 군집이 세 줄이 되어 「묶는다」는 목적이
        # 사라진다. 그래서 **다음 순간이 오거나 세션이 끝날 때** 한 줄로 flush한다.
        #
        # **대가는 한 건이 아니었다** (2026-08-24 F-24). 위 주석은 종전에
        # *"그날 마지막 한 건은 세션 요약 시점에 남는다"* 고 적혀 있었다. 2026-08-24
        # 실측은 7군집 중 **6건이 3~149분 밀렸고** 중앙 지연이 10분이었다 — 08:56의
        # 정체가 11:25에 기록됐다. 군집을 닫는 계기가 「다음 정체의 도착」 하나뿐이라,
        # 정체가 드문 날일수록 경보가 더 늦게 온다(있어야 할 성질의 정반대다).
        # 이제 `_PUBLISH_STALL_FLUSH_MS`가 지나면 다음 정체를 기다리지 않고 닫는다.
        #
        # 세 번째 원소는 군집을 연 시점의 monotonic 시각이다 — 벽시계를 쓰면 시계
        # 동기가 군집 수명을 바꾼다.
        self._pending_stall: tuple[datetime, list[tuple[Horizon, float]], float] | None = None
        # **봉을 받고 나서 내보내기까지** (2026-08-24 F-21) — (시각, ms, Horizon).
        # `_publish_offsets`와 나란히 두는 이유: 저쪽은 대기를 포함한 종단 지연이고
        # 이쪽은 엔진이 자기 몫으로 쓴 시간이다. 둘을 갈라 놓아야 「늦었다」의 원인이
        # 회선인지 대기인지 계산인지 갈린다.
        self._bar_to_publish: list[tuple[datetime, float, Horizon]] = []
        # 피처별 세션 누적 통계 (2026-08-05, 고도화 3) — `_FeatureStat` 주석 참고.
        self._feature_stats: dict[Horizon, dict[str, _FeatureStat]] = {
            h: {} for h in self._horizons
        }

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

    @property
    def feature_set(self) -> str:
        """이 엔진의 피처셋 이름 — `required_bars()` 등 셋 단위 질의에 쓴다 (2026-08-14 G-5)."""
        return self._feature_set

    @property
    def symbol(self) -> str:
        """이 엔진이 보는 종목 — 다일 누적 기록이 롤 경계를 표시하는 데 쓴다(2026-08-14 G-9)."""
        return self._symbol

    def seconds_since_last_publish(self) -> float | None:
        if self._last_publish_at is None:
            return None
        return self._monotonic() - self._last_publish_at

    def feature_health(self) -> list[FeatureHealth]:
        """세션 동안 **한 번도 값이 안 변한** 피처와 **항상 NaN이던** 피처를 Horizon별로.

        `nan_ratio`가 못 보는 것을 본다. 2026-08-04 관문이 처음 찾아낸 `px_macd_h_5`는
        프로덕션에서 항상 정확히 0이었는데 **값을 내므로 nan_ratio에 아무 흔적이 없었다** —
        무결성 리포트는 8거래일 내내 그 피처가 죽어 있다는 걸 말할 수단이 없었다.

        표본이 `_MIN_SAMPLES_FOR_HEALTH` 미만인 Horizon은 판정하지 않는다(빈 목록) — 장
        초반 몇 봉이 우연히 같은 값인 것과 진짜 상수를 구분할 수 없기 때문이다. 30m처럼
        하루에 15봉밖에 안 나오는 Horizon은 그래서 대부분의 날 판정되지 않는데, 그게 맞다:
        표본이 없는 것을 "정상"이라고 말하지 않는다.

        ## 정의상 상수인 피처는 상수라고 말하지 않는다 (2026-08-06)

        `px_gap_open`은 `log(당일 시가 / 전일 종가)`라 **장중에 변할 수가 없다**.
        `px_ema_cross`(sign)와 `px_breakout`(대부분 0.0)도 하루 종일 같은 값인 것이 정상
        범위다. 2026-08-06 퇴화 10건 중 **9건이 이 셋**이었고, 등록부는 `max: 0`이라
        구조적으로 통과 불가였다 — 매일 울리는 경고는 결국 아무도 안 본다.

        **검출력은 안 잃는다**: 이 셋도 `always_nan`이면 그대로 잡힌다(그게 이 피처들의
        진짜 사고다). 목록은 각 계산기 모듈의 `INTRADAY_CONSTANT_OK`에 근거와 함께 있고
        (`px_core`·`ev_core`), `spec.intraday_constant_ok()`가 모아 준다.

        2026-08-11에 EV 캘린더 11종이 같은 이유로 목록에 들어갔다 — 그쪽은 추가로 **날짜 간
        동결**을 리포트가 따로 잡는다(`ops/integrity_report._calendar_freeze_finding`).
        """
        out: list[FeatureHealth] = []
        for horizon in self._horizons:
            stats = self._feature_stats.get(horizon) or {}
            if not stats:
                continue
            samples = max((s.n for s in stats.values()), default=0)
            out.append(
                FeatureHealth(
                    horizon=horizon.value,
                    samples=samples,
                    always_nan=sorted(n for n, s in stats.items() if s.always_nan),
                    constant=sorted(
                        n for n, s in stats.items() if s.constant and not _constant_is_normal(n)
                    ),
                    allowed_constant_values={
                        n: s.lo
                        for n, s in sorted(stats.items())
                        if s.constant and _constant_is_normal(n)
                    },
                )
            )
        return out

    def log_feature_health(self) -> list[FeatureHealth]:
        """장 마감 시 한 번 부른다 — 판정 결과를 로그로 남기고 그대로 돌려준다.

        정상(퇴화 0건)일 때도 남긴다. "오늘 몇 개를 검사했고 몇 개가 죽어 있었나"가 매일
        기록돼야 `0건`이 **측정된 0**이라는 뜻이 되기 때문이다 — 로그가 없는 날은 검사를
        안 한 날과 구분되지 않는다(L18).

        ## 세 번째 상태를 어휘에 넣는다 (2026-08-14 F-C)

        종전엔 `degenerate_count`가 0인지 아닌지 **두 갈래**였다. 그래서 표본이 하한에 못
        미쳐 **판정 자체를 못 한** 날도 *"퇴화 0건"* 으로 나갔다 — 2026-08-14의
        *"30m 피처 퇴화 0건(14표본)"* 이 그것이고, 30m은 하루 15봉이 상한이라 그 문장이
        **매일** 나온다. 가장 위험한 Horizon에 대한 가장 안심되는 문장이었다.

        새 태그는 **INFO**다. WARNING으로 올리면 15m·30m가 대부분의 날 표본 미달이라 매일
        2건씩 울고, 그건 이 파일 자신이 경고해 온 형태다(*"매일 울리는 경고는 결국 아무도
        안 본다"*). 판정의 정본은 리포트의 `unmeasured` 축이고 로그는 그 근거다.
        """
        healths = self.feature_health()
        for health in healths:
            degenerate = health.degenerate_count
            if not health.judged:
                tag = "FeatureHealthNotJudged"
                msg = (
                    f"{health.horizon} 퇴화 판정 보류 — {health.samples}표본 < 최소 "
                    f"{_MIN_SAMPLES_FOR_HEALTH} (0건이 아니라 '모른다'이다)"
                )
            elif degenerate:
                tag = "FeatureHealthDegenerate"
                msg = (
                    f"{health.horizon} 피처 {degenerate}개가 세션 내내 죽어 있었다 — "
                    f"항상NaN {health.always_nan} · 상수 {health.constant}"
                )
            else:
                tag = "FeatureHealthSummary"
                msg = f"{health.horizon} 피처 퇴화 0건 ({health.samples}표본 · 판정됨)"
            mlog.log(
                tag,
                msg,
                symbol=self._symbol,
                horizon=health.horizon,
                samples=health.samples,
                judged=health.judged,
                min_samples=_MIN_SAMPLES_FOR_HEALTH,
                always_nan=health.always_nan,
                constant=health.constant,
                # 퇴화로는 안 세지만 값은 남긴다 — 날짜 간 동결 검사의 재료
                # (`FeatureHealth.allowed_constant_values` 주석).
                allowed_constant_values=health.allowed_constant_values,
            )
            # **그 상수가 야간 갭 때문인가** (2026-08-20 F-G). 종전엔 퇴화 판정이 나와도
            # 원인이 익명이라 등록부가 「수정이 듣지 않았다 · 3회 재발」로만 말했다.
            # 이 줄이 붙으면 같은 재발이 「원인이 규명된 알려진 기전」으로 읽힌다 —
            # 판정 자체는 안 바꾼다(피처가 실제로 퇴화한 것은 사실이다).
            if health.constant:
                self._log_session_boundary_inflation(health)
        return healths

    def _log_session_boundary_inflation(self, health: FeatureHealth) -> None:
        """퇴화한 Horizon에서 세션 경계 오염을 계량해 남긴다 (2026-08-20 F-G).

        실패해도 장 마감 절차를 막지 않는다 — 관측 한 줄이 하루의 종료를 멈추면 본말전도다.
        """
        try:
            horizon = Horizon(health.horizon)
        except ValueError:
            return
        bars = list(self._history.get(horizon, ()))
        # 표준 창 중 **가장 긴 것**으로 잰다 — 창이 세션(약 41봉)보다 길어야 경계가 창 안에
        # 갇히고, 그때만 이 기전이 성립한다.
        window = max(px_core.W_STD)
        try:
            inflation = px_core.session_boundary_inflation(bars, window)
        except Exception:  # noqa: BLE001
            return
        if inflation is None or not inflation.get("boundary_pairs"):
            return
        mlog.log(
            "SessionBoundaryInflation",
            f"{health.horizon} 창 {window}봉 안에 세션 경계 "
            f"{inflation['boundary_pairs']}쌍 — 최대 단봉수익률이 "
            f"{inflation['with_boundary']:+.6f}인데 경계를 빼면 "
            f"{inflation['same_session']:+.6f}이다({inflation['ratio']:.2f}배). "
            "야간 갭이 봉 한 칸으로 들어가 있다",
            symbol=self._symbol,
            horizon=health.horizon,
            window_bars=window,
            constant_features=health.constant,
            **{key: value for key, value in inflation.items()},
        )

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
            # 이 축이 재는 것은 **발행** 간격이다 — 수집기의 "수신"과 같은 단어를 쓰면
            # M1 주기(60초) 안의 정상 간격이 정체로 오독된다(`core/health.py` P1-1).
            subject="발행",
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

        # **OK일 때 무엇을 근거로 OK인지 말한다** (2026-08-05 2차, 고도화 3). 종전에는
        # "최근 수신 3초 전"만 나갔는데, 그건 신선도일 뿐 NaN 검사가 실제로 돌았다는 뜻이
        # 아니다 — `_last_nan_ratio`가 비어 있어도 같은 문장이 나갔다. 근거를 못 대는 OK와
        # 근거가 있는 OK를 화면에서 구분할 수 있어야 한다.
        if not self._last_nan_ratio:
            return HealthStatus(
                HealthLevel.UNKNOWN, f"{status.detail} · NaN 비율 표본 없음(검사 미수행)"
            )
        return HealthStatus(
            status.level, f"{status.detail} · NaN 임계 이하 {len(self._last_nan_ratio)}개 Horizon"
        )

    @property
    def history_capacity(self) -> int:
        """웜스타트 호출측이 "몇 개를 읽어와야 하는지" 알기 위한 값 — `_MAX_HISTORY`를
        호출측에 다시 하드코딩하지 않게 노출한다(단일 소스)."""
        return _MAX_HISTORY

    def warm_start(
        self,
        bars_by_horizon: Mapping[Horizon, Sequence[BarClosed]],
        *,
        prev_day_close_ticks: int | None = None,
        accept_symbols: Sequence[str] | None = None,
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

        ## 그 자연스러운 롤오버는 **장중 재기동에서 깨진다** (2026-08-05 실측)

        08:35 기동에서는 웜스타트 창(200봉)이 통째로 전일 것이라 롤오버가 반드시 일어난다.
        그런데 장중에 재기동하면 최근 200봉이 **전부 오늘 것**이라 일자 경계가 창 안에 없고,
        `prev_day_close_ticks`는 영영 None으로 남는다 — 그날 나머지 시간 내내 `px_gap_open`이
        NaN이다. 2026-08-05 14:12 재기동 후 실제로 그랬고, 그날 처음 붙은 피처 건강도 검사가
        `1m 피처 1개가 세션 내내 죽어 있었다(px_gap_open)`로 잡아냈다.

        그래서 전일 종가를 **명시적으로 받는다**. 창이 우연히 일자를 걸치는지에 기대지 않는다.
        웜스타트 봉이 일자를 걸치면 그쪽이 이기고(더 정확한 실측), 안 걸치면 이 인자가 채운다.

        ## 롤 경계 — 심볼 필터가 F-1을 통째로 무효화하고 있었다 (2026-08-16 실측)

        `ParquetArchiver.load_recent_bars_by_source()`는 롤 경계에서 직전 월물까지 이어
        읽는데, **이어 붙인 봉의 `symbol`을 의도적으로 바꾸지 않는다**(그쪽 docstring
        *"이어 붙였다는 사실이 데이터에 남아야 한다"*). 그런데 이 함수의 필터는
        `b.symbol == self._symbol` 하나였다 — **로더가 건네준 직전 월물 봉을 전량 버렸다.**

        2026-08-16 리허설 실측(대상일 2026-08-18): 로더는 30m 200봉(A05609 15 · A05608
        185)을 돌려줬는데 적재된 것은 **15봉**이었다. 2026-08-14 저녁의 F-1 커밋은 체인
        해석과 로더만 고쳤고 그 결과를 받는 쪽은 손대지 않아, 롤 이후 첫 거래일도 30m
        15봉으로 개장할 예정이었다(= 국면 하한 22봉 미달 → 판단 전량 NO_TRADE 재현).

        그래서 **받아들일 심볼을 호출측이 명시한다**. 필터를 없애지 않는 이유는 그것이
        "남의 심볼 봉이 섞여 들어오는 것"에 대한 마지막 방어선이기 때문이다(`handle_bar`의
        같은 줄과 짝을 이룬다) — 없애는 대신, 무엇을 허용하는지 말하게 한다.

        입력: Horizon별 완성봉 목록. 심볼/Horizon이 안 맞는 봉은 버린다. 시간순이 아니어도
             되며(여기서 정렬한다), 용량(`history_capacity`)을 넘으면 최신 것만 남는다.
             `prev_day_close_ticks`는 **직전 거래일의 마지막 종가**(틱 단위) — 호출측이
             아카이브에서 읽어 넘긴다(`scripts/run_l1_daily.py`의 `_load_warmup_artifacts`).
             `accept_symbols`는 이 웜스타트에서 받아들일 심볼 목록 — 롤 경계에서 로더에
             넘긴 것과 **같은 체인**(`data/backfill.warmstart_symbol_chain()`)을 넘긴다.
             생략하면 자기 심볼만 받는다(롤이 아닌 날의 기존 동작과 동일).
        반환: Horizon별로 실제 적재된 봉 수 — 호출측이 로그로 남긴다.
        """
        allowed = frozenset(accept_symbols) if accept_symbols else frozenset({self._symbol})
        for horizon, bars in bars_by_horizon.items():
            history = self._history.get(horizon)
            if history is None:
                continue  # 이 엔진이 구독하지 않는 Horizon
            accepted = sorted(
                (b for b in bars if b.symbol in allowed and b.horizon == horizon),
                key=lambda b: b.bar_open_kst,
            )
            history.clear()
            history.extend(accepted)  # deque(maxlen)이 알아서 오래된 것부터 버린다

        # 명시 인자를 **먼저** 넣는다 — 웜스타트 봉이 일자를 걸치면 `on_bar()`의 롤오버가
        # 이 값을 실측으로 덮어쓴다(그쪽이 더 정확하다). 안 걸치면 이 값이 그대로 남는다.
        if prev_day_close_ticks is not None and prev_day_close_ticks > 0:
            self._session.prev_day_close_ticks = prev_day_close_ticks

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
        # 타입부터 본다 (2026-08-07 P0-1). 2026-08-07 13:41에 이 줄이 `KillSignal`을 받아
        # `bar.symbol`에서 AttributeError를 냈고, 그것이 구독 루프째 무너뜨려 수집 프로세스가
        # 종료됐다(1시간 54분 유실). 버스가 이제 kill을 원한 구독자에게만 보내므로 그 경로는
        # 막혔지만, **핸들러가 자기 타입을 확인하는 것이 마지막 방어선**이다.
        if not isinstance(bar, BarClosed):
            return
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
        # **여기가 예산의 기점이다** (2026-08-24 F-21). 봉이 도착한 순간부터 잰다 —
        # 그 앞의 대기(합성 위상·겹④·회선)는 엔진의 몫이 아니다.
        started = self._monotonic()
        bars = list(history)
        vector = self._build_feature_vector(bar, bars)
        await self._publish(vector, started_at=started)

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

        # 세션 누적 통계 — 상수·항상NaN 피처를 운영 경로에서 잡는다(고도화 3, `_FeatureStat`).
        stats = self._feature_stats.setdefault(bar.horizon, {})
        for name, value in values.items():
            stats.setdefault(name, _FeatureStat()).observe(value)

        nan_ratio = sum(1 for v in values.values() if v is None) / len(values)
        # 워밍업 중(예: 30m은 최대 윈도우 60개를 채우는 데만 30시간 = 며칠이 걸림)엔 nan_ratio가
        # 높은 게 정상이라 매 봉마다 WARNING을 찍으면 agenda.py의 주간 경보 집계가 이 잡음에
        # 파묻힌다(2026-07-24, 실제 운영 로그 리뷰 중 발견) — len(history)가 _MAX_HISTORY에
        # 도달해 "워밍업이 끝났어야 할 시점"이 된 뒤에도 nan_ratio가 여전히 높을 때만 경고한다.
        # ## 억제가 아니라 분류다 (2026-08-14 F-9)
        #
        # 종전엔 `warmed_up`이 아니면 임계 초과를 **아예 안 찍었다**. 그 억제는 위 문단의
        # 이유로 옳았지만, `len(history) < _MAX_HISTORY`라는 한 조건이 **두 사건**을 함께
        # 덮고 있었다: 평범한 워밍업과, 월물 롤로 아카이브가 통째로 빈 상태.
        #
        # 2026-08-14(첫 월물 롤)에 전 Horizon이 0봉에서 출발해 1m NaN 84.7%로 개장했고
        # 30m은 종일 62% 아래로 안 내려갔는데, **로그에는 한 줄도 안 남았다.** 화면과
        # 자가점검이 정상을 말하는 동안 판단은 종일 불가였다.
        #
        # 그래서 억제를 분류로 바꾼다. 새 태그는 **INFO**이고 Horizon당 1회 + 재고지 간격을
        # 둔다 — 기존 WARNING에 합치면 2026-07-24가 없앤 잡음이 그대로 돌아오고, 태그를
        # 가르면 R6(태그 1개 = 심각도 1개)도 함께 지켜진다.
        warmed_up = len(history) >= _MAX_HISTORY
        if nan_ratio > _NAN_RATIO_HALT_THRESHOLD and not warmed_up:
            self._log_warmup_nan(bar, nan_ratio, len(history))
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

    def _log_warmup_nan(self, bar: BarClosed, nan_ratio: float, bars: int) -> None:
        """워밍업 중 NaN 임계 초과 — Horizon당 1회 + 재고지 간격 (2026-08-14 F-9).

        **매 봉 찍지 않는 것이 핵심이다.** 30m 기준 하루 15건 × 매일이면 2026-07-24가 없앤
        잡음이 그대로 돌아온다. 그렇다고 침묵하면 롤 당일처럼 "종일 판단 불가인데 로그가
        조용한" 날이 또 생긴다. 둘 사이가 이 함수다.

        간격은 봉 시각(`bar_open_kst`) 기준이다 — 벽시계로 재면 replay에서 전부 한 번에
        찍히거나 전부 눌린다.
        """
        last = self._warmup_nan_last.get(bar.horizon)
        if last is not None and bar.bar_open_kst - last < _WARMUP_NAN_RENOTIFY:
            return
        self._warmup_nan_last[bar.horizon] = bar.bar_open_kst
        mlog.log(
            "FeatureNanWarmupExceeded",
            f"워밍업 중 NaN 비율 {nan_ratio:.0%} — {bar.horizon.value} "
            f"{bars}/{_MAX_HISTORY}봉 (창이 차면 해소되는지 확인할 것)",
            symbol=self._symbol,
            horizon=bar.horizon.value,
            nan_ratio=nan_ratio,
            bars=bars,
            required=_MAX_HISTORY,
        )

    @staticmethod
    def _safe_call(fn, *args) -> float | None:
        """개별 Feature 계산 실패는 그 Feature만 None으로 마킹 — 다른 Feature·전체 발행까지
        죽이지 않는다(Ver 1.1 §2-2 "특정 Feature 계산 오류 → 해당 Feature만 NaN 마킹")."""
        try:
            return fn(*args)
        except Exception:  # noqa: BLE001
            return None

    async def _publish(self, vector: FeatureVector, *, started_at: float | None = None) -> None:
        """`started_at`은 `handle_bar()`가 봉을 받은 monotonic 시각 (2026-08-24 F-21).

        기본값이 None인 것은 이 메서드를 직접 부르는 테스트·도구 때문이다 — 그 경우
        `bar_to_publish_ms`는 **0이 아니라 None**이다(못 잰 것이지 즉시 나간 것이 아니다).
        """
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
        # **봉 확정 시각을 함께 싣는다** (2026-08-20 F-E).
        #
        # 종전엔 로그의 `ts`(발행 wall clock)만 남았고, 오프셋을 알려면 사람이 `ts`의
        # 초 단위를 Horizon 격자로 나눠 역산해야 했다. 그 프록시는 **되감기 모호성**이 있다 —
        # 봉 확정은 거래소 시각으로 판정하는데 `ts`는 로컬 시계라, 시계 스큐가 +0.156초인 날
        # 「경계보다 0.15초 이르게 발행」이 「59.85초 늦게 발행」과 초 단위에서 구분되지 않는다.
        # 2026-08-20 replay가 실제로 그 함정에 빠졌다가 `ClockSkewMeasured`와 대조해서야 갈랐다.
        #
        # 확정 시각을 그대로 실으면 그 모호성이 **구조적으로** 사라진다. 값은 이미 손에 있다
        # (`vector.valid_until` = `bar_open_kst + Horizon길이`) — 새 입력이 필요 없다.
        offset_ms, offset_axis, skew_ms = self._record_publish_offset(vector)
        self._note_delay_spike(vector, offset_ms)
        # **엔진이 자기 몫으로 쓴 시간** (2026-08-24 F-21) — 위 오프셋과 다른 것을 잰다.
        # monotonic 차라 시계 스큐·리플레이와 무관하고, 대기가 섞여 있지 않다.
        bar_to_publish_ms: float | None = None
        if started_at is not None:
            bar_to_publish_ms = round((self._monotonic() - started_at) * 1000.0, 1)
            self._bar_to_publish.append((self._now(), bar_to_publish_ms, vector.horizon))
        mlog.log(
            "FeaturePublish",
            "FeatureVector 발행",
            symbol=vector.symbol,
            horizon=vector.horizon.value,
            feature_set=vector.feature_set,
            nan_ratio=vector.nan_ratio,
            bar_confirm_kst=(
                None if vector.valid_until is None else vector.valid_until.isoformat()
            ),
            publish_offset_ms=offset_ms,
            # **어느 축으로 잰 값인지 로그가 스스로 말하게 한다** (2026-08-21 F-12).
            # 종전엔 같은 이름의 필드가 Horizon에 따라 다른 것을 재고 있었고, 그 사실이
            # 로그 어디에도 없어서 사람이 매번 코드를 읽어야 했다.
            publish_offset_axis=offset_axis,
            publish_offset_skew_ms=skew_ms,
            # 예산이 채점하는 값 (2026-08-24 F-21). 못 잰 경우 None이다 — 0이 아니다.
            bar_to_publish_ms=bar_to_publish_ms,
        )
        self._note_publish_sla(vector, bar_to_publish_ms)

    def _record_publish_offset(
        self, vector: FeatureVector
    ) -> tuple[float | None, str, float | None]:
        """봉 확정 → 발행까지의 지연(ms)과 **그것을 어느 축으로 쟀는가**.

        반환은 `(오프셋ms, 축, 적용한 스큐ms)`. `valid_until`이 없으면 오프셋은
        `None`(0이 아니다 — L18).

        ## 왜 1m에만 보정을 거는가 (2026-08-21 F-12)

        축이 Horizon마다 다르다.

        - **1m**: 봉 확정이 `bar_close: 1분봉 확정 timer`로 **거래소 시각 경계** 구동이다.
          발행 시각은 로컬 시계다. 그래서 이 뺄셈은 **두 축을 섞는다** — 스큐만큼
          계통오차가 붙고, 스큐가 장중에 미끄러지면 오프셋도 같이 미끄러진다.
          2026-08-21 실측: 1m 시간대 중앙값이 09시 −495ms → 15시 +353ms로 **848ms**
          이동했고, 음수(봉 닫히기 전 발행) 206건이 **전부 1m**이었다.
        - **3m 이상**: 1m 봉이 **도착한 시점**을 기점으로 합성·발행한다. 양쪽 다 로컬
          시계다 — 축이 하나다. 같은 날 이동폭이 3m +29ms · 5m −1ms · 10m +72ms로
          사실상 미동이 없었고, 음수는 0건이었다.

        **그래서 전 계열에 보정을 걸면 지금 평평한 다섯 개를 새로 휘게 만든다.** 부호가
        반대인 계통오차를 없는 곳에 주입하는 셈이다. 장전 F-2·장중 F-7이 정확히 그
        계획이었고, 장후 실측이 그 전에 잡았다.

        ## 보정의 부호

        스큐는 `거래소 시각 − 로컬 시계`다(`ops/clock_skew.py`). 봉 확정이 거래소 시각
        `T_e`면 그 순간의 로컬 시계는 `T_e − skew`이므로, 로컬 시계로 찍은 발행 시각 `P`
        기준 경과는 `P − (T_e − skew) = raw + skew`다. 실측 대조: 1m 하루 중앙값 −3.5ms에
        스큐 +798ms를 더하면 +795ms — 3m(586ms) · 5m(596ms)와 같은 대역에 들어온다.

        스큐를 못 재면(표본 부족) **보정하지 않고** 축만 정직하게 남긴다. `skew_ms`가
        `None`인 것이 "보정 안 됨"의 지문이다.
        """
        axis = (
            _PUBLISH_OFFSET_AXIS_EXCHANGE
            if vector.horizon is Horizon.M1
            else _PUBLISH_OFFSET_AXIS_LOCAL
        )
        if vector.valid_until is None:
            return None, axis, None
        moment = self._now()
        try:
            offset_ms = (moment - vector.valid_until).total_seconds() * 1000.0
        except TypeError:  # naive/aware 혼재 — 못 재는 것이지 0이 아니다
            return None, axis, None

        skew_ms: float | None = None
        if axis == _PUBLISH_OFFSET_AXIS_EXCHANGE and self._clock_skew_seconds is not None:
            try:
                skew_seconds = self._clock_skew_seconds()
            except Exception:  # noqa: BLE001 — 계기 하나가 발행을 막으면 본말전도다
                skew_seconds = None
            if skew_seconds is not None:
                skew_ms = round(skew_seconds * 1000.0, 1)
                offset_ms += skew_ms

        offset_ms = round(offset_ms, 1)
        self._publish_offsets.append((moment, offset_ms, vector.horizon, axis))
        return offset_ms, axis, skew_ms

    def _note_delay_spike(self, vector: FeatureVector, offset_ms: float | None) -> None:
        """1분봉 발행 지연의 5분 롤링 최댓값이 상한의 70%를 넘는 **순간**을 남긴다 (G-57).

        ## M1만 넣는다

        합성기가 기다리는 것은 그 버킷의 **마지막 1분봉**이고 상한이 재는 것도 그것이다.
        3m 이상의 오프셋에는 **기다린 시간이 섞여 있어서**(`_record_publish_offset` 주석)
        그것을 창에 넣으면 「상한에 가까워졌나」를 상한이 만든 값으로 되묻는 순환이 된다.

        ## 리플레이는 이 축을 안 탄다

        `_note_publish_sla`와 같은 규율이다 (2026-08-24 F-28). 재생은 저장된 봉을 순서대로
        흘리므로 발행 지연이 실시간의 그것이 아니다 — 그 값으로 경보를 내면 재생할 때마다
        가짜 스파이크가 뜬다.
        """
        if not self._live or vector.horizon is not Horizon.M1 or offset_ms is None:
            return
        spike = self._delay_spike.observe(self._now(), offset_ms / 1000.0)
        if spike is None:
            return
        # 2026-09-10 G-62 — 상한을 넘긴 경보에 「아직 유실 아님」을 쓰지 않는다. 그날 15:21의
        # 경보가 상한을 5.99배 넘기고도 그 문구를 달고 있었고, 사람이 그 줄만 보고는 정상
        # 스파이크 4건과 같은 것으로 읽었다. 태그는 하나 그대로고 무게만 `severity`로 가른다.
        if spike.severity == "overrun":
            tail = f"상한을 {-spike.headroom_seconds:.2f}초 넘겼다(유실 위험)"
        else:
            tail = f"{spike.headroom_seconds:.2f}초 남았다(아직 유실 아님)"
        mlog.log(
            "BarPublishDelaySpike",
            f"1분봉 발행 지연 5분 최댓값 {spike.window_max_seconds:.2f}초 — "
            f"합성 대기 상한 {spike.bound_seconds:.0f}초까지 {tail}",
            symbol=vector.symbol,
            severity=spike.severity,
            window_max_seconds=spike.window_max_seconds,
            threshold_seconds=spike.threshold_seconds,
            bound_seconds=spike.bound_seconds,
            headroom_seconds=spike.headroom_seconds,
            headroom_ratio=round(spike.headroom_ratio, 4),
            samples=spike.samples,
            worst_at=spike.worst_at.isoformat(),
        )

    def _note_publish_sla(self, vector: FeatureVector, elapsed_ms: float | None) -> None:
        """**발행 예산**을 넘긴 발행을 모아 두고, 순간이 바뀌면 한 줄로 남긴다.

        2026-08-21 F-15 ③ + F-8. 두 사건을 **가른다**:

        - `PublishGraceExceeded` — 한 Horizon이 늦었다. 그 Horizon의 문제다.
        - `PublishLoopStalled` — 같은 봉 확정 순간에 **여러 Horizon이 함께** 늦었다.
          개별 Horizon의 문제가 아니라 발행 루프 자체가 멈춘 것이다.

        종전엔 이 구분이 로그에 없어서 사람이 `FeaturePublish` 700여 줄을 초 단위로 묶어
        세어야 했다(2026-08-21 1-11이 실제로 그 작업이었다).

        ## 재는 값이 바뀌었다 (2026-08-24 F-21)

        종전 입력은 `publish_offset_ms`(봉 확정 → 발행)였고 임계는 합성 스케줄러 위상의
        4배(2,000ms)였다. 그 값에는 **기다린 시간**이 섞여 있어서, 3m~30m은 위상 0.5초가
        구조적으로 포함돼 500ms 아래로 내려갈 수가 없었다(2거래일 598건 중 500ms 미만 0건).
        이제 입력은 `bar_to_publish_ms`(봉 도착 → 발행, monotonic)이고 임계는 실측에서
        온 절대값 `_PUBLISH_SLA_MS`다.
        """
        if not self._live:
            # **리플레이는 경보 축을 아예 안 탄다** (2026-08-24 F-28). 값은
            # `_bar_to_publish`에 이미 들어갔으므로 사실이 사라지지는 않는다 —
            # 세션 요약은 그대로 나온다.
            return
        # **정상 발행도 군집을 닫는다** (2026-08-24 F-24). 아래 조기 반환들보다 먼저
        # 부른다 — 이 자리를 지나 내려가는 것은 「또 예산을 넘었다」뿐이고, 그것만
        # 계기로 삼는 것이 149분 지연의 원인이었다.
        self._flush_stale_publish_stall()
        if elapsed_ms is None or vector.valid_until is None:
            return
        if not (_PUBLISH_SLA_MS < elapsed_ms < _PUBLISH_OFFSET_LIVE_CEILING_MS):
            # 상한을 넘는 경과는 「느린 것」이 아니라 프로세스가 멈췄다 깨어난 것이다
            # (상수 주석). 값은 `_bar_to_publish`에 이미 들어갔다 — 경보 축에서만 뺀다.
            return

        confirm = vector.valid_until
        pending = self._pending_stall
        if pending is not None:
            moment, members, _opened = pending
            if abs((moment - confirm).total_seconds()) * 1000.0 <= _PUBLISH_STALL_CLUSTER_MS:
                members.append((vector.horizon, elapsed_ms))
                return
            self._flush_publish_stall()
        self._pending_stall = (confirm, [(vector.horizon, elapsed_ms)], time.monotonic())

    def _flush_stale_publish_stall(self) -> None:
        """군집이 `_PUBLISH_STALL_FLUSH_MS`보다 오래 열려 있으면 닫는다 (F-24)."""
        pending = self._pending_stall
        if pending is None:
            return
        _confirm, _members, opened = pending
        if (time.monotonic() - opened) * 1000.0 >= _PUBLISH_STALL_FLUSH_MS:
            self._flush_publish_stall()

    def _flush_publish_stall(self) -> None:
        """모아 둔 군집을 **한 줄로** 남긴다 — 1건이면 유예 초과, 2건 이상이면 루프 정체."""
        pending, self._pending_stall = self._pending_stall, None
        if pending is None:
            return
        confirm, members, _opened = pending
        worst = max(elapsed for _horizon, elapsed in members)
        # **경보가 얼마나 늦게 왔는지를 경보 자신이 말한다** (2026-08-24 F-24).
        # 이 값이 없으면 "08:56 정체"라고 적힌 줄이 11:25에 남았다는 사실을 로그만
        # 보고는 알 수 없다. 못 재면 None이다 — 0이 아니다(L18).
        detection_lag_ms: float | None
        try:
            detection_lag_ms = round((self._now() - confirm).total_seconds() * 1000.0, 1)
        except TypeError:  # naive/aware 혼재
            detection_lag_ms = None

        if len(members) == 1:
            horizon, elapsed_ms = members[0]
            mlog.log(
                "PublishGraceExceeded",
                f"발행 예산 초과 — {horizon.value} 봉 도착 후 {elapsed_ms:.0f}ms "
                f"(예산 {_PUBLISH_SLA_MS:.0f}ms)",
                symbol=self._symbol,
                horizon=horizon.value,
                bar_to_publish_ms=elapsed_ms,
                sla_ms=_PUBLISH_SLA_MS,
                bar_confirm_kst=confirm.isoformat(),
                detection_lag_ms=detection_lag_ms,
            )
            return

        # 굵기 순으로 적는다 — 문자열 정렬이면 "10m, 15m, 1m, 30m, 3m, 5m"가 되어
        # 사람이 읽을 때 순서가 뜻을 잃는다.
        ordered = sorted(
            {horizon for horizon, _elapsed in members}, key=lambda h: HORIZON_SECONDS[h]
        )
        horizons = [horizon.value for horizon in ordered]
        mlog.log(
            "PublishLoopStalled",
            f"발행 루프 정체 — {len(horizons)}개 Horizon이 함께 최대 {worst:.0f}ms 소요 "
            f"({', '.join(horizons)})",
            symbol=self._symbol,
            stall_ms=round(worst, 1),
            horizons=horizons,
            bar_confirm_kst=confirm.isoformat(),
            sla_ms=_PUBLISH_SLA_MS,
            detection_lag_ms=detection_lag_ms,
        )

    def log_publish_offsets(self) -> dict[str, float] | None:
        """세션 전체 발행 오프셋 분포를 **시간대 축과 함께** 한 줄로 남긴다 (2026-08-20 F-E).

        장 마감 절차에서 부른다 — `collector.log_delivery_latency()` 바로 다음 자리다.
        둘은 같은 예산의 양쪽 끝을 잰다: 저쪽은 **회선이 틱을 얼마나 늦게 주는가**,
        이쪽은 **그 틱을 받아 피처를 내보내기까지 얼마나 걸리는가**. 종전엔 후자를 재는 축이
        아예 없어서, 오프셋이 나빠진 날 원인이 회선인지 내부 적체인지 판단할 수 없었다.

        `by_hour`가 G-D의 1차 소비처다 — 하루 한 숫자로는 「종일 나쁨」과 「갈수록 나빠짐」이
        같은 값으로 접힌다. 못 잰 날은 `measured=False`로 남긴다(L18).
        """
        # 그날 마지막 군집을 여기서 내보낸다 — 안 하면 그 한 줄이 영영 안 남는다.
        self._flush_publish_stall()
        if not self._publish_offsets:
            mlog.log(
                "FeaturePublishOffset",
                "발행 오프셋 표본 없음 — 그 세션에 완성봉 발행이 없었다는 뜻",
                symbol=self._symbol,
                samples=0,
                measured=False,
            )
            return None
        values = sorted(offset for _moment, offset, _h, _axis in self._publish_offsets)
        stats = _percentiles(values)
        by_hour: dict[str, dict[str, float]] = {}
        buckets: dict[int, list[tuple[float, Horizon]]] = {}
        for moment, offset, horizon, _axis in self._publish_offsets:
            buckets.setdefault(moment.hour, []).append((offset, horizon))
        for hour, rows in sorted(buckets.items()):
            offsets = [offset for offset, _h in rows]
            hour_stats = _percentiles(sorted(offsets))
            # **꼬리를 시간대별로 남긴다** (2026-08-25 F-43).
            #
            # 2026-08-25에 이 축의 중앙값은 09시 245.3ms → 15시 259.4ms(1.06배)로 거의
            # 안 움직였는데, 같은 하루의 1,000ms 초과율은 1.7% → 25.7%(15.1배)였다.
            # 중앙값만 실어 보내면 하류 감시기가 볼 수 있는 것이 앞의 1.06배뿐이고,
            # 실제로 그날 `drift: false`가 나왔다. **꼬리는 상류가 세어야 한다** —
            # p50/p90만 넘기면 하류에서 초과 건수를 복원할 방법이 없다.
            over_sla = sum(1 for offset in offsets if offset > _PUBLISH_SLA_MS)
            over_grace = sum(1 for offset, horizon in rows if offset > _grace_ms(horizon))
            by_hour[f"{hour:02d}"] = {
                "p50": hour_stats["p50"],
                "p90": hour_stats["p90"],
                "p99": hour_stats["p99"],
                "samples": hour_stats["samples"],
                # 예산(1,000ms) 초과 — **건수와 비율을 함께** 싣는다. 비율만 두면
                # 표본 3건짜리 15시대가 33%로 튀는 것과 60건 중 20건이 같아 보인다.
                "over_1000": float(over_sla),
                "over_1000_ratio": round(over_sla / len(offsets), 4),
                # 유예(=손실 경계) 초과 — 이 값이 0이 아닌 날은 자료가 실제로 빠졌을 수
                # 있는 날이다. 2026-08-25는 15시대 2건이었고 손실은 0건이었다.
                "over_grace": float(over_grace),
            }
        mlog.log(
            "FeaturePublishOffset",
            f"완성봉 확정 → 발행 지연 — p50 {stats['p50']:.0f}ms · p90 {stats['p90']:.0f}ms · "
            f"p99 {stats['p99']:.0f}ms · 최대 {stats['max']:.0f}ms "
            f"(표본 {int(stats['samples'])}건)",
            symbol=self._symbol,
            measured=True,
            by_hour=by_hour,
            # Horizon × 시간대 (2026-08-21 F-12 검증 ㉡·㉢).
            #
            # 하루 한 표로는 "1m만 848ms 미끄러지고 나머지 다섯은 미동이 없다"를 볼 수
            # 없다 — 전 Horizon을 한 통에 담으면 1m의 409건이 나머지를 눌러 버린다.
            # 이 축이 F-12의 효과를 판정하는 재료다: 1m `by_hour` 중앙값의 하루 이동폭이
            # 100ms 이내인가(오늘 848ms가 기준선), 3m가 오늘(29ms)보다 나빠지지 않았는가.
            by_horizon=self._offsets_by_horizon(),
            # **예산이 채점하는 축** (2026-08-24 F-21). 위 오프셋과 나란히 실어야
            # 「늦었다」의 원인이 대기인지 계산인지 한 줄에서 갈린다 — 종전엔 두 값이
            # 한 숫자에 섞여 있어서 그 질문 자체가 성립하지 않았다.
            bar_to_publish=self._bar_to_publish_stats(),
            # **손실까지 남은 여유** (2026-08-25 F-43 · 1-9가 처음 물은 질문).
            #
            # 2026-08-25 장중 점검이 발행 지연을 P1으로 격상하면서 물은 것은 "몇 ms인가"가
            # 아니라 **"얼마나 남았나"** 였고, 그 답은 그날 사람이 손으로 뺄셈해서 나왔다
            # (5,000 − 3,119.7 = 1,880.3ms). 계기가 매일 답하게 한다 — 손으로 세는 값은
            # 세는 사람이 없는 날 사라진다.
            grace_headroom=self._grace_headroom(),
            **stats,
        )
        self._warn_if_grace_breached(by_hour)
        return stats

    #: 전일 무결성 리포트가 놓이는 자리 — `ops/integrity_report.DEFAULT_LOG_DIR`과 같은 값,
    #: 같은 규칙(상대경로, R4)이다. 그 모듈을 들여오지 않는 이유는 방향이다: 리포트가 이
    #: 모듈의 로그를 읽지, 이 모듈이 리포트 코드에 기대면 안 된다.
    _INTEGRITY_LOG_DIR = Path("logs")

    #: 전일 대비 몇 %를 넘으면 「급변」이라 부를 것인가 (2026-09-01 F-87).
    #:
    #: 0.5(=50%)는 2026-08-27~09-01 4거래일 실측에서 나온 값이다. 08-27→08-28은 0.5%,
    #: 08-28→08-31은 7.9%로 잔물결이고, 08-31→09-01은 **130%**로 홀로 튄다 — 그 사이
    #: 어디에 선을 그어도 같은 하루만 걸린다. 낮게 잡으면 매일 울고, 매일 울면 배경이 된다.
    _HEADROOM_SURGE_RATIO = 0.5

    @classmethod
    def _previous_worst_headroom(
        cls, today: date, log_dir: Path | None = None
    ) -> tuple[float, date] | None:
        """직전 거래일의 1일 최악 여유(ms)와 그 날짜 — 못 찾으면 None (2026-09-01 F-87).

        달력으로 "어제"를 계산하지 않는다 — 월요일의 어제는 일요일이고 그날엔 리포트가
        없다. **파일이 있는 가장 가까운 과거 날**을 직전 거래일로 본다. 휴장일·기동
        실패일이 자동으로 건너뛰어진다.
        """
        directory = cls._INTEGRITY_LOG_DIR if log_dir is None else log_dir
        stamp = today.strftime("%Y%m%d")
        try:
            candidates = sorted(directory.glob("daily_integrity_*.json"))
        except OSError:
            return None
        for path in reversed(candidates):
            day_text = path.stem.removeprefix("daily_integrity_")
            if not (len(day_text) == 8 and day_text.isdigit() and day_text < stamp):
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                worst = payload["publish_offset"]["grace_headroom"]["worst_headroom_ms"]
                previous = datetime.strptime(day_text, "%Y%m%d").date()  # noqa: DTZ007
            except (OSError, ValueError, TypeError, KeyError):
                continue  # 그날 리포트가 깨졌거나 옛 스키마다 — 더 과거로 간다
            if isinstance(worst, (int, float)):
                return float(worst), previous
        return None

    @classmethod
    def _headroom_delta(cls, worst_ms: float, today: date) -> tuple[str, dict[str, Any]]:
        """전일 대비 증감 한 조각 — (문구, 구조화 필드) (2026-09-01 F-87).

        전일 값이 없으면 **0이 아니라 「못 잼」**이다(L18) — `headroom_delta_ms: None`과
        「전일 비교 불가」 문구를 함께 낸다. 0으로 접으면 「전날과 똑같았다」가 되어
        도입 첫날마다 거짓말을 한다.
        """
        previous = cls._previous_worst_headroom(today)
        if previous is None:
            return "전일 비교 불가(직전 거래일 리포트 없음)", {
                "headroom_delta_ms": None,
                "previous_headroom_ms": None,
                "previous_day": None,
                "headroom_surge": None,
            }
        previous_ms, previous_day = previous
        delta = round(worst_ms - previous_ms, 1)
        # 「나빠졌나」는 **부호가 아니라 절대값**이 답한다 — 여유는 음수 쪽이 나쁘므로
        # 델타가 음수면 악화다. 급변 판정은 방향을 묻지 않고 폭만 본다.
        surge = abs(previous_ms) > 0 and abs(delta) > abs(previous_ms) * cls._HEADROOM_SURGE_RATIO
        text = f"전일({previous_day.isoformat()}) 대비 {delta:+.0f}ms"
        if surge:
            text += " ⚠ 급변"
        return text, {
            "headroom_delta_ms": delta,
            "previous_headroom_ms": previous_ms,
            "previous_day": previous_day.isoformat(),
            "headroom_surge": surge,
        }

    def _warn_if_grace_breached(self, by_hour: dict[str, dict[str, float]]) -> None:
        """[MW0601 2026-08-26 F-66] 유예 여유가 **음수면 운다.**

        2026-08-26이 이 계기의 첫날이었고, 첫 값이 곧바로 음수였다 — 1분봉 최악
        −3,596ms · 유예 초과 14건. 그런데 그날 경고는 **0건**이었다. `FeaturePublishOffset`
        은 INFO 한 줄이고, 그 줄 안의 `grace_headroom` 을 사람이 열어 봐야만 부호가 보인다.
        「자료가 빠질 수 있는 경계를 이미 넘었다」는 사실이 INFO 안에 접혀 있으면 안 된다
        (R10 · 금지계명 12 — 조용한 폴백 금지의 정신).

        ⚠ **이것은 게이트도 차단도 아니다.** 판정을 바꾸지 않고 사람을 부를 뿐이라
        R18(게이트 신설은 섀도 20거래일 후 승격) 대상이 아니다. 발행은 그대로 나간다.

        하루 한 번(마감 절차)만 부르므로 **세션당 최대 1줄**이다.

        **리플레이는 이 축을 타지 않는다** (2026-08-24 F-28과 같은 규율) — 옛 하루를 다시
        재생할 때마다 이미 아는 사실로 경보가 뜨면 경보의 값이 떨어진다. 요약
        `FeaturePublishOffset`은 기록이므로 리플레이에서도 그대로 나간다.
        """
        if not self._live:
            return
        headroom = self._grace_headroom()
        if not headroom:
            return
        worst_ms = headroom.get("worst_headroom_ms")
        if worst_ms is None or worst_ms >= 0:
            return
        # 시간대별 유예 초과 분포 — 「몇 시에 몰렸나」가 원인 추적의 첫 질문이다.
        over_by_hour = {
            hour: stat["over_grace"]
            for hour, stat in sorted(by_hour.items())
            if stat.get("over_grace")
        }
        # **몇 종류로 번졌나** (2026-08-31 F-80). 최악 한 건만 적으면 1m 하나가 넘긴 날과
        # 1m·3m 둘이 넘긴 날이 같은 문장으로 나온다 — 번지는 것을 늦게 안다.
        breached = headroom.get("breached_horizons") or [headroom["worst_horizon"]]
        by_h = headroom["by_horizon"]
        spread = " · ".join(
            f"{name} {by_h[name]['headroom_ms']:.0f}ms" for name in breached if name in by_h
        )
        # **어제보다 나빠졌나** (2026-09-01 F-87 · 이상점 1-7 · F-80 잔여②).
        #
        # 2026-09-01의 −6,730.9ms는 직전 3거래일 평균(−2,778.8ms)의 2.4배였다. 그런데 그
        # 사실을 아는 유일한 방법이 **사람이 과거 로그를 손으로 grep하는 것**이었다 —
        # 한 줄짜리 경보는 그날의 값만 말하고, 추세는 세는 사람이 없는 날 사라진다.
        delta_text, delta_fields = self._headroom_delta(worst_ms, now_kst().date())
        mlog.log(
            "PublishGraceBreached",
            f"유예까지 남은 여유가 음수 — 음수 Horizon {len(breached)}개({spread}) · "
            f"최악 {headroom['worst_horizon']} {worst_ms:.0f}ms({delta_text}) · "
            f"유예 초과 {sum(over_by_hour.values()):.0f}건. "
            f"완성봉 경계를 넘겨 발행한 회차가 있다(자료 유실 경계)",
            symbol=self._symbol,
            worst_horizon=headroom["worst_horizon"],
            headroom_ms=worst_ms,
            breached_horizons=breached,
            by_horizon=by_h,
            over_grace_by_hour=over_by_hour,
            **delta_fields,
        )

    def _grace_headroom(self) -> dict[str, Any] | None:
        """Horizon별 「유예까지 남은 여유」 + 최악 한 건 (2026-08-25 F-43).

        여유는 **Horizon마다 경계가 다르므로** Horizon별로 낸다 — 1분봉 2,000ms와 상위
        5,000ms를 한 통에 담으면 "가장 아슬아슬한 계열이 어디인가"가 접힌다. 그 위에
        `worst`로 하루 한 줄 요약을 얹는다(사람이 하나만 본다면 그것이다).

        표본이 없으면 `None`이다 — **0이 아니라 못 잼**이다(L18).
        """
        if not self._publish_offsets:
            return None
        by_horizon: dict[str, dict[str, float]] = {}
        worst: tuple[float, str] | None = None
        grouped: dict[Horizon, list[float]] = {}
        for _moment, offset, horizon, _axis in self._publish_offsets:
            grouped.setdefault(horizon, []).append(offset)
        for horizon, offsets in sorted(grouped.items(), key=lambda kv: HORIZON_SECONDS[kv[0]]):
            grace = _grace_ms(horizon)
            headroom = round(grace - max(offsets), 1)
            by_horizon[horizon.value] = {
                "grace_ms": grace,
                "max_offset_ms": round(max(offsets), 1),
                "headroom_ms": headroom,
                # 여유를 **비율로도** 낸다 — 2,000ms 경계의 500ms와 5,000ms 경계의
                # 500ms는 같은 숫자지만 같은 위험이 아니다.
                "headroom_ratio": round(headroom / grace, 4) if grace > 0 else None,
            }
            if worst is None or headroom < worst[0]:
                worst = (headroom, horizon.value)
        return {
            "by_horizon": by_horizon,
            "worst_headroom_ms": worst[0] if worst else None,
            "worst_horizon": worst[1] if worst else None,
            # **번진 범위** (2026-08-31 F-80 · 이상점 1-10).
            #
            # `worst_*` 두 줄은 **가장 나쁜 하나**만 답한다. 2026-08-31에 처음으로 1m 밖
            # (3m −160ms)이 음수로 넘어갔는데, 최악이 여전히 1m(−2,926ms)이라 요약 줄은
            # 전날과 **같은 모양**이었다 — 「한 계열의 사건」이 「두 계열의 사건」이 된 것을
            # 요약이 말하지 못했다. 몇 종류로 번졌는가는 **얼마나 나쁜가와 다른 질문**이다.
            #
            # 위 `by_horizon`에 값이 이미 다 들어 있으므로 집계는 늘지 않는다 — 목록 하나다.
            "breached_horizons": [
                name for name, stat in by_horizon.items() if stat["headroom_ms"] < 0
            ],
        }

    def _bar_to_publish_stats(self) -> dict[str, Any] | None:
        """봉 도착 → 발행 경과의 세션 분포 + Horizon별 (2026-08-24 F-21).

        표본이 없으면 None이다 — **0건이 아니라 못 잼**이다(L18). `_publish`를 직접
        부르는 경로(테스트·도구)는 기점을 안 넘기므로 여기 안 쌓인다.
        """
        if not self._bar_to_publish:
            return None
        overall = _percentiles(sorted(value for _m, value, _h in self._bar_to_publish))
        grouped: dict[str, list[float]] = {}
        for _moment, value, horizon in self._bar_to_publish:
            grouped.setdefault(horizon.value, []).append(value)
        by_horizon = {
            name: {
                **_percentiles(sorted(values)),
                # 예산을 넘긴 건수 — 비율이 아니라 건수다. 하루 몇 건인지가 판정 재료다.
                "over_sla": float(sum(1 for v in values if v > _PUBLISH_SLA_MS)),
            }
            for name, values in sorted(grouped.items())
        }
        return {
            "sla_ms": _PUBLISH_SLA_MS,
            **overall,
            "over_sla": float(sum(1 for _m, v, _h in self._bar_to_publish if v > _PUBLISH_SLA_MS)),
            "by_horizon": by_horizon,
        }

    def _offsets_by_horizon(self) -> dict[str, dict[str, object]]:
        """Horizon별 분포 + 시간대별 중앙값 + **축**과 하루 이동폭 (2026-08-21 F-12)."""
        grouped: dict[str, list[tuple[datetime, float]]] = {}
        axes: dict[str, str] = {}
        for moment, offset, horizon, axis in self._publish_offsets:
            grouped.setdefault(horizon.value, []).append((moment, offset))
            axes[horizon.value] = axis

        out: dict[str, dict[str, object]] = {}
        for horizon_value, rows in grouped.items():
            stats = _percentiles(sorted(offset for _m, offset in rows))
            hourly: dict[str, float] = {}
            hour_buckets: dict[int, list[float]] = {}
            for moment, offset in rows:
                hour_buckets.setdefault(moment.hour, []).append(offset)
            for hour, offsets in sorted(hour_buckets.items()):
                hourly[f"{hour:02d}"] = _percentiles(sorted(offsets))["p50"]
            medians = list(hourly.values())
            out[horizon_value] = {
                "axis": axes[horizon_value],
                "p50": stats["p50"],
                "p90": stats["p90"],
                "samples": stats["samples"],
                "negative": float(sum(1 for _m, offset in rows if offset < 0)),
                "by_hour_p50": hourly,
                # 하루 이동폭 — 표본이 한 시간대뿐이면 못 잰다(0이 아니다 — L18).
                "day_drift_ms": (
                    round(max(medians) - min(medians), 1) if len(medians) >= 2 else None
                ),
            }
        return out

    async def run_forever(self) -> None:
        patterns = [f"{TOPIC_BAR}.{h.value}.{self._symbol}" for h in self._horizons]
        await self._bus.subscribe(patterns, self.handle_bar)


def _percentiles(sorted_values: list[float]) -> dict[str, float]:
    """정렬된 표본의 p50/p90/p99/max/samples (2026-08-20 F-E).

    `statistics.quantiles`를 안 쓴다 — 표본이 1건인 시간대가 실제로 있고(15시대는 마감까지
    한두 봉뿐이다) 그쪽은 예외를 던진다. 여기서는 **표본이 적어도 값을 낸다**: 적은 표본으로
    잰 값이라는 사실은 `samples`가 말한다(못 잰 것과 적게 잰 것을 안 합친다).
    """

    def _at(fraction: float) -> float:
        if not sorted_values:
            return 0.0
        index = min(len(sorted_values) - 1, max(0, int(round(fraction * len(sorted_values))) - 1))
        return sorted_values[index]

    return {
        "p50": round(_at(0.5), 1),
        "p90": round(_at(0.9), 1),
        "p99": round(_at(0.99), 1),
        "max": round(sorted_values[-1], 1) if sorted_values else 0.0,
        "samples": float(len(sorted_values)),
    }
