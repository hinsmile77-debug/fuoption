"""오프라인 작업의 전제 조건을 코드로 강제 — **언제** 돌려도 되는가(R11)와 **무엇 위에서**
돌려도 되는가(아카이브 정합) 두 가지 (2026-08-05).

## 왜 이 모듈이 생겼나

2026-08-04 일일점검에서 그날 **정규장 중에 다섯 번** 무거운 오프라인 작업이 돌았다는 것이
나왔다:

    09:52~09:56  8개월 백필 + 상위 Horizon 재합성 (KIS REST 대량 호출)
    13:27·13:50·14:03·14:24  LightGBM 모델 스윕
    13:42  G1 워크포워드

라이브 프로세스에 배포된 것은 없어 그날 실해는 없었다. 그러나 R11("장중 학습 금지·장중
배포 금지")은 지금까지 **문서에만 있었고** 아무것도 그것을 막지 않았다.

무해하지 않은 이유가 두 가지다:

1. **REST 유량 공유.** 백필은 KIS REST를 쓰고, 2026-08-04 저녁에 결선된 옵션체인 폴러는
   장중 내내 용량의 33%를 상시 점유한다(`scripts/run_l1_daily.py` `_option_chain_plan()`).
   선행 프로젝트 마흐디는 정확히 이 형태로 2026-07-30에 옵션체인 25사이클(5.1%)을 잃었다.
2. **CPU 경합.** 수집·합성·피처는 같은 PC의 이벤트 루프에서 돌고, 완성봉 규율의 유예는
   500ms다. 모델 스윕이 코어를 다 먹으면 그 유예가 실제로 좁아진다.

## 왜 예외를 남기나

정말 필요한 경우가 있다(장중에 발견한 결함을 그 자리에서 재현해야 할 때 등). 막되
**의식적으로 넘게** 한다 — `--force-intraday` 없이는 거부하고, 넘길 때는 그 사실이
표준출력에 남는다. 조용히 통과시키는 기본값은 두지 않는다(L18).
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Sequence

from messiah.core import logging as mlog
from messiah.core.event_calendar import DEFAULT_SESSION, EventCalendar
from messiah.core.timeutil import now_kst
from messiah.ops import task_schedule

# 거부 시 종료 코드 — 0(성공)·1(일반 실패)과 구분해 자동화가 "규칙에 막혔다"를 식별할 수 있게.
REFUSED_EXIT_CODE = 2

# 무결성 리포트의 **정본 파일명**. `daily_integrity_report.py`가 쓰는 이름은 정확히 이 모양이고,
# 사람이 남긴 보관본(`*_pre_recompose.json` 등)은 같은 `date`를 담고 있어도 판정이 아니다
# (`corrupt_archive_days()` docstring "보관본을 판정으로 읽고 있었다").
_CANONICAL_REPORT_NAME = re.compile(r"daily_integrity_(\d{8})\.json")


def is_regular_session_now(
    *, now: datetime | None = None, calendar: EventCalendar | None = None
) -> bool:
    """지금이 정규장(거래일 09:00~15:35 KST)인가. 휴장일이면 False."""
    moment = now or now_kst()
    cal = calendar or EventCalendar.from_file()
    return cal.is_trading_day(moment.date()) and cal.is_regular_session(moment)


def refuse_if_regular_session(
    what: str,
    *,
    force: bool = False,
    now: datetime | None = None,
    calendar: EventCalendar | None = None,
) -> None:
    """정규장 중이면 `SystemExit(REFUSED_EXIT_CODE)`. `force=True`면 통과하되 사실을 남긴다.

    입력: what은 사람이 읽을 작업 이름("모델 스윕" 등) — 거부 메시지에 그대로 들어간다.
    실패 조건: 없다. 달력을 못 읽는 등의 이유로 판정이 불가능하면 **통과시킨다** —
              가드가 본 기능을 막는 쪽으로 실패하면 안 된다(`core/docker_bootstrap.py`류의
              "부가 기능 실패가 본 기능을 막지 않는다"와 같은 원칙). 다만 조용히는 안 한다.
    """
    try:
        in_session = is_regular_session_now(now=now, calendar=calendar)
    except Exception as exc:  # noqa: BLE001 — 가드가 본 기능을 막지 않는다
        print(f"[session_guard] 장중 여부 판정 불가({exc}) — 그대로 진행", flush=True)
        return

    if not in_session:
        return
    if force:
        print(
            f"[session_guard] 정규장 중이지만 --force-intraday로 {what} 진행 — "
            "REST 유량과 CPU를 수집 파이프라인과 나눠 쓴다(R11 예외)",
            flush=True,
        )
        return

    print(
        f"[session_guard] 정규장 중에는 {what}을(를) 하지 않는다 — SYSTEM.md R11 "
        "(장중 학습 금지·장중 배포 금지, 금지 15계명 3·4).\n"
        "  근거: 백필/스윕은 KIS REST 유량과 CPU를 장중 수집 파이프라인과 공유한다. "
        "선행 프로젝트 마흐디가 같은 형태로 2026-07-30에 옵션체인 25사이클을 잃었다.\n"
        "  장 마감(15:35) 후에 다시 실행하거나, 정말 지금 필요하면 --force-intraday 를 붙일 것.",
        file=sys.stderr,
        flush=True,
    )
    raise SystemExit(REFUSED_EXIT_CODE)


# ------------------------------------------------ 비거래일 (2026-08-17 F-1/F-2/F-3)

# `SessionEnd`의 `reason` 값. **새 태그를 만들지 않는다** — "이 프로세스가 끝났다"를 세는
# 소비처가 여럿이고(`ops/integrity_report._abnormal_exits`, `ops/task_exit_codes`,
# 일일점검 다이제스트 §2), 새 태그는 그 전부가 둘 다 알아야만 맞는다. 하나만 모르면 그
# 소비처에서 이 날은 **비정상 종료**로 남는다.
NON_TRADING_DAY_REASON = "non_trading_day"


def non_trading_day_reason(
    *, day: date | None = None, calendar: EventCalendar | None = None
) -> str | None:
    """오늘(또는 `day`)이 비거래일이면 사람이 읽을 사유, 거래일이면 None.

    ## 왜 예외를 안 올리나 — 비대칭 (2026-08-17)

    `EventCalendar.is_trading_day()`는 등록 연도 밖을 물으면 `ValueError`다(L3). 그 예외가
    진입점까지 올라가면 **2027년 첫 거래일에 수집이 통째로 죽는다** — 달력 미갱신 하나로
    하루치 체결틱·수급·옵션체인이 영구 소실되고, 그건 이 함수가 막으려는 것(휴장일 적재)
    보다 훨씬 비싸다. 그래서 **판정 불가는 「거래일」로 접고** 그 사실을 시끄럽게 남긴다.
    같은 판단이 `launch_window_verdict()`에도 이미 있다(그쪽 docstring "달력을 못 읽으면
    띄우는 쪽으로").

    미갱신을 조용히 넘기지 않는 축은 따로 있다 — `scripts/self_check.py`의 `calendar`가
    `covered_through` 만료를 **만료되기 전부터** 매일 경고한다(금지계명 12는 그렇게 지킨다).

    ## 주말과 등재 휴장일을 문장에서 가른다

    둘 다 "안 여는 날"이지만 출처가 다르다 — 하나는 계산(`weekday() < 5`)이고 하나는 사람이
    확인해 파일에 적은 사실이다. 섞어 적으면 로그를 읽는 사람이 "달력이 맞았는가"를 물을 수
    없다(마흐디 `market_calendar.holiday_name()`이 주말을 None으로 두는 것과 같은 이유).
    """
    target = day or now_kst().date()
    try:
        trading = (calendar or EventCalendar.from_file()).is_trading_day(target)
    except Exception as exc:  # noqa: BLE001 — 판정 불가는 「거래일」로 (docstring)
        print(
            f"[session_guard] 거래일 판정 불가({exc}) — 거래일로 보고 진행한다"
            " (configs/krx_holidays.yaml 갱신 필요, 자가 점검 calendar 축 확인)",
            flush=True,
        )
        return None
    if trading:
        return None
    weekday = "월화수목금토일"[target.weekday()]
    if target.weekday() >= 5:
        return f"{target.isoformat()}({weekday})은 주말"
    return f"{target.isoformat()}({weekday})은 KRX 휴장일(configs/krx_holidays.yaml 등재)"


def announce_non_trading_day(process: str, reason: str) -> None:
    """비거래일 종료를 **사람용 한 줄 + 구조화 마커**로 남긴다 (2026-08-17 F-1).

    ## 왜 이 한 줄이 필요했나 (2026-08-15~17 실측)

    휴장 조기 종료는 2026-07-27부터 있었고 `print()` 한 줄로 끝났다. 그래서 08-15(토)·
    16(일)·17(대체공휴일) 사흘간 `SessionEnd` **0건**이었고, 거래일 5일은 각 1건이었다.
    결과는 일일점검이 그 사흘을 읽는 방식이었다: `SessionStart` 2회(부팅 트리거 + 정시
    트리거)만 보이고 종료 마커가 없으니 **"중복 기동" + "비정상 종료 의심"** 으로 찍혔다.
    08-17 다이제스트의 자동 적신호 5건 중 4건이 그 계열의 위양성이었다.

    `reason` 필드가 요점이다 — `msg`는 사람용이고 기계는 `reason`을 읽는다. 이 값이 있으면
    `_abnormal_exits`가 "기동 수 == 종료 수"로 균형을 잡고, 리포트는 "쉬었다"와
    "죽었다"를 구분할 근거를 갖는다.

    전제: 호출 전에 `mlog.setup()`이 끝나 있어야 한다 — 안 그러면 이 줄이 아무 데도 안 남는다
         (세 진입점 모두 `_arm_forensics_and_logging()`/`mlog.setup()`을 먼저 부른다).
    호출측은 이 함수를 부른 **직후 종료 코드 0으로 끝낸다** — 비거래일에 안 뜨는 것은
    설계된 동작이고 실패가 아니다(스케줄러에 실패로 남으면 늑대소년이 된다).
    """
    print(f"{reason} — {process} 운영 생략, 즉시 종료", flush=True)
    mlog.log(
        "SessionEnd",
        f"{reason} — 운영 생략",
        process=process,
        reason=NON_TRADING_DAY_REASON,
        calendar_source="EventCalendar",
    )


# ------------------------------------------------ 기동 창 (2026-08-06 P0-2, 부팅 자동 복구)

# 정시 기동보다 몇 분 이르다 — 부팅이 트리거 직전에 끝난 날 "곧 스케줄러가 부를 테니까" 하고
# 거절하면 그 몇 분을 사람이 지켜봐야 한다. 앞으로 당기되, 장 시작 훨씬 전에 켜진 PC가 하루
# 종일 빈 프로세스를 물고 있지는 않게 한다.
#
# **이 값을 여기 적지 않는다** (2026-08-10 P0). 종전에는 `time(8, 30)`이 하드코딩돼 있었고,
# 그 근거는 "정시 트리거 08:35보다 5분 이르게"였다. 2026-08-08에 트리거가 08:20/08:25로
# 옮겨지자 전제가 조용히 무효가 됐고, 08-10 아침 두 프로세스가 정시에 떠서 self-check까지
# 통과한 뒤 "기동 창 이전"으로 즉시 종료했다 — 종료 코드 0이라 스케줄러에는 성공으로 남았다.
# 이제 `configs/scheduled_tasks.json`의 등록 정본에서 파생한다(`ops/task_schedule.py` 참고).
LAUNCH_WINDOW_START = task_schedule.launch_window_start()

# 끝은 정규장 종료와 같다 — 그 뒤에 뜨는 것은 수집할 것이 없다. `run_l1_daily.py`의
# 종료 절차(통합·재합성·리포트)를 소급해 돌리고 싶으면 그건 `run_postmarket.py`의 일이다.
LAUNCH_WINDOW_END = DEFAULT_SESSION.close_time


def launch_window_verdict(
    *, now: datetime | None = None, calendar: EventCalendar | None = None
) -> tuple[bool, str]:
    """지금 일일 수집 프로세스를 띄워도 되는가 — (가부, 근거 문장).

    ## 왜 필요한가 (2026-08-06 실측)

    그날 10:03:49에 호스트가 재부팅됐다(Windows 이벤트 1074, `RuntimeBroker.exe`,
    "기타(계획되지 않음)"). OS는 10:05:03에 올라왔는데 **MESSIAH는 안 올라왔다** —
    Task Scheduler에 평일 08:35 트리거만 있고 at-startup 트리거도, 실패 시 재시작도
    없었기 때문이다. 사람이 알아챈 10:25까지 21분이 죽어 있었고 1분봉 21개가 영구 소실됐다.

    at-startup 트리거를 붙이면 그 21분이 사라진다. 대신 **아무 때나 부팅해도 뜨는** 문제가
    생긴다 — 새벽 3시 재부팅에 수집 프로세스가 떠서 하루 종일 KIS WS를 물고 있으면 안 된다.
    이 함수가 그 경계다.

    실패 조건: 없다. 달력을 못 읽으면 **띄우는 쪽으로** 판단한다 — 가드가 오판해서 수집을
              막는 것이 오판해서 한 번 더 뜨는 것보다 나쁘다(휴장일이면 진입점의 기존
              휴장일 검사가 어차피 한 번 더 거른다).
    """
    moment = now or now_kst()
    clock = moment.timetz().replace(tzinfo=None)

    try:
        trading_day = (calendar or EventCalendar.from_file()).is_trading_day(moment.date())
    except Exception as exc:  # noqa: BLE001 — 판정 불가는 "띄운다"로 (docstring)
        return True, f"기동 허용 — 거래일 판정 불가({exc}), 진입점의 휴장일 검사에 맡긴다"

    if not trading_day:
        return False, f"{moment.date().isoformat()}은 거래일이 아니다 — 기동 생략"
    if clock < LAUNCH_WINDOW_START:
        # 정시 트리거 시각을 문장에 박아 넣지 않는다 (2026-08-10) — 종전 문구는 "정시
        # 트리거(08:35)에 맡기고"라고 단언했는데, 그 시각이 이미 08:20으로 바뀐 뒤였다.
        # 사람을 향한 안내문이 틀린 시각을 가리키면 원인 추적이 그만큼 늦어진다.
        try:
            nudge = f"정시 트리거({task_schedule.earliest_collection_trigger():%H:%M})에 맡기고"
        except task_schedule.ScheduleUnreadable:
            nudge = "정시 트리거에 맡기고"
        return False, (
            f"기동 창({LAUNCH_WINDOW_START:%H:%M}~{LAUNCH_WINDOW_END:%H:%M}) 이전 "
            f"{clock:%H:%M:%S} — {nudge} 지금은 뜨지 않는다"
        )
    if clock >= LAUNCH_WINDOW_END:
        return False, (
            f"기동 창({LAUNCH_WINDOW_START:%H:%M}~{LAUNCH_WINDOW_END:%H:%M}) 이후 "
            f"{clock:%H:%M:%S} — 수집할 정규장이 남아 있지 않다"
            " (장후 절차는 scripts/run_postmarket.py)"
        )
    return True, f"기동 허용 — 정규장 종료까지 {clock:%H:%M:%S} 시점 기준 남은 구간 있음"


# 거부 시각이 등록 트리거와 이만큼 안쪽이면 "정시 기동이 거부된 것"으로 본다. self_check +
# Docker 기동에 실측 20~30초가 걸리므로(2026-08-10 로그: 08:20:00 트리거 → 08:20:27 거부)
# 넉넉히 잡되, 부팅 트리거가 우연히 이 창에 걸리는 일은 드물게 유지되는 폭이다.
SCHEDULED_LAUNCH_TOLERANCE_MINUTES = 5


def minutes_since_scheduled_trigger(*, now: datetime | None = None) -> float | None:
    """지금이 **가장 이른 수집 트리거**보다 몇 분 늦었나 — 판정 불가면 None (2026-08-10 B-2).

    수집 프로세스가 기동 직후 한 번 부른다. 이 값은 그 순간 **이미 확정된 손실**이다 —
    그 구간의 체결틱·수급·옵션체인은 과거 조회 경로가 없어 영원히 없다.

    `ops/integrity_report.py`의 `_collection_start_lag_minutes()`가 장후에 로그로 재는 값과
    **같은 뜻·같은 기준점**이다. 둘을 나눠 둔 이유는 시점뿐이다: 이쪽은 장중에 화면이 쓰고,
    그쪽은 장후에 리포트가 쓴다. 기준점(등록 정본)이 하나라 두 값이 어긋날 수 없다.

    음수(트리거보다 이르게 손으로 띄운 날)도 그대로 돌려준다 — 0으로 접으면 "오늘 스케줄러가
    안 떴을 수 있다"는 신호가 사라진다.
    """
    moment = now or now_kst()
    try:
        trigger = task_schedule.earliest_collection_trigger()
    except task_schedule.ScheduleUnreadable:
        return None
    scheduled = moment.replace(hour=trigger.hour, minute=trigger.minute, second=0, microsecond=0)
    return round((moment - scheduled).total_seconds() / 60.0, 1)


def prior_sessions_today(
    process_log: Path, *, now: datetime | None = None, within_seconds: float = 15.0
) -> int:
    """오늘 이 로그 파일에 **이번 기동보다 먼저** 찍힌 `SessionStart`의 수 (2026-08-19 F-2).

    ## 왜 필요한가 — 장중 재기동이 249분짜리 기동 지연으로 적혔다

    `minutes_since_scheduled_trigger()`의 docstring은 이렇게 적고 있었다: *"기준점(등록
    정본)이 하나라 두 값이 어긋날 수 없다."* 2026-08-19가 그 문장을 반증했다.

        logs/status_snapshot.json (15:34:47)  start_lag_minutes 249.4
        logs/daily_integrity_20260819.json    collection_start_lag_minutes 0.5

    둘 다 자기 기준으로는 옳다. 장후 축은 **그날 첫 기동**(08:20:29)을 재고, 인프로세스
    장부는 **자기가 뜬 시각**(12:29)을 잰다. 그런데 그날 08:20~09:50은 정상 수집됐다 —
    「기동 지연 249분」은 그 구간을 통째로 없던 것으로 만드는 문장이고, 화면은 오후 내내
    그렇게 말했다. 실제로 잃은 것은 09:50~12:29의 158.9분이었다.

    재기동한 프로세스는 **이전 세션이 무엇을 봤는지 모른다**(`ops/loss_ledger.py` 모듈
    docstring "프로세스 로컬이다"). 그러니 지어내지 않는 것이 옳다 — 다만 「내가 첫 기동이
    아니다」는 로그 파일 하나로 알 수 있고, 그 사실만 있으면 거짓 숫자를 안 만들 수 있다.

    ## 자기 자신을 세지 않는다

    `mlog.setup()`이 이미 이번 세션의 `SessionStart`를 찍었고 그 줄이 같은 파일에 있다.
    그래서 **`now`로부터 `within_seconds` 안쪽의 줄은 이번 기동으로 보고 뺀다.** 자식
    프로세스는 `NestedSessionStart`라 애초에 안 걸린다(`core/logging.session_start`).

    실패 조건: 없다. 파일이 없거나 못 읽으면 0 — 판정 불가를 이유로 거짓 재기동을 만들지
              않는다(`refused_a_scheduled_launch()`와 같은 원칙).
    """
    moment = now or now_kst()
    try:
        text = process_log.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return 0
    count = 0
    for line in text.splitlines():
        if '"SessionStart"' not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("tag") != "SessionStart":
            continue
        stamp = str(record.get("ts", ""))[11:19]
        if len(stamp) != 8:
            continue
        try:
            clock = datetime.strptime(stamp, "%H:%M:%S").time()  # noqa: DTZ007 — KST 벽시계
        except ValueError:
            continue
        at = datetime.combine(moment.date(), clock, tzinfo=moment.tzinfo)
        if (moment - at).total_seconds() <= within_seconds:
            continue  # 이번 기동 자신
        count += 1
    return count


def refused_a_scheduled_launch(*, now: datetime | None = None) -> bool:
    """이번 거부가 **정시 트리거로 뜬 기동**을 거부한 것인가 (2026-08-10 P0).

    ## 왜 이걸 구분하나

    거부 자체는 정상 동작이다 — 새벽 3시 재부팅에 at-startup 트리거가 부르면 거부하는 것이
    맞고, 그건 실패가 아니므로 종료 코드 0이 옳다.

    그런데 **정시 트리거가 거부되는 것은 절대 정상이 아니다.** 그날 수집이 통째로 없어진다는
    뜻이기 때문이다. 2026-08-10에 정확히 그 일이 일어났고, 두 프로세스 모두 종료 코드 0으로
    끝나 스케줄러에는 `LastTaskResult=0`(성공)으로 남았다. 스케줄러 기록·자가 점검 경고·
    로그가 전부 "정상"이라고 말하는 동안 오전이 사라졌다.

    같은 아침 `boot_recovery` 항목은 이미 경고를 찍고 있었다(부팅 트리거가 08-08에 지워졌다).
    아무도 못 봤다 — **경고만 하는 채널은 이미 한 번 실패한 채널이다.** 그래서 이 경우만은
    종료 코드를 갈라, 사람이 아니라 스케줄러가 실패로 기록하게 한다.

    실패 조건: 없다. 정본을 못 읽으면 `False`(평소대로 조용히 종료) — 판정 불가를 이유로
              거짓 실패를 만들지는 않는다.
    """
    moment = now or now_kst()
    try:
        triggers = [task.weekly for task in task_schedule.collection_tasks()]
    except task_schedule.ScheduleUnreadable:
        return False

    # 벽시계 초로 내려서 비교한다 — 날짜는 필요 없고, `datetime`에 얹으면 tz 없는 연산이라
    # 정적 검사(DTZ001)에 걸린다(`ops/task_schedule.py`의 같은 자리와 같은 이유).
    clock = moment.timetz().replace(tzinfo=None)
    here = clock.hour * 3600 + clock.minute * 60 + clock.second
    for trigger in triggers:
        gap = here - (trigger.hour * 3600 + trigger.minute * 60)
        if 0 <= gap <= SCHEDULED_LAUNCH_TOLERANCE_MINUTES * 60:
            return True
    return False


def add_force_intraday_argument(parser) -> None:  # type: ignore[no-untyped-def]
    """`argparse` 파서에 `--force-intraday`를 붙인다 — 여섯 스크립트가 같은 문구를 쓰도록."""
    parser.add_argument(
        "--force-intraday",
        action="store_true",
        help="정규장 중에도 실행(R11 예외 — REST 유량·CPU를 수집 파이프라인과 공유한다)",
    )


# ------------------------------------------------ 아카이브 정합 (2026-08-05 2차, 고도화 5)


def corrupt_archive_days(
    days: Sequence[date], *, log_dir: Path = Path("logs")
) -> dict[date, list[str]]:
    """학습 구간 중 **상위 Horizon 봉이 손상된 것으로 이미 판정된** 날들.

    근거는 그날 무결성 리포트의 `horizon_findings`다 — "상위 봉 거래량 합 ≠ 1분봉 합"이라는
    정의상의 항등식 위반이고, `run_recompose.py`로 재합성하면 사라진다. 즉 이 목록이 비면
    "재합성이 끝났다"는 뜻이기도 하다.

    **`late_bar_drops`는 일부러 안 본다.** 그건 수집 당시의 사건을 세는 지표라 재합성 후에도
    남는다(그게 그 지표의 존재 이유다). 그걸로 막으면 2026-08-05가 영원히 학습 불가가 된다.

    리포트가 없는 날은 목록에 넣지 않는다 — 리포트는 2026-07-27부터 있고 학습 구간은 보통
    수개월이라, 없는 날을 전부 막으면 아무것도 학습할 수 없다. 대신 호출측이 "몇 날을 못
    봤는지"를 함께 알리도록 `unjudged_days()`를 따로 둔다.

    ## **보관본을 판정으로 읽고 있었다** (2026-08-11 실측)

    종전 glob은 `daily_integrity_*.json`이라 `daily_integrity_20260805_pre_recompose.json`도
    함께 읽었다. 그 파일은 재합성 **직전** 상태를 남겨 둔 보관본인데 `date` 필드가 같아서,
    정렬상 뒤에 오는 그것이 **재합성 후의 깨끗한 판정을 덮어썼다.** 결과는 이 함수의
    docstring이 약속한 것의 정반대다: *"이 목록이 비면 재합성이 끝났다는 뜻"*이라고 적어
    두고, 정작 재합성이 끝난 2026-08-05를 **영원히 손상으로** 판정하고 있었다.

    2026-08-11에 RegimeAI 실학습을 돌리려다 그 거부에 막혀 발견했다. 6일간 아무도 그 경로를
    안 밟아서 안 드러난 것이고, 밟았다면 `--force-corrupt-archive`로 넘겼을 것이다 —
    가드가 오탐하면 사람은 가드를 끄는 법을 배운다.

    이제 **정본 파일명만** 읽는다(`daily_integrity_YYYYMMDD.json`). 파일명의 날짜와 내용의
    `date`가 다르면 그것도 버린다 — 손으로 복사한 파일이 남의 날짜를 주장하는 경우다.
    """
    findings: dict[date, list[str]] = {}
    wanted = set(days)
    for path in sorted(log_dir.glob("daily_integrity_*.json")):
        stamp = _CANONICAL_REPORT_NAME.fullmatch(path.name)
        if stamp is None:
            continue  # 보관본(`*_pre_recompose` 등) — 판정이 아니라 기록이다
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            day = date.fromisoformat(report["date"])
        except (OSError, ValueError, KeyError):
            continue  # 깨진 리포트 하나가 가드 전체를 막지 않는다
        if day.strftime("%Y%m%d") != stamp.group(1):
            continue  # 파일명과 내용이 다른 날을 말한다 — 어느 쪽도 못 믿는다
        if day not in wanted:
            continue
        items = [str(item) for item in (report.get("horizon_findings") or [])]
        if items:
            findings[day] = items
    return findings


def refuse_if_archive_corrupt(
    what: str,
    days: Sequence[date],
    *,
    force: bool = False,
    log_dir: Path = Path("logs"),
) -> None:
    """학습 구간에 손상 판정된 날이 있으면 `SystemExit(REFUSED_EXIT_CODE)`.

    ## 왜 필요했나 (2026-08-05 2차)

    그날 상위 Horizon 봉의 3~17%가 잘린 채 아카이브에 들어갔다(`data/bar_composer.py`).
    1분봉은 무손상이라 `run_recompose.py`로 복구되지만, **복구 전에 학습을 돌리면 잘린 봉을
    그대로 배운다.** 그리고 그 순서는 그날 문서에만 적혀 있었다 — 2026-07-29~08-03에 "다음
    거래일에 확인한다"가 문서에만 있어 세 번 재발했던 것과 같은 형태다.

    실패 조건: 리포트를 못 읽는 등 판정이 불가능하면 **통과시킨다** — 가드가 본 기능을 막는
              쪽으로 실패하면 안 된다(`refuse_if_regular_session`과 같은 원칙). 조용히는 안 한다.
    """
    try:
        corrupt = corrupt_archive_days(days, log_dir=log_dir)
    except Exception as exc:  # noqa: BLE001 — 가드가 본 기능을 막지 않는다
        print(f"[session_guard] 아카이브 정합 판정 불가({exc}) — 그대로 진행", flush=True)
        return

    if not corrupt:
        return
    listed = ", ".join(day.isoformat() for day in sorted(corrupt))
    if force:
        print(
            f"[session_guard] 손상 판정된 날({listed})이 있지만 --force-corrupt-archive로 "
            f"{what} 진행 — 잘린 상위 Horizon 봉을 그대로 학습한다",
            flush=True,
        )
        return

    sample = next(iter(sorted(corrupt)))
    print(
        f"[session_guard] {what} 구간에 상위 Horizon 봉이 손상된 날이 있다: {listed}\n"
        f"  예: {sample.isoformat()} — {corrupt[sample][0]}\n"
        "  1분봉은 무손상이므로 먼저 재합성할 것:\n"
        "      python scripts/run_recompose.py --symbol <심볼>\n"
        "      python scripts/daily_integrity_report.py --date <해당일>   # 판정 갱신\n"
        "  정말 손상된 봉으로 학습해야 하면 --force-corrupt-archive 를 붙일 것.",
        file=sys.stderr,
        flush=True,
    )
    raise SystemExit(REFUSED_EXIT_CODE)


def add_force_corrupt_archive_argument(parser) -> None:  # type: ignore[no-untyped-def]
    parser.add_argument(
        "--force-corrupt-archive",
        action="store_true",
        help="상위 Horizon 봉이 손상 판정된 날이 있어도 학습 진행(재합성 전 학습 — 권장하지 않음)",
    )
