"""화면 ⑤ 국면 실태 — 2026-09-02 신설.

이 보드가 존재하는 이유는 한 줄에 두 축을 붙이기 위해서다: **게이트 도달성**과 **방향
적중률**. 2026-09-02에 드러난 사실이 그 이음매에 있다 — 고변동 국면은 방향이 26%밖에 안
맞는데 손실이 안 났고, 그 이유는 판단력이 아니라 그 국면의 실효 천장이 게이트에 못 닿기
때문이었다. 둘을 따로 보여주면 사람이 둘을 이어 읽지 않는다.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from messiah.ui import app as app_module
from messiah.ui.app import _regime_board_lines

_DAY = date(2026, 9, 2)


@pytest.fixture(autouse=True)
def _isolated_logs(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app_module, "_REGIME_BOARD_DIR", tmp_path)
    monkeypatch.setattr(app_module, "_REGIME_BOARD_CACHE", app_module._RegimeBoardCache())
    return tmp_path


def _write(directory: Path, payload, day: date = _DAY) -> Path:
    path = directory / f"regime_direction_{day.strftime('%Y%m%d')}.json"
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    return path


def _payload(**overrides):
    payload = {
        "date": _DAY.isoformat(),
        "symbol": "A05609",
        "direction": {
            "n_scored": 146,
            "trading_days": 11,
            "min_trading_days": 20,
            "meets_shadow_window": False,
            "regime_sources": {"self": 0, "joined": 148},
            "regimes": {
                "HIGH_VOL": {
                    "status": "측정",
                    "n": 38,
                    "n_hit": 10,
                    "hit_rate": 0.263,
                    "n_gate_passed": 1,
                    "net_ticks": -11415.8,
                    "flagged": True,
                },
                "TREND_UP": {
                    "status": "표본 부족",
                    "n": 12,
                    "n_hit": 8,
                    "hit_rate": 0.667,
                    "n_gate_passed": 3,
                    "net_ticks": 2165.8,
                    "flagged": False,
                },
            },
        },
        "reachability": {
            "HIGH_VOL": {"closed": True, "ceiling_solo": 0.188, "score_gate": 0.2},
            "TREND_UP": {
                "closed": False,
                "reachable_solo": True,
                "ceiling_solo": 0.353,
                "score_gate": 0.2,
            },
        },
    }
    payload.update(overrides)
    return payload


def test_the_two_axes_land_on_one_line(_isolated_logs: Path) -> None:
    _write(_isolated_logs, _payload())

    lines = _regime_board_lines(_DAY)
    high_vol = next(line for line in lines if line.startswith("고변동"))

    assert "닫힘" in high_vol and "0.188" in high_vol  # 게이트 도달성
    assert "26%" in high_vol and "(10/38)" in high_vol  # 방향 적중률
    assert "⚠" in high_vol  # 깃발이 그 줄에 붙는다


def test_the_shadow_window_countdown_is_shown(_isolated_logs: Path) -> None:
    """20거래일을 채우기 전에는 **승격 판단 전**임이 헤더에 적힌다(R18)."""
    _write(_isolated_logs, _payload())

    header = _regime_board_lines(_DAY)[0]

    assert "11거래일" in header
    assert "9일 남음" in header


def test_a_joined_regime_is_disclosed(_isolated_logs: Path) -> None:
    """폴백으로 만든 성적은 그 사실이 화면에 남는다 — 조용한 폴백이 이 저장소의 병이다."""
    _write(_isolated_logs, _payload())

    assert any("이어 붙인 값" in line for line in _regime_board_lines(_DAY))


def test_yesterdays_file_is_used_but_dated(_isolated_logs: Path) -> None:
    """채점은 장후에 도니 장중 화면이 보는 것은 늘 어제 것이다 — 쓰되 날짜를 밝힌다."""
    _write(_isolated_logs, _payload(date="2026-09-01"), day=date(2026, 9, 1))

    header = _regime_board_lines(_DAY)[0]

    assert "2026-09-01" in header


def test_a_missing_file_says_how_to_produce_it(_isolated_logs: Path) -> None:
    lines = _regime_board_lines(_DAY)

    assert len(lines) == 1
    assert "미측정" in lines[0]
    assert "run_regime_direction_scorecard.py" in lines[0]


def test_a_broken_file_says_why(_isolated_logs: Path) -> None:
    path = _isolated_logs / f"regime_direction_{_DAY.strftime('%Y%m%d')}.json"
    path.write_text("{not json", encoding="utf-8")

    (line,) = _regime_board_lines(_DAY)

    assert "못 읽었다" in line


def test_missing_reachability_is_named_not_hidden(_isolated_logs: Path) -> None:
    """`--no-reachability`로 돌린 날 천장 칸이 조용히 비면 "열려 있다"로 읽힌다."""
    payload = _payload()
    payload.pop("reachability")
    _write(_isolated_logs, payload)

    assert any("실효 천장 미계산" in line for line in _regime_board_lines(_DAY))
