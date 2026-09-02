"""국면별 방향 채점 실행 — 제안 2순위 (2026-09-02 신설).

    python scripts/run_regime_direction_scorecard.py              # 마지막 거래일 기준 20거래일 창
    python scripts/run_regime_direction_scorecard.py --date 2026-09-02 --days 9
    python scripts/run_regime_direction_scorecard.py --no-reachability   # 천장 계산 생략(빠름)

## 무엇을 답하는가

"|S| 게이트를 낮출까"라는 물음에 2026-09-02 반사실 측정이 답한 것은 **임계가 아니라 국면**
이었다: 고변동 국면 34사이클의 방향 적중률이 21%(이항 p=0.0004)였고, 그 손실이 실현되지
않은 이유는 그 국면의 실효 천장(0.188)이 게이트(0.20)에 산술적으로 못 닿기 때문이었다.
**우연이 최악의 국면을 막고 있었다.** 이 스크립트가 그 두 숫자를 매 거래일 다시 센다.

산출은 `logs/regime_direction_<날짜>.json` 하나이고, 화면 ⑤(`ui/app.py`)가 그것을 읽는다.
채점 로직·판정 문구는 전부 `models/regime_direction.py`에 있다 — 이 파일은 인자 해석과
입출력만 한다(`daily_integrity_report.py`와 같은 껍데기 규율).

## 어디에 붙어 있나

`run_postmarket.py`의 5/7단계다. **문서에 적는 것으로는 안 돈다**는 것이 이 저장소의 실측
교훈이라(그 파일 docstring "이틀 연속 안 돌았다"), 처음부터 배치에 넣는다. 안 돌린 날은
화면 ⑤가 "미측정"이라 적는다 — 측정 불능이 조용히 지나가지 않는다.

`--reachability`(기본 켬)는 전 이력을 다시 레이블링하지만 실측 **6.8초**다(2026-09-02,
1분봉 71,088봉). `run_vol_scorecard.py`와 달리 피처를 만들지 않아 싸다 — 끄는 스위치를 남긴
것은 예산 때문이 아니라, 아카이브가 없는 환경(테스트·새 인스턴스)에서도 방향 채점만은
돌아야 하기 때문이다.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from messiah.backtest.harness import aggregate_to_horizon  # noqa: E402
from messiah.core import symbol_resolution  # noqa: E402
from messiah.core.event_calendar import EventCalendar  # noqa: E402
from messiah.core.messages import BarClosed, Horizon  # noqa: E402
from messiah.core.timeutil import now_kst  # noqa: E402
from messiah.data import backfill  # noqa: E402
from messiah.data.archiver import ParquetArchiver  # noqa: E402
from messiah.models import regime_direction  # noqa: E402
from messiah.models.label_geometry import (  # noqa: E402
    LabelGeometry,
    regime_reachability,
    summarise_regime_reachability,
)
from messiah.models.labeling import label_and_weight  # noqa: E402
from messiah.ops import session_guard  # noqa: E402
from messiah.risk.cost_model import CostModel  # noqa: E402
from messiah.strategy.futures.aggregator import REGIME_WEIGHTS  # noqa: E402

_HORIZON = Horizon.M30  # live 번들이 30m 한 종이다 — 판단이 나가는 유일한 축
_DATA_DIR = Path("data") / "bars"
_LOG_DIR = Path("logs")

# ATR(14)를 데우는 데 필요한 앞선 거래일. 30m은 하루 15봉이라 하루면 충분하지만, 결측일·
# 반장을 감안해 3일 잡는다(워밍업이 모자라면 그날 첫 사이클들이 통째로 미채점이 된다).
WARMUP_TRADING_DAYS = 3


def _parse_day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()  # noqa: DTZ007


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="국면별 방향 채점")
    p.add_argument("--date", type=_parse_day, default=None, help="창의 마지막 날(기본: 오늘)")
    p.add_argument(
        "--days",
        type=int,
        default=regime_direction.MIN_TRADING_DAYS,
        help=f"롤링 창의 거래일 수(기본 {regime_direction.MIN_TRADING_DAYS} — R18 섀도 계측 기간)",
    )
    p.add_argument("--base-dir", default=str(_DATA_DIR))
    p.add_argument("--log-dir", default=str(_LOG_DIR))
    p.add_argument("--symbol", default=None, help="명시하면 이것이 이긴다(기본: 런타임 기록)")
    p.add_argument("--reachability", dest="reachability", action="store_true", default=True)
    p.add_argument("--no-reachability", dest="reachability", action="store_false")
    p.add_argument("--force-intraday", action="store_true")
    return p.parse_args()


def _trading_days(calendar: EventCalendar, end: date, count: int) -> list[date]:
    days: list[date] = []
    cursor = end
    while len(days) < count:
        if calendar.is_trading_day(cursor):
            days.append(cursor)
        cursor -= timedelta(days=1)
        if (end - cursor).days > count * 4 + 30:  # 휴장이 길어도 무한히 뒤로 가지 않는다
            break
    return sorted(days)


_WANTED_TAGS = ('"DecisionEmitted"', '"RegimeClassified"', '"MetaGateEvaluated"')


def _load_payloads(log_dir: Path, days: list[date]) -> list[dict]:
    """`logs/g2_daily_<YYYYMMDD>.log`에서 채점에 쓰는 세 태그만 — 파싱 실패는 건너뛴다.

    깨진 줄에서 멈추면 그날 이후가 통째로 안 세어진다. 로그는 추가 전용이고 프로세스가
    죽는 순간 마지막 줄이 잘릴 수 있다(`ops/integrity_report`의 같은 처리).

    국면 태그를 함께 읽는 이유는 옛 로그 때문이다 — `regime_direction.build_regime_lookup()`
    docstring 참고. 새 로그만 남는 날이 오면 이 조인은 0건이 되고, 그 사실이 산출물의
    `regime_sources`에 그대로 찍힌다.
    """
    payloads: list[dict] = []
    for day in days:
        path = log_dir / f"g2_daily_{day.strftime('%Y%m%d')}.log"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = line.strip()
            if not line.startswith("{") or not any(tag in line for tag in _WANTED_TAGS):
                continue
            try:
                payloads.append(json.loads(line))
            except ValueError:
                continue
    return payloads


def _load_bars(
    archiver: ParquetArchiver, symbols: set[str], days: list[date], warmup: list[date]
) -> dict[str, list[BarClosed]]:
    """심볼별 30m 봉 — 워밍업일을 앞에 붙여 ATR가 그날 첫 사이클부터 서 있게 한다."""
    out: dict[str, list[BarClosed]] = {}
    for symbol in symbols:
        bars: list[BarClosed] = []
        for day in warmup + days:
            bars.extend(archiver.read_day_bars(symbol, _HORIZON, day))
        # 같은 봉이 정본과 샤드에 함께 있을 수 있다 — 확정 시각으로 중복을 접는다.
        deduped = {bar.bar_open_kst: bar for bar in bars}
        out[symbol] = [deduped[key] for key in sorted(deduped)]
    return out


def _reachability_summary(archiver: ParquetArchiver, end: date) -> dict[str, object] | None:
    """국면별 실효 천장 — 레이블 천장 × `REGIME_WEIGHTS`(`label_geometry` 모듈 docstring).

    실패하면 None을 돌려주고 **사유를 찍는다**. 이 값이 없다고 방향 채점까지 죽으면 안 된다
    (부가 축의 실패가 본 축을 막지 않는다).
    """
    try:
        m1_bars, _ = backfill.load_continuous_series(
            archiver, backfill.front_month_days(date(2025, 12, 12), end), symbol_out="K200MFC"
        )
        if not m1_bars:
            print("  실효 천장: 연속 시계열이 비어 있다 — run_backfill.py를 먼저 돌릴 것")
            return None
        bars = aggregate_to_horizon(m1_bars, _HORIZON)
        cost = CostModel().estimate_round_trip_from_bars(bars, qty=1).total_ticks
        labels = label_and_weight(bars, cost_ticks=cost)
        geometry = LabelGeometry.build(labels, cost_ticks=cost)
        cards = regime_reachability([geometry], REGIME_WEIGHTS)
        for card in cards:
            print("  " + card.format_lines()[0])
        return summarise_regime_reachability(cards)
    except Exception as exc:  # noqa: BLE001 — 사유를 남기고 계속한다
        print(f"  실효 천장: 계산 실패 — {exc.__class__.__name__}: {exc}")
        return None


def main() -> int:
    args = _parse_args()
    session_guard.refuse_if_regular_session("국면별 방향 채점", force=args.force_intraday)

    calendar = EventCalendar.from_file()
    end = args.date or now_kst().date()
    days = _trading_days(calendar, end, args.days)
    if not days:
        print("거래일을 못 정했다 — 달력을 확인할 것", file=sys.stderr)
        return 2
    warmup = _trading_days(calendar, days[0] - timedelta(days=1), WARMUP_TRADING_DAYS)

    log_dir, archiver = Path(args.log_dir), ParquetArchiver(Path(args.base_dir))
    payloads = _load_payloads(log_dir, days)
    lookup = regime_direction.build_regime_lookup(payloads)
    records, sources = regime_direction.parse_decisions(payloads, regime_lookup=lookup)
    print(f"=== 국면별 방향 채점 {days[0]} ~ {days[-1]} ({len(days)}거래일) ===")
    print(
        f"  판단 {len(records)}건 채점 대상 — 국면 출처 self={sources['self']} "
        f"joined={sources['joined']} · 제외 no_score={sources['no_score']} "
        f"no_regime={sources['no_regime']}"
    )
    if not records:
        print("  채점 가능한 판단 0건 — 그 구간 로그에 국면도 점수도 없다")
        return 1

    symbol, origin = symbol_resolution.resolve_for_tools(days[-1], explicit=args.symbol)
    bars_by_symbol = _load_bars(archiver, {r.symbol for r in records}, days, warmup)
    outcomes, skipped = regime_direction.resolve_outcomes(records, bars_by_symbol)
    cards = regime_direction.score_regimes(outcomes)
    for line in regime_direction.format_cards(cards, skipped):
        print(line)

    reachability = _reachability_summary(archiver, days[-1]) if args.reachability else None
    out = regime_direction.write_scorecard(
        cards,
        skipped,
        symbol=symbol,
        day=days[-1],
        log_dir=log_dir,
        reachability=reachability,
        sources=sources,
    )
    print(f"  → {out} (심볼 {symbol} · {origin})")
    # 종료코드 1 = "표시할 이상이 있다" — 작업 스케줄러가 사람 눈 없이도 안다
    # (`daily_integrity_report.py`와 같은 관례).
    return 1 if any(card.is_flagged for card in cards) else 0


if __name__ == "__main__":
    raise SystemExit(main())
