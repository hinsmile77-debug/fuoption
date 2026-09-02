"""Self-Eval 미니보드가 **실제 산출물**을 말한다 (2026-09-01 F-86 · 2026-08-11 F-5 잔여).

화면 ④엔 `Self-Evaluation 미니보드: Phase 5 미구현 — 자리만`이 조건 없이 찍혀 있었다.
그런데 자기평가는 매 거래일 15:34에 돌아 `logs/self_eval_<날짜>.json`을 남긴다 —
2026-09-01에도 1.1KB가 쌓인 채로 화면은 「미구현」이라 적었다. F-5가 고친 줄 **바로 아래**
줄이 같은 형태로 살아남아 있었다.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from messiah.ui import app as app_module
from messiah.ui.app import _self_eval_lines

_DAY = date(2026, 9, 1)


@pytest.fixture(autouse=True)
def _isolated_logs(tmp_path: Path, monkeypatch):
    """캐시는 모듈 전역이라 테스트마다 비운다 — 안 비우면 앞 테스트의 지문이 남는다."""
    monkeypatch.setattr(app_module, "_SELF_EVAL_DIR", tmp_path)
    monkeypatch.setattr(app_module, "_SELF_EVAL_CACHE", app_module._SelfEvalCache())
    return tmp_path


def _write(directory: Path, payload) -> Path:
    path = directory / f"self_eval_{_DAY.isoformat()}.json"
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    return path


def test_a_real_report_is_read_out(_isolated_logs: Path) -> None:
    """2026-09-01 실제 산출물의 세 칸 그대로."""
    _write(
        _isolated_logs,
        {"wiring_stage": "주문 미발생", "n_return_samples": 17, "pnl_measurable": False},
    )

    lines = _self_eval_lines(_DAY)

    assert len(lines) == 1
    assert "주문 미발생" in lines[0]
    assert "17" in lines[0]
    assert "미구현" not in lines[0]


def test_a_missing_file_says_when_it_is_due(_isolated_logs: Path) -> None:
    """조용히 빈칸으로 두면 「오늘 자기평가가 없다」로 읽힌다 — 답할 수 없는 상태다."""
    lines = _self_eval_lines(_DAY)

    assert len(lines) == 1
    assert "산출 전" in lines[0]
    assert "15:34" in lines[0]


def test_broken_json_reports_the_reason(_isolated_logs: Path) -> None:
    path = _isolated_logs / f"self_eval_{_DAY.isoformat()}.json"
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("{망가짐")

    lines = _self_eval_lines(_DAY)

    assert len(lines) == 1
    assert "못 읽었다" in lines[0]


def test_an_older_schema_shows_only_what_it_has(_isolated_logs: Path) -> None:
    """없는 칸을 0이나 빈칸으로 지어내지 않는다(L18)."""
    _write(_isolated_logs, {"wiring_stage": "주문 미발생"})

    line = _self_eval_lines(_DAY)[0]

    assert "주문 미발생" in line
    assert "수익률 표본" not in line


def test_an_empty_payload_says_there_is_nothing_to_read(_isolated_logs: Path) -> None:
    _write(_isolated_logs, {})

    assert "읽을 칸이 없다" in _self_eval_lines(_DAY)[0]


def test_the_file_is_not_reread_while_its_fingerprint_holds(
    _isolated_logs: Path, monkeypatch
) -> None:
    """LIVE 화면은 5초마다 다시 그린다 — 그 주기로 파일을 여는 것을 막는 캐시다."""
    _write(_isolated_logs, {"wiring_stage": "주문 미발생"})
    calls = {"n": 0}
    original = app_module._read_self_eval

    def _counting(path):
        calls["n"] += 1
        return original(path)

    monkeypatch.setattr(app_module, "_read_self_eval", _counting)

    _self_eval_lines(_DAY)
    _self_eval_lines(_DAY)

    assert calls["n"] == 1
