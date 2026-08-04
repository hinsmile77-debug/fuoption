"""장중 학습·백필 거부 — SYSTEM.md R11 / 금지 15계명 3·4를 코드로 강제 (2026-08-05).

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

import sys
from datetime import datetime

from messiah.core.event_calendar import EventCalendar
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


def add_force_intraday_argument(parser) -> None:  # type: ignore[no-untyped-def]
    """`argparse` 파서에 `--force-intraday`를 붙인다 — 여섯 스크립트가 같은 문구를 쓰도록."""
    parser.add_argument(
        "--force-intraday",
        action="store_true",
        help="정규장 중에도 실행(R11 예외 — REST 유량·CPU를 수집 파이프라인과 공유한다)",
    )
