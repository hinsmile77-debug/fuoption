"""섀도 메타게이트 20거래일 요약 (2026-09-29 G-8).

관측 축이므로 이 파일이 지키는 것은 셋이다 — 못 잰 날을 0으로 세지 않는다(L18),
창은 잰 날로 센다, 그리고 요약 파일 실패가 무결성 리포트 종료 코드를 흔들지 않는다.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from messiah.ops.meta_gate_shadow_rolling import judge, write

_CLI = Path(__file__).resolve().parents[2] / "scripts" / "daily_integrity_report.py"


def _report(measured: int | None, passes: int = 0, regimes: dict | None = None) -> dict:
    if measured is None:
        return {"meta_gate": {"evaluations": 14, "passes": 14, "shadow_measured": None}}
    return {
        "meta_gate": {
            "shadow_measured": measured,
            "shadow_passes": passes,
            "shadow_blocks": measured - passes,
            "blocked_by_regime": regimes or {},
        }
    }


def test_sums_window_and_rate_is_pooled_not_day_average():
    reports = {
        date(2026, 9, 28): _report(14, 13, {"RANGE": 1}),
        date(2026, 9, 29): _report(10, 4, {"HIGH_VOL": 4, "RANGE": 2}),
    }
    v = judge(day=date(2026, 9, 29), reports=reports)

    assert v.days_measured == 2
    assert v.shadow_measured == 24
    assert v.shadow_passes == 17
    assert v.shadow_pass_rate == round(17 / 24, 4)
    assert v.blocked_by_regime == {"HIGH_VOL": 4, "RANGE": 3}
    assert not v.window_complete


def test_unmeasured_days_do_not_fill_the_window():
    """`shadow_measured`가 None인 날·`meta_gate` 자체가 없는 날은 창에 안 들어간다 (L18)."""
    reports = {
        date(2026, 8, 28): {"meta_gate": None},
        date(2026, 8, 31): _report(None),
        date(2026, 9, 1): _report(14, 8),
    }
    v = judge(day=date(2026, 9, 1), reports=reports)

    assert v.days_measured == 1
    assert [d.day for d in v.days] == [date(2026, 9, 1)]


def test_nothing_measured_is_none_rate_not_zero():
    v = judge(day=date(2026, 9, 1), reports={date(2026, 9, 1): _report(None)})

    assert v.days_measured == 0
    assert v.shadow_pass_rate is None
    assert "미측정" in v.summary_line()


def test_window_takes_latest_measured_days_and_ignores_future():
    start = date(2026, 8, 1)
    reports = {start + timedelta(days=i): _report(10, 5) for i in range(25)}
    day = start + timedelta(days=22)
    v = judge(day=day, reports=reports, window_days=20)

    assert v.days_measured == 20
    assert v.window_complete
    assert v.days[-1].day == day  # 미래 날은 안 읽는다
    assert v.days[0].day == start + timedelta(days=3)
    assert "R18 판단 가능" in v.summary_line()


def test_write_roundtrip(tmp_path: Path):
    v = judge(day=date(2026, 9, 29), reports={date(2026, 9, 29): _report(14, 8, {"RANGE": 6})})
    out = write(v, day=date(2026, 9, 29), path=tmp_path / "x" / "rolling.json")

    body = json.loads(out.read_text(encoding="utf-8"))
    assert body["as_of"] == "2026-09-29"
    assert body["shadow_blocks"] == 6
    assert body["days"][0]["blocked_by_regime"] == {"RANGE": 6}


def _load_cli():
    spec = importlib.util.spec_from_file_location("daily_integrity_report_cli", _CLI)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Report:
    def __init__(self, breaches: list) -> None:
        self.breaches = breaches


def _run_cli(monkeypatch, breaches: list, rolling_raises: bool) -> int:
    cli = _load_cli()
    monkeypatch.setattr(sys, "argv", ["daily_integrity_report.py", "--date", "2026-09-29"])
    monkeypatch.setattr(cli.mlog, "setup", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "generate_and_write", lambda **_k: _Report(breaches))

    def _judge(**_k):
        if rolling_raises:
            raise RuntimeError("boom")
        return judge(day=date(2026, 9, 29), reports={})

    monkeypatch.setattr(cli.meta_gate_shadow_rolling, "judge", _judge)
    monkeypatch.setattr(cli.meta_gate_shadow_rolling, "write", lambda *_a, **_k: None)
    return cli.main()


def test_exit_code_unchanged_by_shadow_rolling(monkeypatch, capsys):
    """판정 불변 — 종료 코드는 여전히 `breaches` 유무만으로 갈린다."""
    for breaches, expected in (([], 0), (["x"], 1)):
        for raises in (False, True):
            assert _run_cli(monkeypatch, breaches, raises) == expected
    assert "실패" in capsys.readouterr().err
