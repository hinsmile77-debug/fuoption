"""사이징 0계약 원시 수량 전수 집계 (2026-10-19 G-84).

여기서 붙잡는 성질:
  ① `SizerZeroQty`의 raw_qty 최대·중앙과 「필요/실제 우위」 배율을 맞게 낸다.
  ② `sized_calls − zero_qty_cycles` 를 「0계약 외(상한)」로 낸다 — 누계 줄이 없으면 미측정(None).
  ③ 기간 필터와, 기록이 하나도 없으면 0이 아니라 「미측정」이라고 말하는 것.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_sizer_shortfall_tally.py"


@pytest.fixture(scope="module")
def st():
    spec = importlib.util.spec_from_file_location("_sizer_shortfall_tally", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _zero(hhmm: str, raw: float, edge: float, need: float) -> dict:
    return {
        "ts": f"2026-10-19T{hhmm}:00+09:00",
        "tag": "SizerZeroQty",
        "raw_qty": raw,
        "edge": edge,
        "edge_needed_for_min_qty": need,
        "vol_target_qty": 9.7,
        "uncertainty_penalty": 0.88,
    }


def _streak(sized: int, zero: int) -> dict:
    return {"tag": "SizerZeroQtyStreak", "sized_calls": sized, "zero_qty_cycles": zero}


def test_tally_day_reports_shortfall_and_upper_bound(st):
    t = st.tally_day(
        [
            _zero("09:00", 0.42, 0.2, 0.466),
            _zero("14:30", 0.80, 0.25, 0.30),
            _streak(sized=3, zero=2),
            {"tag": "OrderSubmit"},
        ]
    )
    assert t["zero_qty"] == 2
    assert t["raw_max"] == pytest.approx(0.80)
    assert t["raw_p50"] == pytest.approx(0.42)
    assert t["edge_gap_min"] == pytest.approx(0.30 / 0.25)
    assert t["non_zero_upper"] == 1
    assert t["order_submits"] == 1


def test_missing_streak_means_unmeasured_not_zero(st):
    t = st.tally_day([_zero("09:00", 0.5, 0.2, 0.4)])
    assert t["sized_calls"] is None
    assert t["non_zero_upper"] is None


def test_collect_filters_dates_and_ignores_unrelated_logs(st, tmp_path):
    def write(name: str, recs: list[dict]) -> None:
        body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs) + "not json\n"
        (tmp_path / name).write_text(body, encoding="utf-8", newline="\n")

    write("g2_daily_20261016.log", [_zero("09:00", 0.6, 0.2, 0.33), _streak(1, 1)])
    write("g2_daily_20261019.log", [_zero("14:30", 0.42, 0.2, 0.466), _streak(1, 1)])
    write("l1_daily_20261019.log", [_zero("14:30", 0.99, 0.2, 0.2)])
    days = st.collect(tmp_path, date(2026, 10, 19), None)
    assert list(days) == [date(2026, 10, 19)]
    assert days[date(2026, 10, 19)]["raw_max"] == pytest.approx(0.42)
    text = st.render(days)
    assert "| 2026-10-19 | 1 | 0.420 |" in text


def test_empty_period_says_unmeasured(st, tmp_path):
    assert "미측정" in st.render(st.collect(tmp_path, None, None))
