"""과거 1분봉 백필 실행 — KIS 선물옵션 분봉조회로 월물 체인을 거슬러 아카이브를 채운다.

설계·실측 근거는 `src/messiah/data/backfill.py` 모듈 docstring 참고. 이 스크립트가 하는 일:

  1. 월물 구간 계산 → 각 월물의 만기 규칙을 **거래소 일봉과 1회 대조**(가정을 조용히 안 믿음)
  2. (심볼, 날짜)마다 분봉을 페이징으로 완주 → `ParquetArchiver.write_day()`로 통합본 교체
  3. 0봉인 날은 휴장으로 보고 목록에 남긴다 — 달력을 안 믿는 대신 결과로 확인한다

**기존 아카이브를 덮어쓴다.** 그게 목적이다 — 2026-07-24~08-03 수집분은 WS 다중 레코드
유실로 거래량이 실제의 절반이었고 종가도 20~29% 어긋나 있었다(`data/normalizer.py` 모듈
docstring 실측표). 같은 날을 거래소 공식 값으로 갈아끼워야 백필 구간과 수집 구간의 경계에
인공적인 구조 변화가 안 생긴다.

토큰·유량은 Redis로 라이브 세션과 공유한다(`RedisTokenDaemon`/`RedisRateLimiter`) — 별도
토큰 발급은 403을 부르고(재발급 제한), 유량을 따로 쓰면 라이브 수집을 밀어낸다.

사용:
    python scripts/run_backfill.py                      # 2025-12-12 ~ 어제
    python scripts/run_backfill.py --start 2026-07-24   # 구간 지정
    python scripts/run_backfill.py --dry-run            # 계획만 출력(API 호출 없음)
    python scripts/run_backfill.py --skip-existing      # 이미 있는 날은 건너뜀(재개용)
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx  # noqa: E402
import redis  # noqa: E402
from messiah.broker.kis.credentials import KISCredentials  # noqa: E402
from messiah.broker.kis.redis_rate_limiter import RedisRateLimiter  # noqa: E402
from messiah.broker.kis.redis_token_cache import RedisTokenDaemon  # noqa: E402
from messiah.broker.kis.rest_client import KISRestClient  # noqa: E402
from messiah.core.config import load_instance  # noqa: E402
from messiah.core.messages import Horizon  # noqa: E402
from messiah.core.timeutil import now_kst  # noqa: E402
from messiah.data import backfill  # noqa: E402
from messiah.data.archiver import ParquetArchiver  # noqa: E402

_DATA_DIR = Path("data") / "bars"

# 소급 한계 — 2026-08-04 실측: A05601(1월물)까지는 응답이 오고 A05512 이하는 빈 응답이다.
# A05601이 근월이던 구간의 첫날 = 12월물 만기(2025-12-11) 다음 거래일.
_EARLIEST_START = date(2025, 12, 12)


# 일시적 네트워크/서버 장애 재시도 — 835회 호출을 한 번에 도는 작업이라 1회 실패로 전체가
# 멈추면 안 된다(2026-08-04 첫 실행이 13일째에 `RemoteProtocolError: Server disconnected`로
# 중단). `fetch_day_bars()`가 예외를 그대로 올리는 계약이라(모듈 docstring) 재시도 정책은
# 호출측인 여기가 정한다.
_RETRY_ERRORS = (httpx.TransportError, httpx.HTTPStatusError)
_RETRY_ATTEMPTS = 4
_RETRY_BACKOFF_SECONDS = 3.0


def _fetch_with_retry(fetch, symbol: str, day: date, tick_size: Decimal):
    last: Exception | None = None
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            return backfill.fetch_day_bars(fetch, symbol, day, tick_size)
        except _RETRY_ERRORS as exc:
            last = exc
            if attempt == _RETRY_ATTEMPTS:
                break
            wait = _RETRY_BACKOFF_SECONDS * attempt
            print(
                f"      재시도 {attempt}/{_RETRY_ATTEMPTS - 1} — {exc.__class__.__name__}: "
                f"{exc} ({wait:.0f}초 대기)",
                flush=True,
            )
            time.sleep(wait)
    raise RuntimeError(f"{symbol} {day}: {_RETRY_ATTEMPTS}회 재시도 후에도 실패") from last


def _parse_day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()  # noqa: DTZ007 — 날짜만 다루는 CLI 인자


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=_parse_day, default=_EARLIEST_START)
    p.add_argument(
        "--end",
        type=_parse_day,
        default=None,
        help="기본값 = 어제. 오늘은 라이브 수집이 조각을 쓰는 중이라 백필 대상이 아니다.",
    )
    p.add_argument("--base-dir", default=str(_DATA_DIR))
    p.add_argument("--configs", default="configs")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--skip-existing",
        action="store_true",
        help="통합본이 이미 있는 날은 건너뛴다(중단 후 재개용). 기본은 전부 덮어쓴다 — "
        "오염된 수집분을 갈아끼우는 것이 이 스크립트의 목적이기 때문.",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    today = now_kst().date()
    end = args.end or (today - timedelta(days=1))

    if end >= today:
        print(
            f"[run_backfill] 거부: --end({end})가 오늘 이후 — 오늘은 라이브 수집이 조각 파일을 "
            f"쓰는 중이고 write_day()가 그 조각을 지운다(그날 수집분 소실).",
            file=sys.stderr,
        )
        return 2
    if args.start < _EARLIEST_START:
        print(
            f"[run_backfill] 경고: --start({args.start})가 실측 소급 한계"
            f"({_EARLIEST_START})보다 이르다 — 그 이전 월물은 빈 응답만 온다.",
            file=sys.stderr,
        )

    segments = backfill.front_month_days(args.start, end)
    targets = backfill.continuous_days(segments)
    print(f"백필 구간: {args.start} ~ {end}")
    for seg in segments:
        print(f"  {seg.symbol}  {seg.start} ~ {seg.end}  ({len(seg.days)}일)")
    print(
        f"총 {len(targets)}일 · 예상 호출 약 {len(targets) * 5}회 (모의투자 1건/초 ≈ "
        f"{len(targets) * 5 / 60:.0f}분)"
    )

    if args.dry_run:
        print("\n--dry-run — 여기까지.")
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
    tick_size = Decimal(cfg.futures_tick_size)
    print(f"계좌={creds.account_no} is_mock={creds.is_mock} tick_size={tick_size}\n")

    print("만기 규칙 대조 (거래소 일봉):")
    for seg in segments:
        expected = backfill.monthly_expiry(seg.end.year, seg.end.month)
        if seg.end < expected:
            print(f"  {seg.symbol}  구간이 만기 전에 끝남({seg.end}) — 대조 생략")
            continue
        ok, actual = backfill.verify_expiry_against_chart(
            client.get_futureoption_daily_chart, seg.symbol, expected
        )
        mark = "일치" if ok else ("확인 불가" if actual is None else f"불일치(실제 {actual})")
        print(f"  {seg.symbol}  만기 계산 {expected} → {mark}")
        if not ok and actual is not None:
            print(
                f"[run_backfill] 중단: {seg.symbol}의 실제 마지막 거래일이 계산과 다르다 — "
                f"롤오버 경계가 어긋나면 연속 시계열 전체가 틀어진다.",
                file=sys.stderr,
            )
            return 3

    print("\n백필 시작")
    written_days = 0
    written_rows = 0
    empty_days: list[tuple[str, date]] = []
    failed_days: list[tuple[str, date, str]] = []
    for index, (symbol, day) in enumerate(targets, start=1):
        if args.skip_existing and archiver.read_day(symbol, Horizon.M1, day) is not None:
            continue
        # 하루가 끝내 안 되면 그 하루만 포기하고 계속한다 — 1일 때문에 나머지 160일을
        # 버리면 안 되고, 조용히 넘어가면 그 결손을 나중에 시장 상태로 오인한다. 실패
        # 목록은 끝에 모아 출력하고 종료코드로도 드러낸다(재실행은 --skip-existing).
        try:
            bars = _fetch_with_retry(client.get_futureoption_minute_chart, symbol, day, tick_size)
        except RuntimeError as exc:
            failed_days.append((symbol, day, str(exc)))
            print(f"  [{index}/{len(targets)}] {symbol} {day}  실패 — 건너뜀")
            continue
        if not bars:
            empty_days.append((symbol, day))
            print(f"  [{index}/{len(targets)}] {symbol} {day}  0봉 (휴장 추정)")
            continue
        rows = archiver.write_day(symbol, Horizon.M1, bars)
        written_days += 1
        written_rows += rows
        first = bars[0].bar_open_kst.strftime("%H:%M")
        last = bars[-1].bar_open_kst.strftime("%H:%M")
        print(f"  [{index}/{len(targets)}] {symbol} {day}  {rows}봉  {first}~{last}")

    # 롤 겹침 — 나가는 월물의 마지막 날에 **들어오는 월물**도 하루치 받아둔다. 이게 있어야
    # 두 계약의 basis를 같은 날 관측한 값으로 잴 수 있고, 연속 시계열의 후방조정이 성립한다
    # (`data/backfill.roll_overlap_targets` docstring). 롤 1회당 1일이라 비용이 작다.
    overlap = backfill.roll_overlap_targets(segments)
    if overlap:
        print(f"\n롤 겹침 확보 — {len(overlap)}일")
        for symbol, day in overlap:
            if args.skip_existing and archiver.read_day(symbol, Horizon.M1, day) is not None:
                continue
            try:
                bars = _fetch_with_retry(
                    client.get_futureoption_minute_chart, symbol, day, tick_size
                )
            except RuntimeError as exc:
                failed_days.append((symbol, day, str(exc)))
                print(f"  {symbol} {day}  실패 — 건너뜀")
                continue
            if not bars:
                print(f"  {symbol} {day}  0봉 — 그날 이 월물은 거래가 없었다(롤 조정 불가)")
                continue
            rows = archiver.write_day(symbol, Horizon.M1, bars)
            written_rows += rows
            print(f"  {symbol} {day}  {rows}봉 (겹침)")

    print(f"\n완료 — {written_days}일 / {written_rows}봉 기록")
    if empty_days:
        print(
            f"0봉(휴장 추정) {len(empty_days)}일: "
            + ", ".join(d.isoformat() for _, d in empty_days)
        )
    if failed_days:
        print(f"\n실패 {len(failed_days)}일 — `--skip-existing`으로 재실행할 것:")
        for symbol, day, reason in failed_days:
            print(f"  {symbol} {day}  {reason}")
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
