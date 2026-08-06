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

## "죽어도 남는다"는 **재기동에서 거짓이었다** (2026-08-06 실측)

위 문단은 크래시에만 맞고 재기동에는 틀렸다. `_flush()`가 쓰는 것은 **메모리에 있는
`self._rows` 전부**이고, 재기동하면 그게 빈 채로 시작한다 — 재기동 후 첫 폴링의 flush가
`os.replace()`로 **그날 오전치를 담은 파일을 통째로 갈아엎었다.**

2026-08-06: 10:03:49에 호스트가 재부팅되고 10:25에 수동 재기동했더니, 파일의 첫 행이
**10:26**이었다. 08:36~10:03의 88분 × 3업종 ≈ 264행이 사라졌다. 폴러는 그 시간 내내
정상 동작했고 로그에도 그 흔적이 남아 있다 — 파일만 없다. 8/5(14:11 재기동)에는 같은
방식으로 **5시간 35분치**가 날아갔고, 아무도 몰랐다.

이 계열은 **소급이 불가능**하다(모듈 상단 "못 받는 데이터는 지금 안 받으면 영원히 없다").
그래서 두 겹으로 막는다:

1. **기동 복원**(`_restore_day`) — 그날 파일이 이미 있으면 읽어서 `self._rows`를 채운 뒤
   시작한다. 중복 키는 나중 값이 이기므로(이 클래스의 기존 규율) 병합이 정의돼 있다.
2. **파괴 거부**(`_flush`) — 쓰기 직전에 디스크 행수가 메모리 행수보다 많으면 덮지 않고
   먼저 병합한다. 1이 어떤 이유로 실패해도(파일 손상·스키마 변경) **줄어드는 쓰기는
   일어나지 않는다.** 조용히는 안 한다(L18) — `InvestorFlowArchiveShrinkRefused`.
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
        if day != self._day:
            # 이 날짜를 처음 다루는 순간(기동 직후 또는 날짜 롤오버) 디스크에 이미 있는
            # 그날치를 먼저 흡수한다 — 안 그러면 첫 flush가 오전치를 덮어쓴다(모듈 docstring).
            self._day = day
            self._restore_day(day)
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

    def _restore_day(self, day: date) -> int:
        """디스크에 이미 있는 그날치를 `self._rows`로 흡수한다 — 재기동 복원(겹①).

        반환: 흡수한 행 수(파일이 없으면 0).
        실패 조건: 없다 — 복원 실패가 그날 수집을 막으면 안 된다(L22). 다만 조용히 넘기면
                  다음 flush가 파일을 줄여 버리므로 반드시 로그를 남긴다(L18). 그 경우
                  `_flush`의 파괴 거부(겹②)가 마지막 방어선이 된다.

        `ts_kst`는 `to_kst()`로 다시 정규화한다 — `read_day()`가 돌려주는 tzinfo와 새
        스냅샷의 tzinfo가 섞이면 polars가 한 컬럼으로 못 묶을 수 있다.
        """
        try:
            frame = read_day(self._base_dir, self._market_code, day)
        except Exception as exc:  # noqa: BLE001 — 복원 실패가 수집을 막지 않는다(L22)
            mlog.log(
                "InvestorFlowArchiveRestoreFailed",
                f"{day.isoformat()} 기존 적재분을 못 읽었다 — 덮어쓰기 방지만 남는다: {exc}",
                market_code=self._market_code,
                date=day.isoformat(),
            )
            return 0
        if frame is None or frame.height == 0:
            return 0

        restored = 0
        for row in frame.to_dicts():
            moment, sector = row.get("ts_kst"), row.get("sector_code")
            if moment is None or sector is None:
                continue  # 키를 못 만드는 행은 병합 대상이 아니다(옛 포맷 방어)
            row["ts_kst"] = to_kst(moment)
            self._rows[(row["ts_kst"].strftime("%H%M%S"), str(sector))] = row
            restored += 1
        if restored:
            mlog.log(
                "InvestorFlowArchiveRestored",
                f"{day.isoformat()} 기존 적재분 {restored}행을 이어받고 시작 — "
                "재기동 전 수집분이 다음 쓰기에 지워지지 않는다",
                market_code=self._market_code,
                date=day.isoformat(),
                rows=restored,
            )
        return restored

    def _write_is_safe(self, path: Path) -> bool:
        """쓰기 직전 겹② — 이 쓰기가 파일을 **줄이지 않는가**.

        정상 경로에서는 `_restore_day()` 덕에 항상 True다. False가 나온다면 복원이
        실패했거나 다른 프로세스가 같은 파일을 쓰고 있다는 뜻이고, 둘 다 **덮어쓰면
        영구 소실**이다(이 계열은 소급 조회가 없다).

        한 번 더 병합을 시도하고, 그래도 줄어들면 **이번 쓰기를 건너뛴다**. 메모리 행은
        그대로 남아 다음 폴링에서 다시 시도하므로 새 데이터는 안 잃고, 디스크의 옛
        데이터는 확실히 안 지운다 — 비대칭이 명확하다(새 것은 복구 가능, 옛 것은 아니다).
        """
        if not path.exists():
            return True
        try:
            on_disk = pl.read_parquet(path).height
        except Exception:  # noqa: BLE001 — 못 읽으면 판정 불가, 기존 동작대로 진행
            return True
        if on_disk <= len(self._rows):
            return True

        if self._day is not None:
            self._restore_day(self._day)
        if on_disk <= len(self._rows):
            return True
        mlog.log(
            "InvestorFlowArchiveShrinkRefused",
            f"디스크 {on_disk}행 > 병합 후 메모리 {len(self._rows)}행 — 덮으면 소실이므로 "
            "이번 쓰기를 건너뛴다(다음 폴링에서 재시도)",
            market_code=self._market_code,
            on_disk=on_disk,
            in_memory=len(self._rows),
        )
        return False

    def _flush(self) -> None:
        if not self._rows or self._day is None:
            return
        path = self.path_for(self._day)
        if not self._write_is_safe(path):
            return
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
