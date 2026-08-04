"""아카이브 1분봉을 거래소 공식 분봉과 **거래량으로** 대조한다 (2026-08-05 신설).

    python scripts/verify_archive_volume.py --date 2026-08-04
    python scripts/verify_archive_volume.py --start 2026-07-27 --end 2026-08-04

## 왜 이 도구가 필요했나

2026-08-04 일일 무결성 리포트는 "1분봉 410개 · 결손 0분 · CRITICAL 0 · ERROR 0 · WARNING 0"
으로 깨끗했다. 그런데 그날 수집 프로세스는 08:35에 뜬 `d5e6b01`로 돌았고, **WS 프레임에
여러 체결이 묶여 오는데 첫 건만 파싱하던 결함**(거래량의 약 절반 유실)의 수정은 같은 날
12:22에 들어갔다. 즉 그날 아카이브 전체가 거래량 절반짜리인데 리포트는 아무 말도 안 했다.

리포트가 그걸 못 본 이유는 단순하다 — **"봉이 있는가"만 보고 "봉이 맞는가"는 안 봤다.**
`analyze_horizon_consistency()`(08-05 신설)가 내부 정합성(1분봉 합 = 상위 Horizon 합)은
매일 자동으로 보지만, 그건 **수집값끼리의** 일치라 절반 유실은 양쪽에 똑같이 반영돼 통과한다.
외부 기준이 있어야만 잡힌다.

## 왜 장후 자동 실행에 넣지 않았나

거래소 REST 호출이 필요하고, 그것을 15:35~15:40 종료 예산 안에 넣으면 종료 절차가 네트워크에
의존하게 된다. 그리고 이 대조가 필요한 상황(파서 변경·백필 이후)은 매일이 아니다. 그래서
**사람이 부르는 도구**로 두고, 정기 점검 체크리스트(`dev_memory/NEXT_TODO.md`)가 부른다.

## 판정

행별로 비교하지 않는다 — 거래소 분봉과 수집 분봉은 장전 구간 포함 여부가 다를 수 있다.
**공통 분(minute)의 거래량 합 비율**만 본다. 1.0에서 멀면 파서나 수집 경로를 의심한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
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
from messiah.core.config import load_instance  # noqa: E402
from messiah.core.messages import Horizon  # noqa: E402
from messiah.data import backfill  # noqa: E402
from messiah.data.archiver import ParquetArchiver  # noqa: E402
from messiah.ops import session_guard  # noqa: E402

_DATA_DIR = Path("data") / "bars"
_LOG_DIR = Path("logs")

# 이 비율을 밑돌면 수집 경로를 의심한다. 2026-07-28~30 실측으로 WS 다중 레코드 유실 상태의
# 비율은 **0.49~0.52**였다 — 0.95는 그 사고를 확실히 잡으면서, 장전 프린트 처리 차이 같은
# 소소한 어긋남은 통과시키는 값이다(미검증 초기값).
WARN_RATIO = 0.95


def _parse_day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()  # noqa: DTZ007


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="아카이브 1분봉 거래량을 거래소 공식 분봉과 대조")
    p.add_argument("--date", type=_parse_day, default=None, help="하루만 대조")
    p.add_argument("--start", type=_parse_day, default=None)
    p.add_argument("--end", type=_parse_day, default=None)
    p.add_argument("--base-dir", default=str(_DATA_DIR))
    p.add_argument("--configs", default="configs")
    session_guard.add_force_intraday_argument(p)
    return p.parse_args()


def compare_day(
    archived_volumes: dict[str, int], official_volumes: dict[str, int]
) -> tuple[float | None, int, int, int]:
    """(비율, 공통 분 수, 아카이브 합, 공식 합) — 공통 분이 없으면 비율은 None.

    **공통 분만** 더한다. 거래소 분봉과 수집 분봉은 장전 구간 포함 여부가 다를 수 있고,
    그 차이를 거래량 결함으로 오인하면 매일 오탐이 난다.
    """
    common = sorted(set(archived_volumes) & set(official_volumes))
    if not common:
        return None, 0, 0, 0
    mine = sum(archived_volumes[m] for m in common)
    theirs = sum(official_volumes[m] for m in common)
    return (mine / theirs if theirs else None), len(common), mine, theirs


def main() -> int:
    args = _parse_args()
    session_guard.refuse_if_regular_session("거래량 대조(REST 호출)", force=args.force_intraday)

    if args.date is not None:
        start = end = args.date
    elif args.start is not None:
        start, end = args.start, (args.end or args.start)
    else:
        print("--date 또는 --start 가 필요하다", file=sys.stderr)
        return 2

    cfg = load_instance(args.configs)
    creds = KISCredentials.from_broker_config(cfg.broker)
    rds = redis.from_url(cfg.redis_url, decode_responses=True)
    client = KISRestClient(
        creds,
        token_daemon=RedisTokenDaemon(creds, rds),
        rate_limiter=RedisRateLimiter(1.0, rds),
    )
    archiver = ParquetArchiver(Path(args.base_dir))
    tick_size = Decimal(cfg.futures_tick_size)

    segments = backfill.front_month_days(start, end)
    targets = backfill.continuous_days(segments)
    print(f"대조 구간: {start} ~ {end} ({len(targets)}일)\n")

    suspicious: list[tuple[str, date, float]] = []
    results: list[tuple[str, date, float | None, int, int, int]] = []
    for symbol, day in targets:
        frame = archiver.read_day(symbol, Horizon.M1, day)
        if frame is None or frame.height == 0:
            print(f"  {symbol} {day}  아카이브 없음 — 건너뜀")
            continue
        official = backfill.fetch_day_bars(
            client.get_futureoption_minute_chart, symbol, day, tick_size
        )
        if not official:
            print(f"  {symbol} {day}  공식 분봉 0봉 — 대조 불가")
            continue

        mine = {
            row["bar_open_kst"].astimezone(official[0].bar_open_kst.tzinfo).strftime("%H:%M"): int(
                row["volume"]
            )
            for row in frame.iter_rows(named=True)
        }
        theirs = {b.bar_open_kst.strftime("%H:%M"): b.volume for b in official}
        ratio, common, mine_sum, theirs_sum = compare_day(mine, theirs)
        if ratio is None:
            print(f"  {symbol} {day}  공통 분 없음 — 대조 불가")
            continue

        mark = "OK" if ratio >= WARN_RATIO else "** 의심 **"
        print(
            f"  {symbol} {day}  비율 {ratio:.3f}  "
            f"(공통 {common}분 · 아카이브 {mine_sum:,} / 공식 {theirs_sum:,})  {mark}"
        )
        results.append((symbol, day, ratio, common, mine_sum, theirs_sum))
        if ratio < WARN_RATIO:
            suspicious.append((symbol, day, ratio))

    # 결과를 파일로 남긴다 (2026-08-05, 고도화 1) — 무결성 리포트가 이걸 읽어 **외부 대조**를
    # 1급 축으로 갖는다. REST 호출을 종료 절차에 넣지 않는다는 판단은 그대로지만, 그렇다고
    # "안 돌린 날"이 조용히 지나가서는 안 된다. 파일이 없으면 리포트가 `unmeasured`로 남긴다.
    for symbol, day, ratio, common, mine_sum, theirs_sum in results:
        out_path = _LOG_DIR / f"volume_check_{day.strftime('%Y%m%d')}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {
                    "date": day.isoformat(),
                    "symbol": symbol,
                    "ratio": ratio,
                    "common_minutes": common,
                    "archived_volume": mine_sum,
                    "official_volume": theirs_sum,
                    "warn_ratio": WARN_RATIO,
                    "ok": ratio is not None and ratio >= WARN_RATIO,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"  → {out_path}")

    if suspicious:
        print(f"\n의심 {len(suspicious)}일 — 그 날짜는 수집 당시 코드로 파싱된 값이다.")
        print("  재백필: python scripts/run_backfill.py --start <일> --end <일>")
        print("  재합성: python scripts/run_recompose.py --start <일> --end <일>")
        return 1

    print("\n전 구간 정상.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
