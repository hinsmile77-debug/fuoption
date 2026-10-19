"""수집기가 **설계상 생기는 g2 08:25~08:50 공백**을 적신호에서 빼 따로 적는다 (2026-10-19 G-82).

2026-09-21 이후 매 거래일 `g2_daily` 08:25~08:50 25분 공백이 §9 적신호로 올라왔다 —
선물 AI 방향 뷰가 08:50 전에는 오지 않아 기다리는 설계된 결과다.
여기서 붙잡는 성질:
  ① 시작·끝 창과 길이 상한을 **전부** 맞을 때만 면제한다.
  ② 창을 벗어나거나(08:25~09:53) 다른 프로세스면 그대로 적신호다.
"""

from __future__ import annotations

import importlib.util
import sys
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


@pytest.fixture(scope="module")
def ce():
    spec = importlib.util.spec_from_file_location("_collect_evidence_known_gaps", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _m(ce, hhmm: str) -> int:
    return ce.hhmm_to_minutes(hhmm)


def test_the_designed_g2_morning_wait_is_known(ce):
    cfg = ce.DEFAULT_CONFIG
    reason = ce.known_gap_reason(cfg, "g2_daily", _m(ce, "08:25"), _m(ce, "08:50"))
    assert reason and "뷰" in reason


@pytest.mark.parametrize(
    "start,end",
    [
        ("08:25", "09:53"),  # 뷰 수신이 늦어진 날 — 진짜 신호
        ("09:30", "10:00"),  # 장중 30분 결손
        ("08:10", "08:50"),  # 시작이 창 밖
        ("08:25", "09:00"),  # 끝이 창 밖
    ],
)
def test_outside_the_registered_window_stays_a_flag(ce, start, end):
    cfg = ce.DEFAULT_CONFIG
    assert ce.known_gap_reason(cfg, "g2_daily", _m(ce, start), _m(ce, end)) is None


def test_other_processes_are_never_exempt(ce):
    cfg = ce.DEFAULT_CONFIG
    assert ce.known_gap_reason(cfg, "l1_daily", _m(ce, "08:25"), _m(ce, "08:50")) is None


def test_length_cap_applies_even_inside_both_windows(ce):
    cfg = {
        "known_gaps": [
            {
                "proc": "g2_daily",
                "start": ["08:00", "08:30"],
                "end": ["08:45", "09:00"],
                "max_minutes": 30,
                "reason": "x",
            }
        ]
    }
    assert ce.known_gap_reason(cfg, "g2_daily", _m(ce, "08:00"), _m(ce, "08:55")) is None
    assert ce.known_gap_reason(cfg, "g2_daily", _m(ce, "08:25"), _m(ce, "08:55")) == "x"


def test_no_registry_means_no_exemption(ce):
    assert ce.known_gap_reason({}, "g2_daily", _m(ce, "08:25"), _m(ce, "08:50")) is None
