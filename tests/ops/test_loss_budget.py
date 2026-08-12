"""소급 불가 손실의 이동 예산 (2026-08-10 G-6).

세 번 잃었고 세 번 고쳤는데, **"3주에 173분을 잃었다"** 고 말한 축은 없었다:

    2026-08-06   21분   호스트 재부팅
    2026-08-07  114분   UI 스모크 테스트가 운영 버스에 sys.kill
    2026-08-10   38분   정시 트리거가 기동 창 가드에 막혔다

매번 "이번 한 번"으로 읽혔고, 그래서 매번 그날의 개별 원인만 고쳤다.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from messiah.ops import loss_budget


def _write(log_dir: Path, day: date, minutes: float | None) -> None:
    report: dict = {"date": day.isoformat(), "symbol": "A05608"}
    if minutes is not None:
        report["irrecoverable_loss_minutes"] = minutes
    (log_dir / f"daily_integrity_{day:%Y%m%d}.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )


def test_a_clean_week_is_zero_and_quiet(tmp_path: Path):
    """정상 주간은 0분이어야 한다 — 2026-08-04·08-05 실측이 그랬다."""
    for offset, day in enumerate((3, 4, 5, 6, 7)):
        _write(tmp_path, date(2026, 8, day), 0.0)
        assert offset >= 0

    budget = loss_budget.summarize(tmp_path)

    assert budget.total_minutes == 0.0
    assert not budget.over_budget
    assert budget.finding() == []
    assert "합 0분" in budget.describe()


def test_the_real_three_weeks_are_over_budget(tmp_path: Path):
    """08-06 21분 + 08-07 114분 + 08-10 38분 = 173분. 첫 주는 무조건 넘는다 — 그게 맞다."""
    _write(tmp_path, date(2026, 8, 4), 0.0)
    _write(tmp_path, date(2026, 8, 5), 0.0)
    _write(tmp_path, date(2026, 8, 6), 21.0)
    _write(tmp_path, date(2026, 8, 7), 114.0)
    _write(tmp_path, date(2026, 8, 10), 38.0)

    budget = loss_budget.summarize(tmp_path)

    assert budget.total_minutes == 173.0
    assert budget.over_budget
    (finding,) = budget.finding()
    assert "173분" in finding
    assert "원인은 날마다 달랐지만" in finding
    assert "최대 2026-08-07 114분" in budget.describe()


def test_the_window_slides_so_a_fixed_week_actually_goes_down(tmp_path: Path):
    """누적 총합이면 영원히 커지기만 해서 아무도 안 본다 — 좋아지면 내려가야 한다."""
    _write(tmp_path, date(2026, 8, 6), 21.0)
    _write(tmp_path, date(2026, 8, 7), 114.0)
    for day in (10, 11, 12, 13, 14):
        _write(tmp_path, date(2026, 8, day), 0.0)

    budget = loss_budget.summarize(tmp_path)

    assert budget.total_minutes == 0.0, "옛 사고가 창 밖으로 나갔다"
    assert budget.measured_days == 5


def test_days_counted_are_trading_days_not_calendar_days(tmp_path: Path):
    """주말·휴장을 세면 창이 실제로는 사흘치가 된다."""
    for day in (3, 4, 5, 6, 7):  # 월~금
        _write(tmp_path, date(2026, 8, day), 2.0)
    _write(tmp_path, date(2026, 8, 10), 2.0)  # 다음 월요일

    budget = loss_budget.summarize(tmp_path)

    assert budget.measured_days == 5
    assert [day for day, _ in budget.days][0] == date(2026, 8, 4), "가장 오래된 날이 밀려났다"


def test_reports_without_the_axis_are_counted_as_unmeasured_not_zero(tmp_path: Path):
    """창의 절반이 비었는데 "합 0분"이라 말하면 그건 좋은 소식이 아니라 계측 고장이다(L18)."""
    _write(tmp_path, date(2026, 8, 6), None)
    _write(tmp_path, date(2026, 8, 7), None)
    _write(tmp_path, date(2026, 8, 10), 38.0)

    budget = loss_budget.summarize(tmp_path)

    assert budget.total_minutes == 38.0
    assert budget.missing_days == 2
    assert "이 축이 없는 날 2일" in budget.describe()


def test_no_reports_at_all_is_undecidable(tmp_path: Path):
    budget = loss_budget.summarize(tmp_path)

    assert budget.days == []
    assert "판정 불가" in budget.describe()
    assert budget.finding() == []


def test_a_broken_report_does_not_stop_the_tally(tmp_path: Path):
    """깨진 파일 하나가 나머지 집계를 막으면, 그날 이후로 이 축이 조용해진다."""
    _write(tmp_path, date(2026, 8, 10), 38.0)
    (tmp_path / "daily_integrity_20260807.json").write_text("{깨짐", encoding="utf-8")

    assert loss_budget.summarize(tmp_path).total_minutes == 38.0


# ------------------------------------- 단일 사건 지배 표시 (2026-08-12 G-3)


def test_a_single_bad_day_is_named_in_the_finding(tmp_path: Path):
    """2026-08-12 실측 재현 — 51분의 80%가 사흘 전 하루 몫이었다.

    그날 경보 문장은 「3거래일에 51분(> 예산 20분)」뿐이었고, 그것을 읽은 사람의 첫인상은
    **"요즘 계속 새고 있다"**였다. 사실은 "사흘 전 한 번 크게 샜고 이후 안정적"이며 다음
    날이면 08-10이 창에서 빠져 10분으로 자동 복귀할 상태였다.

    **추세와 단발은 처방이 다르다** — 추세면 구조를 봐야 하고 단발이면 그날 원인 하나로
    끝난다. 임계 판정은 그대로 두고(누적이 예산을 넘은 것은 사실이다) 구분만 가능하게 한다.
    """
    _write(tmp_path, date(2026, 8, 10), 41.0)
    _write(tmp_path, date(2026, 8, 11), 5.0)
    _write(tmp_path, date(2026, 8, 12), 5.0)

    budget = loss_budget.summarize(tmp_path)

    assert budget.total_minutes == 51.0
    assert budget.over_budget, "임계 판정은 그대로다 — 누적이 예산을 넘은 것은 사실이다"

    day, minutes, share = budget.dominant_day
    assert (day, minutes) == (date(2026, 8, 10), 41.0)
    assert share == pytest.approx(0.8039, abs=1e-3)

    line = budget.finding()[0]
    assert "최대 2026-08-10 41분(80%)" in line
    assert "나머지 2일 10분" in line


def test_a_steady_leak_is_not_dominated_by_one_day(tmp_path: Path):
    """매일 고르게 새는 날은 최대 기여일의 몫이 작다 — 그 차이가 곧 추세와 단발의 구분이다."""
    for day in (10, 11, 12):
        _write(tmp_path, date(2026, 8, day), 15.0)

    budget = loss_budget.summarize(tmp_path)

    _day, _minutes, share = budget.dominant_day
    assert share == pytest.approx(1 / 3)
    assert "나머지 2일 30분" in budget.finding()[0]


def test_no_loss_has_no_dominant_day(tmp_path: Path):
    """0분인 주간에 "최대 기여일"을 말하면 없는 사고를 지목하는 것이다."""
    for day in (10, 11, 12):
        _write(tmp_path, date(2026, 8, day), 0.0)

    assert loss_budget.summarize(tmp_path).dominant_day is None
