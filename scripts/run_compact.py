"""장중 시간대 조각 → 하루 1개 통합본 (2026-08-07 P1-1).

## 왜 별도 스크립트인가

통합은 원래 `run_l1_daily.py`의 **종료 시퀀스**에만 있었다(`_compact_archive`). 그런데
그 시퀀스는 프로세스가 살아서 15:35에 도달해야 돈다 — **죽으면 통째로 안 돈다.**

2026-08-07이 그랬다. 13:41에 수집이 죽어 `data/bars/A05608/1m/2026-08-07`이 시간대 조각
6개인 채로 남았고, 다른 날은 전부 `2026-08-07.parquet` 일자 파일이었다. 데이터가 사라진
것은 아니지만(`read_day()`가 조각도 읽는다) **물리 배치가 다른 날과 다르다** — Digital
Twin·백테스트 하니스·Replay는 `{date}.parquet`를 직접 여는 코드를 갖고 있고, 그 경로들이
그날만 조용히 빈손으로 돌아온다(`data/archiver.py`의 `compact_day()` docstring).

그래서 장후 절차(`run_postmarket.py`)가 **재합성보다 먼저** 이것을 돌린다. 08-06에
"장후 절차는 프로세스가 죽어도 돌아야 한다"고 자동화한 그 판단의 연장이다.

## 멱등이다

이미 통합된 날에 다시 돌리면 조각이 없으므로 0행을 반환하고 끝난다 — 종료 시퀀스가
정상적으로 돈 날에 장후 절차가 또 돌아도 아무 일도 일어나지 않는다.

사용:
    python scripts/run_compact.py --symbol A05608                 # 오늘
    python scripts/run_compact.py --symbol A05608 --date 2026-08-07
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.core.messages import Horizon  # noqa: E402
from messiah.core.timeutil import now_kst  # noqa: E402
from messiah.data.archiver import ParquetArchiver  # noqa: E402

_DATA_DIR = Path("data") / "bars"


def _parse_day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()  # noqa: DTZ007 — 날짜만 다루는 CLI 인자


def main() -> int:
    parser = argparse.ArgumentParser(description="장중 조각 → 일자 통합본")
    parser.add_argument("--symbol", default="A05608")
    parser.add_argument("--date", type=_parse_day, default=None, help="기본: 오늘(KST)")
    parser.add_argument("--base-dir", default=str(_DATA_DIR))
    args = parser.parse_args()

    day = args.date or now_kst().date()
    archiver = ParquetArchiver(Path(args.base_dir))

    print(f"조각 통합 — {args.symbol} {day.isoformat()}")
    total = 0
    failed: list[str] = []
    for horizon in Horizon:
        try:
            rows = archiver.compact_day(args.symbol, horizon, day)
        except Exception as exc:  # noqa: BLE001 — 한 Horizon 실패가 나머지를 막지 않는다(L22)
            failed.append(f"{horizon.value}: {exc}")
            print(f"  {horizon.value}  실패 — {exc}")
            continue
        if rows:
            total += rows
            print(f"  {horizon.value}  통합 {rows}행")

    if failed:
        # 조각은 그대로 남아 `read_day()`로 읽히므로 데이터 손실은 아니다 — 그래도
        # 조용히 넘어가면 "왜 그날만 일자 파일이 없지"를 나중에 다시 조사하게 된다.
        print(f"\n{len(failed)}개 Horizon 통합 실패 — 조각은 남아 있어 읽기는 가능")
        return 1
    if total == 0:
        print("  통합할 조각 없음 — 이미 통합됐거나 그날 데이터가 없다(멱등)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
