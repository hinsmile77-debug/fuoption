"""Vol Engine 파생 지표 — IV Rank/Percentile·Skew·Term Structure·실현변동성·IV−RV 스프레드
(Ver 1.3 §3.1, Ver 2.0 §9 W27~29).

전부 순수 함수(+ 작은 순수 상태 클래스 `IVHistory`) — `surface.py`가 이미 만든
`SmileFit`/`IVSurface`·`find_strike_for_delta()`의 산출물(IV 숫자)을 입력으로 받을 뿐,
스마일 자체는 다시 건드리지 않는다(관심사 분리: surface.py=피팅, vol_metrics.py=피팅
결과로부터의 산술)."""

from __future__ import annotations

import math
from collections import deque
from typing import Sequence

DEFAULT_IV_HISTORY_WINDOW = 252  # Ver 1.3 §3.1 "최근 252일 내 위치"


class IVHistory:
    """ATM IV 롤링 이력 — IV Rank/Percentile 계산 재료. DB 의존 없이 프로세스 메모리에
    보관하는 순수 상태(`data/normalizer.py`의 `MinuteBarAggregator`와 같은 deque 버퍼링
    스타일). 영속화·재시작 복원은 호출측 책임(알려진 갭 — 재시작 시 이력이 비어 초기 구간은
    `rank()`가 None을 반환한다)."""

    def __init__(self, maxlen: int = DEFAULT_IV_HISTORY_WINDOW) -> None:
        self._values: deque[float] = deque(maxlen=maxlen)

    def add(self, iv: float) -> None:
        self._values.append(iv)

    def __len__(self) -> int:
        return len(self._values)

    def rank(self, current_iv: float) -> float | None:
        """0~100 스케일 백분위(현재 IV보다 낮거나 같은 이력의 비율). 이력이 2개 미만이면
        판정 불가(None) — 매트릭스(matrix.py)가 이를 "IV 상태 미판정"으로 취급해야 한다."""
        if len(self._values) < 2:
            return None
        below_or_equal = sum(1 for v in self._values if v <= current_iv)
        return below_or_equal / len(self._values) * 100.0


def skew(put_iv_25d: float, call_iv_25d: float) -> float:
    """리스크 리버설 — 25Δ 풋 IV − 25Δ 콜 IV. 양수가 클수록 하방 공포(풋 프리미엄 우위)가
    크다는 뜻(Ver 1.3 §3.1)."""
    return put_iv_25d - call_iv_25d


def term_structure(near_month_atm_iv: float, far_month_atm_iv: float) -> float:
    """근월 ATM IV − 차월 ATM IV. 양수(근월 > 차월, backwardation)면 근월에 프리미엄이
    몰려있다는 뜻 — 이벤트 프리미엄 감지에 쓰인다(Ver 1.3 §3.1)."""
    return near_month_atm_iv - far_month_atm_iv


def realized_vol(closes: Sequence[float], *, annualization_factor: float = 252.0) -> float | None:
    """종가 close-to-close 로그수익률의 표본표준편차를 연율화. `annualization_factor`는 종가
    간격 기준(일봉이면 252, 5분봉이면 252×78 등 — 호출측이 실제 샘플링 간격에 맞춰 지정).
    실패 조건: 로그수익률이 2개 미만(종가 3개 미만)이면 표본표준편차가 정의되지 않아 None."""
    if len(closes) < 3:
        return None
    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    n = len(log_returns)
    mean = sum(log_returns) / n
    variance = sum((r - mean) ** 2 for r in log_returns) / (n - 1)
    return math.sqrt(variance) * math.sqrt(annualization_factor)


def iv_rv_spread(iv: float, rv: float) -> float:
    """IV − 실현변동성(예측). 양수면 변동성이 고평가 — 매도 전략의 원천 수익(Ver 1.3 §3.1)."""
    return iv - rv
