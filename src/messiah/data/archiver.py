"""완성봉(BarClosed) Parquet 적재 — Master Plan Ver 2.0 §9 "L1 DATA: Archiver(Parquet)".

Digital Twin(W9~11)이 날짜·심볼 단위로 바로 읽을 수 있게 {base_dir}/{symbol}/{date}.parquet로
파티셔닝한다. 1분봉은 심볼당 하루 최대 수백 행이라(정규장 405분) append마다 파일 전체를
읽어-합쳐-다시 쓰는 것으로 충분하다(배치 flush 최적화는 실제 처리량을 실측한 뒤 필요해지면 추가).

주의(2026-07-22 실측): polars는 tz-aware datetime 컬럼을 Parquet에 쓸 때 내부적으로 UTC로
정규화하고, 다시 읽을 때 zoneinfo로 "UTC" 존을 조회한다 — Windows에는 시스템 tzdata가 없어
`tzdata` 패키지가 설치돼 있지 않으면 읽기 단계에서 ZoneInfoNotFoundError가 난다
(pyproject.toml에 sys_platform=='win32' 조건부 의존성으로 추가함).
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from messiah.core.messages import BarClosed


class ParquetArchiver:
    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    def append_bar(self, bar: BarClosed) -> None:
        """
        계산: bar를 1행 DataFrame으로 만들어, 같은 (symbol, 날짜) 파일이 이미 있으면 읽어
             합친 뒤 bar_open_kst 기준 중복 제거(나중 값 유지)·정렬 후 덮어쓴다. 파일이
             없으면 새로 만든다.
        해석: 같은 분(bar_open_kst)에 대해 append_bar가 두 번 불려도(예: 재시작 후 재처리)
             마지막 값으로 덮어써지고 행이 두 번 쌓이지 않는다.
        """
        path = self._path_for(bar)
        path.parent.mkdir(parents=True, exist_ok=True)
        new_row = self._bar_to_frame(bar)

        if path.exists():
            combined = pl.concat([pl.read_parquet(path), new_row])
        else:
            combined = new_row

        combined = combined.unique(subset=["bar_open_kst"], keep="last").sort("bar_open_kst")
        combined.write_parquet(path)

    def _path_for(self, bar: BarClosed) -> Path:
        date_str = bar.bar_open_kst.strftime("%Y-%m-%d")
        return self._base_dir / bar.symbol / f"{date_str}.parquet"

    @staticmethod
    def _bar_to_frame(bar: BarClosed) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "symbol": [bar.symbol],
                "horizon": [bar.horizon.value],
                "bar_open_kst": [bar.bar_open_kst],
                "o_ticks": [bar.o_ticks],
                "h_ticks": [bar.h_ticks],
                "l_ticks": [bar.l_ticks],
                "c_ticks": [bar.c_ticks],
                "volume": [bar.volume],
                "quality_ok": [bar.quality_ok],
            }
        )
