"""**오늘의 정본 심볼** — 해석을 한 곳으로 모은다 (2026-08-14 G-7).

## 왜 생겼나

2026-08-14 첫 월물 롤에서 **하나의 리포 안에서 두 심볼이 동시에 정본 행세를 했다.**

    수집·매매·verify_archive_volume   →  A05609  (옳음)
    장후 배치 4/5단계                  →  A05608  (만기된 월물)
    Command Center UI                 →  A05608  (하드코딩 상수)

어느 쪽도 자기가 소수파인지 몰랐다. 도구들은 저마다 "0행"을 정상 산출로 리포트에 썼고,
`fix_verification`이 그 오염된 입력으로 재발 12건을 찍었다(1건 허위·3건 수치 오류).

그날 확인된 해석 경로만 **세 갈래**였다:

    ① 하드코딩 default          `--symbol` 기본값, `DEFAULT_SYMBOL` 상수
    ② 마스터파일 조회            `symbol_master.front_month_future_code()` — **날짜를 안 받는다**
    ③ 만기 규칙 계산             `backfill.front_month_code_for_day(day)`

②가 특히 위험하다. 소급 실행(`--date 2026-08-12`)에서 **오늘의** 근월물을 답하므로
그 실행은 성공으로 끝나고 리포트만 거짓이 된다 — 조용히 틀리는 형태다.

## 해석이 아니라 조회가 되면 갈라질 수 없다

그날 3/5단계(`verify_archive_volume.py`)만 옳았는데, 이유는 그 도구가 `--symbol`을 아예
안 받고 **스스로 찾도록** 만들어졌기 때문이다. 설계 방향이 이미 거기 있었다.

이 모듈은 그 방향을 전 도구로 넓힌다:

    resolve(day)          만기 규칙으로 계산 — 네트워크 없음, 날짜를 받는다
    record(day, symbol)   그날 런타임이 실제로 고른 값을 파일로 남긴다
    recorded(day)         남겨진 값을 **조회**한다 (없으면 None)
    resolve_for_tools(day) 조회 우선 · 없으면 계산 — 도구들이 부를 자리

`record()`가 필요한 이유: 런타임(`run_l1_daily`)은 KIS 마스터파일로 근월물을 정하는데,
그 판단이 만기 규칙과 어긋날 수 있다(상장 일정 변경 등). **어긋나면 런타임이 옳다** —
실제로 그 심볼을 구독하고 그 심볼로 적재하기 때문이다. 그래서 런타임이 자기 선택을 남기고
나머지 도구는 그것을 읽는다.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from messiah.core import logging as mlog
from messiah.core.event_calendar import EventCalendar
from messiah.data import backfill

DEFAULT_RECORD_DIR = Path("logs")


def _record_path(day: date, log_dir: Path) -> Path:
    return log_dir / f"trading_symbol_{day.strftime('%Y%m%d')}.json"


def resolve(day: date, calendar: EventCalendar | None = None) -> str:
    """그날의 근월물 — **만기 규칙 계산**. 네트워크를 안 탄다.

    달력을 못 읽으면 만기 보정 없이 계산한다 — 부가 정보 하나 때문에 해석 전체가 죽는 것이
    훨씬 나쁘다(`EventCalendar` 예외를 삼키는 다른 소비처들과 같은 판단).
    """
    if calendar is None:
        try:
            calendar = EventCalendar.from_file()
        except Exception:  # noqa: BLE001
            calendar = None
    return backfill.front_month_code_for_day(day, calendar)


def record(day: date, symbol: str, *, log_dir: Path = DEFAULT_RECORD_DIR) -> Path | None:
    """런타임이 **실제로 고른** 심볼을 남긴다 — 나머지 도구가 읽을 정본.

    실패해도 예외를 올리지 않는다. 이건 관측 보조이고, 기록 실패가 수집 본 임무를 죽이면
    본말전도다(`ops/status_board.py`와 같은 규율). 대신 조용히 넘어가지 않는다.
    """
    path = _record_path(day, log_dir)
    payload = {"date": day.isoformat(), "symbol": symbol}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path
    except OSError as exc:
        mlog.log(
            "TradingSymbolRecordFailed",
            f"오늘의 정본 심볼 기록 실패 — 도구들이 계산으로 폴백한다: {exc}",
            date=day.isoformat(),
            symbol=symbol,
            path=str(path),
        )
        return None


def recorded(day: date, *, log_dir: Path = DEFAULT_RECORD_DIR) -> str | None:
    """그날 런타임이 남긴 심볼 — 없거나 깨졌으면 None.

    **파일 안의 날짜가 요청한 날짜와 다르면 버린다.** 파일명과 내용이 어긋나는 경우를
    조용히 한쪽 편들지 않는다 — `fix_verification.load_daily_reports()`가 2026-08-14에
    바로 그 형태로 9거래일간 오염돼 있었다.
    """
    try:
        payload = json.loads(_record_path(day, log_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    symbol = payload.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        return None
    if payload.get("date") != day.isoformat():
        return None
    return symbol


def is_rollover_day(day: date, calendar: EventCalendar | None = None) -> bool:
    """오늘 근월물이 **직전 거래일과 다른가** (2026-08-14 G-10).

    롤은 4주에 한 번뿐이라 사람이 잊는다. 그래서 시스템이 먼저 말해야 하고, 그러려면
    "오늘이 롤인가"가 **하나의 질문**이어야 한다 — 자가점검·장후 배치·CI 게이트가 각자
    날짜 산술을 하면 세 곳이 다르게 답할 수 있다.
    """
    if calendar is None:
        try:
            calendar = EventCalendar.from_file()
        except Exception:  # noqa: BLE001
            calendar = None
    try:
        previous = (
            calendar.previous_trading_day(day) if calendar is not None else day - timedelta(days=1)
        )
    except Exception:  # noqa: BLE001 — 달력 밖 연도 등. 판정 불가는 롤 아님으로 둔다
        return False
    return resolve(day, calendar) != resolve(previous, calendar)


def next_rollover_day(after: date, calendar: EventCalendar | None = None, limit: int = 400) -> date:
    """`after` **다음**의 첫 롤 당일 — 사전 백필(G-1)이 언제 돌아야 하는지의 답.

    만기 다음 거래일이 롤이므로 만기 규칙에서 바로 나온다. 달력이 없으면 주말만 건너뛴다.
    """
    if calendar is None:
        try:
            calendar = EventCalendar.from_file()
        except Exception:  # noqa: BLE001
            calendar = None
    probe = after
    for _ in range(limit):
        probe = probe + timedelta(days=1)
        if calendar is not None:
            try:
                if not calendar.is_trading_day(probe):
                    continue
            except Exception:  # noqa: BLE001
                pass
        elif probe.weekday() >= 5:
            continue
        if is_rollover_day(probe, calendar):
            return probe
    raise ValueError(f"{after} 이후 {limit}일 안에 롤 경계를 못 찾았다 — 만기 규칙 확인 필요")


def resolve_for_tools(
    day: date, *, explicit: str | None = None, log_dir: Path = DEFAULT_RECORD_DIR
) -> tuple[str, str]:
    """도구가 부를 자리 — `(심볼, 출처)`.

    우선순위: **명시 > 런타임 기록 > 만기 규칙 계산**.

    사람이 `--symbol`로 명시하면 그것이 이긴다(소급 조사·수동 복구에 필요하다). 그 다음이
    런타임 기록인 이유는 위 모듈 docstring "record()가 필요한 이유" 그대로다.
    """
    if explicit:
        return explicit, "명시"
    from_record = recorded(day, log_dir=log_dir)
    if from_record:
        return from_record, "런타임 기록"
    return resolve(day), "만기 규칙 계산"
