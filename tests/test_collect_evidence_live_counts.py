"""수집기가 **오늘 섀도 통과 수와 WS 끊김 수를 직접 센다** (2026-10-13 G-79·G-80).

장후 배치 전에는 이 두 숫자가 어디에도 없어 매 점검이 `grep -c` 로 손수 셌다.
여기서 붙잡는 성질:
  ① `passed_shadow` 가 bool 인 판단만 분모에 넣는다(F-41 이전 레코드는 미측정).
  ② WS 끊김은 오늘 로그에서 세고, 직전 기록일(최대 5개)의 `tag_counts` 평균을 붙인다.
     오늘·미래 날짜의 무결성 파일은 평균에 넣지 않는다.
  ③ 로그가 없으면 0이 아니라 **미측정**이라고 말한다(L18).
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

_DAY = date(2026, 10, 13)


@pytest.fixture(scope="module")
def ce():
    spec = importlib.util.spec_from_file_location("_collect_evidence_live_counts", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _log(tmp_path: Path, name: str, records: list[dict], junk: str = "") -> None:
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records) + junk
    (logs / name).write_text(body, encoding="utf-8", newline="\n")


def _integrity(tmp_path: Path, ymd: str, ws: int) -> None:
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    obj = {"tag_counts": {"CollectorWSDisconnected": ws}}
    (logs / f"daily_integrity_{ymd}.json").write_text(json.dumps(obj), encoding="utf-8")


def _meta(shadow) -> dict:
    rec = {"tag": "MetaGateEvaluated", "passed": True}
    if shadow is not None:
        rec["passed_shadow"] = shadow
    return rec


def test_shadow_counts_only_bool_records(tmp_path: Path, ce) -> None:
    _log(
        tmp_path,
        "g2_daily_20261013.log",
        [_meta(True), _meta(False), _meta(False), _meta(None), {"tag": "DecisionEmitted"}],
        junk='{"tag": "MetaGateEvaluated", broken\n',
    )

    shadow_line = ce.live_counts_lines(tmp_path, _DAY)[0]

    assert "통과 1/3건" in shadow_line and "33.3%" in shadow_line


def test_ws_today_and_trailing_average(tmp_path: Path, ce) -> None:
    ws = {"tag": "CollectorWSDisconnected"}
    _log(tmp_path, "l1_daily_20261013.log", [ws] * 7 + [{"tag": "CollectorWSReconnected"}])
    for ymd, n in [
        ("20261002", 9),
        ("20261006", 3),
        ("20261007", 1),
        ("20261012", 2),
        ("20261001", 0),
        ("20260930", 50),
        ("20261013", 99),
        ("20261014", 99),
    ]:
        _integrity(tmp_path, ymd, n)

    ws_line = ce.live_counts_lines(tmp_path, _DAY)[-1]

    assert "오늘 7건" in ws_line
    # 오늘(13일)·미래(14일)는 빼고 가장 가까운 5개(12·07·06·02·01일): (2+1+3+9+0)/5
    assert "직전 5개 기록일 평균 3.0건" in ws_line
    assert "09-30" not in ws_line and "10-13" not in ws_line


def test_missing_logs_are_unmeasured_not_zero(tmp_path: Path, ce) -> None:
    shadow_line, ws_line = ce.live_counts_lines(tmp_path, _DAY)

    assert "미측정" in shadow_line
    assert "미측정" in ws_line


def test_no_shadow_records_is_unmeasured(tmp_path: Path, ce) -> None:
    _log(tmp_path, "g2_daily_20261013.log", [_meta(None)])

    assert "미측정" in ce.live_counts_lines(tmp_path, _DAY)[0]


def test_no_history_says_so(tmp_path: Path, ce) -> None:
    _log(tmp_path, "l1_daily_20261013.log", [{"tag": "CollectorWSDisconnected"}])

    ws_line = ce.live_counts_lines(tmp_path, _DAY)[-1]

    assert "오늘 1건" in ws_line and "비교 재료 없음" in ws_line
