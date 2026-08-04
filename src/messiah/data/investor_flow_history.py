"""일별 투자자매매동향 이력 — FL Feature의 데이터 계층 (2026-08-04 신설).

## 무엇을 가져오고, 무엇은 못 가져오나

`broker/kis/rest_client.get_investor_flow_daily()`를 날짜 커서로 페이징해 **KOSPI 현물
시장의 일별 순매수**를 모은다. 파생(선물/옵션) 수급이 아니다 — 그건 장중 엔드포인트에만
있고 과거 조회가 없다(`tr_codes.PATH_INVESTOR_FLOW_DAILY_BY_MARKET` 주석의 실측).

**일 단위라는 점이 이 데이터의 성질을 정한다**: 5분봉 모델에 붙이면 하루 78봉이 전부 같은
값을 받는다. 일중 타이밍은 못 주고 그날의 방향성 기울기만 준다 — 이걸 모르고 성능을 읽으면
"피처를 넣었는데 왜 일중 예측이 안 늘지"로 헤매게 된다.

## 미래 참조 금지 — 이 모듈이 지키는 가장 중요한 계약

그날의 순매수는 **장이 끝나야 확정된다.** 그래서 D일 봉의 피처로 D일 수급을 쓰면 그건
미래를 보는 것이다(그날 외국인이 얼마나 샀는지를 아침에 알 수 없다). `as_of()`는
**요청한 날짜보다 엄격히 이전** 거래일의 값만 돌려준다.

이 규율이 없으면 백테스트 성과가 극적으로 좋아지고, 그게 곧 버그의 증상이 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Mapping, Sequence

import polars as pl

from messiah.core import logging as mlog

# 응답에서 보관할 순매수 필드 — 30개 전부가 아니라 **의미가 겹치지 않는 축**만 고른다.
# `orgn`은 하위 8개(scrt/ivtr/pe_fund/bank/insu/mrbn/fund/etc_orgt)의 합이라(2026-08-04
# 산술 확인) 둘 다 쓰면 완전 공선성이 생긴다 — 상위 축만 남기고 하위는 뺀다.
FLOW_FIELDS: tuple[str, ...] = (
    "frgn_ntby_qty",  # 외국인 순매수 수량
    "frgn_ntby_tr_pbmn",  # 외국인 순매수 거래대금
    "prsn_ntby_qty",  # 개인
    "prsn_ntby_tr_pbmn",
    "orgn_ntby_qty",  # 기관계 (하위 8종의 합)
    "orgn_ntby_tr_pbmn",
)

_DATE_FIELD = "stck_bsop_date"
_MAX_PAGES = 40  # 300행/호출 × 40 = 12,000 거래일 — 어떤 요청 구간보다 넉넉하다


@dataclass(frozen=True, slots=True)
class FlowRow:
    day: date
    values: dict[str, float]


def _parse_day(raw: str) -> date | None:
    try:
        return datetime.strptime(raw, "%Y%m%d").date()  # noqa: DTZ007 — 날짜만
    except (ValueError, TypeError):
        return None


def _parse_row(row: Mapping[str, str]) -> FlowRow | None:
    day = _parse_day(str(row.get(_DATE_FIELD, "")))
    if day is None:
        return None
    values: dict[str, float] = {}
    for field in FLOW_FIELDS:
        raw = row.get(field)
        try:
            values[field] = float(raw)
        except (TypeError, ValueError):
            return None  # 필드 하나라도 못 읽으면 그 행은 통째로 버린다(부분 행 금지)
    return FlowRow(day=day, values=values)


def fetch_history(
    fetch: Callable[..., dict],
    *,
    start: date,
    end: date,
    max_pages: int = _MAX_PAGES,
) -> list[FlowRow]:
    """[start, end] 구간의 일별 수급 — 날짜 커서로 과거로 페이징한다.

    종료 조건: ① start 이전까지 받았거나 ② 새 행이 없거나(커서가 더 못 감) ③ 페이지 상한.
    산출: 날짜 오름차순, 중복 없음. 응답이 전부 0인 경우(파생 업종코드를 넣은 경우)도
         그대로 담는다 — 여기서 판단하지 않고 호출측이 `looks_unsupported()`로 확인한다.
    """
    if start > end:
        raise ValueError(f"start({start})가 end({end})보다 늦다")

    collected: dict[date, FlowRow] = {}
    cursor = end
    for _ in range(max_pages):
        body = fetch(date_yyyymmdd=cursor.strftime("%Y%m%d"))
        raw_rows = body.get("output") or body.get("output1") or []
        if not isinstance(raw_rows, list):
            raw_rows = [raw_rows]
        parsed = [r for r in (_parse_row(x) for x in raw_rows) if r is not None]
        if not parsed:
            break

        new = 0
        for row in parsed:
            if row.day not in collected and start <= row.day <= end:
                collected[row.day] = row
                new += 1
        oldest = min(r.day for r in parsed)
        if oldest <= start:
            break
        if new == 0 and oldest >= cursor:
            break  # 커서가 안 움직인다 — 무한 루프 방지
        cursor = oldest
    else:
        mlog.log(
            "InvestorFlowPagingLimit",
            f"페이지 상한 {max_pages}회 도달 — 더 이른 날이 남아 있을 수 있음"
            f"(현재 {len(collected)}일)",
            rows=len(collected),
        )

    return [collected[d] for d in sorted(collected)]


def looks_unsupported(rows: Sequence[FlowRow]) -> bool:
    """전 행·전 필드가 0인가 — 파생 업종코드를 넣었을 때의 응답 모양이다.

    rt_cd=0에 값만 0이라 조용히 "그날 수급이 0이었다"로 오해하기 쉽다. 실제로 KOSPI 현물은
    순매수가 정확히 0인 날이 사실상 없으므로, 전 구간이 0이면 미지원으로 보는 게 맞다.
    """
    return bool(rows) and all(all(v == 0.0 for v in r.values.values()) for r in rows)


def to_frame(rows: Sequence[FlowRow]) -> pl.DataFrame:
    """저장용 프레임 — 날짜 1행, 컬럼은 `FLOW_FIELDS`."""
    return pl.DataFrame(
        {
            "day": [r.day for r in rows],
            **{f: [r.values[f] for r in rows] for f in FLOW_FIELDS},
        }
    )


def write(rows: Sequence[FlowRow], path: Path) -> int:
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = to_frame(rows).unique(subset=["day"], keep="last").sort("day")
    tmp = path.with_suffix(".tmp")
    frame.write_parquet(tmp)
    tmp.replace(path)
    return frame.height


def read(path: Path) -> list[FlowRow]:
    if not path.exists():
        return []
    frame = pl.read_parquet(path).sort("day")
    return [
        FlowRow(day=row["day"], values={f: float(row[f]) for f in FLOW_FIELDS})
        for row in frame.iter_rows(named=True)
    ]


class FlowHistory:
    """날짜 → 그날 **이전** 마지막 수급. 미래 참조를 구조적으로 막는다(모듈 docstring).

    `as_of(d)`는 **d보다 엄격히 이전** 거래일의 값을 준다 — 그날의 순매수는 장이 끝나야
    확정되므로 d일 봉의 피처로 d일 수급을 쓰면 미래를 보는 것이다.

    `features/sidecar.DailySidecar`의 첫 구현체다(2026-08-04). 메서드 이름이
    `flow_as_of` → `as_of`로 바뀐 것은 그 계약을 OP·RG 사이드카가 그대로 물려받게 하려는
    것이다 — 카테고리마다 이름이 다르면 "엄격히 이전만 본다"는 규율을 각자 다시 발명하게
    되고, 그중 하나가 안 지키면 그 카테고리만 미래를 보게 된다.
    """

    def __init__(self, rows: Sequence[FlowRow]) -> None:
        self._days = [r.day for r in sorted(rows, key=lambda r: r.day)]
        self._rows = sorted(rows, key=lambda r: r.day)

    def __len__(self) -> int:
        return len(self._rows)

    @property
    def days(self) -> list[date]:
        return list(self._days)

    def as_of(self, day: date) -> FlowRow | None:
        """`day`보다 엄격히 이전인 마지막 행. 없으면 None(이력 시작 전)."""
        lo, hi = 0, len(self._days)
        while lo < hi:  # bisect_left — day 이상인 첫 위치
            mid = (lo + hi) // 2
            if self._days[mid] < day:
                lo = mid + 1
            else:
                hi = mid
        return self._rows[lo - 1] if lo > 0 else None

    def recent(self, day: date, n: int) -> list[FlowRow]:
        """`day` 이전 최근 n일(오래된 것 → 최신). 누적·연속 계열 계산의 입력."""
        lo, hi = 0, len(self._days)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._days[mid] < day:
                lo = mid + 1
            else:
                hi = mid
        return self._rows[max(0, lo - n) : lo]
