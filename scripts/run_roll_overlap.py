"""롤 겹침 하루 확보 — 만기일 장후에 **들어오는 월물**의 그날치를 받아둔다 (2026-08-14 G-1).

## 왜 필요한가 — 조사가 밝힌 것

2026-08-14 첫 월물 롤을 조사하다 드러났다. 학습 경로는 이미 **후방조정**을 한다
(`backfill.load_continuous_series` → `compute_roll_offsets` + `back_adjust`). 즉 "학습
데이터가 롤에서 8번 끊겨 있다"는 오래된 전제는 **틀렸다**. 조정은 있었다.

문제는 그 조정이 **basis를 못 재면 무의미**하다는 것이다. basis는 같은 날 두 계약을 비교해
재는데, 각 월물의 근월 구간만 있으면 두 계약이 같은 날 관측된 적이 한 번도 없다. 백필은
이걸 알고 `roll_overlap_targets()`로 겹침 하루를 받아 왔고, 그래서 **과거 7개 롤은 basis가
측정돼 있다**:

    A05601→02  +49틱      A05605→06  +161틱
    A05602→03  +36틱      A05606→07  +240틱   ← 최대(4.80pt)
    A05603→04  -50틱      A05607→08  +202틱
    A05604→05  +116틱     A05608→09  **0틱 · matched_minute=None**  ← 측정 실패

마지막 줄이 이번 롤이다. 백필은 일상적으로 안 돌고 라이브 수집은 신규 월물을 롤 전에
받아두지 않으므로, **겹침이 없어 basis를 못 쟀다.** `compute_roll_offsets()`는 그 경우
offset 0으로 두고 `matched_minute=None`으로 표시하는데(그쪽 docstring: *"조용히 0으로
처리하면 그 경계의 가짜 급등이 조정된 줄 알고 넘어가게 된다"*), **아무도 그 표시를 안 읽었다.**

크기를 재 두면: 측정된 7곳의 basis 절대값은 중앙값 116틱(2.32pt)이고, 같은 구간 1분봉의
봉간 절대변동 중앙값이 39틱이다. **롤 점프는 평소 1분 움직임의 3배**이고 최대치(240틱)는
p99(247틱)와 맞먹는다. 조정 없이 이어붙이면 수익률·변동성 계열에 그만한 가짜 사건이 하나
박힌다.

## 무엇을 하나

만기일(= 다음 거래일이 롤인 날) 장후에 **들어오는 월물의 그날치 1분봉**만 받는다.
그 심볼의 그날 아카이브는 비어 있으므로 `write_day()`가 지울 것이 없다 — 오늘 데이터를
덮어쓰는 위험이 없다는 것이 이 스크립트를 `run_backfill.py`와 분리한 이유다
(저쪽은 교체가 목적이라 조각까지 지운다).

롤 1회당 1일, 약 5회 호출. 4주에 한 번이다.

사용:
    python scripts/run_roll_overlap.py               # 오늘이 만기일이면 실행, 아니면 조용히 종료
    python scripts/run_roll_overlap.py --date 2026-09-10
    python scripts/run_roll_overlap.py --dry-run     # 계획만
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import redis  # noqa: E402

from messiah.broker.kis.credentials import KISCredentials  # noqa: E402
from messiah.broker.kis.redis_rate_limiter import RedisRateLimiter  # noqa: E402
from messiah.broker.kis.redis_token_cache import RedisTokenDaemon  # noqa: E402
from messiah.broker.kis.rest_client import KISRestClient  # noqa: E402
from messiah.core import logging as mlog  # noqa: E402
from messiah.core import symbol_resolution  # noqa: E402
from messiah.core.config import load_instance  # noqa: E402
from messiah.core.event_calendar import EventCalendar  # noqa: E402
from messiah.core.messages import Horizon  # noqa: E402
from messiah.core.timeutil import now_kst  # noqa: E402
from messiah.data import backfill, bar_paths  # noqa: E402
from messiah.data.archiver import ParquetArchiver  # noqa: E402
from messiah.ops import session_guard  # noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "data" / "bars"


def _parse_day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()  # noqa: DTZ007


def _next_trading_day(day: date, calendar: EventCalendar | None) -> date:
    if calendar is not None:
        try:
            return calendar.next_trading_day(day)
        except Exception:  # noqa: BLE001 — 달력 밖 연도
            pass
    probe = day + timedelta(days=1)
    while probe.weekday() >= 5:
        probe += timedelta(days=1)
    return probe


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="롤 겹침 하루 확보 (basis 측정용)")
    p.add_argument("--date", type=_parse_day, default=None, help="기본: 오늘 KST")
    p.add_argument("--base-dir", default=str(_DATA_DIR))
    p.add_argument("--configs", default="configs")
    p.add_argument("--dry-run", action="store_true")
    session_guard.add_force_intraday_argument(p)
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    session_guard.refuse_if_regular_session("롤 겹침 확보", force=args.force_intraday)
    mlog.setup("roll-overlap")

    day = args.date or now_kst().date()
    try:
        calendar: EventCalendar | None = EventCalendar.from_file()
    except Exception:  # noqa: BLE001
        calendar = None

    outgoing = symbol_resolution.resolve(day, calendar)
    nxt = _next_trading_day(day, calendar)
    incoming = symbol_resolution.resolve(nxt, calendar)

    if incoming == outgoing:
        # **평시엔 조용하다.** 4주에 한 번만 일하는 도구가 매일 무언가를 찍으면 그 줄을
        # 아무도 안 읽게 되고, 정작 롤 당일의 한 줄도 같이 묻힌다.
        print(f"오늘({day})은 만기일이 아니다 — 다음 거래일 {nxt}도 {outgoing}. 할 일 없음.")
        return 0

    print(f"=== 롤 겹침 확보 — {day} 만기 · {outgoing} → {incoming} (다음 거래일 {nxt}) ===")
    if bar_paths.day_sources(Path(args.base_dir), incoming, Horizon.M1, day):
        print(f"  {incoming} {day} 이미 있음 — 멱등 종료")
        return 0
    if args.dry_run:
        print(f"  (--dry-run) {incoming} {day} 1분봉을 받아 write_day() 했을 것")
        return 0

    cfg = load_instance(args.configs)
    creds = KISCredentials.from_broker_config(cfg.broker)
    rds = redis.from_url(cfg.redis_url, decode_responses=True)
    client = KISRestClient(
        creds,
        token_daemon=RedisTokenDaemon(creds, rds),
        rate_limiter=RedisRateLimiter(1.0, rds),
    )
    archiver = ParquetArchiver(Path(args.base_dir))

    try:
        bars = backfill.fetch_day_bars(
            client.get_futureoption_minute_chart, incoming, day, Decimal(cfg.futures_tick_size)
        )
    except Exception as exc:  # noqa: BLE001 — 실패해도 장후 배치를 죽이지 않는다
        mlog.log(
            "RollOverlapFailed",
            f"{incoming} {day} 겹침 수집 실패 — 이번 롤의 basis는 측정 불가로 남는다: {exc}",
            outgoing=outgoing,
            incoming=incoming,
            date=day.isoformat(),
        )
        print(f"  실패: {exc}", file=sys.stderr)
        return 1

    if not bars:
        # 들어오는 월물이 아직 거래되지 않는 경우 — 실측상 근월이 되기 전에도 거래되므로
        # (A05608은 2026-02-13부터) 정상 경로에서는 안 나온다. 나오면 그 자체가 신호다.
        mlog.log(
            "RollOverlapFailed",
            f"{incoming} {day} 응답 0봉 — 그 월물이 아직 안 거래되거나 조회 조건이 틀렸다",
            outgoing=outgoing,
            incoming=incoming,
            date=day.isoformat(),
        )
        print("  0봉 — 겹침을 못 만들었다", file=sys.stderr)
        return 1

    rows = archiver.write_day(incoming, Horizon.M1, bars)
    mlog.log(
        "RollOverlapCaptured",
        f"{incoming} {day} {rows}봉 확보 — 이번 롤의 basis를 잴 수 있다",
        outgoing=outgoing,
        incoming=incoming,
        date=day.isoformat(),
        rows=rows,
    )
    print(f"  {incoming} {day}  {rows}봉 (겹침) → basis 측정 가능")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
