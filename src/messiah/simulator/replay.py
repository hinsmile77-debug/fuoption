"""Parquet 재생 — Master Plan Ver 2.0 §9 W9~11 "Digital Twin 시뮬레이터".

`ParquetArchiver`가 이미 `{symbol}/{horizon}/{date}.parquet`로 적재해 둔 완성봉을 그대로
읽어, 실제 운영에서 봉이 확정되던 순서(확정시각 = bar_open_kst + horizon초, 동률이면 짧은
Horizon 먼저 — 예: 09:35 확정 시점엔 09:34 1분봉이 09:30 5분봉보다 항상 먼저 확정됐다)로
정렬해 하나의 시퀀스로 합친다. 새로 계산하지 않고 이미 기록된 값을 그대로 재생하는 것이라
`bar_composer`가 실제로 만들었던 봉과 재생 결과가 항상 일치한다(재현성).

없는 파일(휴장일 등)은 조용히 건너뛴다 — 리플레이 구간에 데이터 공백이 있는 건 정상이다.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Sequence

import polars as pl

from messiah.core.messages import HORIZON_SECONDS, BarClosed, Horizon, bar_confirm_time
from messiah.data.archiver import ParquetArchiver


class ParquetBarReplaySource:
    """단일 심볼용 — 아카이브된 전 Horizon 완성봉을 확정시각 순으로 재생 가능한 리스트로 로드."""

    def __init__(
        self,
        base_dir: Path | str,
        symbol: str,
        horizons: Sequence[Horizon] | None = None,
    ) -> None:
        self._base_dir = Path(base_dir)
        self._symbol = symbol
        self._horizons = list(horizons) if horizons is not None else list(Horizon)

    def load(self, start_date: date, end_date: date) -> list[BarClosed]:
        """
        입력: [start_date, end_date] 양끝 포함 날짜 구간(KST 달력 기준 — bar_open_kst의
             날짜와 대응).
        계산: 존재하는 파일만 읽어 BarClosed로 복원한 뒤, (확정시각, Horizon초) 오름차순
             정렬. 확정시각이 같으면 Horizon초가 짧은 쪽이 먼저 — 실제 운영에서 굵은
             Horizon은 그걸 구성하는 가는 Horizon 봉들이 전부 확정된 뒤에야 확정되므로,
             재생도 그 인과 순서를 그대로 지켜야 FeatureEngine 등 하위 소비자가 실제와
             동일한 순서로 이벤트를 받는다.
        """
        archiver = ParquetArchiver(self._base_dir)
        bars: list[BarClosed] = []
        for horizon in self._horizons:
            for day in _date_range(start_date, end_date):
                # 경로를 직접 조립하지 않고 아카이버에 묻는다 — 장중에는 시간대 조각으로,
                # 장후에는 통합본 하나로 물리 배치가 달라진다(`data/archiver.py` "조각 쓰기").
                # 재생은 보통 지난 날짜(= 통합본)를 보지만, 장중 재생·당일 리허설도 가능해야
                # 하므로 두 배치를 다 아는 쪽에 위임한다.
                df = archiver.read_day(self._symbol, horizon, day)
                if df is None:
                    continue
                bars.extend(self._to_bars(df, horizon))

        bars.sort(key=lambda b: (bar_confirm_time(b), HORIZON_SECONDS[b.horizon]))
        return bars

    def _to_bars(self, df: pl.DataFrame, horizon: Horizon) -> list[BarClosed]:
        return [
            BarClosed(
                symbol=self._symbol,
                horizon=horizon,
                bar_open_kst=row["bar_open_kst"],
                o_ticks=row["o_ticks"],
                h_ticks=row["h_ticks"],
                l_ticks=row["l_ticks"],
                c_ticks=row["c_ticks"],
                volume=row["volume"],
                quality_ok=row["quality_ok"],
            )
            for row in df.iter_rows(named=True)
        ]


def _date_range(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)
