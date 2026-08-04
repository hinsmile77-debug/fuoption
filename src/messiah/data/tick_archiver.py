"""체결틱 원본 적재 — `md.tick.*`를 하루 시간대 조각으로 (2026-08-04 신설, F2).

## 왜 지금 만드나

이 프로젝트는 **틱을 한 번도 저장한 적이 없다.** `TickCollector`는 틱을 받아 분봉으로
집계하고 버렸다(`_archive_and_publish_bar()`가 저장하는 것은 완성봉뿐). 그래서 MS(마이크로
구조) 카테고리 30개가 통째로 "데이터 없음"이었다.

그런데 **호가는 이미 매 틱 도착하고 있었다** — H0IFCNT0 프레임 50필드 중 idx34~37이
매도호가1/매수호가1/잔량이고, 파서가 4필드만 읽고 나머지를 버렸을 뿐이다
(`data/normalizer.py` 인덱스 주석).

그리고 **틱은 봉과 달리 백필 경로가 없다.** KIS 분봉 API(`data/backfill.py`)는 OHLCV만
주고, 체결 단위 과거 조회는 아예 없다. 안 받은 날은 영원히 빈다 — 파생 수급(7개월)과
옵션체인에서 이미 두 번 겪은 것과 정확히 같은 성질이다.

**그래서 순서가 이 모듈의 존재 이유다**: 아카이버가 구독을 걸고 있어야 폴러/수집기가 낸
것이 남는다. 반대로 켜면 첫 데이터가 버스에서 증발한다(`data/flow_archiver.py`와 동일).

## 프레임 전체를 남긴다 — `f00`~`f49`

파싱한 6개(가격·수량·호가4)만 남기고 싶은 유혹이 있지만 원시 필드를 전부 남긴다. 미결제
약정·이론가·총잔량·체결강도로 **보이는** 필드가 더 있는데, 위치를 실측으로 확정한 적이
없어 지금 이름을 붙이면 그게 곧 마흐디 L16 사고(단위 미확인 스키마로 5일치 유실)다.
필드 위치는 다음 주에 실측으로 확정하면 되지만, **그때 쓸 데이터는 오늘 안 받으면 없다.**

컬럼 이름은 의미가 아니라 위치(`f34` = idx34)로 붙인다 — 추정한 의미를 이름에 박으면
그 추정이 틀렸을 때 컬럼 이름이 거짓말을 하게 된다.

## 쓰기 방식 — 시간대 조각 + 버퍼 flush

규모가 다른 아카이버들과 다르다:

    InvestorFlowArchiver  하루 1,200행   → 스냅샷마다 그날 전체 재작성(무해)
    OptionChainArchiver   하루 3,276행   → 사이클 단위 버퍼 flush
    TickArchiver          하루 5~10만행  → **시간대 조각 + 버퍼 flush** (여기)

`data/archiver.py`가 O(n²)를 피하려 도입한 시간대 조각(`{date}/{HH}.parquet`)을 그대로
쓰되, 조각 안에서도 매 틱 다시 쓰면 여전히 O(n²)라 `flush_every`행마다 모아서 쓴다.
대가는 프로세스가 죽으면 마지막 버퍼(기본 500틱, 초당 3틱 기준 3분 미만)를 잃는다는 것.
`close()`가 남은 버퍼를 flush한다.

**조각을 하루 1파일로 통합하지 않는다.** 봉 아카이버가 `compact_day()`를 하는 이유는
Digital Twin·백테스트가 `{date}.parquet`를 직접 여는 코드를 갖고 있어서인데, 틱에는 아직
그런 소비자가 없다. 하루 5~10만행을 한 파일로 합치는 비용만 지고 얻는 게 없다 —
`read_day()`가 조각을 알아서 이어 읽는다.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import polars as pl

from messiah.core import logging as mlog
from messiah.core.bus import TOPIC_TICK, BusLike
from messiah.core.messages import Tick
from messiah.core.timeutil import to_kst

# `core/timeutil.py`의 KST는 고정 오프셋 객체라 polars가 못 받는다 — IANA 이름을 쓴다
# (`data/archiver.py`·`data/flow_archiver.py`와 동일).
_KST_ZONE_NAME = "Asia/Seoul"

DEFAULT_FLUSH_EVERY = 500
"""버퍼에 이만큼 쌓이면 조각에 쓴다. 실측 기준 초당 3틱 안팎(2026-07-23 90초 257틱)이라
약 3분 분량 — 프로세스가 죽었을 때 잃는 최대치가 그만큼이라는 뜻이다. 더 키우면 손실
구간이 늘고, 더 줄이면 조각 재작성 횟수가 는다."""


class TickArchiver:
    """`md.tick.{symbol}` 구독 → `{base}/{symbol}/{date}/{HH}.parquet`.

    같은 (거래소시각, 가격, 수량)이 두 번 들어와도 **중복 제거하지 않는다** — 같은 초에
    같은 수량이 같은 가격에 두 번 체결되는 것은 실제로 일어나는 일이고, 그걸 지우면
    거래량이 조용히 줄어든다. 2026-08-04에 프레임당 레코드를 하나만 읽어 거래량 절반을
    날린 사고와 같은 방향의 실수다(그때는 파서, 여기는 적재).

    재시작으로 같은 구간을 다시 받는 경우가 중복의 실제 원인인데, 그건 조각 파일이
    통째로 다시 쓰이는 것이 아니라 append이므로 여기서 판단할 수 없다 — 소비자가 필요하면
    (거래소시각, 가격, 수량, 누적거래량) 조합으로 사후 정제하면 된다(누적거래량 필드가
    `raw_fields`에 그대로 있다).
    """

    def __init__(
        self,
        base_dir: Path,
        symbol: str,
        *,
        flush_every: int = DEFAULT_FLUSH_EVERY,
    ) -> None:
        self._base_dir = Path(base_dir)
        self._symbol = symbol
        self._flush_every = max(1, flush_every)
        self._buffer: list[dict[str, object]] = []
        self._written = 0

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def buffered(self) -> int:
        return len(self._buffer)

    @property
    def written(self) -> int:
        """flush로 실제 디스크에 나간 행 수 — 기동 로그·장후 리포트가 "정말 쌓였나"를
        확인하는 값이다. 결선만 하고 0으로 하루가 끝나는 것이 이 프로젝트의 반복 실패
        모드였다."""
        return self._written

    def shard_path(self, ts_kst) -> Path:
        return self._base_dir / self._symbol / ts_kst.date().isoformat() / f"{ts_kst:%H}.parquet"

    async def handle_tick(self, tick: Tick) -> None:
        if not isinstance(tick, Tick) or tick.symbol != self._symbol:
            return

        kst = to_kst(tick.ts_exchange)
        row: dict[str, object] = {
            "ts_kst": kst,
            "symbol": tick.symbol,
            "price_ticks": tick.price_ticks,
            "qty": tick.qty,
            "side_hint": tick.side_hint,
            "bid1_ticks": tick.bid1_ticks,
            "ask1_ticks": tick.ask1_ticks,
            "bid_qty1": tick.bid_qty1,
            "ask_qty1": tick.ask_qty1,
            "source": tick.source,
        }
        # 원시 필드는 **위치 이름**으로 남긴다(모듈 docstring) — 숫자로 바꾸지 않고 문자열
        # 그대로다. 지금 float로 바꾸면 "000000" 같은 값이 0이 되어 원본과 달라지고,
        # 필드 의미를 확정하기 전에 손실 변환을 하는 셈이 된다.
        for index, value in enumerate(tick.raw_fields):
            row[f"f{index:02d}"] = value

        self._buffer.append(row)
        if len(self._buffer) >= self._flush_every:
            self.flush()

    def flush(self) -> int:
        """버퍼를 조각 파일에 이어 쓴다. 반환: 이번에 쓴 행 수.

        버퍼가 자정을 넘거나 시간대를 걸치면 조각이 갈리므로 **시간대별로 나눠서** 쓴다 —
        버퍼 전체를 첫 틱의 조각에 몰아넣으면 09:59~10:01 구간의 틱이 09시 조각에 섞인다.
        """
        if not self._buffer:
            return 0

        by_shard: dict[Path, list[dict[str, object]]] = {}
        for row in self._buffer:
            by_shard.setdefault(self.shard_path(row["ts_kst"]), []).append(row)

        written = 0
        for path, rows in by_shard.items():
            try:
                written += self._write_shard(path, rows)
            except Exception as exc:  # noqa: BLE001 — 적재 실패가 수집 루프를 죽이면 안 됨(L22)
                mlog.log(
                    "TickArchiveError",
                    f"틱 조각 적재 실패({path.name}) — 이 버퍼는 유실된다: {exc}",
                    symbol=self._symbol,
                    rows=len(rows),
                )
        self._buffer.clear()
        self._written += written
        return written

    def _write_shard(self, path: Path, rows: list[dict[str, object]]) -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        frame = pl.DataFrame(rows, infer_schema_length=None)
        if path.exists():
            # 스키마가 다를 수 있다(호가가 없는 프레임이 섞이면 컬럼 수가 다름) —
            # `data/archiver.py`가 `session` 컬럼 추가일에 겪은 것과 같은 이유로
            # `diagonal_relaxed`를 쓴다. 기본 concat은 그 순간 예외를 던지고, 그러면
            # 그 시간대가 통째로 사라진다.
            frame = pl.concat([_read_shard(path), frame], how="diagonal_relaxed")
        frame = frame.sort("ts_kst")
        # 원자적 교체 — 읽는 쪽(UI·분석 스크립트)이 찢어진 중간 상태를 보지 않게
        # (`data/archiver.py` "원자적 쓰기" 사고 대응과 같은 규율).
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        frame.write_parquet(tmp)
        try:
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return len(rows)

    def close(self) -> int:
        """종료 시퀀스에서 남은 버퍼를 내보낸다 — 안 부르면 마지막 몇 분이 사라진다(L23
        "종료 시퀀스는 자기검증 필수")."""
        return self.flush()

    async def run_forever(self, bus: BusLike) -> None:
        await bus.subscribe([f"{TOPIC_TICK}.{self._symbol}"], self._dispatch)

    async def _dispatch(self, msg: object) -> None:
        if isinstance(msg, Tick):
            await self.handle_tick(msg)


def _read_shard(path: Path) -> pl.DataFrame:
    return pl.read_parquet(path)


def read_day(base_dir: Path, symbol: str, day: date) -> pl.DataFrame | None:
    """그날치 조각을 전부 이어 읽고 `ts_kst`를 **이름대로 KST로** 되돌린다.

    polars는 tz-aware datetime을 Parquet에 쓸 때 UTC로 정규화한다(`data/archiver.py` 모듈
    docstring의 실측) — 그대로 읽으면 컬럼 이름은 `ts_kst`인데 dtype이 UTC라 보는 사람이
    9시간 틀리게 읽는다. `bar_open_kst`·`flow_archiver.ts_kst`와 같은 규율.
    """
    directory = Path(base_dir) / symbol / day.isoformat()
    if not directory.is_dir():
        return None
    frames: list[pl.DataFrame] = []
    for path in sorted(directory.glob("*.parquet")):
        try:
            frames.append(_read_shard(path))
        except Exception:  # noqa: BLE001 — 조각 하나가 나머지를 못 읽게 만들면 안 됨
            continue
    if not frames:
        return None
    frame = pl.concat(frames, how="diagonal_relaxed")
    if "ts_kst" in frame.columns:
        frame = frame.with_columns(pl.col("ts_kst").dt.convert_time_zone(_KST_ZONE_NAME))
    return frame.sort("ts_kst")


def available_days(base_dir: Path, symbol: str) -> list[date]:
    directory = Path(base_dir) / symbol
    if not directory.is_dir():
        return []
    days: list[date] = []
    for child in directory.iterdir():
        if not child.is_dir():
            continue
        try:
            days.append(date.fromisoformat(child.name))
        except ValueError:
            continue
    return sorted(days)
