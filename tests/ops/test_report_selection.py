"""**어느 리포트를 채점 대상으로 고르는가** — 2026-08-14 F-B · F-D.

채점기가 옳아도 **입력을 잘못 고르면** 결론이 통째로 거짓이 된다. 이 파일은 그 고르는
단계만 본다(지표 계산은 `test_fix_verification.py`).

2026-08-14에 그 단계가 두 방향으로 무너져 있었다:

  ① 보존본이 정본을 덮었다 — `load_daily_reports()`가 파일명을 안 보고 JSON 안의 `date`로
     키를 잡아서, 옆에 둔 `daily_integrity_20260805_pre_recompose.json`이 정본을 밀어냈다.
     **9거래일간** 08-05의 채점이 재합성 이전 값으로 돌아가 있었다(실측: `horizon_findings`
     정본 0 → 읽힌 값 5 · `unmeasured` 0 → 2 · `breaches` 4 → 9).
  ② 엉뚱한 심볼을 본 리포트가 그대로 채점됐다 — 첫 월물 롤에서 배치가 만기된 A05608을
     조회해 `tick_rows`가 0으로 찍혔고, 그 0은 "틱이 없었다"가 아니라 "안 봤다"였다
     (정정 후 110,397).
"""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

import pytest

from messiah.ops import fix_verification, task_exit_codes
from messiah.ops.integrity_report import _detect_symbol_mismatch

_DAY = date(2026, 8, 14)


def _write_report(log_dir: Path, name: str, **fields) -> None:
    payload = {"date": _DAY.isoformat(), "tick_rows": 1, **fields}
    (log_dir / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------- ① 어느 파일이 그날의 정본인가


def test_preserved_copies_do_not_displace_the_canonical_report(tmp_path: Path) -> None:
    """보존본은 증거이지 채점 입력이 아니다 — 2026-08-05가 9거래일간 당한 형태."""
    _write_report(tmp_path, "daily_integrity_20260814.json", tick_rows=110397)
    # `sorted()`에서 접미사 붙은 이름이 **뒤에** 온다 — 종전 구현은 이것이 이겼다.
    _write_report(tmp_path, "daily_integrity_20260814_wrong_symbol.json", tick_rows=0)
    _write_report(tmp_path, "daily_integrity_20260805_pre_recompose.json", tick_rows=7)

    reports = fix_verification.load_daily_reports(tmp_path)

    assert list(reports) == [_DAY]
    assert reports[_DAY]["tick_rows"] == 110397


def test_report_whose_name_and_content_disagree_is_dropped(tmp_path: Path) -> None:
    """파일명과 안의 `date`가 다르면 **어느 쪽을 믿을지 조용히 고르지 않는다**."""
    (tmp_path / "daily_integrity_20260814.json").write_text(
        json.dumps({"date": "2026-08-13", "tick_rows": 5}), encoding="utf-8"
    )
    assert fix_verification.load_daily_reports(tmp_path) == {}


def test_provisional_report_is_still_skipped(tmp_path: Path) -> None:
    """2026-08-12 F-3의 처분은 그대로다 — 이번 변경이 그것을 지우지 않았는지 지킨다."""
    _write_report(tmp_path, "daily_integrity_20260814.json", provisional=True)
    assert fix_verification.load_daily_reports(tmp_path) == {}


def test_symbol_mismatched_report_is_not_scored(tmp_path: Path) -> None:
    """엉뚱한 심볼을 본 날은 통과로도 위반으로도 안 센다 — `provisional`과 같은 처분,
    다른 사유. 침묵은 리포트 `breaches` 첫 줄이 대신 말한다."""
    _write_report(
        tmp_path,
        "daily_integrity_20260814.json",
        tick_rows=0,
        symbol_mismatch_suspected=True,
        symbol_candidates=["A05609"],
    )
    assert fix_verification.load_daily_reports(tmp_path) == {}


# ------------------------------------------------------- ② 0행인가, 안 본 것인가


def _touch_bar(bar_dir: Path, symbol: str, day: date) -> None:
    target = bar_dir / symbol / "1m" / f"{day.isoformat()}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()


def test_mismatch_is_suspected_only_when_someone_else_holds_the_day(tmp_path: Path) -> None:
    _touch_bar(tmp_path, "A05609", _DAY)
    # 대상은 비었고 다른 심볼이 갖고 있다 → 의심 성립(2026-08-14의 실제 형태).
    assert _detect_symbol_mismatch(tmp_path, "A05608", _DAY) == ["A05609"]


def test_no_suspicion_when_the_target_has_the_day(tmp_path: Path) -> None:
    _touch_bar(tmp_path, "A05609", _DAY)
    _touch_bar(tmp_path, "A05608", _DAY)
    assert _detect_symbol_mismatch(tmp_path, "A05609", _DAY) == []


def test_no_suspicion_when_nobody_holds_the_day(tmp_path: Path) -> None:
    """휴장이나 전면 수집 실패 — 그건 다른 축이 잡는다. 여기서 울면 늑대소년이 된다."""
    _touch_bar(tmp_path, "A05609", date(2026, 8, 13))
    assert _detect_symbol_mismatch(tmp_path, "A05609", _DAY) == []


# ------------------------------------------------------- ③ 못 재는 상태가 굳지 않게


def test_winevent_query_is_retried_before_giving_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """한 번 튕긴 것과 계속 안 되는 것은 다른 사건이다.

    2026-08-14 실측: `exit-code-matches-log`가 08-11 위반에 고정돼 있는데 그 뒤 사흘이 전부
    `TimeoutExpired`라 **위반을 씻을 기회 자체가 없었다.** 재시도는 그 고착을 푸는 쪽이다.
    """
    monkeypatch.setattr(task_exit_codes.sys, "platform", "win32")
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise subprocess.TimeoutExpired(cmd="powershell", timeout=kwargs.get("timeout", 0))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="OK 0\n", stderr="")

    report = task_exit_codes.collect(_DAY, runner=flaky, timeout_seconds=0.01, attempts=2)

    assert calls["n"] == 2
    assert report.available is True


def test_repeated_failure_says_how_many_times_it_tried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_exit_codes.sys, "platform", "win32")

    def always_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="powershell", timeout=kwargs.get("timeout", 0))

    report = task_exit_codes.collect(_DAY, runner=always_timeout, timeout_seconds=0.01, attempts=2)

    assert report.available is False
    assert "TimeoutExpired" in report.detail
    assert "2/2회 시도" in report.detail  # "계속 안 된다"가 문장에 남아야 한다
