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

## 위 문단이 말한 대가는 **세 자릿수 배 틀렸다** (2026-08-06 실측)

"마지막 미완 사이클"이 아니라 **재기동 전 그날 전부**였다. `_flush()`가 쓰는 것은 메모리에
있는 `self._rows[series]` 전부이고 재기동하면 그게 비어 있으므로, 재기동 후 첫 flush가
`os.replace()`로 오전치를 담은 파일을 통째로 갈아엎는다. 버퍼링 여부와 무관한, 쓰기 방식
자체의 결함이다(`InvestorFlowArchiver`도 같은 형태였다).

2026-08-06: 호스트 재부팅(10:03:49) → 10:25 재기동. `data/option_chain/regular/2026-08-06.parquet`의
첫 사이클이 **10:30**이었다 — 08:40~10:00의 9사이클 × 42다리가 사라졌다. 폴러는 그 시간
내내 정상이었고 `OptionChainPollRetried`가 09:18·09:31·09:43·10:01·10:03에 남아 있다.
8/5(14:11 재기동)에는 같은 방식으로 오전~오후 초반이 전부 날아갔다.

옵션 시세는 **과거 조회 경로가 없다**(모듈 상단). 그래서 두 겹으로 막는다:

1. **기동 복원**(`_restore_series`) — 그 시리즈의 그날 파일이 있으면 읽어서 버퍼를 채운 뒤
   시작한다. 중복 키는 나중 값이 이기므로 병합이 이미 정의돼 있다.
2. **축소 쓰기 거부**(`_write_is_safe`) — 디스크 행수 > 메모리 행수면 병합을 재시도하고,
   그래도 줄어들면 그 쓰기를 건너뛴다. 새 행은 메모리에 남아 다음 사이클에 다시 나가지만,
   지워진 옛 행은 영영 못 돌아온다 — 비대칭이 명확하다.
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
        # 이 날짜에 대해 디스크 복원을 이미 시도한 시리즈 — 시리즈마다 한 번만 읽는다.
        self._restored: set[str] = set()

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
            self._restored.clear()
        self._day = day
        # 이 시리즈를 이 날짜로 처음 다루는 순간 디스크에 있는 그날치를 먼저 흡수한다 —
        # 안 그러면 첫 flush가 재기동 전 사이클을 덮어쓴다(모듈 docstring).
        if snapshot.series not in self._restored:
            self._restored.add(snapshot.series)
            self._restore_series(snapshot.series, day)

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

    def _restore_series(self, series: str, day: date) -> int:
        """디스크에 있는 그 시리즈의 그날치를 버퍼로 흡수한다 — 재기동 복원(겹①).

        반환: 흡수한 행 수(파일이 없으면 0).
        실패 조건: 없다 — 복원 실패가 그날 수집을 막으면 안 된다(L22). 조용히는 안 넘긴다:
                  그 경우 `_write_is_safe`(겹②)가 마지막 방어선이 된다.

        `ts_kst`는 `to_kst()`로 다시 정규화한다 — `read_day()`가 돌려주는 tzinfo와 새
        스냅샷의 tzinfo가 섞이면 polars가 한 컬럼으로 못 묶을 수 있다.
        """
        try:
            frame = read_day(self._base_dir, series, day)
        except Exception as exc:  # noqa: BLE001 — 복원 실패가 수집을 막지 않는다(L22)
            mlog.log(
                "OptionChainArchiveRestoreFailed",
                f"{series} {day.isoformat()} 기존 적재분을 못 읽었다 — "
                f"덮어쓰기 방지만 남는다: {exc}",
                underlying=self._underlying,
                series=series,
                date=day.isoformat(),
            )
            return 0
        if frame is None or frame.height == 0:
            return 0

        series_rows = self._rows.setdefault(series, {})
        restored = 0
        for row in frame.to_dicts():
            moment, symbol = row.get("ts_kst"), row.get("symbol")
            if moment is None or symbol is None:
                continue  # 키를 못 만드는 행은 병합 대상이 아니다(옛 포맷 방어)
            row["ts_kst"] = to_kst(moment)
            series_rows[(row["ts_kst"].strftime("%H%M%S"), str(symbol))] = row
            restored += 1
        if restored:
            mlog.log(
                "OptionChainArchiveRestored",
                f"{series} {day.isoformat()} 기존 적재분 {restored}행을 이어받고 시작 — "
                "재기동 전 사이클이 다음 쓰기에 지워지지 않는다",
                underlying=self._underlying,
                series=series,
                date=day.isoformat(),
                rows=restored,
            )
        return restored

    def _write_is_safe(self, series: str, path: Path) -> bool:
        """쓰기 직전 겹② — 이 쓰기가 그 시리즈 파일을 **줄이지 않는가**(모듈 docstring).

        정상 경로에서는 `_restore_series()` 덕에 항상 True다. 줄어드는 쓰기는 건너뛴다 —
        메모리 행은 남아 다음 사이클에 다시 나가지만 디스크의 옛 사이클은 소급이 없다.
        """
        if not path.exists():
            return True
        try:
            on_disk = pl.read_parquet(path).height
        except Exception:  # noqa: BLE001 — 못 읽으면 판정 불가, 기존 동작대로 진행
            return True
        if on_disk <= len(self._rows.get(series, {})):
            return True

        if self._day is not None:
            self._restore_series(series, self._day)
        if on_disk <= len(self._rows.get(series, {})):
            return True
        mlog.log(
            "OptionChainArchiveShrinkRefused",
            f"{series} 디스크 {on_disk}행 > 병합 후 메모리 "
            f"{len(self._rows.get(series, {}))}행 — 덮으면 소실이므로 이번 쓰기를 건너뛴다",
            underlying=self._underlying,
            series=series,
            on_disk=on_disk,
            in_memory=len(self._rows.get(series, {})),
        )
        return False

    def _flush(self, series: str) -> None:
        rows = self._rows.get(series)
        if not rows or self._day is None:
            return
        path = self.path_for(series, self._day)
        if not self._write_is_safe(series, path):
            return
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
