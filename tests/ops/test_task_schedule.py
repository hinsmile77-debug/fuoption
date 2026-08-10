"""등록 정본과 기동 창의 단일 소스 (2026-08-10 P0).

회귀 대상은 그날 아침 그 자체다: 트리거 08:20 / 기동 창 08:30 → 정시에 떠서 self-check까지
통과한 뒤 "기동 창 이전"으로 즉시 종료, 종료 코드 0이라 스케줄러에는 성공. 이 파일의 테스트는
그 조합이 **구조적으로 불가능해졌는지**를 본다.
"""

from __future__ import annotations

import json
from datetime import time

import pytest

from messiah.ops import task_schedule


def _write(tmp_path, tasks, margin=5):
    path = tmp_path / "scheduled_tasks.json"
    path.write_text(
        json.dumps({"launch_window_margin_minutes": margin, "tasks": tasks}), encoding="utf-8"
    )
    return path


def _task(name, weekly, collection=True):
    return {
        "name": name,
        "bat": f"scripts\\{name}.bat",
        "weekly": weekly,
        "at_boot": collection,
        "restart": collection,
        "collection": collection,
    }


# ------------------------------------------------------------------ 정본 읽기


def test_loads_tasks_and_margin(tmp_path):
    path = _write(tmp_path, [_task("Messiah", "08:20")], margin=7)

    tasks, margin = task_schedule.load_schedule(path)

    assert margin == 7
    assert tasks[0].name == "Messiah"
    assert tasks[0].weekly == time(8, 20)
    assert tasks[0].collection


def test_non_collection_tasks_are_excluded_from_the_window(tmp_path):
    """장후 절차(15:45)는 기동 창과 무관하다 — 그것까지 세면 창이 오후까지 끌려간다."""
    path = _write(
        tmp_path,
        [_task("Messiah", "08:20"), _task("Messiah-Postmarket", "15:45", collection=False)],
    )

    assert task_schedule.earliest_collection_trigger(path) == time(8, 20)


# ------------------------------------------------------------------ 기동 창 파생


def test_window_starts_before_the_earliest_trigger(tmp_path):
    """2026-08-10의 사고가 불가능해졌는지 — 창은 언제나 트리거보다 이르다."""
    path = _write(tmp_path, [_task("Messiah", "08:20"), _task("Messiah-G2", "08:25")])

    assert task_schedule.launch_window_start(path) == time(8, 15)


def test_moving_the_trigger_moves_the_window(tmp_path):
    """정본만 고치면 창이 따라온다 — 두 곳을 맞춰 고칠 필요가 없다는 것이 이 모듈의 요지다."""
    path = _write(tmp_path, [_task("Messiah", "07:10")])

    assert task_schedule.launch_window_start(path) == time(7, 5)


def test_the_earliest_of_several_collection_tasks_wins(tmp_path):
    """L1이 G2보다 늦게 등록된 날에도 창은 더 이른 쪽을 기준으로 잡혀야 한다."""
    path = _write(tmp_path, [_task("Messiah", "08:25"), _task("Messiah-G2", "08:20")])

    assert task_schedule.launch_window_start(path) == time(8, 15)


def test_margin_across_midnight_opens_the_whole_day(tmp_path):
    """00:02 트리거에 5분을 빼면 전날로 넘어간다 — 음수 시각을 만들지 않는다."""
    path = _write(tmp_path, [_task("Messiah", "00:02")])

    assert task_schedule.launch_window_start(path) == time(0, 0)


# ------------------------------------------------------------------ 못 읽을 때


def test_missing_file_falls_back_to_launching_not_blocking(tmp_path):
    """가드가 오판해서 수집을 막는 것이 오판해서 한 번 더 뜨는 것보다 나쁘다."""
    assert (
        task_schedule.launch_window_start(tmp_path / "없다.json")
        == task_schedule.FALLBACK_LAUNCH_WINDOW_START
    )


def test_corrupt_file_falls_back_too(tmp_path):
    path = tmp_path / "scheduled_tasks.json"
    path.write_text("{ 이건 JSON이 아니다", encoding="utf-8")

    assert task_schedule.launch_window_start(path) == task_schedule.FALLBACK_LAUNCH_WINDOW_START


def test_fallback_still_blocks_an_overnight_reboot():
    """폴백이 너무 이르면 at-startup 트리거의 원래 취지(새벽 부팅은 안 뜬다)가 깨진다."""
    assert task_schedule.FALLBACK_LAUNCH_WINDOW_START > time(4, 0)


def test_load_raises_rather_than_guessing(tmp_path):
    """`load_schedule`은 삼키지 않는다 — 호출측마다 폴백이 달라야 하기 때문이다(L18)."""
    with pytest.raises(task_schedule.ScheduleUnreadable):
        task_schedule.load_schedule(tmp_path / "없다.json")


def test_empty_task_list_is_unreadable_not_empty(tmp_path):
    path = _write(tmp_path, [])

    with pytest.raises(task_schedule.ScheduleUnreadable):
        task_schedule.load_schedule(path)


# ------------------------------------------------------------------ 실제 정본


def test_the_real_schedule_file_parses():
    """저장소에 실제로 들어 있는 정본이 읽히는가 — 이게 깨지면 전부 폴백으로 돈다."""
    tasks, margin = task_schedule.load_schedule()

    assert margin > 0
    assert {task.name for task in tasks} >= {"Messiah", "Messiah-G2"}
    assert all(
        task.at_boot for task in tasks if task.collection
    ), "수집 작업에 부팅 트리거가 없으면 장중 재부팅에 관측이 죽는다(2026-08-06에 21분)"
