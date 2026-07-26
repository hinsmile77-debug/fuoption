from datetime import datetime, timedelta

import pytest
from messiah.core.timeutil import KST
from messiah.models.cv import PurgedKFold, WalkForwardSplitter

_START = datetime(2026, 7, 27, 9, 0, tzinfo=KST)  # 월요일


def _pt(idx: int) -> datetime:
    """idx분 뒤의 순간 이벤트 시각."""
    return _START + timedelta(minutes=idx)


def _day(offset: int) -> datetime:
    """offset일 뒤 같은 시각(09:00 KST) — 달력일 기준 WalkForward 테스트용."""
    return _START + timedelta(days=offset)


# ---------------------------------------------------------------- PurgedKFold


def test_rejects_invalid_n_splits():
    with pytest.raises(ValueError):
        PurgedKFold(n_splits=1)


def test_rejects_negative_embargo():
    with pytest.raises(ValueError):
        PurgedKFold(n_splits=3, embargo_bars=-1)


def test_test_folds_partition_all_indices_exactly_once():
    events = [(_pt(i), _pt(i)) for i in range(10)]  # 전부 순간 이벤트, 안 겹침
    folds = list(PurgedKFold(n_splits=5).split(events))

    all_test = sorted(idx for _, test in folds for idx in test)
    assert all_test == list(range(10))
    for train, test in folds:
        assert set(train).isdisjoint(test)


def test_fold_sizes_are_near_equal_when_not_evenly_divisible():
    events = [(_pt(i), _pt(i)) for i in range(11)]  # 11 / 5 folds → 첫 폴드들이 1개씩 더
    folds = list(PurgedKFold(n_splits=5).split(events))
    sizes = [len(test) for _, test in folds]
    assert sizes == [3, 2, 2, 2, 2]


def test_purge_removes_train_sample_whose_interval_overlaps_test():
    # 10 이벤트, 전부 순간 이벤트(t_start=t_end)이되 idx=3만 idx=6까지 뻗은 구간 이벤트.
    events = [(_pt(i), _pt(i)) for i in range(10)]
    events[3] = (_pt(3), _pt(6))  # test 폴드(idx 4,5)의 시간 범위를 침범

    folds = list(PurgedKFold(n_splits=5).split(events))
    train_45, test_45 = folds[2]  # 세 번째 폴드 = idx 4,5 (fold_bounds: [0,2)[2,4)[4,6)[6,8)[8,10))

    assert test_45 == [4, 5]
    assert 3 not in train_45  # 구간이 test 범위(idx4~5의 시각)를 침범해 퍼지됨
    assert 0 in train_45  # 안 겹치는 이벤트는 그대로 학습에 남음


def test_embargo_removes_indices_adjacent_to_test_fold_even_without_overlap():
    events = [(_pt(i), _pt(i)) for i in range(10)]  # 전부 순간 이벤트 — 겹침 없음
    folds = list(PurgedKFold(n_splits=5, embargo_bars=1).split(events))
    train_45, test_45 = folds[2]  # test = idx 4,5

    assert test_45 == [4, 5]
    assert 3 not in train_45  # embargo: 테스트 직전 1개
    assert 6 not in train_45  # embargo: 테스트 직후 1개
    assert 2 in train_45  # embargo 범위 밖은 그대로 학습에 남음
    assert 7 in train_45


# ---------------------------------------------------------------- WalkForwardSplitter


def test_empty_events_yields_no_windows():
    assert list(WalkForwardSplitter(train_days=10, test_days=5).split([])) == []


def test_generates_expected_number_of_rolling_windows():
    # 30일치 순간 이벤트(하루 1건), train=10일/test=5일/embargo=1일/step=기본(=test_days=5)
    events = [(_day(i), _day(i)) for i in range(30)]
    windows = list(WalkForwardSplitter(train_days=10, test_days=5, embargo_days=1).split(events))

    # window_start 0,5,10,15까지는 test_start(=window_start+10)가 마지막 이벤트 날짜(29) 이하,
    # window_start=20이면 test_start=30 > 29라 멈춘다 → 총 4개.
    assert len(windows) == 4
    assert [w.test_start.day for w in windows] == [
        (_day(10)).day,
        (_day(15)).day,
        (_day(20)).day,
        (_day(25)).day,
    ]


def test_first_window_train_and_test_membership_with_embargo():
    events = [(_day(i), _day(i)) for i in range(30)]
    windows = list(WalkForwardSplitter(train_days=10, test_days=5, embargo_days=1).split(events))
    first = windows[0]

    # test_start=day10, test_end=day15 → idx 10..14 (5개)
    assert first.test_indices == list(range(10, 15))
    # embargo_cutoff = test_start(day10) - embargo(1일) = day9 → train은 idx 0..8 (day9 자체는 제외)
    assert first.train_indices == list(range(0, 9))


def test_purge_excludes_train_sample_whose_barrier_extends_into_test_period():
    events = [(_day(i), _day(i)) for i in range(30)]
    # idx=5(원래 day5 순간 이벤트)의 배리어 판정이 day10 자정을 넘겨서야 끝난다고 가정
    # (예: 장시간 미체결 후 시간배리어) — embargo_cutoff(day9) 이전에 시작했지만 purge 대상.
    events[5] = (_day(5), _day(10) + timedelta(hours=1))

    windows = list(WalkForwardSplitter(train_days=10, test_days=5, embargo_days=1).split(events))
    first = windows[0]

    assert 5 not in first.train_indices


def test_step_days_defaults_to_test_days_for_non_overlapping_test_windows():
    events = [(_day(i), _day(i)) for i in range(30)]
    windows = list(WalkForwardSplitter(train_days=10, test_days=5).split(events))
    test_starts = [w.test_start for w in windows]
    gaps = [(b - a).days for a, b in zip(test_starts, test_starts[1:])]
    assert all(gap == 5 for gap in gaps)


def test_custom_step_days_overrides_default():
    events = [(_day(i), _day(i)) for i in range(60)]
    windows = list(WalkForwardSplitter(train_days=10, test_days=5, step_days=20).split(events))
    starts = [w.train_start for w in windows]
    gaps = [(b - a).days for a, b in zip(starts, starts[1:])]
    assert all(gap == 20 for gap in gaps)


def test_rejects_invalid_window_sizes():
    with pytest.raises(ValueError):
        WalkForwardSplitter(train_days=0, test_days=5)
    with pytest.raises(ValueError):
        WalkForwardSplitter(train_days=10, test_days=0)
    with pytest.raises(ValueError):
        WalkForwardSplitter(train_days=10, test_days=5, embargo_days=-1)
