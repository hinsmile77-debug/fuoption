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
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Sequence

from messiah.core.event_calendar import DEFAULT_SESSION, EventCalendar
from messiah.core.timeutil import now_kst

# 거부 시 종료 코드 — 0(성공)·1(일반 실패)과 구분해 자동화가 "규칙에 막혔다"를 식별할 수 있게.
REFUSED_EXIT_CODE = 2


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


# ------------------------------------------------ 기동 창 (2026-08-06 P0-2, 부팅 자동 복구)

# 정시 기동(08:35)보다 5분 이르다 — 부팅이 08:32에 끝난 날 "5분 뒤 스케줄러가 부를 테니까"
# 하고 거절하면 그 5분을 사람이 지켜봐야 한다. 앞으로 당기되, 장 시작 훨씬 전에 켜진 PC가
# 하루 종일 빈 프로세스를 물고 있지는 않게 한다.
LAUNCH_WINDOW_START = time(8, 30)

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
        return False, (
            f"기동 창({LAUNCH_WINDOW_START:%H:%M}~{LAUNCH_WINDOW_END:%H:%M}) 이전 "
            f"{clock:%H:%M:%S} — 정시 트리거(08:35)에 맡기고 지금은 뜨지 않는다"
        )
    if clock >= LAUNCH_WINDOW_END:
        return False, (
            f"기동 창({LAUNCH_WINDOW_START:%H:%M}~{LAUNCH_WINDOW_END:%H:%M}) 이후 "
            f"{clock:%H:%M:%S} — 수집할 정규장이 남아 있지 않다"
            " (장후 절차는 scripts/run_postmarket.py)"
        )
    return True, f"기동 허용 — 정규장 종료까지 {clock:%H:%M:%S} 시점 기준 남은 구간 있음"


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
    """
    findings: dict[date, list[str]] = {}
    wanted = set(days)
    for path in sorted(log_dir.glob("daily_integrity_*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            day = date.fromisoformat(report["date"])
        except (OSError, ValueError, KeyError):
            continue  # 깨진 리포트 하나가 가드 전체를 막지 않는다
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
