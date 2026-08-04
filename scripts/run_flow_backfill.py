"""일별 투자자매매동향 백필 — FL Feature의 데이터 확보.

설계·실측 근거는 `src/messiah/data/investor_flow_history.py` 모듈 docstring 참고.
요약하면 **KOSPI 현물 일별 수급**만 백필 가능하다 — 파생(선물/옵션) 수급은 장중
엔드포인트에만 있고 과거 조회가 없으며, 그 폴러는 어디에도 결선돼 있지 않아 이 프로젝트가
지금껏 한 건도 수집한 적이 없다.

받은 값이 전부 0이면(파생 업종코드를 넣었을 때의 응답 모양) 저장하지 않고 실패로 보고한다 —
rt_cd=0이라 조용히 "수급이 0인 날들"로 저장되면 나중에 피처가 통째로 죽는다.

사용:
    python scripts/run_flow_backfill.py                    # 2025-12-12 ~ 어제
    python scripts/run_flow_backfill.py --start 2024-01-01 # 더 길게(다년 소급 가능)
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
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
from messiah.core.timeutil import now_kst  # noqa: E402
from messiah.data import investor_flow_history as ifh  # noqa: E402

DEFAULT_PATH = Path("data") / "flow" / "kospi_daily.parquet"


def _parse_day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()  # noqa: DTZ007 — 날짜만 다루는 CLI 인자


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=_parse_day, default=date(2025, 12, 12))
    p.add_argument("--end", type=_parse_day, default=None, help="기본값 = 어제")
    p.add_argument("--out", default=str(DEFAULT_PATH))
    p.add_argument("--configs", default="configs")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    end = args.end or (now_kst().date() - timedelta(days=1))
    if args.start > end:
        print(f"[run_flow_backfill] start({args.start}) > end({end})", file=sys.stderr)
        return 2

    cfg = load_instance(args.configs)
    creds = KISCredentials.from_broker_config(cfg.broker)
    rds = redis.from_url(cfg.redis_url, decode_responses=True)
    client = KISRestClient(
        creds,
        token_daemon=RedisTokenDaemon(creds, rds),
        rate_limiter=RedisRateLimiter(1.0, rds),
    )

    print(f"KOSPI 현물 일별 수급 백필: {args.start} ~ {end}")
    rows = ifh.fetch_history(client.get_investor_flow_daily, start=args.start, end=end)
    if not rows:
        print("[run_flow_backfill] 0행 — 구간에 데이터가 없다", file=sys.stderr)
        return 3
    if ifh.looks_unsupported(rows):
        print(
            "[run_flow_backfill] 전 행·전 필드가 0 — 미지원 업종코드로 보인다(파생은 이 "
            "엔드포인트로 못 받는다). 저장하지 않는다.",
            file=sys.stderr,
        )
        return 4

    written = ifh.write(rows, Path(args.out))
    print(f"저장: {args.out}  {written}일  ({rows[0].day} ~ {rows[-1].day})")

    last = rows[-1]
    print("\n마지막 날 값 (단위: 계약/백만원):")
    for field, value in last.values.items():
        print(f"  {field:<22} {value:>16,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
