"""Task Scheduler 등록 시각과 진입점 기동 창의 **단일 소스** (2026-08-10 P0).

## 왜 이 모듈이 생겼나 — 2026-08-10 실측

그날 08:20(Messiah)과 08:25(Messiah-G2) 트리거가 정확히 제 시각에 떴다. self-check도 PASS였다.
그리고 두 프로세스 모두 **그 자리에서 종료했다**:

    [기동 창] 기동 창(08:30~15:35) 이전 08:20:27 — 정시 트리거(08:35)에 맡기고 지금은 뜨지 않는다

`session_guard.LAUNCH_WINDOW_START`가 `time(8, 30)`으로 하드코딩돼 있었기 때문이다. 그 값은
2026-08-06에 "정시 트리거 08:35보다 5분 이르게"라는 근거로 정해졌는데, 2026-08-08 12:00에
스케줄러 트리거가 08:20/08:25로 손수 옮겨지면서 **그 전제가 조용히 무효가 됐다.**

세 겹으로 조용했다:

1. 거부 경로의 종료 코드가 0이다 → 스케줄러 기록은 `LastTaskResult=0`, 즉 **성공**이다.
2. 트리거 시각은 OS 상태고 기동 창은 코드다 → 둘이 어긋나도 테스트가 못 본다.
3. 실제로 `tests/ops/test_session_guard.py`에 "정시 트리거가 자기 가드에 막히면 매일 아침
   아무것도 안 뜬다"는 테스트가 **있었고 통과하고 있었다** — 08:35라는 *기억된* 시각을
   단언했지, *등록된* 시각을 읽지 않았기 때문이다.

3번이 이 모듈의 존재 이유다. 같은 숫자를 두 곳에 적어 두고 "어긋나지 않게 조심한다"는 대책은
이 프로젝트에서 이미 여러 번 실패했다(`core/event_calendar.py`의 `SessionHours` 주석이 같은
이유로 쓰였다). 그래서 시각을 `configs/scheduled_tasks.json` **한 곳**에만 두고, 등록하는
쪽(`scripts/install_scheduled_tasks.ps1`)과 판정하는 쪽(`ops/session_guard.py`)이 둘 다 그것을
읽는다. 그리고 파일과 **실제 등록 상태**가 어긋났는지는 매일
`ops/host_health.py check_schedule_drift`가 실측해서 대조한다 — 설정 파일도 결국 "적어 둔 것"일
뿐이라, 손으로 스케줄러를 만지면 또 어긋날 수 있다.

## 못 읽을 때의 태도

`session_guard.launch_window_verdict`와 같다 — **띄우는 쪽으로** 실패한다. 이 파일이 깨졌다고
그날 수집을 통째로 포기하는 것은 본말전도다. 대신 조용히는 안 한다(L18): 폴백을 썼다는 사실은
`schedule_drift` 항목이 매일 리포트에 남긴다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import time
from pathlib import Path

DEFAULT_SCHEDULE_PATH = Path("configs/scheduled_tasks.json")

# 정본을 못 읽을 때 쓰는 기동 창 시작. 실제 트리거(08:20)보다 넉넉히 이르되, 새벽 재부팅에
# 하루 종일 빈 프로세스가 뜨는 것은 여전히 막는 값이다. "막는 쪽으로 실패하지 않는다"를
# 지키면서도 at-startup 트리거의 원래 취지를 깨지 않는 절충.
FALLBACK_LAUNCH_WINDOW_START = time(8, 0)

# 정본에 `launch_window_margin_minutes`가 없을 때의 기본값. 2026-08-06에 정해진 근거를 그대로
# 옮겼다 — 부팅이 정시 트리거 몇 분 전에 끝난 날 "곧 스케줄러가 부를 테니까" 하고 거절하면
# 그 몇 분을 사람이 지켜봐야 한다.
DEFAULT_MARGIN_MINUTES = 5


@dataclass(frozen=True)
class ScheduledTask:
    """정본 한 줄. `weekly`는 평일 정시 트리거 시각(KST)."""

    name: str
    bat: str
    weekly: time
    at_boot: bool
    restart: bool
    collection: bool


class ScheduleUnreadable(Exception):
    """정본을 읽거나 해석할 수 없다 — 호출측은 폴백으로 넘어가되 사실을 남긴다."""


def _parse_hhmm(text: str) -> time:
    hour, _, minute = str(text).partition(":")
    return time(int(hour), int(minute))


def load_schedule(path: Path | str = DEFAULT_SCHEDULE_PATH) -> tuple[list[ScheduledTask], int]:
    """정본을 읽어 (작업 목록, 기동 창 여유분(분))을 돌려준다.

    실패 조건: 파일이 없거나 형식이 어긋나면 `ScheduleUnreadable`. 여기서 삼키지 않는 이유는
              호출측마다 폴백이 다르기 때문이다 — 기동 창은 "띄운다"로, 드리프트 검사는
              "측정 실패"로 각각 다르게 처리해야 한다(못 잰 것과 정상은 다르다, L18).
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        margin = int(raw.get("launch_window_margin_minutes", DEFAULT_MARGIN_MINUTES))
        tasks = [
            ScheduledTask(
                name=str(item["name"]),
                bat=str(item["bat"]),
                weekly=_parse_hhmm(item["weekly"]),
                at_boot=bool(item.get("at_boot", False)),
                restart=bool(item.get("restart", False)),
                collection=bool(item.get("collection", False)),
            )
            for item in raw["tasks"]
        ]
    except Exception as exc:  # noqa: BLE001 — 원인 종류가 무엇이든 호출측 처리는 같다
        raise ScheduleUnreadable(f"{path}: {type(exc).__name__}: {exc}") from exc

    if not tasks:
        raise ScheduleUnreadable(f"{path}: 작업이 하나도 없다")
    return tasks, margin


def collection_tasks(path: Path | str = DEFAULT_SCHEDULE_PATH) -> list[ScheduledTask]:
    """수집 계열 작업만 — 기동 창이 적용되는 것들.

    장후 정리(Messiah-Shutdown)·장후 절차(Messiah-Postmarket)는 기동 창과 무관하다. 그것들의
    15:40/15:45는 기동 창 밖이지만 정상이다 — 그쪽은 `run_l1_daily.py` 진입점을 안 탄다.
    """
    tasks, _ = load_schedule(path)
    return [task for task in tasks if task.collection]


def earliest_collection_trigger(path: Path | str = DEFAULT_SCHEDULE_PATH) -> time:
    """수집 작업 중 가장 이른 정시 트리거 — 기동 창이 반드시 포함해야 하는 시각."""
    tasks = collection_tasks(path)
    if not tasks:
        raise ScheduleUnreadable(f"{path}: 수집 계열(collection=true) 작업이 없다")
    return min(task.weekly for task in tasks)


def launch_window_start(path: Path | str = DEFAULT_SCHEDULE_PATH) -> time:
    """기동 창 시작 = 가장 이른 수집 트리거 − 여유분.

    이 함수가 있는 한 "정시 트리거가 자기 기동 창에 막힌다"는 2026-08-10의 사고는 **구조적으로**
    불가능하다. 창은 항상 트리거보다 이르게 계산되기 때문이다. 남는 위험은 정본과 실제 등록이
    어긋나는 것뿐이고, 그건 `host_health.check_schedule_drift`가 매일 실측한다.

    실패 조건: 없다. 정본을 못 읽으면 `FALLBACK_LAUNCH_WINDOW_START`를 돌려준다 — 가드가
              오판해서 수집을 막는 것이 오판해서 한 번 더 뜨는 것보다 나쁘다.
    """
    try:
        earliest = earliest_collection_trigger(path)
        _, margin = load_schedule(path)
    except ScheduleUnreadable:
        return FALLBACK_LAUNCH_WINDOW_START

    # `time`끼리는 빼기가 안 된다 — 분 단위 정수로 내려서 계산한다. `datetime`에 얹지 않는
    # 이유는 그쪽이 tz 없는 벽시계 연산이라 정적 검사(DTZ001)에 걸리기 때문이고, 여기서
    # 필요한 것은 애초에 날짜가 아니라 **벽시계 시각의 뺄셈**뿐이다.
    total = earliest.hour * 60 + earliest.minute - margin
    if total < 0:
        return time(0, 0)  # 자정을 넘겨 돌아가면 그날 전체를 여는 편이 안전하다
    return time(total // 60, total % 60)
