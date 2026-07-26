"""Purged K-Fold + Walk-Forward CV 프레임 — Ver 1.0.1 §1.2, Ver 1.2 §8.2, Ver 2.0 §9 W12~13.

두 계층으로 쓰인다(Ver 1.6 §7.1 Trainer 파이프라인):
- `WalkForwardSplitter`: 전체 학습 이력을 "학습 N일 / 검증 M일" 창으로 굴려가며 바깥쪽
  검증 스킴을 만든다(Ver 1.2 §8.2 "6개월 학습·1개월 검증, 1개월씩 전진"). 창별 성과를
  연결해 Deflated Sharpe를 내는 건 Validator(W14~16, 이번 스코프 밖)의 몫이다.
- `PurgedKFold`: 각 학습 창 "안에서" Optuna 하이퍼파라미터 탐색에 쓰는 표준 K-Fold(Ver 1.6
  §2.2 "Purged 5-Fold"). Lopez de Prado(2018) Ch.7 알고리즘을 그대로 따른다.

둘 다 같은 원칙을 공유한다: **레이블은 시점 하나가 아니라 [t_start, t_end] 구간이다**
(Triple Barrier가 시간 배리어까지 미래를 들여다보므로 진입 시점만으로 판단하면 검증
구간으로 정보가 샌다). 검증 구간과 겹치는 학습 샘플은 제거(**Purge**)하고, 겹치지 않아도
직렬상관 때문에 검증 경계에 인접한 구간은 추가로 제외한다(**Embargo**).

이 모듈은 이벤트를 `(t_start, t_end)` 튜플 시퀀스로만 다룬다 — `labeling.py`의
`TripleBarrierLabel`에 종속되지 않는다: `[(l.t_start, l.t_end) for l in labels]`로 바로
연결해 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterator, Sequence

from messiah.core.timeutil import KST, to_kst

EventTimes = Sequence[tuple[datetime, datetime]]


class PurgedKFold:
    """de Prado(2018) Ch.7 — 연속 시간 폴드를 순서대로 테스트 폴드로 돌리며, 겹치는 학습
    샘플을 제거(purge)하고 경계 인접 샘플을 추가로 제외(embargo)한다."""

    def __init__(self, n_splits: int, embargo_bars: int = 0) -> None:
        if n_splits < 2:
            raise ValueError("n_splits는 2 이상이어야 한다")
        if embargo_bars < 0:
            raise ValueError("embargo_bars는 음수일 수 없다")
        self._n_splits = n_splits
        self._embargo = embargo_bars

    def split(self, event_times: EventTimes) -> Iterator[tuple[list[int], list[int]]]:
        """
        입력: (t_start, t_end) 시퀀스 — 인덱스 순서가 시간 순서라고 가정한다(Triple Barrier
             레이블 생성 순서와 동일하게 넘기면 된다).
        산출: (train_indices, test_indices)를 n_splits회. test는 연속 구간 하나, train은
             그 구간과 [t_start,t_end]가 겹치는 샘플이 제거되고, test 폴드 양옆
             embargo_bars개(인덱스 기준)도 추가로 제외된 나머지 전부.
        """
        n = len(event_times)
        starts = [t0 for t0, _ in event_times]
        ends = [t1 for _, t1 in event_times]

        for i, j in _fold_bounds(n, self._n_splits):
            test_indices = list(range(i, j))
            test_start, test_end = starts[i], max(ends[i:j])

            train_indices = [
                k
                for k in range(n)
                if not (i <= k < j)
                and not (ends[k] >= test_start and starts[k] <= test_end)  # purge: 구간 겹침
                and not (i - self._embargo <= k < i)  # embargo: 테스트 직전
                and not (j <= k < j + self._embargo)  # embargo: 테스트 직후
            ]
            yield train_indices, test_indices


def _fold_bounds(n: int, n_splits: int) -> list[tuple[int, int]]:
    """n개 인덱스를 n_splits개의 연속 구간으로 최대한 균등 분할 — 나머지는 앞쪽 폴드부터
    하나씩 더 받는다(numpy.array_split과 동일 규칙, 결과 검증 가능하게 순수 함수로 분리)."""
    base, remainder = divmod(n, n_splits)
    bounds: list[tuple[int, int]] = []
    start = 0
    for i in range(n_splits):
        size = base + (1 if i < remainder else 0)
        bounds.append((start, start + size))
        start += size
    return bounds


@dataclass(frozen=True)
class WalkForwardWindow:
    train_indices: list[int]
    test_indices: list[int]
    train_start: date
    train_end: date  # 배타적 상한
    test_start: date  # 배타적 상한이자 train_end와 동일(embargo 적용 전 경계)
    test_end: date  # 배타적 상한


class WalkForwardSplitter:
    """Ver 1.2 §8.2 — "학습 N일 / 검증 M일" 창을 step_days씩 전진(기본값=test_days, 즉
    검증 구간이 서로 겹치지 않게 이어짐). 프로덕션 기본값은 train_days=180(~6개월)·
    test_days=30(~1개월)·embargo_days=1(Ver 1.2 §8.2 원문 그대로) — 여기선 강제하지 않고
    호출자가 명시하게 한다. **달력일 기준**이다 — KRX 휴장일 인식은 아직 없다(Event
    Calendar 미구현, capability_matrix.md 기존 갭과 동일 한계이며, 휴장일이 껴도 그날은
    이벤트 자체가 없을 뿐 창 경계 계산은 안전하다)."""

    def __init__(
        self,
        train_days: int,
        test_days: int,
        embargo_days: int = 1,
        step_days: int | None = None,
    ) -> None:
        if train_days <= 0 or test_days <= 0:
            raise ValueError("train_days/test_days는 1 이상이어야 한다")
        if embargo_days < 0:
            raise ValueError("embargo_days는 음수일 수 없다")
        self._train_days = train_days
        self._test_days = test_days
        self._embargo_days = embargo_days
        self._step_days = step_days if step_days is not None else test_days

    def split(self, event_times: EventTimes) -> Iterator[WalkForwardWindow]:
        """
        입력: (t_start, t_end) 시퀀스 — 시간 순 정렬 불필요(내부에서 날짜만 뽑아 필터링).
        산출: 학습 구간이 전체 이력의 첫 이벤트 날짜에서 시작해 step_days씩 전진하며, 검증
             구간 시작이 마지막 이벤트 날짜를 넘으면 멈춘다. train/test가 비는 창도 그대로
             내보낸다(호출자가 판단 — 이 함수는 침묵하지 않는다).
        """
        if not event_times:
            return

        start_dates = [to_kst(t0).date() for t0, _ in event_times]
        end_times_kst = [to_kst(t1) for _, t1 in event_times]
        last_date = max(start_dates)

        window_start = min(start_dates)
        while True:
            train_start = window_start
            train_end = train_start + timedelta(days=self._train_days)
            test_start = train_end
            test_end = test_start + timedelta(days=self._test_days)
            if test_start > last_date:
                return

            embargo_cutoff = test_start - timedelta(days=self._embargo_days)
            test_start_midnight = _kst_midnight(test_start)

            train_indices = [
                k
                for k in range(len(event_times))
                if train_start <= start_dates[k] < embargo_cutoff
                and end_times_kst[k] < test_start_midnight  # purge: 배리어가 검증 구간을 침범 안 함
            ]
            test_indices = [
                k for k in range(len(event_times)) if test_start <= start_dates[k] < test_end
            ]

            yield WalkForwardWindow(
                train_indices=train_indices,
                test_indices=test_indices,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
            window_start += timedelta(days=self._step_days)


def _kst_midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=KST)
