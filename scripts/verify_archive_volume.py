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
from dataclasses import dataclass
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

# 공식에는 있는데 우리에겐 없는 분이 이만큼을 넘으면 **수집이 끊긴 것**이다 (2026-08-07 P0-4).
#
# `WARN_RATIO`는 "받은 것이 정확한가"를 재고 이 값은 "받아야 할 것을 다 받았나"를 잰다.
# 2026-08-07에 수집이 13:41에 죽어 115분이 없었는데 비율은 0.998이었다 — 없는 분은 애초에
# 비교 대상이 아니었기 때문이다. 두 축이 따로 필요하다는 것이 그날의 교훈이다.
#
# 20분: `ops/series_coverage.py`의 꼬리 구멍 하한·`integrity_report`의
# `bar_tail_gap_minutes`와 같은 값이다. 세 곳이 같은 질문("얼마나 비어야 사고인가")에
# 답하므로 임계도 같아야 한다 — 다르면 어느 축은 울고 어느 축은 조용한 날이 생긴다.
MISSING_MINUTES_LIMIT = 20

# **머리 미수집은 한 분도 봐주지 않는다** (2026-08-10 B-1).
#
# 위 20분은 "장중에 얼마나 비어야 사고인가"에 답하는 값이다. 그런데 **아침에 늦게 뜬 것**은
# 다른 사건이고 처방도 다르다 — 장중 구멍은 재기동·회선을 의심하지만, 머리 구멍은 스케줄러와
# 기동 창을 의심한다.
#
# 2026-08-10이 그 차이를 실측으로 보여줬다: 08:20 트리거가 기동 창에 막혀 08:58에야 떴고
# **미수집 13분**이 나왔는데, 임계 20분 아래라 `ok: true`였다. 그날 이 축은 잘림을 본
# **유일한 축**이었는데도 조용했다.
#
# 0이 안전한 근거: 정상일(2026-08-04·08-07)은 공통 410분 = 공식 410분으로 미수집이 0이었다.
# 우리 첫 봉과 거래소 첫 분봉이 둘 다 08:45이기 때문이다(`series_expectation.FIRST_DATA_KST`
# 와 같은 사실). 한 분이라도 비면 그날 아침에 무슨 일이 있었다는 뜻이다.
HEAD_MISSING_MINUTES_LIMIT = 0


@dataclass(frozen=True)
class DayComparison:
    """하루치 거래량 대조 결과.

    5-튜플에서 dataclass로 바꾼 이유(2026-08-10 B-1): 값이 여덟 개가 되면서 호출측이
    `_ratio, _common, _mine, _theirs, missing = ...`처럼 자리로 세게 됐다. 그 형태는
    필드를 하나 더 넣을 때마다 모든 호출측이 조용히 어긋날 수 있다.
    """

    ratio: float | None
    common_minutes: int
    archived_volume: int
    official_volume: int
    missing_minutes: int
    # 미수집 분을 **어디가 빈 것인지**로 나눈다 — 같은 숫자라도 처방이 다르다.
    head_missing_minutes: int
    middle_missing_minutes: int
    tail_missing_minutes: int

    @property
    def ok(self) -> bool:
        """머리는 0분, 중간·꼬리는 20분, 비율은 0.95 — 세 질문에 각각 답한다."""
        if self.ratio is None or self.ratio < WARN_RATIO:
            return False
        if self.head_missing_minutes > HEAD_MISSING_MINUTES_LIMIT:
            return False
        return self.middle_missing_minutes + self.tail_missing_minutes <= MISSING_MINUTES_LIMIT


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
) -> DayComparison:
    """하루치 대조 — 공통 분이 없으면 `ratio`가 None.

    **공통 분만** 더한다. 거래소 분봉과 수집 분봉은 장전 구간 포함 여부가 다를 수 있고,
    그 차이를 거래량 결함으로 오인하면 매일 오탐이 난다.

    ## 그런데 공통 분만 보면 "구간이 잘린 것"을 못 본다 (2026-08-07 P0-4)

    그날 13:41에 수집이 죽어 봉 115분이 없었는데 이 대조는 **`비율 0.998 · 전 구간 정상`**
    을 찍었다 — 없는 115분은 애초에 비교 대상이 아니었기 때문이다. 비율은 "받은 것이
    정확한가"를 재는 값이고 그건 여전히 옳지만, **"받아야 할 것을 다 받았나"는 아무도
    묻지 않았다.** 그래서 `공식에는 있는데 아카이브에 없는 분 수`를 함께 돌려준다.
    반대 방향(아카이브에만 있는 분)은 장전 구간 차이라 정상이므로 세지 않는다.

    ## 그리고 그 한 숫자로는 "어디가 빈 것인지"를 못 본다 (2026-08-10 B-1)

    2026-08-10에 미수집 **13분**이 나왔다. 임계 20분 아래라 `ok: true`였고, 그날 이 축이
    잘림을 본 **유일한 축**이었는데도 조용히 지나갔다. 13분은 전부 아침(08:45~08:58)이었고,
    그건 "장중에 13분 빠진 날"과 전혀 다른 사건이다 — 전자는 스케줄러를, 후자는 회선을
    의심해야 한다.

    그래서 미수집을 세 구간으로 나눈다. 기준은 **아카이브의 첫/마지막 분**이다:
    그보다 이른 공식 분은 머리, 늦은 것은 꼬리, 사이에 낀 것은 중간.
    """
    archived = set(archived_volumes)
    official = set(official_volumes)
    common = sorted(archived & official)
    absent = sorted(official - archived)
    if not common:
        # 공통 분이 없으면 머리/꼬리를 가를 기준선 자체가 없다 — 전부 미수집이되 구간
        # 판정은 하지 않는다(모르는 것을 아는 척하지 않는다).
        return DayComparison(None, 0, 0, 0, len(absent), 0, 0, 0)

    first, last = min(archived), max(archived)
    head = sum(1 for minute in absent if minute < first)
    tail = sum(1 for minute in absent if minute > last)
    mine = sum(archived_volumes[m] for m in common)
    theirs = sum(official_volumes[m] for m in common)
    return DayComparison(
        ratio=(mine / theirs if theirs else None),
        common_minutes=len(common),
        archived_volume=mine,
        official_volume=theirs,
        missing_minutes=len(absent),
        head_missing_minutes=head,
        middle_missing_minutes=len(absent) - head - tail,
        tail_missing_minutes=tail,
    )


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
    results: list[tuple[str, date, DayComparison]] = []
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
        result = compare_day(mine, theirs)
        if result.ratio is None:
            print(f"  {symbol} {day}  공통 분 없음 — 대조 불가")
            continue

        # 비율과 미수집은 **다른 사고**다: 비율은 "받은 것이 정확한가", 미수집은 "받아야
        # 할 것을 다 받았나". 2026-08-07엔 전자가 0.998로 정상이고 후자가 114분이었다.
        # 그리고 미수집 안에서도 **머리와 나머지가 다른 사고**다(2026-08-10 B-1).
        mark = "OK" if result.ratio >= WARN_RATIO else "** 의심 **"
        if result.head_missing_minutes > HEAD_MISSING_MINUTES_LIMIT:
            mark = f"** 아침 미수집 {result.head_missing_minutes}분 **"
        elif result.missing_minutes > MISSING_MINUTES_LIMIT:
            mark = f"** 미수집 {result.missing_minutes}분 **"
        breakdown = (
            f" · 미수집 머리 {result.head_missing_minutes}/중간 "
            f"{result.middle_missing_minutes}/꼬리 {result.tail_missing_minutes}분"
            if result.missing_minutes
            else ""
        )
        print(
            f"  {symbol} {day}  비율 {result.ratio:.3f}  "
            f"(공통 {result.common_minutes}분 · 공식 {len(theirs)}분 · "
            f"아카이브 {result.archived_volume:,} / 공식 {result.official_volume:,}"
            f"{breakdown})  {mark}"
        )
        results.append((symbol, day, result))
        if not result.ok:
            suspicious.append((symbol, day, result.ratio))

    # 결과를 파일로 남긴다 (2026-08-05, 고도화 1) — 무결성 리포트가 이걸 읽어 **외부 대조**를
    # 1급 축으로 갖는다. REST 호출을 종료 절차에 넣지 않는다는 판단은 그대로지만, 그렇다고
    # "안 돌린 날"이 조용히 지나가서는 안 된다. 파일이 없으면 리포트가 `unmeasured`로 남긴다.
    for symbol, day, result in results:
        out_path = _LOG_DIR / f"volume_check_{day.strftime('%Y%m%d')}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {
                    "date": day.isoformat(),
                    "symbol": symbol,
                    "ratio": result.ratio,
                    "common_minutes": result.common_minutes,
                    "archived_volume": result.archived_volume,
                    "official_volume": result.official_volume,
                    # 공식에는 있는데 우리에겐 없는 분 (2026-08-07 P0-4) — `ratio`가 못 보는 축.
                    "missing_minutes": result.missing_minutes,
                    # 그 안에서 **어디가 빈 것인지** (2026-08-10 B-1) — 머리는 스케줄러를,
                    # 중간·꼬리는 회선·프로세스를 의심하게 하는 다른 사건이다.
                    "head_missing_minutes": result.head_missing_minutes,
                    "middle_missing_minutes": result.middle_missing_minutes,
                    "tail_missing_minutes": result.tail_missing_minutes,
                    "warn_ratio": WARN_RATIO,
                    "missing_minutes_limit": MISSING_MINUTES_LIMIT,
                    "head_missing_minutes_limit": HEAD_MISSING_MINUTES_LIMIT,
                    "ok": result.ok,
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
