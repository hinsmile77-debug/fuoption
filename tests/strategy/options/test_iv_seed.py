"""IV Rank 이력 시드 (2026-09-02 O-4 선행 수정).

지키는 것 넷:
  ① 아카이브에서 **일별 1표본**을 복원한다(사이클마다 넣으면 252일 창이 17거래일로 쪼그라든다),
  ② **오늘은 안 넣는다**(라이브가 쌓는다 — 넣으면 당일 값이 두 번 센다),
  ③ 아카이브가 없어도 **기동을 막지 않는다**(빈 이력을 돌려주고 그 사실이 로그에 남는다),
  ④ 아카이브 행 해석은 라이브와 **같은 해석기**(`chain_smile.parse_leg`)를 탄다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import pytest

from messiah.strategy.options.iv_seed import _atm_iv_of_day, seed_iv_history
from messiah.strategy.options.surface import black76_price

_KST = timezone(timedelta(hours=9))
_FORWARD = 1000.0
_DTE = 11.0  # t = (11-1)/365


def _chain_rows(day: date, iv: float, *, cycles: int = 2) -> list[dict]:
    """그날의 체인 스냅샷 행들 — 마지막 사이클의 IV가 `iv`가 되도록 짓는다."""
    t = (_DTE - 1.0) / 365.0
    rows: list[dict] = []
    for cycle in range(cycles):
        # 앞 사이클은 다른 IV로 채워, "마지막 사이클을 쓴다"가 실제로 검증되게 한다.
        cycle_iv = iv if cycle == cycles - 1 else iv + 0.20
        stamp = datetime(day.year, day.month, day.day, 10 + cycle, 0, tzinfo=_KST)
        for strike in (960.0, 980.0, 1000.0, 1020.0, 1040.0):
            for option_type in ("C", "P"):
                price = black76_price(
                    forward=_FORWARD,
                    strike=strike,
                    r=0.0,
                    sigma=cycle_iv,
                    t=t,
                    option_type=option_type,
                )
                rows.append(
                    {
                        "ts_kst": stamp,
                        "series": "regular",
                        "symbol": f"{option_type}{int(strike)}",
                        "option_type": option_type,
                        "strike": strike,
                        "expiry": "202609",
                        "futs_prpr": price,
                        "hts_rmnn_dynu": _DTE,
                        "hts_otst_stpl_qty": 500.0,
                        "acml_vol": 100.0,
                        "acpr": strike,
                        "hts_ints_vltl": cycle_iv * 100.0,
                    }
                )
    return rows


def _write_day(base: Path, day: date, iv: float, *, series: str = "regular") -> None:
    directory = base / series
    directory.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(_chain_rows(day, iv)).write_parquet(directory / f"{day.isoformat()}.parquet")


def test_one_sample_per_day_and_it_is_the_last_cycle(tmp_path: Path):
    """일별 1표본 — 그리고 그 표본은 **마지막 사이클**이다(장 초반은 낡은 체결가가 섞인다)."""
    _write_day(tmp_path, date(2026, 9, 1), 0.30)

    atm = _atm_iv_of_day(tmp_path, "regular", date(2026, 9, 1))

    assert atm == pytest.approx(0.30, abs=0.01)  # 0.50이면 첫 사이클을 쓴 것


def test_seed_restores_days_in_time_order(tmp_path: Path):
    for offset, iv in enumerate((0.40, 0.35, 0.30)):  # 9/1 · 8/31 · 8/28 순서로 과거
        _write_day(tmp_path, date(2026, 9, 1) - timedelta(days=offset), iv)

    history = seed_iv_history(tmp_path, today=date(2026, 9, 2))

    values = list(history._values)  # noqa: SLF001 — 순서 검증이 목적
    assert len(values) == 3
    assert values[-1] == pytest.approx(0.40, abs=0.01)  # 가장 최근 값이 뒤에 온다
    # 가장 최근 IV가 셋 중 가장 높으므로 랭크는 100
    assert history.rank(values[-1]) == pytest.approx(100.0)


def test_today_is_not_seeded(tmp_path: Path):
    """오늘 값은 라이브가 쌓는다 — 여기서 넣으면 당일이 두 번 센다."""
    today = date(2026, 9, 2)
    _write_day(tmp_path, today, 0.50)
    _write_day(tmp_path, today - timedelta(days=1), 0.30)

    history = seed_iv_history(tmp_path, today=today)

    assert len(history) == 1
    assert list(history._values)[0] == pytest.approx(0.30, abs=0.01)  # noqa: SLF001


def test_missing_archive_does_not_block_startup(tmp_path: Path):
    """새 인스턴스·테스트 환경 — 빈 이력을 돌려주고 서비스는 선다(부가 축이 본 축을 막지 않는다)."""
    history = seed_iv_history(tmp_path / "없음", today=date(2026, 9, 2))

    assert len(history) == 0
    assert history.rank(0.5) is None  # 종전과 같은 "이력 부족" 동작


def test_window_caps_the_number_of_days(tmp_path: Path):
    for offset in range(5):
        _write_day(tmp_path, date(2026, 9, 1) - timedelta(days=offset), 0.30 + offset * 0.01)

    history = seed_iv_history(tmp_path, today=date(2026, 9, 2), maxlen=3)

    assert len(history) == 3


def test_a_day_without_a_valid_smile_is_skipped_not_zeroed(tmp_path: Path):
    """스마일이 안 서는 날(만기 당일 등)은 **건너뛴다** — 0을 넣으면 랭크가 통째로 틀어진다."""
    directory = tmp_path / "regular"
    directory.mkdir(parents=True)
    rows = _chain_rows(date(2026, 9, 1), 0.30)
    for row in rows:
        row["hts_rmnn_dynu"] = 1.0  # 만기 당일 → t <= 0 → 스마일 없음
    pl.DataFrame(rows).write_parquet(directory / "2026-09-01.parquet")
    _write_day(tmp_path, date(2026, 8, 31), 0.25)

    history = seed_iv_history(tmp_path, today=date(2026, 9, 2))

    assert len(history) == 1
    assert list(history._values)[0] == pytest.approx(0.25, abs=0.01)  # noqa: SLF001
