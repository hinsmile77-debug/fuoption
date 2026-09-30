"""수집기가 **R18 판단 가능 시점을 매일 드러낸다** (2026-09-30 G-8-B).

섀도 메타게이트 20거래일 창이 찬 사실은 `postmarket_*.log` 의 비-JSON 한 줄로만 나와,
다음 점검이 그 줄을 놓치면 승격 검토 시점이 조용히 지나간다. 여기서 붙잡는 성질:
  ① 창이 차면 「R18 판단 가능」과 「사람 결정」을 함께 말한다.
  ② 창이 덜 찼으면 판단 불가라고 말한다.
  ③ 파일이 없거나 깨졌으면 0이 아니라 **미측정**이라고 말한다(L18).
  ④ 기준일이 오늘이 아니면 그 사실을 붙인다.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / ".claude"
    / "skills"
    / "messiah-daily-check"
    / "scripts"
    / "collect_evidence.py"
)

_DAY = date(2026, 9, 30)


@pytest.fixture(scope="module")
def ce():
    spec = importlib.util.spec_from_file_location("_collect_evidence_shadow_gate", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(tmp_path: Path, **over) -> Path:
    obj = {
        "as_of": "2026-09-30",
        "window_days": 20,
        "days_measured": 20,
        "window_complete": True,
        "shadow_measured": 278,
        "shadow_passes": 182,
        "shadow_pass_rate": 0.6547,
    }
    obj.update(over)
    (tmp_path / "logs").mkdir(exist_ok=True)
    (tmp_path / "logs" / "meta_gate_shadow_rolling.json").write_text(
        json.dumps(obj), encoding="utf-8", newline="\n"
    )
    return tmp_path


def test_complete_window_says_r18_ready_and_human_decides(tmp_path: Path, ce) -> None:
    (line,) = ce.shadow_gate_r18_lines(_write(tmp_path), _DAY)

    assert "R18 판단 가능" in line
    assert "사람 결정" in line
    assert "20/20일" in line
    assert "278건" in line and "182건" in line and "65.5%" in line
    assert "오늘 것 아님" not in line


def test_incomplete_window_is_not_ready(tmp_path: Path, ce) -> None:
    (line,) = ce.shadow_gate_r18_lines(
        _write(tmp_path, days_measured=12, window_complete=False), _DAY
    )

    assert "R18 판단 불가" in line
    assert "12/20일" in line
    assert "판단 가능" not in line


def test_missing_file_is_unmeasured_not_zero(tmp_path: Path, ce) -> None:
    (line,) = ce.shadow_gate_r18_lines(tmp_path, _DAY)

    assert "미측정" in line


def test_broken_file_is_unmeasured(tmp_path: Path, ce) -> None:
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "meta_gate_shadow_rolling.json").write_text("{", encoding="utf-8")

    (line,) = ce.shadow_gate_r18_lines(tmp_path, _DAY)

    assert "해석 실패" in line and "미측정" in line


def test_stale_as_of_is_called_out(tmp_path: Path, ce) -> None:
    (line,) = ce.shadow_gate_r18_lines(_write(tmp_path, as_of="2026-09-29"), _DAY)

    assert "오늘 것 아님" in line


def test_missing_pass_rate_is_unmeasured(tmp_path: Path, ce) -> None:
    (line,) = ce.shadow_gate_r18_lines(_write(tmp_path, shadow_pass_rate=None), _DAY)

    assert "(미측정)" in line
