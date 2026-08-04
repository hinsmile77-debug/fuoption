"""옵션체인 적재 — `raw.option_chain.*`를 시리즈별·하루 1파일로 (2026-08-04 신설).

`InvestorFlowArchiver`와 같은 이유로 **폴러보다 먼저** 만들었다: 구독자가 없는 상태로 폴러를
켜면 스냅샷이 버스에 흘러갔다가 그대로 사라진다. 파생 수급에서 실제로 그 순서를 틀려 7개월을
날린 전례가 있다.

## 응답 전체를 보존한다 — output1/2/3 전부

`get_quote(O)` 응답은 세 덩어리다:

    output1  이 다리의 시세·Greeks·IV·미결제약정 (29필드)
    output2  KOSPI 종합지수
    output3  **KOSPI200 현물지수**

output1만 남기고 싶은 유혹이 있지만 셋 다 남긴다. 특히 output3은 이 프로젝트가 아직 못 구한
현물지수 소스(RG 카테고리 갭)를 **매 폴링마다 실어 나른다** — 지금 안 남기면 나중에 소급이
불가능하다(옵션 시세는 과거 조회 경로가 없다). 컬럼 충돌을 피하려 output2/3은 `idx2_`/`idx3_`
접두어를 붙인다. 숫자로 못 바꾸는 값은 **버리지 않고** 문자열로 남긴다.

## 쓰기 방식 — 사이클 단위 flush (수급 아카이버와 다르다)

`InvestorFlowArchiver`는 스냅샷마다 그날 전체를 다시 쓴다. 거기선 하루 1,200행이라 무해했지만
여기는 규모가 다르다: 먼쓰리 42다리 × 78사이클 = 3,276행/일. 스냅샷마다 전체를 재작성하면
`data/archiver.py`가 겪었던 **O(n²)** 를 그대로 반복한다.

그래서 시리즈별로 버퍼링하다 `flush_every`행마다 쓴다(기본값은 한 사이클 분량). 대가는
**프로세스가 죽으면 마지막 미완 사이클을 잃는다**는 것인데, 5분 해상도 데이터에서 1사이클
미만의 손실이라 O(n²) 위험을 지는 것보다 낫다. `close()`로 남은 버퍼를 flush한다.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import polars as pl

from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_RAW, BusLike
from messiah.core.messages import OptionQuoteSnapshot
from messiah.core.timeutil import to_kst

_KST_ZONE_NAME = "Asia/Seoul"

DEFAULT_FLUSH_EVERY = 42
"""한 사이클(ATM±10 = 21행사가 × 콜/풋) 분량. `option_chain_poller.DEFAULT_STRIKE_WINDOW`와
맞춰 둔 값이지 계약은 아니다 — 창을 좁혀도 `close()`가 남은 것을 쓴다."""


class OptionChainArchiver:
    """`raw.option_chain.{underlying}` 구독 → `{base}/{series}/{date}.parquet`.

    같은 (시각, 종목)이 두 번 들어오면 나중 값이 이긴다 — 재시작 후 같은 사이클을 다시 받는
    경우가 실제로 생기고, 그때 행이 두 배로 쌓이면 나중에 시계열이 틀어진다.
    """

    def __init__(
        self,
        base_dir: Path,
        underlying: str = "KOSPI200",
        *,
        flush_every: int = DEFAULT_FLUSH_EVERY,
    ) -> None:
        if flush_every < 1:
            raise ValueError("flush_every는 1 이상이어야 한다")
        self._base_dir = Path(base_dir)
        self._underlying = underlying
        self._flush_every = flush_every
        self._rows: dict[str, dict[tuple[str, str], dict[str, object]]] = {}
        self._pending: dict[str, int] = {}
        self._day: date | None = None

    @property
    def row_count(self) -> int:
        return sum(len(rows) for rows in self._rows.values())

    def path_for(self, series: str, day: date) -> Path:
        return self._base_dir / series / f"{day.isoformat()}.parquet"

    async def handle_snapshot(self, snapshot: OptionQuoteSnapshot) -> None:
        if not isinstance(snapshot, OptionQuoteSnapshot):
            return
        if snapshot.underlying != self._underlying:
            return

        kst = to_kst(snapshot.ts_utc)
        day = kst.date()
        if self._day is not None and day != self._day:
            # 날짜가 바뀌면 남은 버퍼를 이전 날짜로 확정한 뒤 비운다 — 안 그러면 다음 날
            # 파일에 전날 행이 섞이거나, 마지막 사이클이 통째로 사라진다.
            self._flush_all()
            self._rows.clear()
            self._pending.clear()
        self._day = day

        row: dict[str, object] = {
            "ts_kst": kst,
            "series": snapshot.series,
            "symbol": snapshot.symbol,
            "option_type": snapshot.option_type,
            "strike": float(snapshot.strike),
            "expiry": snapshot.expiry,
        }
        row.update(_flatten_quote(snapshot.raw))

        series_rows = self._rows.setdefault(snapshot.series, {})
        series_rows[(kst.strftime("%H%M%S"), snapshot.symbol)] = row
        self._pending[snapshot.series] = self._pending.get(snapshot.series, 0) + 1
        if self._pending[snapshot.series] >= self._flush_every:
            self._flush(snapshot.series)

    def close(self) -> None:
        """남은 버퍼를 전부 쓴다 — 장 마감 정리(`daily_close`)에서 부른다."""
        self._flush_all()

    def _flush_all(self) -> None:
        for series in list(self._rows):
            self._flush(series)

    def _flush(self, series: str) -> None:
        rows = self._rows.get(series)
        if not rows or self._day is None:
            return
        path = self.path_for(series, self._day)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            frame = pl.DataFrame(list(rows.values()), infer_schema_length=None).sort(
                ["ts_kst", "strike", "option_type"]
            )
            tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            frame.write_parquet(tmp)
            os.replace(tmp, path)
            self._pending[series] = 0
        except Exception as exc:  # noqa: BLE001 — 적재 실패가 수집 루프를 죽이면 안 됨(L22)
            mlog.log(
                "OptionChainArchiveError",
                f"옵션체인 스냅샷 적재 실패: {exc}",
                underlying=self._underlying,
                series=series,
                rows=len(rows),
            )

    async def run_forever(self, bus: BusLike) -> None:
        await bus.subscribe([f"{TOPIC_RAW}.option_chain.{self._underlying}"], self._dispatch)

    async def _dispatch(self, msg: object) -> None:
        if isinstance(msg, OptionQuoteSnapshot):
            await self.handle_snapshot(msg)


def _flatten_quote(raw: object) -> dict[str, object]:
    """`get_quote(O)` 응답의 output1/2/3을 한 행으로 펼친다(모듈 docstring).

    output1은 접두어 없이(이 다리 자신의 값이라 컬럼명이 곧 의미), output2/3은 `idx2_`/`idx3_`
    접두어를 붙여 충돌을 막는다. 숫자 변환에 실패한 값은 문자열로 보존한다.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, object] = {}
    for key, prefix in (("output1", ""), ("output2", "idx2_"), ("output3", "idx3_")):
        block = raw.get(key)
        if isinstance(block, list) and block and isinstance(block[0], dict):
            block = block[0]
        if not isinstance(block, dict):
            continue
        for field, value in block.items():
            try:
                out[f"{prefix}{field}"] = float(value)
            except (TypeError, ValueError):
                out[f"{prefix}{field}"] = str(value)
    return out


def read_day(base_dir: Path, series: str, day: date) -> pl.DataFrame | None:
    """그날치를 읽어 `ts_kst`를 **이름대로 KST로** 되돌린다 — polars가 tz-aware를 UTC로
    정규화해 저장하므로(`data/archiver.py`의 같은 실측), 그대로 읽으면 컬럼 이름은 `ts_kst`인데
    dtype은 UTC라 9시간 틀리게 읽힌다."""
    path = Path(base_dir) / series / f"{day.isoformat()}.parquet"
    if not path.exists():
        return None
    frame = pl.read_parquet(path)
    if "ts_kst" in frame.columns:
        frame = frame.with_columns(pl.col("ts_kst").dt.convert_time_zone(_KST_ZONE_NAME))
    return frame.sort(["ts_kst", "strike", "option_type"])


def available_days(base_dir: Path, series: str) -> list[date]:
    directory = Path(base_dir) / series
    if not directory.is_dir():
        return []
    days: list[date] = []
    for path in directory.glob("*.parquet"):
        try:
            days.append(date.fromisoformat(path.stem))
        except ValueError:
            continue
    return sorted(days)
