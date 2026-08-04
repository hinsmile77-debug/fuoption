from datetime import date

import pytest

from messiah.data import investor_flow_history as ifh
from messiah.data.investor_flow_history import FlowHistory, FlowRow


def _raw(day: str, frgn: int = 100, prsn: int = -50, orgn: int = -50) -> dict:
    return {
        "stck_bsop_date": day,
        "frgn_ntby_qty": str(frgn),
        "frgn_ntby_tr_pbmn": str(frgn * 10),
        "prsn_ntby_qty": str(prsn),
        "prsn_ntby_tr_pbmn": str(prsn * 10),
        "orgn_ntby_qty": str(orgn),
        "orgn_ntby_tr_pbmn": str(orgn * 10),
    }


def _rows(days: list[str]) -> list[FlowRow]:
    return [r for r in (ifh._parse_row(_raw(d)) for d in days) if r]


class _FakeApi:
    """날짜 커서로 과거 300행씩 주는 실제 규약을 흉내낸다."""

    def __init__(self, days: list[str], page: int = 300):
        self.days = sorted(days)
        self.page = page
        self.calls: list[str] = []

    def __call__(self, *, date_yyyymmdd: str, **kw):
        self.calls.append(date_yyyymmdd)
        upto = [d for d in self.days if d <= date_yyyymmdd]
        chunk = upto[-self.page :]
        return {"output": [_raw(d) for d in reversed(chunk)]}


# ---------------------------------------------------------------- 파싱


def test_parse_row_reads_all_flow_fields():
    row = ifh._parse_row(_raw("20260803", frgn=1057))

    assert row.day == date(2026, 8, 3)
    assert row.values["frgn_ntby_qty"] == 1057.0
    assert set(row.values) == set(ifh.FLOW_FIELDS)


def test_parse_row_rejects_partial_rows():
    """필드 하나라도 못 읽으면 행 전체를 버린다 — 반쪽 행이 피처로 새면 안 된다."""
    broken = _raw("20260803")
    broken["orgn_ntby_qty"] = ""

    assert ifh._parse_row(broken) is None


def test_parse_row_rejects_bad_date():
    assert ifh._parse_row(_raw("not-a-date")) is None


# ---------------------------------------------------------------- 페이징


def test_fetch_history_pages_backwards_until_start():
    api = _FakeApi([f"2026{m:02d}{d:02d}" for m in (6, 7) for d in range(1, 29)], page=10)

    rows = ifh.fetch_history(api, start=date(2026, 6, 5), end=date(2026, 7, 20))

    assert rows[0].day == date(2026, 6, 5)
    assert rows[-1].day == date(2026, 7, 20)
    assert rows == sorted(rows, key=lambda r: r.day)
    assert len(api.calls) > 1  # 실제로 페이징했다


def test_fetch_history_stops_when_cursor_cannot_move():
    """같은 응답이 계속 오면 무한 루프에 빠지지 않는다."""

    def stuck(*, date_yyyymmdd: str, **kw):
        return {"output": [_raw("20260801")]}

    rows = ifh.fetch_history(stuck, start=date(2020, 1, 1), end=date(2026, 8, 1))

    assert len(rows) == 1


def test_fetch_history_rejects_reversed_range():
    with pytest.raises(ValueError):
        ifh.fetch_history(_FakeApi([]), start=date(2026, 8, 1), end=date(2026, 7, 1))


def test_looks_unsupported_detects_all_zero_response():
    """파생 업종코드(F001/OC01)를 넣으면 rt_cd=0에 값만 전부 0으로 온다 — 조용히
    '그날 수급이 0'으로 오해하면 안 된다(2026-08-04 실측)."""
    zeros = [r for r in (ifh._parse_row(_raw(d, 0, 0, 0)) for d in ("20260801", "20260803")) if r]

    assert ifh.looks_unsupported(zeros) is True
    assert ifh.looks_unsupported(_rows(["20260801"])) is False
    assert ifh.looks_unsupported([]) is False


# ---------------------------------------------------------------- 미래 참조 금지


def test_as_of_returns_strictly_earlier_day():
    """**이 파일에서 가장 중요한 테스트** — 그날 순매수는 장이 끝나야 확정된다.
    D일 봉의 피처로 D일 수급을 쓰면 미래를 보는 것이고, 백테스트 성과가 거짓으로 좋아진다."""
    hist = FlowHistory(_rows(["20260731", "20260803", "20260804"]))

    assert hist.as_of(date(2026, 8, 4)).day == date(2026, 8, 3)
    assert hist.as_of(date(2026, 8, 3)).day == date(2026, 7, 31)


def test_as_of_returns_none_before_history_starts():
    hist = FlowHistory(_rows(["20260803"]))

    assert hist.as_of(date(2026, 8, 3)) is None
    assert hist.as_of(date(2020, 1, 1)) is None


def test_as_of_skips_gaps_to_the_last_available_day():
    """휴장·결손으로 며칠 비어도 그 이전 마지막 값을 쓴다(가짜 0을 만들지 않는다)."""
    hist = FlowHistory(_rows(["20260717", "20260803"]))

    assert hist.as_of(date(2026, 7, 31)).day == date(2026, 7, 17)


def test_recent_returns_only_days_before_the_asked_date():
    hist = FlowHistory(_rows(["20260728", "20260729", "20260730", "20260731"]))

    recent = hist.recent(date(2026, 7, 30), n=5)

    assert [r.day for r in recent] == [date(2026, 7, 28), date(2026, 7, 29)]


def test_recent_caps_at_n():
    hist = FlowHistory(_rows([f"202607{d:02d}" for d in range(1, 20)]))

    assert len(hist.recent(date(2026, 7, 19), n=3)) == 3


# ---------------------------------------------------------------- 저장·복원


def test_write_read_round_trip(tmp_path):
    rows = _rows(["20260731", "20260803"])
    path = tmp_path / "flow.parquet"

    assert ifh.write(rows, path) == 2
    restored = ifh.read(path)

    assert [r.day for r in restored] == [r.day for r in rows]
    assert restored[0].values == rows[0].values


def test_read_missing_file_returns_empty(tmp_path):
    assert ifh.read(tmp_path / "nope.parquet") == []


def test_write_empty_does_nothing(tmp_path):
    path = tmp_path / "flow.parquet"

    assert ifh.write([], path) == 0
    assert not path.exists()
