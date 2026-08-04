"""장중 투자자매매동향 적재 — `raw.investor_flow.*`를 하루 1파일로 (2026-08-04 신설).

## 왜 필요한가

`InvestorFlowPoller`는 2026-07-27에 만들어졌지만 **어떤 스크립트에도 결선돼 있지 않았고**
`raw.investor_flow.*`를 구독하는 것도 없었다. 즉 이 프로젝트는 파생 수급을 한 건도 갖고
있지 않다. 게다가 KIS 장중 엔드포인트는 **당일 누적만** 준다 — 과거 조회가 없어서
`data/backfill.py`처럼 나중에 소급해 채울 방법이 아예 없다.

**못 받는 데이터는 지금 안 받으면 영원히 없다.** 그래서 이 아카이버가 먼저 있어야 폴러를
결선할 수 있다(폴러만 켜면 버스에 흘러갔다가 사라진다).

## 72개 필드를 전부 저장한다

지금 쓸 피처는 순매수 몇 개뿐이지만 원시 응답 전체를 남긴다. 나중에 "그때 그 필드도
받아둘 걸" 하는 순간이 오면 **되돌릴 방법이 없기 때문**이다 — 백필 가능한 봉 데이터와
성질이 다르다. 하루 3업종 × 1분 = 약 1,200행이라 저장 비용은 무시할 수 있다.

## 쓰기 방식

폴링마다 그날 전체를 다시 쓴다(원자적 교체). 행이 하루 1,200개 수준이라 비용이 없고,
프로세스가 어느 시점에 죽어도 직전 폴링까지가 온전히 남는다 — `data/archiver.py`가
조각 쓰기를 도입해야 했던 O(n²) 문제(1분봉 405행 × 매분 전체 재작성)와는 규모가 다르다.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Sequence

import polars as pl

from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_RAW, BusLike
from messiah.core.messages import InvestorFlowSnapshot
from messiah.core.timeutil import to_kst

# `core/timeutil.py`의 KST는 고정 오프셋 객체라 polars가 못 받는다 — IANA 이름을
# 쓴다(`data/archiver.py`와 동일).
_KST_ZONE_NAME = "Asia/Seoul"


class InvestorFlowArchiver:
    """`raw.investor_flow.*` 구독 → `{base}/{market}/{date}.parquet`.

    같은 (시각, 업종)이 두 번 들어오면 나중 값이 이긴다 — 재시작 후 같은 분을 다시 받는
    경우가 실제로 생기고, 그때 행이 두 배로 쌓이면 나중에 누적 계열이 틀어진다.
    """

    def __init__(self, base_dir: Path, market_code: str) -> None:
        self._base_dir = Path(base_dir)
        self._market_code = market_code
        self._rows: dict[tuple[str, str], dict[str, object]] = {}
        self._day: date | None = None

    @property
    def row_count(self) -> int:
        return len(self._rows)

    def path_for(self, day: date) -> Path:
        return self._base_dir / self._market_code / f"{day.isoformat()}.parquet"

    async def handle_snapshot(self, snapshot: InvestorFlowSnapshot) -> None:
        if not isinstance(snapshot, InvestorFlowSnapshot):
            return
        if snapshot.market_code != self._market_code:
            return

        kst = to_kst(snapshot.ts_utc)
        day = kst.date()
        if self._day is not None and day != self._day:
            # 날짜가 바뀌면 이전 날 버퍼를 비운다 — 안 그러면 다음 날 파일에 전날 행이 섞인다.
            self._rows.clear()
        self._day = day

        row: dict[str, object] = {
            "ts_kst": kst,
            "sector_code": snapshot.sector_code,
        }
        # 원시 응답의 output(1행)만 펼친다. 값은 전부 문자열로 오므로 float로 바꾸되,
        # 못 바꾸는 건 **버리지 않고** 문자열 그대로 둔다(모르는 필드를 조용히 없애지 않는다).
        for key, value in _output_row(snapshot.raw).items():
            try:
                row[key] = float(value)
            except (TypeError, ValueError):
                row[key] = str(value)

        self._rows[(kst.strftime("%H%M%S"), snapshot.sector_code)] = row
        self._flush()

    def _flush(self) -> None:
        if not self._rows or self._day is None:
            return
        path = self.path_for(self._day)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            frame = pl.DataFrame(list(self._rows.values()), infer_schema_length=None).sort(
                ["ts_kst", "sector_code"]
            )
            tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            frame.write_parquet(tmp)
            os.replace(tmp, path)
        except Exception as exc:  # noqa: BLE001 — 적재 실패가 수집 루프를 죽이면 안 됨(L22)
            mlog.log(
                "InvestorFlowArchiveError",
                f"수급 스냅샷 적재 실패: {exc}",
                market_code=self._market_code,
                rows=len(self._rows),
            )

    async def run_forever(self, bus: BusLike) -> None:
        await bus.subscribe([f"{TOPIC_RAW}.investor_flow.{self._market_code}"], self._dispatch)

    async def _dispatch(self, msg: object) -> None:
        if isinstance(msg, InvestorFlowSnapshot):
            await self.handle_snapshot(msg)


def _output_row(raw: object) -> dict[str, object]:
    """KIS 응답에서 지표 행 하나를 꺼낸다 — `output`이 list면 첫 행, dict면 그대로."""
    if not isinstance(raw, dict):
        return {}
    for key in ("output", "output1"):
        value = raw.get(key)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return dict(value[0])
        if isinstance(value, dict):
            return dict(value)
    return {}


def read_day(base_dir: Path, market_code: str, day: date) -> pl.DataFrame | None:
    """그날치를 읽어 `ts_kst`를 **이름대로 KST로** 되돌린다.

    polars는 tz-aware datetime을 Parquet에 쓸 때 UTC로 정규화한다(`data/archiver.py` 모듈
    docstring의 같은 실측) — 그대로 읽으면 컬럼 이름은 `ts_kst`인데 dtype은 UTC라 보는
    사람이 시각을 9시간 틀리게 읽는다. `bar_open_kst`를 읽을 때와 같은 규율로 되돌린다.
    """
    path = Path(base_dir) / market_code / f"{day.isoformat()}.parquet"
    if not path.exists():
        return None
    frame = pl.read_parquet(path)
    if "ts_kst" in frame.columns:
        frame = frame.with_columns(pl.col("ts_kst").dt.convert_time_zone(_KST_ZONE_NAME))
    return frame.sort(["ts_kst", "sector_code"])


def available_days(base_dir: Path, market_code: str) -> list[date]:
    directory = Path(base_dir) / market_code
    if not directory.is_dir():
        return []
    days: list[date] = []
    for path in directory.glob("*.parquet"):
        try:
            days.append(date.fromisoformat(path.stem))
        except ValueError:
            continue
    return sorted(days)


def sector_codes_seen(frames: Sequence[pl.DataFrame]) -> list[str]:
    """수집된 업종 목록 — 결선이 의도대로 됐는지 사후 확인용."""
    out: set[str] = set()
    for frame in frames:
        if "sector_code" in frame.columns:
            out.update(frame["sector_code"].to_list())
    return sorted(out)
